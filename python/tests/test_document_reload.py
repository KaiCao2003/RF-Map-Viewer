"""Reload snapshots publish RF early and preserve independent companions."""

import json
from queue import SimpleQueue
from unittest import mock

from gui_test_support import base_payload
from rfmapping_viewer.companions import TuningCurveData, load_probe_geometry
from rfmapping_viewer.document_reload import ReloadRequest, reload_document
from rfmapping_viewer.rf_model import RFMappingData
from test_indexed_rfmap import wait_for, write_pair


def test_rf_is_published_before_whole_tuning_file_is_loaded(tmp_path):
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(base_payload()))
    tuning_path = tmp_path / "manual.tc"
    tuning_path.write_text(json.dumps({"42": [1.] * 180}))
    results = SimpleQueue()
    original_load = TuningCurveData.load

    def load_tuning(candidate):
        assert results.qsize() == 1
        return original_load(candidate)

    with mock.patch.object(TuningCurveData, "load", side_effect=load_tuning):
        reload_document(ReloadRequest(4, path, 42, tuning_path=tuning_path), results)
    ready, complete = results.get_nowait(), results.get_nowait()
    try:
        assert (ready.stage, complete.stage) == ("rf", "complete")
        assert ready.generation == complete.generation == 4
        assert ready.data is complete.data
        assert ready.tuning_data is None
        assert complete.tuning_data.path == tuning_path
        assert complete.data.hd_tuning() is complete.tuning_data
        assert complete.loaded == frozenset({"tuning"})
        assert complete.missing == frozenset({"results"})
        assert not complete.errors
    finally:
        ready.data.close()


def test_indexed_reload_starts_from_selected_unit_and_preloads_remaining(tmp_path):
    _old, path, _arrays = write_pair(tmp_path)
    results = SimpleQueue()
    reload_document(ReloadRequest(3, path, 200), results)
    ready, complete = results.get_nowait(), results.get_nowait()
    try:
        assert ready.data.unit_archive.initial_unit_index == 2
        wait_for(lambda: ready.data.unit_archive.cache_count == ready.data.n_units)
        assert complete.data is ready.data
        assert not complete.errors
    finally:
        ready.data.close()


def test_bad_tuning_does_not_prevent_probe_and_waveform_reload(tmp_path):
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(base_payload()))
    tuning_path = tmp_path / "manual.tc"
    tuning_path.write_text("unfinished JSON")
    positions = tmp_path / "positions.csv"
    positions.write_text("unit_index,unit_id,x_um,y_um\n0,42,10,20\n")
    old_geometry = load_probe_geometry("A", positions)
    positions.write_text("unit_index,unit_id,x_um,y_um\n0,42,30,40\n")
    results = SimpleQueue()
    waveform = {"unit_id": 42}
    with (
        mock.patch.object(RFMappingData, "waveform_store", return_value=object()),
        mock.patch.object(RFMappingData, "waveform_plot_payload", return_value=waveform),
    ):
        reload_document(ReloadRequest(
            1, path, 42, tuning_path=tuning_path,
            probe_geometry=old_geometry, load_waveform=True,
        ), results)
    ready, complete = results.get_nowait(), results.get_nowait()
    try:
        assert set(complete.errors) == {"tuning"}
        assert complete.tuning_data is None
        assert complete.loaded == frozenset({"probe", "waveform"})
        assert complete.probe_geometry.units[0].x_um == 30
        assert old_geometry.units[0].x_um == 10
        assert complete.waveform_payload is waveform
    finally:
        ready.data.close()


def test_rf_failure_publishes_only_error(tmp_path):
    path = tmp_path / "mapping.json"
    path.write_text("unfinished JSON")
    results = SimpleQueue()
    reload_document(ReloadRequest(5, path, 42), results)
    failure = results.get_nowait()
    assert failure.stage == "error"
    assert failure.data is None
    assert set(failure.errors) == {"rf"}
    assert results.empty()


def test_cancellation_after_rf_handoff_leaves_close_to_caller(tmp_path):
    _old, path, _arrays = write_pair(tmp_path)
    results = SimpleQueue()
    with mock.patch.object(RFMappingData, "close", autospec=True) as close:
        reload_document(ReloadRequest(2, path, 200), results, cancelled=lambda: not results.empty())
        ready = results.get_nowait()
        assert ready.stage == "rf"
        assert results.empty()
        close.assert_not_called()
    ready.data.close()
