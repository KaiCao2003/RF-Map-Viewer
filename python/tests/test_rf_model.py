import math
import unittest
from types import SimpleNamespace
from unittest import mock
import numpy as np
import rfmapping_gui as gui
import rfmapping_viewer.constants as constants_module
import rfmapping_viewer.display as display_module
import rfmapping_viewer.rf_model as rf_model_module
from gui_test_support import base_payload, current_rf_payload, write_payload


class RFMappingRateTests(unittest.TestCase):
    def load(self, payload: dict) -> rf_model_module.RFMappingData:
        directory, path = write_payload(payload)
        self.addCleanup(directory.cleanup)
        return rf_model_module.RFMappingData(path)

    def test_count_and_rate_use_occupancy_seconds(self) -> None:
        data = self.load(base_payload())

        self.assertEqual(data.response_value(0, 0, 0, 0, 0, constants_module.VALUE_MODE_COUNT), 10)
        self.assertAlmostEqual(
            data.response_value(0, 0, 0, 0, 0, constants_module.VALUE_MODE_RATE),
            10.0,
        )
        self.assertAlmostEqual(
            data.response_value(0, 0, 0, 1, 1, constants_module.VALUE_MODE_RATE),
            20.0,
        )
        self.assertAlmostEqual(
            data.response_value(0, 0, 0, 0, 1, constants_module.VALUE_MODE_RATE),
            30.0,
        )
        self.assertAlmostEqual(
            data.response_value(0, 0, 1, 0, 1, constants_module.VALUE_MODE_RATE),
            20.0,
        )

    def test_count_matrix_matches_existing_range_sum(self) -> None:
        data = self.load(base_payload())
        old_matrix = data.aggregate_matrix(0, "Range sum", 0, 1, 2)
        new_matrix = data.response_matrix(0, 1, 2, constants_module.VALUE_MODE_COUNT)
        self.assertEqual(new_matrix, old_matrix)

    def test_reversed_range_is_normalized_without_losing_bins(self) -> None:
        data = self.load(base_payload())
        forward = data.response_value(0, 0, 0, 0, 2, constants_module.VALUE_MODE_RATE)
        reverse = data.response_value(0, 0, 0, 2, 0, constants_module.VALUE_MODE_RATE)
        self.assertEqual(forward, reverse)
        self.assertAlmostEqual(data.time_span_seconds(2, 0), 0.3)

    def test_missing_occupancy_raises_key_error(self) -> None:
        payload = base_payload()
        del payload["occupancyTimeSec"]
        with self.assertRaisesRegex(KeyError, "occupancyTimeSec"):
            self.load(payload)

    def test_zero_occupancy_with_zero_counts_is_no_data(self) -> None:
        payload = base_payload()
        payload["unitsSpikeCounts"][0][0][1] = [0, 0, 0]
        payload["occupancyTimeSec"][0][1] = 0
        data = self.load(payload)
        self.assertIsNone(data.response_value(0, 0, 1, 0, 2, constants_module.VALUE_MODE_RATE))
        self.assertIsNone(data.response_value(0, 0, 1, 0, 2, constants_module.VALUE_MODE_COUNT))
        self.assertIsNone(data.response_matrix(0, 0, 2, constants_module.VALUE_MODE_COUNT)[0][1])
        self.assertEqual(
            data.spatial_group_response_value(
                0,
                (0, 0),
                (0, 1),
                0,
                2,
                constants_module.VALUE_MODE_COUNT,
            ),
            60.0,
        )
        self.assertIsNone(
            data.spatial_group_response_value(
                0,
                (0, 0),
                (1, 1),
                0,
                2,
                constants_module.VALUE_MODE_COUNT,
            )
        )
        self.assertEqual(
            data.spatial_group_source_pixel_count((0, 0), (0, 1)),
            1,
        )

    def test_zero_occupancy_with_nonzero_counts_is_rejected(self) -> None:
        payload = base_payload()
        payload["occupancyTimeSec"][0][0] = 0
        with self.assertRaisesRegex(ValueError, "zero where unitsSpikeCounts is nonzero"):
            self.load(payload)

    def test_occupancy_metadata_shape_and_values_are_validated(self) -> None:
        bad_shape = base_payload()
        bad_shape["occupancyTimeSec"] = [[1.0]]
        with self.assertRaisesRegex(ValueError, "dimensions do not match unitsSpikeCountsSize"):
            self.load(bad_shape)

        negative = base_payload()
        negative["occupancyTimeSec"] = [[-0.5, 0.75]]
        with self.assertRaisesRegex(ValueError, "non-negative"):
            self.load(negative)

    def test_matlab_singleton_occupancy_dimensions_are_restored(self) -> None:
        one_row = base_payload()
        one_row["occupancyTimeSec"] = [1.0, 0.75]
        self.assertEqual(self.load(one_row).occupancy_time_s, [[1.0, 0.75]])

        scalar = current_rf_payload({
            "unitsSpikeCounts": [[[[1, 2]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 2],
            "unitPool": 1,
            "xPositions": [0],
            "yPositions": [0],
            "timeBinEdges": [0, 0.1, 0.2],
        }, 0.3)
        self.assertEqual(self.load(scalar).occupancy_time_s, [[0.3]])

    def test_count_values_must_be_json_numbers(self) -> None:
        payload = base_payload()
        payload["unitsSpikeCounts"][0][0][0][1] = "20"
        with self.assertRaisesRegex(ValueError, "must be JSON numbers"):
            self.load(payload)

    def test_batched_timeline_windows_match_direct_sums_and_pooling(self) -> None:
        payload = base_payload()
        payload.update(
            unitsSpikeCounts=[
                [
                    [[1, 2, 3, 4], [5, 6, 7, 8], [0, 0, 0, 0]],
                    [[9, 10, 11, 12], [13, 14, 15, 16], [17, 18, 19, 20]],
                ]
            ],
            unitsSpikeCountsSize=[1, 2, 3, 4],
            xPositions=[-1, 0, 1],
            yPositions=[-1, 1],
            timeBinEdges=[-0.1, 0.0, 0.1, 0.2, 0.3],
            occupancyTimeSec=[[1.0, 2.0, 0.0], [0.5, 1.5, 2.5]],
            occupancyTimeSecSize=[2, 3],
        )
        data = self.load(payload)
        time_groups = [(0, 0), (1, 2), (3, 3)]
        counts = np.asarray(payload["unitsSpikeCounts"], dtype=np.uint64)[0]

        expected_windows = np.stack(
            [counts[..., start : end + 1].sum(axis=-1) for start, end in time_groups]
        )
        np.testing.assert_array_equal(
            data.count_windows_array(0, time_groups),
            expected_windows,
        )
        self.assertFalse(data.count_windows_array(0, time_groups).flags.writeable)
        self.assertEqual(
            data.all_positions_timeline_values(
                0,
                time_groups,
                constants_module.VALUE_MODE_COUNT,
            ),
            expected_windows.sum(axis=(1, 2)).astype(float).tolist(),
        )

        y_groups = [(1, 1), (0, 0)]
        x_groups = [(0, 1), (2, 2)]
        frames = data.spatial_group_response_frames(
            0,
            time_groups,
            constants_module.VALUE_MODE_RATE,
            y_groups,
            x_groups,
            smooth_radius=1,
        )
        reference = []
        for start, end in time_groups:
            observations = [
                [
                    data.spatial_group_observations(0, y_group, x_group, start, end)
                    for x_group in x_groups
                ]
                for y_group in y_groups
            ]
            count_matrix = [
                [
                    value.count if value.source_pixel_count > 0 else None
                    for value in row
                ]
                for row in observations
            ]
            occupancy_matrix = [
                [
                    value.occupancy_time_s
                    if value.source_pixel_count > 0
                    else None
                    for value in row
                ]
                for row in observations
            ]
            count_matrix = display_module.smooth_matrix(count_matrix, 1)
            occupancy_matrix = display_module.smooth_matrix(occupancy_matrix, 1)
            reference.append(
                [
                    [
                        None if count is None or occupancy is None else count / occupancy
                        for count, occupancy in zip(count_row, occupancy_row)
                    ]
                    for count_row, occupancy_row in zip(
                        count_matrix,
                        occupancy_matrix,
                    )
                ]
            )
        expected = np.asarray(
            [
                [
                    [np.nan if value is None else value for value in row]
                    for row in frame
                ]
                for frame in reference
            ]
        )
        np.testing.assert_allclose(frames, expected, equal_nan=True)

    def test_large_counts_do_not_wrap_in_windows_or_spatial_pooling(self) -> None:
        payload = base_payload()
        payload["unitsSpikeCounts"] = [[[[2**63, 2**63, 1], [2**63, 0, 0]]]]
        payload["occupancyTimeSec"] = [[1.0, 1.0]]
        data = self.load(payload)
        groups = [(0, 2), (2, 2)]
        windows = data.count_windows_array(0, groups)
        self.assertEqual(windows[0, 0, 0], 2**64 + 1)
        self.assertEqual(windows[1, 0, 0], 1)
        self.assertEqual(data.best_cell(0), (0, 0))
        mode = constants_module.VALUE_MODE_COUNT
        self.assertEqual(data.response_value(0, 0, 0, 0, 2, mode), float(2**64 + 1))
        self.assertEqual(data.response_matrix(0, 0, 2, mode)[0][0], float(2**64 + 1))
        self.assertEqual(
            data.all_positions_timeline_values(0, groups, mode),
            [float(3 * 2**63 + 1), 1.0],
        )
        self.assertEqual(
            data.spatial_group_response_values(0, (0, 0), (0, 0), groups, mode),
            [float(2**64 + 1), 1.0],
        )
        self.assertEqual(
            data.spatial_group_observations(0, (0, 0), (0, 1), 0, 2).count,
            float(3 * 2**63 + 1),
        )
        self.assertEqual(
            data.spatial_group_count_histogram(0, (0, 0), (0, 1)),
            [float(2**64), float(2**63), 1.0],
        )

    def test_batched_temporal_metrics_match_scalar_reference(self) -> None:
        data = self.load(base_payload())
        y_groups = [(0, 0)]
        x_groups = [(0, 0), (1, 1)]
        time_groups = [(0, 0), (1, 2)]
        delay, entropy = data.spatial_group_temporal_arrays(
            0,
            y_groups,
            x_groups,
            time_groups,
            smooth_radius=1,
        )
        histograms = [
            [
                [
                    value
                    / max(
                        1,
                        data.spatial_group_source_pixel_count(y_group, x_group),
                    )
                    for value in data.spatial_group_count_histogram(
                        0,
                        y_group,
                        x_group,
                    )
                ]
                for x_group in x_groups
            ]
            for y_group in y_groups
        ]
        for bin_idx in range(data.n_bins):
            smoothed = display_module.smooth_matrix(
                [
                    [histogram[bin_idx] for histogram in row]
                    for row in histograms
                ],
                1,
            )
            for y_idx, row in enumerate(smoothed):
                for x_idx, value in enumerate(row):
                    histograms[y_idx][x_idx][bin_idx] = float(value or 0.0)

        for y_idx, row in enumerate(histograms):
            for x_idx, histogram in enumerate(row):
                metrics = data.temporal_metrics_from_histogram(
                    histogram,
                    time_groups,
                )
                self.assertAlmostEqual(delay[y_idx, x_idx], metrics.delay_ms)
                self.assertAlmostEqual(entropy[y_idx, x_idx], metrics.entropy)


    def test_spatial_groups_pool_counts_and_unequal_occupancy(self) -> None:
        payload = current_rf_payload({
            "unitsSpikeCounts": [[[[100, 0], [0, 9]]]],
            "unitsSpikeCountsSize": [1, 1, 2, 2],
            "unitPool": [42],
            "xPositions": [-1, 1],
            "yPositions": [0],
            "timeBinEdges": [0, 0.1, 0.2],
        }, [[100.0, 1.0]])
        data = self.load(payload)

        self.assertAlmostEqual(
            data.spatial_group_response_value(
                0,
                (0, 0),
                (0, 1),
                0,
                1,
                constants_module.VALUE_MODE_RATE,
            ),
            109 / 101,
        )
        self.assertEqual(
            data.spatial_group_response_value(
                0,
                (0, 0),
                (0, 1),
                0,
                1,
                constants_module.VALUE_MODE_COUNT,
            ),
            54.5,
        )

    def test_grouped_delay_and_entropy_use_the_pooled_full_histogram(self) -> None:
        payload = current_rf_payload({
            "unitsSpikeCounts": [[[[100, 0], [0, 9]]]],
            "unitsSpikeCountsSize": [1, 1, 2, 2],
            "unitPool": [42],
            "xPositions": [-1, 1],
            "yPositions": [0],
            "timeBinEdges": [0, 0.1, 0.2],
        }, [[100.0, 1.0]])
        data = self.load(payload)
        metrics = data.spatial_group_temporal_metrics(
            0,
            (0, 0),
            (0, 1),
            [(0, 0), (1, 1)],
        )
        expected_entropy = -sum(
            probability * math.log(probability)
            for probability in (100 / 109, 9 / 109)
        ) / math.log(2)

        self.assertEqual(metrics.peak_group_index, 0)
        self.assertEqual(metrics.delay_ms, 50.0)
        self.assertAlmostEqual(metrics.entropy, expected_entropy)
        self.assertGreater(metrics.entropy, 0.0)

        viewer = SimpleNamespace(
            data=data,
            _x_groups=lambda: [(0, 1)],
            _display_y_groups=lambda: [(0, 0)],
            _selected_local_unit_index=lambda: 0,
            _time_groups=lambda: [(0, 0), (1, 1)],
            _smooth_radius=lambda: 0,
        )
        delay, entropy, _x_groups, _y_groups = (
            gui.RFMViewer._grouped_temporal_metric_matrices(
                viewer,
                0.0,
                smooth=False,
            )
        )
        self.assertEqual(delay, [[50.0]])
        self.assertAlmostEqual(entropy[0][0], expected_entropy)

        viewer._x_groups = lambda: [(0, 0), (1, 1)]
        viewer._smooth_radius = lambda: 1
        smoothed_delay, smoothed_entropy, _x_groups, _y_groups = (
            gui.RFMViewer._grouped_temporal_metric_matrices(
                viewer,
                0.0,
                smooth=True,
            )
        )
        self.assertEqual(smoothed_delay, [[50.0, 50.0]])
        self.assertGreater(smoothed_entropy[0][0], 0.0)
        self.assertGreater(smoothed_entropy[0][1], 0.0)

    def test_delay_peak_uses_exact_interval_count_rate_while_counts_stay_summed(self) -> None:
        payload = current_rf_payload({
            "unitsSpikeCounts": [[[[5, 5, 12]]]],
            "unitsSpikeCountsSize": [1, 1, 1, 3],
            "unitPool": [42],
            "xPositions": [0],
            "yPositions": [0],
            "timeBinEdges": [0.0, 0.1, 0.2, 0.5],
        })
        data = self.load(payload)
        groups = [(0, 1), (2, 2)]

        metrics = data.temporal_metrics_from_histogram([5, 5, 12], groups)

        # Count sums stay 10 and 12. Delay compares 10 / 0.2 s with
        # 12 / 0.3 s, so the first physical interval wins.
        self.assertEqual(data.response_value(0, 0, 0, 0, 1, constants_module.VALUE_MODE_COUNT), 10)
        self.assertEqual(data.response_value(0, 0, 0, 2, 2, constants_module.VALUE_MODE_COUNT), 12)
        self.assertEqual(metrics.peak_group_index, 0)
        self.assertEqual(metrics.delay_ms, 100.0)

    def test_normalized_spatial_smoothing_smooths_counts_and_exposure(self) -> None:
        payload = current_rf_payload({
            "unitsSpikeCounts": [[[[100], [9]]]],
            "unitsSpikeCountsSize": [1, 1, 2, 1],
            "unitPool": [42],
            "xPositions": [-1, 1],
            "yPositions": [0],
            "timeBinEdges": [0, 0.1],
        }, [[100.0, 1.0]])
        data = self.load(payload)
        viewer = SimpleNamespace(
            data=data,
            value_mode_var=mock.Mock(),
            _x_groups=lambda: [(0, 0), (1, 1)],
            _display_y_groups=lambda: [(0, 0)],
            _selected_local_unit_index=lambda: 0,
            _smooth_radius=lambda: 1,
        )
        viewer.value_mode_var.get.return_value = constants_module.VALUE_MODE_RATE
        matrix, _x_groups, _y_groups = gui.RFMViewer._prepare_response_plot_matrix(
            viewer,
            0,
            0,
            smooth=True,
        )

        self.assertAlmostEqual(matrix[0][0], (4 * 100 + 2 * 9) / (4 * 100 + 2 * 1))
        self.assertAlmostEqual(matrix[0][1], (4 * 9 + 2 * 100) / (4 * 1 + 2 * 100))
        self.assertNotAlmostEqual(matrix[0][0], (4 * 1 + 2 * 9) / 6)

    def test_best_cell_does_not_force_full_metrics(self) -> None:
        data = self.load(base_payload())
        self.assertEqual(data.best_cell(0), (0, 0))
        self.assertEqual(data._metrics_cache, {})
        self.assertEqual(data.best_cell(0), (0, 0))

    def test_best_cell_uses_occupancy_normalized_strength(self) -> None:
        payload = current_rf_payload(
            {
                "unitsSpikeCounts": [[[[100], [9]]]],
                "unitsSpikeCountsSize": [1, 1, 2, 1],
                "unitPool": [42],
                "xPositions": [-1, 1],
                "yPositions": [0],
                "timeBinEdges": [0, 0.1],
            },
            [[100.0, 1.0]],
        )
        data = self.load(payload)

        self.assertEqual(data.best_cell(0), (0, 1))
        self.assertEqual(data.metrics(0).best_x, 1)

    def test_zero_occupancy_stays_missing_after_display_smoothing(self) -> None:
        payload = base_payload()
        payload["unitsSpikeCounts"][0][0][1] = [0, 0, 0]
        payload["occupancyTimeSec"][0][1] = 0
        data = self.load(payload)
        viewer = SimpleNamespace(
            data=data,
            value_mode_var=mock.Mock(),
            _x_groups=lambda: [(0, 0), (1, 1)],
            _display_y_groups=lambda: [(0, 0)],
            _selected_local_unit_index=lambda: 0,
            _smooth_radius=lambda: 1,
        )
        viewer.value_mode_var.get.return_value = constants_module.VALUE_MODE_COUNT

        matrix, _x_groups, _y_groups = gui.RFMViewer._prepare_response_plot_matrix(
            viewer,
            0,
            2,
            smooth=True,
        )

        self.assertEqual(matrix[0][0], 60.0)
        self.assertIsNone(matrix[0][1])

    def test_repeated_spatial_smoothing_does_not_impute_missing_centers(self) -> None:
        self.assertEqual(
            display_module.smooth_matrix([[60.0, None, 30.0]], 2),
            [[60.0, None, 30.0]],
        )

    def test_missing_occupancy_is_excluded_from_temporal_smoothing_and_floor(self) -> None:
        payload = base_payload()
        payload["unitsSpikeCounts"][0][0][1] = [0, 0, 0]
        payload["occupancyTimeSec"][0][1] = 0
        data = self.load(payload)
        groups = [(0, 0), (1, 1), (2, 2)]
        expected = data.temporal_metrics_from_histogram([10, 20, 30], groups)
        for radius in (0, 1, 2):
            with self.subTest(radius=radius):
                histogram = data.spatial_group_histograms_array(
                    0, [(0, 0)], [(0, 0), (1, 1)], smooth_radius=radius,
                )
                np.testing.assert_allclose(histogram[0, 0], [10, 20, 30])
                self.assertTrue(np.isnan(histogram[0, 1]).all())
                delay, entropy = data.spatial_group_temporal_arrays(
                    0, [(0, 0)], [(0, 0), (1, 1)], groups,
                    smooth_radius=radius, count_floor=50,
                )
                self.assertEqual(delay[0, 0], expected.delay_ms)
                self.assertAlmostEqual(entropy[0, 0], expected.entropy)
                self.assertTrue(np.isnan(delay[0, 1]))
                self.assertTrue(np.isnan(entropy[0, 1]))


class VectorizedSpatialHelpersTests(unittest.TestCase):
    def test_array_smoothing_matches_scalar_smoothing_for_frame_stack(self) -> None:
        frames = np.asarray(
            [
                [[1.0, np.nan, 3.0], [4.0, 5.0, 6.0]],
                [[9.0, 8.0, 7.0], [np.nan, 2.0, 1.0]],
            ]
        )
        expected = np.asarray(
            [
                [
                    [np.nan if value is None else value for value in row]
                    for row in display_module.smooth_matrix(
                        [
                            [None if not math.isfinite(value) else value for value in row]
                            for row in frame
                        ],
                        2,
                    )
                ]
                for frame in frames
            ]
        )

        np.testing.assert_allclose(
            display_module._smooth_matrix_array(frames, 2),
            expected,
            equal_nan=True,
        )

    def test_rectangular_group_sums_support_reordered_groups_and_frames(self) -> None:
        values = np.arange(2 * 3 * 4, dtype=float).reshape(2, 3, 4)
        y_groups = [(2, 2), (0, 1)]
        x_groups = [(1, 3), (0, 0)]
        expected = np.asarray(
            [
                [
                    [
                        frame[min(y_group) : max(y_group) + 1, min(x_group) : max(x_group) + 1].sum()
                        for x_group in x_groups
                    ]
                    for y_group in y_groups
                ]
                for frame in values
            ]
        )

        np.testing.assert_array_equal(
            display_module._rectangular_group_sums(values, y_groups, x_groups),
            expected,
        )
