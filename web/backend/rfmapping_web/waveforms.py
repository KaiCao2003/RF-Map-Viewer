from __future__ import annotations

"""Read-only schema-v4 SpikeInterface waveform companion support.

The scientific exporter lives outside this GUI repository.  This module owns
an independent, strict reader for the published CSV/gzip artifact so the Web
viewer can render already-computed local average templates without importing
analysis code or SpikeInterface.
"""

import csv
import gzip
import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping, Sequence

import numpy as np

from .paths import is_within


WAVEFORM_SCHEMA_NAME = "rfmapping-spikeinterface-waveforms"
WAVEFORM_SCHEMA_VERSION = 4
WAVEFORM_CHANNEL_MODES = ("same_x_column", "same_shank")
DEFAULT_WAVEFORM_CHANNEL_MODE = "same_x_column"
DEFAULT_LOCAL_CHANNEL_COUNT = 5
DEFAULT_BASELINE_END_MS = -0.25

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
_UNIT_SCOPES = {"all", "good", "present_good"}
_PROBE_PART_RE = re.compile(r"probe[\s_-]*([ab])(?:\b|[_-])", re.IGNORECASE)
_FILENAME_PROBE_RE = re.compile(r"(?:^|[\s_-])([ab])$", re.IGNORECASE)


class WaveformArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class WaveformChannel:
    channel_index: int
    channel_id: int
    raw_channel_index: int
    x_um: float
    y_um: float
    shank_id: int


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class WaveformPayload:
    source_dir: Path
    summary: WaveformUnitSummary
    mode: LocalChannelMode
    local_channel_count: int
    baseline_end_ms: float
    time_ms: np.ndarray
    time_edges_ms: np.ndarray
    values_uv: np.ndarray
    channels: tuple[WaveformChannel, ...]
    best_channel_index: int
    best_channel_row: int
    amplitude_limit_uv: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": True,
            "sourcePath": str(self.source_dir),
            "unitId": self.summary.unit_id,
            "quality": self.summary.quality,
            "totalSpikeCount": self.summary.total_spike_count,
            "selectedSpikeCount": self.summary.selected_spike_count,
            "timeCoveragePercent": self.summary.time_coverage_percent,
            "maxPtpUv": self.summary.max_ptp_uv,
            "mode": self.mode,
            "localChannelCount": self.local_channel_count,
            "baselineEndMs": self.baseline_end_ms,
            "timesMs": self.time_ms.tolist(),
            "timeEdgesMs": self.time_edges_ms.tolist(),
            "valuesUv": self.values_uv.tolist(),
            "channels": [
                {
                    "channelIndex": channel.channel_index,
                    "channelId": channel.channel_id,
                    "rawChannelIndex": channel.raw_channel_index,
                    "xUm": channel.x_um,
                    "yUm": channel.y_um,
                    "shankId": channel.shank_id,
                }
                for channel in self.channels
            ],
            "channelLabels": [f"ch {channel.channel_id}" for channel in self.channels],
            "bestChannelIndex": self.best_channel_index,
            "bestChannelRow": self.best_channel_row,
            "amplitudeLimitUv": self.amplitude_limit_uv,
        }


def unavailable_waveform_payload(detail: str) -> dict[str, Any]:
    return {"available": False, "detail": detail}


