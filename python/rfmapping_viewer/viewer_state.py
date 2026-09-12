"""Typed values passed between viewer windows and background workers."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from dataclasses import dataclass, replace

from rfmapping_viewer.constants import DEFAULT_HD_DISPLAY_BINS, DEFAULT_HD_SMOOTH_SIGMA


@dataclass(frozen=True)
class WaveformLoadResult:
    generation: int
    data_path: Path
    key: tuple[int, str]
    payload: Mapping[str, object] | None
    error: str | None


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
    tuning_plot_mode: str = "Auto"
    tuning_display_bins: int = DEFAULT_HD_DISPLAY_BINS
    tuning_smoothing: bool = True
    tuning_smooth_sigma: float = DEFAULT_HD_SMOOTH_SIGMA
    tuning_compare_scale: bool = False
    show_tuning_curve: bool = True
    show_waveform: bool = True
    show_probe_layout: bool = True

    rf_subtract: bool = False
    rf_subtract_start_ms: float = 0.0
    rf_subtract_end_ms: float = 80.0

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
        if (
            self.rf_start_ms != baseline.rf_start_ms
            or self.rf_end_ms != baseline.rf_end_ms
            or self.rf_subtract != baseline.rf_subtract
            or self.rf_subtract_start_ms != baseline.rf_subtract_start_ms
            or self.rf_subtract_end_ms != baseline.rf_subtract_end_ms
        ):
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
        if (
            self.tuning_plot_mode != baseline.tuning_plot_mode
            or self.tuning_display_bins != baseline.tuning_display_bins
            or self.tuning_smoothing != baseline.tuning_smoothing
            or self.tuning_smooth_sigma != baseline.tuning_smooth_sigma
            or self.tuning_compare_scale != baseline.tuning_compare_scale
        ):
            fields.add("tuning_display")
        if (
            self.show_tuning_curve != baseline.show_tuning_curve
            or self.show_waveform != baseline.show_waveform
            or self.show_probe_layout != baseline.show_probe_layout
        ):
            fields.add("optional_views")
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
            updates.update(
                rf_start_ms=incoming.rf_start_ms,
                rf_end_ms=incoming.rf_end_ms,
                rf_subtract=incoming.rf_subtract,
                rf_subtract_start_ms=incoming.rf_subtract_start_ms,
                rf_subtract_end_ms=incoming.rf_subtract_end_ms,
            )
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
        if "tuning_display" in fields:
            updates.update(
                tuning_plot_mode=incoming.tuning_plot_mode,
                tuning_display_bins=incoming.tuning_display_bins,
                tuning_smoothing=incoming.tuning_smoothing,
                tuning_smooth_sigma=incoming.tuning_smooth_sigma,
                tuning_compare_scale=incoming.tuning_compare_scale,
            )
        if "optional_views" in fields:
            updates.update(
                show_tuning_curve=incoming.show_tuning_curve,
                show_waveform=incoming.show_waveform,
                show_probe_layout=incoming.show_probe_layout,
            )
        return replace(self, **updates)
