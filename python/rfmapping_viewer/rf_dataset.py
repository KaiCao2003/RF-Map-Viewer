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
from typing import Any, TextIO, overload

import numpy as np
from numpy.typing import NDArray


_EDGE_ATOL_S = 1e-12
_RESPONSE_UNITS = "spike_count"
_RESPONSE_NORMALIZATION = "none"
_SPIKE_COUNT_DEFINITION = (
    "each_qualifying_trial_contributes_once_per_final_spatial_bin"
)
_OCCUPANCY_TIME_DEFINITION = (
    "sum_of_qualifying_trial_durations_per_final_spatial_bin"
)
_STRUCTURAL_JSON_FIELDS = {
    "unitsSpikeCounts",
    "unitsSpikeCountsSize",
    "unitPool",
    "xPositions",
    "yPositions",
    "timeBinEdges",
    "occupancyTimeSec",
    "occupancyTimeSecSize",
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


def _axis_values(
    value: Any,
    label: str,
    expected_length: int,
) -> list[Any]:
    """Normalize MATLAB's scalar encoding for a singleton declared axis."""

    if not isinstance(value, list):
        if (
            expected_length == 1
            and isinstance(value, Real)
            and not isinstance(value, bool)
        ):
            return [value]
    return _flat_list(value, label)


def _spike_count_histograms(
    value: Any,
    shape: tuple[int, int, int, int],
) -> Iterator[tuple[int, int, int, list[Any]]]:
    """Yield validated 1-D histograms without walking every scalar recursively.

    RF count documents commonly contain tens of millions of scalar values.
    Validating each one through a recursive Python call is needlessly costly;
    the declared four-dimensional shape lets us validate only the comparatively
    small number of container rows here, then inspect each leaf row in bulk.
    """

    n_units, n_y, n_x, n_time_bins = shape
    if not isinstance(value, list) or len(value) != n_units:
        raise ValueError(
            f"unitsSpikeCounts has an invalid shape; expected {shape}"
        )
    for unit_index, unit in enumerate(value):
        if not isinstance(unit, list) or len(unit) != n_y:
            raise ValueError(
                f"unitsSpikeCounts has an invalid shape in y; expected {shape}"
            )
        for y_index, row in enumerate(unit):
            if not isinstance(row, list) or len(row) != n_x:
                raise ValueError(
                    f"unitsSpikeCounts has an invalid shape in x; expected {shape}"
                )
            for x_index, histogram in enumerate(row):
                if not isinstance(histogram, list) or len(histogram) != n_time_bins:
                    raise ValueError(
                        "unitsSpikeCounts has an invalid shape in time; "
                        f"expected {shape}"
                    )
                yield unit_index, y_index, x_index, histogram


def _compact_spike_counts(
    value: Any,
    shape: tuple[int, int, int, int],
) -> NDArray[np.unsignedinteger[Any]]:
    """Validate counts and copy them directly into the smallest safe dtype.

    Only one unit's nested Python lists are materialized by the streaming
    reader. This also avoids constructing an ``int64`` array before
    down-casting it. MATLAB integer JSON normally takes the fast ``int`` row
    path; uncommon floating-point JSON numbers retain the previous strict
    finite, non-negative, integral validation.
    """

    maximum = 0
    for _unit_index, _y_index, _x_index, histogram in _spike_count_histograms(
        value, shape
    ):
        scalar_types = set(map(type, histogram))
        if not scalar_types.issubset({int, float}):
            raise ValueError(
                "unitsSpikeCounts value is not numeric; values must be JSON "
                "numbers, not bool"
            )
        if float in scalar_types:
            for item in histogram:
                parsed = float(item)
                if (
                    not math.isfinite(parsed)
                    or parsed < 0.0
                    or not parsed.is_integer()
                    or parsed >= 2**64
                ):
                    raise ValueError(
                        "unitsSpikeCounts values must be finite non-negative "
                        "integer spike counts"
                    )
            row_maximum = max(float(item) for item in histogram)
            maximum = max(maximum, int(row_maximum))
            continue

        row_minimum = min(histogram)
        row_maximum = max(histogram)
        if row_minimum < 0 or row_maximum > np.iinfo(np.uint64).max:
            raise ValueError(
                "unitsSpikeCounts values must be finite non-negative integer "
                "spike counts representable as uint64"
            )
        maximum = max(maximum, int(row_maximum))

    if maximum <= np.iinfo(np.uint8).max:
        dtype: Any = np.uint8
    elif maximum <= np.iinfo(np.uint16).max:
        dtype = np.uint16
    elif maximum <= np.iinfo(np.uint32).max:
        dtype = np.uint32
    else:
        dtype = np.uint64

    result = np.empty(shape, dtype=dtype)
    try:
        for unit_index, y_index, x_index, histogram in _spike_count_histograms(
            value, shape
        ):
            result[unit_index, y_index, x_index, :] = histogram
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Unable to parse unitsSpikeCounts: {exc}") from exc
    result.setflags(write=False)
    return result


class _RFJSONReader:
    """Decode JSON values incrementally, retaining at most one raw unit.

    The standard decoder still handles strings, escapes and number syntax.
    Only the top-level object and the outer counts array are streamed here;
    metadata may precede or follow the counts, as in MATLAB jsonencode output.
    """

    def __init__(self, handle: TextIO):
        self.handle = handle
        self.buffer = ""
        self.position = 0
        self.eof = False
        self.decoder = json.JSONDecoder()
        self.value_chars = 0

    def _fill(self) -> None:
        remaining = self.buffer[self.position :]
        chunk = self.handle.read(max(64 * 1024, len(remaining)))
        self.buffer = remaining + chunk
        self.position = 0
        self.eof = not chunk

    def peek(self) -> str:
        while True:
            while (
                self.position < len(self.buffer)
                and self.buffer[self.position] in " \t\r\n"
            ):
                self.position += 1
            if self.position < len(self.buffer):
                return self.buffer[self.position]
            if self.eof:
                return ""
            self._fill()

    def take(self, token: str) -> None:
        if self.peek() != token:
            raise ValueError(f"Unable to parse RF mapping JSON: expected {token!r}")
        self.position += 1

    def value(self, *, minimum_chars: int = 0) -> Any:
        self.peek()
        # Successive units normally have comparable encoded sizes. Reading
        # that much first avoids repeatedly decoding partial nested arrays.
        while not self.eof and len(self.buffer) - self.position < minimum_chars:
            self._fill()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.position)
            except json.JSONDecodeError as exc:
                if self.eof:
                    raise ValueError(f"Unable to parse RF mapping JSON: {exc}") from exc
            else:
                # A number at the end of a chunk may still continue (12, 1e3).
                if self.eof or (
                    end < len(self.buffer) and self.buffer[end] in " \t\r\n,]}:"
                ):
                    self.value_chars = end - self.position
                    self.position = end
                    return value
            self._fill()


