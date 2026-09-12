import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import rfmapping_gui as gui
import rfmapping_viewer.companions as companions_module
import rfmapping_viewer.constants as constants_module
import rfmapping_viewer.rf_model as rf_model_module
import rfmapping_viewer.settings as settings_module
from gui_test_support import base_payload


class UnitFilterSettingsTests(unittest.TestCase):
    def test_rf_subtraction_defaults_round_trip_and_validate_ranges(self) -> None:
        defaults = settings_module.ViewerSettings()
        self.assertEqual((defaults.rf_difference_start_ms, defaults.rf_difference_end_ms), (80, 160))
        self.assertEqual((defaults.rf_subtract_start_ms, defaults.rf_subtract_end_ms), (0, 80))
        custom = replace(defaults, rf_subtract=True, rf_difference_start_ms=40, rf_difference_end_ms=120, rf_subtract_start_ms=-40, rf_subtract_end_ms=40)
        self.assertEqual(settings_module.ViewerSettings.from_mapping(custom.to_mapping()), custom)
        for changes in (
            {"rf_difference_start_ms": 200, "rf_difference_end_ms": 100},
            {"rf_subtract_start_ms": 100, "rf_subtract_end_ms": 0},
            {"rf_subtract": "yes", "rf_subtract_start_ms": float("nan")},
        ):
            self.assertEqual(settings_module.ViewerSettings.from_mapping(changes), defaults)

    def test_defaults_are_enabled_at_one_bin_and_round_trip(self) -> None:
        defaults = settings_module.ViewerSettings()

        self.assertTrue(defaults.rf_filter_units_with_zero_bins)
        self.assertEqual(defaults.rf_zero_bin_threshold, 1)
        self.assertEqual(defaults.rf_value_mode, constants_module.VALUE_MODE_RATE)
        restored = settings_module.ViewerSettings.from_mapping(defaults.to_mapping())
        self.assertEqual(restored, defaults)

    def test_removed_or_invalid_value_mode_falls_back_to_rate(self) -> None:
        restored = settings_module.ViewerSettings.from_mapping(
            {
                "schema_version": constants_module.SETTINGS_SCHEMA_VERSION,
                "rf_value_mode": "Spikes / presentation",
            }
        )

        self.assertEqual(restored.rf_value_mode, constants_module.VALUE_MODE_RATE)

    def test_invalid_persisted_filter_fields_fall_back_independently(self) -> None:
        defaults = settings_module.ViewerSettings()
        restored = settings_module.ViewerSettings.from_mapping(
            {
                "schema_version": constants_module.SETTINGS_SCHEMA_VERSION,
                "rf_filter_units_with_zero_bins": "yes",
                "rf_zero_bin_threshold": 0,
            }
        )

        self.assertEqual(
            restored.rf_filter_units_with_zero_bins,
            defaults.rf_filter_units_with_zero_bins,
        )
        self.assertEqual(restored.rf_zero_bin_threshold, 1)


