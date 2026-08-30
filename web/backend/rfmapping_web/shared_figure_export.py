"""UI-independent figure export models and a Pillow renderer.

The Web backend owns this copy so deployment is independent of the Python/Tk
project. It contains no Tk, HTTP, matplotlib, or application-state dependencies:
callers describe page
templates with :class:`PlotSpec`, provide the per-unit data at render time, and
use the same renderer for live previews and final exports.

Pillow rasterizes every page.  PNG is written directly, PDF embeds one rendered
page at a time, and SVG uses a documented, portable contract: the exact PNG
produced by the renderer is embedded in an SVG ``<image>`` element.  The SVG
therefore scales as an SVG document, while its plot content remains raster
data.  This is what guarantees preview parity across all three formats.
"""

from __future__ import annotations

import base64
import ctypes
import errno
import fcntl
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import sys
import uuid
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, TypeAlias

from PIL import Image, ImageColor, ImageDraw, ImageFont


DEFAULT_PAGE_SIZE = (1600, 1200)
SVG_RENDERING_CONTRACT = (
    "SVG files contain the renderer's lossless PNG as an embedded data URI; "
    "plot primitives are not editable vector paths."
)
EXPORT_MANIFEST_NAME = "manifest.json"
EXPORT_MANIFEST_VERSION = 1
EXPORT_PRODUCER = "rfmapping.python.figure-export"
SINGLETON_Y_REFERENCE_COLUMNS = 30
SINGLETON_Y_REFERENCE_ROWS = 7


class FigureExportError(RuntimeError):
    """Base error for figure rendering and export failures."""


class FigureExportValidationError(ValueError):
    """An export model or plot payload is invalid."""


class DestinationExistsError(FileExistsError, FigureExportError):
    """The destination exists and explicit overwrite was not requested."""


class FigureFormat(str, Enum):
    """Supported final figure containers."""

    PDF = "pdf"
    PNG = "png"
    SVG = "svg"

    @classmethod
    def coerce(cls, value: FigureFormat | str) -> FigureFormat:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise FigureExportValidationError(
                f"format must be one of: {', '.join(item.value for item in cls)}"
            )
        normalized = value.strip().lower().lstrip(".")
        try:
            return cls(normalized)
        except ValueError as exc:
            raise FigureExportValidationError(
                f"unknown figure format {value!r}; available formats: "
                f"{', '.join(item.value for item in cls)}"
            ) from exc


class PlotKind(str, Enum):
    """Stable identifiers stored by GUI and web export-plan editors."""

    RF_CARTESIAN = "rf.cartesian"
    RF_POLAR = "rf.polar"
    DELAY_CARTESIAN = "delay.cartesian"
    DELAY_POLAR = "delay.polar"
    RGB_CARTESIAN = "rgb.cartesian"
    RGB_POLAR = "rgb.polar"
    TIMELINE_CURRENT = "timeline.current"
    HD_LINE = "hd.line"
    HD_POLAR = "hd.polar"
    PROBE_LAYOUT = "probe"
    WAVEFORM_LOCAL_AVERAGE = "waveform.local_average"

    @classmethod
    def coerce(cls, value: PlotKind | str) -> PlotKind:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise FigureExportValidationError("plot kind must be a string")
        try:
            return cls(value.strip())
        except ValueError as exc:
            raise FigureExportValidationError(
                f"unknown plot kind {value!r}; available kinds: "
                f"{', '.join(item.value for item in cls)}"
            ) from exc


@dataclass(frozen=True, slots=True)
class PlotKindDefinition:
    """Display and renderer metadata for one stable plot identifier."""

    kind: PlotKind
    label: str
    renderer_family: str
    is_polar: bool = False


_PLOT_DEFINITIONS = (
    PlotKindDefinition(PlotKind.RF_CARTESIAN, "RF map", "scalar_map"),
    PlotKindDefinition(PlotKind.RF_POLAR, "RF map (polar)", "scalar_map", True),
    PlotKindDefinition(PlotKind.DELAY_CARTESIAN, "Delay map", "scalar_map"),
    PlotKindDefinition(
        PlotKind.DELAY_POLAR, "Delay map (polar)", "scalar_map", True
    ),
    PlotKindDefinition(PlotKind.RGB_CARTESIAN, "RGB map", "rgb_map"),
    PlotKindDefinition(PlotKind.RGB_POLAR, "RGB map (polar)", "rgb_map", True),
    PlotKindDefinition(
        PlotKind.TIMELINE_CURRENT, "Timeline (current settings)", "timeline"
    ),
    PlotKindDefinition(PlotKind.HD_LINE, "HD tuning curve", "line"),
    PlotKindDefinition(
        PlotKind.HD_POLAR, "HD tuning curve (polar)", "line", True
    ),
    PlotKindDefinition(PlotKind.PROBE_LAYOUT, "Probe layout", "points"),
    PlotKindDefinition(
        PlotKind.WAVEFORM_LOCAL_AVERAGE,
        "Local average waveform",
        "waveform_heatmap",
    ),
)

# Registry keys are plain strings on purpose: both JSON clients and Python code
# can look up a stable identifier without depending on Enum serialization.
PLOT_KIND_REGISTRY: Mapping[str, PlotKindDefinition] = MappingProxyType(
    {definition.kind.value: definition for definition in _PLOT_DEFINITIONS}
)


@dataclass(frozen=True, slots=True)
class PlotSpec:
    """A reusable plot template.

    ``data`` can hold a static payload.  For multi-unit plans, callers normally
    leave it as ``None`` and pass a ``data_provider(unit_id, spec)`` to preview
    and export functions.  A provider may return raw payload data or a complete
    replacement :class:`PlotSpec`.
    """

    kind: PlotKind | str
    data: Any = None
    title: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", PlotKind.coerce(self.kind))
        if self.title is not None and not isinstance(self.title, str):
            raise FigureExportValidationError("plot title must be a string or None")
        if not isinstance(self.options, Mapping):
            raise FigureExportValidationError("plot options must be a mapping")
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


@dataclass(frozen=True, slots=True)
class ExportPage:
    """One page template, repeated once for every selected unit."""

    name: str
    plots: Sequence[PlotSpec]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise FigureExportValidationError("page name must not be empty")
        normalized = tuple(self.plots)
        if not normalized:
            raise FigureExportValidationError(
                f"export page {self.name!r} must contain at least one plot"
            )
        if not all(isinstance(plot, PlotSpec) for plot in normalized):
            raise FigureExportValidationError(
                f"all plots in export page {self.name!r} must be PlotSpec objects"
            )
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "plots", normalized)


@dataclass(frozen=True, slots=True)
class ExportPlan:
    """Validated export choices made by a page-builder UI.

    PDF destinations are files ending in ``.pdf``.  PNG and SVG destinations
    are directories containing one file for every unit/page combination.
    Every selected unit receives every page template, which proves that a unit
    can never be selected without at least one output page.
    """

    format: FigureFormat | str
    unit_ids: Sequence[int]
    pages: Sequence[ExportPage]
    destination: str | os.PathLike[str]

    def __post_init__(self) -> None:
        figure_format = FigureFormat.coerce(self.format)
        unit_ids = tuple(self.unit_ids)
        pages = tuple(self.pages)
        if not unit_ids:
            raise FigureExportValidationError(
                "an export plan must select at least one unit"
            )
        for unit_id in unit_ids:
            if isinstance(unit_id, bool) or not isinstance(unit_id, int):
                raise FigureExportValidationError("unit IDs must be integers")
        if len(set(unit_ids)) != len(unit_ids):
            raise FigureExportValidationError("unit IDs must not contain duplicates")
        if not pages:
            raise FigureExportValidationError(
                "an export plan must contain at least one page per unit"
            )
        if not all(isinstance(page, ExportPage) for page in pages):
            raise FigureExportValidationError("pages must be ExportPage objects")
        if len({page.name for page in pages}) != len(pages):
            raise FigureExportValidationError("page names must be unique")

        if not isinstance(self.destination, (str, os.PathLike)):
            raise FigureExportValidationError("destination must be a path")
        if isinstance(self.destination, str) and not self.destination.strip():
            raise FigureExportValidationError("destination must not be empty")
        destination = Path(self.destination).expanduser()
        if figure_format is FigureFormat.PDF and destination.suffix.lower() != ".pdf":
            raise FigureExportValidationError(
                "PDF destination must be a file whose name ends in .pdf"
            )

        object.__setattr__(self, "format", figure_format)
        object.__setattr__(self, "unit_ids", unit_ids)
        object.__setattr__(self, "pages", pages)
        object.__setattr__(self, "destination", destination)