def _read_count_units(reader: _RFJSONReader) -> tuple[np.ndarray, ...]:
    reader.take("[")
    units: list[np.ndarray] = []
    unit_chars = 0
    if reader.peek() != "]":
        while True:
            unit = reader.value(minimum_chars=unit_chars)
            unit_chars = max(unit_chars, reader.value_chars)
            if not (
                isinstance(unit, list) and unit
                and isinstance(unit[0], list) and unit[0]
                and isinstance(unit[0][0], list) and unit[0][0]
            ):
                raise ValueError("unitsSpikeCounts has an invalid shape")
            shape = (1, len(unit), len(unit[0]), len(unit[0][0]))
            units.append(_compact_spike_counts([unit], shape)[0])
            del unit
            if reader.peek() == "]":
                break
            reader.take(",")
    reader.take("]")
    return tuple(units)


def _read_rf_json(handle: TextIO) -> dict[str, Any]:
    reader = _RFJSONReader(handle)
    if reader.peek() != "{":
        raise ValueError("RF mapping JSON must contain an object at the top level")
    reader.take("{")
    raw: dict[str, Any] = {}
    if reader.peek() != "}":
        while True:
            key = reader.value()
            if not isinstance(key, str):
                raise ValueError("Unable to parse RF mapping JSON: expected an object key")
            reader.take(":")
            raw[key] = (
                _read_count_units(reader)
                if key == "unitsSpikeCounts"
                else reader.value()
            )
            if reader.peek() == "}":
                break
            reader.take(",")
    reader.take("}")
    if reader.peek():
        raise ValueError("Unable to parse RF mapping JSON: trailing data")
    return raw


