from __future__ import annotations

"""Object model for per-unit RF mapping JSON data."""

import math
import operator
import warnings
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from types import MappingProxyType
from typing import Any, overload

import numpy as np
from numpy.typing import NDArray

from Utils.json_tools import read_formatted_json

__all__ = ["RFMap", "RFMapList", "load_rf_maps"]


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

    if len(rows) != n_y:
        raise ValueError(
            "stimulusPresentationCounts y dimension does not match "
            "unitsSpikeCountsSize"
        )
    if any(len(row) != n_x for row in rows):
        raise ValueError(
            "stimulusPresentationCounts x dimension does not match "
            "unitsSpikeCountsSize"
        )

    normalized = np.empty((n_y, n_x), dtype=float)
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
            normalized[y_index, x_index] = parsed
    normalized.setflags(write=False)
    return normalized


def _coerce_lookup_integer(value: Any, label: str) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise TypeError(f"{label} must be an integer, not bool")
    try:
        return operator.index(value)
    except TypeError as exc:
        raise TypeError(f"{label} must be an integer") from exc


@dataclass(frozen=True, slots=True)
class RFMap:
    """RF mapping data for one unit.

    ``spike_counts`` always has axes ``(y, x, time_bin)``. Time-summed maps
    retain a singleton time dimension so they remain RFMap objects.
    """

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
        return self.spike_counts.shape[0]

    @property
    def n_x(self) -> int:
        return self.spike_counts.shape[1]

    @property
    def n_time_bins(self) -> int:
        return self.spike_counts.shape[2]

    @property
    def time_bin_centers_s(self) -> NDArray[np.float64]:
        centers = (self.time_bin_edges_s[:-1] + self.time_bin_edges_s[1:]) / 2
        centers.setflags(write=False)
        return centers

    @property
    def time_bin_widths_s(self) -> NDArray[np.float64]:
        widths = np.diff(self.time_bin_edges_s)
        widths.setflags(write=False)
        return widths

    def _available_edges_message(self) -> str:
        return f"Available time bin edges (s): {self.time_bin_edges_s.tolist()}"

    def _coerce_time(self, value: float, label: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(
                f"{label} must be a finite number. {self._available_edges_message()}"
            )
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                f"{label} must be a finite number. {self._available_edges_message()}"
            ) from exc
        if not math.isfinite(parsed):
            raise ValueError(
                f"{label} must be a finite number. {self._available_edges_message()}"
            )
        return parsed

    def _edge_index(self, value: float, label: str) -> int:
        exact_matches = np.flatnonzero(self.time_bin_edges_s == value)
        if exact_matches.size:
            # Source edges are unique. A zero-width derived RFMap deliberately
            # stores [t, t]; choosing the first copy makes [t, t) empty.
            return int(exact_matches[0])

        matches = np.flatnonzero(
            np.isclose(
                self.time_bin_edges_s,
                value,
                rtol=0.0,
                atol=_EDGE_ATOL_S,
            )
        )
        if matches.size == 0:
            raise ValueError(
                f"{label}={value!r} is not in timeBinEdges. "
                f"{self._available_edges_message()}"
            )
        if matches.size > 1:
            matching_edges = self.time_bin_edges_s[matches].tolist()
            raise ValueError(
                f"{label}={value!r} is within {_EDGE_ATOL_S:g} s of multiple "
                f"timeBinEdges {matching_edges}; use an exact edge value. "
                f"{self._available_edges_message()}"
            )
        return int(matches[0])

    def _time_indices(
        self,
        earlier_s: float,
        later_s: float,
        *,
        allow_empty: bool,
    ) -> tuple[int, int, float, float]:
        earlier = self._coerce_time(earlier_s, "earlier_s")
        later = self._coerce_time(later_s, "later_s")
        if later < earlier or (not allow_empty and later == earlier):
            relation = ">=" if allow_empty else ">"
            raise ValueError(
                f"later_s must be {relation} earlier_s. "
                f"Received earlier_s={earlier!r}, later_s={later!r}. "
                f"{self._available_edges_message()}"
            )

        start_index = self._edge_index(earlier, "earlier_s")
        stop_index = self._edge_index(later, "later_s")
        if stop_index < start_index or (not allow_empty and stop_index == start_index):
            relation = ">=" if allow_empty else ">"
            raise ValueError(
                f"later_s must resolve to an edge {relation} the earlier_s edge. "
                f"{self._available_edges_message()}"
            )
        return (
            start_index,
            stop_index,
            float(self.time_bin_edges_s[start_index]),
            float(self.time_bin_edges_s[stop_index]),
        )

    def sum_between_s(self, earlier_s: float, later_s: float) -> RFMap:
        """Return this unit summed over the half-open interval [earlier, later)."""

        start, stop, canonical_start, canonical_stop = self._time_indices(
            earlier_s,
            later_s,
            allow_empty=True,
        )
        summed_counts = self.spike_counts[..., start:stop].sum(
            axis=-1,
            keepdims=True,
        )
        summed_edges = np.asarray(
            [canonical_start, canonical_stop],
            dtype=float,
        )
        summed_metadata = deepcopy(dict(self.metadata))
        if "VSTimeWindow" in summed_metadata:
            summed_metadata["VSTimeWindow"] = [canonical_start, canonical_stop]
        if "timeWindowMs" in summed_metadata:
            summed_metadata["timeWindowMs"] = [
                canonical_start * 1000.0,
                canonical_stop * 1000.0,
            ]
        if "timeBinWidthMs" in summed_metadata:
            summed_metadata["timeBinWidthMs"] = (
                canonical_stop - canonical_start
            ) * 1000.0

        return _make_rf_map(
            unit_index=self.unit_index,
            unit_id=self.unit_id,
            spike_counts=summed_counts,
            x_positions=self.x_positions,
            y_positions=self.y_positions,
            time_bin_edges_s=summed_edges,
            presentation_counts=self.presentation_counts,
            metadata=summed_metadata,
            source_path=self.source_path,
        )

    def detect_bumps(
        self,
        threshold_ratio: float = 1.2,
        *,
        baseline_start_s: float = -0.1,
        baseline_end_s: float = 0.0,
    ) -> NDArray[np.uint8]:
        """Return a full ``(y, x, time)`` mask thresholded by baseline mean."""

        ratio = self._coerce_time(threshold_ratio, "threshold_ratio")
        if ratio <= 1.0:
            raise ValueError("threshold_ratio must be finite and greater than 1")

        start, stop, _canonical_start, _canonical_stop = self._time_indices(
            baseline_start_s,
            baseline_end_s,
            allow_empty=False,
        )
        baseline_counts = self.spike_counts[..., start:stop]
        baseline = float(np.mean(baseline_counts, dtype=np.float64))
        mask = np.asarray(
            self.spike_counts > baseline * ratio,
            dtype=np.uint8,
        )
        if np.any(mask[..., start:stop]):
            warnings.warn(
                f"RFMap unit {self.unit_id} has detected bumps inside the baseline "
                f"window; threshold_ratio={ratio:g} may be too low. Increase the "
                "threshold ratio if baseline bins should all be zero.",
                UserWarning,
                stacklevel=2,
            )
        return mask

    def detect_spatial_bumps(
        self,
        threshold_ratio: float = 1.2,
        *,
        spatial_size: int | tuple[int, int] = 3,
        baseline_start_s: float = -0.1,
        baseline_end_s: float = 0.0,
    ) -> NDArray[np.uint8]:
        """Return thresholded 2-D local maxima for every time bin.

        ``scipy.ndimage.maximum_filter`` is applied only across the ``(y, x)``
        axes; neighboring time bins never compete with one another. A value is
        marked when it is both above the same scalar baseline threshold used by
        :meth:`detect_bumps` and equal to the maximum in its spatial window.
        Flat maxima are retained in full rather than reduced arbitrarily to one
        pixel.
        """

        if isinstance(spatial_size, tuple):
            if len(spatial_size) != 2:
                raise ValueError("spatial_size must be an integer or a (y, x) pair")
            raw_y, raw_x = spatial_size
        else:
            raw_y = raw_x = spatial_size

        sizes: list[int] = []
        for value, label in ((raw_y, "spatial_size y"), (raw_x, "spatial_size x")):
            if isinstance(value, (bool, np.bool_)):
                raise ValueError(f"{label} must be a positive odd integer")
            try:
                parsed = operator.index(value)
            except TypeError as exc:
                raise ValueError(f"{label} must be a positive odd integer") from exc
            if parsed <= 0 or parsed % 2 == 0:
                raise ValueError(f"{label} must be a positive odd integer")
            sizes.append(parsed)

        threshold_mask = self.detect_bumps(
            threshold_ratio,
            baseline_start_s=baseline_start_s,
            baseline_end_s=baseline_end_s,
        )
        from scipy.ndimage import maximum_filter

        local_maxima = maximum_filter(
            self.spike_counts,
            size=(sizes[0], sizes[1], 1),
            mode="nearest",
        )
        return np.asarray(
            (threshold_mask != 0) & (self.spike_counts == local_maxima),
            dtype=np.uint8,
        )


