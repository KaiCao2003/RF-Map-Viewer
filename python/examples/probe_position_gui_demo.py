#!/usr/bin/env python3
"""Standalone interaction demo for spatially filtering RF-mapping units.

This file is intentionally independent from the production viewer.  It reads the
real ``positions.csv`` and ``channels.csv`` exports, while generating lightweight
plot data so the layout and probe-selection interaction can be evaluated without
changing the analysis pipeline.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable, Optional


DEFAULT_DATA_ROOT = Path("/mnt/senzailab/Kai/#Recording/m14/260615/260615_3/data")
BG = "#f3f4f7"
PANEL = "#ffffff"
INK = "#172033"
MUTED = "#687083"
LINE = "#d9dde7"
BLUE = "#3978f6"
BLUE_PALE = "#e7efff"
TEAL = "#20a88a"
ORANGE = "#f28b3c"


def fit_aspect_rect(
    width: float,
    height: float,
    content_width: float,
    content_height: float,
    *,
    padding: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Center a fixed-aspect rectangle inside a padded viewport.

    Returning a letterboxed rectangle keeps spatial geometry stable while the
    surrounding window changes shape (including macOS full-screen transitions).
    Padding is ordered as left, top, right, bottom.
    """
    left_pad, top_pad, right_pad, bottom_pad = padding
    available_width = max(1.0, width - left_pad - right_pad)
    available_height = max(1.0, height - top_pad - bottom_pad)
    safe_content_width = max(1.0, content_width)
    safe_content_height = max(1.0, content_height)
    scale = min(
        available_width / safe_content_width,
        available_height / safe_content_height,
    )
    fitted_width = safe_content_width * scale
    fitted_height = safe_content_height * scale
    left = left_pad + (available_width - fitted_width) / 2.0
    top = top_pad + (available_height - fitted_height) / 2.0
    return left, top, left + fitted_width, top + fitted_height


@dataclass(frozen=True)
class Channel:
    channel_id: int
    x: float
    y: float
    shank: int


@dataclass(frozen=True)
class Unit:
    unit_id: int
    x: float
    y: float


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def load_geometry(data_root: Path, probe: str) -> tuple[list[Channel], list[Unit]]:
    channels_path = data_root / "waveform" / probe / "channels.csv"
    positions_path = data_root / "spike_position" / probe / "positions.csv"
    if not channels_path.is_file() or not positions_path.is_file():
        missing = [str(p) for p in (channels_path, positions_path) if not p.is_file()]
        raise FileNotFoundError("Missing geometry file(s):\n" + "\n".join(missing))

    channels = [
        Channel(
            channel_id=int(float(row["channel_id"])),
            x=float(row["x_um"]),
            y=float(row["y_um"]),
            shank=int(float(row.get("shank_id", 0))),
        )
        for row in _read_rows(channels_path)
    ]
    units = [
        Unit(
            unit_id=int(float(row["unit_id"])),
            x=float(row["x_um"]),
            y=float(row["y_um"]),
        )
        for row in _read_rows(positions_path)
    ]
    if not channels or not units:
        raise ValueError("Probe geometry CSV files must contain channels and units")
    return channels, units


class PlotCanvas(tk.Canvas):
    """Canvas that redraws plot content after resize or state changes."""

    def __init__(self, parent: tk.Misc, draw: Callable[[tk.Canvas, int, int], None], **kwargs: object):
        super().__init__(parent, bg=PANEL, highlightthickness=0, **kwargs)
        self._draw_callback = draw
        self._pending: Optional[str] = None
        self.bind("<Configure>", self._queue_draw)

    def _queue_draw(self, _event: object = None) -> None:
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(30, self.redraw)

    def redraw(self) -> None:
        self._pending = None
        self.delete("all")
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        self._draw_callback(self, width, height)


