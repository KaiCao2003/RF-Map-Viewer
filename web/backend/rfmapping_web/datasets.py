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
from decimal import Decimal
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
    "occupancyTimeSec",
}
CACHE_SCHEMA_VERSION = 3
COUNT_DTYPES = ("|u1", "<u2", "<u4", "<u8")
UNCLAIMED_DATASET_TIMEOUT_SECONDS = 60.0

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
    maximum_count: int | Decimal = 0
    try:
        with path.open("rb") as handle:
            # Decimal mode also keeps unsigned 64-bit JSON integers intact;
            # YAJL's float mode rejects integers above signed int64.
            for prefix, event, value in IJSON_BACKEND.parse(handle, use_float=False):
                if prefix == "" and event == "map_key":
                    if value in seen:
                        raise DatasetValidationError(
                            f"Duplicate top-level JSON key: {value}"
                        )
                    seen.add(value)
                    continue
                root_name = prefix.split(".", 1)[0]
                if root_name == "unitsSpikeCounts" and event == "number":
                    # This pass already visits every count. Discover its storage
                    # width without materializing the dataset or adding a pass.
                    maximum_count = max(maximum_count, value)
                if root_name in METADATA_FIELDS:
                    builder = builders.setdefault(root_name, ObjectBuilder())
                    builder.event(
                        event, float(value) if isinstance(value, Decimal) else value
                    )
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
    for dtype in COUNT_DTYPES:
        if maximum_count <= np.iinfo(dtype).max:
            result["countsDtype"] = dtype
            break
    else:
        raise DatasetValidationError("Spike counts must be representable as uint64")
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
        # MATLAB JSON and indexed exports can omit these descriptions;
        # explicit markers must still agree with the raw-count contract.
        if field in (
            "spikeCountDefinition", "occupancyTimeDefinition"
        ) and field not in raw:
            continue
        if raw.get(field) != expected:
            raise DatasetValidationError(f"{field} must be {expected!r}")

    if "occupancyTimeSecSize" in raw:
        occupancy_size = [
            _integer(value, "occupancyTimeSecSize value")
            for value in _flat_list(raw["occupancyTimeSecSize"], "occupancyTimeSecSize")
        ]
        if occupancy_size != [n_y, n_x]:
            raise DatasetValidationError(
                "occupancyTimeSecSize must equal the y-by-x unitsSpikeCountsSize dimensions"
            )
    # Validate occupancy against the count axes even without a size marker.
    occupancy_time_sec = _occupancy_matrix(raw["occupancyTimeSec"], n_y, n_x)
    if not any(value > 0 for row in occupancy_time_sec for value in row):
        raise DatasetValidationError(
            "occupancyTimeSec must contain at least one positive value"
        )
    return {
        "shape": shape,
        "countsDtype": raw["countsDtype"],
        "unitPool": unit_pool,
        "xPositions": x_positions,
        "yPositions": y_positions,
        "timeBinEdges": time_bin_edges,
        "occupancyTimeSec": occupancy_time_sec,
        "isVerticalBar": raw.get("isVerticalBar"),
        "responseUnits": raw["responseUnits"],
        "responseNormalization": raw["responseNormalization"],
        **{
            field: raw[field]
            for field in ("spikeCountDefinition", "occupancyTimeDefinition")
            if field in raw
        },
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
                        handle, "unitsSpikeCounts.item", use_float=False
                    )
                    for unit_index, unit in enumerate(units):
                        if unit_index >= n_units:
                            raise DatasetValidationError(
                                "unitsSpikeCounts first dimension exceeds unitsSpikeCountsSize"
                            )
                        array = _compact_unit_counts(
                            unit, (n_y, n_x, n_bins), metadata["countsDtype"], unit_index
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


def _compact_unit_counts(
    unit: Any, shape: tuple[int, int, int], dtype: str, unit_index: int
) -> np.ndarray:
    """Validate one streamed unit before copying directly to compact storage.

    Integer JSON values must not pass through float64: that silently rounds
    counts above 2**53 and can overflow the selected unsigned storage width.
    """

    n_y, n_x, n_bins = shape
    if (
        not isinstance(unit, list)
        or len(unit) != n_y
        or any(not isinstance(row, list) or len(row) != n_x for row in unit)
        or any(
            not isinstance(histogram, list) or len(histogram) != n_bins
            for row in unit
            for histogram in row
        )
    ):
        raise DatasetValidationError(
            f"Unit {unit_index} has an invalid shape, expected {shape}"
        )
    maximum = np.iinfo(dtype).max
    for row in unit:
        for histogram in row:
            scalar_types = set(map(type, histogram))
            if not scalar_types.issubset({int, float, Decimal}):
                raise DatasetValidationError(
                    f"Unit {unit_index} contains non-numeric values"
                )
            if float in scalar_types or Decimal in scalar_types:
                for value in histogram:
                    finite = (
                        value.is_finite()
                        if isinstance(value, Decimal)
                        else math.isfinite(value)
                    )
                    if not finite or value < 0:
                        raise DatasetValidationError(
                            f"Unit {unit_index} contains non-finite or negative counts"
                        )
                    integral = (
                        value == value.to_integral_value()
                        if isinstance(value, Decimal)
                        else type(value) is int or value.is_integer()
                    )
                    if not integral:
                        raise DatasetValidationError(
                            f"Unit {unit_index} contains non-integer spike counts"
                        )
            if min(histogram) < 0:
                raise DatasetValidationError(
                    f"Unit {unit_index} contains non-finite or negative counts"
                )
            if max(histogram) > maximum:
                raise DatasetValidationError(
                    f"Unit {unit_index} spike counts exceed their storage width"
                )
    try:
        return np.asarray(unit, dtype=dtype)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DatasetValidationError(f"Unable to parse unitsSpikeCounts: {exc}") from exc


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
        return self.root / f"{key}.counts", self.root / f"{key}.meta.json"

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
            if metadata.get("countsDtype") not in COUNT_DTYPES:
                return None
            expected_bytes = math.prod(metadata["shape"]) * np.dtype(
                metadata["countsDtype"]
            ).itemsize
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
                expected_bytes = math.prod(normalized["shape"]) * np.dtype(
                    normalized["countsDtype"]
                ).itemsize
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
                # Older releases stored the same source entirely as float64.
                # Remove that obsolete entry only after its replacement exists.
                (self.root / f"{key}.f64").unlink(missing_ok=True)
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
                try:
                    with metadata_path.open("r", encoding="utf-8") as handle:
                        metadata = json.load(handle)
                    suffix = (
                        ".counts"
                        if metadata.get("schemaVersion") == CACHE_SCHEMA_VERSION
                        else ".f64"
                    )
                    data_path = self.root / f"{key}{suffix}"
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
    indexed: Any = None


class DatasetStore:
    def __init__(self, cache_root: Path, cache_max_bytes: int):
        self._lock = threading.RLock()
        self._records: dict[str, DatasetRecord] = {}
        self._unclaimed: dict[str, threading.Timer] = {}
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
        with source.open("rb") as handle:
            indexed_format = handle.read(4) == b"PK\x03\x04"
        indexed = None
        if indexed_format:
            from .indexed import IndexedUnits

            indexed = IndexedUnits(source, self.cache.max_bytes)
            cache = CacheEntry(_cache_key(indexed.signature), source, source, indexed.metadata)
        else:
            cache = self.cache.get_or_build(source)
        dataset_id = uuid.uuid4().hex
        try:
            record = DatasetRecord(
                dataset_id=dataset_id,
                source=source,
                public_source_path=public_source_path,
                scope_root=scope_root,
                source_signature=cache.metadata["source"],
                cache=cache,
                companions=discover_companions(source, scope_root),
                indexed=indexed,
            )
            with self._lock:
                if not cache.data_path.is_file() or not cache.metadata_path.is_file():
                    raise DatasetChangedError("Dataset cache was evicted while opening; retry")
                self._records[dataset_id] = record
                if indexed is not None:
                    # A response can be lost after the HTTP disconnect check.
                    # Keep unclaimed RAM caches only until the client first uses them.
                    timer = threading.Timer(
                        UNCLAIMED_DATASET_TIMEOUT_SECONDS,
                        self._expire_unclaimed,
                        args=(dataset_id,),
                    )
                    timer.daemon = True
                    self._unclaimed[dataset_id] = timer
                    timer.start()
                    indexed.start()
        except BaseException:
            self.close(dataset_id)
            if indexed is not None:
                indexed.close()
            raise
        return record

    def _expire_unclaimed(self, dataset_id: str) -> None:
        with self._lock:
            if dataset_id in self._unclaimed:
                self.close(dataset_id)

    def close(self, dataset_id: str) -> None:
        with self._lock:
            timer = self._unclaimed.pop(dataset_id, None)
            if timer is not None:
                timer.cancel()
            record = self._records.pop(dataset_id, None)
        if record is not None and record.indexed is not None:
            record.indexed.close()

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
            timer = self._unclaimed.pop(dataset_id, None)
            if timer is not None:
                timer.cancel()
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
            "cacheProgress": record.indexed.status() if record.indexed is not None else {
                "indexed": False, "cachedUnits": len(metadata["unitPool"]),
                "totalUnits": len(metadata["unitPool"]), "complete": True, "error": None,
            },
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
        if record.indexed is not None:
            values = record.indexed.unit(cluster_id)
            return np.asarray(values, dtype="<f8").tobytes(order="C"), list(values.shape)
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
            dtype = np.dtype(metadata["countsDtype"])
            try:
                mapped = np.memmap(
                    record.cache.data_path,
                    dtype=dtype,
                    mode="r",
                    offset=unit_index * values_per_unit * dtype.itemsize,
                    shape=(n_y, n_x, n_bins),
                    order="C",
                )
                # Preserve the existing binary HTTP contract for all clients.
                payload = np.asarray(mapped, dtype="<f8").tobytes(order="C")
                del mapped
            except OSError as exc:
                self._records.pop(record.dataset_id, None)
                raise DatasetChangedError("Dataset cache was evicted; reopen it") from exc
            return payload, [n_y, n_x, n_bins]

    def unit_array(self, record: DatasetRecord, cluster_id: int) -> tuple[int, np.ndarray]:
        """Return one validated unit as an in-memory y-by-x-by-time array."""

        if record.indexed is not None:
            values = record.indexed.unit(cluster_id)
            return record.cache.metadata["unitPool"].index(cluster_id), np.asarray(values, dtype=np.float64)
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
            dtype = np.dtype(metadata["countsDtype"])
            try:
                mapped = np.memmap(
                    record.cache.data_path,
                    dtype=dtype,
                    mode="r",
                    offset=unit_index * values_per_unit * dtype.itemsize,
                    shape=(n_y, n_x, n_bins),
                    order="C",
                )
                values = np.array(mapped, dtype=np.float64, copy=True, order="C")
                del mapped
            except OSError as exc:
                self._records.pop(record.dataset_id, None)
                raise DatasetChangedError("Dataset cache was evicted; reopen it") from exc
            return unit_index, values

    def zero_spike_unit_filter(
        self,
        record: DatasetRecord,
        start_bin: int,
        end_bin: int,
        threshold: int,
    ) -> tuple[list[int], list[int]]:
        """Return visible unit IDs and native zero-spike spatial-bin counts.

        The inclusive source-bin range is summed on the original ``y*x`` RF
        grid.  Display rebinning and smoothing are intentionally absent from
        this quality filter, matching the Python 1.10.0 viewer contract.
        """

        with self._lock:
            if self._records.get(record.dataset_id) is not record:
                raise DatasetChangedError("Dataset cache was evicted; reopen it")
            metadata = record.cache.metadata
            n_units, n_y, n_x, n_bins = metadata["shape"]
            start = max(0, min(n_bins - 1, min(int(start_bin), int(end_bin))))
            end = max(0, min(n_bins - 1, max(int(start_bin), int(end_bin))))
            if type(threshold) is not int or threshold < 1:
                raise ValueError("Zero-spike spatial-bin threshold must be positive")
            if record.indexed is not None:
                cached = record.indexed.cached()
                # Uncached units remain navigable so selecting one can prioritize
                # its read. Apply the native filter once that unit is available.
                result = [
                    int(np.count_nonzero(~np.any(cached[uid][..., start:end + 1], axis=2)))
                    if uid in cached else 0
                    for uid in metadata["unitPool"]
                ]
                return [uid for uid, zero in zip(metadata["unitPool"], result) if zero < threshold], result
            try:
                mapped = np.memmap(
                    record.cache.data_path,
                    dtype=metadata["countsDtype"],
                    mode="r",
                    shape=(n_units, n_y, n_x, n_bins),
                    order="C",
                )
                # Counts are non-negative, so any() is equivalent to summing
                # for this filter and cannot overflow uint64 at large counts.
                occupied = np.any(mapped[..., start : end + 1], axis=3)
                zero_counts = np.count_nonzero(~occupied, axis=(1, 2)).astype(
                    np.int64,
                    copy=False,
                )
                result = [int(value) for value in zero_counts]
                del occupied
                del zero_counts
                del mapped
            except OSError as exc:
                self._records.pop(record.dataset_id, None)
                raise DatasetChangedError("Dataset cache was evicted; reopen it") from exc
            visible = [
                int(unit_id)
                for unit_id, zero_count in zip(
                    metadata["unitPool"], result, strict=True
                )
                if zero_count < threshold
            ]
            return visible, result
