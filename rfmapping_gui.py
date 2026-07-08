#!/usr/bin/env python3
"""Standalone native GUI viewer for RF mapping spike-count JSON files.

The app intentionally uses only Python's standard library and Tk. It does not
depend on notebook state, web servers, numpy, matplotlib, or pandas.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from tkinter import filedialog, messagebox, ttk
    import tkinter as tk
    TK_AVAILABLE = True
except ModuleNotFoundError:
    filedialog = messagebox = ttk = None
    TK_AVAILABLE = False

    class _MissingTk:
        Tk = object
        TclError = ValueError

    tk = _MissingTk()


DEFAULT_JSON_DIR = Path("data")
DEFAULT_JSON = DEFAULT_JSON_DIR / "unitsSpikeCounts_260701_1.json"
INNER_BLANK_ROWS = 4
POLAR_PAD_ROWS = 1
RF_MODES = ("Total", "Peak", "Bin", "Range sum")
PALETTES = ("Gray", "Viridis", "Inferno")
POLAR_RADIUS_MODES = ("MATLAB row 1 inner", "Display bottom inner")
AxisGroup = tuple[int, int]
CellRef = tuple[int, int, int, int]


def safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _resolve_existing_file(path: Path) -> Path | None:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        resolved = path.expanduser().absolute()
    return resolved if resolved.is_file() else None


def discover_json_files(root: Path | None = None, current_path: Path | None = None) -> list[Path]:
    base = (root or Path.cwd()).expanduser()
    candidates: list[Path] = []
    for folder in (base / DEFAULT_JSON_DIR, base):
        if folder.is_dir():
            candidates.extend(folder.glob("*.json"))
    if current_path is not None:
        candidates.append(current_path)

    unique: dict[str, Path] = {}
    for candidate in candidates:
        resolved = _resolve_existing_file(candidate)
        if resolved is not None:
            unique[str(resolved)] = resolved
    return sorted(unique.values(), key=lambda path: (safe_mtime(path), path.name), reverse=True)


def latest_json_path(root: Path | None = None) -> Path:
    files = discover_json_files(root)
    return files[0] if files else DEFAULT_JSON


@dataclass(frozen=True)
class UnitMetrics:
    total: list[list[float]]
    peak: list[list[float]]
    peak_bin: list[list[int | None]]
    delay_ms: list[list[float | None]]
    entropy: list[list[float]]
    bin_totals: list[float]
    max_total: float
    max_peak: float
    max_bin_count: float
    total_spikes: float
    best_y: int
    best_x: int


class RFMappingData:
    """Validated in-memory representation of the JSON payload."""

    def __init__(self, path: Path):
        self.path = path
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

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

        self.counts = raw["unitsSpikeCounts"]
        self.size = tuple(int(v) for v in raw["unitsSpikeCountsSize"])
        if len(self.size) != 4:
            raise ValueError(f"unitsSpikeCountsSize must have 4 values, got {self.size!r}")

        self.n_units, self.n_y, self.n_x, self.n_bins = self.size
        self.unit_pool = [int(v) for v in raw["unitPool"]]
        self.x_positions = [float(v) for v in raw["xPositions"]]
        self.y_positions = [float(v) for v in raw["yPositions"]]
        self.time_bin_edges = [float(v) for v in raw["timeBinEdges"]]
        self._metrics_cache: dict[int, UnitMetrics] = {}

        self._validate()

    def _validate(self) -> None:
        if len(self.counts) != self.n_units:
            raise ValueError("unitsSpikeCounts first dimension does not match unitsSpikeCountsSize")
        if len(self.unit_pool) != self.n_units:
            raise ValueError("unitPool length does not match unit count")
        if len(self.x_positions) != self.n_x:
            raise ValueError("xPositions length does not match x dimension")
        if len(self.y_positions) != self.n_y:
            raise ValueError("yPositions length does not match y dimension")
        if len(self.time_bin_edges) != self.n_bins + 1:
            raise ValueError("timeBinEdges must contain nBins + 1 edges")

        for unit_idx, unit in enumerate(self.counts):
            if len(unit) != self.n_y:
                raise ValueError(f"Unit {unit_idx} has wrong y dimension")
            for y_idx, row in enumerate(unit):
                if len(row) != self.n_x:
                    raise ValueError(f"Unit {unit_idx}, y {y_idx} has wrong x dimension")
                for x_idx, hist in enumerate(row):
                    if len(hist) != self.n_bins:
                        raise ValueError(
                            f"Unit {unit_idx}, y {y_idx}, x {x_idx} has wrong bin dimension"
                        )

    def display_y_indices(self, flip_y: bool = True) -> list[int]:
        if flip_y:
            return list(range(self.n_y - 1, -1, -1))
        return list(range(self.n_y))

    def cluster_id(self, unit_idx: int) -> int:
        return self.unit_pool[unit_idx]

    def bin_label(self, bin_idx: int) -> str:
        start = self.time_bin_edges[bin_idx] * 1000.0
        end = self.time_bin_edges[bin_idx + 1] * 1000.0
        return f"{bin_idx}: {start:.0f}-{end:.0f} ms"

    def bin_center_ms(self, bin_idx: int) -> float:
        return (self.time_bin_edges[bin_idx] + self.time_bin_edges[bin_idx + 1]) * 500.0

    def infer_total_deg(self) -> float:
        if self.n_x <= 1:
            return 360.0
        diffs = [self.x_positions[i + 1] - self.x_positions[i] for i in range(self.n_x - 1)]
        step = sum(diffs) / len(diffs)
        if all(abs(d - step) < 1e-6 for d in diffs) and abs(step) > 1e-9:
            return abs(step) * self.n_x
        return abs(self.x_positions[-1] - self.x_positions[0])

    def metrics(self, unit_idx: int) -> UnitMetrics:
        cached = self._metrics_cache.get(unit_idx)
        if cached is not None:
            return cached

        unit = self.counts[unit_idx]
        total: list[list[float]] = []
        peak: list[list[float]] = []
        peak_bin: list[list[int | None]] = []
        delay_ms: list[list[float | None]] = []
        entropy: list[list[float]] = []
        bin_totals = [0.0 for _ in range(self.n_bins)]

        max_total = 0.0
        max_peak = 0.0
        max_bin_count = 0.0
        total_spikes = 0.0
        best_y = 0
        best_x = 0

        for y_idx in range(self.n_y):
            total_row: list[float] = []
            peak_row: list[float] = []
            peak_bin_row: list[int | None] = []
            delay_row: list[float | None] = []
            entropy_row: list[float] = []
            for x_idx in range(self.n_x):
                hist = [float(v) for v in unit[y_idx][x_idx]]
                cell_total = sum(hist)
                cell_peak = max(hist) if hist else 0.0
                if cell_total > 0:
                    best_bin = max(range(self.n_bins), key=lambda i: hist[i])
                    delay = self.bin_center_ms(best_bin)
                    ent = 0.0
                    for count in hist:
                        if count > 0:
                            p = count / cell_total
                            ent -= p * math.log(p)
                    ent = ent / math.log(self.n_bins) if self.n_bins > 1 else 0.0
                else:
                    best_bin = None
                    delay = None
                    ent = 0.0

                for bin_idx, count in enumerate(hist):
                    bin_totals[bin_idx] += count
                    if count > max_bin_count:
                        max_bin_count = count

                if cell_total > max_total:
                    max_total = cell_total
                    best_y = y_idx
                    best_x = x_idx
                if cell_peak > max_peak:
                    max_peak = cell_peak

                total_spikes += cell_total
                total_row.append(cell_total)
                peak_row.append(cell_peak)
                peak_bin_row.append(best_bin)
                delay_row.append(delay)
                entropy_row.append(ent)

            total.append(total_row)
            peak.append(peak_row)
            peak_bin.append(peak_bin_row)
            delay_ms.append(delay_row)
            entropy.append(entropy_row)

        metrics = UnitMetrics(
            total=total,
            peak=peak,
            peak_bin=peak_bin,
            delay_ms=delay_ms,
            entropy=entropy,
            bin_totals=bin_totals,
            max_total=max_total,
            max_peak=max_peak,
            max_bin_count=max_bin_count,
            total_spikes=total_spikes,
            best_y=best_y,
            best_x=best_x,
        )
        self._metrics_cache[unit_idx] = metrics
        return metrics

    def aggregate_matrix(
        self,
        unit_idx: int,
        mode: str,
        bin_idx: int,
        range_start: int,
        range_end: int,
    ) -> list[list[float]]:
        metrics = self.metrics(unit_idx)
        if mode == "Total":
            return clone_matrix(metrics.total)
        if mode == "Peak":
            return clone_matrix(metrics.peak)

        unit = self.counts[unit_idx]
        if mode == "Bin":
            return [
                [float(unit[y_idx][x_idx][bin_idx]) for x_idx in range(self.n_x)]
                for y_idx in range(self.n_y)
            ]
        if mode == "Range sum":
            start = max(0, min(range_start, range_end))
            end = min(self.n_bins - 1, max(range_start, range_end))
            return [
                [
                    float(sum(unit[y_idx][x_idx][start : end + 1]))
                    for x_idx in range(self.n_x)
                ]
                for y_idx in range(self.n_y)
            ]
        raise ValueError(f"Unknown RF mode: {mode}")


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


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def hex_color(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


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


class RFMViewer(tk.Tk):
    def __init__(self, data: RFMappingData):
        super().__init__()
        self.data = data
        self.title("RF Mapping Viewer")
        self.geometry("1440x900")
        self.minsize(1120, 720)

        self.unit_idx = tk.IntVar(value=0)
        self.mode_var = tk.StringVar(value="Total")
        self.bin_var = tk.IntVar(value=0)
        self.range_start_var = tk.IntVar(value=0)
        self.range_end_var = tk.IntVar(value=data.n_bins - 1)
        self.flip_y_var = tk.BooleanVar(value=False)
        self.palette_var = tk.StringVar(value="Gray")
        self.polar_radius_var = tk.StringVar(value=POLAR_RADIUS_MODES[1])
        self.response_floor_var = tk.DoubleVar(value=0.0)
        self.x_bins_var = tk.IntVar(value=data.n_x)
        self.y_bins_var = tk.IntVar(value=data.n_y)
        self.time_res_ms_var = tk.StringVar(value=format_ms(self._base_bin_ms()))
        self.smooth_radius_var = tk.IntVar(value=0)
        self.selected_cell: CellRef | None = None
        self.hover_cell: CellRef | None = None
        self.json_paths: list[Path] = []
        self._json_choice_to_path: dict[str, Path] = {}
        self._canvas_layouts: dict[str, dict[str, object]] = {}
        self._timeline_cells: list[dict[str, float | int]] = []
        self._redraw_after: str | None = None
        self._updating_controls = False
        self._timeline_range_anchor: int | None = None
        self._timeline_scroll_fraction = 0.0
        self._restoring_timeline_scroll = False
        self._tab_keys: dict[str, str] = {}

        self._build_style()
        self._build_layout()
        self._wire_events()
        self._sync_json_combo()
        self._sync_unit_combo()
        self._update_all()

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f5f7fa")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f5f7fa", foreground="#18212f")
        style.configure("Panel.TLabel", background="#ffffff", foreground="#18212f")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#667085")
        style.configure("Title.TLabel", background="#ffffff", foreground="#0f172a", font=("TkDefaultFont", 15, "bold"))
        style.configure("Value.TLabel", background="#ffffff", foreground="#0f172a", font=("TkDefaultFont", 11, "bold"))
        style.configure("TButton", padding=(8, 5))
        style.configure("TNotebook", background="#f5f7fa", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 7))

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, style="Panel.TFrame", padding=14)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)

        main = ttk.Frame(self, padding=(12, 12, 12, 12))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        self._build_sidebar(sidebar)
        self._build_main(main)

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        row = 0
        ttk.Label(parent, text="RF Mapping Viewer", style="Title.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        self.data_label = ttk.Label(parent, text="", style="Muted.TLabel", wraplength=260, justify="left")
        self.data_label.grid(row=row, column=0, sticky="ew", pady=(6, 14))
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, sticky="ew", pady=(0, 12))
        row += 1

        ttk.Label(parent, text="JSON", style="Panel.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        json_row = ttk.Frame(parent, style="Panel.TFrame")
        json_row.grid(row=row, column=0, sticky="ew", pady=(5, 10))
        json_row.columnconfigure(0, weight=1)
        self.json_combo = ttk.Combobox(json_row, state="readonly", width=23)
        self.json_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(json_row, text="Scan", width=5, command=self._sync_json_combo).grid(row=0, column=1, padx=(5, 0))
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, sticky="ew", pady=(0, 12))
        row += 1

        ttk.Label(parent, text="Unit", style="Panel.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        unit_row = ttk.Frame(parent, style="Panel.TFrame")
        unit_row.grid(row=row, column=0, sticky="ew", pady=(5, 10))
        unit_row.columnconfigure(1, weight=1)
        ttk.Button(unit_row, text="<", width=3, command=lambda: self._step_unit(-1)).grid(row=0, column=0, padx=(0, 5))
        self.unit_combo = ttk.Combobox(unit_row, state="readonly", width=23)
        self.unit_combo.grid(row=0, column=1, sticky="ew")
        ttk.Button(unit_row, text=">", width=3, command=lambda: self._step_unit(1)).grid(row=0, column=2, padx=(5, 0))
        row += 1

        self.unit_stats_label = ttk.Label(parent, text="", style="Value.TLabel", wraplength=260, justify="left")
        self.unit_stats_label.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, sticky="ew", pady=(0, 12))
        row += 1

        ttk.Label(parent, text="Display", style="Panel.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        display_frame = ttk.Frame(parent, style="Panel.TFrame")
        display_frame.grid(row=row, column=0, sticky="ew", pady=(5, 10))
        display_frame.columnconfigure(1, weight=1)
        ttk.Checkbutton(display_frame, text="Invert Y (MATLAB flip)", variable=self.flip_y_var, command=self._on_control_changed).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(display_frame, text="X bins", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.x_bins_spin = ttk.Spinbox(
            display_frame,
            from_=1,
            to=self.data.n_x,
            increment=1,
            width=8,
            textvariable=self.x_bins_var,
            command=self._on_control_changed,
        )
        self.x_bins_spin.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(display_frame, text="Y bins", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.y_bins_spin = ttk.Spinbox(
            display_frame,
            from_=1,
            to=self.data.n_y,
            increment=1,
            width=8,
            textvariable=self.y_bins_var,
            command=self._on_control_changed,
        )
        self.y_bins_spin.grid(row=2, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(display_frame, text="Smooth", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.smooth_spin = ttk.Spinbox(
            display_frame,
            from_=0,
            to=3,
            increment=1,
            width=8,
            textvariable=self.smooth_radius_var,
            command=self._on_control_changed,
        )
        self.smooth_spin.grid(row=3, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(display_frame, text="Palette", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(display_frame, state="readonly", values=PALETTES, textvariable=self.palette_var, width=12).grid(row=4, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(display_frame, text="Polar radius", style="Panel.TLabel").grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(display_frame, state="readonly", values=POLAR_RADIUS_MODES, textvariable=self.polar_radius_var, width=18).grid(row=5, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(display_frame, text="Delay floor", style="Panel.TLabel").grid(row=6, column=0, sticky="w", pady=(8, 0))
        self.floor_spin = ttk.Spinbox(
            display_frame,
            from_=0,
            to=9999,
            increment=1,
            width=8,
            textvariable=self.response_floor_var,
            command=self._on_control_changed,
        )
        self.floor_spin.grid(row=6, column=1, sticky="ew", pady=(8, 0))
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, sticky="ew", pady=(0, 12))
        row += 1

        ttk.Label(parent, text="Selected cell", style="Panel.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        self.cell_label = ttk.Label(parent, text="", style="Muted.TLabel", justify="left", wraplength=260)
        self.cell_label.grid(row=row, column=0, sticky="ew", pady=(5, 12))
        row += 1

        button_frame = ttk.Frame(parent, style="Panel.TFrame")
        button_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)
        ttk.Button(button_frame, text="Open JSON", command=self._open_json).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(button_frame, text="Export CSV", command=self._export_current_matrix).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        row += 1

        parent.rowconfigure(row, weight=1)

    def _build_main(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)
        self.header_label = ttk.Label(header, text="", font=("TkDefaultFont", 13, "bold"))
        self.header_label.grid(row=0, column=0, sticky="w")
        self.status_label = ttk.Label(header, text="", foreground="#667085")
        self.status_label.grid(row=1, column=0, sticky="w", pady=(3, 0))

        self._build_plot_controls(parent)

        self.notebook = ttk.Notebook(parent)
        self.notebook.grid(row=2, column=0, sticky="nsew")

        self.canvases: dict[str, tk.Canvas] = {}
        self._tab_keys = {}
        for key, title in (
            ("rf", "2D RF"),
            ("delay", "Delay"),
            ("polar", "Polar"),
            ("timeline", "Timeline"),
            ("rgb", "RGB"),
            ("stack", "Stack"),
        ):
            frame = ttk.Frame(self.notebook)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            canvas = tk.Canvas(frame, background="#ffffff", highlightthickness=0)
            canvas.grid(row=0, column=0, sticky="nsew")
            if key == "timeline":
                frame.columnconfigure(1, weight=0)
                self.timeline_scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self._timeline_yview)
                self.timeline_scrollbar.grid(row=0, column=1, sticky="ns")
                canvas.configure(yscrollcommand=self._timeline_scroll_set)
            self.notebook.add(frame, text=title)
            self.canvases[key] = canvas
            self._tab_keys[str(frame)] = key

    def _build_plot_controls(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent, style="Panel.TFrame", padding=(10, 8))
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        controls.columnconfigure(3, weight=1)

        ttk.Label(controls, text="RF value", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.mode_combo = ttk.Combobox(
            controls,
            state="readonly",
            values=RF_MODES,
            textvariable=self.mode_var,
            width=12,
        )
        self.mode_combo.grid(row=0, column=1, sticky="ew", padx=(0, 14))

        ttk.Label(controls, text="Time res (ms)", style="Panel.TLabel").grid(row=0, column=2, sticky="e", padx=(0, 6))
        self.time_res_spin = ttk.Spinbox(
            controls,
            from_=self._base_bin_ms(),
            to=self._total_time_ms(),
            increment=self._base_bin_ms(),
            width=8,
            textvariable=self.time_res_ms_var,
            command=self._on_control_changed,
        )
        self.time_res_spin.grid(row=0, column=3, sticky="w", padx=(0, 14))

        ttk.Label(controls, text="Plot range", style="Panel.TLabel").grid(row=0, column=4, sticky="e", padx=(0, 6))
        self.range_start_spin = ttk.Spinbox(
            controls,
            from_=0,
            to=self._time_group_count() - 1,
            width=4,
            textvariable=self.range_start_var,
            command=self._on_control_changed,
        )
        self.range_start_spin.grid(row=0, column=5, sticky="ew")
        ttk.Label(controls, text="to", style="Panel.TLabel").grid(row=0, column=6, padx=6)
        self.range_end_spin = ttk.Spinbox(
            controls,
            from_=0,
            to=self._time_group_count() - 1,
            width=4,
            textvariable=self.range_end_var,
            command=self._on_control_changed,
        )
        self.range_end_spin.grid(row=0, column=7, sticky="ew")

        ttk.Label(controls, text="Bin", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0), padx=(0, 6))
        self.bin_scale = ttk.Scale(
            controls,
            from_=0,
            to=self._time_group_count() - 1,
            orient="horizontal",
            command=self._on_bin_scale,
        )
        self.bin_scale.grid(row=1, column=1, columnspan=6, sticky="ew", pady=(8, 0), padx=(0, 8))
        self.bin_label = ttk.Label(controls, text="", style="Muted.TLabel")
        self.bin_label.grid(row=1, column=7, sticky="e", pady=(8, 0))

    def _wire_events(self) -> None:
        self.json_combo.bind("<<ComboboxSelected>>", self._on_json_selected)
        self.unit_combo.bind("<<ComboboxSelected>>", self._on_unit_selected)
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_control_changed)
        self.range_start_spin.bind("<Return>", self._on_control_changed)
        self.range_end_spin.bind("<Return>", self._on_control_changed)
        self.time_res_spin.bind("<Return>", self._on_control_changed)
        self.floor_spin.bind("<Return>", self._on_control_changed)
        self.x_bins_spin.bind("<Return>", self._on_control_changed)
        self.y_bins_spin.bind("<Return>", self._on_control_changed)
        self.smooth_spin.bind("<Return>", self._on_control_changed)
        self.palette_var.trace_add("write", lambda *_: self._on_control_changed())
        self.polar_radius_var.trace_add("write", lambda *_: self._on_control_changed())
        self.notebook.bind("<<NotebookTabChanged>>", self._on_control_changed)
        self.bind("<Left>", lambda _event: self._step_bin(-1))
        self.bind("<Right>", lambda _event: self._step_bin(1))
        self.bind_all("<Escape>", self._clear_timeline_selection)
        self.bind("<bracketleft>", lambda _event: self._step_unit(-1))
        self.bind("<bracketright>", lambda _event: self._step_unit(1))
        for key, canvas in self.canvases.items():
            canvas.bind("<Configure>", self._schedule_redraw)
            canvas.bind("<Motion>", lambda event, k=key: self._on_canvas_motion(k, event))
            canvas.bind("<Button-1>", lambda event, k=key: self._on_canvas_click(k, event))
            canvas.bind("<Leave>", lambda _event: self._clear_hover())
        self.canvases["timeline"].bind("<MouseWheel>", self._on_timeline_mousewheel)
        self.canvases["timeline"].bind("<Button-4>", self._on_timeline_mousewheel)
        self.canvases["timeline"].bind("<Button-5>", self._on_timeline_mousewheel)

    def _json_choice_label(self, path: Path) -> str:
        try:
            rel = path.relative_to(Path.cwd())
        except ValueError:
            rel = path
        modified = safe_mtime(path)
        stamp = ""
        if modified > 0:
            try:
                from datetime import datetime

                stamp = datetime.fromtimestamp(modified).strftime("  %Y-%m-%d %H:%M")
            except (OSError, ValueError):
                stamp = ""
        return f"{rel}{stamp}"

    def _sync_json_combo(self) -> None:
        current = _resolve_existing_file(self.data.path) or self.data.path
        self.json_paths = discover_json_files(current_path=current)
        if current not in self.json_paths:
            self.json_paths.insert(0, current)
        labels = [self._json_choice_label(path) for path in self.json_paths]
        self._json_choice_to_path = dict(zip(labels, self.json_paths))
        self.json_combo.configure(values=labels)
        current_index = next((idx for idx, path in enumerate(self.json_paths) if path == current), None)
        if current_index is not None and labels:
            self.json_combo.current(current_index)

    def _on_json_selected(self, _event: object | None = None) -> None:
        choice = self.json_combo.get()
        path = self._json_choice_to_path.get(choice)
        if path is None:
            return
        if _resolve_existing_file(self.data.path) == path:
            return
        self._load_json_path(path)

    def _sync_unit_combo(self) -> None:
        values = [
            f"{idx:03d}  cluster {cluster_id}"
            for idx, cluster_id in enumerate(self.data.unit_pool)
        ]
        self.unit_combo.configure(values=values)
        self.unit_combo.current(self.unit_idx.get())

    def _on_unit_selected(self, _event: object | None = None) -> None:
        idx = self.unit_combo.current()
        if idx >= 0:
            self.unit_idx.set(idx)
            self.selected_cell = None
            self._update_all()

    def _step_unit(self, delta: int) -> None:
        idx = (self.unit_idx.get() + delta) % self.data.n_units
        self.unit_idx.set(idx)
        self.unit_combo.current(idx)
        self.selected_cell = None
        self._update_all()

    def _step_bin(self, delta: int) -> None:
        value = max(0, min(self._time_group_count() - 1, self.bin_var.get() + delta))
        self.bin_var.set(value)
        self._set_bin_scale_silently(value)
        self.mode_var.set("Bin")
        self._update_all()

    def _on_bin_scale(self, value: str) -> None:
        if self._updating_controls:
            return
        bin_idx = int(round(float(value)))
        self.bin_var.set(max(0, min(self._time_group_count() - 1, bin_idx)))
        self._update_all()

    def _on_control_changed(self, _event: object | None = None) -> None:
        self._normalize_control_values()
        self._update_all()

    def _clear_timeline_selection(self, _event: object | None = None) -> None:
        self._timeline_range_anchor = None
        self.mode_var.set("Total")
        self.range_start_var.set(0)
        self.range_end_var.set(self._time_group_count() - 1)
        self._update_all()

    def _schedule_redraw(self, _event: object | None = None) -> None:
        if self._redraw_after is not None:
            self.after_cancel(self._redraw_after)
        self._redraw_after = self.after(80, self._update_all)

    def _timeline_scroll_set(self, first: str, last: str) -> None:
        if hasattr(self, "timeline_scrollbar"):
            self.timeline_scrollbar.set(first, last)
        if self._restoring_timeline_scroll:
            return
        try:
            first_value = float(first)
            last_value = float(last)
        except ValueError:
            return
        if last_value < 0.999:
            self._timeline_scroll_fraction = max(0.0, min(1.0, first_value))

    def _timeline_yview(self, *args: object) -> None:
        canvas = self.canvases.get("timeline")
        if canvas is None:
            return
        canvas.yview(*args)
        self._remember_timeline_scroll()

    def _remember_timeline_scroll(self) -> None:
        canvas = self.canvases.get("timeline")
        if canvas is None:
            return
        try:
            first, last = canvas.yview()
        except tk.TclError:
            return
        if last < 0.999:
            self._timeline_scroll_fraction = max(0.0, min(1.0, float(first)))

    def _restore_timeline_scroll(self) -> None:
        canvas = self.canvases.get("timeline")
        if canvas is None:
            return
        self._restoring_timeline_scroll = True
        try:
            canvas.yview_moveto(max(0.0, min(1.0, self._timeline_scroll_fraction)))
        finally:
            self._restoring_timeline_scroll = False
        self._remember_timeline_scroll()

    def _on_timeline_mousewheel(self, event: tk.Event) -> str:
        canvas = self.canvases.get("timeline")
        if canvas is None:
            return "break"
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return "break"
            units = -1 * (delta // 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
            units *= 3
        canvas.yview_scroll(units, "units")
        self._remember_timeline_scroll()
        return "break"

    def _normalize_control_values(self) -> None:
        time_count = self._time_group_count()
        max_bin = max(0, time_count - 1)
        for var in (self.bin_var, self.range_start_var, self.range_end_var):
            try:
                value = int(var.get())
            except (tk.TclError, ValueError):
                value = 0
            var.set(max(0, min(max_bin, value)))
        if self._timeline_range_anchor is not None:
            self._timeline_range_anchor = max(0, min(max_bin, self._timeline_range_anchor))
        self._x_target_bins()
        self._y_target_bins()
        self._smooth_radius()
        self._sync_time_control_ranges(max_bin)
        self._set_bin_scale_silently(self.bin_var.get())

    def _set_bin_scale_silently(self, value: int) -> None:
        if not hasattr(self, "bin_scale"):
            return
        self._updating_controls = True
        try:
            self.bin_scale.set(value)
        finally:
            self._updating_controls = False

    def _active_tab_key(self) -> str:
        if not hasattr(self, "notebook"):
            return "rf"
        selected = self.notebook.select()
        return self._tab_keys.get(str(selected), "rf")

    def _draw_active_tab(self) -> None:
        key = self._active_tab_key()
        if key == "rf":
            self._draw_rf()
        elif key == "delay":
            self._draw_delay()
        elif key == "polar":
            self._draw_polar()
        elif key == "timeline":
            self._draw_timeline()
        elif key == "rgb":
            self._draw_rgb()
        elif key == "stack":
            self._draw_stack()

    def _update_all(self) -> None:
        self._redraw_after = None
        self._normalize_control_values()
        unit_idx = self.unit_idx.get()
        cluster_id = self.data.cluster_id(unit_idx)
        metrics = self.data.metrics(unit_idx)
        self.data_label.configure(
            text=(
                f"{self.data.path}\n"
                f"{self.data.n_units} units  {self.data.n_y} y x {self.data.n_x} x  "
                f"{self.data.n_bins} bins"
            )
        )
        self.header_label.configure(text=f"Unit {unit_idx:03d} / cluster {cluster_id}")
        self.status_label.configure(
            text=(
                f"x: {format_pos(self.data.x_positions[0])}..{format_pos(self.data.x_positions[-1])}  "
                f"y: {format_pos(self.data.y_positions[0])}..{format_pos(self.data.y_positions[-1])}  "
                f"time: {format_ms(self._time_axis_start_ms())}..{format_ms(self._time_axis_end_ms())} ms"
            )
        )
        best_delay = metrics.delay_ms[metrics.best_y][metrics.best_x]
        self.unit_stats_label.configure(
            text=(
                f"Total spikes: {metrics.total_spikes:.0f}\n"
                f"Best cell: yIdx {metrics.best_y + 1}, xIdx {metrics.best_x + 1}\n"
                f"Best delay: {best_delay:.1f} ms" if best_delay is not None else
                f"Total spikes: {metrics.total_spikes:.0f}\n"
                f"Best cell: yIdx {metrics.best_y + 1}, xIdx {metrics.best_x + 1}\n"
                f"Best delay: n/a"
            )
        )
        self.bin_label.configure(text=self._time_group_label(self.bin_var.get()))

        self._update_cell_label()
        self._draw_active_tab()

    def _current_matrix(self) -> list[list[float]]:
        mode = self.mode_var.get()
        if mode == "Total":
            start, end = self._source_bins_for_display_range()
            return self.data.aggregate_matrix(self.unit_idx.get(), "Range sum", 0, start, end)
        if mode == "Bin":
            start, end = self._source_bins_for_display_bin(self.bin_var.get())
            return self.data.aggregate_matrix(self.unit_idx.get(), "Range sum", 0, start, end)
        if mode == "Range sum":
            start, end = self._source_bins_for_display_range()
            return self.data.aggregate_matrix(self.unit_idx.get(), "Range sum", 0, start, end)
        return self.data.aggregate_matrix(
            self.unit_idx.get(),
            mode,
            self.bin_var.get(),
            self.range_start_var.get(),
            self.range_end_var.get(),
        )

    def _delay_matrix_for_time_groups(self, floor: float = 0.0) -> list[list[float | None]]:
        unit = self.data.counts[self.unit_idx.get()]
        metrics = self.data.metrics(self.unit_idx.get())
        groups = self._time_groups()
        delay_matrix: list[list[float | None]] = []
        for y_idx in range(self.data.n_y):
            row: list[float | None] = []
            for x_idx in range(self.data.n_x):
                if metrics.total[y_idx][x_idx] <= floor:
                    row.append(None)
                    continue
                hist = [float(v) for v in unit[y_idx][x_idx]]
                grouped = [sum(hist[start : end + 1]) for start, end in groups]
                if not grouped or max(grouped) <= 0:
                    row.append(None)
                    continue
                peak_group = max(range(len(grouped)), key=lambda idx: grouped[idx])
                row.append(self._time_group_center_ms(peak_group))
            delay_matrix.append(row)
        return delay_matrix

    def _base_bin_ms(self) -> float:
        if len(self.data.time_bin_edges) < 2:
            return 1.0
        diffs = [
            (self.data.time_bin_edges[i + 1] - self.data.time_bin_edges[i]) * 1000.0
            for i in range(len(self.data.time_bin_edges) - 1)
        ]
        positive = [diff for diff in diffs if diff > 1e-9]
        return min(positive) if positive else 1.0

    def _time_axis_start_ms(self) -> float:
        if not self.data.time_bin_edges:
            return 0.0
        return self.data.time_bin_edges[0] * 1000.0

    def _time_axis_end_ms(self) -> float:
        if not self.data.time_bin_edges:
            return self._base_bin_ms() * self.data.n_bins
        return self.data.time_bin_edges[-1] * 1000.0

    def _time_axis_range_ms(self) -> tuple[float, float]:
        start = self._time_axis_start_ms()
        end = self._time_axis_end_ms()
        if end <= start:
            end = start + self._base_bin_ms()
        return start, end

    def _total_time_ms(self) -> float:
        start, end = self._time_axis_range_ms()
        return max(end - start, self._base_bin_ms())

    def _time_group_size(self) -> int:
        base = self._base_bin_ms()
        total = self._total_time_ms()
        try:
            requested = float(self.time_res_ms_var.get())
        except (tk.TclError, ValueError):
            requested = base
        requested = max(base, min(total, requested))
        group_size = max(1, min(self.data.n_bins, int(round(requested / base))))
        self.time_res_ms_var.set(format_ms(group_size * base))
        return group_size

    def _time_groups(self) -> list[AxisGroup]:
        group_size = self._time_group_size()
        return [
            (start, min(start + group_size - 1, self.data.n_bins - 1))
            for start in range(0, self.data.n_bins, group_size)
        ]

    def _time_group_count(self) -> int:
        return max(1, len(self._time_groups()))

    def _display_range_indices(self) -> AxisGroup:
        count = self._time_group_count()
        start = max(0, min(count - 1, min(self.range_start_var.get(), self.range_end_var.get())))
        end = max(0, min(count - 1, max(self.range_start_var.get(), self.range_end_var.get())))
        return start, end

    def _is_full_display_range(self) -> bool:
        start, end = self._display_range_indices()
        return start == 0 and end == self._time_group_count() - 1

    def _display_range_label(self) -> str:
        start, end = self._display_range_indices()
        if start == end:
            return self._time_group_label(start)
        return f"{self._time_group_label(start)} to {self._time_group_label(end)}"

    def _time_group_bounds_ms(self, display_bin: int) -> tuple[float, float]:
        groups = self._time_groups()
        idx = max(0, min(len(groups) - 1, int(display_bin)))
        start, end = groups[idx]
        return self.data.time_bin_edges[start] * 1000.0, self.data.time_bin_edges[end + 1] * 1000.0

    def _time_group_label(self, display_bin: int) -> str:
        start_ms, end_ms = self._time_group_bounds_ms(display_bin)
        idx = max(0, min(self._time_group_count() - 1, int(display_bin))) + 1
        return f"{idx}: {format_ms(start_ms)}-{format_ms(end_ms)} ms"

    def _time_group_end_label(self, display_bin: int) -> str:
        _start_ms, end_ms = self._time_group_bounds_ms(display_bin)
        return f"{format_ms(end_ms)} ms"

    def _time_group_center_ms(self, display_bin: int) -> float:
        start_ms, end_ms = self._time_group_bounds_ms(display_bin)
        return (start_ms + end_ms) / 2.0

    def _source_bins_for_display_bin(self, display_bin: int) -> AxisGroup:
        groups = self._time_groups()
        idx = max(0, min(len(groups) - 1, int(display_bin)))
        return groups[idx]

    def _source_bins_for_display_range(self) -> AxisGroup:
        groups = self._time_groups()
        start_idx, end_idx = self._display_range_indices()
        return groups[start_idx][0], groups[end_idx][1]

    def _time_grouped_hist(self, hist: list[float]) -> list[float]:
        return [float(sum(hist[start : end + 1])) for start, end in self._time_groups()]

    def _has_time_selection(self) -> bool:
        mode = self.mode_var.get()
        return mode in {"Bin", "Range sum"} or (mode == "Total" and not self._is_full_display_range())

    def _visible_timeline_bins(self, display_bins: int) -> list[int]:
        if self.mode_var.get() == "Bin":
            return [max(0, min(display_bins - 1, self.bin_var.get()))]
        if self.mode_var.get() == "Range sum" or (self.mode_var.get() == "Total" and not self._is_full_display_range()):
            start = max(0, min(display_bins - 1, min(self.range_start_var.get(), self.range_end_var.get())))
            end = max(0, min(display_bins - 1, max(self.range_start_var.get(), self.range_end_var.get())))
            return list(range(start, end + 1))
        return list(range(display_bins))

    def _sync_time_control_ranges(self, max_bin: int) -> None:
        if hasattr(self, "bin_scale"):
            self.bin_scale.configure(to=max_bin)
        if hasattr(self, "range_start_spin"):
            self.range_start_spin.configure(to=max_bin)
        if hasattr(self, "range_end_spin"):
            self.range_end_spin.configure(to=max_bin)
        if hasattr(self, "time_res_spin"):
            base = self._base_bin_ms()
            self.time_res_spin.configure(from_=base, to=self._total_time_ms(), increment=base)

    def _x_target_bins(self) -> int:
        try:
            value = int(self.x_bins_var.get())
        except (tk.TclError, ValueError):
            value = self.data.n_x
        value = max(1, min(self.data.n_x, value))
        self.x_bins_var.set(value)
        return value

    def _y_target_bins(self) -> int:
        try:
            value = int(self.y_bins_var.get())
        except (tk.TclError, ValueError):
            value = self.data.n_y
        value = max(1, min(self.data.n_y, value))
        self.y_bins_var.set(value)
        return value

    def _smooth_radius(self) -> int:
        try:
            value = int(self.smooth_radius_var.get())
        except (tk.TclError, ValueError):
            value = 0
        value = max(0, min(3, value))
        self.smooth_radius_var.set(value)
        return value

    def _x_groups(self) -> list[AxisGroup]:
        return axis_groups_for_target(self.data.n_x, self._x_target_bins())

    def _display_y_groups(self) -> list[AxisGroup]:
        groups = axis_groups_for_target(self.data.n_y, self._y_target_bins())
        if self.flip_y_var.get():
            groups = list(reversed(groups))
        return groups

    def _prepare_plot_matrix(
        self,
        matrix: list[list[float | None]],
        *,
        smooth: bool = True,
    ) -> tuple[list[list[float | None]], list[AxisGroup], list[AxisGroup]]:
        x_groups = self._x_groups()
        y_groups = self._display_y_groups()
        prepared = reduce_matrix_xy(matrix, y_groups, x_groups)
        if smooth:
            prepared = smooth_matrix(prepared, self._smooth_radius())
        return prepared, x_groups, y_groups

    def _group_hist(self, y_start: int, y_end: int, x_start: int, x_end: int) -> list[float]:
        hist = [0.0 for _ in range(self.data.n_bins)]
        n = max(1, (y_end - y_start + 1) * (x_end - x_start + 1))
        unit = self.data.counts[self.unit_idx.get()]
        for y_idx in range(y_start, y_end + 1):
            for x_idx in range(x_start, x_end + 1):
                for bin_idx, value in enumerate(unit[y_idx][x_idx]):
                    hist[bin_idx] += float(value) / n
        return hist

    def _group_total(self, matrix: list[list[float | None]], y_start: int, y_end: int, x_start: int, x_end: int) -> float:
        values = [
            float(matrix[y_idx][x_idx])
            for y_idx in range(y_start, y_end + 1)
            for x_idx in range(x_start, x_end + 1)
            if matrix[y_idx][x_idx] is not None
        ]
        return sum(values) / len(values) if values else 0.0

    def _y_group_text(self, y_start: int, y_end: int) -> str:
        if y_start == y_end:
            return f"yIdx {y_start + 1}; y {format_pos(self.data.y_positions[y_start])}"
        return (
            f"yIdx {y_start + 1}-{y_end + 1}; "
            f"y {format_pos(self.data.y_positions[y_start])}..{format_pos(self.data.y_positions[y_end])}"
        )

    def _x_group_text(self, x_start: int, x_end: int) -> str:
        if x_start == x_end:
            return f"xIdx {x_start + 1}; x {format_pos(self.data.x_positions[x_start])}"
        return (
            f"xIdx {x_start + 1}-{x_end + 1}; "
            f"x {format_pos(self.data.x_positions[x_start])}..{format_pos(self.data.x_positions[x_end])}"
        )

    def _current_matrix_label(self) -> str:
        mode = self.mode_var.get()
        if mode == "Total":
            return f"Total: {self._display_range_label()}"
        if mode == "Bin":
            return self._time_group_label(self.bin_var.get())
        if mode == "Range sum":
            return f"Range sum: {self._display_range_label()}"
        return mode

    def _cell_metrics_text(self, y_start: int, y_end: int, x_idx: int, x_end: int) -> str:
        unit_idx = self.unit_idx.get()
        metrics = self.data.metrics(unit_idx)
        hist = self._group_hist(y_start, y_end, x_idx, x_end)
        display_hist = self._time_grouped_hist(hist)
        bin_idx = self.bin_var.get()
        start = min(self.range_start_var.get(), self.range_end_var.get())
        end = max(self.range_start_var.get(), self.range_end_var.get())
        total_value = self._group_total(metrics.total, y_start, y_end, x_idx, x_end)
        peak_value = max(display_hist) if display_hist else 0.0
        if sum(display_hist) > 0:
            peak_bin = max(range(len(display_hist)), key=lambda i: display_hist[i])
            delay = self._time_group_center_ms(peak_bin)
            ent = 0.0
            total_hist = sum(display_hist)
            for count in display_hist:
                if count > 0:
                    p = count / total_hist
                    ent -= p * math.log(p)
            ent = ent / math.log(len(display_hist)) if len(display_hist) > 1 else 0.0
        else:
            peak_bin = None
            delay = None
            ent = 0.0
        delay_text = f"{delay:.1f} ms" if delay is not None else "n/a"
        peak_text = f"{peak_bin + 1} ({self._time_group_label(peak_bin)})" if peak_bin is not None else "n/a"
        group_note = "avg over source pixels\n" if (x_end != x_idx or y_end != y_start) else ""
        return (
            f"cluster {self.data.cluster_id(unit_idx)}\n"
            f"{self._y_group_text(y_start, y_end)}, {self._x_group_text(x_idx, x_end)}\n"
            f"{group_note}"
            f"bin count {float(display_hist[bin_idx]):.0f} ({self._time_group_label(bin_idx)})\n"
            f"range sum {float(sum(display_hist[start:end + 1])):.0f}\n"
            f"total {total_value:.0f}, peak {peak_value:.0f}\n"
            f"peak bin {peak_text}\n"
            f"delay {delay_text}, entropy {ent:.3f}"
        )

    def _update_cell_label(self, cell: CellRef | None = None, prefix: str = "") -> None:
        if cell is None and self.hover_cell is not None:
            cell = self.hover_cell
            prefix = "Hover\n"
        if cell is None and self.selected_cell is None:
            metrics = self.data.metrics(self.unit_idx.get())
            self.selected_cell = (metrics.best_y, metrics.best_y, metrics.best_x, metrics.best_x)
        if cell is None:
            cell = self.selected_cell
        if cell is None:
            return
        y_start, y_end, x_idx, x_end = cell
        self.cell_label.configure(text=prefix + self._cell_metrics_text(y_start, y_end, x_idx, x_end))

    def _draw_rf(self) -> None:
        matrix = self._current_matrix()
        title = f"2D RF map - {self._current_matrix_label()}"
        self._draw_heatmap(
            "rf",
            matrix,
            title,
            self.palette_var.get(),
            value_suffix="",
            fixed_range=None,
        )

    def _draw_delay(self) -> None:
        delay_matrix = self._delay_matrix_for_time_groups(self._response_floor())
        self._draw_heatmap(
            "delay",
            delay_matrix,
            "Delay map - peak displayed bin center",
            "Delay",
            value_suffix=" ms",
            fixed_range=self._time_axis_range_ms(),
        )

    def _draw_heatmap(
        self,
        key: str,
        matrix: list[list[float | None]],
        title: str,
        palette: str,
        value_suffix: str,
        fixed_range: tuple[float, float] | None,
    ) -> None:
        canvas = self.canvases[key]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 160)
        margin_l, margin_r, margin_t, margin_b = 78, 104, 56, 68
        plot_w = max(10, w - margin_l - margin_r)
        plot_h = max(10, h - margin_t - margin_b)
        disp, x_groups, y_groups = self._prepare_plot_matrix(matrix)
        n_cols = len(x_groups)
        n_rows = len(y_groups)
        cell = max(4.0, min(plot_w / n_cols, plot_h / n_rows))
        grid_w = cell * n_cols
        grid_h = cell * n_rows
        x0 = margin_l + (plot_w - grid_w) / 2
        y0 = margin_t + (plot_h - grid_h) / 2
        if fixed_range is None:
            low, high = finite_min_max(disp)
        else:
            low, high = fixed_range

        canvas.create_text(20, 22, anchor="w", text=title, font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(20, 44, anchor="w", text=f"Unit {self.unit_idx.get():03d} / cluster {self.data.cluster_id(self.unit_idx.get())}", fill="#667085")

        for display_y, row in enumerate(disp):
            y = y0 + display_y * cell
            for x_idx, value in enumerate(row):
                x = x0 + x_idx * cell
                if palette == "Delay":
                    fill = delay_color(value, low, high)
                else:
                    fill = palette_color(value, low, high, palette)
                canvas.create_rectangle(x, y, x + cell, y + cell, fill=fill, outline="#ffffff", width=0)

        self._draw_selection_outline(canvas, x0, y0, cell, x_groups, y_groups)
        self._draw_axes(canvas, x0, y0, cell, grid_w, grid_h, x_groups, y_groups)
        self._draw_colorbar(canvas, x0 + grid_w + 36, y0, min(220, grid_h), low, high, palette, value_suffix)
        self._canvas_layouts[key] = {
            "x0": x0,
            "y0": y0,
            "cell": cell,
            "grid_w": grid_w,
            "grid_h": grid_h,
            "x_groups": x_groups,
            "y_groups": y_groups,
        }

    def _draw_axes(self, canvas: tk.Canvas, x0: float, y0: float, cell: float, grid_w: float, grid_h: float, x_groups: list[AxisGroup], y_groups: list[AxisGroup]) -> None:
        axis_color = "#475467"
        canvas.create_rectangle(x0, y0, x0 + grid_w, y0 + grid_h, outline="#1f2937", width=1)
        tick_step = max(1, len(x_groups) // 6)
        for group_idx in range(0, len(x_groups), tick_step):
            start, end = x_groups[group_idx]
            x = x0 + (group_idx + 0.5) * cell
            canvas.create_line(x, y0 + grid_h, x, y0 + grid_h + 5, fill=axis_color)
            pos = (self.data.x_positions[start] + self.data.x_positions[end]) / 2.0
            canvas.create_text(x, y0 + grid_h + 18, text=format_pos(pos), fill=axis_color, font=("TkDefaultFont", 9))
        if (len(x_groups) - 1) not in range(0, len(x_groups), tick_step):
            start, end = x_groups[-1]
            x = x0 + (len(x_groups) - 0.5) * cell
            pos = (self.data.x_positions[start] + self.data.x_positions[end]) / 2.0
            canvas.create_text(x, y0 + grid_h + 18, text=format_pos(pos), fill=axis_color, font=("TkDefaultFont", 9))

        for display_y, (y_start, y_end) in enumerate(y_groups):
            y = y0 + (display_y + 0.5) * cell
            canvas.create_line(x0 - 5, y, x0, y, fill=axis_color)
            pos = (self.data.y_positions[y_start] + self.data.y_positions[y_end]) / 2.0
            label = f"{y_start + 1} / {format_pos(pos)}" if y_start == y_end else f"{y_start + 1}-{y_end + 1} / {format_pos(pos)}"
            canvas.create_text(x0 - 10, y, anchor="e", text=label, fill=axis_color, font=("TkDefaultFont", 9))

        canvas.create_text(x0 + grid_w / 2, y0 + grid_h + 44, text="x position", fill=axis_color)
        canvas.create_text(x0 - 58, y0 + grid_h / 2, text="yIdx / y", angle=90, fill=axis_color)

    def _draw_colorbar(
        self,
        canvas: tk.Canvas,
        x: float,
        y: float,
        height: float,
        low: float,
        high: float,
        palette: str,
        suffix: str,
    ) -> None:
        steps = 90
        width = 16
        for i in range(steps):
            t0 = i / steps
            value = high - (high - low) * t0
            fill = delay_color(value, low, high) if palette == "Delay" else palette_color(value, low, high, palette)
            y1 = y + height * i / steps
            y2 = y + height * (i + 1) / steps
            canvas.create_rectangle(x, y1, x + width, y2, outline="", fill=fill)
        canvas.create_rectangle(x, y, x + width, y + height, outline="#475467")
        canvas.create_text(x + width + 8, y, anchor="w", text=f"{high:.1f}{suffix}", fill="#475467", font=("TkDefaultFont", 9))
        canvas.create_text(x + width + 8, y + height, anchor="w", text=f"{low:.1f}{suffix}", fill="#475467", font=("TkDefaultFont", 9))

    def _draw_selection_outline(self, canvas: tk.Canvas, x0: float, y0: float, cell: float, x_groups: list[AxisGroup] | None = None, y_groups: list[AxisGroup] | None = None) -> None:
        if self.selected_cell is None:
            return
        y_start, _y_end, x_idx, _x_end = self.selected_cell
        x_groups = x_groups or self._x_groups()
        y_groups = y_groups or self._display_y_groups()
        group_idx = 0
        for idx, (start, end) in enumerate(x_groups):
            if start <= x_idx <= end:
                group_idx = idx
                break
        display_y = None
        for idx, (start, end) in enumerate(y_groups):
            if start <= y_start <= end:
                display_y = idx
                break
        if display_y is None:
            return
        x = x0 + group_idx * cell
        y = y0 + display_y * cell
        canvas.create_rectangle(x + 1, y + 1, x + cell - 1, y + cell - 1, outline="#111827", width=2)
        canvas.create_rectangle(x + 3, y + 3, x + cell - 3, y + cell - 3, outline="#ffffff", width=1)

    def _draw_polar(self) -> None:
        canvas = self.canvases["polar"]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 160)
        matrix = self._current_matrix()
        disp, x_groups, y_groups = self._prepare_plot_matrix(matrix)
        low, high = finite_min_max(disp)
        total_deg = self.data.infer_total_deg()
        n_rows = len(y_groups)
        radius_units = INNER_BLANK_ROWS + n_rows + POLAR_PAD_ROWS
        scale = min((w - 180) / (2 * radius_units), (h - 130) / (2 * radius_units))
        scale = max(4.0, scale)
        cx = w / 2
        cy = h / 2 + 22

        canvas.create_text(20, 22, anchor="w", text=f"Polar RF map - {self._current_matrix_label()}", font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(20, 44, anchor="w", text=f"total_deg inferred: {total_deg:.0f}; radius: {self.polar_radius_var.get()}", fill="#667085")
        canvas.create_oval(
            cx - INNER_BLANK_ROWS * scale,
            cy - INNER_BLANK_ROWS * scale,
            cx + INNER_BLANK_ROWS * scale,
            cy + INNER_BLANK_ROWS * scale,
            fill="#f8fafc",
            outline="#e5e7eb",
        )

        theta_edges = [
            math.radians(90.0 + total_deg / 2.0 - total_deg * i / len(x_groups))
            for i in range(len(x_groups) + 1)
        ]
        if self.polar_radius_var.get() == POLAR_RADIUS_MODES[0]:
            ring_rows = sorted(range(n_rows), key=lambda idx: y_groups[idx][0])
        else:
            ring_rows = list(range(n_rows - 1, -1, -1))

        for ring_idx, display_row in enumerate(ring_rows):
            r_inner = INNER_BLANK_ROWS + ring_idx
            r_outer = INNER_BLANK_ROWS + ring_idx + 1
            for col in range(len(x_groups)):
                value = disp[display_row][col]
                fill = palette_color(value, low, high, self.palette_var.get())
                points = self._polar_cell_points(cx, cy, scale, r_inner, r_outer, theta_edges[col], theta_edges[col + 1])
                canvas.create_polygon(points, fill=fill, outline="")

        outer_r = (INNER_BLANK_ROWS + n_rows) * scale
        canvas.create_oval(cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r, outline="#475467")
        canvas.create_text(cx, cy - outer_r - 18, text="x columns span visual angle", fill="#475467")
        canvas.create_text(cx, cy + outer_r + 22, text="RF values share the 2D map color scale", fill="#475467")
        self._draw_colorbar(canvas, w - 82, cy - min(220, 2 * outer_r) / 2, min(220, 2 * outer_r), low, high, self.palette_var.get(), "")
        self._canvas_layouts["polar"] = {
            "cx": cx,
            "cy": cy,
            "scale": scale,
            "total_deg": total_deg,
            "x_groups": x_groups,
            "y_groups": y_groups,
            "ring_rows": ring_rows,
        }

    def _polar_cell_points(
        self,
        cx: float,
        cy: float,
        scale: float,
        r_inner: float,
        r_outer: float,
        theta_start: float,
        theta_end: float,
    ) -> list[float]:
        n_arc = 16
        points: list[tuple[float, float]] = []
        for i in range(n_arc):
            t = theta_start + (theta_end - theta_start) * i / (n_arc - 1)
            points.append((cx + r_outer * scale * math.cos(t), cy - r_outer * scale * math.sin(t)))
        for i in range(n_arc - 1, -1, -1):
            t = theta_start + (theta_end - theta_start) * i / (n_arc - 1)
            points.append((cx + r_inner * scale * math.cos(t), cy - r_inner * scale * math.sin(t)))
        flat: list[float] = []
        for x, y in points:
            flat.extend((x, y))
        return flat

    def _draw_rgb(self) -> None:
        metrics = self.data.metrics(self.unit_idx.get())
        canvas = self.canvases["rgb"]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 160)
        margin_l, margin_r, margin_t, margin_b = 78, 188, 56, 68
        plot_w = max(10, w - margin_l - margin_r)
        plot_h = max(10, h - margin_t - margin_b)
        total_disp, x_groups, y_groups = self._prepare_plot_matrix(metrics.total)
        delay_disp, _x_groups_delay, _y_groups_delay = self._prepare_plot_matrix(self._delay_matrix_for_time_groups(0.0))
        entropy_disp, _x_groups_entropy, _y_groups_entropy = self._prepare_plot_matrix(metrics.entropy)
        n_rows = len(y_groups)
        cell = max(4.0, min(plot_w / len(x_groups), plot_h / n_rows))
        grid_w = cell * len(x_groups)
        grid_h = cell * n_rows
        x0 = margin_l + (plot_w - grid_w) / 2
        y0 = margin_t + (plot_h - grid_h) / 2
        max_total = max(metrics.max_total, 1.0)
        min_delay, max_delay = self._time_axis_range_ms()
        delay_span = max(max_delay - min_delay, 1.0)

        canvas.create_text(20, 22, anchor="w", text="RGB composite", font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(20, 44, anchor="w", text="R total response; G delay; B temporal entropy", fill="#667085")

        for display_y in range(n_rows):
            y = y0 + display_y * cell
            for group_idx, (x_start, x_end) in enumerate(x_groups):
                total_value = total_disp[display_y][group_idx] or 0.0
                total_norm = clamp(total_value / max_total)
                delay = delay_disp[display_y][group_idx]
                delay_norm = 0.0 if delay is None else clamp((delay - min_delay) / delay_span)
                entropy_norm = clamp(entropy_disp[display_y][group_idx] or 0.0)
                if total_value <= 0:
                    fill = "#edf0f3"
                else:
                    fill = hex_color(
                        (
                            int(round(total_norm * 255)),
                            int(round(delay_norm * 255)),
                            int(round(entropy_norm * 255)),
                        )
                    )
                x = x0 + group_idx * cell
                canvas.create_rectangle(x, y, x + cell, y + cell, fill=fill, outline="#ffffff", width=0)
        self._draw_selection_outline(canvas, x0, y0, cell, x_groups, y_groups)
        self._draw_axes(canvas, x0, y0, cell, grid_w, grid_h, x_groups, y_groups)
        legend_x = min(x0 + grid_w + 34, w - 154)
        legend_y = y0
        for i, (label, color) in enumerate((("R total", "#dc2626"), ("G delay", "#16a34a"), ("B entropy", "#2563eb"))):
            y = legend_y + i * 26
            canvas.create_rectangle(legend_x, y, legend_x + 16, y + 16, fill=color, outline="")
            canvas.create_text(legend_x + 24, y + 8, anchor="w", text=label, fill="#475467")
        self._canvas_layouts["rgb"] = {
            "x0": x0,
            "y0": y0,
            "cell": cell,
            "grid_w": grid_w,
            "grid_h": grid_h,
            "x_groups": x_groups,
            "y_groups": y_groups,
        }

    def _draw_stack(self) -> None:
        canvas = self.canvases["stack"]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 300), max(canvas.winfo_height(), 300)
        canvas.create_text(20, 22, anchor="w", text="Vertical stack", font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(20, 44, anchor="w", text="RF, delay, polar, and RGB shown together with the same orientation/resolution", fill="#667085")
        section_top = 68
        section_gap = 14
        section_h = max(120, (h - section_top - 3 * section_gap - 20) / 4.0)
        self._draw_stack_heatmap(canvas, "RF - " + self._current_matrix_label(), self._current_matrix(), 20, section_top, w - 40, section_h, self.palette_var.get(), None)

        delay_matrix = self._delay_matrix_for_time_groups(self._response_floor())
        self._draw_stack_heatmap(canvas, "Delay", delay_matrix, 20, section_top + section_h + section_gap, w - 40, section_h, "Delay", self._time_axis_range_ms())
        self._draw_stack_polar(canvas, "Polar RF", self._current_matrix(), 20, section_top + 2 * (section_h + section_gap), w - 40, section_h)
        self._draw_stack_rgb(canvas, "RGB composite", 20, section_top + 3 * (section_h + section_gap), w - 40, section_h)

    def _draw_stack_heatmap(
        self,
        canvas: tk.Canvas,
        title: str,
        matrix: list[list[float | None]],
        x: float,
        y: float,
        width: float,
        height: float,
        palette: str,
        fixed_range: tuple[float, float] | None,
    ) -> None:
        disp, x_groups, y_groups = self._prepare_plot_matrix(matrix)
        low, high = fixed_range if fixed_range is not None else finite_min_max(disp)
        label_w = 110
        plot_w = width - label_w - 40
        cell = max(2.0, min(plot_w / len(x_groups), (height - 32) / len(y_groups)))
        x0 = x + label_w
        y0 = y + 26
        canvas.create_text(x, y, anchor="nw", text=title, font=("TkDefaultFont", 11, "bold"), fill="#111827")
        for display_y, row in enumerate(disp):
            for group_idx, value in enumerate(row):
                fill = delay_color(value, low, high) if palette == "Delay" else palette_color(value, low, high, palette)
                canvas.create_rectangle(
                    x0 + group_idx * cell,
                    y0 + display_y * cell,
                    x0 + (group_idx + 1) * cell,
                    y0 + (display_y + 1) * cell,
                    fill=fill,
                    outline="",
                )
        grid_w = cell * len(x_groups)
        grid_h = cell * len(y_groups)
        canvas.create_rectangle(x0, y0, x0 + grid_w, y0 + grid_h, outline="#475467")
        canvas.create_text(x0 + grid_w + 10, y0, anchor="nw", text=f"{high:.1f}", fill="#667085", font=("TkDefaultFont", 8))
        canvas.create_text(x0 + grid_w + 10, y0 + grid_h - 10, anchor="nw", text=f"{low:.1f}", fill="#667085", font=("TkDefaultFont", 8))

    def _draw_stack_polar(
        self,
        canvas: tk.Canvas,
        title: str,
        matrix: list[list[float | None]],
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None:
        disp, x_groups, y_groups = self._prepare_plot_matrix(matrix)
        low, high = finite_min_max(disp)
        total_deg = self.data.infer_total_deg()
        canvas.create_text(x, y, anchor="nw", text=title, font=("TkDefaultFont", 11, "bold"), fill="#111827")
        n_rows = len(y_groups)
        radius_units = INNER_BLANK_ROWS + n_rows + POLAR_PAD_ROWS
        scale = max(3.0, min((width - 130) / (2 * radius_units), (height - 30) / (2 * radius_units)))
        cx = x + width * 0.5
        cy = y + height * 0.54
        theta_edges = [
            math.radians(90.0 + total_deg / 2.0 - total_deg * i / len(x_groups))
            for i in range(len(x_groups) + 1)
        ]
        ring_rows = sorted(range(n_rows), key=lambda idx: y_groups[idx][0]) if self.polar_radius_var.get() == POLAR_RADIUS_MODES[0] else list(range(n_rows - 1, -1, -1))
        for ring_idx, display_row in enumerate(ring_rows):
            for col in range(len(x_groups)):
                points = self._polar_cell_points(cx, cy, scale, INNER_BLANK_ROWS + ring_idx, INNER_BLANK_ROWS + ring_idx + 1, theta_edges[col], theta_edges[col + 1])
                canvas.create_polygon(points, fill=palette_color(disp[display_row][col], low, high, self.palette_var.get()), outline="")
        outer = (INNER_BLANK_ROWS + n_rows) * scale
        canvas.create_oval(cx - outer, cy - outer, cx + outer, cy + outer, outline="#475467")

    def _draw_stack_rgb(self, canvas: tk.Canvas, title: str, x: float, y: float, width: float, height: float) -> None:
        metrics = self.data.metrics(self.unit_idx.get())
        total_disp, x_groups, y_groups = self._prepare_plot_matrix(metrics.total)
        delay_disp, _x_groups_delay, _y_groups_delay = self._prepare_plot_matrix(self._delay_matrix_for_time_groups(0.0))
        entropy_disp, _x_groups_entropy, _y_groups_entropy = self._prepare_plot_matrix(metrics.entropy)
        max_total = max(metrics.max_total, 1.0)
        min_delay, max_delay = self._time_axis_range_ms()
        delay_span = max(max_delay - min_delay, 1.0)
        label_w = 110
        plot_w = width - label_w - 40
        cell = max(2.0, min(plot_w / len(x_groups), (height - 32) / len(y_groups)))
        x0 = x + label_w
        y0 = y + 26
        canvas.create_text(x, y, anchor="nw", text=title, font=("TkDefaultFont", 11, "bold"), fill="#111827")
        for display_y in range(len(y_groups)):
            for group_idx in range(len(x_groups)):
                total_value = total_disp[display_y][group_idx] or 0.0
                if total_value <= 0:
                    fill = "#edf0f3"
                else:
                    delay = delay_disp[display_y][group_idx]
                    fill = hex_color(
                        (
                            int(round(clamp(total_value / max_total) * 255)),
                            int(round((0.0 if delay is None else clamp((delay - min_delay) / delay_span)) * 255)),
                            int(round(clamp(entropy_disp[display_y][group_idx] or 0.0) * 255)),
                        )
                    )
                canvas.create_rectangle(x0 + group_idx * cell, y0 + display_y * cell, x0 + (group_idx + 1) * cell, y0 + (display_y + 1) * cell, fill=fill, outline="")
        canvas.create_rectangle(x0, y0, x0 + cell * len(x_groups), y0 + cell * len(y_groups), outline="#475467")

    def _max_time_group_cell_count(self, unit_idx: int, time_groups: list[AxisGroup]) -> float:
        unit = self.data.counts[unit_idx]
        high = 0.0
        for y_idx in range(self.data.n_y):
            for x_idx in range(self.data.n_x):
                hist = [float(v) for v in unit[y_idx][x_idx]]
                for start, end in time_groups:
                    high = max(high, float(sum(hist[start : end + 1])))
        return max(high, 1.0)

    def _timeline_mini_layout(
        self,
        canvas: tk.Canvas,
        width: float,
        height: float,
        mini_top: float,
        visible_count: int,
        x_count: int,
        y_count: int,
    ) -> dict[str, float | int]:
        try:
            screen_w = float(canvas.winfo_screenwidth())
            screen_h = float(canvas.winfo_screenheight())
        except tk.TclError:
            screen_w, screen_h = width, height
        try:
            window = canvas.winfo_toplevel()
            window_w = float(window.winfo_width())
            window_h = float(window.winfo_height())
        except tk.TclError:
            window_w, window_h = width, height

        count = max(1, int(visible_count))
        x_count = max(1, int(x_count))
        y_count = max(1, int(y_count))
        gap_x = max(1.0, min(3.0, width * 0.002))
        gap_y = max(2.0, min(4.0, height * 0.004))
        left = 44.0
        right_pad = 44.0
        available_w = max(120.0, width - left - right_pad)
        target_grid_h = min(78.0, max(44.0, min(screen_h * 0.085, window_h * 0.12)))
        target_cell = target_grid_h / y_count
        target_grid_w = target_cell * x_count
        max_cols_by_width = max(1, int((available_w + gap_x) // max(1.0, target_grid_w + gap_x)))
        max_cols_by_screen = max(1, int((min(screen_w, window_w, width) - left - right_pad + gap_x) // max(1.0, target_grid_w + gap_x)))
        cols = min(count, max(1, min(max_cols_by_width, max_cols_by_screen)))
        slot_w = max(1.0, (available_w - (cols - 1) * gap_x) / cols)
        cell = min(target_cell, slot_w / x_count)
        cell = max(2.0, cell)
        grid_w = cell * x_count
        grid_h = cell * y_count
        row_step = grid_h + 13.0 + gap_y
        rows = int(math.ceil(count / cols))
        return {
            "left": left,
            "cols": cols,
            "rows": rows,
            "gap_x": gap_x,
            "gap_y": gap_y,
            "slot_w": slot_w,
            "cell": cell,
            "grid_w": grid_w,
            "grid_h": grid_h,
            "row_step": row_step,
        }

    def _draw_timeline(self) -> None:
        canvas = self.canvases["timeline"]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 300), max(canvas.winfo_height(), 280)
        unit_idx = self.unit_idx.get()
        metrics = self.data.metrics(unit_idx)
        time_groups = self._time_groups()
        display_bins = len(time_groups)
        visible_bins = self._visible_timeline_bins(display_bins)
        time_totals = [float(sum(metrics.bin_totals[start : end + 1])) for start, end in time_groups]
        visible_note = f"{display_bins} bin maps" if len(visible_bins) == display_bins else f"{len(visible_bins)} selected bin maps"
        canvas.create_text(20, 22, anchor="w", text=f"Timeline and {visible_note}", font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(
            20,
            44,
            anchor="w",
            text=(
                f"Selected bin: {self._time_group_label(self.bin_var.get())}; "
                f"plot range {self._display_range_label()}; "
                f"time res {format_ms(self._time_group_size() * self._base_bin_ms())} ms"
            ),
            fill="#667085",
        )

        chart_x, chart_y = 64, 78
        chart_w = max(320, w - 140)
        chart_h = 62
        max_total = max(max(time_totals), 1.0)
        canvas.create_rectangle(chart_x, chart_y, chart_x + chart_w, chart_y + chart_h, outline="#cbd5e1")
        points: list[float] = []
        for bin_idx, value in enumerate(time_totals):
            x = chart_x + chart_w * (bin_idx + 0.5) / display_bins
            y = chart_y + chart_h - chart_h * value / max_total
            points.extend((x, y))
        if len(points) >= 4:
            canvas.create_line(*points, fill="#2563eb", width=2, smooth=True)
        selected_max = 0.0
        if self.selected_cell is not None:
            y_start, y_end, x_start, x_end = self.selected_cell
            selected_hist = self._time_grouped_hist(self._group_hist(y_start, y_end, x_start, x_end))
            selected_max = max(max(selected_hist), 1.0)
            selected_points: list[float] = []
            for bin_idx, value in enumerate(selected_hist):
                x = chart_x + chart_w * (bin_idx + 0.5) / display_bins
                y = chart_y + chart_h - chart_h * value / selected_max
                selected_points.extend((x, y))
            if len(selected_points) >= 4:
                canvas.create_line(*selected_points, fill="#dc2626", width=1.8, smooth=True)
        red_axis_x = chart_x - 20
        blue_axis_x = chart_x + chart_w + 20
        axis_font = ("TkDefaultFont", 8)
        if self.selected_cell is not None:
            canvas.create_line(red_axis_x, chart_y, red_axis_x, chart_y + chart_h, fill="#dc2626", width=1)
            canvas.create_line(red_axis_x - 4, chart_y, red_axis_x, chart_y, fill="#dc2626")
            canvas.create_line(red_axis_x - 4, chart_y + chart_h, red_axis_x, chart_y + chart_h, fill="#dc2626")
            canvas.create_text(red_axis_x - 7, chart_y, anchor="e", text=f"{selected_max:.0f}", fill="#dc2626", font=axis_font)
            canvas.create_text(red_axis_x - 7, chart_y + chart_h, anchor="e", text="0", fill="#dc2626", font=axis_font)
        canvas.create_line(blue_axis_x, chart_y, blue_axis_x, chart_y + chart_h, fill="#2563eb", width=1)
        canvas.create_line(blue_axis_x, chart_y, blue_axis_x + 4, chart_y, fill="#2563eb")
        canvas.create_line(blue_axis_x, chart_y + chart_h, blue_axis_x + 4, chart_y + chart_h, fill="#2563eb")
        canvas.create_text(blue_axis_x + 7, chart_y, anchor="w", text=f"{max_total:.0f}", fill="#2563eb", font=axis_font)
        canvas.create_text(blue_axis_x + 7, chart_y + chart_h, anchor="w", text="0", fill="#2563eb", font=axis_font)
        bin_w = chart_w / display_bins
        if self._has_time_selection():
            selected_x = chart_x + self.bin_var.get() * bin_w
            if self.mode_var.get() == "Bin":
                canvas.create_rectangle(selected_x, chart_y, selected_x + bin_w, chart_y + chart_h, outline="#f97316", width=2)
            if self.mode_var.get() == "Range sum" or (self.mode_var.get() == "Total" and not self._is_full_display_range()):
                start, end = self._display_range_indices()
                range_x0 = chart_x + start * bin_w
                range_x1 = chart_x + (end + 1) * bin_w
                canvas.create_rectangle(range_x0, chart_y, range_x1, chart_y + chart_h, outline="#16a34a", width=1)
        axis_start_ms, axis_end_ms = self._time_axis_range_ms()
        canvas.create_text(chart_x, chart_y + chart_h + 18, anchor="w", text=f"{format_ms(axis_start_ms)} ms", fill="#475467")
        if display_bins > 1:
            canvas.create_text(chart_x + bin_w, chart_y + chart_h + 18, anchor="center", text=self._time_group_end_label(0), fill="#475467")
        canvas.create_text(chart_x + chart_w, chart_y + chart_h + 18, anchor="e", text=f"{format_ms(axis_end_ms)} ms", fill="#475467")

        mini_top = chart_y + chart_h + 30
        low = 0.0
        high = self._max_time_group_cell_count(unit_idx, time_groups)
        preview_x_groups = self._x_groups()
        preview_y_groups = self._display_y_groups()
        mini_layout = self._timeline_mini_layout(canvas, w, h, mini_top, len(visible_bins), len(preview_x_groups), len(preview_y_groups))
        cols = int(mini_layout["cols"])
        rows = int(mini_layout["rows"])
        gap_x = float(mini_layout["gap_x"])
        gap_y = float(mini_layout["gap_y"])
        slot_w = float(mini_layout["slot_w"])
        preview_cell = float(mini_layout["cell"])
        preview_grid_w = float(mini_layout["grid_w"])
        preview_grid_h = float(mini_layout["grid_h"])
        row_step = float(mini_layout["row_step"])
        mini_left = float(mini_layout["left"])
        self._canvas_layouts["timeline"] = {
            "chart_x": chart_x,
            "chart_y": chart_y,
            "chart_w": chart_w,
            "chart_h": chart_h,
            "mini_top": mini_top,
            "mini_w": slot_w,
            "mini_h": preview_grid_h,
            "mini_left": mini_left,
            "gap_x": gap_x,
            "gap_y": gap_y,
            "row_step": row_step,
            "cols": cols,
            "display_bins": display_bins,
            "visible_bins": visible_bins,
        }
        self._timeline_cells = []

        for visible_idx, bin_idx in enumerate(visible_bins):
            source_start, source_end = time_groups[bin_idx]
            row = visible_idx // cols
            col = visible_idx % cols
            slot_x = mini_left + col * (slot_w + gap_x)
            x0 = slot_x + max(0.0, (slot_w - preview_grid_w) / 2.0)
            y0 = mini_top + row * row_step
            cell = preview_cell
            grid_w = preview_grid_w
            grid_h = preview_grid_h
            matrix = self.data.aggregate_matrix(unit_idx, "Range sum", 0, source_start, source_end)
            disp, x_groups, y_groups = self._prepare_plot_matrix(matrix, smooth=True)
            self._timeline_cells.append(
                {
                    "bin_idx": bin_idx,
                    "source_start": source_start,
                    "source_end": source_end,
                    "x0": x0,
                    "y0": y0,
                    "cell": cell,
                    "grid_w": grid_w,
                    "grid_h": grid_h,
                    "x_groups": x_groups,
                    "y_groups": y_groups,
                }
            )
            for display_y, values in enumerate(disp):
                for group_idx, value in enumerate(values):
                    fill = palette_color(value, low, high, self.palette_var.get())
                    x = x0 + group_idx * cell
                    y = y0 + display_y * cell
                    canvas.create_rectangle(x, y, x + cell, y + cell, fill=fill, outline="", width=0)
            outline = "#f97316" if bin_idx == self.bin_var.get() else "#cbd5e1"
            width_line = 2 if bin_idx == self.bin_var.get() else 1
            canvas.create_rectangle(x0, y0, x0 + grid_w, y0 + grid_h, outline=outline, width=width_line)
            canvas.create_text(x0, y0 + grid_h + 11, anchor="w", text=self._time_group_end_label(bin_idx), fill="#475467", font=("TkDefaultFont", 8))
        content_bottom = mini_top + max(0, rows - 1) * row_step + preview_grid_h + 28
        last_col_count = min(cols, len(visible_bins))
        content_right = max(w, blue_axis_x + 54, mini_left + last_col_count * slot_w + max(0, last_col_count - 1) * gap_x + 44)
        canvas.configure(scrollregion=(0, 0, content_right, max(h, content_bottom)))
        self._restore_timeline_scroll()

    def _canvas_to_cell(self, key: str, event: tk.Event) -> CellRef | None:
        layout = self._canvas_layouts.get(key)
        if not layout or "cell" not in layout:
            return None
        x0 = layout["x0"]
        y0 = layout["y0"]
        cell = layout["cell"]
        grid_w = layout["grid_w"]
        grid_h = layout["grid_h"]
        if not (x0 <= event.x < x0 + grid_w and y0 <= event.y < y0 + grid_h):
            return None
        group_idx = int((event.x - x0) // cell)
        display_y = int((event.y - y0) // cell)
        x_groups = layout.get("x_groups") or self._x_groups()
        y_groups = layout.get("y_groups") or self._display_y_groups()
        if not (0 <= group_idx < len(x_groups) and 0 <= display_y < len(y_groups)):
            return None
        y_start, y_end = y_groups[display_y]
        x_start, x_end = x_groups[group_idx]
        return y_start, y_end, x_start, x_end

    def _timeline_bin_at(self, event: tk.Event) -> int | None:
        layout = self._canvas_layouts.get("timeline")
        if not layout:
            return None
        canvas = self.canvases["timeline"]
        event_x = canvas.canvasx(event.x)
        event_y = canvas.canvasy(event.y)
        chart_x = layout.get("chart_x")
        chart_y = layout.get("chart_y")
        chart_w = layout.get("chart_w")
        chart_h = layout.get("chart_h")
        if (
            chart_x is not None
            and chart_y is not None
            and chart_w is not None
            and chart_h is not None
            and float(chart_x) <= event_x <= float(chart_x) + float(chart_w)
            and float(chart_y) <= event_y <= float(chart_y) + float(chart_h)
        ):
            display_bins = int(layout.get("display_bins", self._time_group_count()))
            bin_idx = int((event_x - float(chart_x)) / (float(chart_w) / display_bins))
            return max(0, min(display_bins - 1, bin_idx))
        for cell_layout in self._timeline_cells:
            x0 = float(cell_layout["x0"])
            y0 = float(cell_layout["y0"])
            grid_w = float(cell_layout["grid_w"])
            grid_h = float(cell_layout["grid_h"])
            if x0 <= event_x <= x0 + grid_w and y0 <= event_y <= y0 + grid_h + 13:
                return int(cell_layout["bin_idx"])
        return None

    def _timeline_cell_at(self, event: tk.Event) -> tuple[int, CellRef] | None:
        canvas = self.canvases["timeline"]
        event_x = canvas.canvasx(event.x)
        event_y = canvas.canvasy(event.y)
        for layout in self._timeline_cells:
            x0 = float(layout["x0"])
            y0 = float(layout["y0"])
            cell = float(layout["cell"])
            grid_w = float(layout["grid_w"])
            grid_h = float(layout["grid_h"])
            if not (x0 <= event_x < x0 + grid_w and y0 <= event_y < y0 + grid_h):
                continue
            group_idx = int((event_x - x0) // cell)
            display_y = int((event_y - y0) // cell)
            x_groups = layout.get("x_groups") or self._x_groups()
            y_groups = layout.get("y_groups") or self._display_y_groups()
            if 0 <= group_idx < len(x_groups) and 0 <= display_y < len(y_groups):
                y_start, y_end = y_groups[display_y]
                x_start, x_end = x_groups[group_idx]
                return int(layout["bin_idx"]), (y_start, y_end, x_start, x_end)
        return None

    def _polar_cell_at(self, event: tk.Event) -> tuple[int, CellRef] | None:
        layout = self._canvas_layouts.get("polar")
        if not layout:
            return None
        cx = layout["cx"]
        cy = layout["cy"]
        scale = layout["scale"]
        total_deg = layout["total_deg"]
        x_groups = layout.get("x_groups") or self._x_groups()
        y_groups = layout.get("y_groups") or self._display_y_groups()
        ring_rows = layout.get("ring_rows")
        if not isinstance(ring_rows, list):
            ring_rows = list(range(len(y_groups) - 1, -1, -1))
        dx = (event.x - cx) / scale
        dy = (cy - event.y) / scale
        radius = math.hypot(dx, dy)
        if not (INNER_BLANK_ROWS <= radius < INNER_BLANK_ROWS + len(y_groups)):
            return None
        ring_idx = int(math.floor(radius - INNER_BLANK_ROWS))
        if not (0 <= ring_idx < len(ring_rows)):
            return None
        display_row = int(ring_rows[ring_idx])
        theta_deg = math.degrees(math.atan2(dy, dx))
        start = 90.0 + total_deg / 2.0
        if total_deg >= 359.999:
            rel = (start - theta_deg) % 360.0
            col = int(rel / (total_deg / len(x_groups)))
        else:
            end = 90.0 - total_deg / 2.0
            while theta_deg > start:
                theta_deg -= 360.0
            while theta_deg < end:
                theta_deg += 360.0
            if not (end <= theta_deg <= start):
                return None
            col = int((start - theta_deg) / (total_deg / len(x_groups)))
        col = max(0, min(len(x_groups) - 1, col))
        y_start, y_end = y_groups[display_row]
        x_start, x_end = x_groups[col]
        return ring_idx, (y_start, y_end, x_start, x_end)

    def _on_canvas_motion(self, key: str, event: tk.Event) -> None:
        if key in {"rf", "delay", "rgb"}:
            cell = self._canvas_to_cell(key, event)
            if cell is not None:
                self._set_hover_cell(key, cell, event)
            else:
                self._clear_canvas_hover(key)
        elif key == "polar":
            polar_cell = self._polar_cell_at(event)
            if polar_cell is not None:
                ring_idx, cell = polar_cell
                self._set_hover_cell(key, cell, event, extra=f"polar ring {ring_idx + 1}")
            else:
                self._clear_canvas_hover(key)
        elif key == "timeline":
            cell = self._timeline_cell_at(event)
            if cell is not None:
                bin_idx, cell_ref = cell
                self._set_hover_cell(key, cell_ref, event, extra=f"timeline bin {self._time_group_label(bin_idx)}")
            else:
                bin_idx = self._timeline_bin_at(event)
                if bin_idx is not None:
                    self.status_label.configure(text=f"Hover bin {self._time_group_label(bin_idx)}")
                self._clear_canvas_hover(key, keep_status=bin_idx is not None)

    def _on_canvas_click(self, key: str, event: tk.Event) -> None:
        if key in {"rf", "delay", "rgb"}:
            cell = self._canvas_to_cell(key, event)
            if cell is not None:
                self.selected_cell = cell
                self._update_all()
        elif key == "polar":
            polar_cell = self._polar_cell_at(event)
            if polar_cell is not None:
                _ring_idx, cell = polar_cell
                self.selected_cell = cell
                self._update_all()
        elif key == "timeline":
            timeline_cell = self._timeline_cell_at(event)
            if timeline_cell is not None:
                bin_idx, cell = timeline_cell
                self.selected_cell = cell
            else:
                bin_idx = self._timeline_bin_at(event)
            if bin_idx is not None:
                self._select_timeline_bin(bin_idx, event)
                self._update_all()

    def _select_timeline_bin(self, bin_idx: int, event: tk.Event) -> None:
        if self._event_has_range_modifier(event):
            if self._timeline_range_anchor is None:
                self._timeline_range_anchor = self.bin_var.get()
            start = min(self._timeline_range_anchor, bin_idx)
            end = max(self._timeline_range_anchor, bin_idx)
            self.range_start_var.set(start)
            self.range_end_var.set(end)
            self.bin_var.set(bin_idx)
            self._set_bin_scale_silently(bin_idx)
            self.mode_var.set("Range sum")
            self._timeline_range_anchor = bin_idx
        else:
            self._timeline_range_anchor = bin_idx
            self.bin_var.set(bin_idx)
            self._set_bin_scale_silently(bin_idx)
            self.mode_var.set("Bin")

    def _event_has_range_modifier(self, event: tk.Event) -> bool:
        state = int(getattr(event, "state", 0) or 0)
        # Tk uses platform-dependent modifier bits. Include Shift, Control,
        # Option/Alt, Command/Meta candidates so the behavior works on macOS.
        modifier_mask = 0x100000 | 0x0001 | 0x0004 | 0x0008 | 0x0010 | 0x0020 | 0x0040 | 0x0080
        return bool(state & modifier_mask)

    def _clear_hover(self) -> None:
        for key in self.canvases:
            self._clear_canvas_hover(key, keep_status=True)
        self.hover_cell = None
        self._update_cell_label(cell=self.selected_cell)
        self.status_label.configure(
            text=(
                f"x: {format_pos(self.data.x_positions[0])}..{format_pos(self.data.x_positions[-1])}  "
                f"y: {format_pos(self.data.y_positions[0])}..{format_pos(self.data.y_positions[-1])}  "
                f"time: {format_ms(self._time_axis_start_ms())}..{format_ms(self._time_axis_end_ms())} ms"
            )
        )

    def _set_hover_cell(
        self,
        key: str,
        cell: CellRef,
        event: tk.Event,
        polygon: tuple[tuple[float, float], ...] | None = None,
        extra: str = "",
    ) -> None:
        self.hover_cell = cell
        y_start, y_end, x_idx, x_end = cell
        self.status_label.configure(
            text=(
                f"Hover {extra + '; ' if extra else ''}"
                f"{self._y_group_text(y_start, y_end)}, {self._x_group_text(x_idx, x_end)}"
            )
        )
        self._update_cell_label(cell=cell, prefix="Hover\n")
        self._draw_hover_overlay(key, cell, event, polygon=polygon)

    def _clear_canvas_hover(self, key: str, keep_status: bool = False) -> None:
        canvas = self.canvases.get(key)
        if canvas is not None:
            canvas.delete("hover")
        self.hover_cell = None
        self._update_cell_label(cell=self.selected_cell)

    def _draw_hover_overlay(
        self,
        key: str,
        cell: CellRef,
        event: tk.Event,
        polygon: tuple[tuple[float, float], ...] | None = None,
    ) -> None:
        canvas = self.canvases[key]
        canvas.delete("hover")
        y_start, _y_end, x_idx, _x_end = cell
        if polygon is not None:
            coords: list[float] = []
            for x, y in polygon:
                coords.extend((x, y))
            canvas.create_polygon(*coords, fill="", outline="#f97316", width=3, tags="hover")
        elif key in {"rf", "delay", "rgb"}:
            layout = self._canvas_layouts.get(key)
            if layout:
                y_groups = layout.get("y_groups") or self._display_y_groups()
                display_y = next((idx for idx, (start, end) in enumerate(y_groups) if start <= y_start <= end), None)
                if display_y is not None:
                    x_groups = layout.get("x_groups") or self._x_groups()
                    group_idx = next((idx for idx, (start, end) in enumerate(x_groups) if start <= x_idx <= end), 0)
                    x0 = layout["x0"]
                    y0 = layout["y0"]
                    cell_size = layout["cell"]
                    x = x0 + group_idx * cell_size
                    y = y0 + display_y * cell_size
                    canvas.create_rectangle(x + 1, y + 1, x + cell_size - 1, y + cell_size - 1, outline="#f97316", width=3, tags="hover")
        elif key == "polar":
            polar = self._polar_cell_at(event)
            layout = self._canvas_layouts.get("polar")
            if polar is not None and layout:
                ring_idx, polar_cell = polar
                _y_start, _y_end, x_start, _x_end = polar_cell
                x_groups = layout.get("x_groups") or self._x_groups()
                col = next((idx for idx, (start, end) in enumerate(x_groups) if start <= x_start <= end), 0)
                total_deg = layout["total_deg"]
                theta_edges = [
                    math.radians(90.0 + total_deg / 2.0 - total_deg * i / len(x_groups))
                    for i in range(len(x_groups) + 1)
                ]
                points = self._polar_cell_points(
                    layout["cx"],
                    layout["cy"],
                    layout["scale"],
                    INNER_BLANK_ROWS + ring_idx,
                    INNER_BLANK_ROWS + ring_idx + 1,
                    theta_edges[col],
                    theta_edges[col + 1],
                )
                canvas.create_polygon(points, fill="", outline="#f97316", width=3, tags="hover")
        elif key == "timeline":
            timeline = self._timeline_cell_at(event)
            if timeline is not None:
                bin_idx, timeline_cell = timeline
                y_start_t, _y_end_t, x_idx_t, _x_end_t = timeline_cell
                for layout in self._timeline_cells:
                    if int(layout["bin_idx"]) != bin_idx:
                        continue
                    y_groups = layout.get("y_groups") or self._display_y_groups()
                    display_y = next((idx for idx, (start, end) in enumerate(y_groups) if start <= y_start_t <= end), 0)
                    x_groups = layout.get("x_groups") or self._x_groups()
                    group_idx = next((idx for idx, (start, end) in enumerate(x_groups) if start <= x_idx_t <= end), 0)
                    x0 = float(layout["x0"])
                    y0 = float(layout["y0"])
                    cell_size = float(layout["cell"])
                    x = x0 + group_idx * cell_size
                    y = y0 + display_y * cell_size
                    canvas.create_rectangle(x, y, x + cell_size, y + cell_size, outline="#f97316", width=2, tags="hover")
                    break
        self._draw_canvas_tooltip(canvas, cell, event)

    def _draw_canvas_tooltip(self, canvas: tk.Canvas, cell: CellRef, event: tk.Event) -> None:
        y_start, y_end, x_idx, x_end = cell
        unit_idx = self.unit_idx.get()
        metrics = self.data.metrics(unit_idx)
        hist = self._group_hist(y_start, y_end, x_idx, x_end)
        display_hist = self._time_grouped_hist(hist)
        bin_idx = self.bin_var.get()
        if sum(display_hist) > 0:
            peak_bin = max(range(len(display_hist)), key=lambda i: display_hist[i])
            delay = self._time_group_center_ms(peak_bin)
        else:
            delay = None
        lines = [
            self._y_group_text(y_start, y_end),
            self._x_group_text(x_idx, x_end),
            f"bin {bin_idx + 1}: {float(display_hist[bin_idx]):.0f}",
            f"total {self._group_total(metrics.total, y_start, y_end, x_idx, x_end):.0f}",
            f"delay {delay:.1f} ms" if delay is not None else "delay n/a",
        ]
        text = "\n".join(lines)
        pad = 8
        event_x = canvas.canvasx(event.x)
        event_y = canvas.canvasy(event.y)
        x = event_x + 14
        y = event_y + 14
        width = 190
        height = 22 + 15 * (len(lines) - 1) + pad
        canvas_w = canvas.winfo_width()
        canvas_h = canvas.winfo_height()
        view_left = canvas.canvasx(0)
        view_right = canvas.canvasx(canvas_w)
        view_top = canvas.canvasy(0)
        view_bottom = canvas.canvasy(canvas_h)
        if x + width > view_right - 8:
            x = event_x - width - 14
        if x < view_left + 8:
            x = view_left + 8
        if y + height > view_bottom - 8:
            y = event_y - height - 14
        if y < view_top + 8:
            y = view_top + 8
        canvas.create_rectangle(x, y, x + width, y + height, fill="#111827", outline="#111827", tags="hover")
        canvas.create_text(x + pad, y + pad, anchor="nw", text=text, fill="#f8fafc", font=("TkDefaultFont", 9), tags="hover")

    def _response_floor(self) -> float:
        try:
            return max(0.0, float(self.response_floor_var.get()))
        except (tk.TclError, ValueError):
            self.response_floor_var.set(0.0)
            return 0.0

    def _open_json(self) -> None:
        path = filedialog.askopenfilename(
            title="Open RF mapping JSON",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        self._load_json_path(Path(path))

    def _load_json_path(self, path: Path) -> None:
        try:
            self.data = RFMappingData(path)
        except Exception as exc:
            messagebox.showerror("Could not load JSON", str(exc))
            return
        self.unit_idx.set(0)
        self.bin_var.set(0)
        self.range_start_var.set(0)
        self.time_res_ms_var.set(format_ms(self._base_bin_ms()))
        self.range_end_var.set(self._time_group_count() - 1)
        self.selected_cell = None
        self.hover_cell = None
        self._timeline_range_anchor = None
        self._timeline_scroll_fraction = 0.0
        self.mode_var.set("Total")
        self.bin_scale.configure(to=self._time_group_count() - 1)
        self._set_bin_scale_silently(0)
        self.range_start_spin.configure(to=self._time_group_count() - 1)
        self.range_end_spin.configure(to=self._time_group_count() - 1)
        self.time_res_spin.configure(from_=self._base_bin_ms(), to=self._total_time_ms(), increment=self._base_bin_ms())
        self.x_bins_var.set(self.data.n_x)
        self.y_bins_var.set(self.data.n_y)
        self.x_bins_spin.configure(to=self.data.n_x)
        self.y_bins_spin.configure(to=self.data.n_y)
        self._sync_json_combo()
        self._sync_unit_combo()
        self._update_all()

    def _export_current_matrix(self) -> None:
        matrix = self._current_matrix()
        path = filedialog.asksaveasfilename(
            title="Export current RF matrix",
            defaultextension=".csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
            initialfile=f"unit_{self.unit_idx.get():03d}_cluster_{self.data.cluster_id(self.unit_idx.get())}_{self.mode_var.get().lower().replace(' ', '_')}.csv",
        )
        if not path:
            return
        try:
            with Path(path).open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "unit_index",
                        "cluster_id",
                        "y_index_0based",
                        "y_index_matlab",
                        "y_position",
                        "x_index_0based",
                        "x_index_matlab",
                        "x_position",
                        "value",
                        "mode",
                    ]
                )
                for y_idx in range(self.data.n_y):
                    for x_idx in range(self.data.n_x):
                        writer.writerow(
                            [
                                self.unit_idx.get(),
                                self.data.cluster_id(self.unit_idx.get()),
                                y_idx,
                                y_idx + 1,
                                self.data.y_positions[y_idx],
                                x_idx,
                                x_idx + 1,
                                self.data.x_positions[x_idx],
                                matrix[y_idx][x_idx],
                                self._current_matrix_label(),
                            ]
                        )
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        messagebox.showinfo("Export complete", f"Wrote {path}")


def run_self_test(path: Path) -> None:
    data = RFMappingData(path)
    assert data.size == (data.n_units, data.n_y, data.n_x, data.n_bins)
    assert len(data.unit_pool) == data.n_units
    assert len(data.time_bin_edges) == data.n_bins + 1
    assert data.display_y_indices(True)[0] == data.n_y - 1
    assert data.display_y_indices(True)[-1] == 0

    unit_idx = 0
    y_idx = 0
    x_idx = 0
    hist = [float(v) for v in data.counts[unit_idx][y_idx][x_idx]]
    metrics = data.metrics(unit_idx)
    assert abs(metrics.total[y_idx][x_idx] - sum(hist)) < 1e-9
    assert abs(metrics.peak[y_idx][x_idx] - (max(hist) if hist else 0.0)) < 1e-9
    if sum(hist) > 0:
        expected_bin = max(range(data.n_bins), key=lambda i: hist[i])
        assert metrics.peak_bin[y_idx][x_idx] == expected_bin
        assert metrics.delay_ms[y_idx][x_idx] == data.bin_center_ms(expected_bin)
    else:
        assert metrics.peak_bin[y_idx][x_idx] is None
        assert metrics.delay_ms[y_idx][x_idx] is None

    total = data.aggregate_matrix(unit_idx, "Total", 0, 0, data.n_bins - 1)
    peak = data.aggregate_matrix(unit_idx, "Peak", 0, 0, data.n_bins - 1)
    one_bin = data.aggregate_matrix(unit_idx, "Bin", 0, 0, data.n_bins - 1)
    range_sum = data.aggregate_matrix(unit_idx, "Range sum", 0, 0, 4)
    assert total[y_idx][x_idx] == sum(hist)
    assert peak[y_idx][x_idx] == (max(hist) if hist else 0.0)
    assert one_bin[y_idx][x_idx] == hist[0]
    assert range_sum[y_idx][x_idx] == sum(hist[:5])
    assert 0.0 <= metrics.entropy[y_idx][x_idx] <= 1.0
    assert abs(data.infer_total_deg() - 360.0) < 1e-6
    print(
        "self-test passed:",
        f"{data.n_units} units, {data.n_y} y, {data.n_x} x, {data.n_bins} bins",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Native GUI viewer for RF mapping JSON data.")
    parser.add_argument(
        "json_path",
        nargs="?",
        default=None,
        help=f"Path to unitsSpikeCounts JSON file. Default: latest JSON in {DEFAULT_JSON_DIR}/",
    )
    parser.add_argument("--self-test", action="store_true", help="Run data/model tests and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    path = latest_json_path() if args.json_path is None else Path(args.json_path).expanduser()
    if not path.exists():
        print(f"JSON file not found: {path}", file=sys.stderr)
        return 2
    if args.self_test:
        run_self_test(path)
        return 0

    try:
        data = RFMappingData(path)
    except Exception as exc:
        print(f"Could not load {path}: {exc}", file=sys.stderr)
        return 1

    if not TK_AVAILABLE:
        print("tkinter is not available in this Python; use a local Python with Tk to launch the GUI.", file=sys.stderr)
        return 1

    app = RFMViewer(data)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
