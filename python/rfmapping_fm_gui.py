#!/usr/bin/env python3
"""Free-moving RF viewer alpha for ``rfmapping_fm_hdf5_v1`` files."""

from __future__ import annotations

import argparse
import json
import math
import queue
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from rfmapping_viewer.fm_dataset import (
    FreeMovingRFMap,
    FreeMovingUnitMap,
    aggregate_rate_hz,
    load_free_moving_rfmap,
    spatial_mean_timeline_hz,
)

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from PIL import ImageTk

    TK_AVAILABLE = True
except ModuleNotFoundError:
    filedialog = messagebox = ttk = None
    ImageTk = None
    TK_AVAILABLE = False

    class _MissingTk:
        Tk = object
        Misc = object

    tk = _MissingTk()

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except ModuleNotFoundError:
    DND_FILES = "DND_Files"
    TkinterDnD = None
    DND_AVAILABLE = False


APP_VERSION = "1.10.0"
APP_PRERELEASE = "alpha.2"
APP_RELEASE_VERSION = f"{APP_VERSION}-{APP_PRERELEASE}"
APP_EDITION = "FreeMovingAlpha"
APP_DISPLAY_VERSION = f"{APP_RELEASE_VERSION} · Free-moving alpha"
DND_SMOKE_ARGUMENT = "--self-test-dnd"

METRIC_RATE = "Mean firing rate (Hz)"
METRIC_EXPOSURE = "Exposure (s)"
METRIC_EFFECTIVE_TRIALS = "Effective trials"
METRICS = (METRIC_RATE, METRIC_EXPOSURE, METRIC_EFFECTIVE_TRIALS)
PALETTES = ("Viridis", "Inferno", "Gray")
VIEW_2D = "2D map"
VIEW_3D = "3D sphere"
VIEWS = (VIEW_2D, VIEW_3D)

_PALETTE_STOPS: dict[str, tuple[tuple[float, tuple[int, int, int]], ...]] = {
    "Viridis": (
        (0.00, (68, 1, 84)),
        (0.25, (59, 82, 139)),
        (0.50, (33, 145, 140)),
        (0.75, (94, 201, 98)),
        (1.00, (253, 231, 37)),
    ),
    "Inferno": (
        (0.00, (0, 0, 4)),
        (0.25, (87, 16, 110)),
        (0.50, (188, 55, 84)),
        (0.75, (249, 142, 8)),
        (1.00, (252, 255, 164)),
    ),
    "Gray": ((0.00, (0, 0, 0)), (1.00, (255, 255, 255))),
}


