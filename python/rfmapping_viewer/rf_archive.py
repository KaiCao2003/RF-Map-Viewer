"""Indexed RF archives with one reader and a cache of validated units.

The GUI requests a unit without waiting for disk I/O. A single background
reader finishes its current entry, then gives the latest selection priority.
Cached arrays remain immutable and keep the complete source time axis.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from .export_inputs import FrozenFileIdentity
from .rf_dataset import RFMap, _lookup_integer, _parse_rf_header


def _compact_array(counts: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    if counts.shape != shape:
        raise ValueError(f"unit counts have shape {counts.shape}; expected {shape}")
    if counts.dtype.kind not in "uif":
        raise ValueError("unit counts must be numeric, not bool or object")
    if not np.all(np.isfinite(counts)) or np.any(counts < 0):
        raise ValueError("unit counts must be finite non-negative integers")
    if counts.dtype.kind == "f" and (
        np.any(counts != np.floor(counts)) or np.any(counts >= 2**64)
    ):
        raise ValueError("unit counts must be integers representable as uint64")
    maximum = int(counts.max())
    dtype = next(
        t
        for t in (np.uint8, np.uint16, np.uint32, np.uint64)
        if maximum <= np.iinfo(t).max
    )
    # Compact the original integer counts losslessly, as the JSON loader does.
    result = counts.astype(dtype, order="C", copy=True)
    result.setflags(write=False)
    return result


class IndexedRFMapList(Sequence[RFMap]):
    def __init__(self, path: str | Path):
        self.source_path = Path(path)
        self._identity = FrozenFileIdentity.capture(self.source_path)
        self._archive = np.load(self.source_path, allow_pickle=False)
        self._read_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cancelled = threading.Event()
        self._cache: dict[int, RFMap] = {}
        self._priority: int | None = None
        self._worker: threading.Thread | None = None
        self._error: tuple[int, str] | None = None
        try:
            encoded = self._archive["metadata"]
            if encoded.dtype != np.uint8 or encoded.ndim != 1:
                raise ValueError("metadata must be a one-dimensional UTF-8 uint8 array")
            raw = json.loads(encoded.tobytes().decode("utf-8"))
            if not isinstance(raw, dict) or raw.get("formatVersion") != 2:
                raise ValueError("Unsupported indexed .rfmap formatVersion; expected 2")
            for key in (
                "unitPool",
                "xPositions",
                "yPositions",
                "timeBinEdges",
                "occupancyTimeSec",
            ):
                value = self._archive[key]
                expected_ndim = 2 if key == "occupancyTimeSec" else 1
                if value.ndim != expected_ndim or value.dtype.kind not in "uif":
                    raise ValueError(
                        f"{key} must be a {expected_ndim}-dimensional numeric array"
                    )
                if key == "unitPool" and value.dtype.kind not in "ui":
                    raise ValueError("unitPool must contain integer unit IDs")
                raw[key] = value.tolist()
            self.header = _parse_rf_header(raw)
            keys = self._archive.files
            if len(set(keys)) != len(keys):
                raise ValueError("Duplicate entries in indexed .rfmap archive")
            missing = {f"unit_{uid}" for uid in self.header.unit_ids}.difference(keys)
            if missing:
                raise ValueError(f"Missing unit arrays: {', '.join(sorted(missing))}")
            self._indices = {uid: i for i, uid in enumerate(self.header.unit_ids)}
            self.by_index(0)
        except BaseException:
            self._archive.close()
            raise

    def __len__(self) -> int:
        return len(self.header.unit_ids)

    @property
    def unit_ids(self) -> list[int]:
        return list(self.header.unit_ids)

    def __getitem__(self, index: int | slice) -> RFMap | tuple[RFMap, ...]:
        if isinstance(index, slice):
            return tuple(self.by_index(i) for i in range(*index.indices(len(self))))
        index = _lookup_integer(index, "index")
        return self.by_index(index + len(self) if index < 0 else index)

    def by_unit_id(self, unit_id: int) -> RFMap:
        return self.by_index(self._indices[_lookup_integer(unit_id, "unit_id")])

    def by_index(self, index: int) -> RFMap:
        index = _lookup_integer(index, "unit_index")
        if not 0 <= index < len(self):
            raise IndexError(f"unit_index {index} is unavailable")
        with self._state_lock:
            cached = self._cache.get(index)
        if cached is not None:
            return cached
        # Serialize ZipFile access, including exports requesting an uncached unit.
        with self._read_lock:
            with self._state_lock:
                cached = self._cache.get(index)
            if cached is not None:
                return cached
            if self._cancelled.is_set():
                raise RuntimeError("RF archive loading was cancelled")
            try:
                self._identity.verify_path()
                key = f"unit_{self.header.unit_ids[index]}"
                counts = _compact_array(self._archive[key], self.header.shape[1:])
                result = self.header.make_map(index, counts, self.source_path)
                self._identity.verify_path()
            except Exception as exc:
                raise ValueError(
                    f"Could not load unit {self.header.unit_ids[index]}: {exc}"
                ) from exc
            if self._cancelled.is_set():
                self._archive.close()
                raise RuntimeError("RF archive loading was cancelled")
            with self._state_lock:
                self._cache[index] = result
            return result

    def is_cached(self, index: int) -> bool:
        with self._state_lock:
            return index in self._cache

    @property
    def cache_count(self) -> int:
        with self._state_lock:
            return len(self._cache)

    @property
    def error(self) -> tuple[int, str] | None:
        with self._state_lock:
            return self._error

    def request(self, index: int) -> None:
        """Prioritize the latest selection without blocking the caller."""
        with self._state_lock:
            self._priority = index

    def start_preload(self, *, retry: bool = False) -> None:
        with self._state_lock:
            if self._cancelled.is_set() or len(self._cache) == len(self):
                return
            if self._worker is not None and self._worker.is_alive():
                return
            if retry:
                self._error = None
            if self._error is not None:
                return
            self._worker = threading.Thread(
                target=self._preload, name="rfmap-unit-cache", daemon=True
            )
            self._worker.start()

    def _preload(self) -> None:
        try:
            while not self._cancelled.is_set():
                with self._state_lock:
                    pending = self._priority
                    self._priority = None
                    if pending is None or pending in self._cache:
                        pending = next(
                            (i for i in range(len(self)) if i not in self._cache), None
                        )
                if pending is None:
                    return
                try:
                    self.by_index(pending)
                except Exception as exc:
                    if not self._cancelled.is_set():
                        with self._state_lock:
                            self._error = (pending, str(exc))
                    return
        finally:
            if self._cancelled.is_set() or self.cache_count == len(self):
                with self._read_lock:
                    self._archive.close()

    def close(self) -> None:
        """Cancel without making the Tk thread wait for a network read."""
        self._cancelled.set()
        if self._read_lock.acquire(blocking=False):
            try:
                self._archive.close()
            finally:
                self._read_lock.release()


class RFCountSequence(Sequence[np.ndarray]):
    """Preserve indexed count access without materializing all lazy RF maps."""

    def __init__(self, maps: IndexedRFMapList):
        self.maps = maps

    def __len__(self) -> int:
        return len(self.maps)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [m.spike_counts for m in self.maps[index]]
        return self.maps[index].spike_counts

    def copy(self) -> list[np.ndarray]:
        return list(self)
