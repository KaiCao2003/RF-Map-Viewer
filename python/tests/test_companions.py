import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import rfmapping_gui as gui
import rfmapping_viewer.companions as companions_module
import rfmapping_viewer.constants as constants_module
import rfmapping_viewer.rf_model as rf_model_module
import rfmapping_viewer.settings as settings_module
from gui_test_support import base_payload


class ProbeGeometryTests(unittest.TestCase):
    def test_probe_name_supports_explicit_probe_tokens(self) -> None:
        self.assertEqual(
            companions_module.probe_name_for_rf(Path("regular_260615_3_-100_200_ProbeA.json")),
            "ProbeA",
        )
        self.assertEqual(companions_module.probe_name_for_rf(Path("session-ProbeB.json")), "ProbeB")
        self.assertEqual(companions_module.probe_name_for_rf(Path("session ProbeA/rf.json")), "ProbeA")
        self.assertIsNone(companions_module.probe_name_for_rf(Path("session/rf.json")))

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

            geometry = companions_module.load_probe_geometry("ProbeA", positions, channels)

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

            discovered = companions_module.discover_probe_geometry_paths(json_path)

            self.assertEqual(
                discovered,
                ("ProbeA", positions.resolve(), channels.resolve()),
            )
            assert discovered is not None
            geometry = companions_module.load_probe_geometry(*discovered)
            self.assertEqual(geometry.units[0].unit_id, 42)

    def test_bad_or_missing_geometry_is_nonfatal_during_discovery(self) -> None:
        for nested in (True, False):
            with self.subTest(nested=nested), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data_root = root / "data" if nested else root
                json_path = data_root / "rfmapping" / "ProbeA" / "regular.json" if nested else root / "regular_A.json"
                json_path.parent.mkdir(parents=True, exist_ok=True)
                json_path.write_text(json.dumps(base_payload()), encoding="utf-8")
                positions = data_root / "spike_position" / "ProbeA" / "positions.csv"
                positions.parent.mkdir(parents=True)
                positions.write_text("wrong,columns\n1,2\n", encoding="utf-8")
                data = rf_model_module.RFMappingData(json_path)

                self.assertIsNone(data.probe_geometry())
                self.assertIn("missing required columns", data.probe_geometry_error or "")

    def test_malformed_optional_channels_still_loads_unit_positions(self) -> None:
        for nested in (True, False):
            with self.subTest(nested=nested), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data_root = root / "data" if nested else root
                json_path = data_root / "rfmapping" / "ProbeA" / "regular.json" if nested else root / "regular_A.json"
                json_path.parent.mkdir(parents=True, exist_ok=True)
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

                discovered = companions_module.discover_probe_geometry_paths(json_path)

                assert discovered is not None
                geometry = companions_module.load_probe_geometry(*discovered)
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

            self.assertIsNone(companions_module.discover_probe_geometry_paths(json_path))


    def test_probe_name_supports_real_trailing_letter_filenames(self) -> None:
        self.assertEqual(
            companions_module.probe_name_for_rf(Path("regular_260615_3_-100_200_A.json")),
            "ProbeA",
        )
        self.assertEqual(companions_module.probe_name_for_rf(Path("session-B.json")), "ProbeB")
        self.assertEqual(companions_module.probe_name_for_rf(Path("session ProbeA/rf.json")), "ProbeA")
        self.assertIsNone(companions_module.probe_name_for_rf(Path("session/rf.json")))

    def test_csv_loading_and_region_filter_join_by_unit_id(self) -> None:
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

            geometry = companions_module.load_probe_geometry("ProbeA", positions, channels)

            self.assertEqual(geometry.channels_path, channels.resolve())
            self.assertEqual(
                (geometry.units[0].unit_id, geometry.units[0].x_um, geometry.units[0].y_um),
                (42, 10.0, 20.0),
            )
            region = companions_module.SpatialRegion.centered(0, 0)
            self.assertEqual((region.x_min, region.x_max), (-80.0, 80.0))
            self.assertEqual((region.y_min, region.y_max), (-37.5, 37.5))
            self.assertEqual(geometry.unit_ids_in_region(region, [99, 42, 7]), [42])
            self.assertTrue(region.contains(10, 20))
            self.assertFalse(region.contains(200, 300))

    def test_discovery_uses_recording_layout_and_environment_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "exports" / "regular_260615_3_A.json"
            json_path.parent.mkdir()
            json_path.write_text("{}", encoding="utf-8")
            positions = root / "spike_position" / "ProbeA" / "positions.csv"
            channels = root / "waveform" / "ProbeA" / "channels.csv"
            positions.parent.mkdir(parents=True)
            channels.parent.mkdir(parents=True)
            positions.write_text("unit_index,unit_id,x_um,y_um\n0,42,1,2\n", encoding="utf-8")
            channels.write_text(
                "channel_index,channel_id,raw_channel_index,x_um,y_um,shank_id\n0,1,1,3,4,0\n",
                encoding="utf-8",
            )

            with mock.patch.dict(gui.os.environ, {"RF_MAPPING_PROBE_DATA_ROOT": str(root)}):
                discovered = companions_module.discover_probe_geometry_paths(json_path)

            self.assertEqual(discovered, ("ProbeA", positions.resolve(), channels.resolve()))
            assert discovered is not None
            geometry = companions_module.load_probe_geometry(*discovered)
            self.assertEqual(geometry.units[0].unit_id, 42)


    def test_discovery_skips_malformed_root_and_uses_valid_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configured = root / "configured"
            bad_positions = configured / "spike_position" / "ProbeA" / "positions.csv"
            bad_positions.parent.mkdir(parents=True)
            bad_positions.write_text("wrong,columns\n1,2\n", encoding="utf-8")

            recording = root / "recording"
            json_path = recording / "exports" / "regular_A.json"
            json_path.parent.mkdir(parents=True)
            json_path.write_text("{}", encoding="utf-8")
            positions = recording / "spike_position" / "ProbeA" / "positions.csv"
            channels = recording / "waveform" / "ProbeA" / "channels.csv"
            positions.parent.mkdir(parents=True)
            channels.parent.mkdir(parents=True)
            positions.write_text(
                "unit_index,unit_id,x_um,y_um\n0,42,10,20\n",
                encoding="utf-8",
            )
            channels.write_text(
                "channel_index,channel_id,raw_channel_index,x_um,y_um,shank_id\n"
                "0,1,1,10,20,0\n",
                encoding="utf-8",
            )

            with mock.patch.dict(
                gui.os.environ,
                {"RF_MAPPING_PROBE_DATA_ROOT": str(configured)},
            ):
                discovered = companions_module.discover_probe_geometry_paths(json_path)

            self.assertIsNotNone(discovered)
            assert discovered is not None
            geometry = companions_module.load_probe_geometry(*discovered)
            self.assertEqual(geometry.positions_path, positions.resolve())
            self.assertEqual(geometry.channels_path, channels.resolve())


