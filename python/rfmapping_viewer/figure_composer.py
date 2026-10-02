"""Immutable figure snapshots, plot data, and the composer window."""

from __future__ import annotations

import json
import math
import queue
import threading
from collections.abc import Mapping
from concurrent.futures import Future
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable
from rfmapping_viewer.figure_export import (
    ExportPage,
    ExportPlan,
    FigureFormat,
    PLOT_KIND_REGISTRY,
    PlotKind,
    PlotSpec,
    export_figures,
    shared_scalar_scale,
    render_live_preview,
)

from rfmapping_viewer.companions import TuningCurveData
from rfmapping_viewer.constants import (
    APP_EDITION,
    APP_VERSION,
    AxisGroup,
    CellRef,
    DEFAULT_HD_DISPLAY_BINS,
    DEFAULT_HD_SMOOTH_SIGMA,
    DEFAULT_TUNING_CURVE_SESSION,
    INNER_BLANK_ROWS,
    POLAR_RADIUS_MODES,
    VALUE_MODE_RATE,
)
from rfmapping_viewer.display import (
    _nullable_array_list,
    format_ms,
    palette_response_range,
    reduce_matrix_xy,
    rgb_response_color,
    smooth_matrix,
    subtract_response_matrices,
    value_mode_unit,
)
from rfmapping_viewer.export_inputs import (
    _export_executor,
    _hash_frozen_file,
    _register_export_job,
    _submit_daemon_future,
    _unregister_export_job,
)
from rfmapping_viewer.figure_layout import (
    FRAME_PRESETS, arrange_frames, automatic_frames, first_available_frame,
)
from rfmapping_viewer.figure_workspace import FigurePageCanvas
from rfmapping_viewer.figure_recipe import make_figure_layout, parse_figure_layout
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.settings import normalize_hd_bin_count
from rfmapping_viewer.tk_support import filedialog, messagebox, tk, ttk

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rfmapping_gui import RFMViewer


def composer_unit_selection_after_click(
    selected_indices: Iterable[int],
    clicked_index: int,
    anchor_index: int | None,
    unit_count: int,
    *,
    command: bool = False,
    shift: bool = False,
) -> tuple[tuple[int, ...], int]:
    """Apply Finder-style row selection and return sorted source indices.

    A plain click replaces the selection, Command-click toggles one row while
    retaining the others, and Shift-click replaces the selection with the
    inclusive range from the stable anchor. Command-Shift-click adds that range.
    Sorting by source index is intentional: downstream exports must always
    follow the JSON ``unitPool`` order, never the order in which rows were
    clicked.
    """

    if unit_count < 0:
        raise ValueError("unit_count must be non-negative")
    if not 0 <= clicked_index < unit_count:
        raise IndexError(f"unit index {clicked_index} is outside 0..{unit_count - 1}")

    selected = {
        int(index)
        for index in selected_indices
        if 0 <= int(index) < unit_count
    }
    valid_anchor = (
        int(anchor_index)
        if anchor_index is not None and 0 <= int(anchor_index) < unit_count
        else None
    )

    if shift:
        anchor = clicked_index if valid_anchor is None else valid_anchor
        first, last = sorted((anchor, clicked_index))
        clicked_range = set(range(first, last + 1))
        selected = selected | clicked_range if command else clicked_range
        return tuple(sorted(selected)), anchor

    if command:
        if clicked_index in selected:
            selected.remove(clicked_index)
        else:
            selected.add(clicked_index)
        return tuple(sorted(selected)), clicked_index

    return (clicked_index,), clicked_index


def composer_unit_checkbox_hit(
    event_x: int,
    row_text_x: int,
    checkbox_hit_width: int,
) -> bool:
    """Return whether a row click landed in its leading checkbox column."""

    if checkbox_hit_width <= 0:
        raise ValueError("checkbox_hit_width must be positive")
    return row_text_x <= event_x < row_text_x + checkbox_hit_width


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
    hd_display_bins: int = DEFAULT_HD_DISPLAY_BINS
    hd_smoothing: bool = True
    hd_smooth_sigma: float = DEFAULT_HD_SMOOTH_SIGMA
    tuning_curve_session: int = DEFAULT_TUNING_CURVE_SESSION
    unit_filter_enabled: bool = False
    zero_bin_threshold: int = 1
    visible_unit_ids: tuple[int, ...] | None = None
    waveform_channel_mode: str = "same_x_column"

    rf_subtract_source_range: AxisGroup | None = None

    @classmethod
    def capture(cls, viewer: RFMViewer) -> FigureViewerSnapshot:
        source_start, source_end = viewer._source_bins_for_time_controls()
        timeline_range_start, timeline_range_end = viewer._display_range_indices()
        return cls(
            value_mode=viewer.value_mode_var.get(),
            rf_source_start=source_start,
            rf_source_end=source_end,
            rf_subtract_source_range=viewer._rf_subtraction_range(),
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
            hd_display_bins=normalize_hd_bin_count(
                viewer.tuning_display_bins_var.get()
            ),
            hd_smoothing=bool(viewer.tuning_smoothing_var.get()),
            hd_smooth_sigma=float(viewer.tuning_smooth_sigma_var.get()),
            tuning_curve_session=int(viewer.settings.tuning_curve_session),
            unit_filter_enabled=bool(
                viewer.settings.rf_filter_units_with_zero_bins
            ),
            zero_bin_threshold=int(viewer.settings.rf_zero_bin_threshold),
            visible_unit_ids=tuple(viewer._local_quality_visible_unit_ids()),
            waveform_channel_mode=viewer.waveform_channel_mode_var.get(),
        )


