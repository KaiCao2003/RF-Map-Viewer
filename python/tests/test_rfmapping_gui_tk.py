import csv
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import rfmapping_gui as gui


def _current_rf_payload(payload: dict[str, object]) -> dict[str, object]:
    size = payload["unitsSpikeCountsSize"]
    assert isinstance(size, list)
    n_y, n_x = int(size[1]), int(size[2])
    payload.update(
        responseUnits="spike_count",
        responseNormalization="none",
        spikeCountDefinition=(
            "each_qualifying_trial_contributes_once_per_final_spatial_bin"
        ),
        occupancyTimeSec=[[1.0 for _x in range(n_x)] for _y in range(n_y)],
        occupancyTimeSecSize=[n_y, n_x],
        occupancyTimeDefinition=(
            "sum_of_qualifying_trial_durations_per_final_spatial_bin"
        ),
    )
    return payload


def _tk_runtime_error() -> str | None:
    if not gui.TK_AVAILABLE:
        return "this Python was built without tkinter"
    root = None
    try:
        root = gui.tk.Tk()
        root.withdraw()
        root.update_idletasks()
    except gui.tk.TclError as exc:
        return f"Tk could not create a root window: {exc}"
    finally:
        if root is not None:
            try:
                root.destroy()
            except gui.tk.TclError:
                pass
    return None


TK_RUNTIME_ERROR = _tk_runtime_error()


class TkRuntimeAvailabilityTests(unittest.TestCase):
    def test_tk_runtime_is_available_for_integration_tests(self) -> None:
        if TK_RUNTIME_ERROR is not None:
            self.fail(
                f"Tk integration tests are mandatory, but {TK_RUNTIME_ERROR}. "
                "Use a Python build with Tk; on headless Linux, run pytest under xvfb-run."
            )