def finite_display_range(
    matrix: np.ndarray, percentile: float = 99.0
) -> tuple[float, float]:
    finite = np.asarray(matrix, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return 0.0, 1.0
    low = max(0.0, float(np.min(finite)))
    high = float(np.percentile(finite, percentile))
    if not math.isfinite(high) or high <= low:
        high = float(np.max(finite))
    if not math.isfinite(high) or high <= low:
        high = low + 1.0
    return low, high


def colorize_matrix(
    matrix: np.ndarray,
    palette: str,
    low: float,
    high: float,
    *,
    missing_rgb: tuple[int, int, int] = (28, 32, 39),
) -> np.ndarray:
    if palette not in _PALETTE_STOPS:
        raise ValueError(f"Unknown palette: {palette}")
    values = np.asarray(matrix, dtype=np.float64)
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        raise ValueError("Color range must be finite and increasing")
    normalized = np.clip((values - low) / (high - low), 0.0, 1.0)
    interpolation_values = np.where(np.isfinite(normalized), normalized, 0.0)
    positions = np.array([item[0] for item in _PALETTE_STOPS[palette]])
    colors = np.array([item[1] for item in _PALETTE_STOPS[palette]], dtype=float)
    rgb = np.empty(values.shape + (3,), dtype=np.uint8)
    for channel in range(3):
        rgb[..., channel] = np.rint(
            np.interp(interpolation_values, positions, colors[:, channel])
        ).astype(np.uint8)
    rgb[~np.isfinite(values)] = missing_rgb
    return rgb


def _nearest_edge_index(edges_sec: np.ndarray, target_sec: float) -> int:
    return int(np.argmin(np.abs(np.asarray(edges_sec) - target_sec)))


def _view_basis(yaw_deg: float, pitch_deg: float) -> tuple[np.ndarray, ...]:
    """Return right, up, and forward axes for a head-centric sphere view."""
    yaw = math.radians(float(yaw_deg))
    pitch = math.radians(float(pitch_deg))
    sin_yaw, cos_yaw = math.sin(yaw), math.cos(yaw)
    sin_pitch, cos_pitch = math.sin(pitch), math.cos(pitch)
    right = np.array((cos_yaw, 0.0, -sin_yaw), dtype=np.float64)
    up = np.array(
        (-sin_pitch * sin_yaw, cos_pitch, -sin_pitch * cos_yaw),
        dtype=np.float64,
    )
    forward = np.array(
        (cos_pitch * sin_yaw, sin_pitch, cos_pitch * cos_yaw),
        dtype=np.float64,
    )
    return right, up, forward


def sphere_direction_from_normalized(
    x: np.ndarray | float,
    y: np.ndarray | float,
    yaw_deg: float,
    pitch_deg: float,
) -> np.ndarray:
    """Map orthographic sphere coordinates to head ``right, up, forward``."""
    x_values, y_values = np.broadcast_arrays(
        np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    )
    radius_squared = x_values * x_values + y_values * y_values
    visible = radius_squared <= 1.0
    depth = np.sqrt(np.clip(1.0 - radius_squared, 0.0, 1.0))
    right, up, forward = _view_basis(yaw_deg, pitch_deg)
    direction = (
        x_values[..., None] * right
        + y_values[..., None] * up
        + depth[..., None] * forward
    )
    return np.where(visible[..., None], direction, np.nan)


def head_angles_from_sphere_point(
    x: float, y: float, yaw_deg: float, pitch_deg: float
) -> tuple[float, float] | None:
    """Return head-centric azimuth/elevation for one visible sphere point."""
    direction = sphere_direction_from_normalized(x, y, yaw_deg, pitch_deg)
    if not np.all(np.isfinite(direction)):
        return None
    azimuth = math.degrees(math.atan2(float(direction[0]), float(direction[2])))
    elevation = math.degrees(math.asin(float(np.clip(direction[1], -1.0, 1.0))))
    return azimuth, elevation


def project_head_angles_to_sphere(
    azimuth_deg: np.ndarray | float,
    elevation_deg: np.ndarray | float,
    yaw_deg: float,
    pitch_deg: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project head-centric directions into the current orthographic view."""
    azimuth, elevation = np.broadcast_arrays(
        np.radians(np.asarray(azimuth_deg, dtype=np.float64)),
        np.radians(np.asarray(elevation_deg, dtype=np.float64)),
    )
    cos_elevation = np.cos(elevation)
    direction = np.stack(
        (
            cos_elevation * np.sin(azimuth),
            np.sin(elevation),
            cos_elevation * np.cos(azimuth),
        ),
        axis=-1,
    )
    right, up, forward = _view_basis(yaw_deg, pitch_deg)
    return direction @ right, direction @ up, direction @ forward


def _nearest_center_indices(
    centers: np.ndarray, values: np.ndarray, *, circular: bool
) -> np.ndarray:
    center_values = np.asarray(centers, dtype=np.float64)
    if center_values.ndim != 1 or center_values.size == 0:
        raise ValueError("Axis centers must be a non-empty one-dimensional array")
    if np.any(np.diff(center_values) <= 0):
        raise ValueError("Axis centers must be strictly increasing")

    target = np.asarray(values, dtype=np.float64)
    if circular:
        target = np.mod(target - center_values[0], 360.0) + center_values[0]
        extended = np.concatenate(
            ((center_values[-1] - 360.0,), center_values, (center_values[0] + 360.0,))
        )
        mapping = np.concatenate(
            ((center_values.size - 1,), np.arange(center_values.size), (0,))
        )
    else:
        extended = center_values
        mapping = np.arange(center_values.size)

    right_index = np.searchsorted(extended, target, side="left")
    right_index = np.clip(right_index, 0, extended.size - 1)
    left_index = np.clip(right_index - 1, 0, extended.size - 1)
    choose_right = np.abs(extended[right_index] - target) < np.abs(
        target - extended[left_index]
    )
    selected = np.where(choose_right, right_index, left_index)
    return mapping[selected]


def render_spherical_texture(
    rgb: np.ndarray,
    azimuth_centers_deg: np.ndarray,
    elevation_centers_deg: np.ndarray,
    diameter: int,
    yaw_deg: float,
    pitch_deg: float,
) -> np.ndarray:
    """Render an azimuth/elevation color map onto a rotatable opaque sphere."""
    colors = np.asarray(rgb, dtype=np.uint8)
    if colors.shape != (
        len(elevation_centers_deg),
        len(azimuth_centers_deg),
        3,
    ):
        raise ValueError("RGB map shape does not match elevation/azimuth axes")
    if diameter < 3:
        raise ValueError("Sphere diameter must be at least 3 pixels")

    coordinate = (np.arange(diameter, dtype=np.float64) + 0.5) * (2.0 / diameter) - 1.0
    x_values, y_values = np.meshgrid(coordinate, -coordinate)
    direction = sphere_direction_from_normalized(
        x_values, y_values, yaw_deg, pitch_deg
    )
    visible = np.all(np.isfinite(direction), axis=-1)
    azimuth = np.degrees(np.arctan2(direction[..., 0], direction[..., 2]))
    elevation = np.degrees(np.arcsin(np.clip(direction[..., 1], -1.0, 1.0)))
    azimuth_index = _nearest_center_indices(
        azimuth_centers_deg, np.where(visible, azimuth, 0.0), circular=True
    )
    elevation_index = _nearest_center_indices(
        elevation_centers_deg, np.where(visible, elevation, 0.0), circular=False
    )

    rgba = np.zeros((diameter, diameter, 4), dtype=np.uint8)
    rgba[..., :3] = colors[elevation_index, azimuth_index]
    rgba[..., 3] = np.where(visible, 255, 0).astype(np.uint8)
    return rgba


if DND_AVAILABLE:
    _RootBase = TkinterDnD.Tk
elif TK_AVAILABLE:
    _RootBase = tk.Tk
else:
    _RootBase = object


class FreeMovingRFViewer(_RootBase):
    """A single-purpose, read-only viewer for free-moving RF maps."""

    def __init__(self, initial_path: str | Path | None = None) -> None:
        super().__init__()
        self.title(f"Free-Moving RF Viewer {APP_DISPLAY_VERSION}")
        self.geometry("1320x860")
        self.minsize(1040, 700)

        self.dataset: FreeMovingRFMap | None = None
        self.unit_map: FreeMovingUnitMap | None = None
        self.unit_index = 0
        self.start_bin = 0
        self.stop_bin = 1
        self._load_generation = 0
        self._render_after: str | None = None
        self._heat_photo: ImageTk.PhotoImage | None = None
        self._legend_photo: ImageTk.PhotoImage | None = None
        self._plot_bounds = (0.0, 0.0, 0.0, 0.0)
        self._display_matrix: np.ndarray | None = None
        self._display_low = 0.0
        self._display_high = 1.0
        self._sphere_yaw_deg = 0.0
        self._sphere_pitch_deg = 0.0
        self._sphere_bounds = (0.0, 0.0, 0.0, 0.0)
        self._sphere_drag: tuple[float, float, float, float] | None = None
        self._closed = False
        self._updating_time = False
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="fm-rf-unit"
        )
        self._result_queue: queue.Queue[
            tuple[int, FreeMovingUnitMap | None, BaseException | None]
        ] = queue.Queue()

        self.metric_var = tk.StringVar(value=METRIC_RATE)
        self.palette_var = tk.StringVar(value="Viridis")
        self.view_var = tk.StringVar(value=VIEW_2D)
        self.minimum_exposure_var = tk.StringVar(value="0")
        self.file_var = tk.StringVar(value="Drop or open a free-moving .rfmap")
        self.document_var = tk.StringVar(value="No document loaded")
        self.calibration_var = tk.StringVar(value="Calibration provenance appears here.")
        self.time_var = tk.StringVar(value="—")
        self.status_var = tk.StringVar(value="Ready")
        self.hover_var = tk.StringVar(value="Move over the map to inspect a bin.")

        self._configure_style()
        self._build_interface()
        self._bind_interactions()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(40, self._poll_unit_results)

        if initial_path is not None:
            self.after_idle(lambda: self.open_document(initial_path))

    def _configure_style(self) -> None:
        self.configure(background="#11151b")
        style = ttk.Style(self)
        if "aqua" in style.theme_names():
            style.theme_use("aqua")
        style.configure("Viewer.TFrame", background="#11151b")
        style.configure("Panel.TFrame", background="#191f27")
        style.configure(
            "Title.TLabel",
            background="#11151b",
            foreground="#f7f8fa",
            font=("TkDefaultFont", 20, "bold"),
        )
        style.configure(
            "Alpha.TLabel",
            background="#2d2154",
            foreground="#d9c7ff",
            padding=(9, 4),
            font=("TkDefaultFont", 10, "bold"),
        )
        style.configure(
            "Section.TLabel",
            background="#191f27",
            foreground="#f2f4f7",
            font=("TkDefaultFont", 12, "bold"),
        )
        style.configure(
            "Body.TLabel", background="#191f27", foreground="#c6ccd5"
        )
        style.configure(
            "Muted.TLabel", background="#191f27", foreground="#8993a2"
        )
        style.configure(
            "Status.TLabel", background="#11151b", foreground="#aeb8c6"
        )
        style.configure("Primary.TButton", padding=(13, 8))
        style.configure("Tool.TButton", padding=(8, 6))

    def _build_interface(self) -> None:
        outer = ttk.Frame(self, style="Viewer.TFrame", padding=(18, 14, 18, 12))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="Viewer.TFrame")
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="Free-Moving RF Viewer", style="Title.TLabel").pack(
            side="left"
        )
        ttk.Label(header, text="ALPHA", style="Alpha.TLabel").pack(
            side="left", padx=(12, 0)
        )
        ttk.Button(
            header,
            text="Open .rfmap…",
            style="Primary.TButton",
            command=self.choose_document,
        ).pack(side="right")

        file_row = ttk.Frame(outer, style="Viewer.TFrame")
        file_row.pack(fill="x", pady=(0, 10))
        ttk.Label(file_row, textvariable=self.file_var, style="Status.TLabel").pack(
            side="left", fill="x", expand=True
        )
        ttk.Label(
            file_row, text=APP_DISPLAY_VERSION, style="Status.TLabel"
        ).pack(side="right")

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)

        sidebar = ttk.Frame(body, style="Panel.TFrame", padding=16, width=320)
        visual = ttk.Frame(body, style="Viewer.TFrame")
        body.add(sidebar, weight=0)
        body.add(visual, weight=1)
        self._build_sidebar(sidebar)
        self._build_visual(visual)

        status = ttk.Frame(outer, style="Viewer.TFrame")
        status.pack(fill="x", pady=(10, 0))
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(
            side="left"
        )
        ttk.Label(status, textvariable=self.hover_var, style="Status.TLabel").pack(
            side="right"
        )

    def _build_sidebar(self, sidebar: Any) -> None:
        ttk.Label(sidebar, text="Document", style="Section.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            sidebar,
            textvariable=self.document_var,
            style="Body.TLabel",
            justify="left",
            wraplength=280,
        ).pack(anchor="w", fill="x", pady=(5, 16))

        ttk.Separator(sidebar).pack(fill="x", pady=(0, 14))
        ttk.Label(sidebar, text="Unit", style="Section.TLabel").pack(anchor="w")
        unit_row = ttk.Frame(sidebar, style="Panel.TFrame")
        unit_row.pack(fill="x", pady=(7, 16))
        self.previous_unit_button = ttk.Button(
            unit_row, text="‹", width=3, style="Tool.TButton", command=self.previous_unit
        )
        self.previous_unit_button.pack(side="left")
        self.unit_combo = ttk.Combobox(unit_row, state="readonly", width=23)
        self.unit_combo.pack(side="left", fill="x", expand=True, padx=7)
        self.unit_combo.bind("<<ComboboxSelected>>", self._unit_selected)
        self.next_unit_button = ttk.Button(
            unit_row, text="›", width=3, style="Tool.TButton", command=self.next_unit
        )
        self.next_unit_button.pack(side="left")

        ttk.Label(sidebar, text="Display", style="Section.TLabel").pack(anchor="w")
        ttk.Label(sidebar, text="View", style="Muted.TLabel").pack(
            anchor="w", pady=(7, 2)
        )
        view_row = ttk.Frame(sidebar, style="Panel.TFrame")
        view_row.pack(fill="x")
        self.view_combo = ttk.Combobox(
            view_row,
            state="readonly",
            values=VIEWS,
            textvariable=self.view_var,
        )
        self.view_combo.pack(side="left", fill="x", expand=True)
        self.view_combo.bind("<<ComboboxSelected>>", self._view_changed)
        self.reset_view_button = ttk.Button(
            view_row,
            text="Reset",
            style="Tool.TButton",
            command=self._reset_sphere_view,
            state="disabled",
        )
        self.reset_view_button.pack(side="left", padx=(7, 0))

        ttk.Label(sidebar, text="Metric", style="Muted.TLabel").pack(
            anchor="w", pady=(8, 2)
        )
        self.metric_combo = ttk.Combobox(
            sidebar,
            state="readonly",
            values=METRICS,
            textvariable=self.metric_var,
        )
        self.metric_combo.pack(fill="x")
        self.metric_combo.bind("<<ComboboxSelected>>", lambda _event: self.schedule_render())

        ttk.Label(sidebar, text="Palette", style="Muted.TLabel").pack(
            anchor="w", pady=(8, 2)
        )
        self.palette_combo = ttk.Combobox(
            sidebar,
            state="readonly",
            values=PALETTES,
            textvariable=self.palette_var,
        )
        self.palette_combo.pack(fill="x")
        self.palette_combo.bind("<<ComboboxSelected>>", lambda _event: self.schedule_render())

        ttk.Label(sidebar, text="Minimum exposure (s)", style="Muted.TLabel").pack(
            anchor="w", pady=(8, 2)
        )
        self.exposure_entry = ttk.Entry(
            sidebar, textvariable=self.minimum_exposure_var
        )
        self.exposure_entry.pack(fill="x")
        self.exposure_entry.bind("<Return>", lambda _event: self.schedule_render())
        self.exposure_entry.bind("<FocusOut>", lambda _event: self.schedule_render())

        ttk.Separator(sidebar).pack(fill="x", pady=14)
        ttk.Label(sidebar, text="Response window", style="Section.TLabel").pack(
            anchor="w"
        )
        ttk.Label(sidebar, textvariable=self.time_var, style="Body.TLabel").pack(
            anchor="w", pady=(5, 8)
        )
        ttk.Label(sidebar, text="Start", style="Muted.TLabel").pack(anchor="w")
        self.start_scale = ttk.Scale(sidebar, from_=0, to=1, command=self._start_changed)
        self.start_scale.pack(fill="x", pady=(0, 6))
        ttk.Label(sidebar, text="End", style="Muted.TLabel").pack(anchor="w")
        self.stop_scale = ttk.Scale(sidebar, from_=1, to=2, command=self._stop_changed)
        self.stop_scale.pack(fill="x", pady=(0, 12))
        ttk.Label(
            sidebar,
            text="Drag either slider, or drag across the timeline. The map updates continuously.",
            style="Muted.TLabel",
            justify="left",
            wraplength=280,
        ).pack(anchor="w")

        ttk.Separator(sidebar).pack(fill="x", pady=14)
        ttk.Label(sidebar, text="Calibration", style="Section.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            sidebar,
            textvariable=self.calibration_var,
            style="Body.TLabel",
            justify="left",
            wraplength=280,
        ).pack(anchor="w", fill="x", pady=(5, 0))

    def _build_visual(self, visual: Any) -> None:
        heat_frame = ttk.Frame(visual, style="Panel.TFrame", padding=1)
        heat_frame.pack(fill="both", expand=True, padx=(12, 0))
        self.heat_canvas = tk.Canvas(
            heat_frame,
            background="#0d1117",
            highlightthickness=0,
            cursor="crosshair",
        )
        self.heat_canvas.pack(fill="both", expand=True)

        timeline_frame = ttk.Frame(visual, style="Panel.TFrame", padding=1)
        timeline_frame.pack(fill="x", padx=(12, 0), pady=(10, 0))
        self.timeline_canvas = tk.Canvas(
            timeline_frame,
            height=132,
            background="#151a21",
            highlightthickness=0,
            cursor="sb_h_double_arrow",
        )
        self.timeline_canvas.pack(fill="x")

    def _bind_interactions(self) -> None:
        self.bind_all("<Command-o>", lambda _event: self.choose_document())
        self.bind_all("<Control-o>", lambda _event: self.choose_document())
        self.bind_all("<Left>", lambda _event: self.previous_unit())
        self.bind_all("<Right>", lambda _event: self.next_unit())
        self.heat_canvas.bind("<Configure>", lambda _event: self.schedule_render())
        self.heat_canvas.bind("<Motion>", self._heat_hover)
        self.heat_canvas.bind("<Leave>", lambda _event: self.hover_var.set(""))
        self.heat_canvas.bind("<ButtonPress-1>", self._heat_press)
        self.heat_canvas.bind("<B1-Motion>", self._heat_drag)
        self.heat_canvas.bind("<ButtonRelease-1>", self._heat_release)
        self.heat_canvas.bind("<Double-Button-1>", self._reset_sphere_view)
        self.timeline_canvas.bind("<Configure>", lambda _event: self._render_timeline())
        self.timeline_canvas.bind("<Button-1>", self._timeline_drag)
        self.timeline_canvas.bind("<B1-Motion>", self._timeline_drag)

        if DND_AVAILABLE:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._drop_document)
        try:
            self.createcommand("::tk::mac::OpenDocument", self._mac_open_document)
        except Exception:
            pass

    def _view_changed(self, _event: Any = None) -> None:
        is_sphere = self.view_var.get() == VIEW_3D
        self.reset_view_button.configure(state="normal" if is_sphere else "disabled")
        self.heat_canvas.configure(cursor="fleur" if is_sphere else "crosshair")
        if is_sphere:
            self.hover_var.set("Drag the sphere to rotate it; double-click to reset.")
        self.schedule_render()

    def _reset_sphere_view(self, _event: Any = None) -> str | None:
        if self.view_var.get() != VIEW_3D:
            return None
        self._sphere_yaw_deg = 0.0
        self._sphere_pitch_deg = 0.0
        self._sphere_drag = None
        self.schedule_render()
        return "break"

    def _heat_press(self, event: Any) -> str | None:
        if self.view_var.get() != VIEW_3D:
            return None
        self._sphere_drag = (
            float(event.x),
            float(event.y),
            self._sphere_yaw_deg,
            self._sphere_pitch_deg,
        )
        return "break"

    def _heat_drag(self, event: Any) -> str | None:
        if self.view_var.get() != VIEW_3D or self._sphere_drag is None:
            return None
        start_x, start_y, start_yaw, start_pitch = self._sphere_drag
        self._sphere_yaw_deg = (
            start_yaw - (float(event.x) - start_x) * 0.35 + 180.0
        ) % 360.0 - 180.0
        self._sphere_pitch_deg = min(
            89.0,
            max(-89.0, start_pitch + (float(event.y) - start_y) * 0.35),
        )
        self.schedule_render()
        return "break"

    def _heat_release(self, _event: Any) -> str | None:
        if self.view_var.get() != VIEW_3D:
            return None
        self._sphere_drag = None
        return "break"

    def choose_document(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Open free-moving RF map",
            filetypes=(("Free-moving RF map", "*.rfmap"),),
        )
        if selected:
            self.open_document(selected)

    def _drop_document(self, event: Any) -> str:
        paths = self.tk.splitlist(event.data)
        if len(paths) != 1:
            messagebox.showerror(
                "Open failed", "Drop exactly one free-moving .rfmap file.", parent=self
            )
            return "break"
        self.open_document(paths[0])
        return "break"

    def _mac_open_document(self, *paths: str) -> None:
        if paths:
            self.open_document(paths[0])

    def open_document(self, path: str | Path) -> None:
        self.status_var.set("Reading free-moving RF metadata…")
        self.update_idletasks()
        try:
            dataset = load_free_moving_rfmap(path)
        except Exception as exc:
            self.status_var.set("Open failed")
            messagebox.showerror("Open failed", str(exc), parent=self)
            return

        self.dataset = dataset
        self.unit_map = None
        self.unit_index = 0
        self.file_var.set(str(dataset.path))
        self.document_var.set(
            f"{dataset.unit_count:,} units\n"
            f"{dataset.elevation_count} elevation × {dataset.azimuth_count} azimuth\n"
            f"{dataset.time_bin_count} time bins · HDF5 FM"
        )
        labels = [
            f"{index + 1} / {dataset.unit_count}  ·  unit {int(unit_id)}"
            for index, unit_id in enumerate(dataset.unit_ids)
        ]
        self.unit_combo.configure(values=labels)
        self.unit_combo.current(0)

        n_time = dataset.time_bin_count
        self.start_scale.configure(from_=0, to=max(0, n_time - 1))
        self.stop_scale.configure(from_=1, to=n_time)
        self.start_bin = min(_nearest_edge_index(dataset.time_edges_sec, 0.0), n_time - 1)
        preferred_stop = _nearest_edge_index(dataset.time_edges_sec, 0.2)
        self.stop_bin = max(self.start_bin + 1, min(preferred_stop, n_time))
        self.start_scale.set(self.start_bin)
        self.stop_scale.set(self.stop_bin)
        self._update_time_text()
        self._update_calibration_text()
        self._request_unit(0)

    def _update_calibration_text(self) -> None:
        if self.dataset is None:
            return
        calibration = self.dataset.calibration
        screen = calibration.get("screen", {})
        head = calibration.get("head", {})
        radius = screen.get("radius_mm", "—") if isinstance(screen, dict) else "—"
        height = screen.get("height_mm", "—") if isinstance(screen, dict) else "—"
        viewpoint = (
            head.get("viewpoint_model", "—") if isinstance(head, dict) else "—"
        )
        rigid_body = calibration.get("rigid_body_name", "—")
        self.calibration_var.set(
            f"Rigid body: {rigid_body}\n"
            f"Viewpoint: {viewpoint}\n"
            f"Cylinder: R {float(radius):.1f} mm · H {float(height):.1f} mm"
        )

    def _unit_selected(self, _event: Any = None) -> None:
        selected = self.unit_combo.current()
        if selected >= 0:
            self._request_unit(selected)

    def previous_unit(self) -> None:
        if self.dataset is not None:
            self._request_unit(max(0, self.unit_index - 1))

    def next_unit(self) -> None:
        if self.dataset is not None:
            self._request_unit(min(self.dataset.unit_count - 1, self.unit_index + 1))

    def _request_unit(self, unit_index: int) -> None:
        if self.dataset is None or unit_index == self.unit_index and self.unit_map is not None:
            return
        self.unit_index = unit_index
        self.unit_combo.current(unit_index)
        self.previous_unit_button.configure(state="normal" if unit_index > 0 else "disabled")
        self.next_unit_button.configure(
            state="normal" if unit_index + 1 < self.dataset.unit_count else "disabled"
        )
        self._load_generation += 1
        generation = self._load_generation
        dataset = self.dataset
        unit_id = int(dataset.unit_ids[unit_index])
        self.status_var.set(f"Loading unit {unit_id}…")
        self.heat_canvas.delete("all")
        self.heat_canvas.create_text(
            self.heat_canvas.winfo_width() / 2,
            self.heat_canvas.winfo_height() / 2,
            text=f"Loading unit {unit_id}…",
            fill="#aab4c1",
            font=("TkDefaultFont", 15),
        )

        future = self._executor.submit(dataset.load_unit, unit_index)

        def completed(job: Any) -> None:
            try:
                result = job.result()
            except BaseException as exc:
                self._result_queue.put((generation, None, exc))
            else:
                self._result_queue.put((generation, result, None))

        future.add_done_callback(completed)

    def _poll_unit_results(self) -> None:
        if self._closed:
            return
        while True:
            try:
                generation, result, error = self._result_queue.get_nowait()
            except queue.Empty:
                break
            if generation != self._load_generation:
                continue
            if error is not None:
                self.status_var.set("Unit load failed")
                messagebox.showerror("Unit load failed", str(error), parent=self)
                continue
            self.unit_map = result
            assert result is not None
            self.status_var.set(f"Unit {result.unit_id} loaded")
            self.schedule_render()
        self.after(40, self._poll_unit_results)

    def _minimum_exposure(self) -> float:
        try:
            value = float(self.minimum_exposure_var.get())
        except ValueError as exc:
            raise ValueError("Minimum exposure must be a number") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError("Minimum exposure must be finite and non-negative")
        return value

    def _start_changed(self, value: str) -> None:
        if self.dataset is None or self._updating_time:
            return
        requested = int(round(float(value)))
        self.start_bin = min(max(0, requested), self.stop_bin - 1)
        self._sync_time_scales()

    def _stop_changed(self, value: str) -> None:
        if self.dataset is None or self._updating_time:
            return
        requested = int(round(float(value)))
        self.stop_bin = max(
            self.start_bin + 1, min(requested, self.dataset.time_bin_count)
        )
        self._sync_time_scales()

    def _sync_time_scales(self) -> None:
        self._updating_time = True
        try:
            self.start_scale.set(self.start_bin)
            self.stop_scale.set(self.stop_bin)
        finally:
            self._updating_time = False
        self._update_time_text()
        self.schedule_render()

    def _update_time_text(self) -> None:
        if self.dataset is None:
            self.time_var.set("—")
            return
        edges = self.dataset.time_edges_sec
        start_ms = edges[self.start_bin] * 1000.0
        stop_ms = edges[self.stop_bin] * 1000.0
        self.time_var.set(
            f"[{start_ms:.1f}, {stop_ms:.1f}) ms · {self.stop_bin - self.start_bin} bins"
        )

    def _timeline_drag(self, event: Any) -> None:
        if self.dataset is None:
            return
        left, right = 44.0, max(45.0, self.timeline_canvas.winfo_width() - 14.0)
        fraction = min(1.0, max(0.0, (event.x - left) / (right - left)))
        width = self.stop_bin - self.start_bin
        center = int(round(fraction * self.dataset.time_bin_count))
        start = max(0, min(center - width // 2, self.dataset.time_bin_count - width))
        self.start_bin = start
        self.stop_bin = start + width
        self._sync_time_scales()

    def schedule_render(self) -> None:
        if self._render_after is not None:
            self.after_cancel(self._render_after)
        self._render_after = self.after(20, self._render)

    def _current_matrix(self) -> tuple[np.ndarray, str]:
        if self.dataset is None or self.unit_map is None:
            raise RuntimeError("No unit is loaded")
        minimum_exposure = self._minimum_exposure()
        metric = self.metric_var.get()
        if metric == METRIC_RATE:
            matrix = aggregate_rate_hz(
                self.unit_map.rate_hz,
                self.dataset.time_edges_sec,
                self.start_bin,
                self.stop_bin,
                self.dataset.exposure_sec,
                minimum_exposure,
            )
            return matrix, "Hz"
        if metric == METRIC_EXPOSURE:
            matrix = self.dataset.exposure_sec.astype(np.float64, copy=True)
            matrix[matrix < minimum_exposure] = np.nan
            return matrix, "s"
        if metric == METRIC_EFFECTIVE_TRIALS:
            matrix = self.dataset.effective_trial_count.astype(np.float64, copy=True)
            matrix[self.dataset.exposure_sec < minimum_exposure] = np.nan
            return matrix, "trials"
        raise ValueError(f"Unknown display metric: {metric}")

    def _render(self) -> None:
        self._render_after = None
        if self.dataset is None or self.unit_map is None:
            return
        try:
            matrix, unit = self._current_matrix()
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        self._display_matrix = matrix
        low, high = finite_display_range(matrix)
        self._display_low, self._display_high = low, high
        rgb = colorize_matrix(matrix, self.palette_var.get(), low, high)
        if self.view_var.get() == VIEW_3D:
            self._render_sphere_map(rgb, low, high, unit)
        else:
            self._render_2d_map(np.flipud(rgb), low, high, unit)
        self._render_timeline()
        visible_bin_count = np.count_nonzero(np.isfinite(matrix))
        self.status_var.set(
            f"Unit {self.unit_map.unit_id} · {visible_bin_count:,} visible bins"
        )

    def _render_2d_map(
        self, rgb: np.ndarray, low: float, high: float, unit: str
    ) -> None:
        assert self.dataset is not None and self.unit_map is not None
        canvas_width = max(300, self.heat_canvas.winfo_width())
        canvas_height = max(220, self.heat_canvas.winfo_height())
        left, right, top, bottom = 62, 82, 26, 54
        available_width = max(1, canvas_width - left - right)
        available_height = max(1, canvas_height - top - bottom)
        ratio = self.dataset.azimuth_count / self.dataset.elevation_count
        plot_width = min(available_width, int(available_height * ratio))
        plot_height = min(available_height, int(plot_width / ratio))
        if plot_height < available_height and plot_width == available_width:
            plot_height = int(plot_width / ratio)
        x0 = left + (available_width - plot_width) / 2
        y0 = top + (available_height - plot_height) / 2
        x1, y1 = x0 + plot_width, y0 + plot_height
        self._plot_bounds = (x0, y0, x1, y1)
        self._sphere_bounds = (0.0, 0.0, 0.0, 0.0)

        image = Image.fromarray(rgb).resize(
            (max(1, plot_width), max(1, plot_height)),
            resample=Image.Resampling.NEAREST,
        )
        self._heat_photo = ImageTk.PhotoImage(image)
        canvas = self.heat_canvas
        canvas.delete("all")
        canvas.create_image(x0, y0, anchor="nw", image=self._heat_photo)
        canvas.create_rectangle(x0, y0, x1, y1, outline="#697586", width=1)

        azimuth_ticks = (
            (0.0, "−180"),
            (0.25, "−90"),
            (0.5, "0"),
            (0.75, "90"),
            (1.0, "180"),
        )
        for fraction, label in azimuth_ticks:
            x = x0 + fraction * plot_width
            canvas.create_line(x, y1, x, y1 + 5, fill="#8792a2")
            canvas.create_text(
                x,
                y1 + 18,
                text=label,
                fill="#aeb7c4",
                font=("TkDefaultFont", 10),
            )
        elevation_ticks = (
            (0.0, "90"),
            (0.25, "45"),
            (0.5, "0"),
            (0.75, "−45"),
            (1.0, "−90"),
        )
        for fraction, label in elevation_ticks:
            y = y0 + fraction * plot_height
            canvas.create_line(x0 - 5, y, x0, y, fill="#8792a2")
            canvas.create_text(
                x0 - 11,
                y,
                text=label,
                anchor="e",
                fill="#aeb7c4",
                font=("TkDefaultFont", 10),
            )
        canvas.create_text(
            (x0 + x1) / 2,
            y1 + 40,
            text="Head-centric azimuth (deg)",
            fill="#d8dde5",
            font=("TkDefaultFont", 11, "bold"),
        )
        canvas.create_text(
            17,
            (y0 + y1) / 2,
            text="Elevation (deg)",
            angle=90,
            fill="#d8dde5",
            font=("TkDefaultFont", 11, "bold"),
        )
        canvas.create_text(
            x0,
            12,
            anchor="w",
            text=f"Unit {self.unit_map.unit_id} · {self.metric_var.get()}",
            fill="#f2f4f7",
            font=("TkDefaultFont", 12, "bold"),
        )
        self._draw_legend(x1 + 22, y0, max(80, plot_height), low, high, unit)

    def _render_sphere_map(
        self, rgb: np.ndarray, low: float, high: float, unit: str
    ) -> None:
        assert self.dataset is not None and self.unit_map is not None
        canvas = self.heat_canvas
        canvas_width = max(300, canvas.winfo_width())
        canvas_height = max(220, canvas.winfo_height())
        left, right, top, bottom = 24, 92, 34, 44
        available_width = max(3, canvas_width - left - right)
        available_height = max(3, canvas_height - top - bottom)
        diameter = max(3, int(min(available_width, available_height)))
        x0 = left + (available_width - diameter) / 2
        y0 = top + (available_height - diameter) / 2
        x1, y1 = x0 + diameter, y0 + diameter
        center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
        radius = diameter / 2
        self._sphere_bounds = (x0, y0, x1, y1)
        self._plot_bounds = (0.0, 0.0, 0.0, 0.0)

        rgba = render_spherical_texture(
            rgb,
            self.dataset.azimuth_centers_deg,
            self.dataset.elevation_centers_deg,
            diameter,
            self._sphere_yaw_deg,
            self._sphere_pitch_deg,
        )
        self._heat_photo = ImageTk.PhotoImage(Image.fromarray(rgba))
        canvas.delete("all")
        canvas.create_image(x0, y0, anchor="nw", image=self._heat_photo)
        self._draw_sphere_grid(center_x, center_y, radius)
        canvas.create_oval(x0, y0, x1, y1, outline="#a2adbb", width=2)
        canvas.create_text(
            x0,
            14,
            anchor="w",
            text=f"Unit {self.unit_map.unit_id} · {self.metric_var.get()} · 3D sphere",
            fill="#f2f4f7",
            font=("TkDefaultFont", 12, "bold"),
        )
        canvas.create_text(
            center_x,
            y1 + 25,
            text=(
                f"center az {self._sphere_yaw_deg:.1f}° · "
                f"el {self._sphere_pitch_deg:.1f}° · drag to rotate"
            ),
            fill="#aeb7c4",
            font=("TkDefaultFont", 10),
        )
        self._draw_legend(x1 + 20, y0, max(80, diameter), low, high, unit)

    def _draw_sphere_grid(self, center_x: float, center_y: float, radius: float) -> None:
        canvas = self.heat_canvas

        def draw_curve(azimuth: np.ndarray, elevation: np.ndarray) -> None:
            x, y, depth = project_head_angles_to_sphere(
                azimuth,
                elevation,
                self._sphere_yaw_deg,
                self._sphere_pitch_deg,
            )
            points: list[float] = []
            for x_value, y_value, depth_value in zip(x, y, depth, strict=True):
                if depth_value >= 0.0:
                    points.extend(
                        (
                            center_x + float(x_value) * radius,
                            center_y - float(y_value) * radius,
                        )
                    )
                elif len(points) >= 4:
                    canvas.create_line(
                        *points, fill="#657080", width=1, smooth=True
                    )
                    points = []
                else:
                    points = []
            if len(points) >= 4:
                canvas.create_line(*points, fill="#657080", width=1, smooth=True)

        elevation_line = np.linspace(-90.0, 90.0, 181)
        for azimuth_value in np.arange(-180.0, 180.0, 45.0):
            draw_curve(
                np.full_like(elevation_line, azimuth_value), elevation_line
            )
        azimuth_line = np.linspace(-180.0, 180.0, 361)
        for elevation_value in (-60.0, -30.0, 0.0, 30.0, 60.0):
            draw_curve(
                azimuth_line, np.full_like(azimuth_line, elevation_value)
            )

    def _draw_legend(
        self, x: float, y: float, height: int, low: float, high: float, unit: str
    ) -> None:
        values = np.linspace(high, low, 256, dtype=float)[:, None]
        rgb = colorize_matrix(values, self.palette_var.get(), low, high)
        image = Image.fromarray(rgb).resize((16, height))
        self._legend_photo = ImageTk.PhotoImage(image)
        self.heat_canvas.create_image(x, y, anchor="nw", image=self._legend_photo)
        self.heat_canvas.create_text(
            x + 22, y, anchor="w", text=f"{high:.3g}", fill="#cbd2dc"
        )
        self.heat_canvas.create_text(
            x + 22, y + height, anchor="w", text=f"{low:.3g}", fill="#cbd2dc"
        )
        self.heat_canvas.create_text(
            x + 8, y + height + 18, text=unit, fill="#8f9aa9"
        )

    def _render_timeline(self) -> None:
        canvas = self.timeline_canvas
        canvas.delete("all")
        if self.dataset is None or self.unit_map is None:
            canvas.create_text(
                canvas.winfo_width() / 2,
                65,
                text="Unit timeline",
                fill="#7f8997",
            )
            return
        try:
            minimum_exposure = self._minimum_exposure()
        except ValueError:
            return
        values = spatial_mean_timeline_hz(
            self.unit_map.rate_hz,
            self.dataset.exposure_sec,
            minimum_exposure,
        )
        width, height = max(120, canvas.winfo_width()), max(100, canvas.winfo_height())
        left, right, top, bottom = 44.0, width - 14.0, 18.0, height - 25.0
        edges_ms = self.dataset.time_edges_sec * 1000.0
        span = edges_ms[-1] - edges_ms[0]
        x_edges = left + (edges_ms - edges_ms[0]) / span * (right - left)
        selection_left = x_edges[self.start_bin]
        selection_right = x_edges[self.stop_bin]
        canvas.create_rectangle(
            selection_left,
            top,
            selection_right,
            bottom,
            fill="#243a54",
            outline="",
        )
        finite = values[np.isfinite(values)]
        peak = float(np.max(finite)) if finite.size else 1.0
        if peak <= 0:
            peak = 1.0
        centers = (x_edges[:-1] + x_edges[1:]) / 2
        points: list[float] = []
        for x, value in zip(centers, values, strict=True):
            if not math.isfinite(float(value)):
                continue
            y = bottom - float(value) / peak * (bottom - top)
            points.extend((float(x), y))
        if len(points) >= 4:
            canvas.create_line(*points, fill="#63d3c5", width=2, smooth=True)
        canvas.create_line(left, bottom, right, bottom, fill="#657080")
        canvas.create_text(
            left,
            bottom + 14,
            anchor="w",
            text=f"{edges_ms[0]:.0f} ms",
            fill="#929dab",
        )
        canvas.create_text(
            right,
            bottom + 14,
            anchor="e",
            text=f"{edges_ms[-1]:.0f} ms",
            fill="#929dab",
        )
        canvas.create_text(left, 8, anchor="w", text="Spatial mean response", fill="#d0d6df")
        canvas.create_text(right, 8, anchor="e", text=f"peak {peak:.3g} Hz", fill="#929dab")

    def _heat_hover(self, event: Any) -> None:
        if self.dataset is None or self._display_matrix is None:
            return
        if self.view_var.get() == VIEW_3D:
            self._sphere_hover(event)
            return
        x0, y0, x1, y1 = self._plot_bounds
        if not (x0 <= event.x < x1 and y0 <= event.y < y1):
            self.hover_var.set("")
            return
        x_fraction = (event.x - x0) / (x1 - x0)
        y_fraction = (event.y - y0) / (y1 - y0)
        az_index = min(
            self.dataset.azimuth_count - 1,
            max(0, int(x_fraction * self.dataset.azimuth_count)),
        )
        elevation_from_top = min(
            self.dataset.elevation_count - 1,
            max(0, int(y_fraction * self.dataset.elevation_count)),
        )
        el_index = self.dataset.elevation_count - 1 - elevation_from_top
        value = float(self._display_matrix[el_index, az_index])
        value_text = "no data" if not math.isfinite(value) else f"{value:.4g}"
        exposure = self.dataset.exposure_sec[el_index, az_index]
        self.hover_var.set(
            f"az {self.dataset.azimuth_centers_deg[az_index]:.1f}° · "
            f"el {self.dataset.elevation_centers_deg[el_index]:.1f}° · "
            f"value {value_text} · exposure {exposure:.4g} s"
        )

    def _sphere_hover(self, event: Any) -> None:
        assert self.dataset is not None and self._display_matrix is not None
        x0, y0, x1, y1 = self._sphere_bounds
        radius = (x1 - x0) / 2
        if radius <= 0:
            self.hover_var.set("")
            return
        x_normalized = (float(event.x) - (x0 + x1) / 2) / radius
        y_normalized = ((y0 + y1) / 2 - float(event.y)) / radius
        angles = head_angles_from_sphere_point(
            x_normalized,
            y_normalized,
            self._sphere_yaw_deg,
            self._sphere_pitch_deg,
        )
        if angles is None:
            self.hover_var.set("Drag the sphere to rotate it; double-click to reset.")
            return
        azimuth, elevation = angles
        az_index = int(
            _nearest_center_indices(
                self.dataset.azimuth_centers_deg,
                np.asarray(azimuth),
                circular=True,
            )
        )
        el_index = int(
            _nearest_center_indices(
                self.dataset.elevation_centers_deg,
                np.asarray(elevation),
                circular=False,
            )
        )
        value = float(self._display_matrix[el_index, az_index])
        value_text = "no data" if not math.isfinite(value) else f"{value:.4g}"
        exposure = self.dataset.exposure_sec[el_index, az_index]
        self.hover_var.set(
            f"3D · az {self.dataset.azimuth_centers_deg[az_index]:.1f}° · "
            f"el {self.dataset.elevation_centers_deg[el_index]:.1f}° · "
            f"value {value_text} · exposure {exposure:.4g} s"
        )

    def _close(self) -> None:
        self._closed = True
        if self._render_after is not None:
            self.after_cancel(self._render_after)
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def self_test(path: str | Path) -> dict[str, object]:
    dataset = load_free_moving_rfmap(path)
    unit = dataset.load_unit(0)
    start = min(_nearest_edge_index(dataset.time_edges_sec, 0.0), dataset.time_bin_count - 1)
    stop = max(
        start + 1,
        min(_nearest_edge_index(dataset.time_edges_sec, 0.2), dataset.time_bin_count),
    )
    matrix = aggregate_rate_hz(
        unit.rate_hz,
        dataset.time_edges_sec,
        start,
        stop,
        dataset.exposure_sec,
    )
    return {
        "format": "rfmapping_fm_hdf5_v1",
        "version": APP_RELEASE_VERSION,
        "edition": APP_EDITION,
        "unitCount": dataset.unit_count,
        "firstUnitId": unit.unit_id,
        "logicalShape": list(dataset.logical_rate_shape),
        "finiteDisplayBins": int(np.count_nonzero(np.isfinite(matrix))),
        "views": list(VIEWS),
    }


def dnd_self_test() -> None:
    if not TK_AVAILABLE or not DND_AVAILABLE:
        raise RuntimeError("Tk and tkinterdnd2 are required for the DND smoke test")
    root = TkinterDnD.Tk()
    root.withdraw()
    frame = ttk.Frame(root)
    frame.drop_target_register(DND_FILES)
    root.update_idletasks()
    root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="free-moving HDF5 .rfmap")
    parser.add_argument("--self-test", metavar="RFMAP")
    parser.add_argument(DND_SMOKE_ARGUMENT, action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        print(json.dumps(self_test(args.self_test), sort_keys=True))
        return 0
    if args.self_test_dnd:
        dnd_self_test()
        print("TkDND smoke test passed")
        return 0
    if not TK_AVAILABLE:
        parser.error("Tk is unavailable in this Python interpreter")
    viewer = FreeMovingRFViewer(args.path)
    viewer.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
