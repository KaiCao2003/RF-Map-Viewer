"""Viewer-only RF JSON model.

This module intentionally implements only the stable data contract needed by
the desktop viewer: strict JSON validation, unit/index lookup, and half-open
time-window sums. Scientific RF detection and raw-trial reconstruction remain
in the separate ``rfmapping`` analysis repository.
"""

from __future__ import annotations

import json
import math
import operator
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from types import MappingProxyType
from typing import Any, overload

import numpy as np
from numpy.typing import NDArray


_EDGE_ATOL_S = 1e-12
_STRUCTURAL_JSON_FIELDS = {
    "unitsSpikeCounts",
    "unitsSpikeCountsSize",
    "unitPool",
    "xPositions",
    "yPositions",
    "timeBinEdges",
    "stimulusPresentationCounts",
}


def _readonly_array(values: Any, *, dtype: Any | None = None) -> NDArray[Any]:
    array = np.asarray(values, dtype=dtype)
    array.setflags(write=False)
    return array


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _integer(value: Any, label: str) -> int:
    parsed = _number(value, label)
    if not parsed.is_integer():
        raise ValueError(f"{label} must be an integer")
    return int(parsed)


def _flat_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list) or any(
        isinstance(item, (list, dict)) for item in value
    ):
        raise ValueError(f"{label} must be a one-dimensional array")
    return value


def _counts_are_numeric(value: Any) -> bool:
    if isinstance(value, list):
        return all(_counts_are_numeric(child) for child in value)
    return isinstance(value, Real) and not isinstance(value, bool)


def _presentation_matrix(value: Any, n_y: int, n_x: int) -> NDArray[np.float64]:
    if isinstance(value, Real) and not isinstance(value, bool):
        if n_y != 1 or n_x != 1:
            raise ValueError("stimulusPresentationCounts must be a y-by-x array")
        rows: list[list[Any]] = [[value]]
    elif isinstance(value, list):
        if all(not isinstance(item, list) for item in value):
            if n_y == 1 and len(value) == n_x:
                rows = [value]
            elif n_x == 1 and len(value) == n_y:
                rows = [[item] for item in value]
            else:
                raise ValueError(
                    "stimulusPresentationCounts dimensions do not match "
                    "unitsSpikeCountsSize"
                )
        elif all(isinstance(item, list) for item in value):
            rows = value
        else:
            raise ValueError(
                "stimulusPresentationCounts must be a rectangular y-by-x array"
            )
    else:
        raise ValueError("stimulusPresentationCounts must be a y-by-x array")

    if len(rows) != n_y or any(len(row) != n_x for row in rows):
        raise ValueError(
            "stimulusPresentationCounts dimensions do not match "
            "unitsSpikeCountsSize"
        )

    result = np.empty((n_y, n_x), dtype=float)
    for y_index, row in enumerate(rows):
        for x_index, item in enumerate(row):
            parsed = _number(
                item,
                f"stimulusPresentationCounts[{y_index}][{x_index}]",
            )
            if parsed < 0 or not parsed.is_integer():
                raise ValueError(
                    "stimulusPresentationCounts values must be non-negative integers"
                )
            result[y_index, x_index] = parsed
    result.setflags(write=False)
    return result


def _lookup_integer(value: Any, label: str) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise TypeError(f"{label} must be an integer, not bool")
    try:
        return operator.index(value)
    except TypeError as exc:
        raise TypeError(f"{label} must be an integer") from exc


