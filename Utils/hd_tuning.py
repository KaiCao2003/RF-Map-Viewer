from __future__ import annotations

"""Validated, per-unit head-direction tuning data shared by figure exporters."""

import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
from numpy.typing import NDArray

from Utils.json_tools import read_formatted_json

HD_RAW_BIN_COUNT = 180
DEFAULT_HD_DISPLAY_BINS = 30
DEFAULT_HD_SMOOTH_SIGMA = 1.5
_SESSION_RE = re.compile(r"^(?P<date>\d{6,8})_(?P<index>\d+)$")


def _readonly(values: Any, *, dtype: Any) -> NDArray[Any]:
    array = np.asarray(values, dtype=dtype)
    array.setflags(write=False)
    return array


def _one_dimensional_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list) or any(isinstance(item, (list, dict)) for item in value):
        raise ValueError(f"{label} must be a one-dimensional array")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _integer(value: Any, label: str) -> int:
    parsed = _finite_number(value, label)
    if not parsed.is_integer():
        raise ValueError(f"{label} must be an integer")
    return int(parsed)


@dataclass(frozen=True, slots=True)
class HDTuningUnit:
    unit_id: int
    spike_counts: NDArray[np.float64]
    raw_rates_hz: NDArray[np.float64]
    hd_class: int | None
    metrics: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ProcessedHDCurve:
    angles_deg: NDArray[np.float64]
    rates_hz: NDArray[np.float64]


class HDTuningData:
    """Strict columnar tuning-curve JSON with explicit unit-ID lookup."""

    __slots__ = (
        "path",
        "angle_bin_edges_deg",
        "occupancy_time_s",
        "metadata",
        "_units",
        "_units_by_id",
    )

    def __init__(
        self,
        *,
        path: Path,
        angle_bin_edges_deg: NDArray[np.float64],
        occupancy_time_s: NDArray[np.float64],
        units: list[HDTuningUnit],
        metadata: Mapping[str, Any],
    ) -> None:
        self.path = path
        self.angle_bin_edges_deg = angle_bin_edges_deg
        self.occupancy_time_s = occupancy_time_s
        self.metadata = MappingProxyType(dict(metadata))
        self._units = tuple(units)
        self._units_by_id = {unit.unit_id: unit for unit in units}

    def __len__(self) -> int:
        return len(self._units)

    def __iter__(self):
        return iter(self._units)

    @property
    def unit_ids(self) -> tuple[int, ...]:
        return tuple(unit.unit_id for unit in self._units)

    def by_unit_id(self, unit_id: int) -> HDTuningUnit:
        try:
            return self._units_by_id[int(unit_id)]
        except (KeyError, TypeError, ValueError) as exc:
            raise KeyError(
                f"HD unit {unit_id!r} is unavailable. Available unit IDs: "
                f"{list(self.unit_ids)}"
            ) from exc

    def processed_curve(
        self,
        unit_id: int,
        *,
        display_bins: int = DEFAULT_HD_DISPLAY_BINS,
        smoothing: bool = True,
        sigma: float = DEFAULT_HD_SMOOTH_SIGMA,
    ) -> ProcessedHDCurve:
        unit = self.by_unit_id(unit_id)
        normalized_bins = normalize_hd_bin_count(display_bins)
        counts = np.asarray(unit.spike_counts, dtype=float)
        occupancy = np.asarray(self.occupancy_time_s, dtype=float)
        if smoothing:
            if isinstance(sigma, bool) or not math.isfinite(float(sigma)) or float(sigma) <= 0:
                raise ValueError("HD smoothing sigma must be positive and finite")
            raw_sigma = float(sigma) * HD_RAW_BIN_COUNT / DEFAULT_HD_DISPLAY_BINS
            counts = smooth_circular(counts, raw_sigma)
            occupancy = smooth_circular(occupancy, raw_sigma)
        group_size = HD_RAW_BIN_COUNT // normalized_bins
        grouped_counts = counts.reshape(normalized_bins, group_size).sum(axis=1)
        grouped_occupancy = occupancy.reshape(normalized_bins, group_size).sum(axis=1)
        rates = np.divide(
            grouped_counts,
            grouped_occupancy,
            out=np.zeros(normalized_bins, dtype=float),
            where=grouped_occupancy > 1e-12,
        )
        width = 360.0 / normalized_bins
        angles = (np.arange(normalized_bins, dtype=float) + 0.5) * width
        angles.setflags(write=False)
        rates.setflags(write=False)
        return ProcessedHDCurve(angles, rates)


def normalize_hd_bin_count(value: int) -> int:
    if isinstance(value, bool):
        raise ValueError("HD display bins must be an integer")
    try:
        requested = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("HD display bins must be an integer") from exc
    requested = max(1, min(HD_RAW_BIN_COUNT, requested))
    for candidate in range(requested, 0, -1):
        if HD_RAW_BIN_COUNT % candidate == 0:
            return candidate
    return 1


def smooth_circular(values: NDArray[np.float64], sigma: float) -> NDArray[np.float64]:
    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError("HD smoothing sigma must be positive and finite")
    radius = math.floor(4.0 * sigma + 0.5)
    offsets = np.arange(-radius, radius + 1, dtype=int)
    weights = np.exp(-0.5 * (offsets.astype(float) / sigma) ** 2)
    weights /= weights.sum()
    result = np.zeros_like(values, dtype=float)
    for offset, weight in zip(offsets, weights):
        result += weight * np.roll(values, -int(offset))
    return result


