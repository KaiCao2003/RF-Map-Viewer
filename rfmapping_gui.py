#!/usr/bin/env python3
"""Native Python/Tk viewer for RF mapping spike-count JSON files.

The viewer shares the validated per-unit :class:`Utils.rfmap.RFMap` model used
by notebook analysis. It does not depend on notebook state or a web server.
"""

from __future__ import annotations

import argparse
import csv
import math
import queue
import sys
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from Utils.figure_export import (
    ExportPage,
    ExportPlan,
    FigureFormat,
    PLOT_KIND_REGISTRY,
    PlotKind,
    PlotSpec,
    export_figures,
    render_live_preview,
)
from Utils.hd_tuning import (
    HDTuningData,
    discover_hd_tuning_path,
    load_hd_tuning,
)
from Utils.rfmap import RFMap, RFMapList, load_rf_maps

try:
    from tkinter import filedialog, messagebox, ttk
    import tkinter as tk
    TK_AVAILABLE = True
except ModuleNotFoundError:
    filedialog = messagebox = ttk = None
    TK_AVAILABLE = False

    class _MissingTk:
        Tk = object
        Toplevel = object
        Misc = object
        TclError = ValueError

    tk = _MissingTk()


DEFAULT_JSON_DIR = Path("data")
DEFAULT_JSON = DEFAULT_JSON_DIR / "unitsSpikeCounts_260701_1.json"
APP_VERSION = "1.9.0"
INNER_BLANK_ROWS = 4
POLAR_PAD_ROWS = 1
STARTUP_EVENT_WAIT_MS = 350
DEFAULT_RF_SUM_START_MS = 0.0
DEFAULT_RF_SUM_END_MS = 200.0
VALUE_MODE_COUNT = "Spike count"
VALUE_MODE_PER_PRESENTATION = "Spikes / presentation"
VALUE_MODE_RATE = "Mean firing rate (Hz)"
VALUE_MODES = (VALUE_MODE_COUNT, VALUE_MODE_PER_PRESENTATION, VALUE_MODE_RATE)
PALETTES = ("Gray", "Viridis", "Inferno")
POLAR_RADIUS_MODES = ("MATLAB row 1 inner", "Display bottom inner")
AxisGroup = tuple[int, int]
CellRef = tuple[int, int, int, int]
PAIR_SYNC_ALL_FIELDS = frozenset(
    {
        "unit",
        "value_mode",
        "active_time",
        "timeline_selection",
        "rf_range",
        "time_resolution",
        "x_bins",
        "y_bins",
        "smoothing",
        "flip_y",
        "palette",
        "polar_radius",
        "spatial_format",
        "delay_rgb",
        "selected_cell",
        "timeline_scroll",
        "selected_tab",
    }
)


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


def startup_json_path() -> Path:
    """Return a real startup document without opening a modal dialog.

    Finder delivers ``Open With`` paths as an Apple event, after Tk has created
    its application object.  A modal chooser here would prevent Tk from
    installing and servicing that event handler.  The bundled JSON is only a
    temporary document; an initial OpenDocument event replaces it as soon as
    the event loop starts.
    """
    if getattr(sys, "frozen", False):
        resources = Path(sys.executable).resolve().parent.parent / "Resources"
        return latest_json_path(resources)
    return latest_json_path()


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


@dataclass(frozen=True)
class ViewerSyncState:
    """Persistent viewer controls shared by paired windows.

    The selected unit is stored by cluster ID rather than by its per-file
    array index so windows with different unit lists can still be paired.
    Time selections are stored in physical milliseconds so files with
    different time axes or display-group resolutions remain synchronized. A
    selected spatial cell is represented by its source-index midpoint for the
    same reason.
    """

    unit_id: int
    value_mode: str
    timeline_bin_center_ms: float
    timeline_selection_start_ms: float
    timeline_selection_end_ms: float
    timeline_anchor_center_ms: float | None
    rf_start_ms: float
    rf_end_ms: float
    time_resolution_ms: float
    x_bins: int
    y_bins: int
    smooth_radius: int
    flip_y: bool
    palette: str
    polar_radius: str
    polar_layout: bool
    rgb_mode: bool
    selected_cell_y_midpoint: float | None
    selected_cell_x_midpoint: float | None
    timeline_scroll_fraction: float
    selected_tab: str

    def changed_fields(self, baseline: ViewerSyncState) -> frozenset[str]:
        fields: set[str] = set()
        if self.unit_id != baseline.unit_id:
            fields.add("unit")
        if self.value_mode != baseline.value_mode:
            fields.add("value_mode")
        if self.timeline_bin_center_ms != baseline.timeline_bin_center_ms:
            fields.add("active_time")
        if (
            self.timeline_selection_start_ms != baseline.timeline_selection_start_ms
            or self.timeline_selection_end_ms != baseline.timeline_selection_end_ms
            or self.timeline_anchor_center_ms != baseline.timeline_anchor_center_ms
        ):
            fields.add("timeline_selection")
        if self.rf_start_ms != baseline.rf_start_ms or self.rf_end_ms != baseline.rf_end_ms:
            fields.add("rf_range")
        if self.time_resolution_ms != baseline.time_resolution_ms:
            fields.add("time_resolution")
        if self.x_bins != baseline.x_bins:
            fields.add("x_bins")
        if self.y_bins != baseline.y_bins:
            fields.add("y_bins")
        if self.smooth_radius != baseline.smooth_radius:
            fields.add("smoothing")
        if self.flip_y != baseline.flip_y:
            fields.add("flip_y")
        if self.palette != baseline.palette:
            fields.add("palette")
        if self.polar_radius != baseline.polar_radius:
            fields.add("polar_radius")
        if self.polar_layout != baseline.polar_layout:
            fields.add("spatial_format")
        if self.rgb_mode != baseline.rgb_mode:
            fields.add("delay_rgb")
        if (
            self.selected_cell_y_midpoint != baseline.selected_cell_y_midpoint
            or self.selected_cell_x_midpoint != baseline.selected_cell_x_midpoint
        ):
            fields.add("selected_cell")
        if abs(self.timeline_scroll_fraction - baseline.timeline_scroll_fraction) > 1e-6:
            fields.add("timeline_scroll")
        if self.selected_tab != baseline.selected_tab:
            fields.add("selected_tab")
        return frozenset(fields)

    def merging(
        self,
        incoming: ViewerSyncState,
        fields: frozenset[str],
    ) -> ViewerSyncState:
        updates: dict[str, object] = {}
        if "unit" in fields:
            updates["unit_id"] = incoming.unit_id
        if "value_mode" in fields:
            updates["value_mode"] = incoming.value_mode
        if "active_time" in fields:
            updates["timeline_bin_center_ms"] = incoming.timeline_bin_center_ms
        if "timeline_selection" in fields:
            updates.update(
                timeline_selection_start_ms=incoming.timeline_selection_start_ms,
                timeline_selection_end_ms=incoming.timeline_selection_end_ms,
                timeline_anchor_center_ms=incoming.timeline_anchor_center_ms,
            )
        if "rf_range" in fields:
            updates.update(rf_start_ms=incoming.rf_start_ms, rf_end_ms=incoming.rf_end_ms)
        if "time_resolution" in fields:
            updates["time_resolution_ms"] = incoming.time_resolution_ms
        if "x_bins" in fields:
            updates["x_bins"] = incoming.x_bins
        if "y_bins" in fields:
            updates["y_bins"] = incoming.y_bins
        if "smoothing" in fields:
            updates["smooth_radius"] = incoming.smooth_radius
        if "flip_y" in fields:
            updates["flip_y"] = incoming.flip_y
        if "palette" in fields:
            updates["palette"] = incoming.palette
        if "polar_radius" in fields:
            updates["polar_radius"] = incoming.polar_radius
        if "spatial_format" in fields:
            updates["polar_layout"] = incoming.polar_layout
        if "delay_rgb" in fields:
            updates["rgb_mode"] = incoming.rgb_mode
        if "selected_cell" in fields:
            updates.update(
                selected_cell_y_midpoint=incoming.selected_cell_y_midpoint,
                selected_cell_x_midpoint=incoming.selected_cell_x_midpoint,
            )
        if "timeline_scroll" in fields:
            updates["timeline_scroll_fraction"] = incoming.timeline_scroll_fraction
        if "selected_tab" in fields:
            updates["selected_tab"] = incoming.selected_tab
        return replace(self, **updates)


class RFMappingData:
    """GUI adapter around the shared, validated per-unit RFMap model."""

    def __init__(self, path: Path):
        self.path = path
        self.rf_maps: RFMapList = load_rf_maps(path)
        first = self.rf_maps[0]
        self.n_units = len(self.rf_maps)
        self.n_y = first.n_y
        self.n_x = first.n_x
        self.n_bins = first.n_time_bins
        self.size = (self.n_units, self.n_y, self.n_x, self.n_bins)
        self.counts = [rf_map.spike_counts for rf_map in self.rf_maps]
        self.unit_pool = list(self.rf_maps.unit_ids)
        self.x_positions = first.x_positions.tolist()
        self.y_positions = first.y_positions.tolist()
        self.time_bin_edges = first.time_bin_edges_s.tolist()
        self.presentation_counts = (
            first.presentation_counts.tolist()
            if first.presentation_counts is not None
            else None
        )
        self._metrics_cache: dict[int, UnitMetrics] = {}
        self._hd_tuning_lock = threading.Lock()
        self._hd_tuning_checked = False
        self._hd_tuning: HDTuningData | None = None
        self._hd_tuning_error: str | None = None

    def rf_map(self, unit_idx: int) -> RFMap:
        """Return the shared per-unit object for an original JSON unit index."""

        return self.rf_maps.by_index(unit_idx)

    def rf_map_by_unit_id(self, unit_id: int) -> RFMap:
        """Return a per-unit object by its recorded cluster/unit ID."""

        return self.rf_maps.by_unit_id(unit_id)

    def hd_tuning(self) -> HDTuningData | None:
        """Lazily discover and validate the companion HD tuning JSON."""

        if self._hd_tuning_checked:
            return self._hd_tuning
        # Preview rendering runs on Tk's main thread while final export runs on
        # a worker.  Publish the checked flag only after discovery/loading is
        # complete so another caller can never observe a false "missing" state.
        with self._hd_tuning_lock:
            if self._hd_tuning_checked:
                return self._hd_tuning
            tuning: HDTuningData | None = None
            error: str | None = None
            tuning_path = discover_hd_tuning_path(self.path)
            if tuning_path is not None:
                try:
                    tuning = load_hd_tuning(tuning_path)
                except Exception as exc:
                    error = str(exc)
            self._hd_tuning = tuning
            self._hd_tuning_error = error
            self._hd_tuning_checked = True
        return self._hd_tuning

    @property
    def hd_tuning_error(self) -> str | None:
        self.hd_tuning()
        return self._hd_tuning_error

    def _validate(self) -> None:
        if any(size <= 0 for size in self.size):
            raise ValueError(f"unitsSpikeCountsSize values must be positive, got {self.size!r}")
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
        if not all(math.isfinite(value) for value in self.x_positions):
            raise ValueError("xPositions must contain only finite values")
        if not all(math.isfinite(value) for value in self.y_positions):
            raise ValueError("yPositions must contain only finite values")
        if not all(math.isfinite(value) for value in self.time_bin_edges):
            raise ValueError("timeBinEdges must contain only finite values")
        if not all(left < right for left, right in zip(self.time_bin_edges, self.time_bin_edges[1:])):
            raise ValueError("timeBinEdges must be strictly increasing")

        if self.presentation_counts is not None:
            if not isinstance(self.presentation_counts, list):
                raise ValueError("stimulusPresentationCounts must be a y-by-x array")
            if len(self.presentation_counts) != self.n_y:
                raise ValueError("stimulusPresentationCounts y dimension does not match unitsSpikeCountsSize")
            for y_idx, row in enumerate(self.presentation_counts):
                if not isinstance(row, list):
                    raise ValueError(f"stimulusPresentationCounts row {y_idx} must be an array")
                if len(row) != self.n_x:
                    raise ValueError(
                        f"stimulusPresentationCounts row {y_idx} x dimension does not match unitsSpikeCountsSize"
                    )
                for x_idx, value in enumerate(row):
                    if (
                        not isinstance(value, (int, float))
                        or isinstance(value, bool)
                        or not math.isfinite(value)
                        or value < 0
                        or abs(value - round(value)) > 1e-9
                    ):
                        raise ValueError(
                            "stimulusPresentationCounts values must be finite, non-negative integers "
                            f"(y {y_idx}, x {x_idx})"
                        )

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
                    try:
                        valid_hist = min(hist) >= 0 and all(map(math.isfinite, hist))
                    except (TypeError, ValueError):
                        valid_hist = False
                    if not valid_hist:
                        for bin_idx, value in enumerate(hist):
                            if not isinstance(value, (int, float)) or isinstance(value, bool):
                                raise ValueError(
                                    f"Unit {unit_idx}, y {y_idx}, x {x_idx}, bin {bin_idx} is not numeric"
                                )
                            if not math.isfinite(value) or value < 0:
                                raise ValueError(
                                    f"Unit {unit_idx}, y {y_idx}, x {x_idx}, bin {bin_idx} "
                                    "must be finite and non-negative"
                                )

        if self.presentation_counts is not None:
            for y_idx, row in enumerate(self.presentation_counts):
                for x_idx, presentations in enumerate(row):
                    if presentations == 0 and any(
                        float(self.counts[unit_idx][y_idx][x_idx][bin_idx]) != 0.0
                        for unit_idx in range(self.n_units)
                        for bin_idx in range(self.n_bins)
                    ):
                        raise ValueError(
                            "stimulusPresentationCounts is zero where spike counts are nonzero "
                            f"(y {y_idx}, x {x_idx})"
                        )

    def display_y_indices(self, flip_y: bool = True) -> list[int]:
        if flip_y:
            return list(range(self.n_y - 1, -1, -1))
        return list(range(self.n_y))

    def cluster_id(self, unit_idx: int) -> int:
        return self.rf_map(unit_idx).unit_id

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
        if mode == "Total":
            metrics = self.metrics(unit_idx)
            return clone_matrix(metrics.total)
        if mode == "Peak":
            metrics = self.metrics(unit_idx)
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
            summed = self.rf_map(unit_idx).sum_between_s(
                self.time_bin_edges[start],
                self.time_bin_edges[end + 1],
            )
            return summed.spike_counts[..., 0].astype(float).tolist()
        raise ValueError(f"Unknown RF mode: {mode}")

    def supports_value_mode(self, value_mode: str) -> bool:
        if value_mode == VALUE_MODE_COUNT:
            return True
        if value_mode in {VALUE_MODE_PER_PRESENTATION, VALUE_MODE_RATE}:
            return self.presentation_counts is not None
        return False

    def time_span_seconds(self, start: int, end: int) -> float:
        requested_start, requested_end = min(start, end), max(start, end)
        start = max(0, min(self.n_bins - 1, requested_start))
        end = max(0, min(self.n_bins - 1, requested_end))
        return self.time_bin_edges[end + 1] - self.time_bin_edges[start]

    def response_value(
        self,
        unit_idx: int,
        y_idx: int,
        x_idx: int,
        start: int,
        end: int,
        value_mode: str,
    ) -> float | None:
        requested_start, requested_end = min(start, end), max(start, end)
        start = max(0, min(self.n_bins - 1, requested_start))
        end = max(0, min(self.n_bins - 1, requested_end))
        count = float(sum(self.counts[unit_idx][y_idx][x_idx][start : end + 1]))
        if value_mode == VALUE_MODE_COUNT:
            return count
        if value_mode not in VALUE_MODES:
            raise ValueError(f"Unknown value mode: {value_mode}")
        if not self.supports_value_mode(value_mode):
            raise ValueError(
                f"{value_mode} requires stimulusPresentationCounts metadata in the JSON file."
            )
        presentation_counts = self.presentation_counts
        if presentation_counts is None:
            raise ValueError("stimulusPresentationCounts metadata is unavailable")
        presentations = presentation_counts[y_idx][x_idx]
        if presentations <= 0:
            return None
        if value_mode == VALUE_MODE_PER_PRESENTATION:
            return count / presentations
        if value_mode == VALUE_MODE_RATE:
            duration = self.time_span_seconds(start, end)
            return count / (presentations * duration)
        raise ValueError(f"Unknown value mode: {value_mode}")

    def response_matrix(
        self,
        unit_idx: int,
        start: int,
        end: int,
        value_mode: str,
    ) -> list[list[float | None]]:
        requested_start, requested_end = min(start, end), max(start, end)
        start = max(0, min(self.n_bins - 1, requested_start))
        end = max(0, min(self.n_bins - 1, requested_end))
        count_matrix = self.aggregate_matrix(unit_idx, "Range sum", 0, start, end)
        if value_mode == VALUE_MODE_COUNT:
            return count_matrix
        if value_mode not in VALUE_MODES:
            raise ValueError(f"Unknown value mode: {value_mode}")
        if not self.supports_value_mode(value_mode):
            raise ValueError(
                f"{value_mode} requires stimulusPresentationCounts metadata in the JSON file."
            )
        presentation_counts = self.presentation_counts
        if presentation_counts is None:
            raise ValueError("stimulusPresentationCounts metadata is unavailable")
        duration = self.time_span_seconds(start, end)
        return [
            [
                None
                if presentation_counts[y_idx][x_idx] <= 0
                else count_matrix[y_idx][x_idx]
                / (
                    presentation_counts[y_idx][x_idx]
                    * (duration if value_mode == VALUE_MODE_RATE else 1.0)
                )
                for x_idx in range(self.n_x)
            ]
            for y_idx in range(self.n_y)
        ]


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


def value_mode_unit(value_mode: str) -> str:
    if value_mode == VALUE_MODE_COUNT:
        return "spikes"
    if value_mode == VALUE_MODE_PER_PRESENTATION:
        return "spikes/presentation"
    if value_mode == VALUE_MODE_RATE:
        return "Hz"
    raise ValueError(f"Unknown value mode: {value_mode}")


def value_mode_slug(value_mode: str) -> str:
    if value_mode == VALUE_MODE_COUNT:
        return "spike_count"
    if value_mode == VALUE_MODE_PER_PRESENTATION:
        return "spikes_per_presentation"
    if value_mode == VALUE_MODE_RATE:
        return "mean_firing_rate_hz"
    raise ValueError(f"Unknown value mode: {value_mode}")


def value_mode_suffix(value_mode: str) -> str:
    if value_mode == VALUE_MODE_COUNT:
        return " spikes"
    if value_mode == VALUE_MODE_PER_PRESENTATION:
        return " sp/pres"
    if value_mode == VALUE_MODE_RATE:
        return " Hz"
    raise ValueError(f"Unknown value mode: {value_mode}")


def format_response_value(value: float | None, value_mode: str) -> str:
    if value is None:
        return "n/a"
    if value_mode == VALUE_MODE_COUNT:
        return f"{value:.0f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


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
    tiles: list[tuple[list[list[float | None]], float, float, float]],
    width: int,
    height: int,
    color_for_value: Callable[[float | None], str],
) -> bytes:
    """Rasterize many equally-scaled matrices into one white PPM atlas."""
    width = max(1, int(width))
    height = max(1, int(height))
    pixels = bytearray(b"\xff" * (width * height * 3))
    color_cache: dict[str, bytes] = {}

    for matrix, origin_x, origin_y, cell_size in tiles:
        rows = len(matrix)
        cols = len(matrix[0]) if rows else 0
        if any(len(row) != cols for row in matrix):
            raise ValueError("Cannot rasterize a ragged matrix")
        for row_idx, row in enumerate(matrix):
            y0 = max(0, min(height, int(round(origin_y + row_idx * cell_size))))
            y1 = max(y0, min(height, int(round(origin_y + (row_idx + 1) * cell_size))))
            for col_idx, value in enumerate(row):
                x0 = max(0, min(width, int(round(origin_x + col_idx * cell_size))))
                x1 = max(x0, min(width, int(round(origin_x + (col_idx + 1) * cell_size))))
                if x1 <= x0 or y1 <= y0:
                    continue
                color = color_for_value(value).lower()
                rgb = color_cache.get(color)
                if rgb is None:
                    raw = color.lstrip("#")
                    if len(raw) != 6:
                        raise ValueError(f"Expected #RRGGBB color, got {color!r}")
                    rgb = bytes(int(raw[index : index + 2], 16) for index in (0, 2, 4))
                    color_cache[color] = rgb
                scanline = rgb * (x1 - x0)
                for pixel_y in range(y0, y1):
                    offset = (pixel_y * width + x0) * 3
                    pixels[offset : offset + len(scanline)] = scanline

    return f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(pixels)


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

    for matrix, origin_x, origin_y, scale, total_deg, ring_rows in tiles:
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
                raw = color_for_value(value).lstrip("#")
                if len(raw) != 6:
                    raise ValueError(f"Expected #RRGGBB color, got {raw!r}")
                rgb_row.append(bytes(int(raw[index : index + 2], 16) for index in (0, 2, 4)))
            rgb_by_cell.append(rgb_row)

        scale = max(float(scale), 1e-9)
        radius_units = INNER_BLANK_ROWS + rows
        diameter = 2.0 * radius_units * scale
        cx = origin_x + diameter / 2.0
        cy = origin_y + diameter / 2.0
        x_start = max(0, int(math.floor(origin_x)))
        x_end = min(width, int(math.ceil(origin_x + diameter)))
        y_start = max(0, int(math.floor(origin_y)))
        y_end = min(height, int(math.ceil(origin_y + diameter)))
        column_span = total_deg / cols
        theta_start = 90.0 + total_deg / 2.0
        theta_end = 90.0 - total_deg / 2.0

        for pixel_y in range(y_start, y_end):
            dy = (cy - (pixel_y + 0.5)) / scale
            for pixel_x in range(x_start, x_end):
                dx = ((pixel_x + 0.5) - cx) / scale
                radius = math.hypot(dx, dy)
                if not (INNER_BLANK_ROWS <= radius < radius_units):
                    continue
                ring_idx = int(radius - INNER_BLANK_ROWS)
                if not (0 <= ring_idx < len(ring_rows)):
                    continue

                theta_deg = math.degrees(math.atan2(dy, dx))
                if total_deg >= 359.999:
                    relative = (theta_start - theta_deg) % 360.0
                else:
                    while theta_deg > theta_start:
                        theta_deg -= 360.0
                    while theta_deg < theta_end:
                        theta_deg += 360.0
                    if not (theta_end <= theta_deg <= theta_start):
                        continue
                    relative = theta_start - theta_deg
                column = max(0, min(cols - 1, int(relative / column_span)))
                rgb = rgb_by_cell[ring_rows[ring_idx]][column]
                offset = (pixel_y * width + pixel_x) * 3
                pixels[offset : offset + 3] = rgb

    return f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(pixels)