@dataclass(frozen=True, slots=True)
class RFMap:
    """RF counts for one unit with axes ``(y, x, time)``."""

    unit_index: int
    unit_id: int
    spike_counts: NDArray[Any]
    x_positions: NDArray[np.float64]
    y_positions: NDArray[np.float64]
    time_bin_edges_s: NDArray[np.float64]
    presentation_counts: NDArray[np.float64] | None
    metadata: Mapping[str, Any]
    source_path: Path

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.spike_counts.shape

    @property
    def n_y(self) -> int:
        return self.shape[0]

    @property
    def n_x(self) -> int:
        return self.shape[1]

    @property
    def n_time_bins(self) -> int:
        return self.shape[2]

    def _edge_index(self, value: float, label: str) -> int:
        parsed = _number(value, label)
        exact = np.flatnonzero(self.time_bin_edges_s == parsed)
        if exact.size:
            return int(exact[0])
        matches = np.flatnonzero(
            np.isclose(
                self.time_bin_edges_s,
                parsed,
                rtol=0.0,
                atol=_EDGE_ATOL_S,
            )
        )
        if matches.size != 1:
            available = self.time_bin_edges_s.tolist()
            raise ValueError(
                f"{label}={parsed!r} is not an unambiguous timeBinEdges value; "
                f"available edges: {available}"
            )
        return int(matches[0])

    def sum(self, earlier_s: float, later_s: float) -> RFMap:
        """Return the half-open sum ``[earlier_s, later_s)``."""

        earlier = _number(earlier_s, "earlier_s")
        later = _number(later_s, "later_s")
        if later < earlier:
            raise ValueError("later_s must be greater than or equal to earlier_s")
        start = self._edge_index(earlier, "earlier_s")
        stop = self._edge_index(later, "later_s")
        if stop < start:
            raise ValueError("later_s must resolve at or after earlier_s")
        counts = self.spike_counts[..., start:stop].sum(axis=-1, keepdims=True)
        edges = _readonly_array(
            [self.time_bin_edges_s[start], self.time_bin_edges_s[stop]],
            dtype=float,
        )
        return _make_rf_map(
            unit_index=self.unit_index,
            unit_id=self.unit_id,
            spike_counts=counts,
            x_positions=self.x_positions,
            y_positions=self.y_positions,
            time_bin_edges_s=edges,
            presentation_counts=self.presentation_counts,
            metadata=self.metadata,
            source_path=self.source_path,
        )


class RFMapList(Sequence[RFMap]):
    """Ordered per-unit maps with separate source-index and unit-ID lookup."""

    def __init__(self, maps: Sequence[RFMap], source_path: str | Path):
        self._maps = tuple(maps)
        if not self._maps:
            raise ValueError("RFMapList requires at least one RFMap")
        self.source_path = Path(source_path)
        self._by_unit_id = {item.unit_id: item for item in self._maps}
        self._by_index = {item.unit_index: item for item in self._maps}
        if len(self._by_unit_id) != len(self._maps):
            raise ValueError("RFMap unit IDs must be unique")
        if len(self._by_index) != len(self._maps):
            raise ValueError("RFMap source indices must be unique")

    def __len__(self) -> int:
        return len(self._maps)

    def __iter__(self) -> Iterator[RFMap]:
        return iter(self._maps)

    @overload
    def __getitem__(self, index: int) -> RFMap: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[RFMap, ...]: ...

    def __getitem__(self, index: int | slice) -> RFMap | tuple[RFMap, ...]:
        return self._maps[index]

    @property
    def unit_ids(self) -> list[int]:
        return [item.unit_id for item in self._maps]

    def by_index(self, unit_index: int) -> RFMap:
        parsed = _lookup_integer(unit_index, "unit_index")
        try:
            return self._by_index[parsed]
        except KeyError as exc:
            raise IndexError(f"unit_index {parsed} is unavailable") from exc

    def by_unit_id(self, unit_id: int) -> RFMap:
        parsed = _lookup_integer(unit_id, "unit_id")
        try:
            return self._by_unit_id[parsed]
        except KeyError as exc:
            raise KeyError(
                f"unit_id {parsed} is unavailable; available IDs: {self.unit_ids}"
            ) from exc


def _make_rf_map(
    *,
    unit_index: int,
    unit_id: int,
    spike_counts: Any,
    x_positions: Any,
    y_positions: Any,
    time_bin_edges_s: Any,
    presentation_counts: NDArray[np.float64] | None,
    metadata: Mapping[str, Any],
    source_path: str | Path,
) -> RFMap:
    return RFMap(
        unit_index=int(unit_index),
        unit_id=int(unit_id),
        spike_counts=_readonly_array(spike_counts),
        x_positions=_readonly_array(x_positions, dtype=float),
        y_positions=_readonly_array(y_positions, dtype=float),
        time_bin_edges_s=_readonly_array(time_bin_edges_s, dtype=float),
        presentation_counts=presentation_counts,
        metadata=MappingProxyType(deepcopy(dict(metadata))),
        source_path=Path(source_path),
    )