class TuningCurveModelTests(unittest.TestCase):
    @staticmethod
    def rates(offset: float = 0.0) -> list[float]:
        return [offset + float(index) for index in range(constants_module.HD_RAW_BIN_COUNT)]

    def load(self, payload: object) -> companions_module.TuningCurveData:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "tuning_curves.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return companions_module.TuningCurveData.load(path)

    def test_hd_display_bin_count_uses_greatest_divisor_at_or_below_request(self) -> None:
        self.assertEqual(settings_module.normalize_hd_bin_count(8), 6)
        self.assertEqual(settings_module.normalize_hd_bin_count(30), 30)
        self.assertEqual(settings_module.normalize_hd_bin_count(0), 1)
        self.assertEqual(settings_module.normalize_hd_bin_count(-10), 1)
        self.assertEqual(settings_module.normalize_hd_bin_count(181), 180)

    def test_valid_tuning_curve_schema_normalizes_cluster_ids_and_rates(self) -> None:
        data = self.load({"42": self.rates(), "007": self.rates(1.5)})

        self.assertEqual(set(data.curves), {7, 42})
        self.assertEqual(data.rates_for(42), tuple(self.rates()))
        self.assertEqual(data.rates_for(7), tuple(self.rates(1.5)))
        self.assertIsNone(data.rates_for(99))
        self.assertTrue(data.path.is_absolute())

    def test_schema_v2_loads_classes_and_aggregates_counts_over_occupancy(self) -> None:
        occupancy = [1.0] * constants_module.HD_RAW_BIN_COUNT
        occupancy[5] = 5.0
        counts = [0] * constants_module.HD_RAW_BIN_COUNT
        counts[0] = 10
        rates = [count / occupied for count, occupied in zip(counts, occupancy)]
        second_counts = [2] * constants_module.HD_RAW_BIN_COUNT
        second_rates = [count / occupied for count, occupied in zip(second_counts, occupancy)]
        data = self.load(
            {
                "schema_version": 2,
                "metadata": {
                    "session": "260730_1",
                    "probe": "ProbeA",
                    "timebase": "Open Ephys ADC seconds",
                    "timestamp_reference": "Exposure TTL rising edge",
                    "angle_convention_note": "0° up; positive counterclockwise",
                    "num_angle_bins": 180,
                    "feature_fs_hz": 119.82,
                    "classification": {
                        "method": "Rayleigh and circular shuffle",
                        "rayleigh_alpha": 0.05,
                        "shuffle_alpha": 0.01,
                        "num_shuffle": 1000,
                        "shuffle_seed": 7,
                    },
                    "ttl_qc": {
                        "ttl_pulse_count": 12_345,
                        "median_period_s": 0.008346,
                        "measured_rate_hz": 119.82,
                        "camera_input_channel": 2,
                        "camera_ttl_threshold": 1.5,
                        "camera_ttl_active_high": True,
                        "motive_frame_count_raw": 451_971,
                        "matched_motive_frame_count": 451_970,
                        "dropped_motive_frame_ids": [451_970],
                        "frame_alignment_policy_requested": "drop_unmatched_last_frame",
                        "frame_alignment_policy_applied": "drop_unmatched_last_frame",
                        "frame_timestamp_mapping": "one_gated_exposure_pulse_center_per_matched_motive_frame",
                    },
                },
                "angle_bin_edges_deg": [2.0 * index for index in range(181)],
                "occupancy_time_s": occupancy,
                "units": [
                    {
                        "unit_id": 7,
                        "spike_counts": counts,
                        "firing_rate_hz": rates,
                        "hd_class": 1,
                    },
                    {
                        "unit_id": 8,
                        "spike_counts": second_counts,
                        "firing_rate_hz": second_rates,
                        "hd_class": 2,
                    },
                ],
            }
        )

        self.assertEqual(data.hd_class_for(7), 1)
        self.assertEqual(data.hd_class_for(8), 2)
        self.assertIsNone(data.hd_class_for(99))
        self.assertIsNotNone(data.metadata)
        self.assertEqual(data.metadata.timestamp_reference, "Exposure TTL rising edge")
        self.assertEqual(
            data.metadata.angle_convention_note,
            "0° up; positive counterclockwise",
        )
        self.assertEqual(data.metadata.classification.num_shuffle, 1000)
        self.assertEqual(data.metadata.ttl_qc.ttl_pulse_count, 12_345)
        self.assertTrue(data.metadata.ttl_qc.camera_ttl_active_high)
        self.assertEqual(data.metadata.ttl_qc.motive_frame_count_raw, 451_971)
        self.assertEqual(data.metadata.ttl_qc.matched_motive_frame_count, 451_970)
        self.assertEqual(data.metadata.ttl_qc.dropped_motive_frame_ids, (451_970,))
        self.assertEqual(
            data.metadata.ttl_qc.frame_alignment_policy_applied,
            "drop_unmatched_last_frame",
        )
        processed = data.processed_for(7, 30, smoothing=False, sigma=1.5)
        self.assertIsNotNone(processed)
        centers, values = processed
        self.assertEqual(centers[0], 6.0)
        self.assertAlmostEqual(values[0], 1.0)
        self.assertNotAlmostEqual(values[0], sum(rates[:6]) / 6)

    def test_schema_v2_keeps_zero_occupancy_missing_and_smooths_counts_over_time(self) -> None:
        occupancy = [0.0] * 6 + [1.0] * (constants_module.HD_RAW_BIN_COUNT - 6)
        counts = [0] * 6 + [2] * (constants_module.HD_RAW_BIN_COUNT - 6)
        rates = [None] * 6 + [2.0] * (constants_module.HD_RAW_BIN_COUNT - 6)
        data = self.load(
            {
                "schema_version": 2,
                "angle_bin_edges_deg": [2.0 * index for index in range(181)],
                "occupancy_time_s": occupancy,
                "units": [
                    {
                        "unit_id": 7,
                        "spike_counts": counts,
                        "firing_rate_hz": rates,
                        "hd_class": 0,
                    }
                ],
            }
        )

        self.assertTrue(math.isnan(data.rates_for(7)[0]))
        _centers, unsmoothed = data.processed_for(
            7,
            30,
            smoothing=False,
            sigma=1.5,
        )
        self.assertTrue(math.isnan(unsmoothed[0]))
        self.assertTrue(all(value == 2.0 for value in unsmoothed[1:]))

        def circular_three_bin_mean(values, _sigma):
            return tuple(
                (values[index - 1] + values[index] + values[(index + 1) % len(values)])
                / 3.0
                for index in range(len(values))
            )

        with mock.patch.object(
            companions_module,
            "smooth_tuning_curve",
            side_effect=circular_three_bin_mean,
        ) as smoother:
            _centers, smoothed = data.processed_for(
                7,
                30,
                smoothing=True,
                sigma=1.5,
            )

        self.assertEqual(smoother.call_count, 2)
        self.assertTrue(
            all(len(call.args[0]) == constants_module.HD_RAW_BIN_COUNT for call in smoother.call_args_list)
        )
        self.assertTrue(all(call.args[1] == 9.0 for call in smoother.call_args_list))
        self.assertTrue(all(math.isclose(value, 2.0) for value in smoothed))

    def test_schema_v2_rejects_invalid_class_duplicate_unit_and_unknown_version(self) -> None:
        unit = {
            "unit_id": 7,
            "spike_counts": [1] * constants_module.HD_RAW_BIN_COUNT,
            "firing_rate_hz": [1.0] * constants_module.HD_RAW_BIN_COUNT,
            "hd_class": 1,
        }
        payload = {
            "schema_version": 2,
            "angle_bin_edges_deg": [2.0 * index for index in range(181)],
            "occupancy_time_s": [1.0] * constants_module.HD_RAW_BIN_COUNT,
            "units": [unit],
        }

        invalid_class = json.loads(json.dumps(payload))
        invalid_class["units"][0]["hd_class"] = 3
        with self.assertRaisesRegex(ValueError, "hd_class"):
            self.load(invalid_class)

        duplicate = json.loads(json.dumps(payload))
        duplicate["units"].append(dict(duplicate["units"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate schema v2 unit_id"):
            self.load(duplicate)

        invalid_metadata = json.loads(json.dumps(payload))
        invalid_metadata["metadata"] = {"timestamp_reference": 120}
        with self.assertRaisesRegex(ValueError, "timestamp_reference must be a string"):
            self.load(invalid_metadata)

        invalid_ttl_metadata = json.loads(json.dumps(payload))
        invalid_ttl_metadata["metadata"] = {
            "ttl_qc": {"camera_ttl_active_high": 1}
        }
        with self.assertRaisesRegex(ValueError, "camera_ttl_active_high must be boolean"):
            self.load(invalid_ttl_metadata)

        invalid_frame_ids = json.loads(json.dumps(payload))
        invalid_frame_ids["metadata"] = {
            "ttl_qc": {"dropped_motive_frame_ids": [1, 2.5]}
        }
        with self.assertRaisesRegex(ValueError, "dropped_motive_frame_ids"):
            self.load(invalid_frame_ids)

        unknown_version = dict(payload)
        unknown_version["schema_version"] = 3
        with self.assertRaisesRegex(ValueError, "Unsupported tuning-curve schema version"):
            self.load(unknown_version)

    def test_tuning_curve_schema_rejects_bad_top_level_and_duplicate_clusters(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty cluster mapping"):
            self.load([])
        with self.assertRaisesRegex(ValueError, "non-empty cluster mapping"):
            self.load({})
        with self.assertRaisesRegex(ValueError, "Invalid cluster ID"):
            self.load({"unit-42": self.rates()})
        with self.assertRaisesRegex(ValueError, "Duplicate cluster ID"):
            self.load({"1": self.rates(), "01": self.rates(1.0)})

    def test_tuning_curve_schema_rejects_bad_lengths_and_rates(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly 180 rates"):
            self.load({"42": self.rates()[:-1]})

        invalid_cases = (
            (True, "not numeric"),
            ("1.0", "not numeric"),
            (-0.1, "finite and non-negative"),
            (math.inf, "finite and non-negative"),
            (math.nan, "finite and non-negative"),
        )
        for invalid_rate, message in invalid_cases:
            with self.subTest(rate=invalid_rate):
                rates = self.rates()
                rates[9] = invalid_rate
                with self.assertRaisesRegex(ValueError, message):
                    self.load({"42": rates})

    def test_discovery_uses_earliest_matching_session_and_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            day_root = Path(directory)
            rf_path = (
                day_root
                / "260730_3"
                / "data"
                / "rfmapping"
                / "good"
                / "-100_400_1ms"
                / "ProbeA"
                / "regular_unitsSpikeCounts_260730_3.json"
            )
            rf_path.parent.mkdir(parents=True)
            rf_path.write_text("{}", encoding="utf-8")

            first_probe_a = (
                day_root / "260730_1" / "data" / "tuning_curves" / "ProbeA" / "tuning_curves.json"
            )
            later_probe_a = (
                day_root / "260730_2" / "data" / "tuning_curves" / "ProbeA" / "tuning_curves.json"
            )
            probe_b = (
                day_root / "260730_1" / "data" / "tuning_curves" / "ProbeB" / "tuning_curves.json"
            )
            for path in (first_probe_a, later_probe_a, probe_b):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")

            self.assertEqual(companions_module.discover_tuning_curve_path(rf_path), first_probe_a.resolve())
            self.assertEqual(
                companions_module.discover_tuning_curve_path(rf_path, 2),
                later_probe_a.resolve(),
            )
            self.assertIsNone(companions_module.discover_tuning_curve_path(rf_path, 3))
            for invalid in (0, -1, True):
                with self.subTest(session=invalid):
                    with self.assertRaisesRegex(ValueError, "positive integer"):
                        companions_module.discover_tuning_curve_path(rf_path, invalid)

            first_probe_a.unlink()
            self.assertEqual(companions_module.discover_tuning_curve_path(rf_path), later_probe_a.resolve())
            self.assertIsNone(companions_module.discover_tuning_curve_path(rf_path, 1))

            later_probe_a.unlink()
            self.assertIsNone(companions_module.discover_tuning_curve_path(rf_path))

    def test_aggregation_averages_consecutive_raw_bins_and_uses_hd_centers(self) -> None:
        centers, values = companions_module.aggregate_tuning_curve(self.rates(), 30)

        self.assertEqual(len(centers), 30)
        self.assertEqual(len(values), 30)
        self.assertEqual(centers[:2], (6.0, 18.0))
        self.assertEqual(centers[-1], 354.0)
        self.assertEqual(values[:2], (2.5, 8.5))
        self.assertEqual(values[-1], 176.5)

    def test_legacy_processing_smooths_raw_rates_before_aggregation(self) -> None:
        raw_rates = tuple(self.rates())
        smoothed_raw = tuple(value + 0.25 for value in raw_rates)
        expected_centers, expected_values = companions_module.aggregate_tuning_curve(smoothed_raw, 30)
        with mock.patch.object(
            companions_module,
            "smooth_tuning_rates_missing_aware",
            return_value=smoothed_raw,
        ) as smoother:
            centers, values = companions_module.processed_tuning_curve(
                raw_rates,
                30,
                smoothing=True,
                sigma=1.5,
            )

        smoother.assert_called_once_with(raw_rates, 9.0)
        self.assertEqual(centers, expected_centers)
        self.assertEqual(values, expected_values)

    def test_boundary_impulse_smoothing_is_invariant_across_display_bins(self) -> None:
        occupancy = [1.0] * constants_module.HD_RAW_BIN_COUNT
        counts = [0] * constants_module.HD_RAW_BIN_COUNT
        counts[-1] = constants_module.HD_RAW_BIN_COUNT
        rates = [count / occupied for count, occupied in zip(counts, occupancy)]
        schema_v2 = self.load(
            {
                "schema_version": 2,
                "angle_bin_edges_deg": [2.0 * index for index in range(181)],
                "occupancy_time_s": occupancy,
                "units": [
                    {
                        "unit_id": 7,
                        "spike_counts": counts,
                        "firing_rate_hz": rates,
                        "hd_class": 2,
                    }
                ],
            }
        )
        legacy = self.load({"7": rates})

        for schema, subject in (("schema-v2", schema_v2), ("legacy", legacy)):
            with self.subTest(schema=schema):
                curves = {
                    bins: subject.processed_for(
                        7,
                        bins,
                        smoothing=True,
                        sigma=1.5,
                    )[1]
                    for bins in (6, 30, 180)
                }
                fine = curves[180]
                self.assertGreater(fine[0], 0.0)
                self.assertGreater(fine[-1], 0.0)
                for bins in (6, 30):
                    group_size = constants_module.HD_RAW_BIN_COUNT // bins
                    expected = tuple(
                        sum(fine[start : start + group_size]) / group_size
                        for start in range(0, constants_module.HD_RAW_BIN_COUNT, group_size)
                    )
                    for actual, rebinned_fine in zip(curves[bins], expected):
                        self.assertAlmostEqual(actual, rebinned_fine, delta=1e-12)

    def test_legacy_smoothing_does_not_treat_missing_rates_as_zero_hz(self) -> None:
        smoothed = companions_module.smooth_tuning_rates_missing_aware(
            (math.nan, 4.0, math.nan),
            1.0,
        )

        self.assertTrue(all(math.isclose(rate, 4.0) for rate in smoothed))

    def test_smoothing_sigma_keeps_one_angular_width_across_display_bins(self) -> None:
        for display_bins in (6, 30, 60, 180):
            with self.subTest(display_bins=display_bins):
                sigma_bins = companions_module.tuning_smoothing_sigma(1.5, display_bins)
                self.assertAlmostEqual(sigma_bins * 360.0 / display_bins, 18.0)

    def test_line_plot_aligns_clockwise_head_direction_with_rf(self) -> None:
        angles, values = companions_module.center_tuning_curve_on_zero(
            (0.0, 90.0, 180.0, 270.0),
            (10.0, 20.0, 30.0, 40.0),
        )

        self.assertEqual(angles, (-180.0, -90.0, 0.0, 90.0))
        self.assertEqual(values, (30.0, 40.0, 10.0, 20.0))

        with self.assertRaisesRegex(ValueError, "same length"):
            companions_module.center_tuning_curve_on_zero((0.0,), ())

    def test_smoothing_matches_scipy_circular_gaussian_goldens(self) -> None:
        cases = (
            (
                (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                1.0,
                (
                    0.39894346935609776,
                    0.24197144565660073,
                    0.05399112742070441,
                    0.0044318616200312655,
                    0.0002676612492294835,
                    0.0044318616200312655,
                    0.05399112742070441,
                    0.24197144565660073,
                ),
            ),
            (
                (1.0, 2.0, 4.0),
                1.25,
                (2.2900252837261768, 2.322506320931544, 2.3874683953422795),
            ),
            (
                (0.0, 1.0, 3.0, 7.0, 2.0),
                1.5,
                (
                    2.1153176857216525,
                    2.2380028130107616,
                    2.8599662115132154,
                    3.1247508343151122,
                    2.661962455439259,
                ),
            ),
        )

        for rates, sigma, expected in cases:
            with self.subTest(rates=rates, sigma=sigma):
                actual = companions_module.smooth_tuning_curve(rates, sigma)
                self.assertEqual(len(actual), len(expected))
                for value, golden in zip(actual, expected):
                    self.assertAlmostEqual(value, golden, delta=1e-15)
                self.assertAlmostEqual(sum(actual), sum(rates), delta=1e-14)

    def test_smoothing_matches_scipy_radius_rounding_and_empty_input(self) -> None:
        self.assertEqual(
            companions_module.smooth_tuning_curve((1.0, 2.0, 3.0), 0.1),
            (1.0, 2.0, 3.0),
        )
        self.assertEqual(companions_module.smooth_tuning_curve((), 1.5), ())

    def test_head_direction_vectors_are_north_zero_and_clockwise(self) -> None:
        expected = {
            0.0: (0.0, -1.0),
            90.0: (1.0, 0.0),
            180.0: (0.0, 1.0),
            270.0: (-1.0, 0.0),
        }
        for angle, vector in expected.items():
            with self.subTest(angle=angle):
                actual = companions_module.head_direction_unit_vector(angle)
                self.assertAlmostEqual(actual[0], vector[0], places=12)
                self.assertAlmostEqual(actual[1], vector[1], places=12)