def _reject_constant(value: str) -> None:
    raise WaveformArtifactError(f"Invalid non-finite JSON value: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WaveformArtifactError(f"Duplicate manifest.json key: {key}")
        result[key] = value
    return result


def _mapping(value: Any, label: str) -> dict[str, Any]:
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
    if positive and parsed <= 0:
        raise WaveformArtifactError(f"{label} must be positive")
    return parsed


def _csv_integer(value: str | None, label: str) -> int:
    if value is None or re.fullmatch(r"[+-]?\d+", value.strip()) is None:
        raise WaveformArtifactError(f"{label} must be an integer")
    return int(value)


def _csv_float(value: str | None, label: str) -> float:
    try:
        parsed = float(value) if value is not None else math.nan
    except ValueError as exc:
        raise WaveformArtifactError(f"{label} must be numeric") from exc
    if not math.isfinite(parsed):
        raise WaveformArtifactError(f"{label} must be finite")
    return parsed


def _read_exact_rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        actual = tuple(reader.fieldnames or ())
        if actual != columns:
            raise WaveformArtifactError(
                f"{path.name} header must be {','.join(columns)}; "
                f"got {','.join(actual) if actual else '<missing>'}"
            )
        rows = list(reader)
    for row_number, row in enumerate(rows, start=2):
        if None in row or any(row.get(column) is None for column in columns):
            raise WaveformArtifactError(
                f"{path.name} row {row_number} has the wrong column count"
            )
    return rows


def _probe_name(path: Path) -> str | None:
    filename_match = _FILENAME_PROBE_RE.search(path.stem)
    if filename_match:
        return f"Probe{filename_match.group(1).upper()}"
    for part in (path.name, *(parent.name for parent in path.parents)):
        match = _PROBE_PART_RE.search(part)
        if match:
            return f"Probe{match.group(1).upper()}"
    return None


def _safe_artifact(candidate: Path, scope_root: Path) -> Path | None:
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return None
    if not resolved.is_dir() or not is_within(resolved, scope_root):
        return None
    manifest = resolved / "manifest.json"
    return resolved if manifest.is_file() and is_within(manifest.resolve(), scope_root) else None


def discover_waveform_artifact(
    rf_path: Path,
    scope_root: Path,
) -> Path | None:
    """Find a same-session ProbeA/ProbeB waveform artifact without a walk."""

    root = scope_root.resolve(strict=True)
    source = rf_path.resolve(strict=True)
    if not is_within(source, root):
        return None
    direct = source.parent if source.name == "manifest.json" else source
    if direct.is_dir():
        found = _safe_artifact(direct, root)
        if found is not None:
            return found

    probe = _probe_name(source)
    if probe is None:
        return None
    parents = list(source.parents)
    data_boundary = next(
        (parent for parent in parents if parent.name == "data" and is_within(parent, root)),
        None,
    )
    roots: list[Path] = []
    for parent in parents:
        if not is_within(parent, root):
            break
        roots.append(parent)
        if parent == data_boundary:
            break
        if data_boundary is None and len(roots) >= 2:
            break
    seen: set[Path] = set()
    for base in roots:
        for candidate in (
            base / "waveform" / probe,
            base / "data" / "waveform" / probe,
            base / probe,
            base if base.name == probe else base / "__not_an_artifact__",
        ):
            if candidate in seen:
                continue
            seen.add(candidate)
            found = _safe_artifact(candidate, root)
            if found is not None:
                return found
    return None


def select_local_channel_indices(
    channel_locations: np.ndarray,
    channel_shank_ids: np.ndarray,
    best_channel_index: int,
    mode: LocalChannelMode,
    local_channel_count: int = DEFAULT_LOCAL_CHANNEL_COUNT,
) -> np.ndarray:
    locations = np.asarray(channel_locations, dtype=np.float64)
    shanks = np.asarray(channel_shank_ids)
    if locations.ndim != 2 or locations.shape[1] != 2 or not len(locations):
        raise WaveformArtifactError("channel_locations must have shape (channels, 2)")
    if shanks.ndim != 1 or len(shanks) != len(locations):
        raise WaveformArtifactError("channel_shank_ids must match channel_locations")
    if not np.all(np.isfinite(locations)):
        raise WaveformArtifactError("channel_locations must be finite")
    if type(best_channel_index) is not int or not 0 <= best_channel_index < len(locations):
        raise WaveformArtifactError("best_channel_index is out of range")
    if mode not in WAVEFORM_CHANNEL_MODES:
        raise WaveformArtifactError(
            f"mode must be one of {', '.join(WAVEFORM_CHANNEL_MODES)}"
        )
    if type(local_channel_count) is not int or local_channel_count < 1:
        raise WaveformArtifactError("local_channel_count must be a positive integer")

    distances = np.linalg.norm(locations - locations[best_channel_index], axis=1)
    if mode == "same_shank":
        candidates = np.flatnonzero(shanks == shanks[best_channel_index])
    else:
        candidates = np.flatnonzero(
            np.isclose(
                locations[:, 0], locations[best_channel_index, 0], rtol=0, atol=1e-6
            )
        )
    neighbours = candidates[candidates != best_channel_index]
    nearest = neighbours[np.argsort(distances[neighbours], kind="stable")]
    selected = np.r_[best_channel_index, nearest[: local_channel_count - 1]].astype(int)
    return selected[np.lexsort((locations[selected, 0], -locations[selected, 1]))]


def baseline_correct_template(
    template_uv: np.ndarray,
    time_ms: np.ndarray,
    *,
    baseline_end_ms: float = DEFAULT_BASELINE_END_MS,
) -> np.ndarray:
    template = np.asarray(template_uv, dtype=np.float64)
    times = np.asarray(time_ms, dtype=np.float64)
    if template.ndim != 2 or times.ndim != 1 or len(times) != template.shape[0]:
        raise WaveformArtifactError("template_uv must have shape (samples, channels)")
    if not np.all(np.isfinite(template)) or not np.all(np.isfinite(times)):
        raise WaveformArtifactError("waveform template and time values must be finite")
    if isinstance(baseline_end_ms, bool) or not isinstance(baseline_end_ms, (int, float)):
        raise WaveformArtifactError("baseline_end_ms must be numeric")
    baseline_end = float(baseline_end_ms)
    if not math.isfinite(baseline_end):
        raise WaveformArtifactError("baseline_end_ms must be finite")
    mask = times <= baseline_end
    if not np.any(mask):
        raise WaveformArtifactError(
            f"No waveform samples are at or before {baseline_end:g} ms"
        )
    return template - np.mean(template[mask], axis=0, keepdims=True)


class WaveformArtifactStore:
    def __init__(
        self,
        analysis_dir: Path,
        *,
        scope_root: Path,
        template_cache_size: int = 8,
    ) -> None:
        if type(template_cache_size) is not int or template_cache_size < 1:
            raise ValueError("template_cache_size must be a positive integer")
        root = scope_root.resolve(strict=True)
        source = analysis_dir.resolve(strict=True)
        if not source.is_dir() or not is_within(source, root):
            raise WaveformArtifactError("Waveform artifact must stay within the RF root")
        self.scope_root = root
        self.analysis_dir = source
        self.manifest_path = source / "manifest.json"
        self.channels_path = source / "channels.csv"
        self.time_path = source / "waveform_time.csv"
        for path in (self.manifest_path, self.channels_path, self.time_path):
            if not path.is_file() or not is_within(path.resolve(), root):
                raise WaveformArtifactError(f"Required waveform file not found: {path}")

        try:
            raw = json.loads(
                self.manifest_path.read_text(encoding="utf-8"),
                parse_constant=_reject_constant,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except json.JSONDecodeError as exc:
            raise WaveformArtifactError(f"manifest.json is not valid JSON: {exc}") from exc
        self.manifest = self._validate_manifest(raw)
        self.channels = self._load_channels()
        self.channel_locations = np.array(
            [(item.x_um, item.y_um) for item in self.channels], dtype=np.float64
        )
        self.channel_shank_ids = np.array(
            [item.shank_id for item in self.channels], dtype=np.int64
        )
        self.sample_indices, self.time_ms, self.time_edges_ms = self._load_time()
        files = _mapping(self.manifest["files"], "manifest.files")
        self.units_path = self._confined_file(files["units"], "manifest.files.units")
        self.unit_summaries = self._load_units()
        self.unit_scope = str(_mapping(self.manifest["units"], "manifest.units")["scope"])
        self._template_cache_size = template_cache_size
        self._template_cache: OrderedDict[int, tuple[WaveformUnitSummary, np.ndarray, Path]] = OrderedDict()

    @classmethod
    def discover(
        cls,
        rf_path: Path,
        *,
        scope_root: Path,
        template_cache_size: int = 8,
    ) -> WaveformArtifactStore | None:
        directory = discover_waveform_artifact(rf_path, scope_root)
        return None if directory is None else cls(
            directory,
            scope_root=scope_root,
            template_cache_size=template_cache_size,
        )

    def _validate_manifest(self, raw: Any) -> dict[str, Any]:
        manifest = _mapping(raw, "manifest")
        if manifest.get("schema_name") != WAVEFORM_SCHEMA_NAME:
            raise WaveformArtifactError(
                f"Unsupported waveform schema name: {manifest.get('schema_name')!r}"
            )
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != WAVEFORM_SCHEMA_VERSION:
            raise WaveformArtifactError(
                f"Unsupported waveform schema version: {manifest.get('schema_version')!r}"
            )
        units = _mapping(manifest.get("units"), "manifest.units")
        if units.get("scope") not in _UNIT_SCOPES:
            raise WaveformArtifactError("manifest.units.scope is not supported")
        _json_integer(units.get("count"), "manifest.units.count", minimum=0)
        waveform = _mapping(manifest.get("waveform"), "manifest.waveform")
        count = _json_integer(
            waveform.get("num_samples"), "manifest.waveform.num_samples", minimum=2
        )
        before = _json_integer(
            waveform.get("nbefore"), "manifest.waveform.nbefore", minimum=0
        )
        if before > count:
            raise WaveformArtifactError("manifest.waveform.nbefore cannot exceed num_samples")
        recording = _mapping(manifest.get("recording"), "manifest.recording")
        _json_number(
            recording.get("sampling_frequency_hz"),
            "manifest.recording.sampling_frequency_hz",
            positive=True,
        )
        _json_integer(recording.get("num_frames"), "manifest.recording.num_frames", minimum=1)
        _json_number(
            recording.get("duration_minutes"),
            "manifest.recording.duration_minutes",
            positive=True,
        )
        files = _mapping(manifest.get("files"), "manifest.files")
        if not isinstance(files.get("units"), str) or not files["units"].strip():
            raise WaveformArtifactError("manifest.files.units must be a non-empty path")
        return manifest

    def _confined_file(self, raw: Any, label: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise WaveformArtifactError(f"{label} must be a non-empty relative path")
        relative = PurePosixPath(raw)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise WaveformArtifactError(f"{label} must stay within the waveform artifact")
        candidate = self.analysis_dir.joinpath(*relative.parts).resolve(strict=False)
        if not is_within(candidate, self.analysis_dir) or not is_within(candidate, self.scope_root):
            raise WaveformArtifactError(f"{label} must stay within the waveform artifact")
        if not candidate.is_file():
            raise WaveformArtifactError(f"Required waveform file not found: {candidate}")
        return candidate

    def _load_channels(self) -> tuple[WaveformChannel, ...]:
        rows = _read_exact_rows(self.channels_path, _CHANNEL_COLUMNS)
        if not rows:
            raise WaveformArtifactError("channels.csv must contain at least one channel")
        result: list[WaveformChannel] = []
        seen: set[int] = set()
        for row_number, row in enumerate(rows, start=2):
            try:
                item = WaveformChannel(
                    _csv_integer(row["channel_index"], "channel_index"),
                    _csv_integer(row["channel_id"], "channel_id"),
                    _csv_integer(row["raw_channel_index"], "raw_channel_index"),
                    _csv_float(row["x_um"], "x_um"),
                    _csv_float(row["y_um"], "y_um"),
                    _csv_integer(row["shank_id"], "shank_id"),
                )
            except WaveformArtifactError as exc:
                raise WaveformArtifactError(f"Invalid channels.csv row {row_number}: {exc}") from exc
            if item.channel_index != len(result):
                raise WaveformArtifactError("channels.csv channel_index must be contiguous and row ordered")
            if item.channel_id in seen:
                raise WaveformArtifactError(f"channels.csv contains duplicate channel_id {item.channel_id}")
            seen.add(item.channel_id)
            result.append(item)
        return tuple(result)

    def _load_time(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        rows = _read_exact_rows(self.time_path, _TIME_COLUMNS)
        waveform = _mapping(self.manifest["waveform"], "manifest.waveform")
        if len(rows) != int(waveform["num_samples"]):
            raise WaveformArtifactError("waveform_time.csv row count does not match manifest")
        indices: list[int] = []
        offsets: list[int] = []
        times: list[float] = []
        for row_number, row in enumerate(rows, start=2):
            try:
                index = _csv_integer(row["sample_index"], "sample_index")
                offset = _csv_integer(row["sample_offset"], "sample_offset")
                time = _csv_float(row["time_ms"], "time_ms")
            except WaveformArtifactError as exc:
                raise WaveformArtifactError(f"Invalid waveform_time.csv row {row_number}: {exc}") from exc
            if index != len(indices):
                raise WaveformArtifactError("waveform_time.csv sample_index must be contiguous")
            indices.append(index)
            offsets.append(offset)
            times.append(time)
        offset_array = np.asarray(offsets, dtype=np.int64)
        if offset_array[0] != -int(waveform["nbefore"]) or not np.all(np.diff(offset_array) == 1):
            raise WaveformArtifactError("waveform_time.csv sample_offset does not match manifest")
        time_array = np.asarray(times, dtype=np.float64)
        if not np.all(np.diff(time_array) > 0):
            raise WaveformArtifactError("waveform_time.csv time_ms must be strictly increasing")
        step = float(np.median(np.diff(time_array)))
        edges = np.r_[
            time_array[0] - step / 2,
            (time_array[:-1] + time_array[1:]) / 2,
            time_array[-1] + step / 2,
        ]
        return np.asarray(indices, dtype=np.int64), time_array, edges

    def _load_units(self) -> Mapping[int, WaveformUnitSummary]:
        rows = _read_exact_rows(self.units_path, _UNIT_COLUMNS)
        units_manifest = _mapping(self.manifest["units"], "manifest.units")
        if len(rows) != int(units_manifest["count"]):
            raise WaveformArtifactError("units.csv row count does not match manifest.units.count")
        result: dict[int, WaveformUnitSummary] = {}
        for row_number, row in enumerate(rows, start=2):
            try:
                unit_dir = PurePosixPath(row["unit_data_dir"])
                if unit_dir.is_absolute() or any(part in {"", ".", ".."} for part in unit_dir.parts):
                    raise WaveformArtifactError("unit_data_dir must stay within the waveform artifact")
                item = WaveformUnitSummary(
                    _csv_integer(row["unit_index"], "unit_index"),
                    _csv_integer(row["unit_id"], "unit_id"),
                    row["quality"].strip(),
                    _csv_integer(row["total_spike_count"], "total_spike_count"),
                    _csv_integer(row["selected_spike_count"], "selected_spike_count"),
                    _csv_float(row["time_coverage_percent"], "time_coverage_percent"),
                    _csv_integer(row["best_channel_index"], "best_channel_index"),
                    _csv_integer(row["best_channel_id"], "best_channel_id"),
                    _csv_float(row["best_channel_x_um"], "best_channel_x_um"),
                    _csv_float(row["best_channel_y_um"], "best_channel_y_um"),
                    _csv_float(row["max_ptp_uv"], "max_ptp_uv"),
                    unit_dir.as_posix(),
                )
            except (KeyError, WaveformArtifactError) as exc:
                raise WaveformArtifactError(f"Invalid units.csv row {row_number}: {exc}") from exc
            if item.unit_index != len(result):
                raise WaveformArtifactError("units.csv unit_index must be contiguous and row ordered")
            if item.unit_id in result:
                raise WaveformArtifactError(f"units.csv contains duplicate unit_id {item.unit_id}")
            if not item.quality:
                raise WaveformArtifactError("quality cannot be empty")
            if item.total_spike_count < 0 or item.selected_spike_count < 0:
                raise WaveformArtifactError("Spike counts must be non-negative")
            if item.selected_spike_count > item.total_spike_count:
                raise WaveformArtifactError("selected_spike_count cannot exceed total_spike_count")
            if not 0 <= item.time_coverage_percent <= 100 + 1e-9:
                raise WaveformArtifactError("time_coverage_percent must be between 0 and 100")
            if not 0 <= item.best_channel_index < len(self.channels):
                raise WaveformArtifactError("best_channel_index is out of range")
            best = self.channels[item.best_channel_index]
            if item.best_channel_id != best.channel_id:
                raise WaveformArtifactError("best_channel_id does not match channels.csv")
            if not math.isclose(item.best_channel_x_um, best.x_um, abs_tol=1e-6, rel_tol=0) or not math.isclose(item.best_channel_y_um, best.y_um, abs_tol=1e-6, rel_tol=0):
                raise WaveformArtifactError("best channel coordinates do not match channels.csv")
            if item.max_ptp_uv < 0:
                raise WaveformArtifactError("max_ptp_uv must be non-negative")
            result[item.unit_id] = item
        return result

    def _template_path(self, summary: WaveformUnitSummary) -> Path:
        relative = PurePosixPath(summary.unit_data_dir) / "template_uv.csv.gz"
        candidate = self.analysis_dir.joinpath(*relative.parts).resolve(strict=False)
        if not is_within(candidate, self.analysis_dir) or not is_within(candidate, self.scope_root):
            raise WaveformArtifactError("template path must stay within the waveform artifact")
        if not candidate.is_file():
            raise WaveformArtifactError(f"Required waveform file not found: {candidate}")
        return candidate

    def source_paths_for_unit(self, unit_id: int) -> tuple[Path, ...]:
        summary = self.summary_for(unit_id)
        return (
            self.manifest_path,
            self.channels_path,
            self.time_path,
            self.units_path,
            self._template_path(summary),
        )

    def summary_for(self, unit_id: int) -> WaveformUnitSummary:
        try:
            return self.unit_summaries[int(unit_id)]
        except (KeyError, TypeError, ValueError) as exc:
            raise KeyError(
                f"Unit {unit_id} is not available in this {self.unit_scope} waveform artifact"
            ) from exc

    def _load_template(self, summary: WaveformUnitSummary) -> tuple[np.ndarray, Path]:
        path = self._template_path(summary)
        expected_header = (
            "sample_index",
            *(f"chidx_{index:03d}_uv" for index in range(len(self.channels))),
        )
        values = np.empty((len(self.time_ms), len(self.channels)), dtype=np.float64)
        try:
            with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                try:
                    actual = tuple(next(reader))
                except StopIteration as exc:
                    raise WaveformArtifactError(f"{path.name} is missing a header") from exc
                if actual != expected_header:
                    raise WaveformArtifactError(f"{path.name} channel header does not match channels.csv")
                row_count = 0
                for csv_row_number, row in enumerate(reader, start=2):
                    if row_count >= len(self.time_ms) or len(row) != len(expected_header):
                        raise WaveformArtifactError(f"{path.name} row {csv_row_number} has the wrong shape")
                    if _csv_integer(row[0], "sample_index") != int(self.sample_indices[row_count]):
                        raise WaveformArtifactError(f"{path.name} sample_index does not match waveform_time.csv")
                    for channel_index, raw in enumerate(row[1:]):
                        values[row_count, channel_index] = _csv_float(raw, f"channel {channel_index} amplitude")
                    row_count += 1
        except (gzip.BadGzipFile, EOFError) as exc:
            raise WaveformArtifactError(f"{path.name} is not a valid gzip CSV") from exc
        if row_count != len(self.time_ms):
            raise WaveformArtifactError(f"{path.name} row count does not match waveform_time.csv")
        return values, path

    def load_unit_template(self, unit_id: int) -> tuple[WaveformUnitSummary, np.ndarray, Path]:
        summary = self.summary_for(unit_id)
        cached = self._template_cache.get(summary.unit_id)
        if cached is not None:
            self._template_cache.move_to_end(summary.unit_id)
            return cached
        values, path = self._load_template(summary)
        loaded = (summary, values, path)
        self._template_cache[summary.unit_id] = loaded
        while len(self._template_cache) > self._template_cache_size:
            self._template_cache.popitem(last=False)
        return loaded

    def payload_for(
        self,
        unit_id: int,
        mode: LocalChannelMode = DEFAULT_WAVEFORM_CHANNEL_MODE,
        local_channel_count: int = DEFAULT_LOCAL_CHANNEL_COUNT,
        *,
        baseline_end_ms: float = DEFAULT_BASELINE_END_MS,
    ) -> WaveformPayload:
        summary, template, _path = self.load_unit_template(unit_id)
        corrected = baseline_correct_template(
            template, self.time_ms, baseline_end_ms=baseline_end_ms
        )
        indices = select_local_channel_indices(
            self.channel_locations,
            self.channel_shank_ids,
            summary.best_channel_index,
            mode,
            local_channel_count,
        )
        channels = tuple(self.channels[int(index)] for index in indices)
        best_rows = np.flatnonzero(indices == summary.best_channel_index)
        if len(best_rows) != 1:
            raise WaveformArtifactError("Local channel selection lost the best channel")
        limit = max(float(np.max(np.abs(corrected))), float(np.finfo(np.float64).eps))
        return WaveformPayload(
            source_dir=self.analysis_dir,
            summary=summary,
            mode=mode,
            local_channel_count=local_channel_count,
            baseline_end_ms=float(baseline_end_ms),
            time_ms=self.time_ms,
            time_edges_ms=self.time_edges_ms,
            values_uv=corrected[:, indices].T,
            channels=channels,
            best_channel_index=summary.best_channel_index,
            best_channel_row=int(best_rows[0]),
            amplitude_limit_uv=limit,
        )


def shared_amplitude_limit(
    store: WaveformArtifactStore,
    unit_ids: Sequence[int],
    mode: LocalChannelMode,
) -> float | None:
    limits: list[float] = []
    for unit_id in unit_ids:
        try:
            limits.append(store.payload_for(int(unit_id), mode).amplitude_limit_uv)
        except (KeyError, OSError, WaveformArtifactError):
            continue
    return max(limits) if limits else None
