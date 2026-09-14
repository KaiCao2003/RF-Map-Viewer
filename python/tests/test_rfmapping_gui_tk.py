import csv
import gc
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import rfmapping_gui as gui
import rfmapping_viewer.companions as companions_module
import rfmapping_viewer.constants as constants_module
import rfmapping_viewer.export_inputs as export_inputs_module
import rfmapping_viewer.figure_composer as figure_composer_module
import rfmapping_viewer.rf_model as rf_model_module
import rfmapping_viewer.settings as settings_module
import rfmapping_viewer.settings_window as settings_window_module
import rfmapping_viewer.tk_support as tk_support_module
from gui_test_support import base_payload, current_rf_payload, write_payload




def _tk_runtime_error() -> str | None:
    if not tk_support_module.TK_AVAILABLE:
        return "this Python was built without tkinter"
    root = None
    try:
        root = tk_support_module.tk.Tk()
        root.withdraw()
        root.update_idletasks()
    except tk_support_module.tk.TclError as exc:
        return f"Tk could not create a root window: {exc}"
    finally:
        if root is not None:
            try:
                root.destroy()
            except tk_support_module.tk.TclError:
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
        payload = current_rf_payload({
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
            self.app = gui.RFMViewer(rf_model_module.RFMappingData(path))
        self.addCleanup(self._destroy_app)
        if self.app._optional_autoload_after is not None:
            self.app.after_cancel(self.app._optional_autoload_after)
            self.app._optional_autoload_after = None
            self.app._optional_autoload_generation += 1
        self.app.notebook.select(2)
        self.app.update()

    def test_spatial_controls_leave_companions_unchanged_and_reuse_temporal_data(self) -> None:
        self.app.notebook.select(1)
        self.app.update()
        self.app.smooth_radius_var.set(1)
        self.app._on_control_changed()
        with (
            mock.patch.object(self.app.data, "spatial_group_histograms_array", side_effect=AssertionError("recomputed temporal data")),
            mock.patch.object(self.app, "_draw_probe_canvas") as probe,
            mock.patch.object(self.app, "_draw_tuning_curve") as tuning,
            mock.patch.object(self.app, "_draw_waveform") as waveform,
            mock.patch.object(self.app, "_sync_unit_combo") as units,
        ):
            self.app.palette_var.set("Inferno")
            self.app.rgb_mode_var.set(True)
            self.app._on_control_changed()
            probe.assert_not_called()
            tuning.assert_not_called()
            waveform.assert_not_called()
            units.assert_not_called()
        with mock.patch.object(
            self.app.data, "spatial_group_histograms_array",
            wraps=self.app.data.spatial_group_histograms_array,
        ) as pooling:
            self.app._step_unit(1)
            self.assertEqual(pooling.call_count, 1)

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
            if isinstance(widget, tk_support_module.ttk.Label)
        ]
        buttons = [
            str(widget.cget("text"))
            for widget in chooser._startup_chooser_frame.winfo_children()
            if isinstance(widget, tk_support_module.ttk.Button)
        ]
        self.assertIn("Open RF mapping data", labels)
        self.assertTrue(any("never loads sample data" in label for label in labels))
        self.assertEqual(buttons, ["Open RF Map…"])

    def test_delayed_macos_document_open_never_opens_file_chooser(self) -> None:
        with (
            mock.patch.object(gui.sys, "platform", "darwin"),
            mock.patch.object(gui.filedialog, "askopenfilename", return_value="") as dialog,
        ):
            viewer = gui.RFMViewer(master=self.app._app_root)
            self.addCleanup(viewer.destroy)
            # Finder may deliver the file after the former 200 ms picker timer.
            viewer.after(
                350,
                lambda: viewer.tk.call("::tk::mac::OpenDocument", str(self.app.data.path)),
            )
            deadline = time.monotonic() + 5
            while not viewer._viewer_ready and time.monotonic() < deadline:
                viewer.update()
                time.sleep(0.01)

            self.assertTrue(viewer._viewer_ready)
            self.assertEqual(viewer.data.path, self.app.data.path)
            dialog.assert_not_called()

    def test_macos_application_open_shows_file_chooser(self) -> None:
        with (
            mock.patch.object(gui.sys, "platform", "darwin"),
            mock.patch.object(gui.filedialog, "askopenfilename", return_value="") as dialog,
        ):
            viewer = gui.RFMViewer(master=self.app._app_root)
            self.addCleanup(viewer.destroy)
            viewer.tk.call("::tk::mac::OpenApplication")
            viewer.update()

            dialog.assert_called_once()
            self.assertFalse(viewer._viewer_ready)

    def test_macos_application_open_during_tk_initialization_is_preserved(self) -> None:
        loadtk = gui.tk.Tk.loadtk

        def loadtk_with_application_event(root) -> None:
            # A Python createcommand callback here aborts inside macOS Tk_Init.
            self.assertEqual(
                root.tk.call("info", "procs", "::tk::mac::OpenApplication"),
                ("::tk::mac::OpenApplication",),
            )
            root.tk.call("::tk::mac::OpenApplication")
            loadtk(root)

        with (
            mock.patch.object(gui.sys, "platform", "darwin"),
            mock.patch.object(gui.tk.Tk, "loadtk", loadtk_with_application_event),
            mock.patch.object(gui.filedialog, "askopenfilename", return_value="") as dialog,
        ):
            viewer = gui.RFMViewer()
            self.addCleanup(viewer.destroy)
            viewer.update()

            dialog.assert_called_once()
            self.assertFalse(viewer._viewer_ready)

    def test_macos_application_open_does_not_interrupt_pending_document(self) -> None:
        with (
            mock.patch.object(gui.sys, "platform", "darwin"),
            mock.patch.object(gui.filedialog, "askopenfilename", return_value="") as dialog,
        ):
            viewer = gui.RFMViewer(master=self.app._app_root)
            self.addCleanup(viewer.destroy)
            viewer.tk.call("::tk::mac::OpenDocument", str(self.app.data.path))
            viewer.tk.call("::tk::mac::OpenApplication")
            deadline = time.monotonic() + 5
            while not viewer._viewer_ready and time.monotonic() < deadline:
                viewer.update()
                time.sleep(0.01)

            self.assertTrue(viewer._viewer_ready)
            dialog.assert_not_called()

    def test_rf_subtraction_shortcut_controls_and_display_toggle(self) -> None:
        self.app.settings = replace(self.app.settings, rf_difference_start_ms=8, rf_difference_end_ms=16)
        self.app._reset_rf_window_defaults()
        self.app._select_tab(0)
        self.app.update()
        self.app.range_start_ms_var.set("8")
        self.app.range_end_ms_var.set("16")
        self.app.subtract_start_ms_var.set("0")
        self.app.subtract_end_ms_var.set("8")
        self.app._on_range_changed()
        original = self.app._current_matrix()
        for widget in (self.app.canvases["rf"], self.app.notebook):
            widget.focus_force()
            widget.event_generate("<KeyPress-minus>")
            self.app.update()
            self.assertTrue(self.app.rf_subtract_var.get())
            self.assertTrue(self.app.subtract_controls_frame.winfo_ismapped())
            self.assertEqual(self.app._rf_window_expression(), "(8 ms – 16 ms) − (0 ms – 8 ms)")
            # Each cell has the same counts in these two complete periods.
            self.assertEqual(self.app._current_matrix(), [[0.0] * 3, [0.0] * 3])
            self.assertEqual(len(self.app._visible_timeline_bins(self.app._time_group_count())), self.app._time_group_count())
            self.app._toggle_rf_subtraction()
            self.assertEqual(self.app._current_matrix(), original)
        self.app.notebook.focus_force()
        self.app.notebook.event_generate("<KeyPress-d>")
        self.app.update()
        self.assertTrue(self.app.display_expanded_var.get())
        self.assertEqual(self.app.display_toggle_button.cget("text"), "Hide (D)")
        self.assertEqual(self.app._view_menu.entrycget(self.app._display_options_menu_index, "accelerator"), "D")
        self.app.notebook.event_generate("<KeyPress-d>")
        self.app.update()
        self.assertFalse(self.app.display_expanded_var.get())
        self.assertEqual(self.app.display_toggle_button.cget("text"), "Display Options (D)")
        self.app.range_start_spin.focus_force()
        self.app.range_start_spin.delete(0, "end")
        self.app.range_start_spin.event_generate("<KeyPress-minus>")
        self.app.update()
        self.assertEqual(self.app.range_start_ms_var.get(), "-")
        self.assertFalse(self.app.rf_subtract_var.get())

    def test_rf_subtraction_plot_csv_snapshot_and_nan_color_agree(self) -> None:
        self.app.settings = replace(self.app.settings, rf_filter_units_with_zero_bins=False)
        self.app._select_tab(0)
        self.app._toggle_rf_subtraction()
        self.app.range_start_ms_var.set("0")
        self.app.range_end_ms_var.set("1")
        self.app.subtract_start_ms_var.set("1")
        self.app.subtract_end_ms_var.set("2")
        self.app._on_range_changed()
        expected = [[None, None, None], [None, None, 3.0]]
        self.assertEqual(self.app._current_matrix(), expected)
        self.assertEqual(self.app._prepare_rf_plot_matrix()[0], expected)
        self.assertIn("NaN", self.app._cell_tooltip_text((0, 0, 0, 0)))
        snapshot = figure_composer_module.FigureViewerSnapshot.capture(self.app)
        self.assertEqual(snapshot.rf_subtract_source_range, (1, 1))
        self.assertEqual(figure_composer_module.GUIFigureDataProvider(self.app.data, snapshot)._rf_matrix(0, polar=False), expected)
        for polar in (False, True):
            self.app.polar_layout_var.set(polar)
            with mock.patch.object(self.app, "_draw_missing_hatch") as hatch:
                self.app._draw_rf()
                hatch.assert_not_called()
            canvas = self.app.canvases["rf"]
            self.assertIn("NaN", [canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text"])
        destination = Path(self.directory.name) / "difference.csv"
        with mock.patch.object(tk_support_module.filedialog, "asksaveasfilename", return_value=str(destination)), mock.patch.object(tk_support_module.messagebox, "showinfo"):
            self.app._export_current_matrix()
        with destination.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["value"] for row in rows], ["", "", "", "", "", "3.0"])
        self.assertTrue(all(row["rf_window_operation"] == "A - B" for row in rows))
        self.assertEqual((rows[0]["rf_subtract_start_ms"], rows[0]["rf_subtract_end_ms"]), ("1.0", "2.0"))

    def test_rf_subtraction_normalizes_both_ranges_and_fits_minimum_window(self) -> None:
        self.app._select_tab(0)
        self.app.geometry("1120x720")
        self.app._toggle_rf_subtraction()
        self.app.range_start_ms_var.set("999")
        self.app.range_end_ms_var.set("-999")
        self.app.subtract_start_ms_var.set("17.8")
        self.app.subtract_end_ms_var.set("8.2")
        self.app._on_range_changed()
        self.app._toggle_display_controls()
        self.app.update()
        self.assertEqual(self.app._selected_time_bounds_ms(), (0.0, 30.0))
        self.assertEqual(self.app._source_bins_for_subtract_controls(), (8, 17))
        self.assertEqual(self.app.subtract_start_ms_var.get(), "8")
        self.assertEqual(self.app.subtract_end_ms_var.get(), "18")
        for widget in (self.app.reset_plot_range_button, self.app.display_toggle_button):
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), self.app.winfo_rootx() + self.app.winfo_width())
        self.assertGreaterEqual(self.app.display_controls_frame.winfo_rooty(), self.app.range_controls_frame.winfo_rooty() + self.app.range_controls_frame.winfo_height())
        self.app._toggle_rf_subtraction()
        self.app.update()
        for widget in (self.app.reset_plot_range_button, self.app.display_toggle_button):
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), self.app.winfo_rootx() + self.app.winfo_width())

    def test_rf_subtraction_pairs_both_windows_and_mode(self) -> None:
        second = self.app._open_json_window(self.app.data.path)
        self.assertIsNotNone(second)
        self.addCleanup(second.destroy)
        self.app.pair_windows_var.set(True)
        self.app._on_pair_windows_toggled()
        self.app._toggle_rf_subtraction()
        self.app.range_start_ms_var.set("8")
        self.app.range_end_ms_var.set("16")
        self.app.subtract_start_ms_var.set("0")
        self.app.subtract_end_ms_var.set("8")
        self.app._on_range_changed()
        self.assertTrue(second.rf_subtract_var.get())
        self.assertEqual(second._rf_window_expression(), "(8 ms – 16 ms) − (0 ms – 8 ms)")
        self.assertEqual(second._current_matrix(), self.app._current_matrix())
        second.subtract_end_ms_var.set("4")
        second._on_range_changed()
        self.assertEqual(self.app.subtract_end_ms_var.get(), "4")
        second._toggle_rf_subtraction()
        self.assertFalse(self.app.rf_subtract_var.get())
        first_range = self.app._selected_time_bounds_ms()
        self.app._toggle_zero_bin_filter()
        self.assertFalse(second.settings.rf_filter_units_with_zero_bins)
        self.assertFalse(self.app.settings.rf_filter_units_with_zero_bins)
        self.assertEqual(self.app._selected_time_bounds_ms(), first_range)

    def test_settings_save_default_mode_and_independent_rf_windows(self) -> None:
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        settings.rf_window_mode_var.set("A − B")
        settings.rf_sum_start_var.set("0")
        settings.rf_sum_end_var.set("20")
        settings.rf_difference_start_var.set("8")
        settings.rf_difference_end_var.set("16")
        settings.rf_subtract_start_var.set("0")
        settings.rf_subtract_end_var.set("8")
        settings._save()
        stored = settings_module.load_viewer_settings(self.app._app_root._rfm_settings_path)
        self.assertTrue(stored.rf_subtract)
        self.assertEqual((stored.rf_difference_start_ms, stored.rf_difference_end_ms), (8, 16))
        self.assertEqual((stored.rf_subtract_start_ms, stored.rf_subtract_end_ms), (0, 8))
        self.assertEqual(self.app._rf_window_expression(), "(8 ms – 16 ms) − (0 ms – 8 ms)")
        self.app.range_end_ms_var.set("18")
        self.app._on_range_changed()
        self.app._toggle_rf_subtraction()
        self.assertEqual(self.app._selected_time_bounds_ms(), (0.0, 20.0))
        self.app._toggle_rf_subtraction()
        self.assertEqual(self.app._selected_time_bounds_ms()[0], 8.0)
        self.assertAlmostEqual(self.app._selected_time_bounds_ms()[1], 18.0)
        second = self.app._open_json_window(self.app.data.path)
        self.addCleanup(second.destroy)
        self.assertTrue(second.rf_subtract_var.get())
        self.assertEqual(second._rf_window_expression(), "(8 ms – 16 ms) − (0 ms – 8 ms)")

    def test_settings_reject_invalid_subtraction_windows(self) -> None:
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        for start_var, end_var in (
            (settings.rf_difference_start_var, settings.rf_difference_end_var),
            (settings.rf_subtract_start_var, settings.rf_subtract_end_var),
        ):
            original = (start_var.get(), end_var.get())
            for first, last in (("20", "10"), ("10", "10"), ("nan", "20"), ("0", "inf"), ("bad", "20")):
                start_var.set(first)
                end_var.set(last)
                with self.assertRaises(settings_window_module.SettingsValidationError):
                    settings._validated_settings()
            start_var.set(original[0])
            end_var.set(original[1])

    def test_unit_filter_shortcut_restores_units_without_changing_live_controls(self) -> None:
        self.app._select_tab(0)
        self.app.range_start_ms_var.set("0")
        self.app.range_end_ms_var.set("1")
        self.app.time_res_ms_var.set("3")
        self.app.palette_var.set("Viridis")
        self.app._on_range_changed()
        self.assertEqual(self.app._local_quality_visible_unit_ids(), [])
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        settings.rf_subtract_end_var.set("50")  # An unrelated, unsaved edit.
        self.app._bind_unit_filter_shortcut()
        canvas = self.app.canvases["rf"]
        sequences = ["<Command-Shift-period>", "<Command-Shift-greater>"]
        # Xvfb cannot map kana_fullstop to a keycode and generates "??".
        # Exercise the IME keysym on Aqua, where the physical key reports it.
        if self.app.tk.call("tk", "windowingsystem") == "aqua":
            sequences.append("<Command-Shift-kana_fullstop>")
        for sequence in sequences:
            canvas.focus_force()
            self.app.update()
            canvas.event_generate(sequence)
            self.app.update()
            self.assertFalse(self.app.settings.rf_filter_units_with_zero_bins)
            self.assertEqual(self.app._local_quality_visible_unit_ids(), [7, 8])
            self.assertFalse(settings.rf_filter_units_with_zero_bins_var.get())
            self.assertEqual(settings.rf_subtract_end_var.get(), "50")
            self.assertEqual(self.app._selected_time_bounds_ms(), (0.0, 1.0))
            self.assertEqual(self.app.time_res_ms_var.get(), "3")
            self.assertEqual(self.app.palette_var.get(), "Viridis")
            self.assertFalse(settings_module.load_viewer_settings(self.app._app_root._rfm_settings_path).rf_filter_units_with_zero_bins)
            canvas.event_generate(sequence)
            self.app.update()
            self.assertTrue(self.app.settings.rf_filter_units_with_zero_bins)
            self.assertEqual(self.app._local_quality_visible_unit_ids(), [])
        canvas.event_generate("<KeyPress-greater>")
        self.app.update()
        self.assertNotEqual(self.app.time_res_ms_var.get(), "3")
        self.assertTrue(self.app.settings.rf_filter_units_with_zero_bins)
        self.app._bind_unit_filter_shortcut(settings)
        settings.notebook.focus_force()
        self.app.update()
        settings.notebook.event_generate(sequences[-1])
        self.app.update()
        self.assertFalse(self.app.settings.rf_filter_units_with_zero_bins)
        self.assertFalse(settings.rf_filter_units_with_zero_bins_var.get())

    def test_zero_spike_unit_filter_settings_validate_current_spatial_max(self) -> None:
        settings_value = replace(
            settings_module.ViewerSettings(),
            rf_filter_units_with_zero_bins=True,
            rf_zero_bin_threshold=1,
        )
        self.app.settings = settings_value
        self.app._app_root._rfm_settings = settings_value
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, settings_window_module.SettingsWindow)
        assert isinstance(settings, settings_window_module.SettingsWindow)
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
        with self.assertRaisesRegex(settings_window_module.SettingsValidationError, r"max is 6"):
            settings._validated_settings()
        settings.rf_zero_bin_threshold_var.set("0")
        with self.assertRaisesRegex(settings_window_module.SettingsValidationError, "positive integer"):
            settings._validated_settings()

    def test_tuning_curve_session_requires_positive_int_and_reloads(self) -> None:
        settings_value = replace(
            self.app.settings,
            show_tuning_curve=True,
            auto_load_tuning_curve=True,
            tuning_curve_session=1,
        )
        self.app.settings = settings_value
        self.app._app_root._rfm_settings = settings_value
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, settings_window_module.SettingsWindow)
        assert isinstance(settings, settings_window_module.SettingsWindow)
        self.addCleanup(settings._close)

        self.assertEqual(settings.tuning_curve_session_var.get(), "1")
        self.assertTrue(settings.tuning_curve_session_entry.instate(["!disabled"]))
        settings.auto_load_tuning_curve_var.set(False)
        settings._sync_dependent_controls()
        self.assertTrue(settings.tuning_curve_session_entry.instate(["disabled"]))
        settings.auto_load_tuning_curve_var.set(True)

        for invalid in ("", "0", "-2", "1.5"):
            with self.subTest(session=invalid):
                settings.tuning_curve_session_var.set(invalid)
                with self.assertRaisesRegex(
                    settings_window_module.SettingsValidationError,
                    "Tuning Curve Session must be a positive integer",
                ):
                    settings._validated_settings()
        settings.tuning_curve_session_var.set("3")
        self.assertEqual(settings._validated_settings().tuning_curve_session, 3)
        settings._close()

        self.app.tuning_curve_data = mock.MagicMock()
        self.app._tuning_curve_candidate = Path("old-session.tc")
        self.app.data._hd_tuning = mock.MagicMock()
        self.app.data._hd_tuning_checked = True
        with mock.patch.object(self.app, "_schedule_optional_autoload") as schedule:
            self.assertTrue(
                self.app._apply_viewer_settings(
                    replace(settings_value, tuning_curve_session=3),
                    persist=False,
                    broadcast=False,
                )
            )
        schedule.assert_called_once_with()
        self.assertEqual(self.app.settings.tuning_curve_session, 3)
        self.assertIsNone(self.app.tuning_curve_data)
        self.assertIsNone(self.app._tuning_curve_candidate)
        self.assertIsNone(self.app.data._hd_tuning)

    def test_zero_spike_filter_tracks_rf_window_and_can_restore_all_units(self) -> None:
        payload = current_rf_payload({
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
            settings_module.ViewerSettings(),
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

        composer = figure_composer_module.FigureExportWindow(self.app)
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
        payload = current_rf_payload({
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
            settings_module.ViewerSettings(),
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
        with mock.patch.object(tk_support_module.messagebox, "showinfo") as showinfo:
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
                - (constants_module.INNER_BLANK_ROWS + 0.5) * float(layout["scale"])
            ),
        )
        hit = self.app._timeline_cell_at(event)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], 0)

    def _destroy_app(self) -> None:
        if self.app is not None:
            self.app.destroy()
            self.app = None
        gc.collect()

    def test_timeline_uses_and_reuses_one_raster_atlas(self) -> None:
        self.app.value_mode_var.set(constants_module.VALUE_MODE_COUNT)
        self.app._timeline_preview_cache_key = None
        self.app._draw_timeline()
        first_atlas = self.app._timeline_preview_images[-1]
        first_cache_key = self.app._timeline_preview_cache_key
        self.assertEqual(len(self.app._timeline_preview_images), 1)
        self.assertLess(len(self.app.canvases["timeline"].find_all()), 4 * self.app.data.n_bins)

        self.app._draw_timeline()
        self.assertIs(self.app._timeline_preview_images[-1], first_atlas)
        self.assertEqual(self.app._timeline_preview_cache_key, first_cache_key)

        # Keep the RF window inside this fixture's 0–30 ms axis so the
        # default zero-bin filter still leaves a unit available for drawing.
        self.app.range_start_ms_var.set("5")
        self.app.range_end_ms_var.set("15")
        self.app.selected_cell = (1, 1, 2, 2)
        self.app._draw_timeline()
        self.assertIs(self.app._timeline_preview_images[-1], first_atlas)
        self.assertEqual(self.app._timeline_preview_cache_key, first_cache_key)

        self.app.value_mode_var.set(constants_module.VALUE_MODE_RATE)
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
        composer = figure_composer_module.FigureExportWindow(self.app)
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
        self.assertIs(self.app.cell_label.master, self.app.unit_info_pane)
        self.assertGreater(
            self.app.cell_label.winfo_rootx(),
            self.app.waveform_pane.winfo_rootx(),
        )
        self.assertLessEqual(self.app.waveform_pane.winfo_height(), 200)
        self.assertFalse(self.app.tuning_curve_status_label.winfo_ismapped())
        self.assertIn("bin", self.app.cell_label.cget("text"))
        self.assertIn("cluster 7", self.app.unit_stats_label.cget("text"))
        self.assertGreater(len(self.app.canvases["waveform"].find_all()), 20)
        self.assertIn("Same x column", self.app.waveform_subtitle_label.cget("text"))
        self.assertTrue(self.app.waveform_canvas.bind("<Double-Button-1>"))

        self.app._toggle_waveform_zoom()
        self.app.update()
        self.assertTrue(self.app._waveform_zoomed)
        self.assertEqual(self.app.waveform_zoom_overlay.winfo_manager(), "place")
        self.assertTrue(self.app.waveform_zoom_canvas.winfo_ismapped())
        self.assertGreater(
            self.app.waveform_zoom_canvas.winfo_width(),
            self.app.waveform_canvas.winfo_width(),
        )
        self.assertGreater(len(self.app.waveform_zoom_canvas.find_all()), 20)

        self.app._toggle_waveform_zoom()
        self.app.update_idletasks()
        self.assertFalse(self.app._waveform_zoomed)
        self.assertEqual(self.app.waveform_zoom_overlay.winfo_manager(), "")
        self.app._open_waveform_zoom()
        self.app._handle_escape()
        self.assertFalse(self.app._waveform_zoomed)

        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, settings_window_module.SettingsWindow)
        assert isinstance(settings, settings_window_module.SettingsWindow)
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

        with mock.patch.object(figure_composer_module.FigureExportWindow, "_schedule_preview"):
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
        self.app._open_waveform_zoom()
        hidden = replace(self.app.settings, show_waveform=False)
        self.assertTrue(
            self.app._apply_viewer_settings(
                hidden,
                persist=False,
                broadcast=False,
            )
        )
        self.app.update()
        self.assertFalse(self.app._waveform_zoomed)
        self.assertEqual(self.app.waveform_zoom_overlay.winfo_manager(), "")
        self.assertFalse(self.app.waveform_pane.winfo_ismapped())
        self.app._step_unit(1)
        self.app.update()
        self.assertEqual(len(requests), request_count)

    def test_preview_daemon_worker_cooperatively_cancels_on_close(self) -> None:
        started = threading.Event()
        cancellation_observed = threading.Event()
        root = self.app._app_root
        jobs_before = export_inputs_module._active_export_jobs(root)

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
            figure_composer_module,
            "_export_executor",
            side_effect=AssertionError("preview must not use the final-export executor"),
        ):
            composer = figure_composer_module.FigureExportWindow(self.app)
            if composer._preview_after is not None:
                composer.after_cancel(composer._preview_after)
                composer._preview_after = None
            composer._freeze_context = freeze_until_cancelled
            composer._start_preview(composer._preview_generation)
            self.assertTrue(started.wait(timeout=1.0))
            preview_future = composer._preview_future
            self.assertIsNotNone(preview_future)
            self.assertEqual(export_inputs_module._active_export_jobs(root), jobs_before)

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
        future = export_inputs_module._export_executor(root).submit(release.wait, 2.0)
        export_inputs_module._register_export_job(root, self.app, future)
        self.addCleanup(export_inputs_module._unregister_export_job, root, future)

        with mock.patch.object(tk_support_module.messagebox, "showinfo") as showinfo:
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

            export_inputs_module._unregister_export_job(root, future)
            child = self.app
            child._close_window()
            self.assertFalse(child.winfo_exists())
            self.app = None

            self.assertTrue(second.winfo_exists())
            second._quit_application()

        try:
            root_exists = bool(root.winfo_exists())
        except tk_support_module.tk.TclError:
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
                tk_support_module.filedialog,
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
        payload = current_rf_payload({
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
            settings_module.ViewerSettings(),
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
            payload = current_rf_payload({
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

    def test_view_shortcuts_switch_tabs_flip_y_toggle_polar_and_cycle_palette(self) -> None:
        event = SimpleNamespace(widget=self.app.canvases["rf"])
        self.app._run_navigation_shortcut(event, self.app._select_tab, 2)
        self.assertEqual(self.app._active_tab_key(), "timeline")

        self.assertFalse(self.app.flip_y_var.get())
        self.app._run_navigation_shortcut(event, self.app._toggle_flip_y)
        self.assertTrue(self.app.flip_y_var.get())

        self.app._select_tab(0)
        self.app.polar_layout_var.set(False)
        self.app._run_navigation_shortcut(event, self.app._toggle_polar_layout)
        self.assertTrue(self.app.polar_layout_var.get())
        self.assertEqual(self.app._active_tab_key(), "rf")

        self.app.polar_layout_var.set(False)
        self.assertEqual(
            self.app.notebook.bindtags()[0],
            str(self.app.notebook),
        )
        self.assertTrue(self.app.notebook.bind("<KeyPress-p>"))
        notebook_event = SimpleNamespace(widget=self.app.notebook)
        result = self.app._run_navigation_shortcut(
            notebook_event,
            self.app._toggle_polar_layout,
        )
        self.assertEqual(result, "break")
        self.assertTrue(self.app.polar_layout_var.get())
        self.assertEqual(self.app._active_tab_key(), "rf")

        self.app.palette_var.set("Gray")
        self.app._run_navigation_shortcut(event, self.app._cycle_palette)
        self.assertEqual(self.app.palette_var.get(), "Viridis")

    def test_export_records_displayed_rate_and_units(self) -> None:
        destination = Path(self.directory.name) / "rate.csv"
        original_ask = tk_support_module.filedialog.asksaveasfilename
        original_info = tk_support_module.messagebox.showinfo
        self.addCleanup(setattr, tk_support_module.filedialog, "asksaveasfilename", original_ask)
        self.addCleanup(setattr, tk_support_module.messagebox, "showinfo", original_info)
        tk_support_module.filedialog.asksaveasfilename = lambda **_kwargs: str(destination)
        tk_support_module.messagebox.showinfo = lambda *_args, **_kwargs: None

        self.app.value_mode_var.set(constants_module.VALUE_MODE_RATE)
        expected = self.app._current_matrix()[0][0]
        self.app._export_current_matrix()

        with destination.open(newline="", encoding="utf-8") as file:
            first = next(csv.DictReader(file))
        self.assertEqual(first["value_mode"], constants_module.VALUE_MODE_RATE)
        self.assertEqual(first["value_unit"], "Hz")
        self.assertEqual(first["occupancy_time_sec_min"], "1.0")
        self.assertAlmostEqual(float(first["value"]), expected)


    def test_app_level_help_and_resolution_actions_live_in_menus(self) -> None:
        navigate_entries = {
            self.app._navigate_menu.entrycget(index, "label"): self.app._navigate_menu.entrycget(
                index, "accelerator"
            )
            for index in range(self.app._navigate_menu.index("end") + 1)
            if self.app._navigate_menu.type(index) == "command"
        }
        self.assertEqual(navigate_entries["Decrease Time Resolution"], "⇧,")
        self.assertEqual(navigate_entries["Increase Time Resolution"], "⇧.")
        self.assertNotIn("Decrease Time Resolution 1 ms", navigate_entries)
        self.assertNotIn("Increase Time Resolution 1 ms", navigate_entries)

        help_labels = [
            self.app._help_menu.entrycget(index, "label")
            for index in range(self.app._help_menu.index("end") + 1)
            if self.app._help_menu.type(index) == "command"
        ]
        self.assertEqual(self.app._help_menu.winfo_name(), "help")
        self.assertEqual(help_labels, ["Keyboard Shortcuts", "Support Documentation"])

        sidebar_text = [
            str(child.cget("text"))
            for child in self.app.sidebar_panel.winfo_children()
            if "text" in child.keys()
        ]
        self.assertFalse(any("all shortcuts" in text for text in sidebar_text))

    def test_background_startup_decode_keeps_tk_heartbeat_responsive(self) -> None:
        startup_path = Path(self.directory.name) / "slow.json"
        startup_path.write_text("{}", encoding="utf-8")
        heartbeat: list[float] = []

        def slow_decode(_path: Path) -> rf_model_module.RFMappingData:
            time.sleep(0.14)
            return self.app.data

        with mock.patch.object(gui, "RFMappingData", side_effect=slow_decode):
            viewer = gui.RFMViewer(startup_path=startup_path, master=self.app._app_root)
            self.addCleanup(viewer.destroy)
            viewer._cancel_startup_callback()
            viewer._startup_after = viewer.after_idle(
                lambda: viewer._load_startup_document(startup_path)
            )
            viewer.after(15, lambda: heartbeat.append(time.perf_counter()))
            deadline = time.perf_counter() + 2.0
            while not viewer._viewer_ready and time.perf_counter() < deadline:
                viewer.update()
                time.sleep(0.004)

        self.assertTrue(viewer._viewer_ready)
        self.assertTrue(heartbeat, "Tk callback did not run while RF data decoded")
        self.assertIsNone(viewer._startup_loading_frame)

    def test_rf_companion_stack_follows_independent_visibility_settings(self) -> None:
        self.app.notebook.select(0)
        self.app.update()
        initial_rf_width = self.app.rf_map_pane.winfo_width()
        self.assertTrue(self.app.tuning_curve_pane.winfo_ismapped())
        self.assertTrue(self.app.tuning_curve_section.winfo_ismapped())
        self.assertTrue(self.app.unit_info_pane.winfo_ismapped())
        self.assertTrue(self.app.waveform_pane.winfo_ismapped())
        self.assertEqual(int(self.app.tuning_curve_section.grid_info()["row"]), 0)
        self.assertEqual(int(self.app.unit_info_pane.grid_info()["row"]), 1)
        self.assertIs(self.app.waveform_pane.master, self.app.waveform_host)
        self.assertEqual(int(self.app.waveform_pane.grid_info()["row"]), 0)
        self.assertGreater(
            initial_rf_width,
            self.app.tuning_curve_pane.winfo_width(),
        )

        waveform_only = replace(
            self.app.settings,
            show_tuning_curve=False,
            auto_load_tuning_curve=False,
            show_waveform=True,
        )
        self.assertTrue(
            self.app._apply_viewer_settings(
                waveform_only,
                persist=False,
                broadcast=False,
            )
        )
        self.app.update()
        self.assertTrue(self.app.tuning_curve_pane.winfo_ismapped())
        self.assertFalse(self.app.tuning_curve_section.winfo_ismapped())
        self.assertTrue(self.app.unit_info_pane.winfo_ismapped())
        self.assertTrue(self.app.waveform_pane.winfo_ismapped())
        self.assertEqual(int(self.app.waveform_pane.grid_info()["row"]), 0)

        hidden = replace(waveform_only, show_waveform=False)
        self.assertTrue(
            self.app._apply_viewer_settings(
                hidden,
                persist=False,
                broadcast=False,
            )
        )
        self.app.update()
        self.assertTrue(self.app.tuning_curve_pane.winfo_ismapped())
        self.assertTrue(self.app.unit_info_pane.winfo_ismapped())
        self.assertGreater(self.app.rf_map_pane.winfo_width(), initial_rf_width)

        tuning_only = replace(hidden, show_tuning_curve=True)
        self.assertTrue(
            self.app._apply_viewer_settings(
                tuning_only,
                persist=False,
                broadcast=False,
            )
        )
        self.app.update()
        self.assertTrue(self.app.tuning_curve_pane.winfo_ismapped())
        self.assertTrue(self.app.tuning_curve_section.winfo_ismapped())
        self.assertTrue(self.app.unit_info_pane.winfo_ismapped())
        self.assertFalse(self.app.waveform_pane.winfo_ismapped())

        shown = replace(tuning_only, show_waveform=True)
        self.assertTrue(
            self.app._apply_viewer_settings(
                shown,
                persist=False,
                broadcast=False,
            )
        )
        self.app.update()
        self.assertTrue(self.app.tuning_curve_section.winfo_ismapped())
        self.assertTrue(self.app.unit_info_pane.winfo_ismapped())
        self.assertTrue(self.app.waveform_pane.winfo_ismapped())

    def test_narrow_window_keeps_tuning_and_unit_info_beside_rf(self) -> None:
        self.app.notebook.select(0)
        self.app.geometry("1120x720")
        self.app.update()
        self.assertEqual(int(self.app.tuning_curve_pane.grid_info()["row"]), 0)
        self.assertEqual(int(self.app.tuning_curve_pane.grid_info()["column"]), 1)
        self.assertEqual(int(self.app.unit_info_pane.grid_info()["row"]), 1)
        self.assertEqual(int(self.app.unit_info_pane.grid_info()["column"]), 1)
        self.assertIs(self.app.unit_info_pane.master, self.app.rf_split_container)
        self.app.selected_cell = (0, 1, 0, 1)
        self.app._update_cell_label()
        self.app.update()
        self.assertGreaterEqual(
            self.app.unit_stats_label.winfo_width(),
            self.app.unit_stats_label.winfo_reqwidth(),
        )
        self.assertIs(self.app.cell_label.master, self.app.unit_info_pane)
        self.assertGreater(
            self.app.cell_label.winfo_rootx(),
            self.app.waveform_pane.winfo_rootx(),
        )

        stacked = replace(self.app.settings, tuning_layout="Stacked")
        self.assertTrue(
            self.app._apply_viewer_settings(
                stacked,
                persist=False,
                broadcast=False,
            )
        )
        self.app.update()
        self.assertEqual(int(self.app.tuning_curve_pane.grid_info()["row"]), 1)
        self.assertEqual(int(self.app.tuning_curve_pane.grid_info()["column"]), 0)
        self.assertEqual(int(self.app.unit_info_pane.grid_info()["row"]), 1)
        self.assertEqual(int(self.app.unit_info_pane.grid_info()["column"]), 1)
        self.assertGreaterEqual(self.app.tuning_curve_canvas.winfo_height(), 180)

    def test_missing_tuning_curve_has_a_real_attach_action(self) -> None:
        self.app.notebook.select(0)
        self.app.tuning_curve_data = None
        self.app._tuning_curve_error = None
        self.app._draw_tuning_curve()

        text = "\n".join(
            self.app.tuning_curve_canvas.itemcget(item, "text")
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "text"
        )
        self.assertIn("No tuning curves", text)
        self.assertIn("Attach head-direction data", text)
        self.assertTrue(
            any(
                self.app.tuning_curve_canvas.type(item) == "window"
                for item in self.app.tuning_curve_canvas.find_all()
            )
        )
        self.assertEqual(
            self.app.tuning_attach_button.cget("text"),
            "Choose tuning_curves.tc or .json…",
        )
        self.assertFalse(self.app.tuning_curve_status_label.winfo_ismapped())
        self.assertEqual(self.app.tuning_curve_status_label.cget("text"), "")

        with mock.patch.object(self.app, "_attach_tuning_curve") as attach:
            self.app._on_tuning_curve_click(SimpleNamespace(x=20, y=20))
        attach.assert_called_once_with()

        self.app._tuning_curve_error = "invalid tuning header"
        self.app._draw_tuning_curve()
        error_text = "\n".join(
            self.app.tuning_curve_canvas.itemcget(item, "text")
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "text"
        )
        self.assertIn("Could not load tuning curves", error_text)
        self.assertIn("invalid tuning header", error_text)

    def test_loaded_tuning_curve_draws_line_and_polar_without_extending_units(self) -> None:
        tuning_path = Path(self.directory.name) / "tuning_curves.json"
        curve = tuple(float((index % 24) + 1) for index in range(constants_module.HD_RAW_BIN_COUNT))
        self.app.tuning_curve_data = companions_module.TuningCurveData(
            tuning_path,
            {7: curve, 999: curve},
        )
        self.app.tuning_smoothing_var.set(False)
        self.app.tuning_plot_mode_var.set("Line")
        self.app._draw_tuning_curve()
        line_text = "\n".join(
            self.app.tuning_curve_canvas.itemcget(item, "text")
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "text"
        )
        self.assertIn("Head direction (deg)", line_text)
        self.assertEqual(self.app.tuning_cluster_label.cget("text"), "Cluster 7")
        self.assertEqual(self.app.tuning_curve_status_label.cget("text"), "")
        self.assertFalse(self.app.tuning_curve_status_label.winfo_ismapped())

        self.app._sync_unit_combo()
        self.assertEqual(self.app._unit_combo_unit_ids, [7, 8])
        self.assertNotIn(999, self.app._unit_combo_unit_ids)

        self.app.tuning_plot_mode_var.set("Polar")
        self.app._draw_tuning_curve()
        polar_text = "\n".join(
            self.app.tuning_curve_canvas.itemcget(item, "text")
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "text"
        )
        item_types = {
            self.app.tuning_curve_canvas.type(item)
            for item in self.app.tuning_curve_canvas.find_all()
        }
        self.assertIn("0°", polar_text)
        self.assertIn("90°", polar_text)
        self.assertNotIn("polygon", item_types)
        self.assertTrue(
            any(
                self.app.tuning_curve_canvas.type(item) == "line"
                and self.app.tuning_curve_canvas.itemcget(item, "fill") == "#1570ef"
                for item in self.app.tuning_curve_canvas.find_all()
            )
        )
        self.assertTrue(any(label.endswith(" Hz") for label in polar_text.splitlines()))
        self.assertFalse(
            any(
                self.app.tuning_curve_canvas.type(item) == "oval"
                and self.app.tuning_curve_canvas.itemcget(item, "dash")
                for item in self.app.tuning_curve_canvas.find_all()
            )
        )

        self.app.tuning_plot_mode_var.set("Auto")
        self.app.polar_layout_var.set(False)
        self.assertEqual(self.app._effective_tuning_plot_mode(), "Line")
        self.app.polar_layout_var.set(True)
        self.assertEqual(self.app._effective_tuning_plot_mode(), "Polar")

    def test_hd_class_label_tracks_selected_unit_and_hides_zero(self) -> None:
        tuning_path = Path(self.directory.name) / "tuning_curves.json"
        curve = tuple(float((index % 24) + 1) for index in range(constants_module.HD_RAW_BIN_COUNT))
        self.app.tuning_curve_data = companions_module.TuningCurveData(
            tuning_path,
            {7: curve, 8: curve},
            hd_classes={7: 1, 8: 2},
        )
        self.app.tuning_smoothing_var.set(False)

        self.app._set_selected_unit_id(7)
        self.app._draw_tuning_curve()
        self.assertEqual(self.app.tuning_hd_class_label.cget("text"), "1")
        self.assertEqual(self.app.tuning_hd_class_label.cget("style"), "HDClass1.TLabel")

        self.app._set_selected_unit_id(8)
        self.app._draw_tuning_curve()
        self.assertEqual(self.app.tuning_hd_class_label.cget("text"), "2")
        self.assertEqual(self.app.tuning_hd_class_label.cget("style"), "HDClass2.TLabel")

        self.app.tuning_curve_data = companions_module.TuningCurveData(
            tuning_path,
            {7: curve, 8: curve},
            hd_classes={7: 0, 8: None},
        )
        self.app._set_selected_unit_id(7)
        self.app._draw_tuning_curve()
        self.assertEqual(self.app.tuning_hd_class_label.cget("text"), "")

        self.app._set_selected_unit_id(8)
        self.app._draw_tuning_curve()
        self.assertEqual(self.app.tuning_hd_class_label.cget("text"), "")

        self.app.tuning_curve_data = None
        self.app._draw_tuning_curve()
        self.assertEqual(self.app.tuning_hd_class_label.cget("text"), "")

    def test_tuning_provenance_info_is_visible_and_reports_ttl_timebase(self) -> None:
        tuning_path = Path(self.directory.name) / "tuning_curves.json"
        curve = tuple(2.0 for _index in range(constants_module.HD_RAW_BIN_COUNT))
        metadata = companions_module.TuningCurveMetadata(
            timebase="Open Ephys ADC seconds",
            timestamp_reference="Exposure TTL rising edge",
            angle_convention_note="0° up; positive counterclockwise",
            feature_fs_hz=119.82,
            classification=companions_module.TuningCurveClassificationProvenance(
                method="Rayleigh and circular shuffle",
                rayleigh_alpha=0.05,
                shuffle_alpha=0.01,
                num_shuffle=1000,
            ),
            ttl_qc=companions_module.TuningCurveTTLProvenance(
                ttl_pulse_count=12_345,
                median_period_s=0.008346,
                measured_rate_hz=119.82,
                camera_input_channel=2,
                camera_ttl_threshold=1.5,
                camera_ttl_active_high=True,
                motive_frame_count_raw=451_971,
                matched_motive_frame_count=451_970,
                dropped_motive_frame_ids=(451_970,),
                frame_alignment_policy_requested="drop_unmatched_last_frame",
                frame_alignment_policy_applied="drop_unmatched_last_frame",
                frame_timestamp_mapping="one_gated_exposure_pulse_center_per_matched_motive_frame",
            ),
        )
        self.app.tuning_curve_data = companions_module.TuningCurveData(
            tuning_path,
            {7: curve},
            metadata=metadata,
        )
        self.app.notebook.select(0)
        self.app._set_selected_unit_id(7)
        self.app._draw_tuning_curve()
        self.app.update()

        self.assertTrue(self.app.tuning_provenance_button.winfo_ismapped())
        with mock.patch.object(tk_support_module.messagebox, "showinfo") as show_info:
            self.app._show_tuning_provenance()
        show_info.assert_called_once()
        title, detail = show_info.call_args.args
        self.assertEqual(title, "Tuning Provenance")
        self.assertIn("Exposure TTL rising edge", detail)
        self.assertIn("0° up; positive counterclockwise", detail)
        self.assertIn("Motive trigger TTLs", detail)
        self.assertIn("12345", detail)
        self.assertIn("451970 / 451971", detail)
        self.assertIn("drop_unmatched_last_frame", detail)
        self.assertIn("451970", detail)

        self.app.tuning_curve_data = companions_module.TuningCurveData(tuning_path, {7: curve})
        self.app._draw_tuning_curve()
        self.app.update()
        self.assertTrue(self.app.tuning_provenance_button.winfo_ismapped())
        with mock.patch.object(tk_support_module.messagebox, "showinfo") as show_info:
            self.app._show_tuning_provenance()
        show_info.assert_called_once()
        _title, detail = show_info.call_args.args
        self.assertIn("Legacy", detail)
        self.assertIn("Timing / occupancy", detail)
        self.assertIn("Not recorded", detail)

        missing_curve = (float("nan"),) * 6 + curve[6:]
        self.app.tuning_curve_data = companions_module.TuningCurveData(
            tuning_path, {7: missing_curve}
        )
        self.app.tuning_smoothing_var.set(False)
        self.app._draw_tuning_curve()
        with mock.patch.object(tk_support_module.messagebox, "showinfo") as show_info:
            self.app._show_tuning_provenance()
        _title, detail = show_info.call_args.args
        self.assertIn("Bins without occupancy", detail)

    def test_tuning_polar_uses_one_outline_and_an_explicit_hz_axis(self) -> None:
        rates = tuple(float(index + 1) for index in range(30))
        angles = tuple(index * 12.0 for index in range(30))

        self.app.tuning_curve_canvas.delete("all")
        self.app._draw_tuning_polar(angles, rates, 7, max(rates))

        item_types = [
            self.app.tuning_curve_canvas.type(item)
            for item in self.app.tuning_curve_canvas.find_all()
        ]
        labels = {
            self.app.tuning_curve_canvas.itemcget(item, "text")
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "text"
        }
        self.assertEqual(item_types.count("oval"), 1)
        self.assertIn("0 Hz", labels)
        self.assertIn(f"{max(rates):.3g} Hz", labels)

    def test_tuning_line_axis_starts_at_zero_and_ends_at_displayed_peak(self) -> None:
        self.app.tuning_curve_canvas.delete("all")
        self.app._draw_tuning_line((0.0, 180.0, 360.0), (8.0, 12.0, 10.0), 7)
        labels = [
            self.app.tuning_curve_canvas.itemcget(item, "text")
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "text"
        ]
        self.assertIn("0", labels)
        self.assertIn("6", labels)
        self.assertIn("12", labels)
        self.assertNotIn("8", labels)
        zero_tick = next(
            item
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "text"
            and self.app.tuning_curve_canvas.itemcget(item, "text") == "0"
        )
        left = 54.0
        right = max(self.app.tuning_curve_canvas.winfo_width(), 280) - 16.0
        self.assertAlmostEqual(
            self.app.tuning_curve_canvas.coords(zero_tick)[0],
            (left + right) / 2.0,
        )
        bottom = max(self.app.tuning_curve_canvas.winfo_height(), 220) - 44.0
        direction_labels = sorted(
            (
                self.app.tuning_curve_canvas.coords(item)[0],
                self.app.tuning_curve_canvas.itemcget(item, "text"),
            )
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "text"
            and self.app.tuning_curve_canvas.coords(item)[1] == bottom + 16.0
        )
        self.assertEqual(
            [label for _x, label in direction_labels],
            ["180", "90", "0", "270", "180"],
        )

    def test_compare_scale_uses_one_processed_peak_for_line_and_polar(self) -> None:
        tuning_path = Path(self.directory.name) / "tuning_curves.json"
        self.app.tuning_curve_data = companions_module.TuningCurveData(
            tuning_path,
            {
                7: (10.0,) * constants_module.HD_RAW_BIN_COUNT,
                8: (20.0,) * constants_module.HD_RAW_BIN_COUNT,
            },
        )
        self.app._set_selected_unit_id(7)
        self.app.tuning_smoothing_var.set(False)
        self.app.tuning_plot_mode_var.set("Line")

        self.app.tuning_compare_scale_var.set(False)
        self.app._draw_tuning_curve()
        per_cell_labels = {
            self.app.tuning_curve_canvas.itemcget(item, "text")
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "text"
        }
        self.assertIn("10", per_cell_labels)
        self.assertNotIn("20", per_cell_labels)

        self.app.tuning_compare_scale_var.set(True)
        self.app._draw_tuning_curve()
        shared_labels = {
            self.app.tuning_curve_canvas.itemcget(item, "text")
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "text"
        }
        self.assertIn("20", shared_labels)
        self.assertEqual(self.app.tuning_curve_status_label.cget("text"), "")
        self.assertFalse(self.app.tuning_curve_status_label.winfo_ismapped())

        self.app.tuning_plot_mode_var.set("Polar")
        self.app._draw_tuning_curve()
        polar_labels = {
            self.app.tuning_curve_canvas.itemcget(item, "text")
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "text"
        }
        self.assertIn("20 Hz", polar_labels)
        self.assertNotIn("polygon", {
            self.app.tuning_curve_canvas.type(item)
            for item in self.app.tuning_curve_canvas.find_all()
        })

    def test_one_and_two_bin_tuning_curves_remain_visible(self) -> None:
        tuning_path = Path(self.directory.name) / "tuning_curves.json"
        curve = tuple(float(index + 1) for index in range(constants_module.HD_RAW_BIN_COUNT))
        self.app.tuning_curve_data = companions_module.TuningCurveData(tuning_path, {7: curve})
        self.app.tuning_smoothing_var.set(False)

        self.app.tuning_display_bins_var.set(1)
        self.app.tuning_plot_mode_var.set("Line")
        self.app._draw_tuning_curve()
        line_markers = [
            item
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "oval"
            and self.app.tuning_curve_canvas.itemcget(item, "fill") == "#1570ef"
        ]
        self.assertEqual(len(line_markers), 1)

        self.app.tuning_display_bins_var.set(2)
        self.app.tuning_plot_mode_var.set("Polar")
        self.app._draw_tuning_curve()
        polar_markers = [
            item
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "oval"
            and self.app.tuning_curve_canvas.itemcget(item, "fill") == "#1570ef"
        ]
        self.assertEqual(len(polar_markers), 2)
        self.assertFalse(
            any(
                self.app.tuning_curve_canvas.type(item) == "line"
                and self.app.tuning_curve_canvas.itemcget(item, "fill") == "#1570ef"
                for item in self.app.tuning_curve_canvas.find_all()
            )
        )

        self.app.tuning_curve_data = companions_module.TuningCurveData(
            tuning_path,
            {7: (0.0,) * constants_module.HD_RAW_BIN_COUNT},
        )
        self.app.tuning_display_bins_var.set(30)
        self.app._draw_tuning_curve()
        zero_markers = [
            item
            for item in self.app.tuning_curve_canvas.find_all()
            if self.app.tuning_curve_canvas.type(item) == "oval"
            and self.app.tuning_curve_canvas.itemcget(item, "fill") == "#1570ef"
        ]
        self.assertEqual(len(zero_markers), 1)

    def test_settings_tabs_and_dependent_controls(self) -> None:
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, settings_window_module.SettingsWindow)
        self.addCleanup(settings.destroy)
        self.assertEqual(
            [settings.notebook.tab(tab, "text") for tab in settings.notebook.tabs()],
            ["General", "RF Map", "Waveform", "Tuning Curve"],
        )

        settings.show_tuning_curve_var.set(False)
        settings.show_waveform_var.set(False)
        settings.show_probe_layout_var.set(False)
        settings.tuning_smoothing_var.set(False)
        settings.update_idletasks()
        self.assertIn("disabled", settings.auto_tuning_check.state())
        self.assertIn("disabled", settings.waveform_channel_mode_combo.state())
        self.assertIn("disabled", settings.auto_probe_check.state())
        self.assertIn("disabled", settings.tuning_sigma_entry.state())

        settings.show_tuning_curve_var.set(True)
        settings.show_waveform_var.set(True)
        settings.show_probe_layout_var.set(True)
        settings.tuning_smoothing_var.set(True)
        settings.update_idletasks()
        self.assertNotIn("disabled", settings.auto_tuning_check.state())
        self.assertNotIn("disabled", settings.waveform_channel_mode_combo.state())
        self.assertNotIn("disabled", settings.auto_probe_check.state())
        self.assertNotIn("disabled", settings.tuning_sigma_entry.state())

        pending = list(settings.winfo_children())
        button_labels = []
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, tk_support_module.ttk.Button):
                button_labels.append(widget.cget("text"))
        self.assertIn("Save", button_labels)
        self.assertIn("Cancel", button_labels)
        self.assertNotIn("Apply", button_labels)
        self.assertNotIn("Restore Defaults", button_labels)

    def test_applying_settings_updates_the_active_window(self) -> None:
        self.app._app_root._rfm_active_viewer = self.app
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, settings_window_module.SettingsWindow)
        self.addCleanup(settings.destroy)

        settings.rf_sum_start_var.set("4")
        settings.rf_sum_end_var.set("24")
        settings.rf_time_resolution_var.set("4")
        settings.rf_layout_var.set("Polar")
        settings.tuning_plot_mode_var.set("Line")
        settings.tuning_layout_var.set("Stacked")
        settings.tuning_display_bins_var.set("8")
        settings.tuning_smoothing_var.set(False)
        settings.tuning_smooth_sigma_var.set("24")
        settings.tuning_compare_scale_var.set(True)
        settings._commit(close=False)

        self.assertEqual(settings.error_var.get(), "")
        self.assertEqual(settings.tuning_display_bins_var.get(), "6")
        self.assertEqual(self.app.range_start_ms_var.get(), "4")
        self.assertEqual(self.app.range_end_ms_var.get(), "24")
        self.assertEqual(self.app.time_res_ms_var.get(), "4")
        self.assertTrue(self.app.polar_layout_var.get())
        self.assertEqual(self.app.tuning_plot_mode_var.get(), "Line")
        self.assertEqual(self.app.tuning_layout_var.get(), "Stacked")
        self.assertEqual(int(self.app.tuning_curve_pane.grid_info()["row"]), 1)
        self.assertEqual(int(self.app.tuning_curve_pane.grid_info()["column"]), 0)
        self.assertEqual(self.app.tuning_display_bins_var.get(), 6)
        self.assertFalse(self.app.tuning_smoothing_var.get())
        self.assertEqual(self.app.tuning_smooth_sigma_var.get(), 2.0)
        self.assertTrue(self.app.tuning_compare_scale_var.get())
        saved = json.loads(
            self.app._app_root._rfm_settings_path.read_text(encoding="utf-8")
        )
        self.assertEqual(saved["tuning_display_bins"], 6)
        self.assertEqual(saved["tuning_layout"], "Stacked")
        self.assertTrue(saved["tuning_compare_scale"])
        self.assertTrue(saved["rf_polar_layout"])

    def test_probe_and_tuning_views_fold_and_restore(self) -> None:
        self.app.notebook.select(0)
        self.app.update()
        initial_split_width = self.app.rf_split_container.winfo_width()

        self.app._toggle_probe_collapsed()
        self.app.update_idletasks()
        self.assertGreater(
            self.app.rf_split_container.winfo_width(), initial_split_width
        )
        expanded_rf_width = self.app.rf_map_pane.winfo_width()
        self.app._toggle_tuning_collapsed()
        self.app.update_idletasks()
        self.assertFalse(self.app.probe_canvas.winfo_ismapped())
        self.assertFalse(self.app.tuning_curve_canvas.winfo_ismapped())
        self.assertFalse(self.app.sidebar_panel.winfo_ismapped())
        self.assertTrue(self.app.sidebar_collapsed_rail.winfo_ismapped())
        self.assertTrue(self.app.tuning_curve_pane.winfo_ismapped())
        self.assertTrue(self.app.unit_info_pane.winfo_ismapped())
        self.assertTrue(self.app.tuning_collapsed_rail.winfo_ismapped())
        self.assertFalse(self.app.waveform_pane.winfo_ismapped())
        self.assertGreater(self.app.rf_map_pane.winfo_width(), expanded_rf_width)

        self.app._toggle_probe_collapsed()
        self.app._toggle_tuning_collapsed()
        self.app.update_idletasks()
        self.assertTrue(self.app.probe_canvas.winfo_ismapped())
        self.assertTrue(self.app.tuning_curve_canvas.winfo_ismapped())

    def test_rf_navigation_uses_cheap_best_cell_path(self) -> None:
        self.app.selected_cell = None
        with mock.patch.object(
            self.app.data,
            "metrics",
            side_effect=AssertionError("full metrics should remain lazy"),
        ):
            self.app._update_cell_label()
        self.assertIsNotNone(self.app.selected_cell)

    def test_probe_static_geometry_is_reused_across_unit_steps(self) -> None:
        base = Path(self.directory.name)
        self.app.probe_geometry = companions_module.ProbeGeometry(
            probe_name="ProbeA",
            positions_path=base / "positions.csv",
            channels_path=base / "channels.csv",
            units=(
                companions_module.ProbeUnitPosition(7, 0.0, 0.0),
                companions_module.ProbeUnitPosition(8, 10.0, 100.0),
            ),
            channels=(
                companions_module.ProbeChannel(0, 0.0, 0.0, 0),
                companions_module.ProbeChannel(1, 10.0, 100.0, 0),
            ),
        )
        self.app._probe_static_signature = None
        self.app._draw_probe_canvas()
        static_before = tuple(self.app.probe_canvas.find_withtag("probe-static"))

        self.app._set_selected_unit_id(8)
        self.app._draw_probe_canvas()
        static_after = tuple(self.app.probe_canvas.find_withtag("probe-static"))
        self.assertEqual(static_after, static_before)

    def test_nan_probe_selection_overlay_and_spatial_filter_parity(self) -> None:
        base = Path(self.directory.name)
        self.app.probe_geometry = companions_module.ProbeGeometry(
            probe_name="ProbeA",
            positions_path=base / "positions.csv",
            channels_path=base / "channels.csv",
            units=(
                companions_module.ProbeUnitPosition(7, 0.0, 0.0),
                companions_module.ProbeUnitPosition(8, None, None),
            ),
            channels=(companions_module.ProbeChannel(0, 0.0, 0.0, 0),),
        )
        self.app._set_selected_unit_id(8)
        self.app._probe_static_signature = None
        self.app._draw_probe_canvas()

        selection = self.app.probe_canvas.find_withtag("probe-selection")
        self.assertEqual(len(selection), 1)
        self.assertEqual(self.app.probe_canvas.type(selection[0]), "text")
        self.assertEqual(self.app.probe_canvas.itemcget(selection[0], "text"), "NaN")
        self.assertEqual(self.app._unit_navigation_ids(), [7, 8])

        self.app._apply_spatial_region(
            companions_module.SpatialRegion.from_corners(-10, -10, 10, 10)
        )
        self.assertEqual(self.app._unit_navigation_ids(), [7])
        self.assertEqual(self.app._selected_unit_id_value(), 7)

        self.app._clear_spatial_filter()
        self.assertEqual(self.app._unit_navigation_ids(), [7, 8])

        self.app._set_selected_unit_id(8)
        self.app._apply_spatial_region(
            companions_module.SpatialRegion.from_corners(100, 100, 110, 110)
        )
        self.assertEqual(self.app._unit_navigation_ids(), [])
        self.assertEqual(self.app.probe_canvas.find_withtag("probe-selection"), ())

        self.app._clear_spatial_filter()
        self.assertEqual(self.app._unit_navigation_ids(), [7, 8])
        self.assertEqual(self.app._selected_unit_id_value(), 8)
        selection = self.app.probe_canvas.find_withtag("probe-selection")
        self.assertEqual(len(selection), 1)
        self.assertEqual(self.app.probe_canvas.itemcget(selection[0], "text"), "NaN")

    def test_missing_only_probe_geometry_still_draws_nan_selection(self) -> None:
        base = Path(self.directory.name)
        self.app.probe_geometry = companions_module.ProbeGeometry(
            probe_name="ProbeA",
            positions_path=base / "positions.csv",
            channels_path=None,
            units=(companions_module.ProbeUnitPosition(8, None, None),),
            channels=(),
        )
        self.app._set_selected_unit_id(8)
        self.app._probe_static_signature = None

        self.app._draw_probe_canvas()

        selection = self.app.probe_canvas.find_withtag("probe-selection")
        self.assertEqual(len(selection), 1)
        self.assertEqual(self.app.probe_canvas.itemcget(selection[0], "text"), "NaN")
        self.assertIn("0/1 units positioned", self.app.spatial_status_label.cget("text"))

    def test_optional_discovery_starts_off_the_tk_thread(self) -> None:
        probe_finished = threading.Event()
        worker_names = []

        def slow_probe(_path: Path) -> None:
            worker_names.append(threading.current_thread().name)
            time.sleep(0.12)
            probe_finished.set()
            return None

        self.app._optional_autoload_generation += 1
        generation = self.app._optional_autoload_generation
        with (
            mock.patch.object(gui, "discover_probe_geometry", side_effect=slow_probe),
            mock.patch.object(gui, "discover_tuning_curve_path", return_value=None),
        ):
            started = time.perf_counter()
            self.app._autoload_optional_resources_deferred(generation)
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.05)
            self.assertTrue(probe_finished.wait(1.0))

        self.assertTrue(worker_names)
        self.assertNotEqual(worker_names[0], threading.current_thread().name)

    def test_settings_newly_enabled_autoload_is_scheduled_off_tk(self) -> None:
        hidden = replace(
            self.app.settings,
            show_probe_layout=False,
            auto_load_probe_layout=False,
            show_tuning_curve=False,
            auto_load_tuning_curve=False,
        )
        self.assertTrue(
            self.app._apply_viewer_settings(
                hidden,
                persist=False,
                broadcast=False,
            )
        )
        enabled = replace(
            hidden,
            show_probe_layout=True,
            auto_load_probe_layout=True,
            show_tuning_curve=True,
            auto_load_tuning_curve=True,
        )

        with (
            mock.patch.object(
                gui,
                "discover_probe_geometry",
                side_effect=AssertionError("must not run on Tk"),
            ),
            mock.patch.object(
                gui,
                "discover_tuning_curve_path",
                side_effect=AssertionError("must not run on Tk"),
            ),
            mock.patch.object(self.app, "_schedule_optional_autoload") as schedule,
        ):
            self.assertTrue(
                self.app._apply_viewer_settings(
                    enabled,
                    persist=False,
                    broadcast=False,
                )
            )

        schedule.assert_called_once_with()

    def test_optional_worker_always_enqueues_a_terminal_result(self) -> None:
        self.app._optional_result_queue = gui.queue.SimpleQueue()
        snapshot = {
            "generation": 99,
            "data_path": self.app.data.path,
            "load_probe": True,
            "load_tuning": True,
            "cluster_id": 7,
            "tuning_bins": 30,
            "tuning_smoothing": True,
            "tuning_sigma": 1.5,
        }

        with mock.patch.object(
            gui,
            "discover_probe_geometry",
            side_effect=RuntimeError("unexpected discovery failure"),
        ):
            self.app._optional_autoload_worker(snapshot)

        result = self.app._optional_result_queue.get_nowait()
        self.assertEqual(result["generation"], 99)
        self.assertIn("unexpected discovery failure", str(result["worker_error"]))

    def test_missing_tuning_result_collapses_only_tuning_curve(self) -> None:
        self.app.notebook.select(0)
        self.app.show_tuning_curve_var.set(True)
        self.app.show_waveform_var.set(True)
        self.app.tuning_collapsed_var.set(False)
        generation = self.app._optional_autoload_generation
        self.app._optional_result_queue.put(
            {
                "generation": generation,
                "data_path": self.app.data.path,
                "probe_geometry": None,
                "tuning_path": None,
                "tuning_data": None,
                "tuning_error": None,
            }
        )

        self.app._poll_optional_results()
        self.app.update_idletasks()

        self.assertTrue(self.app.tuning_collapsed_var.get())
        self.assertTrue(self.app.tuning_curve_pane.winfo_ismapped())
        self.assertFalse(self.app.tuning_curve_section.winfo_ismapped())
        self.assertTrue(self.app.tuning_collapsed_rail.winfo_ismapped())
        self.assertTrue(self.app.unit_info_pane.winfo_ismapped())
        self.assertTrue(self.app.waveform_pane.winfo_ismapped())

    def test_settings_validation_selects_and_marks_the_owning_tab(self) -> None:
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, settings_window_module.SettingsWindow)
        self.addCleanup(settings.destroy)
        settings.notebook.select(settings._tab_widget_by_name["General"])
        settings.rf_sum_start_var.set("10")
        settings.rf_sum_end_var.set("10")

        settings._commit(close=False)

        selected = settings._tab_name_by_widget[str(settings.notebook.select())]
        self.assertEqual(selected, "RF Map")
        self.assertIn("start before end", settings._tab_error_vars["RF Map"].get())
        self.assertEqual(
            settings.notebook.tab(settings._tab_widget_by_name["RF Map"], "text"),
            "RF Map •",
        )
        self.assertEqual(settings.error_var.get(), "")

        settings.rf_sum_start_var.set("0")
        settings.rf_sum_end_var.set("20")
        settings._commit(close=False)
        self.assertEqual(settings._tab_error_vars["RF Map"].get(), "")
        self.assertEqual(
            settings.notebook.tab(settings._tab_widget_by_name["RF Map"], "text"),
            "RF Map",
        )

    def test_disabled_invalid_tuning_sigma_keeps_last_valid_value(self) -> None:
        expected_sigma = self.app._app_root._rfm_settings.tuning_smooth_sigma
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, settings_window_module.SettingsWindow)
        self.addCleanup(settings.destroy)
        settings.tuning_smoothing_var.set(False)
        settings.tuning_smooth_sigma_var.set("not-a-number")

        settings._commit(close=False)

        self.assertEqual(settings.error_var.get(), "")
        self.assertEqual(settings._tab_error_vars["Tuning Curve"].get(), "")
        self.assertEqual(
            float(settings.tuning_smooth_sigma_var.get()),
            expected_sigma * 360.0 / constants_module.DEFAULT_HD_DISPLAY_BINS,
        )
        self.assertFalse(self.app.tuning_smoothing_var.get())
        self.assertEqual(self.app.tuning_smooth_sigma_var.get(), expected_sigma)

        settings.tuning_smoothing_var.set(True)
        settings.tuning_smooth_sigma_var.set("still-not-a-number")
        settings._commit(close=False)
        selected = settings._tab_name_by_widget[str(settings.notebook.select())]
        self.assertEqual(selected, "Tuning Curve")
        self.assertIn(
            "must be a number",
            settings._tab_error_vars["Tuning Curve"].get(),
        )

    def test_settings_apply_preserves_viewer_tab_and_suppresses_trace_publish(self) -> None:
        self.app.notebook.select(2)
        palette = next(
            value for value in constants_module.PALETTES if value != self.app.palette_var.get()
        )
        polar_radius = next(
            value
            for value in constants_module.POLAR_RADIUS_MODES
            if value != self.app.polar_radius_var.get()
        )
        updated = replace(
            self.app.settings,
            rf_palette=palette,
            rf_polar_radius=polar_radius,
            default_viewer_tab="rf",
        )

        with mock.patch.object(
            self.app,
            "_publish_pairing_state_if_changed",
        ) as publish:
            applied = self.app._apply_viewer_settings(
                updated,
                persist=False,
                broadcast=False,
            )

        self.assertTrue(applied)
        self.assertEqual(self.app._active_tab_key(), "timeline")
        self.assertFalse(self.app._pair_apply_in_progress)
        publish.assert_not_called()

        def fail_during_redraw() -> None:
            self.assertTrue(self.app._pair_apply_in_progress)
            raise RuntimeError("redraw failed")

        with mock.patch.object(self.app, "_update_all", side_effect=fail_during_redraw):
            with self.assertRaisesRegex(RuntimeError, "redraw failed"):
                self.app._apply_viewer_settings(
                    updated,
                    persist=False,
                    broadcast=False,
                )
        self.assertFalse(self.app._pair_apply_in_progress)

    def test_settings_commit_renders_each_visible_optional_view_once(self) -> None:
        self.app.notebook.select(0)
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, settings_window_module.SettingsWindow)
        self.addCleanup(settings.destroy)

        with (
            mock.patch.object(
                self.app,
                "_draw_probe_canvas",
                wraps=self.app._draw_probe_canvas,
            ) as draw_probe,
            mock.patch.object(
                self.app,
                "_draw_tuning_curve",
                wraps=self.app._draw_tuning_curve,
            ) as draw_tuning,
            mock.patch.object(
                self.app,
                "_draw_waveform",
                wraps=self.app._draw_waveform,
            ) as draw_waveform,
        ):
            settings._commit(close=False)

        self.assertEqual(draw_probe.call_count, 1)
        self.assertEqual(draw_tuning.call_count, 1)
        self.assertEqual(draw_waveform.call_count, 1)

    def test_applying_settings_propagates_only_to_paired_windows(self) -> None:
        paired = self.app._open_json_window(self.app.data.path)
        self.assertIsNotNone(paired)
        assert paired is not None
        self.addCleanup(paired.destroy)
        self.app.pair_windows_var.set(True)
        self.app._on_pair_windows_toggled()
        self.app._app_root._rfm_active_viewer = self.app
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, settings_window_module.SettingsWindow)
        self.addCleanup(settings.destroy)

        settings.rf_flip_y_var.set(True)
        settings.rf_palette_var.set("Viridis")
        settings.tuning_plot_mode_var.set("Polar")
        settings.tuning_display_bins_var.set("12")
        settings.tuning_compare_scale_var.set(True)
        settings.show_tuning_curve_var.set(False)
        settings.show_waveform_var.set(False)
        settings._commit(close=False)

        self.assertEqual(settings.error_var.get(), "")
        for viewer in (self.app, paired):
            self.assertTrue(viewer.flip_y_var.get())
            self.assertEqual(viewer.palette_var.get(), "Viridis")
            self.assertEqual(viewer.tuning_plot_mode_var.get(), "Polar")
            self.assertEqual(viewer.tuning_display_bins_var.get(), 12)
            self.assertTrue(viewer.tuning_compare_scale_var.get())
            self.assertFalse(viewer.show_tuning_curve_var.get())
            self.assertFalse(viewer.show_waveform_var.get())
            self.assertEqual(viewer.tuning_curve_pane.winfo_manager(), "grid")
            self.assertEqual(viewer.tuning_curve_section.winfo_manager(), "")
            self.assertEqual(viewer.unit_info_pane.winfo_manager(), "grid")

    def test_reused_settings_window_follows_the_active_viewer(self) -> None:
        second = self.app._open_json_window(self.app.data.path)
        self.assertIsNotNone(second)
        assert second is not None
        self.addCleanup(second.destroy)
        self.app._app_root._rfm_active_viewer = self.app
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, settings_window_module.SettingsWindow)
        self.addCleanup(settings.destroy)

        self.app._app_root._rfm_active_viewer = second
        second._show_settings()

        self.assertIs(settings.owner, second)
        self.assertEqual(settings.transient(), str(second))

    def test_optional_file_drop_returns_copy_only_after_successful_load(self) -> None:
        event = SimpleNamespace(data=str(Path(self.directory.name) / "tuning_curves.json"))
        with mock.patch.object(
            self.app,
            "_load_tuning_curve_path",
            return_value=True,
        ) as loader:
            result = self.app._on_optional_file_drop("tuning", event)
        self.assertEqual(result, self.app._dnd_copy_action)
        loader.assert_called_once()

        with mock.patch.object(
            self.app,
            "_load_tuning_curve_path",
            return_value=False,
        ):
            result = self.app._on_optional_file_drop("tuning", event)
        self.assertEqual(result, self.app._dnd_refuse_action)

        self.app.show_tuning_curve_var.set(False)
        with mock.patch.object(self.app, "_load_tuning_curve_path") as hidden_loader:
            result = self.app._on_optional_file_drop("tuning", event)
        self.assertEqual(result, self.app._dnd_refuse_action)
        hidden_loader.assert_not_called()
        self.assertEqual(
            self.app._on_optional_file_drop("unknown", event),
            self.app._dnd_refuse_action,
        )

    def test_missing_probe_click_opens_the_positions_picker(self) -> None:
        self.app.show_probe_layout_var.set(True)
        self.app.probe_geometry = None
        self.app._draw_probe_canvas()
        event = SimpleNamespace(x=40, y=40)

        with mock.patch.object(
            tk_support_module.filedialog,
            "askopenfilename",
            return_value="",
        ) as picker:
            self.app._on_probe_press(event)
            self.app._on_probe_release(event)

        picker.assert_called_once()
        self.assertEqual(picker.call_args.kwargs["title"], "Attach probe positions")

    def test_timeline_draws_separate_red_and_blue_y_axes(self) -> None:
        self.app.selected_cell = (0, 0, 0, 0)
        self.app._draw_timeline()
        canvas = self.app.canvases["timeline"]
        layout = self.app._canvas_layouts["timeline"]
        chart_x = float(layout["chart_x"])
        chart_y = float(layout["chart_y"])
        chart_w = float(layout["chart_w"])
        chart_h = float(layout["chart_h"])

        def has_vertical_axis(x: float, fill: str) -> bool:
            expected = [x, chart_y, x, chart_y + chart_h]
            return any(
                canvas.type(item) == "line"
                and canvas.itemcget(item, "fill") == fill
                and canvas.coords(item) == expected
                for item in canvas.find_all()
            )

        self.assertTrue(has_vertical_axis(chart_x - 20, "#dc2626"))
        self.assertTrue(has_vertical_axis(chart_x + chart_w + 20, "#2563eb"))
        labels = [
            canvas.itemcget(item, "text")
            for item in canvas.find_all()
            if canvas.type(item) == "text"
        ]
        self.assertIn("Selected cell", labels)
        self.assertNotIn("Selected cell · same y", labels)

    def test_destroy_cancels_pending_resize_callback(self) -> None:
        self.app._schedule_redraw()
        self.assertIsNotNone(self.app._redraw_after)
        self.app.destroy()
        self.assertIsNone(self.app._redraw_after)
        self.app = None

    def test_discovered_json_menu_always_opens_a_new_window(self) -> None:
        labels = [
            self.app._discovered_json_menu.entrycget(index, "label")
            for index in range(self.app._discovered_json_menu.index("end") + 1)
        ]
        selected_index = next(index for index, label in enumerate(labels) if "viewer.json" in label)
        with mock.patch.object(self.app, "_open_json_window") as opener:
            self.app._discovered_json_menu.invoke(selected_index)
        opener.assert_called_once_with(self.app.data.path.resolve())

    def test_display_controls_are_main_area_and_collapsible(self) -> None:
        self.assertEqual(self.app.display_controls_frame.winfo_manager(), "")
        self.assertFalse(self.app.display_expanded_var.get())
        self.assertIs(self.app.range_start_spin.master, self.app.range_controls_frame)
        self.assertIs(self.app.range_end_spin.master, self.app.range_controls_frame)
        self.assertIs(self.app.reset_plot_range_button.master, self.app.range_controls_frame)
        self.assertIs(self.app.display_toggle_button.master, self.app.plot_controls_frame)
        self.assertGreater(
            int(self.app.reset_plot_range_button.grid_info()["column"]),
            int(self.app.range_end_spin.grid_info()["column"]),
        )
        self.assertEqual(int(self.app.display_toggle_button.grid_info()["column"]), 8)
        self.app._toggle_display_controls()
        self.app.update_idletasks()
        self.assertEqual(self.app.display_controls_frame.winfo_manager(), "grid")
        self.assertTrue(self.app.display_expanded_var.get())
        self.assertEqual(int(self.app.display_controls_frame.grid_info()["row"]), 1)
        self.assertIs(self.app.display_controls_frame.master.master, self.app.nametowidget(self.app.notebook.winfo_parent()))
        self.app._toggle_display_controls()
        self.assertEqual(self.app.display_controls_frame.winfo_manager(), "")

    def test_spike_time_and_unit_info_inspectors_are_visible_and_update(self) -> None:
        self.app.notebook.select(0)
        self.app._update_all()
        self.app.update_idletasks()

        self.assertTrue(self.app.cell_label.winfo_ismapped())
        self.assertIn("bin", self.app.cell_label.cget("text"))
        self.assertTrue(self.app.unit_stats_label.winfo_ismapped())
        self.assertIn("cluster", self.app.unit_stats_label.cget("text"))
        self.assertIs(self.app.unit_stats_label.master, self.app.unit_info_pane)
        self.assertIs(self.app.cell_label.master, self.app.unit_info_pane)
        self.assertEqual(int(self.app.cell_label.grid_info()["row"]), 5)
        self.app.selected_cell = (0, 0, 0, 0)
        self.app._update_cell_label()
        self.assertIn("xIdx 1", self.app.unit_stats_label.cget("text"))

    def test_spatial_region_filters_navigation_and_handles_no_matches(self) -> None:
        positions_path = Path(self.directory.name) / "positions.csv"
        self.app.probe_geometry = companions_module.ProbeGeometry(
            probe_name="ProbeA",
            positions_path=positions_path,
            channels_path=None,
            units=(
                companions_module.ProbeUnitPosition(7, 0.0, 0.0),
                companions_module.ProbeUnitPosition(8, 300.0, 300.0),
            ),
            channels=(),
        )
        self.app._set_selected_unit_id(8)

        self.app._apply_spatial_region(companions_module.SpatialRegion.from_corners(-10, -10, 10, 10))
        self.assertEqual(self.app._unit_combo_unit_ids, [7])
        self.assertEqual(self.app._selected_unit_id_value(), 7)

        self.app._apply_spatial_region(companions_module.SpatialRegion.from_corners(100, 100, 110, 110))
        self.assertEqual(self.app._unit_combo_unit_ids, [])
        self.assertEqual(self.app.unit_idx.get(), -1)
        self.assertIn("No units match", self.app.status_label.cget("text"))

        self.app._handle_escape()
        self.assertIsNone(self.app.spatial_region)
        self.assertEqual(self.app._unit_combo_unit_ids, [7, 8])

    def test_paired_unit_outside_local_region_clears_spatial_filter(self) -> None:
        self.app.probe_geometry = companions_module.ProbeGeometry(
            probe_name="ProbeA",
            positions_path=Path(self.directory.name) / "positions.csv",
            channels_path=None,
            units=(
                companions_module.ProbeUnitPosition(7, 0.0, 0.0),
                companions_module.ProbeUnitPosition(8, 300.0, 300.0),
            ),
            channels=(),
        )
        self.app._apply_spatial_region(companions_module.SpatialRegion.from_corners(-10, -10, 10, 10))
        self.assertEqual(self.app._unit_combo_unit_ids, [7])

        incoming = replace(self.app._capture_pairing_state(), unit_id=8)
        self.app._apply_pairing_state(incoming, frozenset({"unit"}))

        self.assertIsNone(self.app.spatial_region)
        self.assertEqual(self.app._selected_unit_id_value(), 8)
        self.assertEqual(self.app._unit_combo_unit_ids, [7, 8])


if __name__ == "__main__":
    unittest.main()