def load_hd_tuning(path: str | Path) -> HDTuningData:
    source = Path(path)
    raw = read_formatted_json(source)
    if not isinstance(raw, dict):
        raise ValueError("Tuning-curve JSON must contain an object")
    required = {
        "metadata",
        "angle_bin_edges_deg",
        "occupancy_time_s",
        "unit_id",
        "spike_counts",
        "firing_rate_hz",
        "unit_data",
    }
    missing = sorted(required.difference(raw))
    if missing:
        raise ValueError(f"Missing tuning-curve keys: {', '.join(missing)}")

    edges = _readonly(
        [_finite_number(value, "angle_bin_edges_deg value") for value in _one_dimensional_list(raw["angle_bin_edges_deg"], "angle_bin_edges_deg")],
        dtype=float,
    )
    occupancy = _readonly(
        [_finite_number(value, "occupancy_time_s value") for value in _one_dimensional_list(raw["occupancy_time_s"], "occupancy_time_s")],
        dtype=float,
    )
    if edges.shape != (HD_RAW_BIN_COUNT + 1,) or not np.all(np.diff(edges) > 0):
        raise ValueError("angle_bin_edges_deg must contain 181 strictly increasing edges")
    if occupancy.shape != (HD_RAW_BIN_COUNT,) or np.any(occupancy < 0):
        raise ValueError("occupancy_time_s must contain 180 non-negative values")

    unit_ids = tuple(
        _integer(value, "unit_id value")
        for value in _one_dimensional_list(raw["unit_id"], "unit_id")
    )
    if not unit_ids or len(set(unit_ids)) != len(unit_ids):
        raise ValueError("unit_id must contain unique unit IDs")
    n_units = len(unit_ids)
    counts = np.asarray(raw["spike_counts"], dtype=float)
    rates = np.asarray(raw["firing_rate_hz"], dtype=float)
    expected_shape = (n_units, HD_RAW_BIN_COUNT)
    if counts.shape != expected_shape or rates.shape != expected_shape:
        raise ValueError(
            f"spike_counts and firing_rate_hz must have shape {expected_shape}"
        )
    if not np.all(np.isfinite(counts)) or np.any(counts < 0):
        raise ValueError("spike_counts values must be finite and non-negative")
    if not np.all(np.isfinite(rates)) or np.any(rates < 0):
        raise ValueError("firing_rate_hz values must be finite and non-negative")

    unit_data = raw["unit_data"]
    if not isinstance(unit_data, dict):
        raise ValueError("unit_data must be an object of per-unit columns")
    for key, column in unit_data.items():
        if not isinstance(column, list) or len(column) != n_units:
            raise ValueError(f"unit_data.{key} must contain {n_units} values")
    classes = unit_data.get("hd_class", [None] * n_units)
    units: list[HDTuningUnit] = []
    for index, unit_id in enumerate(unit_ids):
        hd_class_raw = classes[index]
        hd_class = None if hd_class_raw is None else _integer(hd_class_raw, "hd_class value")
        count_row = _readonly(counts[index], dtype=float)
        rate_row = _readonly(rates[index], dtype=float)
        metrics = MappingProxyType({key: column[index] for key, column in unit_data.items()})
        units.append(HDTuningUnit(unit_id, count_row, rate_row, hd_class, metrics))

    metadata = raw["metadata"] if isinstance(raw["metadata"], dict) else {}
    return HDTuningData(
        path=source,
        angle_bin_edges_deg=edges,
        occupancy_time_s=occupancy,
        units=units,
        metadata=metadata,
    )


def probe_name_for_rf(path: str | Path) -> str | None:
    source = Path(path)
    for part in (source.stem, *(parent.name for parent in source.parents)):
        match = re.search(r"probe[\s_-]*([ab])(?:\b|[_-])", part, re.IGNORECASE)
        if match:
            return f"Probe{match.group(1).upper()}"
    return None


def discover_hd_tuning_path(rf_path: str | Path) -> Path | None:
    """Find the first same-date session's columnar tuning JSON for this probe."""

    source = Path(rf_path)
    probe = probe_name_for_rf(source)
    if probe is None:
        return None
    session: Path | None = None
    recording_date: str | None = None
    for candidate in (source.parent, *source.parents):
        match = _SESSION_RE.fullmatch(candidate.name)
        if match:
            session = candidate
            recording_date = match.group("date")
            break
    if session is None or recording_date is None:
        return None
    try:
        siblings = sorted(
            (
                (int(match.group("index")), child)
                for child in session.parent.iterdir()
                if child.is_dir()
                and (match := _SESSION_RE.fullmatch(child.name)) is not None
                and match.group("date") == recording_date
            ),
            key=lambda item: item[0],
        )
    except OSError:
        return None
    for _index, sibling in siblings:
        candidate = sibling / "data" / "tuning_curves" / probe / "tuning_curves.json"
        if candidate.is_file():
            return candidate
    return None


__all__ = [
    "DEFAULT_HD_DISPLAY_BINS",
    "DEFAULT_HD_SMOOTH_SIGMA",
    "HD_RAW_BIN_COUNT",
    "HDTuningData",
    "HDTuningUnit",
    "ProcessedHDCurve",
    "discover_hd_tuning_path",
    "load_hd_tuning",
    "normalize_hd_bin_count",
    "probe_name_for_rf",
    "smooth_circular",
]
