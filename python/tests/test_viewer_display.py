import hashlib
import math
import unittest
from types import MethodType, SimpleNamespace
from unittest import mock
import numpy as np
import rfmapping_gui as gui
import rfmapping_viewer.constants as constants_module
import rfmapping_viewer.display as display_module
import rfmapping_viewer.rf_model as rf_model_module
import rfmapping_viewer.settings as settings_module
from gui_test_support import Variable, base_payload, write_payload


class WaveformCanvasTests(unittest.TestCase):
    def test_live_canvas_accepts_the_shared_numpy_payload(self) -> None:
        class Canvas:
            def __init__(self) -> None:
                self.rectangles: list[tuple[tuple[object, ...], dict[str, object]]] = []
                self.texts: list[tuple[tuple[object, ...], dict[str, object]]] = []
                self.lines: list[tuple[tuple[object, ...], dict[str, object]]] = []

            def delete(self, *_args: object) -> None:
                return None

            def winfo_width(self) -> int:
                return 900

            def winfo_height(self) -> int:
                return 520

            def create_rectangle(self, *args: object, **kwargs: object) -> None:
                self.rectangles.append((args, kwargs))

            def create_text(self, *args: object, **kwargs: object) -> None:
                self.texts.append((args, kwargs))

            def create_line(self, *args: object, **kwargs: object) -> None:
                self.lines.append((args, kwargs))

        class Variable:
            def get(self) -> str:
                return "same_x_column"

        class Label:
            text = ""

            def configure(self, *, text: str) -> None:
                self.text = text

        canvas = Canvas()
        subtitle = Label()
        viewer = SimpleNamespace(
            canvases={"waveform": canvas},
            waveform_canvas=canvas,
            _waveform_zoomed=False,
            show_waveform_var=SimpleNamespace(get=lambda: True),
            waveform_channel_mode_var=Variable(),
            waveform_subtitle_label=subtitle,
            waveform_payload={
                "matrix": np.arange(20, dtype=float).reshape(5, 4) - 10.0,
                "times_ms": np.asarray((-0.5, -0.25, 0.0, 0.25)),
                "channel_labels": tuple(f"ch {index}" for index in range(5)),
                "best_channel_row": 2,
                "amplitude_limit_uv": 12.0,
                "max_ptp_uv": 22.5,
            },
            _waveform_payload_key=(17, "same_x_column"),
            _waveform_loading_key=None,
            _waveform_error_key=None,
            _selected_unit_id_value=lambda: 17,
        )
        viewer._request_waveform_payload = MethodType(gui.RFMViewer._request_waveform_payload, viewer)
        viewer._draw_waveform_canvas = MethodType(gui.RFMViewer._draw_waveform_canvas, viewer)

        gui.RFMViewer._draw_waveform(viewer)

        self.assertGreaterEqual(len(canvas.rectangles), 20 + 80)
        self.assertGreaterEqual(len(canvas.lines), 4)
        self.assertTrue(
            any(call[1].get("text") == "★ ch 2" for call in canvas.texts)
        )
        self.assertIn("Cluster 17", subtitle.text)
        self.assertIn("Same x column", subtitle.text)
        self.assertIn("best + 4 nearest", subtitle.text)

    def test_legacy_waveform_default_tab_migrates_to_rf(self) -> None:
        restored = settings_module.ViewerSettings.from_mapping(
            {
                "schema_version": constants_module.SETTINGS_SCHEMA_VERSION,
                "default_viewer_tab": "waveform",
                "waveform_channel_mode": "same_shank",
                "rf_palette": "Viridis",
            }
        )

        self.assertEqual(restored.default_viewer_tab, "rf")
        self.assertEqual(restored.waveform_channel_mode, "same_shank")
        self.assertEqual(restored.rf_palette, "Viridis")


