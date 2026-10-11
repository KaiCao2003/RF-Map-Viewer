"""ADN unit selection in physical probe coordinates and session storage."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from rfmapping_viewer.companions import ProbeGeometry
from rfmapping_viewer.constants import _RECORDING_SESSION_RE


ADN_DTYPE = np.dtype([("probe", "U16"), ("unit_id", "i8")])


def adn_selection_path(source: Path) -> Path | None:
    """Use the open RF recording's session, independently of its geometry source."""

    for parent in source.parents:
        if _RECORDING_SESSION_RE.fullmatch(parent.name):
            return parent / "data" / "adn_neuron.npy"
    return None


def _load_selection(path: Path) -> np.ndarray:
    if not path.exists():
        return np.empty(0, dtype=ADN_DTYPE)
    rows = np.load(path, allow_pickle=False)
    if rows.ndim != 1 or rows.dtype != ADN_DTYPE:
        raise ValueError("ADN selection must contain probe (U16) and unit_id (int64) fields.")
    return rows


def load_adn_units(path: Path, probe_name: str) -> set[int]:
    rows = _load_selection(path)
    return set(map(int, rows["unit_id"][rows["probe"] == probe_name]))


def save_adn_units(path: Path, probe_name: str, unit_ids: Iterable[int]) -> None:
    """Replace this probe's annotation while preserving other probes in the session."""

    rows = _load_selection(path)
    ids = sorted(set(map(int, unit_ids)))
    selected = np.empty(len(ids), dtype=ADN_DTYPE)
    selected["probe"] = probe_name
    selected["unit_id"] = ids
    rows = np.concatenate((rows[rows["probe"] != probe_name], selected))
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, rows, allow_pickle=False)


def unit_ids_in_polygon(
    geometry: ProbeGeometry,
    vertices: Sequence[tuple[float, float]],
) -> list[int]:
    """Return unique unit IDs in geometry order, including the polygon boundary."""

    polygon = np.asarray(vertices, dtype=float)
    if polygon.ndim != 2 or polygon.shape[1] != 2 or len(polygon) < 3:
        raise ValueError("A selection needs at least three (x, y) vertices.")
    if not np.isfinite(polygon).all():
        raise ValueError("Selection vertices must be finite.")
    units = [
        unit
        for unit in geometry.positioned_units
        if np.isfinite(unit.x_um) and np.isfinite(unit.y_um)
    ]
    if not units:
        return []
    points = np.asarray([(unit.x_um, unit.y_um) for unit in units], dtype=float)
    x, y = points.T
    inside = np.zeros(len(units), dtype=bool)
    boundary = np.zeros(len(units), dtype=bool)
    for start, end in zip(polygon, np.roll(polygon, -1, axis=0)):
        x0, y0 = start
        dx, dy = end - start
        length = np.hypot(dx, dy)
        if length == 0:
            continue
        # Tolerance is in micrometres so rounding does not drop edge points.
        cross = (x - x0) * dy - (y - y0) * dx
        along = ((x - x0) * dx + (y - y0) * dy) / length
        boundary |= (
            (np.abs(cross) <= 1e-9 * length)
            & (along >= -1e-9)
            & (along <= length + 1e-9)
        )
        if dy != 0:
            inside ^= ((y0 > y) != (end[1] > y)) & (x < x0 + (y - y0) * dx / dy)
    return list(dict.fromkeys(
        unit.unit_id
        for unit, selected in zip(units, inside | boundary)
        if selected
    ))
