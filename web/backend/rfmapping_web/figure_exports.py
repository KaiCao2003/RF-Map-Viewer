from __future__ import annotations

import errno
import base64
import hashlib
import io
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
from PIL import Image

from .shared_figure_export import (
    SVG_RENDERING_CONTRACT,
    DestinationExistsError as SharedDestinationExistsError,
    ExportPage as SharedExportPage,
    FigureExportError as SharedFigureExportError,
    PLOT_KIND_REGISTRY as SHARED_PLOT_KIND_REGISTRY,
    PillowFigureRenderer,
    PlotSpec as SharedPlotSpec,
    _EntryIdentity as SharedEntryIdentity,
    _ParentDirectory as SharedParentDirectory,
    _atomic_directory_rename as _shared_atomic_directory_rename,
    _atomic_write_bytes_at as _shared_atomic_write_bytes_at,
    _commit_file as _shared_commit_file,
    _directory_publish_lock as _shared_directory_publish_lock,
    _entry_lstat as _shared_entry_lstat,
    _fallback_replace_directory_locked as _shared_fallback_replace_directory_locked,
    _fsync_directory_fd as _shared_fsync_directory_fd,
    _make_staging_directory as _shared_make_staging_directory,
    _make_staging_file as _shared_make_staging_file,
    _inspect_pdf_destination as _shared_inspect_pdf_destination,
    _open_directory_entry as _shared_open_directory_entry,
    _open_parent_directory as _shared_open_parent_directory,
    _read_regular_bytes_at as _shared_read_regular_bytes_at,
    _recover_directory_publish_locked as _shared_recover_directory_publish_locked,
    _remove_directory_at as _shared_remove_directory_at,
    _same_identity as _shared_same_identity,
    write_streaming_pdf,
)

from .companions import TuningCurveData
from .datasets import DatasetChangedError, DatasetRecord
from .exports import (
    VALUE_MODE_COUNT,
    VALUE_MODE_RATE,
    VALUE_MODES,
    _axis_groups,
    _smooth_matrix,
    _snap_time_range,
)
from .paths import is_within
from .waveforms import (
    DEFAULT_BASELINE_END_MS,
    DEFAULT_WAVEFORM_CHANNEL_MODE,
    WAVEFORM_CHANNEL_MODES,
    WaveformArtifactError,
    WaveformArtifactStore,
    shared_amplitude_limit,
)


FIGURE_SPEC_VERSION = 1
FIGURE_EXPORT_PRODUCER = "rfmapping.web.figure-export"
FIGURE_MANIFEST_VERSION = 2
FIGURE_PROVENANCE_VERSION = 1
PAGE_ORDERS = {"unit-major", "page-major"}
OUTPUT_FORMATS = {"pdf", "png", "svg"}

_PALETTE_NAMES = {
    "Gray": "gray",
    "Viridis": "viridis",
    "Inferno": "inferno",
}
_BASE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,127}$")


class FigureExportValidationError(ValueError):
    """A figure export specification is invalid for the selected dataset."""


class FigureOutputPathError(ValueError):
    """A figure export destination is outside the configured destination root."""


@dataclass(frozen=True)
class FrozenScientificFile:
    """Identity and digest for one scientific input used by an export.

    The digest is read through a no-follow descriptor and the path plus open
    handle identities are checked on both sides of the read.  The loaded
    companion objects may then be reused for every page, while :meth:`verify`
    rejects publication if the authoritative path changes afterwards.
    """

    path: Path
    sha256: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    mode: int
    handle_device: int
    handle_inode: int
    handle_size: int
    handle_mtime_ns: int
    handle_mode: int

    @staticmethod
    def _signature(info: os.stat_result) -> tuple[int, ...]:
        return (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(info.st_ctime_ns),
            int(stat.S_IFMT(info.st_mode)),
        )

    @staticmethod
    def _open_signature(info: os.stat_result) -> tuple[int, ...]:
        return (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(stat.S_IFMT(info.st_mode)),
        )

    @classmethod
    def capture(cls, path: Path) -> FrozenScientificFile:
        try:
            source = path.expanduser().resolve(strict=True)
            before = os.stat(source, follow_symlinks=False)
        except OSError as exc:
            raise DatasetChangedError(
                f"Scientific input is no longer available: {path}"
            ) from exc
        if not stat.S_ISREG(before.st_mode):
            raise DatasetChangedError(f"Scientific input is not a regular file: {source}")
        try:
            descriptor = os.open(
                source,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as exc:
            raise DatasetChangedError(
                f"Scientific input could not be opened safely: {source}"
            ) from exc
        try:
            handle_before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(handle_before.st_mode)
                or int(handle_before.st_size) != int(before.st_size)
            ):
                raise DatasetChangedError(
                    f"Scientific input changed while it was opened: {source}"
                )
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
            handle_after = os.fstat(descriptor)
            if cls._open_signature(handle_after) != cls._open_signature(handle_before):
                raise DatasetChangedError(
                    f"Scientific input changed while provenance was computed: {source}"
                )
        finally:
            os.close(descriptor)
        try:
            after = os.stat(source, follow_symlinks=False)
        except OSError as exc:
            raise DatasetChangedError(
                f"Scientific input is no longer available: {source}"
            ) from exc
        if cls._signature(after) != cls._signature(before):
            raise DatasetChangedError(
                f"Scientific input changed while provenance was computed: {source}"
            )
        return cls(
            path=source,
            sha256=digest.hexdigest(),
            device=int(after.st_dev),
            inode=int(after.st_ino),
            size=int(after.st_size),
            mtime_ns=int(after.st_mtime_ns),
            ctime_ns=int(after.st_ctime_ns),
            mode=int(stat.S_IFMT(after.st_mode)),
            handle_device=int(handle_after.st_dev),
            handle_inode=int(handle_after.st_ino),
            handle_size=int(handle_after.st_size),
            handle_mtime_ns=int(handle_after.st_mtime_ns),
            handle_mode=int(stat.S_IFMT(handle_after.st_mode)),
        )

    def verify(self) -> None:
        try:
            current = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise DatasetChangedError(
                f"Scientific input is no longer available: {self.path}"
            ) from exc
        expected = (
            self.device,
            self.inode,
            self.size,
            self.mtime_ns,
            self.ctime_ns,
            self.mode,
        )
        if self._signature(current) != expected:
            raise DatasetChangedError(
                "Scientific input changed after it was loaded; reopen it before "
                f"exporting: {self.path}"
            )

    def metadata(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "sizeBytes": self.size,
            "device": self.device,
            "inode": self.inode,
            "mtimeNs": self.mtime_ns,
            "ctimeNs": self.ctime_ns,
        }


