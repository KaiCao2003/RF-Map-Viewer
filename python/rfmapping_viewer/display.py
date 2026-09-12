"""Spatial, temporal, color, and raster display calculations."""

from __future__ import annotations

from bisect import bisect_left
import math
from functools import lru_cache
from typing import Callable, Sequence
import numpy as np

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rfmapping_viewer.rf_model import RFMappingData

from rfmapping_viewer.constants import (
    AxisGroup,
    INNER_BLANK_ROWS,
    SINGLETON_Y_REFERENCE_COLUMNS,
    SINGLETON_Y_REFERENCE_ROWS,
    VALUE_MODE_COUNT,
    VALUE_MODE_RATE,
)


class PreparedSpatialMatrix(list):
    """A display-grouped matrix that must not be reduced or smoothed again."""

    def __init__(
        self,
        values: list[list[float | None]],
        x_groups: list[AxisGroup],
        y_groups: list[AxisGroup],
    ) -> None:
        super().__init__(values)
        self.x_groups = x_groups
        self.y_groups = y_groups


def timeline_scroll_progress(first: float, last: float) -> float | None:
    """Convert a Tk canvas yview into viewport-independent scroll progress.

    Tk reports fractions of the full scroll region.  The first fraction at the
    bottom therefore depends on how much of that region the current viewport
    can show.  Pairing stores progress through the *scrollable travel* instead.
    ``None`` means the canvas is currently not scrollable, so callers should
    preserve the last meaningful progress for a later draw.
    """

    first = float(first)
    last = float(last)
    visible_span = max(0.0, min(1.0, last - first))
    max_first = max(0.0, 1.0 - visible_span)
    if max_first <= 1e-9:
        return None
    progress = max(0.0, min(1.0, first / max_first))
    if progress <= 1e-9:
        return 0.0
    if progress >= 1.0 - 1e-9:
        return 1.0
    return progress


def timeline_scroll_offset(progress: float, first: float, last: float) -> float | None:
    """Map normalized scroll progress to a target canvas yview offset."""

    visible_span = max(0.0, min(1.0, float(last) - float(first)))
    max_first = max(0.0, 1.0 - visible_span)
    if max_first <= 1e-9:
        return None
    return max(0.0, min(1.0, float(progress))) * max_first


def timeline_position_fraction(
    time_ms: float,
    axis_start_ms: float,
    axis_end_ms: float,
) -> float:
    """Map physical time onto the timeline axis, clamped to its visible span."""

    values = (float(time_ms), float(axis_start_ms), float(axis_end_ms))
    if not all(math.isfinite(value) for value in values):
        return 0.0
    span = values[2] - values[1]
    if span <= 0.0:
        return 0.0
    return max(0.0, min(1.0, (values[0] - values[1]) / span))


def timeline_chart_points(
    values: Sequence[float],
    center_times_ms: Sequence[float],
    axis_range_ms: tuple[float, float],
    high: float,
    chart_rect: tuple[float, float, float, float],
) -> list[float]:
    """Return a Tk polyline whose x coordinates use real bin-center times."""

    chart_x, chart_y, chart_width, chart_height = map(float, chart_rect)
    safe_high = float(high)
    if not math.isfinite(safe_high) or safe_high <= 0.0:
        safe_high = 1.0
    points: list[float] = []
    for value, center_ms in zip(values, center_times_ms):
        numeric = float(value)
        if not math.isfinite(numeric):
            numeric = 0.0
        response_fraction = max(0.0, min(1.0, numeric / safe_high))
        points.extend(
            (
                chart_x
                + chart_width
                * timeline_position_fraction(center_ms, axis_range_ms[0], axis_range_ms[1]),
                chart_y + chart_height - chart_height * response_fraction,
            )
        )
    return points


def timeline_response_high(values: Sequence[float]) -> float:
    """Return one trace's non-negative y-axis maximum."""

    high = 0.0
    for value in values:
        numeric = float(value)
        if math.isfinite(numeric):
            high = max(high, numeric)
    return max(high, 1.0)


