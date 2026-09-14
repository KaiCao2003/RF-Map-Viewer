#!/usr/bin/env python3
"""Native Python/Tk RF viewer and command-line entry point."""

from __future__ import annotations

import argparse
import csv
import json
import math
import multiprocessing
import os
import queue
import sys
import threading
import traceback
import webbrowser
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping, Sequence
import numpy as np
from rfmapping_viewer.figure_export import (
    ExportPage,
    ExportPlan,
    FigureFormat,
    PlotKind,
    PlotSpec,
    export_figures,
)

from rfmapping_viewer.companions import (
    ProbeChannel,
    ProbeGeometry,
    SpatialRegion,
    TuningCurveData,
    center_tuning_curve_on_zero,
    discover_probe_geometry,
    discover_tuning_curve_path,
    head_direction_unit_vector,
    probe_name_for_json,
    processed_tuning_curve,
    tuning_rate_peak,
)
from rfmapping_viewer.constants import (
    APP_DISPLAY_VERSION,
    APP_EDITION,
    APP_VERSION,
    ASYNC_DOCUMENT_LOAD_BYTES,
    AxisGroup,
    CellRef,
    DEFAULT_HD_DISPLAY_BINS,
    DEFAULT_HD_SMOOTH_SIGMA,
    DEFAULT_TUNING_CURVE_SESSION,
    HD_RAW_BIN_COUNT,
    INNER_BLANK_ROWS,
    PAIR_SYNC_ALL_FIELDS,
    PALETTES,
    POLAR_PAD_ROWS,
    POLAR_RADIUS_MODES,
    PROBE_POSITION_FILETYPES,
    RF_DOCUMENT_FILETYPES,
    SINGLETON_Y_REFERENCE_COLUMNS,
    SINGLETON_Y_REFERENCE_ROWS,
    STARTUP_EVENT_WAIT_MS,
    TUNING_CURVE_FILETYPES,
    TUNING_PLOT_MODES,
    VALUE_MODES,
    VALUE_MODE_COUNT,
    VALUE_MODE_RATE,
    WAVEFORM_CHANNEL_MODE_LABELS,
)
from rfmapping_viewer.display import (
    PreparedSpatialMatrix,
    _nullable_array_list,
    axis_groups_for_target,
    delay_color,
    display_group_index_for_source_bin,
    format_ms,
    format_pos,
    format_response_value,
    hex_color,
    matrix_atlas_ppm_data,
    nonnegative_response_range,
    palette_color,
    palette_response_range,
    physical_time_groups,
    polar_matrix_atlas_ppm_data,
    polar_ring_span,
    reduce_matrix_xy,
    rgb_response_color,
    smooth_matrix,
    spatial_grid_dimensions,
    subtract_response_matrices,
    timeline_bin_index,
    timeline_chart_points,
    timeline_position_fraction,
    timeline_response_high,
    timeline_scroll_offset,
    timeline_scroll_progress,
    value_mode_slug,
    value_mode_suffix,
    value_mode_unit,
    waveform_color,
)
from rfmapping_viewer.export_inputs import (
    FrozenFileIdentity,
    _active_export_jobs,
    _atomic_write_csv,
    _shutdown_export_executor,
)
from rfmapping_viewer.figure_composer import FigureExportWindow
from rfmapping_viewer.paths import (
    _resolve_existing_file,
    discover_json_files,
    document_kind,
    safe_mtime,
    startup_file_dialog_directory,
    support_documentation_path,
)
from rfmapping_viewer.rf_dataset import is_indexed_rfmap
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.settings import (
    ViewerSettings,
    load_viewer_settings,
    normalize_hd_bin_count,
    save_viewer_settings,
    viewer_settings_path,
)
from rfmapping_viewer.settings_window import SettingsWindow
from rfmapping_viewer.tk_support import (
    TK_AVAILABLE,
    allow_macos_fullscreen_resize,
    filedialog,
    messagebox,
    tk,
    ttk,
)
from rfmapping_viewer.viewer_state import ViewerSyncState, WaveformLoadResult


