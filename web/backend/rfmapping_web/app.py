from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .asgi_access_gate import install_access_gate
from .companions import (
    companion_for_positions,
    discover_tuning_curve_path,
    find_hd_image,
    load_probe_geometry,
    load_tuning_curve,
    tuning_cluster_payload,
    tuning_dataset_payload,
    unavailable_tuning_cluster_payload,
    unavailable_tuning_dataset_payload,
)
from .config import Settings
from .datasets import DatasetChangedError, DatasetStore, DatasetValidationError
from .exports import (
    DisplayedCsvOptions,
    ExportValidationError,
    LinuxExportService,
    OutputPathError,
)
from .figure_exports import (
    FIGURE_SPEC_VERSION,
    FigureExportService,
    FigureExportValidationError,
    FigureInputSnapshot,
    FigureOutputPathError,
    FigurePageRenderer,
    FrozenScientificFile,
    SharedDestinationExistsError,
    SharedFigureExportError,
    expand_pages,
    figure_spec_registry,
    list_figure_directories,
    normalize_pages,
)
from .middleware import DirectAccessMiddleware
from .paths import PathAccessError, list_directory, resolve_under
from .waveforms import (
    DEFAULT_WAVEFORM_CHANNEL_MODE,
    WaveformArtifactError,
    WaveformArtifactStore,
    unavailable_waveform_payload,
)


WEB_VERSION = "1.9.6"


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenDatasetRequest(StrictRequest):
    path: str


class DisplayedCsvExportRequest(StrictRequest):
    clusterId: int
    valueMode: Literal[
        "Mean firing rate (Hz)",
        "Spike count",
    ]
    rfStartMs: float = Field(allow_inf_nan=False)
    rfEndMs: float = Field(allow_inf_nan=False)
    timeResolutionMs: float = Field(allow_inf_nan=False)
    xBins: int = Field(ge=1)
    yBins: int = Field(ge=1)
    smoothRadius: int = Field(ge=0, le=3)
    flipY: bool
    palette: Literal["Gray", "Viridis", "Inferno"]
    outputPath: str | None = Field(default=None, max_length=4096)
    overwrite: bool = False


class SaveImageRequest(StrictRequest):
    outputPath: str | None = Field(default=None, max_length=4096)
    overwrite: bool = False


class FigurePlotRequest(StrictRequest):
    type: str = Field(min_length=1, max_length=64)
    settings: dict[str, Any] = Field(default_factory=dict)


class FigurePageRequest(StrictRequest):
    title: str = Field(default="", max_length=200)
    plots: list[FigurePlotRequest] = Field(min_length=1, max_length=12)


class FigurePlanRequest(StrictRequest):
    specVersion: StrictInt
    pages: list[FigurePageRequest] = Field(min_length=1, max_length=50)
    hdPath: str | None = Field(default=None, max_length=4096)
    probePositionsPath: str | None = Field(default=None, max_length=4096)
    tuningSession: StrictInt = Field(default=1, ge=1)
    waveformChannelMode: Literal["same_x_column", "same_shank"] = (
        DEFAULT_WAVEFORM_CHANNEL_MODE
    )


class FigurePreviewRequest(FigurePlanRequest):
    clusterId: StrictInt
    pageIndex: StrictInt = Field(default=0, ge=0)


class FigureDestinationRequest(StrictRequest):
    directory: str = Field(default="", max_length=4096)
    baseName: str = Field(default="rfmapping_export", min_length=1, max_length=128)
    overwrite: bool = False


class FigureExportRequest(FigurePlanRequest):
    clusterIds: list[StrictInt] = Field(min_length=1)
    order: Literal["unit-major", "page-major"] = "unit-major"
    format: Literal["pdf", "png"] = "pdf"
    destination: FigureDestinationRequest


class BackendServices:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.datasets = DatasetStore(settings.cache_root, settings.cache_max_bytes)
        self.exports = LinuxExportService(settings.output_root)
        self.figure_exports = FigureExportService(settings.figure_export_root)


