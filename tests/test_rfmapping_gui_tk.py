import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import rfmapping_gui as gui


@unittest.skipUnless(gui.TK_AVAILABLE, "Tk is not available in this Python")
class TkViewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        n_bins = 30
        payload = {
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
            "stimulusPresentationCounts": [[5, 5, 5], [5, 5, 5]],
        }
        path = Path(self.directory.name) / "viewer.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
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
        self.assertEqual(float(self.app.range_end_ms_var.get()), 20.0)

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
        self.assertEqual(first["presentation_count_min"], "5.0")
        self.assertAlmostEqual(float(first["value"]), expected)


if __name__ == "__main__":
    unittest.main()
