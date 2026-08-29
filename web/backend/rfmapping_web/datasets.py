from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np
from ijson.common import JSONError, ObjectBuilder

try:
    from ijson.backends import yajl2_cffi as IJSON_BACKEND
except ImportError:  # pragma: no cover - production installs the CFFI backend.
    from ijson.backends import python as IJSON_BACKEND

from .companions import CompanionSet, discover_companions
from .paths import has_supported_rf_suffix


class DatasetValidationError(ValueError):
    """The selected file is not a valid RF Mapping dataset."""


class DatasetChangedError(RuntimeError):
    """The selected source changed after it was opened."""


METADATA_FIELDS = {
    "unitsSpikeCountsSize",
    "unitPool",
    "xPositions",
    "yPositions",
    "VSTimeWindow",
    "timeWindowMs",
    "timeBinWidthMs",
    "timeBinEdges",
    "isVerticalBar",
    "responseUnits",
    "responseNormalization",
    "spikeCountDefinition",
    "occupancyTimeSec",
    "occupancyTimeSecSize",
    "occupancyTimeDefinition",
}
REQUIRED_TOP_LEVEL = {
    "unitsSpikeCounts",
    "unitsSpikeCountsSize",
    "unitPool",
    "xPositions",
    "yPositions",
    "timeBinEdges",
    "responseUnits",
    "responseNormalization",
    "spikeCountDefinition",
    "occupancyTimeSec",
    "occupancyTimeSecSize",
    "occupancyTimeDefinition",
}
CACHE_SCHEMA_VERSION = 2

EXPECTED_RESPONSE_UNITS = "spike_count"
EXPECTED_RESPONSE_NORMALIZATION = "none"
EXPECTED_SPIKE_COUNT_DEFINITION = (
    "each_qualifying_trial_contributes_once_per_final_spatial_bin"
)
EXPECTED_OCCUPANCY_DEFINITION = (
    "sum_of_qualifying_trial_durations_per_final_spatial_bin"
)


def _source_signature(path: Path) -> dict[str, int | str]:
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "device": stat.st_dev,
        "inode": stat.st_ino,
    }


def _cache_key(signature: dict[str, int | str]) -> str:
    serialized = json.dumps(signature, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_metadata_stream(path: Path) -> dict[str, Any]:
    builders: dict[str, ObjectBuilder] = {}
    seen: set[str] = set()
    try:
        with path.open("rb") as handle:
            for prefix, event, value in IJSON_BACKEND.parse(handle, use_float=True):
                if prefix == "" and event == "map_key":
                    if value in seen:
                        raise DatasetValidationError(
                            f"Duplicate top-level JSON key: {value}"
                        )
                    seen.add(value)
                    continue
                root_name = prefix.split(".", 1)[0]
                if root_name in METADATA_FIELDS:
                    builder = builders.setdefault(root_name, ObjectBuilder())
                    builder.event(event, value)
    except DatasetValidationError:
        raise
    except (OSError, JSONError, TypeError, ValueError, OverflowError) as exc:
        raise DatasetValidationError(f"Unable to parse RF JSON: {exc}") from exc

    missing = sorted(REQUIRED_TOP_LEVEL - seen)
    if missing:
        raise DatasetValidationError(f"Missing JSON keys: {', '.join(missing)}")
    result: dict[str, Any] = {}
    for name in METADATA_FIELDS:
        builder = builders.get(name)
        if builder is not None:
            result[name] = builder.value
    return result


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DatasetValidationError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise DatasetValidationError(f"{label} must be finite")
    return parsed


def _integer(value: Any, label: str) -> int:
    parsed = _number(value, label)
    if not parsed.is_integer():
        raise DatasetValidationError(f"{label} must be an integer")
    return int(parsed)


def _flat_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list) or any(isinstance(item, (list, dict)) for item in value):
        raise DatasetValidationError(f"{label} must be a one-dimensional array")
    return value


