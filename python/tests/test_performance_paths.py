"""Correctness and bounded-work contracts for interactive performance paths."""

from __future__ import annotations

import multiprocessing
import os
import queue
import threading
import weakref
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

import rfmapping_gui as gui
import rfmapping_viewer.constants as constants_module
import rfmapping_viewer.display as display_module
import rfmapping_viewer.rf_model as rf_model_module
from rfmapping_viewer.rf_dataset import load_rf_maps
from rfmapping_viewer.rf_loading import load_rf_maps_isolated
from test_rf_dataset import _write_dataset
from gui_test_support import Variable as Var


def test_time_labels_reuse_groups_and_invalidate_on_resolution_or_document():
    viewer = gui.RFMViewer.__new__(gui.RFMViewer)
    viewer._time_groups_cache = viewer._base_bin_cache = None
    viewer.data = SimpleNamespace(
        n_bins=1000, time_bin_edges=[i * 0.001 - 0.1 for i in range(1001)]
    )
    viewer.time_res_ms_var = Var("1")
    with mock.patch.object(gui, "physical_time_groups", wraps=display_module.physical_time_groups) as grouping:
        groups = viewer._time_groups()
        for index in range(len(groups)):
            viewer._time_group_center_ms(index)
            viewer._time_group_label(index)
        assert grouping.call_count == 1
        assert viewer._time_groups() is groups
        assert viewer._time_group_bounds_ms(0) == pytest.approx((-100, -99))
        assert viewer._time_group_bounds_ms(999) == pytest.approx((899, 900))
        viewer.time_res_ms_var.set("2")
        assert len(viewer._time_groups()) == 500
        assert grouping.call_count == 2
        viewer.data = SimpleNamespace(n_bins=3, time_bin_edges=[-0.005, 0, 0.01, 0.02])
        assert viewer._base_bin_ms() == 5
        assert viewer._time_group_bounds_ms(2) == (10, 20)
        assert grouping.call_count == 3


def test_temporal_cache_reuses_results_and_bounds_retention(tmp_path):
    data = rf_model_module.RFMappingData(_write_dataset(tmp_path))
    args = (0, [(0, 0)], [(0, 0), (1, 1)], [(0, 0), (1, 1)])
    with mock.patch.object(data, "spatial_group_histograms_array", wraps=data.spatial_group_histograms_array) as pooling:
        first = data.spatial_group_temporal_arrays(*args)
        assert data.spatial_group_temporal_arrays(*args) is first
        assert pooling.call_count == 1
        for radius in range(1, constants_module.RF_COUNT_CACHE_UNIT_LIMIT + 1):
            data.spatial_group_temporal_arrays(*args, smooth_radius=radius)
        assert len(data._temporal_array_cache) == constants_module.RF_COUNT_CACHE_UNIT_LIMIT
        assert data.spatial_group_temporal_arrays(*args) is not first
        assert pooling.call_count == constants_module.RF_COUNT_CACHE_UNIT_LIMIT + 2
    assert not first[0].flags.writeable
    assert not first[1].flags.writeable
    floored, entropy = data.spatial_group_temporal_arrays(*args, count_floor=100)
    assert np.isnan(floored).all()
    np.testing.assert_array_equal(entropy, first[1])
    changed = data.spatial_group_temporal_arrays(1, *args[1:])
    assert changed is not data.spatial_group_temporal_arrays(*args)
    regrouped = data.spatial_group_temporal_arrays(*args[:3], [(0, 1)])
    np.testing.assert_array_equal(regrouped[0], [[0, 0]])


@pytest.mark.parametrize("invalidate", [None, "document", "hidden", "closed", "cached", "released"])
def test_waveform_loading_keeps_one_worker_and_only_latest_pending_request(invalidate):
    started = threading.Event()
    release = threading.Event()
    completed = queue.Queue()
    reads = []

    def load(unit_id, _mode):
        reads.append(unit_id)
        if len(reads) == 1:
            started.set()
            assert release.wait(5)
        return {"matrix": [[unit_id]]}

    viewer = gui.RFMViewer.__new__(gui.RFMViewer)
    selected = [0]
    viewer.show_waveform_var = Var(True)
    viewer.waveform_channel_mode_var = Var("same_x_column")
    viewer._selected_unit_id_value = lambda: selected[0]
    viewer.data = SimpleNamespace(path=Path("synthetic.rfmap"), waveform_plot_payload=load)
    viewer._waveform_generation = 0
    viewer._waveform_payload_key = viewer._waveform_loading_key = viewer._waveform_error_key = None
    viewer._waveform_worker_running = False
    viewer._waveform_pending_request = None
    viewer._waveform_result_queue = completed
    viewer._quitting = False
    viewer._viewer_ready = True
    viewer._schedule_waveform_result_poll = mock.Mock()
    viewer._draw_waveform = mock.Mock()
    viewer._request_waveform_payload()
    assert started.wait(5)
    try:
        for unit_id in range(1, 24):
            selected[0] = unit_id
            viewer._request_waveform_payload()
        assert reads == [0]
        if invalidate and invalidate != "cached":
            viewer._waveform_generation += 1
            viewer._waveform_loading_key = None
        if invalidate == "document":
            viewer.data = SimpleNamespace(path=Path("next.rfmap"), waveform_plot_payload=load)
            selected[0] = 42
            viewer._request_waveform_payload()
        elif invalidate == "hidden":
            viewer.show_waveform_var.set(False)
        elif invalidate == "closed":
            viewer._quitting = True
        elif invalidate == "cached":
            selected[0] = 100
            viewer._waveform_payload_key = (100, "same_x_column")
            viewer._request_waveform_payload()
        elif invalidate == "released":
            reference = weakref.ref(viewer)
            del viewer
            assert reference() is None
    finally:
        release.set()
    first = completed.get(timeout=5)
    if invalidate == "released":
        assert reads == [0]
        return
    completed.put(first)
    viewer._poll_waveform_results()
    if invalidate in ("hidden", "closed", "cached"):
        assert reads == [0]
        assert viewer._waveform_pending_request is None
        assert not viewer._waveform_worker_running
        return
    result = completed.get(timeout=5)
    completed.put(result)
    viewer._poll_waveform_results()
    expected_unit = 42 if invalidate == "document" else 23
    assert reads == [0, expected_unit]
    assert viewer._waveform_payload_key == (expected_unit, "same_x_column")
    assert viewer.waveform_payload == {"matrix": [[expected_unit]]}
    assert not viewer._waveform_worker_running
    viewer._draw_waveform.assert_called_once()


