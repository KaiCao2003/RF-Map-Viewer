import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import MethodType, SimpleNamespace
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


class AppIdentityTests(unittest.TestCase):
    def test_display_version_omits_internal_edition(self) -> None:
        self.assertEqual(gui.APP_DISPLAY_VERSION, gui.APP_VERSION)
        self.assertNotIn(gui.APP_EDITION, gui.APP_DISPLAY_VERSION)


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
        with self.assertRaisesRegex(ValueError, "dimensions do not match unitsSpikeCountsSize"):
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
        with self.assertRaisesRegex(ValueError, "must be JSON numbers"):
            self.load(payload)


class UnitFilterSettingsTests(unittest.TestCase):
    def test_defaults_are_enabled_at_one_bin_and_round_trip(self) -> None:
        defaults = gui.ViewerSettings()

        self.assertTrue(defaults.rf_filter_units_with_zero_bins)
        self.assertEqual(defaults.rf_zero_bin_threshold, 1)
        restored = gui.ViewerSettings.from_mapping(defaults.to_mapping())
        self.assertEqual(restored, defaults)

    def test_invalid_persisted_filter_fields_fall_back_independently(self) -> None:
        defaults = gui.ViewerSettings()
        restored = gui.ViewerSettings.from_mapping(
            {
                "schema_version": gui.SETTINGS_SCHEMA_VERSION,
                "rf_filter_units_with_zero_bins": "yes",
                "rf_zero_bin_threshold": 0,
            }
        )

        self.assertEqual(
            restored.rf_filter_units_with_zero_bins,
            defaults.rf_filter_units_with_zero_bins,
        )
        self.assertEqual(restored.rf_zero_bin_threshold, 1)


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