def _occupancy_matrix(value: Any, n_y: int, n_x: int) -> NDArray[np.float64]:
    if isinstance(value, Real) and not isinstance(value, bool):
        if n_y != 1 or n_x != 1:
            raise ValueError("occupancyTimeSec must be a y-by-x array")
        rows: list[list[Any]] = [[value]]
    elif isinstance(value, list):
        if all(not isinstance(item, list) for item in value):
            if n_y == 1 and len(value) == n_x:
                rows = [value]
            elif n_x == 1 and len(value) == n_y:
                rows = [[item] for item in value]
            else:
                raise ValueError(
                    "occupancyTimeSec dimensions do not match "
                    "unitsSpikeCountsSize"
                )
        elif all(isinstance(item, list) for item in value):
            rows = value
        else:
            raise ValueError(
                "occupancyTimeSec must be a rectangular y-by-x array"
            )
    else:
        raise ValueError("occupancyTimeSec must be a y-by-x array")

    if len(rows) != n_y or any(len(row) != n_x for row in rows):
        raise ValueError(
            "occupancyTimeSec x dimension or row count is invalid; "
            "dimensions do not match "
            "unitsSpikeCountsSize"
        )

    result = np.empty((n_y, n_x), dtype=float)
    for y_index, row in enumerate(rows):
        for x_index, item in enumerate(row):
            parsed = _number(
                item,
                f"occupancyTimeSec[{y_index}][{x_index}]",
            )
            if parsed < 0:
                raise ValueError("occupancyTimeSec values must be non-negative")
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
    occupancy_time_s: NDArray[np.float64]
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
            occupancy_time_s=self.occupancy_time_s,
            metadata=self.metadata,
            source_path=self.source_path,
        )

    def zero_spike_spatial_bin_count(
        self,
        earlier_s: float,
        later_s: float,
    ) -> int:
        """Count unavailable or zero-spike bins in a summed RF window.

        The count is deliberately evaluated on the native ``(y, x)`` grid,
        before display rebinning or smoothing.  It therefore stays a stable
        data-quality property of this RF map for the requested half-open time
        window.  A source bin with zero occupancy is unavailable and is
        counted even though the validated data contract also requires its
        spike count to be zero.
        """

        earlier = _number(earlier_s, "earlier_s")
        later = _number(later_s, "later_s")
        if later < earlier:
            raise ValueError("later_s must be greater than or equal to earlier_s")
        start = self._edge_index(earlier, "earlier_s")
        stop = self._edge_index(later, "later_s")
        if stop < start:
            raise ValueError("later_s must resolve at or after earlier_s")
        counts = self.spike_counts[..., start:stop].sum(axis=-1)
        unavailable = self.occupancy_time_s <= 0
        return int(np.count_nonzero((counts == 0) | unavailable))


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
    occupancy_time_s: NDArray[np.float64],
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
        occupancy_time_s=occupancy_time_s,
        metadata=MappingProxyType(deepcopy(dict(metadata))),
        source_path=Path(source_path),
    )