class RFMapList(Sequence[RFMap]):
    """Ordered per-unit RFMap objects loaded from one JSON file."""

    __slots__ = (
        "_maps",
        "_maps_by_unit_id",
        "_maps_by_unit_index",
        "source_path",
    )

    def __init__(self, maps: Sequence[RFMap], source_path: str | Path):
        self._maps = tuple(maps)
        self.source_path = Path(source_path)
        self._maps_by_unit_id = {rf_map.unit_id: rf_map for rf_map in self._maps}
        if len(self._maps_by_unit_id) != len(self._maps):
            raise ValueError("RFMap unit IDs must be unique")
        self._maps_by_unit_index = {
            rf_map.unit_index: rf_map for rf_map in self._maps
        }
        if len(self._maps_by_unit_index) != len(self._maps):
            raise ValueError("RFMap original unit indices must be unique")

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
    def unit_ids(self) -> tuple[int, ...]:
        return tuple(rf_map.unit_id for rf_map in self._maps)

    def by_index(self, unit_index: int) -> RFMap:
        index = _coerce_lookup_integer(unit_index, "unit_index")
        try:
            return self._maps_by_unit_index[index]
        except KeyError as exc:
            available_indices = sorted(self._maps_by_unit_index)
            raise IndexError(
                f"unit_index {index} is unavailable. Available original unit "
                f"indices: {available_indices}"
            ) from exc

    def by_unit_id(self, unit_id: int) -> RFMap:
        parsed_id = _coerce_lookup_integer(unit_id, "unit_id")
        try:
            return self._maps_by_unit_id[parsed_id]
        except KeyError as exc:
            raise KeyError(
                f"unit_id {parsed_id} is unavailable. Available unit IDs: "
                f"{list(self.unit_ids)}"
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
    """Load one RF mapping JSON file into ordered, per-unit RFMap objects."""

    source_path = Path(path)
    raw = read_formatted_json(source_path)
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
    shape = tuple(_integer(value, "unitsSpikeCountsSize value") for value in size_values)
    if any(value <= 0 for value in shape):
        raise ValueError("unitsSpikeCountsSize values must be positive")
    n_units, n_y, n_x, n_time_bins = shape

    if not _counts_are_numeric(raw["unitsSpikeCounts"]):
        raise ValueError(
            "unitsSpikeCounts contains a value that is not numeric "
            "(JSON numbers only; bool is invalid)"
        )
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
    if len(set(unit_pool)) != len(unit_pool):
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
    time_bin_edges_s = _readonly_array(
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
    if len(time_bin_edges_s) != n_time_bins + 1:
        raise ValueError("timeBinEdges must contain nTimeBins + 1 edges")
    if not np.all(np.diff(time_bin_edges_s) > 0):
        raise ValueError("timeBinEdges must be strictly increasing")

    presentation_counts = None
    if "stimulusPresentationCounts" in raw:
        presentation_counts = _presentation_matrix(
            raw["stimulusPresentationCounts"],
            n_y,
            n_x,
        )
        zero_presentations = presentation_counts == 0
        if np.any(spike_counts[:, zero_presentations, :] != 0):
            raise ValueError(
                "stimulusPresentationCounts is zero where spike counts are nonzero"
            )

    metadata = {
        key: deepcopy(value)
        for key, value in raw.items()
        if key not in _STRUCTURAL_JSON_FIELDS
    }
    maps = [
        _make_rf_map(
            unit_index=unit_index,
            unit_id=unit_id,
            spike_counts=spike_counts[unit_index],
            x_positions=x_positions,
            y_positions=y_positions,
            time_bin_edges_s=time_bin_edges_s,
            presentation_counts=presentation_counts,
            metadata=metadata,
            source_path=source_path,
        )
        for unit_index, unit_id in enumerate(unit_pool)
    ]
    return RFMapList(maps, source_path)
