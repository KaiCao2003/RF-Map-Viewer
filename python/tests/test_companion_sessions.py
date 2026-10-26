"""Waveforms and positions belong to the RF session, independently of HD."""

import json
from queue import SimpleQueue
from types import SimpleNamespace

import pytest

import rfmapping_gui as gui
from rfmapping_viewer.companions import (
    discover_probe_geometry,
    discover_probe_geometry_paths,
)
from rfmapping_viewer.document_reload import ReloadRequest, reload_document
from rfmapping_viewer.figure_composer import GUIFigureDataProvider
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.waveform import discover_waveform_artifact
from test_gui_figure_export import _snapshot
from test_indexed_rfmap import write_pair
from test_waveform import _write_artifact


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    monkeypatch.delenv("RF_MAPPING_PROBE_DATA_ROOT", raising=False)
    sources = {}
    for number in (1, 3):
        data_root = tmp_path / "260625" / f"260625_{number}" / "data"
        rf_root = data_root / "rfmapping" / "good" / "ProbeA"
        rf_root.mkdir(parents=True)
        json_rf, indexed_rf, _ = write_pair(rf_root)
        tuning = data_root / "tuning_curves" / "ProbeA" / "tuning_curves.tc"
        tuning.parent.mkdir(parents=True)
        tuning.write_text(json.dumps({"41": [float(number)] * 180}))
        positions = data_root / "spike_position" / "ProbeA" / "positions.probe"
        positions.parent.mkdir(parents=True)
        positions.write_text(
            "unit_index,unit_id,x_um,y_um\n"
            f"0,41,{number * 10},{number * 100}\n"
        )
        waveform = _write_artifact(data_root / "waveform" / "ProbeA", unit_ids=(41,))
        sources[number] = dict(
            json=json_rf, indexed=indexed_rf, tuning=tuning,
            positions=positions, waveform=waveform, data_root=data_root,
        )
    return sources


@pytest.mark.parametrize("storage", ["json", "indexed"])
def test_changing_tc_keeps_rf_companions_in_viewer_and_export(sessions, storage):
    rf = sessions[1]
    data = RFMappingData(rf[storage])
    try:
        # Attach HD first so the first lazy companion read must choose RF.
        for number in (3, 1, 3):
            tuning = data.attach_hd_tuning(sessions[number]["tuning"])
            assert tuning.path == sessions[number]["tuning"]
            geometry = data.probe_geometry()
            waveform = data.waveform_payload(41, "same_x_column")
            assert geometry.positions_path == rf["positions"]
            assert (geometry.units[0].x_um, geometry.units[0].y_um) == (10, 100)
            assert geometry.channels_path == rf["waveform"] / "channels.csv"
            assert waveform.source_dir == rf["waveform"]

            provider = GUIFigureDataProvider(data, _snapshot())
            assert provider.hd_tuning.path == sessions[number]["tuning"]
            assert provider.probe_geometry.positions_path == rf["positions"]
            assert provider.waveform_store.payload_for(41).source_dir == rf["waveform"]
    finally:
        data.close()


@pytest.mark.parametrize("storage", ["json", "indexed"])
def test_changing_rf_with_same_tc_uses_new_rf_session(sessions, storage):
    for number in (1, 3):
        rf = sessions[number]
        data = RFMappingData(rf[storage])
        try:
            data.attach_hd_tuning(sessions[3]["tuning"])
            geometry = data.probe_geometry()
            assert geometry.positions_path == rf["positions"]
            assert geometry.units[0].y_um == number * 100
            assert data.waveform_payload(41, "same_x_column").source_dir == rf["waveform"]
        finally:
            data.close()


@pytest.mark.parametrize("storage", ["json", "indexed"])
def test_rf_companions_ignore_another_sessions_environment_root(
    sessions, storage, monkeypatch,
):
    rf = sessions[1]
    monkeypatch.setenv("RF_MAPPING_PROBE_DATA_ROOT", str(sessions[3]["data_root"]))
    assert discover_probe_geometry_paths(rf[storage])[1] == rf["positions"]
    # The deferred Tk loader uses this reader, while exports use the paths reader.
    assert discover_probe_geometry(rf[storage]).positions_path == rf["positions"]


@pytest.mark.parametrize("storage", ["json", "indexed"])
def test_missing_rf_companions_do_not_fall_back_to_hd(sessions, storage, monkeypatch):
    rf = sessions[1]
    rf["positions"].unlink()
    (rf["waveform"] / "manifest.json").unlink()
    monkeypatch.setenv("RF_MAPPING_PROBE_DATA_ROOT", str(sessions[3]["data_root"]))
    data = RFMappingData(rf[storage])
    try:
        data.attach_hd_tuning(sessions[3]["tuning"])
        assert data.probe_geometry() is None
        assert discover_probe_geometry(rf[storage]) is None
        assert data.waveform_store() is None
    finally:
        data.close()


@pytest.mark.parametrize("suffix", [".tc", ".json"])
def test_hd_source_without_rf_discovers_its_own_session(sessions, suffix, monkeypatch):
    hd = sessions[3]
    for source in sessions.values():
        source["json"].unlink()
        source["indexed"].unlink()
    path = hd["tuning"].with_suffix(suffix)
    if path != hd["tuning"]:
        path.write_bytes(hd["tuning"].read_bytes())
    monkeypatch.setenv("RF_MAPPING_PROBE_DATA_ROOT", str(sessions[1]["data_root"]))
    assert discover_probe_geometry_paths(path)[1] == hd["positions"]
    assert discover_probe_geometry(path).positions_path == hd["positions"]
    assert discover_waveform_artifact(path) == hd["waveform"]


@pytest.mark.parametrize("storage", ["json", "indexed"])
def test_reload_keeps_rf_positions_and_waveform_with_other_session_tc(sessions, storage):
    rf = sessions[1]
    results = SimpleQueue()
    reload_document(ReloadRequest(
        1, rf[storage], 41, tuning_path=sessions[3]["tuning"],
        load_probe=True, load_waveform=True,
    ), results)
    ready, complete = results.get_nowait(), results.get_nowait()
    try:
        assert not complete.errors
        assert {"probe", "waveform", "tuning"} <= complete.loaded
        assert complete.tuning_data.path == sessions[3]["tuning"]
        assert complete.probe_geometry.positions_path == rf["positions"]
        assert complete.data.waveform_payload(41, "same_x_column").source_dir == rf["waveform"]
    finally:
        ready.data.close()


def test_deferred_gui_autoload_uses_rf_session_with_tc_from_other_session(sessions, monkeypatch):
    rf = sessions[1]
    monkeypatch.setenv("RF_MAPPING_PROBE_DATA_ROOT", str(sessions[3]["data_root"]))
    data = RFMappingData(rf["json"])
    viewer = SimpleNamespace(data=data, _optional_result_queue=SimpleQueue())
    try:
        gui.RFMViewer._optional_autoload_worker(viewer, dict(
            generation=1, data=data, data_path=data.path, load_probe=True,
            load_tuning=True, tuning_session=3, cluster_id=41,
            tuning_bins=30, tuning_smoothing=False, tuning_sigma=1.5,
        ))
        result = viewer._optional_result_queue.get_nowait()
        assert result["worker_error"] is None
        assert result["tuning_data"].path == sessions[3]["tuning"]
        assert result["probe_geometry"].positions_path == rf["positions"]
    finally:
        data.close()
