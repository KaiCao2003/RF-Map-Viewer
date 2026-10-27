"""A reload keeps the visible document usable until its replacement is ready."""

from contextlib import ExitStack
from dataclasses import replace
import json
import sys
import threading
import time
from unittest import mock

import numpy as np
import pytest

import rfmapping_gui as gui
from gui_test_support import tk_test_root
from rfmapping_viewer.companions import TuningCurveData
from rfmapping_viewer.companions import load_probe_geometry
from rfmapping_viewer.document_reload import DocumentReloadEvent
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.rf_result_overlay import RFResultOverlayCache
from rfmapping_viewer.settings import ViewerSettings, save_viewer_settings
from test_indexed_rfmap import write_pair


def wait_for(app, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        app.update()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "Timed out waiting for reload state"


@pytest.fixture
def make_app(tmp_path):
    tk_test_root()
    preferences = tmp_path / "settings.json"
    save_viewer_settings(replace(
        ViewerSettings(), rf_filter_units_with_zero_bins=False,
        show_tuning_curve=False, auto_load_tuning_curve=False,
        show_probe_layout=False, auto_load_probe_layout=False, show_waveform=False,
    ), preferences)
    viewers = []
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(gui, "viewer_settings_path", return_value=preferences))
        stack.enter_context(mock.patch.object(gui, "list_recent_documents", return_value=[]))
        stack.enter_context(mock.patch.object(gui, "record_recent_document"))

        def create(path):
            data = RFMappingData(path)
            for index in range(data.n_units):
                data.rf_map(index)
            app = gui.RFMViewer(data)
            viewers.append(app)
            app.geometry("1120x720")
            app._select_tab_key("rf")
            app._set_selected_unit_id(200)
            app._update_all()
            app.update()
            assert app.canvases["rf"].find_all()
            return app

        yield create
        for app in viewers:
            app._quit_application()


def paused_reload(monkeypatch, candidate):
    requests = []

    def worker(request, results, *, cancelled):
        requests.append((request, cancelled))
        results.put(DocumentReloadEvent(request.generation, "rf", data=candidate))

    monkeypatch.setattr(gui, "reload_document", worker)
    return requests


def complete_reload(app, request, candidate, **companions):
    app._reload_result_queue.put(DocumentReloadEvent(
        request.generation, "complete", data=candidate, **companions,
    ))
    wait_for(app, lambda: not app._reload_active)


def test_json_replacement_waits_for_companions_without_clearing_current_plot(tmp_path, make_app, monkeypatch):
    path, _indexed, _arrays = write_pair(tmp_path)
    app = make_app(path)
    previous = app.data
    old_counts = previous.rf_map_by_unit_id(200).spike_counts.copy()
    tuning_path = tmp_path / "manual.tc"
    tuning_path.write_text(json.dumps({"200": [1.0] * 180}))
    app.tuning_curve_data = TuningCurveData.load(tuning_path)
    old_tuning = app.tuning_curve_data
    payload = json.loads(path.read_text())
    payload["unitsSpikeCounts"] = (np.asarray(payload["unitsSpikeCounts"]) + 100).tolist()
    path.write_text(json.dumps(payload))
    candidate = RFMappingData(path)
    tuning_path.write_text(json.dumps({"200": [2.0] * 180}))
    new_tuning = candidate.attach_hd_tuning(tuning_path)
    requests = paused_reload(monkeypatch, candidate)

    app._reload_current_document()
    wait_for(app, lambda: app._reload_candidate is candidate)
    assert app.data is previous
    assert app.tuning_curve_data is old_tuning
    assert app._selected_unit_id_value() == 200
    assert app.canvases["rf"].find_all()
    assert app.reload_frame.winfo_ismapped()
    assert app.reload_frame.master is app.status_label.master
    assert int(app.reload_frame.grid_info()["column"]) > int(app.status_label.grid_info()["column"])
    assert "disabled" in app.export_toolbar_button.state()
    np.testing.assert_array_equal(app.data.rf_map_by_unit_id(200).spike_counts, old_counts)

    complete_reload(app, requests[0][0], candidate, tuning_data=new_tuning, loaded=frozenset({"tuning"}))
    assert app.data is candidate
    assert app.tuning_curve_data is new_tuning
    assert app._selected_unit_id_value() == 200
    np.testing.assert_array_equal(app.data.rf_map_by_unit_id(200).spike_counts, old_counts + 100)
    assert app.canvases["rf"].find_all()
    assert not app.reload_frame.winfo_ismapped()
    assert "disabled" not in app.export_toolbar_button.state()