class RFMViewer(tk.Toplevel):
    def __init__(
        self,
        data: RFMappingData | None = None,
        *,
        startup_path: Path | None = None,
        master: tk.Misc | None = None,
    ):
        if data is not None and startup_path is not None:
            raise ValueError("Provide at most one of data or startup_path")

        created_root = master is None
        if created_root:
            master = tk.Tk(useTk=False)
            if sys.platform == "darwin":
                # OpenApplication can arrive inside loadtk(). Stay in Tcl here:
                # entering a Python callback during Tk_Init aborts on macOS.
                master.tk.eval("""
                    namespace eval ::tk::mac {
                        variable rfmapOpenedApplication 0
                        proc OpenApplication {} {
                            set ::tk::mac::rfmapOpenedApplication 1
                        }
                    }
                """)
            master.loadtk()
            master.withdraw()
        self._app_root = master.winfo_toplevel()
        if not hasattr(self._app_root, "_rfm_viewer_windows"):
            self._app_root._rfm_settings_path = viewer_settings_path()
            self._app_root._rfm_settings = load_viewer_settings(
                self._app_root._rfm_settings_path
            )
            self._app_root._rfm_settings_window = None
            self._app_root._rfm_settings_tab = "General"
            self._app_root._rfm_tuning_cache = {}
            self._app_root._rfm_viewer_windows = []
            self._app_root._rfm_pairing_enabled = False
            self._app_root._rfm_pairing_state = None
            self._app_root._rfm_pairing_broadcasting = False
            self._app_root._rfm_quitting = False
        self.settings: ViewerSettings = self._app_root._rfm_settings
        super().__init__(self._app_root)
        self._app_root._rfm_viewer_windows.append(self)
        self._quitting = False
        self._viewer_ready = False
        self._pair_apply_in_progress = False
        self._pair_last_local_state: ViewerSyncState | None = None
        self._unit_cache_after: str | None = None
        self._unit_cache_count = 0
        self._unit_cache_waiting = False
        self._startup_after: str | None = None
        self._startup_poll_after: str | None = None
        self._startup_generation = 0
        self._startup_cancel_event: threading.Event | None = None
        self._startup_result_queue: queue.SimpleQueue[
            tuple[int, Path, RFMappingData | None, Exception | None]
        ] = queue.SimpleQueue()
        self._startup_loading_frame: ttk.Frame | None = None
        self._startup_progress: ttk.Progressbar | None = None
        self._startup_chooser_frame: ttk.Frame | None = None
        self._optional_autoload_after: str | None = None
        self._optional_poll_after: str | None = None
        self._optional_autoload_generation = 0
        self._optional_result_queue: queue.SimpleQueue[dict[str, object]] = (
            queue.SimpleQueue()
        )
        self._waveform_poll_after: str | None = None
        self._waveform_generation = 0
        self._waveform_result_queue: queue.SimpleQueue[WaveformLoadResult] = (
            queue.SimpleQueue()
        )
        self._waveform_worker_running = False
        self._waveform_pending_request: tuple[
            int, RFMappingData, tuple[int, str]
        ] | None = None
        self._redraw_after: str | None = None
        self._focus_after: str | None = None
        self._optional_redraw_after: str | None = None
        self._optional_redraw_dirty: set[str] = set()
        self._pending_open_documents: list[Path] = []
        self._show_settings_when_ready = False
        self._figure_export_window: FigureExportWindow | None = None
        self._base_bin_cache: tuple[Sequence[float], float] | None = None
        self._time_groups_cache: tuple[
            Sequence[float], str, tuple[AxisGroup, ...]
        ] | None = None
        self.title("RF Map Viewer")
        self.withdraw()
        self._install_application_handlers()
        opened_application = False
        if created_root and sys.platform == "darwin":
            opened_application = self.tk.getboolean(
                self.tk.getvar("::tk::mac::rfmapOpenedApplication")
            )
            self.tk.call("unset", "::tk::mac::rfmapOpenedApplication")

        if data is not None:
            self._initialize_viewer(data)
        elif startup_path is not None:
            self._show_startup_loading_shell(startup_path)
            self._startup_after = self.after(
                STARTUP_EVENT_WAIT_MS,
                lambda path=startup_path: self._load_startup_document(path),
            )
        else:
            self._show_startup_chooser_shell()
            # On macOS, OpenApplication opens the chooser; OpenDocument loads
            # the Finder selection, regardless of when that event arrives.
            if sys.platform != "darwin":
                self._startup_after = self.after(200, self._open_startup_file_dialog)
            elif opened_application:
                self._dispatch_macos_open_application()

    def _initialize_viewer(self, data: RFMappingData) -> None:
        self._remove_startup_chooser_shell()
        self._remove_startup_loading_shell()
        self.data = data
        self.settings = self._app_root._rfm_settings
        self.title(f"{data.path.name} — RF Map Viewer")
        self.geometry("1440x900")
        self.minsize(1120, 720)

        self.unit_idx = tk.IntVar(value=0)
        self._selected_unit_id = data.unit_pool[0]
        self._last_supported_unit_id = data.unit_pool[0]
        value_mode = self.settings.rf_value_mode
        if not data.supports_value_mode(value_mode):
            value_mode = VALUE_MODE_RATE
        self.value_mode_var = tk.StringVar(value=value_mode)
        self.bin_var = tk.IntVar(value=0)
        self.range_start_var = tk.IntVar(value=0)
        self.range_end_var = tk.IntVar(value=data.n_bins - 1)
        plot_start_ms, plot_end_ms = self._default_plot_time_bounds_ms()
        self.range_start_ms_var = tk.StringVar(value=format_ms(plot_start_ms))
        self.range_end_ms_var = tk.StringVar(value=format_ms(plot_end_ms))
        self.rf_subtract_var = tk.BooleanVar(value=False)
        self.subtract_start_ms_var = tk.StringVar(value="0")
        self.subtract_end_ms_var = tk.StringVar(value="80")
        self._reset_rf_window_defaults()
        self.flip_y_var = tk.BooleanVar(value=self.settings.rf_flip_y)
        self.palette_var = tk.StringVar(value=self.settings.rf_palette)
        self.polar_radius_var = tk.StringVar(value=self.settings.rf_polar_radius)
        self.polar_layout_var = tk.BooleanVar(value=self.settings.rf_polar_layout)
        self.rgb_mode_var = tk.BooleanVar(value=self.settings.rf_rgb_mode)
        self.pair_windows_var = tk.BooleanVar(
            value=bool(self._app_root._rfm_pairing_enabled)
        )
        self.x_bins_var = tk.IntVar(
            value=min(data.n_x, self.settings.rf_x_bins or data.n_x)
        )
        self.y_bins_var = tk.IntVar(
            value=min(data.n_y, self.settings.rf_y_bins or data.n_y)
        )
        self.time_res_ms_var = tk.StringVar(
            value=format_ms(max(self._base_bin_ms(), self.settings.rf_time_resolution_ms))
        )
        self._last_time_group_count = data.n_bins
        self._last_time_groups = [(index, index) for index in range(data.n_bins)]
        self.smooth_radius_var = tk.IntVar(value=self.settings.rf_smooth_radius)
        self.show_tuning_curve_var = tk.BooleanVar(value=self.settings.show_tuning_curve)
        self.show_waveform_var = tk.BooleanVar(value=self.settings.show_waveform)
        self.show_probe_layout_var = tk.BooleanVar(value=self.settings.show_probe_layout)
        self.tuning_plot_mode_var = tk.StringVar(value=self.settings.tuning_plot_mode)
        self.tuning_layout_var = tk.StringVar(value=self.settings.tuning_layout)
        self.tuning_display_bins_var = tk.IntVar(value=self.settings.tuning_display_bins)
        self.tuning_smoothing_var = tk.BooleanVar(value=self.settings.tuning_smoothing)
        self.tuning_smooth_sigma_var = tk.DoubleVar(value=self.settings.tuning_smooth_sigma)
        self.tuning_compare_scale_var = tk.BooleanVar(
            value=self.settings.tuning_compare_scale
        )
        self.waveform_channel_mode_var = tk.StringVar(
            value=self.settings.waveform_channel_mode
        )
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
        self.probe_geometry: ProbeGeometry | None = None
        self.tuning_curve_data: TuningCurveData | None = None
        self._tuning_curve_error: str | None = None
        self._tuning_curve_candidate: Path | None = None
        self._tuning_processed_cache: tuple[object, ...] | None = None
        self._tuning_scale_cache: tuple[object, ...] | None = None
        self._probe_static_signature: tuple[object, ...] | None = None
        self.waveform_payload: Mapping[str, object] | None = None
        self._waveform_payload_key: tuple[int, str] | None = None
        self._waveform_loading_key: tuple[int, str] | None = None
        self._waveform_error: str | None = None
        self._waveform_error_key: tuple[int, str] | None = None
        self.spatial_region: SpatialRegion | None = None
        self._probe_drag_start: tuple[float, float] | None = None
        self._probe_drag_moved = False
        self._probe_canvas_transform: tuple[float, float, float, float] | None = None
        self.probe_collapsed_var = tk.BooleanVar(value=False)
        self.tuning_collapsed_var = tk.BooleanVar(value=False)
        self.display_expanded_var = tk.BooleanVar(value=False)

        self._build_style()
        self._build_layout()
        self._build_menu()
        self._wire_events()
        self._sync_optional_view_visibility(redraw=False)
        self._sync_json_menu()
        self._sync_unit_combo()
        self._select_tab_key(self.settings.default_viewer_tab)
        self._update_all()
        self._viewer_ready = True
        self._start_unit_cache()
        self._pair_ready_viewer_set_changed(adopt_viewer=self)
        self.deiconify()
        allow_macos_fullscreen_resize(self)
        self.lift()
        self._focus_after = self.after_idle(self._focus_rf_canvas)
        self._schedule_optional_autoload()
        pending_documents = tuple(self._pending_open_documents)
        self._pending_open_documents.clear()
        for pending_path in pending_documents:
            self._open_external_companion(pending_path)
        if self._show_settings_when_ready:
            self._show_settings_when_ready = False
            self.after_idle(self._show_settings)

    def _unit_loading_message(self) -> str | None:
        index = self._local_unit_index(self._selected_unit_id_value())
        archive = self.data.unit_archive
        if archive is None or index is None or archive.is_cached(index):
            return None
        error = archive.error
        if error is not None:
            return f"Unit cache paused: {error[1]} — use Retry below."
        return f"Loading cluster {self._selected_unit_id_value()}…"

    def _stop_unit_cache(self) -> None:
        if self._unit_cache_after is not None:
            self.after_cancel(self._unit_cache_after)
            self._unit_cache_after = None

    def _start_unit_cache(self) -> None:
        self._stop_unit_cache()
        self._unit_cache_count = 0
        self.unit_cache_retry.grid_remove()
        self.export_toolbar_button.state(["!disabled"])
        archive = self.data.unit_archive
        if archive is None:
            self.unit_cache_frame.grid_remove()
            return
        self.unit_cache_frame.grid()
        if archive.cache_count < self.data.n_units:
            self.export_toolbar_button.state(["disabled"])
        self.unit_cache_progress.configure(maximum=self.data.n_units,
                                           value=archive.cache_count)
        self.unit_cache_label.configure(text=f"Caching {archive.cache_count} / {self.data.n_units}")
        # Let Tk present the first plot before starting background reads.
        self._unit_cache_after = self.after_idle(self._begin_unit_cache)

    def _begin_unit_cache(self) -> None:
        self._unit_cache_after = None
        if self._quitting:
            return
        self.data.unit_archive.start_preload()
        self._poll_unit_cache()

    def _retry_unit_cache(self) -> None:
        self.data.unit_archive.start_preload(retry=True)
        self.unit_cache_retry.grid_remove()
        self._stop_unit_cache()
        self._poll_unit_cache()

    def _poll_unit_cache(self) -> None:
        self._unit_cache_after = None
        if self._quitting:
            return
        archive = self.data.unit_archive
        count = archive.cache_count
        error = archive.error
        self.unit_cache_progress.configure(value=count)
        self.unit_cache_label.configure(text=(
            f"Cache paused ({count} / {self.data.n_units})" if error is not None
            else f"Cached {count} / {self.data.n_units}"
        ))
        if error is not None:
            self.unit_cache_retry.grid()
        if count != self._unit_cache_count:
            self._unit_cache_count = count
            selected = self._selected_unit_id_value()
            self._sync_unit_combo()
            self._reconcile_unit_filter_selection()
            if selected != self._selected_unit_id_value() or self._unit_cache_waiting:
                self._unit_cache_waiting = False
                self._update_all()
        if error is not None:
            self.status_label.configure(text=error[1])
            return
        if count == self.data.n_units:
            self.export_toolbar_button.state(["!disabled"])
            self._unit_cache_after = self.after(1000, self._hide_unit_cache)
        else:
            self._unit_cache_after = self.after(100, self._poll_unit_cache)

    def _hide_unit_cache(self) -> None:
        self._unit_cache_after = None
        self.unit_cache_frame.grid_remove()

    def _focus_rf_canvas(self) -> None:
        self._focus_after = None
        try:
            if self.winfo_exists() and self.canvases["rf"].winfo_exists():
                self.canvases["rf"].focus_set()
        except tk.TclError:
            pass

    def _load_startup_document(self, path: Path) -> None:
        self._startup_after = None
        if self._quitting or self._viewer_ready:
            return
        self._startup_generation += 1
        generation = self._startup_generation
        if self._startup_cancel_event is not None:
            self._startup_cancel_event.set()
        cancel_event = threading.Event()
        self._startup_cancel_event = cancel_event
        path = Path(path).expanduser()
        self._remove_startup_chooser_shell()
        self._show_startup_loading_shell(path)

        def decode_document() -> None:
            try:
                if path.stat().st_size >= ASYNC_DOCUMENT_LOAD_BYTES:
                    data = RFMappingData(
                        path, isolated=True, cancelled=cancel_event.is_set
                    )
                else:
                    data = RFMappingData(path)
            except Exception as exc:
                if not cancel_event.is_set():
                    self._startup_result_queue.put((generation, path, None, exc))
            else:
                if not cancel_event.is_set():
                    self._startup_result_queue.put((generation, path, data, None))
                else:
                    data.close()

        threading.Thread(
            target=decode_document,
            name=f"rf-map-load-{generation}",
            daemon=True,
        ).start()
        self._schedule_startup_result_poll()

    def _show_startup_loading_shell(self, path: Path) -> None:
        if self._startup_loading_frame is None:
            self.geometry("560x190")
            self.minsize(480, 170)
            frame = ttk.Frame(self, padding=24)
            frame.pack(fill="both", expand=True)
            frame.columnconfigure(0, weight=1)
            ttk.Label(
                frame,
                text="Opening RF mapping data",
                font=("TkDefaultFont", 15, "bold"),
            ).grid(row=0, column=0, sticky="w")
            self._startup_path_label = ttk.Label(
                frame,
                text="",
                foreground="#667085",
                wraplength=500,
            )
            self._startup_path_label.grid(row=1, column=0, sticky="ew", pady=(8, 16))
            progress = ttk.Progressbar(frame, mode="indeterminate")
            progress.grid(row=2, column=0, sticky="ew")
            progress.start(12)
            ttk.Label(
                frame,
                text="Decoding and validating counts off the interface thread…",
                foreground="#667085",
            ).grid(row=3, column=0, sticky="w", pady=(10, 0))
            self._startup_loading_frame = frame
            self._startup_progress = progress
        self._startup_path_label.configure(text=path.name)
        self.title(f"Opening {path.name} — RF Map Viewer")
        self.deiconify()
        self.lift()

    def _show_startup_chooser_shell(self) -> None:
        """Show the no-document landing view behind the native file chooser."""

        if self._startup_chooser_frame is not None:
            return
        self.geometry("560x230")
        self.minsize(480, 210)
        frame = ttk.Frame(self, padding=28)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        ttk.Label(
            frame,
            text="Open RF mapping data",
            font=("TkDefaultFont", 15, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            text=(
                "Choose a current .rfmap or JSON result. The viewer never "
                "loads sample data when opened without a document."
            ),
            foreground="#667085",
            wraplength=500,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(10, 18))
        ttk.Button(
            frame,
            text="Open RF Map…",
            command=self._open_json,
        ).grid(row=2, column=0, sticky="w")
        self._startup_chooser_frame = frame
        self.title("RF Map Viewer")
        self.deiconify()
        self.lift()

    def _remove_startup_chooser_shell(self) -> None:
        if self._startup_chooser_frame is not None:
            self._startup_chooser_frame.destroy()
            self._startup_chooser_frame = None

    def _open_startup_file_dialog(self) -> None:
        self._startup_after = None
        if not self._quitting and self._startup_chooser_frame is not None:
            self._open_json()

    def _remove_startup_loading_shell(self) -> None:
        if self._startup_progress is not None:
            self._startup_progress.stop()
            self._startup_progress = None
        if self._startup_loading_frame is not None:
            self._startup_loading_frame.destroy()
            self._startup_loading_frame = None

    def _schedule_startup_result_poll(self) -> None:
        if self._startup_poll_after is None:
            self._startup_poll_after = self.after(30, self._poll_startup_result)

    def _poll_startup_result(self) -> None:
        self._startup_poll_after = None
        matching: tuple[int, Path, RFMappingData | None, Exception | None] | None = None
        while True:
            try:
                candidate = self._startup_result_queue.get_nowait()
            except queue.Empty:
                break
            if candidate[0] == self._startup_generation:
                matching = candidate
            elif candidate[2] is not None:
                candidate[2].close()
        if matching is None:
            if not self._quitting and not self._viewer_ready:
                self._schedule_startup_result_poll()
            return
        _generation, _path, data, error = matching
        if error is not None:
            messagebox.showerror("Could not open RF map", str(error), parent=self)
            self._quit_application()
            return
        assert data is not None
        self._initialize_viewer(data)

    def _cancel_startup_callback(self) -> None:
        self._startup_generation += 1
        cancel_event = self._startup_cancel_event
        if cancel_event is not None:
            cancel_event.set()
            self._startup_cancel_event = None
        if self._startup_after is None:
            pass
        else:
            try:
                self.after_cancel(self._startup_after)
            except tk.TclError:
                pass
            self._startup_after = None
        if self._startup_poll_after is not None:
            try:
                self.after_cancel(self._startup_poll_after)
            except tk.TclError:
                pass
            self._startup_poll_after = None

    def destroy(self) -> None:
        if (
            not self._app_root._rfm_quitting
            and _active_export_jobs(self._app_root, self)
        ):
            messagebox.showinfo(
                "Export is running",
                "Wait for this window's export to finish before closing it.",
                parent=self,
            )
            return
        self._quitting = True
        self._stop_unit_cache()
        if self._viewer_ready:
            self.data.close()
        self._cancel_startup_callback()
        if self._optional_autoload_after is not None:
            try:
                self.after_cancel(self._optional_autoload_after)
            except tk.TclError:
                pass
            self._optional_autoload_after = None
        self._optional_autoload_generation += 1
        if self._optional_poll_after is not None:
            try:
                self.after_cancel(self._optional_poll_after)
            except tk.TclError:
                pass
            self._optional_poll_after = None
        self._waveform_generation += 1
        self._waveform_pending_request = None
        if self._waveform_poll_after is not None:
            try:
                self.after_cancel(self._waveform_poll_after)
            except tk.TclError:
                pass
            self._waveform_poll_after = None
        if self._redraw_after is not None:
            try:
                self.after_cancel(self._redraw_after)
            except tk.TclError:
                pass
            self._redraw_after = None
        if self._optional_redraw_after is not None:
            try:
                self.after_cancel(self._optional_redraw_after)
            except tk.TclError:
                pass
            self._optional_redraw_after = None
        if self._focus_after is not None:
            try:
                self.after_cancel(self._focus_after)
            except tk.TclError:
                pass
            self._focus_after = None
        windows = self._app_root._rfm_viewer_windows
        if self in windows:
            windows.remove(self)
        if not self._app_root._rfm_quitting:
            self._pair_ready_viewer_set_changed()
        try:
            super().destroy()
        except tk.TclError:
            return
        if self._app_root._rfm_quitting:
            return
        if windows:
            windows[-1]._install_application_handlers()
            return
        try:
            self._app_root._rfm_quitting = True
            _shutdown_export_executor(self._app_root)
            self._app_root.destroy()
        except tk.TclError:
            pass

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if sys.platform == "darwin" and "aqua" in style.theme_names():
            style.theme_use("aqua")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f5f5f7")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Sidebar.TFrame", background="#eef0f4")
        style.configure("Toolbar.TFrame", background="#f5f5f7")
        style.configure("TLabel", background="#f5f5f7", foreground="#1d1d1f")
        style.configure("Panel.TLabel", background="#ffffff", foreground="#1d1d1f")
        style.configure("Sidebar.TLabel", background="#eef0f4", foreground="#1d1d1f")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#6e6e73")
        style.configure("SidebarMuted.TLabel", background="#eef0f4", foreground="#6e6e73")
        style.configure(
            "Section.TLabel",
            background="#eef0f4",
            foreground="#6e6e73",
            font=("TkDefaultFont", 10, "bold"),
        )
        style.configure(
            "Title.TLabel",
            background="#ffffff",
            foreground="#1d1d1f",
            font=("TkDefaultFont", 13, "bold"),
        )
        style.configure(
            "SidebarTitle.TLabel",
            background="#eef0f4",
            foreground="#1d1d1f",
            font=("TkDefaultFont", 12, "bold"),
        )
        style.configure(
            "Value.TLabel",
            background="#ffffff",
            foreground="#1d1d1f",
            font=("TkDefaultFont", 11, "bold"),
        )
        style.configure(
            "Status.TLabel",
            background="#f5f5f7",
            foreground="#6e6e73",
            font=("TkDefaultFont", 10),
        )
        style.configure(
            "HDClass1.TLabel",
            background="#fff3c4",
            foreground="#805b00",
            font=("TkDefaultFont", 10, "bold"),
            padding=(6, 2),
        )
        style.configure(
            "HDClass2.TLabel",
            background="#dff5e8",
            foreground="#08783f",
            font=("TkDefaultFont", 10, "bold"),
            padding=(6, 2),
        )
        style.configure("TButton", padding=(7, 4))
        style.configure("TNotebook", background="#ffffff", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 7))
        self._pane_icons = {
            placement: self._make_pane_icon(placement)
            for placement in ("leading", "trailing", "bottom")
        }

    def _make_pane_icon(self, placement: str) -> tk.PhotoImage:
        """Draw a compact sidebar/split-pane icon without font glyph arrows."""

        image = tk.PhotoImage(master=self, width=18, height=18)
        outline = "#667085"
        panel = "#98a2b3"
        interior = "#f8fafc"
        image.put(outline, to=(2, 3, 16, 15))
        image.put(interior, to=(3, 4, 15, 14))
        if placement == "leading":
            image.put(panel, to=(3, 4, 7, 14))
            image.put(outline, to=(7, 4, 8, 14))
        elif placement == "trailing":
            image.put(panel, to=(11, 4, 15, 14))
            image.put(outline, to=(10, 4, 11, 14))
        elif placement == "bottom":
            image.put(panel, to=(3, 11, 15, 14))
            image.put(outline, to=(3, 10, 15, 11))
        else:
            raise ValueError(f"Unsupported pane icon placement: {placement}")
        return image

    def _build_menu(self) -> None:
        menu = tk.Menu(self)

        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(
            label="Open RF Map in New Window…",
            accelerator="⌘O" if sys.platform == "darwin" else "Ctrl+O",
            command=self._open_json,
        )
        self._discovered_json_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(
            label="Open Discovered RF Map",
            menu=self._discovered_json_menu,
        )
        file_menu.add_command(
            label="Attach Probe Geometry…",
            command=self._attach_probe_geometry,
        )
        file_menu.add_command(
            label="Export Figures…",
            accelerator="⌘E" if sys.platform == "darwin" else "Ctrl+E",
            command=self._open_figure_exporter,
        )
        file_menu.add_command(
            label="Attach Tuning Curves…",
            command=self._attach_tuning_curve,
        )
        file_menu.add_separator()
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
        self._file_menu = file_menu

        navigate_menu = tk.Menu(menu, tearoff=False)
        navigate_menu.add_command(label="Previous Unit", accelerator="←  or  [", command=lambda: self._step_unit(-1))
        navigate_menu.add_command(label="Next Unit", accelerator="→  or  ]", command=lambda: self._step_unit(1))
        navigate_menu.add_separator()
        navigate_menu.add_command(label="Previous Timeline Bin", accelerator="↑", command=lambda: self._step_timeline_bin(-1))
        navigate_menu.add_command(label="Next Timeline Bin", accelerator="↓", command=lambda: self._step_timeline_bin(1))
        navigate_menu.add_command(
            label="Decrease Time Resolution",
            accelerator="⇧,",
            command=lambda: self._step_time_resolution(1.0),
        )
        navigate_menu.add_command(
            label="Increase Time Resolution",
            accelerator="⇧.",
            command=lambda: self._step_time_resolution(-1.0),
        )
        navigate_menu.add_separator()
        navigate_menu.add_command(
            label="Show Full Timeline Range",
            accelerator="Esc",
            command=self._clear_timeline_selection,
        )
        menu.add_cascade(label="Navigate", menu=navigate_menu)
        self._navigate_menu = navigate_menu

        view_menu = tk.Menu(menu, tearoff=False)
        for tab_index, title in enumerate(("RF", "Delay / RGB", "Timeline")):
            view_menu.add_command(
                label=title,
                accelerator=str(tab_index + 1),
                command=lambda index=tab_index: self._select_tab(index),
            )
        view_menu.add_separator()
        view_menu.add_command(label="Invert Y", accelerator="F", command=self._toggle_flip_y)
        view_menu.add_command(
            label="Toggle Polar Layout",
            accelerator="P",
            command=self._toggle_polar_layout,
        )
        view_menu.add_command(
            label="Cycle Palette",
            accelerator="⇧P",
            command=self._cycle_palette,
        )
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="Subtract RF Windows (A − B)",
            accelerator="−",
            variable=self.rf_subtract_var,
            command=self._on_rf_mode_changed,
        )
        view_menu.add_command(
            label="Show Display Options",
            accelerator="D",
            command=self._toggle_display_controls,
        )
        self._display_options_menu_index = view_menu.index("end")
        view_menu.add_command(
            label="Show Filtered Units",
            accelerator="⌘⇧.",
            command=self._toggle_zero_bin_filter,
        )
        self._unit_filter_menu_index = view_menu.index("end")
        self._view_menu = view_menu
        if sys.platform != "darwin":
            view_menu.add_separator()
            view_menu.add_command(
                label="Settings…",
                accelerator="Ctrl+,",
                command=self._show_settings,
            )
        menu.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menu, name="help", tearoff=False)
        help_menu.add_command(label="Keyboard Shortcuts", accelerator="?", command=self._show_shortcuts)
        help_menu.add_separator()
        help_menu.add_command(
            label="Support Documentation",
            command=self._open_support_documentation,
        )
        menu.add_cascade(label="Help", menu=help_menu)
        self._help_menu = help_menu
        self.configure(menu=menu)
        self._menu = menu

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 10))
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)
        self.sidebar_panel = sidebar
        self.sidebar_frame = sidebar
        self.sidebar_collapsed_rail = ttk.Frame(
            self, style="Sidebar.TFrame", padding=(4, 10)
        )
        ttk.Button(
            self.sidebar_collapsed_rail,
            image=self._pane_icons["leading"],
            text="Show sidebar",
            width=2,
            command=self._toggle_probe_collapsed,
        ).grid(row=0, column=0, sticky="n")
        self.sidebar_collapsed_rail.grid(row=0, column=0, sticky="ns")
        self.sidebar_collapsed_rail.grid_remove()

        main = ttk.Frame(self, style="Panel.TFrame")
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        self._build_sidebar(sidebar)
        self._build_main(main)
        self._build_waveform_zoom_overlay()

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        row = 0
        ttk.Label(parent, text="Windows", style="Section.TLabel").grid(
            row=row, column=0, sticky="w", pady=(0, 5)
        )
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
        self.pair_windows_toggle.grid(row=row, column=0, sticky="w", pady=(0, 5))
        row += 1
        self.pair_status_label = ttk.Label(
            parent,
            text="Open another loaded viewer window to enable sync.",
            style="SidebarMuted.TLabel",
            wraplength=220,
            justify="left",
        )
        self.pair_status_label.grid(row=row, column=0, sticky="ew", pady=(2, 8))
        self.pair_status_label.grid_remove()
        row += 1

        self.probe_section = ttk.Frame(parent, style="Sidebar.TFrame")
        self.probe_section.grid(row=row, column=0, sticky="nsew", pady=(10, 0))
        self.probe_section.columnconfigure(0, weight=1)
        self.probe_section.rowconfigure(1, weight=1)
        self._probe_section_row = row
        parent.rowconfigure(row, weight=1)

        probe_header = ttk.Frame(self.probe_section, style="Sidebar.TFrame")
        probe_header.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        probe_header.columnconfigure(1, weight=1)
        self.probe_fold_button = ttk.Button(
            probe_header,
            image=self._pane_icons["leading"],
            text="Hide sidebar",
            width=2,
            command=self._toggle_probe_collapsed,
        )
        self.probe_fold_button.grid(row=0, column=0, sticky="w", padx=(0, 5))
        ttk.Label(probe_header, text="Probe", style="Section.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        self.clear_spatial_button = ttk.Button(
            probe_header,
            text="Clear",
            width=6,
            command=self._clear_spatial_filter,
        )
        self.clear_spatial_button.grid(row=0, column=2, sticky="e")
        row += 1

        self.probe_canvas = tk.Canvas(
            self.probe_section,
            width=240,
            height=330,
            background="#ffffff",
            highlightthickness=1,
            highlightbackground="#d7d9de",
        )
        self.probe_canvas.grid(row=1, column=0, sticky="nsew")
        self.probe_attach_button = ttk.Button(
            self.probe_canvas,
            text="Choose positions.probe or .csv…",
            command=self._attach_probe_geometry,
        )
        self.spatial_status_label = ttk.Label(
            self.probe_section,
            text="",
            style="SidebarMuted.TLabel",
            wraplength=240,
            justify="left",
        )
        self.spatial_status_label.grid(row=2, column=0, sticky="ew", pady=(5, 10))
        row += 1

        self.waveform_host = ttk.Frame(
            parent,
            style="Sidebar.TFrame",
        )
        self.waveform_host.grid(row=row, column=0, sticky="nsew", pady=(8, 0))
        self.waveform_host.columnconfigure(0, weight=1)
        self.waveform_host.rowconfigure(0, weight=1)
        self._waveform_section_row = row

    def _build_main(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent, style="Toolbar.TFrame", padding=(10, 7))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(4, weight=1)

        self.previous_unit_button = ttk.Button(
            toolbar,
            text="‹",
            width=3,
            command=lambda: self._step_unit(-1),
        )
        self.previous_unit_button.grid(row=0, column=0, padx=(0, 4))
        self.unit_combo = ttk.Combobox(toolbar, state="readonly", width=27)
        self.unit_combo.grid(row=0, column=1, sticky="w")
        self.next_unit_button = ttk.Button(
            toolbar,
            text="›",
            width=3,
            command=lambda: self._step_unit(1),
        )
        self.next_unit_button.grid(row=0, column=2, padx=(4, 0))
        ttk.Separator(toolbar, orient="vertical").grid(
            row=0, column=3, sticky="ns", padx=10
        )

        # Kept as a data-bearing widget for the update path; the unit picker
        # already exposes the same context, so repeating it would add chrome.
        self.header_label = ttk.Label(toolbar, text="", style="Status.TLabel")
        self.open_toolbar_button = ttk.Button(
            toolbar,
            text="Open…",
            command=self._open_json,
        )
        self.open_toolbar_button.grid(row=0, column=5, padx=(8, 4))
        self.export_toolbar_button = ttk.Button(
            toolbar,
            text="Figures…",
            command=self._open_figure_exporter,
        )
        self.export_toolbar_button.grid(row=0, column=6)

        self._build_plot_controls(parent)

        self.notebook = ttk.Notebook(parent)
        self.notebook.grid(row=2, column=0, sticky="nsew")

        status_bar = ttk.Frame(parent)
        status_bar.grid(row=3, column=0, sticky="ew")
        status_bar.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(
            status_bar,
            text="",
            style="Status.TLabel",
            anchor="w",
            padding=(10, 4),
        )
        self.status_label.grid(row=0, column=0, sticky="ew")
        self.unit_cache_frame = ttk.Frame(status_bar, padding=(8, 2))
        self.unit_cache_frame.grid(row=0, column=1, sticky="e")
        self.unit_cache_label = ttk.Label(self.unit_cache_frame, style="Status.TLabel")
        self.unit_cache_label.grid(row=0, column=0, padx=(0, 8))
        self.unit_cache_progress = ttk.Progressbar(
            self.unit_cache_frame, mode="determinate", length=100,
        )
        self.unit_cache_progress.grid(row=0, column=1)
        self.unit_cache_retry = ttk.Button(
            self.unit_cache_frame, text="Retry", command=self._retry_unit_cache,
        )
        self.unit_cache_retry.grid(row=0, column=2, padx=(6, 0))
        self.unit_cache_retry.grid_remove()
        self.unit_cache_frame.grid_remove()

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
            if key == "rf":
                self.rf_tab_frame = frame
                self.rf_split_container = ttk.Frame(frame)
                self.rf_split_container.grid(row=0, column=0, sticky="nsew")
                self._rf_split_responsive_stacked = False
                self.rf_split_container.bind(
                    "<Configure>",
                    self._on_rf_split_configure,
                    add="+",
                )
                self.rf_map_pane = ttk.Frame(
                    self.rf_split_container,
                    style="Panel.TFrame",
                )
                self.rf_map_pane.columnconfigure(0, weight=1)
                self.rf_map_pane.rowconfigure(1, weight=1)
                rf_header = ttk.Frame(
                    self.rf_map_pane,
                    style="Panel.TFrame",
                    padding=(12, 9),
                )
                rf_header.grid(row=0, column=0, sticky="ew")
                rf_header.columnconfigure(0, weight=1)
                ttk.Label(
                    rf_header,
                    text="RF Map",
                    style="Title.TLabel",
                ).grid(row=0, column=0, sticky="w")
                self.rf_map_subtitle_label = ttk.Label(
                    rf_header,
                    text="",
                    style="Muted.TLabel",
                    font=("TkDefaultFont", 10),
                )
                self.rf_map_subtitle_label.grid(
                    row=1, column=0, sticky="w", pady=(2, 0)
                )
                canvas = tk.Canvas(
                    self.rf_map_pane,
                    background="#ffffff",
                    highlightthickness=0,
                )
                canvas.grid(row=1, column=0, sticky="nsew")

                self.tuning_curve_pane = ttk.Frame(
                    self.rf_split_container,
                    style="Panel.TFrame",
                )
                self.tuning_curve_pane.columnconfigure(0, weight=1)
                self.tuning_curve_section = ttk.Frame(
                    self.tuning_curve_pane,
                    style="Panel.TFrame",
                )
                self.tuning_curve_section.columnconfigure(0, weight=1)
                self.tuning_curve_section.rowconfigure(1, weight=1)
                tuning_header = ttk.Frame(
                    self.tuning_curve_section,
                    style="Panel.TFrame",
                    padding=(12, 9),
                )
                tuning_header.grid(row=0, column=0, sticky="ew")
                tuning_header.columnconfigure(0, weight=1)
                ttk.Label(
                    tuning_header,
                    text="HD Tuning Curve",
                    style="Title.TLabel",
                ).grid(row=0, column=0, sticky="w")
                self.tuning_cluster_label = ttk.Label(
                    tuning_header,
                    text="",
                    style="Muted.TLabel",
                    font=("TkDefaultFont", 10),
                )
                self.tuning_cluster_label.grid(
                    row=1, column=0, sticky="w", pady=(2, 0)
                )
                self.tuning_hd_class_label = ttk.Label(
                    tuning_header,
                    text="",
                    style="Panel.TLabel",
                    width=2,
                    anchor="center",
                )
                self.tuning_hd_class_label.grid(
                    row=0,
                    column=1,
                    sticky="e",
                    padx=(6, 4),
                )
                self.tuning_hd_class_label.grid_remove()
                self.tuning_provenance_button = ttk.Button(
                    tuning_header,
                    text="Info",
                    width=4,
                    command=self._show_tuning_provenance,
                )
                self.tuning_provenance_button.grid(
                    row=0,
                    column=2,
                    sticky="e",
                    padx=(4, 4),
                )
                self.tuning_provenance_button.grid_remove()
                self.tuning_fold_button = ttk.Button(
                    tuning_header,
                    image=self._pane_icons["trailing"],
                    text="Collapse HD tuning curve",
                    width=2,
                    command=self._toggle_tuning_collapsed,
                )
                self.tuning_fold_button.grid(row=0, column=3, sticky="e")
                self.tuning_curve_canvas = tk.Canvas(
                    self.tuning_curve_section,
                    background="#ffffff",
                    highlightthickness=0,
                )
                self.tuning_curve_canvas.grid(row=1, column=0, sticky="nsew")
                self.tuning_attach_button = ttk.Button(
                    self.tuning_curve_canvas,
                    text="Choose tuning_curves.tc or .json…",
                    command=self._attach_tuning_curve,
                )
                self.tuning_curve_status_label = ttk.Label(
                    self.tuning_curve_section,
                    text="",
                    style="Muted.TLabel",
                    wraplength=360,
                    justify="left",
                )

                self.unit_info_pane = ttk.Frame(
                    self.rf_split_container,
                    style="Panel.TFrame",
                )
                self.unit_info_pane.columnconfigure(0, weight=1)
                ttk.Separator(self.unit_info_pane, orient="horizontal").grid(
                    row=0, column=0, sticky="ew"
                )
                unit_info_header = ttk.Frame(
                    self.unit_info_pane,
                    style="Panel.TFrame",
                    padding=(12, 8),
                )
                unit_info_header.grid(row=1, column=0, sticky="ew")
                ttk.Label(
                    unit_info_header,
                    text="Unit Info",
                    style="Title.TLabel",
                ).grid(row=0, column=0, sticky="w")
                self.unit_stats_label = ttk.Label(
                    self.unit_info_pane,
                    text="",
                    style="Muted.TLabel",
                    font=("TkFixedFont", 9),
                    wraplength=330,
                    justify="left",
                )
                self.unit_stats_label.grid(
                    row=2,
                    column=0,
                    sticky="ew",
                    padx=12,
                    pady=(0, 8),
                )
                ttk.Separator(self.unit_info_pane, orient="horizontal").grid(
                    row=3, column=0, sticky="ew"
                )
                spike_time_header = ttk.Frame(
                    self.unit_info_pane,
                    style="Panel.TFrame",
                    padding=(12, 8),
                )
                spike_time_header.grid(row=4, column=0, sticky="ew")
                self.spike_time_title_label = ttk.Label(
                    spike_time_header,
                    text="Spike Time",
                    style="Title.TLabel",
                )
                self.spike_time_title_label.grid(row=0, column=0, sticky="w")
                self.cell_label = ttk.Label(
                    self.unit_info_pane,
                    text="",
                    style="Muted.TLabel",
                    font=("TkFixedFont", 9),
                    wraplength=330,
                    justify="left",
                )
                self.cell_label.grid(
                    row=5,
                    column=0,
                    sticky="ew",
                    padx=12,
                    pady=(0, 10),
                )
                self.unit_info_pane.bind(
                    "<Configure>",
                    self._on_unit_info_pane_configure,
                    add="+",
                )

                self.waveform_pane = ttk.Frame(
                    self.waveform_host,
                    style="Sidebar.TFrame",
                )
                self.waveform_pane.columnconfigure(0, weight=1)
                self.waveform_pane.rowconfigure(1, weight=1)
                waveform_header = ttk.Frame(
                    self.waveform_pane,
                    style="Sidebar.TFrame",
                    padding=(0, 4),
                )
                waveform_header.grid(row=0, column=0, sticky="ew")
                waveform_header.columnconfigure(0, weight=1)
                ttk.Label(
                    waveform_header,
                    text="Local Average Waveform",
                    style="SidebarTitle.TLabel",
                ).grid(row=0, column=0, sticky="w")
                self.waveform_subtitle_label = ttk.Label(
                    waveform_header,
                    text="",
                    style="SidebarMuted.TLabel",
                    font=("TkDefaultFont", 8),
                    wraplength=240,
                    justify="left",
                )
                self.waveform_subtitle_label.grid(
                    row=1, column=0, sticky="w", pady=(1, 0)
                )
                self.waveform_canvas = tk.Canvas(
                    self.waveform_pane,
                    background="#ffffff",
                    highlightthickness=1,
                    highlightbackground="#d7d9de",
                    height=100,
                )
                self.waveform_canvas.grid(row=1, column=0, sticky="nsew")
                self.canvases["waveform"] = self.waveform_canvas
                self.tuning_collapsed_rail = ttk.Frame(
                    self.tuning_curve_pane,
                    style="Panel.TFrame",
                    padding=(4, 6),
                )
                self.tuning_collapsed_rail.columnconfigure(0, weight=1)
                self.tuning_restore_button = ttk.Button(
                    self.tuning_collapsed_rail,
                    image=self._pane_icons["trailing"],
                    text="Restore HD tuning curve",
                    width=2,
                    command=self._toggle_tuning_collapsed,
                )
                self.tuning_restore_button.grid(row=0, column=0, sticky="ne")
                self._sync_auxiliary_sections()
                self._layout_rf_and_tuning()
            else:
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

    def _build_waveform_zoom_overlay(self) -> None:
        """Build the reversible in-window waveform enlargement layer."""

        self._waveform_zoomed = False
        overlay = ttk.Frame(
            self,
            style="Panel.TFrame",
            padding=(24, 18),
        )
        overlay.columnconfigure(0, weight=1)
        overlay.rowconfigure(1, weight=1)

        header = ttk.Frame(overlay, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Local Average Waveform",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.waveform_zoom_subtitle_label = ttk.Label(
            header,
            text="",
            style="Muted.TLabel",
            font=("TkDefaultFont", 10),
            justify="left",
        )
        self.waveform_zoom_subtitle_label.grid(
            row=1,
            column=0,
            sticky="w",
            pady=(2, 0),
        )
        ttk.Label(
            header,
            text="Double-click the waveform or press Esc to return",
            style="Muted.TLabel",
        ).grid(row=0, column=1, sticky="e", padx=(12, 10))
        self.waveform_zoom_done_button = ttk.Button(
            header,
            text="Done",
            command=self._close_waveform_zoom,
        )
        self.waveform_zoom_done_button.grid(
            row=0,
            column=2,
            rowspan=2,
            sticky="e",
        )
        self.waveform_zoom_canvas = tk.Canvas(
            overlay,
            background="#ffffff",
            highlightthickness=1,
            highlightbackground="#d7d9de",
            takefocus=True,
        )
        self.waveform_zoom_canvas.grid(row=1, column=0, sticky="nsew")
        self.waveform_zoom_overlay = overlay

    def _on_unit_info_pane_configure(self, event: tk.Event) -> None:
        wraplength = max(160, int(event.width) - 24)
        for label in (self.unit_stats_label, self.cell_label):
            if int(float(label.cget("wraplength"))) != wraplength:
                label.configure(wraplength=wraplength)

    def _sync_auxiliary_sections(self) -> None:
        """Place waveform in the sidebar and both inspectors below tuning."""

        if not hasattr(self, "tuning_curve_section"):
            return
        tuning_visible = bool(self.show_tuning_curve_var.get())
        tuning_collapsed = bool(self.tuning_collapsed_var.get())
        waveform_visible = bool(self.show_waveform_var.get())
        self.tuning_curve_section.grid_remove()
        self.tuning_collapsed_rail.grid_remove()
        self.waveform_pane.grid_remove()
        self.waveform_host.grid_remove()
        self.tuning_curve_pane.rowconfigure(0, weight=0, minsize=0)
        self.sidebar_frame.rowconfigure(
            self._waveform_section_row,
            weight=0,
            minsize=0,
        )

        self.tuning_curve_pane.rowconfigure(0, weight=1)
        if tuning_visible and tuning_collapsed:
            self.tuning_collapsed_rail.grid(row=0, column=0, sticky="nsew")
            self.tuning_restore_button.configure(
                image=self._pane_icons["trailing"],
                text="Restore HD tuning curve",
            )
        elif tuning_visible:
            self.tuning_curve_section.grid(row=0, column=0, sticky="nsew")
            self.tuning_fold_button.grid()
        if waveform_visible:
            self.waveform_host.grid()
            self.waveform_pane.grid(row=0, column=0, sticky="nsew")
            self.sidebar_frame.rowconfigure(
                self._waveform_section_row,
                weight=0,
                minsize=180,
            )

    def _layout_rf_and_tuning(self) -> None:
        """Place RF beside the optional tuning and unit-info stack."""

        if not hasattr(self, "rf_split_container"):
            return
        container = self.rf_split_container
        self.rf_map_pane.grid_forget()
        self.tuning_curve_pane.grid_forget()
        self.unit_info_pane.grid_forget()
        for index in range(2):
            container.columnconfigure(index, weight=0, uniform="", minsize=0)
            container.rowconfigure(index, weight=0, uniform="", minsize=0)

        container_width = max(1, int(container.winfo_width()))
        responsive_stacked = self._should_responsively_stack_auxiliary(
            container_width
        )
        self._rf_split_responsive_stacked = responsive_stacked
        stacked = (
            self.show_tuning_curve_var.get()
            and not self.tuning_collapsed_var.get()
            and (
                self.tuning_layout_var.get() == "Stacked"
                or responsive_stacked
            )
        )

        if stacked:
            self.rf_map_pane.grid(
                row=0, column=0, columnspan=2, sticky="nsew"
            )
            self.tuning_curve_pane.grid(
                row=1, column=0, sticky="nsew", pady=(1, 0), padx=(0, 1)
            )
            self.unit_info_pane.grid(
                row=1, column=1, sticky="nsew", pady=(1, 0)
            )
            container.columnconfigure(0, weight=5, uniform="rf-hd-columns")
            container.columnconfigure(
                1,
                weight=2,
                uniform="rf-hd-columns",
                minsize=220,
            )
            container.rowconfigure(0, weight=5, uniform="rf-hd-rows")
            container.rowconfigure(
                1,
                weight=3,
                uniform="rf-hd-rows",
                minsize=260,
            )
            self.tuning_fold_button.configure(
                image=self._pane_icons["bottom"],
                text="Collapse HD tuning curve",
            )
        else:
            self.rf_map_pane.grid(
                row=0, column=0, rowspan=2, sticky="nsew"
            )
            self.tuning_curve_pane.grid(
                row=0, column=1, sticky="nsew", padx=(1, 0)
            )
            self.unit_info_pane.grid(
                row=1, column=1, sticky="nsew", padx=(1, 0)
            )
            # At the minimum supported window width the two companion plots
            # need a little more horizontal room, while retaining the usual
            # 5:2 split on larger displays and for a single companion.
            auxiliary_weight = self._responsive_auxiliary_column_weight(
                container_width
            )
            self._rf_split_auxiliary_weight = auxiliary_weight
            container.columnconfigure(0, weight=5, uniform="rf-hd-columns")
            container.columnconfigure(
                1,
                weight=auxiliary_weight,
                uniform="rf-hd-columns",
                minsize=220,
            )
            container.rowconfigure(0, weight=1)
            container.rowconfigure(1, weight=0, minsize=230)
            self.tuning_fold_button.configure(
                image=self._pane_icons["trailing"],
                text="Collapse HD tuning curve",
            )

    def _should_responsively_stack_auxiliary(self, width: int) -> bool:
        # Unit Info owns the bottom-right position. Keep the right-side stack
        # beside RF unless the user explicitly selected Stacked in Settings.
        return False

    def _responsive_auxiliary_column_weight(self, width: int) -> int:
        if (
            not self.show_tuning_curve_var.get()
            or self.tuning_collapsed_var.get()
        ):
            return 1
        if (
            self.show_tuning_curve_var.get()
            and int(width) < 1050
        ):
            return 3
        return 2

    def _on_rf_split_configure(self, event: tk.Event) -> None:
        # At narrow window widths a side-by-side HD pane would be smaller than
        # its scientific axes. Switch arrangement without changing the saved
        # user preference, then restore it when space returns.
        responsive_stacked = self._should_responsively_stack_auxiliary(
            int(event.width)
        )
        auxiliary_weight = self._responsive_auxiliary_column_weight(
            int(event.width)
        )
        if (
            responsive_stacked
            == getattr(self, "_rf_split_responsive_stacked", False)
            and auxiliary_weight
            == getattr(self, "_rf_split_auxiliary_weight", 2)
        ):
            return
        self._rf_split_responsive_stacked = responsive_stacked
        self._rf_split_auxiliary_weight = auxiliary_weight
        self._layout_rf_and_tuning()

    def _toggle_probe_collapsed(self) -> None:
        self.probe_collapsed_var.set(not self.probe_collapsed_var.get())
        self._sync_probe_collapsed_state()

    def _sync_probe_collapsed_state(self, *, schedule_redraw: bool = True) -> None:
        if not hasattr(self, "probe_canvas"):
            return
        collapsed = bool(self.probe_collapsed_var.get())
        self.probe_fold_button.configure(
            image=self._pane_icons["leading"],
            text="Hide sidebar",
        )
        if collapsed:
            self.sidebar_panel.grid_remove()
            self.sidebar_collapsed_rail.grid()
        else:
            self.sidebar_collapsed_rail.grid_remove()
            self.sidebar_panel.grid()
            if schedule_redraw and self._viewer_ready:
                self._schedule_optional_redraw("probe")

    def _toggle_tuning_collapsed(self) -> None:
        self.tuning_collapsed_var.set(not self.tuning_collapsed_var.get())
        self._sync_tuning_collapsed_state()

    def _sync_tuning_collapsed_state(self, *, schedule_redraw: bool = True) -> None:
        if not hasattr(self, "tuning_curve_canvas"):
            return
        collapsed = bool(self.tuning_collapsed_var.get())
        self._sync_auxiliary_sections()
        self._layout_rf_and_tuning()
        if (
            not collapsed
            and schedule_redraw
            and self._viewer_ready
        ):
            if self.show_tuning_curve_var.get():
                self._schedule_optional_redraw("tuning")

    def _build_plot_controls(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent, style="Panel.TFrame", padding=(10, 6))
        controls.grid(row=1, column=0, sticky="ew")
        controls.columnconfigure(6, weight=1)
        self.plot_controls_frame = controls
        controls.bind("<Configure>", lambda _event: self._sync_context_controls())

        ttk.Label(controls, text="Metric", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self.value_mode_combo = ttk.Combobox(
            controls,
            state="readonly",
            values=VALUE_MODES,
            textvariable=self.value_mode_var,
            width=18,
        )
        self.value_mode_combo.grid(row=0, column=1, sticky="w", padx=(0, 10))

        ttk.Separator(controls, orient="vertical").grid(
            row=0, column=2, sticky="ns", padx=(0, 10)
        )
        ttk.Label(controls, text="Target width", style="Panel.TLabel").grid(
            row=0, column=3, sticky="w", padx=(0, 6)
        )
        self.time_res_spin = ttk.Spinbox(
            controls,
            from_=self._base_bin_ms(),
            to=self._total_time_ms(),
            increment=self._base_bin_ms(),
            width=6,
            textvariable=self.time_res_ms_var,
            command=self._on_time_resolution_changed,
        )
        self.time_res_spin.grid(row=0, column=4, sticky="w")
        ttk.Label(controls, text="ms", style="Panel.TLabel").grid(
            row=0, column=5, sticky="w", padx=(4, 12)
        )

        range_controls = ttk.Frame(controls, style="Panel.TFrame")
        range_controls.grid(row=0, column=7, sticky="w")
        self.range_controls_frame = range_controls

        ttk.Label(range_controls, text="RF window", style="Panel.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 6),
        )
        self.range_start_spin = ttk.Spinbox(
            range_controls,
            from_=self._time_axis_start_ms(),
            to=self._time_axis_end_ms(),
            increment=self._base_bin_ms(),
            width=6,
            textvariable=self.range_start_ms_var,
            command=self._on_range_changed,
        )
        self.range_open_label = ttk.Label(range_controls, text="(", style="Panel.TLabel")
        self.range_open_label.grid(row=0, column=1)
        self.range_start_spin.grid(row=0, column=2, sticky="w")
        self.range_start_unit_label = ttk.Label(range_controls, text="ms", style="Panel.TLabel")
        self.range_start_unit_label.grid(row=0, column=3, padx=(4, 0))
        ttk.Label(range_controls, text="–", style="Panel.TLabel").grid(
            row=0, column=4, padx=5
        )
        self.range_end_spin = ttk.Spinbox(
            range_controls,
            from_=self._time_axis_start_ms(),
            to=self._time_axis_end_ms(),
            increment=self._base_bin_ms(),
            width=6,
            textvariable=self.range_end_ms_var,
            command=self._on_range_changed,
        )
        self.range_end_spin.grid(row=0, column=5, sticky="w")
        self.range_end_unit_label = ttk.Label(range_controls, text="ms", style="Panel.TLabel")
        self.range_end_unit_label.grid(row=0, column=6, sticky="w", padx=(4, 8))

        self.subtract_controls_frame = ttk.Frame(range_controls, style="Panel.TFrame")
        self.subtract_controls_frame.grid(row=0, column=7, sticky="w")
        ttk.Label(self.subtract_controls_frame, text="− (", style="Panel.TLabel").grid(
            row=0, column=0, padx=(0, 4)
        )
        self.subtract_start_spin = ttk.Spinbox(
            self.subtract_controls_frame,
            from_=self._time_axis_start_ms(),
            to=self._time_axis_end_ms(),
            increment=self._base_bin_ms(),
            width=6,
            textvariable=self.subtract_start_ms_var,
            command=self._on_range_changed,
        )
        self.subtract_start_spin.grid(row=0, column=1)
        ttk.Label(self.subtract_controls_frame, text="ms –", style="Panel.TLabel").grid(
            row=0, column=2, padx=4
        )
        self.subtract_end_spin = ttk.Spinbox(
            self.subtract_controls_frame,
            from_=self._time_axis_start_ms(),
            to=self._time_axis_end_ms(),
            increment=self._base_bin_ms(),
            width=6,
            textvariable=self.subtract_end_ms_var,
            command=self._on_range_changed,
        )
        self.subtract_end_spin.grid(row=0, column=3)
        ttk.Label(self.subtract_controls_frame, text="ms)", style="Panel.TLabel").grid(
            row=0, column=4, padx=(4, 8)
        )

        self.reset_plot_range_button = ttk.Button(
            range_controls,
            text="Reset",
            command=self._reset_plot_range,
        )
        self.reset_plot_range_button.grid(row=0, column=8, sticky="w")

        self.delay_controls_frame = ttk.Frame(controls, style="Panel.TFrame")
        self.rgb_mode_toggle = ttk.Checkbutton(
            self.delay_controls_frame,
            text="RGB composite",
            variable=self.rgb_mode_var,
            command=self._on_control_changed,
        )
        self.rgb_mode_toggle.grid(row=0, column=0, sticky="w")

        self.timeline_context_frame = ttk.Frame(controls, style="Panel.TFrame")
        ttk.Label(
            self.timeline_context_frame,
            text="Full physical time axis",
            style="Panel.TLabel",
        ).grid(row=0, column=0, sticky="w")

        self.display_toggle_button = ttk.Button(
            controls,
            text="Display Options (D)",
            command=self._toggle_display_controls,
        )
        self.display_toggle_button.grid(row=0, column=8, sticky="e", padx=(10, 0))
        self.display_controls_frame = ttk.Frame(controls, style="Panel.TFrame")
        display = self.display_controls_frame
        for column in (1, 3, 5):
            display.columnconfigure(column, weight=1)

        ttk.Label(display, text="X bins", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.x_bins_spin = ttk.Spinbox(
            display,
            from_=1,
            to=self.data.n_x,
            increment=1,
            width=8,
            textvariable=self.x_bins_var,
            command=self._on_control_changed,
        )
        self.x_bins_spin.grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(display, text="Y bins", style="Panel.TLabel").grid(row=0, column=2, sticky="w")
        self.y_bins_spin = ttk.Spinbox(
            display,
            from_=1,
            to=self.data.n_y,
            increment=1,
            width=8,
            textvariable=self.y_bins_var,
            command=self._on_control_changed,
        )
        self.y_bins_spin.grid(row=0, column=3, sticky="w", padx=(6, 18))
        ttk.Label(display, text="Smooth", style="Panel.TLabel").grid(row=0, column=4, sticky="w")
        self.smooth_spin = ttk.Spinbox(
            display,
            from_=0,
            to=3,
            increment=1,
            width=8,
            textvariable=self.smooth_radius_var,
            command=self._on_control_changed,
        )
        self.smooth_spin.grid(row=0, column=5, sticky="w", padx=(6, 18))

        ttk.Label(display, text="Palette", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Combobox(
            display,
            state="readonly",
            values=PALETTES,
            textvariable=self.palette_var,
            width=12,
        ).grid(row=1, column=1, sticky="w", padx=(6, 18), pady=(8, 0))
        ttk.Label(display, text="Polar radius", style="Panel.TLabel").grid(
            row=1, column=2, sticky="w", pady=(8, 0)
        )
        ttk.Combobox(
            display,
            state="readonly",
            values=POLAR_RADIUS_MODES,
            textvariable=self.polar_radius_var,
            width=18,
        ).grid(row=1, column=3, sticky="w", padx=(6, 18), pady=(8, 0))
        self.polar_layout_toggle = ttk.Checkbutton(
            display,
            text="Polar layout",
            variable=self.polar_layout_var,
            command=self._on_spatial_format_changed,
        )
        self.polar_layout_toggle.grid(row=1, column=4, sticky="w", pady=(8, 0))

    def _wire_events(self) -> None:
        self.unit_combo.bind("<<ComboboxSelected>>", self._on_unit_selected)
        self.value_mode_combo.bind("<<ComboboxSelected>>", self._on_value_mode_changed)
        for spin in (
            self.range_start_spin, self.range_end_spin,
            self.subtract_start_spin, self.subtract_end_spin,
        ):
            spin.bind("<Return>", self._on_range_changed)
            spin.bind("<FocusOut>", self._on_range_changed)
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
        self.bind("<Escape>", lambda event: self._run_navigation_shortcut(event, self._handle_escape))
        self.bind("<KeyPress-f>", lambda event: self._run_navigation_shortcut(event, self._toggle_flip_y))
        self.bind("<KeyPress-p>", lambda event: self._run_navigation_shortcut(event, self._toggle_polar_layout))
        self.bind("<KeyPress-P>", lambda event: self._run_navigation_shortcut(event, self._cycle_palette))
        for sequence, action in (
            ("<KeyPress-minus>", self._toggle_rf_subtraction),
            ("<KeyPress-d>", self._toggle_display_controls),
        ):
            callback = lambda event, action=action: self._run_navigation_shortcut(event, action)
            self.bind(sequence, callback)
            self.notebook.bind(sequence, callback, add="+")
        # TNotebook handles letter traversal before a toplevel bindtag.  Own P
        # on the notebook widget itself so Polar toggles before tab mnemonics.
        self.notebook.bind(
            "<KeyPress-p>",
            lambda event: self._run_navigation_shortcut(
                event,
                self._toggle_polar_layout,
            ),
            add="+",
        )
        self.notebook.bind(
            "<KeyPress-P>",
            lambda event: self._run_navigation_shortcut(
                event,
                self._cycle_palette,
            ),
            add="+",
        )
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
            self._bind_unit_filter_shortcut()
        for key, canvas in self.canvases.items():
            canvas.bind("<Configure>", self._schedule_redraw)
            canvas.bind("<Motion>", lambda event, k=key: self._on_canvas_motion(k, event))
            canvas.bind("<Button-1>", lambda event, k=key: self._on_canvas_click(k, event))
            canvas.bind("<Leave>", lambda _event: self._clear_hover())
        self.canvases["timeline"].bind("<MouseWheel>", self._on_timeline_mousewheel)
        self.canvases["timeline"].bind("<Button-4>", self._on_timeline_mousewheel)
        self.canvases["timeline"].bind("<Button-5>", self._on_timeline_mousewheel)
        self.probe_canvas.bind(
            "<Configure>", lambda _event: self._schedule_optional_redraw("probe")
        )
        self.probe_canvas.bind("<ButtonPress-1>", self._on_probe_press)
        self.probe_canvas.bind("<B1-Motion>", self._on_probe_drag)
        self.probe_canvas.bind("<ButtonRelease-1>", self._on_probe_release)
        self.tuning_curve_canvas.bind(
            "<Configure>", lambda _event: self._schedule_optional_redraw("tuning")
        )
        self.tuning_curve_canvas.bind("<Button-1>", self._on_tuning_curve_click)
        self.waveform_canvas.bind(
            "<Double-Button-1>",
            self._toggle_waveform_zoom,
            add="+",
        )
        self.waveform_zoom_canvas.bind(
            "<Double-Button-1>",
            self._toggle_waveform_zoom,
        )
        self.waveform_zoom_canvas.bind("<Configure>", self._schedule_redraw)
        self._install_optional_drop_targets()

    def _toggle_display_controls(self) -> None:
        expanded = not self.display_expanded_var.get()
        self.display_expanded_var.set(expanded)
        self._sync_display_controls()

    def _sync_display_controls(self) -> None:
        expanded = self.display_expanded_var.get()
        self.display_toggle_button.configure(
            text="Hide (D)" if expanded else "Display Options (D)"
        )
        self._view_menu.entryconfigure(
            self._display_options_menu_index,
            label="Hide Display Options" if expanded else "Show Display Options",
        )
        self._view_menu.entryconfigure(
            self._unit_filter_menu_index,
            label="Show Filtered Units" if self.settings.rf_filter_units_with_zero_bins
            else "Hide Units with Zero RF Bins",
        )
        if expanded:
            self.display_controls_frame.grid(
                row=2 if self._rf_range_uses_second_row() and self._active_tab_key() == "rf" else 1,
                column=0, columnspan=9, sticky="ew", pady=(7, 0)
            )
        else:
            self.display_controls_frame.grid_remove()

    def _toggle_waveform_zoom(self, _event: object | None = None) -> str:
        if self._waveform_zoomed:
            self._close_waveform_zoom()
        else:
            self._open_waveform_zoom()
        return "break"

    def _open_waveform_zoom(self) -> None:
        if self._waveform_zoomed or not self.show_waveform_var.get():
            return
        self._waveform_zoomed = True
        self.waveform_zoom_overlay.place(
            x=0,
            y=0,
            relwidth=1.0,
            relheight=1.0,
        )
        self.waveform_zoom_overlay.tkraise()
        self._draw_waveform()
        self.after_idle(self.waveform_zoom_canvas.focus_set)

    def _close_waveform_zoom(self, *, refocus: bool = True) -> None:
        if not self._waveform_zoomed:
            return
        self._waveform_zoomed = False
        self.waveform_zoom_overlay.place_forget()
        if refocus and self.waveform_canvas.winfo_ismapped():
            self.waveform_canvas.focus_set()

    def _handle_escape(self) -> None:
        if self._waveform_zoomed:
            self._close_waveform_zoom()
            return
        if self.spatial_region is not None:
            self._clear_spatial_filter()
        else:
            self._clear_timeline_selection()

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

    def _bind_unit_filter_shortcut(self, target: tk.Misc | None = None) -> None:
        def toggle(_event: object) -> str:
            self._active_viewer()._toggle_zero_bin_filter()
            return "break"

        # Aqua may report the period, its shifted keysym, or kana_fullstop
        # for the same physical key while a punctuation input method is active.
        target = self if target is None else target
        for keysym in ("period", "greater", "kana_fullstop"):
            target.bind(f"<Command-Shift-{keysym}>", toggle)

    def _toggle_zero_bin_filter(self) -> None:
        enabled = not self.settings.rf_filter_units_with_zero_bins
        defaults = replace(self._app_root._rfm_settings, rf_filter_units_with_zero_bins=enabled)
        try:
            save_viewer_settings(defaults, self._app_root._rfm_settings_path)
        except OSError as exc:
            messagebox.showerror("Could not save settings", str(exc), parent=self)
            return
        self._app_root._rfm_settings = defaults
        ready = self._ready_pairing_viewers()
        for window in ready:
            window.settings = replace(window.settings, rf_filter_units_with_zero_bins=enabled)
        settings_window = self._app_root._rfm_settings_window
        if settings_window is not None and settings_window.winfo_exists():
            settings_window.rf_filter_units_with_zero_bins_var.set(enabled)
        # Apply to all windows before reconciling the paired visible-unit union.
        self._pair_ready_viewer_set_changed()
        for window in ready:
            window._update_all()
            window._sync_unit_combo()
        self._publish_pairing_state_if_changed()

    def _toggle_rf_subtraction(self) -> None:
        self.rf_subtract_var.set(not self.rf_subtract_var.get())
        self._on_rf_mode_changed()

    def _on_rf_mode_changed(self) -> None:
        subtract = self.rf_subtract_var.get()
        if subtract != self._rf_controls_subtract:
            self._rf_mode_ranges[self._rf_controls_subtract] = self._selected_time_bounds_ms()
            first, last = self._rf_mode_ranges[subtract]
            self.range_start_ms_var.set(format_ms(first))
            self.range_end_ms_var.set(format_ms(last))
            self._rf_controls_subtract = subtract
        self._on_range_changed()

    def _reset_rf_window_defaults(self) -> None:
        settings = self.settings
        self._rf_mode_ranges = {
            False: (settings.rf_sum_start_ms, settings.rf_sum_end_ms),
            True: (settings.rf_difference_start_ms, settings.rf_difference_end_ms),
        }
        self._rf_controls_subtract = settings.rf_subtract
        self.rf_subtract_var.set(settings.rf_subtract)
        first, last = self._rf_mode_ranges[settings.rf_subtract]
        self.range_start_ms_var.set(format_ms(first))
        self.range_end_ms_var.set(format_ms(last))
        self.subtract_start_ms_var.set(format_ms(settings.rf_subtract_start_ms))
        self.subtract_end_ms_var.set(format_ms(settings.rf_subtract_end_ms))

    def _toggle_polar_layout(self) -> None:
        self.polar_layout_var.set(not self.polar_layout_var.get())
        self._on_spatial_format_changed()

    def _cycle_palette(self) -> None:
        try:
            index = PALETTES.index(self.palette_var.get())
        except ValueError:
            index = 0
        self.palette_var.set(PALETTES[(index + 1) % len(PALETTES)])

    def _show_shortcuts(self) -> None:
        primary = "Command" if sys.platform == "darwin" else "Ctrl"
        messagebox.showinfo(
            "Keyboard Shortcuts",
            "← / →   Previous / next unit\n"
            "↑ / ↓   Previous / next timeline bin\n"
            "Shift+, / Shift+.   Coarser / finer by one source bin\n"
            "1–3   Switch plot tab\n"
            "F   Invert Y\n"
            "P   Toggle Rectangle / Polar layout\n"
            "Shift+P   Cycle palette\n"
            "−   Toggle RF sum / A − B\n"
            "D   Show / hide Display Options\n"
            "Command+Shift+.   Hide / show units filtered by RF bins\n"
            "Esc   Show Full Timeline Range\n"
            "[ / ]   Previous / next unit (legacy)\n"
            "Command-O   Open an RF map in a new window\n"
            "Command-E   Open figure exporter\n"
            "Shift-Command-E   Export displayed data CSV\n"
            "Command-W   Close current window",
            parent=self,
        )

    def _open_support_documentation(self) -> None:
        path = support_documentation_path()
        if path is None:
            messagebox.showerror(
                "Support Documentation",
                "The local README.md could not be found in this installation.",
                parent=self,
            )
            return
        try:
            opened = webbrowser.open(path.as_uri())
        except (OSError, webbrowser.Error) as exc:
            messagebox.showerror(
                "Support Documentation",
                f"Could not open {path.name}:\n\n{exc}",
                parent=self,
            )
            return
        if not opened:
            messagebox.showerror(
                "Support Documentation",
                f"Could not open the local documentation:\n\n{path}",
                parent=self,
            )

    def _install_application_handlers(self) -> None:
        self.protocol("WM_DELETE_WINDOW", self._close_window)
        self._app_root._rfm_active_viewer = self
        self.bind_all("<Control-o>", self._dispatch_open_json)
        self.bind_all("<Control-comma>", self._dispatch_settings)

        if sys.platform != "darwin":
            return
        try:
            self.bind_all("<Command-o>", self._dispatch_open_json)
            self.bind_all("<Command-comma>", self._dispatch_settings)
            self.tk.createcommand("::tk::mac::OpenApplication", self._dispatch_macos_open_application)
            self.tk.createcommand("::tk::mac::OpenDocument", self._dispatch_macos_open_documents)
            self.tk.createcommand("::tk::mac::Quit", self._quit_application)
            self.tk.createcommand("::tk::mac::ShowPreferences", self._dispatch_settings)
            self.tk.createcommand("::tk::mac::ShowHelp", self._open_support_documentation)
        except tk.TclError:
            # The in-app Open button and window close protocol remain usable
            # if this Tk build does not expose the macOS application callbacks.
            return

    def _active_viewer(self) -> RFMViewer:
        active = getattr(self._app_root, "_rfm_active_viewer", None)
        windows = self._app_root._rfm_viewer_windows
        return active if active in windows else (windows[-1] if windows else self)

    def _ready_pairing_viewers(self) -> list[RFMViewer]:
        windows = self._app_root._rfm_viewer_windows
        return [
            window
            for window in windows
            if window._viewer_ready
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
        try:
            return self.data.unit_pool.index(int(unit_id))
        except ValueError:
            return None

    def _selected_unit_id_value(self) -> int:
        return int(self._selected_unit_id)

    def _selected_local_unit_index(self) -> int | None:
        navigation_ids = self._unit_navigation_ids()
        if not navigation_ids:
            if int(self.unit_idx.get()) != -1:
                self.unit_idx.set(-1)
            return None
        unit_id = self._selected_unit_id_value()
        if unit_id not in navigation_ids or not self._local_unit_passes_quality_filter(
            unit_id
        ):
            if int(self.unit_idx.get()) != -1:
                self.unit_idx.set(-1)
            return None
        local_index = self._local_unit_index(unit_id)
        if local_index is None:
            return None
        if int(self.unit_idx.get()) != local_index:
            self.unit_idx.set(local_index)
        archive = self.data.unit_archive
        if archive is not None and not archive.is_cached(local_index):
            archive.request(local_index)
            self._unit_cache_waiting = True
            return None
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

    def _quality_filter_status(self, unit_id: int | None = None) -> str | None:
        if not self.settings.rf_filter_units_with_zero_bins:
            return None
        visible = self._local_quality_visible_unit_ids()
        if not visible:
            return (
                "No units pass the zero-spike RF-bin filter for the current "
                "RF window. Change the window or filter in Settings."
            )
        if unit_id is not None and self._local_unit_index(unit_id) is not None:
            if not self._local_unit_passes_quality_filter(unit_id):
                return (
                    f"Cluster {unit_id} is hidden by the zero-spike RF-bin "
                    "filter for the current RF window."
                )
        return None

    def _restore_local_unit_selection(self) -> None:
        local_units = RFMViewer._local_quality_visible_unit_ids(self)
        if not local_units:
            self.unit_idx.set(-1)
            if hasattr(self, "unit_combo"):
                self._sync_unit_combo()
            return
        selected = self._selected_unit_id_value()
        if selected in local_units:
            target = selected
        else:
            last_supported = self._last_supported_unit_id
            target = int(last_supported) if last_supported in local_units else local_units[0]
        changed = target != selected or self._selected_local_unit_index() is None
        self._set_selected_unit_id(target)
        if changed and self._viewer_ready:
            self.selected_cell = None
            self._update_all()

    def _local_quality_visible_unit_ids(self) -> list[int]:
        unit_ids = [int(unit_id) for unit_id in self.data.unit_pool]
        settings = self.settings
        if not settings.rf_filter_units_with_zero_bins:
            return unit_ids
        start, end = self._source_bins_for_time_controls()
        threshold = settings.rf_zero_bin_threshold
        return [
            unit_id
            for index, unit_id in enumerate(unit_ids)
            if (
                self.data.unit_archive is not None
                and not self.data.unit_archive.is_cached(index)
            ) or self.data.zero_spike_spatial_bin_count(index, start, end) < threshold
        ]

    def _local_unit_passes_quality_filter(self, unit_id: int) -> bool:
        if not self.settings.rf_filter_units_with_zero_bins:
            return True
        local_index = self._local_unit_index(unit_id)
        if local_index is None:
            return False
        archive = self.data.unit_archive
        if archive is not None and not archive.is_cached(local_index):
            return True
        start, end = self._source_bins_for_time_controls()
        return (
            self.data.zero_spike_spatial_bin_count(local_index, start, end)
            < self.settings.rf_zero_bin_threshold
        )

    def _quality_filtered_pairing_unit_ids(
        self,
        ready: list[RFMViewer],
    ) -> list[int]:
        return sorted(
            {
                unit_id
                for window in ready
                for unit_id in RFMViewer._local_quality_visible_unit_ids(window)
            }
        )

    def _unit_navigation_ids(self) -> list[int]:
        if self._app_root._rfm_pairing_enabled:
            ready, eligible = self._pairing_eligibility()
            if eligible:
                unit_ids = RFMViewer._quality_filtered_pairing_unit_ids(self, ready)
            else:
                unit_ids = RFMViewer._local_quality_visible_unit_ids(self)
        else:
            unit_ids = RFMViewer._local_quality_visible_unit_ids(self)
        region = self.spatial_region
        geometry = self.probe_geometry
        if region is not None and geometry is not None:
            return geometry.unit_ids_in_region(region, unit_ids)
        return unit_ids

    def _reconcile_unit_filter_selection(self) -> None:
        """Keep selection valid as the RF sum window or filter settings change."""

        unit_ids = self._unit_navigation_ids()
        selected = self._selected_unit_id_value()
        if not unit_ids:
            self.unit_idx.set(-1)
            if hasattr(self, "unit_combo"):
                self._sync_unit_combo()
            return
        if selected not in unit_ids:
            self.selected_cell = None
            self._set_selected_unit_id(self._next_union_unit_id(unit_ids, selected))
            return
        local_index = self._local_unit_index(selected)
        if local_index is None or not self._local_unit_passes_quality_filter(selected):
            self.unit_idx.set(-1)
        elif int(self.unit_idx.get()) != local_index:
            self.unit_idx.set(local_index)
        if hasattr(self, "unit_combo"):
            self._sync_unit_combo()

    def _pairing_eligibility(self) -> tuple[list[RFMViewer], bool]:
        ready = self._ready_pairing_viewers()
        return ready, len(ready) >= 2

    def _refresh_pairing_controls(self) -> None:
        ready, eligible = self._pairing_eligibility()
        active = bool(self._app_root._rfm_pairing_enabled and eligible)
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

        windows = self._app_root._rfm_viewer_windows
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
        if not self._app_root._rfm_pairing_enabled:
            self._refresh_pairing_controls()
            return
        if not eligible:
            self._disable_window_pairing()
            return

        state = self._app_root._rfm_pairing_state
        if state is None:
            source = ready[0]
            state = source._capture_pairing_state()
            source._pair_last_local_state = state
            self._app_root._rfm_pairing_state = state
        unit_ids = RFMViewer._quality_filtered_pairing_unit_ids(self, ready)
        if not unit_ids:
            for window in ready:
                window._set_selected_unit_id(state.unit_id)
                window.unit_idx.set(-1)
                window._sync_unit_combo()
                window._update_all()
            self._refresh_pairing_controls()
            return
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
            value_mode = VALUE_MODE_RATE
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
            rf_subtract=bool(self.rf_subtract_var.get()),
            rf_subtract_start_ms=float(self.subtract_start_ms_var.get()),
            rf_subtract_end_ms=float(self.subtract_end_ms_var.get()),
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
            tuning_plot_mode=self.tuning_plot_mode_var.get(),
            tuning_display_bins=normalize_hd_bin_count(
                self.tuning_display_bins_var.get()
            ),
            tuning_smoothing=bool(self.tuning_smoothing_var.get()),
            tuning_smooth_sigma=float(self.tuning_smooth_sigma_var.get()),
            tuning_compare_scale=bool(self.tuning_compare_scale_var.get()),
            show_tuning_curve=bool(self.show_tuning_curve_var.get()),
            show_waveform=bool(self.show_waveform_var.get()),
            show_probe_layout=bool(self.show_probe_layout_var.get()),
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
                if (
                    self.spatial_region is not None
                    and int(state.unit_id) not in self._unit_navigation_ids()
                ):
                    self.spatial_region = None
                self._set_selected_unit_id(state.unit_id)
            if "value_mode" in fields:
                value_mode = state.value_mode
                if (
                    value_mode not in VALUE_MODES
                    or not self.data.supports_value_mode(value_mode)
                ):
                    value_mode = VALUE_MODE_RATE
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
                self._rf_mode_ranges[self._rf_controls_subtract] = self._selected_time_bounds_ms()
                self._rf_controls_subtract = state.rf_subtract
                self.range_start_ms_var.set(format_ms(state.rf_start_ms))
                self.range_end_ms_var.set(format_ms(state.rf_end_ms))
                self.rf_subtract_var.set(state.rf_subtract)
                self.subtract_start_ms_var.set(format_ms(state.rf_subtract_start_ms))
                self.subtract_end_ms_var.set(format_ms(state.rf_subtract_end_ms))
            if "timeline_scroll" in fields:
                self._timeline_scroll_fraction = max(
                    0.0, min(1.0, float(state.timeline_scroll_fraction))
                )
            if "tuning_display" in fields:
                self.tuning_plot_mode_var.set(
                    state.tuning_plot_mode
                    if state.tuning_plot_mode in TUNING_PLOT_MODES
                    else "Auto"
                )
                self.tuning_display_bins_var.set(
                    normalize_hd_bin_count(state.tuning_display_bins)
                )
                self.tuning_smoothing_var.set(bool(state.tuning_smoothing))
                self.tuning_smooth_sigma_var.set(
                    state.tuning_smooth_sigma
                    if math.isfinite(state.tuning_smooth_sigma)
                    and state.tuning_smooth_sigma > 0.0
                    else DEFAULT_HD_SMOOTH_SIGMA
                )
                self.tuning_compare_scale_var.set(bool(state.tuning_compare_scale))
                self._tuning_processed_cache = None
                self._tuning_scale_cache = None
            if "optional_views" in fields:
                self.show_tuning_curve_var.set(bool(state.show_tuning_curve))
                self.show_waveform_var.set(bool(state.show_waveform))
                self.show_probe_layout_var.set(bool(state.show_probe_layout))
                self._sync_optional_view_visibility(redraw=False)
                if (
                    state.show_probe_layout
                    and self.probe_geometry is None
                    and self.settings.auto_load_probe_layout
                ):
                    self.probe_geometry = discover_probe_geometry(self.data.path)
                    self._probe_static_signature = None
                if (
                    state.show_tuning_curve
                    and self.tuning_curve_data is None
                    and self.settings.auto_load_tuning_curve
                ):
                    candidate = discover_tuning_curve_path(
                        self.data.path,
                        self.settings.tuning_curve_session,
                    )
                    if candidate is not None:
                        self._load_tuning_curve_path(
                            candidate, show_error=False, redraw=False
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
                    "tuning_display",
                    "optional_views",
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
        if not self._viewer_ready:
            return
        if self._pair_apply_in_progress:
            return
        if not self._app_root._rfm_pairing_enabled:
            return
        if self._app_root._rfm_pairing_broadcasting:
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
        canonical = self._app_root._rfm_pairing_state
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

    def _dispatch_settings(self, _event: object | None = None) -> None:
        self._active_viewer()._show_settings()

    def _show_settings(self) -> None:
        active = self._active_viewer()
        if not getattr(active, "_viewer_ready", False):
            active._show_settings_when_ready = True
            return
        existing = self._app_root._rfm_settings_window
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.owner = active
                    existing.transient(active)
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        window = SettingsWindow(active)
        self._app_root._rfm_settings_window = window
        window.lift()

    def _apply_viewer_settings(
        self,
        settings: ViewerSettings,
        *,
        persist: bool,
        broadcast: bool,
    ) -> bool:
        previous = self.settings
        if persist:
            try:
                save_viewer_settings(settings, self._app_root._rfm_settings_path)
            except OSError as exc:
                messagebox.showerror("Could not save settings", str(exc), parent=self)
                return False
            self._app_root._rfm_settings = settings
        self.settings = settings

        old_show_probe = bool(self.show_probe_layout_var.get())
        old_show_tuning = bool(self.show_tuning_curve_var.get())
        old_show_waveform = bool(self.show_waveform_var.get())
        tuning_session_changed = (
            settings.tuning_curve_session != previous.tuning_curve_session
        )
        needs_optional_autoload = tuning_session_changed
        previous_pair_apply = self._pair_apply_in_progress
        self._pair_apply_in_progress = True
        try:
            self.show_probe_layout_var.set(settings.show_probe_layout)
            self.show_tuning_curve_var.set(settings.show_tuning_curve)
            self.show_waveform_var.set(settings.show_waveform)
            value_mode = settings.rf_value_mode
            if not self.data.supports_value_mode(value_mode):
                value_mode = VALUE_MODE_RATE
            self.value_mode_var.set(value_mode)
            self._reset_rf_window_defaults()
            self.time_res_ms_var.set(format_ms(settings.rf_time_resolution_ms))
            self.x_bins_var.set(min(self.data.n_x, settings.rf_x_bins or self.data.n_x))
            self.y_bins_var.set(min(self.data.n_y, settings.rf_y_bins or self.data.n_y))
            self.smooth_radius_var.set(settings.rf_smooth_radius)
            self.flip_y_var.set(settings.rf_flip_y)
            self.palette_var.set(settings.rf_palette)
            self.polar_radius_var.set(settings.rf_polar_radius)
            self.polar_layout_var.set(settings.rf_polar_layout)
            self.rgb_mode_var.set(settings.rf_rgb_mode)
            self.tuning_plot_mode_var.set(settings.tuning_plot_mode)
            self.tuning_layout_var.set(settings.tuning_layout)
            self.tuning_display_bins_var.set(settings.tuning_display_bins)
            self.tuning_smoothing_var.set(settings.tuning_smoothing)
            self.tuning_smooth_sigma_var.set(settings.tuning_smooth_sigma)
            self.tuning_compare_scale_var.set(settings.tuning_compare_scale)
            if tuning_session_changed:
                with self.data._hd_tuning_lock:
                    self.data._hd_tuning = None
                    self.data._hd_tuning_identity = None
                    self.data._hd_tuning_error = None
                    # Prevent another consumer from falling back to a different
                    # session while the exact configured session is reloaded.
                    self.data._hd_tuning_checked = True
                self.tuning_curve_data = None
                self._tuning_curve_error = None
                self._tuning_curve_candidate = None
                self.tuning_collapsed_var.set(False)
            mode_changed = (
                self.waveform_channel_mode_var.get()
                != settings.waveform_channel_mode
            )
            self.waveform_channel_mode_var.set(settings.waveform_channel_mode)
            if mode_changed or (
                old_show_waveform and not settings.show_waveform
            ):
                self.waveform_payload = None
                self._waveform_payload_key = None
                self._waveform_error = None
                self._waveform_error_key = None
                self._waveform_loading_key = None
                self._waveform_generation += 1
            self._tuning_processed_cache = None
            self._tuning_scale_cache = None

            self._sync_optional_view_visibility(redraw=False)
            if settings.show_probe_layout and self.probe_geometry is None:
                should_load_probe = settings.auto_load_probe_layout and (
                    not old_show_probe or not previous.auto_load_probe_layout
                )
                if should_load_probe:
                    needs_optional_autoload = True
            if settings.show_tuning_curve and self.tuning_curve_data is None:
                should_load_tuning = settings.auto_load_tuning_curve and (
                    tuning_session_changed
                    or not old_show_tuning
                    or not previous.auto_load_tuning_curve
                )
                if should_load_tuning:
                    needs_optional_autoload = True
            self._normalize_control_values()
            self._timeline_preview_cache_key = None
            self._timeline_preview_images = {}
            self._sync_unit_combo()
            self._update_all()
        finally:
            self._pair_apply_in_progress = previous_pair_apply

        if needs_optional_autoload:
            self._schedule_optional_autoload()

        if (
            broadcast
            and self._app_root._rfm_pairing_enabled
            and not self._app_root._rfm_pairing_broadcasting
        ):
            self._app_root._rfm_pairing_broadcasting = True
            try:
                ready = self._ready_pairing_viewers()
                for window in ready:
                    if window is not self:
                        window._apply_viewer_settings(
                            settings,
                            persist=False,
                            broadcast=False,
                        )
                # Settings are global, but each file has its own RF counts.
                # Reconcile the shared unit only after every paired window has
                # adopted the new filter so no window observes a mixed old/new
                # visible-unit union.
                visible_union = self._quality_filtered_pairing_unit_ids(ready)
                selected = self._selected_unit_id_value()
                target = (
                    self._next_union_unit_id(visible_union, selected)
                    if visible_union
                    else selected
                )
                for window in ready:
                    if (
                        window.spatial_region is not None
                        and target not in window._unit_navigation_ids()
                    ):
                        window.spatial_region = None
                    window._set_selected_unit_id(target)
                    window.selected_cell = None
                    window._update_all()
            finally:
                self._app_root._rfm_pairing_broadcasting = False
            state = self._capture_pairing_state()
            self._app_root._rfm_pairing_state = state
            for window in self._ready_pairing_viewers():
                window._pair_last_local_state = window._capture_pairing_state()
        return True

    def _dispatch_macos_open_application(self) -> None:
        viewer = self._active_viewer()
        if (
            not viewer._quitting
            and viewer._startup_chooser_frame is not None
            and viewer._startup_after is None
        ):
            viewer._startup_after = viewer.after_idle(viewer._open_startup_file_dialog)

    def _dispatch_macos_open_documents(self, *paths: str) -> None:
        self._active_viewer()._on_macos_open_documents(*paths)

    def _close_window(self, _event: object | None = None) -> None:
        if _active_export_jobs(self._app_root, self):
            messagebox.showinfo(
                "Export is running",
                "Wait for this window's export to finish before closing it.",
                parent=self,
            )
            return
        self.destroy()

    def _quit_application(self, _event: object | None = None) -> None:
        if self._app_root._rfm_quitting:
            return
        if _active_export_jobs(self._app_root):
            messagebox.showinfo(
                "Export is running",
                "Wait for all figure exports to finish before quitting RF Map Viewer.",
                parent=self,
            )
            return
        self._quitting = True
        self._app_root._rfm_quitting = True
        _shutdown_export_executor(self._app_root)
        self._app_root.destroy()

    def _open_json_window(self, path: Path) -> RFMViewer | None:
        path = Path(path).expanduser()
        try:
            use_background_load = (
                path.stat().st_size >= ASYNC_DOCUMENT_LOAD_BYTES or is_indexed_rfmap(path)
            )
        except OSError:
            use_background_load = False
        if use_background_load:
            window = RFMViewer(startup_path=path, master=self._app_root)
            window._cancel_startup_callback()
            window._startup_after = window.after_idle(
                lambda: window._load_startup_document(path)
            )
            window.lift()
            return window
        try:
            data = RFMappingData(path)
        except Exception as exc:
            messagebox.showerror("Could not open RF map", str(exc), parent=self)
            return None
        window = RFMViewer(data, master=self._app_root)
        window.lift()
        return window

    def _open_json(self, _event: object | None = None) -> None:
        initial_dir = (
            self.data.path.parent
            if self._viewer_ready
            else startup_file_dialog_directory()
        )
        path = filedialog.askopenfilename(
            parent=self,
            title="Open RF mapping file",
            initialdir=str(initial_dir),
            filetypes=RF_DOCUMENT_FILETYPES,
        )
        if path:
            if self._viewer_ready:
                self._open_json_window(Path(path))
            else:
                self._cancel_startup_callback()
                self._remove_startup_chooser_shell()
                self._startup_after = self.after_idle(
                    lambda selected=Path(path): self._load_startup_document(selected)
                )

    def _open_external_companion(self, path: Path) -> bool:
        """Attach a Finder-opened companion to this RF document window."""

        if not self._viewer_ready:
            self._pending_open_documents.append(path)
            return True
        kind = document_kind(path)
        if kind == "tuning":
            return self._load_tuning_curve_path(path)
        if kind == "probe":
            return self._load_probe_geometry_path(path)
        raise ValueError(f"Not a companion document: {path}")

    def _on_macos_open_documents(self, *paths: str) -> None:
        documents = [Path(raw_path).expanduser() for raw_path in paths]
        if not documents:
            return
        rf_documents = [path for path in documents if document_kind(path) == "rf"]
        companions = [
            path
            for path in documents
            if document_kind(path) in {"tuning", "probe"}
        ]
        if not self._viewer_ready:
            pending = getattr(self, "_pending_open_documents", None)
            if pending is None:
                pending = []
                self._pending_open_documents = pending
            pending.extend(companions)
            if not rf_documents:
                return
            self._cancel_startup_callback()
            selected, *additional = rf_documents

            def load_documents() -> None:
                self._load_startup_document(selected)
                for path in additional:
                    self._open_json_window(path)

            self._startup_after = self.after_idle(
                load_documents
            )
            return

        companion_target = self
        for index, path in enumerate(rf_documents):
            opened = self._open_json_window(path)
            if index == 0 and opened is not None:
                companion_target = opened
        for path in companions:
            companion_target._open_external_companion(path)

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

    def _sync_json_menu(self) -> None:
        current = _resolve_existing_file(self.data.path) or self.data.path
        self.json_paths = discover_json_files(current_path=current)
        if current not in self.json_paths:
            self.json_paths.insert(0, current)
        labels = [self._json_choice_label(path) for path in self.json_paths]
        self._json_choice_to_path = dict(zip(labels, self.json_paths))
        menu = getattr(self, "_discovered_json_menu", None)
        if menu is None:
            return
        menu.delete(0, "end")
        if not labels:
            menu.add_command(label="No RF mapping files found", state="disabled")
            return
        for label, path in zip(labels, self.json_paths):
            menu.add_command(
                label=label,
                command=lambda selected=path: self._open_json_window(selected),
            )

    def _sync_json_combo(self) -> None:
        """Compatibility alias retained for the minimal 1.9 call surface."""

        self._sync_json_menu()

    def _on_json_selected(self, _event: object | None = None) -> None:
        combo = getattr(self, "json_combo", None)
        if combo is None:
            return
        choice = combo.get()
        path = self._json_choice_to_path.get(choice)
        if path is None:
            return
        if _resolve_existing_file(self.data.path) == path:
            return
        self._open_json_window(path)

    def _autoload_optional_resources(self, *, redraw: bool = True) -> None:
        if self.show_probe_layout_var.get() and self.settings.auto_load_probe_layout:
            self.probe_geometry = self.data.probe_geometry()
            self._probe_static_signature = None
        if self.show_tuning_curve_var.get() and self.settings.auto_load_tuning_curve:
            candidate = discover_tuning_curve_path(
                self.data.path,
                self.settings.tuning_curve_session,
            )
            if candidate is not None:
                self._load_tuning_curve_path(
                    candidate, show_error=False, redraw=False
                )
        if redraw:
            self._draw_probe_canvas()
            if self._active_tab_key() == "rf":
                self._draw_tuning_curve()

    def _autoload_optional_resources_deferred(self, generation: int) -> None:
        self._optional_autoload_after = None
        if (
            generation != self._optional_autoload_generation
            or not self._viewer_ready
            or self._quitting
        ):
            return
        snapshot = {
            "generation": generation,
            "data": self.data,
            "data_path": self.data.path,
            "load_probe": bool(
                self.show_probe_layout_var.get()
                and self.settings.auto_load_probe_layout
            ),
            "load_tuning": bool(
                self.show_tuning_curve_var.get()
                and self.settings.auto_load_tuning_curve
            ),
            "tuning_session": int(self.settings.tuning_curve_session),
            "cluster_id": self._selected_unit_id_value(),
            "tuning_bins": normalize_hd_bin_count(
                self.tuning_display_bins_var.get()
            ),
            "tuning_smoothing": bool(self.tuning_smoothing_var.get()),
            "tuning_sigma": float(self.tuning_smooth_sigma_var.get()),
        }
        threading.Thread(
            target=self._optional_autoload_worker,
            args=(snapshot,),
            name=f"rfmapping-optional-{generation}",
            daemon=True,
        ).start()
        self._schedule_optional_result_poll()

    def _optional_autoload_worker(self, snapshot: Mapping[str, object]) -> None:
        """Discover and parse optional files without blocking Tk's UI thread."""

        result: dict[str, object] = {
            "generation": snapshot.get("generation"),
            "data_path": snapshot.get("data_path"),
            "probe_geometry": None,
            "tuning_path": None,
            "tuning_identity": None,
            "tuning_signature": None,
            "tuning_data": None,
            "tuning_error": None,
            "worker_error": None,
            "processed": None,
            "processed_cluster": snapshot.get("cluster_id"),
            "processed_bins": snapshot.get("tuning_bins"),
            "processed_smoothing": snapshot.get("tuning_smoothing"),
            "processed_sigma": snapshot.get("tuning_sigma"),
        }
        try:
            data = snapshot.get("data", self.data)
            if not isinstance(data, RFMappingData):
                raise TypeError("Optional-load snapshot lost its RF data owner")
            data_path = data.path
            result["data_path"] = data_path
            if snapshot["load_probe"]:
                geometry = discover_probe_geometry(data_path)
                if geometry is not None:
                    geometry = data.attach_probe_geometry(
                        geometry.positions_path,
                        geometry.channels_path,
                        probe_name=geometry.probe_name,
                    )
                result["probe_geometry"] = geometry
            if snapshot["load_tuning"]:
                candidate = discover_tuning_curve_path(
                    data_path,
                    int(
                        snapshot.get(
                            "tuning_session",
                            DEFAULT_TUNING_CURVE_SESSION,
                        )
                    ),
                )
                result["tuning_path"] = candidate
                if candidate is not None:
                    identity = FrozenFileIdentity.capture(candidate)
                    tuning_data = TuningCurveData.load(identity.path)
                    identity.verify_path()
                    result["tuning_identity"] = identity
                    result["tuning_signature"] = (identity.mtime_ns, identity.size)
                    result["tuning_data"] = tuning_data
                    raw_rates = tuning_data.rates_for(int(snapshot["cluster_id"]))
                    if raw_rates is not None:
                        result["processed"] = tuning_data.processed_for(
                            int(snapshot["cluster_id"]),
                            int(snapshot["tuning_bins"]),
                            smoothing=bool(snapshot["tuning_smoothing"]),
                            sigma=float(snapshot["tuning_sigma"]),
                        )
        except Exception as exc:
            result["worker_error"] = f"{type(exc).__name__}: {exc}"
            if snapshot.get("load_tuning"):
                if isinstance(exc, (ImportError, OSError, ValueError)):
                    result["tuning_error"] = str(exc)
                else:
                    result["tuning_error"] = "Could not auto-load tuning curves."
        finally:
            # Always end the matching poll generation, even if mounted-volume
            # discovery or optional preprocessing fails unexpectedly.
            self._optional_result_queue.put(result)

    def _schedule_optional_result_poll(self) -> None:
        if self._optional_poll_after is not None:
            return
        self._optional_poll_after = self.after(30, self._poll_optional_results)

    def _poll_optional_results(self) -> None:
        self._optional_poll_after = None
        current_result: dict[str, object] | None = None
        while True:
            try:
                candidate = self._optional_result_queue.get_nowait()
            except queue.Empty:
                break
            if candidate.get("generation") == self._optional_autoload_generation:
                current_result = candidate
        if current_result is None:
            if not self._quitting:
                self._schedule_optional_result_poll()
            return
        if (
            current_result.get("data_path") != self.data.path
            or not self._viewer_ready
        ):
            return

        if (
            self.show_probe_layout_var.get()
            and self.settings.auto_load_probe_layout
            and self.probe_geometry is None
        ):
            geometry = current_result.get("probe_geometry")
            if geometry is None or isinstance(geometry, ProbeGeometry):
                self.probe_geometry = geometry
                self._probe_static_signature = None

        if (
            self.show_tuning_curve_var.get()
            and self.settings.auto_load_tuning_curve
            and self.tuning_curve_data is None
        ):
            tuning_path = current_result.get("tuning_path")
            tuning_data = current_result.get("tuning_data")
            if isinstance(tuning_path, Path):
                self._tuning_curve_candidate = tuning_path
            if isinstance(tuning_data, TuningCurveData):
                identity = current_result.get("tuning_identity")
                if not isinstance(identity, FrozenFileIdentity):
                    tuning_data = None
                    current_result["tuning_error"] = (
                        "Could not verify the discovered tuning-curve file."
                    )
                else:
                    try:
                        self.data._publish_hd_tuning(identity, tuning_data)
                    except (OSError, ValueError) as exc:
                        tuning_data = None
                        current_result["tuning_error"] = str(exc)
            if isinstance(tuning_data, TuningCurveData):
                self.tuning_curve_data = tuning_data
                self._tuning_curve_error = None
                self._tuning_scale_cache = None
                self.tuning_collapsed_var.set(False)
                self._sync_tuning_collapsed_state(schedule_redraw=False)
                signature = current_result.get("tuning_signature")
                if (
                    isinstance(signature, tuple)
                    and len(signature) == 2
                    and isinstance(tuning_path, Path)
                ):
                    self._app_root._rfm_tuning_cache[str(tuning_path)] = (
                        *signature,
                        tuning_data,
                    )
                processed = current_result.get("processed")
                processed_cluster = int(current_result["processed_cluster"])
                if (
                    isinstance(processed, tuple)
                    and len(processed) == 2
                    and processed_cluster == self._selected_unit_id_value()
                    and int(current_result["processed_bins"])
                    == normalize_hd_bin_count(self.tuning_display_bins_var.get())
                    and bool(current_result["processed_smoothing"])
                    == bool(self.tuning_smoothing_var.get())
                    and math.isclose(
                        float(current_result["processed_sigma"]),
                        float(self.tuning_smooth_sigma_var.get()),
                    )
                ):
                    key = (
                        tuning_data.path,
                        processed_cluster,
                        int(current_result["processed_bins"]),
                        bool(current_result["processed_smoothing"]),
                        float(current_result["processed_sigma"]),
                    )
                    self._tuning_processed_cache = (key, processed[0], processed[1])
            elif current_result.get("tuning_error"):
                self._tuning_curve_error = str(current_result["tuning_error"])
            elif (
                self.settings.auto_load_tuning_curve
                and current_result.get("tuning_path") is None
            ):
                # A missing optional file should not reserve two fifths of the
                # RF tab. Keep a small, explicit HD restore control instead.
                self.tuning_collapsed_var.set(True)
                self._sync_tuning_collapsed_state(schedule_redraw=False)

        self._draw_probe_canvas()
        if self._active_tab_key() == "rf":
            self._draw_tuning_curve()

    def _schedule_optional_autoload(self) -> None:
        if self._optional_autoload_after is not None:
            try:
                self.after_cancel(self._optional_autoload_after)
            except tk.TclError:
                pass
        self._optional_autoload_generation += 1
        generation = self._optional_autoload_generation
        # Give Tk a chance to map and paint the RF window before starting the
        # mounted-volume discovery worker.
        self._optional_autoload_after = self.after(
            100,
            lambda: self._autoload_optional_resources_deferred(generation),
        )

    def _load_tuning_curve_path(
        self,
        path: Path,
        *,
        show_error: bool = True,
        redraw: bool = True,
    ) -> bool:
        resolved = Path(path).expanduser().resolve()
        previous_data = self.tuning_curve_data
        previous_error = self._tuning_curve_error
        previous_candidate = self._tuning_curve_candidate
        try:
            data = self.data.attach_hd_tuning(resolved)
            identity = self.data._hd_tuning_identity
            if identity is not None:
                self._app_root._rfm_tuning_cache[str(resolved)] = (
                    identity.mtime_ns,
                    identity.size,
                    data,
                )
        except (OSError, ValueError) as exc:
            if show_error:
                self.tuning_curve_data = previous_data
                self._tuning_curve_error = previous_error
                self._tuning_curve_candidate = previous_candidate
                messagebox.showerror("Could not attach tuning curves", str(exc), parent=self)
            else:
                self.tuning_curve_data = None
                self._tuning_curve_error = str(exc)
                self._tuning_curve_candidate = resolved
                self._tuning_processed_cache = None
                self._tuning_scale_cache = None
            if redraw:
                self._draw_tuning_curve()
            return False
        self.tuning_curve_data = data
        self._tuning_curve_error = None
        self._tuning_curve_candidate = resolved
        self._tuning_processed_cache = None
        self._tuning_scale_cache = None
        self.tuning_collapsed_var.set(False)
        self._sync_tuning_collapsed_state(schedule_redraw=False)
        if redraw:
            self._draw_tuning_curve()
        return True

    def _attach_tuning_curve(self) -> None:
        if not self.show_tuning_curve_var.get():
            return
        initial_dir = (
            self._tuning_curve_candidate.parent
            if self._tuning_curve_candidate is not None
            else self.data.path.parent
        )
        path = filedialog.askopenfilename(
            parent=self,
            title="Attach tuning curves",
            initialdir=str(initial_dir),
            filetypes=TUNING_CURVE_FILETYPES,
        )
        if path:
            self._load_tuning_curve_path(Path(path))

    def _clear_tuning_curve(self) -> None:
        with self.data._hd_tuning_lock:
            self.data._hd_tuning = None
            self.data._hd_tuning_identity = None
            self.data._hd_tuning_error = None
            self.data._hd_tuning_checked = True
        self.tuning_curve_data = None
        self._tuning_curve_error = None
        self._tuning_curve_candidate = None
        self._tuning_processed_cache = None
        self._tuning_scale_cache = None
        self._draw_tuning_curve()

    def _on_tuning_curve_click(self, _event: object | None = None) -> None:
        if self.show_tuning_curve_var.get() and self.tuning_curve_data is None:
            self._attach_tuning_curve()

    def _load_probe_geometry_path(
        self,
        positions: Path,
        *,
        show_error: bool = True,
        redraw: bool = True,
    ) -> bool:
        previous_geometry = self.probe_geometry
        try:
            geometry = self.data.attach_probe_geometry(
                positions,
                self._infer_attached_channels_path(positions),
                probe_name=probe_name_for_json(self.data.path) or positions.parent.name,
            )
        except (OSError, ValueError) as exc:
            self.probe_geometry = previous_geometry
            if show_error:
                messagebox.showerror("Could not attach probe geometry", str(exc), parent=self)
            if redraw:
                self._draw_probe_canvas()
            return False
        self.probe_geometry = geometry
        self._probe_static_signature = None
        self.spatial_region = None
        self._sync_unit_combo()
        if redraw:
            self._draw_probe_canvas()
        return True

    def _install_optional_drop_targets(self) -> None:
        self._optional_drop_available = False
        self._dnd_copy_action = "copy"
        self._dnd_refuse_action = "refuse_drop"
        try:
            from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD

            TkinterDnD.require(self._app_root)
            self._dnd_copy_action = COPY
            self._dnd_refuse_action = REFUSE_DROP
            for widget, resource in (
                (self.probe_canvas, "probe"),
                (self.tuning_curve_canvas, "tuning"),
            ):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind(
                    "<<Drop>>",
                    lambda event, target=resource: self._on_optional_file_drop(
                        target, event
                    ),
                )
        except (ImportError, RuntimeError, tk.TclError):
            return
        self._optional_drop_available = True

    def _on_optional_file_drop(self, resource: str, event: object) -> str:
        copy_action = getattr(self, "_dnd_copy_action", "copy")
        refuse_action = getattr(self, "_dnd_refuse_action", "refuse_drop")
        if resource not in {"probe", "tuning"}:
            return refuse_action
        visible = (
            self.show_probe_layout_var.get()
            if resource == "probe"
            else self.show_tuning_curve_var.get()
        )
        if not visible:
            return refuse_action
        raw_data = getattr(event, "data", "")
        try:
            paths = tuple(Path(value) for value in self.tk.splitlist(raw_data))
        except tk.TclError:
            paths = ()
        if len(paths) != 1:
            messagebox.showerror(
                "Could not attach file",
                "Drop exactly one file at a time.",
                parent=self,
            )
            return refuse_action
        if resource == "probe":
            loaded = self._load_probe_geometry_path(paths[0])
        else:
            loaded = self._load_tuning_curve_path(paths[0])
        return copy_action if loaded else refuse_action

    def _sync_optional_menu_states(self) -> None:
        menu = getattr(self, "_file_menu", None)
        if menu is None:
            return
        try:
            menu.entryconfigure(
                "Attach Probe Geometry…",
                state="normal" if self.show_probe_layout_var.get() else "disabled",
            )
            menu.entryconfigure(
                "Attach Tuning Curves…",
                state="normal" if self.show_tuning_curve_var.get() else "disabled",
            )
        except tk.TclError:
            return

    def _sync_optional_view_visibility(self, *, redraw: bool = True) -> None:
        if not self.show_probe_layout_var.get():
            self.spatial_region = None
            self.probe_geometry = None
            self._probe_static_signature = None
            self.probe_section.grid_remove()
            self.sidebar_frame.rowconfigure(self._probe_section_row, weight=0)
        else:
            self.probe_section.grid()
            self._sync_probe_collapsed_state(schedule_redraw=False)

        if not self.show_tuning_curve_var.get():
            self.tuning_curve_data = None
            self._tuning_curve_error = None
            self._tuning_curve_candidate = None
            self._tuning_processed_cache = None
            self._tuning_scale_cache = None
        if not self.show_waveform_var.get():
            self._close_waveform_zoom(refocus=False)
            had_waveform_state = any(
                value is not None
                for value in (
                    self.waveform_payload,
                    self._waveform_payload_key,
                    self._waveform_loading_key,
                    self._waveform_error,
                    self._waveform_error_key,
                )
            )
            if had_waveform_state:
                self._waveform_generation += 1
            self.waveform_payload = None
            self._waveform_payload_key = None
            self._waveform_loading_key = None
            self._waveform_error = None
            self._waveform_error_key = None
            self.waveform_subtitle_label.configure(text="")
            self.waveform_canvas.delete("all")
            self.waveform_zoom_subtitle_label.configure(text="")
            self.waveform_zoom_canvas.delete("all")
        self._sync_auxiliary_sections()
        self._sync_tuning_collapsed_state(schedule_redraw=False)
        self._layout_rf_and_tuning()
        self._sync_optional_menu_states()
        if redraw:
            self._draw_probe_canvas()
            self._draw_tuning_curve()
            if self.show_waveform_var.get():
                self._draw_waveform()

    def _effective_tuning_plot_mode(self) -> str:
        mode = self.tuning_plot_mode_var.get()
        if mode == "Auto":
            return "Polar" if self.polar_layout_var.get() else "Line"
        return mode if mode in {"Polar", "Line"} else "Line"

    def _processed_tuning_values(
        self,
        cluster_id: int,
        rates: Sequence[float],
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        bins = normalize_hd_bin_count(self.tuning_display_bins_var.get())
        smoothing = bool(self.tuning_smoothing_var.get())
        sigma = float(self.tuning_smooth_sigma_var.get())
        key = (
            self.tuning_curve_data.path if self.tuning_curve_data is not None else None,
            int(cluster_id),
            bins,
            smoothing,
            sigma,
        )
        cached = self._tuning_processed_cache
        if cached is not None and cached[0] == key:
            return cached[1], cached[2]
        processed = (
            self.tuning_curve_data.processed_for(
                cluster_id,
                bins,
                smoothing=smoothing,
                sigma=sigma,
            )
            if self.tuning_curve_data is not None
            else None
        )
        if processed is None:
            centers, values = processed_tuning_curve(
                rates,
                bins,
                smoothing=smoothing,
                sigma=sigma,
            )
        else:
            centers, values = processed
        self._tuning_processed_cache = (key, centers, values)
        return centers, values

    def _shared_tuning_scale_high(self) -> float:
        data = self.tuning_curve_data
        if data is None:
            return 0.0
        bins = normalize_hd_bin_count(self.tuning_display_bins_var.get())
        smoothing = bool(self.tuning_smoothing_var.get())
        sigma = float(self.tuning_smooth_sigma_var.get())
        cached = self._tuning_scale_cache
        if (
            cached is not None
            and cached[0] is data
            and cached[1:4] == (bins, smoothing, sigma)
        ):
            return float(cached[4])

        high = 0.0
        for cluster_id in data.curves:
            processed = data.processed_for(
                cluster_id,
                bins,
                smoothing=smoothing,
                sigma=sigma,
            )
            if processed is not None:
                high = max(high, tuning_rate_peak(processed[1]))
        self._tuning_scale_cache = (data, bins, smoothing, sigma, high)
        return high

    def _set_tuning_hd_class_label(self, hd_class: int | None) -> None:
        if not hasattr(self, "tuning_hd_class_label"):
            return
        if hd_class == 1:
            self.tuning_hd_class_label.configure(text="1", style="HDClass1.TLabel")
            self.tuning_hd_class_label.grid()
        elif hd_class == 2:
            self.tuning_hd_class_label.configure(text="2", style="HDClass2.TLabel")
            self.tuning_hd_class_label.grid()
        else:
            self.tuning_hd_class_label.configure(text="", style="Panel.TLabel")
            self.tuning_hd_class_label.grid_remove()

    def _show_tuning_provenance(self) -> None:
        data = self.tuning_curve_data
        if data is None:
            return
        metadata = data.metadata
        rows = [("File", data.path.name)]
        if metadata is None:
            rows.extend(
                (
                    ("Schema", "Legacy"),
                    ("Timing / occupancy", "Not recorded"),
                )
            )
        else:
            rows.extend(
                (
                    ("Schema", "2"),
                    ("Timestamp", metadata.timestamp_reference or "Not recorded"),
                    ("Timebase", metadata.timebase or "Not recorded"),
                    ("Direction", metadata.angle_convention_note or "Not recorded"),
                )
            )
            if metadata.feature_fs_hz is not None:
                rows.append(("Tracking", f"{metadata.feature_fs_hz:g} Hz"))
            classification = metadata.classification
            if classification is not None:
                rows.append(("Classification", classification.method or "Not recorded"))
                if classification.rayleigh_alpha is not None:
                    rows.append(("Rayleigh α", f"{classification.rayleigh_alpha:g}"))
                if classification.shuffle_alpha is not None:
                    rows.append(("Shuffle α", f"{classification.shuffle_alpha:g}"))
                if classification.num_shuffle is not None:
                    rows.append(("Shuffles", str(classification.num_shuffle)))
            ttl_qc = metadata.ttl_qc
            if ttl_qc is not None:
                if ttl_qc.ttl_pulse_count is not None:
                    rows.append(("Motive trigger TTLs", str(ttl_qc.ttl_pulse_count)))
                if ttl_qc.measured_rate_hz is not None:
                    rows.append(("Measured rate", f"{ttl_qc.measured_rate_hz:g} Hz"))
                if ttl_qc.median_period_s is not None:
                    rows.append(("Median period", f"{ttl_qc.median_period_s:g} s"))
                if ttl_qc.camera_input_channel is not None:
                    rows.append(("Camera input", str(ttl_qc.camera_input_channel)))
                if ttl_qc.camera_ttl_threshold is not None:
                    rows.append(("TTL threshold", f"{ttl_qc.camera_ttl_threshold:g}"))
                if ttl_qc.camera_ttl_active_high is not None:
                    rows.append(
                        (
                            "TTL polarity",
                            "Active high" if ttl_qc.camera_ttl_active_high else "Active low",
                        )
                    )
                if (
                    ttl_qc.matched_motive_frame_count is not None
                    and ttl_qc.motive_frame_count_raw is not None
                ):
                    rows.append(
                        (
                            "Matched frames",
                            f"{ttl_qc.matched_motive_frame_count} / {ttl_qc.motive_frame_count_raw}",
                        )
                    )
                if ttl_qc.frame_alignment_policy_applied is not None:
                    rows.append(
                        ("Alignment", ttl_qc.frame_alignment_policy_applied)
                    )
                if ttl_qc.dropped_motive_frame_ids:
                    rows.append(
                        (
                            "Dropped frame IDs",
                            ", ".join(str(value) for value in ttl_qc.dropped_motive_frame_ids),
                        )
                    )
                if ttl_qc.frame_timestamp_mapping is not None:
                    rows.append(("Frame mapping", ttl_qc.frame_timestamp_mapping))
        raw_rates = data.rates_for(self._selected_unit_id_value())
        if raw_rates is not None:
            try:
                _angles, rates = self._processed_tuning_values(
                    self._selected_unit_id_value(), raw_rates
                )
            except (ImportError, ValueError):
                rates = ()
            missing_bins = sum(
                not math.isfinite(float(rate)) for rate in rates
            )
            if missing_bins:
                rows.append(("Bins without occupancy", str(missing_bins)))
        label_width = max(len(label) for label, _value in rows)
        messagebox.showinfo(
            "Tuning Provenance",
            "\n".join(f"{label:<{label_width}}   {value}" for label, value in rows),
            parent=self,
        )

    def _draw_tuning_placeholder(self, title: str, detail: str) -> None:
        canvas = self.tuning_curve_canvas
        width = max(canvas.winfo_width(), 280)
        height = max(canvas.winfo_height(), 220)
        offers_attach = title in {"No tuning curves", "Could not load tuning curves"}
        canvas.create_text(
            width / 2,
            height / 2 - 28 if offers_attach else height / 2 - 12,
            text=title,
            fill="#1d1d1f",
            font=("TkDefaultFont", 13, "bold"),
        )
        canvas.create_text(
            width / 2,
            height / 2 + 4 if offers_attach else height / 2 + 22,
            text=detail,
            justify="center",
            fill="#6e6e73",
            font=("TkDefaultFont", 10),
        )
        if offers_attach and hasattr(self, "tuning_attach_button"):
            canvas.create_window(
                width / 2,
                height / 2 + 48,
                window=self.tuning_attach_button,
            )

    def _draw_tuning_curve(self) -> None:
        if (
            not hasattr(self, "tuning_curve_canvas")
            or not self.show_tuning_curve_var.get()
            or self.tuning_collapsed_var.get()
        ):
            return
        self.tuning_curve_status_label.configure(text="")
        self._set_tuning_hd_class_label(None)
        canvas = self.tuning_curve_canvas
        canvas.delete("all")
        cluster_id = self._selected_unit_id_value()
        if hasattr(self, "tuning_cluster_label"):
            self.tuning_cluster_label.configure(text=f"Cluster {cluster_id}")
        filter_status = self._quality_filter_status(cluster_id)
        if filter_status is not None and not self._local_unit_passes_quality_filter(
            cluster_id
        ):
            if hasattr(self, "tuning_provenance_button"):
                self.tuning_provenance_button.grid_remove()
            self._draw_tuning_placeholder("Unit filtered", filter_status)
            return
        data = self.tuning_curve_data
        if data is None:
            if hasattr(self, "tuning_provenance_button"):
                self.tuning_provenance_button.grid_remove()
            detail = (
                "No tuning_curves.tc or tuning_curves.json was found automatically for this "
                f"recording date in Tuning Curve Session "
                f"{self.settings.tuning_curve_session}. Generate it with the analysis pipeline, "
                "or attach a matching file.\nAttach head-direction data "
                "for the selected RF unit."
            )
            if self._optional_drop_available:
                detail += "\nYou can also drop a .tc or tuning JSON file here."
            if self._tuning_curve_error:
                detail += f"\n\n{self._tuning_curve_error}"
                self._draw_tuning_placeholder("Could not load tuning curves", detail)
            else:
                self._draw_tuning_placeholder("No tuning curves", detail)
            return

        if hasattr(self, "tuning_provenance_button"):
            self.tuning_provenance_button.grid()

        if self._app_root._rfm_pairing_enabled:
            ready, eligible = self._pairing_eligibility()
            rf_unit_ids = (
                set(self._quality_filtered_pairing_unit_ids(ready))
                if eligible
                else set(self._local_quality_visible_unit_ids())
            )
        else:
            rf_unit_ids = set(self._local_quality_visible_unit_ids())
        if cluster_id not in rf_unit_ids:
            self._draw_tuning_placeholder(
                f"Cluster {cluster_id} skipped",
                "No open RF map contains this cluster.",
            )
            return
        raw_rates = data.rates_for(cluster_id)
        if raw_rates is None:
            self._draw_tuning_placeholder(
                f"No tuning curve for cluster {cluster_id}",
                "The selected RF unit is not present in this tuning file.",
            )
            return
        self._set_tuning_hd_class_label(data.hd_class_for(cluster_id))
        try:
            angles_deg, rates = self._processed_tuning_values(cluster_id, raw_rates)
            scale_high = (
                self._shared_tuning_scale_high()
                if self.tuning_compare_scale_var.get()
                else tuning_rate_peak(rates)
            )
        except (ImportError, ValueError) as exc:
            self._draw_tuning_placeholder("Could not plot tuning curve", str(exc))
            return

        if self._effective_tuning_plot_mode() == "Polar":
            self._draw_tuning_polar(angles_deg, rates, cluster_id, scale_high)
        else:
            self._draw_tuning_line(angles_deg, rates, cluster_id, scale_high)

    def _draw_tuning_line(
        self,
        angles_deg: Sequence[float],
        rates: Sequence[float],
        cluster_id: int,
        scale_high: float | None = None,
    ) -> None:
        canvas = self.tuning_curve_canvas
        width = max(canvas.winfo_width(), 280)
        height = max(canvas.winfo_height(), 220)
        left, right, top, bottom = 54.0, width - 16.0, 18.0, height - 44.0
        plot_width = max(1.0, right - left)
        plot_height = max(1.0, bottom - top)
        current_high = tuning_rate_peak(rates)
        high = max(
            current_high,
            float(scale_high) if scale_high is not None else current_high,
        )
        denominator = high if high > 1e-12 else 1.0
        centered_angles, centered_rates = center_tuning_curve_on_zero(
            angles_deg,
            rates,
        )
        canvas.create_line(left, top, left, bottom, right, bottom, fill="#98a2b3")
        for angle, label in zip(
            (-180, -90, 0, 90, 180),
            ("180", "90", "0", "270", "180"),
        ):
            x = left + plot_width * (angle + 180.0) / 360.0
            canvas.create_line(x, bottom, x, bottom + 4, fill="#98a2b3")
            canvas.create_text(x, bottom + 16, text=label, fill="#667085", font=("TkDefaultFont", 10))
        tick_fractions = (0.0, 0.5, 1.0) if high > 1e-12 else (0.0,)
        for fraction in tick_fractions:
            y = bottom - plot_height * fraction
            if fraction:
                canvas.create_line(left, y, right, y, fill="#eaecf0", dash=(3, 3))
            canvas.create_text(
                left - 7,
                y,
                anchor="e",
                text=f"{high * fraction:.3g}",
                fill="#667085",
                font=("TkDefaultFont", 10),
            )
        segments: list[list[float]] = []
        points: list[float] = []
        for angle, rate in zip(centered_angles, centered_rates):
            if not math.isfinite(float(rate)):
                if points:
                    segments.append(points)
                    points = []
                continue
            normalized = max(0.0, float(rate)) / denominator
            points.extend(
                (
                    left + plot_width * (float(angle) + 180.0) / 360.0,
                    bottom - plot_height * normalized,
                )
            )
        if points:
            segments.append(points)
        for points in segments:
            if len(points) >= 4:
                canvas.create_line(*points, fill="#1570ef", width=2, joinstyle="round")
            elif len(points) == 2:
                x, y = points
                canvas.create_oval(
                    x - 3,
                    y - 3,
                    x + 3,
                    y + 3,
                    fill="#1570ef",
                    outline="",
                )
        canvas.create_text(
            (left + right) / 2,
            height - 10,
            text="Head direction (deg)",
            fill="#475467",
            font=("TkDefaultFont", 10),
        )
        canvas.create_text(
            12,
            (top + bottom) / 2,
            text="Hz",
            angle=90,
            fill="#475467",
            font=("TkDefaultFont", 10),
        )

    def _draw_tuning_polar(
        self,
        angles_deg: Sequence[float],
        rates: Sequence[float],
        cluster_id: int,
        scale_high: float | None = None,
    ) -> None:
        canvas = self.tuning_curve_canvas
        width = max(canvas.winfo_width(), 280)
        height = max(canvas.winfo_height(), 220)
        center_x = width / 2.0
        center_y = height / 2.0 + 8.0
        radius = max(30.0, min(width, height) / 2.0 - 40.0)
        current_high = tuning_rate_peak(rates)
        radial_high = max(
            current_high,
            float(scale_high) if scale_high is not None else current_high,
        )
        denominator = radial_high if radial_high > 1e-12 else 1.0
        canvas.create_oval(
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
            outline="#c7c9ce",
        )
        for angle, label in ((0, "0°"), (90, "90°"), (180, "180°"), (270, "270°")):
            vector_x, vector_y = head_direction_unit_vector(angle)
            canvas.create_line(
                center_x,
                center_y,
                center_x + vector_x * radius,
                center_y + vector_y * radius,
                fill="#eaecf0",
            )
            canvas.create_text(
                center_x + vector_x * (radius + 15),
                center_y + vector_y * (radius + 15),
                text=label,
                fill="#667085",
                font=("TkDefaultFont", 10),
            )

        # A single labelled radial axis states the scale without implying
        # that decorative rings are measured contours.
        scale_x, scale_y = head_direction_unit_vector(315.0)
        normal_x, normal_y = -scale_y, scale_x
        canvas.create_line(
            center_x,
            center_y,
            center_x + scale_x * radius,
            center_y + scale_y * radius,
            fill="#d2d3d7",
        )
        tick_fractions = (0.0, 0.5, 1.0) if radial_high > 1e-12 else (0.0,)
        for fraction in tick_fractions:
            tick_x = center_x + scale_x * radius * fraction
            tick_y = center_y + scale_y * radius * fraction
            canvas.create_line(
                tick_x - normal_x * 3,
                tick_y - normal_y * 3,
                tick_x + normal_x * 3,
                tick_y + normal_y * 3,
                fill="#8e8e93",
            )
            canvas.create_text(
                tick_x + normal_x * 8,
                tick_y + normal_y * 8,
                anchor="w",
                text=f"{radial_high * fraction:.3g} Hz",
                fill="#6e6e73",
                font=("TkDefaultFont", 10),
            )
        points: list[tuple[float, float] | None] = []
        for angle, rate in zip(angles_deg, rates):
            if not math.isfinite(float(rate)):
                points.append(None)
                continue
            vector_x, vector_y = head_direction_unit_vector(angle)
            scaled = radius * max(0.0, float(rate)) / denominator
            points.append((center_x + vector_x * scaled, center_y + vector_y * scaled))
        finite_points = [point for point in points if point is not None]
        if radial_high <= 1e-12 and finite_points:
            canvas.create_oval(
                center_x - 3,
                center_y - 3,
                center_x + 3,
                center_y + 3,
                fill="#1570ef",
                outline="",
            )
        elif len(finite_points) >= 3 and len(finite_points) == len(points):
            flattened = [coordinate for point in finite_points for coordinate in point]
            canvas.create_line(
                *flattened,
                *finite_points[0],
                fill="#1570ef",
                width=2,
                joinstyle="round",
            )
        elif len(finite_points) <= 2:
            for x, y in finite_points:
                canvas.create_oval(
                    x - 3,
                    y - 3,
                    x + 3,
                    y + 3,
                    fill="#1570ef",
                    outline="",
                )
        else:
            segments: list[list[tuple[float, float]]] = []
            segment: list[tuple[float, float]] = []
            for point in points:
                if point is None:
                    if segment:
                        segments.append(segment)
                        segment = []
                else:
                    segment.append(point)
            if segment:
                segments.append(segment)
            if points[0] is not None and points[-1] is not None and len(segments) > 1:
                segments[0] = segments[-1] + segments[0]
                segments.pop()
            for segment in segments:
                flattened = [coordinate for point in segment for coordinate in point]
                if len(segment) >= 2:
                    canvas.create_line(
                        *flattened,
                        fill="#1570ef",
                        width=2,
                        joinstyle="round",
                    )
                else:
                    x, y = segment[0]
                    canvas.create_oval(
                        x - 3,
                        y - 3,
                        x + 3,
                        y + 3,
                        fill="#1570ef",
                        outline="",
                    )

    def _infer_attached_channels_path(self, positions_path: Path) -> Path | None:
        sibling = positions_path.with_name("channels.csv")
        if sibling.is_file():
            return sibling
        probe_name = positions_path.parent.name
        for ancestor in positions_path.parents:
            if ancestor.name == "spike_position":
                candidate = ancestor.parent / "waveform" / probe_name / "channels.csv"
                return candidate if candidate.is_file() else None
        return None

    def _attach_probe_geometry(self) -> None:
        if not self.show_probe_layout_var.get():
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="Attach probe positions",
            initialdir=str(self.data.path.parent),
            filetypes=PROBE_POSITION_FILETYPES,
        )
        if not path:
            return
        self._load_probe_geometry_path(Path(path))

    def _probe_to_canvas(self, x_um: float, y_um: float) -> tuple[float, float] | None:
        transform = self._probe_canvas_transform
        if transform is None:
            return None
        x_min, y_min, x_scale, y_scale = transform
        height = max(self.probe_canvas.winfo_height(), 2)
        margin = 14.0
        return margin + (x_um - x_min) * x_scale, height - margin - (y_um - y_min) * y_scale

    def _canvas_to_probe(self, canvas_x: float, canvas_y: float) -> tuple[float, float] | None:
        transform = self._probe_canvas_transform
        if transform is None:
            return None
        x_min, y_min, x_scale, y_scale = transform
        if x_scale <= 0 or y_scale <= 0:
            return None
        height = max(self.probe_canvas.winfo_height(), 2)
        margin = 14.0
        return (
            x_min + (canvas_x - margin) / x_scale,
            y_min + (height - margin - canvas_y) / y_scale,
        )

    def _draw_probe_canvas(self) -> None:
        if not hasattr(self, "probe_canvas"):
            return
        if self.probe_collapsed_var.get():
            return
        canvas = self.probe_canvas
        if not self.show_probe_layout_var.get():
            return
        geometry = self.probe_geometry
        compact = geometry is None
        requested_height = 170 if compact else 330
        self.probe_section.rowconfigure(1, weight=0 if compact else 1)
        self.sidebar_frame.rowconfigure(
            self._probe_section_row,
            weight=0 if compact else 1,
        )
        if int(float(canvas.cget("height"))) != requested_height:
            canvas.configure(height=requested_height)
        width = max(canvas.winfo_width(), 220)
        height = max(canvas.winfo_height(), 200)
        if geometry is None:
            self._probe_canvas_transform = None
            detail = "Geometry is optional"
            if getattr(self, "_optional_drop_available", False):
                detail += " · drop is supported"
            signature = ("missing", width, height, detail)
            if signature != self._probe_static_signature:
                canvas.delete("all")
                canvas.create_text(
                    width / 2,
                    height / 2 - 34,
                    text="No probe geometry",
                    justify="center",
                    fill="#1d1d1f",
                    font=("TkDefaultFont", 12, "bold"),
                    tags=("probe-static",),
                )
                canvas.create_text(
                    width / 2,
                    height / 2 - 8,
                    text=detail,
                    justify="center",
                    fill="#6e6e73",
                    font=("TkDefaultFont", 10),
                    tags=("probe-static",),
                )
                if hasattr(self, "probe_attach_button"):
                    canvas.create_window(
                        width / 2,
                        height / 2 + 30,
                        window=self.probe_attach_button,
                        tags=("probe-static",),
                    )
                self._probe_static_signature = signature
            self.spatial_status_label.configure(text="Geometry optional")
            self.clear_spatial_button.state(["disabled"])
            return

        available = set(self._local_quality_visible_unit_ids())
        units = [unit for unit in geometry.units if unit.unit_id in available]
        points = [(channel.x_um, channel.y_um) for channel in geometry.channels]
        positioned_units = [
            unit
            for unit in units
            if unit.x_um is not None and unit.y_um is not None
        ]
        points.extend(
            (float(unit.x_um), float(unit.y_um))
            for unit in positioned_units
        )
        selected_id = self._selected_unit_id_value()
        selected = (
            geometry.units_by_id.get(selected_id)
            if selected_id in available
            else None
        )
        if not points:
            self._probe_canvas_transform = None
            signature = ("no-matches", id(geometry), width, height)
            if signature != self._probe_static_signature:
                canvas.delete("all")
                canvas.create_text(
                    width / 2,
                    height / 2 + 42,
                    text=(
                        "Geometry has no finite positions"
                        if units
                        else "Geometry has no visible units"
                    ),
                    fill="#667085",
                    tags=("probe-static",),
                )
                self._probe_static_signature = signature
            canvas.delete("probe-selection")
            if (
                self.spatial_region is None
                and selected is not None
                and selected.x_um is None
                and selected.y_um is None
            ):
                canvas.create_text(
                    width / 2,
                    height / 2,
                    text="NaN",
                    fill="#b42318",
                    font=("TkDefaultFont", 24, "bold"),
                    tags=("probe-selection",),
                )
            if self.spatial_region is None:
                self.spatial_status_label.configure(
                    text=f"{geometry.probe_name} · 0/{len(units)} units positioned"
                )
                self.clear_spatial_button.state(["disabled"])
            else:
                self.spatial_status_label.configure(text="No units in region")
                self.clear_spatial_button.state(["!disabled"])
            return
        region_ids = set(self._unit_navigation_ids()) if self.spatial_region is not None else set()
        signature = (
            id(geometry),
            id(self.data),
            width,
            height,
            self.spatial_region,
            frozenset(region_ids),
        )
        if signature != self._probe_static_signature:
            canvas.delete("all")
            xs, ys = zip(*points)
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            if x_max <= x_min:
                x_min, x_max = x_min - 1.0, x_max + 1.0
            if y_max <= y_min:
                y_min, y_max = y_min - 1.0, y_max + 1.0
            margin = 14.0
            self._probe_canvas_transform = (
                x_min,
                y_min,
                (width - margin * 2.0) / (x_max - x_min),
                (height - margin * 2.0) / (y_max - y_min),
            )
            shank_colors = ("#98a2b3", "#7f8ea3", "#667085", "#475467")
            for channel in geometry.channels:
                point = self._probe_to_canvas(channel.x_um, channel.y_um)
                if point is None:
                    continue
                x, y = point
                color = shank_colors[channel.shank_id % len(shank_colors)]
                canvas.create_rectangle(
                    x - 2,
                    y - 2,
                    x + 2,
                    y + 2,
                    fill=color,
                    outline="",
                    tags=("probe-static",),
                )
            for unit in positioned_units:
                point = self._probe_to_canvas(
                    float(unit.x_um), float(unit.y_um)
                )
                if point is None:
                    continue
                x, y = point
                in_region = unit.unit_id in region_ids
                fill = "#f79009" if in_region else "#2e90fa"
                radius = 4 if in_region else 3
                canvas.create_oval(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    fill=fill,
                    outline="#ffffff",
                    tags=("probe-static",),
                )
            if self.spatial_region is not None:
                top_left = self._probe_to_canvas(
                    self.spatial_region.x_min, self.spatial_region.y_max
                )
                bottom_right = self._probe_to_canvas(
                    self.spatial_region.x_max, self.spatial_region.y_min
                )
                if top_left is not None and bottom_right is not None:
                    canvas.create_rectangle(
                        *top_left,
                        *bottom_right,
                        outline="#f04438",
                        width=2,
                        dash=(5, 3),
                        tags=("probe-static",),
                    )
            self._probe_static_signature = signature

        canvas.delete("probe-selection")
        if (
            selected is not None
            and selected.x_um is not None
            and selected.y_um is not None
            and (
            self.spatial_region is None or selected_id in region_ids
            )
        ):
            point = self._probe_to_canvas(
                float(selected.x_um), float(selected.y_um)
            )
            if point is not None:
                x, y = point
                canvas.create_oval(
                    x - 7,
                    y - 7,
                    x + 7,
                    y + 7,
                    outline="#d92d20",
                    width=2,
                    tags=("probe-selection",),
                )
        if (
            self.spatial_region is None
            and selected is not None
            and selected.x_um is None
            and selected.y_um is None
        ):
            canvas.create_text(
                width / 2,
                height / 2,
                text="NaN",
                fill="#b42318",
                font=("TkDefaultFont", 24, "bold"),
                tags=("probe-selection",),
            )
        count = (
            len(region_ids)
            if self.spatial_region is not None
            else len(positioned_units)
        )
        if self.spatial_region is None:
            status = (
                f"{geometry.probe_name} · {count}/{len(units)} units positioned"
            )
            self.clear_spatial_button.state(["disabled"])
        elif count:
            status = f"{count} unit{'s' if count != 1 else ''} in region"
            self.clear_spatial_button.state(["!disabled"])
        else:
            status = "No units in region"
            self.clear_spatial_button.state(["!disabled"])
        self.spatial_status_label.configure(text=status)

    def _on_probe_press(self, event: tk.Event) -> None:
        point = self._canvas_to_probe(float(event.x), float(event.y))
        self._probe_drag_start = point
        self._probe_press_canvas = (float(event.x), float(event.y))
        self._probe_drag_moved = False

    def _on_probe_drag(self, event: tk.Event) -> None:
        start_canvas = getattr(self, "_probe_press_canvas", None)
        if start_canvas is None:
            return
        self._probe_drag_moved = math.hypot(event.x - start_canvas[0], event.y - start_canvas[1]) >= 4.0

    def _on_probe_release(self, event: tk.Event) -> None:
        start = self._probe_drag_start
        end = self._canvas_to_probe(float(event.x), float(event.y))
        self._probe_drag_start = None
        if self.probe_geometry is None and not self._probe_drag_moved:
            self._attach_probe_geometry()
            return
        if start is None or end is None or self.probe_geometry is None:
            return
        if self._probe_drag_moved:
            region = SpatialRegion.from_corners(start[0], start[1], end[0], end[1])
        else:
            nearest: tuple[float, ProbeChannel] | None = None
            for channel in self.probe_geometry.channels:
                point = self._probe_to_canvas(channel.x_um, channel.y_um)
                if point is None:
                    continue
                distance = math.hypot(event.x - point[0], event.y - point[1])
                if nearest is None or distance < nearest[0]:
                    nearest = distance, channel
            if nearest is None or nearest[0] > 14.0:
                return
            channel = nearest[1]
            region = SpatialRegion.centered(channel.x_um, channel.y_um)
        self._apply_spatial_region(region)

    def _apply_spatial_region(self, region: SpatialRegion) -> None:
        self.spatial_region = region
        eligible = self._unit_navigation_ids()
        if eligible:
            selected = self._selected_unit_id_value()
            if selected not in eligible:
                center_x = (region.x_min + region.x_max) / 2.0
                center_y = (region.y_min + region.y_max) / 2.0
                positions = self.probe_geometry.units_by_id if self.probe_geometry is not None else {}
                target = min(
                    eligible,
                    key=lambda unit_id: (
                        (float(positions[unit_id].x_um) - center_x) ** 2
                        + (float(positions[unit_id].y_um) - center_y) ** 2
                        if unit_id in positions
                        and positions[unit_id].x_um is not None
                        and positions[unit_id].y_um is not None
                        else math.inf
                    ),
                )
                self._set_selected_unit_id(target)
        else:
            self.unit_idx.set(-1)
        self.selected_cell = None
        self._sync_unit_combo()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _clear_spatial_filter(self) -> None:
        if self.spatial_region is None:
            return
        self.spatial_region = None
        self._reconcile_unit_filter_selection()
        self._sync_unit_combo()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _sync_unit_combo(self) -> None:
        unit_ids = self._unit_navigation_ids()
        self._unit_combo_unit_ids = unit_ids
        values: list[str] = []
        for unit_id in unit_ids:
            local_index = self._local_unit_index(unit_id)
            if local_index is None:
                values.append(f"N/A  cluster {unit_id} — not in this session")
            elif not self._local_unit_passes_quality_filter(unit_id):
                values.append(f"N/A  cluster {unit_id} — hidden by RF-bin filter")
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

    def _step_time_resolution(self, delta_groups: float) -> None:
        try:
            current = float(self.time_res_ms_var.get())
        except (tk.TclError, TypeError, ValueError):
            current = self._base_bin_ms()
        source_bin_ms = self._base_bin_ms()
        target = max(
            source_bin_ms,
            min(self._total_time_ms(), current + delta_groups * source_bin_ms),
        )
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
            self.value_mode_var.set(VALUE_MODE_RATE)
            return
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _on_range_changed(self, _event: object | None = None) -> None:
        self._normalize_control_values()
        self._update_all()
        self._publish_pairing_state_if_changed()

    def _reset_plot_range(self) -> None:
        start_ms, end_ms = self._default_plot_time_bounds_ms(difference=self.rf_subtract_var.get())
        self.range_start_ms_var.set(format_ms(start_ms))
        self.range_end_ms_var.set(format_ms(end_ms))
        self.subtract_start_ms_var.set(format_ms(self.settings.rf_subtract_start_ms))
        self.subtract_end_ms_var.set(format_ms(self.settings.rf_subtract_end_ms))
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
        if self._pair_apply_in_progress:
            return
        self._normalize_control_values()
        self._update_all(update_optional_views=False)
        self._publish_pairing_state_if_changed()

    def _on_spatial_format_changed(self) -> None:
        self._timeline_preview_cache_key = None
        self._timeline_preview_images = {}
        self._on_control_changed()

    def _on_tab_changed(self, _event: object | None = None) -> None:
        if not self._pair_apply_in_progress:
            self._update_all()
            self._publish_pairing_state_if_changed()

    def _sync_context_controls(self) -> None:
        if not hasattr(self, "rgb_mode_toggle"):
            return
        for frame in (
            self.range_controls_frame,
            self.delay_controls_frame,
            self.timeline_context_frame,
        ):
            frame.grid_remove()
        tab = self._active_tab_key()
        if tab == "delay":
            self.delay_controls_frame.grid(row=0, column=7, sticky="w")
            self.rgb_mode_toggle.state(["!disabled"])
        elif tab == "timeline":
            self.timeline_context_frame.grid(row=0, column=7, sticky="w")
            self.rgb_mode_toggle.state(["disabled"])
        else:
            difference = self.rf_subtract_var.get()
            second_row = self._rf_range_uses_second_row()
            self.range_controls_frame.grid(
                row=1 if second_row else 0,
                column=0 if second_row else 7,
                columnspan=9 if second_row else 1,
                sticky="w",
                pady=(7, 0) if second_row else 0,
            )
            for widget in (
                self.range_open_label, self.range_start_unit_label,
                self.subtract_controls_frame,
            ):
                widget.grid() if difference else widget.grid_remove()
            self.range_end_unit_label.configure(text="ms)" if difference else "ms")
            self.rgb_mode_toggle.state(["disabled"])
        self._sync_display_controls()

    def _rf_range_uses_second_row(self) -> bool:
        # Keep all four time inputs and the display toggle reachable at the
        # minimum window width, including when returning to ordinary sum mode.
        return self.rf_subtract_var.get() or self.plot_controls_frame.winfo_width() < 1100

    def _schedule_redraw(self, _event: object | None = None) -> None:
        if self._redraw_after is not None:
            self.after_cancel(self._redraw_after)
        self._redraw_after = self.after(40, self._run_scheduled_redraw)

    def _run_scheduled_redraw(self) -> None:
        self._redraw_after = None
        self._draw_active_tab()

    def _schedule_optional_redraw(self, view: str) -> None:
        if view not in {"probe", "tuning"}:
            return
        self._optional_redraw_dirty.add(view)
        if self._optional_redraw_after is not None:
            try:
                self.after_cancel(self._optional_redraw_after)
            except tk.TclError:
                pass
        self._optional_redraw_after = self.after(
            60, self._run_scheduled_optional_redraw
        )

    def _run_scheduled_optional_redraw(self) -> None:
        self._optional_redraw_after = None
        dirty = set(self._optional_redraw_dirty)
        self._optional_redraw_dirty.clear()
        if "probe" in dirty:
            self._draw_probe_canvas()
        if "tuning" in dirty and self._active_tab_key() == "rf":
            self._draw_tuning_curve()

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
        self._source_bins_for_subtract_controls()
        if self._timeline_range_anchor is not None:
            self._timeline_range_anchor = max(0, min(max_bin, self._timeline_range_anchor))
        self._x_target_bins()
        self._y_target_bins()
        self._smooth_radius()
        self._sync_time_control_ranges()
        self._last_time_group_count = time_count
        self._last_time_groups = list(time_groups)
        selected_cell = self.selected_cell
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

    def _default_plot_time_bounds_ms(self, *, difference: bool | None = None) -> tuple[float, float]:
        settings = self.settings
        difference = settings.rf_subtract if difference is None else difference
        start, end = self._snap_time_range_to_bins(
            settings.rf_difference_start_ms if difference else settings.rf_sum_start_ms,
            settings.rf_difference_end_ms if difference else settings.rf_sum_end_ms,
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

    def _source_bins_for_time_controls(
        self,
        start_var: tk.StringVar | None = None,
        end_var: tk.StringVar | None = None,
    ) -> AxisGroup:
        start_var = self.range_start_ms_var if start_var is None else start_var
        end_var = self.range_end_ms_var if end_var is None else end_var
        edges_ms = [edge * 1000.0 for edge in self.data.time_bin_edges]
        axis_start, axis_end = edges_ms[0], edges_ms[-1]
        requested_start = self._parse_time_control(start_var, axis_start)
        requested_end = self._parse_time_control(end_var, axis_end)
        start, end = self._snap_time_range_to_bins(requested_start, requested_end)
        start_edge, end_edge = start, end + 1
        start_var.set(format_ms(edges_ms[start_edge]))
        end_var.set(format_ms(edges_ms[end_edge]))
        return start, end

    def _source_bins_for_subtract_controls(self) -> AxisGroup:
        return self._source_bins_for_time_controls(
            self.subtract_start_ms_var, self.subtract_end_ms_var,
        )

    def _rf_subtraction_range(self) -> AxisGroup | None:
        if not self.rf_subtract_var.get():
            return None
        return self._source_bins_for_subtract_controls()

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

    def _draw_active_tab(self, *, update_optional_views: bool = True) -> None:
        key = self._active_tab_key()
        if self._selected_local_unit_index() is None:
            self._draw_unavailable_unit(key)
            if key == "rf":
                self._draw_tuning_curve()
            if self.show_waveform_var.get():
                unavailable_text = (
                    f"Cluster {self._selected_unit_id_value()} · waveform unavailable"
                )
                self.waveform_subtitle_label.configure(text=unavailable_text)
                self._draw_unavailable_unit("waveform")
                if self._waveform_zoomed:
                    self.waveform_zoom_subtitle_label.configure(
                        text=unavailable_text
                    )
                    self._draw_unavailable_unit(
                        "waveform",
                        canvas=self.waveform_zoom_canvas,
                    )
            return
        if key == "rf":
            self._draw_rf()
            if update_optional_views:
                self._draw_tuning_curve()
        elif key == "delay":
            self._draw_rgb() if self.rgb_mode_var.get() else self._draw_delay()
        elif key == "timeline":
            self._draw_timeline()
        if update_optional_views and self.show_waveform_var.get():
            self._draw_waveform()

    def _request_waveform_payload(self) -> None:
        if not self.show_waveform_var.get():
            return
        key = (
            self._selected_unit_id_value(),
            self.waveform_channel_mode_var.get(),
        )
        if self._waveform_loading_key == key:
            return
        if self._waveform_payload_key == key or self._waveform_error_key == key:
            if self._waveform_loading_key is not None:
                self._waveform_generation += 1
                self._waveform_loading_key = None
                self._waveform_pending_request = None
            return
        self._waveform_generation += 1
        self._waveform_loading_key = key
        self._waveform_pending_request = (self._waveform_generation, self.data, key)
        self._start_waveform_load()
        self._schedule_waveform_result_poll()

    def _start_waveform_load(self) -> None:
        if self._waveform_worker_running or self._waveform_pending_request is None:
            return
        generation, data, key = self._waveform_pending_request
        self._waveform_pending_request = None
        if self._quitting or generation != self._waveform_generation:
            return
        self._waveform_worker_running = True
        result_queue = self._waveform_result_queue

        def load() -> None:
            payload = None
            error = None
            try:
                payload = data.waveform_plot_payload(key[0], key[1])
            except Exception as exc:
                error = str(exc)
            finally:
                result_queue.put(WaveformLoadResult(generation, data.path, key, payload, error))

        threading.Thread(
            target=load,
            name=f"rfmapping-waveform-{generation}",
            daemon=True,
        ).start()

    def _schedule_waveform_result_poll(self) -> None:
        if self._waveform_poll_after is None:
            self._waveform_poll_after = self.after(
                30, self._poll_waveform_results
            )

    def _poll_waveform_results(self) -> None:
        self._waveform_poll_after = None
        current: WaveformLoadResult | None = None
        while True:
            try:
                result = self._waveform_result_queue.get_nowait()
            except queue.Empty:
                break
            self._waveform_worker_running = False
            if result.generation == self._waveform_generation:
                current = result
        self._start_waveform_load()
        if current is None:
            if (
                self._waveform_loading_key is not None or self._waveform_worker_running
            ) and not self._quitting:
                self._schedule_waveform_result_poll()
            return
        if (
            current.data_path != self.data.path
            or not self._viewer_ready
        ):
            return
        self._waveform_loading_key = None
        if current.payload is not None:
            self.waveform_payload = current.payload
            self._waveform_payload_key = current.key
            self._waveform_error = None
            self._waveform_error_key = None
        else:
            self.waveform_payload = None
            self._waveform_payload_key = None
            self._waveform_error = current.error or "Waveform data is unavailable."
            self._waveform_error_key = current.key
        if self.show_waveform_var.get():
            self._draw_waveform()

    @staticmethod
    def _draw_waveform_message(
        canvas: tk.Canvas,
        heading: str,
        detail: str,
    ) -> None:
        width = max(canvas.winfo_width(), 220)
        height = max(canvas.winfo_height(), 165)
        canvas.create_text(
            width / 2,
            height / 2 - 14,
            text=heading,
            fill="#667085",
            font=("TkDefaultFont", 16, "bold"),
        )
        canvas.create_text(
            width / 2,
            height / 2 + 25,
            text=detail,
            fill="#667085",
            font=("TkDefaultFont", 9),
            width=max(180, width - 60),
            justify="center",
        )

    def _draw_waveform(self) -> None:
        self._request_waveform_payload()
        targets = [(self.waveform_canvas, self.waveform_subtitle_label)]
        if self._waveform_zoomed:
            targets.append(
                (self.waveform_zoom_canvas, self.waveform_zoom_subtitle_label)
            )
        for canvas, subtitle_label in targets:
            self._draw_waveform_canvas(canvas, subtitle_label)

    def _draw_waveform_canvas(
        self,
        canvas: tk.Canvas,
        subtitle_label: ttk.Label,
    ) -> None:
        canvas.delete("all")
        if not self.show_waveform_var.get():
            subtitle_label.configure(text="")
            return
        key = (
            self._selected_unit_id_value(),
            self.waveform_channel_mode_var.get(),
        )
        if self._waveform_payload_key != key:
            if self._waveform_error_key == key:
                subtitle_label.configure(
                    text=f"Cluster {key[0]} · waveform unavailable"
                )
                self._draw_waveform_message(
                    canvas,
                    "N/A",
                    self._waveform_error or "Waveform data is unavailable.",
                )
                return
            subtitle_label.configure(
                text=f"Cluster {key[0]} · loading waveform artifact…"
            )
            self._draw_waveform_message(
                canvas,
                "Loading…",
                "Reading the selected unit's precomputed average template.",
            )
            return

        payload = self.waveform_payload
        if payload is None:
            self._draw_waveform_message(
                canvas, "N/A", "Waveform data is unavailable."
            )
            return
        matrix_raw = payload.get("matrix")
        times_raw = payload.get("times_ms", payload.get("time_ms"))
        labels_raw = payload.get("channel_labels")
        if hasattr(matrix_raw, "tolist"):
            matrix_raw = matrix_raw.tolist()
        if hasattr(times_raw, "tolist"):
            times_raw = times_raw.tolist()
        if hasattr(labels_raw, "tolist"):
            labels_raw = labels_raw.tolist()
        if not (
            isinstance(matrix_raw, Sequence)
            and not isinstance(matrix_raw, (str, bytes))
            and isinstance(times_raw, Sequence)
            and not isinstance(times_raw, (str, bytes))
            and isinstance(labels_raw, Sequence)
            and not isinstance(labels_raw, (str, bytes))
        ):
            self._draw_waveform_message(
                canvas, "N/A", "The waveform payload is incomplete."
            )
            return
        try:
            matrix = [
                [float(value) for value in row]
                for row in matrix_raw
            ]
            times = [float(value) for value in times_raw]
            labels = [str(value).split(" · ", 1)[0] for value in labels_raw]
        except (TypeError, ValueError):
            self._draw_waveform_message(
                canvas, "N/A", "The waveform payload contains invalid values."
            )
            return
        if (
            not matrix
            or len(labels) != len(matrix)
            or len(times) < 2
            or any(len(row) != len(times) for row in matrix)
            or not all(math.isfinite(value) for value in times)
            or any(right <= left for left, right in zip(times, times[1:]))
        ):
            self._draw_waveform_message(
                canvas, "N/A", "The waveform payload has inconsistent dimensions."
            )
            return

        amplitude_limit = max(
            (
                abs(value)
                for row in matrix
                for value in row
                if math.isfinite(value)
            ),
            default=0.0,
        )
        configured_limit = payload.get("amplitude_limit_uv")
        if isinstance(configured_limit, (int, float)) and math.isfinite(
            float(configured_limit)
        ):
            amplitude_limit = max(amplitude_limit, abs(float(configured_limit)))
        amplitude_limit = max(amplitude_limit, 1e-9)
        best_row_raw = payload.get(
            "best_channel_row", payload.get("best_row_index", -1)
        )
        best_row = int(best_row_raw) if isinstance(best_row_raw, int) else -1

        width = max(canvas.winfo_width(), 220)
        height = max(canvas.winfo_height(), 165)
        compact = width < 280
        margin_l = 48 if compact else 58
        margin_r = 54 if compact else 68
        margin_t, margin_b = 16, 42
        grid_w = max(70.0, width - margin_l - margin_r)
        grid_h = max(80.0, height - margin_t - margin_b)
        x0, y0 = float(margin_l), float(margin_t)
        cell_w = grid_w / len(times)
        cell_h = grid_h / len(matrix)
        for row_index, row in enumerate(matrix):
            top = y0 + row_index * cell_h
            for sample_index, value in enumerate(row):
                left = x0 + sample_index * cell_w
                canvas.create_rectangle(
                    left,
                    top,
                    left + cell_w + 0.5,
                    top + cell_h + 0.5,
                    fill=waveform_color(value, amplitude_limit),
                    outline="",
                )
            label_color = "#b42318" if row_index == best_row else "#475467"
            canvas.create_text(
                x0 - 12,
                top + cell_h / 2,
                anchor="e",
                text=("★ " if row_index == best_row else "") + labels[row_index],
                fill=label_color,
                font=("TkFixedFont", 8, "bold" if row_index == best_row else "normal"),
            )
            if row_index == best_row:
                canvas.create_rectangle(
                    x0,
                    top,
                    x0 + grid_w,
                    top + cell_h,
                    outline="#b42318",
                    width=2,
                )
        canvas.create_rectangle(
            x0, y0, x0 + grid_w, y0 + grid_h, outline="#344054", width=1
        )

        time_span = times[-1] - times[0]
        if time_span > 0.0 and times[0] <= 0.0 <= times[-1]:
            zero_x = x0 + (
                (0.0 - times[0]) / time_span
            ) * max(0.0, grid_w - cell_w) + cell_w / 2.0
            canvas.create_line(
                zero_x,
                y0,
                zero_x,
                y0 + grid_h,
                fill="#111827",
                dash=(5, 4),
                width=2,
            )
        tick_values = [times[0], 0.0, times[-1]]
        for value in tick_values:
            if not times[0] <= value <= times[-1]:
                continue
            x = x0 + ((value - times[0]) / time_span) * max(
                0.0, grid_w - cell_w
            ) + cell_w / 2.0
            canvas.create_line(
                x, y0 + grid_h, x, y0 + grid_h + 5, fill="#475467"
            )
            canvas.create_text(
                x,
                y0 + grid_h + 19,
                text=f"{value:g}",
                fill="#475467",
                font=("TkDefaultFont", 8),
            )
        canvas.create_text(
            x0 + grid_w / 2,
            y0 + grid_h + 34,
            text="Time from spike (ms)",
            fill="#475467",
            font=("TkDefaultFont", 8),
        )

        colorbar_gap = 10 if compact else 28
        colorbar_width = 11 if compact else 16
        colorbar_label_gap = 5 if compact else 7
        colorbar_x = x0 + grid_w + colorbar_gap
        colorbar_h = min(120.0, grid_h)
        steps = 80
        for index in range(steps):
            value = amplitude_limit * (1.0 - 2.0 * index / max(1, steps - 1))
            top = y0 + colorbar_h * index / steps
            bottom = y0 + colorbar_h * (index + 1) / steps
            canvas.create_rectangle(
                colorbar_x,
                top,
                colorbar_x + colorbar_width,
                bottom,
                fill=waveform_color(value, amplitude_limit),
                outline="",
            )
        canvas.create_rectangle(
            colorbar_x,
            y0,
            colorbar_x + colorbar_width,
            y0 + colorbar_h,
            outline="#475467",
        )
        for value, top in (
            (amplitude_limit, y0),
            (0.0, y0 + colorbar_h / 2),
            (-amplitude_limit, y0 + colorbar_h),
        ):
            canvas.create_text(
                colorbar_x + colorbar_width + colorbar_label_gap,
                top,
                anchor="w",
                text=f"{value:.3g}",
                fill="#475467",
                font=("TkDefaultFont", 7),
            )
        canvas.create_text(
            colorbar_x,
            y0 - 16,
            anchor="w",
            text="µV",
            fill="#475467",
            font=("TkDefaultFont", 8),
        )
        mode_label = WAVEFORM_CHANNEL_MODE_LABELS.get(
            key[1], key[1]
        )
        max_ptp = payload.get("max_ptp_uv")
        ptp_text = (
            f" · max PTP {float(max_ptp):.3g} µV"
            if isinstance(max_ptp, (int, float))
            and math.isfinite(float(max_ptp))
            else ""
        )
        subtitle_label.configure(
            text=(
                f"Cluster {key[0]} · {mode_label} · "
                f"best + {len(matrix) - 1} nearest{ptp_text}"
            )
        )

    def _draw_unavailable_unit(
        self,
        key: str,
        *,
        canvas: tk.Canvas | None = None,
    ) -> None:
        canvas = self.canvases[key] if canvas is None else canvas
        canvas.delete("all")
        if canvas is self.canvases.get(key):
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
        no_spatial_matches = (
            self.spatial_region is not None and not self._unit_navigation_ids()
        )
        filter_status = self._quality_filter_status(unit_id)
        loading_message = self._unit_loading_message()
        canvas.create_text(
            width / 2,
            height / 2 - 14,
            text="Loading…" if loading_message else "N/A",
            fill="#667085",
            font=("TkDefaultFont", 28, "bold"),
        )
        canvas.create_text(
            width / 2,
            height / 2 + 26,
            text=(
                "No units are inside the selected probe region."
                if no_spatial_matches
                else (
                    loading_message or filter_status
                    or f"Cluster {unit_id} is not available in this session."
                )
            ),
            fill="#667085",
            font=("TkDefaultFont", 12),
        )

    def _update_all(self, *, update_optional_views: bool = True) -> None:
        if self._redraw_after is not None:
            self.after_cancel(self._redraw_after)
            self._redraw_after = None
        self._normalize_control_values()
        if update_optional_views:
            self._reconcile_unit_filter_selection()
        self.hover_cell = None
        self._hover_signature = None
        self._hover_tooltip_text = ""
        unit_idx = self._selected_local_unit_index()
        cluster_id = self._selected_unit_id_value()
        if unit_idx is None:
            self.selected_cell = None
            loading_message = self._unit_loading_message()
            self.header_label.configure(text=f"Unit N/A / cluster {cluster_id}")
            no_spatial_matches = (
                self.spatial_region is not None and not self._unit_navigation_ids()
            )
            filter_status = self._quality_filter_status(cluster_id)
            self.status_label.configure(
                text=(
                    "No units match the probe region."
                    if no_spatial_matches
                    else (
                        self._unit_loading_message() or filter_status
                        or f"N/A: cluster {cluster_id} is not available in this session."
                    )
                )
            )
            self.unit_stats_label.configure(
                text=(
                    loading_message or (
                        "N/A\nHidden by the zero-spike RF-bin filter."
                        if filter_status
                        else "N/A\nThis unit is available only in another paired window."
                    )
                )
            )
            self.cell_label.configure(text="Loading…" if loading_message else "N/A for this session")
            self._sync_context_controls()
            self._draw_probe_canvas()
            self._draw_active_tab()
            return

        self.header_label.configure(text=f"Unit {unit_idx:03d} / cluster {cluster_id}")
        self.status_label.configure(text="")
        self.unit_stats_label.configure(text="")
        self._update_cell_label()
        self._sync_context_controls()
        if update_optional_views:
            self._draw_probe_canvas()
        self._draw_active_tab(update_optional_views=update_optional_views)

    def _current_matrix(self) -> list[list[float | None]]:
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return [[None for _x in range(self.data.n_x)] for _y in range(self.data.n_y)]
        start, end = self._source_bins_for_display_range()
        matrix = self.data.response_matrix(
            unit_idx,
            start,
            end,
            self.value_mode_var.get(),
        )
        subtract_range = self._rf_subtraction_range()
        if subtract_range is not None:
            matrix = subtract_response_matrices(
                matrix,
                self.data.response_matrix(unit_idx, *subtract_range, self.value_mode_var.get()),
            )
        return matrix

    def _delay_matrix_for_time_groups(self, floor: float = 0.0) -> list[list[float | None]]:
        delay, _entropy, _x_groups, _y_groups = self._grouped_temporal_metric_matrices(
            floor,
            smooth=False,
        )
        return delay

    def _base_bin_ms(self) -> float:
        edges = self.data.time_bin_edges
        cached = self._base_bin_cache
        if cached is not None and cached[0] is edges:
            return cached[1]
        diffs = [
            (edges[i + 1] - edges[i]) * 1000.0
            for i in range(len(edges) - 1)
        ]
        positive = [diff for diff in diffs if diff > 1e-9]
        base = min(positive) if positive else 1.0
        self._base_bin_cache = (edges, base)
        return base

    def _time_axis_start_ms(self) -> float:
        return self.data.time_bin_edges[0] * 1000.0

    def _time_axis_end_ms(self) -> float:
        return self.data.time_bin_edges[-1] * 1000.0

    def _time_axis_range_ms(self) -> tuple[float, float]:
        return self._time_axis_start_ms(), self._time_axis_end_ms()

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

    def _time_groups(self) -> tuple[AxisGroup, ...]:
        # Loaded axes are immutable for a document's lifetime. Keep the axis
        # object in the key so switching documents cannot reuse stale groups.
        edges = self.data.time_bin_edges
        try:
            requested = self.time_res_ms_var.get()
        except (tk.TclError, ValueError):
            requested = None
        cached = self._time_groups_cache
        if cached is not None and cached[0] is edges and cached[1] == requested:
            return cached[2]
        group_size = self._time_group_size()
        target_duration_ms = group_size * self._base_bin_ms()
        groups = tuple(
            physical_time_groups(
                [edge * 1000.0 for edge in edges],
                target_duration_ms,
            )
        )
        self._time_groups_cache = (edges, self.time_res_ms_var.get(), groups)
        return groups

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
        start_ms, end_ms = self._time_group_bounds_ms(display_bin)
        return f"{format_ms(start_ms)}–{format_ms(end_ms)} ms"

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
        for name in ("range_start_spin", "range_end_spin", "subtract_start_spin", "subtract_end_spin"):
            if hasattr(self, name):
                getattr(self, name).configure(from_=axis_start, to=axis_end, increment=source_step)
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
        if isinstance(matrix, PreparedSpatialMatrix):
            return [row[:] for row in matrix], matrix.x_groups, matrix.y_groups
        x_groups = self._x_groups()
        y_groups = self._display_y_groups()
        prepared = reduce_matrix_xy(matrix, y_groups, x_groups)
        if smooth:
            prepared = smooth_matrix(prepared, self._smooth_radius())
        return prepared, x_groups, y_groups

    def _prepare_response_plot_matrix(
        self,
        source_start: int,
        source_end: int,
        *,
        smooth: bool = True,
    ) -> tuple[list[list[float | None]], list[AxisGroup], list[AxisGroup]]:
        """Pool display-cell observations before deriving normalized values."""

        x_groups = self._x_groups()
        y_groups = self._display_y_groups()
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return [], x_groups, y_groups
        frames = self.data.spatial_group_response_frames(
            unit_idx,
            [(source_start, source_end)],
            self.value_mode_var.get(),
            y_groups,
            x_groups,
            smooth_radius=self._smooth_radius() if smooth else 0,
        )
        return _nullable_array_list(frames[0]), x_groups, y_groups

    def _prepare_rf_plot_matrix(
        self,
    ) -> tuple[list[list[float | None]], list[AxisGroup], list[AxisGroup]]:
        matrix, x_groups, y_groups = self._prepare_response_plot_matrix(
            *self._source_bins_for_time_controls()
        )
        subtract_range = self._rf_subtraction_range()
        if subtract_range is not None:
            baseline, _, _ = self._prepare_response_plot_matrix(*subtract_range)
            matrix = subtract_response_matrices(matrix, baseline)
        return matrix, x_groups, y_groups

    def _grouped_temporal_metric_matrices(
        self,
        floor: float = 0.0,
        *,
        smooth: bool = True,
    ) -> tuple[
        list[list[float | None]],
        list[list[float | None]],
        list[AxisGroup],
        list[AxisGroup],
    ]:
        x_groups = self._x_groups()
        y_groups = self._display_y_groups()
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return [], [], x_groups, y_groups
        delay, entropy = self.data.spatial_group_temporal_arrays(
            unit_idx,
            y_groups,
            x_groups,
            self._time_groups(),
            smooth_radius=self._smooth_radius() if smooth else 0,
            count_floor=max(0.0, float(floor)),
        )
        return _nullable_array_list(delay), _nullable_array_list(entropy), x_groups, y_groups

    def _group_hist(self, y_start: int, y_end: int, x_start: int, x_end: int) -> list[float]:
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return [0.0 for _ in range(self.data.n_bins)]
        n = max(
            1,
            self.data.spatial_group_source_pixel_count(
                (y_start, y_end),
                (x_start, x_end),
            ),
        )
        return [
            value / n
            for value in self.data.spatial_group_count_histogram(
                unit_idx,
                (y_start, y_end),
                (x_start, x_end),
            )
        ]

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
        return self.data.spatial_group_response_value(
            unit_idx,
            (y_start, y_end),
            (x_start, x_end),
            source_start,
            source_end,
            self.value_mode_var.get(),
        )

    def _group_response_values(
        self,
        y_start: int,
        y_end: int,
        x_start: int,
        x_end: int,
    ) -> list[float | None]:
        unit_idx = self._selected_local_unit_index()
        if unit_idx is None:
            return [None for _group in self._time_groups()]
        return self.data.spatial_group_response_values(
            unit_idx,
            (y_start, y_end),
            (x_start, x_end),
            self._time_groups(),
            self.value_mode_var.get(),
        )

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
        if self._rf_subtraction_range() is not None:
            return f"{self.value_mode_var.get()}: {self._rf_window_expression()}"
        return f"{self.value_mode_var.get()}: {format_ms(start_ms)} to {format_ms(end_ms)} ms"

    def _rf_window_expression(self) -> str:
        start_ms, end_ms = self._selected_time_bounds_ms()
        first = f"{format_ms(start_ms)} ms – {format_ms(end_ms)} ms"
        subtract_range = self._rf_subtraction_range()
        if subtract_range is None:
            return first
        start, end = subtract_range
        second = (
            f"{format_ms(self.data.time_bin_edges[start] * 1000.0)} ms – "
            f"{format_ms(self.data.time_bin_edges[end + 1] * 1000.0)} ms"
        )
        return f"({first}) − ({second})"

    def _rf_sum_range_value_text(self, value: float | None) -> str:
        value_mode = self.value_mode_var.get()
        start_ms, end_ms = self._selected_time_bounds_ms()
        if self._rf_subtraction_range() is not None:
            value_text = "NaN" if value is None else format_response_value(value, value_mode)
            return (
                f"RF A − B {self._rf_window_expression()}: "
                f"{value_text} {value_mode_unit(value_mode)}"
            )
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
        unit_info, spike_time = self._cell_inspector_texts(
            y_start,
            y_end,
            x_idx,
            x_end,
            display_bin,
        )
        return f"{unit_info}\n{spike_time}"

    def _cell_inspector_texts(
        self,
        y_start: int,
        y_end: int,
        x_idx: int,
        x_end: int,
        display_bin: int | None = None,
    ) -> tuple[str, str]:
        unit_idx = self.unit_idx.get()
        value_mode = self.value_mode_var.get()
        unit = value_mode_unit(value_mode)
        display_values = self._group_response_values(y_start, y_end, x_idx, x_end)
        bin_idx = self.bin_var.get() if display_bin is None else int(display_bin)
        bin_idx = max(0, min(len(display_values) - 1, bin_idx))
        range_start, range_end = self._source_bins_for_time_controls()
        range_value = self._group_response_value(
            y_start, y_end, x_idx, x_end, range_start, range_end
        )
        range_value = self._subtract_group_response_value(
            range_value, y_start, y_end, x_idx, x_end
        )
        total_value = self._group_response_value(
            y_start, y_end, x_idx, x_end, 0, self.data.n_bins - 1
        )
        temporal = self.data.spatial_group_temporal_metrics(
            unit_idx,
            (y_start, y_end),
            (x_idx, x_end),
            self._time_groups(),
        )
        peak_bin = temporal.peak_group_index
        peak_value = display_values[peak_bin] if peak_bin is not None else None
        delay = temporal.delay_ms
        ent = temporal.entropy
        delay_text = f"{delay:.1f} ms" if delay is not None else "n/a"
        peak_text = f"{peak_bin + 1} ({self._time_group_label(peak_bin)})" if peak_bin is not None else "n/a"
        group_note = (
            (("mean" if value_mode == VALUE_MODE_COUNT else "occupancy-pooled")
             + " over source pixels")
            if (x_end != x_idx or y_end != y_start)
            else ""
        )
        unit_info = (
            f"cluster {self.data.cluster_id(unit_idx)}\n"
            f"{self._y_group_text(y_start, y_end)}, {self._x_group_text(x_idx, x_end)}"
        )
        if group_note:
            unit_info += f"\n{group_note}"
        spike_time = (
            f"bin {format_response_value(display_values[bin_idx], value_mode)} {unit} "
            f"({self._time_group_label(bin_idx)})\n"
            f"{self._rf_sum_range_value_text(range_value)}\n"
            f"full window {format_response_value(total_value, value_mode)} {unit}\n"
            f"peak {format_response_value(peak_value, value_mode)} {unit}\n"
            f"peak bin {peak_text}\n"
            f"count-rate peak delay {delay_text}, count entropy {ent:.3f}"
        )
        return unit_info, spike_time

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
            best_y, best_x = self.data.best_cell(self.unit_idx.get())
            self.selected_cell = (best_y, best_y, best_x, best_x)
        if cell is None:
            cell = self.selected_cell
        if cell is None:
            return
        y_start, y_end, x_idx, x_end = cell
        unit_info, spike_time = self._cell_inspector_texts(
            y_start,
            y_end,
            x_idx,
            x_end,
            display_bin,
        )
        self.cell_label.configure(
            text=prefix + spike_time
        )
        self.unit_stats_label.configure(text=prefix + unit_info)

    def _cell_tooltip_text(self, cell: CellRef, display_bin: int | None = None) -> str:
        y_start, y_end, x_start, x_end = cell
        value_mode = self.value_mode_var.get()
        unit = value_mode_unit(value_mode)
        display_values = self._group_response_values(y_start, y_end, x_start, x_end)
        bin_idx = self.bin_var.get() if display_bin is None else int(display_bin)
        bin_idx = max(0, min(len(display_values) - 1, bin_idx))
        temporal = self.data.spatial_group_temporal_metrics(
            self.unit_idx.get(),
            (y_start, y_end),
            (x_start, x_end),
            self._time_groups(),
        )
        delay = temporal.delay_ms
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
        plot_value = self._subtract_group_response_value(
            plot_value, y_start, y_end, x_start, x_end
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

    def _subtract_group_response_value(
        self, value: float | None, y_start: int, y_end: int, x_start: int, x_end: int,
    ) -> float | None:
        subtract_range = self._rf_subtraction_range()
        if subtract_range is None:
            return value
        baseline = self._group_response_value(
            y_start, y_end, x_start, x_end, *subtract_range
        )
        if value is None or baseline is None:
            return None
        difference = value - baseline
        return difference if difference >= 0.0 else None

    def _draw_rf(self) -> None:
        prepared = self._prepare_rf_plot_matrix()
        matrix = PreparedSpatialMatrix(*prepared)
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
        delay, _entropy, x_groups, y_groups = self._grouped_temporal_metric_matrices(0.0)
        delay_matrix = PreparedSpatialMatrix(delay, x_groups, y_groups)
        if self.polar_layout_var.get():
            self._draw_polar_matrix(
                "delay",
                delay_matrix,
                "Delay map - peak count-rate interval center",
                "Delay",
                value_suffix=" ms",
                fixed_range=self._time_axis_range_ms(),
            )
        else:
            self._draw_heatmap(
                "delay",
                delay_matrix,
                "Delay map - peak count-rate interval center",
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
        margin_l, margin_r, margin_t, margin_b = 78, 128, (22 if key == "rf" else 56), 72
        plot_w = max(10, w - margin_l - margin_r)
        plot_h = max(10, h - margin_t - margin_b)
        disp, x_groups, y_groups = self._prepare_plot_matrix(matrix)
        n_cols = len(x_groups)
        n_rows = len(y_groups)
        cell_x, cell_y, grid_w, grid_h = spatial_grid_dimensions(
            plot_w,
            plot_h,
            n_cols,
            n_rows,
            minimum_cell_width=4.0,
        )
        x0 = margin_l + (plot_w - grid_w) / 2
        y0 = margin_t + (plot_h - grid_h) / 2
        if fixed_range is None:
            low, high = palette_response_range(disp, palette)
        else:
            low, high = fixed_range

        unit_text = (
            f"Unit {self.unit_idx.get():03d} · "
            f"cluster {self.data.cluster_id(self.unit_idx.get())}"
        )
        if key == "rf" and hasattr(self, "rf_map_subtitle_label"):
            summary = title.removeprefix("RF map - ").removeprefix("RF map – ")
            self.rf_map_subtitle_label.configure(text=f"{summary} · {unit_text}")
        else:
            canvas.create_text(
                20,
                22,
                anchor="w",
                text=title,
                font=("TkDefaultFont", 13, "bold"),
                fill="#1d1d1f",
            )
            canvas.create_text(20, 44, anchor="w", text=unit_text, fill="#6e6e73")

        for display_y, row in enumerate(disp):
            y = y0 + display_y * cell_y
            for x_idx, value in enumerate(row):
                x = x0 + x_idx * cell_x
                if palette == "Delay":
                    fill = delay_color(value, low, high)
                else:
                    fill = palette_color(value, low, high, palette)
                canvas.create_rectangle(
                    x,
                    y,
                    x + cell_x,
                    y + cell_y,
                    fill=fill,
                    outline="#ffffff",
                    width=0,
                )
                if (value is None or not math.isfinite(float(value))) and not (
                    key == "rf" and self.rf_subtract_var.get()
                ):
                    self._draw_missing_hatch(canvas, x, y, x + cell_x, y + cell_y)

        self._draw_selection_outline(
            canvas,
            x0,
            y0,
            cell_x,
            cell_y,
            x_groups,
            y_groups,
        )
        self._draw_axes(
            canvas,
            x0,
            y0,
            cell_x,
            cell_y,
            grid_w,
            grid_h,
            x_groups,
            y_groups,
        )
        self._draw_colorbar(canvas, x0 + grid_w + 36, y0, min(220, grid_h), low, high, palette, value_suffix)
        self._canvas_layouts[key] = {
            "geometry": "rectangle",
            "x0": x0,
            "y0": y0,
            "cell": cell_x,
            "cell_y": cell_y,
            "grid_w": grid_w,
            "grid_h": grid_h,
            "x_groups": x_groups,
            "y_groups": y_groups,
        }

    @staticmethod
    def _draw_missing_hatch(
        canvas: tk.Canvas,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> None:
        """Overlay clipped diagonal hatching so missing is not read as zero."""

        diagonal = x0 + y0
        diagonal_end = x1 + y1
        while diagonal <= diagonal_end + 1e-9:
            candidates: list[tuple[float, float]] = []
            for x, y in (
                (x0, diagonal - x0),
                (x1, diagonal - x1),
                (diagonal - y0, y0),
                (diagonal - y1, y1),
            ):
                if x0 - 1e-9 <= x <= x1 + 1e-9 and y0 - 1e-9 <= y <= y1 + 1e-9:
                    point = (x, y)
                    if point not in candidates:
                        candidates.append(point)
            if len(candidates) >= 2:
                canvas.create_line(
                    *candidates[0],
                    *candidates[-1],
                    fill="#a9abb1",
                    width=1,
                )
            diagonal += 7.0

    def _draw_axes(
        self,
        canvas: tk.Canvas,
        x0: float,
        y0: float,
        cell_x: float,
        cell_y: float,
        grid_w: float,
        grid_h: float,
        x_groups: list[AxisGroup],
        y_groups: list[AxisGroup],
    ) -> None:
        axis_color = "#475467"
        canvas.create_rectangle(x0, y0, x0 + grid_w, y0 + grid_h, outline="#1f2937", width=1)
        tick_step = max(1, len(x_groups) // 6)
        for group_idx in range(0, len(x_groups), tick_step):
            start, end = x_groups[group_idx]
            x = x0 + (group_idx + 0.5) * cell_x
            canvas.create_line(x, y0 + grid_h, x, y0 + grid_h + 5, fill=axis_color)
            pos = (self.data.x_positions[start] + self.data.x_positions[end]) / 2.0
            canvas.create_text(x, y0 + grid_h + 18, text=format_pos(pos), fill=axis_color, font=("TkDefaultFont", 10))
        if (len(x_groups) - 1) not in range(0, len(x_groups), tick_step):
            start, end = x_groups[-1]
            x = x0 + (len(x_groups) - 0.5) * cell_x
            pos = (self.data.x_positions[start] + self.data.x_positions[end]) / 2.0
            canvas.create_text(x, y0 + grid_h + 18, text=format_pos(pos), fill=axis_color, font=("TkDefaultFont", 10))

        for display_y, (y_start, y_end) in enumerate(y_groups):
            y = y0 + (display_y + 0.5) * cell_y
            canvas.create_line(x0 - 5, y, x0, y, fill=axis_color)
            pos = (self.data.y_positions[y_start] + self.data.y_positions[y_end]) / 2.0
            label = f"{y_start + 1} / {format_pos(pos)}" if y_start == y_end else f"{y_start + 1}-{y_end + 1} / {format_pos(pos)}"
            canvas.create_text(x0 - 10, y, anchor="e", text=label, fill=axis_color, font=("TkDefaultFont", 10))

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
        unit_title = suffix.strip() or "Value"
        canvas.create_text(
            x,
            y - 17,
            anchor="w",
            text=unit_title,
            fill="#6e6e73",
            font=("TkDefaultFont", 10),
        )
        for i in range(steps):
            t0 = i / steps
            value = high - (high - low) * t0
            fill = delay_color(value, low, high) if palette == "Delay" else palette_color(value, low, high, palette)
            y1 = y + height * i / steps
            y2 = y + height * (i + 1) / steps
            canvas.create_rectangle(x, y1, x + width, y2, outline="", fill=fill)
        canvas.create_rectangle(x, y, x + width, y + height, outline="#475467")
        canvas.create_text(
            x + width + 8,
            y,
            anchor="w",
            text=f"{high:.3g}",
            fill="#475467",
            font=("TkDefaultFont", 10),
        )
        canvas.create_text(
            x + width + 8,
            y + height,
            anchor="w",
            text=f"{low:.3g}",
            fill="#475467",
            font=("TkDefaultFont", 10),
        )

        legend_y = y + height + 17
        canvas.create_rectangle(
            x,
            legend_y,
            x + 13,
            legend_y + 13,
            fill="#e6e8eb",
            outline="#c4c6ca",
        )
        difference = canvas is self.canvases.get("rf") and self.rf_subtract_var.get()
        if not difference:
            self._draw_missing_hatch(canvas, x, legend_y, x + 13, legend_y + 13)
        if difference:
            missing_label = "NaN"
        elif palette == "Delay":
            missing_label = "No detected peak"
        else:
            missing_label = "No occupancy"
        canvas.create_text(
            x + 20,
            legend_y + 6.5,
            anchor="w",
            text=missing_label,
            fill="#6e6e73",
            font=("TkDefaultFont", 10),
        )

    def _draw_selection_outline(
        self,
        canvas: tk.Canvas,
        x0: float,
        y0: float,
        cell_x: float,
        cell_y: float,
        x_groups: list[AxisGroup] | None = None,
        y_groups: list[AxisGroup] | None = None,
    ) -> None:
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
        x = x0 + group_idx * cell_x
        y = y0 + display_y * cell_y
        canvas.create_rectangle(
            x + 1,
            y + 1,
            x + cell_x - 1,
            y + cell_y - 1,
            outline="#111827",
            width=2,
        )
        canvas.create_rectangle(
            x + 3,
            y + 3,
            x + cell_x - 3,
            y + cell_y - 3,
            outline="#ffffff",
            width=1,
        )

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
        low, high = (
            fixed_range
            if fixed_range is not None
            else palette_response_range(disp, palette)
        )
        total_deg = self.data.infer_total_deg()
        n_rows = len(y_groups)
        ring_span = polar_ring_span(n_rows)
        radius_units = INNER_BLANK_ROWS + n_rows * ring_span + POLAR_PAD_ROWS
        reserved_height = 84 if key == "rf" else 130
        scale = min((w - 180) / (2 * radius_units), (h - reserved_height) / (2 * radius_units))
        scale = max(4.0, scale)
        cx = w / 2
        cy = h / 2 + (0 if key == "rf" else 22)

        polar_summary = (
            f"{title.removeprefix('RF map - ')} · polar {total_deg:.0f}° · "
            f"radius {self.polar_radius_var.get()}"
        )
        if key == "rf" and hasattr(self, "rf_map_subtitle_label"):
            self.rf_map_subtitle_label.configure(text=polar_summary)
        else:
            canvas.create_text(
                20,
                22,
                anchor="w",
                text=title,
                font=("TkDefaultFont", 13, "bold"),
                fill="#1d1d1f",
            )
            canvas.create_text(
                20,
                44,
                anchor="w",
                text=(
                    f"Polar layout · total angle {total_deg:.0f}° · "
                    f"radius {self.polar_radius_var.get()}"
                ),
                fill="#6e6e73",
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
            r_inner = INNER_BLANK_ROWS + ring_idx * ring_span
            r_outer = r_inner + ring_span
            for col in range(len(x_groups)):
                value = disp[display_row][col]
                fill = delay_color(value, low, high) if palette == "Delay" else palette_color(value, low, high, palette)
                points = self._polar_cell_points(cx, cy, scale, r_inner, r_outer, theta_edges[col], theta_edges[col + 1])
                missing = value is None or not math.isfinite(float(value))
                hatch_missing = missing and not (key == "rf" and self.rf_subtract_var.get())
                canvas.create_polygon(
                    points,
                    fill=fill,
                    outline="#c4c6ca" if hatch_missing else "",
                    stipple="gray25" if hatch_missing else "",
                )

        self._draw_polar_selection_outline(
            canvas,
            cx,
            cy,
            scale,
            theta_edges,
            x_groups,
            y_groups,
            ring_rows,
            ring_span,
        )

        outer_r = (INNER_BLANK_ROWS + n_rows * ring_span) * scale
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
            "ring_span": ring_span,
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
        ring_span: float,
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
            INNER_BLANK_ROWS + ring_idx * ring_span,
            INNER_BLANK_ROWS + (ring_idx + 1) * ring_span,
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
        canvas = self.canvases["delay"]
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 160)
        margin_l, margin_r, margin_t, margin_b = 78, 188, 56, 68
        plot_w = max(10, w - margin_l - margin_r)
        plot_h = max(10, h - margin_t - margin_b)
        total_disp, x_groups, y_groups = self._prepare_response_plot_matrix(
            0,
            self.data.n_bins - 1,
        )
        delay_disp, entropy_disp, _x_groups_temporal, _y_groups_temporal = (
            self._grouped_temporal_metric_matrices(0.0)
        )
        n_rows = len(y_groups)
        cell_x, cell_y, grid_w, grid_h = spatial_grid_dimensions(
            plot_w,
            plot_h,
            len(x_groups),
            n_rows,
            minimum_cell_width=4.0,
        )
        x0 = margin_l + (plot_w - grid_w) / 2
        y0 = margin_t + (plot_h - grid_h) / 2
        _response_low, response_high = nonnegative_response_range(total_disp)
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

        canvas.create_text(20, 22, anchor="w", text="RGB composite", font=("TkDefaultFont", 13, "bold"), fill="#1d1d1f")
        canvas.create_text(
            20,
            44,
            anchor="w",
            text=f"R {self.value_mode_var.get()}; G count-rate-peak delay; B temporal entropy",
            fill="#667085",
        )

        for display_y in range(n_rows):
            y = y0 + display_y * cell_y
            for group_idx, (x_start, x_end) in enumerate(x_groups):
                color = rgb_response_color(
                    total_disp[display_y][group_idx],
                    delay_disp[display_y][group_idx],
                    entropy_disp[display_y][group_idx],
                    max_total, min_delay, delay_span,
                )
                missing = color is None
                fill = "#e6e8eb" if missing else hex_color(color)
                x = x0 + group_idx * cell_x
                canvas.create_rectangle(
                    x,
                    y,
                    x + cell_x,
                    y + cell_y,
                    fill=fill,
                    outline="#ffffff",
                    width=0,
                )
                if missing:
                    self._draw_missing_hatch(canvas, x, y, x + cell_x, y + cell_y)
        self._draw_selection_outline(
            canvas,
            x0,
            y0,
            cell_x,
            cell_y,
            x_groups,
            y_groups,
        )
        self._draw_axes(
            canvas,
            x0,
            y0,
            cell_x,
            cell_y,
            grid_w,
            grid_h,
            x_groups,
            y_groups,
        )
        legend_x = min(x0 + grid_w + 34, w - 154)
        legend_y = y0
        for i, (label, color) in enumerate(
            ((f"R {value_mode_unit(self.value_mode_var.get())}", "#dc2626"), ("G delay", "#16a34a"), ("B entropy", "#2563eb"))
        ):
            y = legend_y + i * 26
            canvas.create_rectangle(legend_x, y, legend_x + 16, y + 16, fill=color, outline="")
            canvas.create_text(legend_x + 24, y + 8, anchor="w", text=label, fill="#475467")
        missing_y = legend_y + 82
        canvas.create_rectangle(
            legend_x,
            missing_y,
            legend_x + 16,
            missing_y + 16,
            fill="#e6e8eb",
            outline="#c4c6ca",
        )
        self._draw_missing_hatch(
            canvas,
            legend_x,
            missing_y,
            legend_x + 16,
            missing_y + 16,
        )
        canvas.create_text(
            legend_x + 24,
            missing_y + 8,
            anchor="w",
            text="No occupancy",
            fill="#6e6e73",
        )
        self._canvas_layouts["delay"] = {
            "geometry": "rectangle",
            "x0": x0,
            "y0": y0,
            "cell": cell_x,
            "cell_y": cell_y,
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
        ring_span = polar_ring_span(n_rows)
        radius_units = INNER_BLANK_ROWS + n_rows * ring_span + POLAR_PAD_ROWS
        scale = max(4.0, min((w - 220) / (2 * radius_units), (h - 130) / (2 * radius_units)))
        cx = w / 2
        cy = h / 2 + 22
        canvas.create_text(20, 22, anchor="w", text="RGB composite", font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(
            20,
            44,
            anchor="w",
            text=(
                f"Polar layout; R {self.value_mode_var.get()}; G count-rate-peak delay; "
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
                color = rgb_response_color(
                    total_disp[display_row][column],
                    delay_disp[display_row][column],
                    entropy_disp[display_row][column],
                    max_total, min_delay, delay_span,
                )
                missing = color is None
                fill = "#e6e8eb" if missing else hex_color(color)
                points = self._polar_cell_points(
                    cx,
                    cy,
                    scale,
                    INNER_BLANK_ROWS + ring_idx * ring_span,
                    INNER_BLANK_ROWS + (ring_idx + 1) * ring_span,
                    theta_edges[column],
                    theta_edges[column + 1],
                )
                canvas.create_polygon(
                    points,
                    fill=fill,
                    outline="#c4c6ca" if missing else "",
                    stipple="gray25" if missing else "",
                )

        self._draw_polar_selection_outline(
            canvas,
            cx,
            cy,
            scale,
            theta_edges,
            x_groups,
            y_groups,
            ring_rows,
            ring_span,
        )
        outer_r = (INNER_BLANK_ROWS + n_rows * ring_span) * scale
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
        missing_y = legend_y + 82
        canvas.create_rectangle(
            legend_x,
            missing_y,
            legend_x + 16,
            missing_y + 16,
            fill="#e6e8eb",
            outline="#c4c6ca",
        )
        self._draw_missing_hatch(
            canvas,
            legend_x,
            missing_y,
            legend_x + 16,
            missing_y + 16,
        )
        canvas.create_text(
            legend_x + 24,
            missing_y + 8,
            anchor="w",
            text="No occupancy",
            fill="#6e6e73",
        )
        self._canvas_layouts["delay"] = {
            "geometry": "polar",
            "cx": cx,
            "cy": cy,
            "scale": scale,
            "total_deg": total_deg,
            "x_groups": x_groups,
            "y_groups": y_groups,
            "ring_rows": ring_rows,
            "ring_span": ring_span,
        }

    def _all_positions_timeline_values(
        self,
        unit_idx: int,
        time_groups: list[AxisGroup],
    ) -> list[float]:
        return self.data.all_positions_timeline_values(
            unit_idx,
            time_groups,
            self.value_mode_var.get(),
        )

    def _ensure_timeline_preview_images(
        self,
        canvas: tk.Canvas,
        unit_idx: int,
        visible_bins: list[int],
        time_groups: list[AxisGroup],
        x_groups: list[AxisGroup],
        y_groups: list[AxisGroup],
        smooth_radius: int,
        cell_width: float,
        cell_height: float,
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
            round(cell_width, 6),
            round(cell_height, 6),
            tuple((bin_idx, *tile_positions[bin_idx]) for bin_idx in visible_bins),
            atlas_width,
            atlas_height,
        )
        if self._timeline_preview_cache_key == cache_key:
            return self._timeline_preview_high

        visible_groups = [time_groups[bin_idx] for bin_idx in visible_bins]
        prepared_frames = self.data.spatial_group_response_frames(
            unit_idx,
            visible_groups,
            self.value_mode_var.get(),
            y_groups,
            x_groups,
            smooth_radius=smooth_radius,
        )
        prepared_by_bin = {
            bin_idx: _nullable_array_list(prepared_frames[frame_index])
            for frame_index, bin_idx in enumerate(visible_bins)
        }
        finite_values = prepared_frames[np.isfinite(prepared_frames)]
        high = float(finite_values.max()) if finite_values.size else 0.0

        high = max(high, 1.0)
        palette = self.palette_var.get()
        color_for_value = lambda value, high=high, palette=palette: palette_color(value, 0.0, high, palette)
        if self.polar_layout_var.get():
            total_deg = self.data.infer_total_deg()
            if self.polar_radius_var.get() == POLAR_RADIUS_MODES[0]:
                ring_rows = sorted(range(len(y_groups)), key=lambda index: y_groups[index][0])
            else:
                ring_rows = list(range(len(y_groups) - 1, -1, -1))
            ring_span = polar_ring_span(len(y_groups))
            polar_tiles = [
                (
                    prepared_by_bin[bin_idx],
                    *tile_positions[bin_idx],
                    cell_width,
                    total_deg,
                    ring_rows,
                    ring_span,
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
                (
                    prepared_by_bin[bin_idx],
                    *tile_positions[bin_idx],
                    cell_width,
                    cell_height,
                )
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
        target_aspect = (
            SINGLETON_Y_REFERENCE_COLUMNS / SINGLETON_Y_REFERENCE_ROWS
            if y_count == 1
            else x_count / y_count
        )
        target_grid_w = target_grid_h * target_aspect
        target_grid_w = max(target_grid_w, 2.0 * x_count)
        target_grid_h = target_grid_w / target_aspect
        max_cols_by_width = max(1, int((available_w + gap_x) // max(1.0, target_grid_w + gap_x)))
        max_cols_by_screen = max(1, int((min(screen_w, window_w, width) - left - right_pad + gap_x) // max(1.0, target_grid_w + gap_x)))
        cols = min(count, max(1, min(max_cols_by_width, max_cols_by_screen)))
        slot_w = max(1.0, (available_w - (cols - 1) * gap_x) / cols)
        grid_w = min(target_grid_w, slot_w)
        cell_x = max(2.0, grid_w / x_count)
        grid_w = cell_x * x_count
        grid_h = grid_w / target_aspect
        cell_y = grid_h / y_count
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
            "cell": cell_x,
            "cell_y": cell_y,
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
        time_group_centers_ms = [
            self._time_group_center_ms(bin_idx) for bin_idx in range(display_bins)
        ]
        time_group_end_bounds_ms = [
            self._time_group_bounds_ms(bin_idx)[1] for bin_idx in range(display_bins)
        ]
        timing_warning = " Negative bins may include previous-stimulus responses." if axis_start_ms < 0.0 else ""
        canvas.create_text(20, 22, anchor="w", text=f"Timeline and {display_bins} bin maps", font=("TkDefaultFont", 15, "bold"), fill="#111827")
        canvas.create_text(
            20,
            44,
            anchor="w",
            text=(
                f"Timeline selection {self._display_range_label()}; "
                f"target width {format_ms(self._time_group_size() * self._base_bin_ms())} ms; "
                "maps show actual time intervals; "
                f"{self.value_mode_var.get()}."
                f"{timing_warning}"
            ),
            fill="#667085",
        )

        chart_x, chart_y = 64, 78
        chart_w = max(320, w - 140)
        chart_h = 62
        selected_values: list[float] | None = None
        if self.selected_cell is not None:
            y_start, y_end, x_start, x_end = self.selected_cell
            selected_values_optional = self._group_response_values(
                y_start,
                y_end,
                x_start,
                x_end,
            )
            selected_values = [
                float(value) if value is not None else 0.0
                for value in selected_values_optional
            ]
        blue_high = timeline_response_high(time_totals)
        red_high = (
            timeline_response_high(selected_values)
            if selected_values is not None
            else None
        )
        zero_x: float | None = None
        if axis_start_ms <= 0.0 <= axis_end_ms and axis_end_ms > axis_start_ms:
            zero_x = chart_x + chart_w * (0.0 - axis_start_ms) / (axis_end_ms - axis_start_ms)
            if axis_start_ms < 0.0:
                canvas.create_rectangle(chart_x, chart_y, zero_x, chart_y + chart_h, fill="#f8fafc", outline="")
        canvas.create_rectangle(chart_x, chart_y, chart_x + chart_w, chart_y + chart_h, outline="#cbd5e1")
        if zero_x is not None:
            canvas.create_line(zero_x, chart_y, zero_x, chart_y + chart_h, fill="#7c3aed", width=1, dash=(4, 3))
            canvas.create_text(zero_x + 4, chart_y + 5, anchor="nw", text="VS 0 ms", fill="#6d28d9", font=("TkDefaultFont", 10, "bold"))

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
            font=("TkDefaultFont", 10),
        )
        if self.selected_cell is not None:
            canvas.create_line(chart_x + 196, legend_y, chart_x + 212, legend_y, fill="#dc2626", width=2)
            canvas.create_text(chart_x + 217, legend_y, anchor="w", text="Selected cell", fill="#dc2626", font=("TkDefaultFont", 10))
        points = timeline_chart_points(
            time_totals,
            time_group_centers_ms,
            (axis_start_ms, axis_end_ms),
            blue_high,
            (chart_x, chart_y, chart_w, chart_h),
        )
        if len(points) >= 4:
            canvas.create_line(*points, fill="#2563eb", width=2, smooth=False)
        elif len(points) == 2:
            canvas.create_oval(
                points[0] - 2,
                points[1] - 2,
                points[0] + 2,
                points[1] + 2,
                fill="#2563eb",
                outline="",
            )
        if selected_values is not None:
            assert red_high is not None
            selected_points = timeline_chart_points(
                selected_values,
                time_group_centers_ms,
                (axis_start_ms, axis_end_ms),
                red_high,
                (chart_x, chart_y, chart_w, chart_h),
            )
            if len(selected_points) >= 4:
                canvas.create_line(
                    *selected_points,
                    fill="#dc2626",
                    width=1.8,
                    smooth=False,
                )
            elif len(selected_points) == 2:
                canvas.create_oval(
                    selected_points[0] - 2,
                    selected_points[1] - 2,
                    selected_points[0] + 2,
                    selected_points[1] + 2,
                    fill="#dc2626",
                    outline="",
                )
        red_axis_x = chart_x - 20
        blue_axis_x = chart_x + chart_w + 20
        axis_font = ("TkDefaultFont", 10)
        if red_high is not None:
            canvas.create_line(red_axis_x, chart_y, red_axis_x, chart_y + chart_h, fill="#dc2626", width=1)
            canvas.create_line(red_axis_x - 4, chart_y, red_axis_x, chart_y, fill="#dc2626")
            canvas.create_line(red_axis_x - 4, chart_y + chart_h, red_axis_x, chart_y + chart_h, fill="#dc2626")
            canvas.create_text(
                red_axis_x - 7,
                chart_y,
                anchor="e",
                text=format_response_value(red_high, self.value_mode_var.get()),
                fill="#dc2626",
                font=axis_font,
            )
            canvas.create_text(
                red_axis_x - 7,
                chart_y + chart_h,
                anchor="e",
                text="0",
                fill="#dc2626",
                font=axis_font,
            )
        canvas.create_line(blue_axis_x, chart_y, blue_axis_x, chart_y + chart_h, fill="#2563eb", width=1)
        canvas.create_line(blue_axis_x, chart_y, blue_axis_x + 4, chart_y, fill="#2563eb")
        canvas.create_line(blue_axis_x, chart_y + chart_h, blue_axis_x + 4, chart_y + chart_h, fill="#2563eb")
        canvas.create_text(
            blue_axis_x + 7,
            chart_y,
            anchor="w",
            text=format_response_value(blue_high, self.value_mode_var.get()),
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
            time_ms = axis_start_ms if boundary == 0 else self._time_group_bounds_ms(boundary - 1)[1]
            x = chart_x + chart_w * timeline_position_fraction(
                time_ms,
                axis_start_ms,
                axis_end_ms,
            )
            anchor = "w" if boundary == 0 else ("e" if boundary == display_bins else "center")
            canvas.create_line(x, chart_y + chart_h, x, chart_y + chart_h + 4, fill="#64748b")
            canvas.create_text(
                x,
                chart_y + chart_h + 17,
                anchor=anchor,
                text=format_ms(time_ms),
                fill="#475467",
                font=("TkDefaultFont", 10),
            )
        canvas.create_text(
            chart_x + chart_w / 2,
            chart_y + chart_h + 36,
            anchor="center",
            text="Time from VS onset (ms)",
            fill="#475467",
            font=("TkDefaultFont", 10),
        )

        mini_top = chart_y + chart_h + 54
        preview_x_groups = self._x_groups()
        preview_y_groups = self._display_y_groups()
        smooth_radius = self._smooth_radius()
        preview_ring_span = polar_ring_span(len(preview_y_groups))
        if self.polar_layout_var.get():
            polar_diameter_units = 2 * (
                INNER_BLANK_ROWS + len(preview_y_groups) * preview_ring_span
            )
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
        preview_cell_x = float(mini_layout["cell"])
        preview_cell_y = float(mini_layout.get("cell_y", preview_cell_x))
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
            preview_cell_x,
            preview_cell_y,
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
            "axis_start_ms": axis_start_ms,
            "axis_end_ms": axis_end_ms,
            "time_group_end_bounds_ms": time_group_end_bounds_ms,
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
            cell_x = preview_cell_x
            cell_y = preview_cell_y
            grid_w = preview_grid_w
            grid_h = preview_grid_h
            timeline_layout: dict[str, object] = {
                "geometry": "polar" if self.polar_layout_var.get() else "rectangle",
                "bin_idx": bin_idx,
                "source_start": source_start,
                "source_end": source_end,
                "x0": x0,
                "y0": y0,
                "cell": cell_x,
                "cell_y": cell_y,
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
                        "scale": cell_x,
                        "total_deg": self.data.infer_total_deg(),
                        "ring_span": preview_ring_span,
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
            label_font = ("TkDefaultFont", 10, "bold") if has_time_selection and in_selected_range else ("TkDefaultFont", 10)
            canvas.create_text(
                x0,
                y0 + grid_h + label_gap,
                anchor="nw",
                text=self._time_group_label(bin_idx),
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
        content_right = max(
            w,
            blue_axis_x + 54,
            mini_left
            + last_col_count * slot_w
            + max(0, last_col_count - 1) * gap_x
            + 44,
        )
        canvas.configure(scrollregion=(0, 0, content_right, max(h, content_bottom)))
        self._restore_timeline_scroll()

    def _canvas_to_cell(self, key: str, event: tk.Event) -> CellRef | None:
        layout = self._canvas_layouts.get(key)
        if not layout or "cell" not in layout:
            return None
        x0 = layout["x0"]
        y0 = layout["y0"]
        cell_x = layout["cell"]
        cell_y = layout.get("cell_y", cell_x)
        grid_w = layout["grid_w"]
        grid_h = layout["grid_h"]
        if not (x0 <= event.x < x0 + grid_w and y0 <= event.y < y0 + grid_h):
            return None
        group_idx = int((event.x - x0) // cell_x)
        display_y = int((event.y - y0) // cell_y)
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
            axis_start_ms = layout.get("axis_start_ms")
            axis_end_ms = layout.get("axis_end_ms")
            end_bounds_ms = layout.get("time_group_end_bounds_ms")
            if (
                axis_start_ms is not None
                and axis_end_ms is not None
                and isinstance(end_bounds_ms, (list, tuple))
            ):
                fraction = max(
                    0.0,
                    min(1.0, (event_x - float(chart_x)) / float(chart_w)),
                )
                time_ms = float(axis_start_ms) + fraction * (
                    float(axis_end_ms) - float(axis_start_ms)
                )
                physical_bin = timeline_bin_index(time_ms, end_bounds_ms)
                if physical_bin is not None:
                    return max(0, min(display_bins - 1, physical_bin))
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
        cell_x = float(layout["cell"])
        cell_y = float(layout.get("cell_y", cell_x))
        group_idx = int((event_x - x0) // cell_x)
        display_y = int((event_y - y0) // cell_y)
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
        ring_span = max(float(layout.get("ring_span", 1.0)), 1e-9)
        if not (
            INNER_BLANK_ROWS
            <= radius
            < INNER_BLANK_ROWS + len(y_groups) * ring_span
        ):
            return None
        ring_idx = int(math.floor((radius - INNER_BLANK_ROWS) / ring_span))
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
            unit_id = self._selected_unit_id_value()
            self.status_label.configure(
                text=(
                    self._quality_filter_status(unit_id)
                    or (
                        f"N/A: cluster {unit_id} is not available in this session. "
                        "Use ←/→ to continue through the paired unit list."
                    )
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
                    cell_x = layout["cell"]
                    cell_y = layout.get("cell_y", cell_x)
                    x = x0 + group_idx * cell_x
                    y = y0 + display_y * cell_y
                    canvas.create_rectangle(
                        x + 1,
                        y + 1,
                        x + cell_x - 1,
                        y + cell_y - 1,
                        outline="#f97316",
                        width=3,
                        tags="hover",
                    )
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
                    INNER_BLANK_ROWS + ring_idx * float(layout.get("ring_span", 1.0)),
                    INNER_BLANK_ROWS + (ring_idx + 1) * float(layout.get("ring_span", 1.0)),
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
                    cell_x = float(layout["cell"])
                    cell_y = float(layout.get("cell_y", cell_x))
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
                            INNER_BLANK_ROWS
                            + ring_idx * float(layout.get("ring_span", 1.0)),
                            INNER_BLANK_ROWS
                            + (ring_idx + 1) * float(layout.get("ring_span", 1.0)),
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
                        x = x0 + group_idx * cell_x
                        y = y0 + display_y * cell_y
                        canvas.create_rectangle(
                            x,
                            y,
                            x + cell_x,
                            y + cell_y,
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
        canvas.create_text(x + pad, y + pad, anchor="nw", text=text, fill="#f8fafc", font=("TkDefaultFont", 10), tags="hover")

    def _load_json_path(self, path: Path) -> None:
        try:
            data = RFMappingData(path)
        except Exception as exc:
            messagebox.showerror("Could not load RF map", str(exc))
            return
        self._stop_unit_cache()
        self.data.close()
        self.data = data
        self.settings = self._app_root._rfm_settings
        self.title(f"{self.data.path.name} — RF Map Viewer")
        self.unit_idx.set(0)
        self._selected_unit_id = self.data.unit_pool[0]
        self._last_supported_unit_id = self.data.unit_pool[0]
        self.bin_var.set(0)
        self.range_start_var.set(0)
        self.time_res_ms_var.set(
            format_ms(max(self._base_bin_ms(), self.settings.rf_time_resolution_ms))
        )
        self._last_time_group_count = self.data.n_bins
        self._last_time_groups = [(index, index) for index in range(self.data.n_bins)]
        self.range_end_var.set(self._time_group_count() - 1)
        plot_start_ms, plot_end_ms = self._default_plot_time_bounds_ms()
        self.range_start_ms_var.set(format_ms(plot_start_ms))
        self.range_end_ms_var.set(format_ms(plot_end_ms))
        self._reset_rf_window_defaults()
        value_mode = self.settings.rf_value_mode
        self.value_mode_var.set(
            value_mode if self.data.supports_value_mode(value_mode) else VALUE_MODE_RATE
        )
        self.flip_y_var.set(self.settings.rf_flip_y)
        self.palette_var.set(self.settings.rf_palette)
        self.polar_radius_var.set(self.settings.rf_polar_radius)
        self.polar_layout_var.set(self.settings.rf_polar_layout)
        self.rgb_mode_var.set(self.settings.rf_rgb_mode)
        self.smooth_radius_var.set(self.settings.rf_smooth_radius)
        self.show_probe_layout_var.set(self.settings.show_probe_layout)
        self.show_tuning_curve_var.set(self.settings.show_tuning_curve)
        self.show_waveform_var.set(self.settings.show_waveform)
        self.tuning_plot_mode_var.set(self.settings.tuning_plot_mode)
        self.tuning_layout_var.set(self.settings.tuning_layout)
        self.tuning_display_bins_var.set(self.settings.tuning_display_bins)
        self.tuning_smoothing_var.set(self.settings.tuning_smoothing)
        self.tuning_smooth_sigma_var.set(self.settings.tuning_smooth_sigma)
        self.tuning_compare_scale_var.set(self.settings.tuning_compare_scale)
        self.waveform_channel_mode_var.set(self.settings.waveform_channel_mode)
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
        self.probe_geometry = None
        self.tuning_curve_data = None
        self._tuning_curve_error = None
        self._tuning_curve_candidate = None
        self._tuning_processed_cache = None
        self._tuning_scale_cache = None
        self.spatial_region = None
        self._probe_drag_start = None
        self._probe_canvas_transform = None
        self._probe_static_signature = None
        self._waveform_generation += 1
        self.waveform_payload = None
        self._waveform_payload_key = None
        self._waveform_loading_key = None
        self._waveform_error = None
        self._waveform_error_key = None
        self._sync_time_control_ranges()
        self.time_res_spin.configure(from_=self._base_bin_ms(), to=self._total_time_ms(), increment=self._base_bin_ms())
        self.x_bins_var.set(min(self.data.n_x, self.settings.rf_x_bins or self.data.n_x))
        self.y_bins_var.set(min(self.data.n_y, self.settings.rf_y_bins or self.data.n_y))
        self.x_bins_spin.configure(to=self.data.n_x)
        self.y_bins_spin.configure(to=self.data.n_y)
        self._sync_optional_view_visibility(redraw=False)
        self._select_tab_key(self.settings.default_viewer_tab)
        self._sync_json_menu()
        self._sync_unit_combo()
        self._update_all()
        self._pair_ready_viewer_set_changed(adopt_viewer=self)
        self._schedule_optional_autoload()
        self._start_unit_cache()

    def _open_figure_exporter(self) -> None:
        archive = self.data.unit_archive
        if archive is not None and archive.cache_count < self.data.n_units:
            self.status_label.configure(text="Figures will be available when all units finish loading.")
            return
        existing = self._figure_export_window
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        if not self._local_quality_visible_unit_ids():
            messagebox.showinfo(
                "No visible units",
                "No units pass the zero-spike RF-bin filter for the current RF "
                "window. Change the RF window or unit-filter Settings before exporting.",
                parent=self,
            )
            return
        self._figure_export_window = FigureExportWindow(self)

    def _export_current_matrix(self) -> None:
        if self._selected_local_unit_index() is None:
            messagebox.showinfo(
                "Unit unavailable",
                f"Cluster {self._selected_unit_id_value()} is not available in this session.",
                parent=self,
            )
            return
        matrix, x_groups, y_groups = self._prepare_rf_plot_matrix()
        export_space = "displayed"

        range_start, range_end = self._plot_range_group_indices()
        range_start_ms, range_end_ms = self._selected_time_bounds_ms()
        subtract_range = self._rf_subtraction_range()
        subtract_start_ms = (
            self.data.time_bin_edges[subtract_range[0]] * 1000.0
            if subtract_range is not None else ""
        )
        subtract_end_ms = (
            self.data.time_bin_edges[subtract_range[1] + 1] * 1000.0
            if subtract_range is not None else ""
        )
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
            def write_export(writer: csv.writer) -> None:
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
                        "occupancy_time_sec_min",
                        "occupancy_time_sec_max",
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
                        "rf_window_operation",
                        "rf_subtract_start_ms",
                        "rf_subtract_end_ms",
                    ]
                )
                for display_y, (y_start, y_end) in enumerate(y_groups):
                    for display_x, (x_start, x_end) in enumerate(x_groups):
                        occupancy_times = [
                            self.data.occupancy_time_s[y_idx][x_idx]
                            for y_idx in range(y_start, y_end + 1)
                            for x_idx in range(x_start, x_end + 1)
                        ]
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
                                min(occupancy_times) if occupancy_times else "",
                                max(occupancy_times) if occupancy_times else "",
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
                                "A - B" if subtract_range is not None else "sum",
                                subtract_start_ms,
                                subtract_end_ms,
                            ]
                        )

            _atomic_write_csv(path, write_export)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        messagebox.showinfo("Export complete", f"Wrote {export_space} matrix to {path}")


def run_self_test(path: Path, *, isolated: bool = False) -> None:
    data = RFMappingData(path, isolated=isolated)
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
    if data.occupancy_time_s[y_idx][x_idx] <= 0:
        assert count_response[y_idx][x_idx] is None
    else:
        assert count_response[y_idx][x_idx] == range_sum[y_idx][x_idx]
    occupancy_time_s = data.occupancy_time_s[y_idx][x_idx]
    if occupancy_time_s > 0:
        expected_rate = sum(hist[: test_range_end + 1]) / occupancy_time_s
        firing_rate = data.response_value(
            unit_idx, y_idx, x_idx, 0, test_range_end, VALUE_MODE_RATE
        )
        assert firing_rate is not None
        assert abs(firing_rate - expected_rate) < 1e-9
    assert 0.0 <= metrics.entropy[y_idx][x_idx] <= 1.0
    inferred_total_deg = data.infer_total_deg()
    assert math.isfinite(inferred_total_deg) and inferred_total_deg > 0
    hd_angles, hd_rates = processed_tuning_curve(
        tuple(float(index % 17) for index in range(HD_RAW_BIN_COUNT)),
        DEFAULT_HD_DISPLAY_BINS,
        smoothing=True,
        sigma=DEFAULT_HD_SMOOTH_SIGMA,
    )
    assert len(hd_angles) == DEFAULT_HD_DISPLAY_BINS
    assert len(hd_rates) == DEFAULT_HD_DISPLAY_BINS
    assert all(math.isfinite(value) and value >= 0.0 for value in hd_rates)
    _cli_print(
        "self-test passed:",
        f"{data.n_units} units, {data.n_y} y, {data.n_x} x, {data.n_bins} bins",
        "occupancy metadata: yes",
    )


def run_tkdnd_self_test() -> None:
    """Verify that the optional-file drop runtime is usable in a frozen app."""

    if not TK_AVAILABLE:
        raise RuntimeError("tkinter is not available")
    try:
        from tkinterdnd2 import TkinterDnD
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError("tkinterdnd2 is not available") from exc

    root = TkinterDnD.Tk()
    try:
        root.withdraw()
        version = TkinterDnD.require(root)
        root.update_idletasks()
    finally:
        root.destroy()
    _cli_print(f"TkDND self-test passed: {version}")


def run_figure_export_self_test(output_root: Path) -> None:
    """Exercise packaged PDF, directory-figure, and CSV publication paths."""

    if output_root.exists():
        if not output_root.is_dir() or any(output_root.iterdir()):
            raise RuntimeError("figure export self-test directory must be empty")
    else:
        output_root.mkdir(parents=True)
    page = ExportPage(
        "Self test",
        [
            PlotSpec(
                PlotKind.RF_CARTESIAN,
                [[0.0, 1.0], [2.0, 3.0]],
                options={"subtitle": "packaged publication smoke"},
            ),
            PlotSpec(
                PlotKind.WAVEFORM_LOCAL_AVERAGE,
                {
                    "matrix": [
                        [-1.0, -2.0, 0.0, 1.0],
                        [-2.0, -5.0, 2.0, 1.0],
                    ],
                    "times_ms": [-0.5, 0.0, 0.5, 1.0],
                    "time_edges_ms": [-0.75, -0.25, 0.25, 0.75, 1.25],
                    "channel_labels": ["ch 7", "ch 3"],
                    "best_channel_row": 1,
                },
                options={"subtitle": "same x column"},
            ),
        ],
    )
    png_destination = output_root / "figure-export-smoke"
    pdf_destination = output_root / "figure-export-smoke.pdf"
    png_result = export_figures(
        ExportPlan(FigureFormat.PNG, [1], [page], png_destination)
    )
    pdf_result = export_figures(
        ExportPlan(FigureFormat.PDF, [1], [page], pdf_destination)
    )
    csv_destination = output_root / "displayed-data-smoke.csv"

    def write_smoke_csv(writer: csv.writer) -> None:
        writer.writerow(["unit_id", "value"])
        writer.writerow([1, 3])

    _atomic_write_csv(csv_destination, write_smoke_csv)
    if (
        png_result.page_count != 1
        or not (png_destination / "manifest.json").is_file()
        or not png_result.files[0].is_file()
        or pdf_result.page_count != 1
        or not pdf_destination.read_bytes().startswith(b"%PDF-")
        or csv_destination.read_text(encoding="utf-8") != "unit_id,value\n1,3\n"
    ):
        raise RuntimeError("figure export self-test output verification failed")
    _cli_print(f"Figure export self-test passed: {output_root}")


def _cli_print(*values: object, error: bool = False) -> None:
    """Emit CLI diagnostics when the frozen executable has a console.

    PyInstaller's Windows ``--windowed`` bootloader intentionally exposes
    ``sys.stdout`` and ``sys.stderr`` as ``None``.  Release smoke tests still
    need their exit status to be meaningful, so a missing stream must not turn
    a successful (or deliberately failed) self-test into an unrelated
    ``AttributeError``.
    """

    stream = sys.stderr if error else sys.stdout
    if stream is not None:
        print(*values, file=stream)


WINDOWED_SMOKE_REPORT_ENV = "RF_MAPPING_WINDOWED_SMOKE_REPORT"


def _write_windowed_smoke_report(path: Path, payload: Mapping[str, object]) -> None:
    """Write diagnostics for a frozen ``--windowed`` release smoke run.

    A Windows noconsole executable shows an unhandled traceback in a modal
    bootloader dialog. Headless release runners cannot dismiss that dialog,
    so the packaging harness supplies an explicit report path and expects the
    entrypoint to convert failures into a non-zero exit with useful evidence.
    Normal interactive launches never set the environment variable and retain
    the regular exception behavior.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _run_main_entrypoint(argv: list[str] | None = None) -> int:
    """Run :func:`main`, optionally reporting a windowed release smoke result."""

    report_value = os.environ.get(WINDOWED_SMOKE_REPORT_ENV, "").strip()
    report_path = Path(report_value) if report_value else None
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if report_path is not None:
        _write_windowed_smoke_report(
            report_path,
            {
                "status": "started",
                "argv": effective_argv,
                "executable": sys.executable,
                "frozen": bool(getattr(sys, "frozen", False)),
            },
        )
    try:
        exit_code = int(main(effective_argv))
    except BaseException as exc:
        if report_path is None:
            raise
        _write_windowed_smoke_report(
            report_path,
            {
                "status": "error",
                "argv": effective_argv,
                "exceptionType": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return 1
    if report_path is not None:
        _write_windowed_smoke_report(
            report_path,
            {
                "status": "success" if exit_code == 0 else "exit",
                "argv": effective_argv,
                "exitCode": exit_code,
            },
        )
    return exit_code


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Native GUI viewer for RF mapping data.")
    parser.add_argument(
        "json_path",
        nargs="?",
        default=None,
        help="Path to an RF .rfmap or JSON file. Omit it to open the file chooser.",
    )
    parser.add_argument("--self-test", action="store_true", help="Run data/model tests and exit.")
    parser.add_argument(
        "--self-test-isolated",
        action="store_true",
        help="Run data/model tests through the spawned document loader and exit.",
    )
    parser.add_argument(
        "--self-test-dnd",
        action="store_true",
        help="Load the bundled TkDND runtime and exit.",
    )
    parser.add_argument(
        "--self-test-export",
        metavar="DIRECTORY",
        default=None,
        help="Write packaged PDF, PNG, and CSV smoke outputs, then exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.self_test_dnd:
        try:
            run_tkdnd_self_test()
        except Exception as exc:
            _cli_print(f"TkDND self-test failed: {exc}", error=True)
            return 1
        return 0
    if args.self_test_export is not None:
        try:
            run_figure_export_self_test(Path(args.self_test_export).expanduser())
        except Exception as exc:
            _cli_print(f"Figure export self-test failed: {exc}", error=True)
            return 1
        return 0
    path: Path | None
    if args.json_path is not None:
        path = Path(args.json_path).expanduser()
        if not path.exists():
            _cli_print(f"RF mapping file not found: {path}", error=True)
            return 2
        if document_kind(path) != "rf":
            _cli_print(
                "A .tc or .probe companion needs an RF map; "
                "open a .rfmap or .json file first.",
                error=True,
            )
            return 2
    else:
        path = None
    if args.self_test or args.self_test_isolated:
        if path is None:
            flag = "--self-test-isolated" if args.self_test_isolated else "--self-test"
            _cli_print(f"{flag} requires an explicit RF mapping file", error=True)
            return 2
        assert path is not None
        run_self_test(path, isolated=args.self_test_isolated)
        return 0

    if not TK_AVAILABLE:
        _cli_print(
            "tkinter is not available in this Python; use a local Python with Tk to launch the GUI.",
            error=True,
        )
        return 1

    app = RFMViewer(startup_path=path) if path is not None else RFMViewer()
    app.mainloop()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(_run_main_entrypoint())
