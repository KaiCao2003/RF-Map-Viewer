"""Full Python/Tk interaction regression contract from ``python-v1.8.2``.

The module is an additive pytest/unittest suite.  It runs under Xvfb on the
remote Linux validation host and becomes a macOS/Tk release gate once the full
1.8 interaction surface has been forward-ported into 1.9.1.
"""

import csv
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


def current_rf_payload(payload: dict) -> dict:
    n_y, n_x = payload["unitsSpikeCountsSize"][1:3]
    payload.update(
        responseUnits="spike_count",
        responseNormalization="none",
        spikeCountDefinition=(
            "each_qualifying_trial_contributes_once_per_final_spatial_bin"
        ),
        occupancyTimeSec=[
            [1.0 for _x in range(n_x)]
            for _y in range(n_y)
        ],
        occupancyTimeSecSize=[n_y, n_x],
        occupancyTimeDefinition=(
            "sum_of_qualifying_trial_durations_per_final_spatial_bin"
        ),
    )
    return payload


@unittest.skipUnless(gui.TK_AVAILABLE, "Tk is not available in this Python")
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
        # Keep the GUI suite independent from preferences saved by a real app
        # session on the host running the tests.
        # Minimal 1.9 temporarily removed the persistence hook.  ``create``
        # keeps one absent symbol from masking all 48 independent Tk contracts;
        # restored 1.9.1 code still consumes the patched path normally.
        with mock.patch.object(
            gui,
            "viewer_settings_path",
            return_value=settings_path,
            create=True,
        ):
            self.app = gui.RFMViewer(gui.RFMappingData(path))
        self.addCleanup(self._destroy_app)
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

        def slow_decode(_path: Path) -> gui.RFMappingData:
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

    def test_rf_tab_tuning_pane_hides_and_restores_rf_space(self) -> None:
        self.app.notebook.select(0)
        self.app.update()
        initial_rf_width = self.app.rf_map_pane.winfo_width()
        self.assertTrue(self.app.tuning_curve_pane.winfo_ismapped())
        self.assertGreater(
            initial_rf_width,
            2 * self.app.tuning_curve_pane.winfo_width(),
        )

        hidden = replace(
            self.app.settings,
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
        self.app.update()
        self.assertFalse(self.app.tuning_curve_pane.winfo_ismapped())
        self.assertGreater(self.app.rf_map_pane.winfo_width(), initial_rf_width)

        shown = replace(hidden, show_tuning_curve=True)
        self.assertTrue(
            self.app._apply_viewer_settings(
                shown,
                persist=False,
                broadcast=False,
            )
        )
        self.app.update()
        self.assertTrue(self.app.tuning_curve_pane.winfo_ismapped())

    def test_narrow_window_uses_responsive_stacked_tuning_layout(self) -> None:
        self.app.notebook.select(0)
        self.app.geometry("1120x720")
        self.app.update()
        self.assertEqual(int(self.app.tuning_curve_pane.grid_info()["row"]), 1)
        self.assertEqual(int(self.app.tuning_curve_pane.grid_info()["column"]), 0)

        self.app.geometry("1440x900")
        self.app.update()
        self.assertEqual(int(self.app.tuning_curve_pane.grid_info()["row"]), 0)
        self.assertEqual(int(self.app.tuning_curve_pane.grid_info()["column"]), 1)

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
        self.assertIn("optional", self.app.tuning_curve_status_label.cget("text").lower())

        with mock.patch.object(self.app, "_attach_tuning_curve") as attach:
            self.app._on_tuning_curve_click(SimpleNamespace(x=20, y=20))
        attach.assert_called_once_with()

    def test_loaded_tuning_curve_draws_line_and_polar_without_extending_units(self) -> None:
        tuning_path = Path(self.directory.name) / "tuning_curves.json"
        curve = tuple(float((index % 24) + 1) for index in range(gui.HD_RAW_BIN_COUNT))
        self.app.tuning_curve_data = gui.TuningCurveData(
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
        self.assertIn(
            "legacy schema",
            self.app.tuning_curve_status_label.cget("text"),
        )

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
        curve = tuple(float((index % 24) + 1) for index in range(gui.HD_RAW_BIN_COUNT))
        self.app.tuning_curve_data = gui.TuningCurveData(
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

        self.app.tuning_curve_data = gui.TuningCurveData(
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
        curve = tuple(2.0 for _index in range(gui.HD_RAW_BIN_COUNT))
        metadata = gui.TuningCurveMetadata(
            timebase="Open Ephys ADC seconds",
            timestamp_reference="Exposure TTL rising edge",
            angle_convention_note="0° up; positive counterclockwise",
            feature_fs_hz=119.82,
            classification=gui.TuningCurveClassificationProvenance(
                method="Rayleigh and circular shuffle",
                rayleigh_alpha=0.05,
                shuffle_alpha=0.01,
                num_shuffle=1000,
            ),
            ttl_qc=gui.TuningCurveTTLProvenance(
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
        self.app.tuning_curve_data = gui.TuningCurveData(
            tuning_path,
            {7: curve},
            metadata=metadata,
        )
        self.app.notebook.select(0)
        self.app._set_selected_unit_id(7)
        self.app._draw_tuning_curve()
        self.app.update()

        self.assertTrue(self.app.tuning_provenance_button.winfo_ismapped())
        with mock.patch.object(gui.messagebox, "showinfo") as show_info:
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

        self.app.tuning_curve_data = gui.TuningCurveData(tuning_path, {7: curve})
        self.app._draw_tuning_curve()
        self.app.update()
        self.assertFalse(self.app.tuning_provenance_button.winfo_ismapped())

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
        self.app.tuning_curve_data = gui.TuningCurveData(
            tuning_path,
            {
                7: (10.0,) * gui.HD_RAW_BIN_COUNT,
                8: (20.0,) * gui.HD_RAW_BIN_COUNT,
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
        self.assertIn("shared within file: 0–20 Hz", self.app.tuning_curve_status_label.cget("text"))

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
        curve = tuple(float(index + 1) for index in range(gui.HD_RAW_BIN_COUNT))
        self.app.tuning_curve_data = gui.TuningCurveData(tuning_path, {7: curve})
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

        self.app.tuning_curve_data = gui.TuningCurveData(
            tuning_path,
            {7: (0.0,) * gui.HD_RAW_BIN_COUNT},
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
        self.assertIsInstance(settings, gui.SettingsWindow)
        self.addCleanup(settings.destroy)
        self.assertEqual(
            [settings.notebook.tab(tab, "text") for tab in settings.notebook.tabs()],
            ["General", "RF Map", "Tuning Curve"],
        )

        settings.show_tuning_curve_var.set(False)
        settings.show_probe_layout_var.set(False)
        settings.tuning_smoothing_var.set(False)
        settings.update_idletasks()
        self.assertIn("disabled", settings.auto_tuning_check.state())
        self.assertIn("disabled", settings.auto_probe_check.state())
        self.assertIn("disabled", settings.tuning_sigma_entry.state())

        settings.show_tuning_curve_var.set(True)
        settings.show_probe_layout_var.set(True)
        settings.tuning_smoothing_var.set(True)
        settings.update_idletasks()
        self.assertNotIn("disabled", settings.auto_tuning_check.state())
        self.assertNotIn("disabled", settings.auto_probe_check.state())
        self.assertNotIn("disabled", settings.tuning_sigma_entry.state())

        pending = list(settings.winfo_children())
        button_labels = []
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, gui.ttk.Button):
                button_labels.append(widget.cget("text"))
        self.assertIn("Save", button_labels)
        self.assertIn("Cancel", button_labels)
        self.assertNotIn("Apply", button_labels)
        self.assertNotIn("Restore Defaults", button_labels)

    def test_applying_settings_updates_the_active_window(self) -> None:
        self.app._app_root._rfm_active_viewer = self.app
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, gui.SettingsWindow)
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
        self.assertFalse(self.app.tuning_curve_pane.winfo_ismapped())
        self.assertTrue(self.app.tuning_collapsed_rail.winfo_ismapped())
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
        self.app.probe_geometry = gui.ProbeGeometry(
            probe_name="ProbeA",
            positions_path=base / "positions.csv",
            channels_path=base / "channels.csv",
            units=(
                gui.ProbeUnitPosition(7, 0.0, 0.0),
                gui.ProbeUnitPosition(8, 10.0, 100.0),
            ),
            channels=(
                gui.ProbeChannel(0, 0.0, 0.0, 0),
                gui.ProbeChannel(1, 10.0, 100.0, 0),
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
        self.app.probe_geometry = gui.ProbeGeometry(
            probe_name="ProbeA",
            positions_path=base / "positions.csv",
            channels_path=base / "channels.csv",
            units=(
                gui.ProbeUnitPosition(7, 0.0, 0.0),
                gui.ProbeUnitPosition(8, None, None),
            ),
            channels=(gui.ProbeChannel(0, 0.0, 0.0, 0),),
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
            gui.SpatialRegion.from_corners(-10, -10, 10, 10)
        )
        self.assertEqual(self.app._unit_navigation_ids(), [7])
        self.assertEqual(self.app._selected_unit_id_value(), 7)

        self.app._clear_spatial_filter()
        self.assertEqual(self.app._unit_navigation_ids(), [7, 8])

        self.app._set_selected_unit_id(8)
        self.app._apply_spatial_region(
            gui.SpatialRegion.from_corners(100, 100, 110, 110)
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
        self.app.probe_geometry = gui.ProbeGeometry(
            probe_name="ProbeA",
            positions_path=base / "positions.csv",
            channels_path=None,
            units=(gui.ProbeUnitPosition(8, None, None),),
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

    def test_settings_validation_selects_and_marks_the_owning_tab(self) -> None:
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, gui.SettingsWindow)
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
        self.assertIsInstance(settings, gui.SettingsWindow)
        self.addCleanup(settings.destroy)
        settings.tuning_smoothing_var.set(False)
        settings.tuning_smooth_sigma_var.set("not-a-number")

        settings._commit(close=False)

        self.assertEqual(settings.error_var.get(), "")
        self.assertEqual(settings._tab_error_vars["Tuning Curve"].get(), "")
        self.assertEqual(
            float(settings.tuning_smooth_sigma_var.get()),
            expected_sigma * 360.0 / gui.DEFAULT_HD_DISPLAY_BINS,
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
            value for value in gui.PALETTES if value != self.app.palette_var.get()
        )
        polar_radius = next(
            value
            for value in gui.POLAR_RADIUS_MODES
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
        self.assertIsInstance(settings, gui.SettingsWindow)
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
        ):
            settings._commit(close=False)

        self.assertEqual(draw_probe.call_count, 1)
        self.assertEqual(draw_tuning.call_count, 1)

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
        self.assertIsInstance(settings, gui.SettingsWindow)
        self.addCleanup(settings.destroy)

        settings.rf_flip_y_var.set(True)
        settings.rf_palette_var.set("Viridis")
        settings.tuning_plot_mode_var.set("Polar")
        settings.tuning_display_bins_var.set("12")
        settings.tuning_compare_scale_var.set(True)
        settings.show_tuning_curve_var.set(False)
        settings._commit(close=False)

        self.assertEqual(settings.error_var.get(), "")
        for viewer in (self.app, paired):
            self.assertTrue(viewer.flip_y_var.get())
            self.assertEqual(viewer.palette_var.get(), "Viridis")
            self.assertEqual(viewer.tuning_plot_mode_var.get(), "Polar")
            self.assertEqual(viewer.tuning_display_bins_var.get(), 12)
            self.assertTrue(viewer.tuning_compare_scale_var.get())
            self.assertFalse(viewer.show_tuning_curve_var.get())
            self.assertFalse(viewer.tuning_curve_pane.winfo_ismapped())

    def test_reused_settings_window_follows_the_active_viewer(self) -> None:
        second = self.app._open_json_window(self.app.data.path)
        self.assertIsNotNone(second)
        assert second is not None
        self.addCleanup(second.destroy)
        self.app._app_root._rfm_active_viewer = self.app
        self.app._show_settings()
        settings = self.app._app_root._rfm_settings_window
        self.assertIsInstance(settings, gui.SettingsWindow)
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
            gui.filedialog,
            "askopenfilename",
            return_value="",
        ) as picker:
            self.app._on_probe_press(event)
            self.app._on_probe_release(event)

        picker.assert_called_once()
        self.assertEqual(picker.call_args.kwargs["title"], "Attach probe positions")

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

    def test_destroy_cancels_pending_resize_callback(self) -> None:
        self.app._schedule_redraw()
        self.assertIsNotNone(self.app._redraw_after)
        self.app.destroy()
        self.assertIsNone(self.app._redraw_after)
        self.app = None

    def test_document_windows_keep_independent_state(self) -> None:
        second = self.app._open_json_window(self.app.data.path)
        self.assertIsNotNone(second)
        assert second is not None
        self.addCleanup(second.destroy)

        self.assertEqual(len(self.app._app_root._rfm_viewer_windows), 2)
        second._step_unit(1)
        self.assertEqual(self.app.unit_idx.get(), 0)
        self.assertEqual(second.unit_idx.get(), 1)

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
        self.assertEqual(int(self.app.reset_plot_range_button.grid_info()["column"]), 5)
        self.assertEqual(int(self.app.display_toggle_button.grid_info()["column"]), 8)
        self.app._toggle_display_controls()
        self.app.update_idletasks()
        self.assertEqual(self.app.display_controls_frame.winfo_manager(), "grid")
        self.assertTrue(self.app.display_expanded_var.get())
        self.assertEqual(int(self.app.display_controls_frame.grid_info()["row"]), 1)
        self.assertIs(self.app.display_controls_frame.master.master, self.app.nametowidget(self.app.notebook.winfo_parent()))
        self.app._toggle_display_controls()
        self.assertEqual(self.app.display_controls_frame.winfo_manager(), "")

    def test_sidebar_has_no_horizontal_separators(self) -> None:
        separator_children = [
            child
            for child in self.app.sidebar_frame.winfo_children()
            if child.winfo_class() == "TSeparator"
        ]
        self.assertEqual(separator_children, [])

    def test_sidebar_selection_inspector_is_visible_and_updates(self) -> None:
        self.app.notebook.select(0)
        self.app._update_all()
        self.app.update_idletasks()

        self.assertTrue(self.app.cell_label.winfo_ismapped())
        self.assertIn("cluster", self.app.cell_label.cget("text"))
        self.app.selected_cell = (0, 0, 0, 0)
        self.app._update_cell_label()
        self.assertIn("xIdx 1", self.app.cell_label.cget("text"))

    def test_spatial_region_filters_navigation_and_handles_no_matches(self) -> None:
        positions_path = Path(self.directory.name) / "positions.csv"
        self.app.probe_geometry = gui.ProbeGeometry(
            probe_name="ProbeA",
            positions_path=positions_path,
            channels_path=None,
            units=(
                gui.ProbeUnitPosition(7, 0.0, 0.0),
                gui.ProbeUnitPosition(8, 300.0, 300.0),
            ),
            channels=(),
        )
        self.app._set_selected_unit_id(8)

        self.app._apply_spatial_region(gui.SpatialRegion.from_corners(-10, -10, 10, 10))
        self.assertEqual(self.app._unit_combo_unit_ids, [7])
        self.assertEqual(self.app._selected_unit_id_value(), 7)

        self.app._apply_spatial_region(gui.SpatialRegion.from_corners(100, 100, 110, 110))
        self.assertEqual(self.app._unit_combo_unit_ids, [])
        self.assertEqual(self.app.unit_idx.get(), -1)
        self.assertIn("No units match", self.app.status_label.cget("text"))

        self.app._handle_escape()
        self.assertIsNone(self.app.spatial_region)
        self.assertEqual(self.app._unit_combo_unit_ids, [7, 8])

    def test_paired_unit_outside_local_region_clears_spatial_filter(self) -> None:
        self.app.probe_geometry = gui.ProbeGeometry(
            probe_name="ProbeA",
            positions_path=Path(self.directory.name) / "positions.csv",
            channels_path=None,
            units=(
                gui.ProbeUnitPosition(7, 0.0, 0.0),
                gui.ProbeUnitPosition(8, 300.0, 300.0),
            ),
            channels=(),
        )
        self.app._apply_spatial_region(gui.SpatialRegion.from_corners(-10, -10, 10, 10))
        self.assertEqual(self.app._unit_combo_unit_ids, [7])

        incoming = replace(self.app._capture_pairing_state(), unit_id=8)
        self.app._apply_pairing_state(incoming, frozenset({"unit"}))

        self.assertIsNone(self.app.spatial_region)
        self.assertEqual(self.app._selected_unit_id_value(), 8)
        self.assertEqual(self.app._unit_combo_unit_ids, [7, 8])

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