@dataclass(frozen=True)
class FigureInputSnapshot:
    """Frozen RF/companion inputs and their export provenance document."""

    source: FrozenScientificFile
    companions: tuple[tuple[str, FrozenScientificFile], ...]
    companion_status: Mapping[str, str]
    snapshot: Mapping[str, Any]

    def verify(self) -> None:
        self.source.verify()
        for _kind, identity in self.companions:
            identity.verify()

    def scientific_snapshot_token(self) -> str:
        """Return a compact canonical identity for every scientific file.

        The token deliberately covers file membership, resolved paths, bytes,
        and filesystem identities, but not mutable figure recipe choices.  A
        composer can therefore change pages or selected units while retaining
        one frozen scientific-input identity.
        """

        companions = sorted(
            (
                {"kind": kind, **identity.metadata()}
                for kind, identity in self.companions
            ),
            key=lambda item: (str(item["kind"]), str(item["path"])),
        )
        document = {
            "identityVersion": 1,
            "source": self.source.metadata(),
            "companions": companions,
        }
        canonical = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return f"rf1.{hashlib.sha256(canonical).hexdigest()}"

    def provenance(
        self,
        *,
        application_version: str,
        shared_rf_scale_values: Sequence[Mapping[str, Any]] = (),
        shared_waveform_scale_values: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        waveform_scales = [dict(scale) for scale in shared_waveform_scale_values]
        return {
            "provenanceVersion": FIGURE_PROVENANCE_VERSION,
            "application": {
                "name": "RF Map Viewer",
                "version": application_version,
                "edition": "Web",
            },
            "source": self.source.metadata(),
            "snapshot": dict(self.snapshot),
            "companions": [
                {"kind": kind, **identity.metadata()}
                for kind, identity in self.companions
            ],
            "companionStatus": dict(self.companion_status),
            "sharedRFScales": [dict(scale) for scale in shared_rf_scale_values],
            "sharedWaveformScale": (
                waveform_scales[0] if len(waveform_scales) == 1 else None
            ),
            "sharedWaveformScales": waveform_scales,
            "renderingContract": {
                "preview": "same-page-renderer",
                "publication": "all scientific inputs reverified before atomic publish",
                "svg": SVG_RENDERING_CONTRACT,
            },
        }


def _setting(
    value_type: str,
    default: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    choices: Sequence[Any] | None = None,
    description: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": value_type,
        "default": default,
        "description": description,
    }
    if minimum is not None:
        result["minimum"] = minimum
    if maximum is not None:
        result["maximum"] = maximum
    if choices is not None:
        result["choices"] = list(choices)
    return result


_SPATIAL_SETTINGS = {
    "rfStartMs": _setting(
        "number", None, description="Left edge of the displayed half-open RF interval."
    ),
    "rfEndMs": _setting(
        "number", None, description="Right edge of the displayed half-open RF interval."
    ),
    "valueMode": _setting(
        "string",
        VALUE_MODE_RATE,
        choices=(VALUE_MODE_RATE, VALUE_MODE_COUNT),
        description="Displayed response normalization.",
    ),
    "xBins": _setting(
        "integer", None, minimum=1, description="Displayed x-bin count."
    ),
    "yBins": _setting(
        "integer", None, minimum=1, description="Displayed y-bin count."
    ),
    "smoothRadius": _setting(
        "integer", 0, minimum=0, maximum=3, description="Spatial smoothing passes."
    ),
    "flipY": _setting("boolean", False, description="Reverse displayed y rows."),
    "palette": _setting(
        "string",
        "Viridis",
        choices=tuple(_PALETTE_NAMES),
        description="Response colour palette.",
    ),
    "polarRadius": _setting(
        "string",
        "Display bottom inner",
        choices=("Display bottom inner", "MATLAB row 1 inner"),
        description="Mapping of displayed rows onto polar radius.",
    ),
}

_TEMPORAL_SETTINGS = {
    "timeResolutionMs": _setting(
        "number", 10.0, minimum=0.000001, description="Peak-search time grouping."
    ),
    "valueMode": _SPATIAL_SETTINGS["valueMode"],
    "xBins": _SPATIAL_SETTINGS["xBins"],
    "yBins": _SPATIAL_SETTINGS["yBins"],
    "smoothRadius": _SPATIAL_SETTINGS["smoothRadius"],
    "flipY": _SPATIAL_SETTINGS["flipY"],
    "palette": _SPATIAL_SETTINGS["palette"],
    "polarRadius": _SPATIAL_SETTINGS["polarRadius"],
    "responseFloor": _setting(
        "number", 0.0, minimum=0.0, description="Hide delay values at or below this mean count."
    ),
}

_TIMELINE_SETTINGS = {
    "timelineStartMs": _setting(
        "number", None, description="Current timeline selection start."
    ),
    "timelineEndMs": _setting(
        "number", None, description="Current timeline selection end."
    ),
    "activeTimeCenterMs": _setting(
        "number", None, description="Center of the currently active timeline group."
    ),
    "timeResolutionMs": _TEMPORAL_SETTINGS["timeResolutionMs"],
    "valueMode": _SPATIAL_SETTINGS["valueMode"],
    "xBins": _SPATIAL_SETTINGS["xBins"],
    "yBins": _SPATIAL_SETTINGS["yBins"],
    "smoothRadius": _SPATIAL_SETTINGS["smoothRadius"],
    "flipY": _SPATIAL_SETTINGS["flipY"],
    "palette": _SPATIAL_SETTINGS["palette"],
    "polarLayout": _setting(
        "boolean", False, description="Render timeline map thumbnails in polar layout."
    ),
    "polarRadius": _SPATIAL_SETTINGS["polarRadius"],
    "spatialProjection": _setting(
        "object",
        None,
        description="Optional frozen source selection: yStart, yEnd, xStart, xEnd.",
    ),
}

_HD_SETTINGS = {
    "displayBins": _setting(
        "integer", 30, minimum=1, maximum=180, description="Displayed HD bins."
    ),
    "smoothing": _setting(
        "boolean", True, description="Apply circular Gaussian smoothing."
    ),
    "sigmaDeg": _setting(
        "number", 18.0, minimum=0.1, maximum=180.0, description="Circular Gaussian sigma."
    ),
}

_WAVEFORM_SETTINGS = {
    "channelMode": _setting(
        "string",
        DEFAULT_WAVEFORM_CHANNEL_MODE,
        choices=WAVEFORM_CHANNEL_MODES,
        description="Local channels selected around the unit's best channel.",
    ),
}


FIGURE_TYPE_REGISTRY: dict[str, dict[str, Any]] = {
    "rf.cartesian": {
        "label": "RF map",
        "family": "rf",
        "projection": "cartesian",
        "settings": _SPATIAL_SETTINGS,
    },
    "rf.polar": {
        "label": "RF map (polar)",
        "family": "rf",
        "projection": "polar",
        "settings": _SPATIAL_SETTINGS,
    },
    "delay.cartesian": {
        "label": "Delay map",
        "family": "delay",
        "projection": "cartesian",
        "settings": _TEMPORAL_SETTINGS,
    },
    "delay.polar": {
        "label": "Delay map (polar)",
        "family": "delay",
        "projection": "polar",
        "settings": _TEMPORAL_SETTINGS,
    },
    "rgb.cartesian": {
        "label": "RGB response composite",
        "family": "rgb",
        "projection": "cartesian",
        "settings": _TEMPORAL_SETTINGS,
    },
    "rgb.polar": {
        "label": "RGB response composite (polar)",
        "family": "rgb",
        "projection": "polar",
        "settings": _TEMPORAL_SETTINGS,
    },
    "timeline.current": {
        "label": "Timeline (current settings)",
        "family": "timeline",
        "projection": "current",
        "settings": _TIMELINE_SETTINGS,
    },
    "hd.line": {
        "label": "HD tuning curve",
        "family": "hd",
        "projection": "line",
        "settings": _HD_SETTINGS,
        "capability": "hd",
    },
    "hd.polar": {
        "label": "HD tuning curve (polar)",
        "family": "hd",
        "projection": "polar",
        "settings": _HD_SETTINGS,
        "capability": "hd",
    },
    "probe": {
        "label": "Probe layout",
        "family": "probe",
        "projection": "cartesian",
        "settings": {},
        "capability": "probe",
    },
    "waveform.local_average": {
        "label": "Local average waveform",
        "family": "waveform",
        "projection": "time-channel",
        "settings": _WAVEFORM_SETTINGS,
        "capability": "waveform",
    },
}

if set(FIGURE_TYPE_REGISTRY) != set(SHARED_PLOT_KIND_REGISTRY):
    raise RuntimeError("Web and shared figure type registries are out of sync")


def figure_spec_registry() -> dict[str, Any]:
    return {
        "specVersion": FIGURE_SPEC_VERSION,
        "figureTypes": [
            {"id": type_id, **definition}
            for type_id, definition in FIGURE_TYPE_REGISTRY.items()
        ],
        "pageOrders": ["unit-major", "page-major"],
        "formats": ["pdf", "png", "svg"],
        "page": {
            "minPlots": 1,
            "maxPlots": 12,
            "default": {"title": "", "plots": [{"type": "rf.cartesian", "settings": {}}]},
        },
    }


@dataclass(frozen=True)
class FigurePlot:
    type_id: str
    settings: dict[str, Any]


@dataclass(frozen=True)
class FigurePage:
    title: str
    plots: tuple[FigurePlot, ...]


@dataclass(frozen=True)
class ExpandedPage:
    output_index: int
    cluster_id: int
    page_index: int
    page: FigurePage


@dataclass(frozen=True)
class RenderedPage:
    contents: bytes
    sha256: str
    placeholders: tuple[str, ...]


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FigureExportValidationError(f"{label} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise FigureExportValidationError(f"{label} must be a finite number")
    return parsed


def _integer(value: Any, label: str) -> int:
    parsed = _finite_number(value, label)
    if not parsed.is_integer():
        raise FigureExportValidationError(f"{label} must be an integer")
    return int(parsed)


def _projection(value: Any, metadata: Mapping[str, Any]) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"yStart", "yEnd", "xStart", "xEnd"}:
        raise FigureExportValidationError(
            "spatialProjection must contain exactly yStart, yEnd, xStart, and xEnd"
        )
    _n_units, n_y, n_x, _n_bins = metadata["shape"]
    parsed = {key: _integer(item, f"spatialProjection.{key}") for key, item in value.items()}
    if not (0 <= parsed["yStart"] <= parsed["yEnd"] < n_y):
        raise FigureExportValidationError("spatialProjection y range is outside the dataset")
    if not (0 <= parsed["xStart"] <= parsed["xEnd"] < n_x):
        raise FigureExportValidationError("spatialProjection x range is outside the dataset")
    return parsed


def _normalized_settings(
    type_id: str, raw: Mapping[str, Any], metadata: Mapping[str, Any]
) -> dict[str, Any]:
    definition = FIGURE_TYPE_REGISTRY.get(type_id)
    if definition is None:
        raise FigureExportValidationError(f"Unknown figure type: {type_id}")
    if not isinstance(raw, Mapping):
        raise FigureExportValidationError(f"Settings for {type_id} must be an object")
    schema: Mapping[str, Mapping[str, Any]] = definition["settings"]
    unknown = sorted(set(raw) - set(schema))
    if unknown:
        raise FigureExportValidationError(
            f"Unknown settings for {type_id}: {', '.join(unknown)}"
        )
    result: dict[str, Any] = {}
    _n_units, n_y, n_x, _n_bins = metadata["shape"]
    for name, setting in schema.items():
        value = raw.get(name, setting["default"])
        if value is None:
            if name in {"xBins", "yBins"}:
                result[name] = n_x if name == "xBins" else n_y
            else:
                result[name] = None
            continue
        value_type = setting["type"]
        if value_type == "number":
            value = _finite_number(value, name)
        elif value_type == "integer":
            value = _integer(value, name)
        elif value_type == "boolean":
            if type(value) is not bool:
                raise FigureExportValidationError(f"{name} must be boolean")
        elif value_type == "string":
            if not isinstance(value, str):
                raise FigureExportValidationError(f"{name} must be a string")
        elif value_type == "object":
            value = _projection(value, metadata)
        choices = setting.get("choices")
        if choices is not None and value not in choices:
            raise FigureExportValidationError(
                f"{name} must be one of: {', '.join(str(item) for item in choices)}"
            )
        if value is not None and "minimum" in setting and value < setting["minimum"]:
            raise FigureExportValidationError(f"{name} must be >= {setting['minimum']}")
        if value is not None and "maximum" in setting and value > setting["maximum"]:
            raise FigureExportValidationError(f"{name} must be <= {setting['maximum']}")
        result[name] = value

    if "valueMode" in schema:
        if result.get("valueMode") not in VALUE_MODES:
            raise FigureExportValidationError(
                f"Unknown value mode: {result.get('valueMode')}"
            )
    if "xBins" in schema:
        result["xBins"] = min(n_x, result["xBins"])
    if "yBins" in schema:
        result["yBins"] = min(n_y, result["yBins"])
    return result


def normalize_pages(
    raw_pages: Sequence[Mapping[str, Any]], metadata: Mapping[str, Any]
) -> tuple[FigurePage, ...]:
    if not isinstance(raw_pages, Sequence) or isinstance(raw_pages, (str, bytes)):
        raise FigureExportValidationError("pages must be an array")
    if not 1 <= len(raw_pages) <= 50:
        raise FigureExportValidationError("pages must contain between 1 and 50 pages")
    pages: list[FigurePage] = []
    for page_index, raw_page in enumerate(raw_pages):
        if not isinstance(raw_page, Mapping):
            raise FigureExportValidationError(f"pages[{page_index}] must be an object")
        if set(raw_page) - {"title", "plots"}:
            unknown = sorted(set(raw_page) - {"title", "plots"})
            raise FigureExportValidationError(
                f"Unknown page fields at pages[{page_index}]: {', '.join(unknown)}"
            )
        title = raw_page.get("title", "")
        if not isinstance(title, str) or len(title) > 200:
            raise FigureExportValidationError(f"pages[{page_index}].title is invalid")
        raw_plots = raw_page.get("plots")
        if not isinstance(raw_plots, Sequence) or isinstance(raw_plots, (str, bytes)):
            raise FigureExportValidationError(f"pages[{page_index}].plots must be an array")
        if not 1 <= len(raw_plots) <= 12:
            raise FigureExportValidationError(
                f"pages[{page_index}].plots must contain between 1 and 12 plots"
            )
        plots: list[FigurePlot] = []
        for plot_index, raw_plot in enumerate(raw_plots):
            if not isinstance(raw_plot, Mapping):
                raise FigureExportValidationError(
                    f"pages[{page_index}].plots[{plot_index}] must be an object"
                )
            if set(raw_plot) - {"type", "settings"}:
                unknown = sorted(set(raw_plot) - {"type", "settings"})
                raise FigureExportValidationError(
                    f"Unknown plot fields: {', '.join(unknown)}"
                )
            type_id = raw_plot.get("type")
            if not isinstance(type_id, str):
                raise FigureExportValidationError("Plot type must be a string")
            plots.append(
                FigurePlot(
                    type_id=type_id,
                    settings=_normalized_settings(
                        type_id, raw_plot.get("settings", {}), metadata
                    ),
                )
            )
        pages.append(FigurePage(title=title, plots=tuple(plots)))
    return tuple(pages)


def expand_pages(
    cluster_ids: Sequence[int], pages: Sequence[FigurePage], order: str
) -> tuple[ExpandedPage, ...]:
    if order not in PAGE_ORDERS:
        raise FigureExportValidationError(
            f"order must be one of: {', '.join(sorted(PAGE_ORDERS))}"
        )
    pairs: Iterable[tuple[int, int]]
    if order == "unit-major":
        pairs = ((cluster_id, page_index) for cluster_id in cluster_ids for page_index in range(len(pages)))
    else:
        pairs = ((cluster_id, page_index) for page_index in range(len(pages)) for cluster_id in cluster_ids)
    return tuple(
        ExpandedPage(
            output_index=output_index,
            cluster_id=cluster_id,
            page_index=page_index,
            page=pages[page_index],
        )
        for output_index, (cluster_id, page_index) in enumerate(pairs)
    )


def _time_groups(metadata: Mapping[str, Any], resolution_ms: float) -> list[tuple[int, int]]:
    edges = np.asarray(metadata["timeBinEdges"], dtype=np.float64) * 1000.0
    widths = np.diff(edges)
    base = float(np.min(widths[widths > 1e-12])) if np.any(widths > 1e-12) else 1.0
    n_bins = int(metadata["shape"][3])
    total = max(float(edges[-1] - edges[0]), base)
    requested = min(max(float(resolution_ms), base), total)
    # Python's round, like JavaScript's explicit helper in frontend/src/math.ts,
    # uses ties-to-even. Keep this grouping byte-for-byte aligned with the
    # interactive timeline, including irregular source edges.
    group_size = min(n_bins, max(1, int(round(requested / base))))
    target_duration = group_size * base
    groups: list[tuple[int, int]] = []
    start = 0
    while start < n_bins:
        target_edge = edges[start] + target_duration
        upper = start + 1
        while upper < n_bins and edges[upper] < target_edge:
            upper += 1
        lower = max(start + 1, upper - 1)
        end_exclusive = (
            lower
            if abs(edges[lower] - target_edge) <= abs(edges[upper] - target_edge)
            else upper
        )
        groups.append((start, end_exclusive - 1))
        start = end_exclusive
    return groups


def _time_group_for_ms(
    metadata: Mapping[str, Any], groups: Sequence[tuple[int, int]], milliseconds: float
) -> int:
    edges_ms = np.asarray(metadata["timeBinEdges"], dtype=np.float64) * 1000.0
    for index, (start, end) in enumerate(groups):
        group_start = float(edges_ms[start])
        group_end = float(edges_ms[end + 1])
        if group_start <= milliseconds and (
            milliseconds < group_end
            or (index == len(groups) - 1 and milliseconds <= group_end)
        ):
            return index
    return min(
        range(len(groups)),
        key=lambda index: abs(
            (
                float(edges_ms[groups[index][0]])
                + float(edges_ms[groups[index][1] + 1])
            )
            / 2.0
            - milliseconds
        ),
    )


def _time_group_range_for_ms(
    metadata: Mapping[str, Any],
    groups: Sequence[tuple[int, int]],
    requested_start_ms: float,
    requested_end_ms: float,
) -> tuple[int, int]:
    start_ms, end_ms = sorted((requested_start_ms, requested_end_ms))
    boundary_tolerance_ms = 1e-9
    if abs(start_ms - end_ms) < boundary_tolerance_ms:
        index = _time_group_for_ms(metadata, groups, start_ms)
        return index, index
    edges_ms = np.asarray(metadata["timeBinEdges"], dtype=np.float64) * 1000.0
    overlapping = [
        index
        for index, (start, end) in enumerate(groups)
        if float(edges_ms[end + 1]) > start_ms + boundary_tolerance_ms
        and float(edges_ms[start]) < end_ms - boundary_tolerance_ms
    ]
    if overlapping:
        return overlapping[0], overlapping[-1]
    return (
        _time_group_for_ms(metadata, groups, start_ms),
        _time_group_for_ms(metadata, groups, end_ms),
    )


def _spatial_group_observations(
    counts: np.ndarray,
    metadata: Mapping[str, Any],
    y_group: tuple[int, int],
    x_group: tuple[int, int],
    start: int,
    end: int,
) -> tuple[float, float, int]:
    """Mirror frontend/src/math.ts spatialGroupObservations for one display cell."""

    _n_units, n_y, n_x, n_bins = metadata["shape"]
    y_start = max(0, min(n_y - 1, min(y_group)))
    y_end = max(0, min(n_y - 1, max(y_group)))
    x_start = max(0, min(n_x - 1, min(x_group)))
    x_end = max(0, min(n_x - 1, max(x_group)))
    range_start = max(0, min(n_bins - 1, min(start, end)))
    range_end = max(0, min(n_bins - 1, max(start, end)))
    block = np.asarray(
        counts[y_start : y_end + 1, x_start : x_end + 1, :],
        dtype=np.float64,
    )
    occupancy = np.asarray(metadata["occupancyTimeSec"], dtype=np.float64)[
        y_start : y_end + 1, x_start : x_end + 1
    ]
    valid = occupancy > 0
    source_pixel_count = int(np.count_nonzero(valid))
    valid_histograms = block[valid]
    count = (
        float(valid_histograms[:, range_start : range_end + 1].sum())
        if source_pixel_count
        else 0.0
    )
    return count, float(occupancy[valid].sum()), source_pixel_count


def _spatial_group_histogram(
    counts: np.ndarray,
    metadata: Mapping[str, Any],
    y_group: tuple[int, int],
    x_group: tuple[int, int],
) -> tuple[np.ndarray, int]:
    """Mirror the frontend histogram and exposed-source-pixel helpers."""

    _n_units, n_y, n_x, _n_bins = metadata["shape"]
    y_start = max(0, min(n_y - 1, min(y_group)))
    y_end = max(0, min(n_y - 1, max(y_group)))
    x_start = max(0, min(n_x - 1, min(x_group)))
    x_end = max(0, min(n_x - 1, max(x_group)))
    block = np.asarray(
        counts[y_start : y_end + 1, x_start : x_end + 1, :],
        dtype=np.float64,
    )
    occupancy = np.asarray(metadata["occupancyTimeSec"], dtype=np.float64)[
        y_start : y_end + 1, x_start : x_end + 1
    ]
    valid = occupancy > 0
    source_pixel_count = int(np.count_nonzero(valid))
    if not source_pixel_count:
        return np.zeros(block.shape[2], dtype=np.float64), 0
    return block[valid].sum(axis=0), source_pixel_count


def _smooth_preserving_missing(
    matrix: list[list[float | None]], radius: int
) -> list[list[float | None]]:
    """Apply the shared kernel while retaining frontend null-center behavior."""

    current = [row[:] for row in matrix]
    for _pass in range(max(0, int(radius))):
        smoothed = _smooth_matrix(current, 1)
        for y_index, row in enumerate(current):
            for x_index, center in enumerate(row):
                if center is None or not math.isfinite(float(center)):
                    smoothed[y_index][x_index] = None
        current = smoothed
    return current


def _group_response_value(
    counts: np.ndarray,
    metadata: Mapping[str, Any],
    cell: tuple[int, int, int, int],
    source_range: tuple[int, int],
    value_mode: str,
) -> float | None:
    """Mirror frontend/src/math.ts groupResponseValue."""

    count, occupancy_seconds, source_pixel_count = _spatial_group_observations(
        counts,
        metadata,
        (cell[0], cell[1]),
        (cell[2], cell[3]),
        source_range[0],
        source_range[1],
    )
    if value_mode == VALUE_MODE_COUNT:
        return count / source_pixel_count if source_pixel_count > 0 else None
    if occupancy_seconds <= 0:
        return None
    return count / occupancy_seconds if value_mode == VALUE_MODE_RATE else None


def _prepared_response(
    counts: np.ndarray, metadata: Mapping[str, Any], settings: Mapping[str, Any]
) -> tuple[np.ndarray, list[tuple[int, int]], list[tuple[int, int]], tuple[float, float]]:
    edges_ms = [float(edge) * 1000.0 for edge in metadata["timeBinEdges"]]
    requested_start = edges_ms[0] if settings["rfStartMs"] is None else settings["rfStartMs"]
    requested_end = edges_ms[-1] if settings["rfEndMs"] is None else settings["rfEndMs"]
    start, end = _snap_time_range(edges_ms, requested_start, requested_end)
    x_groups = _axis_groups(metadata["shape"][2], settings["xBins"])
    y_groups = _axis_groups(metadata["shape"][1], settings["yBins"])
    if settings["flipY"]:
        y_groups.reverse()
    observations = [
        [
            _spatial_group_observations(
                counts, metadata, y_group, x_group, start, end
            )
            for x_group in x_groups
        ]
        for y_group in y_groups
    ]
    valid = [
        [
            source_pixel_count > 0
            for _count, _occupancy_seconds, source_pixel_count in row
        ]
        for row in observations
    ]
    if settings["valueMode"] == VALUE_MODE_COUNT:
        matrix = [
            [
                count / source_pixel_count if source_pixel_count > 0 else None
                for count, _occupancy_seconds, source_pixel_count in row
            ]
            for row in observations
        ]
        if settings["smoothRadius"] > 0:
            matrix = _smooth_preserving_missing(matrix, settings["smoothRadius"])
            matrix = [
                [
                    value if valid[y_index][x_index] else None
                    for x_index, value in enumerate(row)
                ]
                for y_index, row in enumerate(matrix)
            ]
    else:
        pooled_counts: list[list[float | None]] = [
            [
                count if source_pixel_count > 0 else None
                for count, _occupancy_seconds, source_pixel_count in row
            ]
            for row in observations
        ]
        pooled_occupancy: list[list[float | None]] = [
            [
                occupancy_seconds if source_pixel_count > 0 else None
                for _count, occupancy_seconds, source_pixel_count in row
            ]
            for row in observations
        ]
        if settings["smoothRadius"] > 0:
            pooled_counts = _smooth_preserving_missing(
                pooled_counts, settings["smoothRadius"]
            )
            pooled_occupancy = _smooth_preserving_missing(
                pooled_occupancy, settings["smoothRadius"]
            )
        matrix = []
        for y_index, count_row in enumerate(pooled_counts):
            row: list[float | None] = []
            for x_index, count in enumerate(count_row):
                occupancy_seconds = pooled_occupancy[y_index][x_index]
                if (
                    not valid[y_index][x_index]
                    or count is None
                    or occupancy_seconds is None
                    or occupancy_seconds <= 0
                ):
                    row.append(None)
                    continue
                row.append(count / occupancy_seconds)
            matrix.append(row)
    return (
        np.asarray(
            [[np.nan if value is None else float(value) for value in row] for row in matrix],
            dtype=np.float64,
        ),
        x_groups,
        y_groups,
        (edges_ms[start], edges_ms[end + 1]),
    )


def shared_rf_scales(
    record: DatasetRecord,
    pages: Sequence[FigurePage],
    unit_ids: Sequence[int],
    unit_loader: Callable[[int], tuple[int, np.ndarray]],
) -> tuple[dict[str, Any], ...]:
    """Compute Python-compatible scalar bounds across selected RF units.

    Every RF Cartesian/Polar recipe using the same response unit contributes
    to one min/max range.  This keeps projections and pages comparable while
    never mixing spike counts with firing rates.
    """

    if not unit_ids:
        raise FigureExportValidationError(
            "shared RF scale requires at least one selected unit"
        )
    recipes: dict[str, list[Mapping[str, Any]]] = {}
    recipe_keys: dict[str, set[str]] = {}
    for page in pages:
        for plot in page.plots:
            if FIGURE_TYPE_REGISTRY[plot.type_id]["family"] != "rf":
                continue
            value_mode = str(plot.settings["valueMode"])
            key = json.dumps(
                plot.settings,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            seen = recipe_keys.setdefault(value_mode, set())
            if key in seen:
                continue
            seen.add(key)
            recipes.setdefault(value_mode, []).append(plot.settings)
    if not recipes:
        return ()

    bounds = {
        value_mode: [math.inf, -math.inf]
        for value_mode in recipes
    }
    expected_pool = record.cache.metadata["unitPool"]
    for unit_id in unit_ids:
        unit_index, counts = unit_loader(int(unit_id))
        try:
            if expected_pool[unit_index] != int(unit_id):
                raise FigureExportValidationError(
                    "Dataset unit index changed while computing shared RF scale"
                )
            for value_mode, settings_list in recipes.items():
                low, high = bounds[value_mode]
                for settings in settings_list:
                    matrix, _x, _y, _time_bounds = _prepared_response(
                        counts,
                        record.cache.metadata,
                        settings,
                    )
                    finite = matrix[np.isfinite(matrix)]
                    if finite.size:
                        low = min(low, float(np.min(finite)))
                        high = max(high, float(np.max(finite)))
                bounds[value_mode] = [low, high]
        finally:
            del counts

    result: list[dict[str, Any]] = []
    for value_mode in recipes:
        low, high = bounds[value_mode]
        if not math.isfinite(low) or not math.isfinite(high):
            low, high = 0.0, 1.0
        result.append(
            {
                "valueMode": value_mode,
                "valueUnit": "spikes" if value_mode == VALUE_MODE_COUNT else "Hz",
                "vmin": float(low),
                "vmax": float(high),
                "unitIds": [int(value) for value in unit_ids],
            }
        )
    return tuple(result)


def shared_waveform_scales(
    waveform: WaveformArtifactStore | None,
    pages: Sequence[FigurePage],
    unit_ids: Sequence[int],
) -> tuple[dict[str, Any], ...]:
    """Freeze every selected-unit waveform scale used by the page recipe."""

    if waveform is None:
        return ()
    modes: list[str] = []
    for page in pages:
        for plot in page.plots:
            if FIGURE_TYPE_REGISTRY[plot.type_id]["family"] != "waveform":
                continue
            mode = str(plot.settings["channelMode"])
            if mode not in modes:
                modes.append(mode)
    result: list[dict[str, Any]] = []
    frozen_unit_ids = [int(value) for value in unit_ids]
    for mode in modes:
        limit = shared_amplitude_limit(waveform, frozen_unit_ids, mode)
        if limit is None:
            continue
        amplitude = abs(float(limit))
        result.append(
            {
                "vmin": -amplitude,
                "vmax": amplitude,
                "unit": "µV",
                "unitIds": frozen_unit_ids,
                "baselineEndMs": DEFAULT_BASELINE_END_MS,
                "channelMode": mode,
            }
        )
    return tuple(result)


def _prepared_temporal(
    counts: np.ndarray, metadata: Mapping[str, Any], settings: Mapping[str, Any]
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[tuple[int, int]], list[tuple[int, int]]]:
    groups = _time_groups(metadata, settings["timeResolutionMs"])
    x_groups = _axis_groups(metadata["shape"][2], settings["xBins"])
    y_groups = _axis_groups(metadata["shape"][1], settings["yBins"])
    if settings["flipY"]:
        y_groups.reverse()
    edges_ms = np.asarray(metadata["timeBinEdges"], dtype=np.float64) * 1000.0
    n_bins = int(metadata["shape"][3])
    histograms = np.zeros((len(y_groups), len(x_groups), n_bins), dtype=np.float64)
    for display_y, y_group in enumerate(y_groups):
        for display_x, x_group in enumerate(x_groups):
            histogram, source_pixel_count = _spatial_group_histogram(
                counts, metadata, y_group, x_group
            )
            histograms[display_y, display_x, :] = histogram / max(
                1, source_pixel_count
            )
    if settings["smoothRadius"] > 0:
        output = np.zeros_like(histograms)
        for bin_index in range(n_bins):
            temporal_slice = histograms[:, :, bin_index].tolist()
            smoothed = _smooth_matrix(temporal_slice, settings["smoothRadius"])
            output[:, :, bin_index] = np.asarray(smoothed, dtype=np.float64)
        histograms = output

    delays = np.full((len(y_groups), len(x_groups)), np.nan)
    entropy = np.zeros_like(delays)
    safe_floor = max(0.0, float(settings["responseFloor"]))
    for display_y in range(len(y_groups)):
        for display_x in range(len(x_groups)):
            histogram = histograms[display_y, display_x, :]
            total = float(histogram.sum())
            if total > safe_floor:
                rates = [
                    float(histogram[start : end + 1].sum())
                    / ((edges_ms[end + 1] - edges_ms[start]) / 1000.0)
                    if edges_ms[end + 1] > edges_ms[start]
                    else 0.0
                    for start, end in groups
                ]
                peak_index = int(np.argmax(rates))
                peak_start, peak_end = groups[peak_index]
                delays[display_y, display_x] = (edges_ms[peak_start] + edges_ms[peak_end + 1]) / 2.0
            if total > 0:
                positive = histogram[histogram > 0] / total
                value = -float(np.sum(positive * np.log(positive)))
                entropy[display_y, display_x] = (
                    value / math.log(len(histogram))
                    if len(histogram) > 1
                    else 0.0
                )

    response_settings = {
        "rfStartMs": float(edges_ms[0]),
        "rfEndMs": float(edges_ms[-1]),
        "valueMode": settings["valueMode"],
        "xBins": settings["xBins"],
        "yBins": settings["yBins"],
        "smoothRadius": settings["smoothRadius"],
        "flipY": settings["flipY"],
    }
    response, _response_x, _response_y, _response_bounds = _prepared_response(
        counts, metadata, response_settings
    )
    return delays, entropy, response, x_groups, y_groups


def _rgb_values(response: np.ndarray, delays: np.ndarray, entropy: np.ndarray) -> np.ndarray:
    rgba = np.zeros((*response.shape, 4), dtype=np.float64)
    response_values = response[np.isfinite(response)]
    response_high = float(np.max(response_values)) if response_values.size else 1.0
    delay_values = delays[np.isfinite(delays)]
    delay_low = float(np.min(delay_values)) if delay_values.size else 0.0
    delay_high = float(np.max(delay_values)) if delay_values.size else delay_low + 1.0
    rgba[..., 0] = np.nan_to_num(response / max(response_high, np.finfo(float).eps), nan=0.0)
    rgba[..., 1] = np.nan_to_num(
        (delays - delay_low) / max(delay_high - delay_low, np.finfo(float).eps), nan=0.0
    )
    rgba[..., 2] = np.nan_to_num(entropy, nan=0.0)
    rgba[..., :3] = np.clip(rgba[..., :3], 0.0, 1.0)
    rgba[..., 3] = 1.0
    missing = ~np.isfinite(response) & ~np.isfinite(delays)
    rgba[missing, :3] = 0.9
    return rgba


def _normalize_hd_display_bins(value: int) -> int:
    requested = max(1, min(180, int(value)))
    return max(candidate for candidate in range(1, requested + 1) if 180 % candidate == 0)


def _hd_curve(
    data: TuningCurveData, cluster_id: int, settings: Mapping[str, Any]
) -> tuple[np.ndarray, np.ndarray] | None:
    unit = data.units_by_id.get(cluster_id)
    if unit is None:
        return None
    raw_counts = np.asarray(unit.spike_counts, dtype=np.float64)
    occupancy = np.asarray(data.occupancy_time_s, dtype=np.float64)
    display_bins = _normalize_hd_display_bins(int(settings["displayBins"]))
    group_size = 180 // display_bins
    if settings["smoothing"]:
        # Match the live Web view and Python viewer: smooth the 2-degree raw
        # observations before display-bin aggregation, then derive the rate.
        # Smoothing a pre-divided rate biases bins when occupancy is uneven.
        sigma_bins = settings["sigmaDeg"] / (360.0 / 180.0)
        radius = math.floor(sigma_bins * 4.0 + 0.5)
        offsets = np.arange(-radius, radius + 1, dtype=int)
        weights = np.exp(-0.5 * np.square(offsets.astype(np.float64) / sigma_bins))
        weights /= weights.sum()
        smoothed_counts = np.zeros_like(raw_counts)
        smoothed_occupancy = np.zeros_like(occupancy)
        for offset, weight in zip(offsets, weights):
            smoothed_counts += np.roll(raw_counts, -int(offset)) * weight
            smoothed_occupancy += np.roll(occupancy, -int(offset)) * weight
        raw_counts = smoothed_counts
        occupancy = smoothed_occupancy
    counts = raw_counts.reshape(display_bins, group_size).sum(axis=1)
    exposure = occupancy.reshape(display_bins, group_size).sum(axis=1)
    minimum_exposure = 1e-12 if settings["smoothing"] else 0.0
    rates = np.divide(
        counts,
        exposure,
        out=np.full(display_bins, np.nan),
        where=exposure > minimum_exposure,
    )
    centers = (np.arange(display_bins, dtype=np.float64) + 0.5) * (360.0 / display_bins)
    return centers, rates


class FigurePageRenderer:
    def __init__(
        self,
        record: DatasetRecord,
        *,
        tuning: TuningCurveData | None,
        probe: Mapping[str, Any] | None,
        waveform: WaveformArtifactStore | None = None,
        tuning_error: str | None = None,
        probe_error: str | None = None,
        waveform_error: str | None = None,
        waveform_channel_mode: str = DEFAULT_WAVEFORM_CHANNEL_MODE,
        waveform_unit_ids: Sequence[int] = (),
        shared_rf_scale_values: Sequence[Mapping[str, Any]] = (),
        shared_waveform_scale_values: Sequence[Mapping[str, Any]] = (),
    ):
        self.record = record
        self.metadata = record.cache.metadata
        self.tuning = tuning
        self.probe = probe
        self.waveform = waveform
        self.tuning_error = tuning_error
        self.probe_error = probe_error
        self.waveform_error = waveform_error
        self.waveform_channel_mode = waveform_channel_mode
        self.waveform_unit_ids = tuple(int(value) for value in waveform_unit_ids)
        self.shared_rf_scales = {
            str(scale["valueMode"]): (
                float(scale["vmin"]),
                float(scale["vmax"]),
                str(scale["valueUnit"]),
            )
            for scale in shared_rf_scale_values
        }
        self._waveform_limits: dict[str, float | None] = {
            str(scale["channelMode"]): max(
                abs(float(scale["vmin"])),
                abs(float(scale["vmax"])),
            )
            for scale in shared_waveform_scale_values
        }
        self.shared_renderer = PillowFigureRenderer()

    def _waveform_amplitude_limit(self, mode: str, cluster_id: int) -> float | None:
        if self.waveform is None:
            return None
        if mode not in self._waveform_limits:
            unit_ids = self.waveform_unit_ids or (cluster_id,)
            self._waveform_limits[mode] = shared_amplitude_limit(
                self.waveform,
                unit_ids,
                mode,
            )
        return self._waveform_limits[mode]

    def _total_degrees(self) -> float:
        positions = [float(value) for value in self.metadata["xPositions"]]
        if len(positions) <= 1:
            return 360.0
        differences = np.diff(np.asarray(positions, dtype=np.float64))
        step = float(np.mean(differences))
        if abs(step) > 1e-9 and np.all(np.abs(differences - step) < 1e-6):
            total = abs(step) * len(positions)
        else:
            total = abs(positions[-1] - positions[0])
        return min(360.0, max(total, np.finfo(float).eps))

    def _map_options(
        self,
        settings: Mapping[str, Any],
        x_groups: Sequence[tuple[int, int]],
        y_groups: Sequence[tuple[int, int]],
    ) -> dict[str, Any]:
        x_positions = [float(value) for value in self.metadata["xPositions"]]
        y_positions = [float(value) for value in self.metadata["yPositions"]]
        return {
            "palette": _PALETTE_NAMES[settings["palette"]],
            "total_degrees": self._total_degrees(),
            # ``MATLAB row 1 inner`` is source-row based, so a flipped
            # Cartesian display must be reversed back before assigning polar
            # radii. ``Display bottom inner`` is display-row based and always
            # reverses the prepared display order.
            "reverse_rings": (
                settings["polarRadius"] != "MATLAB row 1 inner"
                or bool(settings["flipY"])
            ),
            "inner_blank_rows": 4,
            "clockwise": True,
            "x_values": [
                (x_positions[start] + x_positions[end]) / 2.0
                for start, end in x_groups
            ],
            "y_values": [
                (y_positions[start] + y_positions[end]) / 2.0
                for start, end in y_groups
            ],
            "x_unit": "°",
            "y_unit": "°",
            "show_axes": True,
        }

    def _spatial_context(
        self,
        settings: Mapping[str, Any],
        *,
        prefix: str,
    ) -> str:
        n_y = int(self.metadata["shape"][1])
        n_x = int(self.metadata["shape"][2])
        return (
            f"{prefix}; {n_x}×{n_y} to "
            f"{int(settings['xBins'])}×{int(settings['yBins'])}; "
            f"smooth r={int(settings['smoothRadius'])}"
        )

    @staticmethod
    def _unavailable(
        plot: FigurePlot, reason: str, placeholders: list[str]
    ) -> SharedPlotSpec:
        placeholders.append(f"{plot.type_id}: {reason}")
        return SharedPlotSpec(
            plot.type_id,
            options={"unavailable_message": reason},
        )

    def _timeline_data(
        self, counts: np.ndarray, settings: Mapping[str, Any]
    ) -> tuple[dict[str, Any], str]:
        groups = _time_groups(self.metadata, settings["timeResolutionMs"])
        edges_ms = np.asarray(self.metadata["timeBinEdges"], dtype=np.float64) * 1000.0
        start_ms = edges_ms[0] if settings["timelineStartMs"] is None else settings["timelineStartMs"]
        end_ms = edges_ms[-1] if settings["timelineEndMs"] is None else settings["timelineEndMs"]
        selection_start, selection_end = _time_group_range_for_ms(
            self.metadata,
            groups,
            float(start_ms),
            float(end_ms),
        )
        low = float(edges_ms[groups[selection_start][0]])
        high = float(edges_ms[groups[selection_end][1] + 1])
        active_index = (
            None
            if settings["activeTimeCenterMs"] is None
            else _time_group_for_ms(
                self.metadata,
                groups,
                float(settings["activeTimeCenterMs"]),
            )
        )
        projection = settings["spatialProjection"]
        if projection is None:
            selected_cell = (
                0,
                int(self.metadata["shape"][1]) - 1,
                0,
                int(self.metadata["shape"][2]) - 1,
            )
            label = "all spatial bins"
        else:
            selected_cell = (
                projection["yStart"],
                projection["yEnd"],
                projection["xStart"],
                projection["xEnd"],
            )
            label = (
                f"y {projection['yStart']}–{projection['yEnd']}, "
                f"x {projection['xStart']}–{projection['xEnd']}"
            )
        centers: list[float] = []
        totals: list[float] = []
        selected: list[float] = []
        spatial_frames: list[list[list[float]]] = []
        occupancy = np.asarray(self.metadata["occupancyTimeSec"], dtype=np.float64)
        all_occupancy = float(occupancy[occupancy > 0].sum())
        for start, end in groups:
            center = (edges_ms[start] + edges_ms[end + 1]) / 2.0
            selected_value = _group_response_value(
                counts,
                self.metadata,
                selected_cell,
                (start, end),
                settings["valueMode"],
            )
            total_value = float(np.asarray(counts[..., start : end + 1]).sum())
            if settings["valueMode"] != VALUE_MODE_COUNT:
                total_value = (
                    total_value / all_occupancy
                    if all_occupancy > 0
                    else 0.0
                )
            frame_settings = {
                "rfStartMs": edges_ms[start],
                "rfEndMs": edges_ms[end + 1],
                "valueMode": settings["valueMode"],
                "xBins": settings["xBins"],
                "yBins": settings["yBins"],
                "smoothRadius": settings["smoothRadius"],
                "flipY": settings["flipY"],
                "palette": settings["palette"],
                "polarRadius": settings["polarRadius"],
            }
            frame, _x, _y, _bounds = _prepared_response(
                counts, self.metadata, frame_settings
            )
            centers.append(center)
            totals.append(total_value)
            selected.append(0.0 if selected_value is None else selected_value)
            spatial_frames.append(frame.tolist())
        payload: dict[str, Any] = {
            "times": centers,
            "time_edges": [
                float(edges_ms[start]) for start, _end in groups
            ] + [float(edges_ms[groups[-1][1] + 1])],
            "time_unit": "ms",
            "value_unit": (
                "spikes"
                if settings["valueMode"] == VALUE_MODE_COUNT
                else "Hz"
            ),
            "totals": totals,
            "selected": selected,
            "frames": spatial_frames,
            "selection_start_index": selection_start,
            "selection_end_index": selection_end,
        }
        if active_index is not None:
            payload["active_index"] = active_index
        return payload, f"Timeline {low:g}–{high:g} ms — {label}"

    def _shared_spec(
        self,
        cluster_id: int,
        counts: np.ndarray,
        plot: FigurePlot,
        placeholders: list[str],
    ) -> SharedPlotSpec:
        settings = plot.settings
        family = FIGURE_TYPE_REGISTRY[plot.type_id]["family"]
        if family == "rf":
            matrix, x_groups, y_groups, bounds = _prepared_response(
                counts, self.metadata, settings
            )
            options = self._map_options(settings, x_groups, y_groups)
            scale = self.shared_rf_scales.get(str(settings["valueMode"]))
            if scale is not None:
                options.update(
                    {
                        "vmin": scale[0],
                        "vmax": scale[1],
                        "value_unit": scale[2],
                        "show_colorbar": True,
                    }
                )
            value_unit = (
                "spikes" if settings["valueMode"] == VALUE_MODE_COUNT else "Hz"
            )
            options["subtitle"] = self._spatial_context(
                settings,
                prefix=(
                    f"{bounds[0]:g} to {bounds[1]:g} ms; "
                    f"{settings['valueMode']} ({value_unit})"
                ),
            )
            return SharedPlotSpec(
                plot.type_id,
                matrix.tolist(),
                title="RF map",
                options=options,
            )
        if family in {"delay", "rgb"}:
            delays, entropy, response, x_groups, y_groups = _prepared_temporal(
                counts, self.metadata, settings
            )
            options = self._map_options(settings, x_groups, y_groups)
            edges_ms = np.asarray(
                self.metadata["timeBinEdges"], dtype=np.float64
            ) * 1000.0
            options["subtitle"] = self._spatial_context(
                settings,
                prefix=f"full timeline {edges_ms[0]:g} to {edges_ms[-1]:g} ms",
            )
            if family == "delay":
                options["palette"] = "delay"
                options["vmin"] = float(edges_ms[0])
                options["vmax"] = float(edges_ms[-1])
                options["value_unit"] = "ms"
                options["show_colorbar"] = True
                return SharedPlotSpec(
                    plot.type_id,
                    delays.tolist(),
                    title="Peak count-rate interval center (ms)",
                    options=options,
                )
            colors = _rgb_values(response, delays, entropy)
            rgb_data: list[list[list[float] | None]] = []
            for y_index, row in enumerate(colors):
                rgb_row: list[list[float] | None] = []
                for x_index, color in enumerate(row):
                    if not np.isfinite(response[y_index, x_index]) and not np.isfinite(
                        delays[y_index, x_index]
                    ):
                        rgb_row.append(None)
                    else:
                        rgb_row.append(color[:3].tolist())
                rgb_data.append(rgb_row)
            return SharedPlotSpec(
                plot.type_id,
                rgb_data,
                title="RGB: response / delay / entropy",
                options={**options, "show_colorbar": False},
            )
        if family == "timeline":
            data, title = self._timeline_data(counts, settings)
            x_groups = _axis_groups(self.metadata["shape"][2], settings["xBins"])
            y_groups = _axis_groups(self.metadata["shape"][1], settings["yBins"])
            if settings["flipY"]:
                y_groups.reverse()
            options = self._map_options(settings, x_groups, y_groups)
            options["polar"] = settings["polarLayout"]
            options["value_unit"] = data["value_unit"]
            options["time_unit"] = "ms"
            return SharedPlotSpec(plot.type_id, data, title=title, options=options)
        if family == "hd":
            if self.tuning is None:
                reason = self.tuning_error or "HD tuning data are unavailable for this dataset."
                return self._unavailable(plot, reason, placeholders)
            curve = _hd_curve(self.tuning, cluster_id, settings)
            if curve is None:
                return self._unavailable(
                    plot,
                    f"No HD tuning curve for cluster {cluster_id}.",
                    placeholders,
                )
            angles, rates = curve
            valid = np.isfinite(rates)
            if not np.any(valid):
                return self._unavailable(
                    plot,
                    f"HD tuning curve for cluster {cluster_id} has no occupied bins.",
                    placeholders,
                )
            return SharedPlotSpec(
                plot.type_id,
                {
                    "angles_deg": angles[valid].tolist(),
                    "rates": rates[valid].tolist(),
                },
                options={
                    "color": "#7c3aed",
                    "clockwise": False,
                    "x_unit": "°",
                    "y_unit": "Hz",
                    "show_axes": True,
                },
            )
        if family == "probe":
            if self.probe is None:
                reason = self.probe_error or "Probe geometry is unavailable for this dataset."
                return self._unavailable(plot, reason, placeholders)
            probe_units = self.probe.get("units", [])
            if not any(unit.get("unitId") == cluster_id for unit in probe_units):
                return self._unavailable(
                    plot,
                    f"Probe geometry has no position for cluster {cluster_id}.",
                    placeholders,
                )
            points: list[dict[str, Any]] = []
            for channel in self.probe.get("channels", []):
                points.append(
                    {
                        "x": channel["x"],
                        "y": channel["y"],
                        "label": "",
                        "color": "#94a3b8",
                    }
                )
            selected_unit = next(
                unit for unit in probe_units if unit["unitId"] == cluster_id
            )
            selected_x = selected_unit.get("x")
            selected_y = selected_unit.get("y")
            if (selected_x is None) != (selected_y is None):
                raise FigureExportValidationError(
                    f"Probe geometry for cluster {cluster_id} has incomplete coordinates."
                )
            missing_position = selected_x is None and selected_y is None
            if not missing_position:
                points.append(
                    {
                        "x": selected_unit["x"],
                        "y": selected_unit["y"],
                        "label": str(selected_unit["unitId"]),
                        "color": "#dc2626",
                    }
                )
            return SharedPlotSpec(
                plot.type_id,
                {
                    "points": points,
                    **({"missingPosition": True} if missing_position else {}),
                },
                title=f"{self.probe.get('probe', 'Probe')} layout",
                options={
                    "coordinate_unit": "µm",
                    "show_axes": True,
                    "show_scale_bar": True,
                },
            )
        if family == "waveform":
            if self.waveform is None:
                reason = self.waveform_error or (
                    "Local average waveform data are unavailable for this dataset."
                )
                return self._unavailable(plot, reason, placeholders)
            mode = str(settings.get("channelMode", self.waveform_channel_mode))
            try:
                payload = self.waveform.payload_for(cluster_id, mode)
            except KeyError:
                return self._unavailable(
                    plot,
                    f"No local average waveform for cluster {cluster_id}.",
                    placeholders,
                )
            except (OSError, WaveformArtifactError) as exc:
                return self._unavailable(
                    plot,
                    f"Local average waveform could not be loaded: {exc}",
                    placeholders,
                )
            amplitude = self._waveform_amplitude_limit(mode, cluster_id)
            if amplitude is None:
                amplitude = payload.amplitude_limit_uv
            return SharedPlotSpec(
                plot.type_id,
                {
                    "matrix": payload.values_uv.tolist(),
                    "times_ms": payload.time_ms.tolist(),
                    "time_edges_ms": payload.time_edges_ms.tolist(),
                    "channel_labels": [
                        f"ch {channel.channel_id}" for channel in payload.channels
                    ],
                    "best_channel_row": payload.best_channel_row,
                },
                title="Local average waveform",
                options={
                    "palette": "rdbu_r",
                    "vmin": -amplitude,
                    "vmax": amplitude,
                    "value_unit": "µV",
                    "show_axes": True,
                    "show_colorbar": True,
                    "subtitle": (
                        f"best + nearest {max(0, len(payload.channels) - 1)}; "
                        f"{'Same x column' if mode == 'same_x_column' else 'Same shank'}; "
                        f"baseline ≤ {payload.baseline_end_ms:g} ms"
                    ),
                },
            )
        raise FigureExportValidationError(f"Unknown renderer family: {family}")

    def build_image(
        self,
        cluster_id: int,
        unit_index: int,
        counts: np.ndarray,
        page: FigurePage,
    ) -> tuple[Image.Image, tuple[str, ...]]:
        placeholders: list[str] = []
        plots = tuple(
            self._shared_spec(cluster_id, counts, plot, placeholders)
            for plot in page.plots
        )
        heading = page.title.strip() or "RF Mapping export"
        shared_page = SharedExportPage(f"{heading} (original index {unit_index})", plots)
        return (
            self.shared_renderer.render_page(cluster_id, shared_page),
            tuple(placeholders),
        )

    def render_png(
        self,
        cluster_id: int,
        unit_index: int,
        counts: np.ndarray,
        page: FigurePage,
    ) -> RenderedPage:
        image, placeholders = self.build_image(cluster_id, unit_index, counts, page)
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=False, compress_level=6)
        image.close()
        payload = output.getvalue()
        return RenderedPage(payload, hashlib.sha256(payload).hexdigest(), placeholders)


def _svg_with_embedded_png(png: bytes) -> bytes:
    """Wrap the exact renderer PNG in SVG without re-encoding any pixels."""

    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
    encoded = base64.b64encode(png).decode("ascii")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">\n'
        f'  <image width="{width}" height="{height}" '
        f'href="data:image/png;base64,{encoded}"/>\n'
        '</svg>\n'
    ).encode("utf-8")