class SpatialDisplayGeometryTests(unittest.TestCase):
    def test_singleton_y_uses_legacy_30_by_7_visual_aspect(self) -> None:
        cell_x, cell_y, grid_w, grid_h = display_module.spatial_grid_dimensions(
            900.0,
            600.0,
            120,
            1,
        )

        self.assertAlmostEqual(grid_w / grid_h, 30.0 / 7.0)
        self.assertAlmostEqual(cell_x * 120, grid_w)
        self.assertAlmostEqual(cell_y, grid_h)

    def test_multirow_maps_keep_square_cells(self) -> None:
        cell_x, cell_y, grid_w, grid_h = display_module.spatial_grid_dimensions(
            900.0,
            600.0,
            30,
            7,
        )

        self.assertAlmostEqual(cell_x, cell_y)
        self.assertAlmostEqual(grid_w / grid_h, 30.0 / 7.0)

    def test_stretched_rectangle_and_polar_ring_remain_fully_clickable(self) -> None:
        rectangle_layout = {
            "geometry": "rectangle",
            "x0": 10.0,
            "y0": 20.0,
            "cell": 2.0,
            "cell_y": 28.0,
            "grid_w": 4.0,
            "grid_h": 28.0,
            "x_groups": [(0, 0), (1, 1)],
            "y_groups": [(0, 0)],
        }
        rectangle_viewer = SimpleNamespace(_canvas_layouts={"rf": rectangle_layout})
        rectangle_cell = gui.RFMViewer._canvas_to_cell(
            rectangle_viewer,
            "rf",
            SimpleNamespace(x=13.0, y=47.0),
        )
        self.assertEqual(rectangle_cell, (0, 0, 1, 1))

        polar_layout = {
            "geometry": "polar",
            "cx": 20.0,
            "cy": 20.0,
            "scale": 1.0,
            "total_deg": 360.0,
            "ring_span": 7.0,
            "x_groups": [(0, 0)],
            "y_groups": [(0, 0)],
            "ring_rows": [0],
        }
        polar_cell = gui.RFMViewer._polar_cell_from_layout(
            SimpleNamespace(),
            polar_layout,
            30.0,
            20.0,
        )
        self.assertEqual(polar_cell, (0, (0, 0, 0, 0)))