@unittest.skipIf(TK_RUNTIME_ERROR is not None, TK_RUNTIME_ERROR or "Tk unavailable")
class TkViewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        n_bins = 30
        payload = _current_rf_payload({
            "unitsSpikeCounts": [
                [
                    [[(unit + x + y + bin_idx) % 4 for bin_idx in range(n_bins)] for x in range(3)]
                    for y in range(2)
                ]
                for unit in range(2)
            ],
            "unitsSpikeCountsSize": [2, 2, 3, n_bins],
            "unitPool": [7, 8],
            "xPositions": [-1, 0, 1],
            "yPositions": [-1, 1],
            "timeBinEdges": [index * 0.001 for index in range(n_bins + 1)],
        })
        path = Path(self.directory.name) / "viewer.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        settings_path = Path(self.directory.name) / "settings.json"
        with mock.patch.object(gui, "viewer_settings_path", return_value=settings_path):
            self.app = gui.RFMViewer(gui.RFMappingData(path))
        self.addCleanup(self._destroy_app)
        if self.app._optional_autoload_after is not None:
            self.app.after_cancel(self.app._optional_autoload_after)
            self.app._optional_autoload_after = None
            self.app._optional_autoload_generation += 1
        self.app.notebook.select(2)
        self.app.update()

    def test_combined_tabs_and_default_rf_sum_range(self) -> None:
        self.assertEqual(len(self.app.notebook.tabs()), 3)
        self.assertEqual(
            [self.app.notebook.tab(tab, "text") for tab in self.app.notebook.tabs()],
            ["RF", "Delay / RGB", "Timeline"],
        )
        self.assertEqual(float(self.app.range_start_ms_var.get()), 0.0)
        self.assertEqual(float(self.app.range_end_ms_var.get()), 30.0)

    def test_no_document_window_has_real_open_file_landing(self) -> None:
        chooser = gui.RFMViewer(master=self.app._app_root)
        self.addCleanup(chooser.destroy)
        if chooser._startup_after is not None:
            chooser.after_cancel(chooser._startup_after)
            chooser._startup_after = None

        self.assertFalse(chooser._viewer_ready)
        self.assertIsNotNone(chooser._startup_chooser_frame)
        assert chooser._startup_chooser_frame is not None
        labels = [
            str(widget.cget("text"))
            for widget in chooser._startup_chooser_frame.winfo_children()
            if isinstance(widget, gui.ttk.Label)
        ]
        buttons = [
            str(widget.cget("text"))
            for widget in chooser._startup_chooser_frame.winfo_children()
            if isinstance(widget, gui.ttk.Button)
        ]
        self.assertIn("Open RF mapping data", labels)
        self.assertTrue(any("never loads sample data" in label for label in labels))
        self.assertEqual(buttons, ["Open RF Map…"])

    def test_zero_spike_unit_filter_settings_validate_current_spatial_max(self) -> None:
        settings_value = replace(
            gui.ViewerSettings(),
            rf_filter_units_with_zero_bins=True,
            rf_zero_bin_threshold=1,
        )
        self.app.settings = settings_value
        self.app._app_root._rfm_settings = settings_value
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, gui.SettingsWindow)
        assert isinstance(settings, gui.SettingsWindow)
        self.addCleanup(settings._close)

        self.assertTrue(settings.rf_filter_units_with_zero_bins_var.get())
        self.assertEqual(settings.rf_zero_bin_threshold_var.get(), "1")
        self.assertTrue(settings.rf_zero_bin_threshold_entry.instate(["!disabled"]))

        settings.rf_filter_units_with_zero_bins_var.set(False)
        settings._sync_dependent_controls()
        self.assertTrue(settings.rf_zero_bin_threshold_entry.instate(["disabled"]))

        settings.rf_filter_units_with_zero_bins_var.set(True)
        settings.rf_zero_bin_threshold_var.set("+2")
        self.assertEqual(settings._validated_settings().rf_zero_bin_threshold, 2)

        settings.rf_zero_bin_threshold_var.set("7")
        with self.assertRaisesRegex(gui.SettingsValidationError, r"max is 6"):
            settings._validated_settings()
        settings.rf_zero_bin_threshold_var.set("0")
        with self.assertRaisesRegex(gui.SettingsValidationError, "positive integer"):
            settings._validated_settings()

    def test_zero_spike_filter_tracks_rf_window_and_can_restore_all_units(self) -> None:
        payload = _current_rf_payload({
            "unitsSpikeCounts": [
                [[[1, 0], [1, 0], [1, 0]]],
                [[[0, 1], [0, 1], [0, 1]]],
            ],
            "unitsSpikeCountsSize": [2, 1, 3, 2],
            "unitPool": [10, 20],
            "xPositions": [-1, 0, 1],
            "yPositions": [0],
            "timeBinEdges": [0.0, 0.1, 0.2],
        })
        path = Path(self.directory.name) / "unit-filter.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        settings_value = replace(
            gui.ViewerSettings(),
            rf_sum_start_ms=0.0,
            rf_sum_end_ms=200.0,
            rf_filter_units_with_zero_bins=True,
            rf_zero_bin_threshold=1,
        )
        self.app._app_root._rfm_settings = settings_value
        self.app._load_json_path(path)

        self.assertEqual(self.app._unit_navigation_ids(), [10, 20])
        self.app._set_selected_unit_id(10)
        self.app.range_start_ms_var.set("100")
        self.app.range_end_ms_var.set("200")
        self.app._on_range_changed()
        self.assertEqual(self.app._unit_navigation_ids(), [20])
        self.assertEqual(self.app._selected_unit_id_value(), 20)

        composer = gui.FigureExportWindow(self.app)
        self.addCleanup(composer._close)
        self.assertEqual(composer.unit_ids, (20,))
        self.assertEqual(composer.unit_list.size(), 1)
        self.assertIn("unit 20", composer.unit_list.get(0))

        filter_off = replace(
            self.app.settings,
            rf_filter_units_with_zero_bins=False,
        )
        self.assertTrue(
            self.app._apply_viewer_settings(
                filter_off,
                persist=False,
                broadcast=False,
            )
        )
        self.assertEqual(self.app._unit_navigation_ids(), [10, 20])

    def test_all_filtered_is_nonfatal_and_blocks_empty_figure_composer(self) -> None:
        payload = _current_rf_payload({
            "unitsSpikeCounts": [[[[0], [0]]], [[[0], [0]]]],
            "unitsSpikeCountsSize": [2, 1, 2, 1],
            "unitPool": [10, 20],
            "xPositions": [-1, 1],
            "yPositions": [0],
            "timeBinEdges": [0.0, 0.1],
        })
        path = Path(self.directory.name) / "all-filtered.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        settings_value = replace(
            gui.ViewerSettings(),
            rf_sum_start_ms=0.0,
            rf_sum_end_ms=100.0,
            rf_filter_units_with_zero_bins=True,
            rf_zero_bin_threshold=1,
        )
        self.app._app_root._rfm_settings = settings_value
        self.app._load_json_path(path)

        self.assertEqual(self.app._unit_navigation_ids(), [])
        self.assertEqual(self.app.unit_idx.get(), -1)
        self.assertEqual(self.app._unit_combo_unit_ids, [])
        self.assertIn("No units pass", self.app.status_label.cget("text"))
        with mock.patch.object(gui.messagebox, "showinfo") as showinfo:
            self.app._open_figure_exporter()
        showinfo.assert_called_once()
        self.assertIsNone(self.app.__dict__.get("_figure_export_window"))

        self.assertTrue(
            self.app._apply_viewer_settings(
                replace(settings_value, rf_filter_units_with_zero_bins=False),
                persist=False,
                broadcast=False,
            )
        )
        self.assertEqual(self.app._unit_navigation_ids(), [10, 20])
        self.assertGreaterEqual(self.app.unit_idx.get(), 0)

    def test_global_polar_toggle_applies_to_spatial_tabs(self) -> None:
        self.app.polar_layout_var.set(True)
        self.app.notebook.select(0)
        self.app._draw_rf()
        self.assertEqual(self.app._canvas_layouts["rf"]["geometry"], "polar")

        self.app.notebook.select(1)
        self.app.rgb_mode_var.set(False)
        self.app._draw_delay()
        self.assertEqual(self.app._canvas_layouts["delay"]["geometry"], "polar")
        self.app.rgb_mode_var.set(True)
        self.app._draw_rgb()
        self.assertEqual(self.app._canvas_layouts["delay"]["geometry"], "polar")

    def test_polar_timeline_preview_cache_and_hit_testing(self) -> None:
        self.app.polar_layout_var.set(True)
        self.app.notebook.select(2)
        self.app._timeline_preview_cache_key = None
        self.app._draw_timeline()
        first_atlas = self.app._timeline_preview_images[-1]
        self.app._draw_timeline()
        self.assertIs(self.app._timeline_preview_images[-1], first_atlas)

        layout = self.app._timeline_cells[0]
        event = SimpleNamespace(
            x=int(float(layout["cx"])),
            y=int(
                float(layout["cy"])
                - (gui.INNER_BLANK_ROWS + 0.5) * float(layout["scale"])
            ),
        )
        hit = self.app._timeline_cell_at(event)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], 0)

    def _destroy_app(self) -> None:
        if self.app is not None:
            self.app.destroy()
            self.app = None

    def test_timeline_uses_and_reuses_one_raster_atlas(self) -> None:
        self.app._timeline_preview_cache_key = None
        self.app._draw_timeline()
        first_atlas = self.app._timeline_preview_images[-1]
        first_cache_key = self.app._timeline_preview_cache_key
        self.assertEqual(len(self.app._timeline_preview_images), 1)
        self.assertLess(len(self.app.canvases["timeline"].find_all()), 4 * self.app.data.n_bins)

        self.app._draw_timeline()
        self.assertIs(self.app._timeline_preview_images[-1], first_atlas)
        self.assertEqual(self.app._timeline_preview_cache_key, first_cache_key)

        self.app.range_start_ms_var.set("50")
        self.app.range_end_ms_var.set("150")
        self.app.selected_cell = (1, 1, 2, 2)
        self.app._draw_timeline()
        self.assertIs(self.app._timeline_preview_images[-1], first_atlas)
        self.assertEqual(self.app._timeline_preview_cache_key, first_cache_key)

        self.app.value_mode_var.set(gui.VALUE_MODE_RATE)
        self.app._draw_timeline()
        self.assertIsNot(self.app._timeline_preview_images[-1], first_atlas)
        self.assertNotEqual(self.app._timeline_preview_cache_key, first_cache_key)

    def test_same_cell_hover_reuses_computed_text(self) -> None:
        self.app.notebook.select(0)
        self.app.update()
        self.app._draw_rf()
        layout = self.app._canvas_layouts["rf"]
        event = SimpleNamespace(
            x=int(float(layout["x0"]) + float(layout["cell"]) * 0.5),
            y=int(float(layout["y0"]) + float(layout["cell"]) * 0.5),
        )
        original = self.app._cell_tooltip_text
        calls = 0

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        self.app._cell_tooltip_text = counted
        for _ in range(20):
            self.app._on_canvas_motion("rf", event)
        self.assertEqual(calls, 1)

    def test_destroy_cancels_pending_resize_and_focus_callbacks(self) -> None:
        second = self.app._open_json_window(self.app.data.path)
        self.assertIsNotNone(second)
        assert second is not None
        self.addCleanup(second.destroy)

        callback_errors: list[tuple[object, object, object]] = []
        root = self.app._app_root
        root.report_callback_exception = lambda *exc: callback_errors.append(exc)
        child = self.app
        child._schedule_redraw()
        child._focus_after = child.after_idle(child._focus_rf_canvas)
        self.assertIsNotNone(child._redraw_after)
        self.assertIsNotNone(child._focus_after)

        child.destroy()
        self.assertIsNone(child._redraw_after)
        self.assertIsNone(child._focus_after)
        self.app = None
        root.update_idletasks()
        root.update()

        self.assertTrue(second.winfo_exists())
        self.assertEqual(callback_errors, [])

    def test_figure_composer_constructs_and_finishes_background_preview(self) -> None:
        composer = gui.FigureExportWindow(self.app)
        self.addCleanup(lambda: composer.winfo_exists() and composer.destroy())
        deadline = __import__("time").monotonic() + 5.0
        while __import__("time").monotonic() < deadline:
            self.app.update()
            status = str(composer.preview_status.cget("text"))
            if "provenance verified" in status or "disabled" in status:
                break
        self.assertNotIn("AttributeError", str(composer.preview_label.cget("text")))
        self.assertIn("provenance verified", str(composer.preview_status.cget("text")))

    def test_compact_waveform_settings_and_unit_selection_drive_live_canvas(self) -> None:
        hidden_settings = replace(
            self.app.settings,
            show_waveform=False,
            waveform_channel_mode="same_x_column",
        )
        self.assertTrue(
            self.app._apply_viewer_settings(
                hidden_settings,
                persist=False,
                broadcast=False,
            )
        )
        self.app._app_root._rfm_settings = hidden_settings
        self.app._app_root._rfm_settings_path = (
            Path(self.directory.name) / "waveform-settings.json"
        )

        requests: list[tuple[int, str]] = []

        def waveform_payload(unit_id: int, channel_mode: str) -> dict[str, object]:
            requests.append((unit_id, channel_mode))
            mode_offset = 10.0 if channel_mode == "same_shank" else 0.0
            return {
                "unit_id": unit_id,
                "channel_mode": channel_mode,
                "matrix": tuple(
                    tuple(
                        float(unit_id + row + sample) + mode_offset
                        for sample in range(4)
                    )
                    for row in range(5)
                ),
                "times_ms": (-0.5, -0.25, 0.0, 0.25),
                "channel_labels": tuple(
                    f"{channel_mode} ch {row}" for row in range(5)
                ),
                "best_channel_row": 2,
                "amplitude_limit_uv": 40.0,
                "max_ptp_uv": 80.0,
                "baseline_end_ms": -0.25,
            }

        self.app.data.waveform_plot_payload = waveform_payload  # type: ignore[method-assign]

        def wait_for_payload(key: tuple[int, str]) -> None:
            deadline = __import__("time").monotonic() + 2.0
            while __import__("time").monotonic() < deadline:
                self.app.update()
                if self.app._waveform_payload_key == key:
                    return
            self.fail(f"Timed out waiting for waveform payload {key}")

        settings_value = replace(hidden_settings, show_waveform=True)
        # Exercise the supported minimum window where the responsive layout
        # must not let the compact waveform squeeze HD down to its header.
        self.app.geometry("1120x720")
        self.app.notebook.select(0)
        self.app.update()
        self.assertTrue(
            self.app._apply_viewer_settings(
                settings_value,
                persist=False,
                broadcast=False,
            )
        )
        self.app._app_root._rfm_settings = settings_value
        wait_for_payload((7, "same_x_column"))
        self.assertEqual(self.app._active_tab_key(), "rf")
        self.assertTrue(self.app.waveform_pane.winfo_ismapped())
        self.assertEqual(int(self.app.tuning_curve_section.grid_info()["row"]), 0)
        self.assertEqual(int(self.app.unit_info_pane.grid_info()["row"]), 1)
        self.assertIs(self.app.waveform_pane.master, self.app.waveform_host)
        self.assertEqual(int(self.app.waveform_pane.grid_info()["row"]), 0)
        self.assertGreaterEqual(
            self.app.waveform_pane.winfo_rooty(),
            self.app.cell_label.winfo_rooty() + self.app.cell_label.winfo_height(),
        )
        self.assertLessEqual(self.app.waveform_pane.winfo_height(), 200)
        self.assertFalse(self.app.tuning_curve_status_label.winfo_ismapped())
        self.assertIn("bin", self.app.cell_label.cget("text"))
        self.assertIn("cluster 7", self.app.unit_stats_label.cget("text"))
        self.assertGreater(len(self.app.canvases["waveform"].find_all()), 20)
        self.assertIn("Same x column", self.app.waveform_subtitle_label.cget("text"))

        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, gui.SettingsWindow)
        assert isinstance(settings, gui.SettingsWindow)
        settings.notebook.select(settings._tab_widget_by_name["Waveform"])
        settings.waveform_channel_mode_var.set("Same shank")
        settings._save()
        self.assertFalse(settings.winfo_exists())

        wait_for_payload((7, "same_shank"))
        self.assertEqual(self.app.waveform_channel_mode_var.get(), "same_shank")
        self.assertIn("Same shank", self.app.waveform_subtitle_label.cget("text"))
        saved = json.loads(
            self.app._app_root._rfm_settings_path.read_text(encoding="utf-8")
        )
        self.assertEqual(saved["waveform_channel_mode"], "same_shank")

        self.app.unit_combo.current(1)
        self.app.unit_combo.event_generate("<<ComboboxSelected>>")
        wait_for_payload((8, "same_shank"))
        self.assertEqual(self.app._selected_unit_id_value(), 8)
        self.assertIn("Cluster 8", self.app.waveform_subtitle_label.cget("text"))

        with mock.patch.object(gui.FigureExportWindow, "_schedule_preview"):
            self.app.export_toolbar_button.invoke()
        composer = self.app._figure_export_window
        self.addCleanup(lambda: composer.winfo_exists() and composer.destroy())
        self.assertIn(gui.PlotKind.WAVEFORM_LOCAL_AVERAGE, composer.available_kinds)
        composer.pages[0]["plots"] = [gui.PlotKind.WAVEFORM_LOCAL_AVERAGE]
        self.assertEqual(
            composer.pages[0]["plots"],
            [gui.PlotKind.WAVEFORM_LOCAL_AVERAGE],
        )
        self.assertEqual(composer.snapshot.waveform_channel_mode, "same_shank")

        request_count = len(requests)
        hidden = replace(self.app.settings, show_waveform=False)
        self.assertTrue(
            self.app._apply_viewer_settings(
                hidden,
                persist=False,
                broadcast=False,
            )
        )
        self.app.update()
        self.assertFalse(self.app.waveform_pane.winfo_ismapped())
        self.app._step_unit(1)
        self.app.update()
        self.assertEqual(len(requests), request_count)

    def test_preview_daemon_worker_cooperatively_cancels_on_close(self) -> None:
        started = threading.Event()
        cancellation_observed = threading.Event()
        root = self.app._app_root
        jobs_before = gui._active_export_jobs(root)

        def freeze_until_cancelled(_unit_ids, _raw_pages, cancelled):
            started.set()
            deadline = __import__("time").monotonic() + 2.0
            while not cancelled():
                if __import__("time").monotonic() >= deadline:
                    raise AssertionError("preview cancellation was not delivered")
                __import__("time").sleep(0.005)
            cancellation_observed.set()
            raise RuntimeError("Preview superseded by a newer recipe")

        with mock.patch.object(
            gui,
            "_export_executor",
            side_effect=AssertionError("preview must not use the final-export executor"),
        ):
            composer = gui.FigureExportWindow(self.app)
            if composer._preview_after is not None:
                composer.after_cancel(composer._preview_after)
                composer._preview_after = None
            composer._freeze_context = freeze_until_cancelled
            composer._start_preview(composer._preview_generation)
            self.assertTrue(started.wait(timeout=1.0))
            preview_future = composer._preview_future
            self.assertIsNotNone(preview_future)
            self.assertEqual(gui._active_export_jobs(root), jobs_before)

            composer.destroy()
            self.assertFalse(composer.winfo_exists())
            self.assertTrue(cancellation_observed.wait(timeout=1.0))
            assert preview_future is not None
            with self.assertRaisesRegex(RuntimeError, "superseded"):
                preview_future.result(timeout=1.0)

        root.update_idletasks()
        root.update()

    def test_registered_export_blocks_close_and_quit_until_result_is_consumed(self) -> None:
        second = self.app._open_json_window(self.app.data.path)
        self.assertIsNotNone(second)
        assert second is not None
        self.addCleanup(second.destroy)
        root = self.app._app_root
        release = threading.Event()
        self.addCleanup(release.set)
        future = gui._export_executor(root).submit(release.wait, 2.0)
        gui._register_export_job(root, self.app, future)
        self.addCleanup(gui._unregister_export_job, root, future)

        with mock.patch.object(gui.messagebox, "showinfo") as showinfo:
            self.app._close_window()
            self.app._quit_application()
            self.assertTrue(self.app.winfo_exists())
            self.assertTrue(root.winfo_exists())

            release.set()
            self.assertTrue(future.result(timeout=1.0))
            # Completion alone is insufficient: Tk has not consumed/reported the result.
            self.app._close_window()
            self.app._quit_application()
            self.assertTrue(self.app.winfo_exists())
            self.assertTrue(root.winfo_exists())
            self.assertEqual(showinfo.call_count, 4)

            gui._unregister_export_job(root, future)
            child = self.app
            child._close_window()
            self.assertFalse(child.winfo_exists())
            self.app = None

            self.assertTrue(second.winfo_exists())
            second._quit_application()

        try:
            root_exists = bool(root.winfo_exists())
        except gui.tk.TclError:
            root_exists = False
        self.assertFalse(root_exists)

    def test_document_windows_keep_independent_state(self) -> None:
        second = self.app._open_json_window(self.app.data.path)
        self.assertIsNotNone(second)
        assert second is not None
        self.addCleanup(second.destroy)

        self.assertEqual(len(self.app._app_root._rfm_viewer_windows), 2)
        second._step_unit(1)
        self.assertEqual(self.app.unit_idx.get(), 0)
        self.assertEqual(second.unit_idx.get(), 1)

    def test_file_open_always_opens_a_new_window(self) -> None:
        selected_path = self.app.data.path.resolve()
        with (
            mock.patch.object(
                gui.filedialog,
                "askopenfilename",
                return_value=str(selected_path),
            ),
            mock.patch.object(self.app, "_open_json_window") as opener,
        ):
            self.app._open_json()
        opener.assert_called_once_with(self.app.data.path.resolve())

    def test_display_controls_are_collapsible_in_main_toolbar(self) -> None:
        display_frame = self.app.x_bins_spin.master
        main_panel = self.app.nametowidget(self.app.notebook.winfo_parent())

        self.assertEqual(display_frame.winfo_manager(), "")
        self.assertIs(self.app.y_bins_spin.master, display_frame)
        self.assertIs(self.app.smooth_spin.master, display_frame)
        self.assertIs(display_frame.master.master, main_panel)
        self.app._toggle_display_controls()
        self.assertEqual(display_frame.winfo_manager(), "grid")
        self.assertEqual(int(display_frame.grid_info()["row"]), 1)

    def test_sidebar_has_no_horizontal_separators(self) -> None:
        sidebar = self.app.sidebar_frame
        separator_children = [
            child
            for child in sidebar.winfo_children()
            if child.winfo_class() == "TSeparator"
        ]
        self.assertEqual(separator_children, [])

    def test_pairing_status_reports_matching_unit_lists(self) -> None:
        second = self.app._open_json_window(self.app.data.path)
        self.assertIsNotNone(second)
        assert second is not None
        self.addCleanup(second.destroy)

        self.assertIn("matching unit lists", self.app.pair_status_label.cget("text"))
        self.assertIn("matching unit lists", second.pair_status_label.cget("text"))

    def test_paired_filter_settings_reconcile_one_shared_visible_unit(self) -> None:
        payload = _current_rf_payload({
            "unitsSpikeCounts": [[[[1], [1]]], [[[0], [1]]]],
            "unitsSpikeCountsSize": [2, 1, 2, 1],
            "unitPool": [7, 8],
            "xPositions": [-1, 1],
            "yPositions": [0],
            "timeBinEdges": [0.0, 0.1],
        })
        path = Path(self.directory.name) / "paired-filter.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        filter_off = replace(
            gui.ViewerSettings(),
            rf_sum_start_ms=0.0,
            rf_sum_end_ms=100.0,
            rf_filter_units_with_zero_bins=False,
        )
        self.app._app_root._rfm_settings = filter_off
        self.app._load_json_path(path)
        second = self.app._open_json_window(path)
        self.assertIsNotNone(second)
        assert second is not None
        self.addCleanup(second.destroy)

        self.app.pair_windows_var.set(True)
        self.app._on_pair_windows_toggled()
        self.app._set_selected_unit_id(8)
        self.app._publish_pairing_state_if_changed()
        self.assertEqual(second._selected_unit_id_value(), 8)

        filter_on = replace(
            filter_off,
            rf_filter_units_with_zero_bins=True,
            rf_zero_bin_threshold=1,
        )
        self.assertTrue(
            self.app._apply_viewer_settings(
                filter_on,
                persist=False,
                broadcast=True,
            )
        )

        self.assertEqual(self.app._unit_navigation_ids(), [7])
        self.assertEqual(second._unit_navigation_ids(), [7])
        self.assertEqual(self.app._selected_unit_id_value(), 7)
        self.assertEqual(second._selected_unit_id_value(), 7)
        self.assertEqual(self.app._app_root._rfm_pairing_state.unit_id, 7)

    def test_pairing_status_warns_when_unit_lists_differ(self) -> None:
        payload = json.loads(self.app.data.path.read_text(encoding="utf-8"))
        payload["unitPool"] = [7, 9]
        different_path = Path(self.directory.name) / "different-units.json"
        different_path.write_text(json.dumps(payload), encoding="utf-8")

        second = self.app._open_json_window(different_path)
        self.assertIsNotNone(second)
        assert second is not None
        self.addCleanup(second.destroy)

        self.assertIn("Unit lists differ", self.app.pair_status_label.cget("text"))
        self.assertIn("Unit lists differ", second.pair_status_label.cget("text"))

    def test_pairing_navigates_union_and_shows_na_for_missing_units(self) -> None:
        def write_units(name: str, unit_ids: list[int]) -> Path:
            n_bins = 30
            payload = _current_rf_payload({
                "unitsSpikeCounts": [
                    [
                        [
                            [(unit + x + y + bin_idx) % 4 for bin_idx in range(n_bins)]
                            for x in range(3)
                        ]
                        for y in range(2)
                    ]
                    for unit in range(len(unit_ids))
                ],
                "unitsSpikeCountsSize": [len(unit_ids), 2, 3, n_bins],
                "unitPool": unit_ids,
                "xPositions": [-1, 0, 1],
                "yPositions": [-1, 1],
                "timeBinEdges": [index * 0.001 for index in range(n_bins + 1)],
            })
            path = Path(self.directory.name) / name
            path.write_text(json.dumps(payload), encoding="utf-8")
            return path

        main_path = write_units("main.json", [1, 3, 5, 7])
        sync_one_path = write_units("sync-one.json", [1, 3, 5, 6, 7])
        sync_two_path = write_units("sync-two.json", [2, 3, 5, 7])
        self.app._load_json_path(main_path)
        sync_one = self.app._open_json_window(sync_one_path)
        sync_two = self.app._open_json_window(sync_two_path)
        self.assertIsNotNone(sync_one)
        self.assertIsNotNone(sync_two)
        assert sync_one is not None and sync_two is not None
        self.addCleanup(sync_one.destroy)
        self.addCleanup(sync_two.destroy)

        self.app.pair_windows_var.set(True)
        self.app._on_pair_windows_toggled()
        self.assertEqual(self.app._unit_combo_unit_ids, [1, 2, 3, 5, 6, 7])

        self.app._step_unit(1)
        self.assertEqual(
            [
                self.app._selected_unit_id,
                sync_one._selected_unit_id,
                sync_two._selected_unit_id,
            ],
            [2, 2, 2],
        )
        self.assertEqual(
            [self.app.unit_idx.get(), sync_one.unit_idx.get(), sync_two.unit_idx.get()],
            [-1, -1, 0],
        )
        self.assertIn("Unit N/A / cluster 2", self.app.header_label.cget("text"))
        self.assertIn("Unit N/A / cluster 2", sync_one.header_label.cget("text"))
        self.app._clear_hover()
        self.assertIn("N/A: cluster 2", self.app.status_label.cget("text"))

        self.app._step_unit(1)
        self.assertEqual(
            [self.app.unit_idx.get(), sync_one.unit_idx.get(), sync_two.unit_idx.get()],
            [1, 1, 1],
        )

    def test_arrow_keys_control_units_and_timeline_bin(self) -> None:
        event = SimpleNamespace(widget=self.app.canvases["timeline"])
        initial_rf_range = (
            self.app.range_start_ms_var.get(),
            self.app.range_end_ms_var.get(),
        )
        initial_rf_matrix = self.app._current_matrix()

        self.app._run_navigation_shortcut(event, self.app._step_unit, -1)
        self.assertEqual(self.app.unit_idx.get(), 1)
        self.app._run_navigation_shortcut(event, self.app._step_unit, 1)
        self.assertEqual(self.app.unit_idx.get(), 0)

        self.app.bin_var.set(5)
        self.app._run_navigation_shortcut(event, self.app._step_timeline_bin, -1)
        self.assertEqual(self.app.bin_var.get(), 4)
        self.assertEqual((self.app.range_start_var.get(), self.app.range_end_var.get()), (4, 4))
        self.app._run_navigation_shortcut(event, self.app._step_timeline_bin, 1)
        self.assertEqual(self.app.bin_var.get(), 5)
        self.assertEqual(
            (self.app.range_start_ms_var.get(), self.app.range_end_ms_var.get()),
            initial_rf_range,
        )
        self.assertEqual(self.app._current_matrix(), initial_rf_matrix)

    def test_shift_comma_and_period_adjust_resolution_one_ms(self) -> None:
        event = SimpleNamespace(widget=self.app.canvases["timeline"])
        self.app.time_res_ms_var.set("5")

        self.app._run_navigation_shortcut(event, self.app._step_time_resolution, -1.0)
        self.assertEqual(float(self.app.time_res_ms_var.get()), 4.0)
        self.app._run_navigation_shortcut(event, self.app._step_time_resolution, 1.0)
        self.assertEqual(float(self.app.time_res_ms_var.get()), 5.0)

    def test_navigation_shortcuts_do_not_override_text_editing(self) -> None:
        event = SimpleNamespace(widget=self.app.time_res_spin)
        result = self.app._run_navigation_shortcut(event, self.app._step_unit, 1)
        self.assertIsNone(result)
        self.assertEqual(self.app.unit_idx.get(), 0)

    def test_view_shortcuts_switch_tabs_flip_y_and_cycle_palette(self) -> None:
        event = SimpleNamespace(widget=self.app.canvases["rf"])
        self.app._run_navigation_shortcut(event, self.app._select_tab, 2)
        self.assertEqual(self.app._active_tab_key(), "timeline")

        self.assertFalse(self.app.flip_y_var.get())
        self.app._run_navigation_shortcut(event, self.app._toggle_flip_y)
        self.assertTrue(self.app.flip_y_var.get())

        self.app.palette_var.set("Gray")
        self.app._run_navigation_shortcut(event, self.app._cycle_palette)
        self.assertEqual(self.app.palette_var.get(), "Viridis")

    def test_export_records_displayed_rate_and_units(self) -> None:
        destination = Path(self.directory.name) / "rate.csv"
        original_ask = gui.filedialog.asksaveasfilename
        original_info = gui.messagebox.showinfo
        self.addCleanup(setattr, gui.filedialog, "asksaveasfilename", original_ask)
        self.addCleanup(setattr, gui.messagebox, "showinfo", original_info)
        gui.filedialog.asksaveasfilename = lambda **_kwargs: str(destination)
        gui.messagebox.showinfo = lambda *_args, **_kwargs: None

        self.app.value_mode_var.set(gui.VALUE_MODE_RATE)
        expected = self.app._current_matrix()[0][0]
        self.app._export_current_matrix()

        with destination.open(newline="", encoding="utf-8") as file:
            first = next(csv.DictReader(file))
        self.assertEqual(first["value_mode"], gui.VALUE_MODE_RATE)
        self.assertEqual(first["value_unit"], "Hz")
        self.assertEqual(first["occupancy_time_sec_min"], "1.0")
        self.assertAlmostEqual(float(first["value"]), expected)


if __name__ == "__main__":
    unittest.main()