@pytest.mark.parametrize("keysym", ["r", "R"])
@pytest.mark.parametrize("modifier", [
    "Control",
    pytest.param("Command", marks=pytest.mark.skipif(sys.platform != "darwin", reason="Command requires Cocoa")),
])
def test_reload_shortcut_invokes_worker_with_selected_unit(tmp_path, make_app, monkeypatch, modifier, keysym):
    path, _indexed, _arrays = write_pair(tmp_path)
    app = make_app(path)
    candidate = RFMappingData(path)
    requests = paused_reload(monkeypatch, candidate)
    canvas = app.canvases["rf"]
    canvas.focus_force()
    app.update()
    canvas.event_generate(f"<{modifier}-{keysym}>")
    wait_for(app, lambda: app._reload_candidate is candidate)
    assert len(requests) == 1
    assert requests[0][0].path == path
    assert requests[0][0].preferred_unit_id == 200
    assert app._selected_unit_id_value() == 200
    complete_reload(app, requests[0][0], candidate)


@pytest.mark.parametrize("changed_header", [False, True])
def test_indexed_reload_streams_current_first_and_keeps_old_units_until_ready(
    tmp_path, make_app, monkeypatch, changed_header,
):
    _json, path, arrays = write_pair(tmp_path)
    app = make_app(path)
    previous = app.data
    previous_counts = {unit: previous.rf_map_by_unit_id(unit).spike_counts.astype(np.int64) for unit in previous.unit_pool}
    for key in arrays:
        if key.startswith("unit_"):
            arrays[key] = arrays[key] + 500
    if changed_header:
        arrays["xPositions"] = arrays["xPositions"] + 10
    with path.open("wb") as stream:
        np.savez_compressed(stream, **arrays)
    with np.load(path, allow_pickle=False) as archive:
        archive_type = type(archive)
    original_read = archive_type.__getitem__
    blocked, release = threading.Event(), threading.Event()
    reads = []

    def read(archive, key):
        if key.startswith("unit_"):
            reads.append(int(key[5:]))
            if key == "unit_7":
                blocked.set()
                assert release.wait(10), "Test did not release the blocked indexed read"
        return original_read(archive, key)

    monkeypatch.setattr(archive_type, "__getitem__", read)
    app._reload_current_document()
    try:
        wait_for(app, lambda: blocked.is_set() and app._reload_candidate is not None)
        candidate = app._reload_candidate
        assert reads[:2] == [200, 7]
        assert candidate.unit_archive.cache_count == 1
        assert app._selected_unit_id_value() == 200
        assert app.canvases["rf"].find_all()
        assert app.reload_frame.winfo_ismapped()
        assert app._reload_active
        if changed_header:
            assert app.data is previous
            np.testing.assert_array_equal(app.data.rf_map_by_unit_id(200).spike_counts, previous_counts[200])
        else:
            assert app.data is candidate
            np.testing.assert_array_equal(app.data.rf_map_by_unit_id(200).spike_counts, previous_counts[200] + 500)
            np.testing.assert_array_equal(app.data.rf_map_by_unit_id(3).spike_counts, previous_counts[3])
            app.unit_combo.current(app._unit_combo_unit_ids.index(3))
            app.unit_combo.event_generate("<<ComboboxSelected>>")
            app.update()
            assert app._selected_unit_id_value() == 3
            assert app.canvases["rf"].find_all()
    finally:
        release.set()
    wait_for(app, lambda: not app._reload_active)
    assert app.data is candidate
    assert app._selected_unit_id_value() == (200 if changed_header else 3)
    for unit in candidate.unit_pool:
        np.testing.assert_array_equal(candidate.rf_map_by_unit_id(unit).spike_counts, previous_counts[unit] + 500)
    assert not app.reload_frame.winfo_ismapped()