def _coordinate_axis(value: Any, label: str, expected_length: int) -> list[float]:
    """Normalize MATLAB's scalar JSON encoding for a singleton spatial axis."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if expected_length != 1:
            raise DatasetValidationError(
                f"{label} may be scalar only when its spatial dimension is 1"
            )
        source = [value]
    else:
        source = _flat_list(value, label)
    return [_number(item, f"{label} value") for item in source]


def _matlab_vector(
    value: Any, label: str, expected_length: int, *, integer: bool = False
) -> list[float] | list[int]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if expected_length != 1:
            raise DatasetValidationError(
                f"{label} may be scalar only when its declared dimension is 1"
            )
        source = [value]
    else:
        source = _flat_list(value, label)
    if integer:
        return [_integer(item, f"{label} value") for item in source]
    return [_number(item, f"{label} value") for item in source]


def _occupancy_matrix(value: Any, n_y: int, n_x: int) -> list[list[float]]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if n_y != 1 or n_x != 1:
            raise DatasetValidationError(
                "occupancyTimeSec must be a y-by-x array"
            )
        rows: list[list[Any]] = [[value]]
    elif isinstance(value, list):
        if all(not isinstance(item, list) for item in value):
            if n_y == 1 and len(value) == n_x:
                rows = [value]
            elif n_x == 1 and len(value) == n_y:
                rows = [[item] for item in value]
            else:
                raise DatasetValidationError(
                    "occupancyTimeSec dimensions do not match unitsSpikeCountsSize"
                )
        elif all(isinstance(item, list) for item in value):
            rows = value
        else:
            raise DatasetValidationError(
                "occupancyTimeSec must be a rectangular y-by-x array"
            )
    else:
        raise DatasetValidationError("occupancyTimeSec must be a y-by-x array")
    if len(rows) != n_y or any(len(row) != n_x for row in rows):
        raise DatasetValidationError(
            "occupancyTimeSec dimensions do not match unitsSpikeCountsSize"
        )
    normalized: list[list[float]] = []
    for y_index, row in enumerate(rows):
        normalized_row: list[float] = []
        for x_index, item in enumerate(row):
            parsed = _number(item, f"occupancyTimeSec[{y_index}][{x_index}]")
            if parsed < 0:
                raise DatasetValidationError(
                    "occupancyTimeSec values must be non-negative"
                )
            normalized_row.append(parsed)
        normalized.append(normalized_row)
    return normalized


def _normalize_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    size_values = _flat_list(raw["unitsSpikeCountsSize"], "unitsSpikeCountsSize")
    if len(size_values) != 4:
        raise DatasetValidationError("unitsSpikeCountsSize must contain four values")
    shape = [_integer(value, "unitsSpikeCountsSize") for value in size_values]
    if any(value <= 0 for value in shape):
        raise DatasetValidationError("unitsSpikeCountsSize values must be positive")
    n_units, n_y, n_x, n_bins = shape

    unit_pool = _matlab_vector(raw["unitPool"], "unitPool", n_units, integer=True)
    x_positions = _coordinate_axis(raw["xPositions"], "xPositions", n_x)
    y_positions = _coordinate_axis(raw["yPositions"], "yPositions", n_y)
    time_bin_edges = [
        _number(value, "timeBinEdges value")
        for value in _flat_list(raw["timeBinEdges"], "timeBinEdges")
    ]
    if len(unit_pool) != n_units:
        raise DatasetValidationError("unitPool length does not match unit count")
    if len(set(unit_pool)) != len(unit_pool):
        raise DatasetValidationError("unitPool must contain unique cluster IDs")
    if len(x_positions) != n_x:
        raise DatasetValidationError("xPositions length does not match x dimension")
    if len(y_positions) != n_y:
        raise DatasetValidationError("yPositions length does not match y dimension")
    if len(time_bin_edges) != n_bins + 1:
        raise DatasetValidationError("timeBinEdges must contain nBins + 1 edges")
    if any(left >= right for left, right in zip(time_bin_edges, time_bin_edges[1:])):
        raise DatasetValidationError("timeBinEdges must be strictly increasing")

    vs_time_window: list[float] | None = None
    if "VSTimeWindow" in raw:
        vs_time_window = [
            _number(value, "VSTimeWindow value")
            for value in _flat_list(raw["VSTimeWindow"], "VSTimeWindow")
        ]
        if len(vs_time_window) != 2:
            raise DatasetValidationError("VSTimeWindow must contain two values")
        if not math.isclose(
            vs_time_window[0], time_bin_edges[0], abs_tol=1e-12
        ) or not math.isclose(
            vs_time_window[1], time_bin_edges[-1], abs_tol=1e-12
        ):
            raise DatasetValidationError(
                "VSTimeWindow must match the first and last timeBinEdges"
            )
    if "timeWindowMs" in raw:
        time_window_ms = [
            _number(value, "timeWindowMs value")
            for value in _flat_list(raw["timeWindowMs"], "timeWindowMs")
        ]
        if len(time_window_ms) != 2:
            raise DatasetValidationError("timeWindowMs must contain two values")
        reference_window = vs_time_window or [time_bin_edges[0], time_bin_edges[-1]]
        for seconds, milliseconds in zip(reference_window, time_window_ms):
            if not math.isclose(milliseconds, seconds * 1000.0, abs_tol=1e-9):
                raise DatasetValidationError(
                    "timeWindowMs must match the time-bin bounds in milliseconds"
                )
    if "timeBinWidthMs" in raw:
        time_bin_width_ms = _number(raw["timeBinWidthMs"], "timeBinWidthMs")
        if time_bin_width_ms <= 0 or any(
            not math.isclose(
                (right - left) * 1000.0,
                time_bin_width_ms,
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            for left, right in zip(time_bin_edges, time_bin_edges[1:])
        ):
            raise DatasetValidationError(
                "timeBinEdges must use the declared uniform timeBinWidthMs"
            )

    if "isVerticalBar" in raw and type(raw["isVerticalBar"]) is not bool:
        raise DatasetValidationError("isVerticalBar must be boolean")
    fixed_strings = {
        "responseUnits": EXPECTED_RESPONSE_UNITS,
        "responseNormalization": EXPECTED_RESPONSE_NORMALIZATION,
        "spikeCountDefinition": EXPECTED_SPIKE_COUNT_DEFINITION,
        "occupancyTimeDefinition": EXPECTED_OCCUPANCY_DEFINITION,
    }
    for field, expected in fixed_strings.items():
        if raw[field] != expected:
            raise DatasetValidationError(f"{field} must be {expected!r}")

    occupancy_size = [
        _integer(value, "occupancyTimeSecSize value")
        for value in _flat_list(raw["occupancyTimeSecSize"], "occupancyTimeSecSize")
    ]
    if occupancy_size != [n_y, n_x]:
        raise DatasetValidationError(
            "occupancyTimeSecSize must equal the y-by-x unitsSpikeCountsSize dimensions"
        )
    occupancy_time_sec = _occupancy_matrix(raw["occupancyTimeSec"], n_y, n_x)
    if not any(value > 0 for row in occupancy_time_sec for value in row):
        raise DatasetValidationError(
            "occupancyTimeSec must contain at least one positive value"
        )
    return {
        "shape": shape,
        "unitPool": unit_pool,
        "xPositions": x_positions,
        "yPositions": y_positions,
        "timeBinEdges": time_bin_edges,
        "occupancyTimeSec": occupancy_time_sec,
        "isVerticalBar": raw.get("isVerticalBar"),
        "responseUnits": raw["responseUnits"],
        "responseNormalization": raw["responseNormalization"],
        "spikeCountDefinition": raw["spikeCountDefinition"],
        "occupancyTimeDefinition": raw["occupancyTimeDefinition"],
    }


def _write_counts_stream(
    source: Path,
    destination: Path,
    metadata: dict[str, Any],
) -> None:
    shape = tuple(metadata["shape"])
    n_units, n_y, n_x, n_bins = shape
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    unit_count = 0
    occupancy = np.asarray(metadata["occupancyTimeSec"], dtype=np.float64)
    zero_occupancy_mask = occupancy == 0
    try:
        with temporary.open("wb") as output:
            try:
                with source.open("rb") as handle:
                    units = IJSON_BACKEND.items(
                        handle, "unitsSpikeCounts.item", use_float=True
                    )
                    for unit_index, unit in enumerate(units):
                        if unit_index >= n_units:
                            raise DatasetValidationError(
                                "unitsSpikeCounts first dimension exceeds unitsSpikeCountsSize"
                            )
                        if not _counts_are_numeric(unit):
                            raise DatasetValidationError(
                                f"Unit {unit_index} contains non-numeric values"
                            )
                        try:
                            array = np.asarray(unit, dtype="<f8")
                        except (TypeError, ValueError, OverflowError) as exc:
                            raise DatasetValidationError(
                                f"Unit {unit_index} contains non-numeric values"
                            ) from exc
                        expected = (n_y, n_x, n_bins)
                        if array.shape != expected:
                            raise DatasetValidationError(
                                f"Unit {unit_index} has shape {array.shape}, expected {expected}"
                            )
                        if not np.all(np.isfinite(array)) or np.any(array < 0):
                            raise DatasetValidationError(
                                f"Unit {unit_index} contains non-finite or negative counts"
                            )
                        if np.any(array != np.floor(array)):
                            raise DatasetValidationError(
                                f"Unit {unit_index} contains non-integer spike counts"
                            )
                        if np.any(array[zero_occupancy_mask, :] != 0):
                            raise DatasetValidationError(
                                "occupancyTimeSec is zero where spike counts are nonzero"
                            )
                        output.write(array.tobytes(order="C"))
                        unit_count += 1
            except (JSONError, OSError, TypeError) as exc:
                raise DatasetValidationError(f"Unable to parse unitsSpikeCounts: {exc}") from exc
            if unit_count != n_units:
                raise DatasetValidationError(
                    "unitsSpikeCounts first dimension does not match unitsSpikeCountsSize"
                )
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _counts_are_numeric(value: Any) -> bool:
    if isinstance(value, list):
        return all(_counts_are_numeric(child) for child in value)
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class CacheEntry:
    key: str
    data_path: Path
    metadata_path: Path
    metadata: dict[str, Any]


class MemmapCache:
    def __init__(
        self,
        root: Path,
        max_bytes: int,
        *,
        eviction_lock: threading.RLock | None = None,
        on_evict: Callable[[set[Path]], None] | None = None,
    ):
        self.root = root
        self.max_bytes = max_bytes
        self.eviction_lock = eviction_lock or threading.RLock()
        self.on_evict = on_evict

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.root / f"{key}.f64", self.root / f"{key}.meta.json"

    def _load_valid(
        self,
        key: str,
        signature: dict[str, int | str],
        data_path: Path,
        metadata_path: Path,
    ) -> dict[str, Any] | None:
        try:
            with metadata_path.open("r", encoding="utf-8") as handle:
                metadata = json.load(handle)
            expected_bytes = math.prod(metadata["shape"]) * 8
            if (
                metadata.get("schemaVersion") != CACHE_SCHEMA_VERSION
                or metadata.get("cacheKey") != key
                or metadata.get("source") != signature
                or data_path.stat().st_size != expected_bytes
            ):
                return None
            return metadata
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def get_or_build(self, source: Path) -> CacheEntry:
        self.root.mkdir(parents=True, exist_ok=True)
        signature = _source_signature(source)
        key = _cache_key(signature)
        data_path, metadata_path = self._paths(key)
        lock_path = self.root / f".{key}.lock"
        with _exclusive_lock(lock_path):
            metadata = self._load_valid(
                key, signature, data_path, metadata_path
            )
            if metadata is None:
                raw = _read_metadata_stream(source)
                normalized = _normalize_metadata(raw)
                expected_bytes = math.prod(normalized["shape"]) * 8
                if expected_bytes > self.max_bytes:
                    raise DatasetValidationError(
                        f"Decoded RF dataset exceeds cache limit ({self.max_bytes} bytes)"
                    )
                _write_counts_stream(source, data_path, normalized)
                now = time.time()
                metadata = {
                    "schemaVersion": CACHE_SCHEMA_VERSION,
                    "cacheKey": key,
                    "source": signature,
                    **normalized,
                    "createdAt": now,
                    "accessedAt": now,
                }
                _atomic_json(metadata_path, metadata)
            else:
                metadata["accessedAt"] = time.time()
                _atomic_json(metadata_path, metadata)
        try:
            current_signature = _source_signature(source)
        except FileNotFoundError as exc:
            raise DatasetChangedError("Dataset source was deleted while opening") from exc
        if current_signature != signature:
            raise DatasetChangedError("Dataset source changed while opening; try again")
        self._evict(exclude=key)
        return CacheEntry(key, data_path, metadata_path, metadata)

    def _evict(self, *, exclude: str) -> None:
        with _exclusive_lock(self.root / ".eviction.lock"):
            entries: list[tuple[float, str, Path, Path, int]] = []
            total = 0
            for metadata_path in self.root.glob("*.meta.json"):
                key = metadata_path.name[: -len(".meta.json")]
                data_path = self.root / f"{key}.f64"
                try:
                    with metadata_path.open("r", encoding="utf-8") as handle:
                        metadata = json.load(handle)
                    size = metadata_path.stat().st_size + data_path.stat().st_size
                    accessed = float(metadata.get("accessedAt", 0))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    continue
                total += size
                entries.append((accessed, key, data_path, metadata_path, size))
            with self.eviction_lock:
                evicted: set[Path] = set()
                for _accessed, key, data_path, metadata_path, size in sorted(entries):
                    if total <= self.max_bytes:
                        break
                    if key == exclude:
                        continue
                    evicted.add(data_path)
                    data_path.unlink(missing_ok=True)
                    metadata_path.unlink(missing_ok=True)
                    total -= size
                if evicted and self.on_evict is not None:
                    self.on_evict(evicted)


@dataclass
class DatasetRecord:
    dataset_id: str
    source: Path
    public_source_path: str
    scope_root: Path
    source_signature: dict[str, int | str]
    cache: CacheEntry
    companions: CompanionSet


class DatasetStore:
    def __init__(self, cache_root: Path, cache_max_bytes: int):
        self._lock = threading.RLock()
        self._records: dict[str, DatasetRecord] = {}
        self.cache = MemmapCache(
            cache_root,
            cache_max_bytes,
            eviction_lock=self._lock,
            on_evict=self._invalidate_cache_paths,
        )

    def _invalidate_cache_paths(self, paths: set[Path]) -> None:
        with self._lock:
            stale = [
                dataset_id
                for dataset_id, record in self._records.items()
                if record.cache.data_path in paths
            ]
            for dataset_id in stale:
                self._records.pop(dataset_id, None)

    def open(
        self,
        source: Path,
        *,
        public_source_path: str,
        scope_root: Path,
    ) -> DatasetRecord:
        if not has_supported_rf_suffix(source) or source.name.startswith("._"):
            raise DatasetValidationError(
                "RF dataset must be a non-AppleDouble .rfmap or .json file"
            )
        cache = self.cache.get_or_build(source)
        dataset_id = uuid.uuid4().hex
        record = DatasetRecord(
            dataset_id=dataset_id,
            source=source,
            public_source_path=public_source_path,
            scope_root=scope_root,
            source_signature=cache.metadata["source"],
            cache=cache,
            companions=discover_companions(source, scope_root),
        )
        with self._lock:
            if not cache.data_path.is_file() or not cache.metadata_path.is_file():
                raise DatasetChangedError("Dataset cache was evicted while opening; retry")
            self._records[dataset_id] = record
        return record

    def get(self, dataset_id: str) -> DatasetRecord:
        with self._lock:
            record = self._records.get(dataset_id)
            if record is None:
                raise KeyError(dataset_id)
            try:
                current = _source_signature(record.source)
            except FileNotFoundError as exc:
                raise DatasetChangedError("Dataset source was deleted; reopen it") from exc
            if current != record.source_signature:
                raise DatasetChangedError("Dataset source changed; reopen it")
            if not record.cache.data_path.is_file():
                self._records.pop(dataset_id, None)
                raise DatasetChangedError("Dataset cache was evicted; reopen it")
            return record

    @staticmethod
    def response_metadata(record: DatasetRecord) -> dict[str, Any]:
        metadata = record.cache.metadata
        response = {
            "id": record.dataset_id,
            "name": record.source.name,
            "sourcePath": record.public_source_path,
            "shape": metadata["shape"],
            "unitPool": metadata["unitPool"],
            "xPositions": metadata["xPositions"],
            "yPositions": metadata["yPositions"],
            "timeBinEdges": metadata["timeBinEdges"],
            "occupancyTimeSec": metadata["occupancyTimeSec"],
            "responseUnits": metadata["responseUnits"],
            "responseNormalization": metadata["responseNormalization"],
            "capabilities": {
                "probe": record.companions.has_probe,
                "hd": record.companions.has_hd,
                "waveform": record.companions.has_waveform,
                "occupancy": True,
            },
        }
        if metadata["isVerticalBar"] is not None:
            response["isVerticalBar"] = metadata["isVerticalBar"]
        return response

    def unit_bytes(self, record: DatasetRecord, cluster_id: int) -> tuple[bytes, list[int]]:
        with self._lock:
            if self._records.get(record.dataset_id) is not record:
                raise DatasetChangedError("Dataset cache was evicted; reopen it")
            metadata = record.cache.metadata
            try:
                unit_index = metadata["unitPool"].index(cluster_id)
            except ValueError as exc:
                raise KeyError(cluster_id) from exc
            _n_units, n_y, n_x, n_bins = metadata["shape"]
            values_per_unit = n_y * n_x * n_bins
            try:
                mapped = np.memmap(
                    record.cache.data_path,
                    dtype="<f8",
                    mode="r",
                    offset=unit_index * values_per_unit * 8,
                    shape=(n_y, n_x, n_bins),
                    order="C",
                )
                payload = mapped.tobytes(order="C")
                del mapped
            except OSError as exc:
                self._records.pop(record.dataset_id, None)
                raise DatasetChangedError("Dataset cache was evicted; reopen it") from exc
            return payload, [n_y, n_x, n_bins]

    def unit_array(self, record: DatasetRecord, cluster_id: int) -> tuple[int, np.ndarray]:
        """Return one validated unit as an in-memory y-by-x-by-time array."""

        with self._lock:
            if self._records.get(record.dataset_id) is not record:
                raise DatasetChangedError("Dataset cache was evicted; reopen it")
            metadata = record.cache.metadata
            try:
                unit_index = metadata["unitPool"].index(cluster_id)
            except ValueError as exc:
                raise KeyError(cluster_id) from exc
            _n_units, n_y, n_x, n_bins = metadata["shape"]
            values_per_unit = n_y * n_x * n_bins
            try:
                mapped = np.memmap(
                    record.cache.data_path,
                    dtype="<f8",
                    mode="r",
                    offset=unit_index * values_per_unit * 8,
                    shape=(n_y, n_x, n_bins),
                    order="C",
                )
                values = np.array(mapped, dtype=np.float64, copy=True, order="C")
                del mapped
            except OSError as exc:
                self._records.pop(record.dataset_id, None)
                raise DatasetChangedError("Dataset cache was evicted; reopen it") from exc
            return unit_index, values