def _safe_relative_directory(root: Path, requested: str) -> tuple[Path, str]:
    try:
        root_stat = root.lstat()
        canonical_root = root.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        raise FigureOutputPathError(f"Figure destination root is unavailable: {root}") from exc
    if root_stat and root.is_symlink():
        raise FigureOutputPathError("Figure destination root must not be a symbolic link")
    if not canonical_root.is_dir():
        raise FigureOutputPathError("Figure destination root is not a directory")
    if not isinstance(requested, str) or "\x00" in requested:
        raise FigureOutputPathError("Destination directory must be a relative path")
    normalized = requested.replace("\\", "/")
    if requested.startswith(("/", "\\")):
        raise FigureOutputPathError("Destination directory must be relative to the configured root")
    if normalized == "":
        return canonical_root, ""
    raw_parts = normalized.split("/")
    if any(
        part in {"", ".", ".."} or part.startswith(".") for part in raw_parts
    ):
        raise FigureOutputPathError(
            "Destination directory contains an unsafe path component"
        )
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or any(part in {"", ".", ".."} or part.startswith(".") for part in relative.parts):
        raise FigureOutputPathError("Destination directory contains an unsafe path component")
    current = canonical_root
    for part in relative.parts:
        candidate = current / part
        try:
            stat = candidate.lstat()
        except FileNotFoundError as exc:
            raise FigureOutputPathError("Destination directory must already exist") from exc
        except OSError as exc:
            raise FigureOutputPathError("Destination directory is unavailable") from exc
        if candidate.is_symlink():
            raise FigureOutputPathError("Destination directory must not contain symbolic links")
        current = candidate.resolve(strict=True)
        if not current.is_dir() or not is_within(current, canonical_root):
            raise FigureOutputPathError("Destination directory escapes the configured root")
    return current, relative.as_posix()