class ProbeCanvas(tk.Canvas):
    """Four-shank probe view supporting click and drag spatial selection."""

    def __init__(
        self,
        parent: tk.Misc,
        channels: list[Channel],
        units: list[Unit],
        selection_changed: Callable[[Optional[tuple[float, float, float, float]], str], None],
    ) -> None:
        super().__init__(parent, bg=PANEL, highlightthickness=0, cursor="crosshair")
        self.channels = channels
        self.units = units
        self.selection_changed = selection_changed
        self.selection: Optional[tuple[float, float, float, float]] = None
        self.selected_unit_id: Optional[int] = None
        self.filtered_ids: set[int] = set()
        self._drag_start: Optional[tuple[float, float]] = None
        self._drag_now: Optional[tuple[float, float]] = None
        self._layout = (20.0, 14.0, 1.0, 1.0)
        self._bounds = self._find_bounds()
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)

    def _find_bounds(self) -> tuple[float, float, float, float]:
        xs = [c.x for c in self.channels] + [u.x for u in self.units]
        ys = [c.y for c in self.channels] + [u.y for u in self.units]
        return min(xs) - 35, max(xs) + 35, min(ys) - 35, max(ys) + 35

    def _compute_layout(self) -> None:
        x0, x1, y0, y1 = self._bounds
        left, top, right, bottom = fit_aspect_rect(
            self.winfo_width(),
            self.winfo_height(),
            x1 - x0,
            y1 - y0,
            padding=(18.0, 16.0, 18.0, 16.0),
        )
        scale = min((right - left) / (x1 - x0), (bottom - top) / (y1 - y0))
        self._layout = left, top, scale, scale

    def _screen(self, x: float, y: float) -> tuple[float, float]:
        x0, _x1, y0, _y1 = self._bounds
        pad_x, pad_y, sx, sy = self._layout
        return pad_x + (x - x0) * sx, self.winfo_height() - pad_y - (y - y0) * sy

    def _data(self, sx0: float, sy0: float) -> tuple[float, float]:
        x0, _x1, y0, _y1 = self._bounds
        pad_x, pad_y, sx, sy = self._layout
        return x0 + (sx0 - pad_x) / sx, y0 + (self.winfo_height() - pad_y - sy0) / sy

    def set_selected_unit(self, unit_id: Optional[int]) -> None:
        self.selected_unit_id = unit_id
        self.redraw()

    def set_selection(self, region: Optional[tuple[float, float, float, float]], reason: str) -> None:
        self.selection = region
        if region is None:
            self.filtered_ids.clear()
        else:
            xa, xb, ya, yb = region
            self.filtered_ids = {u.unit_id for u in self.units if xa <= u.x <= xb and ya <= u.y <= yb}
        self.redraw()
        self.selection_changed(region, reason)

    def select_demo_region(self) -> None:
        center = min(self.channels, key=lambda c: abs(c.y - 1220) + abs(c.x - 516))
        self.set_selection((center.x - 80, center.x + 80, center.y - 37.5, center.y + 37.5), "channel")

    def _press(self, event: tk.Event) -> None:
        self.focus_set()
        self._drag_start = (event.x, event.y)
        self._drag_now = self._drag_start

    def _motion(self, event: tk.Event) -> None:
        if self._drag_start is not None:
            self._drag_now = (event.x, event.y)
            self.redraw()

    def _release(self, event: tk.Event) -> None:
        if self._drag_start is None:
            return
        start = self._drag_start
        self._drag_start = self._drag_now = None
        distance = math.hypot(event.x - start[0], event.y - start[1])
        if distance < 6:
            px, py = event.x, event.y
            channel = min(self.channels, key=lambda c: math.hypot(*(a - b for a, b in zip(self._screen(c.x, c.y), (px, py)))))
            self.set_selection((channel.x - 80, channel.x + 80, channel.y - 37.5, channel.y + 37.5), "channel")
        else:
            ax, ay = self._data(*start)
            bx, by = self._data(event.x, event.y)
            self.set_selection((min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)), "box")

    def redraw(self) -> None:
        self.delete("all")
        self._compute_layout()
        by_shank: dict[int, list[Channel]] = {}
        for channel in self.channels:
            by_shank.setdefault(channel.shank, []).append(channel)

        for shank, items in sorted(by_shank.items()):
            xs = [c.x for c in items]
            ys = [c.y for c in items]
            left, top = self._screen(min(xs) - 16, max(ys) + 18)
            right, bottom = self._screen(max(xs) + 16, min(ys) - 18)
            self.create_rectangle(left, top, right, bottom, fill="#f7f8fb", outline="#e1e5ed", width=1)
            self.create_text(
                (left + right) / 2,
                max(8.0, top - 8.0),
                text=f"S{shank + 1}",
                fill=MUTED,
                font=("TkDefaultFont", 9, "bold"),
            )

        if self.selection:
            xa, xb, ya, yb = self.selection
            left, bottom = self._screen(xa, ya)
            right, top = self._screen(xb, yb)
            self.create_rectangle(left, top, right, bottom, fill=BLUE_PALE, outline=BLUE, width=2, stipple="gray25")

        for channel in self.channels:
            x, y = self._screen(channel.x, channel.y)
            self.create_oval(x - 2.2, y - 2.2, x + 2.2, y + 2.2, fill="#9ea7ba", outline="")

        for unit in self.units:
            x, y = self._screen(unit.x, unit.y)
            inside = unit.unit_id in self.filtered_ids
            selected = unit.unit_id == self.selected_unit_id
            if selected:
                self.create_oval(x - 7, y - 7, x + 7, y + 7, fill="#ffffff", outline=ORANGE, width=2)
            radius = 4.3 if inside else 3.0
            fill = ORANGE if selected else (TEAL if inside else "#5065a8")
            self.create_oval(x - radius, y - radius, x + radius, y + radius, fill=fill, outline="#ffffff", width=1)

        if self._drag_start and self._drag_now:
            ax, ay = self._drag_start
            bx, by = self._drag_now
            self.create_rectangle(ax, ay, bx, by, fill=BLUE_PALE, outline=BLUE, width=2, stipple="gray25")