class ProbeGeometryTests(unittest.TestCase):
    def test_probe_name_supports_explicit_probe_tokens(self) -> None:
        self.assertEqual(
            gui.probe_name_for_rf(Path("regular_260615_3_-100_200_ProbeA.json")),
            "ProbeA",
        )
        self.assertEqual(gui.probe_name_for_rf(Path("session-ProbeB.json")), "ProbeB")
        self.assertEqual(gui.probe_name_for_rf(Path("session ProbeA/rf.json")), "ProbeA")
        self.assertIsNone(gui.probe_name_for_rf(Path("session/rf.json")))

    def test_csv_loading_preserves_units_and_channels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            positions = root / "positions.csv"
            channels = root / "channels.csv"
            positions.write_text(
                "unit_index,unit_id,x_um,y_um\n0,42,10,20\n1,99,200,300\n",
                encoding="utf-8",
            )
            channels.write_text(
                "channel_index,channel_id,raw_channel_index,x_um,y_um,shank_id\n"
                "0,10,11,0,0,0\n1,12,13,250,400,3\n",
                encoding="utf-8",
            )

            geometry = gui.load_probe_geometry("ProbeA", positions, channels)

            self.assertEqual(geometry.positions_path, positions.resolve())
            self.assertEqual(geometry.channels_path, channels.resolve())
            self.assertEqual(
                [(unit.unit_id, unit.x_um, unit.y_um) for unit in geometry.units],
                [(42, 10.0, 20.0), (99, 200.0, 300.0)],
            )
            self.assertEqual(
                [
                    (channel.channel_id, channel.x_um, channel.y_um, channel.shank_id)
                    for channel in geometry.channels
                ],
                [(10, 0.0, 0.0, 0), (12, 250.0, 400.0, 3)],
            )

    def test_discovery_uses_bounded_data_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            json_path = data_root / "rfmapping" / "good" / "window" / "ProbeA" / "regular.json"
            json_path.parent.mkdir(parents=True)
            json_path.write_text("{}", encoding="utf-8")
            positions = data_root / "spike_position" / "ProbeA" / "positions.csv"
            channels = data_root / "waveform" / "ProbeA" / "channels.csv"
            positions.parent.mkdir(parents=True)
            channels.parent.mkdir(parents=True)
            positions.write_text("unit_index,unit_id,x_um,y_um\n0,42,1,2\n", encoding="utf-8")
            channels.write_text(
                "channel_index,channel_id,raw_channel_index,x_um,y_um,shank_id\n0,1,1,3,4,0\n",
                encoding="utf-8",
            )

            discovered = gui.discover_probe_geometry_paths(json_path)

            self.assertEqual(
                discovered,
                ("ProbeA", positions.resolve(), channels.resolve()),
            )
            assert discovered is not None
            geometry = gui.load_probe_geometry(*discovered)
            self.assertEqual(geometry.units[0].unit_id, 42)

    def test_bad_or_missing_geometry_is_nonfatal_during_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            json_path = data_root / "rfmapping" / "ProbeA" / "regular.json"
            json_path.parent.mkdir(parents=True)
            json_path.write_text(json.dumps(base_payload()), encoding="utf-8")
            positions = data_root / "spike_position" / "ProbeA" / "positions.csv"
            positions.parent.mkdir(parents=True)
            positions.write_text("wrong,columns\n1,2\n", encoding="utf-8")
            data = gui.RFMappingData(json_path)

            self.assertIsNone(data.probe_geometry())
            self.assertIn("missing required columns", data.probe_geometry_error or "")

    def test_malformed_optional_channels_still_loads_unit_positions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            json_path = data_root / "rfmapping" / "ProbeA" / "regular.json"
            json_path.parent.mkdir(parents=True)
            json_path.write_text("{}", encoding="utf-8")
            positions = data_root / "spike_position" / "ProbeA" / "positions.csv"
            channels = data_root / "waveform" / "ProbeA" / "channels.csv"
            positions.parent.mkdir(parents=True)
            channels.parent.mkdir(parents=True)
            positions.write_text(
                "unit_index,unit_id,x_um,y_um\n0,42,10,20\n",
                encoding="utf-8",
            )
            channels.write_text("wrong,columns\n1,2\n", encoding="utf-8")

            discovered = gui.discover_probe_geometry_paths(json_path)

            assert discovered is not None
            geometry = gui.load_probe_geometry(*discovered)
            self.assertEqual([unit.unit_id for unit in geometry.units], [42])
            self.assertEqual(geometry.channels, ())
            self.assertIsNone(geometry.channels_path)

    def test_discovery_does_not_escape_data_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            json_path = data_root / "rfmapping" / "ProbeA" / "regular.json"
            json_path.parent.mkdir(parents=True)
            json_path.write_text("{}", encoding="utf-8")
            positions = root / "spike_position" / "ProbeA" / "positions.csv"
            positions.parent.mkdir(parents=True)
            positions.write_text(
                "unit_index,unit_id,x_um,y_um\n0,42,10,20\n",
                encoding="utf-8",
            )

            self.assertIsNone(gui.discover_probe_geometry_paths(json_path))


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

    def test_default_rf_range_is_zero_to_two_hundred_ms(self) -> None:
        viewer = self.viewer_with_edges([-100, 0, 50, 100, 150, 200, 250])
        self.assertEqual(gui.RFMViewer._default_plot_time_bounds_ms(viewer), (0.0, 200.0))

    def test_default_rf_range_clamps_to_available_axis(self) -> None:
        all_negative = self.viewer_with_edges([-100, -50, -20])
        all_positive = self.viewer_with_edges([50, 60, 100])
        self.assertEqual(gui.RFMViewer._default_plot_time_bounds_ms(all_negative), (-50.0, -20.0))
        self.assertEqual(gui.RFMViewer._default_plot_time_bounds_ms(all_positive), (50.0, 100.0))

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
        viewer._selected_local_unit_index = lambda: 0

        before = gui.RFMViewer._current_matrix(viewer)
        viewer.range_start_var.set(3)
        viewer.range_end_var.set(3)
        after = gui.RFMViewer._current_matrix(viewer)

        self.assertEqual(before, [[110.0]])
        self.assertEqual(after, before)


class MacOSLifecycleTests(unittest.TestCase):
    def test_frozen_startup_uses_bundled_json_without_modal_picker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / "RF Map Viewer.app" / "Contents"
            executable = contents / "MacOS" / "RF Map Viewer"
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


