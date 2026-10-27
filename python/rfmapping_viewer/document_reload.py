"""Build replacement RF and companion snapshots away from the Tk thread."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from queue import SimpleQueue
from typing import Callable, Literal

from rfmapping_viewer.companions import (
    ProbeGeometry,
    TuningCurveData,
    discover_tuning_curve_path,
)
from rfmapping_viewer.constants import (
    ASYNC_DOCUMENT_LOAD_BYTES,
    DEFAULT_TUNING_CURVE_SESSION,
)
from rfmapping_viewer.rf_dataset import is_indexed_rfmap
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.rf_result_overlay import RFResultOverlayCache
from rfmapping_viewer.rf_results import DEFAULT_RF_RESULT_LAYOUT_PATTERN, RFResultSource, rf_result_path


@dataclass(frozen=True)
class ReloadRequest:
    generation: int
    path: Path
    preferred_unit_id: int | None
    tuning_path: Path | None = None
    probe_geometry: ProbeGeometry | None = None
    load_tuning: bool = False
    load_probe: bool = False
    load_waveform: bool = False
    tuning_session: int = DEFAULT_TUNING_CURVE_SESSION
    waveform_channel_mode: str = "same_x_column"
    result_layout_pattern: str = DEFAULT_RF_RESULT_LAYOUT_PATTERN


@dataclass(frozen=True)
class DocumentReloadEvent:
    generation: int
    stage: Literal["rf", "progress", "complete", "error"]
    data: RFMappingData | None = None
    tuning_data: TuningCurveData | None = None
    probe_geometry: ProbeGeometry | None = None
    waveform_payload: dict[str, object] | None = None
    result_cache: RFResultOverlayCache | None = None
    loaded: frozenset[str] = frozenset()
    missing: frozenset[str] = frozenset()
    errors: dict[str, str] = field(default_factory=dict)
    loading: str | None = None


def reload_document(
    request: ReloadRequest,
    results: SimpleQueue[DocumentReloadEvent],
    *,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """Publish RF first, then complete companions without changing the old data.

    The caller owns ``data`` from the moment the ``rf`` event is queued and must
    close stale candidates. Cancellation after that handoff never closes it in
    this worker. ``loaded``, ``missing`` and ``errors`` distinguish successful,
    absent and failed companions; a missing/failed replacement can retain the
    caller's previous display. Unrequested companions appear in none of them.
    """

    if cancelled is not None and cancelled():
        return
    try:
        data = RFMappingData(
            request.path,
            preferred_unit_id=request.preferred_unit_id,
            isolated=(
                not is_indexed_rfmap(request.path)
                and request.path.stat().st_size >= ASYNC_DOCUMENT_LOAD_BYTES
            ),
            cancelled=cancelled,
        )
    except Exception as exc:
        results.put(DocumentReloadEvent(request.generation, "error", errors={"rf": str(exc)}))
        return
    if cancelled is not None and cancelled():
        data.close()
        return
    results.put(DocumentReloadEvent(request.generation, "rf", data=data))
    if cancelled is not None and cancelled():
        return
    if data.unit_archive is not None:
        data.unit_archive.start_preload()

    loaded: set[str] = set()
    missing: set[str] = set()
    errors: dict[str, str] = {}
    tuning_data = None
    geometry = None
    waveform_payload = None
    result_cache = None
    for companion in ("tuning", "probe", "waveform", "results"):
        if cancelled is not None and cancelled():
            return
        if companion == "tuning" and request.tuning_path is None and not request.load_tuning:
            continue
        if companion == "probe" and request.probe_geometry is None and not request.load_probe:
            continue
        if companion == "waveform" and (not request.load_waveform or request.preferred_unit_id is None):
            continue
        results.put(DocumentReloadEvent(request.generation, "progress", loading=companion))
        try:
            if companion == "tuning":
                path = request.tuning_path or discover_tuning_curve_path(
                    data.path, request.tuning_session
                )
                if path is None:
                    missing.add(companion)
                    continue
                tuning_data = data.attach_hd_tuning(path)
            elif companion == "probe":
                previous = request.probe_geometry
                if previous is not None:
                    geometry = data.attach_probe_geometry(
                        previous.positions_path, previous.channels_path,
                        probe_name=previous.probe_name,
                    )
                else:
                    geometry = data.probe_geometry()
                    if data.probe_geometry_error:
                        raise ValueError(data.probe_geometry_error)
                if geometry is None:
                    missing.add(companion)
                    continue
            elif companion == "waveform":
                if data.waveform_store() is None:
                    if data.waveform_error:
                        raise ValueError(data.waveform_error)
                    missing.add(companion)
                    continue
                waveform_payload = data.waveform_plot_payload(
                    request.preferred_unit_id, request.waveform_channel_mode
                )
            else:
                result_cache = RFResultOverlayCache(RFResultSource.from_document(
                    data, layout_pattern=request.result_layout_pattern,
                ))
                result_errors = []
                result_count = 0
                for polarity in ("excitatory", "inhibitory"):
                    for dimension in ("2d", "1d"):
                        result = result_cache.result(dimension, polarity)
                        result_count += result is not None
                        if result is None and rf_result_path(
                            data.path, dimension=dimension, rf_type=polarity,
                            layout_pattern=request.result_layout_pattern,
                        ).exists():
                            result_errors.append(result_cache.error(dimension, polarity))
                if result_errors:
                    raise ValueError("; ".join(result_errors))
                if not result_count:
                    missing.add(companion)
                    continue
            loaded.add(companion)
        except Exception as exc:
            errors[companion] = str(exc)
    if cancelled is not None and cancelled():
        return
    results.put(DocumentReloadEvent(
        request.generation, "complete", data=data,
        tuning_data=tuning_data, probe_geometry=geometry,
        waveform_payload=waveform_payload, result_cache=result_cache,
        loaded=frozenset(loaded), missing=frozenset(missing), errors=errors,
    ))