def load_rf_maps(path: str | Path) -> RFMapList:
    """Load and validate one RF mapping JSON document."""

    source_path = Path(path)
    with source_path.open("r", encoding="utf-8") as handle:
        raw = _read_rf_json(handle)

    required = {
        "occupancyTimeDefinition",
        "occupancyTimeSec",
        "occupancyTimeSecSize",
        "responseNormalization",
        "responseUnits",
        "spikeCountDefinition",
        "unitsSpikeCounts",
        "unitsSpikeCountsSize",
        "unitPool",
        "xPositions",
        "yPositions",
        "timeBinEdges",
    }
    missing = sorted(required.difference(raw))
    if missing:
        raise ValueError(
            "Unsupported legacy RF map; missing current schema keys: "
            + ", ".join(missing)
        )

    expected_contract = {
        "responseUnits": _RESPONSE_UNITS,
        "responseNormalization": _RESPONSE_NORMALIZATION,
        "spikeCountDefinition": _SPIKE_COUNT_DEFINITION,
        "occupancyTimeDefinition": _OCCUPANCY_TIME_DEFINITION,
    }
    for key, expected in expected_contract.items():
        if raw[key] != expected:
            raise ValueError(
                f"Unsupported RF map schema: {key} must be {expected!r}; "
                f"got {raw[key]!r}"
            )

    size_values = _flat_list(raw["unitsSpikeCountsSize"], "unitsSpikeCountsSize")
    if len(size_values) != 4:
        raise ValueError("unitsSpikeCountsSize must contain four values")
    shape = tuple(
        _integer(value, "unitsSpikeCountsSize value") for value in size_values
    )
    if any(value <= 0 for value in shape):
        raise ValueError("unitsSpikeCountsSize values must be positive")
    n_units, n_y, n_x, n_time_bins = shape

    count_units = raw.pop("unitsSpikeCounts")
    if len(count_units) != n_units or any(
        unit.shape != shape[1:] for unit in count_units
    ):
        raise ValueError(f"unitsSpikeCounts has an invalid shape; expected {shape}")
    spike_counts = np.stack(count_units)
    spike_counts.setflags(write=False)
    del count_units

    unit_pool = tuple(
        _integer(value, "unitPool value")
        for value in _axis_values(raw["unitPool"], "unitPool", n_units)
    )
    if len(unit_pool) != n_units:
        raise ValueError("unitPool length does not match unit count")
    if len(set(unit_pool)) != n_units:
        raise ValueError("unitPool must contain unique unit IDs")

    x_positions = _readonly_array(
        [
            _number(value, "xPositions value")
            for value in _axis_values(raw["xPositions"], "xPositions", n_x)
        ],
        dtype=float,
    )
    y_positions = _readonly_array(
        [
            _number(value, "yPositions value")
            for value in _axis_values(raw["yPositions"], "yPositions", n_y)
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

    occupancy_size_values = _flat_list(
        raw["occupancyTimeSecSize"], "occupancyTimeSecSize"
    )
    if len(occupancy_size_values) != 2:
        raise ValueError("occupancyTimeSecSize must contain two values")
    occupancy_shape = tuple(
        _integer(value, "occupancyTimeSecSize value")
        for value in occupancy_size_values
    )
    if occupancy_shape != (n_y, n_x):
        raise ValueError(
            "occupancyTimeSecSize must match the y-by-x dimensions in "
            "unitsSpikeCountsSize"
        )
    occupancy_time_s = _occupancy_matrix(raw["occupancyTimeSec"], n_y, n_x)
    if not np.any(occupancy_time_s > 0):
        raise ValueError("occupancyTimeSec must contain at least one positive value")
    if np.any(spike_counts[:, occupancy_time_s == 0, :] != 0):
        raise ValueError(
            "occupancyTimeSec is zero where unitsSpikeCounts is nonzero"
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
                occupancy_time_s=occupancy_time_s,
                metadata=metadata,
                source_path=source_path,
            )
            for unit_index, unit_id in enumerate(unit_pool)
        ],
        source_path,
    )


__all__ = ["RFMap", "RFMapList", "load_rf_maps"]