def timeline_bin_index(time_ms: float, end_bounds_ms: Sequence[float]) -> int | None:
    """Return the half-open physical-time bin containing ``time_ms``."""

    if not end_bounds_ms:
        return None
    for index, end_ms in enumerate(end_bounds_ms):
        if float(time_ms) < float(end_ms):
            return index
    return len(end_bounds_ms) - 1


def clone_matrix(matrix: list[list[float]]) -> list[list[float]]:
    return [row[:] for row in matrix]


def display_matrix(
    matrix: list[list[float | None]],
    data: RFMappingData,
    flip_y: bool,
) -> list[list[float | None]]:
    return [matrix[y_idx][:] for y_idx in data.display_y_indices(flip_y)]


def axis_groups_for_target(source_count: int, target_count: int) -> list[AxisGroup]:
    target = max(1, min(source_count, int(target_count)))
    groups: list[AxisGroup] = []
    for group_idx in range(target):
        start = group_idx * source_count // target
        end = ((group_idx + 1) * source_count // target) - 1
        groups.append((start, max(start, end)))
    return groups


def physical_time_groups(
    edges_ms: Sequence[float],
    target_duration_ms: float,
) -> list[AxisGroup]:
    """Group native bins by measured timestamps around a target duration.

    Starting at each native edge, the next boundary is the available edge
    nearest ``target_duration_ms`` later. Exact ties choose the earlier edge so
    the requested target is not silently exceeded. The final residual interval
    is retained. Uniform edges with an integer-bin target therefore reproduce
    fixed-count grouping exactly.
    """

    edges = tuple(float(edge) for edge in edges_ms)
    if len(edges) < 2:
        return []
    source_bin_count = len(edges) - 1
    target = float(target_duration_ms)
    if not math.isfinite(target) or target <= 0.0:
        target = max(edges[1] - edges[0], math.ulp(0.0))

    groups: list[AxisGroup] = []
    start = 0
    while start < source_bin_count:
        target_edge = edges[start] + target
        upper = bisect_left(
            edges,
            target_edge,
            lo=start + 1,
            hi=source_bin_count + 1,
        )
        upper = min(source_bin_count, upper)
        lower = max(start + 1, upper - 1)
        end_exclusive = (
            lower
            if abs(edges[lower] - target_edge) <= abs(edges[upper] - target_edge)
            else upper
        )
        groups.append((start, end_exclusive - 1))
        start = end_exclusive
    return groups


def display_group_index_for_source_bin(groups: list[AxisGroup], source_bin: int) -> int:
    """Return the display group containing a source bin, clamped at the ends."""
    if not groups:
        return 0
    for index, (start, end) in enumerate(groups):
        if start <= source_bin <= end:
            return index
    return 0 if source_bin < groups[0][0] else len(groups) - 1


def x_groups_for_count(n_x: int, group_size: int) -> list[AxisGroup]:
    group_size = max(1, min(n_x, int(group_size)))
    return [(start, min(start + group_size - 1, n_x - 1)) for start in range(0, n_x, group_size)]


def reduce_x_matrix(
    matrix: list[list[float | None]],
    x_groups: list[AxisGroup],
) -> list[list[float | None]]:
    reduced: list[list[float | None]] = []
    for row in matrix:
        out_row: list[float | None] = []
        for start, end in x_groups:
            values = [
                float(row[x_idx])
                for x_idx in range(start, end + 1)
                if row[x_idx] is not None and math.isfinite(float(row[x_idx]))
            ]
            out_row.append(sum(values) / len(values) if values else None)
        reduced.append(out_row)
    return reduced


def reduce_matrix_xy(
    matrix: list[list[float | None]],
    y_groups: list[AxisGroup],
    x_groups: list[AxisGroup],
) -> list[list[float | None]]:
    reduced: list[list[float | None]] = []
    for y_start, y_end in y_groups:
        out_row: list[float | None] = []
        for x_start, x_end in x_groups:
            values: list[float] = []
            for y_idx in range(y_start, y_end + 1):
                row = matrix[y_idx]
                for x_idx in range(x_start, x_end + 1):
                    value = row[x_idx]
                    if value is not None and math.isfinite(float(value)):
                        values.append(float(value))
            out_row.append(sum(values) / len(values) if values else None)
        reduced.append(out_row)
    return reduced


def smooth_matrix(
    matrix: list[list[float | None]],
    radius: int,
) -> list[list[float | None]]:
    radius = max(0, int(radius))
    if radius <= 0:
        return [row[:] for row in matrix]
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    current = [row[:] for row in matrix]
    for _ in range(radius):
        out: list[list[float | None]] = []
        for y in range(rows):
            out_row: list[float | None] = []
            for x in range(cols):
                center = current[y][x]
                if center is None or not math.isfinite(float(center)):
                    out_row.append(None)
                    continue
                total = 0.0
                weight_total = 0.0
                for dy in (-1, 0, 1):
                    yy = y + dy
                    if yy < 0 or yy >= rows:
                        continue
                    for dx in (-1, 0, 1):
                        xx = x + dx
                        if xx < 0 or xx >= cols:
                            continue
                        value = current[yy][xx]
                        if value is None or not math.isfinite(float(value)):
                            continue
                        weight = 4.0 if dx == 0 and dy == 0 else (2.0 if dx == 0 or dy == 0 else 1.0)
                        total += float(value) * weight
                        weight_total += weight
                out_row.append(total / weight_total if weight_total else None)
            out.append(out_row)
        current = out
    return current


def _smooth_matrix_array(values: np.ndarray, radius: int) -> np.ndarray:
    """Apply ``smooth_matrix`` semantics to one matrix or a frame stack.

    Only the final two dimensions are spatial. Missing centers remain missing,
    while finite neighbors contribute with the same 4/2/1 center/edge/corner
    weights as the scalar implementation.
    """

    current = np.asarray(values, dtype=np.float64)
    if current.ndim < 2:
        raise ValueError("matrix array must have at least two dimensions")
    current = current.copy()
    for _iteration in range(max(0, int(radius))):
        finite_centers = np.isfinite(current)
        padded_values = np.pad(
            np.where(finite_centers, current, 0.0),
            [(0, 0)] * (current.ndim - 2) + [(1, 1), (1, 1)],
            mode="constant",
        )
        padded_finite = np.pad(
            finite_centers,
            [(0, 0)] * (current.ndim - 2) + [(1, 1), (1, 1)],
            mode="constant",
            constant_values=False,
        )
        rows, columns = current.shape[-2:]
        total = np.zeros_like(current, dtype=np.float64)
        weight_total = np.zeros_like(current, dtype=np.float64)
        for dy in range(3):
            for dx in range(3):
                weight = 4.0 if dx == 1 and dy == 1 else (2.0 if dx == 1 or dy == 1 else 1.0)
                source = padded_values[..., dy : dy + rows, dx : dx + columns]
                source_finite = padded_finite[
                    ..., dy : dy + rows, dx : dx + columns
                ]
                total += source * weight
                weight_total += source_finite * weight
        current = np.divide(
            total,
            weight_total,
            out=np.full_like(total, np.nan),
            where=finite_centers & (weight_total > 0.0),
        )
    return current


def _rectangular_group_sums(
    values: np.ndarray,
    y_groups: Sequence[AxisGroup],
    x_groups: Sequence[AxisGroup],
) -> np.ndarray:
    """Sum Cartesian products of inclusive rectangular groups in one batch."""

    source = np.asarray(values, dtype=np.float64)
    if source.ndim < 2:
        raise ValueError("spatial values must have at least two dimensions")
    n_y, n_x = source.shape[-2:]
    if not y_groups or not x_groups:
        return np.empty(
            source.shape[:-2] + (len(y_groups), len(x_groups)),
            dtype=np.float64,
        )

    y_starts = np.asarray(
        [max(0, min(n_y - 1, min(group))) for group in y_groups],
        dtype=np.intp,
    )
    y_stops = np.asarray(
        [max(0, min(n_y - 1, max(group))) + 1 for group in y_groups],
        dtype=np.intp,
    )
    x_starts = np.asarray(
        [max(0, min(n_x - 1, min(group))) for group in x_groups],
        dtype=np.intp,
    )
    x_stops = np.asarray(
        [max(0, min(n_x - 1, max(group))) + 1 for group in x_groups],
        dtype=np.intp,
    )
    integral = np.pad(
        source,
        [(0, 0)] * (source.ndim - 2) + [(1, 0), (1, 0)],
        mode="constant",
    )
    integral = integral.cumsum(axis=-2).cumsum(axis=-1)
    return (
        integral[..., y_stops[:, None], x_stops[None, :]]
        - integral[..., y_starts[:, None], x_stops[None, :]]
        - integral[..., y_stops[:, None], x_starts[None, :]]
        + integral[..., y_starts[:, None], x_starts[None, :]]
    )


def _nullable_array_list(values: np.ndarray) -> list:
    """Convert finite array values to Python floats and NaN to ``None``."""

    source = np.asarray(values, dtype=np.float64)
    result = source.astype(object)
    result[~np.isfinite(source)] = None
    return result.tolist()


def finite_min_max(matrix: list[list[float | None]]) -> tuple[float, float]:
    values = [
        float(value)
        for row in matrix
        for value in row
        if value is not None and math.isfinite(float(value))
    ]
    if not values:
        return 0.0, 1.0
    low = min(values)
    high = max(values)
    if abs(high - low) < 1e-12:
        high = low + 1.0
    return low, high


def nonnegative_response_range(
    matrix: Sequence[Sequence[float | None]],
) -> tuple[float, float]:
    """Use a truthful zero baseline for non-negative response estimands."""

    peak = max(
        (
            max(0.0, float(value))
            for row in matrix
            for value in row
            if value is not None and math.isfinite(float(value))
        ),
        default=0.0,
    )
    return 0.0, peak


def subtract_response_matrices(
    first: list[list[float | None]],
    second: list[list[float | None]],
) -> list[list[float | None]]:
    """Subtract normalized RF windows; negative and missing cells display as NaN."""
    difference = np.asarray(first, dtype=np.float64) - np.asarray(second, dtype=np.float64)
    return _nullable_array_list(np.where(difference < 0.0, np.nan, difference))


def palette_response_range(
    matrix: list[list[float | None]],
    palette: str,
) -> tuple[float, float]:
    """Return the response range used by each display palette.

    Gray retains the previous Python viewer's contrast-stretched range, while
    color palettes keep the explicit zero baseline.
    """

    if palette == "Gray":
        return finite_min_max(matrix)
    return nonnegative_response_range(matrix)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def hex_color(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def rgb_response_color(
    response: float | None,
    delay: float | None,
    entropy: float | None,
    max_response: float,
    delay_start: float,
    delay_span: float,
) -> tuple[int, int, int] | None:
    if response is None or not math.isfinite(response):
        return None
    if response <= 0.0:
        return (0, 0, 0)
    delay_fraction = 0.0 if delay is None else clamp((delay - delay_start) / delay_span)
    return (
        int(round(clamp(response / max_response) * 255)),
        int(round(delay_fraction * 255)),
        int(round(clamp(entropy or 0.0) * 255)),
    )


def shade_hex(color: str, factor: float) -> str:
    color = color.lstrip("#")
    r = clamp(int(color[0:2], 16) * factor, 0, 255)
    g = clamp(int(color[2:4], 16) * factor, 0, 255)
    b = clamp(int(color[4:6], 16) * factor, 0, 255)
    return hex_color((int(round(r)), int(round(g)), int(round(b))))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        int(round(lerp(a[0], b[0], t))),
        int(round(lerp(a[1], b[1], t))),
        int(round(lerp(a[2], b[2], t))),
    )


def palette_color(value: float | None, low: float, high: float, palette: str) -> str:
    if value is None or not math.isfinite(float(value)):
        return "#e6e8eb"
    t = clamp((float(value) - low) / (high - low if high != low else 1.0))
    if palette == "Gray":
        shade = int(round(18 + t * 232))
        return hex_color((shade, shade, shade))
    if palette == "Inferno":
        return gradient_color(
            t,
            (
                (0.0, (22, 11, 57)),
                (0.25, (90, 18, 110)),
                (0.50, (190, 54, 85)),
                (0.75, (249, 140, 10)),
                (1.0, (252, 255, 164)),
            ),
        )
    return gradient_color(
        t,
        (
            (0.0, (68, 1, 84)),
            (0.25, (59, 82, 139)),
            (0.50, (33, 145, 140)),
            (0.75, (94, 201, 98)),
            (1.0, (253, 231, 37)),
        ),
    )


def delay_color(value: float | None, low: float = 0.0, high: float = 100.0) -> str:
    if value is None:
        return "#eceff2"
    t = clamp((float(value) - low) / (high - low if high != low else 1.0))
    return gradient_color(
        t,
        (
            (0.0, (47, 88, 167)),
            (0.35, (44, 171, 184)),
            (0.68, (246, 204, 89)),
            (1.0, (203, 71, 45)),
        ),
    )


def waveform_color(value: float | None, amplitude_limit_uv: float) -> str:
    """Return the notebook's red-white-blue diverging waveform color."""

    if value is None or not math.isfinite(float(value)):
        return "#e6e8eb"
    limit = max(float(amplitude_limit_uv), 1e-12)
    t = clamp((float(value) + limit) / (2.0 * limit))
    return gradient_color(
        t,
        (
            (0.0, (5, 48, 97)),
            (0.25, (67, 147, 195)),
            (0.50, (247, 247, 247)),
            (0.75, (214, 96, 77)),
            (1.0, (103, 0, 31)),
        ),
    )


def gradient_color(t: float, stops: tuple[tuple[float, tuple[int, int, int]], ...]) -> str:
    t = clamp(t)
    for i in range(len(stops) - 1):
        left_t, left_c = stops[i]
        right_t, right_c = stops[i + 1]
        if left_t <= t <= right_t:
            local = (t - left_t) / (right_t - left_t if right_t != left_t else 1.0)
            return hex_color(lerp_color(left_c, right_c, local))
    return hex_color(stops[-1][1])


def text_color_for(fill: str) -> str:
    fill = fill.lstrip("#")
    r = int(fill[0:2], 16)
    g = int(fill[2:4], 16)
    b = int(fill[4:6], 16)
    luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return "#0f172a" if luminance > 0.58 else "#f8fafc"


def point_in_polygon(x: float, y: float, points: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    j = len(points) - 1
    for i in range(len(points)):
        xi, yi = points[i]
        xj, yj = points[j]
        intersects = (yi > y) != (yj > y)
        if intersects:
            x_cross = (xj - xi) * (y - yi) / (yj - yi if yj != yi else 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def format_pos(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}"


def format_ms(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def value_mode_unit(value_mode: str) -> str:
    if value_mode == VALUE_MODE_COUNT:
        return "spikes"
    if value_mode == VALUE_MODE_RATE:
        return "Hz"
    raise ValueError(f"Unknown value mode: {value_mode}")


def value_mode_slug(value_mode: str) -> str:
    if value_mode == VALUE_MODE_COUNT:
        return "spike_count"
    if value_mode == VALUE_MODE_RATE:
        return "mean_firing_rate_hz"
    raise ValueError(f"Unknown value mode: {value_mode}")


def value_mode_suffix(value_mode: str) -> str:
    if value_mode == VALUE_MODE_COUNT:
        return " spikes"
    if value_mode == VALUE_MODE_RATE:
        return " Hz"
    raise ValueError(f"Unknown value mode: {value_mode}")


def format_response_value(value: float | None, value_mode: str) -> str:
    if value is None:
        return "n/a"
    if value_mode == VALUE_MODE_COUNT:
        return f"{value:.0f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def spatial_grid_dimensions(
    available_width: float,
    available_height: float,
    columns: int,
    rows: int,
    *,
    minimum_cell_width: float = 0.0,
) -> tuple[float, float, float, float]:
    """Fit a spatial grid and keep singleton-y maps near the legacy 30:7 shape.

    Multi-row RF maps retain square cells.  A singleton y axis has no physical
    height increment to preserve, so stretching only that display row avoids
    turning vertical-bar datasets into an unreadable strip without changing
    their data or hit-test groups.
    """

    columns = max(1, int(columns))
    rows = max(1, int(rows))
    width = max(0.0, float(available_width))
    height = max(0.0, float(available_height))
    if rows == 1:
        aspect = SINGLETON_Y_REFERENCE_COLUMNS / SINGLETON_Y_REFERENCE_ROWS
        grid_width = min(width, height * aspect)
        cell_width = max(float(minimum_cell_width), grid_width / columns)
        grid_width = cell_width * columns
        grid_height = grid_width / aspect
        return cell_width, grid_height, grid_width, grid_height

    cell = max(
        float(minimum_cell_width),
        min(width / columns, height / rows),
    )
    return cell, cell, cell * columns, cell * rows


def polar_ring_span(rows: int) -> float:
    """Return the visual radial width of one scientific y row."""

    return float(SINGLETON_Y_REFERENCE_ROWS if int(rows) == 1 else 1)


def matrix_ppm_data(
    matrix: list[list[float | None]],
    width: int,
    height: int,
    color_for_value: Callable[[float | None], str],
) -> bytes:
    """Rasterize a matrix into a binary PPM image using nearest-neighbor cells."""
    width = max(1, int(width))
    height = max(1, int(height))
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    if rows == 0 or cols == 0:
        return f"P6\n{width} {height}\n255\n".encode("ascii") + bytes([230, 232, 235]) * (width * height)

    rgb_by_cell: list[list[tuple[int, int, int]]] = []
    for row in matrix:
        if len(row) != cols:
            raise ValueError("Cannot rasterize a ragged matrix")
        rgb_row: list[tuple[int, int, int]] = []
        for value in row:
            color = color_for_value(value).lstrip("#")
            if len(color) != 6:
                raise ValueError(f"Expected #RRGGBB color, got {color!r}")
            rgb_row.append(tuple(int(color[index : index + 2], 16) for index in (0, 2, 4)))
        rgb_by_cell.append(rgb_row)

    pixels = bytearray(width * height * 3)
    offset = 0
    for pixel_y in range(height):
        source_y = min(rows - 1, pixel_y * rows // height)
        for pixel_x in range(width):
            source_x = min(cols - 1, pixel_x * cols // width)
            red, green, blue = rgb_by_cell[source_y][source_x]
            pixels[offset : offset + 3] = bytes((red, green, blue))
            offset += 3
    return f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(pixels)


def matrix_atlas_ppm_data(
    tiles: list[
        tuple[list[list[float | None]], float, float, float]
        | tuple[list[list[float | None]], float, float, float, float]
    ],
    width: int,
    height: int,
    color_for_value: Callable[[float | None], str],
) -> bytes:
    """Rasterize many equally-scaled matrices into one white PPM atlas."""
    width = max(1, int(width))
    height = max(1, int(height))
    pixels = bytearray(b"\xff" * (width * height * 3))
    color_cache: dict[str, bytes] = {}
    value_color_cache: dict[float | None, bytes] = {}

    for tile in tiles:
        if len(tile) == 4:
            matrix, origin_x, origin_y, cell_width = tile
            cell_height = cell_width
        else:
            matrix, origin_x, origin_y, cell_width, cell_height = tile
        rows = len(matrix)
        cols = len(matrix[0]) if rows else 0
        if any(len(row) != cols for row in matrix):
            raise ValueError("Cannot rasterize a ragged matrix")
        cell_width = float(cell_width)
        cell_height = float(cell_height)
        if cell_width <= 0.0 or cell_height <= 0.0:
            continue
        x_ranges: list[tuple[int, int]] = []
        for col_idx in range(cols):
            x0 = max(0, min(width, int(round(origin_x + col_idx * cell_width))))
            x1 = max(
                x0,
                min(width, int(round(origin_x + (col_idx + 1) * cell_width))),
            )
            x_ranges.append((x0, x1))
        for row_idx, row in enumerate(matrix):
            y0 = max(0, min(height, int(round(origin_y + row_idx * cell_height))))
            y1 = max(
                y0,
                min(height, int(round(origin_y + (row_idx + 1) * cell_height))),
            )
            if y1 <= y0:
                continue
            scanlines: list[tuple[int, bytes]] = []
            scanline_start: int | None = None
            scanline_end = 0
            scanline_parts: list[bytes] = []
            for col_idx, value in enumerate(row):
                x0, x1 = x_ranges[col_idx]
                if x1 <= x0:
                    continue
                value_key = None if value is None else float(value)
                rgb = value_color_cache.get(value_key)
                if rgb is None:
                    color = color_for_value(value).lower()
                    rgb = color_cache.get(color)
                    if rgb is None:
                        raw = color.lstrip("#")
                        if len(raw) != 6:
                            raise ValueError(f"Expected #RRGGBB color, got {color!r}")
                        rgb = bytes(
                            int(raw[index : index + 2], 16)
                            for index in (0, 2, 4)
                        )
                        color_cache[color] = rgb
                    value_color_cache[value_key] = rgb
                if scanline_start is not None and x0 != scanline_end:
                    scanlines.append((scanline_start, b"".join(scanline_parts)))
                    scanline_start = None
                    scanline_parts = []
                if scanline_start is None:
                    scanline_start = x0
                scanline_end = x1
                scanline_parts.append(rgb * (x1 - x0))
            if scanline_start is not None:
                scanlines.append((scanline_start, b"".join(scanline_parts)))
            for x0, scanline in scanlines:
                for pixel_y in range(y0, y1):
                    offset = (pixel_y * width + x0) * 3
                    pixels[offset : offset + len(scanline)] = scanline

    return f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(pixels)


@lru_cache(maxsize=256)
def _polar_tile_pixel_runs(
    origin_x_fraction: float,
    origin_y_fraction: float,
    scale: float,
    total_deg: float,
    rows: int,
    cols: int,
    ring_span: float = 1.0,
) -> tuple[tuple[int, int, int, int, int], ...]:
    """Map one polar tile's scanlines to ring/column runs for reuse."""

    ring_span = max(float(ring_span), 1e-9)
    radius_units = INNER_BLANK_ROWS + rows * ring_span
    diameter = 2.0 * radius_units * scale
    center_x = origin_x_fraction + diameter / 2.0
    center_y = origin_y_fraction + diameter / 2.0
    local_width = int(math.ceil(origin_x_fraction + diameter))
    local_height = int(math.ceil(origin_y_fraction + diameter))
    column_span = total_deg / cols
    theta_start = 90.0 + total_deg / 2.0
    theta_end = 90.0 - total_deg / 2.0
    runs: list[tuple[int, int, int, int, int]] = []

    for pixel_y in range(local_height):
        dy = (center_y - (pixel_y + 0.5)) / scale
        run_start: int | None = None
        run_value: tuple[int, int] | None = None
        for pixel_x in range(local_width):
            dx = ((pixel_x + 0.5) - center_x) / scale
            radius = math.hypot(dx, dy)
            value: tuple[int, int] | None = None
            if INNER_BLANK_ROWS <= radius < radius_units:
                ring_idx = int((radius - INNER_BLANK_ROWS) / ring_span)
                if 0 <= ring_idx < rows:
                    theta_deg = math.degrees(math.atan2(dy, dx))
                    if total_deg >= 359.999:
                        relative = (theta_start - theta_deg) % 360.0
                    else:
                        while theta_deg > theta_start:
                            theta_deg -= 360.0
                        while theta_deg < theta_end:
                            theta_deg += 360.0
                        if theta_end <= theta_deg <= theta_start:
                            relative = theta_start - theta_deg
                        else:
                            relative = None
                    if relative is not None:
                        column = max(
                            0,
                            min(cols - 1, int(relative / column_span)),
                        )
                        value = ring_idx, column

            if value == run_value:
                continue
            if run_value is not None and run_start is not None:
                runs.append(
                    (pixel_y, run_start, pixel_x, run_value[0], run_value[1])
                )
            run_start = pixel_x if value is not None else None
            run_value = value
        if run_value is not None and run_start is not None:
            runs.append(
                (pixel_y, run_start, local_width, run_value[0], run_value[1])
            )
    return tuple(runs)


def polar_matrix_atlas_ppm_data(
    tiles: list[
        tuple[
            list[list[float | None]],
            float,
            float,
            float,
            float,
            list[int],
        ]
        | tuple[
            list[list[float | None]],
            float,
            float,
            float,
            float,
            list[int],
            float,
        ]
    ],
    width: int,
    height: int,
    color_for_value: Callable[[float | None], str],
) -> bytes:
    """Rasterize polar matrices into one white PPM atlas.

    Keeping the timeline previews in a single image avoids creating thousands
    of individual Tk canvas polygons when the source contains many time bins.
    """
    width = max(1, int(width))
    height = max(1, int(height))
    pixels = bytearray(b"\xff" * (width * height * 3))
    color_cache: dict[str, bytes] = {}
    value_color_cache: dict[float | None, bytes] = {}

    for tile in tiles:
        if len(tile) == 6:
            matrix, origin_x, origin_y, scale, total_deg, ring_rows = tile
            ring_span = 1.0
        else:
            (
                matrix,
                origin_x,
                origin_y,
                scale,
                total_deg,
                ring_rows,
                ring_span,
            ) = tile
        rows = len(matrix)
        cols = len(matrix[0]) if rows else 0
        if rows == 0 or cols == 0:
            continue
        if any(len(row) != cols for row in matrix):
            raise ValueError("Cannot rasterize a ragged polar matrix")
        if len(ring_rows) != rows:
            raise ValueError("Polar ring order must match matrix rows")

        rgb_by_cell: list[list[bytes]] = []
        for row in matrix:
            rgb_row: list[bytes] = []
            for value in row:
                value_key = None if value is None else float(value)
                rgb = value_color_cache.get(value_key)
                if rgb is None:
                    color = color_for_value(value).lower()
                    rgb = color_cache.get(color)
                    if rgb is None:
                        raw = color.lstrip("#")
                        if len(raw) != 6:
                            raise ValueError(f"Expected #RRGGBB color, got {color!r}")
                        rgb = bytes(
                            int(raw[index : index + 2], 16)
                            for index in (0, 2, 4)
                        )
                        color_cache[color] = rgb
                    value_color_cache[value_key] = rgb
                rgb_row.append(rgb)
            rgb_by_cell.append(rgb_row)

        scale = max(float(scale), 1e-9)
        origin_x_floor = math.floor(origin_x)
        origin_y_floor = math.floor(origin_y)
        runs = _polar_tile_pixel_runs(
            float(origin_x - origin_x_floor),
            float(origin_y - origin_y_floor),
            scale,
            float(total_deg),
            rows,
            cols,
            float(ring_span),
        )
        for local_y, local_x0, local_x1, ring_idx, column in runs:
            pixel_y = origin_y_floor + local_y
            if not (0 <= pixel_y < height):
                continue
            pixel_x0 = max(0, origin_x_floor + local_x0)
            pixel_x1 = min(width, origin_x_floor + local_x1)
            if pixel_x1 <= pixel_x0:
                continue
            rgb = rgb_by_cell[ring_rows[ring_idx]][column]
            offset = (pixel_y * width + pixel_x0) * 3
            scanline = rgb * (pixel_x1 - pixel_x0)
            pixels[offset : offset + len(scanline)] = scanline

    return f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(pixels)