@pytest.mark.parametrize("mode", constants_module.VALUE_MODES)
def test_local_queries_match_frames_without_building_full_grid(tmp_path, mode):
    data = rf_model_module.RFMappingData(_write_dataset(tmp_path))
    windows = [(0, 0), (1, 1), (1, 0), (-5, 9)]
    expected = data.spatial_group_response_frames(
        0, windows, mode, [(0, 0)], [(1, 0)]
    )[:, 0, 0].tolist()
    with mock.patch.object(data, "count_windows_array", side_effect=AssertionError("full grid")):
        assert data.spatial_group_response_values(0, (0, 0), (1, 0), windows, mode) == expected
        assert data.spatial_group_response_value(0, (0, 0), (1, 0), 1, 0, mode) == expected[2]
        assert data.spatial_group_response_values(0, (0, 0), (0, 0), [], mode) == []
        assert data.response_value(0, 0, 0, 0, 1, mode) == (
            3 if mode == constants_module.VALUE_MODE_COUNT else 15
        )


def test_local_queries_preserve_unavailable_cells_and_unsigned_window_sums(tmp_path):
    path = _write_dataset(
        tmp_path,
        occupancyTimeSec=[[0, 1]],
        unitsSpikeCounts=[[[[0, 0], [2**63, 2**63 - 1]]], [[[0, 0], [1, 2]]]],
    )
    data = rf_model_module.RFMappingData(path)
    assert data.spatial_group_response_values(0, (0, 0), (0, 0), [(0, 1)], constants_module.VALUE_MODE_RATE) == [None]
    assert data.spatial_group_response_values(0, (0, 0), (1, 1), [(0, 1)], constants_module.VALUE_MODE_COUNT) == [float(2**64 - 1)]


def test_isolated_load_preserves_arrays_metadata_and_read_only_contract(tmp_path):
    path = _write_dataset(tmp_path, nested={"list": [1, {"a": "b"}]})
    direct = load_rf_maps(path)
    isolated = load_rf_maps_isolated(path)
    assert isolated.unit_ids == direct.unit_ids
    for expected, actual in zip(direct, isolated):
        assert actual.source_path == path
        assert actual.metadata == expected.metadata
        for name in ("spike_counts", "x_positions", "y_positions", "time_bin_edges_s", "occupancy_time_s"):
            np.testing.assert_array_equal(getattr(actual, name), getattr(expected, name))
            assert not getattr(actual, name).flags.writeable


def test_isolated_load_cancellation_reaps_worker(tmp_path):
    path = _write_dataset(tmp_path)
    before = {child.pid for child in multiprocessing.active_children()}
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(CancelledError):
        load_rf_maps_isolated(path, cancelled=cancelled)
    assert {child.pid for child in multiprocessing.active_children()} == before


def test_isolated_load_surfaces_invalid_occupancy(tmp_path):
    path = _write_dataset(tmp_path, occupancyTimeSec=[[-0.2, 0.4]])
    with pytest.raises(ValueError, match="occupancyTimeSec"):
        load_rf_maps_isolated(path)


def test_isolated_load_transfers_arrays_across_binary_chunk_boundaries(tmp_path):
    counts = np.ones((1, 32, 64, 513), dtype=np.uint16)
    counts[0, -1, -1, -1] = 256
    path = _write_dataset(
        tmp_path, unitsSpikeCounts=counts.tolist(),
        unitsSpikeCountsSize=list(counts.shape), unitPool=[7],
        xPositions=list(range(64)), yPositions=list(range(32)),
        timeBinEdges=[i * 0.001 for i in range(514)],
        occupancyTimeSec=np.ones((32, 64)).tolist(),
    )
    actual = load_rf_maps_isolated(path)
    np.testing.assert_array_equal(actual[0].spike_counts, counts[0])
    assert actual[0].spike_counts.dtype == counts.dtype


def _exiting_decoder(_path, _connection):
    os._exit(17)


def test_isolated_load_reports_worker_exit_and_reaps_it(tmp_path, monkeypatch):
    from rfmapping_viewer import rf_loading

    before = {child.pid for child in multiprocessing.active_children()}
    monkeypatch.setattr(rf_loading, "_decode_worker", _exiting_decoder)
    with pytest.raises(RuntimeError, match="exited before completing"):
        load_rf_maps_isolated(_write_dataset(tmp_path))
    assert {child.pid for child in multiprocessing.active_children()} == before
