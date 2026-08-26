"""Strict viewer-side reader for exported SpikeInterface waveforms.

The scientific exporter lives in the sibling ``rfmapping`` repository.  This
module deliberately re-implements only its versioned, read-only artifact
contract so the independent Python viewer can display and export an already
computed template without importing the analysis code or SpikeInterface.

Schema version 4 stores shared channel/time metadata as plain CSV and one
compressed template table per unit.  Store construction validates the small
shared tables; unit templates remain lazy and are held in a bounded LRU cache.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal, Mapping

import numpy as np
from numpy.typing import NDArray


WAVEFORM_SCHEMA_NAME = "rfmapping-spikeinterface-waveforms"
WAVEFORM_SCHEMA_VERSION = 4
WAVEFORM_CHANNEL_MODES = ("same_x_column", "same_shank")
DEFAULT_WAVEFORM_CHANNEL_MODE = "same_x_column"
DEFAULT_LOCAL_CHANNEL_COUNT = 5
DEFAULT_BASELINE_END_MS = -0.25

# ``present_good`` is emitted when a session export reuses the canonical
# concatenated good-unit templates and retains only units present in that
# individual session.  It is a schema-v4 scope, distinct from the older
# session-local ``good`` and unfiltered ``all`` exports.
_WAVEFORM_UNIT_SCOPES = ("all", "good", "present_good")

LocalChannelMode = Literal["same_x_column", "same_shank"]

_CHANNEL_COLUMNS = (
    "channel_index",
    "channel_id",
    "raw_channel_index",
    "x_um",
    "y_um",
    "shank_id",
)
_TIME_COLUMNS = ("sample_index", "sample_offset", "time_ms")
_UNIT_COLUMNS = (
    "unit_index",
    "unit_id",
    "quality",
    "total_spike_count",
    "selected_spike_count",
    "time_coverage_percent",
    "best_channel_index",
    "best_channel_id",
    "best_channel_x_um",
    "best_channel_y_um",
    "max_ptp_uv",
    "unit_data_dir",
)
_PROBE_PART_RE = re.compile(r"probe[\s_-]*([ab])(?:\b|[_-])", re.IGNORECASE)
_FILENAME_PROBE_RE = re.compile(r"(?:^|[\s_-])([ab])$", re.IGNORECASE)


class WaveformArtifactError(ValueError):
    """Raised when a waveform artifact violates its versioned contract."""


@dataclass(frozen=True, slots=True)
class WaveformChannel:
    channel_index: int
    channel_id: int
    raw_channel_index: int
    x_um: float
    y_um: float
    shank_id: int


@dataclass(frozen=True, slots=True)
class WaveformUnitSummary:
    unit_index: int
    unit_id: int
    quality: str
    total_spike_count: int
    selected_spike_count: int
    time_coverage_percent: float
    best_channel_index: int
    best_channel_id: int
    best_channel_x_um: float
    best_channel_y_um: float
    max_ptp_uv: float
    unit_data_dir: str


@dataclass(frozen=True, slots=True)
class WaveformUnitTemplate:
    """One lazily loaded full-channel mean template (samples by channels)."""

    summary: WaveformUnitSummary
    template_uv: NDArray[np.float64]
    source_path: Path


@dataclass(frozen=True, slots=True)
class WaveformPayload:
    """Renderer-neutral local heatmap payload shared by Tk and figure export.

    ``values_uv`` is channel-by-time and already baseline corrected.  Channels
    have the notebook plot's final spatial order: descending y, then ascending
    x.  ``best_channel_index`` is the artifact-global channel index while
    ``best_channel_row`` addresses the corresponding row in ``values_uv``.
    """

    source_dir: Path
    summary: WaveformUnitSummary
    mode: LocalChannelMode
    local_channel_count: int
    baseline_end_ms: float
    time_ms: NDArray[np.float64]
    time_edges_ms: NDArray[np.float64]
    values_uv: NDArray[np.float64]
    channels: tuple[WaveformChannel, ...]
    best_channel_index: int
    best_channel_row: int
    amplitude_limit_uv: float

    @property
    def unit_id(self) -> int:
        return self.summary.unit_id

    @property
    def best_channel(self) -> WaveformChannel:
        return self.channels[self.best_channel_row]

    @property
    def matrix(self) -> NDArray[np.float64]:
        """Exporter-friendly alias for the channel-by-time values."""

        return self.values_uv

    @property
    def times_ms(self) -> NDArray[np.float64]:
        """Exporter-friendly alias matching the figure payload vocabulary."""

        return self.time_ms

    @property
    def channel_labels(self) -> tuple[str, ...]:
        return tuple(f"ch {channel.channel_id}" for channel in self.channels)


def _readonly_float_array(values: Any) -> NDArray[np.float64]:
    result = np.array(values, dtype=np.float64, copy=True)
    result.setflags(write=False)
    return result


def _readonly_int_array(values: Any) -> NDArray[np.int64]:
    result = np.array(values, dtype=np.int64, copy=True)
    result.setflags(write=False)
    return result


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise WaveformArtifactError(f"{label} must be a JSON object")
    return value


def _json_integer(value: Any, label: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise WaveformArtifactError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise WaveformArtifactError(f"{label} must be at least {minimum}")
    return value


def _json_number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WaveformArtifactError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise WaveformArtifactError(f"{label} must be finite")
    if positive and parsed <= 0.0:
        raise WaveformArtifactError(f"{label} must be positive")
    return parsed


def _csv_integer(value: str | None, label: str) -> int:
    if value is None or re.fullmatch(r"[+-]?\d+", value.strip()) is None:
        raise WaveformArtifactError(f"{label} must be an integer")
    return int(value)


def _csv_float(value: str | None, label: str) -> float:
    if value is None:
        raise WaveformArtifactError(f"{label} must be numeric")
    try:
        parsed = float(value)
    except ValueError as exc:
        raise WaveformArtifactError(f"{label} must be numeric") from exc
    if not math.isfinite(parsed):
        raise WaveformArtifactError(f"{label} must be finite")
    return parsed


def _read_exact_dict_rows(
    path: Path,
    expected_columns: tuple[str, ...],
) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            actual = tuple(reader.fieldnames or ())
            if actual != expected_columns:
                raise WaveformArtifactError(
                    f"{path.name} header must be {','.join(expected_columns)}; "
                    f"got {','.join(actual) if actual else '<missing>'}"
                )
            rows = list(reader)
    except OSError:
        raise
    for row_number, row in enumerate(rows, start=2):
        if None in row or any(row.get(column) is None for column in expected_columns):
            raise WaveformArtifactError(
                f"{path.name} row {row_number} has the wrong column count"
            )
    return rows


def _confined_relative_path(root: Path, raw: Any, label: str) -> tuple[str, Path]:
    if not isinstance(raw, str) or not raw.strip():
        raise WaveformArtifactError(f"{label} must be a non-empty relative path")
    posix_path = PurePosixPath(raw)
    if posix_path.is_absolute() or any(part in {"", ".", ".."} for part in posix_path.parts):
        raise WaveformArtifactError(f"{label} must stay within the waveform artifact")
    candidate = root.joinpath(*posix_path.parts).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WaveformArtifactError(
            f"{label} must stay within the waveform artifact"
        ) from exc
    if candidate == root:
        raise WaveformArtifactError(f"{label} must identify a unit directory")
    return posix_path.as_posix(), candidate


def _validate_manifest(raw: Any) -> dict[str, Any]:
    manifest = _require_mapping(raw, "manifest")
    if manifest.get("schema_name") != WAVEFORM_SCHEMA_NAME:
        raise WaveformArtifactError(
            f"Unsupported waveform schema name: {manifest.get('schema_name')!r}"
        )
    version = manifest.get("schema_version")
    if type(version) is not int or version != WAVEFORM_SCHEMA_VERSION:
        raise WaveformArtifactError(
            f"Unsupported waveform schema version: {version!r}"
        )

    units = _require_mapping(manifest.get("units"), "manifest.units")
    if units.get("scope") not in _WAVEFORM_UNIT_SCOPES:
        raise WaveformArtifactError(
            "manifest.units.scope must be 'all', 'good', or 'present_good'"
        )
    _json_integer(units.get("count"), "manifest.units.count", minimum=0)

    waveform = _require_mapping(manifest.get("waveform"), "manifest.waveform")
    num_samples = _json_integer(
        waveform.get("num_samples"), "manifest.waveform.num_samples", minimum=2
    )
    nbefore = _json_integer(
        waveform.get("nbefore"), "manifest.waveform.nbefore", minimum=0
    )
    if nbefore > num_samples:
        raise WaveformArtifactError(
            "manifest.waveform.nbefore cannot exceed num_samples"
        )

    recording = _require_mapping(manifest.get("recording"), "manifest.recording")
    _json_number(
        recording.get("sampling_frequency_hz"),
        "manifest.recording.sampling_frequency_hz",
        positive=True,
    )
    _json_integer(
        recording.get("num_frames"), "manifest.recording.num_frames", minimum=1
    )
    _json_number(
        recording.get("duration_minutes"),
        "manifest.recording.duration_minutes",
        positive=True,
    )

    files = _require_mapping(manifest.get("files"), "manifest.files")
    if not isinstance(files.get("units"), str) or not files["units"].strip():
        raise WaveformArtifactError("manifest.files.units must be a non-empty path")
    return manifest


def _probe_name_for_path(path: Path) -> str | None:
    filename_match = _FILENAME_PROBE_RE.search(path.stem)
    if filename_match:
        return f"Probe{filename_match.group(1).upper()}"
    for part in (path.name, *(parent.name for parent in path.parents)):
        match = _PROBE_PART_RE.search(part)
        if match:
            return f"Probe{match.group(1).upper()}"
    return None


def discover_waveform_artifact(
    rf_path: str | Path,
    *,
    data_root: str | Path | None = None,
) -> Path | None:
    """Return a bounded, same-session waveform artifact directory.

    Direct manifest/artifact paths are accepted for tests and manual opens.
    RF documents use their ProbeA/ProbeB filename vocabulary and search only
    nearby ancestors through the nearest ``data`` directory (or two ancestors
    for compact fixtures); there is no recursive filesystem walk.
    """

    source = Path(rf_path).expanduser()
    direct = source.parent if source.name == "manifest.json" else source
    if direct.is_dir() and (direct / "manifest.json").is_file():
        return direct.resolve()

    probe_name = _probe_name_for_path(source)
    if probe_name is None:
        return None

    roots: list[Path] = []
    if data_root is not None:
        roots.append(Path(data_root).expanduser())
    else:
        parents = list(source.parents)
        data_boundary = next((parent for parent in parents if parent.name == "data"), None)
        if data_boundary is None:
            roots.extend(parents[:2])
        else:
            for parent in parents:
                roots.append(parent)
                if parent == data_boundary:
                    break

    seen: set[str] = set()
    for root in roots:
        candidates = (
            root / "waveform" / probe_name,
            root / "data" / "waveform" / probe_name,
            root / probe_name,
            root if root.name == probe_name else root / "__not_an_artifact__",
        )
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            manifest = candidate / "manifest.json"
            if manifest.is_file():
                return candidate.resolve()
    return None


def select_local_channel_indices(
    channel_locations: NDArray[np.float64],
    channel_shank_ids: NDArray[np.int64],
    best_channel_index: int,
    mode: LocalChannelMode,
    local_channel_count: int = DEFAULT_LOCAL_CHANNEL_COUNT,
) -> NDArray[np.int64]:
    """Select the notebook plot's best channel plus nearest neighbours.

    Neighbours are ranked by stable 2-D Euclidean distance, constrained either
    to the best channel's shank or its x coordinate.  The chosen sites are then
    displayed by descending y and ascending x.  This intentionally does not
    force two sites above and two below; edge units retain the current plot's
    naturally asymmetric selection.
    """

    locations = np.asarray(channel_locations, dtype=float)
    shank_ids = np.asarray(channel_shank_ids)
    if locations.ndim != 2 or locations.shape[1] != 2 or locations.shape[0] == 0:
        raise WaveformArtifactError("channel_locations must have shape (channels, 2)")
    if shank_ids.ndim != 1 or shank_ids.shape[0] != locations.shape[0]:
        raise WaveformArtifactError("channel_shank_ids must match channel_locations")
    if not np.all(np.isfinite(locations)):
        raise WaveformArtifactError("channel_locations must be finite")
    if isinstance(best_channel_index, bool) or not isinstance(
        best_channel_index, (int, np.integer)
    ):
        raise WaveformArtifactError("best_channel_index must be an integer")
    best_channel_index = int(best_channel_index)
    if not 0 <= best_channel_index < len(locations):
        raise WaveformArtifactError("best_channel_index is out of range")
    if mode not in WAVEFORM_CHANNEL_MODES:
        raise WaveformArtifactError(
            f"mode must be one of {', '.join(WAVEFORM_CHANNEL_MODES)}"
        )
    if isinstance(local_channel_count, bool) or not isinstance(
        local_channel_count, (int, np.integer)
    ):
        raise WaveformArtifactError("local_channel_count must be an integer")
    local_channel_count = int(local_channel_count)
    if local_channel_count < 1:
        raise WaveformArtifactError("local_channel_count must be at least 1")

    distances = np.linalg.norm(locations - locations[best_channel_index], axis=1)
    if mode == "same_shank":
        candidates = np.flatnonzero(shank_ids == shank_ids[best_channel_index])
    else:
        candidates = np.flatnonzero(
            np.isclose(
                locations[:, 0],
                locations[best_channel_index, 0],
                rtol=0.0,
                atol=1e-6,
            )
        )
    neighbor_candidates = candidates[candidates != best_channel_index]
    candidate_order = np.argsort(distances[neighbor_candidates], kind="stable")
    selected = np.r_[
        best_channel_index,
        neighbor_candidates[candidate_order[: local_channel_count - 1]],
    ].astype(np.int64)
    display_order = np.lexsort(
        (locations[selected, 0], -locations[selected, 1])
    )
    return _readonly_int_array(selected[display_order])


def baseline_correct_template(
    template_uv: NDArray[np.float64],
    time_ms: NDArray[np.float64],
    *,
    baseline_end_ms: float = DEFAULT_BASELINE_END_MS,
) -> NDArray[np.float64]:
    """Subtract each channel's mean at ``time_ms <= baseline_end_ms``."""

    template = np.asarray(template_uv, dtype=float)
    times = np.asarray(time_ms, dtype=float)
    if template.ndim != 2:
        raise WaveformArtifactError("template_uv must have shape (samples, channels)")
    if times.ndim != 1 or len(times) != template.shape[0]:
        raise WaveformArtifactError("time_ms must match template_uv samples")
    if not np.all(np.isfinite(template)) or not np.all(np.isfinite(times)):
        raise WaveformArtifactError("waveform template and time values must be finite")
    if isinstance(baseline_end_ms, bool) or not isinstance(
        baseline_end_ms, (int, float, np.integer, np.floating)
    ):
        raise WaveformArtifactError("baseline_end_ms must be numeric")
    baseline_end_ms = float(baseline_end_ms)
    if not math.isfinite(baseline_end_ms):
        raise WaveformArtifactError("baseline_end_ms must be finite")
    baseline_mask = times <= baseline_end_ms
    if not np.any(baseline_mask):
        raise WaveformArtifactError(
            f"No waveform samples are at or before {baseline_end_ms:g} ms"
        )
    baseline_uv = np.mean(template[baseline_mask], axis=0, keepdims=True)
    return _readonly_float_array(template - baseline_uv)