class WindowPairingTests(unittest.TestCase):
    class FakeVar:
        def __init__(self, value) -> None:
            self.value = value

        def get(self):
            return self.value

        def set(self, value) -> None:
            self.value = value

    class FakeToggle:
        def __init__(self) -> None:
            self.states = []

        def state(self, values) -> None:
            self.states.append(values)

    class FakeLabel:
        def __init__(self) -> None:
            self.text = ""

        def configure(self, **kwargs) -> None:
            self.text = kwargs["text"]

    @staticmethod
    def bind(viewer, *method_names: str) -> None:
        for method_name in method_names:
            setattr(
                viewer,
                method_name,
                MethodType(getattr(gui.RFMViewer, method_name), viewer),
            )

    @classmethod
    def window(cls, units: list[int], *, ready: bool = True):
        return SimpleNamespace(
            _viewer_ready=ready,
            data=SimpleNamespace(unit_pool=list(units)),
            pair_windows_var=cls.FakeVar(False),
            pair_windows_toggle=cls.FakeToggle(),
            pair_status_label=cls.FakeLabel(),
            _pair_last_local_state=None,
        )

    @staticmethod
    def state(**overrides) -> gui.ViewerSyncState:
        values = {
            "unit_id": 20,
            "value_mode": gui.VALUE_MODE_COUNT,
            "timeline_bin_center_ms": 50.0,
            "timeline_selection_start_ms": 20.0,
            "timeline_selection_end_ms": 80.0,
            "timeline_anchor_center_ms": 80.0,
            "rf_start_ms": 0.0,
            "rf_end_ms": 20.0,
            "time_resolution_ms": 10.0,
            "x_bins": 6,
            "y_bins": 5,
            "smooth_radius": 1,
            "flip_y": False,
            "palette": "Gray",
            "polar_radius": gui.POLAR_RADIUS_MODES[1],
            "polar_layout": False,
            "rgb_mode": False,
            "selected_cell_y_midpoint": 4.0,
            "selected_cell_x_midpoint": 5.0,
            "timeline_scroll_fraction": 0.25,
            "selected_tab": "timeline",
        }
        values.update(overrides)
        return gui.ViewerSyncState(**values)

    def test_pairing_status_warns_but_allows_different_unit_lists(self) -> None:
        first = self.window([10, 20])
        loading = self.window([10, 20], ready=False)
        root = SimpleNamespace(
            _rfm_viewer_windows=[first, loading],
            _rfm_pairing_enabled=False,
        )
        first._app_root = root
        loading._app_root = root
        self.bind(first, "_ready_pairing_viewers", "_pairing_eligibility")
        first._unit_lists_match = gui.RFMViewer._unit_lists_match

        gui.RFMViewer._refresh_pairing_controls(first)
        self.assertEqual(
            first.pair_status_label.text,
            "Open another loaded viewer window to enable sync.",
        )
        self.assertEqual(first.pair_windows_toggle.states[-1], ["disabled"])

        second = self.window([10, 20])
        second._app_root = root
        root._rfm_viewer_windows.append(second)
        gui.RFMViewer._refresh_pairing_controls(first)
        self.assertEqual(
            first.pair_status_label.text,
            "2 loaded windows have matching unit lists.",
        )
        self.assertEqual(second.pair_windows_toggle.states[-1], ["!disabled"])

        second.data.unit_pool = [20, 10]
        gui.RFMViewer._refresh_pairing_controls(first)
        self.assertIn("Unit lists differ", first.pair_status_label.text)
        self.assertEqual(first.pair_windows_toggle.states[-1], ["!disabled"])

        second.data.unit_pool = [10, 20]
        root._rfm_pairing_enabled = True
        gui.RFMViewer._refresh_pairing_controls(first)
        self.assertEqual(
            second.pair_status_label.text,
            "2 windows paired. Changes in any paired window sync to the others.",
        )

        second.data.unit_pool = [20, 30]
        gui.RFMViewer._refresh_pairing_controls(first)
        self.assertEqual(
            first.pair_status_label.text,
            "2 windows paired. Unit lists differ; these files may be from different "
            "sessions. Missing units display N/A.",
        )
        self.assertEqual(first.pair_windows_toggle.states[-1], ["!disabled"])

    def test_paired_navigation_uses_sorted_union_and_marks_missing_unit(self) -> None:
        main = self.window([1, 3, 5, 7])
        sync_one = self.window([1, 3, 5, 6, 7])
        sync_two = self.window([2, 3, 5, 7])
        root = SimpleNamespace(
            _rfm_viewer_windows=[main, sync_one, sync_two],
            _rfm_pairing_enabled=True,
        )
        for window in root._rfm_viewer_windows:
            window._app_root = root

        main.data.n_units = len(main.data.unit_pool)
        main.unit_idx = self.FakeVar(0)
        main._selected_unit_id = 1
        main._last_supported_unit_id = 1
        main.selected_cell = None
        main._update_all = mock.Mock()
        main._publish_pairing_state_if_changed = mock.Mock()
        self.bind(
            main,
            "_ready_pairing_viewers",
            "_pairing_eligibility",
            "_pairing_unit_ids",
            "_unit_navigation_ids",
            "_local_unit_index",
            "_selected_unit_id_value",
            "_set_selected_unit_id",
        )

        self.assertEqual(main._pairing_unit_ids(), [1, 2, 3, 5, 6, 7])
        gui.RFMViewer._step_unit(main, 1)
        self.assertEqual(main._selected_unit_id, 2)
        self.assertEqual(main.unit_idx.get(), -1)
        gui.RFMViewer._step_unit(main, 1)
        self.assertEqual(main._selected_unit_id, 3)
        self.assertEqual(main.unit_idx.get(), 1)

    def test_pairing_state_maps_unit_id_instead_of_local_index(self) -> None:
        incoming = self.state(unit_id=20)
        viewer = SimpleNamespace(
            _viewer_ready=True,
            _pair_apply_in_progress=False,
            data=SimpleNamespace(unit_pool=[30, 20], n_units=2),
            unit_idx=self.FakeVar(0),
            _selected_unit_id=30,
            _last_supported_unit_id=30,
            _normalize_control_values=mock.Mock(),
            _timeline_preview_cache_key="cached",
            _timeline_preview_images={1: object()},
            _update_all=mock.Mock(),
            _capture_pairing_state=mock.Mock(return_value=incoming),
            _pair_last_local_state=None,
        )
        self.bind(
            viewer,
            "_local_unit_index",
            "_selected_unit_id_value",
            "_set_selected_unit_id",
        )

        gui.RFMViewer._apply_pairing_state(viewer, incoming, frozenset({"unit"}))
        self.assertEqual(viewer._selected_unit_id, 20)
        self.assertEqual(viewer.unit_idx.get(), 1)

        missing = replace(incoming, unit_id=99)
        viewer._capture_pairing_state.return_value = missing
        gui.RFMViewer._apply_pairing_state(viewer, missing, frozenset({"unit"}))
        self.assertEqual(viewer._selected_unit_id, 99)
        self.assertEqual(viewer.unit_idx.get(), -1)

    def test_removed_only_selected_unit_advances_to_next_union_id(self) -> None:
        canonical = self.state(unit_id=2)
        first = self.window([1, 3])
        removed = self.window([2, 3])
        third = self.window([3, 5])
        root = SimpleNamespace(
            _rfm_viewer_windows=[first, removed, third],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=canonical,
            _rfm_pairing_broadcasting=False,
        )
        for window in root._rfm_viewer_windows:
            window._app_root = root
            window._apply_pairing_state = mock.Mock()
        first._refresh_pairing_controls = mock.Mock()
        self.bind(
            first,
            "_ready_pairing_viewers",
            "_pairing_eligibility",
            "_pairing_unit_ids",
            "_disable_window_pairing",
        )
        first._next_union_unit_id = gui.RFMViewer._next_union_unit_id

        root._rfm_viewer_windows.remove(removed)
        gui.RFMViewer._pair_ready_viewer_set_changed(first)

        self.assertEqual(root._rfm_pairing_state.unit_id, 3)
        first._apply_pairing_state.assert_called_once_with(
            root._rfm_pairing_state,
            frozenset({"unit"}),
        )
        third._apply_pairing_state.assert_called_once_with(
            root._rfm_pairing_state,
            frozenset({"unit"}),
        )

    def test_reloaded_adopted_window_receives_full_state_when_unit_is_normalized(self) -> None:
        canonical = self.state(unit_id=2, palette="Inferno", rf_end_ms=200.0)
        first = self.window([1, 3])
        reloaded = self.window([3, 5])
        root = SimpleNamespace(
            _rfm_viewer_windows=[first, reloaded],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=canonical,
            _rfm_pairing_broadcasting=False,
        )
        for window in root._rfm_viewer_windows:
            window._app_root = root
            window._apply_pairing_state = mock.Mock()
        first._refresh_pairing_controls = mock.Mock()
        self.bind(
            first,
            "_ready_pairing_viewers",
            "_pairing_eligibility",
            "_pairing_unit_ids",
            "_disable_window_pairing",
        )
        first._next_union_unit_id = gui.RFMViewer._next_union_unit_id

        gui.RFMViewer._pair_ready_viewer_set_changed(first, adopt_viewer=reloaded)

        normalized = root._rfm_pairing_state
        self.assertEqual(normalized.unit_id, 3)
        self.assertEqual(normalized.palette, "Inferno")
        self.assertEqual(normalized.rf_end_ms, 200.0)
        first._apply_pairing_state.assert_called_once_with(
            normalized,
            frozenset({"unit"}),
        )
        reloaded._apply_pairing_state.assert_called_once_with(normalized)

    def test_leaving_pairing_restores_last_supported_local_unit(self) -> None:
        viewer = SimpleNamespace(
            _viewer_ready=False,
            data=SimpleNamespace(unit_pool=[1, 3], n_units=2),
            unit_idx=self.FakeVar(-1),
            _selected_unit_id=2,
            _last_supported_unit_id=1,
        )
        self.bind(
            viewer,
            "_local_unit_index",
            "_selected_unit_id_value",
            "_selected_local_unit_index",
            "_set_selected_unit_id",
        )

        gui.RFMViewer._restore_local_unit_selection(viewer)

        self.assertEqual(viewer._selected_unit_id, 1)
        self.assertEqual(viewer.unit_idx.get(), 0)

    def test_enabling_uses_toggling_window_as_initial_source(self) -> None:
        state = self.state()
        source = self.window([10, 20])
        target = self.window([10, 20])
        root = SimpleNamespace(
            _rfm_viewer_windows=[source, target],
            _rfm_pairing_enabled=False,
            _rfm_pairing_state=None,
            _rfm_pairing_broadcasting=False,
        )
        source._app_root = target._app_root = root
        source.pair_windows_var.set(True)
        source._capture_pairing_state = mock.Mock(return_value=state)
        source._refresh_pairing_controls = mock.Mock()
        target._apply_pairing_state = mock.Mock()
        self.bind(source, "_ready_pairing_viewers", "_pairing_eligibility")

        gui.RFMViewer._on_pair_windows_toggled(source)

        self.assertTrue(root._rfm_pairing_enabled)
        self.assertEqual(root._rfm_pairing_state, state)
        self.assertEqual(source._pair_last_local_state, state)
        target._apply_pairing_state.assert_called_once_with(state)
        self.assertFalse(root._rfm_pairing_broadcasting)

    def test_new_window_adopts_canonical_even_when_unit_is_missing(self) -> None:
        canonical = self.state()
        first = self.window([10, 20])
        newcomer = self.window([10, 20])
        root = SimpleNamespace(
            _rfm_viewer_windows=[first, newcomer],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=canonical,
            _rfm_pairing_broadcasting=False,
        )
        first._app_root = newcomer._app_root = root
        newcomer._apply_pairing_state = mock.Mock()
        first._refresh_pairing_controls = mock.Mock()
        self.bind(
            first,
            "_ready_pairing_viewers",
            "_pairing_eligibility",
            "_pairing_unit_ids",
            "_disable_window_pairing",
        )
        first._next_union_unit_id = gui.RFMViewer._next_union_unit_id

        gui.RFMViewer._pair_ready_viewer_set_changed(first, adopt_viewer=newcomer)
        newcomer._apply_pairing_state.assert_called_once_with(canonical)
        self.assertTrue(root._rfm_pairing_enabled)

        newcomer._apply_pairing_state.reset_mock()
        newcomer.data.unit_pool = [10, 99]
        gui.RFMViewer._pair_ready_viewer_set_changed(first, adopt_viewer=newcomer)
        newcomer._apply_pairing_state.assert_called_once_with(canonical)
        self.assertTrue(root._rfm_pairing_enabled)
        self.assertEqual(root._rfm_pairing_state, canonical)

    def test_pairing_survives_three_to_two_closure_but_not_two_to_one(self) -> None:
        canonical = self.state()
        first, second, third = (self.window([10, 20]) for _ in range(3))
        root = SimpleNamespace(
            _rfm_viewer_windows=[first, second, third],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=canonical,
            _rfm_pairing_broadcasting=False,
        )
        first._app_root = second._app_root = third._app_root = root
        first._refresh_pairing_controls = mock.Mock()
        self.bind(
            first,
            "_ready_pairing_viewers",
            "_pairing_eligibility",
            "_pairing_unit_ids",
            "_disable_window_pairing",
        )
        first._next_union_unit_id = gui.RFMViewer._next_union_unit_id

        root._rfm_viewer_windows.remove(third)
        gui.RFMViewer._pair_ready_viewer_set_changed(first)
        self.assertTrue(root._rfm_pairing_enabled)

        root._rfm_viewer_windows.remove(second)
        gui.RFMViewer._pair_ready_viewer_set_changed(first)
        self.assertFalse(root._rfm_pairing_enabled)

    def test_clamped_peer_palette_change_does_not_overwrite_canonical_ranges(self) -> None:
        canonical = self.state()
        clamped_baseline = self.state(
            timeline_bin_center_ms=10.0,
            timeline_selection_start_ms=0.0,
            timeline_selection_end_ms=20.0,
            timeline_anchor_center_ms=20.0,
            rf_start_ms=-10.0,
            rf_end_ms=10.0,
            selected_cell_y_midpoint=1.0,
            selected_cell_x_midpoint=1.0,
        )
        changed = replace(clamped_baseline, palette="Inferno")
        source = self.window([10, 20])
        target = self.window([10, 20])
        root = SimpleNamespace(
            _rfm_viewer_windows=[source, target],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=canonical,
            _rfm_pairing_broadcasting=False,
        )
        source._app_root = target._app_root = root
        source._pair_last_local_state = clamped_baseline
        source._capture_pairing_state = mock.Mock(return_value=changed)
        target._apply_pairing_state = mock.Mock()
        target._apply_pairing_scroll_fraction = mock.Mock()
        self.bind(source, "_ready_pairing_viewers", "_pairing_eligibility")

        gui.RFMViewer._publish_pairing_state_if_changed(source)

        self.assertEqual(root._rfm_pairing_state.palette, "Inferno")
        self.assertEqual(
            root._rfm_pairing_state.timeline_selection_start_ms,
            canonical.timeline_selection_start_ms,
        )
        self.assertEqual(
            root._rfm_pairing_state.timeline_bin_center_ms,
            canonical.timeline_bin_center_ms,
        )
        self.assertEqual(root._rfm_pairing_state.rf_start_ms, canonical.rf_start_ms)
        self.assertEqual(
            root._rfm_pairing_state.selected_cell_y_midpoint,
            canonical.selected_cell_y_midpoint,
        )
        target._apply_pairing_state.assert_called_once_with(
            changed,
            frozenset({"palette"}),
        )
        target._apply_pairing_scroll_fraction.assert_not_called()

    def test_scroll_only_change_uses_direct_peer_fast_path(self) -> None:
        baseline = self.state(timeline_scroll_fraction=0.1)
        changed = replace(baseline, timeline_scroll_fraction=0.7)
        source = self.window([10, 20])
        target = self.window([10, 20])
        root = SimpleNamespace(
            _rfm_viewer_windows=[source, target],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=baseline,
            _rfm_pairing_broadcasting=False,
        )
        source._app_root = target._app_root = root
        source._pair_last_local_state = baseline
        source._capture_pairing_state = mock.Mock(return_value=changed)
        target._apply_pairing_state = mock.Mock()
        target._apply_pairing_scroll_fraction = mock.Mock()
        self.bind(source, "_ready_pairing_viewers", "_pairing_eligibility")

        gui.RFMViewer._publish_pairing_state_if_changed(source)

        target._apply_pairing_scroll_fraction.assert_called_once_with(0.7)
        target._apply_pairing_state.assert_not_called()
        self.assertEqual(root._rfm_pairing_state.timeline_scroll_fraction, 0.7)

    def test_root_broadcast_guard_prevents_feedback_loop(self) -> None:
        source = self.window([10, 20])
        root = SimpleNamespace(
            _rfm_viewer_windows=[source],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=self.state(),
            _rfm_pairing_broadcasting=True,
        )
        source._app_root = root
        source._capture_pairing_state = mock.Mock()

        gui.RFMViewer._publish_pairing_state_if_changed(source)

        source._capture_pairing_state.assert_not_called()

    def test_direct_scroll_apply_never_redraws(self) -> None:
        baseline = self.state(timeline_scroll_fraction=0.1)
        canvas = mock.Mock()
        canvas.yview.return_value = (0.55, 1.0)
        viewer = SimpleNamespace(
            _viewer_ready=True,
            _pair_apply_in_progress=False,
            _timeline_scroll_fraction=0.1,
            _restoring_timeline_scroll=False,
            _pair_last_local_state=baseline,
            canvases={"timeline": canvas},
            _capture_pairing_state=mock.Mock(),
            _update_all=mock.Mock(),
        )

        gui.RFMViewer._apply_pairing_scroll_fraction(viewer, 0.6)

        canvas.yview_moveto.assert_called_once_with(0.33)
        viewer._update_all.assert_not_called()
        viewer._capture_pairing_state.assert_not_called()
        self.assertEqual(viewer._pair_last_local_state.timeline_scroll_fraction, 0.6)

    def test_exact_bottom_timeline_scroll_is_remembered(self) -> None:
        canvas = mock.Mock()
        canvas.yview.return_value = (0.72, 1.0)
        viewer = SimpleNamespace(
            _restoring_timeline_scroll=False,
            _timeline_scroll_fraction=0.1,
            canvases={"timeline": canvas},
        )

        gui.RFMViewer._timeline_scroll_set(viewer, "0.72", "1.0")
        self.assertEqual(viewer._timeline_scroll_fraction, 1.0)

        viewer._timeline_scroll_fraction = 0.1
        gui.RFMViewer._remember_timeline_scroll(viewer)
        self.assertEqual(viewer._timeline_scroll_fraction, 1.0)

    def test_scroll_progress_is_independent_of_viewport_span(self) -> None:
        self.assertEqual(gui.timeline_scroll_progress(0.375, 0.625), 0.5)

        short_viewport = mock.Mock()
        short_viewport.yview.return_value = (0.0, 0.2)
        tall_viewport = mock.Mock()
        tall_viewport.yview.return_value = (0.0, 0.5)
        short_viewer = SimpleNamespace(
            canvases={"timeline": short_viewport},
            _timeline_scroll_fraction=0.5,
            _restoring_timeline_scroll=False,
        )
        tall_viewer = SimpleNamespace(
            canvases={"timeline": tall_viewport},
            _timeline_scroll_fraction=0.5,
            _restoring_timeline_scroll=False,
        )

        gui.RFMViewer._restore_timeline_scroll(short_viewer)
        gui.RFMViewer._restore_timeline_scroll(tall_viewer)

        short_viewport.yview_moveto.assert_called_once_with(0.4)
        tall_viewport.yview_moveto.assert_called_once_with(0.25)

    def test_unscrollable_timeline_preserves_desired_progress(self) -> None:
        canvas = mock.Mock()
        canvas.yview.return_value = (0.0, 1.0)
        viewer = SimpleNamespace(
            _restoring_timeline_scroll=False,
            _timeline_scroll_fraction=0.65,
            canvases={"timeline": canvas},
        )

        gui.RFMViewer._timeline_scroll_set(viewer, "0.0", "1.0")
        gui.RFMViewer._remember_timeline_scroll(viewer)
        gui.RFMViewer._restore_timeline_scroll(viewer)

        self.assertEqual(viewer._timeline_scroll_fraction, 0.65)
        canvas.yview_moveto.assert_not_called()

    def test_physical_time_and_cell_midpoints_map_to_target_groups(self) -> None:
        bounds = [(-40.0, -20.0), (-20.0, 0.0), (0.0, 20.0), (20.0, 40.0)]
        viewer = SimpleNamespace(
            _time_groups=lambda: [(0, 1), (2, 3), (4, 5), (6, 7)],
            _time_group_bounds_ms=lambda index: bounds[index],
        )
        viewer._time_group_index_for_ms = MethodType(
            gui.RFMViewer._time_group_index_for_ms, viewer
        )

        self.assertEqual(gui.RFMViewer._time_group_index_for_ms(viewer, 12.0), 2)
        self.assertEqual(gui.RFMViewer._time_group_index_for_ms(viewer, 100.0), 3)
        self.assertEqual(gui.RFMViewer._time_group_range_for_ms(viewer, -5.0, 25.0), (1, 3))

        viewer._display_y_groups = lambda: [(4, 5), (2, 3), (0, 1)]
        viewer._x_groups = lambda: [(0, 2), (3, 5)]
        viewer._axis_group_for_midpoint = gui.RFMViewer._axis_group_for_midpoint
        self.assertEqual(
            gui.RFMViewer._cell_for_pairing_midpoint(viewer, 2.6, 4.2),
            (2, 3, 3, 5),
        )
        self.assertEqual(
            gui.RFMViewer._cell_for_pairing_midpoint(viewer, 99.0, -99.0),
            (4, 5, 0, 2),
        )

    def test_partial_apply_changes_only_requested_fields(self) -> None:
        incoming = self.state(palette="Viridis")
        viewer = SimpleNamespace(
            _viewer_ready=True,
            _pair_apply_in_progress=False,
            palette_var=self.FakeVar("Gray"),
            _normalize_control_values=mock.Mock(),
            _timeline_preview_cache_key="cached",
            _timeline_preview_images={1: object()},
            _update_all=mock.Mock(),
            _capture_pairing_state=mock.Mock(return_value=incoming),
            _pair_last_local_state=None,
        )

        gui.RFMViewer._apply_pairing_state(viewer, incoming, frozenset({"palette"}))

        self.assertEqual(viewer.palette_var.get(), "Viridis")
        viewer._normalize_control_values.assert_called_once_with()
        viewer._update_all.assert_called_once_with()
        self.assertFalse(viewer._pair_apply_in_progress)

    def test_time_resolution_delta_preserves_peer_local_physical_timeline(self) -> None:
        incoming = self.state(time_resolution_ms=20.0)
        time_resolution = self.FakeVar("10")
        bin_var = self.FakeVar(2)
        range_start = self.FakeVar(2)
        range_end = self.FakeVar(3)

        def bounds(index: int) -> tuple[float, float]:
            size = float(time_resolution.get())
            return index * size, (index + 1) * size

        viewer = SimpleNamespace(
            _viewer_ready=True,
            _pair_apply_in_progress=False,
            time_res_ms_var=time_resolution,
            bin_var=bin_var,
            range_start_var=range_start,
            range_end_var=range_end,
            _timeline_range_anchor=3,
            _time_group_center_ms=lambda index: sum(bounds(index)) / 2.0,
            _timeline_selected_time_bounds_ms=lambda: (
                bounds(min(range_start.get(), range_end.get()))[0],
                bounds(max(range_start.get(), range_end.get()))[1],
            ),
            _time_group_index_for_ms=lambda value: max(
                0, min(4, int(float(value) // float(time_resolution.get())))
            ),
            _time_group_range_for_ms=lambda start, end: (
                max(0, min(4, int(float(start) // float(time_resolution.get())))),
                max(
                    0,
                    min(
                        4,
                        int((float(end) - 1e-9) // float(time_resolution.get())),
                    ),
                ),
            ),
            _normalize_control_values=mock.Mock(),
            _timeline_preview_cache_key="cached",
            _timeline_preview_images={1: object()},
            _update_all=mock.Mock(),
            _capture_pairing_state=mock.Mock(return_value=incoming),
            _pair_last_local_state=None,
        )

        gui.RFMViewer._apply_pairing_state(
            viewer,
            incoming,
            frozenset({"time_resolution"}),
        )

        self.assertEqual(float(time_resolution.get()), 20.0)
        self.assertEqual(bin_var.get(), 1)
        self.assertEqual((range_start.get(), range_end.get()), (1, 1))
        self.assertEqual(viewer._timeline_range_anchor, 1)

    def test_spatial_bin_delta_remaps_peer_local_selected_midpoint(self) -> None:
        incoming = self.state(x_bins=2, y_bins=2)
        viewer = SimpleNamespace(
            _viewer_ready=True,
            _pair_apply_in_progress=False,
            data=SimpleNamespace(n_x=6, n_y=6),
            x_bins_var=self.FakeVar(6),
            y_bins_var=self.FakeVar(6),
            selected_cell=(2, 3, 4, 5),
            _normalize_control_values=mock.Mock(),
            _display_y_groups=lambda: gui.axis_groups_for_target(6, 2),
            _x_groups=lambda: gui.axis_groups_for_target(6, 2),
            _axis_group_for_midpoint=gui.RFMViewer._axis_group_for_midpoint,
            _timeline_preview_cache_key="cached",
            _timeline_preview_images={1: object()},
            _update_all=mock.Mock(),
            _capture_pairing_state=mock.Mock(return_value=incoming),
            _pair_last_local_state=None,
        )
        viewer._cell_for_pairing_midpoint = MethodType(
            gui.RFMViewer._cell_for_pairing_midpoint, viewer
        )

        gui.RFMViewer._apply_pairing_state(
            viewer,
            incoming,
            frozenset({"x_bins", "y_bins"}),
        )

        self.assertEqual(viewer.selected_cell, (3, 5, 3, 5))

    def test_control_normalization_keeps_selected_cell_in_current_groups(self) -> None:
        viewer = SimpleNamespace(
            bin_var=self.FakeVar(0),
            range_start_var=self.FakeVar(0),
            range_end_var=self.FakeVar(0),
            _timeline_range_anchor=None,
            selected_cell=(2, 3, 4, 5),
            _time_groups=mock.Mock(return_value=[(0, 0)]),
            _source_bins_for_time_controls=mock.Mock(),
            _x_target_bins=mock.Mock(return_value=2),
            _y_target_bins=mock.Mock(return_value=2),
            _smooth_radius=mock.Mock(return_value=0),
            _sync_time_control_ranges=mock.Mock(),
            _cell_for_pairing_midpoint=mock.Mock(return_value=(3, 5, 3, 5)),
        )

        gui.RFMViewer._normalize_control_values(viewer)

        viewer._cell_for_pairing_midpoint.assert_called_once_with(2.5, 4.5)
        self.assertEqual(viewer.selected_cell, (3, 5, 3, 5))


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