class TuningCurveSessionSettingsTests(unittest.TestCase):
    def test_session_defaults_to_one_and_round_trips(self) -> None:
        defaults = settings_module.ViewerSettings()
        self.assertEqual(defaults.tuning_curve_session, 1)

        restored = settings_module.ViewerSettings.from_mapping(
            replace(defaults, tuning_curve_session=4).to_mapping()
        )
        self.assertEqual(restored.tuning_curve_session, 4)

    def test_invalid_persisted_sessions_fall_back_to_one(self) -> None:
        for invalid in (0, -1, True, "2", 1.5):
            with self.subTest(session=invalid):
                restored = settings_module.ViewerSettings.from_mapping(
                    {
                        "schema_version": constants_module.SETTINGS_SCHEMA_VERSION,
                        "tuning_curve_session": invalid,
                    }
                )
                self.assertEqual(restored.tuning_curve_session, 1)

    def test_optional_worker_parses_only_selected_session_without_stale_publish(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rf_path = (
                root
                / "260730_3"
                / "data"
                / "rfmapping"
                / "ProbeA"
                / "map.json"
            )
            rf_path.parent.mkdir(parents=True)
            rf_path.write_text(json.dumps(base_payload()), encoding="utf-8")
            tuning_paths = []
            for session in (1, 2):
                tuning_path = (
                    root
                    / f"260730_{session}"
                    / "data"
                    / "tuning_curves"
                    / "ProbeA"
                    / "tuning_curves.json"
                )
                tuning_path.parent.mkdir(parents=True)
                tuning_path.write_text(
                    json.dumps({"42": [float(session)] * constants_module.HD_RAW_BIN_COUNT}),
                    encoding="utf-8",
                )
                tuning_paths.append(tuning_path.resolve())

            data = rf_model_module.RFMappingData(rf_path)
            result_queue = gui.queue.SimpleQueue()
            worker = SimpleNamespace(
                data=data,
                _optional_result_queue=result_queue,
            )
            snapshot = {
                "generation": 7,
                "data": data,
                "data_path": data.path,
                "load_probe": False,
                "load_tuning": True,
                "tuning_session": 2,
                "cluster_id": 42,
                "tuning_bins": 30,
                "tuning_smoothing": False,
                "tuning_sigma": 1.5,
            }

            gui.RFMViewer._optional_autoload_worker(worker, snapshot)
            result = result_queue.get_nowait()

            self.assertEqual(result["tuning_path"], tuning_paths[1])
            self.assertIsInstance(result["tuning_data"], companions_module.TuningCurveData)
            self.assertEqual(result["tuning_data"].rates_for(42)[0], 2.0)
            self.assertIsNone(data._hd_tuning)
            self.assertFalse(data._hd_tuning_checked)


class WaveformSettingsTests(unittest.TestCase):
    def test_waveform_visibility_and_channel_mode_round_trip(self) -> None:
        for mode in constants_module.WAVEFORM_CHANNEL_MODES:
            with self.subTest(mode=mode):
                settings = replace(
                    settings_module.ViewerSettings(),
                    show_waveform=False,
                    waveform_channel_mode=mode,
                )
                serialized = settings.to_mapping()

                self.assertIs(serialized["show_waveform"], False)
                self.assertEqual(serialized["waveform_channel_mode"], mode)
                restored = settings_module.ViewerSettings.from_mapping(serialized)
                self.assertFalse(restored.show_waveform)
                self.assertEqual(restored.waveform_channel_mode, mode)

    def test_invalid_waveform_channel_mode_falls_back_independently(self) -> None:
        defaults = settings_module.ViewerSettings()
        restored = settings_module.ViewerSettings.from_mapping(
            {
                "schema_version": constants_module.SETTINGS_SCHEMA_VERSION,
                "default_viewer_tab": "waveform",
                "waveform_channel_mode": "same_y_row",
            }
        )

        self.assertEqual(restored.waveform_channel_mode, defaults.waveform_channel_mode)
        self.assertEqual(restored.default_viewer_tab, "rf")

    def test_invalid_waveform_visibility_falls_back_without_resetting_mode(self) -> None:
        defaults = settings_module.ViewerSettings()
        restored = settings_module.ViewerSettings.from_mapping(
            {
                "schema_version": constants_module.SETTINGS_SCHEMA_VERSION,
                "show_waveform": "yes",
                "waveform_channel_mode": "same_shank",
            }
        )

        self.assertEqual(restored.show_waveform, defaults.show_waveform)
        self.assertEqual(restored.waveform_channel_mode, "same_shank")


class ViewerSettingsTests(unittest.TestCase):
    def test_platform_settings_paths_use_native_locations_and_fallbacks(self) -> None:
        home = Path("/Users/tester")
        self.assertEqual(
            settings_module.viewer_settings_path(platform="darwin", environ={}, home=home),
            home / "Library" / "Application Support" / "RF Map Viewer" / "settings.json",
        )
        self.assertEqual(
            settings_module.viewer_settings_path(
                platform="win32",
                environ={"APPDATA": r"C:\Users\tester\AppData\Roaming"},
                home=Path(r"C:\Users\tester"),
            ),
            Path(r"C:\Users\tester\AppData\Roaming") / "RF Map Viewer" / "settings.json",
        )
        self.assertEqual(
            settings_module.viewer_settings_path(platform="win32", environ={}, home=home),
            home / "AppData" / "Roaming" / "RF Map Viewer" / "settings.json",
        )
        self.assertEqual(
            settings_module.viewer_settings_path(
                platform="linux",
                environ={"XDG_CONFIG_HOME": "/var/config/tester"},
                home=home,
            ),
            Path("/var/config/tester/rf-map-viewer/settings.json"),
        )
        self.assertEqual(
            settings_module.viewer_settings_path(platform="linux", environ={}, home=home),
            home / ".config" / "rf-map-viewer" / "settings.json",
        )

    def test_settings_save_and_load_round_trip_through_default_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "settings.json"
            settings = replace(
                settings_module.ViewerSettings(),
                show_tuning_curve=False,
                rf_sum_start_ms=-50.0,
                rf_sum_end_ms=125.0,
                rf_palette="Inferno",
                tuning_plot_mode="Line",
                tuning_layout="Stacked",
                tuning_display_bins=36,
                tuning_smoothing=False,
                tuning_compare_scale=True,
            )
            with mock.patch.object(settings_module, "viewer_settings_path", return_value=path):
                written = settings_module.save_viewer_settings(settings)
                loaded = settings_module.load_viewer_settings()

            self.assertEqual(written, path)
            self.assertEqual(loaded, settings)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1)
            self.assertTrue(
                json.loads(path.read_text(encoding="utf-8"))["tuning_compare_scale"]
            )
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_missing_malformed_and_unknown_schema_settings_use_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.assertEqual(settings_module.load_viewer_settings(path), settings_module.ViewerSettings())

            path.write_text("not JSON", encoding="utf-8")
            self.assertEqual(settings_module.load_viewer_settings(path), settings_module.ViewerSettings())

            path.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")
            self.assertEqual(settings_module.load_viewer_settings(path), settings_module.ViewerSettings())

            path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
            self.assertEqual(settings_module.load_viewer_settings(path), settings_module.ViewerSettings())

    def test_invalid_settings_fall_back_per_field_without_discarding_valid_fields(self) -> None:
        defaults = settings_module.ViewerSettings()
        settings = settings_module.ViewerSettings.from_mapping(
            {
                "schema_version": constants_module.SETTINGS_SCHEMA_VERSION,
                "show_tuning_curve": 1,
                "auto_load_tuning_curve": False,
                "show_waveform": "yes",
                "show_probe_layout": "yes",
                "auto_load_probe_layout": False,
                "rf_sum_start_ms": 20,
                "rf_sum_end_ms": 10,
                "rf_time_resolution_ms": 0,
                "rf_value_mode": "unsupported",
                "rf_x_bins": -2,
                "rf_y_bins": 17,
                "rf_smooth_radius": 50,
                "rf_flip_y": True,
                "rf_palette": "Viridis",
                "rf_polar_radius": "unsupported",
                "rf_polar_layout": True,
                "rf_rgb_mode": "yes",
                "default_viewer_tab": "settings",
                "tuning_plot_mode": "Radar",
                "tuning_layout": "Diagonal",
                "tuning_display_bins": 8,
                "tuning_smoothing": False,
                "tuning_smooth_sigma": math.inf,
                "tuning_compare_scale": "yes",
            }
        )

        self.assertEqual(settings.show_tuning_curve, defaults.show_tuning_curve)
        self.assertFalse(settings.auto_load_tuning_curve)
        self.assertEqual(settings.show_waveform, defaults.show_waveform)
        self.assertEqual(settings.show_probe_layout, defaults.show_probe_layout)
        self.assertFalse(settings.auto_load_probe_layout)
        self.assertEqual(
            (settings.rf_sum_start_ms, settings.rf_sum_end_ms),
            (defaults.rf_sum_start_ms, defaults.rf_sum_end_ms),
        )
        self.assertEqual(settings.rf_time_resolution_ms, defaults.rf_time_resolution_ms)
        self.assertEqual(settings.rf_value_mode, defaults.rf_value_mode)
        self.assertEqual(settings.rf_x_bins, 0)
        self.assertEqual(settings.rf_y_bins, 17)
        self.assertEqual(settings.rf_smooth_radius, 3)
        self.assertTrue(settings.rf_flip_y)
        self.assertEqual(settings.rf_palette, "Viridis")
        self.assertEqual(settings.rf_polar_radius, defaults.rf_polar_radius)
        self.assertTrue(settings.rf_polar_layout)
        self.assertEqual(settings.rf_rgb_mode, defaults.rf_rgb_mode)
        self.assertEqual(settings.default_viewer_tab, defaults.default_viewer_tab)
        self.assertEqual(settings.tuning_plot_mode, defaults.tuning_plot_mode)
        self.assertEqual(settings.tuning_layout, defaults.tuning_layout)
        self.assertEqual(settings.tuning_display_bins, 6)
        self.assertFalse(settings.tuning_smoothing)
        self.assertEqual(settings.tuning_smooth_sigma, defaults.tuning_smooth_sigma)
        self.assertEqual(settings.tuning_compare_scale, defaults.tuning_compare_scale)