def test_failed_reload_preserves_document_selection_and_canvas(tmp_path, make_app):
    path, _indexed, _arrays = write_pair(tmp_path)
    app = make_app(path)
    previous = app.data
    path.write_text("unfinished JSON")
    app._reload_current_document()
    wait_for(app, lambda: not app._reload_active)
    assert app.data is previous
    assert app._selected_unit_id_value() == 200
    assert app.canvases["rf"].find_all()
    assert not app.reload_frame.winfo_ismapped()
    assert "Reload stopped" in app.status_label.cget("text")
    assert "disabled" not in app.export_toolbar_button.state()


def test_navigation_during_reload_keeps_current_waveform_until_its_refresh_finishes(
    tmp_path, make_app, monkeypatch,
):
    path, _indexed, _arrays = write_pair(tmp_path)
    app = make_app(path)
    candidate = RFMappingData(path)
    requests = paused_reload(monkeypatch, candidate)
    mode = app.waveform_channel_mode_var.get()

    def waveform(unit, amplitude):
        return {
            "unit_id": unit, "matrix": [[-amplitude, 0.0, amplitude]],
            "times_ms": [-0.1, 0.0, 0.1], "channel_labels": ["ch 1"],
            "best_channel_row": 0, "amplitude_limit_uv": amplitude,
            "max_ptp_uv": 2 * amplitude,
        }

    app.show_waveform_var.set(True)
    app.waveform_payload = waveform(200, 5.0)
    app._waveform_payload_key = (200, mode)
    app._draw_waveform()
    app._reload_current_document()
    wait_for(app, lambda: app._reload_candidate is candidate)
    assert requests[0][0].preferred_unit_id == 200
    old_current = waveform(3, 10.0)
    app.waveform_payload = old_current
    app._waveform_payload_key = (3, mode)
    app.unit_combo.current(app._unit_combo_unit_ids.index(3))
    app.unit_combo.event_generate("<<ComboboxSelected>>")
    app.update()
    new_current = waveform(3, 20.0)
    blocked, release = threading.Event(), threading.Event()
    reads = []

    def load_waveform(unit, channel_mode):
        reads.append((unit, channel_mode))
        blocked.set()
        assert release.wait(10), "Test did not release the current waveform read"
        return new_current

    monkeypatch.setattr(candidate, "waveform_plot_payload", load_waveform)
    try:
        complete_reload(
            app, requests[0][0], candidate,
            waveform_payload=waveform(200, 30.0), loaded=frozenset({"waveform"}),
        )
        wait_for(app, blocked.is_set)
        assert reads == [(3, mode)]
        assert app._selected_unit_id_value() == 3
        assert app.waveform_payload is old_current
        assert app._waveform_payload_key == (3, mode)
        assert app.waveform_canvas.winfo_ismapped()
        assert len(app.waveform_canvas.find_all()) > 20
        assert "Loading…" not in [
            app.waveform_canvas.itemcget(item, "text")
            for item in app.waveform_canvas.find_all()
            if app.waveform_canvas.type(item) == "text"
        ]
    finally:
        release.set()
    wait_for(app, lambda: app.waveform_payload is new_current)
    assert app._selected_unit_id_value() == 3
    assert app._waveform_payload_key == (3, mode)
    assert app._waveform_loading_key is None