class RFMViewer(tk.Toplevel):
    def __init__(
        self,
        data: RFMappingData | None = None,
        *,
        startup_path: Path | None = None,
        master: tk.Misc | None = None,
    ):
        if (data is None) == (startup_path is None):
            raise ValueError("Provide exactly one of data or startup_path")

        if master is None:
            master = tk.Tk()
            master.withdraw()
        self._app_root = master.winfo_toplevel()
        super().__init__(self._app_root)
        windows = getattr(self._app_root, "_rfm_viewer_windows", None)
        if windows is None:
            windows = []
            self._app_root._rfm_viewer_windows = windows
        windows.append(self)
        if not hasattr(self._app_root, "_rfm_pairing_enabled"):
            self._app_root._rfm_pairing_enabled = False
            self._app_root._rfm_pairing_state = None
            self._app_root._rfm_pairing_broadcasting = False
        self._quitting = False
        self._viewer_ready = False
        self._pair_apply_in_progress = False
        self._pair_last_local_state: ViewerSyncState | None = None
        self._startup_after: str | None = None
        self._redraw_after: str | None = None
        self.title(f"RF Map Viewer {APP_VERSION}")
        self.withdraw()
        self._install_application_handlers()

        if data is not None:
            self._initialize_viewer(data)
        else:
            assert startup_path is not None
            self._startup_after = self.after(
                STARTUP_EVENT_WAIT_MS,
                lambda path=startup_path: self._load_startup_document(path),
            )

    def _initialize_viewer(self, data: RFMappingData) -> None:
        self.data = data
        self.title(f"{data.path.name} — RF Map Viewer {APP_VERSION}")
        self.geometry("1440x900")
        self.minsize(1120, 720)

        self.unit_idx = tk.IntVar(value=0)
        self._selected_unit_id = data.unit_pool[0]
        self._last_supported_unit_id = data.unit_pool[0]
        self.value_mode_var = tk.StringVar(value=VALUE_MODE_COUNT)
        self.bin_var = tk.IntVar(value=0)
        self.range_start_var = tk.IntVar(value=0)
        self.range_end_var = tk.IntVar(value=data.n_bins - 1)
        plot_start_ms, plot_end_ms = self._default_plot_time_bounds_ms()
        self.range_start_ms_var = tk.StringVar(value=format_ms(plot_start_ms))
        self.range_end_ms_var = tk.StringVar(value=format_ms(plot_end_ms))
        self.flip_y_var = tk.BooleanVar(value=False)
        self.palette_var = tk.StringVar(value="Gray")
        self.polar_radius_var = tk.StringVar(value=POLAR_RADIUS_MODES[1])
        self.polar_layout_var = tk.BooleanVar(value=False)
        self.rgb_mode_var = tk.BooleanVar(value=False)
        self.pair_windows_var = tk.BooleanVar(
            value=bool(getattr(self._app_root, "_rfm_pairing_enabled", False))
        )
        self.x_bins_var = tk.IntVar(value=data.n_x)
        self.y_bins_var = tk.IntVar(value=data.n_y)
        self.time_res_ms_var = tk.StringVar(value=format_ms(self._base_bin_ms()))
        self._last_time_group_count = data.n_bins
        self._last_time_groups = [(index, index) for index in range(data.n_bins)]
        self.smooth_radius_var = tk.IntVar(value=0)
        self.selected_cell: CellRef | None = None
        self.hover_cell: CellRef | None = None
        self.json_paths: list[Path] = []
        self._json_choice_to_path: dict[str, Path] = {}
        self._canvas_layouts: dict[str, dict[str, object]] = {}
        self._timeline_cells: list[dict[str, object]] = []
        self._timeline_cells_by_bin: dict[int, dict[str, object]] = {}
        self._timeline_preview_cache_key: tuple[object, ...] | None = None
        self._timeline_preview_images: dict[int, object] = {}
        self._timeline_preview_high = 1.0
        self._timeline_range_anchor: int | None = None
        self._timeline_scroll_fraction = 0.0
        self._restoring_timeline_scroll = False
        self._tab_keys: dict[str, str] = {}
        self._hover_signature: tuple[object, ...] | None = None
        self._hover_tooltip_text = ""

        self._build_style()
        self._build_layout()
        self._build_menu()
        self._wire_events()
        self._sync_json_combo()
        self._sync_unit_combo()
        self._update_all()
        self._viewer_ready = True
        self._pair_ready_viewer_set_changed(adopt_viewer=self)
        self.deiconify()
        self.lift()
        self.after_idle(lambda: self.canvases["rf"].focus_set())

    def _load_startup_document(self, path: Path) -> None:
        self._startup_after = None
        if self._quitting or self._viewer_ready:
            return
        if not path.is_file():
            initial_dir = path.parent if path.parent.is_dir() else Path.home()
            selected = filedialog.askopenfilename(
                parent=self,
                title="Open RF mapping JSON",
                initialdir=str(initial_dir),
                filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            )
            if not selected:
                self._quit_application()
                return
            path = Path(selected)
        try:
            data = RFMappingData(path)
        except Exception as exc:
            messagebox.showerror("Could not open JSON", str(exc), parent=self)
            self._quit_application()
            return
        self._initialize_viewer(data)

    def _cancel_startup_callback(self) -> None:
        if self._startup_after is None:
            return
        try:
            self.after_cancel(self._startup_after)
        except tk.TclError:
            pass
        self._startup_after = None

    def destroy(self) -> None:
        self._cancel_startup_callback()
        if self._redraw_after is not None:
            try:
                self.after_cancel(self._redraw_after)
            except tk.TclError:
                pass
            self._redraw_after = None
        windows = getattr(self._app_root, "_rfm_viewer_windows", [])
        if self in windows:
            windows.remove(self)
        if not getattr(self._app_root, "_rfm_quitting", False):
            self._pair_ready_viewer_set_changed()
        try:
            super().destroy()
        except tk.TclError:
            return
        if getattr(self._app_root, "_rfm_quitting", False):
            return
        if windows:
            windows[-1]._install_application_handlers()
            return
        try:
            self._app_root._rfm_quitting = True
            self._app_root.destroy()
        except tk.TclError:
            pass

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

    def _build_menu(self) -> None:
        menu = tk.Menu(self)

        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(
            label="Open JSON in New Window…",
            accelerator="⌘O" if sys.platform == "darwin" else "Ctrl+O",
            command=self._open_json,
        )
        file_menu.add_command(
            label="Export Figures…",
            accelerator="⌘E" if sys.platform == "darwin" else "Ctrl+E",
            command=self._open_figure_exporter,
        )
        file_menu.add_command(
            label="Export Displayed Data CSV…",
            accelerator="⇧⌘E" if sys.platform == "darwin" else "Ctrl+Shift+E",
            command=self._export_current_matrix,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Close Window",
            accelerator="⌘W" if sys.platform == "darwin" else "Ctrl+W",
            command=self._close_window,
        )
        menu.add_cascade(label="File", menu=file_menu)

        navigate_menu = tk.Menu(menu, tearoff=False)
        navigate_menu.add_command(label="Previous Unit", accelerator="←  or  [", command=lambda: self._step_unit(-1))
        navigate_menu.add_command(label="Next Unit", accelerator="→  or  ]", command=lambda: self._step_unit(1))
        navigate_menu.add_separator()
        navigate_menu.add_command(label="Previous Timeline Bin", accelerator="↑", command=lambda: self._step_timeline_bin(-1))
        navigate_menu.add_command(label="Next Timeline Bin", accelerator="↓", command=lambda: self._step_timeline_bin(1))
        navigate_menu.add_command(label="Decrease Time Resolution 1 ms", accelerator="⇧,", command=lambda: self._step_time_resolution(-1.0))
        navigate_menu.add_command(label="Increase Time Resolution 1 ms", accelerator="⇧.", command=lambda: self._step_time_resolution(1.0))
        navigate_menu.add_separator()
        navigate_menu.add_command(
            label="Show Full Timeline Range",
            accelerator="Esc",
            command=self._clear_timeline_selection,
        )
        menu.add_cascade(label="Navigate", menu=navigate_menu)

        view_menu = tk.Menu(menu, tearoff=False)
        for tab_index, title in enumerate(("RF", "Delay / RGB", "Timeline")):
            view_menu.add_command(
                label=title,
                accelerator=str(tab_index + 1),
                command=lambda index=tab_index: self._select_tab(index),
            )
        view_menu.add_separator()
        view_menu.add_command(label="Invert Y", accelerator="F", command=self._toggle_flip_y)
        view_menu.add_command(label="Cycle Palette", accelerator="P", command=self._cycle_palette)
        menu.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="Keyboard Shortcuts", accelerator="?", command=self._show_shortcuts)
        menu.add_cascade(label="Help", menu=help_menu)
        self.configure(menu=menu)
        self._menu = menu

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
        ttk.Label(parent, text=f"RF Map Viewer {APP_VERSION}", style="Title.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        self.data_label = ttk.Label(parent, text="", style="Muted.TLabel", wraplength=260, justify="left")
        self.data_label.grid(row=row, column=0, sticky="ew", pady=(6, 14))
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, sticky="ew", pady=(0, 12))
        row += 1

        ttk.Label(parent, text="Current JSON", style="Panel.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        json_row = ttk.Frame(parent, style="Panel.TFrame")
        json_row.grid(row=row, column=0, sticky="ew", pady=(5, 10))
        json_row.columnconfigure(0, weight=1)
        self.json_combo = ttk.Combobox(json_row, state="readonly", width=23)
        self.json_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(json_row, text="Open…", width=6, command=self._open_json).grid(row=0, column=1, padx=(5, 0))
        row += 1

        ttk.Label(parent, text="Window pairing", style="Panel.TLabel").grid(
            row=row, column=0, sticky="w", pady=(2, 0)
        )
        row += 1
        self.pair_windows_toggle = ttk.Checkbutton(
            parent,
            text="Sync viewer windows",
            variable=self.pair_windows_var,
            command=self._on_pair_windows_toggled,
        )
        self.pair_windows_toggle.grid(row=row, column=0, sticky="w", pady=(5, 0))
        row += 1
        self.pair_status_label = ttk.Label(
            parent,
            text="Open another loaded viewer window to enable sync.",
            style="Muted.TLabel",
            wraplength=260,
            justify="left",
        )
        self.pair_status_label.grid(row=row, column=0, sticky="ew", pady=(4, 10))
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
        ttk.Button(button_frame, text="Export figures…", command=self._open_figure_exporter).grid(
            row=0, column=0, sticky="ew"
        )
        row += 1

        ttk.Label(
            parent,
            text="←/→ unit   ↑/↓ timeline\n⇧,/⇧. time resolution   ? all shortcuts",
            style="Muted.TLabel",
            justify="left",
        ).grid(row=row, column=0, sticky="ew", pady=(0, 8))
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
            ("rf", "RF"),
            ("delay", "Delay / RGB"),
            ("timeline", "Timeline"),
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
        controls.columnconfigure(8, weight=1)

        ttk.Label(controls, text="Value", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.value_mode_combo = ttk.Combobox(
            controls,
            state="readonly",
            values=VALUE_MODES,
            textvariable=self.value_mode_var,
            width=21,
        )
        self.value_mode_combo.grid(row=0, column=1, sticky="w", padx=(0, 18))

        ttk.Label(controls, text="Time resolution (ms)", style="Panel.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.time_res_spin = ttk.Spinbox(
            controls,
            from_=self._base_bin_ms(),
            to=self._total_time_ms(),
            increment=self._base_bin_ms(),
            width=8,
            textvariable=self.time_res_ms_var,
            command=self._on_time_resolution_changed,
        )
        self.time_res_spin.grid(row=0, column=3, sticky="w", padx=(0, 18))

        ttk.Label(controls, text="RF sum range (ms)", style="Panel.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0), padx=(0, 6)
        )
        self.range_start_spin = ttk.Spinbox(
            controls,
            from_=self._time_axis_start_ms(),
            to=self._time_axis_end_ms(),
            increment=self._base_bin_ms(),
            width=8,
            textvariable=self.range_start_ms_var,
            command=self._on_range_changed,
        )
        self.range_start_spin.grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Label(controls, text="to", style="Panel.TLabel").grid(row=1, column=3, padx=6, pady=(8, 0))
        self.range_end_spin = ttk.Spinbox(
            controls,
            from_=self._time_axis_start_ms(),
            to=self._time_axis_end_ms(),
            increment=self._base_bin_ms(),
            width=8,
            textvariable=self.range_end_ms_var,
            command=self._on_range_changed,
        )
        self.range_end_spin.grid(row=1, column=4, sticky="w", pady=(8, 0), padx=(0, 18))

        self.polar_layout_toggle = ttk.Checkbutton(
            controls,
            text="Polar layout",
            variable=self.polar_layout_var,
            command=self._on_spatial_format_changed,
        )
        self.polar_layout_toggle.grid(row=1, column=5, sticky="w", pady=(8, 0), padx=(0, 18))
        self.rgb_mode_toggle = ttk.Checkbutton(
            controls,
            text="RGB composite",
            variable=self.rgb_mode_var,
            command=self._on_control_changed,
        )
        self.rgb_mode_toggle.grid(row=1, column=6, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(
            controls,
            text="Reset 0–200",
            command=self._reset_plot_range,
        ).grid(row=1, column=8, sticky="e", pady=(8, 0), padx=(18, 0))

    def _wire_events(self) -> None:
        self.json_combo.bind("<<ComboboxSelected>>", self._on_json_selected)
        self.unit_combo.bind("<<ComboboxSelected>>", self._on_unit_selected)
        self.value_mode_combo.bind("<<ComboboxSelected>>", self._on_value_mode_changed)
        self.range_start_spin.bind("<Return>", self._on_range_changed)
        self.range_end_spin.bind("<Return>", self._on_range_changed)
        self.range_start_spin.bind("<FocusOut>", self._on_range_changed)
        self.range_end_spin.bind("<FocusOut>", self._on_range_changed)
        self.time_res_spin.bind("<Return>", self._on_time_resolution_changed)
        self.time_res_spin.bind("<FocusOut>", self._on_time_resolution_changed)
        self.x_bins_spin.bind("<Return>", self._on_control_changed)
        self.y_bins_spin.bind("<Return>", self._on_control_changed)
        self.smooth_spin.bind("<Return>", self._on_control_changed)
        self.x_bins_spin.bind("<FocusOut>", self._on_control_changed)
        self.y_bins_spin.bind("<FocusOut>", self._on_control_changed)
        self.smooth_spin.bind("<FocusOut>", self._on_control_changed)
        self.palette_var.trace_add("write", lambda *_: self._on_control_changed())
        self.polar_radius_var.trace_add("write", lambda *_: self._on_control_changed())
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.bind("<FocusIn>", self._on_window_focus, add="+")
        self.bind("<Left>", lambda event: self._run_navigation_shortcut(event, self._step_unit, -1))
        self.bind("<Right>", lambda event: self._run_navigation_shortcut(event, self._step_unit, 1))
        self.bind("<bracketleft>", lambda event: self._run_navigation_shortcut(event, self._step_unit, -1))
        self.bind("<bracketright>", lambda event: self._run_navigation_shortcut(event, self._step_unit, 1))
        self.bind("<Up>", lambda event: self._run_navigation_shortcut(event, self._step_timeline_bin, -1))
        self.bind("<Down>", lambda event: self._run_navigation_shortcut(event, self._step_timeline_bin, 1))
        self.bind("<less>", lambda event: self._run_navigation_shortcut(event, self._step_time_resolution, -1.0))
        self.bind("<greater>", lambda event: self._run_navigation_shortcut(event, self._step_time_resolution, 1.0))
        self.bind("<Escape>", lambda event: self._run_navigation_shortcut(event, self._clear_timeline_selection))
        self.bind("<KeyPress-f>", lambda event: self._run_navigation_shortcut(event, self._toggle_flip_y))
        self.bind("<KeyPress-p>", lambda event: self._run_navigation_shortcut(event, self._cycle_palette))
        self.bind("<question>", lambda event: self._run_navigation_shortcut(event, self._show_shortcuts))
        for tab_index in range(3):
            self.bind(
                f"<KeyPress-{tab_index + 1}>",
                lambda event, index=tab_index: self._run_navigation_shortcut(event, self._select_tab, index),
            )
        self.bind("<Control-e>", lambda _event: self._open_figure_exporter())
        self.bind("<Control-Shift-E>", lambda _event: self._export_current_matrix())
        self.bind("<Control-w>", lambda _event: self._close_window())
        if sys.platform == "darwin":
            self.bind("<Command-e>", lambda _event: self._open_figure_exporter())
            self.bind("<Command-Shift-E>", lambda _event: self._export_current_matrix())
            self.bind("<Command-w>", lambda _event: self._close_window())
        for key, canvas in self.canvases.items():
            canvas.bind("<Configure>", self._schedule_redraw)
            canvas.bind("<Motion>", lambda event, k=key: self._on_canvas_motion(k, event))
            canvas.bind("<Button-1>", lambda event, k=key: self._on_canvas_click(k, event))
            canvas.bind("<Leave>", lambda _event: self._clear_hover())
        self.canvases["timeline"].bind("<MouseWheel>", self._on_timeline_mousewheel)
        self.canvases["timeline"].bind("<Button-4>", self._on_timeline_mousewheel)
        self.canvases["timeline"].bind("<Button-5>", self._on_timeline_mousewheel)

    def _on_window_focus(self, _event: object | None = None) -> None:
        self._app_root._rfm_active_viewer = self

    def _shortcut_uses_editing_widget(self, event: object) -> bool:
        widget = getattr(event, "widget", None)
        return isinstance(widget, (tk.Entry, tk.Text, ttk.Entry, ttk.Spinbox, ttk.Combobox))

    def _run_navigation_shortcut(
        self,
        event: object,
        action: Callable[..., object],
        *args: object,
    ) -> str | None:
        if self._shortcut_uses_editing_widget(event):
            return None
        action(*args)
        return "break"

    def _select_tab(self, tab_index: int) -> None:
        if not hasattr(self, "notebook"):
            return
        tabs = self.notebook.tabs()
        if 0 <= tab_index < len(tabs):
            self.notebook.select(tab_index)

    def _toggle_flip_y(self) -> None:
        self.flip_y_var.set(not self.flip_y_var.get())
        self._on_control_changed()

    def _cycle_palette(self) -> None:
        try:
            index = PALETTES.index(self.palette_var.get())
        except ValueError:
            index = 0
        self.palette_var.set(PALETTES[(index + 1) % len(PALETTES)])

    def _show_shortcuts(self) -> None:
        messagebox.showinfo(
            "Keyboard Shortcuts",
            "← / →   Previous / next unit\n"
            "↑ / ↓   Previous / next timeline bin\n"
            "Shift+, / Shift+.   Time resolution −/+ 1 ms\n"
            "1–3   Switch plot tab\n"
            "F   Invert Y\n"
            "P   Cycle palette\n"
            "Esc   Show Full Timeline Range\n"
            "[ / ]   Previous / next unit (legacy)\n"
            "Command-O   Open JSON in a new window\n"
            "Command-E   Open figure exporter\n"
            "Shift-Command-E   Export displayed data CSV\n"
            "Command-W   Close current window",
            parent=self,
        )

    def _install_application_handlers(self) -> None:
        self.protocol("WM_DELETE_WINDOW", self._close_window)
        self._app_root._rfm_active_viewer = self
        self.bind_all("<Control-o>", self._dispatch_open_json)

        if sys.platform != "darwin":
            return
        try:
            self.bind_all("<Command-o>", self._dispatch_open_json)
            self.tk.createcommand("::tk::mac::OpenDocument", self._dispatch_macos_open_documents)
            self.tk.createcommand("::tk::mac::Quit", self._quit_application)
        except tk.TclError:
            # The in-app Open button and window close protocol remain usable
            # if this Tk build does not expose the macOS application callbacks.
            return

    def _active_viewer(self) -> RFMViewer:
        active = getattr(self._app_root, "_rfm_active_viewer", None)
        windows = getattr(self._app_root, "_rfm_viewer_windows", [])
        return active if active in windows else (windows[-1] if windows else self)

    def _ready_pairing_viewers(self) -> list[RFMViewer]:
        windows = getattr(self._app_root, "_rfm_viewer_windows", [])
        return [
            window
            for window in windows
            if getattr(window, "_viewer_ready", False) and hasattr(window, "data")
        ]

    def _pairing_unit_ids(
        self,
        ready: list[RFMViewer] | None = None,
    ) -> list[int]:
        viewers = self._ready_pairing_viewers() if ready is None else ready
        return sorted(
            {
                int(unit_id)
                for window in viewers
                for unit_id in window.data.unit_pool
            }
        )

    @staticmethod
    def _unit_lists_match(ready: list[RFMViewer]) -> bool:
        if len(ready) < 2:
            return True
        first_units = tuple(int(unit_id) for unit_id in ready[0].data.unit_pool)
        return all(
            tuple(int(unit_id) for unit_id in window.data.unit_pool) == first_units
            for window in ready[1:]
        )

    @staticmethod
    def _next_union_unit_id(unit_ids: list[int], requested: int) -> int:
        if not unit_ids:
            raise ValueError("Cannot select a unit from an empty unit union")
        requested = int(requested)
        if requested in unit_ids:
            return requested
        return next((unit_id for unit_id in unit_ids if unit_id > requested), unit_ids[0])

    def _local_unit_index(self, unit_id: int) -> int | None:
        lookup = getattr(self.data, "rf_map_by_unit_id", None)
        if callable(lookup):
            try:
                return lookup(int(unit_id)).unit_index
            except KeyError:
                return None
        try:
            return self.data.unit_pool.index(int(unit_id))
        except ValueError:
            return None

    def _selected_unit_id_value(self) -> int:
        selected = self.__dict__.get("_selected_unit_id")
        if selected is not None:
            return int(selected)
        local_index = int(self.unit_idx.get())
        if 0 <= local_index < self.data.n_units:
            return int(self.data.cluster_id(local_index))
        return int(self.data.unit_pool[0])

    def _selected_local_unit_index(self) -> int | None:
        unit_id = self._selected_unit_id_value()
        local_index = self._local_unit_index(unit_id)
        if local_index is None:
            return None
        if int(self.unit_idx.get()) != local_index:
            self.unit_idx.set(local_index)
        return local_index

    def _set_selected_unit_id(self, unit_id: int) -> None:
        unit_id = int(unit_id)
        self._selected_unit_id = unit_id
        local_index = self._local_unit_index(unit_id)
        if local_index is None:
            self.unit_idx.set(-1)
        else:
            self.unit_idx.set(local_index)
            self._last_supported_unit_id = unit_id
        if hasattr(self, "unit_combo"):
            self._sync_unit_combo()

    def _restore_local_unit_selection(self) -> None:
        local_units = [int(unit_id) for unit_id in self.data.unit_pool]
        if not local_units:
            return
        selected = self._selected_unit_id_value()
        if selected in local_units:
            target = selected
        else:
            last_supported = self.__dict__.get("_last_supported_unit_id")
            target = int(last_supported) if last_supported in local_units else local_units[0]
        changed = target != selected or self._selected_local_unit_index() is None
        self._set_selected_unit_id(target)
        if changed and self.__dict__.get("_viewer_ready", False):
            self.selected_cell = None
            self._update_all()

    def _unit_navigation_ids(self) -> list[int]:
        if getattr(self._app_root, "_rfm_pairing_enabled", False):
            ready, eligible = self._pairing_eligibility()
            if eligible:
                return self._pairing_unit_ids(ready)
        return [int(unit_id) for unit_id in self.data.unit_pool]

    def _pairing_eligibility(self) -> tuple[list[RFMViewer], bool]:
        ready = self._ready_pairing_viewers()
        return ready, len(ready) >= 2

    def _refresh_pairing_controls(self) -> None:
        ready, eligible = self._pairing_eligibility()
        active = bool(getattr(self._app_root, "_rfm_pairing_enabled", False) and eligible)
        matching_units = self._unit_lists_match(ready)
        if len(ready) < 2:
            status = "Open another loaded viewer window to enable sync."
        elif not matching_units:
            prefix = f"{len(ready)} windows paired. " if active else f"{len(ready)} windows ready. "
            status = (
                prefix
                + "Unit lists differ; these files may be from different sessions. "
                "Missing units display N/A."
            )
        elif active:
            status = (
                f"{len(ready)} windows paired. Changes in any paired window sync to the others."
            )
        else:
            status = f"{len(ready)} loaded windows have matching unit lists."

        windows = getattr(self._app_root, "_rfm_viewer_windows", [])
        for window in windows:
            if not hasattr(window, "pair_windows_var"):
                continue
            try:
                window.pair_windows_var.set(active)
                if hasattr(window, "pair_windows_toggle"):
                    window.pair_windows_toggle.state(
                        ["!disabled"] if eligible else ["disabled"]
                    )
                if hasattr(window, "pair_status_label"):
                    window.pair_status_label.configure(text=status)
                if getattr(window, "_viewer_ready", False) and hasattr(window, "_sync_unit_combo"):
                    window._sync_unit_combo()
            except tk.TclError:
                continue

    def _disable_window_pairing(self) -> None:
        self._app_root._rfm_pairing_enabled = False
        self._app_root._rfm_pairing_state = None
        self._app_root._rfm_pairing_broadcasting = False
        for window in self._ready_pairing_viewers():
            window._pair_last_local_state = None
            if hasattr(window, "_restore_local_unit_selection"):
                window._restore_local_unit_selection()
        self._refresh_pairing_controls()

    def _pair_ready_viewer_set_changed(
        self,
        *,
        adopt_viewer: RFMViewer | None = None,
    ) -> None:
        ready, eligible = self._pairing_eligibility()
        if not getattr(self._app_root, "_rfm_pairing_enabled", False):
            self._refresh_pairing_controls()
            return
        if not eligible:
            self._disable_window_pairing()
            return

        state = getattr(self._app_root, "_rfm_pairing_state", None)
        if state is None:
            source = ready[0]
            state = source._capture_pairing_state()
            source._pair_last_local_state = state
            self._app_root._rfm_pairing_state = state
        unit_ids = self._pairing_unit_ids(ready)
        normalized_unit_id = self._next_union_unit_id(unit_ids, state.unit_id)
        unit_changed = normalized_unit_id != state.unit_id
        if unit_changed:
            state = replace(state, unit_id=normalized_unit_id)
            self._app_root._rfm_pairing_state = state

        recipients = ready if unit_changed else (
            [adopt_viewer] if adopt_viewer is not None and adopt_viewer in ready else []
        )
        if recipients:
            self._app_root._rfm_pairing_broadcasting = True
            try:
                for window in recipients:
                    if unit_changed and window is not adopt_viewer:
                        window._apply_pairing_state(state, frozenset({"unit"}))
                    else:
                        window._apply_pairing_state(state)
            finally:
                self._app_root._rfm_pairing_broadcasting = False
        self._refresh_pairing_controls()

    def _on_pair_windows_toggled(self) -> None:
        if not self.pair_windows_var.get():
            self._disable_window_pairing()
            return

        ready, eligible = self._pairing_eligibility()
        if not eligible or self not in ready:
            self._disable_window_pairing()
            return

        state = self._capture_pairing_state()
        self._app_root._rfm_pairing_enabled = True
        self._app_root._rfm_pairing_state = state
        self._pair_last_local_state = state
        self._app_root._rfm_pairing_broadcasting = True
        try:
            for window in ready:
                if window is not self:
                    window._apply_pairing_state(state)
        finally:
            self._app_root._rfm_pairing_broadcasting = False
        self._refresh_pairing_controls()

    def _capture_pairing_state(self) -> ViewerSyncState:
        self._normalize_control_values()
        timeline_start_ms, timeline_end_ms = self._timeline_selected_time_bounds_ms()
        rf_start_ms, rf_end_ms = self._selected_time_bounds_ms()
        current_bin = max(0, min(self._time_group_count() - 1, self.bin_var.get()))
        anchor_center_ms = (
            self._time_group_center_ms(self._timeline_range_anchor)
            if self._timeline_range_anchor is not None
            else None
        )
        selected_y_midpoint: float | None = None
        selected_x_midpoint: float | None = None
        if self.selected_cell is not None:
            y_start, y_end, x_start, x_end = self.selected_cell
            selected_y_midpoint = (float(y_start) + float(y_end)) / 2.0
            selected_x_midpoint = (float(x_start) + float(x_end)) / 2.0

        value_mode = self.value_mode_var.get()
        if value_mode not in VALUE_MODES or not self.data.supports_value_mode(value_mode):
            value_mode = VALUE_MODE_COUNT
        palette = self.palette_var.get()
        if palette not in PALETTES:
            palette = PALETTES[0]
        polar_radius = self.polar_radius_var.get()
        if polar_radius not in POLAR_RADIUS_MODES:
            polar_radius = POLAR_RADIUS_MODES[1]
        selected_tab = self._active_tab_key()
        if selected_tab not in {"rf", "delay", "timeline"}:
            selected_tab = "rf"

        return ViewerSyncState(
            unit_id=self._selected_unit_id_value(),
            value_mode=value_mode,
            timeline_bin_center_ms=self._time_group_center_ms(current_bin),
            timeline_selection_start_ms=timeline_start_ms,
            timeline_selection_end_ms=timeline_end_ms,
            timeline_anchor_center_ms=anchor_center_ms,
            rf_start_ms=rf_start_ms,
            rf_end_ms=rf_end_ms,
            time_resolution_ms=float(self.time_res_ms_var.get()),
            x_bins=self._x_target_bins(),
            y_bins=self._y_target_bins(),
            smooth_radius=self._smooth_radius(),
            flip_y=bool(self.flip_y_var.get()),
            palette=palette,
            polar_radius=polar_radius,
            polar_layout=bool(self.polar_layout_var.get()),
            rgb_mode=bool(self.rgb_mode_var.get()),
            selected_cell_y_midpoint=selected_y_midpoint,
            selected_cell_x_midpoint=selected_x_midpoint,
            timeline_scroll_fraction=round(
                max(0.0, min(1.0, float(self._timeline_scroll_fraction))), 9
            ),
            selected_tab=selected_tab,
        )

    def _time_group_index_for_ms(self, time_ms: float) -> int:
        groups = self._time_groups()
        bounds = [self._time_group_bounds_ms(index) for index in range(len(groups))]
        for index, (start_ms, end_ms) in enumerate(bounds):
            if start_ms <= time_ms < end_ms or (
                index == len(bounds) - 1 and time_ms == end_ms
            ):
                return index
        return min(
            range(len(bounds)),
            key=lambda index: abs((bounds[index][0] + bounds[index][1]) / 2.0 - time_ms),
        )

    def _time_group_range_for_ms(self, start_ms: float, end_ms: float) -> AxisGroup:
        if start_ms > end_ms:
            start_ms, end_ms = end_ms, start_ms
        groups = self._time_groups()
        bounds = [self._time_group_bounds_ms(index) for index in range(len(groups))]
        if math.isclose(start_ms, end_ms):
            index = self._time_group_index_for_ms(start_ms)
            return index, index
        overlapping = [
            index
            for index, (group_start, group_end) in enumerate(bounds)
            if group_end > start_ms and group_start < end_ms
        ]
        if overlapping:
            return overlapping[0], overlapping[-1]
        return (
            self._time_group_index_for_ms(start_ms),
            self._time_group_index_for_ms(end_ms),
        )

    @staticmethod
    def _axis_group_for_midpoint(groups: list[AxisGroup], midpoint: float) -> AxisGroup:
        if not groups:
            return 0, 0
        axis_start = min(group[0] for group in groups)
        axis_end = max(group[1] for group in groups)
        midpoint = max(float(axis_start), min(float(axis_end), float(midpoint)))
        source_index = max(axis_start, min(axis_end, int(math.floor(midpoint + 0.5))))
        return next(
            (group for group in groups if group[0] <= source_index <= group[1]),
            min(groups, key=lambda group: abs((group[0] + group[1]) / 2.0 - midpoint)),
        )

    def _cell_for_pairing_midpoint(
        self,
        y_midpoint: float | None,
        x_midpoint: float | None,
    ) -> CellRef | None:
        if y_midpoint is None or x_midpoint is None:
            return None
        y_start, y_end = self._axis_group_for_midpoint(
            self._display_y_groups(), y_midpoint
        )
        x_start, x_end = self._axis_group_for_midpoint(self._x_groups(), x_midpoint)
        return y_start, y_end, x_start, x_end

    def _select_tab_key(self, key: str) -> None:
        if not hasattr(self, "notebook"):
            return
        for tab in self.notebook.tabs():
            if self._tab_keys.get(str(tab)) == key:
                self.notebook.select(tab)
                return

    def _apply_pairing_state(
        self,
        state: ViewerSyncState,
        fields: frozenset[str] = PAIR_SYNC_ALL_FIELDS,
    ) -> None:
        if not self._viewer_ready:
            return
        self._pair_apply_in_progress = True
        try:
            preserved_active_time_ms: float | None = None
            preserved_timeline_bounds_ms: tuple[float, float] | None = None
            preserved_anchor_time_ms: float | None = None
            if "time_resolution" in fields:
                if "active_time" not in fields:
                    preserved_active_time_ms = self._time_group_center_ms(self.bin_var.get())
                if "timeline_selection" not in fields:
                    preserved_timeline_bounds_ms = self._timeline_selected_time_bounds_ms()
                    if self._timeline_range_anchor is not None:
                        preserved_anchor_time_ms = self._time_group_center_ms(
                            self._timeline_range_anchor
                        )
            preserved_cell_midpoint: tuple[float, float] | None = None
            if (
                fields.intersection({"x_bins", "y_bins"})
                and "selected_cell" not in fields
                and self.selected_cell is not None
            ):
                y_start, y_end, x_start, x_end = self.selected_cell
                preserved_cell_midpoint = (
                    (float(y_start) + float(y_end)) / 2.0,
                    (float(x_start) + float(x_end)) / 2.0,
                )
            if "unit" in fields:
                self._set_selected_unit_id(state.unit_id)
            if "value_mode" in fields:
                value_mode = state.value_mode
                if (
                    value_mode not in VALUE_MODES
                    or not self.data.supports_value_mode(value_mode)
                ):
                    value_mode = VALUE_MODE_COUNT
                self.value_mode_var.set(value_mode)
            if "time_resolution" in fields:
                self.time_res_ms_var.set(format_ms(state.time_resolution_ms))
            if "x_bins" in fields:
                self.x_bins_var.set(max(1, min(self.data.n_x, int(state.x_bins))))
            if "y_bins" in fields:
                self.y_bins_var.set(max(1, min(self.data.n_y, int(state.y_bins))))
            if "smoothing" in fields:
                self.smooth_radius_var.set(max(0, min(3, int(state.smooth_radius))))
            if "flip_y" in fields:
                self.flip_y_var.set(bool(state.flip_y))
            if "palette" in fields:
                self.palette_var.set(
                    state.palette if state.palette in PALETTES else PALETTES[0]
                )
            if "polar_radius" in fields:
                self.polar_radius_var.set(
                    state.polar_radius
                    if state.polar_radius in POLAR_RADIUS_MODES
                    else POLAR_RADIUS_MODES[1]
                )
            if "spatial_format" in fields:
                self.polar_layout_var.set(bool(state.polar_layout))
            if "delay_rgb" in fields:
                self.rgb_mode_var.set(bool(state.rgb_mode))
            if "rf_range" in fields:
                self.range_start_ms_var.set(format_ms(state.rf_start_ms))
                self.range_end_ms_var.set(format_ms(state.rf_end_ms))
            if "timeline_scroll" in fields:
                self._timeline_scroll_fraction = max(
                    0.0, min(1.0, float(state.timeline_scroll_fraction))
                )

            self._normalize_control_values()
            if "active_time" in fields:
                self.bin_var.set(
                    self._time_group_index_for_ms(state.timeline_bin_center_ms)
                )
            elif preserved_active_time_ms is not None:
                self.bin_var.set(self._time_group_index_for_ms(preserved_active_time_ms))
            if "timeline_selection" in fields:
                timeline_start, timeline_end = self._time_group_range_for_ms(
                    state.timeline_selection_start_ms,
                    state.timeline_selection_end_ms,
                )
                self.range_start_var.set(timeline_start)
                self.range_end_var.set(timeline_end)
                self._timeline_range_anchor = (
                    self._time_group_index_for_ms(state.timeline_anchor_center_ms)
                    if state.timeline_anchor_center_ms is not None
                    else None
                )
            elif preserved_timeline_bounds_ms is not None:
                timeline_start, timeline_end = self._time_group_range_for_ms(
                    *preserved_timeline_bounds_ms
                )
                self.range_start_var.set(timeline_start)
                self.range_end_var.set(timeline_end)
                self._timeline_range_anchor = (
                    self._time_group_index_for_ms(preserved_anchor_time_ms)
                    if preserved_anchor_time_ms is not None
                    else None
                )
            if "selected_cell" in fields:
                self.selected_cell = self._cell_for_pairing_midpoint(
                    state.selected_cell_y_midpoint,
                    state.selected_cell_x_midpoint,
                )
            elif preserved_cell_midpoint is not None:
                self.selected_cell = self._cell_for_pairing_midpoint(
                    *preserved_cell_midpoint
                )
            if "selected_tab" in fields:
                self._select_tab_key(state.selected_tab)
            if fields.intersection(
                {
                    "unit",
                    "value_mode",
                    "time_resolution",
                    "x_bins",
                    "y_bins",
                    "smoothing",
                    "flip_y",
                    "palette",
                    "polar_radius",
                    "spatial_format",
                    "delay_rgb",
                    "rf_range",
                }
            ):
                self._timeline_preview_cache_key = None
                self._timeline_preview_images = {}
            self._update_all()
            if "timeline_scroll" in fields:
                self._restore_timeline_scroll()
            self._pair_last_local_state = self._capture_pairing_state()
        finally:
            self._pair_apply_in_progress = False

    def _apply_pairing_scroll_fraction(self, fraction: float) -> None:
        if not self._viewer_ready:
            return
        self._pair_apply_in_progress = True
        try:
            fraction = max(0.0, min(1.0, float(fraction)))
            self._timeline_scroll_fraction = fraction
            canvas = self.canvases.get("timeline") if hasattr(self, "canvases") else None
            if canvas is not None:
                try:
                    first, last = canvas.yview()
                except (tk.TclError, TypeError, ValueError):
                    offset = None
                else:
                    offset = timeline_scroll_offset(fraction, first, last)
                if offset is not None:
                    self._restoring_timeline_scroll = True
                    try:
                        canvas.yview_moveto(offset)
                    finally:
                        self._restoring_timeline_scroll = False
            baseline = self._pair_last_local_state or self._capture_pairing_state()
            self._pair_last_local_state = replace(
                baseline,
                timeline_scroll_fraction=round(fraction, 9),
            )
        finally:
            self._pair_apply_in_progress = False

    def _publish_pairing_state_if_changed(self) -> None:
        if not self.__dict__.get("_viewer_ready", False):
            return
        if self.__dict__.get("_pair_apply_in_progress", False):
            return
        if not getattr(self._app_root, "_rfm_pairing_enabled", False):
            return
        if getattr(self._app_root, "_rfm_pairing_broadcasting", False):
            return

        ready, eligible = self._pairing_eligibility()
        if not eligible or self not in ready:
            self._disable_window_pairing()
            return
        state = self._capture_pairing_state()
        previous = self._pair_last_local_state
        if previous is not None:
            changed_fields = state.changed_fields(previous)
        else:
            changed_fields = PAIR_SYNC_ALL_FIELDS
        self._pair_last_local_state = state
        if not changed_fields:
            return
        canonical = getattr(self._app_root, "_rfm_pairing_state", None)
        self._app_root._rfm_pairing_state = (
            canonical.merging(state, changed_fields) if canonical is not None else state
        )

        self._app_root._rfm_pairing_broadcasting = True
        try:
            for window in ready:
                if window is self:
                    continue
                if changed_fields == frozenset({"timeline_scroll"}):
                    window._apply_pairing_scroll_fraction(state.timeline_scroll_fraction)
                else:
                    window._apply_pairing_state(state, changed_fields)
        finally:
            self._app_root._rfm_pairing_broadcasting = False

    def _dispatch_open_json(self, _event: object | None = None) -> None:
        self._active_viewer()._open_json()

    def _dispatch_macos_open_documents(self, *paths: str) -> None:
        self._active_viewer()._on_macos_open_documents(*paths)

    def _close_window(self, _event: object | None = None) -> None:
        self.destroy()

    def _quit_application(self, _event: object | None = None) -> None:
        if getattr(self._app_root, "_rfm_quitting", False):
            return
        self._quitting = True
        self._app_root._rfm_quitting = True
        self._app_root.destroy()

    def _open_json_window(self, path: Path) -> RFMViewer | None:
        try:
            data = RFMappingData(path)
        except Exception as exc:
            messagebox.showerror("Could not open JSON", str(exc), parent=self)
            return None
        window = RFMViewer(data, master=self._app_root)
        window.lift()
        return window

    def _open_json(self, _event: object | None = None) -> None:
        initial_dir = self.data.path.parent if self._viewer_ready else startup_json_path().parent
        path = filedialog.askopenfilename(
            parent=self,
            title="Open RF mapping JSON",
            initialdir=str(initial_dir),
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if path:
            if self._viewer_ready:
                self._open_json_window(Path(path))
            else:
                self._cancel_startup_callback()
                self._startup_after = self.after_idle(
                    lambda selected=Path(path): self._load_startup_document(selected)
                )

    def _on_macos_open_documents(self, *paths: str) -> None:
        documents = [Path(raw_path).expanduser() for raw_path in paths]
        if not documents:
            return
        if not self._viewer_ready:
            self._cancel_startup_callback()
            selected, *additional = documents

            def load_documents() -> None:
                self._load_startup_document(selected)
                for path in additional:
                    self._open_json_window(path)

            self._startup_after = self.after_idle(
                load_documents
            )
            return

        for path in documents:
            self._open_json_window(path)

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
        unit_ids = self._unit_navigation_ids()
        self._unit_combo_unit_ids = unit_ids
        values: list[str] = []
        for unit_id in unit_ids:
            local_index = self._local_unit_index(unit_id)
            if local_index is None:
                values.append(f"N/A  cluster {unit_id} — not in this session")
            else:
                values.append(f"{local_index:03d}  cluster {unit_id}")
        self.unit_combo.configure(values=values)
        selected_unit_id = self._selected_unit_id_value()
        try:
            selected_index = unit_ids.index(selected_unit_id)
        except ValueError:
            self.unit_combo.set("")
        else:
            self.unit_combo.current(selected_index)

    def _on_unit_selected(self, _event: object | None = None) -> None:
        combo_index = self.unit_combo.current()
        unit_ids = self.__dict__.get("_unit_combo_unit_ids", [])
        if 0 <= combo_index < len(unit_ids):
            self._set_selected_unit_id(unit_ids[combo_index])
            self.selected_cell = None
            self._update_all()
            self._publish_pairing_state_if_changed()

    def _step_unit(self, delta: int) -> None:
        unit_ids = self._unit_navigation_ids()
        if not unit_ids:
            return
        selected_unit_id = self._selected_unit_id_value()
        try:
            current_index = unit_ids.index(selected_unit_id)
        except ValueError:
            selected_unit_id = self._next_union_unit_id(unit_ids, selected_unit_id)
            current_index = unit_ids.index(selected_unit_id)
        target_unit_id = unit_ids[(current_index + int(delta)) % len(unit_ids)]
        self._set_selected_unit_id(target_unit_id)
        self.selected_cell = None
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _step_timeline_bin(self, delta: int) -> None:
        max_bin = max(0, self._time_group_count() - 1)
        target = max(0, min(max_bin, self.bin_var.get() + delta))
        self.bin_var.set(target)
        self.range_start_var.set(target)
        self.range_end_var.set(target)
        self._timeline_range_anchor = target
        self._sync_time_range_controls()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _step_time_resolution(self, delta_ms: float) -> None:
        try:
            current = float(self.time_res_ms_var.get())
        except (tk.TclError, TypeError, ValueError):
            current = self._base_bin_ms()
        target = max(self._base_bin_ms(), min(self._total_time_ms(), current + delta_ms))
        self.time_res_ms_var.set(format_ms(target))
        self._on_time_resolution_changed()

    def _clear_timeline_selection(self) -> None:
        self._timeline_range_anchor = None
        self.bin_var.set(0)
        self.range_start_var.set(0)
        self.range_end_var.set(max(0, self._time_group_count() - 1))
        self._sync_time_range_controls()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _on_value_mode_changed(self, _event: object | None = None) -> None:
        value_mode = self.value_mode_var.get()
        if not self.data.supports_value_mode(value_mode):
            self.value_mode_var.set(VALUE_MODE_COUNT)
            messagebox.showinfo(
                "Firing-rate metadata required",
                "This legacy JSON contains pooled spike counts but does not include "
                "stimulusPresentationCounts. A true per-presentation value or firing rate "
                "cannot be recovered safely. Regenerate the JSON with presentation-count metadata.",
            )
            return
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _on_range_changed(self, _event: object | None = None) -> None:
        self._normalize_control_values()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _reset_plot_range(self) -> None:
        start_ms, end_ms = self._default_plot_time_bounds_ms()
        self.range_start_ms_var.set(format_ms(start_ms))
        self.range_end_ms_var.set(format_ms(end_ms))
        self._on_range_changed()

    def _on_time_resolution_changed(self, _event: object | None = None) -> None:
        previous_groups = list(getattr(self, "_last_time_groups", ()))
        if not previous_groups:
            previous_groups = [(index, index) for index in range(self.data.n_bins)]
        previous_count = len(previous_groups)
        previous_start = max(
            0,
            min(
                previous_count - 1,
                min(self.range_start_var.get(), self.range_end_var.get()),
            ),
        )
        previous_end = max(
            0,
            min(
                previous_count - 1,
                max(self.range_start_var.get(), self.range_end_var.get()),
            ),
        )
        source_start = previous_groups[previous_start][0]
        source_end = previous_groups[previous_end][1]
        previous_bin = max(0, min(previous_count - 1, self.bin_var.get()))
        active_source_group = previous_groups[previous_bin]
        active_source_bin = (active_source_group[0] + active_source_group[1]) // 2
        was_full_timeline = (
            previous_start == 0
            and previous_end == previous_count - 1
        )
        self._timeline_range_anchor = None
        self._normalize_control_values()
        new_groups = list(self._last_time_groups)
        if was_full_timeline:
            self.range_start_var.set(0)
            self.range_end_var.set(len(new_groups) - 1)
        else:
            self.range_start_var.set(display_group_index_for_source_bin(new_groups, source_start))
            self.range_end_var.set(display_group_index_for_source_bin(new_groups, source_end))
        self.bin_var.set(display_group_index_for_source_bin(new_groups, active_source_bin))
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _on_control_changed(self, _event: object | None = None) -> None:
        if self.__dict__.get("_pair_apply_in_progress", False):
            return
        self._normalize_control_values()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _on_spatial_format_changed(self) -> None:
        self._timeline_preview_cache_key = None
        self._timeline_preview_images = {}
        self._on_control_changed()

    def _on_tab_changed(self, _event: object | None = None) -> None:
        self._sync_context_controls()
        self._on_control_changed()

    def _sync_context_controls(self) -> None:
        if not hasattr(self, "rgb_mode_toggle"):
            return
        if self._active_tab_key() == "delay":
            self.rgb_mode_toggle.state(["!disabled"])
        else:
            self.rgb_mode_toggle.state(["disabled"])

    def _schedule_redraw(self, _event: object | None = None) -> None:
        if self._redraw_after is not None:
            self.after_cancel(self._redraw_after)
        self._redraw_after = self.after(40, self._run_scheduled_redraw)

    def _run_scheduled_redraw(self) -> None:
        self._redraw_after = None
        self._update_all()

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
        progress = timeline_scroll_progress(first_value, last_value)
        if progress is not None:
            self._timeline_scroll_fraction = progress

    def _timeline_yview(self, *args: object) -> None:
        canvas = self.canvases.get("timeline")
        if canvas is None:
            return
        canvas.yview(*args)
        self._remember_timeline_scroll()
        self._publish_pairing_state_if_changed()

    def _remember_timeline_scroll(self) -> None:
        canvas = self.canvases.get("timeline")
        if canvas is None:
            return
        try:
            first, last = canvas.yview()
        except tk.TclError:
            return
        progress = timeline_scroll_progress(first, last)
        if progress is not None:
            self._timeline_scroll_fraction = progress

    def _restore_timeline_scroll(self) -> None:
        canvas = self.canvases.get("timeline")
        if canvas is None:
            return
        try:
            first, last = canvas.yview()
        except tk.TclError:
            return
        offset = timeline_scroll_offset(self._timeline_scroll_fraction, first, last)
        if offset is None:
            return
        self._restoring_timeline_scroll = True
        try:
            canvas.yview_moveto(offset)
        finally:
            self._restoring_timeline_scroll = False

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
        self._publish_pairing_state_if_changed()
        return "break"

    def _normalize_control_values(self) -> None:
        time_groups = self._time_groups()
        time_count = max(1, len(time_groups))
        max_bin = max(0, time_count - 1)
        for var in (self.bin_var, self.range_start_var, self.range_end_var):
            try:
                value = int(var.get())
            except (tk.TclError, ValueError):
                value = 0
            var.set(max(0, min(max_bin, value)))
        self._source_bins_for_time_controls()
        if self._timeline_range_anchor is not None:
            self._timeline_range_anchor = max(0, min(max_bin, self._timeline_range_anchor))
        self._x_target_bins()
        self._y_target_bins()
        self._smooth_radius()
        self._sync_time_control_ranges()
        self._last_time_group_count = time_count
        self._last_time_groups = list(time_groups)
        selected_cell = self.__dict__.get("selected_cell")
        if selected_cell is not None:
            y_start, y_end, x_start, x_end = selected_cell
            self.selected_cell = self._cell_for_pairing_midpoint(
                (float(y_start) + float(y_end)) / 2.0,
                (float(x_start) + float(x_end)) / 2.0,
            )

    def _parse_time_control(self, variable: tk.StringVar, fallback: float) -> float:
        try:
            return float(variable.get())
        except (tk.TclError, TypeError, ValueError):
            return fallback

    def _default_plot_time_bounds_ms(self) -> tuple[float, float]:
        start, end = self._snap_time_range_to_bins(
            DEFAULT_RF_SUM_START_MS,
            DEFAULT_RF_SUM_END_MS,
        )
        return (
            self.data.time_bin_edges[start] * 1000.0,
            self.data.time_bin_edges[end + 1] * 1000.0,
        )

    def _snap_time_range_to_bins(self, requested_start: float, requested_end: float) -> AxisGroup:
        edges_ms = [edge * 1000.0 for edge in self.data.time_bin_edges]
        axis_start, axis_end = edges_ms[0], edges_ms[-1]
        requested_start = max(axis_start, min(axis_end, requested_start))
        requested_end = max(axis_start, min(axis_end, requested_end))
        if requested_start > requested_end:
            requested_start, requested_end = requested_end, requested_start

        start_edge = min(
            range(self.data.n_bins),
            key=lambda index: abs(edges_ms[index] - requested_start),
        )
        end_edge = min(
            range(1, self.data.n_bins + 1),
            key=lambda index: abs(edges_ms[index] - requested_end),
        )
        if end_edge <= start_edge:
            if requested_start >= axis_end:
                start_edge, end_edge = self.data.n_bins - 1, self.data.n_bins
            elif requested_end <= axis_start:
                start_edge, end_edge = 0, 1
            else:
                end_edge = min(self.data.n_bins, start_edge + 1)
        return start_edge, end_edge - 1

    def _source_bins_for_time_controls(self) -> AxisGroup:
        edges_ms = [edge * 1000.0 for edge in self.data.time_bin_edges]
        axis_start, axis_end = edges_ms[0], edges_ms[-1]
        requested_start = self._parse_time_control(self.range_start_ms_var, axis_start)
        requested_end = self._parse_time_control(self.range_end_ms_var, axis_end)
        start, end = self._snap_time_range_to_bins(requested_start, requested_end)
        start_edge, end_edge = start, end + 1
        self.range_start_ms_var.set(format_ms(edges_ms[start_edge]))
        self.range_end_ms_var.set(format_ms(edges_ms[end_edge]))
        return start, end

    def _sync_time_range_controls(self) -> None:
        # Timeline selection is intentionally independent of the RF sum range
        # shown in the top bar.
        count = self._time_group_count()
        max_bin = max(0, count - 1)
        self.range_start_var.set(max(0, min(max_bin, self.range_start_var.get())))
        self.range_end_var.set(max(0, min(max_bin, self.range_end_var.get())))

    def _active_tab_key(self) -> str:
        if not hasattr(self, "notebook"):
            return "rf"
        selected = self.notebook.select()
        return self._tab_keys.get(str(selected), "rf")

    def _draw_active_tab(self) -> None:
        key = self._active_tab_key()
        if self._selected_local_unit_index() is None:
            self._draw_unavailable_unit(key)
            return
        if key == "rf":
            self._draw_rf()
        elif key == "delay":
            self._draw_rgb() if self.rgb_mode_var.get() else self._draw_delay()
        elif key == "timeline":
            self._draw_timeline()

    def _draw_unavailable_unit(self, key: str) -> None:
        canvas = self.canvases[key]
        canvas.delete("all")
        self._canvas_layouts.pop(key, None)
        if key == "timeline":
            self._timeline_cells = []
            self._timeline_cells_by_bin = {}
            self._timeline_preview_cache_key = None
            self._timeline_preview_images = {}
        width = max(canvas.winfo_width(), 300)
        height = max(canvas.winfo_height(), 220)
        canvas.configure(scrollregion=(0, 0, width, height))
        unit_id = self._selected_unit_id_value()
        canvas.create_text(
            width / 2,
            height / 2 - 14,
            text="N/A",
            fill="#667085",
            font=("TkDefaultFont", 28, "bold"),
        )
        canvas.create_text(
            width / 2,
            height / 2 + 26,
            text=f"Cluster {unit_id} is not available in this session.",
            fill="#667085",
            font=("TkDefaultFont", 12),
        )

    def _update_all(self) -> None:
        if self._redraw_after is not None:
            self.after_cancel(self._redraw_after)
            self._redraw_after = None
        self._normalize_control_values()
        self.hover_cell = None
        self._hover_signature = None
        self._hover_tooltip_text = ""
        self.data_label.configure(
            text=(
                f"{self.data.path}\n"
                f"{self.data.n_units} units  {self.data.n_y} y x {self.data.n_x} x  "
                f"{self.data.n_bins} bins\n"
                f"Firing-rate metadata: {'yes' if self.data.presentation_counts is not None else 'no'}"
            )
        )
        unit_idx = self._selected_local_unit_index()
        cluster_id = self._selected_unit_id_value()
        if unit_idx is None:
            self.selected_cell = None
            self.header_label.configure(text=f"Unit N/A / cluster {cluster_id}")
            self.status_label.configure(
                text=(
                    f"N/A: cluster {cluster_id} is not available in this session. "
                    "Use ←/→ to continue through the paired unit list."
                )
            )
            self.unit_stats_label.configure(
                text="N/A\nThis unit is available only in another paired window."
            )
            self.cell_label.configure(text="N/A for this session")
            self._sync_context_controls()
            self._draw_active_tab()
            return

        metrics = self.data.metrics(unit_idx)
        self.header_label.configure(text=f"Unit {unit_idx:03d} / cluster {cluster_id}")
        self.status_label.configure(
            text=(
                f"x: {format_pos(self.data.x_positions[0])}..{format_pos(self.data.x_positions[-1])}  "
                f"y: {format_pos(self.data.y_positions[0])}..{format_pos(self.data.y_positions[-1])}  "
                f"time: {format_ms(self._time_axis_start_ms())}..{format_ms(self._time_axis_end_ms())} ms  "
                f"value: {self.value_mode_var.get()}"
            )
        )
        best_delay = metrics.delay_ms[metrics.best_y][metrics.best_x]
        self.unit_stats_label.configure(
            text=(
                f"Total spikes: {metrics.total_spikes:.0f}\n"
                f"Best count cell: yIdx {metrics.best_y + 1}, xIdx {metrics.best_x + 1}\n"
                f"Count-peak delay: {best_delay:.1f} ms" if best_delay is not None else
                f"Total spikes: {metrics.total_spikes:.0f}\n"
                f"Best count cell: yIdx {metrics.best_y + 1}, xIdx {metrics.best_x + 1}\n"
                f"Count-peak delay: n/a"
            )
        )
        self._update_cell_label()
        self._sync_context_controls()
        self._draw_active_tab()

    def _current_matrix(self) -> list[list[float | None]]:
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return [[None for _x in range(self.data.n_x)] for _y in range(self.data.n_y)]
        start, end = self._source_bins_for_display_range()
        return self.data.response_matrix(
            unit_idx,
            start,
            end,
            self.value_mode_var.get(),
        )

    def _delay_matrix_for_time_groups(self, floor: float = 0.0) -> list[list[float | None]]:
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return [[None for _x in range(self.data.n_x)] for _y in range(self.data.n_y)]
        unit = self.data.counts[unit_idx]
        metrics = self.data.metrics(unit_idx)
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
        start_ms, end_ms = self._timeline_selected_time_bounds_ms()
        return f"{format_ms(start_ms)} to {format_ms(end_ms)} ms"

    def _selected_time_bounds_ms(self) -> tuple[float, float]:
        """Return the independent spatial RF summation window."""
        start, end = self._source_bins_for_time_controls()
        return (
            self.data.time_bin_edges[start] * 1000.0,
            self.data.time_bin_edges[end + 1] * 1000.0,
        )

    def _timeline_selected_source_bins(self) -> AxisGroup:
        groups = self._time_groups()
        start, end = self._display_range_indices()
        return groups[start][0], groups[end][1]

    def _timeline_selected_time_bounds_ms(self) -> tuple[float, float]:
        start, end = self._timeline_selected_source_bins()
        return (
            self.data.time_bin_edges[start] * 1000.0,
            self.data.time_bin_edges[end + 1] * 1000.0,
        )

    def _time_group_bounds_ms(self, display_bin: int) -> tuple[float, float]:
        groups = self._time_groups()
        idx = max(0, min(len(groups) - 1, int(display_bin)))
        start, end = groups[idx]
        return self.data.time_bin_edges[start] * 1000.0, self.data.time_bin_edges[end + 1] * 1000.0

    def _time_group_label(self, display_bin: int) -> str:
        start_ms, _end_ms = self._time_group_bounds_ms(display_bin)
        return f"{format_ms(start_ms)} ms"

    def _time_group_start_label(self, display_bin: int) -> str:
        start_ms, _end_ms = self._time_group_bounds_ms(display_bin)
        return f"{format_ms(start_ms)} ms"

    def _time_group_center_ms(self, display_bin: int) -> float:
        start_ms, end_ms = self._time_group_bounds_ms(display_bin)
        return (start_ms + end_ms) / 2.0

    def _source_bins_for_display_bin(self, display_bin: int) -> AxisGroup:
        groups = self._time_groups()
        idx = max(0, min(len(groups) - 1, int(display_bin)))
        return groups[idx]

    def _source_bins_for_display_range(self) -> AxisGroup:
        return self._source_bins_for_time_controls()

    def _plot_range_group_indices(self) -> AxisGroup:
        source_start, source_end = self._source_bins_for_time_controls()
        groups = self._time_groups()
        start_group = next(
            (index for index, (start, end) in enumerate(groups) if start <= source_start <= end),
            0,
        )
        end_group = next(
            (index for index, (start, end) in enumerate(groups) if start <= source_end <= end),
            len(groups) - 1,
        )
        return start_group, end_group

    def _time_grouped_hist(self, hist: list[float]) -> list[float]:
        return [float(sum(hist[start : end + 1])) for start, end in self._time_groups()]

    def _has_time_selection(self) -> bool:
        return not self._is_full_display_range()

    def _visible_timeline_bins(self, display_bins: int) -> list[int]:
        # Timeline is an overview: its own selection highlights bins but never
        # removes temporal context. A dedicated timeline filter can be added
        # later if filtering is needed independently of the RF sum controls.
        return list(range(display_bins))

    def _sync_time_control_ranges(self) -> None:
        axis_start, axis_end = self._time_axis_range_ms()
        source_step = self._base_bin_ms()
        if hasattr(self, "range_start_spin"):
            self.range_start_spin.configure(from_=axis_start, to=axis_end, increment=source_step)
        if hasattr(self, "range_end_spin"):
            self.range_end_spin.configure(from_=axis_start, to=axis_end, increment=source_step)
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
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return hist
        n = max(1, (y_end - y_start + 1) * (x_end - x_start + 1))
        unit = self.data.counts[unit_idx]
        for y_idx in range(y_start, y_end + 1):
            for x_idx in range(x_start, x_end + 1):
                for bin_idx, value in enumerate(unit[y_idx][x_idx]):
                    hist[bin_idx] += float(value) / n
        return hist

    def _group_response_value(
        self,
        y_start: int,
        y_end: int,
        x_start: int,
        x_end: int,
        source_start: int,
        source_end: int,
    ) -> float | None:
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return None
        value_mode = self.value_mode_var.get()
        values = [
            self.data.response_value(
                unit_idx,
                y_idx,
                x_idx,
                source_start,
                source_end,
                value_mode,
            )
            for y_idx in range(y_start, y_end + 1)
            for x_idx in range(x_start, x_end + 1)
        ]
        finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
        return sum(finite) / len(finite) if finite else None

    def _group_response_values(
        self,
        y_start: int,
        y_end: int,
        x_start: int,
        x_end: int,
    ) -> list[float | None]:
        return [
            self._group_response_value(y_start, y_end, x_start, x_end, start, end)
            for start, end in self._time_groups()
        ]

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
        start_ms, end_ms = self._selected_time_bounds_ms()
        return f"{self.value_mode_var.get()}: {format_ms(start_ms)} to {format_ms(end_ms)} ms"

    def _rf_sum_range_value_text(self, value: float | None) -> str:
        value_mode = self.value_mode_var.get()
        start_ms, end_ms = self._selected_time_bounds_ms()
        return (
            f"RF sum range {format_ms(start_ms)}–{format_ms(end_ms)} ms: "
            f"{format_response_value(value, value_mode)} {value_mode_unit(value_mode)}"
        )

    def _cell_metrics_text(
        self,
        y_start: int,
        y_end: int,
        x_idx: int,
        x_end: int,
        display_bin: int | None = None,
    ) -> str:
        unit_idx = self.unit_idx.get()
        value_mode = self.value_mode_var.get()
        unit = value_mode_unit(value_mode)
        hist = self._group_hist(y_start, y_end, x_idx, x_end)
        count_hist = self._time_grouped_hist(hist)
        display_values = self._group_response_values(y_start, y_end, x_idx, x_end)
        bin_idx = self.bin_var.get() if display_bin is None else int(display_bin)
        bin_idx = max(0, min(len(display_values) - 1, bin_idx))
        range_start, range_end = self._source_bins_for_time_controls()
        range_value = self._group_response_value(
            y_start, y_end, x_idx, x_end, range_start, range_end
        )
        total_value = self._group_response_value(
            y_start, y_end, x_idx, x_end, 0, self.data.n_bins - 1
        )
        finite_values = [
            (index, float(value))
            for index, value in enumerate(display_values)
            if value is not None and math.isfinite(float(value))
        ]
        if finite_values and max(value for _index, value in finite_values) > 0:
            peak_bin, peak_value = max(finite_values, key=lambda item: item[1])
            delay = self._time_group_center_ms(peak_bin)
        else:
            peak_bin = None
            peak_value = None
            delay = None

        total_hist = sum(count_hist)
        if total_hist > 0:
            ent = 0.0
            for count in count_hist:
                if count > 0:
                    p = count / total_hist
                    ent -= p * math.log(p)
            ent = ent / math.log(len(count_hist)) if len(count_hist) > 1 else 0.0
        else:
            ent = 0.0
        delay_text = f"{delay:.1f} ms" if delay is not None else "n/a"
        peak_text = f"{peak_bin + 1} ({self._time_group_label(peak_bin)})" if peak_bin is not None else "n/a"
        group_note = "avg over source pixels\n" if (x_end != x_idx or y_end != y_start) else ""
        return (
            f"cluster {self.data.cluster_id(unit_idx)}\n"
            f"{self._y_group_text(y_start, y_end)}, {self._x_group_text(x_idx, x_end)}\n"
            f"{group_note}"
            f"bin {format_response_value(display_values[bin_idx], value_mode)} {unit} "
            f"({self._time_group_label(bin_idx)})\n"
            f"{self._rf_sum_range_value_text(range_value)}\n"
            f"full window {format_response_value(total_value, value_mode)} {unit}\n"
            f"peak {format_response_value(peak_value, value_mode)} {unit}\n"
            f"peak bin {peak_text}\n"
            f"delay {delay_text}, count entropy {ent:.3f}"
        )

    def _update_cell_label(
        self,
        cell: CellRef | None = None,
        prefix: str = "",
        display_bin: int | None = None,
    ) -> None:
        if self._selected_local_unit_index() is None:
            self.cell_label.configure(text="N/A for this session")
            return
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
        self.cell_label.configure(
            text=prefix + self._cell_metrics_text(y_start, y_end, x_idx, x_end, display_bin=display_bin)
        )

    def _cell_tooltip_text(self, cell: CellRef, display_bin: int | None = None) -> str:
        y_start, y_end, x_start, x_end = cell
        value_mode = self.value_mode_var.get()
        unit = value_mode_unit(value_mode)
        display_values = self._group_response_values(y_start, y_end, x_start, x_end)
        bin_idx = self.bin_var.get() if display_bin is None else int(display_bin)
        bin_idx = max(0, min(len(display_values) - 1, bin_idx))
        finite_values = [
            (index, float(value))
            for index, value in enumerate(display_values)
            if value is not None and math.isfinite(float(value))
        ]
        if finite_values and max(value for _index, value in finite_values) > 0:
            peak_bin, _peak_value = max(finite_values, key=lambda item: item[1])
            delay = self._time_group_center_ms(peak_bin)
        else:
            delay = None
        total = self._group_response_value(
            y_start,
            y_end,
            x_start,
            x_end,
            0,
            self.data.n_bins - 1,
        )
        plot_start, plot_end = self._source_bins_for_time_controls()
        plot_value = self._group_response_value(
            y_start,
            y_end,
            x_start,
            x_end,
            plot_start,
            plot_end,
        )
        return "\n".join(
            [
                self._y_group_text(y_start, y_end),
                self._x_group_text(x_start, x_end),
                f"bin {bin_idx + 1}: {format_response_value(display_values[bin_idx], value_mode)} {unit}",
                self._rf_sum_range_value_text(plot_value),
                f"full window: {format_response_value(total, value_mode)} {unit}",
                f"delay {delay:.1f} ms" if delay is not None else "delay n/a",
            ]
        )

    def _draw_rf(self) -> None:
        matrix = self._current_matrix()
        title = f"RF map - {self._current_matrix_label()}"
        if self.polar_layout_var.get():
            self._draw_polar_matrix(
                "rf",
                matrix,
                title,
                self.palette_var.get(),
                value_suffix=value_mode_suffix(self.value_mode_var.get()),
                fixed_range=None,
            )
        else:
            self._draw_heatmap(
                "rf",
                matrix,
                title,
                self.palette_var.get(),
                value_suffix=value_mode_suffix(self.value_mode_var.get()),
                fixed_range=None,
            )

    def _draw_delay(self) -> None:
        delay_matrix = self._delay_matrix_for_time_groups(0.0)
        if self.polar_layout_var.get():
            self._draw_polar_matrix(
                "delay",
                delay_matrix,
                "Delay map - peak displayed bin center",
                "Delay",
                value_suffix=" ms",
                fixed_range=self._time_axis_range_ms(),
            )
        else:
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
            "geometry": "rectangle",
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

    def _draw_polar_matrix(
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
        disp, x_groups, y_groups = self._prepare_plot_matrix(matrix)
        low, high = fixed_range if fixed_range is not None else finite_min_max(disp)
        total_deg = self.data.infer_total_deg()
        n_rows = len(y_groups)
        radius_units = INNER_BLANK_ROWS + n_rows + POLAR_PAD_ROWS
        scale = min((w - 180) / (2 * radius_units), (h - 130) / (2 * radius_units))
        scale = max(4.0, scale)
        cx = w / 2
        cy = h / 2 + 22

        canvas.create_text(20, 22, anchor="w", text=title, font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(
            20,
            44,
            anchor="w",
            text=f"Polar layout; total angle {total_deg:.0f}°; radius: {self.polar_radius_var.get()}",
            fill="#667085",
        )
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
                fill = delay_color(value, low, high) if palette == "Delay" else palette_color(value, low, high, palette)
                points = self._polar_cell_points(cx, cy, scale, r_inner, r_outer, theta_edges[col], theta_edges[col + 1])
                canvas.create_polygon(points, fill=fill, outline="")

        self._draw_polar_selection_outline(
            canvas,
            cx,
            cy,
            scale,
            theta_edges,
            x_groups,
            y_groups,
            ring_rows,
        )

        outer_r = (INNER_BLANK_ROWS + n_rows) * scale
        canvas.create_oval(cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r, outline="#475467")
        canvas.create_text(cx, cy - outer_r - 18, text="x columns span visual angle", fill="#475467")
        canvas.create_text(
            cx,
            cy + outer_r + 22,
            text=f"Values: {self.value_mode_var.get() if palette != 'Delay' else 'delay (ms)'}",
            fill="#475467",
        )
        self._draw_colorbar(
            canvas,
            w - 124,
            cy - min(220, 2 * outer_r) / 2,
            min(220, 2 * outer_r),
            low,
            high,
            palette,
            value_suffix,
        )
        self._canvas_layouts[key] = {
            "geometry": "polar",
            "cx": cx,
            "cy": cy,
            "scale": scale,
            "total_deg": total_deg,
            "x_groups": x_groups,
            "y_groups": y_groups,
            "ring_rows": ring_rows,
        }

    def _draw_polar_selection_outline(
        self,
        canvas: tk.Canvas,
        cx: float,
        cy: float,
        scale: float,
        theta_edges: list[float],
        x_groups: list[AxisGroup],
        y_groups: list[AxisGroup],
        ring_rows: list[int],
    ) -> None:
        if self.selected_cell is None:
            return
        y_start, _y_end, x_start, _x_end = self.selected_cell
        display_row = next(
            (index for index, (start, end) in enumerate(y_groups) if start <= y_start <= end),
            None,
        )
        column = next(
            (index for index, (start, end) in enumerate(x_groups) if start <= x_start <= end),
            None,
        )
        if display_row is None or column is None or display_row not in ring_rows:
            return
        ring_idx = ring_rows.index(display_row)
        points = self._polar_cell_points(
            cx,
            cy,
            scale,
            INNER_BLANK_ROWS + ring_idx,
            INNER_BLANK_ROWS + ring_idx + 1,
            theta_edges[column],
            theta_edges[column + 1],
        )
        canvas.create_polygon(points, fill="", outline="#ffffff", width=4)
        canvas.create_polygon(points, fill="", outline="#111827", width=2)

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
        canvas = self.canvases["delay"]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 160)
        margin_l, margin_r, margin_t, margin_b = 78, 188, 56, 68
        plot_w = max(10, w - margin_l - margin_r)
        plot_h = max(10, h - margin_t - margin_b)
        response_matrix = self.data.response_matrix(
            self.unit_idx.get(),
            0,
            self.data.n_bins - 1,
            self.value_mode_var.get(),
        )
        total_disp, x_groups, y_groups = self._prepare_plot_matrix(response_matrix)
        delay_disp, _x_groups_delay, _y_groups_delay = self._prepare_plot_matrix(self._delay_matrix_for_time_groups(0.0))
        entropy_disp, _x_groups_entropy, _y_groups_entropy = self._prepare_plot_matrix(metrics.entropy)
        n_rows = len(y_groups)
        cell = max(4.0, min(plot_w / len(x_groups), plot_h / n_rows))
        grid_w = cell * len(x_groups)
        grid_h = cell * n_rows
        x0 = margin_l + (plot_w - grid_w) / 2
        y0 = margin_t + (plot_h - grid_h) / 2
        _response_low, response_high = finite_min_max(total_disp)
        max_total = max(response_high, 1.0)
        min_delay, max_delay = self._time_axis_range_ms()
        delay_span = max(max_delay - min_delay, 1.0)

        if self.polar_layout_var.get():
            self._draw_rgb_polar(
                total_disp,
                delay_disp,
                entropy_disp,
                x_groups,
                y_groups,
                max_total,
                min_delay,
                delay_span,
            )
            return

        canvas.create_text(20, 22, anchor="w", text="RGB composite", font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(
            20,
            44,
            anchor="w",
            text=f"R {self.value_mode_var.get()}; G delay; B temporal entropy",
            fill="#667085",
        )

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
        for i, (label, color) in enumerate(
            ((f"R {value_mode_unit(self.value_mode_var.get())}", "#dc2626"), ("G delay", "#16a34a"), ("B entropy", "#2563eb"))
        ):
            y = legend_y + i * 26
            canvas.create_rectangle(legend_x, y, legend_x + 16, y + 16, fill=color, outline="")
            canvas.create_text(legend_x + 24, y + 8, anchor="w", text=label, fill="#475467")
        self._canvas_layouts["delay"] = {
            "geometry": "rectangle",
            "x0": x0,
            "y0": y0,
            "cell": cell,
            "grid_w": grid_w,
            "grid_h": grid_h,
            "x_groups": x_groups,
            "y_groups": y_groups,
        }

    def _draw_rgb_polar(
        self,
        total_disp: list[list[float | None]],
        delay_disp: list[list[float | None]],
        entropy_disp: list[list[float | None]],
        x_groups: list[AxisGroup],
        y_groups: list[AxisGroup],
        max_total: float,
        min_delay: float,
        delay_span: float,
    ) -> None:
        canvas = self.canvases["delay"]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 160)
        total_deg = self.data.infer_total_deg()
        n_rows = len(y_groups)
        radius_units = INNER_BLANK_ROWS + n_rows + POLAR_PAD_ROWS
        scale = max(4.0, min((w - 220) / (2 * radius_units), (h - 130) / (2 * radius_units)))
        cx = w / 2
        cy = h / 2 + 22
        canvas.create_text(20, 22, anchor="w", text="RGB composite", font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(
            20,
            44,
            anchor="w",
            text=(
                f"Polar layout; R {self.value_mode_var.get()}; G delay; "
                "B temporal entropy"
            ),
            fill="#667085",
        )
        canvas.create_oval(
            cx - INNER_BLANK_ROWS * scale,
            cy - INNER_BLANK_ROWS * scale,
            cx + INNER_BLANK_ROWS * scale,
            cy + INNER_BLANK_ROWS * scale,
            fill="#f8fafc",
            outline="#e5e7eb",
        )
        theta_edges = [
            math.radians(90.0 + total_deg / 2.0 - total_deg * index / len(x_groups))
            for index in range(len(x_groups) + 1)
        ]
        if self.polar_radius_var.get() == POLAR_RADIUS_MODES[0]:
            ring_rows = sorted(range(n_rows), key=lambda index: y_groups[index][0])
        else:
            ring_rows = list(range(n_rows - 1, -1, -1))

        for ring_idx, display_row in enumerate(ring_rows):
            for column in range(len(x_groups)):
                total_value = total_disp[display_row][column] or 0.0
                delay = delay_disp[display_row][column]
                if total_value <= 0:
                    fill = "#edf0f3"
                else:
                    fill = hex_color(
                        (
                            int(round(clamp(total_value / max_total) * 255)),
                            int(round((0.0 if delay is None else clamp((delay - min_delay) / delay_span)) * 255)),
                            int(round(clamp(entropy_disp[display_row][column] or 0.0) * 255)),
                        )
                    )
                points = self._polar_cell_points(
                    cx,
                    cy,
                    scale,
                    INNER_BLANK_ROWS + ring_idx,
                    INNER_BLANK_ROWS + ring_idx + 1,
                    theta_edges[column],
                    theta_edges[column + 1],
                )
                canvas.create_polygon(points, fill=fill, outline="")

        self._draw_polar_selection_outline(
            canvas,
            cx,
            cy,
            scale,
            theta_edges,
            x_groups,
            y_groups,
            ring_rows,
        )
        outer_r = (INNER_BLANK_ROWS + n_rows) * scale
        canvas.create_oval(cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r, outline="#475467")
        legend_x = min(cx + outer_r + 26, w - 154)
        legend_y = max(64.0, cy - 40.0)
        for index, (label, color) in enumerate(
            (
                (f"R {value_mode_unit(self.value_mode_var.get())}", "#dc2626"),
                ("G delay", "#16a34a"),
                ("B entropy", "#2563eb"),
            )
        ):
            y = legend_y + index * 26
            canvas.create_rectangle(legend_x, y, legend_x + 16, y + 16, fill=color, outline="")
            canvas.create_text(legend_x + 24, y + 8, anchor="w", text=label, fill="#475467")
        self._canvas_layouts["delay"] = {
            "geometry": "polar",
            "cx": cx,
            "cy": cy,
            "scale": scale,
            "total_deg": total_deg,
            "x_groups": x_groups,
            "y_groups": y_groups,
            "ring_rows": ring_rows,
        }

    def _all_positions_timeline_values(
        self,
        unit_idx: int,
        time_groups: list[AxisGroup],
    ) -> list[float]:
        value_mode = self.value_mode_var.get()
        if value_mode == VALUE_MODE_COUNT:
            metrics = self.data.metrics(unit_idx)
            return [float(sum(metrics.bin_totals[start : end + 1])) for start, end in time_groups]

        presentation_total = sum(
            count
            for row in self.data.presentation_counts or []
            for count in row
            if count > 0
        )
        if presentation_total <= 0:
            return [0.0 for _group in time_groups]
        unit = self.data.counts[unit_idx]
        values: list[float] = []
        for start, end in time_groups:
            count = sum(
                float(sum(unit[y_idx][x_idx][start : end + 1]))
                for y_idx in range(self.data.n_y)
                for x_idx in range(self.data.n_x)
            )
            value = count / presentation_total
            if value_mode == VALUE_MODE_RATE:
                value /= self.data.time_span_seconds(start, end)
            values.append(value)
        return values

    def _ensure_timeline_preview_images(
        self,
        canvas: tk.Canvas,
        unit_idx: int,
        visible_bins: list[int],
        time_groups: list[AxisGroup],
        x_groups: list[AxisGroup],
        y_groups: list[AxisGroup],
        smooth_radius: int,
        cell_size: float,
        tile_positions: dict[int, tuple[float, float]],
        atlas_width: int,
        atlas_height: int,
    ) -> float:
        cache_key = (
            id(self.data),
            unit_idx,
            self.value_mode_var.get(),
            tuple(time_groups),
            tuple(visible_bins),
            tuple(x_groups),
            tuple(y_groups),
            smooth_radius,
            self.palette_var.get(),
            self.polar_layout_var.get(),
            self.polar_radius_var.get(),
            round(cell_size, 6),
            tuple((bin_idx, *tile_positions[bin_idx]) for bin_idx in visible_bins),
            atlas_width,
            atlas_height,
        )
        if self._timeline_preview_cache_key == cache_key:
            return self._timeline_preview_high

        prepared_by_bin: dict[int, list[list[float | None]]] = {}
        high = 0.0
        for bin_idx in visible_bins:
            source_start, source_end = time_groups[bin_idx]
            matrix = self.data.response_matrix(
                unit_idx,
                source_start,
                source_end,
                self.value_mode_var.get(),
            )
            prepared = reduce_matrix_xy(matrix, y_groups, x_groups)
            prepared = smooth_matrix(prepared, smooth_radius)
            prepared_by_bin[bin_idx] = prepared
            high = max(
                high,
                max(
                    (
                        float(value)
                        for row in prepared
                        for value in row
                        if value is not None and math.isfinite(float(value))
                    ),
                    default=0.0,
                ),
            )

        high = max(high, 1.0)
        palette = self.palette_var.get()
        color_for_value = lambda value, high=high, palette=palette: palette_color(value, 0.0, high, palette)
        if self.polar_layout_var.get():
            total_deg = self.data.infer_total_deg()
            if self.polar_radius_var.get() == POLAR_RADIUS_MODES[0]:
                ring_rows = sorted(range(len(y_groups)), key=lambda index: y_groups[index][0])
            else:
                ring_rows = list(range(len(y_groups) - 1, -1, -1))
            polar_tiles = [
                (
                    prepared_by_bin[bin_idx],
                    *tile_positions[bin_idx],
                    cell_size,
                    total_deg,
                    ring_rows,
                )
                for bin_idx in visible_bins
            ]
            ppm = polar_matrix_atlas_ppm_data(
                polar_tiles,
                atlas_width,
                atlas_height,
                color_for_value,
            )
        else:
            tiles = [
                (prepared_by_bin[bin_idx], *tile_positions[bin_idx], cell_size)
                for bin_idx in visible_bins
            ]
            ppm = matrix_atlas_ppm_data(
                tiles,
                atlas_width,
                atlas_height,
                color_for_value,
            )
        atlas = tk.PhotoImage(master=canvas, data=ppm, format="PPM")

        self._timeline_preview_cache_key = cache_key
        self._timeline_preview_images = {-1: atlas}
        self._timeline_preview_high = high
        return high

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
        label_gap = 4.0
        label_height = 12.0
        row_gap = max(10.0, min(16.0, height * 0.014))
        left = 44.0
        right_pad = 44.0
        available_w = max(120.0, width - left - right_pad)
        base_grid_h = min(78.0, max(44.0, min(screen_h * 0.085, window_h * 0.12)))
        density_scale = min(1.0, max(0.35, math.sqrt(50.0 / count)))
        target_grid_h = max(18.0, base_grid_h * density_scale)
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
        row_step = grid_h + label_gap + label_height + row_gap
        rows = int(math.ceil(count / cols))
        return {
            "left": left,
            "cols": cols,
            "rows": rows,
            "gap_x": gap_x,
            "label_gap": label_gap,
            "label_height": label_height,
            "row_gap": row_gap,
            "slot_w": slot_w,
            "cell": cell,
            "grid_w": grid_w,
            "grid_h": grid_h,
            "row_step": row_step,
        }

    def _draw_timeline(self) -> None:
        canvas = self.canvases["timeline"]
        canvas.delete("all")
        canvas_width = canvas.winfo_width()
        canvas_height = canvas.winfo_height()
        if canvas_width <= 1 and hasattr(self, "notebook"):
            canvas_width = max(canvas_width, self.notebook.winfo_width() - 20)
            canvas_height = max(canvas_height, self.notebook.winfo_height() - 34)
        w, h = max(canvas_width, 300), max(canvas_height, 280)
        unit_idx = self.unit_idx.get()
        time_groups = self._time_groups()
        display_bins = len(time_groups)
        visible_bins = self._visible_timeline_bins(display_bins)
        time_totals = self._all_positions_timeline_values(unit_idx, time_groups)
        axis_start_ms, axis_end_ms = self._time_axis_range_ms()
        timing_warning = " Negative bins may include previous-stimulus responses." if axis_start_ms < 0.0 else ""
        canvas.create_text(20, 22, anchor="w", text=f"Timeline and {display_bins} bin maps", font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(
            20,
            44,
            anchor="w",
            text=(
                f"Timeline selection {self._display_range_label()}; "
                f"time res {format_ms(self._time_group_size() * self._base_bin_ms())} ms; "
                f"{self.value_mode_var.get()}."
                f"{timing_warning}"
            ),
            fill="#667085",
        )

        chart_x, chart_y = 64, 78
        chart_w = max(320, w - 140)
        chart_h = 62
        max_total = max(max(time_totals), 1.0)
        zero_x: float | None = None
        if axis_start_ms <= 0.0 <= axis_end_ms and axis_end_ms > axis_start_ms:
            zero_x = chart_x + chart_w * (0.0 - axis_start_ms) / (axis_end_ms - axis_start_ms)
            if axis_start_ms < 0.0:
                canvas.create_rectangle(chart_x, chart_y, zero_x, chart_y + chart_h, fill="#f8fafc", outline="")
        canvas.create_rectangle(chart_x, chart_y, chart_x + chart_w, chart_y + chart_h, outline="#cbd5e1")
        if zero_x is not None:
            canvas.create_line(zero_x, chart_y, zero_x, chart_y + chart_h, fill="#7c3aed", width=1, dash=(4, 3))
            canvas.create_text(zero_x + 4, chart_y + 5, anchor="nw", text="VS 0 ms", fill="#6d28d9", font=("TkDefaultFont", 8, "bold"))

        legend_y = chart_y - 11
        canvas.create_line(chart_x, legend_y, chart_x + 16, legend_y, fill="#2563eb", width=2)
        all_positions_label = (
            "All positions (sum)"
            if self.value_mode_var.get() == VALUE_MODE_COUNT
            else "All positions (weighted mean)"
        )
        canvas.create_text(
            chart_x + 21,
            legend_y,
            anchor="w",
            text=all_positions_label,
            fill="#2563eb",
            font=("TkDefaultFont", 8),
        )
        if self.selected_cell is not None:
            canvas.create_line(chart_x + 196, legend_y, chart_x + 212, legend_y, fill="#dc2626", width=2)
            canvas.create_text(chart_x + 217, legend_y, anchor="w", text="Selected cell", fill="#dc2626", font=("TkDefaultFont", 8))
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
            selected_values_optional = self._group_response_values(y_start, y_end, x_start, x_end)
            selected_values = [float(value) if value is not None else 0.0 for value in selected_values_optional]
            selected_max = max(max(selected_values), 1.0)
            selected_points: list[float] = []
            for bin_idx, value in enumerate(selected_values):
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
            canvas.create_text(
                red_axis_x - 7,
                chart_y,
                anchor="e",
                text=format_response_value(selected_max, self.value_mode_var.get()),
                fill="#dc2626",
                font=axis_font,
            )
            canvas.create_text(red_axis_x - 7, chart_y + chart_h, anchor="e", text="0", fill="#dc2626", font=axis_font)
        canvas.create_line(blue_axis_x, chart_y, blue_axis_x, chart_y + chart_h, fill="#2563eb", width=1)
        canvas.create_line(blue_axis_x, chart_y, blue_axis_x + 4, chart_y, fill="#2563eb")
        canvas.create_line(blue_axis_x, chart_y + chart_h, blue_axis_x + 4, chart_y + chart_h, fill="#2563eb")
        canvas.create_text(
            blue_axis_x + 7,
            chart_y,
            anchor="w",
            text=format_response_value(max_total, self.value_mode_var.get()),
            fill="#2563eb",
            font=axis_font,
        )
        canvas.create_text(blue_axis_x + 7, chart_y + chart_h, anchor="w", text="0", fill="#2563eb", font=axis_font)
        if self._has_time_selection():
            selected_start_ms, selected_end_ms = self._timeline_selected_time_bounds_ms()
            time_span_ms = max(axis_end_ms - axis_start_ms, self._base_bin_ms())
            range_x0 = chart_x + chart_w * (selected_start_ms - axis_start_ms) / time_span_ms
            range_x1 = chart_x + chart_w * (selected_end_ms - axis_start_ms) / time_span_ms
            canvas.create_rectangle(range_x0, chart_y, range_x1, chart_y + chart_h, outline="#16a34a", width=1)
        max_tick_intervals = 5
        tick_step = max(1, int(math.ceil(display_bins / max_tick_intervals)))
        tick_boundaries = list(range(0, display_bins + 1, tick_step))
        if tick_boundaries[-1] != display_bins:
            tick_boundaries.append(display_bins)
        for boundary in tick_boundaries:
            x = chart_x + chart_w * boundary / display_bins
            time_ms = axis_start_ms if boundary == 0 else self._time_group_bounds_ms(boundary - 1)[1]
            anchor = "w" if boundary == 0 else ("e" if boundary == display_bins else "center")
            canvas.create_line(x, chart_y + chart_h, x, chart_y + chart_h + 4, fill="#64748b")
            canvas.create_text(
                x,
                chart_y + chart_h + 17,
                anchor=anchor,
                text=format_ms(time_ms),
                fill="#475467",
                font=("TkDefaultFont", 8),
            )
        canvas.create_text(
            chart_x + chart_w / 2,
            chart_y + chart_h + 36,
            anchor="center",
            text="Time from VS onset (ms)",
            fill="#475467",
            font=("TkDefaultFont", 9),
        )

        mini_top = chart_y + chart_h + 54
        preview_x_groups = self._x_groups()
        preview_y_groups = self._display_y_groups()
        smooth_radius = self._smooth_radius()
        if self.polar_layout_var.get():
            polar_diameter_units = 2 * (INNER_BLANK_ROWS + len(preview_y_groups))
            layout_x_count = polar_diameter_units
            layout_y_count = polar_diameter_units
        else:
            layout_x_count = len(preview_x_groups)
            layout_y_count = len(preview_y_groups)
        mini_layout = self._timeline_mini_layout(
            canvas,
            w,
            h,
            mini_top,
            len(visible_bins),
            layout_x_count,
            layout_y_count,
        )
        cols = int(mini_layout["cols"])
        rows = int(mini_layout["rows"])
        gap_x = float(mini_layout["gap_x"])
        label_gap = float(mini_layout["label_gap"])
        label_height = float(mini_layout["label_height"])
        row_gap = float(mini_layout["row_gap"])
        slot_w = float(mini_layout["slot_w"])
        preview_cell = float(mini_layout["cell"])
        preview_grid_w = float(mini_layout["grid_w"])
        preview_grid_h = float(mini_layout["grid_h"])
        row_step = float(mini_layout["row_step"])
        mini_left = float(mini_layout["left"])
        tile_positions: dict[int, tuple[float, float]] = {}
        for visible_idx, bin_idx in enumerate(visible_bins):
            row = visible_idx // cols
            col = visible_idx % cols
            slot_x = mini_left + col * (slot_w + gap_x)
            x0 = slot_x + max(0.0, (slot_w - preview_grid_w) / 2.0)
            tile_positions[bin_idx] = (x0 - mini_left, row * row_step)
        atlas_width = max(
            1,
            int(math.ceil(max((x + preview_grid_w for x, _y in tile_positions.values()), default=1.0))),
        )
        atlas_height = max(
            1,
            int(math.ceil(max((y + preview_grid_h for _x, y in tile_positions.values()), default=1.0))),
        )
        self._ensure_timeline_preview_images(
            canvas,
            unit_idx,
            visible_bins,
            time_groups,
            preview_x_groups,
            preview_y_groups,
            smooth_radius,
            preview_cell,
            tile_positions,
            atlas_width,
            atlas_height,
        )
        canvas.create_image(
            mini_left,
            mini_top,
            anchor="nw",
            image=self._timeline_preview_images[-1],
        )
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
            "label_gap": label_gap,
            "label_height": label_height,
            "row_gap": row_gap,
            "row_step": row_step,
            "cols": cols,
            "display_bins": display_bins,
            "visible_bins": visible_bins,
        }
        self._timeline_cells = []
        self._timeline_cells_by_bin = {}
        selected_start, selected_end = self._timeline_selected_source_bins()
        has_time_selection = self._has_time_selection()

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
            timeline_layout: dict[str, object] = {
                "geometry": "polar" if self.polar_layout_var.get() else "rectangle",
                "bin_idx": bin_idx,
                "source_start": source_start,
                "source_end": source_end,
                "x0": x0,
                "y0": y0,
                "cell": cell,
                "grid_w": grid_w,
                "grid_h": grid_h,
                "label_gap": label_gap,
                "label_height": label_height,
                "x_groups": preview_x_groups,
                "y_groups": preview_y_groups,
            }
            self._timeline_cells.append(timeline_layout)
            self._timeline_cells_by_bin[bin_idx] = timeline_layout
            if self.polar_layout_var.get():
                timeline_layout.update(
                    {
                        "cx": x0 + grid_w / 2.0,
                        "cy": y0 + grid_h / 2.0,
                        "scale": cell,
                        "total_deg": self.data.infer_total_deg(),
                        "ring_rows": (
                            sorted(
                                range(len(preview_y_groups)),
                                key=lambda index: preview_y_groups[index][0],
                            )
                            if self.polar_radius_var.get() == POLAR_RADIUS_MODES[0]
                            else list(range(len(preview_y_groups) - 1, -1, -1))
                        ),
                    }
                )
            in_selected_range = source_start <= selected_end and source_end >= selected_start
            if has_time_selection and in_selected_range:
                outline = "#16a34a"
                width_line = 2
            else:
                outline = "#cbd5e1"
                width_line = 1
            if self.polar_layout_var.get():
                canvas.create_oval(x0, y0, x0 + grid_w, y0 + grid_h, outline=outline, width=width_line)
            else:
                canvas.create_rectangle(x0, y0, x0 + grid_w, y0 + grid_h, outline=outline, width=width_line)
            label_color = "#15803d" if has_time_selection and in_selected_range else "#475467"
            label_font = ("TkDefaultFont", 8, "bold") if has_time_selection and in_selected_range else ("TkDefaultFont", 8)
            canvas.create_text(
                x0,
                y0 + grid_h + label_gap,
                anchor="nw",
                text=f"{format_ms(self.data.time_bin_edges[source_start] * 1000.0)} ms",
                fill=label_color,
                font=label_font,
            )
        content_bottom = (
            mini_top
            + max(0, rows - 1) * row_step
            + preview_grid_h
            + label_gap
            + label_height
            + 12
        )
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

    def _timeline_layout_at_point(
        self,
        event_x: float,
        event_y: float,
        *,
        include_label: bool,
    ) -> dict[str, object] | None:
        """Find the one timeline mini-map candidate at a canvas coordinate."""
        timeline_layout = self._canvas_layouts.get("timeline")
        if not timeline_layout or not self._timeline_cells:
            return None
        mini_left = float(timeline_layout["mini_left"])
        mini_top = float(timeline_layout["mini_top"])
        slot_w = float(timeline_layout["mini_w"])
        gap_x = float(timeline_layout["gap_x"])
        row_step = float(timeline_layout["row_step"])
        cols = max(1, int(timeline_layout["cols"]))
        relative_x = event_x - mini_left
        relative_y = event_y - mini_top
        slot_stride = slot_w + gap_x
        if relative_x < 0.0 or relative_y < 0.0 or slot_stride <= 0.0 or row_step <= 0.0:
            return None
        column = int(relative_x // slot_stride)
        row = int(relative_y // row_step)
        if not (0 <= column < cols and row >= 0):
            return None
        candidate_index = row * cols + column
        if not (0 <= candidate_index < len(self._timeline_cells)):
            return None
        candidate = self._timeline_cells[candidate_index]
        x0 = float(candidate["x0"])
        y0 = float(candidate["y0"])
        grid_w = float(candidate["grid_w"])
        grid_h = float(candidate["grid_h"])
        if include_label:
            bottom = y0 + grid_h + float(candidate.get("label_gap", 4.0)) + float(
                candidate.get("label_height", 12.0)
            )
            inside = x0 <= event_x <= x0 + grid_w and y0 <= event_y <= bottom
        else:
            inside = x0 <= event_x < x0 + grid_w and y0 <= event_y < y0 + grid_h
        return candidate if inside else None

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
        cell_layout = self._timeline_layout_at_point(event_x, event_y, include_label=True)
        return int(cell_layout["bin_idx"]) if cell_layout is not None else None

    def _timeline_cell_at(self, event: tk.Event) -> tuple[int, CellRef] | None:
        canvas = self.canvases["timeline"]
        event_x = canvas.canvasx(event.x)
        event_y = canvas.canvasy(event.y)
        layout = self._timeline_layout_at_point(event_x, event_y, include_label=False)
        if layout is None:
            return None
        if layout.get("geometry") == "polar":
            polar_cell = self._polar_cell_from_layout(layout, event_x, event_y)
            if polar_cell is None:
                return None
            _ring_idx, cell_ref = polar_cell
            return int(layout["bin_idx"]), cell_ref
        x0 = float(layout["x0"])
        y0 = float(layout["y0"])
        cell = float(layout["cell"])
        group_idx = int((event_x - x0) // cell)
        display_y = int((event_y - y0) // cell)
        x_groups = layout.get("x_groups") or self._x_groups()
        y_groups = layout.get("y_groups") or self._display_y_groups()
        if 0 <= group_idx < len(x_groups) and 0 <= display_y < len(y_groups):
            y_start, y_end = y_groups[display_y]
            x_start, x_end = x_groups[group_idx]
            return int(layout["bin_idx"]), (y_start, y_end, x_start, x_end)
        return None

    def _polar_cell_at(self, key: str, event: tk.Event) -> tuple[int, CellRef] | None:
        layout = self._canvas_layouts.get(key)
        if not layout:
            return None
        canvas = self.canvases[key]
        return self._polar_cell_from_layout(layout, canvas.canvasx(event.x), canvas.canvasy(event.y))

    def _polar_cell_from_layout(
        self,
        layout: dict[str, object],
        event_x: float,
        event_y: float,
    ) -> tuple[int, CellRef] | None:
        cx = layout["cx"]
        cy = layout["cy"]
        scale = layout["scale"]
        total_deg = layout["total_deg"]
        x_groups = layout.get("x_groups") or self._x_groups()
        y_groups = layout.get("y_groups") or self._display_y_groups()
        ring_rows = layout.get("ring_rows")
        if not isinstance(ring_rows, list):
            ring_rows = list(range(len(y_groups) - 1, -1, -1))
        dx = (event_x - cx) / scale
        dy = (cy - event_y) / scale
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
        if self._selected_local_unit_index() is None:
            return
        if key in {"rf", "delay"}:
            if self._canvas_layouts.get(key, {}).get("geometry") == "polar":
                polar_cell = self._polar_cell_at(key, event)
                if polar_cell is not None:
                    ring_idx, cell = polar_cell
                    self._set_hover_cell(key, cell, event, extra=f"polar ring {ring_idx + 1}")
                else:
                    self._clear_canvas_hover(key)
            else:
                cell = self._canvas_to_cell(key, event)
                if cell is not None:
                    self._set_hover_cell(key, cell, event)
                else:
                    self._clear_canvas_hover(key)
        elif key == "timeline":
            cell = self._timeline_cell_at(event)
            if cell is not None:
                bin_idx, cell_ref = cell
                self._set_hover_cell(
                    key,
                    cell_ref,
                    event,
                    extra=f"timeline bin {self._time_group_label(bin_idx)}",
                    display_bin=bin_idx,
                )
            else:
                bin_idx = self._timeline_bin_at(event)
                if bin_idx is not None:
                    self.status_label.configure(text=f"Hover bin {self._time_group_label(bin_idx)}")
                self._clear_canvas_hover(key, keep_status=bin_idx is not None)

    def _on_canvas_click(self, key: str, event: tk.Event) -> None:
        self.canvases[key].focus_set()
        if self._selected_local_unit_index() is None:
            return
        if key in {"rf", "delay"}:
            if self._canvas_layouts.get(key, {}).get("geometry") == "polar":
                polar_cell = self._polar_cell_at(key, event)
                cell = polar_cell[1] if polar_cell is not None else None
            else:
                cell = self._canvas_to_cell(key, event)
            if cell is not None:
                self.selected_cell = cell
                self._update_all()
                self._publish_pairing_state_if_changed()
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
                self._publish_pairing_state_if_changed()

    def _select_timeline_bin(self, bin_idx: int, event: tk.Event) -> None:
        if self._event_has_range_modifier(event):
            if self._timeline_range_anchor is None:
                self._timeline_range_anchor = self.range_start_var.get()
            start = min(self._timeline_range_anchor, bin_idx)
            end = max(self._timeline_range_anchor, bin_idx)
            self.range_start_var.set(start)
            self.range_end_var.set(end)
            self.bin_var.set(bin_idx)
            self._timeline_range_anchor = bin_idx
        else:
            self._timeline_range_anchor = bin_idx
            self.bin_var.set(bin_idx)
            self.range_start_var.set(bin_idx)
            self.range_end_var.set(bin_idx)
        self._sync_time_range_controls()

    def _event_has_range_modifier(self, event: tk.Event) -> bool:
        state = int(getattr(event, "state", 0) or 0)
        # Tk uses platform-dependent modifier bits. Include Shift, Control,
        # Option/Alt, Command/Meta candidates so the behavior works on macOS.
        modifier_mask = 0x100000 | 0x0001 | 0x0004 | 0x0008 | 0x0010 | 0x0020 | 0x0040 | 0x0080
        return bool(state & modifier_mask)

    def _clear_hover(self) -> None:
        had_hover = self._hover_signature is not None or self.hover_cell is not None
        for canvas in self.canvases.values():
            canvas.delete("hover")
        self.hover_cell = None
        self._hover_signature = None
        self._hover_tooltip_text = ""
        if had_hover and self._selected_local_unit_index() is not None:
            self._update_cell_label(cell=self.selected_cell)
        if self._selected_local_unit_index() is None:
            self.status_label.configure(
                text=(
                    f"N/A: cluster {self._selected_unit_id_value()} is not available in this "
                    "session. Use ←/→ to continue through the paired unit list."
                )
            )
            return
        self.status_label.configure(
            text=(
                f"x: {format_pos(self.data.x_positions[0])}..{format_pos(self.data.x_positions[-1])}  "
                f"y: {format_pos(self.data.y_positions[0])}..{format_pos(self.data.y_positions[-1])}  "
                f"time: {format_ms(self._time_axis_start_ms())}..{format_ms(self._time_axis_end_ms())} ms  "
                f"value: {self.value_mode_var.get()}"
            )
        )

    def _set_hover_cell(
        self,
        key: str,
        cell: CellRef,
        event: tk.Event,
        polygon: tuple[tuple[float, float], ...] | None = None,
        extra: str = "",
        display_bin: int | None = None,
    ) -> None:
        effective_bin = self.bin_var.get() if display_bin is None else int(display_bin)
        signature = (
            key,
            id(self.data),
            self.unit_idx.get(),
            cell,
            effective_bin,
            self.value_mode_var.get(),
            self.time_res_ms_var.get(),
            self.range_start_ms_var.get(),
            self.range_end_ms_var.get(),
            extra,
        )
        if signature != self._hover_signature:
            self._hover_signature = signature
            self.hover_cell = cell
            y_start, y_end, x_idx, x_end = cell
            self.status_label.configure(
                text=(
                    f"Hover {extra + '; ' if extra else ''}"
                    f"{self._y_group_text(y_start, y_end)}, {self._x_group_text(x_idx, x_end)}"
                )
            )
            self._update_cell_label(cell=cell, prefix="Hover\n", display_bin=display_bin)
            self._hover_tooltip_text = self._cell_tooltip_text(cell, display_bin=display_bin)
        self._draw_hover_overlay(
            key,
            cell,
            event,
            polygon=polygon,
            display_bin=display_bin,
            tooltip_text=self._hover_tooltip_text,
        )

    def _clear_canvas_hover(self, key: str, keep_status: bool = False) -> None:
        canvas = self.canvases.get(key)
        if canvas is not None:
            canvas.delete("hover")
        if self._hover_signature is None and self.hover_cell is None:
            return
        self.hover_cell = None
        self._hover_signature = None
        self._hover_tooltip_text = ""
        if self._selected_local_unit_index() is not None:
            self._update_cell_label(cell=self.selected_cell)

    def _draw_hover_overlay(
        self,
        key: str,
        cell: CellRef,
        event: tk.Event,
        polygon: tuple[tuple[float, float], ...] | None = None,
        display_bin: int | None = None,
        tooltip_text: str = "",
    ) -> None:
        canvas = self.canvases[key]
        canvas.delete("hover")
        y_start, _y_end, x_idx, _x_end = cell
        if polygon is not None:
            coords: list[float] = []
            for x, y in polygon:
                coords.extend((x, y))
            canvas.create_polygon(*coords, fill="", outline="#f97316", width=3, tags="hover")
        elif key in {"rf", "delay"} and self._canvas_layouts.get(key, {}).get("geometry") != "polar":
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
        elif key in {"rf", "delay"}:
            polar = self._polar_cell_at(key, event)
            layout = self._canvas_layouts.get(key)
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
            if display_bin is not None:
                bin_idx = int(display_bin)
                y_start_t, _y_end_t, x_idx_t, _x_end_t = cell
                layout = self._timeline_cells_by_bin.get(bin_idx)
                if layout is not None:
                    y_groups = layout.get("y_groups") or self._display_y_groups()
                    display_y = next((idx for idx, (start, end) in enumerate(y_groups) if start <= y_start_t <= end), 0)
                    x_groups = layout.get("x_groups") or self._x_groups()
                    group_idx = next((idx for idx, (start, end) in enumerate(x_groups) if start <= x_idx_t <= end), 0)
                    x0 = float(layout["x0"])
                    y0 = float(layout["y0"])
                    cell_size = float(layout["cell"])
                    if layout.get("geometry") == "polar":
                        polar = self._polar_cell_from_layout(
                            layout,
                            canvas.canvasx(event.x),
                            canvas.canvasy(event.y),
                        )
                        if polar is None:
                            self._draw_canvas_tooltip(canvas, event, tooltip_text)
                            return
                        ring_idx, polar_cell = polar
                        _polar_y_start, _polar_y_end, polar_x_start, _polar_x_end = polar_cell
                        x_groups = layout.get("x_groups") or self._x_groups()
                        column = next(
                            (
                                index
                                for index, (start, end) in enumerate(x_groups)
                                if start <= polar_x_start <= end
                            ),
                            0,
                        )
                        total_deg = float(layout["total_deg"])
                        theta_edges = [
                            math.radians(
                                90.0 + total_deg / 2.0 - total_deg * index / len(x_groups)
                            )
                            for index in range(len(x_groups) + 1)
                        ]
                        points = self._polar_cell_points(
                            float(layout["cx"]),
                            float(layout["cy"]),
                            float(layout["scale"]),
                            INNER_BLANK_ROWS + ring_idx,
                            INNER_BLANK_ROWS + ring_idx + 1,
                            theta_edges[column],
                            theta_edges[column + 1],
                        )
                        canvas.create_polygon(
                            points,
                            fill="",
                            outline="#f97316",
                            width=2,
                            tags="hover",
                        )
                    else:
                        x = x0 + group_idx * cell_size
                        y = y0 + display_y * cell_size
                        canvas.create_rectangle(
                            x,
                            y,
                            x + cell_size,
                            y + cell_size,
                            outline="#f97316",
                            width=2,
                            tags="hover",
                        )
        self._draw_canvas_tooltip(canvas, event, tooltip_text)

    def _draw_canvas_tooltip(
        self,
        canvas: tk.Canvas,
        event: tk.Event,
        text: str,
    ) -> None:
        line_count = max(1, len(text.splitlines()))
        pad = 8
        event_x = canvas.canvasx(event.x)
        event_y = canvas.canvasy(event.y)
        x = event_x + 14
        y = event_y + 14
        width = 190
        height = 22 + 15 * (line_count - 1) + pad
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

    def _load_json_path(self, path: Path) -> None:
        try:
            self.data = RFMappingData(path)
        except Exception as exc:
            messagebox.showerror("Could not load JSON", str(exc))
            return
        self.title(f"{self.data.path.name} — RF Map Viewer {APP_VERSION}")
        self.unit_idx.set(0)
        self._selected_unit_id = self.data.unit_pool[0]
        self._last_supported_unit_id = self.data.unit_pool[0]
        self.bin_var.set(0)
        self.range_start_var.set(0)
        self.time_res_ms_var.set(format_ms(self._base_bin_ms()))
        self._last_time_group_count = self.data.n_bins
        self._last_time_groups = [(index, index) for index in range(self.data.n_bins)]
        self.range_end_var.set(self._time_group_count() - 1)
        plot_start_ms, plot_end_ms = self._default_plot_time_bounds_ms()
        self.range_start_ms_var.set(format_ms(plot_start_ms))
        self.range_end_ms_var.set(format_ms(plot_end_ms))
        if not self.data.supports_value_mode(self.value_mode_var.get()):
            self.value_mode_var.set(VALUE_MODE_COUNT)
        self.selected_cell = None
        self.hover_cell = None
        self._hover_signature = None
        self._hover_tooltip_text = ""
        self._timeline_preview_cache_key = None
        self._timeline_preview_images = {}
        self._timeline_preview_high = 1.0
        self._timeline_cells = []
        self._timeline_cells_by_bin = {}
        self._timeline_range_anchor = None
        self._timeline_scroll_fraction = 0.0
        self._pair_last_local_state = None
        self._sync_time_control_ranges()
        self.time_res_spin.configure(from_=self._base_bin_ms(), to=self._total_time_ms(), increment=self._base_bin_ms())
        self.x_bins_var.set(self.data.n_x)
        self.y_bins_var.set(self.data.n_y)
        self.x_bins_spin.configure(to=self.data.n_x)
        self.y_bins_spin.configure(to=self.data.n_y)
        self._sync_json_combo()
        self._sync_unit_combo()
        self._update_all()
        self._pair_ready_viewer_set_changed(adopt_viewer=self)

    def _open_figure_exporter(self) -> None:
        existing = self.__dict__.get("_figure_export_window")
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        self._figure_export_window = FigureExportWindow(self)

    def _export_current_matrix(self) -> None:
        if self._selected_local_unit_index() is None:
            messagebox.showinfo(
                "Unit unavailable",
                f"Cluster {self._selected_unit_id_value()} is not available in this session.",
                parent=self,
            )
            return
        raw_matrix = self._current_matrix()
        matrix, x_groups, y_groups = self._prepare_plot_matrix(raw_matrix, smooth=True)
        export_space = "displayed"

        range_start, range_end = self._plot_range_group_indices()
        range_start_ms, range_end_ms = self._selected_time_bounds_ms()
        value_mode = self.value_mode_var.get()
        path = filedialog.asksaveasfilename(
            title=f"Export {export_space} RF matrix",
            defaultextension=".csv",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
            initialfile=(
                f"unit_{self.unit_idx.get():03d}_cluster_{self.data.cluster_id(self.unit_idx.get())}_"
                f"{value_mode_slug(value_mode)}_displayed.csv"
            ),
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
                        "value_mode",
                        "value_unit",
                        "presentation_count_min",
                        "presentation_count_max",
                        "mode",
                        "display_y_index_0based",
                        "source_y_start_0based",
                        "source_y_end_0based",
                        "source_y_start_matlab",
                        "source_y_end_matlab",
                        "y_position_start",
                        "y_position_end",
                        "display_x_index_0based",
                        "source_x_start_0based",
                        "source_x_end_0based",
                        "source_x_start_matlab",
                        "source_x_end_matlab",
                        "x_position_start",
                        "x_position_end",
                        "export_space",
                        "time_resolution_ms",
                        "rf_range_start_group_0based",
                        "rf_range_end_group_0based",
                        "rf_range_start_ms",
                        "rf_range_end_ms",
                        "display_x_bins",
                        "display_y_bins",
                        "smooth_radius",
                        "flip_y",
                        "palette",
                        "source_json",
                    ]
                )
                for display_y, (y_start, y_end) in enumerate(y_groups):
                    for display_x, (x_start, x_end) in enumerate(x_groups):
                        presentation_counts = (
                            [
                                self.data.presentation_counts[y_idx][x_idx]
                                for y_idx in range(y_start, y_end + 1)
                                for x_idx in range(x_start, x_end + 1)
                            ]
                            if self.data.presentation_counts is not None
                            else []
                        )
                        writer.writerow(
                            [
                                self.unit_idx.get(),
                                self.data.cluster_id(self.unit_idx.get()),
                                y_start,
                                y_start + 1,
                                (self.data.y_positions[y_start] + self.data.y_positions[y_end]) / 2.0,
                                x_start,
                                x_start + 1,
                                (self.data.x_positions[x_start] + self.data.x_positions[x_end]) / 2.0,
                                matrix[display_y][display_x],
                                value_mode,
                                value_mode_unit(value_mode),
                                min(presentation_counts) if presentation_counts else "",
                                max(presentation_counts) if presentation_counts else "",
                                self._current_matrix_label(),
                                display_y,
                                y_start,
                                y_end,
                                y_start + 1,
                                y_end + 1,
                                self.data.y_positions[y_start],
                                self.data.y_positions[y_end],
                                display_x,
                                x_start,
                                x_end,
                                x_start + 1,
                                x_end + 1,
                                self.data.x_positions[x_start],
                                self.data.x_positions[x_end],
                                export_space,
                                format_ms(self._time_group_size() * self._base_bin_ms()),
                                range_start,
                                range_end,
                                range_start_ms,
                                range_end_ms,
                                self._x_target_bins(),
                                self._y_target_bins(),
                                self._smooth_radius(),
                                self.flip_y_var.get(),
                                self.palette_var.get(),
                                self.data.path,
                            ]
                        )
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        messagebox.showinfo("Export complete", f"Wrote {export_space} matrix to {path}")


@dataclass(frozen=True)
class FigureViewerSnapshot:
    """Immutable viewer settings used by preview and final figure rendering."""

    value_mode: str
    rf_source_start: int
    rf_source_end: int
    time_groups: tuple[AxisGroup, ...]
    x_groups: tuple[AxisGroup, ...]
    y_groups: tuple[AxisGroup, ...]
    smooth_radius: int
    palette: str
    polar_radius: str
    timeline_polar: bool
    selected_cell: CellRef | None
    total_degrees: float
    timeline_range_start: int = 0
    timeline_range_end: int = -1
    timeline_active_bin: int = 0

    @classmethod
    def capture(cls, viewer: RFMViewer) -> FigureViewerSnapshot:
        source_start, source_end = viewer._source_bins_for_time_controls()
        timeline_range_start, timeline_range_end = viewer._display_range_indices()
        return cls(
            value_mode=viewer.value_mode_var.get(),
            rf_source_start=source_start,
            rf_source_end=source_end,
            time_groups=tuple(viewer._time_groups()),
            x_groups=tuple(viewer._x_groups()),
            y_groups=tuple(viewer._display_y_groups()),
            smooth_radius=viewer._smooth_radius(),
            palette=viewer.palette_var.get(),
            polar_radius=viewer.polar_radius_var.get(),
            timeline_polar=bool(viewer.polar_layout_var.get()),
            selected_cell=viewer.selected_cell,
            total_degrees=viewer.data.infer_total_deg(),
            timeline_range_start=timeline_range_start,
            timeline_range_end=timeline_range_end,
            timeline_active_bin=max(
                0,
                min(len(viewer._time_groups()) - 1, int(viewer.bin_var.get())),
            ),
        )


class GUIFigureDataProvider:
    """Prepare every registered figure without mutating the live viewer."""

    def __init__(self, data: RFMappingData, snapshot: FigureViewerSnapshot):
        self.data = data
        self.snapshot = snapshot

    def __call__(self, unit_id: int, template: PlotSpec) -> PlotSpec:
        try:
            unit_idx = self.data.rf_map_by_unit_id(unit_id).unit_index
        except KeyError:
            return replace(
                template,
                data={"unavailable": f"Unit {unit_id} is unavailable in this RF dataset."},
            )

        kind = template.kind
        options = dict(template.options)
        options.setdefault("palette", self.snapshot.palette)
        options.setdefault("total_degrees", self.snapshot.total_degrees)
        if kind in {
            PlotKind.RF_POLAR,
            PlotKind.DELAY_POLAR,
            PlotKind.RGB_POLAR,
            PlotKind.TIMELINE_CURRENT,
        }:
            options.setdefault("inner_blank_rows", INNER_BLANK_ROWS)
        if kind in {PlotKind.RF_CARTESIAN, PlotKind.RF_POLAR}:
            payload = self._rf_matrix(unit_idx, polar=kind is PlotKind.RF_POLAR)
        elif kind in {PlotKind.DELAY_CARTESIAN, PlotKind.DELAY_POLAR}:
            options["palette"] = "delay"
            options["vmin"] = self.data.time_bin_edges[0] * 1000.0
            options["vmax"] = self.data.time_bin_edges[-1] * 1000.0
            payload = self._delay_matrix(unit_idx, polar=kind is PlotKind.DELAY_POLAR)
        elif kind in {PlotKind.RGB_CARTESIAN, PlotKind.RGB_POLAR}:
            payload = self._rgb_matrix(unit_idx, polar=kind is PlotKind.RGB_POLAR)
        elif kind is PlotKind.TIMELINE_CURRENT:
            options["polar"] = self.snapshot.timeline_polar
            payload = self._timeline_payload(unit_idx)
        elif kind in {PlotKind.HD_LINE, PlotKind.HD_POLAR}:
            payload = self._hd_payload(unit_id)
        elif kind is PlotKind.PROBE_LAYOUT:
            payload = {
                "unavailable": (
                    "Probe layout data is not loaded in the Python viewer for this "
                    "dataset. The page is retained so the export recipe stays complete."
                )
            }
        else:
            payload = {"unavailable": f"Unsupported figure kind: {kind.value}"}
        return replace(template, data=payload, options=options)

    def _prepare(
        self,
        matrix: list[list[float | None]],
        *,
        polar: bool,
    ) -> list[list[float | None]]:
        prepared = reduce_matrix_xy(
            matrix,
            list(self.snapshot.y_groups),
            list(self.snapshot.x_groups),
        )
        prepared = smooth_matrix(prepared, self.snapshot.smooth_radius)
        if not polar:
            return prepared
        if self.snapshot.polar_radius == POLAR_RADIUS_MODES[0]:
            ring_rows = sorted(
                range(len(self.snapshot.y_groups)),
                key=lambda index: self.snapshot.y_groups[index][0],
            )
        else:
            ring_rows = list(range(len(prepared) - 1, -1, -1))
        return [prepared[index] for index in ring_rows]

    def _rf_matrix(self, unit_idx: int, *, polar: bool) -> list[list[float | None]]:
        raw = self.data.response_matrix(
            unit_idx,
            self.snapshot.rf_source_start,
            self.snapshot.rf_source_end,
            self.snapshot.value_mode,
        )
        return self._prepare(raw, polar=polar)

    def _delay_raw(self, unit_idx: int) -> list[list[float | None]]:
        unit = self.data.rf_map(unit_idx).spike_counts
        metrics = self.data.metrics(unit_idx)
        result: list[list[float | None]] = []
        for y_idx in range(self.data.n_y):
            row: list[float | None] = []
            for x_idx in range(self.data.n_x):
                if metrics.total[y_idx][x_idx] <= 0:
                    row.append(None)
                    continue
                hist = unit[y_idx, x_idx]
                grouped = [
                    float(hist[start : end + 1].sum())
                    for start, end in self.snapshot.time_groups
                ]
                if not grouped or max(grouped) <= 0:
                    row.append(None)
                    continue
                peak = max(range(len(grouped)), key=grouped.__getitem__)
                start, end = self.snapshot.time_groups[peak]
                row.append(
                    (
                        self.data.time_bin_edges[start]
                        + self.data.time_bin_edges[end + 1]
                    )
                    * 500.0
                )
            result.append(row)
        return result

    def _delay_matrix(self, unit_idx: int, *, polar: bool) -> list[list[float | None]]:
        return self._prepare(self._delay_raw(unit_idx), polar=polar)

    def _rgb_matrix(self, unit_idx: int, *, polar: bool) -> list[list[tuple[int, int, int]]]:
        response = self._prepare(
            self.data.response_matrix(
                unit_idx,
                0,
                self.data.n_bins - 1,
                self.snapshot.value_mode,
            ),
            polar=polar,
        )
        delay = self._prepare(self._delay_raw(unit_idx), polar=polar)
        entropy = self._prepare(self.data.metrics(unit_idx).entropy, polar=polar)
        response_values = [
            float(value)
            for row in response
            for value in row
            if value is not None and math.isfinite(float(value))
        ]
        response_high = max(response_values, default=0.0)
        max_response = max(response_high, 1.0)
        delay_start = self.data.time_bin_edges[0] * 1000.0
        delay_end = self.data.time_bin_edges[-1] * 1000.0
        delay_span = max(delay_end - delay_start, 1.0)
        rgb: list[list[tuple[int, int, int]]] = []
        for y_idx, row in enumerate(response):
            output_row: list[tuple[int, int, int]] = []
            for x_idx, value in enumerate(row):
                response_value = float(value) if value is not None else 0.0
                delay_value = delay[y_idx][x_idx]
                entropy_value = entropy[y_idx][x_idx]
                if response_value <= 0:
                    output_row.append((237, 240, 243))
                else:
                    output_row.append(
                        (
                            int(round(clamp(response_value / max_response) * 255)),
                            int(
                                round(
                                    clamp(
                                        (
                                            (float(delay_value) if delay_value is not None else delay_start)
                                            - delay_start
                                        )
                                        / delay_span
                                    )
                                    * 255
                                )
                            ),
                            int(round(clamp(float(entropy_value or 0.0)) * 255)),
                        )
                    )
            rgb.append(output_row)
        return rgb

    def _all_positions_timeline(self, unit_idx: int) -> list[float]:
        if self.snapshot.value_mode == VALUE_MODE_COUNT:
            totals = self.data.metrics(unit_idx).bin_totals
            return [
                float(sum(totals[start : end + 1]))
                for start, end in self.snapshot.time_groups
            ]
        presentations = self.data.presentation_counts or []
        presentation_total = sum(
            float(count) for row in presentations for count in row if count > 0
        )
        if presentation_total <= 0:
            return [0.0 for _group in self.snapshot.time_groups]
        unit = self.data.rf_map(unit_idx).spike_counts
        values: list[float] = []
        for start, end in self.snapshot.time_groups:
            value = float(unit[..., start : end + 1].sum()) / presentation_total
            if self.snapshot.value_mode == VALUE_MODE_RATE:
                value /= self.data.time_span_seconds(start, end)
            values.append(value)
        return values

    def _selected_timeline(self, unit_idx: int) -> list[float] | None:
        if self.snapshot.selected_cell is None:
            return None
        y_start, y_end, x_start, x_end = self.snapshot.selected_cell
        result: list[float] = []
        for start, end in self.snapshot.time_groups:
            values = [
                self.data.response_value(
                    unit_idx,
                    y_idx,
                    x_idx,
                    start,
                    end,
                    self.snapshot.value_mode,
                )
                for y_idx in range(y_start, y_end + 1)
                for x_idx in range(x_start, x_end + 1)
            ]
            finite = [float(value) for value in values if value is not None]
            result.append(sum(finite) / len(finite) if finite else 0.0)
        return result

    def _timeline_payload(self, unit_idx: int) -> dict[str, object]:
        frames = [
            self._prepare(
                self.data.response_matrix(
                    unit_idx,
                    start,
                    end,
                    self.snapshot.value_mode,
                ),
                polar=self.snapshot.timeline_polar,
            )
            for start, end in self.snapshot.time_groups
        ]
        times = [
            (
                self.data.time_bin_edges[start]
                + self.data.time_bin_edges[end + 1]
            )
            * 500.0
            for start, end in self.snapshot.time_groups
        ]
        group_count = len(self.snapshot.time_groups)
        selection_start = max(
            0,
            min(group_count - 1, int(self.snapshot.timeline_range_start)),
        )
        requested_end = self.snapshot.timeline_range_end
        selection_end = (
            group_count - 1
            if requested_end < 0
            else max(selection_start, min(group_count - 1, int(requested_end)))
        )
        return {
            "times": times,
            "totals": self._all_positions_timeline(unit_idx),
            "selected": self._selected_timeline(unit_idx),
            "frames": frames,
            "selection_start_index": selection_start,
            "selection_end_index": selection_end,
            "active_index": max(
                0,
                min(group_count - 1, int(self.snapshot.timeline_active_bin)),
            ),
        }

    def _hd_payload(self, unit_id: int) -> dict[str, object]:
        tuning = self.data.hd_tuning()
        if tuning is None:
            detail = self.data.hd_tuning_error
            return {
                "unavailable": (
                    f"HD tuning data could not be loaded: {detail}"
                    if detail
                    else "No companion HD tuning JSON was found for this RF dataset."
                )
            }
        try:
            curve = tuning.processed_curve(unit_id)
        except KeyError:
            return {"unavailable": f"HD tuning is unavailable for unit {unit_id}."}
        return {
            "angles_deg": curve.angles_deg.tolist(),
            "rates": curve.rates_hz.tolist(),
        }


class FigureExportWindow(tk.Toplevel):
    """Page-based, multi-unit figure composer with exact live preview."""

    def __init__(self, viewer: RFMViewer):
        super().__init__(viewer)
        self.viewer = viewer
        # The composer is a recipe for one immutable source session.  Never
        # combine its captured provider with indices from a JSON subsequently
        # selected in the still-interactive parent viewer.
        self.data = viewer.data
        self.unit_ids = tuple(int(unit_id) for unit_id in self.data.rf_maps.unit_ids)
        selected_unit_id = int(viewer._selected_unit_id_value())
        self.current_unit_id = (
            selected_unit_id if selected_unit_id in self.unit_ids else self.unit_ids[0]
        )
        self.snapshot = FigureViewerSnapshot.capture(viewer)
        self.data_provider = GUIFigureDataProvider(self.data, self.snapshot)
        self.pages: list[dict[str, object]] = [
            {"name": "Page 1", "plots": [self._current_plot_kind()]}
        ]
        self._preview_photo = None
        self._preview_after: str | None = None
        self._export_busy = False
        self._export_queue: queue.SimpleQueue[tuple[str, object]] = queue.SimpleQueue()
        self.title(f"Export Figures — RF Map Viewer {APP_VERSION}")
        self.geometry("1380x840")
        self.minsize(1050, 680)
        self.transient(viewer)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._populate_units()
        self._refresh_pages(select=0)
        self._refresh_current_plots()
        self._schedule_preview()

    def _current_plot_kind(self) -> PlotKind:
        tab = self.viewer._active_tab_key()
        polar = bool(self.viewer.polar_layout_var.get())
        if tab == "rf":
            return PlotKind.RF_POLAR if polar else PlotKind.RF_CARTESIAN
        if tab == "delay":
            if self.viewer.rgb_mode_var.get():
                return PlotKind.RGB_POLAR if polar else PlotKind.RGB_CARTESIAN
            return PlotKind.DELAY_POLAR if polar else PlotKind.DELAY_CARTESIAN
        return PlotKind.TIMELINE_CURRENT

    def _build(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=0)
        self.rowconfigure(1, weight=1)
        ttk.Label(
            self,
            text="Export Figures",
            font=("TkDefaultFont", 17, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(14, 4))
        ttk.Label(
            self,
            text=(
                "Each selected unit receives every page below. Preview and final "
                "files use the same renderer."
            ),
            foreground="#667085",
        ).grid(row=0, column=1, columnspan=2, sticky="e", padx=16, pady=(14, 4))

        left = ttk.Frame(self, padding=14)
        left.grid(row=1, column=0, sticky="nsew")
        center = ttk.Frame(self, padding=(6, 14))
        center.grid(row=1, column=1, sticky="nsew")
        right = ttk.Frame(self, padding=14)
        right.grid(row=1, column=2, sticky="nsew")
        center.columnconfigure(0, weight=1)
        center.rowconfigure(1, weight=1)

        ttk.Label(left, text="Figure type", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.format_var = tk.StringVar(value="PDF")
        format_combo = ttk.Combobox(
            left,
            state="readonly",
            values=("PDF", "PNG", "SVG"),
            textvariable=self.format_var,
            width=24,
        )
        format_combo.pack(fill="x", pady=(5, 12))
        format_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_format_changed())

        ttk.Label(left, text="Units", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.unit_list = tk.Listbox(left, selectmode="extended", exportselection=False, width=30, height=13)
        self.unit_list.pack(fill="both", expand=True, pady=(5, 5))
        self.unit_list.bind("<<ListboxSelect>>", lambda _event: self._schedule_preview())
        unit_buttons = ttk.Frame(left)
        unit_buttons.pack(fill="x", pady=(0, 12))
        ttk.Button(unit_buttons, text="Current", command=self._select_current_unit).pack(side="left")
        ttk.Button(unit_buttons, text="All", command=self._select_all_units).pack(side="left", padx=5)
        ttk.Button(unit_buttons, text="Clear", command=self._clear_units).pack(side="left")

        ttk.Label(left, text="Pages per unit", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.page_list = tk.Listbox(left, exportselection=False, width=30, height=8)
        self.page_list.pack(fill="both", expand=True, pady=(5, 5))
        self.page_list.bind("<<ListboxSelect>>", lambda _event: self._on_page_selected())
        page_buttons = ttk.Frame(left)
        page_buttons.pack(fill="x")
        ttk.Button(page_buttons, text="+ Page", command=self._add_page).pack(side="left")
        ttk.Button(page_buttons, text="− Page", command=self._remove_page).pack(side="left", padx=5)
        ttk.Button(
            page_buttons,
            text="↑",
            width=3,
            command=lambda: self._move_page(-1),
        ).pack(side="left", padx=(0, 2))
        ttk.Button(
            page_buttons,
            text="↓",
            width=3,
            command=lambda: self._move_page(1),
        ).pack(side="left")

        ttk.Label(center, text="Live preview", font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.preview_label = ttk.Label(center, text="Preparing preview…", anchor="center", relief="solid")
        self.preview_label.grid(row=1, column=0, sticky="nsew", pady=(6, 6))
        self.preview_status = ttk.Label(center, text="", foreground="#667085")
        self.preview_status.grid(row=2, column=0, sticky="w")

        ttk.Label(right, text="Page name", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.page_name_var = tk.StringVar(value="Page 1")
        page_name_entry = ttk.Entry(right, textvariable=self.page_name_var, width=34)
        page_name_entry.pack(fill="x", pady=(5, 12))
        page_name_entry.bind("<Return>", self._rename_page)
        page_name_entry.bind("<FocusOut>", self._rename_page)

        ttk.Label(right, text="Available views", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.available_kinds = [definition.kind for definition in PLOT_KIND_REGISTRY.values()]
        self.available_list = tk.Listbox(right, exportselection=False, width=36, height=11)
        for kind in self.available_kinds:
            self.available_list.insert("end", PLOT_KIND_REGISTRY[kind.value].label)
        self.available_list.selection_set(0)
        self.available_list.pack(fill="both", expand=True, pady=(5, 5))
        ttk.Button(right, text="Add view to page →", command=self._add_plot).pack(fill="x", pady=(0, 12))

        ttk.Label(right, text="Views on current page", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.current_plot_list = tk.Listbox(right, exportselection=False, width=36, height=10)
        self.current_plot_list.pack(fill="both", expand=True, pady=(5, 5))
        plot_buttons = ttk.Frame(right)
        plot_buttons.pack(fill="x")
        ttk.Button(plot_buttons, text="Remove", command=self._remove_plot).pack(side="left")
        ttk.Button(plot_buttons, text="↑", width=3, command=lambda: self._move_plot(-1)).pack(side="left", padx=(5, 2))
        ttk.Button(plot_buttons, text="↓", width=3, command=lambda: self._move_plot(1)).pack(side="left")

        footer = ttk.Frame(self, padding=(16, 8, 16, 14))
        footer.grid(row=2, column=0, columnspan=3, sticky="ew")
        footer.columnconfigure(1, weight=1)
        ttk.Label(footer, text="Destination").grid(row=0, column=0, sticky="w")
        self.destination_var = tk.StringVar(value="")
        ttk.Entry(footer, textvariable=self.destination_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(footer, text="Choose…", command=self._choose_destination).grid(row=0, column=2)
        self.export_button = ttk.Button(footer, text="Export", command=self._start_export)
        self.export_button.grid(row=0, column=3, padx=(12, 0))
        ttk.Button(footer, text="Close", command=self._close).grid(row=0, column=4, padx=(6, 0))
        self.export_status = ttk.Label(footer, text="", foreground="#475467")
        self.export_status.grid(row=1, column=0, columnspan=5, sticky="w", pady=(7, 0))

    def _populate_units(self) -> None:
        self.unit_list.delete(0, "end")
        for rf_map in self.data.rf_maps:
            self.unit_list.insert("end", f"index {rf_map.unit_index:03d}  ·  unit {rf_map.unit_id}")
        self._select_current_unit()

    def _select_current_unit(self) -> None:
        self.unit_list.selection_clear(0, "end")
        try:
            index = self.unit_ids.index(self.current_unit_id)
        except ValueError:
            index = None
        if index is not None:
            self.unit_list.selection_set(index)
            self.unit_list.see(index)
        self._schedule_preview()

    def _select_all_units(self) -> None:
        self.unit_list.selection_set(0, "end")
        self._schedule_preview()

    def _clear_units(self) -> None:
        self.unit_list.selection_clear(0, "end")
        self._schedule_preview()

    def _selected_unit_ids(self) -> tuple[int, ...]:
        return tuple(
            self.unit_ids[index]
            for index in self.unit_list.curselection()
        )

    def _selected_page_index(self) -> int:
        selection = self.page_list.curselection()
        return int(selection[0]) if selection else 0

    def _refresh_pages(self, *, select: int | None = None) -> None:
        current = self._selected_page_index() if select is None else select
        self.page_list.delete(0, "end")
        for index, page in enumerate(self.pages):
            plots = page["plots"]
            self.page_list.insert("end", f"{index + 1}. {page['name']}  ({len(plots)} views)")
        current = max(0, min(len(self.pages) - 1, current))
        self.page_list.selection_set(current)
        self.page_list.see(current)
        self.page_name_var.set(str(self.pages[current]["name"]))

    def _on_page_selected(self) -> None:
        index = self._selected_page_index()
        self.page_name_var.set(str(self.pages[index]["name"]))
        self._refresh_current_plots()
        self._schedule_preview()

    def _rename_page(self, _event=None) -> None:
        index = self._selected_page_index()
        name = self.page_name_var.get().strip()
        if not name:
            self.page_name_var.set(str(self.pages[index]["name"]))
            return
        self.pages[index]["name"] = name
        self._refresh_pages(select=index)
        self._schedule_preview()

    def _add_page(self) -> None:
        self.pages.append({"name": f"Page {len(self.pages) + 1}", "plots": []})
        self._refresh_pages(select=len(self.pages) - 1)
        self._refresh_current_plots()
        self._schedule_preview()

    def _remove_page(self) -> None:
        if len(self.pages) <= 1:
            messagebox.showinfo("Keep one page", "Each unit must have at least one page.", parent=self)
            return
        index = self._selected_page_index()
        self.pages.pop(index)
        self._refresh_pages(select=max(0, index - 1))
        self._refresh_current_plots()
        self._schedule_preview()

    def _move_page(self, delta: int) -> None:
        index = self._selected_page_index()
        target = index + delta
        if not 0 <= target < len(self.pages):
            return
        self.pages[index], self.pages[target] = self.pages[target], self.pages[index]
        self._refresh_pages(select=target)
        self._refresh_current_plots()
        self._schedule_preview()

    def _current_plot_kinds(self) -> list[PlotKind]:
        return self.pages[self._selected_page_index()]["plots"]  # type: ignore[return-value]

    def _refresh_current_plots(self, *, select: int | None = None) -> None:
        plots = self._current_plot_kinds()
        self.current_plot_list.delete(0, "end")
        for kind in plots:
            self.current_plot_list.insert("end", PLOT_KIND_REGISTRY[kind.value].label)
        if plots and select is not None:
            index = max(0, min(len(plots) - 1, select))
            self.current_plot_list.selection_set(index)
        self._refresh_pages(select=self._selected_page_index())

    def _add_plot(self) -> None:
        selection = self.available_list.curselection()
        if not selection:
            return
        plots = self._current_plot_kinds()
        plots.append(self.available_kinds[int(selection[0])])
        self._refresh_current_plots(select=len(plots) - 1)
        self._schedule_preview()

    def _remove_plot(self) -> None:
        selection = self.current_plot_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        plots = self._current_plot_kinds()
        plots.pop(index)
        self._refresh_current_plots(select=max(0, index - 1))
        self._schedule_preview()

    def _move_plot(self, delta: int) -> None:
        selection = self.current_plot_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        target = index + delta
        plots = self._current_plot_kinds()
        if not 0 <= target < len(plots):
            return
        plots[index], plots[target] = plots[target], plots[index]
        self._refresh_current_plots(select=target)
        self._schedule_preview()

    def _export_pages(self) -> tuple[ExportPage, ...]:
        pages: list[ExportPage] = []
        for index, page in enumerate(self.pages):
            kinds: list[PlotKind] = page["plots"]  # type: ignore[assignment]
            if not kinds:
                raise ValueError(f"Page {index + 1} ({page['name']}) has no views.")
            pages.append(
                ExportPage(
                    str(page["name"]),
                    tuple(PlotSpec(kind) for kind in kinds),
                )
            )
        return tuple(pages)

    def _preview_plan(self) -> ExportPlan:
        unit_ids = self._selected_unit_ids()
        if not unit_ids:
            unit_ids = (self.current_unit_id,)
        return ExportPlan(
            FigureFormat.PDF,
            unit_ids,
            self._export_pages(),
            Path("/tmp/rfmap-live-preview.pdf"),
        )

    def _schedule_preview(self) -> None:
        if self._preview_after is not None:
            try:
                self.after_cancel(self._preview_after)
            except tk.TclError:
                pass
        self._preview_after = self.after(80, self._render_preview)

    def _render_preview(self) -> None:
        self._preview_after = None
        try:
            plan = self._preview_plan()
            unit_id = plan.unit_ids[0]
            page_index = self._selected_page_index()
            image = render_live_preview(
                plan,
                unit_id,
                page_index,
                data_provider=self.data_provider,
            )
            available_width = max(480, self.preview_label.winfo_width() - 20)
            available_height = max(360, self.preview_label.winfo_height() - 20)
            image.thumbnail((available_width, available_height))
            from PIL import ImageTk

            self._preview_photo = ImageTk.PhotoImage(image)
            self.preview_label.configure(image=self._preview_photo, text="")
            self.preview_status.configure(
                text=f"Preview: unit {unit_id}, page {page_index + 1} · exact final renderer"
            )
        except Exception as exc:
            self._preview_photo = None
            self.preview_label.configure(image="", text=f"Preview unavailable\n{exc}")
            self.preview_status.configure(text="Fix the page or unit selection to continue.")

    def _on_format_changed(self) -> None:
        self.destination_var.set("")

    def _default_base_name(self) -> str:
        stem = self.data.path.stem
        return f"{stem}_figures"

    def _choose_destination(self) -> None:
        figure_format = FigureFormat.coerce(self.format_var.get())
        initial_dir = self.data.path.parent
        if figure_format is FigureFormat.PDF:
            path = filedialog.asksaveasfilename(
                parent=self,
                title="Export multi-page PDF",
                initialdir=initial_dir,
                initialfile=f"{self._default_base_name()}.pdf",
                defaultextension=".pdf",
                filetypes=(("PDF document", "*.pdf"),),
            )
            if path:
                self.destination_var.set(path)
            return
        parent = filedialog.askdirectory(
            parent=self,
            title=f"Choose parent folder for {figure_format.value.upper()} pages",
            initialdir=initial_dir,
            mustexist=True,
        )
        if parent:
            self.destination_var.set(str(Path(parent) / self._default_base_name()))

    def _start_export(self) -> None:
        if self._export_busy:
            return
        unit_ids = self._selected_unit_ids()
        if not unit_ids:
            messagebox.showerror("No units", "Select at least one unit to export.", parent=self)
            return
        destination_text = self.destination_var.get().strip()
        if not destination_text:
            self._choose_destination()
            destination_text = self.destination_var.get().strip()
            if not destination_text:
                return
        try:
            figure_format = FigureFormat.coerce(self.format_var.get())
            destination = Path(destination_text).expanduser()
            plan = ExportPlan(figure_format, unit_ids, self._export_pages(), destination)
        except Exception as exc:
            messagebox.showerror("Invalid export", str(exc), parent=self)
            return

        overwrite = False
        if destination.exists():
            if figure_format is not FigureFormat.PDF:
                messagebox.showerror(
                    "Choose a new folder",
                    "PNG/SVG export never replaces an existing directory. Choose a new output folder name.",
                    parent=self,
                )
                return
            overwrite = messagebox.askyesno(
                "Replace PDF?",
                f"{destination} already exists. Replace this file?",
                parent=self,
            )
            if not overwrite:
                return

        self._export_busy = True
        self.export_button.state(["disabled"])
        page_count = len(plan.unit_ids) * len(plan.pages)
        self.export_status.configure(text=f"Exporting {page_count} pages…")

        def worker() -> None:
            try:
                result = export_figures(
                    plan,
                    data_provider=self.data_provider,
                    overwrite=overwrite,
                )
            except Exception as exc:
                self._export_queue.put(("error", str(exc)))
            else:
                self._export_queue.put(("result", result))

        threading.Thread(target=worker, name="rfmap-figure-export", daemon=True).start()
        self.after(50, self._poll_export)

    def _poll_export(self) -> None:
        try:
            outcome, payload = self._export_queue.get_nowait()
        except queue.Empty:
            if self._export_busy:
                self.after(50, self._poll_export)
            return
        if outcome == "error":
            self._finish_export(error=str(payload))
        else:
            self._finish_export(result=payload)

    def _finish_export(self, *, result=None, error: str | None = None) -> None:
        self._export_busy = False
        self.export_button.state(["!disabled"])
        if error is not None:
            self.export_status.configure(text="Export failed.")
            messagebox.showerror("Export failed", error, parent=self)
            return
        self.export_status.configure(
            text=f"Exported {result.page_count} pages to {result.destination}"
        )
        messagebox.showinfo(
            "Export complete",
            f"Exported {result.page_count} pages to\n{result.destination}",
            parent=self,
        )

    def _close(self) -> None:
        if self._export_busy:
            messagebox.showinfo(
                "Export is running",
                "Wait for the export to finish before closing the composer.",
                parent=self,
            )
            return
        self.viewer.__dict__.pop("_figure_export_window", None)
        self.destroy()


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
    test_range_end = min(4, data.n_bins - 1)
    range_sum = data.aggregate_matrix(unit_idx, "Range sum", 0, 0, test_range_end)
    assert total[y_idx][x_idx] == sum(hist)
    assert peak[y_idx][x_idx] == (max(hist) if hist else 0.0)
    assert one_bin[y_idx][x_idx] == hist[0]
    assert range_sum[y_idx][x_idx] == sum(hist[: test_range_end + 1])
    count_response = data.response_matrix(unit_idx, 0, test_range_end, VALUE_MODE_COUNT)
    assert count_response[y_idx][x_idx] == range_sum[y_idx][x_idx]
    if data.presentation_counts is not None:
        presentations = data.presentation_counts[y_idx][x_idx]
        if presentations > 0:
            expected_rate = sum(hist[: test_range_end + 1]) / (
                presentations * (data.time_bin_edges[test_range_end + 1] - data.time_bin_edges[0])
            )
            firing_rate = data.response_value(
                unit_idx, y_idx, x_idx, 0, test_range_end, VALUE_MODE_RATE
            )
            assert firing_rate is not None
            assert abs(firing_rate - expected_rate) < 1e-9
    assert 0.0 <= metrics.entropy[y_idx][x_idx] <= 1.0
    inferred_total_deg = data.infer_total_deg()
    assert math.isfinite(inferred_total_deg) and inferred_total_deg > 0
    print(
        "self-test passed:",
        f"{data.n_units} units, {data.n_y} y, {data.n_x} x, {data.n_bins} bins",
        f"rate metadata: {'yes' if data.presentation_counts is not None else 'no'}",
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
    if args.json_path is not None:
        path = Path(args.json_path).expanduser()
        if not path.exists():
            print(f"JSON file not found: {path}", file=sys.stderr)
            return 2
    else:
        path = startup_json_path()
    if args.self_test and not path.exists():
        print(f"JSON file not found: {path}", file=sys.stderr)
        return 2
    if args.self_test:
        run_self_test(path)
        return 0

    if not TK_AVAILABLE:
        print("tkinter is not available in this Python; use a local Python with Tk to launch the GUI.", file=sys.stderr)
        return 1

    app = RFMViewer(startup_path=path)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
