"""Loading indicators follow pending reads, independently of RF cache completion."""

from dataclasses import replace
import json
import threading

import pytest

import rfmapping_gui as gui
from rfmapping_viewer.tk_support import TK_AVAILABLE
from test_document_reload_tk import make_app, wait_for
from test_indexed_rfmap import write_pair


pytestmark = pytest.mark.skipif(not TK_AVAILABLE, reason="Tk is unavailable")


def waveform_payload(unit):
    return {
        "unit_id": unit, "matrix": [[-1.0, 0.0, 1.0]],
        "times_ms": [-0.1, 0.0, 0.1], "channel_labels": ["ch 1"],
        "best_channel_row": 0, "amplitude_limit_uv": 1.0, "max_ptp_uv": 2.0,
    }


def enable_autoload(app, *, probe=False):
    app.settings = replace(app.settings, auto_load_tuning_curve=True, auto_load_probe_layout=probe)
    app.show_tuning_curve_var.set(True)
    app.show_probe_layout_var.set(probe)
    app._schedule_optional_autoload()


def test_startup_phases_and_waveform_remain_visible_after_rf_cache_completes(
    tmp_path, make_app, monkeypatch,
):
    _json, path, _arrays = write_pair(tmp_path)
    app = make_app(path)
    tuning_path = tmp_path / "manual.tc"
    tuning_path.write_text(json.dumps({"200": [1.] * 180}))
    entered = {kind: threading.Event() for kind in ("probe", "tuning", "waveform")}
    release = {kind: threading.Event() for kind in entered}

    def read(kind, result):
        entered[kind].set()
        assert release[kind].wait(10), f"Test did not release {kind}"
        return result

    monkeypatch.setattr(gui, "discover_probe_geometry", lambda _path: read("probe", None))
    monkeypatch.setattr(gui, "discover_tuning_curve_path", lambda *_args: read("tuning", tuning_path))
    monkeypatch.setattr(app.data, "waveform_plot_payload", lambda unit, _mode: read("waveform", waveform_payload(unit)))
    app.show_waveform_var.set(True)
    app._request_waveform_payload()
    app._start_unit_cache()
    enable_autoload(app, probe=True)
    try:
        wait_for(app, lambda: entered["probe"].is_set() and entered["waveform"].is_set()
                 and "probe" in app.reload_label.cget("text"))
        assert app.data.unit_archive.cache_count == app.data.n_units
        assert app.reload_frame.winfo_ismapped()
        assert str(app.reload_progress.cget("mode")) == "indeterminate"
        release["probe"].set()
        wait_for(app, lambda: entered["tuning"].is_set() and "tuning" in app.reload_label.cget("text"))
        assert "waveform" in app.reload_label.cget("text")
        assert "200" in app.reload_label.cget("text")
        assert app.geometry().startswith("1120x720")
        app.update_idletasks()
        for widget in (app.reload_label, app.reload_progress):
            assert widget.winfo_ismapped()
            assert widget.winfo_rootx() >= app.winfo_rootx()
            assert widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + app.winfo_width()
        assert app.reload_label.winfo_width() >= app.reload_label.winfo_reqwidth()

        app._hide_unit_cache()
        app.update()
        assert not app.unit_cache_frame.winfo_ismapped()
        assert app.reload_frame.winfo_ismapped()
        release["tuning"].set()
        wait_for(app, lambda: app.tuning_curve_data is not None)
        assert app.reload_frame.winfo_ismapped()
        assert "waveform" in app.reload_label.cget("text")
        assert "tuning" not in app.reload_label.cget("text")
    finally:
        for event in release.values():
            event.set()
    wait_for(app, lambda: app._waveform_loading_key is None and app._optional_loading_stage is None)
    assert app.waveform_payload["unit_id"] == 200
    assert not app.reload_frame.winfo_ismapped()


@pytest.mark.parametrize("outcome", ["missing", "failed"])
def test_missing_or_failed_optional_companion_clears_busy_state(tmp_path, make_app, monkeypatch, outcome):
    path, _indexed, _arrays = write_pair(tmp_path)
    app = make_app(path)
    entered, release = threading.Event(), threading.Event()

    def discover(*_args):
        entered.set()
        assert release.wait(10), "Test did not release tuning discovery"
        if outcome == "failed":
            raise ValueError("tuning file is incomplete")
        return None

    monkeypatch.setattr(gui, "discover_tuning_curve_path", discover)
    enable_autoload(app)
    try:
        wait_for(app, lambda: entered.is_set() and "tuning" in app.reload_label.cget("text"))
        assert app.reload_frame.winfo_ismapped()
    finally:
        release.set()
    wait_for(app, lambda: app._optional_loading_stage is None)
    assert not app.reload_frame.winfo_ismapped()
    assert app.tuning_curve_data is None
    if outcome == "failed":
        assert "tuning file is incomplete" in app._tuning_curve_error