class GUIFigureDataProvider:
    """Prepare every registered figure without mutating the live viewer."""

    def __init__(
        self,
        data: RFMappingData,
        snapshot: FigureViewerSnapshot,
        *,
        shared_rf_scale: tuple[float, float] | None = None,
        shared_waveform_limit: float | None = None,
        response_cache: dict[
            tuple[object, ...], list[list[float | None]]
        ]
        | None = None,
        response_cache_lock: threading.Lock | None = None,
    ):
        self.data = data
        self.snapshot = snapshot
        self.shared_rf_scale = shared_rf_scale
        self.shared_waveform_limit = shared_waveform_limit
        self._response_cache = response_cache if response_cache is not None else {}
        self._response_cache_lock = (
            response_cache_lock
            if response_cache_lock is not None
            else threading.Lock()
        )
        self._temporal_cache: dict[
            tuple[int, bool],
            tuple[list[list[float | None]], list[list[float | None]]],
        ] = {}
        self._temporal_cache_lock = threading.Lock()
        # Capture companion geometry with the same source-session object used
        # for every other plot.  A non-modal composer must not start reading a
        # different CSV after the parent viewer switches JSON documents.
        self.probe_geometry = data.probe_geometry()
        self.probe_geometry_error = data.probe_geometry_error
        self.hd_tuning = data.hd_tuning(snapshot.tuning_curve_session)
        self.hd_tuning_error = data.hd_tuning_error
        self.waveform_store = data.waveform_store()
        self.waveform_error = data.waveform_error

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
            bounds = self.shared_rf_scale
            if options.get("normalize_per_unit", False):
                bounds = palette_response_range(payload, self.snapshot.palette)
                options["vmin"], options["vmax"] = bounds
            if self.snapshot.rf_subtract_source_range is not None:
                options.setdefault("missing_color", "#e6e8eb")
                edges = self.data.time_bin_edges
                a_start, a_end = self.snapshot.rf_source_start, self.snapshot.rf_source_end
                b_start, b_end = self.snapshot.rf_subtract_source_range
                expression = (
                    f"({format_ms(edges[a_start] * 1000)}–{format_ms(edges[a_end + 1] * 1000)} ms) − "
                    f"({format_ms(edges[b_start] * 1000)}–{format_ms(edges[b_end + 1] * 1000)} ms)"
                )
                subtitle = str(options.get("subtitle", "")).strip()
                options["subtitle"] = f"{expression} · {subtitle}" if subtitle else expression
            if bounds is not None:
                options.setdefault("vmin", bounds[0])
                options.setdefault("vmax", bounds[1])
            options.setdefault("value_unit", value_mode_unit(self.snapshot.value_mode))
            options.setdefault("show_colorbar", True)
        elif kind in {PlotKind.DELAY_CARTESIAN, PlotKind.DELAY_POLAR}:
            options["palette"] = "delay"
            options["vmin"] = self.data.time_bin_edges[0] * 1000.0
            options["vmax"] = self.data.time_bin_edges[-1] * 1000.0
            payload = self._delay_matrix(unit_idx, polar=kind is PlotKind.DELAY_POLAR)
        elif kind in {PlotKind.RGB_CARTESIAN, PlotKind.RGB_POLAR}:
            payload = self._rgb_matrix(unit_idx, polar=kind is PlotKind.RGB_POLAR)
            options.setdefault("rgb_bytes", True)
            options.setdefault("missing_color", "#e6e8eb")
            options.setdefault("hatch_missing", True)
        elif kind is PlotKind.TIMELINE_CURRENT:
            options["polar"] = self.snapshot.timeline_polar
            payload = self._timeline_payload(unit_idx)
        elif kind in {PlotKind.HD_LINE, PlotKind.HD_POLAR}:
            payload = self._hd_payload(unit_id)
        elif kind is PlotKind.PROBE_LAYOUT:
            options.setdefault("coordinate_unit", "µm")
            payload = self._probe_payload(unit_id)
            if self.probe_geometry is not None:
                template = replace(
                    template,
                    title=f"{self.probe_geometry.probe_name} layout",
                )
        elif kind is PlotKind.WAVEFORM_LOCAL_AVERAGE:
            payload = self._waveform_payload(unit_id)
            options["palette"] = "rdbu_r"
            options["value_unit"] = "µV"
            options["show_colorbar"] = True
            if "unavailable" not in payload:
                local_limit = float(payload["amplitude_limit_uv"])
                limit = (
                    self.shared_waveform_limit
                    if self.shared_waveform_limit is not None
                    and not options.get("normalize_per_unit", False)
                    else local_limit
                )
                options["vmin"] = -abs(float(limit))
                options["vmax"] = abs(float(limit))
        else:
            payload = {"unavailable": f"Unsupported figure kind: {kind.value}"}
        if kind in {PlotKind.RF_CARTESIAN, PlotKind.DELAY_CARTESIAN, PlotKind.RGB_CARTESIAN}:
            options.setdefault(
                "x_values",
                [
                    (self.data.x_positions[start] + self.data.x_positions[end]) / 2.0
                    for start, end in self.snapshot.x_groups
                ],
            )
            options.setdefault(
                "y_values",
                [
                    (self.data.y_positions[start] + self.data.y_positions[end]) / 2.0
                    for start, end in self.snapshot.y_groups
                ],
            )
            options.setdefault("x_unit", "°")
            options.setdefault("y_unit", "°")
            options.setdefault("show_axes", True)
        if kind in {PlotKind.DELAY_CARTESIAN, PlotKind.DELAY_POLAR}:
            options.setdefault("value_unit", "ms")
            options.setdefault("show_colorbar", True)
        return replace(template, data=payload, options=options)

    def shared_rf_bounds(
        self,
        unit_ids: Iterable[int],
        cancelled: Callable[[], bool] | None = None,
    ) -> tuple[float, float]:
        matrices = []
        for unit_id in unit_ids:
            if cancelled is not None and cancelled():
                raise RuntimeError("Preview superseded by a newer recipe")
            unit_idx = self.data.rf_map_by_unit_id(int(unit_id)).unit_index
            matrices.append(self._rf_matrix(unit_idx, polar=False))
        bounds = shared_scalar_scale(matrices)
        low, high = float(bounds["vmin"]), float(bounds["vmax"])
        # Colored RF plots use a zero baseline on screen as well as in exports.
        if self.snapshot.palette != "Gray":
            low = 0.0
            if high <= 0.0:
                high = 1.0
        return low, high

    def shared_waveform_amplitude_limit(
        self,
        unit_ids: Iterable[int],
        cancelled: Callable[[], bool] | None = None,
    ) -> float | None:
        limit = 0.0
        found = False
        for unit_id in unit_ids:
            if cancelled is not None and cancelled():
                raise RuntimeError("Preview superseded by a newer recipe")
            try:
                payload = self.data.waveform_payload(
                    int(unit_id), self.snapshot.waveform_channel_mode
                )
            except (OSError, ValueError):
                continue
            limit = max(limit, float(payload.amplitude_limit_uv))
            found = True
        return limit if found else None

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
        matrix = self._grouped_response_matrix(
            unit_idx,
            self.snapshot.rf_source_start,
            self.snapshot.rf_source_end,
            polar=polar,
        )
        if self.snapshot.rf_subtract_source_range is not None:
            baseline = self._grouped_response_matrix(
                unit_idx, *self.snapshot.rf_subtract_source_range, polar=polar,
            )
            matrix = subtract_response_matrices(matrix, baseline)
        return matrix

    def _polarize_grouped(
        self,
        matrix: list[list[float | None]],
        *,
        polar: bool,
    ) -> list[list[float | None]]:
        if not polar:
            return matrix
        if self.snapshot.polar_radius == POLAR_RADIUS_MODES[0]:
            ring_rows = sorted(
                range(len(self.snapshot.y_groups)),
                key=lambda index: self.snapshot.y_groups[index][0],
            )
        else:
            ring_rows = list(range(len(matrix) - 1, -1, -1))
        return [matrix[index] for index in ring_rows]

    def _grouped_response_matrix(
        self,
        unit_idx: int,
        source_start: int,
        source_end: int,
        *,
        polar: bool,
    ) -> list[list[float | None]]:
        """Pool count/exposure observations before spatial smoothing."""

        normalized = self.data._normalized_time_groups(
            [(source_start, source_end)]
        )[0]
        key = (
            int(unit_idx),
            normalized[0],
            normalized[1],
            self.snapshot.value_mode,
            tuple(self.snapshot.y_groups),
            tuple(self.snapshot.x_groups),
            int(self.snapshot.smooth_radius),
            bool(polar),
            self.snapshot.polar_radius if polar else None,
        )
        with self._response_cache_lock:
            cached = self._response_cache.get(key)
        if cached is not None:
            return cached

        if polar:
            matrix = self._polarize_grouped(
                self._grouped_response_matrix(
                    unit_idx,
                    normalized[0],
                    normalized[1],
                    polar=False,
                ),
                polar=True,
            )
        else:
            frames = self.data.spatial_group_response_frames(
                unit_idx,
                [normalized],
                self.snapshot.value_mode,
                self.snapshot.y_groups,
                self.snapshot.x_groups,
                smooth_radius=self.snapshot.smooth_radius,
            )
            matrix = _nullable_array_list(frames[0])
        with self._response_cache_lock:
            existing = self._response_cache.setdefault(key, matrix)
        return existing

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
        delay, _entropy = self._grouped_temporal_matrices(unit_idx, polar=polar)
        return delay

    def _grouped_temporal_matrices(
        self,
        unit_idx: int,
        *,
        polar: bool,
    ) -> tuple[list[list[float | None]], list[list[float | None]]]:
        key = (int(unit_idx), bool(polar))
        with self._temporal_cache_lock:
            cached = self._temporal_cache.get(key)
        if cached is not None:
            return cached
        if polar:
            delay, entropy = self._grouped_temporal_matrices(
                unit_idx,
                polar=False,
            )
            result = (
                self._polarize_grouped(delay, polar=True),
                self._polarize_grouped(entropy, polar=True),
            )
        else:
            delay_values, entropy_values = self.data.spatial_group_temporal_arrays(
                unit_idx,
                self.snapshot.y_groups,
                self.snapshot.x_groups,
                self.snapshot.time_groups,
                smooth_radius=self.snapshot.smooth_radius,
            )
            result = (
                _nullable_array_list(delay_values),
                _nullable_array_list(entropy_values),
            )
        with self._temporal_cache_lock:
            return self._temporal_cache.setdefault(key, result)

    def _rgb_matrix(self, unit_idx: int, *, polar: bool) -> list[list[tuple[int, int, int] | None]]:
        response = self._grouped_response_matrix(
            unit_idx,
            0,
            self.data.n_bins - 1,
            polar=polar,
        )
        delay, entropy = self._grouped_temporal_matrices(unit_idx, polar=polar)
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
        return [
            [
                rgb_response_color(
                    value, delay[y_idx][x_idx], entropy[y_idx][x_idx],
                    max_response, delay_start, delay_span,
                )
                for x_idx, value in enumerate(row)
            ]
            for y_idx, row in enumerate(response)
        ]

    def _all_positions_timeline(self, unit_idx: int) -> list[float]:
        return self.data.all_positions_timeline_values(
            unit_idx,
            self.snapshot.time_groups,
            self.snapshot.value_mode,
        )

    def _selected_timeline(self, unit_idx: int) -> list[float] | None:
        if self.snapshot.selected_cell is None:
            return None
        y_start, y_end, x_start, x_end = self.snapshot.selected_cell
        values = self.data.spatial_group_response_values(
            unit_idx,
            (y_start, y_end),
            (x_start, x_end),
            self.snapshot.time_groups,
            self.snapshot.value_mode,
        )
        return [float(value) if value is not None else 0.0 for value in values]

    def _timeline_payload(self, unit_idx: int) -> dict[str, object]:
        frame_values = self.data.spatial_group_response_frames(
            unit_idx,
            self.snapshot.time_groups,
            self.snapshot.value_mode,
            self.snapshot.y_groups,
            self.snapshot.x_groups,
            smooth_radius=self.snapshot.smooth_radius,
        )
        if self.snapshot.timeline_polar:
            if self.snapshot.polar_radius == POLAR_RADIUS_MODES[0]:
                ring_rows = sorted(
                    range(len(self.snapshot.y_groups)),
                    key=lambda index: self.snapshot.y_groups[index][0],
                )
            else:
                ring_rows = list(range(len(self.snapshot.y_groups) - 1, -1, -1))
            frame_values = frame_values[:, ring_rows, :]
        frames = _nullable_array_list(frame_values)
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
        time_edges = [
            self.data.time_bin_edges[start] * 1000.0
            for start, _end in self.snapshot.time_groups
        ]
        if self.snapshot.time_groups:
            time_edges.append(
                self.data.time_bin_edges[self.snapshot.time_groups[-1][1] + 1] * 1000.0
            )
        return {
            "times": times,
            "time_edges": time_edges,
            "time_unit": "ms",
            "value_unit": value_mode_unit(self.snapshot.value_mode),
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
        tuning = self.hd_tuning
        if tuning is None:
            detail = self.hd_tuning_error
            return {
                "unavailable": (
                    f"HD tuning data could not be loaded: {detail}"
                    if detail
                    else "No companion HD tuning JSON was found for this RF dataset."
                )
            }
        if isinstance(tuning, TuningCurveData):
            processed = tuning.processed_for(
                unit_id,
                self.snapshot.hd_display_bins,
                smoothing=self.snapshot.hd_smoothing,
                sigma=self.snapshot.hd_smooth_sigma,
            )
            if processed is None:
                return {"unavailable": f"HD tuning is unavailable for unit {unit_id}."}
            angles, rates = processed
            return {"angles_deg": list(angles), "rates": list(rates)}
        try:
            curve = tuning.processed_curve(
                unit_id,
                display_bins=self.snapshot.hd_display_bins,
                smoothing=self.snapshot.hd_smoothing,
                sigma=self.snapshot.hd_smooth_sigma,
            )
        except KeyError:
            return {"unavailable": f"HD tuning is unavailable for unit {unit_id}."}
        return {
            "angles_deg": curve.angles_deg.tolist(),
            "rates": curve.rates_hz.tolist(),
        }

    def _probe_payload(self, unit_id: int) -> dict[str, object]:
        geometry = self.probe_geometry
        if geometry is None:
            detail = self.probe_geometry_error
            return {
                "unavailable": (
                    f"Probe geometry could not be loaded: {detail}"
                    if detail
                    else "No companion positions.csv was found for this RF dataset."
                )
            }
        selected_unit = next(
            (unit for unit in geometry.units if unit.unit_id == unit_id),
            None,
        )
        if selected_unit is None:
            return {
                "unavailable": (
                    f"Probe position is unavailable for RF unit {unit_id}; "
                    "the selected unit is absent from positions.csv."
                )
            }
        missing_position = (
            selected_unit.x_um is None and selected_unit.y_um is None
        )
        points: list[dict[str, object]] = [
            {
                "x": channel.x_um,
                "y": channel.y_um,
                "label": "",
                "color": "#94a3b8",
            }
            for channel in geometry.channels
        ]
        # A Probe plot belongs to one output page and therefore one unit. Keep
        # physical channels as spatial context, but never leak markers for the
        # other selected/exported units onto this page.
        if not missing_position:
            if selected_unit.x_um is None or selected_unit.y_um is None:
                raise ValueError(
                    f"Probe position for unit {unit_id} is incomplete"
                )
            points.append(
                {
                    "x": selected_unit.x_um,
                    "y": selected_unit.y_um,
                    "label": str(selected_unit.unit_id),
                    "color": "#dc2626",
                }
            )
        if not points and not missing_position:
            return {"unavailable": "Probe geometry contains no channels or units."}
        return {
            "points": points,
            **({"missingPosition": True} if missing_position else {}),
        }

    def _waveform_payload(self, unit_id: int) -> dict[str, object]:
        if self.waveform_store is None:
            detail = self.waveform_error
            return {
                "unavailable": (
                    f"Waveform artifact could not be loaded: {detail}"
                    if detail
                    else "No companion waveform artifact was found for this RF dataset."
                )
            }
        try:
            return self.data.waveform_plot_payload(
                int(unit_id), self.snapshot.waveform_channel_mode
            )
        except (OSError, ValueError) as exc:
            return {"unavailable": str(exc)}


def _figure_snapshot_metadata(data: RFMappingData, snapshot: FigureViewerSnapshot) -> dict[str, object]:
    visible_unit_ids = (
        tuple(int(unit_id) for unit_id in data.unit_pool)
        if snapshot.visible_unit_ids is None
        else snapshot.visible_unit_ids
    )
    return {
        "valueMode": snapshot.value_mode,
        "valueUnit": value_mode_unit(snapshot.value_mode),
        "rfSourceBins": [snapshot.rf_source_start, snapshot.rf_source_end],
        "rfWindowOperation": "A - B" if snapshot.rf_subtract_source_range is not None else "sum",
        "rfNegativeDifference": "NaN",
        "rfSubtractSourceBins": (
            list(snapshot.rf_subtract_source_range)
            if snapshot.rf_subtract_source_range is not None else None
        ),
        "rfSubtractTimeRangeMs": (
            [data.time_bin_edges[snapshot.rf_subtract_source_range[0]] * 1000.0,
             data.time_bin_edges[snapshot.rf_subtract_source_range[1] + 1] * 1000.0]
            if snapshot.rf_subtract_source_range is not None else None
        ),
        "rfTimeRangeMs": [
            data.time_bin_edges[snapshot.rf_source_start] * 1000.0,
            data.time_bin_edges[snapshot.rf_source_end + 1] * 1000.0,
        ],
        "timeBinEdgesMs": [edge * 1000.0 for edge in data.time_bin_edges],
        "timeGroups": [list(group) for group in snapshot.time_groups],
        "xPositions": list(data.x_positions),
        "yPositions": list(data.y_positions),
        "xGroups": [list(group) for group in snapshot.x_groups],
        "yGroups": [list(group) for group in snapshot.y_groups],
        "smoothRadius": snapshot.smooth_radius,
        "palette": snapshot.palette,
        "polarRadius": snapshot.polar_radius,
        "timelinePolar": snapshot.timeline_polar,
        "timelineRange": [snapshot.timeline_range_start, snapshot.timeline_range_end],
        "timelineActiveBin": snapshot.timeline_active_bin,
        "tuningCurveSession": snapshot.tuning_curve_session,
        "waveformChannelMode": snapshot.waveform_channel_mode,
        "totalDegrees": snapshot.total_degrees,
        "selectedCell": list(snapshot.selected_cell) if snapshot.selected_cell is not None else None,
        "occupancyTimeSecAvailable": True,
        "occupancyTimeSecSize": [data.n_y, data.n_x],
        "rateNormalization": (
            "presentation_count_time" if snapshot.value_mode == VALUE_MODE_RATE else "none"
        ),
        "stimulusPresentationCountsAvailable": data.presentation_counts is not None,
        "unitFilter": {
            "enabled": snapshot.unit_filter_enabled,
            "zeroSpikeSpatialBinThreshold": snapshot.zero_bin_threshold,
            "spatialBinCount": data.spatial_bin_count,
            "comparison": "hide when zero-bin count is greater than or equal to threshold",
            "visibleUnitIds": list(visible_unit_ids),
            "excludedUnitIds": [
                int(unit_id)
                for unit_id in data.unit_pool
                if int(unit_id) not in visible_unit_ids
            ],
        },
    }


def _figure_provenance_metadata(
    data: RFMappingData,
    snapshot: FigureViewerSnapshot,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    source_hash = _hash_frozen_file(data.source_identity, cancelled)
    companions: list[dict[str, object]] = []
    for kind, identities in (
        ("headDirection", (data._hd_tuning_identity,) if data._hd_tuning_identity else ()),
        ("probeGeometry", data._probe_file_identities),
        ("waveform", data._waveform_file_identities),
    ):
        for identity in identities:
            companions.append({"kind": kind, **identity.metadata(_hash_frozen_file(identity, cancelled))})
    return {
        "provenanceVersion": 1,
        "application": {
            "name": "RF Map Viewer",
            "version": APP_VERSION,
            "edition": APP_EDITION,
        },
        "source": data.source_identity.metadata(source_hash),
        "snapshot": _figure_snapshot_metadata(data, snapshot),
        "companions": companions,
        "companionStatus": {
            "headDirection": "available" if data._hd_tuning is not None else (data._hd_tuning_error or "unavailable"),
            "probeGeometry": "available" if data._probe_geometry is not None else (data._probe_geometry_error or "unavailable"),
            "waveform": "available" if data._waveform_store is not None else (data._waveform_error or "unavailable"),
        },
        "renderingContract": {
            "preview": "same-page-renderer",
            "svg": "lossless PNG embedded in SVG; plot primitives are not vector paths",
        },
    }


class FigureExportWindow(tk.Toplevel):
    """Page-based, multi-unit figure composer with exact live preview."""

    def __init__(self, viewer: RFMViewer):
        super().__init__(viewer)
        self.viewer = viewer
        self._app_root = viewer._app_root
        # The composer is a recipe for one immutable source session.  Never
        # combine its captured provider with indices from a JSON subsequently
        # selected in the still-interactive parent viewer.
        self.data = viewer.data
        self.snapshot = FigureViewerSnapshot.capture(viewer)
        self.unit_ids = self.snapshot.visible_unit_ids or ()
        if not self.unit_ids:
            raise ValueError(
                "No units pass the zero-spike RF-bin filter for the current RF window."
            )
        self._selected_unit_indices: set[int] = set()
        self._unit_selection_anchor: int | None = None
        self._unit_selection_focus: int | None = None
        selected_unit_id = int(viewer._selected_unit_id_value())
        self.current_unit_id = (
            selected_unit_id if selected_unit_id in self.unit_ids else self.unit_ids[0]
        )
        self._provider_lock = threading.Lock()
        self._preview_provider_lock = threading.Lock()
        self._preview_data_provider: GUIFigureDataProvider | None = None
        self._base_data_provider: GUIFigureDataProvider | None = None
        self._provenance_metadata: dict[str, object] | None = None
        self._context_cache: dict[tuple[object, ...], tuple[tuple[ExportPage, ...], dict[str, object], GUIFigureDataProvider]] = {}
        self.pages: list[dict[str, object]] = [
            {"name": "Page 1", "plots": [self._current_plot_kind()], "frames": list(automatic_frames(1))}
        ]
        self._preview_photo = None
        self._preview_after: str | None = None
        self._preview_poll_after: str | None = None
        self._preview_generation = 0
        self._preview_future: Future | None = None
        self._preview_futures: set[Future] = set()
        self._preview_futures_lock = threading.Lock()
        self._preview_queue: queue.SimpleQueue[tuple[int, object]] = queue.SimpleQueue()
        self._preview_shutdown = threading.Event()
        self._preview_cancel_events: dict[int, threading.Event] = {}
        self._export_busy = False
        self._export_future: Future | None = None
        self._export_poll_after: str | None = None
        self._layout_path: Path | None = None
        self.title("Figure Studio — RF Map Viewer")
        self.geometry("1120x760")
        self.minsize(1050, 680)
        self.transient(viewer)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        modifier = "Command" if self.tk.call("tk", "windowingsystem") == "aqua" else "Control"
        self.bind(f"<{modifier}-s>", self._save_layout)
        self.bind(f"<{modifier}-Shift-L>", self._load_layout)
        self._populate_units()
        self._refresh_pages(select=0)
        self._refresh_current_plots()
        self._schedule_preview()

    def _current_plot_kind(self) -> PlotKind:
        tab = self.viewer._active_tab_key()
        polar = bool(self.viewer.polar_layout_var.get())
        if tab in {"rf", "results"}:
            return PlotKind.RF_POLAR if polar else PlotKind.RF_CARTESIAN
        if tab == "delay":
            if self.viewer.rgb_mode_var.get():
                return PlotKind.RGB_POLAR if polar else PlotKind.RGB_CARTESIAN
            return PlotKind.DELAY_POLAR if polar else PlotKind.DELAY_CARTESIAN
        return PlotKind.TIMELINE_CURRENT

    def _build(self) -> None:
        style = ttk.Style(self)
        style.configure("Studio.TFrame", background="#f5f5f5")
        style.configure("Studio.TLabel", background="#f5f5f5", foreground="#242424")
        style.configure("StudioSection.TLabel", background="#f5f5f5",
                        foreground="#666666", font=("TkDefaultFont", 11))
        style.configure("StudioMuted.TLabel", background="#f5f5f5", foreground="#666666")
        self.configure(background="#f5f5f5")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        left = ttk.Frame(self, padding=(16, 16, 14, 8), style="Studio.TFrame")
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=3, minsize=90)
        left.rowconfigure(4, weight=2, minsize=65)
        center = ttk.Frame(self, padding=(0, 12, 16, 8), style="Studio.TFrame")
        center.grid(row=0, column=1, sticky="nsew")
        center.columnconfigure(0, weight=1)
        center.rowconfigure(2, weight=1)

        def listbox(parent, height):
            return tk.Listbox(
                parent, exportselection=False, width=22, height=height,
                background="white", foreground="#242424",
                selectbackground="#e6e6e6", selectforeground="#242424",
                relief="flat", borderwidth=0, highlightthickness=0,
                activestyle="none",
            )

        ttk.Label(left, text="Units", style="StudioSection.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8))
        self.unit_list = listbox(left, 8)
        self.unit_list.configure(selectmode="extended")
        self.unit_list.grid(row=1, column=0, sticky="nsew")
        self._unit_checkbox_hit_width = max(
            24, int(self.tk.call("font", "measure", self.unit_list.cget("font"), "☑  "))
        )
        self.unit_list.bind("<Button-1>", lambda event: self._on_unit_list_click(event))
        self.unit_list.bind(
            "<Shift-Button-1>", lambda event: self._on_unit_list_click(event, shift=True))
        modifier = "Command" if self.tk.call("tk", "windowingsystem") == "aqua" else "Control"
        self.unit_list.bind(
            f"<{modifier}-Button-1>",
            lambda event: self._on_unit_list_click(event, command=True))
        self.unit_list.bind(
            f"<{modifier}-Shift-Button-1>",
            lambda event: self._on_unit_list_click(event, command=True, shift=True))
        self.unit_list.bind("<<ListboxSelect>>", self._on_unit_list_select)
        unit_buttons = ttk.Frame(left, style="Studio.TFrame")
        unit_buttons.grid(row=2, column=0, sticky="ew", pady=(6, 18))
        for text, command in (("All", self._select_all_units), ("Clear", self._clear_units)):
            ttk.Button(unit_buttons, text=text, width=6, command=command).pack(
                side="left", padx=(0, 3))

        ttk.Label(left, text="Pages", style="StudioSection.TLabel").grid(
            row=3, column=0, sticky="w", pady=(0, 8))
        self.page_list = listbox(left, 4)
        self.page_list.grid(row=4, column=0, sticky="nsew")
        self.page_list.bind("<<ListboxSelect>>", lambda _event: self._on_page_selected())
        self.page_list.bind("<Alt-Up>", lambda _event: self._move_page(-1))
        self.page_list.bind("<Alt-Down>", lambda _event: self._move_page(1))
        self.page_buttons = ttk.Frame(left, style="Studio.TFrame")
        self.page_buttons.grid(row=5, column=0, sticky="ew", pady=(6, 18))
        for text, command in (("+", self._add_page), ("−", self._remove_page)):
            ttk.Button(self.page_buttons, text=text, width=3, command=command).pack(
                side="left", padx=(0, 3))

        self.format_var = tk.StringVar(self, value="PDF")
        format_combo = ttk.Combobox(left, state="readonly", values=("PDF", "PNG", "SVG"),
                                    textvariable=self.format_var, width=18)
        format_combo.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        format_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_format_changed())
        self.normalize_per_unit_var = tk.BooleanVar(self, value=True)
        ttk.Checkbutton(left, text="Normalize per unit", variable=self.normalize_per_unit_var,
                        command=self._on_normalization_changed).grid(
                            row=7, column=0, sticky="w")

        navigation = ttk.Frame(center, style="Studio.TFrame")
        navigation.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(navigation, text="‹", width=3,
                   command=lambda: self._step_preview_unit(-1)).pack(side="left")
        self.preview_unit_var = tk.StringVar(self, value=f"Unit {self.current_unit_id}")
        self.preview_unit_combo = ttk.Combobox(
            navigation, state="readonly", values=tuple(f"Unit {uid}" for uid in self.unit_ids),
            textvariable=self.preview_unit_var, width=13)
        self.preview_unit_combo.pack(side="left", padx=3)
        self.preview_unit_combo.bind("<<ComboboxSelected>>",
            lambda _event: self._set_preview_unit(self.unit_ids[self.preview_unit_combo.current()]))
        ttk.Button(navigation, text="›", width=3,
                   command=lambda: self._step_preview_unit(1)).pack(side="left")
        self.page_name_var = tk.StringVar(self, value="Page 1")
        page_name_entry = ttk.Entry(navigation, textvariable=self.page_name_var, width=20)
        page_name_entry.pack(side="right")
        page_name_entry.bind("<Return>", self._rename_page)
        page_name_entry.bind("<FocusOut>", self._rename_page)

        toolbar = ttk.Frame(center, style="Studio.TFrame")
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.available_kinds = [definition.kind for definition in PLOT_KIND_REGISTRY.values()]
        self.view_combo = ttk.Combobox(toolbar, state="readonly", width=24,
            values=tuple(PLOT_KIND_REGISTRY[kind.value].label for kind in self.available_kinds))
        self.view_combo.current(0)
        self.view_combo.pack(side="left")
        ttk.Button(toolbar, text="Add", width=5, command=self._add_plot).pack(
            side="left", padx=(6, 12))
        self.remove_plot_button = ttk.Button(toolbar, text="Remove", width=7,
                                              command=self._remove_plot)
        self.remove_plot_button.pack(side="left")
        self.frame_size_var = tk.StringVar(self, value="Full")
        self.frame_size_combo = ttk.Combobox(toolbar, state="readonly", values=tuple(FRAME_PRESETS),
                                             textvariable=self.frame_size_var, width=9)
        self.frame_size_combo.pack(side="left", padx=(6, 0))
        self.frame_size_combo.bind("<<ComboboxSelected>>", lambda _event: self._resize_selected_plot())
        layout_button = ttk.Menubutton(toolbar, text="Layout", width=7)
        self.layout_menu = tk.Menu(layout_button, tearoff=False)
        self.layout_menu.add_command(label="Load Layout…", command=self._load_layout,
                                     accelerator=f"{modifier}-Shift-l")
        self.layout_menu.add_command(label="Save Layout…", command=self._save_layout,
                                     accelerator=f"{modifier}-s")
        layout_button.configure(menu=self.layout_menu)
        layout_button.pack(side="right", padx=(6, 0))
        ttk.Button(toolbar, text="Arrange", command=self._auto_arrange).pack(side="right")

        self.preview_label = tk.Label(center, background="#eeeeee", borderwidth=0)
        self.preview_label.grid(row=2, column=0, sticky="nsew")
        self.page_canvas = FigurePageCanvas(self.preview_label, on_select=self._select_plot,
            on_change=self._commit_frames,
            on_error=lambda text: self.preview_status.configure(text=text))
        self.page_canvas.pack(fill="both", expand=True)
        self.preview_status = ttk.Label(center, text="", style="StudioMuted.TLabel")
        self.preview_status.grid(row=3, column=0, sticky="w", pady=(4, 0))

        footer = ttk.Frame(self, padding=(16, 8, 16, 12), style="Studio.TFrame")
        footer.grid(row=1, column=0, columnspan=2, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.destination_var = tk.StringVar(self, value="")
        ttk.Entry(footer, textvariable=self.destination_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(footer, text="Save to…", command=self._choose_destination).grid(
            row=0, column=1, padx=(8, 0))
        self.export_button = ttk.Button(footer, text="Export", command=self._start_export)
        self.export_button.grid(row=0, column=2, padx=(12, 0))
        self.export_status = ttk.Label(footer, text="", style="StudioMuted.TLabel")
        self.export_status.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _populate_units(self) -> None:
        self._select_current_unit()

    def _save_layout(self, _event=None) -> str:
        self._rename_page()
        destination = filedialog.asksaveasfilename(
            parent=self, title="Save Layout", defaultextension=".rfmlayout",
            filetypes=(("Figure layout", "*.rfmlayout"),),
            initialfile=self._layout_path.stem if self._layout_path else "Figure layout",
            **({"initialdir": str(self._layout_path.parent)} if self._layout_path else {}),
        )
        if not destination:
            return "break"
        try:
            layout = make_figure_layout(
                self.data, self.snapshot, self.pages,
                bool(self.normalize_per_unit_var.get()), self.format_var.get(),
            )
            path = Path(destination)
            path.write_text(json.dumps(layout, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot save layout", str(exc), parent=self)
        else:
            self._layout_path = path
            self.preview_status.configure(text="Layout saved")
        return "break"

    def _load_layout(self, _event=None) -> str:
        if self._export_busy:
            messagebox.showinfo("Export is running", "Wait for the export to finish before loading a layout.", parent=self)
            return "break"
        source = filedialog.askopenfilename(
            parent=self, title="Load Layout",
            filetypes=(("All files", "*.*"),),
            **({"initialdir": str(self._layout_path.parent)} if self._layout_path else {}),
        )
        if not source:
            return "break"
        try:
            path = Path(source)
            layout = json.loads(path.read_text(encoding="utf-8"))
            snapshot, pages, normalize_per_unit, output_format = parse_figure_layout(
                layout, self.data, self.snapshot,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot load layout", str(exc), parent=self)
            return "break"
        # Keep providers paired with their captured settings while an old preview finishes.
        with self._provider_lock, self._preview_provider_lock:
            self.snapshot = snapshot
            self._base_data_provider = None
            self._preview_data_provider = None
            self._provenance_metadata = None
            self._context_cache.clear()
        self.pages = pages
        self.normalize_per_unit_var.set(normalize_per_unit)
        if self.format_var.get() != output_format:
            self.format_var.set(output_format)
            self._on_format_changed()
        self._layout_path = path
        self._refresh_pages(select=0)
        self._refresh_current_plots()
        self._schedule_preview()
        return "break"

    def _refresh_unit_rows(self, *, see_focus: bool = False) -> None:
        yview = self.unit_list.yview()
        self.unit_list.delete(0, "end")
        for index, unit_id in enumerate(self.unit_ids):
            rf_map = self.data.rf_map_by_unit_id(unit_id)
            checkbox = "☑" if index in self._selected_unit_indices else "☐"
            self.unit_list.insert(
                "end",
                f"{checkbox}  Unit {rf_map.unit_id}",
            )
        self.unit_list.selection_clear(0, "end")
        for index in sorted(self._selected_unit_indices):
            self.unit_list.selection_set(index)
        if self._unit_selection_anchor is not None:
            self.unit_list.selection_anchor(self._unit_selection_anchor)
        if self._unit_selection_focus is not None:
            self.unit_list.activate(self._unit_selection_focus)
            if see_focus:
                self.unit_list.see(self._unit_selection_focus)
            elif yview:
                self.unit_list.yview_moveto(yview[0])
        if not self._export_busy:
            units, pages = len(self._selected_unit_indices), len(self.pages)
            self.export_status.configure(
                text=f"{units} unit{'s' if units != 1 else ''} · {pages} page{'s' if pages != 1 else ''}"
            )

    def _unit_index_at_event(self, event) -> int | None:
        if not self.unit_ids:
            return None
        index = int(self.unit_list.nearest(event.y))
        bounds = self.unit_list.bbox(index)
        if bounds is None:
            return None
        _x, y, _width, height = bounds
        if not y <= int(event.y) < y + height:
            return None
        return index

    def _on_unit_list_click(
        self,
        event,
        *,
        command: bool = False,
        shift: bool = False,
    ) -> str:
        self.unit_list.focus_set()
        index = self._unit_index_at_event(event)
        if index is None:
            return "break"
        bounds = self.unit_list.bbox(index)
        checkbox_click = bool(
            bounds is not None
            and composer_unit_checkbox_hit(
                int(event.x),
                int(bounds[0]),
                self._unit_checkbox_hit_width,
            )
        )
        selected, anchor = composer_unit_selection_after_click(
            self._selected_unit_indices,
            index,
            self._unit_selection_anchor,
            len(self.unit_ids),
            # Clicking the checkbox itself is an additive toggle even without
            # a modifier. Shift retains its range meaning; Command retains its
            # explicit toggle/add-range meaning anywhere on the row.
            command=command or (checkbox_click and not shift),
            shift=shift,
        )
        self._selected_unit_indices = set(selected)
        self._unit_selection_anchor = anchor
        self._unit_selection_focus = index
        self._refresh_unit_rows()
        self._set_preview_unit(self.unit_ids[index])
        return "break"

    def _on_unit_list_select(self, _event=None) -> None:
        selected = set(self.unit_list.curselection())
        focus = int(self.unit_list.index("active"))
        if selected == self._selected_unit_indices and focus == self._unit_selection_focus:
            return
        self._selected_unit_indices = selected
        self._unit_selection_anchor = int(self.unit_list.index("anchor"))
        self._unit_selection_focus = focus
        self._refresh_unit_rows()
        self._set_preview_unit(self.unit_ids[focus])

    def _select_current_unit(self) -> None:
        try:
            index = self.unit_ids.index(self.current_unit_id)
        except ValueError:
            index = None
        self._selected_unit_indices = set() if index is None else {index}
        self._unit_selection_anchor = index
        self._unit_selection_focus = index
        self._refresh_unit_rows(see_focus=True)

    def _select_all_units(self) -> None:
        self._selected_unit_indices = set(range(len(self.unit_ids)))
        try:
            focus = self.unit_ids.index(self.current_unit_id)
        except ValueError:
            focus = 0 if self.unit_ids else None
        self._unit_selection_anchor = focus
        self._unit_selection_focus = focus
        self._refresh_unit_rows(see_focus=True)

    def _clear_units(self) -> None:
        self._selected_unit_indices.clear()
        self._unit_selection_anchor = None
        self._unit_selection_focus = None
        self._refresh_unit_rows()

    def _set_preview_unit(self, unit_id: int) -> None:
        self.preview_unit_var.set(f"Unit {unit_id}")
        if unit_id != self.current_unit_id:
            self.current_unit_id = unit_id
            self._schedule_preview()

    def _step_preview_unit(self, delta: int) -> None:
        index = self.unit_ids.index(self.current_unit_id)
        self._set_preview_unit(self.unit_ids[(index + delta) % len(self.unit_ids)])

    def _on_normalization_changed(self) -> None:
        self.preview_status.configure(text="")

    def _selected_unit_ids(self) -> tuple[int, ...]:
        return tuple(
            unit_id
            for index, unit_id in enumerate(self.unit_ids)
            if index in self._selected_unit_indices
        )

    def _selected_page_index(self) -> int:
        selection = self.page_list.curselection()
        return int(selection[0]) if selection else 0

    def _refresh_pages(self, *, select: int | None = None) -> None:
        current = self._selected_page_index() if select is None else select
        self.page_list.delete(0, "end")
        for index, page in enumerate(self.pages):
            self.page_list.insert("end", str(page["name"]))
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
        self.pages.append({"name": f"Page {len(self.pages) + 1}", "plots": [], "frames": []})
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
        page = self.pages[self._selected_page_index()]
        if "frames" not in page or len(page["frames"]) != len(plots):
            page["frames"] = list(automatic_frames(len(plots)))
        if select is not None:
            select = max(0, min(len(plots) - 1, select)) if plots else None
        self._refresh_pages(select=self._selected_page_index())
        self.page_canvas.set_frames(page["frames"],
            [PLOT_KIND_REGISTRY[kind.value].label for kind in plots], select)
        self._on_plot_selected()

    def _select_plot(self, index: int) -> None:
        self.page_canvas.selected = index
        self._on_plot_selected()

    def _on_plot_selected(self) -> None:
        index = self.page_canvas.selected
        self.page_canvas._draw_selection()
        self.remove_plot_button.state(["disabled"] if index is None else ["!disabled"])
        self.frame_size_combo.configure(state="disabled" if index is None else "readonly")
        if index is not None:
            frame = self.pages[self._selected_page_index()]["frames"][index]
            self.frame_size_var.set(next(
                (name for name, size in FRAME_PRESETS.items() if size == frame[2:]),
                f"{frame[2]} × {frame[3]}"))

    def _commit_frames(self, frames, selected: int) -> None:
        self.pages[self._selected_page_index()]["frames"] = list(frames)
        self._refresh_current_plots(select=selected)
        self.preview_status.configure(text="")
        self._schedule_preview()


    def _resize_selected_plot(self) -> None:
        index = self.page_canvas.selected
        if index is None:
            return
        frames = self.pages[self._selected_page_index()]["frames"]
        width, height = FRAME_PRESETS[self.frame_size_var.get()]
        column, row, _width, _height = frames[index]
        target = (min(column, 6 - width), min(row, 6 - height), width, height)
        try:
            arranged = arrange_frames(frames, index, target)
        except ValueError as exc:
            self.preview_status.configure(text=str(exc))
            self._on_plot_selected()
        else:
            self._commit_frames(arranged, index)


    def _auto_arrange(self) -> None:
        self.pages[self._selected_page_index()]["frames"] = list(
            automatic_frames(len(self._current_plot_kinds()))
        )
        self._refresh_current_plots(select=0 if self._current_plot_kinds() else None)
        self._schedule_preview()


    def _add_plot(self) -> None:
        index = self.view_combo.current()
        if index < 0:
            return
        plots = self._current_plot_kinds()
        if len(plots) >= 9:
            self.preview_status.configure(
                text="Page full"
            )
            return
        page = self.pages[self._selected_page_index()]
        frames = page["frames"]
        try:
            frames.append(first_available_frame(frames))
        except ValueError:
            page["frames"] = list(automatic_frames(len(plots) + 1))
        plots.append(self.available_kinds[index])
        self._refresh_current_plots(select=len(plots) - 1)
        self._schedule_preview()


    def _remove_plot(self) -> None:
        index = self.page_canvas.selected
        if index is None:
            return
        plots = self._current_plot_kinds()
        plots.pop(index)
        self.pages[self._selected_page_index()]["frames"].pop(index)
        self._refresh_current_plots(select=max(0, index - 1))
        self._schedule_preview()


    def _export_pages(self, page_index: int | None = None) -> tuple[ExportPage, ...]:
        pages: list[ExportPage] = []
        for index, page in enumerate(self.pages):
            if page_index is not None and index != page_index:
                continue
            kinds: list[PlotKind] = page["plots"]  # type: ignore[assignment]
            if not kinds:
                raise ValueError(f"Page {index + 1} ({page['name']}) has no views.")
            pages.append(
                ExportPage(
                    str(page["name"]),
                    tuple(
                        PlotSpec(
                            kind,
                            options={
                                "normalize_per_unit": (
                                    bool(self.normalize_per_unit_var.get())
                                    if hasattr(self, "normalize_per_unit_var")
                                    else False
                                ),
                                **(
                                    {"frame": page["frames"][plot_index]}
                                    if "frames" in page
                                    else {}
                                ),
                            },
                        )
                        for plot_index, kind in enumerate(kinds)
                    ),
                )
            )
        return tuple(pages)

    def _resolved_export_pages(
        self,
        raw_pages: tuple[ExportPage, ...],
        shared_rf_scale: tuple[float, float] | None,
        shared_waveform_limit: float | None = None,
    ) -> tuple[ExportPage, ...]:
        pages: list[ExportPage] = []
        for page in raw_pages:
            plots: list[PlotSpec] = []
            for plot in page.plots:
                options = dict(plot.options)
                x_values = [
                    (self.data.x_positions[start] + self.data.x_positions[end]) / 2.0
                    for start, end in self.snapshot.x_groups
                ]
                y_values = [
                    (self.data.y_positions[start] + self.data.y_positions[end]) / 2.0
                    for start, end in self.snapshot.y_groups
                ]
                if self.snapshot.polar_radius == POLAR_RADIUS_MODES[0]:
                    polar_row_indices = sorted(
                        range(len(self.snapshot.y_groups)),
                        key=lambda index: self.snapshot.y_groups[index][0],
                    )
                else:
                    polar_row_indices = list(range(len(y_values) - 1, -1, -1))
                polar_y_values = [y_values[index] for index in polar_row_indices]
                spatial_kinds = {
                    PlotKind.RF_CARTESIAN,
                    PlotKind.RF_POLAR,
                    PlotKind.DELAY_CARTESIAN,
                    PlotKind.DELAY_POLAR,
                    PlotKind.RGB_CARTESIAN,
                    PlotKind.RGB_POLAR,
                }
                if plot.kind in spatial_kinds:
                    options.update(
                        x_values=x_values,
                        y_values=y_values,
                        x_unit="°",
                        y_unit="°",
                        show_axes=True,
                        palette=self.snapshot.palette,
                        total_degrees=self.snapshot.total_degrees,
                    )
                if plot.kind in {
                    PlotKind.RF_POLAR,
                    PlotKind.DELAY_POLAR,
                    PlotKind.RGB_POLAR,
                }:
                    # The provider has already reordered its payload into
                    # inner-to-outer rows. Freeze matching radial labels and
                    # prohibit a second renderer-side reversal.
                    options.update(
                        y_values=polar_y_values,
                        inner_blank_rows=INNER_BLANK_ROWS,
                        ring_order="inner_to_outer",
                        reverse_rings=False,
                        clockwise=True,
                    )
                if plot.kind in {PlotKind.DELAY_CARTESIAN, PlotKind.DELAY_POLAR}:
                    options.update(
                        palette="delay",
                        vmin=self.data.time_bin_edges[0] * 1000.0,
                        vmax=self.data.time_bin_edges[-1] * 1000.0,
                        value_unit="ms",
                        show_colorbar=True,
                    )
                if plot.kind is PlotKind.TIMELINE_CURRENT:
                    options.update(
                        polar=self.snapshot.timeline_polar,
                        inner_blank_rows=INNER_BLANK_ROWS,
                        palette=self.snapshot.palette,
                        total_degrees=self.snapshot.total_degrees,
                        value_unit=value_mode_unit(self.snapshot.value_mode),
                        time_unit="ms",
                    )
                if plot.kind in {PlotKind.HD_LINE, PlotKind.HD_POLAR}:
                    options.update(x_unit="°", y_unit="Hz", show_axes=True)
                if plot.kind is PlotKind.PROBE_LAYOUT:
                    options.update(
                        coordinate_unit="µm",
                        show_axes=True,
                        show_scale_bar=True,
                    )
                if plot.kind is PlotKind.WAVEFORM_LOCAL_AVERAGE:
                    options.update(
                        palette="rdbu_r",
                        value_unit="µV",
                        show_axes=True,
                        show_colorbar=True,
                    )
                    if shared_waveform_limit is not None:
                        options.update(
                            vmin=-abs(float(shared_waveform_limit)),
                            vmax=abs(float(shared_waveform_limit)),
                        )
                if plot.kind in {PlotKind.RGB_CARTESIAN, PlotKind.RGB_POLAR}:
                    options["show_colorbar"] = False
                if shared_rf_scale is not None and plot.kind in {
                    PlotKind.RF_CARTESIAN,
                    PlotKind.RF_POLAR,
                }:
                    options.update(
                        vmin=shared_rf_scale[0],
                        vmax=shared_rf_scale[1],
                        value_unit=value_mode_unit(self.snapshot.value_mode),
                        show_colorbar=True,
                    )
                start_ms = self.data.time_bin_edges[self.snapshot.rf_source_start] * 1000.0
                end_ms = self.data.time_bin_edges[self.snapshot.rf_source_end + 1] * 1000.0
                full_start_ms = self.data.time_bin_edges[0] * 1000.0
                full_end_ms = self.data.time_bin_edges[-1] * 1000.0
                if plot.kind in {PlotKind.RF_CARTESIAN, PlotKind.RF_POLAR}:
                    context = (
                        f"{format_ms(start_ms)}–{format_ms(end_ms)} ms"
                        if self.snapshot.rf_subtract_source_range is None else ""
                    )
                elif plot.kind in {
                    PlotKind.DELAY_CARTESIAN,
                    PlotKind.DELAY_POLAR,
                    PlotKind.RGB_CARTESIAN,
                    PlotKind.RGB_POLAR,
                }:
                    context = (
                        f"{format_ms(full_start_ms)}–{format_ms(full_end_ms)} ms"
                    )
                else:
                    context = None
                if context is not None:
                    options["subtitle"] = context
                title = plot.title or PLOT_KIND_REGISTRY[plot.kind.value].label
                if (
                    plot.kind is PlotKind.PROBE_LAYOUT
                    and self._base_data_provider is not None
                    and self._base_data_provider.probe_geometry is not None
                ):
                    title = f"{self._base_data_provider.probe_geometry.probe_name} layout"
                plots.append(replace(plot, title=title, options=options))
            pages.append(ExportPage(page.name, tuple(plots)))
        return tuple(pages)

    def _verify_export_inputs(self) -> None:
        self.data.source_identity.verify_path()
        identities = tuple(
            identity
            for identity in (
                self.data._hd_tuning_identity,
                *self.data._probe_file_identities,
                *self.data._waveform_file_identities,
            )
            if identity is not None
        )
        for identity in identities:
            identity.verify_path()

    def _recipe_key(
        self,
        unit_ids: tuple[int, ...],
        raw_pages: tuple[ExportPage, ...],
    ) -> tuple[object, ...]:
        def option_key(value):
            if isinstance(value, Mapping):
                return tuple((name, option_key(item)) for name, item in sorted(value.items()))
            if isinstance(value, tuple):
                return tuple(option_key(item) for item in value)
            return value

        return (
            unit_ids,
            tuple(
                (
                    page.name,
                    tuple(
                        (plot.kind.value, plot.title, option_key(plot.options))
                        for plot in page.plots
                    ),
                )
                for page in raw_pages
            ),
        )

    def _freeze_context(
        self,
        unit_ids: tuple[int, ...],
        raw_pages: tuple[ExportPage, ...],
        cancelled: Callable[[], bool] | None = None,
    ) -> tuple[tuple[ExportPage, ...], dict[str, object], GUIFigureDataProvider]:
        key = self._recipe_key(unit_ids, raw_pages)
        with self._provider_lock:
            if cancelled is not None and cancelled():
                raise RuntimeError("Preview superseded by a newer recipe")
            self._verify_export_inputs()
            has_waveform = any(
                plot.kind is PlotKind.WAVEFORM_LOCAL_AVERAGE
                for page in raw_pages
                for plot in page.plots
            )
            if has_waveform:
                previous_waveform_inputs = self.data._waveform_file_identities
                captured_waveform_inputs = self.data.capture_waveform_inputs(
                    unit_ids
                )
                if captured_waveform_inputs != previous_waveform_inputs:
                    self._provenance_metadata = None
                self._verify_export_inputs()
            cached = self._context_cache.get(key)
            if cached is not None:
                return cached
            if self._base_data_provider is None:
                self._base_data_provider = GUIFigureDataProvider(self.data, self.snapshot)
            if self._provenance_metadata is None:
                self._provenance_metadata = _figure_provenance_metadata(
                    self.data,
                    self.snapshot,
                    cancelled,
                )
            has_rf = any(
                plot.kind in {PlotKind.RF_CARTESIAN, PlotKind.RF_POLAR}
                for page in raw_pages
                for plot in page.plots
            )
            normalize_per_unit = bool(raw_pages[0].plots[0].options.get("normalize_per_unit", False))
            scale = (
                self._base_data_provider.shared_rf_bounds(unit_ids, cancelled)
                if has_rf and not normalize_per_unit
                else None
            )
            waveform_limit = (
                self._base_data_provider.shared_waveform_amplitude_limit(
                    unit_ids, cancelled
                )
                if has_waveform and not normalize_per_unit
                else None
            )
            pages = (
                self._resolved_export_pages(raw_pages, scale, waveform_limit)
                if has_waveform
                else self._resolved_export_pages(raw_pages, scale)
            )
            provider = GUIFigureDataProvider(
                self.data,
                self.snapshot,
                shared_rf_scale=scale,
                shared_waveform_limit=waveform_limit,
                response_cache=self._base_data_provider._response_cache,
                response_cache_lock=self._base_data_provider._response_cache_lock,
            )
            metadata = dict(self._provenance_metadata)
            metadata["normalization"] = {
                "perUnit": normalize_per_unit,
                "scope": "each unit" if normalize_per_unit else "selected units",
                "values": "original physical units; color limits only",
                "rf": "palette range up to maximum response",
                "waveform": "symmetric amplitude limits",
            }
            if normalize_per_unit:
                unit_scales = []
                for unit_id in unit_ids:
                    if cancelled is not None and cancelled():
                        raise RuntimeError("Preview superseded by a newer recipe")
                    scales: dict[str, object] = {"unitId": unit_id}
                    if has_rf:
                        unit_idx = self.data.rf_map_by_unit_id(unit_id).unit_index
                        low, high = palette_response_range(
                            self._base_data_provider._rf_matrix(unit_idx, polar=False),
                            self.snapshot.palette,
                        )
                        scales["rf"] = {
                            "vmin": low,
                            "vmax": high,
                            "unit": value_mode_unit(self.snapshot.value_mode),
                        }
                    if has_waveform:
                        limit = self._base_data_provider.shared_waveform_amplitude_limit((unit_id,), cancelled)
                        if limit is not None:
                            scales["waveform"] = {"vmin": -limit, "vmax": limit, "unit": "µV"}
                    unit_scales.append(scales)
                metadata["perUnitScales"] = unit_scales
            if scale is not None:
                metadata["sharedRFScale"] = {
                    "vmin": scale[0],
                    "vmax": scale[1],
                    "unit": value_mode_unit(self.snapshot.value_mode),
                    "unitIds": list(unit_ids),
                }
            if waveform_limit is not None:
                metadata["sharedWaveformScale"] = {
                    "vmin": -waveform_limit,
                    "vmax": waveform_limit,
                    "unit": "µV",
                    "unitIds": list(unit_ids),
                    "baselineEndMs": -0.25,
                    "channelMode": self.snapshot.waveform_channel_mode,
                }
            result = (pages, metadata, provider)
            if cancelled is not None and cancelled():
                raise RuntimeError("Preview superseded by a newer recipe")
            self._verify_export_inputs()
            self._context_cache[key] = result
            return result

    def _preview_context(
        self,
        unit_ids: tuple[int, ...],
        raw_pages: tuple[ExportPage, ...],
        cancelled: Callable[[], bool],
    ) -> tuple[tuple[ExportPage, ...], dict[str, object], GUIFigureDataProvider]:
        # Preview works only from the open dataset. Freezing and hashing every
        # selected input is reserved for the final export.
        with self._preview_provider_lock:
            if cancelled():
                raise RuntimeError("Preview superseded by a newer recipe")
            if self._preview_data_provider is None:
                self._preview_data_provider = GUIFigureDataProvider(self.data, self.snapshot)
            provider = self._preview_data_provider
            preview_pages = tuple(
                ExportPage(
                    page.name,
                    tuple(
                        replace(plot, options={**plot.options, "normalize_per_unit": True})
                        for plot in page.plots
                    ),
                )
                for page in raw_pages
            )
            pages = self._resolved_export_pages(preview_pages, None)
        metadata = {"preview": {"unitId": unit_ids[0], "scaling": "per unit"}}
        return pages, metadata, provider

    def _preview_request(self) -> tuple[tuple[int, ...], tuple[ExportPage, ...], int, int, int]:
        page_index = self._selected_page_index()
        unit_ids = (self.current_unit_id,)
        pages = self._export_pages(page_index=page_index)
        available_width = max(480, self.preview_label.winfo_width() - 20)
        available_height = max(360, self.preview_label.winfo_height() - 20)
        return unit_ids, pages, page_index, available_width, available_height

    def _preview_plan(
        self,
        unit_ids: tuple[int, ...],
        pages: tuple[ExportPage, ...],
        metadata: dict[str, object],
    ) -> ExportPlan:
        return ExportPlan(
            FigureFormat.PDF,
            unit_ids,
            pages,
            Path("/tmp/rfmap-live-preview.pdf"),
            metadata=metadata,
        )

    def _schedule_preview(self) -> None:
        self._preview_generation += 1
        with self._preview_futures_lock:
            old_futures = tuple(self._preview_futures)
            old_cancel_events = tuple(self._preview_cancel_events.values())
        for event in old_cancel_events:
            event.set()
        for future in old_futures:
            future.cancel()
        if self._preview_after is not None:
            try:
                self.after_cancel(self._preview_after)
            except tk.TclError:
                pass
        generation = self._preview_generation
        self._preview_after = self.after(
            80,
            lambda generation=generation: self._start_preview(generation),
        )

    def _start_preview(self, generation: int) -> None:
        self._preview_after = None
        try:
            unit_ids, raw_pages, page_index, width, height = self._preview_request()
        except Exception as exc:
            self._show_preview_error(exc)
            return
        self.preview_status.configure(text=f"Rendering unit {unit_ids[0]}…")
        cancel_event = threading.Event()
        with self._preview_futures_lock:
            self._preview_cancel_events[generation] = cancel_event

        def cancelled() -> bool:
            return self._preview_shutdown.is_set() or cancel_event.is_set()

        def worker() -> tuple[int, int, object]:
            pages, metadata, provider = self._preview_context(
                unit_ids,
                raw_pages,
                cancelled,
            )
            if cancelled():
                raise RuntimeError("Preview superseded by a newer recipe")
            plan = self._preview_plan(unit_ids, pages, metadata)
            image = render_live_preview(
                plan,
                unit_ids[0],
                0,
                data_provider=provider,
            )
            if cancelled():
                image.close()
                raise RuntimeError("Preview superseded by a newer recipe")
            return unit_ids[0], page_index, image

        future = _submit_daemon_future(worker, name="rfmap-preview")
        self._preview_future = future
        with self._preview_futures_lock:
            self._preview_futures.add(future)

        def finished(done: Future) -> None:
            try:
                payload: object = done.result()
            except Exception as exc:
                payload = exc
            with self._preview_futures_lock:
                self._preview_futures.discard(done)
                self._preview_cancel_events.pop(generation, None)
            if self._preview_shutdown.is_set():
                if isinstance(payload, tuple) and len(payload) == 3:
                    image = payload[2]
                    if hasattr(image, "close"):
                        image.close()
                return
            self._preview_queue.put((generation, payload))

        future.add_done_callback(finished)
        self._schedule_preview_poll()

    def _schedule_preview_poll(self) -> None:
        if self._preview_poll_after is None:
            self._preview_poll_after = self.after(40, self._poll_preview)

    def _poll_preview(self) -> None:
        self._preview_poll_after = None
        while True:
            try:
                generation, payload = self._preview_queue.get_nowait()
            except queue.Empty:
                break
            if generation != self._preview_generation:
                if isinstance(payload, tuple) and len(payload) == 3:
                    stale_image = payload[2]
                    if hasattr(stale_image, "close"):
                        stale_image.close()
                continue
            self._preview_future = None
            if isinstance(payload, Exception):
                self._show_preview_error(payload)
            else:
                unit_id, page_index, image = payload
                try:
                    self.page_canvas.set_image(image)
                    self._preview_photo = self.page_canvas._photo
                finally:
                    image.close()
                self.preview_label.configure(text="")
                self.preview_status.configure(text="")
        # A stale result may arrive before the latest worker. Keep polling until
        # the current generation has either rendered or produced an error.
        with self._preview_futures_lock:
            preview_inflight = bool(self._preview_futures)
        if preview_inflight or not self._preview_queue.empty():
            self._schedule_preview_poll()

    def _show_preview_error(self, exc: Exception) -> None:
        self._preview_photo = None
        self.preview_label.configure(text=f"Preview unavailable\n{exc}")
        self.page_canvas.set_message(
            "" if not self._current_plot_kinds()
            else f"Preview unavailable\n{exc}"
        )
        self.preview_status.configure(text="")

    def _on_format_changed(self) -> None:
        self.destination_var.set("")

    def _default_base_name(self) -> str:
        stem = self.data.path.stem
        return f"{stem}_figures"

    def _choose_destination(self) -> None:
        figure_format = FigureFormat.coerce(self.format_var.get().split()[0])
        initial_dir = self.data.path.parent
        if figure_format is FigureFormat.PDF:
            path = filedialog.asksaveasfilename(
                parent=self,
                title="Export PDF",
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
            title="Export folder",
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
            figure_format = FigureFormat.coerce(self.format_var.get().split()[0])
            destination = Path(destination_text).expanduser()
            raw_pages = self._export_pages()
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
        self.export_status.configure(text="Preparing…")

        def worker():
            pages, metadata, provider = self._freeze_context(unit_ids, raw_pages)
            plan = ExportPlan(
                figure_format,
                unit_ids,
                pages,
                destination,
                metadata=metadata,
            )
            return export_figures(
                plan,
                data_provider=provider,
                overwrite=overwrite,
                before_publish=self._verify_export_inputs,
            )

        future = _export_executor(self._app_root).submit(worker)
        self._export_future = future
        _register_export_job(self._app_root, self.viewer, future)
        page_count = len(unit_ids) * len(raw_pages)
        self.export_status.configure(text=f"Exporting {page_count} pages…")
        self._export_poll_after = self.after(50, self._poll_export)

    def _poll_export(self) -> None:
        self._export_poll_after = None
        future = self._export_future
        if future is None:
            return
        if not future.done():
            self._export_poll_after = self.after(50, self._poll_export)
            return
        try:
            result = future.result()
        except Exception as exc:
            self._finish_export(error=str(exc))
        else:
            self._finish_export(result=result)

    def _finish_export(self, *, result=None, error: str | None = None) -> None:
        future = self._export_future
        self._export_future = None
        _unregister_export_job(self._app_root, future)
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
        self.destroy()

    def destroy(self) -> None:
        if (
            not getattr(self._app_root, "_rfm_quitting", False)
            and self._export_busy
        ):
            messagebox.showinfo(
                "Export is running",
                "Wait for the export to finish before closing the composer.",
                parent=self,
            )
            return
        if self.viewer._figure_export_window is self:
            self.viewer._figure_export_window = None
        self._preview_generation += 1
        self._preview_shutdown.set()
        with self._preview_futures_lock:
            cancel_events = tuple(self._preview_cancel_events.values())
        for event in cancel_events:
            event.set()
        if self._preview_future is not None:
            self._preview_future.cancel()
        while True:
            try:
                _generation, payload = self._preview_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(payload, tuple) and len(payload) == 3:
                image = payload[2]
                if hasattr(image, "close"):
                    image.close()
        for name in ("_preview_after", "_preview_poll_after", "_export_poll_after"):
            callback = getattr(self, name, None)
            if callback is not None:
                try:
                    self.after_cancel(callback)
                except tk.TclError:
                    pass
                setattr(self, name, None)
        super().destroy()