def load_rf_maps(path: str | Path) -> RFMapList:
    """Load and validate one RF mapping JSON document."""

    source_path = Path(path)
    try:
        with source_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Unable to parse RF mapping JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("RF mapping JSON must contain an object at the top level")

    required = {
        "unitsSpikeCounts",
        "unitsSpikeCountsSize",
        "unitPool",
        "xPositions",
        "yPositions",
        "timeBinEdges",
    }
    missing = sorted(required.difference(raw))
    if missing:
        raise ValueError(f"Missing JSON keys: {', '.join(missing)}")

    size_values = _flat_list(raw["unitsSpikeCountsSize"], "unitsSpikeCountsSize")
    if len(size_values) != 4:
        raise ValueError("unitsSpikeCountsSize must contain four values")
    shape = tuple(
        _integer(value, "unitsSpikeCountsSize value") for value in size_values
    )
    if any(value <= 0 for value in shape):
        raise ValueError("unitsSpikeCountsSize values must be positive")
    n_units, n_y, n_x, n_time_bins = shape

    if not _counts_are_numeric(raw["unitsSpikeCounts"]):
        raise ValueError("unitsSpikeCounts values must be JSON numbers, not bool")
    try:
        spike_counts = np.asarray(raw["unitsSpikeCounts"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Unable to parse unitsSpikeCounts: {exc}") from exc
    if spike_counts.shape != shape:
        raise ValueError(
            f"unitsSpikeCounts has shape {spike_counts.shape}, expected {shape}"
        )
    if not np.all(np.isfinite(spike_counts)) or np.any(spike_counts < 0):
        raise ValueError("unitsSpikeCounts values must be finite and non-negative")
    spike_counts.setflags(write=False)

    unit_pool = tuple(
        _integer(value, "unitPool value")
        for value in _flat_list(raw["unitPool"], "unitPool")
    )
    if len(unit_pool) != n_units:
        raise ValueError("unitPool length does not match unit count")
    if len(set(unit_pool)) != n_units:
        raise ValueError("unitPool must contain unique unit IDs")

    x_positions = _readonly_array(
        [
            _number(value, "xPositions value")
            for value in _flat_list(raw["xPositions"], "xPositions")
        ],
        dtype=float,
    )
    y_positions = _readonly_array(
        [
            _number(value, "yPositions value")
            for value in _flat_list(raw["yPositions"], "yPositions")
        ],
        dtype=float,
    )
    time_edges = _readonly_array(
        [
            _number(value, "timeBinEdges value")
            for value in _flat_list(raw["timeBinEdges"], "timeBinEdges")
        ],
        dtype=float,
    )
    if len(x_positions) != n_x:
        raise ValueError("xPositions length does not match x dimension")
    if len(y_positions) != n_y:
        raise ValueError("yPositions length does not match y dimension")
    if len(time_edges) != n_time_bins + 1:
        raise ValueError("timeBinEdges must contain nTimeBins + 1 edges")
    if not np.all(np.diff(time_edges) > 0):
        raise ValueError("timeBinEdges must be strictly increasing")

    presentation_counts = None
    if "stimulusPresentationCounts" in raw:
        presentation_counts = _presentation_matrix(
            raw["stimulusPresentationCounts"],
            n_y,
            n_x,
        )
        if np.any(spike_counts[:, presentation_counts == 0, :] != 0):
            raise ValueError(
                "stimulusPresentationCounts is zero where spike counts are nonzero"
            )

    metadata = {
        key: deepcopy(value)
        for key, value in raw.items()
        if key not in _STRUCTURAL_JSON_FIELDS
    }
    return RFMapList(
        [
            _make_rf_map(
                unit_index=unit_index,
                unit_id=unit_id,
                spike_counts=spike_counts[unit_index],
                x_positions=x_positions,
                y_positions=y_positions,
                time_bin_edges_s=time_edges,
                presentation_counts=presentation_counts,
                metadata=metadata,
                source_path=source_path,
            )
            for unit_index, unit_id in enumerate(unit_pool)
        ],
        source_path,
    )


__all__ = ["RFMap", "RFMapList", "load_rf_maps"]