@pytest.mark.parametrize("outcome", ["success", "missing", "failed"])
def test_per_unit_waveform_loading_clears_on_completion(tmp_path, make_app, monkeypatch, outcome):
    path, _indexed, _arrays = write_pair(tmp_path)
    app = make_app(path)
    entered, release = threading.Event(), threading.Event()

    def read(unit, _mode):
        entered.set()
        assert release.wait(10), "Test did not release waveform read"
        if outcome == "failed":
            raise ValueError("waveform file is incomplete")
        return waveform_payload(unit) if outcome == "success" else None

    monkeypatch.setattr(app.data, "waveform_plot_payload", read)
    app.show_waveform_var.set(True)
    app._request_waveform_payload()
    try:
        wait_for(app, entered.is_set)
        assert "waveform" in app.reload_label.cget("text")
        assert "200" in app.reload_label.cget("text")
        assert app.reload_frame.winfo_ismapped()
        assert str(app.reload_progress.cget("mode")) == "indeterminate"
    finally:
        release.set()
    wait_for(app, lambda: app._waveform_loading_key is None)
    assert not app.reload_frame.winfo_ismapped()
    if outcome == "success":
        assert app.waveform_payload["unit_id"] == 200
    else:
        assert app.waveform_payload is None
        assert app._waveform_error_key[0] == 200


def test_new_optional_generation_ignores_old_progress_and_completion(tmp_path, make_app, monkeypatch):
    path, _indexed, _arrays = write_pair(tmp_path)
    app = make_app(path)
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    count = 0
    lock = threading.Lock()

    def discover(*_args):
        nonlocal count
        with lock:
            index = count
            count += 1
        entered[index].set()
        assert release[index].wait(10), "Test did not release tuning discovery"
        return None

    monkeypatch.setattr(gui, "discover_tuning_curve_path", discover)
    enable_autoload(app)
    try:
        wait_for(app, lambda: entered[0].is_set() and app._optional_loading_stage == "tuning")
        old_generation = app._optional_autoload_generation
        app._schedule_optional_autoload()
        wait_for(app, lambda: entered[1].is_set() and app._optional_loading_stage == "tuning")
        app._optional_result_queue.put({
            "generation": old_generation, "data_path": app.data.path, "loading": "probe",
        })
        release[0].set()
        app._optional_result_queue.put({"generation": old_generation, "data_path": app.data.path})
        app._poll_optional_results()
        app.update()
        assert app._optional_loading_stage == "tuning"
        assert "tuning" in app.reload_label.cget("text")
        assert "probe" not in app.reload_label.cget("text")
        assert app.reload_frame.winfo_ismapped()
    finally:
        for event in release:
            event.set()
    wait_for(app, lambda: app._optional_loading_stage is None)
    assert not app.reload_frame.winfo_ismapped()


def test_old_waveform_completion_does_not_clear_new_unit_loading(tmp_path, make_app, monkeypatch):
    path, _indexed, _arrays = write_pair(tmp_path)
    app = make_app(path)
    entered = {unit: threading.Event() for unit in (200, 3)}
    release = {unit: threading.Event() for unit in entered}

    def read(unit, _mode):
        entered[unit].set()
        assert release[unit].wait(10), "Test did not release waveform read"
        return waveform_payload(unit)

    monkeypatch.setattr(app.data, "waveform_plot_payload", read)
    app.show_waveform_var.set(True)
    app._request_waveform_payload()
    try:
        wait_for(app, entered[200].is_set)
        app._set_selected_unit_id(3)
        app._request_waveform_payload()
        release[200].set()
        wait_for(app, entered[3].is_set)
        assert app.reload_frame.winfo_ismapped()
        assert "unit 3" in app.reload_label.cget("text")
        assert "200" not in app.reload_label.cget("text")
        assert app.waveform_payload is None
    finally:
        for event in release.values():
            event.set()
    wait_for(app, lambda: app._waveform_loading_key is None)
    assert app.waveform_payload["unit_id"] == 3
    assert not app.reload_frame.winfo_ismapped()