class RFViewer(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        channels: list[Channel],
        units: list[Unit],
        probe: str,
        source_label: str = "Live geometry preview",
        json_units: Optional[set[int]] = None,
        start_filtered: bool = False,
    ) -> None:
        super().__init__(parent, style="App.TFrame")
        self.channels = channels
        self.all_units = [u for u in units if json_units is None or u.unit_id in json_units]
        self.probe = probe
        self.source_label = source_label
        self.filtered_units = list(self.all_units)
        self.current_unit: Optional[Unit] = self.all_units[0] if self.all_units else None
        self.selection_region: Optional[tuple[float, float, float, float]] = None
        self.controls_visible = tk.BooleanVar(value=True)
        self.metric = tk.StringVar(value="Peak response")
        self.palette = tk.StringVar(value="Viridis")
        self.smoothing = tk.DoubleVar(value=0.7)
        self.grid_lines = tk.BooleanVar(value=False)
        self._build()
        if start_filtered:
            self.after_idle(self.probe_canvas.select_demo_region)
        else:
            self.after_idle(self._refresh_plots)

    def _build(self) -> None:
        self.pack(fill="both", expand=True)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, style="Panel.TFrame", padding=(16, 16, 16, 14), width=294)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 1))
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(3, weight=1)
        ttk.Label(sidebar, text=f"{self.probe} · Spatial filter", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(sidebar, text="Click a channel for a 160 × 75 µm neighborhood,\nor drag to select any region.", style="Hint.TLabel", justify="left").grid(row=1, column=0, sticky="ew", pady=(5, 10))

        legend = ttk.Frame(sidebar, style="Panel.TFrame")
        legend.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._legend_item(legend, 0, "#9ea7ba", "channel")
        self._legend_item(legend, 1, "#5065a8", "unit")
        self._legend_item(legend, 2, TEAL, "in selection")

        self.probe_canvas = ProbeCanvas(sidebar, self.channels, self.all_units, self._selection_changed)
        self.probe_canvas.grid(row=3, column=0, sticky="nsew")
        self.selection_label = ttk.Label(sidebar, text="All units", style="Selection.TLabel", anchor="center")
        self.selection_label.grid(row=4, column=0, sticky="ew", pady=(10, 3), ipady=7)
        ttk.Label(sidebar, text="Esc clears the spatial filter", style="Hint.TLabel", anchor="center").grid(row=5, column=0, sticky="ew")

        main = ttk.Frame(self, style="App.TFrame", padding=(20, 14, 20, 18))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)

        top = ttk.Frame(main, style="App.TFrame")
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        title_box = ttk.Frame(top, style="App.TFrame")
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, text="RF Mapping Explorer", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, text=self.source_label, style="Subtitle.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.controls_button = ttk.Button(top, text="Hide display controls  ▴", style="Quiet.TButton", command=self._toggle_controls)
        self.controls_button.grid(row=0, column=1, rowspan=2, sticky="e")

        nav = ttk.Frame(main, style="Panel.TFrame", padding=(12, 9))
        nav.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        nav.columnconfigure(2, weight=1)
        ttk.Label(nav, text="Unit", style="NavLabel.TLabel").grid(row=0, column=0, padx=(0, 8))
        ttk.Button(nav, text="‹", width=3, command=lambda: self._step(-1)).grid(row=0, column=1, padx=(0, 5))
        self.unit_combo = ttk.Combobox(nav, state="readonly", width=20)
        self.unit_combo.grid(row=0, column=2, sticky="w")
        self.unit_combo.bind("<<ComboboxSelected>>", self._combo_changed)
        ttk.Button(nav, text="›", width=3, command=lambda: self._step(1)).grid(row=0, column=3, padx=(5, 14))
        self.unit_detail = ttk.Label(nav, text="", style="NavValue.TLabel")
        self.unit_detail.grid(row=0, column=4, sticky="e")
        nav.columnconfigure(4, weight=1)

        self.controls = ttk.Frame(main, style="Panel.TFrame", padding=(12, 9))
        self.controls.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(self.controls, text="Display", style="NavLabel.TLabel").grid(row=0, column=0, padx=(0, 10))
        ttk.Label(self.controls, text="Metric", style="Small.TLabel").grid(row=0, column=1, padx=(0, 5))
        metric = ttk.Combobox(self.controls, textvariable=self.metric, values=("Peak response", "Range sum", "Z-score"), state="readonly", width=14)
        metric.grid(row=0, column=2, padx=(0, 14))
        ttk.Label(self.controls, text="Color", style="Small.TLabel").grid(row=0, column=3, padx=(0, 5))
        palette = ttk.Combobox(self.controls, textvariable=self.palette, values=("Viridis", "Magma", "Blue–red"), state="readonly", width=11)
        palette.grid(row=0, column=4, padx=(0, 14))
        ttk.Label(self.controls, text="Smoothing", style="Small.TLabel").grid(row=0, column=5, padx=(0, 5))
        scale = ttk.Scale(self.controls, variable=self.smoothing, from_=0, to=1, length=100, command=lambda _v: self._refresh_plots())
        scale.grid(row=0, column=6, padx=(0, 14))
        ttk.Checkbutton(self.controls, text="Grid", variable=self.grid_lines, command=self._refresh_plots).grid(row=0, column=7)
        metric.bind("<<ComboboxSelected>>", lambda _e: self._refresh_plots())
        palette.bind("<<ComboboxSelected>>", lambda _e: self._refresh_plots())

        plots = ttk.Frame(main, style="App.TFrame")
        plots.grid(row=3, column=0, sticky="nsew")
        plots.columnconfigure(0, weight=1)
        plots.columnconfigure(1, weight=1)
        plots.rowconfigure(0, weight=1)
        plots.rowconfigure(1, weight=1)
        self.rf_canvas = self._plot_card(plots, "RF response", "spikes / presentation", 0, 0, self._draw_rf)
        self.delay_canvas = self._plot_card(plots, "Peak delay", "response latency (ms)", 0, 1, self._draw_delay)
        self.rgb_canvas = self._plot_card(plots, "RGB composite", "early · peak · late", 1, 0, self._draw_rgb)
        self.timeline_canvas = self._plot_card(plots, "Response timeline", "full analysis window", 1, 1, self._draw_timeline)
        self._sync_combo()

    @staticmethod
    def _legend_item(parent: tk.Misc, column: int, color: str, text: str) -> None:
        item = ttk.Frame(parent, style="Panel.TFrame")
        item.grid(row=0, column=column, sticky="w", padx=(0, 12))
        dot = tk.Canvas(item, width=10, height=10, bg=PANEL, highlightthickness=0)
        dot.grid(row=0, column=0, padx=(0, 4))
        dot.create_oval(2, 2, 8, 8, fill=color, outline="")
        ttk.Label(item, text=text, style="Hint.TLabel").grid(row=0, column=1)

    def _plot_card(self, parent: tk.Misc, title: str, subtitle: str, row: int, column: int, draw: Callable[[tk.Canvas, int, int], None]) -> PlotCanvas:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        card.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 4, 4 if column == 0 else 0), pady=(0 if row == 0 else 4, 4 if row == 0 else 0))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)
        ttk.Label(card, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, text=subtitle, style="CardSub.TLabel").grid(row=1, column=0, sticky="w", pady=(1, 4))
        canvas = PlotCanvas(card, draw, height=230)
        canvas.grid(row=2, column=0, sticky="nsew")
        return canvas

    def _toggle_controls(self) -> None:
        visible = not self.controls_visible.get()
        self.controls_visible.set(visible)
        if visible:
            self.controls.grid()
            self.controls_button.configure(text="Hide display controls  ▴")
        else:
            self.controls.grid_remove()
            self.controls_button.configure(text="Show display controls  ▾")

    def _selection_changed(self, region: Optional[tuple[float, float, float, float]], reason: str) -> None:
        self.selection_region = region
        if region is None:
            self.filtered_units = list(self.all_units)
            self.selection_label.configure(text=f"All {len(self.filtered_units)} units")
        else:
            xa, xb, ya, yb = region
            self.filtered_units = [u for u in self.all_units if xa <= u.x <= xb and ya <= u.y <= yb]
            label = "Channel neighborhood" if reason == "channel" else "Custom region"
            self.selection_label.configure(text=f"{label}  ·  {len(self.filtered_units)} units")
        if self.current_unit not in self.filtered_units:
            self.current_unit = self.filtered_units[0] if self.filtered_units else None
        self._sync_combo()

    def clear_selection(self) -> None:
        self.probe_canvas.set_selection(None, "clear")

    def _sync_combo(self) -> None:
        values = [f"Unit {u.unit_id}" for u in self.filtered_units]
        self.unit_combo.configure(values=values)
        if self.current_unit in self.filtered_units:
            idx = self.filtered_units.index(self.current_unit)
            self.unit_combo.current(idx)
        elif values:
            self.current_unit = self.filtered_units[0]
            self.unit_combo.current(0)
        else:
            self.unit_combo.set("No units in region")
        self._update_unit_detail()
        self._refresh_plots()

    def _combo_changed(self, _event: object = None) -> None:
        idx = self.unit_combo.current()
        if 0 <= idx < len(self.filtered_units):
            self.current_unit = self.filtered_units[idx]
            self._update_unit_detail()
            self._refresh_plots()

    def _step(self, direction: int) -> None:
        if not self.filtered_units:
            return
        try:
            idx = self.filtered_units.index(self.current_unit) if self.current_unit else 0
        except ValueError:
            idx = 0
        self.current_unit = self.filtered_units[(idx + direction) % len(self.filtered_units)]
        self._sync_combo()

    def _update_unit_detail(self) -> None:
        if self.current_unit:
            self.unit_detail.configure(text=f"x {self.current_unit.x:.1f} µm    y {self.current_unit.y:.1f} µm    ·    {len(self.filtered_units)} available")
            self.probe_canvas.set_selected_unit(self.current_unit.unit_id)
        else:
            self.unit_detail.configure(text="Draw a larger region to include a unit")
            self.probe_canvas.set_selected_unit(None)

    def _refresh_plots(self) -> None:
        for name in ("rf_canvas", "delay_canvas", "rgb_canvas", "timeline_canvas"):
            canvas = getattr(self, name, None)
            if canvas:
                canvas.redraw()

    def _seed(self) -> int:
        return (self.current_unit.unit_id if self.current_unit else 0) * 7919 + len(self.filtered_units) * 31

    @staticmethod
    def _plot_frame(c: tk.Canvas, w: int, h: int) -> tuple[float, float, float, float]:
        left, top, right, bottom = 38.0, 8.0, w - 12.0, h - 26.0
        c.create_line(left, bottom, right, bottom, fill="#aeb5c3")
        c.create_line(left, top, left, bottom, fill="#aeb5c3")
        return left, top, right, bottom

    @staticmethod
    def _spatial_plot_frame(
        c: tk.Canvas,
        w: int,
        h: int,
        columns: int,
        rows: int,
    ) -> tuple[float, float, float, float]:
        left, top, right, bottom = fit_aspect_rect(
            w,
            h,
            columns,
            rows,
            padding=(38.0, 8.0, 12.0, 26.0),
        )
        c.create_line(left, bottom, right, bottom, fill="#aeb5c3")
        c.create_line(left, top, left, bottom, fill="#aeb5c3")
        return left, top, right, bottom

    @staticmethod
    def _gradient(value: float, palette: str) -> str:
        value = max(0.0, min(1.0, value))
        if palette == "Magma":
            stops = ((25, 14, 52), (151, 43, 108), (251, 135, 97), (252, 244, 170))
        elif palette == "Blue–red":
            stops = ((45, 74, 166), (236, 239, 245), (194, 53, 55), (112, 18, 35))
        else:
            stops = ((55, 35, 105), (32, 140, 141), (85, 192, 100), (247, 221, 72))
        pos = value * (len(stops) - 1)
        idx = min(len(stops) - 2, int(pos))
        t = pos - idx
        rgb = tuple(round(stops[idx][i] * (1 - t) + stops[idx + 1][i] * t) for i in range(3))
        return "#%02x%02x%02x" % rgb

    def _draw_heatmap(self, c: tk.Canvas, w: int, h: int, delay: bool = False) -> None:
        cols, rows = 15, 11
        left, top, right, bottom = self._spatial_plot_frame(c, w, h, cols, rows)
        seed = self._seed() + (43 if delay else 0)
        rng = random.Random(seed)
        cx1, cy1 = rng.uniform(4, 10), rng.uniform(3, 7)
        cx2, cy2 = rng.uniform(2, 12), rng.uniform(2, 8)
        values: list[list[float]] = []
        for y in range(rows):
            row = []
            for x in range(cols):
                if delay:
                    v = 0.5 + 0.30 * math.sin(x * .45 + y * .22 + seed % 13) + 0.18 * math.cos(y * .7 - x * .12)
                else:
                    a = math.exp(-((x - cx1) ** 2 / 9 + (y - cy1) ** 2 / 5))
                    b = .52 * math.exp(-((x - cx2) ** 2 / 7 + (y - cy2) ** 2 / 8))
                    v = .08 + .85 * a + b + rng.uniform(-.04, .04)
                row.append(max(0, min(1, v)))
            values.append(row)
        cw, ch = (right - left) / cols, (bottom - top) / rows
        for y, row in enumerate(values):
            for x, value in enumerate(row):
                color = self._gradient(value, "Blue–red" if delay else self.palette.get())
                outline = "#ffffff" if self.grid_lines.get() else color
                c.create_rectangle(left + x * cw, top + y * ch, left + (x + 1) * cw + .5, top + (y + 1) * ch + .5, fill=color, outline=outline)
        for i, label in enumerate(("−40°", "0°", "+40°")):
            c.create_text(left + i * (right - left) / 2, bottom + 14, text=label, fill=MUTED, font=("TkDefaultFont", 8))
        c.create_text(max(10.0, left - 26.0), (top + bottom) / 2, text="elev.", angle=90, fill=MUTED, font=("TkDefaultFont", 8))
        if delay:
            c.create_text(right - 4, top + 8, text="18–112 ms", anchor="ne", fill="#ffffff", font=("TkDefaultFont", 8, "bold"))

    def _draw_rf(self, c: tk.Canvas, w: int, h: int) -> None:
        self._draw_heatmap(c, w, h, False)

    def _draw_delay(self, c: tk.Canvas, w: int, h: int) -> None:
        self._draw_heatmap(c, w, h, True)

    def _draw_rgb(self, c: tk.Canvas, w: int, h: int) -> None:
        cols, rows = 15, 11
        left, top, right, bottom = self._spatial_plot_frame(c, w, h, cols, rows)
        seed = self._seed()
        centers = ((4 + seed % 4, 4), (8, 5 + seed % 3), (11, 7))
        cw, ch = (right - left) / cols, (bottom - top) / rows
        for y in range(rows):
            for x in range(cols):
                components = []
                for cx, cy in centers:
                    components.append(int(245 * math.exp(-((x - cx) ** 2 / 13 + (y - cy) ** 2 / 7))))
                color = "#%02x%02x%02x" % tuple(max(12, v) for v in components)
                outline = "#ffffff" if self.grid_lines.get() else color
                c.create_rectangle(left + x * cw, top + y * ch, left + (x + 1) * cw + .5, top + (y + 1) * ch + .5, fill=color, outline=outline)
        c.create_text(left + 4, top + 7, text="EARLY", anchor="nw", fill="#ff7777", font=("TkDefaultFont", 8, "bold"))
        c.create_text((left + right) / 2, top + 7, text="PEAK", anchor="n", fill="#64ed86", font=("TkDefaultFont", 8, "bold"))
        c.create_text(right - 4, top + 7, text="LATE", anchor="ne", fill="#78a4ff", font=("TkDefaultFont", 8, "bold"))

    def _draw_timeline(self, c: tk.Canvas, w: int, h: int) -> None:
        left, top, right, bottom = self._plot_frame(c, w, h)
        zero = left + (right - left) / 3
        c.create_rectangle(zero, top, zero + (right - left) * .34, bottom, fill="#f0f4ff", outline="")
        c.create_line(zero, top, zero, bottom, fill=BLUE, dash=(3, 3), width=1)
        c.create_text(zero + 4, top + 4, text="stimulus on", anchor="nw", fill=BLUE, font=("TkDefaultFont", 8, "bold"))
        rng = random.Random(self._seed() + 97)
        colors = (BLUE, TEAL, ORANGE)
        for series, color in enumerate(colors):
            points: list[float] = []
            phase = rng.uniform(-.5, .5)
            for i in range(90):
                t = -100 + i * 300 / 89
                peak = math.exp(-((t - (45 + series * 17)) / (25 + series * 7)) ** 2)
                value = .12 + peak * (.70 - series * .12) + .035 * math.sin(i * .38 + phase)
                x = left + i * (right - left) / 89
                y = bottom - value * (bottom - top) * .88
                points.extend((x, y))
            c.create_line(*points, fill=color, width=2, smooth=True)
        for i, label in enumerate(("−100", "0", "100", "200 ms")):
            c.create_text(left + i * (right - left) / 3, bottom + 14, text=label, fill=MUTED, font=("TkDefaultFont", 8))
        c.create_text(right - 5, top + 8, text="early   peak   late", anchor="ne", fill=MUTED, font=("TkDefaultFont", 8))