class RasterTests(unittest.TestCase):
    def test_matrix_ppm_nearest_neighbor_colors(self) -> None:
        ppm = display_module.matrix_ppm_data(
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
        ppm = display_module.matrix_atlas_ppm_data(
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

    def test_atlas_supports_a_stretched_singleton_row(self) -> None:
        ppm = display_module.matrix_atlas_ppm_data(
            [([[1.0, 1.0]], 0.0, 0.0, 2.0, 6.0)],
            4,
            6,
            lambda _value: "#102030",
        )
        header = b"P6\n4 6\n255\n"
        pixels = ppm[len(header) :]
        self.assertEqual(pixels, b"\x10\x20\x30" * 24)

    def test_polar_atlas_preserves_blank_center_and_colors_rings(self) -> None:
        ppm = display_module.polar_matrix_atlas_ppm_data(
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

    def test_polar_atlas_stretches_one_row_across_seven_radial_units(self) -> None:
        ppm = display_module.polar_matrix_atlas_ppm_data(
            [([[1.0]], 0.0, 0.0, 1.0, 360.0, [0], 7.0)],
            22,
            22,
            lambda _value: "#123456",
        )
        header = b"P6\n22 22\n255\n"
        pixels = ppm[len(header) :]

        def pixel(x: int, y: int) -> bytes:
            offset = (y * 22 + x) * 3
            return pixels[offset : offset + 3]

        self.assertEqual(pixel(11, 11), b"\xff\xff\xff")
        self.assertEqual(pixel(11, 0), b"\x12\x34\x56")


    def test_rectangle_atlas_matches_fractional_overlap_golden_bytes(self) -> None:
        colors = {
            None: "#abcdef",
            0.0: "#000000",
            1.0: "#112233",
            2.0: "#445566",
            3.0: "#778899",
            4.0: "#aabbcc",
            5.0: "#ddeeff",
        }
        ppm = display_module.matrix_atlas_ppm_data(
            [
                ([[0.0, 1.0], [2.0, None]], -0.4, 0.6, 1.75),
                ([[3.0, 4.0, 5.0]], 1.2, 2.1, 0.9),
            ],
            8,
            6,
            lambda value: colors[value],
        )

        self.assertEqual(
            hashlib.sha256(ppm).hexdigest(),
            "f88929559cff9e4d11836ab90983abde0451c6c201fcae04fa244e4ba1107c16",
        )

    def test_polar_atlas_matches_fractional_clipped_golden_bytes(self) -> None:
        colors = {
            0.0: "#000000",
            1.0: "#112233",
            2.0: "#445566",
            3.0: "#778899",
            4.0: "#aabbcc",
            5.0: "#ddeeff",
            6.0: "#13579b",
            7.0: "#2468ac",
        }
        ppm = display_module.polar_matrix_atlas_ppm_data(
            [
                (
                    [[0.0, 1.0, 2.0, 3.0], [4.0, 5.0, 6.0, 7.0]],
                    -0.4,
                    0.25,
                    2.0,
                    360.0,
                    [1, 0],
                ),
                ([[7.0, 6.0, 5.0]], 10.6, 5.4, 1.5, 270.0, [0]),
            ],
            28,
            25,
            lambda value: colors[value],
        )

        self.assertEqual(
            hashlib.sha256(ppm).hexdigest(),
            "747fbb1ae383d0f4721971e5f9cf6fd0005448da1ae2d76a356cf7347c08367a",
        )

    def test_polar_atlas_reuses_translated_tile_geometry(self) -> None:
        display_module._polar_tile_pixel_runs.cache_clear()
        tile = [[1.0, 2.0], [3.0, 4.0]]

        display_module.polar_matrix_atlas_ppm_data(
            [
                (tile, 0.25, 0.5, 2.0, 360.0, [1, 0]),
                (tile, 20.25, 0.5, 2.0, 360.0, [1, 0]),
            ],
            50,
            30,
            lambda value: f"#{int(value):02x}0000",
        )

        cache = display_module._polar_tile_pixel_runs.cache_info()
        self.assertEqual(cache.misses, 1)
        self.assertGreaterEqual(cache.hits, 1)
        self.assertLessEqual(cache.currsize, cache.maxsize)


class RFPlotRangeTests(unittest.TestCase):

    @staticmethod
    def viewer_with_edges(edges_ms: list[float]):
        viewer = mock.Mock()
        viewer.settings = settings_module.ViewerSettings()
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
        viewer.value_mode_var = Variable(constants_module.VALUE_MODE_COUNT)
        viewer._selected_time_bounds_ms.return_value = (0.0, 20.0)
        viewer._rf_subtraction_range.return_value = None

        self.assertEqual(
            gui.RFMViewer._rf_sum_range_value_text(viewer, 110.0),
            "RF sum range 0–20 ms: 110 spikes",
        )

    def test_timeline_selection_cannot_change_current_rf_matrix(self) -> None:
        payload = base_payload()
        payload.update(
            unitsSpikeCounts=[[[[1, 10, 100, 1000]]]],
            unitsSpikeCountsSize=[1, 1, 1, 4],
            xPositions=[0],
            timeBinEdges=[-0.1, 0.0, 0.01, 0.02, 0.03],
            occupancyTimeSec=[[0.4]],
            occupancyTimeSecSize=[1, 1],
        )
        directory, path = write_payload(payload)
        self.addCleanup(directory.cleanup)
        data = rf_model_module.RFMappingData(path)
        viewer = mock.Mock()
        viewer.data = data
        viewer.unit_idx = Variable(0)
        viewer.value_mode_var = Variable(constants_module.VALUE_MODE_COUNT)
        viewer.range_start_ms_var = Variable("0")
        viewer.range_end_ms_var = Variable("20")
        viewer.range_start_var = Variable(0)
        viewer.range_end_var = Variable(3)
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
        viewer._rf_subtraction_range = lambda: None

        before = gui.RFMViewer._current_matrix(viewer)
        viewer.range_start_var.set(3)
        viewer.range_end_var.set(3)
        after = gui.RFMViewer._current_matrix(viewer)

        self.assertEqual(before, [[110.0]])
        self.assertEqual(after, before)


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


    def test_chart_hit_testing_uses_physical_interval_widths(self) -> None:
        canvas = mock.Mock()
        canvas.canvasx.side_effect = lambda value: value
        canvas.canvasy.side_effect = lambda value: value
        viewer = SimpleNamespace(
            canvases={"timeline": canvas},
            _canvas_layouts={
                "timeline": {
                    "chart_x": 0.0,
                    "chart_y": 0.0,
                    "chart_w": 300.0,
                    "chart_h": 60.0,
                    "display_bins": 3,
                    "axis_start_ms": -100.0,
                    "axis_end_ms": 200.0,
                    "time_group_end_bounds_ms": [-50.0, 50.0, 200.0],
                }
            },
            _time_group_count=lambda: 3,
        )

        event = SimpleNamespace(x=75.0, y=30.0)

        self.assertEqual(gui.RFMViewer._timeline_bin_at(viewer, event), 1)


class ScientificScaleTests(unittest.TestCase):
    def test_nonnegative_response_range_starts_at_zero_and_ignores_missing(self) -> None:
        self.assertEqual(
            display_module.nonnegative_response_range([[None, 2.0], [5.5, float("nan")]]),
            (0.0, 5.5),
        )
        self.assertEqual(display_module.nonnegative_response_range([[None, 0.0]]), (0.0, 0.0))

    def test_gray_restores_previous_contrast_range_only_for_gray(self) -> None:
        matrix = [[None, 2.0], [5.5, float("nan")]]

        self.assertEqual(display_module.palette_response_range(matrix, "Gray"), (2.0, 5.5))
        self.assertEqual(display_module.palette_response_range(matrix, "Viridis"), (0.0, 5.5))
        self.assertEqual(display_module.palette_response_range(matrix, "Inferno"), (0.0, 5.5))

        low, high = display_module.palette_response_range([[10.0, 15.0, 20.0]], "Gray")
        self.assertEqual(display_module.palette_color(10.0, low, high, "Gray"), "#121212")
        self.assertEqual(display_module.palette_color(15.0, low, high, "Gray"), "#868686")
        self.assertEqual(display_module.palette_color(20.0, low, high, "Gray"), "#fafafa")


class TimelineGeometryTests(unittest.TestCase):
    def test_physical_grouping_preserves_uniform_bins_and_uses_measured_edges(self) -> None:
        self.assertEqual(
            display_module.physical_time_groups([0, 10, 20, 30, 40, 50], 20),
            [(0, 1), (2, 3), (4, 4)],
        )
        self.assertEqual(
            display_module.physical_time_groups([-100, 0, 50, 200], 100),
            [(0, 0), (1, 1), (2, 2)],
        )

    def test_nonuniform_bin_centers_use_physical_time_geometry(self) -> None:
        points = display_module.timeline_chart_points(
            [1.0, 2.0, 3.0],
            [-75.0, 0.0, 125.0],
            (-100.0, 200.0),
            3.0,
            (10.0, 20.0, 300.0, 60.0),
        )

        self.assertEqual(points[::2], [35.0, 110.0, 235.0])
        self.assertNotEqual(points[::2], [60.0, 160.0, 260.0])

    def test_timeline_points_stay_inside_measured_nonnegative_range(self) -> None:
        points = display_module.timeline_chart_points(
            [-2.0, 5.0, 12.0, math.nan],
            [0.0, 1.0, 2.0, 3.0],
            (0.0, 3.0),
            10.0,
            (0.0, 10.0, 100.0, 40.0),
        )

        self.assertEqual(points[1::2], [50.0, 30.0, 10.0, 50.0])
        self.assertTrue(all(10.0 <= y <= 50.0 for y in points[1::2]))

    def test_overlaid_response_traces_use_independent_y_scales(self) -> None:
        rect = (0.0, 10.0, 100.0, 40.0)
        blue_high = display_module.timeline_response_high([10.0, 5.0])
        red_high = display_module.timeline_response_high([2.0, 2.0])

        all_position_point = display_module.timeline_chart_points(
            [2.0],
            [0.5],
            (0.0, 1.0),
            blue_high,
            rect,
        )
        selected_point = display_module.timeline_chart_points(
            [2.0],
            [0.5],
            (0.0, 1.0),
            red_high,
            rect,
        )

        self.assertEqual(blue_high, 10.0)
        self.assertEqual(red_high, 2.0)
        self.assertEqual(all_position_point[1], 42.0)
        self.assertEqual(selected_point[1], rect[1])
        self.assertNotEqual(all_position_point, selected_point)
        self.assertEqual(display_module.timeline_response_high([]), 1.0)

    def test_timeline_bin_boundaries_are_half_open_in_physical_time(self) -> None:
        ends = [-50.0, 50.0, 200.0]

        self.assertEqual(display_module.timeline_bin_index(-75.0, ends), 0)
        self.assertEqual(display_module.timeline_bin_index(-50.0, ends), 1)
        self.assertEqual(display_module.timeline_bin_index(125.0, ends), 2)
        self.assertEqual(display_module.timeline_bin_index(200.0, ends), 2)
