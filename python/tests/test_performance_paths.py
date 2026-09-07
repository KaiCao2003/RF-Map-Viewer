"""Correctness and bounded-work contracts for interactive performance paths."""

from __future__ import annotations

import io
import json
import multiprocessing
import os
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

import rfmapping_gui as gui
from rfmapping_viewer.rf_dataset import _read_rf_json, load_rf_maps
from rfmapping_viewer.rf_loading import load_rf_maps_isolated
from test_rf_dataset import _write_dataset


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def test_time_labels_reuse_groups_and_invalidate_on_resolution_or_document():
    viewer = gui.RFMViewer.__new__(gui.RFMViewer)
    viewer.data = SimpleNamespace(
        n_bins=1000, time_bin_edges=[i * 0.001 - 0.1 for i in range(1001)]
    )
    viewer.time_res_ms_var = Var("1")
    with mock.patch.object(gui, "physical_time_groups", wraps=gui.physical_time_groups) as grouping:
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


@pytest.mark.parametrize("mode", gui.VALUE_MODES)
def test_local_queries_match_frames_without_building_full_grid(tmp_path, mode):
    data = gui.RFMappingData(_write_dataset(tmp_path))
    windows = [(0, 0), (1, 1), (1, 0), (-5, 9)]
    expected = data.spatial_group_response_frames(
        0, windows, mode, [(0, 0)], [(1, 0)]
    )[:, 0, 0].tolist()
    with mock.patch.object(data, "count_windows_array", side_effect=AssertionError("full grid")):
        assert data.spatial_group_response_values(0, (0, 0), (1, 0), windows, mode) == expected
        assert data.spatial_group_response_value(0, (0, 0), (1, 0), 1, 0, mode) == expected[2]
        assert data.spatial_group_response_values(0, (0, 0), (0, 0), [], mode) == []
        assert data.response_value(0, 0, 0, 0, 1, mode) == (
            3 if mode == gui.VALUE_MODE_COUNT else 15
        )


def test_local_queries_preserve_unavailable_cells_and_unsigned_window_sums(tmp_path):
    path = _write_dataset(
        tmp_path,
        occupancyTimeSec=[[0, 1]],
        unitsSpikeCounts=[[[[0, 0], [2**63, 2**63 - 1]]], [[[0, 0], [1, 2]]]],
    )
    data = gui.RFMappingData(path)
    assert data.spatial_group_response_values(0, (0, 0), (0, 0), [(0, 1)], gui.VALUE_MODE_RATE) == [None]
    assert data.spatial_group_response_values(0, (0, 0), (1, 1), [(0, 1)], gui.VALUE_MODE_COUNT) == [float(2**64 - 1)]


@pytest.mark.parametrize("chunk_size", [1, 7, 61, 65536])
def test_streaming_json_handles_chunk_boundaries_and_key_order(tmp_path, chunk_size):
    path = _write_dataset(tmp_path, note='quotes " and slash \\ and 中文', value=12.5e-7)
    raw = json.loads(path.read_text())
    # Counts last as well as MATLAB's counts-first order.
    for payload in (raw, {**{k: v for k, v in raw.items() if k != "unitsSpikeCounts"}, "unitsSpikeCounts": raw["unitsSpikeCounts"]}):
        class SmallReads(io.StringIO):
            def read(self, n=-1):
                return super().read(min(n, chunk_size))

        decoded = _read_rf_json(SmallReads(json.dumps(payload, ensure_ascii=False)))
        assert decoded["note"] == raw["note"]
        assert decoded["value"] == raw["value"]
        np.testing.assert_array_equal(np.stack(decoded["unitsSpikeCounts"]), raw["unitsSpikeCounts"])


@pytest.mark.parametrize("document", [
    '{"unitsSpikeCounts":[[[[1,2]]]],}',
    '{"unitsSpikeCounts":[[[[1,2]]],]}',
    '{"unitsSpikeCounts":[[[[1,2,]]]]}',
    '{"unitsSpikeCounts":[[[[1e,2]]]]}',
    '{"unitsSpikeCounts":[[[[1,2]]]]',
    '{} trailing', '{"value":12e}',
])
def test_streaming_json_rejects_malformed_documents(document):
    with pytest.raises(ValueError):
        _read_rf_json(io.StringIO(document))


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


def test_isolated_load_surfaces_schema_failure(tmp_path):
    path = _write_dataset(tmp_path, responseUnits="wrong")
    with pytest.raises(ValueError, match="responseUnits"):
        load_rf_maps_isolated(path)


def test_isolated_load_transfers_arrays_across_binary_chunk_boundaries(tmp_path):
    counts = np.ones((1, 32, 64, 513), dtype=np.uint16)
    counts[0, -1, -1, -1] = 256
    path = _write_dataset(
        tmp_path, unitsSpikeCounts=counts.tolist(),
        unitsSpikeCountsSize=list(counts.shape), unitPool=[7],
        xPositions=list(range(64)), yPositions=list(range(32)),
        timeBinEdges=[i * 0.001 for i in range(514)],
        occupancyTimeSec=np.ones((32, 64)).tolist(), occupancyTimeSecSize=[32, 64],
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