class DemoApp(tk.Tk):
    def __init__(self, channels: list[Channel], units: list[Unit], data_root: Path, probe: str, start_filtered: bool) -> None:
        super().__init__()
        self.channels = channels
        self.units = units
        self.data_root = data_root
        self.probe = probe
        self.title("RF Mapping Explorer — Probe selection demo")
        self.geometry("1380x880")
        self.minsize(1060, 700)
        self._configure_style()
        self._build_menu()
        self.viewer = RFViewer(self, channels, units, probe, start_filtered=start_filtered)
        self.bind_all("<Escape>", lambda _e: self._clear_active_view())
        self.bind_all("<Left>", lambda _e: self._step_active(-1))
        self.bind_all("<Right>", lambda _e: self._step_active(1))

    def _configure_style(self) -> None:
        self.configure(bg=BG)
        style = ttk.Style(self)
        if "aqua" in style.theme_names():
            style.theme_use("aqua")
        style.configure("App.TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Card.TFrame", background=PANEL, relief="solid", borderwidth=1)
        style.configure("Title.TLabel", background=BG, foreground=INK, font=("TkDefaultFont", 22, "bold"))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=("TkDefaultFont", 10))
        style.configure("Section.TLabel", background=PANEL, foreground=INK, font=("TkDefaultFont", 14, "bold"))
        style.configure("Hint.TLabel", background=PANEL, foreground=MUTED, font=("TkDefaultFont", 9))
        style.configure("Small.TLabel", background=PANEL, foreground=MUTED, font=("TkDefaultFont", 9))
        style.configure("NavLabel.TLabel", background=PANEL, foreground=INK, font=("TkDefaultFont", 10, "bold"))
        style.configure("NavValue.TLabel", background=PANEL, foreground=MUTED, font=("TkDefaultFont", 10))
        style.configure("Selection.TLabel", background=BLUE_PALE, foreground="#275cb8", font=("TkDefaultFont", 10, "bold"))
        style.configure("CardTitle.TLabel", background=PANEL, foreground=INK, font=("TkDefaultFont", 11, "bold"))
        style.configure("CardSub.TLabel", background=PANEL, foreground=MUTED, font=("TkDefaultFont", 9))
        style.configure("Quiet.TButton", font=("TkDefaultFont", 10))

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Open RF mapping file…", accelerator="⌘O", command=self.open_json)
        file_menu.add_separator()
        file_menu.add_command(label="Close Window", accelerator="⌘W", command=self.destroy)
        menu.add_cascade(label="File", menu=file_menu)
        view_menu = tk.Menu(menu, tearoff=False)
        view_menu.add_command(label="Clear Spatial Filter", accelerator="Esc", command=self._clear_active_view)
        view_menu.add_command(label="Toggle Display Controls", command=lambda: self._active_viewer()._toggle_controls())
        menu.add_cascade(label="View", menu=view_menu)
        self.configure(menu=menu)
        self.bind_all("<Command-o>", lambda _e: self.open_json())
        self.bind_all("<Control-o>", lambda _e: self.open_json())
        self.bind_all("<Command-w>", lambda _e: self._close_active_window())

    def _active_viewer(self) -> RFViewer:
        focus = self.focus_get()
        while focus is not None:
            if isinstance(focus, RFViewer):
                return focus
            focus = focus.master
        return self.viewer

    def _clear_active_view(self) -> None:
        self._active_viewer().clear_selection()

    def _step_active(self, direction: int) -> None:
        if not isinstance(self.focus_get(), (ttk.Combobox, ttk.Scale)):
            self._active_viewer()._step(direction)

    def _close_active_window(self) -> None:
        top = self.winfo_toplevel()
        focus = self.focus_get()
        if focus is not None:
            top = focus.winfo_toplevel()
        top.destroy()

    def open_json(self) -> None:
        initial = Path(__file__).resolve().parent / "data"
        path_text = filedialog.askopenfilename(parent=self, title="Open RF mapping file in new window", initialdir=str(initial if initial.exists() else Path.cwd()), filetypes=(("RF mapping files", "*.rfmap *.json"), ("All files", "*")))
        if not path_text:
            return
        path = Path(path_text)
        try:
            with path.open(encoding="utf-8") as handle:
                raw = json.load(handle)
            pool = raw.get("unitPool", [])
            json_units = {int(value) for value in pool} if pool else None
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Could not open RF map", str(exc), parent=self)
            return
        window = tk.Toplevel(self)
        window.title(f"RF Mapping Explorer — {path.name}")
        window.geometry("1380x880")
        window.minsize(1060, 700)
        RFViewer(window, self.channels, self.units, self.probe, source_label=path.name, json_units=json_units)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT, help="Session data directory containing waveform/ and spike_position/")
    parser.add_argument("--probe", default="ProbeA", help="Probe folder name (default: ProbeA)")
    parser.add_argument("--start-filtered", action="store_true", help="Start with an example 160 × 75 µm channel neighborhood selected")
    parser.add_argument("--auto-close-ms", type=int, default=0, metavar="MS", help="Close automatically after MS milliseconds (useful for screenshot automation)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        channels, units = load_geometry(args.data_root, args.probe)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Cannot load {args.probe} geometry: {exc}") from exc
    app = DemoApp(channels, units, args.data_root, args.probe, args.start_filtered)
    if args.auto_close_ms > 0:
        app.after(args.auto_close_ms, app.destroy)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
