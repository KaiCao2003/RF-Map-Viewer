"""Reusable layouts retain scientific settings without retaining a dataset."""

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from rfmapping_viewer.constants import VALUE_MODE_RATE
from rfmapping_viewer.figure_composer import FigureViewerSnapshot
from rfmapping_viewer.figure_export import PlotKind
from rfmapping_viewer.figure_recipe import make_figure_layout, parse_figure_layout


@pytest.fixture
def source():
    return SimpleNamespace(
        time_bin_edges=(-0.1, -0.05, 0.0, 0.05, 0.1, 0.15, 0.2),
        x_positions=(-90.0, 0.0, 90.0, 180.0),
        y_positions=(-30.0, 0.0, 30.0),
        unit_pool=(17, 42),
        path="/private/recording/RF.json",
    )


@pytest.fixture
def snapshot():
    return FigureViewerSnapshot(
        value_mode=VALUE_MODE_RATE,
        rf_source_start=2,
        rf_source_end=5,
        rf_subtract_source_range=(0, 1),
        time_groups=((0, 1), (2, 3), (4, 5)),
        x_groups=((0, 1), (2, 3)),
        y_groups=((2, 2), (0, 1)),
        smooth_radius=2,
        palette="Inferno",
        polar_radius="MATLAB row 1 inner",
        timeline_polar=True,
        selected_cell=(0, 1, 2, 3),
        total_degrees=360.0,
        timeline_range_start=1,
        timeline_range_end=2,
        timeline_active_bin=2,
        hd_display_bins=60,
        hd_smoothing=False,
        hd_smooth_sigma=2.5,
        tuning_curve_session=7,
        unit_filter_enabled=True,
        zero_bin_threshold=5,
        visible_unit_ids=(17,),
        waveform_channel_mode="same_shank",
    )


@pytest.fixture
def pages():
    return [
        {"name": "RF and timeline", "plots": [PlotKind.RF_POLAR, PlotKind.TIMELINE_CURRENT],
         "frames": [(4, 0, 2, 4), (0, 2, 4, 4)]},
        {"name": "Next page", "plots": [], "frames": []},
    ]


def test_json_round_trip_preserves_windows_time_bins_and_layout(source, snapshot, pages):
    payload = make_figure_layout(source, snapshot, pages, False, "SVG")
    encoded = json.dumps(payload, allow_nan=False)
    current = replace(
        snapshot,
        rf_source_start=0, rf_source_end=1, rf_subtract_source_range=None,
        time_groups=tuple((index, index) for index in range(6)),
        x_groups=tuple((index, index) for index in range(4)),
        y_groups=((0, 0), (1, 1), (2, 2)),
        timeline_range_start=0, timeline_range_end=-1, timeline_active_bin=0,
        selected_cell=None, palette="Gray", hd_display_bins=30,
        hd_smoothing=True, hd_smooth_sigma=1.5, waveform_channel_mode="same_x_column",
        tuning_curve_session=3, unit_filter_enabled=False,
        zero_bin_threshold=1, visible_unit_ids=(81, 96),
    )
    restored, restored_pages, normalized, output_format = parse_figure_layout(json.loads(encoded), source, current)
    assert restored == replace(
        snapshot, tuning_curve_session=3, unit_filter_enabled=False,
        zero_bin_threshold=1, visible_unit_ids=(81, 96),
    )
    assert restored_pages == pages
    assert normalized is False
    assert output_format == "SVG"
    assert not any(key in encoded for key in (
        "private", "recording", "unit_pool", "visible_unit_ids",
        "unit_filter_enabled", "zero_bin_threshold", "tuning_curve_session",
    ))
    assert current.rf_subtract_source_range is None
    assert pages[0]["frames"][0] == (4, 0, 2, 4)
    restored_pages[0]["frames"][0] = (0, 0, 2, 4)
    assert payload["pages"][0]["frames"][0] == [4, 0, 2, 4]


def test_sum_and_full_timeline_sentinel_round_trip(source, snapshot, pages):
    snapshot = replace(snapshot, rf_subtract_source_range=None, selected_cell=None, timeline_range_end=-1)
    payload = make_figure_layout(source, snapshot, pages, True, "PDF")
    assert parse_figure_layout(payload, source, snapshot) == (snapshot, pages, True, "PDF")


