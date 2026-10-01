"""Portable Figure Studio layouts, without source files or unit selections."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from rfmapping_viewer.constants import (
    HD_BIN_DIVISORS,
    PALETTES,
    POLAR_RADIUS_MODES,
    VALUE_MODES,
    WAVEFORM_CHANNEL_MODES,
)
from rfmapping_viewer.figure_export import FigureFormat, PlotKind
from rfmapping_viewer.figure_layout import validate_frames

if TYPE_CHECKING:
    from rfmapping_viewer.figure_composer import FigureViewerSnapshot
    from rfmapping_viewer.rf_model import RFMappingData


FIGURE_LAYOUT_FORMAT = "rfmapping.figure-layout"
FIGURE_LAYOUT_VERSION = 1
FIGURE_LAYOUT_EXTENSION = ".rfmlayout"
_SNAPSHOT_FIELDS = (
    "value_mode", "rf_source_start", "rf_source_end", "time_groups",
    "x_groups", "y_groups", "smooth_radius", "palette", "polar_radius",
    "timeline_polar", "selected_cell", "total_degrees", "timeline_range_start",
    "timeline_range_end", "timeline_active_bin", "hd_display_bins",
    "hd_smoothing", "hd_smooth_sigma",
    "waveform_channel_mode", "rf_subtract_source_range",
)


def _object(value: object, label: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object.")
    return value


def _sequence(value: object, label: str) -> Sequence:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list.")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{label} is outside its supported integer range.")
    return value


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be true or false.")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number.")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number.")
    return number


def _range(value: object, label: str, count: int) -> tuple[int, int]:
    items = _sequence(value, label)
    if len(items) != 2:
        raise ValueError(f"{label} must contain a start and end bin.")
    start = _integer(items[0], label, 0, count - 1)
    return start, _integer(items[1], label, start, count - 1)


def _groups(value: object, label: str, count: int, *, reversed_axis: bool = False) -> tuple:
    groups = tuple(_range(item, label, count) for item in _sequence(value, label))
    ordered = tuple(sorted(groups))
    if (
        not groups
        or ordered[0][0] != 0
        or ordered[-1][1] != count - 1
        or any(left[1] + 1 != right[0] for left, right in zip(ordered, ordered[1:]))
        or (groups != ordered and not (reversed_axis and groups == ordered[::-1]))
    ):
        raise ValueError(f"{label} must cover the full source axis without gaps or overlap.")
    return groups


def _source_axes(data: RFMappingData) -> dict[str, list[float]]:
    return {
        "time_bin_edges": [float(value) for value in data.time_bin_edges],
        "x_positions": [float(value) for value in data.x_positions],
        "y_positions": [float(value) for value in data.y_positions],
    }


def _parse_snapshot(value: object, axes: Mapping) -> dict[str, object]:
    settings = _object(value, "Layout settings")
    if set(settings) != set(_SNAPSHOT_FIELDS):
        raise ValueError("Layout settings are incomplete or unsupported.")
    n_time = len(axes["time_bin_edges"]) - 1
    n_x, n_y = len(axes["x_positions"]), len(axes["y_positions"])
    result = dict(settings)
    for name, choices in (
        ("value_mode", VALUE_MODES), ("palette", PALETTES),
        ("polar_radius", POLAR_RADIUS_MODES),
        ("waveform_channel_mode", WAVEFORM_CHANNEL_MODES),
    ):
        if settings[name] not in choices:
            raise ValueError(f"Unsupported {name} in layout.")
    for name in ("timeline_polar", "hd_smoothing"):
        result[name] = _boolean(settings[name], name)
    result["rf_source_start"], result["rf_source_end"] = _range(
        (settings["rf_source_start"], settings["rf_source_end"]), "RF time range", n_time
    )
    if settings["rf_subtract_source_range"] is not None:
        result["rf_subtract_source_range"] = _range(
            settings["rf_subtract_source_range"], "RF subtraction range", n_time
        )
    for name, count in (("time_groups", n_time), ("x_groups", n_x), ("y_groups", n_y)):
        result[name] = _groups(settings[name], name, count, reversed_axis=name == "y_groups")
    last_group = len(result["time_groups"]) - 1
    result["timeline_range_start"] = _integer(settings["timeline_range_start"], "Timeline start", 0, last_group)
    result["timeline_range_end"] = _integer(settings["timeline_range_end"], "Timeline end", -1, last_group)
    if result["timeline_range_end"] != -1 and result["timeline_range_end"] < result["timeline_range_start"]:
        raise ValueError("Timeline end must not precede its start.")
    result["timeline_active_bin"] = _integer(settings["timeline_active_bin"], "Timeline active bin", 0, last_group)
    if settings["selected_cell"] is not None:
        cell = _sequence(settings["selected_cell"], "Selected cell")
        if len(cell) != 4:
            raise ValueError("Selected cell must contain four spatial indices.")
        result["selected_cell"] = (
            *_range(cell[:2], "Selected cell Y range", n_y),
            *_range(cell[2:], "Selected cell X range", n_x),
        )
    result["smooth_radius"] = _integer(settings["smooth_radius"], "Smoothing radius", 0, 3)
    result["hd_display_bins"] = _integer(settings["hd_display_bins"], "HD bins", 1)
    if result["hd_display_bins"] not in HD_BIN_DIVISORS:
        raise ValueError("HD bins must divide the 180-bin source curve.")
    for name in ("total_degrees", "hd_smooth_sigma"):
        result[name] = _number(settings[name], name)
        if result[name] <= 0:
            raise ValueError(f"{name} must be positive.")
    return result


def _parse_pages(value: object) -> list[dict[str, object]]:
    pages = _sequence(value, "Layout pages")
    if not pages:
        raise ValueError("A layout must contain at least one page.")
    result = []
    for value in pages:
        page = _object(value, "Layout page")
        name = page.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each layout page must have a name.")
        plots = [PlotKind.coerce(kind) for kind in _sequence(page.get("plots"), "Page views")]
        if len(plots) > 9:
            raise ValueError("Each page can contain up to nine views.")
        frames = validate_frames(_sequence(page.get("frames"), "Page frames"))
        if len(frames) != len(plots):
            raise ValueError("Each view must have exactly one frame.")
        result.append({"name": name, "plots": plots, "frames": list(frames)})
    return result


def make_figure_layout(
    data: RFMappingData,
    snapshot: FigureViewerSnapshot,
    pages: Sequence[Mapping],
    normalize_per_unit: bool,
    output_format: FigureFormat | str,
) -> dict[str, object]:
    """Capture reusable composition and rendering settings as JSON data."""
    axes = _source_axes(data)
    settings = _parse_snapshot({name: getattr(snapshot, name) for name in _SNAPSHOT_FIELDS}, axes)
    valid_pages = _parse_pages(pages)

    def json_value(value):
        return [json_value(item) for item in value] if isinstance(value, tuple) else value

    return {
        "format": FIGURE_LAYOUT_FORMAT,
        "version": FIGURE_LAYOUT_VERSION,
        "axes": axes,
        "snapshot": {name: json_value(value) for name, value in settings.items()},
        "pages": [
            {"name": page["name"], "plots": [kind.value for kind in page["plots"]],
             "frames": [list(frame) for frame in page["frames"]]}
            for page in valid_pages
        ],
        "normalize_per_unit": _boolean(normalize_per_unit, "Per-unit normalization"),
        "output_format": FigureFormat.coerce(output_format).value,
    }


def parse_figure_layout(
    payload: object,
    data: RFMappingData,
    current_snapshot: FigureViewerSnapshot,
) -> tuple[FigureViewerSnapshot, list[dict[str, object]], bool, str]:
    """Validate fully before restoring settings onto the current source session.

    Bin/group indices are only reusable when physical axes match. A different
    recording with identical axes is supported; source paths and unit IDs are
    deliberately absent. Current filtering, selection and attached HD session
    remain part of the open document rather than the reusable layout.
    """
    layout = _object(payload, "Figure layout")
    if layout.get("format") != FIGURE_LAYOUT_FORMAT:
        raise ValueError("This file is not a Figure Studio layout.")
    if type(layout.get("version")) is not int or layout["version"] != FIGURE_LAYOUT_VERSION:
        raise ValueError("This Figure Studio layout version is not supported.")
    axes = _object(layout.get("axes"), "Layout axes")
    for name, current in _source_axes(data).items():
        saved = [_number(value, name) for value in _sequence(axes.get(name), name)]
        if saved != current:
            label = {"time_bin_edges": "time-bin edges", "x_positions": "X positions", "y_positions": "Y positions"}[name]
            raise ValueError(f"Layout {label} do not match the current RF dataset.")
    settings = _parse_snapshot(layout.get("snapshot"), axes)
    pages = _parse_pages(layout.get("pages"))
    normalized = _boolean(layout.get("normalize_per_unit"), "Per-unit normalization")
    output_format = FigureFormat.coerce(layout.get("output_format")).value.upper()
    return replace(current_snapshot, **settings), pages, normalized, output_format