class WaveformArtifactStore:
    """Validated schema-v4 artifact with lazy per-unit template loading."""

    def __init__(
        self,
        analysis_dir: str | Path,
        *,
        template_cache_size: int = 8,
    ) -> None:
        if isinstance(template_cache_size, bool) or not isinstance(
            template_cache_size, int
        ):
            raise TypeError("template_cache_size must be an integer")
        if template_cache_size < 1:
            raise ValueError("template_cache_size must be at least 1")

        source = Path(analysis_dir).expanduser()
        if source.name == "manifest.json" and source.is_file():
            source = source.parent
        try:
            source = source.resolve(strict=True)
        except OSError:
            raise
        if not source.is_dir():
            raise WaveformArtifactError(f"Waveform artifact is not a directory: {source}")

        self.analysis_dir = source
        self.manifest_path = source / "manifest.json"
        self.channels_path = source / "channels.csv"
        self.waveform_time_path = source / "waveform_time.csv"
        for path in (self.manifest_path, self.channels_path, self.waveform_time_path):
            if not path.is_file():
                raise WaveformArtifactError(f"Required waveform file not found: {path}")

        try:
            raw_manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WaveformArtifactError(f"manifest.json is not valid JSON: {exc}") from exc
        manifest = _validate_manifest(raw_manifest)
        self.manifest: Mapping[str, Any] = MappingProxyType(manifest)
        files = _require_mapping(manifest["files"], "manifest.files")
        _units_relative, units_path = _confined_relative_path(
            source, files["units"], "manifest.files.units"
        )
        self.units_path = units_path
        if not self.units_path.is_file():
            raise WaveformArtifactError(
                f"Required waveform file not found: {self.units_path}"
            )

        channels = self._load_channels()
        self.channels = channels
        self.channel_ids = _readonly_int_array(
            [channel.channel_id for channel in channels]
        )
        self.channel_locations = _readonly_float_array(
            [(channel.x_um, channel.y_um) for channel in channels]
        )
        self.channel_shank_ids = _readonly_int_array(
            [channel.shank_id for channel in channels]
        )
        (
            self.sample_indices,
            self.sample_offsets,
            self.time_ms,
            self.time_edges_ms,
        ) = self._load_time()
        summaries = self._load_unit_summaries()
        self.unit_summaries: Mapping[int, WaveformUnitSummary] = MappingProxyType(
            summaries
        )
        self.unit_scope = str(_require_mapping(manifest["units"], "manifest.units")["scope"])
        self._template_cache_size = template_cache_size
        self._template_cache: OrderedDict[int, WaveformUnitTemplate] = OrderedDict()
        self._template_cache_lock = threading.Lock()

    @classmethod
    def open(
        cls,
        analysis_dir: str | Path,
        *,
        template_cache_size: int = 8,
    ) -> WaveformArtifactStore:
        return cls(analysis_dir, template_cache_size=template_cache_size)

    @classmethod
    def discover(
        cls,
        rf_path: str | Path,
        *,
        data_root: str | Path | None = None,
        template_cache_size: int = 8,
    ) -> WaveformArtifactStore | None:
        analysis_dir = discover_waveform_artifact(rf_path, data_root=data_root)
        if analysis_dir is None:
            return None
        return cls.open(analysis_dir, template_cache_size=template_cache_size)

    @property
    def source_paths(self) -> tuple[Path, ...]:
        return (
            self.manifest_path,
            self.channels_path,
            self.waveform_time_path,
            self.units_path,
        )

    @property
    def cached_unit_ids(self) -> tuple[int, ...]:
        with self._template_cache_lock:
            return tuple(self._template_cache)

    def _load_channels(self) -> tuple[WaveformChannel, ...]:
        rows = _read_exact_dict_rows(self.channels_path, _CHANNEL_COLUMNS)
        if not rows:
            raise WaveformArtifactError("channels.csv must contain at least one channel")
        channels: list[WaveformChannel] = []
        seen_channel_ids: set[int] = set()
        for row_number, row in enumerate(rows, start=2):
            try:
                channel_index = _csv_integer(row["channel_index"], "channel_index")
                channel_id = _csv_integer(row["channel_id"], "channel_id")
                raw_channel_index = _csv_integer(
                    row["raw_channel_index"], "raw_channel_index"
                )
                x_um = _csv_float(row["x_um"], "x_um")
                y_um = _csv_float(row["y_um"], "y_um")
                shank_id = _csv_integer(row["shank_id"], "shank_id")
            except WaveformArtifactError as exc:
                raise WaveformArtifactError(
                    f"Invalid channels.csv row {row_number}: {exc}"
                ) from exc
            expected_index = len(channels)
            if channel_index != expected_index:
                raise WaveformArtifactError(
                    "channels.csv channel_index must be contiguous and row ordered; "
                    f"expected {expected_index}, got {channel_index}"
                )
            if channel_id in seen_channel_ids:
                raise WaveformArtifactError(
                    f"channels.csv contains duplicate channel_id {channel_id}"
                )
            seen_channel_ids.add(channel_id)
            channels.append(
                WaveformChannel(
                    channel_index,
                    channel_id,
                    raw_channel_index,
                    x_um,
                    y_um,
                    shank_id,
                )
            )
        return tuple(channels)

    def _load_time(
        self,
    ) -> tuple[
        NDArray[np.int64],
        NDArray[np.int64],
        NDArray[np.float64],
        NDArray[np.float64],
    ]:
        rows = _read_exact_dict_rows(self.waveform_time_path, _TIME_COLUMNS)
        manifest_waveform = _require_mapping(
            self.manifest["waveform"], "manifest.waveform"
        )
        expected_count = int(manifest_waveform["num_samples"])
        if len(rows) != expected_count:
            raise WaveformArtifactError(
                "waveform_time.csv row count does not match manifest.waveform.num_samples"
            )
        sample_indices: list[int] = []
        sample_offsets: list[int] = []
        times: list[float] = []
        for row_number, row in enumerate(rows, start=2):
            try:
                sample_index = _csv_integer(row["sample_index"], "sample_index")
                sample_offset = _csv_integer(row["sample_offset"], "sample_offset")
                time_ms = _csv_float(row["time_ms"], "time_ms")
            except WaveformArtifactError as exc:
                raise WaveformArtifactError(
                    f"Invalid waveform_time.csv row {row_number}: {exc}"
                ) from exc
            expected_index = len(sample_indices)
            if sample_index != expected_index:
                raise WaveformArtifactError(
                    "waveform_time.csv sample_index must be contiguous and row ordered; "
                    f"expected {expected_index}, got {sample_index}"
                )
            sample_indices.append(sample_index)
            sample_offsets.append(sample_offset)
            times.append(time_ms)

        offsets = np.asarray(sample_offsets, dtype=np.int64)
        expected_first_offset = -int(manifest_waveform["nbefore"])
        if offsets[0] != expected_first_offset or not np.all(np.diff(offsets) == 1):
            raise WaveformArtifactError(
                "waveform_time.csv sample_offset does not match manifest.waveform.nbefore"
            )
        time_array = np.asarray(times, dtype=np.float64)
        if not np.all(np.diff(time_array) > 0.0):
            raise WaveformArtifactError("waveform_time.csv time_ms must be strictly increasing")
        time_step_ms = float(np.median(np.diff(time_array)))
        time_edges = np.r_[
            time_array[0] - time_step_ms / 2.0,
            (time_array[:-1] + time_array[1:]) / 2.0,
            time_array[-1] + time_step_ms / 2.0,
        ]
        return (
            _readonly_int_array(sample_indices),
            _readonly_int_array(offsets),
            _readonly_float_array(time_array),
            _readonly_float_array(time_edges),
        )

    def _load_unit_summaries(self) -> dict[int, WaveformUnitSummary]:
        rows = _read_exact_dict_rows(self.units_path, _UNIT_COLUMNS)
        manifest_units = _require_mapping(self.manifest["units"], "manifest.units")
        if len(rows) != int(manifest_units["count"]):
            raise WaveformArtifactError(
                "units.csv row count does not match manifest.units.count"
            )
        summaries: dict[int, WaveformUnitSummary] = {}
        for row_number, row in enumerate(rows, start=2):
            try:
                unit_index = _csv_integer(row["unit_index"], "unit_index")
                unit_id = _csv_integer(row["unit_id"], "unit_id")
                quality = row["quality"].strip()
                total_spike_count = _csv_integer(
                    row["total_spike_count"], "total_spike_count"
                )
                selected_spike_count = _csv_integer(
                    row["selected_spike_count"], "selected_spike_count"
                )
                time_coverage_percent = _csv_float(
                    row["time_coverage_percent"], "time_coverage_percent"
                )
                best_channel_index = _csv_integer(
                    row["best_channel_index"], "best_channel_index"
                )
                best_channel_id = _csv_integer(
                    row["best_channel_id"], "best_channel_id"
                )
                best_channel_x_um = _csv_float(
                    row["best_channel_x_um"], "best_channel_x_um"
                )
                best_channel_y_um = _csv_float(
                    row["best_channel_y_um"], "best_channel_y_um"
                )
                max_ptp_uv = _csv_float(row["max_ptp_uv"], "max_ptp_uv")
                unit_data_dir, _resolved_unit_dir = _confined_relative_path(
                    self.analysis_dir, row["unit_data_dir"], "unit_data_dir"
                )
            except WaveformArtifactError as exc:
                raise WaveformArtifactError(
                    f"Invalid units.csv row {row_number}: {exc}"
                ) from exc

            expected_index = len(summaries)
            if unit_index != expected_index:
                raise WaveformArtifactError(
                    "units.csv unit_index must be contiguous and row ordered; "
                    f"expected {expected_index}, got {unit_index}"
                )
            if unit_id in summaries:
                raise WaveformArtifactError(
                    f"units.csv contains duplicate unit_id {unit_id}"
                )
            if not quality:
                raise WaveformArtifactError(
                    f"Invalid units.csv row {row_number}: quality cannot be empty"
                )
            if total_spike_count < 0 or selected_spike_count < 0:
                raise WaveformArtifactError("Spike counts must be non-negative")
            if selected_spike_count > total_spike_count:
                raise WaveformArtifactError(
                    "selected_spike_count cannot exceed total_spike_count"
                )
            if not 0.0 <= time_coverage_percent <= 100.0 + 1e-9:
                raise WaveformArtifactError(
                    "time_coverage_percent must be between 0 and 100"
                )
            if not 0 <= best_channel_index < len(self.channels):
                raise WaveformArtifactError("best_channel_index is out of range")
            best_channel = self.channels[best_channel_index]
            if best_channel_id != best_channel.channel_id:
                raise WaveformArtifactError(
                    "best_channel_id does not match channels.csv at best_channel_index"
                )
            if not math.isclose(
                best_channel_x_um, best_channel.x_um, rel_tol=0.0, abs_tol=1e-6
            ) or not math.isclose(
                best_channel_y_um, best_channel.y_um, rel_tol=0.0, abs_tol=1e-6
            ):
                raise WaveformArtifactError(
                    "best channel coordinates do not match channels.csv"
                )
            if max_ptp_uv < 0.0:
                raise WaveformArtifactError("max_ptp_uv must be non-negative")

            summaries[unit_id] = WaveformUnitSummary(
                unit_index,
                unit_id,
                quality,
                total_spike_count,
                selected_spike_count,
                time_coverage_percent,
                best_channel_index,
                best_channel_id,
                best_channel_x_um,
                best_channel_y_um,
                max_ptp_uv,
                unit_data_dir,
            )
        return summaries

    def summary_for(self, unit_id: int) -> WaveformUnitSummary:
        try:
            normalized = int(unit_id)
        except (TypeError, ValueError) as exc:
            raise KeyError(f"Invalid waveform unit ID: {unit_id!r}") from exc
        try:
            return self.unit_summaries[normalized]
        except KeyError as exc:
            raise KeyError(
                f"Unit {normalized} is not available in this {self.unit_scope} waveform artifact"
            ) from exc

    def _unit_template_path(self, summary: WaveformUnitSummary) -> Path:
        _relative, unit_dir = _confined_relative_path(
            self.analysis_dir, summary.unit_data_dir, "unit_data_dir"
        )
        template_path = (unit_dir / "template_uv.csv.gz").resolve(strict=False)
        try:
            template_path.relative_to(self.analysis_dir)
        except ValueError as exc:
            raise WaveformArtifactError(
                "template path must stay within the waveform artifact"
            ) from exc
        return template_path

    def source_paths_for_unit(self, unit_id: int) -> tuple[Path, ...]:
        summary = self.summary_for(unit_id)
        return (*self.source_paths, self._unit_template_path(summary))

    def _read_template(self, summary: WaveformUnitSummary) -> WaveformUnitTemplate:
        template_path = self._unit_template_path(summary)
        expected_header = (
            "sample_index",
            *(f"chidx_{index:03d}_uv" for index in range(len(self.channels))),
        )
        try:
            with gzip.open(template_path, "rt", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                try:
                    actual_header = tuple(next(reader))
                except StopIteration as exc:
                    raise WaveformArtifactError(
                        f"{template_path.name} is missing a header"
                    ) from exc
                if actual_header != expected_header:
                    raise WaveformArtifactError(
                        f"{template_path.name} channel header does not match channels.csv"
                    )
                values = np.empty(
                    (len(self.time_ms), len(self.channels)), dtype=np.float64
                )
                row_count = 0
                for csv_row_number, row in enumerate(reader, start=2):
                    if row_count >= len(self.time_ms):
                        raise WaveformArtifactError(
                            f"{template_path.name} has more rows than waveform_time.csv"
                        )
                    if len(row) != len(expected_header):
                        raise WaveformArtifactError(
                            f"{template_path.name} row {csv_row_number} has the wrong column count"
                        )
                    sample_index = _csv_integer(row[0], "sample_index")
                    if sample_index != int(self.sample_indices[row_count]):
                        raise WaveformArtifactError(
                            f"{template_path.name} sample_index does not match waveform_time.csv"
                        )
                    for channel_index, raw_value in enumerate(row[1:]):
                        values[row_count, channel_index] = _csv_float(
                            raw_value, f"channel {channel_index} amplitude"
                        )
                    row_count += 1
        except (gzip.BadGzipFile, EOFError) as exc:
            raise WaveformArtifactError(
                f"{template_path.name} is not a valid gzip CSV"
            ) from exc
        if row_count != len(self.time_ms):
            raise WaveformArtifactError(
                f"{template_path.name} row count does not match waveform_time.csv"
            )
        values.setflags(write=False)
        return WaveformUnitTemplate(summary, values, template_path)

    def load_unit_template(self, unit_id: int) -> WaveformUnitTemplate:
        summary = self.summary_for(unit_id)
        normalized = summary.unit_id
        with self._template_cache_lock:
            cached = self._template_cache.get(normalized)
            if cached is not None:
                self._template_cache.move_to_end(normalized)
                return cached

        loaded = self._read_template(summary)
        with self._template_cache_lock:
            cached = self._template_cache.get(normalized)
            if cached is not None:
                self._template_cache.move_to_end(normalized)
                return cached
            self._template_cache[normalized] = loaded
            self._template_cache.move_to_end(normalized)
            while len(self._template_cache) > self._template_cache_size:
                self._template_cache.popitem(last=False)
        return loaded

    # ``load_unit`` is a concise compatibility alias for callers familiar with
    # the scientific store.  It still loads only the template, not spike/PTP CSVs.
    load_unit = load_unit_template

    def payload_for(
        self,
        unit_id: int,
        mode: LocalChannelMode = DEFAULT_WAVEFORM_CHANNEL_MODE,
        local_channel_count: int = DEFAULT_LOCAL_CHANNEL_COUNT,
        *,
        baseline_end_ms: float = DEFAULT_BASELINE_END_MS,
    ) -> WaveformPayload:
        artifact = self.load_unit_template(unit_id)
        corrected = baseline_correct_template(
            artifact.template_uv,
            self.time_ms,
            baseline_end_ms=baseline_end_ms,
        )
        local_indices = select_local_channel_indices(
            self.channel_locations,
            self.channel_shank_ids,
            artifact.summary.best_channel_index,
            mode,
            local_channel_count,
        )
        values = _readonly_float_array(corrected[:, local_indices].T)
        channels = tuple(self.channels[int(index)] for index in local_indices)
        best_rows = np.flatnonzero(
            local_indices == artifact.summary.best_channel_index
        )
        if len(best_rows) != 1:  # Defensive invariant: selector always includes best.
            raise WaveformArtifactError("Local channel selection lost the best channel")
        amplitude_limit_uv = max(
            float(np.max(np.abs(corrected))),
            float(np.finfo(np.float64).eps),
        )
        return WaveformPayload(
            source_dir=self.analysis_dir,
            summary=artifact.summary,
            mode=mode,
            local_channel_count=int(local_channel_count),
            baseline_end_ms=float(baseline_end_ms),
            time_ms=self.time_ms,
            time_edges_ms=self.time_edges_ms,
            values_uv=values,
            channels=channels,
            best_channel_index=artifact.summary.best_channel_index,
            best_channel_row=int(best_rows[0]),
            amplitude_limit_uv=amplitude_limit_uv,
        )


__all__ = [
    "DEFAULT_BASELINE_END_MS",
    "DEFAULT_LOCAL_CHANNEL_COUNT",
    "DEFAULT_WAVEFORM_CHANNEL_MODE",
    "LocalChannelMode",
    "WAVEFORM_CHANNEL_MODES",
    "WAVEFORM_SCHEMA_NAME",
    "WAVEFORM_SCHEMA_VERSION",
    "WaveformArtifactError",
    "WaveformArtifactStore",
    "WaveformChannel",
    "WaveformPayload",
    "WaveformUnitSummary",
    "WaveformUnitTemplate",
    "baseline_correct_template",
    "discover_waveform_artifact",
    "select_local_channel_indices",
]