def test_switching_document_cancels_reload_and_ignores_stale_completion(tmp_path, make_app, monkeypatch):
    path, _indexed, _arrays = write_pair(tmp_path)
    app = make_app(path)
    candidate = RFMappingData(path)
    requests = paused_reload(monkeypatch, candidate)
    app._reload_current_document()
    wait_for(app, lambda: app._reload_candidate is candidate)
    request, cancelled = requests[0]
    other = tmp_path / "another.json"
    payload = json.loads(path.read_text())
    payload["unitPool"] = [501, 502, 503, 504]
    other.write_text(json.dumps(payload))
    with mock.patch.object(candidate, "close", wraps=candidate.close) as close:
        app._load_json_path(other)
        close.assert_called_once()
    assert cancelled()
    assert not app._reload_active
    replacement = app.data
    status_before_stale_event = app.reload_label.cget("text")
    app._reload_result_queue.put(DocumentReloadEvent(
        request.generation, "progress", loading="tuning",
    ))
    app._reload_result_queue.put(DocumentReloadEvent(request.generation, "complete", data=candidate))
    app._poll_document_reload()
    app.update()
    assert app.data is replacement
    assert app.data.path == other
    assert app._selected_unit_id_value() == 501
    assert app.canvases["rf"].find_all()
    assert not app.reload_frame.winfo_ismapped()
    assert app.reload_label.cget("text") == status_before_stale_event


@pytest.mark.parametrize("companion,fail", [
    ("tuning", False), ("probe", False), ("waveform", False), ("results", False),
    ("tuning", True),
])
def test_completed_rf_cache_stays_busy_until_actual_companion_read_finishes(
    tmp_path, make_app, monkeypatch, companion, fail,
):
    _json, path, _arrays = write_pair(tmp_path)
    app = make_app(path)
    tuning_path = tmp_path / "manual.tc"
    tuning_path.write_text(json.dumps({"200": [1.] * 180}))
    app.tuning_curve_data = app.data.attach_hd_tuning(tuning_path)
    previous_tuning = app.tuning_curve_data
    positions = tmp_path / "positions.csv"
    positions.write_text("unit_index,unit_id,x_um,y_um\n0,200,10,20\n")
    app.probe_geometry = load_probe_geometry("A", positions)
    app.show_waveform_var.set(True)
    waveform = {
        "unit_id": 200, "matrix": [[-1.0, 0.0, 1.0]],
        "times_ms": [-0.1, 0.0, 0.1], "channel_labels": ["ch 1"],
        "best_channel_row": 0, "amplitude_limit_uv": 1.0, "max_ptp_uv": 2.0,
    }
    app.waveform_payload = waveform
    app._waveform_payload_key = (200, app.waveform_channel_mode_var.get())
    monkeypatch.setattr(RFMappingData, "waveform_store", lambda _self: object())
    monkeypatch.setattr(RFMappingData, "waveform_plot_payload", lambda *_args: waveform)
    owner, method = {
        "tuning": (RFMappingData, "attach_hd_tuning"),
        "probe": (RFMappingData, "attach_probe_geometry"),
        "waveform": (RFMappingData, "waveform_store"),
        "results": (RFResultOverlayCache, "result"),
    }[companion]
    original = getattr(owner, method)
    entered, release = threading.Event(), threading.Event()

    def read(*args, **kwargs):
        if threading.current_thread() is threading.main_thread():
            return original(*args, **kwargs)
        entered.set()
        assert release.wait(10), "Test did not release the companion read"
        if fail:
            raise ValueError("companion replacement is incomplete")
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, method, read)
    app._reload_current_document()
    try:
        wait_for(app, lambda: entered.is_set() and app._reload_candidate is not None
                 and app._reload_candidate.unit_archive.cache_count == app._reload_candidate.n_units
                 and companion in str(app.reload_label.cget("text")).lower())
        assert app._reload_active
        assert app.reload_frame.winfo_ismapped()
        assert str(app.reload_progress.cget("mode")) == "indeterminate"
        assert f"{app.data.n_units} / {app.data.n_units}" in app.reload_label.cget("text")
        assert "disabled" in app.export_toolbar_button.state()
        assert app.canvases["rf"].find_all()
    finally:
        release.set()
    wait_for(app, lambda: not app._reload_active)
    assert not app.reload_frame.winfo_ismapped()
    assert "disabled" not in app.export_toolbar_button.state()
    if fail:
        assert app.tuning_curve_data is previous_tuning
