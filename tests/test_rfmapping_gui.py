import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import rfmapping_gui as gui


def write_payload(payload: dict) -> tuple[tempfile.TemporaryDirectory, Path]:
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "rf.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return directory, path


def base_payload(*, with_presentations: bool = True) -> dict:
    payload = {
        "unitsSpikeCounts": [[[[10, 20, 30], [5, 10, 15]]]],
        "unitsSpikeCountsSize": [1, 1, 2, 3],
        "unitPool": [42],
        "xPositions": [-1, 1],
        "yPositions": [0],
        "timeBinEdges": [-0.1, 0.0, 0.05, 0.2],
    }
    if with_presentations:
        payload["stimulusPresentationCounts"] = [[10, 5]]
    return payload


class RFMappingRateTests(unittest.TestCase):
    def load(self, payload: dict) -> gui.RFMappingData:
        directory, path = write_payload(payload)
        self.addCleanup(directory.cleanup)
        return gui.RFMappingData(path)

    def test_count_per_presentation_and_rate_use_exact_edges(self) -> None:
        data = self.load(base_payload())

        self.assertEqual(data.response_value(0, 0, 0, 0, 0, gui.VALUE_MODE_COUNT), 10)
        self.assertEqual(
            data.response_value(0, 0, 0, 0, 1, gui.VALUE_MODE_PER_PRESENTATION),
            3,
        )
        self.assertAlmostEqual(
            data.response_value(0, 0, 0, 0, 0, gui.VALUE_MODE_RATE),
            10.0,
        )
        self.assertAlmostEqual(
            data.response_value(0, 0, 0, 1, 1, gui.VALUE_MODE_RATE),
            40.0,
        )
        self.assertAlmostEqual(
            data.response_value(0, 0, 0, 0, 1, gui.VALUE_MODE_RATE),
            20.0,
        )
        self.assertAlmostEqual(
            data.response_value(0, 0, 1, 0, 1, gui.VALUE_MODE_RATE),
            20.0,
        )

    def test_count_matrix_matches_existing_range_sum(self) -> None:
        data = self.load(base_payload())
        old_matrix = data.aggregate_matrix(0, "Range sum", 0, 1, 2)
        new_matrix = data.response_matrix(0, 1, 2, gui.VALUE_MODE_COUNT)
        self.assertEqual(new_matrix, old_matrix)

    def test_reversed_range_is_normalized_without_losing_bins(self) -> None:
        data = self.load(base_payload())
        forward = data.response_value(0, 0, 0, 0, 2, gui.VALUE_MODE_RATE)
        reverse = data.response_value(0, 0, 0, 2, 0, gui.VALUE_MODE_RATE)
        self.assertEqual(forward, reverse)
        self.assertAlmostEqual(data.time_span_seconds(2, 0), 0.3)

    def test_legacy_json_remains_count_only(self) -> None:
        data = self.load(base_payload(with_presentations=False))
        self.assertTrue(data.supports_value_mode(gui.VALUE_MODE_COUNT))
        self.assertFalse(data.supports_value_mode(gui.VALUE_MODE_RATE))
        with self.assertRaisesRegex(ValueError, "stimulusPresentationCounts"):
            data.response_matrix(0, 0, 0, gui.VALUE_MODE_RATE)

    def test_zero_presentations_with_zero_counts_is_no_data(self) -> None:
        payload = base_payload()
        payload["unitsSpikeCounts"][0][0][1] = [0, 0, 0]
        payload["stimulusPresentationCounts"][0][1] = 0
        data = self.load(payload)
        self.assertIsNone(data.response_value(0, 0, 1, 0, 2, gui.VALUE_MODE_RATE))

    def test_zero_presentations_with_nonzero_counts_is_rejected(self) -> None:
        payload = base_payload()
        payload["stimulusPresentationCounts"][0][0] = 0
        with self.assertRaisesRegex(ValueError, "zero where spike counts are nonzero"):
            self.load(payload)

    def test_presentation_metadata_shape_and_values_are_validated(self) -> None:
        bad_shape = base_payload()
        bad_shape["stimulusPresentationCounts"] = [[10]]
        with self.assertRaisesRegex(ValueError, "x dimension"):
            self.load(bad_shape)

        fractional = base_payload()
        fractional["stimulusPresentationCounts"] = [[10.5, 5]]
        with self.assertRaisesRegex(ValueError, "non-negative integers"):
            self.load(fractional)

    def test_matlab_singleton_presentation_dimensions_are_restored(self) -> None:
        one_row = base_payload()
        one_row["stimulusPresentationCounts"] = [10, 5]
        self.assertEqual(self.load(one_row).presentation_counts, [[10.0, 5.0]])

        scalar = {
            "unitsSpikeCounts": [[[[1, 2]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 2],
            "unitPool": [1],
            "xPositions": [0],
            "yPositions": [0],
            "timeBinEdges": [0, 0.1, 0.2],
            "stimulusPresentationCounts": 3,
        }
        self.assertEqual(self.load(scalar).presentation_counts, [[3.0]])

    def test_count_values_must_be_json_numbers(self) -> None:
        payload = base_payload()
        payload["unitsSpikeCounts"][0][0][0][1] = "20"
        with self.assertRaisesRegex(ValueError, "is not numeric"):
            self.load(payload)


class RasterTests(unittest.TestCase):
    def test_matrix_ppm_nearest_neighbor_colors(self) -> None:
        ppm = gui.matrix_ppm_data(
            [[0.0, 1.0]],
            4,
            1,
            lambda value: "#ff0000" if value == 0.0 else "#0000ff",
        )
        header = b"P6\n4 1\n255\n"
        self.assertTrue(ppm.startswith(header))
        self.assertEqual(
            ppm[len(header) :],
            bytes((255, 0, 0)) * 2 + bytes((0, 0, 255)) * 2,
        )

    def test_atlas_places_tiles_without_changing_background(self) -> None:
        ppm = gui.matrix_atlas_ppm_data(
            [([[1.0]], 1.0, 1.0, 2.0)],
            4,
            4,
            lambda _value: "#102030",
        )
        header = b"P6\n4 4\n255\n"
        pixels = ppm[len(header) :]

        def pixel(x: int, y: int) -> bytes:
            offset = (y * 4 + x) * 3
            return pixels[offset : offset + 3]

        self.assertEqual(pixel(0, 0), b"\xff\xff\xff")
        self.assertEqual(pixel(1, 1), b"\x10\x20\x30")
        self.assertEqual(pixel(2, 2), b"\x10\x20\x30")
        self.assertEqual(pixel(3, 3), b"\xff\xff\xff")

    def test_polar_atlas_preserves_blank_center_and_colors_rings(self) -> None:
        ppm = gui.polar_matrix_atlas_ppm_data(
            [([[1.0, 1.0]], 0.0, 0.0, 2.0, 360.0, [0])],
            20,
            20,
            lambda _value: "#123456",
        )
        header = b"P6\n20 20\n255\n"
        pixels = ppm[len(header) :]

        def pixel(x: int, y: int) -> bytes:
            offset = (y * 20 + x) * 3
            return pixels[offset : offset + 3]

        self.assertEqual(pixel(10, 10), b"\xff\xff\xff")
        self.assertEqual(pixel(10, 1), b"\x12\x34\x56")


class RFPlotRangeTests(unittest.TestCase):
    class FakeVar:
        def __init__(self, value) -> None:
            self.value = value

        def get(self):
            return self.value

        def set(self, value) -> None:
            self.value = value

    @staticmethod
    def viewer_with_edges(edges_ms: list[float]):
        viewer = mock.Mock()
        viewer.data = mock.Mock(
            n_bins=len(edges_ms) - 1,
            time_bin_edges=[value / 1000.0 for value in edges_ms],
        )
        viewer._snap_time_range_to_bins = lambda start, end: gui.RFMViewer._snap_time_range_to_bins(
            viewer, start, end
        )
        return viewer

    def test_default_rf_range_is_zero_to_twenty_ms(self) -> None:
        viewer = self.viewer_with_edges([-100, 0, 10, 20, 30])
        self.assertEqual(gui.RFMViewer._default_plot_time_bounds_ms(viewer), (0.0, 20.0))

    def test_default_rf_range_clamps_to_one_available_bin(self) -> None:
        all_negative = self.viewer_with_edges([-100, -50, -20])
        all_positive = self.viewer_with_edges([50, 60, 100])
        self.assertEqual(gui.RFMViewer._default_plot_time_bounds_ms(all_negative), (-50.0, -20.0))
        self.assertEqual(gui.RFMViewer._default_plot_time_bounds_ms(all_positive), (50.0, 60.0))

    def test_reversed_and_out_of_axis_range_is_clamped_and_ordered(self) -> None:
        viewer = self.viewer_with_edges([-100, 0, 10, 20, 30])
        self.assertEqual(gui.RFMViewer._snap_time_range_to_bins(viewer, 1000.0, -1000.0), (0, 3))
        self.assertEqual(gui.RFMViewer._snap_time_range_to_bins(viewer, 1000.0, 2000.0), (3, 3))

    def test_rf_sum_value_text_includes_actual_snapped_bounds(self) -> None:
        viewer = mock.Mock()
        viewer.value_mode_var = self.FakeVar(gui.VALUE_MODE_COUNT)
        viewer._selected_time_bounds_ms.return_value = (0.0, 20.0)

        self.assertEqual(
            gui.RFMViewer._rf_sum_range_value_text(viewer, 110.0),
            "RF sum range 0–20 ms: 110 spikes",
        )

    def test_timeline_selection_cannot_change_current_rf_matrix(self) -> None:
        payload = {
            "unitsSpikeCounts": [[[[1, 10, 100, 1000]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 4],
            "unitPool": [42],
            "xPositions": [0],
            "yPositions": [0],
            "timeBinEdges": [-0.1, 0.0, 0.01, 0.02, 0.03],
        }
        directory, path = write_payload(payload)
        self.addCleanup(directory.cleanup)
        data = gui.RFMappingData(path)
        viewer = mock.Mock()
        viewer.data = data
        viewer.unit_idx = self.FakeVar(0)
        viewer.value_mode_var = self.FakeVar(gui.VALUE_MODE_COUNT)
        viewer.range_start_ms_var = self.FakeVar("0")
        viewer.range_end_ms_var = self.FakeVar("20")
        viewer.range_start_var = self.FakeVar(0)
        viewer.range_end_var = self.FakeVar(3)
        viewer._parse_time_control = lambda variable, fallback: gui.RFMViewer._parse_time_control(
            viewer, variable, fallback
        )
        viewer._snap_time_range_to_bins = lambda start, end: gui.RFMViewer._snap_time_range_to_bins(
            viewer, start, end
        )
        viewer._source_bins_for_display_range = lambda: gui.RFMViewer._source_bins_for_time_controls(
            viewer
        )

        before = gui.RFMViewer._current_matrix(viewer)
        viewer.range_start_var.set(3)
        viewer.range_end_var.set(3)
        after = gui.RFMViewer._current_matrix(viewer)

        self.assertEqual(before, [[110.0]])
        self.assertEqual(after, before)


class MacOSLifecycleTests(unittest.TestCase):
    def test_frozen_startup_uses_bundled_json_without_modal_picker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / "RF Mapping Viewer.app" / "Contents"
            executable = contents / "MacOS" / "RF Mapping Viewer"
            data_dir = contents / "Resources" / "data"
            data_dir.mkdir(parents=True)
            document = data_dir / "bundled.json"
            document.write_text("{}", encoding="utf-8")

            with (
                mock.patch.object(gui.sys, "frozen", True, create=True),
                mock.patch.object(gui.sys, "executable", str(executable)),
            ):
                self.assertEqual(gui.startup_json_path(), document.resolve())

    def test_macos_handlers_include_open_document_and_quit(self) -> None:
        class FakeTk:
            def __init__(self) -> None:
                self.commands = {}

            def createcommand(self, name, callback) -> None:
                self.commands[name] = callback

        class FakeViewer:
            def __init__(self) -> None:
                self.tk = FakeTk()
                self._app_root = mock.Mock()
                self.protocols = {}
                self.bindings = {}
                self._quit_application = lambda *_args: None
                self._close_window = lambda *_args: None
                self._dispatch_open_json = lambda *_args: None
                self._dispatch_macos_open_documents = lambda *_args: None

            def protocol(self, name, callback) -> None:
                self.protocols[name] = callback

            def bind_all(self, event, callback) -> None:
                self.bindings[event] = callback

        viewer = FakeViewer()
        with mock.patch.object(gui.sys, "platform", "darwin"):
            gui.RFMViewer._install_application_handlers(viewer)

        self.assertIs(viewer.protocols["WM_DELETE_WINDOW"], viewer._close_window)
        self.assertIs(viewer.tk.commands["::tk::mac::OpenDocument"], viewer._dispatch_macos_open_documents)
        self.assertIs(viewer.tk.commands["::tk::mac::Quit"], viewer._quit_application)

    def test_open_document_creates_independent_windows(self) -> None:
        class FakeViewer:
            def __init__(self) -> None:
                self._viewer_ready = True
                self.opened = []

            def _open_json_window(self, path: Path) -> None:
                self.opened.append(path)

        viewer = FakeViewer()
        gui.RFMViewer._on_macos_open_documents(viewer, "/tmp/a.json", "/tmp/b.json")
        self.assertEqual(viewer.opened, [Path("/tmp/a.json"), Path("/tmp/b.json")])

    def test_open_dialog_routes_ready_document_to_new_window(self) -> None:
        class FakeViewer:
            def __init__(self) -> None:
                self._viewer_ready = True
                self.data = mock.Mock(path=Path("/tmp/current.json"))
                self.opened = []

            def _open_json_window(self, path: Path) -> None:
                self.opened.append(path)

        viewer = FakeViewer()
        fake_dialog = mock.Mock()
        fake_dialog.askopenfilename.return_value = "/tmp/next.json"
        with mock.patch.object(gui, "filedialog", fake_dialog):
            gui.RFMViewer._open_json(viewer)
        self.assertEqual(viewer.opened, [Path("/tmp/next.json")])

    def test_initial_open_document_replaces_deferred_bundled_load(self) -> None:
        class FakeViewer:
            def __init__(self) -> None:
                self._viewer_ready = False
                self._startup_after = "bundled-load"
                self.cancelled = []
                self.scheduled = []

            def after_cancel(self, callback_id) -> None:
                self.cancelled.append(callback_id)

            def after_idle(self, callback):
                self.scheduled.append(callback)
                return "document-load"

            def _load_startup_document(self, path: Path) -> None:
                self.loaded = path

            def _open_json_window(self, path: Path) -> None:
                self.opened = path

        viewer = FakeViewer()
        viewer._cancel_startup_callback = lambda: gui.RFMViewer._cancel_startup_callback(viewer)
        gui.RFMViewer._on_macos_open_documents(viewer, "/tmp/requested.json")

        self.assertEqual(viewer.cancelled, ["bundled-load"])
        self.assertEqual(viewer._startup_after, "document-load")
        viewer.scheduled[0]()
        self.assertEqual(viewer.loaded, Path("/tmp/requested.json"))

    def test_quit_is_idempotent_and_destroys_root(self) -> None:
        class FakeRoot:
            def __init__(self) -> None:
                self._rfm_quitting = False
                self.destroy_calls = 0

            def destroy(self) -> None:
                self.destroy_calls += 1

        class FakeViewer:
            def __init__(self) -> None:
                self._quitting = False
                self._app_root = FakeRoot()

        viewer = FakeViewer()
        gui.RFMViewer._quit_application(viewer)
        gui.RFMViewer._quit_application(viewer)
        self.assertTrue(viewer._quitting)
        self.assertEqual(viewer._app_root.destroy_calls, 1)

    def test_bundle_prohibits_detached_duplicate_instances(self) -> None:
        build_script = Path(gui.__file__).resolve().parent / "script" / "build_python_macos_app.sh"
        source = build_script.read_text(encoding="utf-8")
        self.assertIn('Add :LSMultipleInstancesProhibited bool true', source)
        self.assertNotIn('Add :LSMultipleInstancesProhibited bool false', source)


class ShortcutBehaviorTests(unittest.TestCase):
    class FakeVar:
        def __init__(self, value) -> None:
            self.value = value

        def get(self):
            return self.value

        def set(self, value) -> None:
            self.value = value

    def test_timeline_step_moves_single_bin_selection(self) -> None:
        viewer = mock.Mock()
        viewer._time_group_count.return_value = 12
        viewer.bin_var = self.FakeVar(5)
        viewer.range_start_var = self.FakeVar(0)
        viewer.range_end_var = self.FakeVar(11)
        viewer.range_start_ms_var = self.FakeVar("0")
        viewer.range_end_ms_var = self.FakeVar("20")
        viewer._timeline_range_anchor = None

        gui.RFMViewer._step_timeline_bin(viewer, -1)

        self.assertEqual(viewer.bin_var.get(), 4)
        self.assertEqual((viewer.range_start_var.get(), viewer.range_end_var.get()), (4, 4))
        self.assertEqual(viewer._timeline_range_anchor, 4)
        self.assertEqual((viewer.range_start_ms_var.get(), viewer.range_end_ms_var.get()), ("0", "20"))
        viewer._sync_time_range_controls.assert_called_once_with()
        viewer._update_all.assert_called_once_with()

    def test_timeline_selection_sync_never_changes_rf_sum_controls(self) -> None:
        viewer = mock.Mock()
        viewer._time_group_count.return_value = 12
        viewer.range_start_var = self.FakeVar(9)
        viewer.range_end_var = self.FakeVar(4)
        viewer.range_start_ms_var = self.FakeVar("0")
        viewer.range_end_ms_var = self.FakeVar("20")

        gui.RFMViewer._sync_time_range_controls(viewer)

        self.assertEqual((viewer.range_start_var.get(), viewer.range_end_var.get()), (9, 4))
        self.assertEqual((viewer.range_start_ms_var.get(), viewer.range_end_ms_var.get()), ("0", "20"))

    def test_time_resolution_step_is_exactly_one_ms_before_data_clamping(self) -> None:
        viewer = mock.Mock()
        viewer.time_res_ms_var = self.FakeVar("8")
        viewer._base_bin_ms.return_value = 1.0
        viewer._total_time_ms.return_value = 30.0

        gui.RFMViewer._step_time_resolution(viewer, 1.0)

        self.assertEqual(viewer.time_res_ms_var.get(), "9")
        viewer._on_time_resolution_changed.assert_called_once_with()

    def test_time_resolution_change_preserves_partial_timeline_source_bounds(self) -> None:
        viewer = mock.Mock()
        viewer.data = mock.Mock(n_bins=8)
        viewer.bin_var = self.FakeVar(4)
        viewer.range_start_var = self.FakeVar(2)
        viewer.range_end_var = self.FakeVar(5)
        viewer._timeline_range_anchor = 5
        viewer._last_time_groups = [(index, index) for index in range(8)]
        new_groups = [(0, 1), (2, 3), (4, 5), (6, 7)]

        def normalize() -> None:
            viewer._last_time_groups = new_groups

        viewer._normalize_control_values.side_effect = normalize

        gui.RFMViewer._on_time_resolution_changed(viewer)

        self.assertEqual((viewer.range_start_var.get(), viewer.range_end_var.get()), (1, 2))
        self.assertEqual(viewer.bin_var.get(), 2)
        self.assertIsNone(viewer._timeline_range_anchor)
        viewer._update_all.assert_called_once_with()

    def test_show_full_timeline_range_resets_timeline_selection(self) -> None:
        viewer = mock.Mock()
        viewer._time_group_count.return_value = 12
        viewer.bin_var = self.FakeVar(6)
        viewer.range_start_var = self.FakeVar(4)
        viewer.range_end_var = self.FakeVar(8)
        viewer._timeline_range_anchor = 8

        gui.RFMViewer._clear_timeline_selection(viewer)

        self.assertEqual(viewer.bin_var.get(), 0)
        self.assertEqual((viewer.range_start_var.get(), viewer.range_end_var.get()), (0, 11))
        self.assertIsNone(viewer._timeline_range_anchor)


class TimelineHitTestingTests(unittest.TestCase):
    @staticmethod
    def viewer_with_layout():
        viewer = mock.Mock()
        viewer._canvas_layouts = {
            "timeline": {
                "mini_left": 10.0,
                "mini_top": 20.0,
                "mini_w": 20.0,
                "gap_x": 5.0,
                "row_step": 20.0,
                "cols": 2,
            }
        }
        viewer._timeline_cells = [
            {
                "bin_idx": 0,
                "x0": 12.0,
                "y0": 20.0,
                "grid_w": 10.0,
                "grid_h": 10.0,
                "label_gap": 2.0,
                "label_height": 3.0,
            },
            {
                "bin_idx": 1,
                "x0": 37.0,
                "y0": 20.0,
                "grid_w": 10.0,
                "grid_h": 10.0,
                "label_gap": 2.0,
                "label_height": 3.0,
            },
            {
                "bin_idx": 2,
                "x0": 12.0,
                "y0": 40.0,
                "grid_w": 10.0,
                "grid_h": 10.0,
                "label_gap": 2.0,
                "label_height": 3.0,
            },
        ]
        return viewer

    def test_direct_candidate_lookup_maps_rows_and_columns(self) -> None:
        viewer = self.viewer_with_layout()

        first = gui.RFMViewer._timeline_layout_at_point(
            viewer, 13.0, 21.0, include_label=False
        )
        second = gui.RFMViewer._timeline_layout_at_point(
            viewer, 38.0, 21.0, include_label=False
        )
        third = gui.RFMViewer._timeline_layout_at_point(
            viewer, 13.0, 41.0, include_label=False
        )

        self.assertEqual(first["bin_idx"], 0)
        self.assertEqual(second["bin_idx"], 1)
        self.assertEqual(third["bin_idx"], 2)

    def test_direct_candidate_lookup_rejects_gaps_and_optionally_accepts_labels(self) -> None:
        viewer = self.viewer_with_layout()

        self.assertIsNone(
            gui.RFMViewer._timeline_layout_at_point(
                viewer, 24.0, 21.0, include_label=False
            )
        )
        self.assertIsNone(
            gui.RFMViewer._timeline_layout_at_point(
                viewer, 13.0, 33.0, include_label=False
            )
        )
        label = gui.RFMViewer._timeline_layout_at_point(
            viewer, 13.0, 33.0, include_label=True
        )
        self.assertEqual(label["bin_idx"], 0)


if __name__ == "__main__":
    unittest.main()