def list_figure_directories(root: Path, requested: str) -> dict[str, Any]:
    directory, relative = _safe_relative_directory(root, requested)
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc:
        raise FigureOutputPathError("Unable to list the destination directory") from exc
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            if child.is_symlink() or not child.is_dir():
                continue
            resolved = child.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not is_within(resolved, root.resolve(strict=True)):
            continue
        child_relative = f"{relative}/{child.name}" if relative else child.name
        entries.append(
            {
                "name": child.name,
                "path": child_relative,
                "writable": os.access(resolved, os.W_OK | os.X_OK),
            }
        )
    return {
        "path": relative,
        "writable": os.access(directory, os.W_OK | os.X_OK),
        "entries": entries,
    }


def _safe_base_name(value: str) -> str:
    if not isinstance(value, str) or not _BASE_NAME_RE.fullmatch(value):
        raise FigureOutputPathError(
            "baseName must be 1–128 safe filename characters and may not begin with a dot"
        )
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise FigureOutputPathError("baseName must not contain a path")
    return value


def _web_export_spec(jobs: Sequence[ExpandedPage]) -> dict[str, Any]:
    pages_by_index: dict[int, FigurePage] = {}
    for job in jobs:
        pages_by_index.setdefault(job.page_index, job.page)
    return {
        "pages": [
            {
                "pageIndex": page_index,
                "title": page.title,
                "plots": [
                    {"type": plot.type_id, "settings": plot.settings}
                    for plot in page.plots
                ],
            }
            for page_index, page in sorted(pages_by_index.items())
        ],
        "jobs": [
            {
                "outputIndex": job.output_index,
                "clusterId": job.cluster_id,
                "pageIndex": job.page_index,
            }
            for job in jobs
        ],
    }