@dataclass(frozen=True, slots=True)
class GeneratedPage:
    """One concrete unit/page pair generated from an export plan."""

    unit_id: int
    unit_position: int
    page_index: int
    page: ExportPage


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Paths and page count produced by :func:`export_figures`."""

    format: FigureFormat
    destination: Path
    files: tuple[Path, ...]
    page_count: int


PlotDataProvider: TypeAlias = Callable[[int, PlotSpec], Any]


def iter_generated_pages(plan: ExportPlan) -> Iterator[GeneratedPage]:
    """Yield concrete pages in deterministic unit-major/page-major order."""

    for unit_position, unit_id in enumerate(plan.unit_ids):
        for page_index, page in enumerate(plan.pages):
            yield GeneratedPage(unit_id, unit_position, page_index, page)


def automatic_grid(plot_count: int) -> tuple[int, int]:
    """Return a compact ``(rows, columns)`` grid for a plot count."""

    if isinstance(plot_count, bool) or not isinstance(plot_count, int):
        raise FigureExportValidationError("plot count must be an integer")
    if plot_count < 1:
        raise FigureExportValidationError("plot count must be at least one")
    columns = math.ceil(math.sqrt(plot_count))
    rows = math.ceil(plot_count / columns)
    return rows, columns


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "Arial Bold.ttf" if bold else "Arial.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10 compatibility for downstream clients.
        return ImageFont.load_default()


def _finite_float(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FigureExportValidationError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise FigureExportValidationError(f"{label} must be finite")
    return number


def _boolean_option(
    options: Mapping[str, Any], key: str, *, default: bool
) -> bool:
    if key not in options:
        return default
    value = options[key]
    if not isinstance(value, bool):
        raise FigureExportValidationError(f"{key} must be a boolean")
    return value


def _color(value: Any, *, label: str) -> tuple[int, int, int]:
    try:
        return ImageColor.getrgb(str(value))
    except ValueError as exc:
        raise FigureExportValidationError(f"{label} is not a valid color") from exc


def _sequence(value: Any, *, label: str) -> list[Any]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise FigureExportValidationError(f"{label} must be a sequence")
    return list(value)


def _mapping_value(data: Any, *keys: str) -> Any:
    if isinstance(data, Mapping):
        for key in keys:
            if key in data:
                return data[key]
    return None


def _matrix_payload(data: Any) -> list[list[Any]]:
    source = _mapping_value(data, "matrix", "values", "data")
    if source is None:
        source = data
    rows = _sequence(source, label="map data")
    if not rows:
        raise FigureExportValidationError("map data must not be empty")
    matrix = [_sequence(row, label="map row") for row in rows]
    width = len(matrix[0])
    if width < 1 or any(len(row) != width for row in matrix):
        raise FigureExportValidationError("map data must be a non-empty rectangle")
    return matrix


def _rgb(value: Any) -> tuple[int, int, int]:
    channels = _sequence(value, label="RGB cell")
    if len(channels) not in (3, 4):
        raise FigureExportValidationError("RGB cells must have three or four channels")
    converted = [_finite_float(channel, label="RGB channel") for channel in channels[:3]]
    if max(converted, default=0.0) <= 1.0 and min(converted, default=0.0) >= 0.0:
        converted = [channel * 255.0 for channel in converted]
    if any(channel < 0.0 or channel > 255.0 for channel in converted):
        raise FigureExportValidationError("RGB channels must be in 0..1 or 0..255")
    return tuple(int(round(channel)) for channel in converted)  # type: ignore[return-value]


def _map_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FigureExportValidationError(
            "map values must be numeric, None, or NaN"
        ) from exc
    if math.isnan(number):
        return None
    if not math.isfinite(number):
        raise FigureExportValidationError("infinite map values are not supported")
    return number


def _scalar_matrix(matrix: Sequence[Sequence[Any]]) -> list[list[float | None]]:
    return [[_map_float(value) for value in row] for row in matrix]


def _palette(value: float, low: float, high: float, name: str) -> tuple[int, int, int]:
    fraction = 0.5 if high <= low else (value - low) / (high - low)
    fraction = max(0.0, min(1.0, fraction))
    name = name.lower()
    if name in {"gray", "grey", "grayscale"}:
        level = int(round(255.0 * fraction))
        return level, level, level
    if name in {"rdbu_r", "rdbu-r"}:
        stops = (
            (0.0, (5, 48, 97)),
            (0.25, (67, 147, 195)),
            (0.5, (247, 247, 247)),
            (0.75, (214, 96, 77)),
            (1.0, (103, 0, 31)),
        )
    elif name == "inferno":
        stops = (
            (0.0, (0, 0, 4)),
            (0.28, (87, 15, 109)),
            (0.55, (188, 55, 84)),
            (0.78, (249, 142, 8)),
            (1.0, (252, 255, 164)),
        )
    elif name == "delay":
        stops = (
            (0.0, (47, 88, 167)),
            (0.35, (44, 171, 184)),
            (0.68, (246, 204, 89)),
            (1.0, (203, 71, 45)),
        )
    else:  # Compact viridis approximation.
        stops = (
            (0.0, (68, 1, 84)),
            (0.25, (59, 82, 139)),
            (0.5, (33, 145, 140)),
            (0.75, (94, 201, 98)),
            (1.0, (253, 231, 37)),
        )
    for (left_x, left_color), (right_x, right_color) in zip(stops, stops[1:]):
        if fraction <= right_x:
            amount = (fraction - left_x) / (right_x - left_x)
            return tuple(
                int(round(left + (right - left) * amount))
                for left, right in zip(left_color, right_color)
            )  # type: ignore[return-value]
    return stops[-1][1]


def _bounds(
    matrix: Sequence[Sequence[float | None]], options: Mapping[str, Any]
) -> tuple[float, float]:
    values = [value for row in matrix for value in row if value is not None]
    low = min(values) if values else 0.0
    high = max(values) if values else 1.0
    if "vmin" in options:
        low = _finite_float(options["vmin"], label="vmin")
    if "vmax" in options:
        high = _finite_float(options["vmax"], label="vmax")
    if high < low:
        raise FigureExportValidationError("vmax must be greater than or equal to vmin")
    return low, high


def _draw_cartesian_map(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    spec: PlotSpec,
    *,
    rgb: bool,
) -> None:
    matrix = _matrix_payload(spec.data)
    rows, columns = len(matrix), len(matrix[0])
    left, top, right, bottom = box
    missing_color = _color(
        spec.options.get("missing_color", "#edf0f3"), label="missing_color"
    )
    if rgb:
        colors = [
            [missing_color if value is None else _rgb(value) for value in row]
            for row in matrix
        ]
    else:
        scalar = _scalar_matrix(matrix)
        low, high = _bounds(scalar, spec.options)
        palette = str(
            spec.options.get(
                "palette",
                "inferno" if spec.kind is PlotKind.DELAY_CARTESIAN else "viridis",
            )
        )
        colors = [
            [
                missing_color if value is None else _palette(value, low, high, palette)
                for value in row
            ]
            for row in scalar
        ]
    # Multi-row RF coordinates preserve square cells. A singleton y axis has
    # no physical height increment, so match the live viewer's legacy 30:7
    # footprint instead of exporting an unreadable horizontal strip.
    if rows == 1:
        target_aspect = SINGLETON_Y_REFERENCE_COLUMNS / SINGLETON_Y_REFERENCE_ROWS
        grid_width = min(right - left, (bottom - top) * target_aspect)
        grid_height = grid_width / target_aspect
        cell_width = grid_width / columns
        cell_height = grid_height
    else:
        cell_width = min((right - left) / columns, (bottom - top) / rows)
        cell_height = cell_width
        grid_width = cell_width * columns
        grid_height = cell_height * rows
    grid_left = (left + right - grid_width) / 2.0
    grid_top = (top + bottom - grid_height) / 2.0
    for y_index, row in enumerate(colors):
        y0 = round(grid_top + cell_height * y_index)
        y1 = round(grid_top + cell_height * (y_index + 1))
        for x_index, color in enumerate(row):
            x0 = round(grid_left + cell_width * x_index)
            x1 = round(grid_left + cell_width * (x_index + 1))
            draw.rectangle((x0, y0, x1, y1), fill=color)
    draw.rectangle(
        (
            round(grid_left),
            round(grid_top),
            round(grid_left + grid_width),
            round(grid_top + grid_height),
        ),
        outline="#334155",
        width=2,
    )


def _draw_polar_map(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    spec: PlotSpec,
    *,
    rgb: bool,
) -> None:
    matrix = _matrix_payload(spec.data)
    ring_order = str(spec.options.get("ring_order", "inner_to_outer"))
    if ring_order not in {"inner_to_outer", "outer_to_inner"}:
        raise FigureExportValidationError(
            "ring_order must be 'inner_to_outer' or 'outer_to_inner'"
        )
    if "reverse_rings" in spec.options:
        if not isinstance(spec.options["reverse_rings"], bool):
            raise FigureExportValidationError("reverse_rings must be a boolean")
        reverse_rings = spec.options["reverse_rings"]
    else:
        reverse_rings = ring_order == "outer_to_inner"
    if reverse_rings:
        matrix = list(reversed(matrix))
    rows, columns = len(matrix), len(matrix[0])
    missing_color = _color(
        spec.options.get("missing_color", "#edf0f3"), label="missing_color"
    )
    if rgb:
        colors = [
            [missing_color if value is None else _rgb(value) for value in row]
            for row in matrix
        ]
    else:
        scalar = _scalar_matrix(matrix)
        low, high = _bounds(scalar, spec.options)
        palette = str(
            spec.options.get(
                "palette",
                "inferno" if spec.kind is PlotKind.DELAY_POLAR else "viridis",
            )
        )
        colors = [
            [
                missing_color if value is None else _palette(value, low, high, palette)
                for value in row
            ]
            for row in scalar
        ]

    left, top, right, bottom = box
    diameter = max(2, min(right - left, bottom - top))
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    outer_radius = diameter / 2.0
    inner_blank_rows = _finite_float(
        spec.options.get("inner_blank_rows", 0.0), label="inner_blank_rows"
    )
    if inner_blank_rows < 0.0:
        raise FigureExportValidationError("inner_blank_rows must be non-negative")
    ring_span = float(SINGLETON_Y_REFERENCE_ROWS if rows == 1 else 1)
    radial_units = inner_blank_rows + rows * ring_span
    clockwise = _boolean_option(spec.options, "clockwise", default=True)
    total_degrees = _finite_float(
        spec.options.get("total_degrees", 360.0), label="total_degrees"
    )
    if total_degrees <= 0.0 or total_degrees > 360.0:
        raise FigureExportValidationError("total_degrees must be greater than 0 and at most 360")
    # Tk's polar view centers the complete visual-angle span on 12 o'clock.
    # Pillow angles increase clockwise in screen coordinates, so this is the
    # direct screen-space equivalent of Tk's
    # ``90 + total/2 - total*i/n`` mathematical-angle formula.
    center_angle = -90.0
    arc_start = center_angle - total_degrees / 2.0
    arc_end = center_angle + total_degrees / 2.0
    # Draw outer rings first; smaller rings overwrite their interior and leave
    # true annular wedges without requiring masking or numpy.
    for ring_index in reversed(range(rows)):
        radius = (
            outer_radius
            * (inner_blank_rows + (ring_index + 1) * ring_span)
            / radial_units
        )
        ring_box = (
            round(center_x - radius),
            round(center_y - radius),
            round(center_x + radius),
            round(center_y + radius),
        )
        for angle_index, color in enumerate(colors[ring_index]):
            fraction_start = angle_index / columns
            fraction_end = (angle_index + 1) / columns
            if clockwise:
                start = arc_start + fraction_start * total_degrees
                end = arc_start + fraction_end * total_degrees
            else:
                start = arc_end - fraction_end * total_degrees
                end = arc_end - fraction_start * total_degrees
            draw.pieslice(ring_box, start=start, end=end, fill=color)
    if inner_blank_rows > 0.0:
        inner_radius = outer_radius * inner_blank_rows / radial_units
        inner_color = _color(
            spec.options.get("inner_color", "#f8fafc"), label="inner_color"
        )
        draw.ellipse(
            (
                round(center_x - inner_radius),
                round(center_y - inner_radius),
                round(center_x + inner_radius),
                round(center_y + inner_radius),
            ),
            fill=inner_color,
            outline="#e2e8f0",
            width=1,
        )
    outline_box = (
        round(center_x - outer_radius),
        round(center_y - outer_radius),
        round(center_x + outer_radius),
        round(center_y + outer_radius),
    )
    if math.isclose(total_degrees, 360.0):
        draw.ellipse(outline_box, outline="#334155", width=2)
    else:
        outline_start, outline_end = arc_start, arc_end
        draw.arc(
            outline_box,
            start=outline_start,
            end=outline_end,
            fill="#334155",
            width=2,
        )
        for angle in (outline_start, outline_end):
            theta = math.radians(angle)
            draw.line(
                (
                    round(center_x),
                    round(center_y),
                    round(center_x + outer_radius * math.cos(theta)),
                    round(center_y + outer_radius * math.sin(theta)),
                ),
                fill="#334155",
                width=2,
            )


def _xy_payload(data: Any) -> tuple[list[float], list[float]]:
    values = _mapping_value(data, "values", "rates", "y")
    angles = _mapping_value(data, "angles_deg", "angles", "x", "times")
    if values is None:
        values = data
    y_values = [
        _finite_float(value, label="line value")
        for value in _sequence(values, label="line values")
    ]
    if not y_values:
        raise FigureExportValidationError("line data must not be empty")
    if angles is None:
        x_values = [360.0 * index / len(y_values) for index in range(len(y_values))]
    else:
        x_values = [
            _finite_float(value, label="line x value")
            for value in _sequence(angles, label="line x values")
        ]
    if len(x_values) != len(y_values):
        raise FigureExportValidationError("line x and y data must have equal lengths")
    return x_values, y_values


def _scale(value: float, low: float, high: float, start: float, end: float) -> float:
    if high <= low:
        return (start + end) / 2.0
    return start + (value - low) / (high - low) * (end - start)


def _draw_line(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    spec: PlotSpec,
) -> None:
    x_values, y_values = _xy_payload(spec.data)
    left, top, right, bottom = box
    padding = max(8, round(min(right - left, bottom - top) * 0.08))
    plot_box = (left + padding, top + padding, right - padding, bottom - padding)
    x_low, x_high = min(x_values), max(x_values)
    y_low, y_high = min(y_values), max(y_values)
    draw.line(
        (plot_box[0], plot_box[3], plot_box[2], plot_box[3]),
        fill="#64748b",
        width=2,
    )
    draw.line(
        (plot_box[0], plot_box[1], plot_box[0], plot_box[3]),
        fill="#64748b",
        width=2,
    )
    points = [
        (
            round(_scale(x, x_low, x_high, plot_box[0], plot_box[2])),
            round(_scale(y, y_low, y_high, plot_box[3], plot_box[1])),
        )
        for x, y in zip(x_values, y_values)
    ]
    if len(points) == 1:
        x, y = points[0]
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill="#2563eb")
    else:
        draw.line(points, fill=str(spec.options.get("color", "#2563eb")), width=4)


def _timeline_index(
    data: Mapping[str, Any], key: str, *, default: int, count: int
) -> int:
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise FigureExportValidationError(f"timeline {key} must be an integer")
    if value < 0 or value >= count:
        raise FigureExportValidationError(
            f"timeline {key} must be inside 0..{count - 1}"
        )
    return value


def _draw_timeline_curves(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    data: Mapping[str, Any],
) -> None:
    totals_source = data.get("totals")
    totals = [
        _finite_float(value, label="timeline total")
        for value in _sequence(totals_source, label="timeline totals")
    ]
    if not totals:
        raise FigureExportValidationError("timeline totals must not be empty")
    times_source = data.get("times")
    if times_source is None:
        times = [float(index) for index in range(len(totals))]
    else:
        times = [
            _finite_float(value, label="timeline time")
            for value in _sequence(times_source, label="timeline times")
        ]
    if len(times) != len(totals):
        raise FigureExportValidationError(
            "timeline times and totals must have equal lengths"
        )

    selected_source = data.get("selected")
    selected: list[float] | None = None
    if selected_source is not None:
        selected = [
            _finite_float(value, label="selected-cell timeline value")
            for value in _sequence(selected_source, label="selected-cell timeline")
        ]
        if len(selected) != len(times):
            raise FigureExportValidationError(
                "timeline selected curve must have the same length as times"
            )

    selection_start = _timeline_index(
        data, "selection_start_index", default=0, count=len(times)
    )
    selection_end = _timeline_index(
        data,
        "selection_end_index",
        default=len(times) - 1,
        count=len(times),
    )
    if selection_end < selection_start:
        raise FigureExportValidationError(
            "timeline selection_end_index must not precede selection_start_index"
        )
    active_index = (
        _timeline_index(data, "active_index", default=0, count=len(times))
        if "active_index" in data
        else None
    )

    left, top, right, bottom = box
    x_padding = max(24, round((right - left) * 0.06))
    y_padding = max(14, round((bottom - top) * 0.13))
    plot_box = (
        left + x_padding,
        top + y_padding,
        right - x_padding,
        bottom - y_padding,
    )
    plot_width = plot_box[2] - plot_box[0]
    plot_height = plot_box[3] - plot_box[1]
    if selection_start != 0 or selection_end != len(times) - 1:
        selection_x0 = plot_box[0] + plot_width * selection_start / len(times)
        selection_x1 = plot_box[0] + plot_width * (selection_end + 1) / len(times)
        draw.rectangle(
            (selection_x0, plot_box[1], selection_x1, plot_box[3]),
            outline="#16a34a",
            width=2,
        )
    if active_index is not None:
        active_x = plot_box[0] + plot_width * (active_index + 0.5) / len(times)
        draw.line(
            (active_x, plot_box[1], active_x, plot_box[3]),
            fill="#7c3aed",
            width=1,
        )

    draw.line(
        (plot_box[0], plot_box[3], plot_box[2], plot_box[3]),
        fill="#64748b",
        width=2,
    )
    draw.line(
        (plot_box[2], plot_box[1], plot_box[2], plot_box[3]),
        fill="#2563eb",
        width=2,
    )
    if selected is not None:
        draw.line(
            (plot_box[0], plot_box[1], plot_box[0], plot_box[3]),
            fill="#dc2626",
            width=2,
        )

    legend_font = _font(max(9, round((bottom - top) * 0.08)))
    legend_x = plot_box[0]
    curves: list[tuple[str, list[float], str, float]] = [
        ("all positions", totals, "#2563eb", max(max(totals), 1.0))
    ]
    if selected is not None:
        curves.append(
            ("selected cell", selected, "#dc2626", max(max(selected), 1.0))
        )
    for label, values, color, maximum in curves:
        points = [
            (
                round(plot_box[0] + plot_width * (index + 0.5) / len(times)),
                round(plot_box[3] - plot_height * value / maximum),
            )
            for index, value in enumerate(values)
        ]
        if len(points) == 1:
            x, y = points[0]
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
        else:
            draw.line(points, fill=color, width=3)
        draw.line(
            (legend_x, top + 4, legend_x + 16, top + 4),
            fill=color,
            width=3,
        )
        draw.text(
            (legend_x + 20, top + 4),
            label,
            fill="#334155",
            font=legend_font,
            anchor="lm",
        )
        text_box = draw.textbbox((0, 0), label, font=legend_font)
        legend_x += 28 + text_box[2] - text_box[0]

    axis_font = _font(max(8, round((bottom - top) * 0.065)))
    blue_maximum = curves[0][3]
    draw.text(
        (plot_box[2] + 4, plot_box[1]),
        f"{blue_maximum:.3g}",
        fill="#2563eb",
        font=axis_font,
        anchor="la",
    )
    draw.text(
        (plot_box[2] + 4, plot_box[3]),
        "0",
        fill="#2563eb",
        font=axis_font,
        anchor="ld",
    )
    if selected is not None:
        red_maximum = curves[1][3]
        draw.text(
            (plot_box[0] - 4, plot_box[1]),
            f"{red_maximum:.3g}",
            fill="#dc2626",
            font=axis_font,
            anchor="ra",
        )
        draw.text(
            (plot_box[0] - 4, plot_box[3]),
            "0",
            fill="#dc2626",
            font=axis_font,
            anchor="rd",
        )


def _draw_polar_line(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    spec: PlotSpec,
) -> None:
    angles, values = _xy_payload(spec.data)
    left, top, right, bottom = box
    radius = min(right - left, bottom - top) * 0.43
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    maximum = max(max(values), 0.0)
    minimum = min(min(values), 0.0)
    draw.ellipse(
        (
            round(center_x - radius),
            round(center_y - radius),
            round(center_x + radius),
            round(center_y + radius),
        ),
        outline="#94a3b8",
        width=2,
    )
    for angle in (0, 90, 180, 270):
        theta = math.radians(angle - 90.0)
        draw.line(
            (
                round(center_x),
                round(center_y),
                round(center_x + radius * math.cos(theta)),
                round(center_y + radius * math.sin(theta)),
            ),
            fill="#e2e8f0",
            width=1,
        )
    clockwise = _boolean_option(spec.options, "clockwise", default=True)
    points: list[tuple[int, int]] = []
    for angle, value in zip(angles, values):
        normalized = 0.0 if maximum <= minimum else (value - minimum) / (maximum - minimum)
        theta_degrees = (-angle if clockwise else angle) - 90.0
        theta = math.radians(theta_degrees)
        points.append(
            (
                round(center_x + radius * normalized * math.cos(theta)),
                round(center_y + radius * normalized * math.sin(theta)),
            )
        )
    if len(points) == 1:
        x, y = points[0]
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill="#2563eb")
    else:
        draw.line(
            points + points[:1],
            fill=str(spec.options.get("color", "#2563eb")),
            width=4,
            joint="curve",
        )


def _draw_timeline(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    spec: PlotSpec,
) -> None:
    frames = _mapping_value(spec.data, "frames")
    totals = _mapping_value(spec.data, "totals")
    if frames is None and totals is None:
        _draw_line(draw, box, spec)
        return
    if not isinstance(spec.data, Mapping):
        raise FigureExportValidationError(
            "timeline data with curves or frames must be a mapping"
        )

    left, top, right, bottom = box
    if totals is not None:
        curve_bottom = bottom if frames is None else top + round((bottom - top) * 0.42)
        _draw_timeline_curves(draw, (left, top, right, curve_bottom), spec.data)
        if frames is None:
            return
        top = curve_bottom + max(6, round((bottom - curve_bottom) * 0.04))

    frame_list = _sequence(frames, label="timeline frames")
    if not frame_list:
        raise FigureExportValidationError("timeline frames must not be empty")
    frame_values = [
        value
        for frame in frame_list
        for row in _scalar_matrix(_matrix_payload(frame))
        for value in row
        if value is not None
    ]
    frame_options = dict(spec.options)
    frame_options.setdefault("vmin", 0.0)
    frame_options.setdefault("vmax", max(max(frame_values, default=0.0), 1.0))

    selection_start = _timeline_index(
        spec.data,
        "selection_start_index",
        default=0,
        count=len(frame_list),
    )
    selection_end = _timeline_index(
        spec.data,
        "selection_end_index",
        default=len(frame_list) - 1,
        count=len(frame_list),
    )
    if selection_end < selection_start:
        raise FigureExportValidationError(
            "timeline selection_end_index must not precede selection_start_index"
        )
    active_index = (
        _timeline_index(
            spec.data,
            "active_index",
            default=0,
            count=len(frame_list),
        )
        if "active_index" in spec.data
        else None
    )
    times_source = spec.data.get("times")
    frame_times: list[float] | None = None
    if times_source is not None:
        parsed_times = [
            _finite_float(value, label="timeline time")
            for value in _sequence(times_source, label="timeline times")
        ]
        if len(parsed_times) == len(frame_list):
            frame_times = parsed_times

    rows, columns = automatic_grid(len(frame_list))
    gap = max(2, round(min(right - left, bottom - top) * 0.01))
    for index, frame in enumerate(frame_list):
        row, column = divmod(index, columns)
        frame_box = (
            left + round((right - left) * column / columns) + gap,
            top + round((bottom - top) * row / rows) + gap,
            left + round((right - left) * (column + 1) / columns) - gap,
            top + round((bottom - top) * (row + 1) / rows) - gap,
        )
        label_height = (
            max(9, round((frame_box[3] - frame_box[1]) * 0.16))
            if frame_times is not None and frame_box[3] - frame_box[1] >= 36
            else 0
        )
        map_box = (
            frame_box[0],
            frame_box[1],
            frame_box[2],
            frame_box[3] - label_height,
        )
        frame_spec = replace(spec, data=frame, options=frame_options)
        if _boolean_option(spec.options, "polar", default=False):
            _draw_polar_map(draw, map_box, frame_spec, rgb=False)
        else:
            _draw_cartesian_map(draw, map_box, frame_spec, rgb=False)
        if label_height and frame_times is not None:
            label_font = _font(max(7, label_height - 2))
            draw.text(
                ((frame_box[0] + frame_box[2]) / 2.0, frame_box[3]),
                f"{frame_times[index]:.3g} ms",
                fill="#475467",
                font=label_font,
                anchor="mb",
            )
        if active_index == index:
            draw.rectangle(frame_box, outline="#7c3aed", width=3)
        elif (
            (selection_start != 0 or selection_end != len(frame_list) - 1)
            and selection_start <= index <= selection_end
        ):
            draw.rectangle(frame_box, outline="#16a34a", width=2)


def _unavailable_message(spec: PlotSpec) -> str | None:
    option_message = spec.options.get("unavailable_message")
    if option_message is not None:
        message = str(option_message).strip()
        return message or "This view is unavailable for the selected unit."
    if isinstance(spec.data, Mapping):
        unavailable = spec.data.get("unavailable")
        if isinstance(unavailable, str):
            return unavailable.strip() or "This view is unavailable for the selected unit."
        if unavailable is True or spec.data.get("available") is False:
            message = str(spec.data.get("message", "")).strip()
            return message or "This view is unavailable for the selected unit."
    return None


def _draw_unavailable(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    message: str,
) -> None:
    left, top, right, bottom = box
    font = _font(max(12, round((bottom - top) * 0.055)))
    # Keep placeholder wrapping deterministic and independent of GUI toolkits.
    font_size = int(getattr(font, "size", 12))
    approximate_characters = max(16, round((right - left) / max(7, font_size * 0.55)))
    words = message.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > approximate_characters:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if not lines:
        lines = ["This view is unavailable for the selected unit."]
    line_height = max(font_size + 4, round((bottom - top) * 0.08))
    first_y = (top + bottom - line_height * (len(lines) - 1)) / 2.0
    for index, line in enumerate(lines):
        draw.text(
            ((left + right) / 2.0, first_y + index * line_height),
            line,
            fill="#64748b",
            font=font,
            anchor="mm",
        )


def _point_payload(
    data: Any, *, allow_empty: bool = False
) -> list[tuple[float, float, str, str]]:
    source = _mapping_value(data, "points", "sites", "values")
    if source is None:
        source = data
    points: list[tuple[float, float, str, str]] = []
    for index, point in enumerate(_sequence(source, label="probe points")):
        if isinstance(point, Mapping):
            x = _finite_float(point.get("x"), label="probe x")
            y = _finite_float(point.get("y"), label="probe y")
            label = str(point.get("label", point.get("unit_id", "")))
            color = str(point.get("color", "#2563eb"))
        else:
            fields = _sequence(point, label="probe point")
            if len(fields) < 2:
                raise FigureExportValidationError(
                    "probe points need at least x and y coordinates"
                )
            x = _finite_float(fields[0], label="probe x")
            y = _finite_float(fields[1], label="probe y")
            label = str(fields[2]) if len(fields) > 2 else ""
            color = str(fields[3]) if len(fields) > 3 else "#2563eb"
        points.append((x, y, label, color))
    if not points and not allow_empty:
        raise FigureExportValidationError("probe points must not be empty")
    return points


def _draw_probe_layout(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    spec: PlotSpec,
) -> None:
    missing_position = _mapping_value(
        spec.data, "missingPosition", "missing_position"
    ) is True
    points = _point_payload(spec.data, allow_empty=missing_position)
    left, top, right, bottom = box
    padding = max(12, round(min(right - left, bottom - top) * 0.08))
    x_values = [point[0] for point in points] or [-1.0, 1.0]
    y_values = [point[1] for point in points] or [0.0, 1.0]
    x_low, x_high = min(x_values), max(x_values)
    y_low, y_high = min(y_values), max(y_values)
    label_font = _font(max(10, round((bottom - top) * 0.035)))
    for x, y, label, color in points:
        pixel_x = round(_scale(x, x_low, x_high, left + padding, right - padding))
        pixel_y = round(_scale(y, y_low, y_high, bottom - padding, top + padding))
        point_radius = max(4, round(min(right - left, bottom - top) * 0.015))
        draw.ellipse(
            (
                pixel_x - point_radius,
                pixel_y - point_radius,
                pixel_x + point_radius,
                pixel_y + point_radius,
            ),
            fill=color,
            outline="white",
            width=1,
        )
        if label:
            draw.text(
                (pixel_x + point_radius + 3, pixel_y),
                label,
                fill="#0f172a",
                font=label_font,
                anchor="lm",
            )
    if missing_position:
        annotation_font = _font(
            max(18, round((bottom - top) * 0.09)), bold=True
        )
        draw.text(
            ((left + right) / 2.0, (top + bottom) / 2.0),
            "NaN",
            fill="#b42318",
            font=annotation_font,
            stroke_width=3,
            stroke_fill="#ffffff",
            anchor="mm",
        )


def _waveform_payload(
    data: Any,
) -> tuple[list[list[float | None]], list[float], list[float], list[str], int]:
    if not isinstance(data, Mapping):
        raise FigureExportValidationError(
            "waveform data must contain matrix, times_ms, channel_labels, and best_channel_row"
        )
    matrix = _scalar_matrix(_matrix_payload(data))
    rows, columns = len(matrix), len(matrix[0])
    times_source = _mapping_value(data, "times_ms", "timesMs", "time_ms")
    labels_source = _mapping_value(data, "channel_labels", "channelLabels")
    best_row = _mapping_value(data, "best_channel_row", "bestChannelRow")
    if times_source is None or labels_source is None:
        raise FigureExportValidationError("waveform times and channel labels are required")
    times = [
        _finite_float(value, label="waveform time")
        for value in _sequence(times_source, label="waveform times")
    ]
    if len(times) != columns or any(b <= a for a, b in zip(times, times[1:])):
        raise FigureExportValidationError(
            "waveform times must match matrix columns and increase strictly"
        )
    edges_source = _mapping_value(data, "time_edges_ms", "timeEdgesMs")
    if edges_source is None:
        if len(times) == 1:
            edges = [times[0] - 0.5, times[0] + 0.5]
        else:
            edges = [times[0] - (times[1] - times[0]) / 2]
            edges.extend((a + b) / 2 for a, b in zip(times, times[1:]))
            edges.append(times[-1] + (times[-1] - times[-2]) / 2)
    else:
        edges = [
            _finite_float(value, label="waveform time edge")
            for value in _sequence(edges_source, label="waveform time edges")
        ]
    if len(edges) != columns + 1 or any(b <= a for a, b in zip(edges, edges[1:])):
        raise FigureExportValidationError("waveform time edges are invalid")
    labels = [
        str(value).strip()
        for value in _sequence(labels_source, label="waveform channel labels")
    ]
    if len(labels) != rows or any(not label for label in labels):
        raise FigureExportValidationError("waveform channel labels must match matrix rows")
    if isinstance(best_row, bool) or not isinstance(best_row, int) or not 0 <= best_row < rows:
        raise FigureExportValidationError("waveform best channel row is invalid")
    return matrix, times, edges, labels, best_row


def _draw_waveform_heatmap(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    spec: PlotSpec,
) -> None:
    matrix, times, edges, labels, best_row = _waveform_payload(spec.data)
    rows, columns = len(matrix), len(matrix[0])
    left, top, right, bottom = box
    label_font = _font(max(9, round((bottom - top) * 0.04)))
    label_width = max(
        (draw.textbbox((0, 0), label, font=label_font)[2] for label in labels),
        default=0,
    )
    grid = (
        left + min(max(48, label_width + 14), max(48, (right - left) // 3)),
        top + 6,
        right - 66,
        bottom - 32,
    )
    if grid[2] - grid[0] < columns or grid[3] - grid[1] < rows:
        raise FigureExportValidationError("plot panel is too small for waveform data")
    low, high = _bounds(matrix, spec.options)
    amplitude = max(abs(low), abs(high), 1e-12)
    low, high = -amplitude, amplitude
    row_height = (grid[3] - grid[1]) / rows

    def time_x(value: float) -> float:
        return _scale(value, edges[0], edges[-1], grid[0], grid[2])

    for row_index, row in enumerate(matrix):
        y0 = round(grid[1] + row_height * row_index)
        y1 = round(grid[1] + row_height * (row_index + 1))
        for column_index, value in enumerate(row):
            color = "#edf0f3" if value is None else _palette(value, low, high, "rdbu_r")
            draw.rectangle(
                (
                    round(time_x(edges[column_index])),
                    y0,
                    round(time_x(edges[column_index + 1])),
                    y1,
                ),
                fill=color,
            )
        draw.text(
            (grid[0] - 7, (y0 + y1) / 2),
            labels[row_index],
            fill="#b42318" if row_index == best_row else "#475467",
            font=label_font,
            anchor="rm",
        )
    best_y0 = round(grid[1] + row_height * best_row)
    best_y1 = round(grid[1] + row_height * (best_row + 1))
    draw.rectangle((grid[0], best_y0, grid[2], best_y1), outline="#dc2626", width=2)
    if edges[0] <= 0 <= edges[-1]:
        zero_x = round(time_x(0))
        draw.line((zero_x, grid[1], zero_x, grid[3]), fill="#111827", width=1)
    draw.rectangle(grid, outline="#334155", width=2)
    tick_indices = sorted({0, len(times) // 2, len(times) - 1})
    for index in tick_indices:
        x = round(time_x(times[index]))
        draw.text(
            (x, grid[3] + 5),
            f"{times[index]:.4g}",
            fill="#475467",
            font=label_font,
            anchor="ma",
        )
    bar_left = grid[2] + 18
    bar_top, bar_bottom = grid[1], grid[3]
    for pixel_y in range(bar_top, bar_bottom):
        fraction = 1 - (pixel_y - bar_top) / max(1, bar_bottom - bar_top - 1)
        value = low + fraction * (high - low)
        draw.line(
            (bar_left, pixel_y, bar_left + 12, pixel_y),
            fill=_palette(value, low, high, "rdbu_r"),
        )
    draw.text((bar_left + 17, bar_top), f"{high:.3g}", fill="#475467", font=label_font, anchor="lm")
    draw.text((bar_left + 17, bar_bottom), f"{low:.3g}", fill="#475467", font=label_font, anchor="lm")
    draw.text((bar_left + 6, (bar_top + bar_bottom) / 2), "µV", fill="#475467", font=label_font, anchor="mm")


class PillowFigureRenderer:
    """Deterministic off-screen renderer used by preview and export."""

    def __init__(self, page_size: tuple[int, int] = DEFAULT_PAGE_SIZE) -> None:
        if (
            not isinstance(page_size, Sequence)
            or len(page_size) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in page_size)
            or page_size[0] < 320
            or page_size[1] < 240
        ):
            raise FigureExportValidationError(
                "page size must be two integers of at least 320 x 240 pixels"
            )
        self.page_size = int(page_size[0]), int(page_size[1])

    def render_preview(
        self,
        unit_id: int,
        page: ExportPage,
        *,
        data_provider: PlotDataProvider | None = None,
    ) -> Image.Image:
        """Render a live preview with the exact final-export code path."""

        return self.render_page(unit_id, page, data_provider=data_provider)

    def render_page(
        self,
        unit_id: int,
        page: ExportPage,
        *,
        data_provider: PlotDataProvider | None = None,
    ) -> Image.Image:
        if isinstance(unit_id, bool) or not isinstance(unit_id, int):
            raise FigureExportValidationError("unit ID must be an integer")
        if not isinstance(page, ExportPage):
            raise FigureExportValidationError("page must be an ExportPage")

        width, height = self.page_size
        image = Image.new("RGB", self.page_size, "white")
        draw = ImageDraw.Draw(image)
        margin = max(18, round(min(width, height) * 0.025))
        header_height = max(50, round(height * 0.07))
        title_font = _font(max(18, round(height * 0.026)), bold=True)
        draw.text(
            (margin, margin),
            f"Unit {unit_id} — {page.name}",
            fill="#0f172a",
            font=title_font,
            anchor="la",
        )

        rows, columns = automatic_grid(len(page.plots))
        grid_top = margin + header_height
        grid_bottom = height - margin
        gap = max(10, round(min(width, height) * 0.014))
        for plot_index, template in enumerate(page.plots):
            row, column = divmod(plot_index, columns)
            panel = (
                margin + round((width - 2 * margin) * column / columns) + gap // 2,
                grid_top
                + round((grid_bottom - grid_top) * row / rows)
                + gap // 2,
                margin
                + round((width - 2 * margin) * (column + 1) / columns)
                - gap // 2,
                grid_top
                + round((grid_bottom - grid_top) * (row + 1) / rows)
                - gap // 2,
            )
            spec = self._resolved_spec(unit_id, template, data_provider)
            try:
                self._draw_plot(draw, panel, spec)
            except FigureExportValidationError as exc:
                raise FigureExportValidationError(
                    f"cannot render unit {unit_id}, page {page.name!r}, "
                    f"plot {plot_index + 1} ({spec.kind.value}): {exc}"
                ) from exc
        return image

    @staticmethod
    def _resolved_spec(
        unit_id: int,
        template: PlotSpec,
        data_provider: PlotDataProvider | None,
    ) -> PlotSpec:
        if data_provider is None:
            spec = template
        else:
            resolved = data_provider(unit_id, template)
            spec = resolved if isinstance(resolved, PlotSpec) else replace(template, data=resolved)
        if spec.kind is not template.kind:
            raise FigureExportValidationError(
                f"data provider changed plot kind from {template.kind.value!r} "
                f"to {spec.kind.value!r}"
            )
        if spec.data is None and "unavailable_message" not in spec.options:
            raise FigureExportValidationError(
                f"plot {spec.kind.value!r} has no data and no provider supplied it"
            )
        return spec

    @staticmethod
    def _draw_plot(
        draw: ImageDraw.ImageDraw,
        panel: tuple[int, int, int, int],
        spec: PlotSpec,
    ) -> None:
        left, top, right, bottom = panel
        if right - left < 40 or bottom - top < 40:
            raise FigureExportValidationError("page has too many plots for its size")
        draw.rounded_rectangle(panel, radius=10, fill="#f8fafc", outline="#cbd5e1", width=2)
        definition = PLOT_KIND_REGISTRY[spec.kind.value]
        title = spec.title.strip() if spec.title and spec.title.strip() else definition.label
        subtitle = str(spec.options.get("subtitle", "")).strip()
        title_height = max(30, round((bottom - top) * (0.16 if subtitle else 0.11)))
        title_font = _font(max(13, round((bottom - top) * 0.048)), bold=True)
        draw.text(
            (left + 12, top + (title_height * (0.34 if subtitle else 0.5))),
            title,
            fill="#0f172a",
            font=title_font,
            anchor="lm",
        )
        if subtitle:
            subtitle_font = _font(max(9, round((bottom - top) * 0.029)))
            draw.text(
                (left + 12, top + title_height * 0.72),
                subtitle,
                fill="#667085",
                font=subtitle_font,
                anchor="lm",
            )
        plot_box = (left + 12, top + title_height, right - 12, bottom - 12)

        unavailable = _unavailable_message(spec)
        if unavailable is not None:
            _draw_unavailable(draw, plot_box, unavailable)
            return

        if spec.kind in {
            PlotKind.RF_CARTESIAN,
            PlotKind.DELAY_CARTESIAN,
        }:
            _draw_cartesian_map(draw, plot_box, spec, rgb=False)
        elif spec.kind is PlotKind.RGB_CARTESIAN:
            _draw_cartesian_map(draw, plot_box, spec, rgb=True)
        elif spec.kind in {PlotKind.RF_POLAR, PlotKind.DELAY_POLAR}:
            _draw_polar_map(draw, plot_box, spec, rgb=False)
        elif spec.kind is PlotKind.RGB_POLAR:
            _draw_polar_map(draw, plot_box, spec, rgb=True)
        elif spec.kind is PlotKind.TIMELINE_CURRENT:
            _draw_timeline(draw, plot_box, spec)
        elif spec.kind is PlotKind.HD_LINE:
            _draw_line(draw, plot_box, spec)
        elif spec.kind is PlotKind.HD_POLAR:
            _draw_polar_line(draw, plot_box, spec)
        elif spec.kind is PlotKind.PROBE_LAYOUT:
            _draw_probe_layout(draw, plot_box, spec)
        elif spec.kind is PlotKind.WAVEFORM_LOCAL_AVERAGE:
            _draw_waveform_heatmap(draw, plot_box, spec)
        else:  # Defensive against future Enum additions without renderer wiring.
            raise FigureExportValidationError(
                f"plot kind {spec.kind.value!r} has no renderer"
            )


def render_live_preview(
    plan: ExportPlan,
    unit_id: int,
    page_index: int,
    *,
    data_provider: PlotDataProvider | None = None,
    renderer: PillowFigureRenderer | None = None,
) -> Image.Image:
    """Render one selected plan page without touching the destination."""

    if unit_id not in plan.unit_ids:
        raise FigureExportValidationError(f"unit {unit_id} is not selected in this plan")
    if isinstance(page_index, bool) or not isinstance(page_index, int):
        raise FigureExportValidationError("page index must be an integer")
    if page_index < 0 or page_index >= len(plan.pages):
        raise FigureExportValidationError(
            f"page index {page_index} is outside 0..{len(plan.pages) - 1}"
        )
    active_renderer = renderer or PillowFigureRenderer()
    return active_renderer.render_preview(
        unit_id, plan.pages[page_index], data_provider=data_provider
    )


def _safe_component(value: Any) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip()).strip("-._")
    return component or "unnamed"


def _page_filename(generated: GeneratedPage, suffix: str) -> str:
    return (
        f"{generated.unit_position + 1:04d}__"
        f"unit-{_safe_component(generated.unit_id)}__"
        f"page-{generated.page_index + 1:02d}-{_safe_component(generated.page.name)}"
        f".{suffix}"
    )


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue()


def _svg_bytes(image: Image.Image) -> bytes:
    encoded = base64.b64encode(_png_bytes(image)).decode("ascii")
    width, height = image.size
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
        f'  <image width="{width}" height="{height}" '
        f'href="data:image/png;base64,{encoded}"/>\n'
        "</svg>\n"
    )
    return document.encode("utf-8")


@dataclass(frozen=True, slots=True)
class _EntryIdentity:
    device: int
    inode: int
    mode: int

    @classmethod
    def from_stat(cls, result: os.stat_result) -> _EntryIdentity:
        return cls(result.st_dev, result.st_ino, result.st_mode)


class _ParentDirectory:
    """A pinned, non-symlink parent directory used for relative publication."""

    __slots__ = ("path", "fd", "identity")

    def __init__(self, path: Path, fd: int):
        self.path = path
        self.fd = fd
        self.identity = _EntryIdentity.from_stat(os.fstat(fd))

    def verify(self) -> None:
        """Fail closed if the path no longer names the pinned directory."""

        try:
            path_stat = os.lstat(self.path)
        except OSError as exc:
            raise FigureExportError(
                f"export parent directory disappeared while rendering: {self.path}"
            ) from exc
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
            raise FigureExportError(
                f"export parent path changed type while rendering: {self.path}"
            )
        comparison_fd = os.open(self.path, _directory_open_flags())
        try:
            current = _EntryIdentity.from_stat(os.fstat(comparison_fd))
        finally:
            os.close(comparison_fd)
        pinned = _EntryIdentity.from_stat(os.fstat(self.fd))
        if current != self.identity or pinned != self.identity:
            raise FigureExportError(
                f"export parent directory was replaced while rendering: {self.path}"
            )


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _fsync_directory_fd(descriptor: int) -> None:
    """Durably flush a directory where the host filesystem supports it."""

    try:
        os.fsync(descriptor)
    except OSError as exc:
        unsupported = {errno.EINVAL, errno.EOPNOTSUPP}
        if hasattr(errno, "ENOTSUP"):
            unsupported.add(errno.ENOTSUP)
        if exc.errno not in unsupported:
            raise


@contextmanager
def _open_parent_directory(path: Path) -> Iterator[_ParentDirectory]:
    try:
        path_stat = os.lstat(path)
    except OSError as exc:
        raise FigureExportError(f"cannot open export parent directory: {path}") from exc
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        raise FigureExportError(f"export parent must be a real directory: {path}")
    descriptor = os.open(path, _directory_open_flags())
    parent = _ParentDirectory(path, descriptor)
    try:
        parent.verify()
        yield parent
    finally:
        os.close(descriptor)


def _entry_lstat(parent: _ParentDirectory, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _same_identity(result: os.stat_result, identity: _EntryIdentity) -> bool:
    return (
        result.st_dev == identity.device
        and result.st_ino == identity.inode
        and result.st_mode == identity.mode
    )


def _atomic_write_bytes_at(directory_fd: int, name: str, contents: bytes) -> None:
    temporary = f".{name}.tmp-{uuid.uuid4().hex}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _read_regular_bytes_at(
    directory_fd: int,
    name: str,
    *,
    maximum_size: int = 16 * 1024 * 1024,
) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise FigureExportError(f"export member is not a regular file: {name}")
        if file_stat.st_size > maximum_size:
            raise FigureExportError(f"export metadata file is too large: {name}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_size + 1 - total))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_size:
                raise FigureExportError(f"export metadata file is too large: {name}")
    finally:
        os.close(descriptor)


def _manifest_json_value(value: Any, *, label: str) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FigureExportValidationError(f"{label} must contain finite numbers")
        return value
    if isinstance(value, Mapping):
        converted: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise FigureExportValidationError(f"{label} keys must be strings")
            converted[key] = _manifest_json_value(value[key], label=f"{label}.{key}")
        return converted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _manifest_json_value(item, label=f"{label}[{index}]")
            for index, item in enumerate(value)
        ]
    raise FigureExportValidationError(
        f"{label} contains unsupported value type {type(value).__name__}"
    )


def _manifest_spec(plan: ExportPlan) -> dict[str, Any]:
    return {
        "unitIds": list(plan.unit_ids),
        "pages": [
            {
                "name": page.name,
                "plots": [
                    {
                        "kind": plot.kind.value,
                        "title": plot.title,
                        "options": _manifest_json_value(
                            plot.options,
                            label=f"plot options for {plot.kind.value}",
                        ),
                    }
                    for plot in page.plots
                ],
            }
            for page in plan.pages
        ],
    }


def _page_manifest_metadata(generated: GeneratedPage, suffix: str) -> dict[str, Any]:
    return {
        "file": _page_filename(generated, suffix),
        "unitId": generated.unit_id,
        "unitPosition": generated.unit_position,
        "pageIndex": generated.page_index,
        "pageName": generated.page.name,
    }


def _manifest_document(
    plan: ExportPlan,
    generated_pages: Sequence[GeneratedPage],
    rendered_integrity: Sequence[tuple[int, str]],
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for generated, (size, digest) in zip(
        generated_pages,
        rendered_integrity,
        strict=True,
    ):
        entry = _page_manifest_metadata(generated, plan.format.value)
        entry.update(
            {
                "size": size,
                "sha256": digest,
            }
        )
        files.append(entry)
    return {
        "manifestVersion": EXPORT_MANIFEST_VERSION,
        "producer": EXPORT_PRODUCER,
        "format": plan.format.value,
        "spec": _manifest_spec(plan),
        "files": files,
    }


def _manifest_bytes(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _validate_manifest_spec_shape(spec: Any) -> None:
    if not isinstance(spec, dict):
        raise FigureExportError("export manifest spec must be an object")
    unit_ids = spec.get("unitIds")
    pages = spec.get("pages")
    if (
        not isinstance(unit_ids, list)
        or not unit_ids
        or any(isinstance(unit_id, bool) or not isinstance(unit_id, int) for unit_id in unit_ids)
        or len(set(unit_ids)) != len(unit_ids)
        or not isinstance(pages, list)
        or not pages
    ):
        raise FigureExportError("export manifest contains an invalid figure spec")
    page_names: list[str] = []
    for page in pages:
        if not isinstance(page, dict) or set(page) != {"name", "plots"}:
            raise FigureExportError("export manifest contains an invalid page spec")
        if not isinstance(page["name"], str) or not page["name"]:
            raise FigureExportError("export manifest contains an invalid page name")
        if page["name"] in page_names:
            raise FigureExportError("export manifest contains duplicate page names")
        page_names.append(page["name"])
        plots = page["plots"]
        if not isinstance(plots, list) or not plots:
            raise FigureExportError("export manifest page must contain plots")
        for plot in plots:
            if not isinstance(plot, dict) or set(plot) != {"kind", "title", "options"}:
                raise FigureExportError("export manifest contains an invalid plot spec")
            if plot["kind"] not in PLOT_KIND_REGISTRY:
                raise FigureExportError("export manifest contains an unknown plot kind")
            if plot["title"] is not None and not isinstance(plot["title"], str):
                raise FigureExportError("export manifest contains an invalid plot title")
            if not isinstance(plot["options"], dict):
                raise FigureExportError("export manifest contains invalid plot options")


def _metadata_from_manifest_spec(
    spec: Mapping[str, Any],
    figure_format: str,
) -> list[dict[str, Any]]:
    metadata: list[dict[str, Any]] = []
    for unit_position, unit_id in enumerate(spec["unitIds"]):
        for page_index, page in enumerate(spec["pages"]):
            metadata.append(
                {
                    "file": (
                        f"{unit_position + 1:04d}__"
                        f"unit-{_safe_component(unit_id)}__"
                        f"page-{page_index + 1:02d}-{_safe_component(page['name'])}"
                        f".{figure_format}"
                    ),
                    "unitId": unit_id,
                    "unitPosition": unit_position,
                    "pageIndex": page_index,
                    "pageName": page["name"],
                }
            )
    return metadata


def _open_directory_entry(
    parent: _ParentDirectory,
    name: str,
) -> tuple[int, _EntryIdentity]:
    entry_stat = _entry_lstat(parent, name)
    if entry_stat is None:
        raise FileNotFoundError(name)
    if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(entry_stat.st_mode):
        raise FigureExportError(f"export target must be a real directory: {parent.path / name}")
    descriptor = os.open(name, _directory_open_flags(), dir_fd=parent.fd)
    opened = os.fstat(descriptor)
    if opened.st_dev != entry_stat.st_dev or opened.st_ino != entry_stat.st_ino:
        os.close(descriptor)
        raise FigureExportError(f"export target changed while it was being opened: {name}")
    return descriptor, _EntryIdentity.from_stat(opened)


def _validate_export_directory(
    parent: _ParentDirectory,
    name: str,
    *,
    expected_plan: ExportPlan | None,
) -> _EntryIdentity:
    """Validate exporter provenance, the frozen spec, and every page checksum."""

    descriptor, identity = _open_directory_entry(parent, name)
    try:
        try:
            raw_manifest = _read_regular_bytes_at(descriptor, EXPORT_MANIFEST_NAME)
            document = json.loads(raw_manifest.decode("utf-8"))
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FigureExportError(
                f"refusing to overwrite unverified directory {parent.path / name}: "
                f"{EXPORT_MANIFEST_NAME} is missing or invalid"
            ) from exc
        if not isinstance(document, dict) or set(document) != {
            "manifestVersion",
            "producer",
            "format",
            "spec",
            "files",
        }:
            raise FigureExportError("export manifest has an invalid top-level structure")
        if document["manifestVersion"] != EXPORT_MANIFEST_VERSION:
            raise FigureExportError("export manifest version is not supported")
        if document["producer"] != EXPORT_PRODUCER:
            raise FigureExportError("export manifest producer marker does not match")
        figure_format = document["format"]
        if figure_format not in {FigureFormat.PNG.value, FigureFormat.SVG.value}:
            raise FigureExportError("export manifest format is not a directory format")
        _validate_manifest_spec_shape(document["spec"])
        if expected_plan is not None:
            if figure_format != expected_plan.format.value:
                raise FigureExportError("existing export format does not match this plan")
            if document["spec"] != _manifest_spec(expected_plan):
                raise FigureExportError("existing export spec does not match this plan")

        files = document["files"]
        if not isinstance(files, list) or not files:
            raise FigureExportError("export manifest files must be a non-empty list")
        expected_metadata = _metadata_from_manifest_spec(document["spec"], figure_format)
        file_names: list[str] = []
        for index, entry in enumerate(files):
            if not isinstance(entry, dict) or set(entry) != {
                "file",
                "unitId",
                "unitPosition",
                "pageIndex",
                "pageName",
                "size",
                "sha256",
            }:
                raise FigureExportError("export manifest contains an invalid file entry")
            metadata = {
                key: entry[key]
                for key in (
                    "file",
                    "unitId",
                    "unitPosition",
                    "pageIndex",
                    "pageName",
                )
            }
            if index >= len(expected_metadata) or metadata != expected_metadata[index]:
                raise FigureExportError("existing export page structure does not match this plan")
            filename = entry["file"]
            if (
                not isinstance(filename, str)
                or Path(filename).name != filename
                or filename in {"", ".", "..", EXPORT_MANIFEST_NAME}
                or Path(filename).suffix.lower() != f".{figure_format}"
            ):
                raise FigureExportError("export manifest contains an unsafe page filename")
            if filename in file_names:
                raise FigureExportError("export manifest contains duplicate page filenames")
            if isinstance(entry["size"], bool) or not isinstance(entry["size"], int) or entry["size"] < 0:
                raise FigureExportError("export manifest contains an invalid page size")
            digest = entry["sha256"]
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise FigureExportError("export manifest contains an invalid page checksum")
            file_names.append(filename)
        if len(files) != len(expected_metadata):
            raise FigureExportError("existing export page count does not match this plan")
        if set(os.listdir(descriptor)) != {EXPORT_MANIFEST_NAME, *file_names}:
            raise FigureExportError("export directory contents do not match its manifest")

        for entry in files:
            page_fd = os.open(
                entry["file"],
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                page_stat = os.fstat(page_fd)
                if not stat.S_ISREG(page_stat.st_mode) or page_stat.st_size != entry["size"]:
                    raise FigureExportError("export page type or size does not match its manifest")
                digest = hashlib.sha256()
                while chunk := os.read(page_fd, 1024 * 1024):
                    digest.update(chunk)
                if digest.hexdigest() != entry["sha256"]:
                    raise FigureExportError("export page checksum does not match its manifest")
            finally:
                os.close(page_fd)
    finally:
        os.close(descriptor)
    return identity


def _remove_directory_at(parent: _ParentDirectory, name: str) -> None:
    entry_stat = _entry_lstat(parent, name)
    if entry_stat is None:
        return
    if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(entry_stat.st_mode):
        raise FigureExportError(f"refusing to remove unexpected export entry: {name}")
    shutil.rmtree(name, dir_fd=parent.fd)


def _make_staging_directory(parent: _ParentDirectory, destination_name: str) -> tuple[str, int]:
    for _attempt in range(32):
        name = f".{destination_name}.tmp-{uuid.uuid4().hex}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent.fd)
        except FileExistsError:
            continue
        descriptor = os.open(name, _directory_open_flags(), dir_fd=parent.fd)
        return name, descriptor
    raise FigureExportError("could not allocate a unique export staging directory")


def _make_staging_file(
    parent: _ParentDirectory,
    destination_name: str,
    *,
    suffix: str,
) -> tuple[str, int]:
    for _attempt in range(32):
        name = f".{destination_name}.tmp-{uuid.uuid4().hex}{suffix}"
        try:
            descriptor = os.open(
                name,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent.fd,
            )
        except FileExistsError:
            continue
        return name, descriptor
    raise FigureExportError("could not allocate a unique export staging file")


def _atomic_directory_rename(
    staged: Path,
    destination: Path,
    *,
    exchange: bool,
    parent_fd: int | None = None,
) -> None:
    """Publish or exchange sibling entries using one pinned parent ``dir_fd``."""

    if staged.parent != destination.parent:
        raise FigureExportError("atomic publication requires sibling paths")
    close_parent = parent_fd is None
    directory_fd = (
        os.open(staged.parent, _directory_open_flags()) if parent_fd is None else parent_fd
    )
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(staged.name)
    destination_bytes = os.fsencode(destination.name)
    try:
        try:
            if sys.platform == "darwin":
                operation = libc.renameatx_np
                operation.argtypes = (
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_uint,
                )
                operation.restype = ctypes.c_int
                # <sys/stdio.h>: RENAME_SWAP=0x2, RENAME_EXCL=0x4.
                flags = 0x00000002 if exchange else 0x00000004
                result = operation(
                    directory_fd,
                    source_bytes,
                    directory_fd,
                    destination_bytes,
                    flags,
                )
            elif sys.platform.startswith("linux"):
                operation = libc.renameat2
                operation.argtypes = (
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_uint,
                )
                operation.restype = ctypes.c_int
                # <linux/fs.h>: RENAME_NOREPLACE=1, RENAME_EXCHANGE=2.
                flags = 2 if exchange else 1
                result = operation(
                    directory_fd,
                    source_bytes,
                    directory_fd,
                    destination_bytes,
                    flags,
                )
            else:
                raise FigureExportError(
                    "atomic directory publication is unsupported on this platform"
                )
        except AttributeError as exc:
            raise FigureExportError(
                "the operating system does not provide atomic directory publication"
            ) from exc
    finally:
        if close_parent:
            os.close(directory_fd)
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if not exchange and error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise DestinationExistsError(
            f"destination already exists: {destination}; pass overwrite=True to replace it"
        )
    if error_number == errno.ENOENT:
        raise FileNotFoundError(error_number, os.strerror(error_number), destination)
    raise OSError(error_number, os.strerror(error_number), destination)


def _publish_journal_name(destination_name: str) -> str:
    return f".{destination_name}.figure-export-journal.json"


def _publish_lock_name(destination_name: str) -> str:
    return f".{destination_name}.figure-export.lock"


@contextmanager
def _directory_publish_lock(
    parent: _ParentDirectory,
    destination_name: str,
) -> Iterator[None]:
    lock_name = _publish_lock_name(destination_name)
    descriptor = os.open(
        lock_name,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=parent.fd,
    )
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise FigureExportError(f"export publish lock is not a regular file: {lock_name}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        acquired = True
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _write_publish_journal(
    parent: _ParentDirectory,
    destination_name: str,
    *,
    staged_name: str,
    backup_name: str,
    state: str,
    old_identity: _EntryIdentity,
    new_identity: _EntryIdentity,
) -> None:
    document = {
        "journalVersion": 2,
        "destination": destination_name,
        "staged": staged_name,
        "backup": backup_name,
        "state": state,
        "oldIdentity": {
            "device": old_identity.device,
            "inode": old_identity.inode,
            "mode": old_identity.mode,
        },
        "newIdentity": {
            "device": new_identity.device,
            "inode": new_identity.inode,
            "mode": new_identity.mode,
        },
    }
    _atomic_write_bytes_at(
        parent.fd,
        _publish_journal_name(destination_name),
        _manifest_bytes(document),
    )
    _fsync_directory_fd(parent.fd)


def _validated_transaction_name(value: Any, *, prefix: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or Path(value).name != value
        or value in {"", ".", ".."}
        or not value.startswith(prefix)
    ):
        raise FigureExportError(f"export publish journal has an invalid {label} name")
    return value


def _read_publish_journal(
    parent: _ParentDirectory,
    destination_name: str,
) -> dict[str, Any] | None:
    journal_name = _publish_journal_name(destination_name)
    if _entry_lstat(parent, journal_name) is None:
        return None
    try:
        document = json.loads(_read_regular_bytes_at(parent.fd, journal_name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FigureExportError("export publish journal is not valid JSON") from exc
    if not isinstance(document, dict) or set(document) != {
        "journalVersion",
        "destination",
        "staged",
        "backup",
        "state",
        "oldIdentity",
        "newIdentity",
    }:
        raise FigureExportError("export publish journal has an invalid structure")
    if document["journalVersion"] != 2 or document["destination"] != destination_name:
        raise FigureExportError("export publish journal target does not match")
    state = document["state"]
    if state not in {"prepared", "old_moved", "new_moved"}:
        raise FigureExportError("export publish journal has an invalid state")
    staged_name = _validated_transaction_name(
        document["staged"],
        prefix=f".{destination_name}.tmp-",
        label="staged",
    )
    backup_name = _validated_transaction_name(
        document["backup"],
        prefix=f".{destination_name}.backup-",
        label="backup",
    )
    identities: dict[str, _EntryIdentity] = {}
    for document_key, result_key in (
        ("oldIdentity", "old_identity"),
        ("newIdentity", "new_identity"),
    ):
        raw_identity = document[document_key]
        if not isinstance(raw_identity, dict) or set(raw_identity) != {
            "device",
            "inode",
            "mode",
        }:
            raise FigureExportError("export publish journal has an invalid entry identity")
        values = (raw_identity["device"], raw_identity["inode"], raw_identity["mode"])
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise FigureExportError("export publish journal identity must contain integers")
        identities[result_key] = _EntryIdentity(*values)
    return {
        "state": state,
        "staged": staged_name,
        "backup": backup_name,
        **identities,
    }


def _clear_publish_journal(parent: _ParentDirectory, destination_name: str) -> None:
    try:
        os.unlink(_publish_journal_name(destination_name), dir_fd=parent.fd)
    except FileNotFoundError:
        pass
    _fsync_directory_fd(parent.fd)


def _require_transaction_directory(
    parent: _ParentDirectory,
    name: str,
) -> bool:
    entry_stat = _entry_lstat(parent, name)
    if entry_stat is None:
        return False
    if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(entry_stat.st_mode):
        raise FigureExportError(f"export transaction entry changed type: {name}")
    return True


def _recover_directory_publish_locked(
    parent: _ParentDirectory,
    destination: Path,
    *,
    directory_validator: Callable[[str], _EntryIdentity] | None = None,
) -> None:
    validate_directory = directory_validator or (
        lambda name: _validate_export_directory(parent, name, expected_plan=None)
    )
    journal = _read_publish_journal(parent, destination.name)
    if journal is None:
        return
    staged_name = journal["staged"]
    backup_name = journal["backup"]
    state = journal["state"]
    old_identity = journal["old_identity"]
    new_identity = journal["new_identity"]
    staged_exists = _require_transaction_directory(parent, staged_name)
    backup_exists = _require_transaction_directory(parent, backup_name)
    destination_exists = _require_transaction_directory(parent, destination.name)
    staged_stat = _entry_lstat(parent, staged_name)
    backup_stat = _entry_lstat(parent, backup_name)
    destination_stat = _entry_lstat(parent, destination.name)
    if backup_exists and (
        backup_stat is None or not _same_identity(backup_stat, old_identity)
    ):
        raise FigureExportError("export publish backup identity does not match its journal")
    if staged_exists and (
        staged_stat is None or not _same_identity(staged_stat, new_identity)
    ):
        raise FigureExportError("export publish staging identity does not match its journal")
    if destination_exists and (
        destination_stat is None
        or not (
            _same_identity(destination_stat, old_identity)
            or _same_identity(destination_stat, new_identity)
        )
    ):
        raise FigureExportError(
            "export destination identity does not match its publish journal; "
            "refusing destructive recovery"
        )

    if backup_exists and not destination_exists:
        _atomic_directory_rename(
            parent.path / backup_name,
            destination,
            exchange=False,
            parent_fd=parent.fd,
        )
        _fsync_directory_fd(parent.fd)
        if staged_exists:
            _remove_directory_at(parent, staged_name)
        _clear_publish_journal(parent, destination.name)
        return

    if backup_exists and destination_exists and not staged_exists:
        if destination_stat is None or not _same_identity(destination_stat, new_identity):
            raise FigureExportError("export publish journal destination is not the new output")
        try:
            validate_directory(destination.name)
        except Exception:
            _atomic_directory_rename(
                destination,
                parent.path / staged_name,
                exchange=False,
                parent_fd=parent.fd,
            )
            _atomic_directory_rename(
                parent.path / backup_name,
                destination,
                exchange=False,
                parent_fd=parent.fd,
            )
            _remove_directory_at(parent, staged_name)
        else:
            _remove_directory_at(parent, backup_name)
        _fsync_directory_fd(parent.fd)
        _clear_publish_journal(parent, destination.name)
        return

    if backup_exists and destination_exists and staged_exists:
        raise FigureExportError("ambiguous export publish journal; refusing destructive recovery")

    if not backup_exists and destination_exists:
        if state == "old_moved":
            raise FigureExportError("export publish backup is missing; recovery cannot be proven safe")
        expected_destination_identity = (
            old_identity if state == "prepared" else new_identity
        )
        if destination_stat is None or not _same_identity(
            destination_stat,
            expected_destination_identity,
        ):
            raise FigureExportError("export publish journal state and destination disagree")
        validate_directory(destination.name)
        if staged_exists:
            if state != "prepared":
                raise FigureExportError("export publish journal and staging state disagree")
            _remove_directory_at(parent, staged_name)
        _clear_publish_journal(parent, destination.name)
        return

    if not backup_exists and not destination_exists and staged_exists and state == "prepared":
        validate_directory(staged_name)
        _atomic_directory_rename(
            parent.path / staged_name,
            destination,
            exchange=False,
            parent_fd=parent.fd,
        )
        _fsync_directory_fd(parent.fd)
        _clear_publish_journal(parent, destination.name)
        return

    raise FigureExportError("export publish journal cannot be recovered without data loss")


def _recover_directory_publish_with_parent(
    parent: _ParentDirectory,
    destination: Path,
) -> None:
    with _directory_publish_lock(parent, destination.name):
        parent.verify()
        _recover_directory_publish_locked(parent, destination)
        parent.verify()


def _recover_directory_publish(destination: Path) -> None:
    """Recover or finish an interrupted CIFS directory replacement."""

    with _open_parent_directory(destination.parent) as parent:
        _recover_directory_publish_with_parent(parent, destination)


def _rollback_fallback_replace(
    parent: _ParentDirectory,
    destination: Path,
    staged_name: str,
    backup_name: str,
    *,
    old_identity: _EntryIdentity,
    new_identity: _EntryIdentity,
) -> None:
    backup_exists = _require_transaction_directory(parent, backup_name)
    destination_exists = _require_transaction_directory(parent, destination.name)
    staged_exists = _require_transaction_directory(parent, staged_name)
    backup_stat = _entry_lstat(parent, backup_name)
    destination_stat = _entry_lstat(parent, destination.name)
    staged_stat = _entry_lstat(parent, staged_name)
    if backup_exists and (
        backup_stat is None or not _same_identity(backup_stat, old_identity)
    ):
        raise FigureExportError("cannot roll back an export backup with changed identity")
    if staged_exists and (
        staged_stat is None or not _same_identity(staged_stat, new_identity)
    ):
        raise FigureExportError("cannot roll back an export staging directory with changed identity")
    if backup_exists:
        if destination_exists:
            if staged_exists:
                raise FigureExportError(
                    "cannot restore old export because an unexpected destination appeared"
                )
            if destination_stat is None or not _same_identity(
                destination_stat,
                new_identity,
            ):
                raise FigureExportError(
                    "cannot restore old export over a destination with changed identity"
                )
            _atomic_directory_rename(
                destination,
                parent.path / staged_name,
                exchange=False,
                parent_fd=parent.fd,
            )
        _atomic_directory_rename(
            parent.path / backup_name,
            destination,
            exchange=False,
            parent_fd=parent.fd,
        )
        _fsync_directory_fd(parent.fd)
        if _require_transaction_directory(parent, staged_name):
            _remove_directory_at(parent, staged_name)
    _clear_publish_journal(parent, destination.name)


def _fallback_replace_directory_locked(
    parent: _ParentDirectory,
    staged: Path,
    destination: Path,
    *,
    expected_identity: _EntryIdentity,
    directory_validator: Callable[[str], _EntryIdentity] | None = None,
) -> None:
    """Crash-recoverable CIFS fallback when atomic exchange is unavailable.

    CIFS mounts commonly implement ``RENAME_NOREPLACE`` but reject
    ``RENAME_EXCHANGE``.  This journaled two-rename transaction keeps a unique
    backup in the same parent, fsyncs every state transition, restores the old
    directory on ordinary exceptions, and lets a later request recover after a
    process crash.  On filesystems without exchange there is unavoidably a very
    short interval between the two renames during which the destination path is
    absent; the backup and journal make that interval recoverable, not invisible.
    """

    backup_name = f".{destination.name}.backup-{uuid.uuid4().hex}"
    backup_removed = False
    staged_stat = _entry_lstat(parent, staged.name)
    if staged_stat is None or not stat.S_ISDIR(staged_stat.st_mode):
        raise FigureExportError("export staging directory disappeared before publication")
    new_identity = _EntryIdentity.from_stat(staged_stat)
    validate_directory = directory_validator or (
        lambda name: _validate_export_directory(parent, name, expected_plan=None)
    )
    _write_publish_journal(
        parent,
        destination.name,
        staged_name=staged.name,
        backup_name=backup_name,
        state="prepared",
        old_identity=expected_identity,
        new_identity=new_identity,
    )
    try:
        _atomic_directory_rename(
            destination,
            parent.path / backup_name,
            exchange=False,
            parent_fd=parent.fd,
        )
        _fsync_directory_fd(parent.fd)
        backup_stat = _entry_lstat(parent, backup_name)
        if backup_stat is None or not _same_identity(backup_stat, expected_identity):
            raise FigureExportError("export destination changed during fallback publication")
        parent.verify()
        _write_publish_journal(
            parent,
            destination.name,
            staged_name=staged.name,
            backup_name=backup_name,
            state="old_moved",
            old_identity=expected_identity,
            new_identity=new_identity,
        )
        _atomic_directory_rename(
            staged,
            destination,
            exchange=False,
            parent_fd=parent.fd,
        )
        _fsync_directory_fd(parent.fd)
        parent.verify()
        validate_directory(destination.name)
        _write_publish_journal(
            parent,
            destination.name,
            staged_name=staged.name,
            backup_name=backup_name,
            state="new_moved",
            old_identity=expected_identity,
            new_identity=new_identity,
        )
        _remove_directory_at(parent, backup_name)
        _fsync_directory_fd(parent.fd)
        backup_removed = True
        _clear_publish_journal(parent, destination.name)
    except BaseException as exc:
        if backup_removed:
            # The new directory is durably committed and the old one no longer
            # exists.  A surviving journal is harmless and is cleared by the
            # next request; do not report a failure that cannot be rolled back.
            return
        try:
            _rollback_fallback_replace(
                parent,
                destination,
                staged.name,
                backup_name,
                old_identity=expected_identity,
                new_identity=new_identity,
            )
        except BaseException as rollback_exc:
            raise FigureExportError(
                "directory overwrite failed and automatic rollback did not complete; "
                "the publish journal was retained for recovery"
            ) from rollback_exc
        raise exc


def _inspect_pdf_destination(
    parent: _ParentDirectory,
    destination: Path,
    *,
    overwrite: bool,
) -> _EntryIdentity | None:
    entry_stat = _entry_lstat(parent, destination.name)
    if entry_stat is None:
        return None
    if not overwrite:
        raise DestinationExistsError(
            f"destination already exists: {destination}; pass overwrite=True to replace it"
        )
    if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISREG(entry_stat.st_mode):
        raise FigureExportError("PDF overwrite target must be a regular non-symlink file")
    descriptor = os.open(
        destination.name,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent.fd,
    )
    try:
        opened = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if opened.st_dev != entry_stat.st_dev or opened.st_ino != entry_stat.st_ino:
        raise FigureExportError("PDF overwrite target changed while it was being opened")
    return _EntryIdentity.from_stat(opened)


def _verified_pdf_backup_link(
    parent: _ParentDirectory,
    destination_name: str,
    backup_name: str,
    *,
    expected_identity: _EntryIdentity,
    backup_stat: os.stat_result,
) -> bool:
    """Verify a hard-link backup when CIFS reports path-scoped inode numbers.

    A ``nounix`` CIFS mount can return distinct synthetic ``st_ino`` values for
    two paths to the same hard-linked file.  In that case an inode-only check
    falsely reports that the stable destination changed.  Keep the normal
    identity check as the fast path; this fallback pins both non-symlink paths
    and requires matching type, device, mode, size, and contents.  The lab CIFS
    client reports unstable ``st_nlink`` values for both path stats and open
    file descriptors, so link counts are deliberately not used as evidence.
    """

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    destination_fd: int | None = None
    backup_fd: int | None = None
    try:
        # Capture both freshly created paths before opening them, then pin each
        # observation to its own nofollow descriptor below.  Do not inspect
        # ``st_nlink``: CIFS can report one or two nondeterministically.
        destination_path_stat = _entry_lstat(parent, destination_name)
        backup_path_stat = _entry_lstat(parent, backup_name)
        if destination_path_stat is None or backup_path_stat is None:
            return False
        destination_path_identity = _EntryIdentity.from_stat(destination_path_stat)
        backup_path_identity = _EntryIdentity.from_stat(backup_path_stat)
        if not _same_identity(destination_path_stat, expected_identity):
            return False
        if not _same_identity(backup_path_stat, _EntryIdentity.from_stat(backup_stat)):
            return False
        destination_fd = os.open(destination_name, flags, dir_fd=parent.fd)
        backup_fd = os.open(backup_name, flags, dir_fd=parent.fd)
        destination_fd_stat = os.fstat(destination_fd)
        backup_fd_stat = os.fstat(backup_fd)
        if not _same_identity(destination_fd_stat, destination_path_identity):
            return False
        if not _same_identity(backup_fd_stat, backup_path_identity):
            return False
        observed = (
            destination_path_stat,
            destination_fd_stat,
            backup_path_stat,
            backup_fd_stat,
        )
        if any(not stat.S_ISREG(result.st_mode) for result in observed):
            return False
        if any(result.st_dev != destination_fd_stat.st_dev for result in observed):
            return False
        if any(result.st_mode != destination_fd_stat.st_mode for result in observed):
            return False
        if any(result.st_size != destination_fd_stat.st_size for result in observed):
            return False
        while True:
            destination_chunk = os.read(destination_fd, 1024 * 1024)
            backup_chunk = os.read(backup_fd, 1024 * 1024)
            if destination_chunk != backup_chunk:
                return False
            if not destination_chunk:
                refreshed_destination = _entry_lstat(parent, destination_name)
                refreshed_backup = _entry_lstat(parent, backup_name)
                return (
                    refreshed_destination is not None
                    and refreshed_backup is not None
                    and _same_identity(
                        refreshed_destination,
                        destination_path_identity,
                    )
                    and _same_identity(refreshed_backup, backup_path_identity)
                )
    except OSError:
        return False
    finally:
        if backup_fd is not None:
            os.close(backup_fd)
        if destination_fd is not None:
            os.close(destination_fd)


def _commit_file(
    parent: _ParentDirectory,
    staged_name: str,
    destination: Path,
    *,
    overwrite: bool,
    expected_identity: _EntryIdentity | None,
) -> None:
    with _directory_publish_lock(parent, destination.name):
        try:
            _commit_file_locked(
                parent,
                staged_name,
                destination,
                overwrite=overwrite,
                expected_identity=expected_identity,
            )
        finally:
            try:
                os.unlink(staged_name, dir_fd=parent.fd)
            except FileNotFoundError:
                pass
            else:
                _fsync_directory_fd(parent.fd)


def _commit_file_locked(
    parent: _ParentDirectory,
    staged_name: str,
    destination: Path,
    *,
    overwrite: bool,
    expected_identity: _EntryIdentity | None,
) -> None:
    """Publish one staged PDF while its destination-name lock is held."""

    parent.verify()
    current = _entry_lstat(parent, destination.name)
    if expected_identity is None:
        try:
            os.link(
                staged_name,
                destination.name,
                src_dir_fd=parent.fd,
                dst_dir_fd=parent.fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise DestinationExistsError(
                f"destination already exists: {destination}; pass overwrite=True to replace it"
            ) from exc
        _fsync_directory_fd(parent.fd)
        try:
            parent.verify()
        except BaseException:
            destination_stat = _entry_lstat(parent, destination.name)
            staged_stat = _entry_lstat(parent, staged_name)
            if (
                destination_stat is not None
                and staged_stat is not None
                and destination_stat.st_dev == staged_stat.st_dev
                and destination_stat.st_ino == staged_stat.st_ino
            ):
                os.unlink(destination.name, dir_fd=parent.fd)
                _fsync_directory_fd(parent.fd)
            raise
        os.unlink(staged_name, dir_fd=parent.fd)
        _fsync_directory_fd(parent.fd)
        return
    if not overwrite or current is None or not _same_identity(current, expected_identity):
        raise FigureExportError("PDF overwrite target changed while pages were rendering")
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
        raise FigureExportError("PDF overwrite target must remain a regular non-symlink file")
    backup_name = f".{destination.name}.backup-{uuid.uuid4().hex}.pdf"
    os.link(
        destination.name,
        backup_name,
        src_dir_fd=parent.fd,
        dst_dir_fd=parent.fd,
        follow_symlinks=False,
    )
    backup_stat = _entry_lstat(parent, backup_name)
    backup_verified = backup_stat is not None and (
        _same_identity(backup_stat, expected_identity)
        or _verified_pdf_backup_link(
            parent,
            destination.name,
            backup_name,
            expected_identity=expected_identity,
            backup_stat=backup_stat,
        )
    )
    if not backup_verified:
        try:
            os.unlink(backup_name, dir_fd=parent.fd)
        except FileNotFoundError:
            pass
        raise FigureExportError("PDF overwrite target changed during publication")
    try:
        # The hard-link backup must be durable before replacing the only
        # stable destination name.  A failure here is handled as a pre-mutation
        # publish failure and removes both transaction entries.
        _fsync_directory_fd(parent.fd)
        os.replace(
            staged_name,
            destination.name,
            src_dir_fd=parent.fd,
            dst_dir_fd=parent.fd,
        )
        _fsync_directory_fd(parent.fd)
        parent.verify()
    except BaseException:
        try:
            _rollback_pdf_file_publish(
                parent,
                staged_name,
                destination.name,
                backup_name,
                expected_identity=expected_identity,
            )
        except BaseException as rollback_error:
            raise FigureExportError(
                "PDF publication failed and the original target could not be restored"
            ) from rollback_error
        raise
    _finalize_pdf_file_publish(parent, destination.name, backup_name)


def _rollback_pdf_file_publish(
    parent: _ParentDirectory,
    staged_name: str,
    destination_name: str,
    backup_name: str,
    *,
    expected_identity: _EntryIdentity,
) -> None:
    """Restore the pre-publish PDF state after a replace-phase failure."""

    staged_still_exists = _entry_lstat(parent, staged_name) is not None
    current = _entry_lstat(parent, destination_name)
    if (
        staged_still_exists
        and current is not None
        and _same_identity(current, expected_identity)
    ):
        # ``os.replace(staged, destination)`` failed before mutating either
        # name.  Renaming two hard links to the same inode is a POSIX no-op, so
        # explicitly remove the backup name instead of attempting a rollback.
        try:
            os.unlink(backup_name, dir_fd=parent.fd)
        except FileNotFoundError:
            pass
    else:
        os.replace(
            backup_name,
            destination_name,
            src_dir_fd=parent.fd,
            dst_dir_fd=parent.fd,
        )
        # When destination already refers to the old inode, POSIX permits the
        # hard-link rename above to be a no-op.  Remove the remaining backup
        # name explicitly after confirming that it still exists.
        if _entry_lstat(parent, backup_name) is not None:
            os.unlink(backup_name, dir_fd=parent.fd)
    _fsync_directory_fd(parent.fd)


def _finalize_pdf_file_publish(
    parent: _ParentDirectory,
    destination_name: str,
    backup_name: str,
) -> None:
    """Remove a PDF backup without reporting a committed export as failed.

    If backup removal fails before changing the directory, the old PDF can
    still be restored and the export fails closed.  Once the backup name is
    gone, the new destination is the only recoverable state.  A cleanup fsync
    failure must therefore not be surfaced as an export failure: the publish
    itself was already fsynced and reporting failure would falsely imply that
    the old destination remains visible.
    """

    try:
        os.unlink(backup_name, dir_fd=parent.fd)
    except FileNotFoundError:
        pass
    except OSError as cleanup_error:
        if _entry_lstat(parent, backup_name) is not None:
            try:
                os.replace(
                    backup_name,
                    destination_name,
                    src_dir_fd=parent.fd,
                    dst_dir_fd=parent.fd,
                )
                _fsync_directory_fd(parent.fd)
            except BaseException as rollback_error:
                raise FigureExportError(
                    "PDF backup cleanup failed and the original target could not be restored"
                ) from rollback_error
            raise FigureExportError(
                "PDF backup cleanup failed; publication was rolled back"
            ) from cleanup_error
        # The unlink completed before raising.  Treat the new PDF as committed.
    try:
        _fsync_directory_fd(parent.fd)
    except OSError:
        # Publication and its first directory fsync already succeeded.  With
        # no named backup left there is no safe state to roll back to.
        pass


def _commit_directory(
    parent: _ParentDirectory,
    staged: Path,
    destination: Path,
    *,
    overwrite: bool,
    expected_identity: _EntryIdentity | None,
    plan: ExportPlan,
) -> None:
    with _directory_publish_lock(parent, destination.name):
        parent.verify()
        _recover_directory_publish_locked(parent, destination)
        if expected_identity is None:
            try:
                _atomic_directory_rename(
                    staged,
                    destination,
                    exchange=False,
                    parent_fd=parent.fd,
                )
                _fsync_directory_fd(parent.fd)
                parent.verify()
            except BaseException:
                if _entry_lstat(parent, destination.name) is not None and _entry_lstat(parent, staged.name) is None:
                    try:
                        _atomic_directory_rename(
                            destination,
                            staged,
                            exchange=False,
                            parent_fd=parent.fd,
                        )
                        _fsync_directory_fd(parent.fd)
                    except BaseException:
                        pass
                raise
            return

        if not overwrite:
            raise DestinationExistsError(
                f"destination already exists: {destination}; pass overwrite=True to replace it"
            )
        current_identity = _validate_export_directory(
            parent,
            destination.name,
            expected_plan=None,
        )
        if current_identity != expected_identity:
            raise FigureExportError("export destination changed while pages were rendering")
        try:
            _atomic_directory_rename(
                staged,
                destination,
                exchange=True,
                parent_fd=parent.fd,
            )
        except OSError as exc:
            unsupported = {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}
            if hasattr(errno, "ENOTSUP"):
                unsupported.add(errno.ENOTSUP)
            if exc.errno not in unsupported:
                raise
            _fallback_replace_directory_locked(
                parent,
                staged,
                destination,
                expected_identity=expected_identity,
            )
            return
        old_stat = _entry_lstat(parent, staged.name)
        try:
            if old_stat is None or not _same_identity(old_stat, expected_identity):
                raise FigureExportError("export destination changed during atomic exchange")
            _fsync_directory_fd(parent.fd)
            parent.verify()
        except BaseException:
            _atomic_directory_rename(
                staged,
                destination,
                exchange=True,
                parent_fd=parent.fd,
            )
            _fsync_directory_fd(parent.fd)
            raise
        _remove_directory_at(parent, staged.name)
        _fsync_directory_fd(parent.fd)


def _pdf_utf16_literal(value: str) -> bytes:
    """Return a PDF literal string containing UTF-16BE text with a BOM."""

    escaped = bytearray()
    for byte in b"\xfe\xff" + value.encode("utf-16-be"):
        replacement = {
            ord("\\"): b"\\\\",
            ord("("): b"\\(",
            ord(")"): b"\\)",
            0x08: b"\\b",
            0x09: b"\\t",
            0x0A: b"\\n",
            0x0C: b"\\f",
            0x0D: b"\\r",
        }.get(byte)
        escaped.extend(replacement if replacement is not None else bytes((byte,)))
    return b"(" + bytes(escaped) + b")"


def _write_pdf_object(
    stream: BinaryIO,
    offsets: list[int],
    object_number: int,
    contents: bytes,
) -> None:
    offsets[object_number] = stream.tell()
    stream.write(f"{object_number} 0 obj\n".encode("ascii"))
    stream.write(contents)
    if not contents.endswith(b"\n"):
        stream.write(b"\n")
    stream.write(b"endobj\n")


def _lossless_rgb_bytes(image: Image.Image) -> bytes:
    """Return a Flate-compressed, byte-exact DeviceRGB raster."""

    return zlib.compress(image.tobytes(), level=6)


def write_streaming_pdf(
    stream: BinaryIO,
    page_count: int,
    image_provider: Callable[[int], Image.Image],
    *,
    title: str,
    resolution: float = 150.0,
    export_metadata: Mapping[str, Any] | None = None,
) -> None:
    """Write a raster PDF while retaining only the current page image.

    ``image_provider`` is called with each zero-based page index.  The writer
    owns the returned image and closes it after its lossless RGB stream has been
    written, including when encoding fails.  The page count must be known up
    front so the single authoritative PDF page tree can be emitted before any
    raster page is requested.

    Pillow 10.x's incremental ``append=True`` PDF path corrupts the trailer
    chain after four appends (``PdfFormatError: trailer loop found``).  The
    project's supported Pillow range includes that release, so construct the
    small PDF object graph directly. Every rendered RGB page is Flate
    compressed, preserving exact preview pixels.
    """

    if not math.isfinite(resolution) or resolution <= 0:
        raise FigureExportValidationError("PDF resolution must be positive and finite")
    if (
        isinstance(page_count, bool)
        or not isinstance(page_count, int)
        or page_count <= 0
    ):
        raise FigureExportValidationError("PDF export requires at least one page")
    if not callable(image_provider):
        raise FigureExportValidationError("PDF image provider must be callable")
    if not isinstance(title, str):
        raise FigureExportValidationError("PDF title must be a string")
    if export_metadata is not None and not isinstance(export_metadata, Mapping):
        raise FigureExportValidationError("PDF export metadata must be a mapping")
    metadata_document = (
        {}
        if export_metadata is None
        else _manifest_json_value(export_metadata, label="PDF export metadata")
    )
    metadata_bytes = json.dumps(
        metadata_document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    metadata_digest = hashlib.sha256(metadata_bytes).hexdigest()

    # 1: catalog, 2: pages tree, then page/content/image triples and an Info obj.
    info_object = 3 + 3 * page_count
    object_count = info_object
    offsets = [0] * (object_count + 1)
    page_objects = [3 + 3 * index for index in range(page_count)]

    stream.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    _write_pdf_object(
        stream,
        offsets,
        1,
        b"<< /Type /Catalog /Pages 2 0 R >>",
    )
    kids = b" ".join(f"{number} 0 R".encode("ascii") for number in page_objects)
    _write_pdf_object(
        stream,
        offsets,
        2,
        b"<< /Type /Pages /Count "
        + str(page_count).encode("ascii")
        + b" /Kids [ "
        + kids
        + b" ] >>",
    )

    for index in range(page_count):
        page_object = page_objects[index]
        contents_object = page_object + 1
        image_object = page_object + 2
        rendered = image_provider(index)
        rgb_image: Image.Image | None = None
        try:
            rgb_image = rendered.convert("RGB")
            width, height = rgb_image.size
            compressed_rgb = _lossless_rgb_bytes(rgb_image)
        finally:
            if rgb_image is not None and rgb_image is not rendered:
                rgb_image.close()
            rendered.close()

        width_points = width * 72.0 / resolution
        height_points = height * 72.0 / resolution
        width_text = f"{width_points:.6f}".rstrip("0").rstrip(".")
        height_text = f"{height_points:.6f}".rstrip("0").rstrip(".")
        media_box = f"0 0 {width_text} {height_text}".encode("ascii")
        _write_pdf_object(
            stream,
            offsets,
            page_object,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [ "
            + media_box
            + b" ] /Resources << /ProcSet [ /PDF /ImageC ] "
            + b"/XObject << /image "
            + f"{image_object} 0 R".encode("ascii")
            + b" >> >> /Contents "
            + f"{contents_object} 0 R".encode("ascii")
            + b" >>",
        )
        page_commands = (
            f"q {width_text} 0 0 {height_text} 0 0 cm /image Do Q\n"
        ).encode("ascii")
        _write_pdf_object(
            stream,
            offsets,
            contents_object,
            b"<< /Length "
            + str(len(page_commands)).encode("ascii")
            + b" >>\nstream\n"
            + page_commands
            + b"endstream",
        )
        _write_pdf_object(
            stream,
            offsets,
            image_object,
            b"<< /Type /XObject /Subtype /Image /Width "
            + str(width).encode("ascii")
            + b" /Height "
            + str(height).encode("ascii")
            + b" /ColorSpace /DeviceRGB /BitsPerComponent 8 "
            + b"/Filter /FlateDecode /Length "
            + str(len(compressed_rgb)).encode("ascii")
            + b" >>\nstream\n"
            + compressed_rgb
            + b"\nendstream",
        )
        del compressed_rgb

    _write_pdf_object(
        stream,
        offsets,
        info_object,
        b"<< /Title "
        + _pdf_utf16_literal(title)
        + b" /RFMExportManifest "
        + _pdf_utf16_literal(metadata_bytes.decode("utf-8"))
        + b" /RFMExportManifestSHA256 "
        + _pdf_utf16_literal(metadata_digest)
        + b" >>",
    )
    xref_offset = stream.tell()
    stream.write(f"xref\n0 {object_count + 1}\n".encode("ascii"))
    stream.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        if not 0 < offset <= 9_999_999_999:
            raise FigureExportError("PDF object offset exceeds the PDF 1.4 xref limit")
        stream.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    stream.write(
        b"trailer\n<< /Size "
        + str(object_count + 1).encode("ascii")
        + b" /Root 1 0 R /Info "
        + f"{info_object} 0 R".encode("ascii")
        + b" >>\nstartxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )
    stream.flush()


def _write_pdf_document(
    stream: BinaryIO,
    generated_pages: Sequence[GeneratedPage],
    *,
    renderer: PillowFigureRenderer,
    data_provider: PlotDataProvider | None,
    title: str,
    resolution: float = 150.0,
) -> None:
    """Render a shared export plan through :func:`write_streaming_pdf`."""

    def image_provider(index: int) -> Image.Image:
        generated = generated_pages[index]
        return renderer.render_page(
            generated.unit_id,
            generated.page,
            data_provider=data_provider,
        )

    write_streaming_pdf(
        stream,
        len(generated_pages),
        image_provider,
        title=title,
        resolution=resolution,
    )


def export_figures(
    plan: ExportPlan,
    *,
    data_provider: PlotDataProvider | None = None,
    renderer: PillowFigureRenderer | None = None,
    overwrite: bool = False,
) -> ExportResult:
    """Render and atomically commit every concrete page in ``plan``.

    ``overwrite`` defaults to ``False``.  PDF overwrite accepts only a regular,
    non-symlink ``.pdf`` file.  PNG/SVG overwrite is deliberately narrower: the
    existing directory must have this exporter's versioned ``manifest.json``,
    contain a structurally valid recorded spec and no unlisted entries, and pass
    every recorded page checksum.  A new recipe may replace a previous verified
    export, but an export root which also contains raw sessions can never replace
    a same-named source directory.

    Rendering happens entirely in a sibling staging entry.  The parent is held
    open and checked by device/inode before publication, while publication uses
    relative operations on that same directory descriptor.
    """

    if not isinstance(plan, ExportPlan):
        raise FigureExportValidationError("plan must be an ExportPlan")
    if not isinstance(overwrite, bool):
        raise FigureExportValidationError("overwrite must be a boolean")
    active_renderer = renderer or PillowFigureRenderer()
    destination = plan.destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    generated_pages = tuple(iter_generated_pages(plan))
    with _open_parent_directory(destination.parent) as parent:
        if plan.format is FigureFormat.PDF:
            expected_identity = _inspect_pdf_destination(
                parent,
                destination,
                overwrite=overwrite,
            )
            staged_name, staged_fd = _make_staging_file(
                parent,
                destination.name,
                suffix=".pdf",
            )
            page_count = len(generated_pages)
            try:
                with os.fdopen(os.dup(staged_fd), "wb") as stream:
                    _write_pdf_document(
                        stream,
                        generated_pages,
                        renderer=active_renderer,
                        data_provider=data_provider,
                        title=destination.stem,
                    )
                os.fsync(staged_fd)
                _commit_file(
                    parent,
                    staged_name,
                    destination,
                    overwrite=overwrite,
                    expected_identity=expected_identity,
                )
            finally:
                os.close(staged_fd)
                try:
                    os.unlink(staged_name, dir_fd=parent.fd)
                except FileNotFoundError:
                    pass
            return ExportResult(plan.format, destination, (destination,), page_count)

        _recover_directory_publish_with_parent(parent, destination)
        existing_stat = _entry_lstat(parent, destination.name)
        if existing_stat is not None and not overwrite:
            raise DestinationExistsError(
                f"destination already exists: {destination}; pass overwrite=True to replace it"
            )
        expected_identity = (
            None
            if existing_stat is None
            else _validate_export_directory(
                parent,
                destination.name,
                expected_plan=None,
            )
        )

        staged_name, staged_fd = _make_staging_directory(parent, destination.name)
        staged = parent.path / staged_name
        filenames: list[str] = []
        rendered_integrity: list[tuple[int, str]] = []
        try:
            for item in generated_pages:
                image = active_renderer.render_page(
                    item.unit_id,
                    item.page,
                    data_provider=data_provider,
                )
                try:
                    contents = (
                        _png_bytes(image)
                        if plan.format is FigureFormat.PNG
                        else _svg_bytes(image)
                    )
                finally:
                    image.close()
                filename = _page_filename(item, plan.format.value)
                _atomic_write_bytes_at(staged_fd, filename, contents)
                filenames.append(filename)
                rendered_integrity.append(
                    (len(contents), hashlib.sha256(contents).hexdigest())
                )
                del contents
            manifest = _manifest_document(plan, generated_pages, rendered_integrity)
            _atomic_write_bytes_at(
                staged_fd,
                EXPORT_MANIFEST_NAME,
                _manifest_bytes(manifest),
            )
            _fsync_directory_fd(staged_fd)
            _validate_export_directory(parent, staged_name, expected_plan=plan)
            _commit_directory(
                parent,
                staged,
                destination,
                overwrite=overwrite,
                expected_identity=expected_identity,
                plan=plan,
            )
        finally:
            os.close(staged_fd)
            if _entry_lstat(parent, staged_name) is not None:
                _remove_directory_at(parent, staged_name)
        files = tuple(destination / filename for filename in filenames)
        return ExportResult(plan.format, destination, files, len(generated_pages))


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "SVG_RENDERING_CONTRACT",
    "DestinationExistsError",
    "ExportPage",
    "ExportPlan",
    "ExportResult",
    "FigureExportError",
    "FigureExportValidationError",
    "FigureFormat",
    "GeneratedPage",
    "PLOT_KIND_REGISTRY",
    "PillowFigureRenderer",
    "PlotDataProvider",
    "PlotKind",
    "PlotKindDefinition",
    "PlotSpec",
    "automatic_grid",
    "export_figures",
    "iter_generated_pages",
    "render_live_preview",
]
