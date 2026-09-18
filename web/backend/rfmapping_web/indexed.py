"""Read-only indexed RF archives with a serialized progressive unit cache."""

from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path

import numpy as np

from .datasets import DatasetChangedError, DatasetValidationError, _normalize_metadata, _source_signature


class IndexedUnits:
    def __init__(self, source: Path, max_bytes: int):
        self.source = source
        self.signature = _source_signature(source)
        try:
            self._archive = np.load(source, allow_pickle=False)
        except (OSError, ValueError, EOFError, zipfile.BadZipFile) as exc:
            raise DatasetValidationError(f"Invalid indexed RF archive: {exc}") from exc
        self._condition = threading.Condition()
        self._read_lock = threading.Lock()
        self._values: dict[int, np.ndarray] = {}
        self._priority: int | None = None
        self._worker: threading.Thread | None = None
        self._cancelled = False
        self._error: str | None = None
        self._max_bytes = max_bytes
        self._bytes = 0
        try:
            encoded = self._archive["metadata"]
            if encoded.dtype != np.uint8 or encoded.ndim != 1:
                raise ValueError("metadata must be a one-dimensional UTF-8 uint8 array")
            raw = json.loads(encoded.tobytes().decode("utf-8"))
            if not isinstance(raw, dict) or raw.get("formatVersion") != 2:
                raise ValueError("Unsupported indexed .rfmap formatVersion; expected 2")
            for key in ("unitPool", "xPositions", "yPositions", "timeBinEdges", "occupancyTimeSec"):
                value = self._archive[key]
                ndim = 2 if key == "occupancyTimeSec" else 1
                if value.ndim != ndim or value.dtype.kind not in "uif":
                    raise ValueError(f"{key} must be a {ndim}-dimensional numeric array")
                if key == "unitPool" and value.dtype.kind not in "ui":
                    raise ValueError("unitPool must contain integer unit IDs")
                raw[key] = value.tolist()
            raw["countsDtype"] = "<u8"
            self.metadata = _normalize_metadata(raw)
            self.metadata["source"] = self.signature
            keys = self._archive.files
            if len(set(keys)) != len(keys):
                raise ValueError("Duplicate entries in indexed .rfmap archive")
            missing = {f"unit_{uid}" for uid in self.metadata["unitPool"]}.difference(keys)
            if missing:
                raise ValueError(f"Missing unit arrays: {', '.join(sorted(missing))}")
            self._read(self.metadata["unitPool"][0])
        except Exception as exc:
            self._archive.close()
            raise DatasetValidationError(f"Invalid indexed RF archive: {exc}") from exc

    def _read(self, unit_id: int) -> np.ndarray:
        with self._read_lock:
            with self._condition:
                if unit_id in self._values:
                    return self._values[unit_id]
                if self._cancelled:
                    raise DatasetChangedError("RF archive loading was cancelled")
            if _source_signature(self.source) != self.signature:
                raise DatasetChangedError("Dataset source changed; reopen it")
            counts = self._archive[f"unit_{unit_id}"]
            if counts.shape != tuple(self.metadata["shape"][1:]):
                raise ValueError(f"Unit {unit_id} has an invalid (y, x, time) shape")
            if counts.dtype.kind not in "uif" or not np.all(np.isfinite(counts)) or np.any(counts < 0):
                raise ValueError(f"Unit {unit_id} counts must be finite non-negative integers")
            if counts.dtype.kind == "f" and (np.any(counts != np.floor(counts)) or np.any(counts >= 2**64)):
                raise ValueError(f"Unit {unit_id} counts must be integers representable as uint64")
            occupancy = np.asarray(self.metadata["occupancyTimeSec"])
            if np.any(counts[occupancy == 0, :] != 0):
                raise ValueError("occupancyTimeSec is zero where spike counts are nonzero")
            maximum = int(counts.max())
            dtype = next(dtype for dtype in (np.uint8, np.uint16, np.uint32, np.uint64) if maximum <= np.iinfo(dtype).max)
            values = counts.astype(dtype, order="C", copy=True)
            values.setflags(write=False)
            if _source_signature(self.source) != self.signature:
                raise DatasetChangedError("Dataset source changed; reopen it")
            with self._condition:
                if self._cancelled:
                    raise DatasetChangedError("RF archive loading was cancelled")
                if self._bytes + values.nbytes > self._max_bytes:
                    raise ValueError("Decoded RF dataset exceeds cache limit")
                self._values[unit_id] = values
                self._bytes += values.nbytes
                self._condition.notify_all()
            return values

    def start(self, *, retry: bool = False) -> None:
        with self._condition:
            if self._cancelled or (self._worker and self._worker.is_alive()):
                return
            if retry:
                self._error = None
            if self._error or len(self._values) == len(self.metadata["unitPool"]):
                return
            self._worker = threading.Thread(target=self._preload, name="rf-web-unit-cache", daemon=True)
            self._worker.start()

    def _preload(self) -> None:
        try:
            while True:
                with self._condition:
                    if self._cancelled:
                        return
                    pending = self._priority
                    self._priority = None
                    if pending is None or pending in self._values:
                        pending = next((uid for uid in self.metadata["unitPool"] if uid not in self._values), None)
                    if pending is None:
                        return
                self._read(pending)
        except Exception as exc:
            with self._condition:
                if not self._cancelled:
                    self._error = str(exc)
                self._condition.notify_all()
        finally:
            with self._condition:
                complete = len(self._values) == len(self.metadata["unitPool"])
                if self._cancelled or complete:
                    self._archive.close()
                self._condition.notify_all()

    def unit(self, unit_id: int) -> np.ndarray:
        if unit_id not in self.metadata["unitPool"]:
            raise KeyError(unit_id)
        with self._condition:
            self._priority = unit_id
            self.start()
            while unit_id not in self._values:
                if self._cancelled or self._error:
                    raise DatasetChangedError(self._error or "RF archive loading was cancelled")
                self._condition.wait()
            return self._values[unit_id]

    def cached(self) -> dict[int, np.ndarray]:
        with self._condition:
            return dict(self._values)

    def status(self) -> dict:
        with self._condition:
            return {"indexed": True, "cachedUnits": len(self._values), "totalUnits": len(self.metadata["unitPool"]),
                    "complete": len(self._values) == len(self.metadata["unitPool"]), "error": self._error}

    def close(self) -> None:
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()
        if self._read_lock.acquire(blocking=False):
            try:
                self._archive.close()
            finally:
                self._read_lock.release()