def _web_manifest_header(
    record: DatasetRecord,
    jobs: Sequence[ExpandedPage],
    order: str,
    provenance: Mapping[str, Any],
    output_format: str = "png",
) -> dict[str, Any]:
    return {
        "manifestVersion": FIGURE_MANIFEST_VERSION,
        "specVersion": FIGURE_SPEC_VERSION,
        "producer": FIGURE_EXPORT_PRODUCER,
        "format": output_format,
        "order": order,
        "source": str(record.source),
        "sourceSignature": dict(record.source_signature),
        "provenance": dict(provenance),
        "spec": _web_export_spec(jobs),
    }


def _web_page_metadata(
    record: DatasetRecord,
    job: ExpandedPage,
    output_format: str = "png",
) -> dict[str, Any]:
    unit_index = record.cache.metadata["unitPool"].index(job.cluster_id)
    return {
        "outputIndex": job.output_index,
        "clusterId": job.cluster_id,
        "unitIndex": unit_index,
        "pageIndex": job.page_index,
        "title": job.page.title,
        "file": (
            f"{job.output_index + 1:04d}_unit_{unit_index:03d}_"
            f"cluster_{job.cluster_id}_page_{job.page_index + 1:02d}.{output_format}"
        ),
    }


def _validate_web_spec(spec: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(spec, dict) or set(spec) != {"pages", "jobs"}:
        raise FigureOutputPathError("Figure export manifest has an invalid spec")
    pages = spec["pages"]
    jobs = spec["jobs"]
    if not isinstance(pages, list) or not pages or not isinstance(jobs, list) or not jobs:
        raise FigureOutputPathError("Figure export manifest spec is empty")
    for page_index, page in enumerate(pages):
        if not isinstance(page, dict) or set(page) != {"pageIndex", "title", "plots"}:
            raise FigureOutputPathError("Figure export manifest has an invalid page spec")
        if page["pageIndex"] != page_index or not isinstance(page["title"], str):
            raise FigureOutputPathError("Figure export manifest page order is invalid")
        plots = page["plots"]
        if not isinstance(plots, list) or not plots:
            raise FigureOutputPathError("Figure export manifest page has no plots")
        for plot in plots:
            if (
                not isinstance(plot, dict)
                or set(plot) != {"type", "settings"}
                or plot["type"] not in FIGURE_TYPE_REGISTRY
                or not isinstance(plot["settings"], dict)
            ):
                raise FigureOutputPathError("Figure export manifest has an invalid plot spec")
    for output_index, job in enumerate(jobs):
        if not isinstance(job, dict) or set(job) != {
            "outputIndex",
            "clusterId",
            "pageIndex",
        }:
            raise FigureOutputPathError("Figure export manifest has an invalid job spec")
        if (
            job["outputIndex"] != output_index
            or isinstance(job["clusterId"], bool)
            or not isinstance(job["clusterId"], int)
            or isinstance(job["pageIndex"], bool)
            or not isinstance(job["pageIndex"], int)
            or not 0 <= job["pageIndex"] < len(pages)
        ):
            raise FigureOutputPathError("Figure export manifest job order is invalid")
    return pages, jobs


def _is_finite_json(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_finite_json(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_finite_json(item)
            for key, item in value.items()
        )
    return False


def _validate_input_metadata(value: Any) -> None:
    expected = {
        "path",
        "sha256",
        "sizeBytes",
        "device",
        "inode",
        "mtimeNs",
        "ctimeNs",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise FigureOutputPathError("Figure export input provenance is invalid")
    if (
        not isinstance(value["path"], str)
        or not value["path"]
        or not isinstance(value["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None
    ):
        raise FigureOutputPathError("Figure export input provenance is invalid")
    for key in expected - {"path", "sha256"}:
        if isinstance(value[key], bool) or not isinstance(value[key], int):
            raise FigureOutputPathError("Figure export input provenance is invalid")
    if value["sizeBytes"] < 0:
        raise FigureOutputPathError("Figure export input provenance is invalid")


def _validate_web_provenance(value: Any) -> None:
    required = {
        "provenanceVersion",
        "application",
        "source",
        "snapshot",
        "companions",
        "companionStatus",
        "renderingContract",
    }
    optional = {
        "sharedRFScales",
        "sharedWaveformScale",
        "sharedWaveformScales",
    }
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or set(value) - required - optional
    ):
        raise FigureOutputPathError("Figure export provenance is invalid")
    if (
        type(value["provenanceVersion"]) is not int
        or value["provenanceVersion"] != FIGURE_PROVENANCE_VERSION
    ):
        raise FigureOutputPathError("Figure export provenance is invalid")
    application = value["application"]
    if (
        not isinstance(application, dict)
        or set(application) != {"name", "version", "edition"}
        or any(not isinstance(item, str) or not item for item in application.values())
    ):
        raise FigureOutputPathError("Figure export application provenance is invalid")
    _validate_input_metadata(value["source"])
    companions = value["companions"]
    if not isinstance(companions, list):
        raise FigureOutputPathError("Figure export companion provenance is invalid")
    for item in companions:
        if not isinstance(item, dict) or "kind" not in item:
            raise FigureOutputPathError("Figure export companion provenance is invalid")
        kind = item["kind"]
        if kind not in {"headDirection", "probeGeometry", "waveform"}:
            raise FigureOutputPathError("Figure export companion provenance is invalid")
        _validate_input_metadata({key: nested for key, nested in item.items() if key != "kind"})
    status = value["companionStatus"]
    if (
        not isinstance(status, dict)
        or set(status) != {"headDirection", "probeGeometry", "waveform"}
        or any(not isinstance(item, str) or not item for item in status.values())
    ):
        raise FigureOutputPathError("Figure export companion status is invalid")
    rendering = value["renderingContract"]
    if (
        not isinstance(rendering, dict)
        or set(rendering) not in (
            {"preview", "publication"},
            {"preview", "publication", "svg"},
        )
        or any(not isinstance(item, str) or not item for item in rendering.values())
    ):
        raise FigureOutputPathError("Figure export rendering provenance is invalid")
    shared_scales = value.get("sharedRFScales", [])
    if not isinstance(shared_scales, list):
        raise FigureOutputPathError("Figure export shared RF scale provenance is invalid")
    for scale in shared_scales:
        if not isinstance(scale, dict) or set(scale) != {
            "valueMode",
            "valueUnit",
            "vmin",
            "vmax",
            "unitIds",
        }:
            raise FigureOutputPathError("Figure export shared RF scale provenance is invalid")
        if (
            scale["valueMode"] not in VALUE_MODES
            or scale["valueUnit"] not in {"spikes", "Hz"}
            or isinstance(scale["vmin"], bool)
            or not isinstance(scale["vmin"], (int, float))
            or not math.isfinite(float(scale["vmin"]))
            or isinstance(scale["vmax"], bool)
            or not isinstance(scale["vmax"], (int, float))
            or not math.isfinite(float(scale["vmax"]))
            or float(scale["vmax"]) < float(scale["vmin"])
            or not isinstance(scale["unitIds"], list)
            or not scale["unitIds"]
            or any(type(unit_id) is not int for unit_id in scale["unitIds"])
            or len(set(scale["unitIds"])) != len(scale["unitIds"])
        ):
            raise FigureOutputPathError("Figure export shared RF scale provenance is invalid")
    waveform_scales = value.get("sharedWaveformScales", [])
    if not isinstance(waveform_scales, list):
        raise FigureOutputPathError(
            "Figure export shared waveform scale provenance is invalid"
        )
    expected_waveform_keys = {
        "vmin",
        "vmax",
        "unit",
        "unitIds",
        "baselineEndMs",
        "channelMode",
    }
    for scale in waveform_scales:
        if not isinstance(scale, dict) or set(scale) != expected_waveform_keys:
            raise FigureOutputPathError(
                "Figure export shared waveform scale provenance is invalid"
            )
        if (
            scale["unit"] != "µV"
            or scale["channelMode"] not in WAVEFORM_CHANNEL_MODES
            or isinstance(scale["vmin"], bool)
            or not isinstance(scale["vmin"], (int, float))
            or not math.isfinite(float(scale["vmin"]))
            or isinstance(scale["vmax"], bool)
            or not isinstance(scale["vmax"], (int, float))
            or not math.isfinite(float(scale["vmax"]))
            or float(scale["vmin"]) > 0.0
            or float(scale["vmax"]) < 0.0
            or not math.isclose(
                abs(float(scale["vmin"])),
                abs(float(scale["vmax"])),
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            or isinstance(scale["baselineEndMs"], bool)
            or not isinstance(scale["baselineEndMs"], (int, float))
            or not math.isfinite(float(scale["baselineEndMs"]))
            or not isinstance(scale["unitIds"], list)
            or not scale["unitIds"]
            or any(type(unit_id) is not int for unit_id in scale["unitIds"])
            or len(set(scale["unitIds"])) != len(scale["unitIds"])
        ):
            raise FigureOutputPathError(
                "Figure export shared waveform scale provenance is invalid"
            )
    if len({scale["channelMode"] for scale in waveform_scales}) != len(
        waveform_scales
    ):
        raise FigureOutputPathError(
            "Figure export shared waveform scale provenance is invalid"
        )
    singular_waveform_scale = value.get("sharedWaveformScale")
    if singular_waveform_scale is not None and (
        len(waveform_scales) != 1 or singular_waveform_scale != waveform_scales[0]
    ):
        raise FigureOutputPathError(
            "Figure export shared waveform scale provenance is invalid"
        )
    if not isinstance(value["snapshot"], dict) or not _is_finite_json(value["snapshot"]):
        raise FigureOutputPathError("Figure export snapshot provenance is invalid")


def _validate_web_export_directory(
    parent: SharedParentDirectory,
    name: str,
    *,
    expected_header: Mapping[str, Any] | None,
    expected_pages: Sequence[Mapping[str, Any]] | None,
) -> SharedEntryIdentity:
    """Prove a directory is a complete output produced by this Web exporter."""

    descriptor, identity = _shared_open_directory_entry(parent, name)
    try:
        try:
            raw = _shared_read_regular_bytes_at(descriptor, "manifest.json")
            manifest = json.loads(raw.decode("utf-8"))
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FigureOutputPathError(
                f"Refusing to overwrite unverified directory {parent.path / name}: "
                "manifest.json is missing or invalid"
            ) from exc
        legacy_keys = {
            "specVersion",
            "producer",
            "format",
            "order",
            "source",
            "sourceSignature",
            "spec",
            "pages",
        }
        current_keys = legacy_keys | {"manifestVersion", "provenance"}
        manifest_keys = frozenset(manifest) if isinstance(manifest, dict) else frozenset()
        if manifest_keys not in {frozenset(legacy_keys), frozenset(current_keys)}:
            raise FigureOutputPathError("Figure export manifest structure is invalid")
        current_manifest = manifest_keys == frozenset(current_keys)
        if (
            type(manifest["specVersion"]) is not int
            or manifest["specVersion"] != FIGURE_SPEC_VERSION
            or manifest["producer"] != FIGURE_EXPORT_PRODUCER
            or manifest["format"] not in {"png", "svg"}
            or manifest["order"] not in PAGE_ORDERS
            or not isinstance(manifest["source"], str)
            or not isinstance(manifest["sourceSignature"], dict)
        ):
            raise FigureOutputPathError("Figure export manifest provenance is invalid")
        if current_manifest:
            if (
                type(manifest["manifestVersion"]) is not int
                or manifest["manifestVersion"] != FIGURE_MANIFEST_VERSION
            ):
                raise FigureOutputPathError("Figure export manifest version is invalid")
            _validate_web_provenance(manifest["provenance"])
        spec_pages, spec_jobs = _validate_web_spec(manifest["spec"])
        if expected_header is not None:
            for key, value in expected_header.items():
                if manifest.get(key) != value:
                    raise FigureOutputPathError(
                        f"Existing Figure export {key} does not match the requested output"
                    )
        pages = manifest["pages"]
        if not isinstance(pages, list) or len(pages) != len(spec_jobs):
            raise FigureOutputPathError("Figure export manifest page count is invalid")
        file_names: list[str] = []
        for index, entry in enumerate(pages):
            if not isinstance(entry, dict) or set(entry) != {
                "outputIndex",
                "clusterId",
                "unitIndex",
                "pageIndex",
                "title",
                "file",
                "sha256",
                "placeholders",
            }:
                raise FigureOutputPathError("Figure export manifest page entry is invalid")
            metadata = {
                key: entry[key]
                for key in (
                    "outputIndex",
                    "clusterId",
                    "unitIndex",
                    "pageIndex",
                    "title",
                    "file",
                )
            }
            job = spec_jobs[index]
            page = spec_pages[job["pageIndex"]]
            if (
                entry["outputIndex"] != index
                or entry["clusterId"] != job["clusterId"]
                or entry["pageIndex"] != job["pageIndex"]
                or entry["title"] != page["title"]
                or isinstance(entry["unitIndex"], bool)
                or not isinstance(entry["unitIndex"], int)
                or entry["unitIndex"] < 0
            ):
                raise FigureOutputPathError("Figure export manifest page order is invalid")
            expected_filename = (
                f"{index + 1:04d}_unit_{entry['unitIndex']:03d}_"
                f"cluster_{entry['clusterId']}_page_{entry['pageIndex'] + 1:02d}."
                f"{manifest['format']}"
            )
            if entry["file"] != expected_filename or Path(entry["file"]).name != entry["file"]:
                raise FigureOutputPathError("Figure export manifest filename is invalid")
            if expected_pages is not None and (
                index >= len(expected_pages) or metadata != expected_pages[index]
            ):
                raise FigureOutputPathError(
                    "Existing Figure export page recipe does not match the requested output"
                )
            if (
                not isinstance(entry["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
                or not isinstance(entry["placeholders"], list)
                or any(not isinstance(item, str) for item in entry["placeholders"])
            ):
                raise FigureOutputPathError("Figure export manifest page integrity is invalid")
            if entry["file"] in file_names:
                raise FigureOutputPathError("Figure export manifest has duplicate files")
            file_names.append(entry["file"])
        if expected_pages is not None and len(pages) != len(expected_pages):
            raise FigureOutputPathError("Figure export page count does not match the request")
        if set(os.listdir(descriptor)) != {"manifest.json", *file_names}:
            raise FigureOutputPathError("Figure export directory contains unlisted files")
        for entry in pages:
            page_fd = os.open(
                entry["file"],
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                page_stat = os.fstat(page_fd)
                if not stat.S_ISREG(page_stat.st_mode):
                    raise FigureOutputPathError("Figure export page is not a regular file")
                digest = hashlib.sha256()
                while chunk := os.read(page_fd, 1024 * 1024):
                    digest.update(chunk)
                if digest.hexdigest() != entry["sha256"]:
                    raise FigureOutputPathError("Figure export page checksum is invalid")
            finally:
                os.close(page_fd)
    finally:
        os.close(descriptor)
    return identity


def _recover_web_directory_publish(
    parent: SharedParentDirectory,
    target: Path,
) -> None:
    validator = lambda name: _validate_web_export_directory(
        parent,
        name,
        expected_header=None,
        expected_pages=None,
    )
    with _shared_directory_publish_lock(parent, target.name):
        parent.verify()
        _shared_recover_directory_publish_locked(
            parent,
            target,
            directory_validator=validator,
        )
        parent.verify()


def _commit_web_directory(
    parent: SharedParentDirectory,
    staged: Path,
    target: Path,
    *,
    overwrite: bool,
    expected_identity: SharedEntryIdentity | None,
) -> None:
    validator = lambda name: _validate_web_export_directory(
        parent,
        name,
        expected_header=None,
        expected_pages=None,
    )
    with _shared_directory_publish_lock(parent, target.name):
        parent.verify()
        _shared_recover_directory_publish_locked(
            parent,
            target,
            directory_validator=validator,
        )
        if expected_identity is None:
            try:
                _shared_atomic_directory_rename(
                    staged,
                    target,
                    exchange=False,
                    parent_fd=parent.fd,
                )
                _shared_fsync_directory_fd(parent.fd)
                parent.verify()
            except BaseException:
                if (
                    _shared_entry_lstat(parent, target.name) is not None
                    and _shared_entry_lstat(parent, staged.name) is None
                ):
                    try:
                        _shared_atomic_directory_rename(
                            target,
                            staged,
                            exchange=False,
                            parent_fd=parent.fd,
                        )
                        _shared_fsync_directory_fd(parent.fd)
                    except BaseException:
                        pass
                raise
            return
        if not overwrite:
            raise FileExistsError(f"Output already exists: {target}")
        current_identity = validator(target.name)
        if current_identity != expected_identity:
            raise FigureOutputPathError(
                "Figure export target changed while pages were rendering"
            )
        try:
            _shared_atomic_directory_rename(
                staged,
                target,
                exchange=True,
                parent_fd=parent.fd,
            )
        except OSError as exc:
            unsupported = {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}
            if hasattr(errno, "ENOTSUP"):
                unsupported.add(errno.ENOTSUP)
            if exc.errno not in unsupported:
                raise
            _shared_fallback_replace_directory_locked(
                parent,
                staged,
                target,
                expected_identity=expected_identity,
                directory_validator=validator,
            )
            return
        old_stat = _shared_entry_lstat(parent, staged.name)
        try:
            if old_stat is None or not _shared_same_identity(old_stat, expected_identity):
                raise FigureOutputPathError(
                    "Figure export target changed during atomic exchange"
                )
            _shared_fsync_directory_fd(parent.fd)
            parent.verify()
        except BaseException:
            _shared_atomic_directory_rename(
                staged,
                target,
                exchange=True,
                parent_fd=parent.fd,
            )
            _shared_fsync_directory_fd(parent.fd)
            raise
        _shared_remove_directory_at(parent, staged.name)
        _shared_fsync_directory_fd(parent.fd)


class FigureExportService:
    """Render and atomically publish versioned multi-unit figure plans."""

    def __init__(self, destination_root: Path):
        self.destination_root = destination_root

    def _destination(self, directory: str) -> Path:
        destination, _relative = _safe_relative_directory(self.destination_root, directory)
        if not os.access(destination, os.W_OK | os.X_OK):
            raise FigureOutputPathError("Destination directory is not writable")
        return destination

    @staticmethod
    def _manifest_entry(
        job: ExpandedPage,
        unit_index: int,
        *,
        filename: str | None,
        rendered: RenderedPage | None,
        placeholders: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "outputIndex": job.output_index,
            "clusterId": job.cluster_id,
            "unitIndex": unit_index,
            "pageIndex": job.page_index,
            "title": job.page.title,
            "file": filename,
            "sha256": None if rendered is None else rendered.sha256,
            "placeholders": list(placeholders),
        }

    def export_pngs(
        self,
        *,
        record: DatasetRecord,
        jobs: Sequence[ExpandedPage],
        renderer: FigurePageRenderer,
        unit_loader: Callable[[int], tuple[int, np.ndarray]],
        validate_source: Callable[[], None],
        provenance: Mapping[str, Any],
        directory: str,
        base_name: str,
        overwrite: bool,
        order: str,
    ) -> dict[str, Any]:
        return self._export_image_directory(
            record=record,
            jobs=jobs,
            renderer=renderer,
            unit_loader=unit_loader,
            validate_source=validate_source,
            provenance=provenance,
            directory=directory,
            base_name=base_name,
            overwrite=overwrite,
            order=order,
            output_format="png",
        )

    def export_svgs(
        self,
        *,
        record: DatasetRecord,
        jobs: Sequence[ExpandedPage],
        renderer: FigurePageRenderer,
        unit_loader: Callable[[int], tuple[int, np.ndarray]],
        validate_source: Callable[[], None],
        provenance: Mapping[str, Any],
        directory: str,
        base_name: str,
        overwrite: bool,
        order: str,
    ) -> dict[str, Any]:
        return self._export_image_directory(
            record=record,
            jobs=jobs,
            renderer=renderer,
            unit_loader=unit_loader,
            validate_source=validate_source,
            provenance=provenance,
            directory=directory,
            base_name=base_name,
            overwrite=overwrite,
            order=order,
            output_format="svg",
        )

    def _export_image_directory(
        self,
        *,
        record: DatasetRecord,
        jobs: Sequence[ExpandedPage],
        renderer: FigurePageRenderer,
        unit_loader: Callable[[int], tuple[int, np.ndarray]],
        validate_source: Callable[[], None],
        provenance: Mapping[str, Any],
        directory: str,
        base_name: str,
        overwrite: bool,
        order: str,
        output_format: str,
    ) -> dict[str, Any]:
        if output_format not in {"png", "svg"}:
            raise FigureExportValidationError("Image directory format must be PNG or SVG")
        destination = self._destination(directory)
        safe_name = _safe_base_name(base_name)
        target = destination / safe_name
        manifest_header = _web_manifest_header(
            record, jobs, order, provenance, output_format
        )
        expected_pages = tuple(
            _web_page_metadata(record, job, output_format) for job in jobs
        )
        with _shared_open_parent_directory(destination) as parent:
            _recover_web_directory_publish(parent, target)
            existing_stat = _shared_entry_lstat(parent, target.name)
            existed = existing_stat is not None
            if existed and not overwrite:
                raise FileExistsError(f"Output already exists: {target}")
            expected_identity = (
                None
                if not existed
                else _validate_web_export_directory(
                    parent,
                    target.name,
                    expected_header=None,
                    expected_pages=None,
                )
            )
            staged_name, staged_fd = _shared_make_staging_directory(parent, target.name)
            staged = parent.path / staged_name
            manifest_entries: list[dict[str, Any]] = []
            page_bytes = 0
            try:
                for job, expected_page in zip(jobs, expected_pages, strict=True):
                    unit_index, counts = unit_loader(job.cluster_id)
                    if unit_index != expected_page["unitIndex"]:
                        del counts
                        raise FigureExportValidationError(
                            "Dataset unit index changed while rendering Figure export"
                        )
                    try:
                        rendered_png = renderer.render_png(
                            job.cluster_id,
                            unit_index,
                            counts,
                            job.page,
                        )
                    finally:
                        del counts
                    contents = (
                        rendered_png.contents
                        if output_format == "png"
                        else _svg_with_embedded_png(rendered_png.contents)
                    )
                    rendered = RenderedPage(
                        contents,
                        hashlib.sha256(contents).hexdigest(),
                        rendered_png.placeholders,
                    )
                    filename = expected_page["file"]
                    _shared_atomic_write_bytes_at(staged_fd, filename, contents)
                    page_bytes += len(contents)
                    manifest_entries.append(
                        self._manifest_entry(
                            job,
                            unit_index,
                            filename=filename,
                            rendered=rendered,
                            placeholders=rendered.placeholders,
                        )
                    )
                    del rendered, rendered_png, contents
                manifest = {**manifest_header, "pages": manifest_entries}
                manifest_bytes = (
                    json.dumps(manifest, indent=2, allow_nan=False) + "\n"
                ).encode("utf-8")
                _shared_atomic_write_bytes_at(
                    staged_fd,
                    "manifest.json",
                    manifest_bytes,
                )
                _shared_fsync_directory_fd(staged_fd)
                _validate_web_export_directory(
                    parent,
                    staged_name,
                    expected_header=manifest_header,
                    expected_pages=expected_pages,
                )
                validate_source()
                parent.verify()
                _commit_web_directory(
                    parent,
                    staged,
                    target,
                    overwrite=overwrite,
                    expected_identity=expected_identity,
                )
                return {
                    "format": output_format,
                    "path": str(target),
                    "pageCount": len(manifest_entries),
                    "bytes": page_bytes + len(manifest_bytes),
                    "overwritten": existed,
                    "manifest": manifest,
                }
            finally:
                os.close(staged_fd)
                if _shared_entry_lstat(parent, staged_name) is not None:
                    _shared_remove_directory_at(parent, staged_name)

    def export_pdf(
        self,
        *,
        record: DatasetRecord,
        jobs: Sequence[ExpandedPage],
        renderer: FigurePageRenderer,
        unit_loader: Callable[[int], tuple[int, np.ndarray]],
        validate_source: Callable[[], None],
        provenance: Mapping[str, Any],
        directory: str,
        base_name: str,
        overwrite: bool,
        order: str,
    ) -> dict[str, Any]:
        destination = self._destination(directory)
        safe_name = _safe_base_name(base_name)
        if safe_name.casefold().endswith(".pdf"):
            safe_name = safe_name[:-4]
        target = destination / f"{safe_name}.pdf"
        manifest_header = {
            **_web_manifest_header(record, jobs, order, provenance),
            "format": "pdf",
            "rendering": {
                "widthPixels": renderer.shared_renderer.page_size[0],
                "heightPixels": renderer.shared_renderer.page_size[1],
                "resolutionDpi": 150.0,
                "encoding": "FlateDecode DeviceRGB 8-bit",
            },
        }
        embedded_manifest = {
            **manifest_header,
            "pages": [
                {**_web_page_metadata(record, job), "file": target.name}
                for job in jobs
            ],
        }
        with _shared_open_parent_directory(destination) as parent:
            expected_identity = _shared_inspect_pdf_destination(
                parent,
                target,
                overwrite=overwrite,
            )
            existed = expected_identity is not None
            staged_name, staged_fd = _shared_make_staging_file(
                parent,
                target.name,
                suffix=".pdf",
            )
            manifest_entries: list[dict[str, Any]] = []
            try:
                def image_provider(page_index: int) -> Image.Image:
                    job = jobs[page_index]
                    unit_index, counts = unit_loader(job.cluster_id)
                    image: Image.Image | None = None
                    try:
                        image, placeholders = renderer.build_image(
                            job.cluster_id,
                            unit_index,
                            counts,
                            job.page,
                        )
                        manifest_entries.append(
                            self._manifest_entry(
                                job,
                                unit_index,
                                filename=target.name,
                                rendered=None,
                                placeholders=placeholders,
                            )
                        )
                        return image
                    except BaseException:
                        if image is not None:
                            image.close()
                        raise
                    finally:
                        del counts

                with os.fdopen(os.dup(staged_fd), "wb") as stream:
                    write_streaming_pdf(
                        stream,
                        len(jobs),
                        image_provider,
                        title=target.stem,
                        resolution=150.0,
                        export_metadata=embedded_manifest,
                    )
                os.fsync(staged_fd)
                validate_source()
                parent.verify()
                pdf_size = os.fstat(staged_fd).st_size
                _shared_commit_file(
                    parent,
                    staged_name,
                    target,
                    overwrite=overwrite,
                    expected_identity=expected_identity,
                )
                return {
                    "format": "pdf",
                    "path": str(target),
                    "pageCount": len(manifest_entries),
                    "bytes": pdf_size,
                    "overwritten": existed,
                    "manifest": {**manifest_header, "pages": manifest_entries},
                }
            finally:
                os.close(staged_fd)
                try:
                    os.unlink(staged_name, dir_fd=parent.fd)
                except FileNotFoundError:
                    pass