def _http_from_path_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail="Path not found")
    return HTTPException(status_code=400, detail=str(exc))


def _get_record(services: BackendServices, dataset_id: str):
    try:
        return services.datasets.get(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Dataset not found") from exc
    except DatasetChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _open_dataset(
    services: BackendServices, request: OpenDatasetRequest
) -> dict[str, Any]:
    try:
        source = resolve_under(services.settings.rf_root, request.path, expect="file")
        scope = services.settings.rf_root.resolve(strict=True)
    except (PathAccessError, FileNotFoundError, OSError) as exc:
        raise _http_from_path_error(exc) from exc
    try:
        record = services.datasets.open(
            source,
            public_source_path=str(source),
            scope_root=scope,
        )
    except DatasetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DatasetChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Unable to cache dataset: {exc}"
        ) from exc
    return services.datasets.response_metadata(record)


def _output_root_available(path: Path) -> bool:
    try:
        return path.is_dir() and os.access(path, os.W_OK)
    except OSError:
        return False


def _frontend_dist_root() -> Path | None:
    """Resolve static assets in either the source or deployed layout."""

    configured = os.environ.get("RFMAPPING_STATIC_ROOT")
    candidates = [] if configured is None else [Path(configured).expanduser()]
    package_dir = Path(__file__).resolve().parent
    candidates.extend(
        (
            package_dir.parents[1] / "frontend" / "dist",
            package_dir.parent / "web" / "dist",
        )
    )
    return next((candidate for candidate in candidates if candidate.is_dir()), None)


