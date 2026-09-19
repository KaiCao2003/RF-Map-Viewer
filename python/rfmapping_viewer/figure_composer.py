"""Immutable figure snapshots, plot data, and the composer window."""

from __future__ import annotations

import math
import queue
import threading
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
    WAVEFORM_CHANNEL_MODE_LABELS,
)
from rfmapping_viewer.display import (
    _nullable_array_list,
    format_ms,
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
        self._base_data_provider: GUIFigureDataProvider | None = None
        self._provenance_metadata: dict[str, object] | None = None
        self._context_cache: dict[tuple[object, ...], tuple[tuple[ExportPage, ...], dict[str, object], GUIFigureDataProvider]] = {}
        self.pages: list[dict[str, object]] = [
            {"name": "Page 1", "plots": [self._current_plot_kind()]}
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
        self.title("Export Figures — RF Map Viewer")
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
                "files use the same renderer; SVG embeds a lossless raster."
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
            values=("PDF", "PNG", "SVG (embedded raster)"),
            textvariable=self.format_var,
            width=24,
        )
        format_combo.pack(fill="x", pady=(5, 12))
        format_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_format_changed())

        ttk.Label(
            left,
            text="Units  (click · Shift-click · ⌘-click)",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w")
        self.unit_list = tk.Listbox(left, selectmode="extended", exportselection=False, width=30, height=13)
        self.unit_list.pack(fill="both", expand=True, pady=(5, 5))
        try:
            checkbox_width = int(
                self.tk.call(
                    "font",
                    "measure",
                    self.unit_list.cget("font"),
                    "☑  ",
                )
            )
        except (tk.TclError, TypeError, ValueError):
            checkbox_width = 24
        self._unit_checkbox_hit_width = max(24, checkbox_width)
        self.unit_list.bind(
            "<Button-1>",
            lambda event: self._on_unit_list_click(event),
        )
        self.unit_list.bind(
            "<Shift-Button-1>",
            lambda event: self._on_unit_list_click(event, shift=True),
        )
        if self.tk.call("tk", "windowingsystem") == "aqua":
            self.unit_list.bind(
                "<Command-Button-1>",
                lambda event: self._on_unit_list_click(event, command=True),
            )
            self.unit_list.bind(
                "<Command-Shift-Button-1>",
                lambda event: self._on_unit_list_click(
                    event,
                    command=True,
                    shift=True,
                ),
            )
        else:
            self.unit_list.bind(
                "<Control-Button-1>",
                lambda event: self._on_unit_list_click(event, command=True),
            )
            self.unit_list.bind(
                "<Control-Shift-Button-1>",
                lambda event: self._on_unit_list_click(
                    event,
                    command=True,
                    shift=True,
                ),
            )
        unit_buttons = ttk.Frame(left)
        unit_buttons.pack(fill="x", pady=(0, 12))
        ttk.Button(unit_buttons, text="Current", command=self._select_current_unit).pack(side="left")
        ttk.Button(unit_buttons, text="All", command=self._select_all_units).pack(side="left", padx=5)
        ttk.Button(unit_buttons, text="Clear", command=self._clear_units).pack(side="left")

        ttk.Label(
            left,
            text="Pages per selected unit  (shared template)",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w")
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
        self._select_current_unit()

    def _refresh_unit_rows(self, *, see_focus: bool = False) -> None:
        yview = self.unit_list.yview()
        self.unit_list.delete(0, "end")
        for index, unit_id in enumerate(self.unit_ids):
            rf_map = self.data.rf_map_by_unit_id(unit_id)
            checkbox = "☑" if index in self._selected_unit_indices else "☐"
            self.unit_list.insert(
                "end",
                f"{checkbox}  index {rf_map.unit_index:03d}  ·  unit {rf_map.unit_id}",
            )
        self.unit_list.selection_clear(0, "end")
        for index in sorted(self._selected_unit_indices):
            self.unit_list.selection_set(index)
        if self._unit_selection_focus is not None:
            self.unit_list.activate(self._unit_selection_focus)
            if see_focus:
                self.unit_list.see(self._unit_selection_focus)
            elif yview:
                self.unit_list.yview_moveto(yview[0])

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
        self._schedule_preview()
        return "break"

    def _select_current_unit(self) -> None:
        try:
            index = self.unit_ids.index(self.current_unit_id)
        except ValueError:
            index = None
        self._selected_unit_indices = set() if index is None else {index}
        self._unit_selection_anchor = index
        self._unit_selection_focus = index
        self._refresh_unit_rows(see_focus=True)
        self._schedule_preview()

    def _select_all_units(self) -> None:
        self._selected_unit_indices = set(range(len(self.unit_ids)))
        try:
            focus = self.unit_ids.index(self.current_unit_id)
        except ValueError:
            focus = 0 if self.unit_ids else None
        self._unit_selection_anchor = focus
        self._unit_selection_focus = focus
        self._refresh_unit_rows(see_focus=True)
        self._schedule_preview()

    def _clear_units(self) -> None:
        self._selected_unit_indices.clear()
        self._unit_selection_anchor = None
        self._unit_selection_focus = None
        self._refresh_unit_rows()
        self._schedule_preview()

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
                grouping = (
                    f"{self.data.n_x}x{self.data.n_y} to "
                    f"{len(self.snapshot.x_groups)}x{len(self.snapshot.y_groups)}; "
                    f"smooth r={self.snapshot.smooth_radius}"
                )
                if plot.kind in {PlotKind.RF_CARTESIAN, PlotKind.RF_POLAR}:
                    window_context = (
                        f"{format_ms(start_ms)} to {format_ms(end_ms)} ms; "
                        if self.snapshot.rf_subtract_source_range is None else ""
                    )
                    context = (
                        f"{window_context}"
                        f"{self.snapshot.value_mode} ({value_mode_unit(self.snapshot.value_mode)}); "
                        f"{grouping}"
                    )
                elif plot.kind in {
                    PlotKind.DELAY_CARTESIAN,
                    PlotKind.DELAY_POLAR,
                    PlotKind.RGB_CARTESIAN,
                    PlotKind.RGB_POLAR,
                }:
                    context = (
                        f"full timeline {format_ms(full_start_ms)} to "
                        f"{format_ms(full_end_ms)} ms; {grouping}"
                    )
                elif plot.kind is PlotKind.WAVEFORM_LOCAL_AVERAGE:
                    context = (
                        "best + nearest 4; "
                        f"{WAVEFORM_CHANNEL_MODE_LABELS.get(self.snapshot.waveform_channel_mode, self.snapshot.waveform_channel_mode)}; "
                        "baseline ≤ -0.25 ms"
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
        return (
            unit_ids,
            tuple(
                (page.name, tuple(plot.kind.value for plot in page.plots))
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
            scale = (
                self._base_data_provider.shared_rf_bounds(unit_ids, cancelled)
                if has_rf
                else None
            )
            waveform_limit = (
                self._base_data_provider.shared_waveform_amplitude_limit(
                    unit_ids, cancelled
                )
                if has_waveform
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

    def _preview_request(self) -> tuple[tuple[int, ...], tuple[ExportPage, ...], int, int, int]:
        unit_ids = self._selected_unit_ids() or (self.current_unit_id,)
        pages = self._export_pages()
        page_index = self._selected_page_index()
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
        self.preview_status.configure(text="Preparing preview and provenance…")
        cancel_event = threading.Event()
        with self._preview_futures_lock:
            self._preview_cancel_events[generation] = cancel_event

        def cancelled() -> bool:
            return self._preview_shutdown.is_set() or cancel_event.is_set()

        def worker() -> tuple[int, int, object]:
            pages, metadata, provider = self._freeze_context(
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
                page_index,
                data_provider=provider,
            )
            if cancelled():
                image.close()
                raise RuntimeError("Preview superseded by a newer recipe")
            image.thumbnail((width, height))
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
                from PIL import ImageTk

                try:
                    self._preview_photo = ImageTk.PhotoImage(image)
                finally:
                    image.close()
                self.preview_label.configure(image=self._preview_photo, text="")
                self.preview_status.configure(
                    text=(
                        f"Preview: unit {unit_id}, page {page_index + 1} "
                        "· same renderer · provenance verified"
                    )
                )
        # A stale result may arrive before the latest worker. Keep polling until
        # the current generation has either rendered or produced an error.
        with self._preview_futures_lock:
            preview_inflight = bool(self._preview_futures)
        if preview_inflight or not self._preview_queue.empty():
            self._schedule_preview_poll()

    def _show_preview_error(self, exc: Exception) -> None:
        self._preview_photo = None
        self.preview_label.configure(image="", text=f"Preview unavailable\n{exc}")
        self.preview_status.configure(
            text=(
                "Export will re-verify this source and fail safely until it is "
                "reopened or the page recipe is fixed."
            )
        )

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
        self.export_status.configure(text="Verifying provenance and freezing export plan…")

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
        self.viewer.__dict__.pop("_figure_export_window", None)
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