@pytest.mark.parametrize("axis", ["time_bin_edges", "x_positions", "y_positions"])
def test_same_shape_with_different_physical_axis_is_rejected(source, snapshot, pages, axis):
    payload = make_figure_layout(source, snapshot, pages, True, "PNG")
    payload["axes"][axis][0] += 0.01
    with pytest.raises(ValueError, match="do not match"):
        parse_figure_layout(payload, source, snapshot)


def test_other_recording_with_matching_axes_retains_current_unit_and_companion(source, snapshot, pages):
    payload = make_figure_layout(source, snapshot, pages, True, "PNG")
    other = SimpleNamespace(**{**vars(source), "unit_pool": (99,), "path": "/different/source.json"})
    current = replace(snapshot, visible_unit_ids=(99,), tuning_curve_session=2)
    restored, _, _, _ = parse_figure_layout(payload, other, current)
    assert restored.visible_unit_ids == (99,)
    assert restored.tuning_curve_session == 2


@pytest.mark.parametrize(("name", "value"), [
    ("rf_source_start", -1), ("rf_source_end", 6),
    ("rf_subtract_source_range", [2, 1]),
    ("time_groups", []), ("time_groups", [[0, 1], [3, 5]]),
    ("time_groups", [[2, 5], [0, 1]]), ("time_groups", [[0, 2], [2, 5]]),
    ("x_groups", [[0, 1], [2, 4]]), ("x_groups", [[2, 3], [0, 1]]),
    ("y_groups", [[1, 1], [2, 2], [0, 0]]),
    ("selected_cell", [0, 3, 0, 1]), ("selected_cell", [0, 1]),
    ("timeline_range_start", 3), ("timeline_range_end", 0),
    ("timeline_active_bin", 3), ("timeline_active_bin", True),
    ("timeline_polar", "false"), ("hd_smoothing", 0),
    ("palette", "unknown"), ("value_mode", "Hz"),
    ("polar_radius", "unknown"), ("waveform_channel_mode", "all"),
    ("smooth_radius", 4), ("smooth_radius", 1.5),
    ("hd_display_bins", 31), ("hd_smooth_sigma", 0),
    ("hd_smooth_sigma", float("nan")), ("total_degrees", float("inf")),
    ("total_degrees", 10 ** 1000),
])
def test_invalid_render_settings_fail_without_mutating_current(source, snapshot, pages, name, value):
    payload = make_figure_layout(source, snapshot, pages, True, "PDF")
    payload["snapshot"][name] = value
    original = copy.deepcopy(vars(snapshot))
    with pytest.raises(ValueError):
        parse_figure_layout(payload, source, snapshot)
    assert vars(snapshot) == original


@pytest.mark.parametrize(("name", "value"), [
    ("format", "export-manifest"), ("version", True), ("version", 2),
    ("snapshot", {}), ("snapshot", None), ("axes", []),
    ("pages", []), ("pages", [{}]),
    ("normalize_per_unit", "false"), ("output_format", "JPEG"),
])
def test_invalid_layout_metadata_is_rejected(source, snapshot, pages, name, value):
    payload = make_figure_layout(source, snapshot, pages, True, "PDF")
    payload[name] = value
    with pytest.raises(ValueError):
        parse_figure_layout(payload, source, snapshot)


@pytest.mark.parametrize(("name", "value"), [
    ("name", "  "), ("plots", ["unknown"]),
    ("frames", [[0, 0, 4, 4], [2, 2, 4, 4]]),
    ("frames", [[4, 0, 3, 4], [0, 2, 4, 4]]),
    ("frames", [[4, 0, 2, 4]]),
    ("frames", [[False, 0, 2, 4], [0, 2, 4, 4]]),
])
def test_invalid_page_geometry_or_kind_is_rejected(source, snapshot, pages, name, value):
    payload = make_figure_layout(source, snapshot, pages, True, "PDF")
    payload["pages"][0][name] = value
    with pytest.raises(ValueError):
        parse_figure_layout(payload, source, snapshot)