def _http_from_export_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (FileExistsError, SharedDestinationExistsError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (OutputPathError, FigureOutputPathError, SharedFigureExportError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, (ExportValidationError, FigureExportValidationError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=f"Unable to write Linux output: {exc}")


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_env()
    services = BackendServices(configured)
    application = FastAPI(
        title="RF Mapping Web API",
        version=WEB_VERSION,
    )
    install_access_gate(
        application,
        app_name="RF Mapping",
        base_path="/rfmapping",
        cookie_name="rfmapping_session",
        csrf_cookie_name="rfmapping_csrf",
        session_db=configured.gate_db_path,
        loopback_public_paths=frozenset({"/api/health"}),
    )
    application.add_middleware(
        DirectAccessMiddleware,
        allowed_networks=configured.allowed_networks,
        prefix="/rfmapping",
    )
    application.state.services = services

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": WEB_VERSION,
            "rfRoot": str(configured.rf_root),
            "rfRootAvailable": configured.rf_root.is_dir(),
            "outputRoot": str(configured.output_root),
            "outputRootAvailable": _output_root_available(configured.output_root),
            "figureExportRoot": str(configured.figure_export_root),
            "figureExportRootAvailable": _output_root_available(
                configured.figure_export_root
            ),
        }

    @application.get("/api/figure-exports/spec")
    def figure_export_spec() -> dict[str, Any]:
        return figure_spec_registry()

    @application.get("/api/figure-exports/directories")
    def figure_export_directories(
        path: str = Query(default="", max_length=4096),
    ) -> dict[str, Any]:
        try:
            return list_figure_directories(configured.figure_export_root, path)
        except FigureOutputPathError as exc:
            raise _http_from_export_error(exc) from exc

    @application.get("/api/fs/list")
    def browse_files(
        path: str = Query(default=""),
        cursor: str | None = Query(default=None),
        limit: int | None = Query(default=None, ge=1),
        kind: Literal["rf-json", "tuning-json", "positions-csv"] = Query(
            default="rf-json"
        ),
    ) -> dict[str, Any]:
        page_limit = limit or min(100, configured.directory_page_size_max)
        if page_limit > configured.directory_page_size_max:
            raise HTTPException(
                status_code=422,
                detail=f"limit must be <= {configured.directory_page_size_max}",
            )
        try:
            return list_directory(
                configured.rf_root,
                path,
                cursor=cursor,
                limit=page_limit,
                kind=kind,
            )
        except (PathAccessError, FileNotFoundError, OSError) as exc:
            raise _http_from_path_error(exc) from exc

    @application.post("/api/datasets/open")
    def open_dataset(request: OpenDatasetRequest) -> dict[str, Any]:
        return _open_dataset(services, request)

    @application.get("/api/datasets/{dataset_id}/meta")
    def dataset_metadata(dataset_id: str) -> dict[str, Any]:
        record = _get_record(services, dataset_id)
        return services.datasets.response_metadata(record)

    @application.get("/api/datasets/{dataset_id}/units/{cluster_id}")
    def unit_counts(dataset_id: str, cluster_id: int) -> Response:
        record = _get_record(services, dataset_id)
        try:
            payload, shape = services.datasets.unit_bytes(record, cluster_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Cluster not found") from exc
        except DatasetChangedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(
            content=payload,
            media_type="application/octet-stream",
            headers={
                "X-RF-Dtype": "<f8",
                "X-RF-Shape": ",".join(str(value) for value in shape),
                "X-RF-Cluster-Id": str(cluster_id),
            },
        )

    @application.post("/api/datasets/{dataset_id}/exports/displayed-csv")
    def export_displayed_csv(
        dataset_id: str, request: DisplayedCsvExportRequest
    ) -> dict[str, Any]:
        record = _get_record(services, dataset_id)
        try:
            unit_index, counts = services.datasets.unit_array(
                record, request.clusterId
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Cluster not found") from exc
        except DatasetChangedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        options = DisplayedCsvOptions(
            cluster_id=request.clusterId,
            value_mode=request.valueMode,
            rf_start_ms=request.rfStartMs,
            rf_end_ms=request.rfEndMs,
            time_resolution_ms=request.timeResolutionMs,
            x_bins=request.xBins,
            y_bins=request.yBins,
            smooth_radius=request.smoothRadius,
            flip_y=request.flipY,
            palette=request.palette,
            output_path=request.outputPath,
            overwrite=request.overwrite,
        )
        try:
            return services.exports.write_displayed_csv(
                record, unit_index, counts, options
            )
        except (FileExistsError, OutputPathError, ExportValidationError, OSError) as exc:
            raise _http_from_export_error(exc) from exc

    def _figure_companions(record, request: FigurePlanRequest):
        cluster_ids = (
            (request.clusterId,)
            if isinstance(request, FigurePreviewRequest)
            else tuple(request.clusterIds)
        )
        source_identity = FrozenScientificFile.capture(record.source)
        companion_identities: list[tuple[str, FrozenScientificFile]] = []

        tuning = None
        tuning_error = None
        tuning_path = (
            resolve_under(configured.rf_root, request.hdPath, expect="file")
            if request.hdPath is not None
            else discover_tuning_curve_path(
                record.source,
                record.scope_root,
                request.tuningSession,
            )
        )
        if tuning_path is not None:
            tuning_identity = FrozenScientificFile.capture(tuning_path)
            companion_identities.append(("headDirection", tuning_identity))
            try:
                tuning = load_tuning_curve(tuning_path)
            except (OSError, ValueError) as exc:
                tuning_identity.verify()
                tuning_error = f"HD tuning data could not be loaded: {exc}"
            else:
                tuning_identity.verify()
        probe = None
        probe_error = None
        probe_companions = record.companions
        if request.probePositionsPath is not None:
            positions_path = resolve_under(
                configured.rf_root, request.probePositionsPath, expect="file"
            )
            probe_companions = companion_for_positions(
                record.companions, positions_path, record.scope_root
            )
        if probe_companions.has_probe:
            probe_identities = tuple(
                FrozenScientificFile.capture(path)
                for path in (
                    probe_companions.positions_path,
                    probe_companions.channels_path,
                )
                if path is not None
            )
            companion_identities.extend(
                ("probeGeometry", identity) for identity in probe_identities
            )
            try:
                probe = load_probe_geometry(
                    probe_companions,
                    record.cache.metadata["unitPool"],
                )
            except (OSError, ValueError) as exc:
                for identity in probe_identities:
                    identity.verify()
                probe_error = f"Probe geometry could not be loaded: {exc}"
            else:
                for identity in probe_identities:
                    identity.verify()
        waveform = None
        waveform_error = None
        if record.companions.waveform_dir is not None:
            waveform_directory = record.companions.waveform_dir
            waveform_identities: dict[Path, FrozenScientificFile] = {}
            for path in (
                waveform_directory / "manifest.json",
                waveform_directory / "channels.csv",
                waveform_directory / "waveform_time.csv",
            ):
                if path.is_file():
                    waveform_identities[path.resolve()] = (
                        FrozenScientificFile.capture(path)
                    )
            try:
                discovered_waveform = WaveformArtifactStore(
                    waveform_directory,
                    scope_root=record.scope_root,
                )
                for path in discovered_waveform.source_paths_for_units(cluster_ids):
                    resolved = path.resolve()
                    if resolved not in waveform_identities:
                        waveform_identities[resolved] = FrozenScientificFile.capture(path)
                waveform = WaveformArtifactStore(
                    waveform_directory,
                    scope_root=record.scope_root,
                    template_cache_size=max(8, len(set(cluster_ids))),
                )
                waveform.preload_units(cluster_ids)
            except (OSError, ValueError) as exc:
                for identity in waveform_identities.values():
                    identity.verify()
                waveform_error = f"Waveform artifact could not be loaded: {exc}"
            else:
                for identity in waveform_identities.values():
                    identity.verify()
            companion_identities.extend(
                ("waveform", identity)
                for identity in waveform_identities.values()
            )

        input_snapshot = FigureInputSnapshot(
            source=source_identity,
            companions=tuple(companion_identities),
            companion_status={
                "headDirection": (
                    "available"
                    if tuning is not None
                    else tuning_error or "unavailable"
                ),
                "probeGeometry": (
                    "available" if probe is not None else probe_error or "unavailable"
                ),
                "waveform": (
                    "available"
                    if waveform is not None
                    else waveform_error or "unavailable"
                ),
            },
            snapshot={
                "unitIds": list(cluster_ids),
                "tuningCurveSession": request.tuningSession,
                "headDirectionPath": request.hdPath,
                "probePositionsPath": request.probePositionsPath,
                "waveformChannelMode": request.waveformChannelMode,
            },
        )
        input_snapshot.verify()
        return (
            tuning,
            probe,
            waveform,
            tuning_error,
            probe_error,
            waveform_error,
            input_snapshot,
        )

    def _normalized_figure_pages(record, request: FigurePlanRequest):
        if request.specVersion != FIGURE_SPEC_VERSION:
            raise FigureExportValidationError(
                f"Unsupported specVersion {request.specVersion}; expected {FIGURE_SPEC_VERSION}"
            )
        return normalize_pages(
            [page.model_dump() for page in request.pages], record.cache.metadata
        )

    @application.post("/api/datasets/{dataset_id}/figure-exports/preview")
    def preview_figure_export(
        dataset_id: str, request: FigurePreviewRequest
    ) -> Response:
        record = _get_record(services, dataset_id)
        try:
            pages = _normalized_figure_pages(record, request)
            if request.pageIndex >= len(pages):
                raise FigureExportValidationError("pageIndex is outside pages")
            unit_index, counts = services.datasets.unit_array(
                record, request.clusterId
            )
            (
                tuning,
                probe,
                waveform,
                tuning_error,
                probe_error,
                waveform_error,
                input_snapshot,
            ) = _figure_companions(
                record, request
            )
            renderer = FigurePageRenderer(
                record,
                tuning=tuning,
                probe=probe,
                waveform=waveform,
                tuning_error=tuning_error,
                probe_error=probe_error,
                waveform_error=waveform_error,
                waveform_channel_mode=request.waveformChannelMode,
                waveform_unit_ids=(request.clusterId,),
            )
            try:
                rendered = renderer.render_png(
                    request.clusterId,
                    unit_index,
                    counts,
                    pages[request.pageIndex],
                )
            finally:
                del counts
            input_snapshot.verify()
            services.datasets.get(dataset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Cluster not found") from exc
        except DatasetChangedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (PathAccessError, FileNotFoundError) as exc:
            raise _http_from_path_error(exc) from exc
        except (FigureExportValidationError, OSError) as exc:
            raise _http_from_export_error(exc) from exc
        headers = {
            "X-RF-Figure-Spec-Version": str(FIGURE_SPEC_VERSION),
            "X-RF-Render-SHA256": rendered.sha256,
            "X-RF-Cluster-Id": str(request.clusterId),
            "X-RF-Page-Index": str(request.pageIndex),
            "Cache-Control": "no-store",
        }
        if rendered.placeholders:
            headers["X-RF-Placeholder-Count"] = str(len(rendered.placeholders))
        return Response(content=rendered.png, media_type="image/png", headers=headers)

    @application.post("/api/datasets/{dataset_id}/figure-exports")
    def export_figures(
        dataset_id: str, request: FigureExportRequest
    ) -> dict[str, Any]:
        record = _get_record(services, dataset_id)
        try:
            pages = _normalized_figure_pages(record, request)
            if len(set(request.clusterIds)) != len(request.clusterIds):
                raise FigureExportValidationError("clusterIds must be unique")
            missing = [
                cluster_id
                for cluster_id in request.clusterIds
                if cluster_id not in record.cache.metadata["unitPool"]
            ]
            if missing:
                raise FigureExportValidationError(
                    "Unknown clusterIds: " + ", ".join(str(value) for value in missing)
                )
            jobs = expand_pages(request.clusterIds, pages, request.order)
            (
                tuning,
                probe,
                waveform,
                tuning_error,
                probe_error,
                waveform_error,
                input_snapshot,
            ) = _figure_companions(
                record, request
            )
            renderer = FigurePageRenderer(
                record,
                tuning=tuning,
                probe=probe,
                waveform=waveform,
                tuning_error=tuning_error,
                probe_error=probe_error,
                waveform_error=waveform_error,
                waveform_channel_mode=request.waveformChannelMode,
                waveform_unit_ids=request.clusterIds,
            )

            def validate_figure_inputs() -> None:
                services.datasets.get(dataset_id)
                input_snapshot.verify()

            arguments = {
                "record": record,
                "jobs": jobs,
                "renderer": renderer,
                "unit_loader": lambda cluster_id: services.datasets.unit_array(
                    record, cluster_id
                ),
                "validate_source": validate_figure_inputs,
                "provenance": input_snapshot.provenance(
                    application_version=WEB_VERSION
                ),
                "directory": request.destination.directory,
                "base_name": request.destination.baseName,
                "overwrite": request.destination.overwrite,
                "order": request.order,
            }
            if request.format == "pdf":
                return services.figure_exports.export_pdf(**arguments)
            return services.figure_exports.export_pngs(**arguments)
        except DatasetChangedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (PathAccessError, FileNotFoundError) as exc:
            raise _http_from_path_error(exc) from exc
        except (
            FileExistsError,
            SharedDestinationExistsError,
            FigureOutputPathError,
            FigureExportValidationError,
            SharedFigureExportError,
            OSError,
        ) as exc:
            raise _http_from_export_error(exc) from exc

    @application.get("/api/datasets/{dataset_id}/probe")
    def probe_geometry(
        dataset_id: str, path: str | None = Query(default=None)
    ) -> dict[str, Any]:
        record = _get_record(services, dataset_id)
        try:
            companions = record.companions
            if path is not None:
                positions = resolve_under(configured.rf_root, path, expect="file")
                companions = companion_for_positions(
                    companions, positions, record.scope_root
                )
            geometry = load_probe_geometry(
                companions,
                record.cache.metadata["unitPool"],
            )
        except (PathAccessError, FileNotFoundError) as exc:
            raise _http_from_path_error(exc) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if geometry is None:
            raise HTTPException(status_code=404, detail="Probe geometry unavailable")
        return geometry

    @application.get("/api/datasets/{dataset_id}/waveform/{cluster_id}")
    def waveform_artifact(
        dataset_id: str,
        cluster_id: int,
        mode: Literal["same_x_column", "same_shank"] = Query(
            default=DEFAULT_WAVEFORM_CHANNEL_MODE
        ),
    ) -> dict[str, Any]:
        record = _get_record(services, dataset_id)
        directory = record.companions.waveform_dir
        if directory is None:
            return unavailable_waveform_payload(
                "No companion data/waveform/Probe*/manifest.json was found for this RF dataset."
            )
        try:
            store = WaveformArtifactStore(directory, scope_root=record.scope_root)
            return store.payload_for(cluster_id, mode).as_dict()
        except KeyError as exc:
            return unavailable_waveform_payload(str(exc))
        except (OSError, WaveformArtifactError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def tuning_data_for_record(
        record,
        path: str | None,
        session: int | None,
    ):
        try:
            tuning_path = (
                resolve_under(configured.rf_root, path, expect="file")
                if path is not None
                else discover_tuning_curve_path(
                    record.source,
                    record.scope_root,
                    session,
                )
            )
        except (PathAccessError, FileNotFoundError) as exc:
            raise _http_from_path_error(exc) from exc
        if tuning_path is None:
            return None
        try:
            return load_tuning_curve(tuning_path)
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Unable to read tuning curves: {exc}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/api/datasets/{dataset_id}/hd")
    def hd_dataset(
        dataset_id: str,
        path: str | None = Query(default=None),
        session: int = Query(default=1, ge=1),
    ) -> dict[str, Any]:
        record = _get_record(services, dataset_id)
        data = tuning_data_for_record(record, path, session)
        return (
            unavailable_tuning_dataset_payload()
            if data is None
            else tuning_dataset_payload(data)
        )

    @application.get("/api/datasets/{dataset_id}/hd/{cluster_id}")
    def hd_artifact(
        dataset_id: str,
        cluster_id: int,
        path: str | None = Query(default=None),
        session: int = Query(default=1, ge=1),
    ) -> dict[str, Any]:
        record = _get_record(services, dataset_id)
        data = tuning_data_for_record(record, path, session)
        return (
            unavailable_tuning_cluster_payload()
            if data is None
            else tuning_cluster_payload(data, cluster_id)
        )

    @application.get("/api/datasets/{dataset_id}/hd/{cluster_id}/image")
    def hd_image(dataset_id: str, cluster_id: int) -> FileResponse:
        record = _get_record(services, dataset_id)
        image = find_hd_image(record.companions, cluster_id)
        if image is None:
            raise HTTPException(status_code=404, detail="HD image unavailable")
        return FileResponse(image, media_type="image/png")

    @application.post("/api/datasets/{dataset_id}/hd/{cluster_id}/save-image")
    def save_hd_image(
        dataset_id: str, cluster_id: int, request: SaveImageRequest
    ) -> dict[str, Any]:
        record = _get_record(services, dataset_id)
        image = find_hd_image(record.companions, cluster_id)
        if image is None:
            raise HTTPException(status_code=404, detail="HD image unavailable")
        try:
            return services.exports.save_png(
                image,
                output_path=request.outputPath,
                overwrite=request.overwrite,
            )
        except (FileExistsError, OutputPathError, OSError) as exc:
            raise _http_from_export_error(exc) from exc

    static_root = _frontend_dist_root()
    if static_root is not None:
        application.mount(
            "/", StaticFiles(directory=static_root, html=True), name="web"
        )
    return application


app = create_app()
