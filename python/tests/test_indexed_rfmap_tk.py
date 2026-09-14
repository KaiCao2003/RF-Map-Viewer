"""The first plot and unit navigation remain responsive during disk reads."""

import threading
import time

import pytest

import rfmapping_gui as gui
import rfmapping_viewer.rf_archive as archive_module
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.settings import ViewerSettings
from test_indexed_rfmap import write_pair


def pump(app, predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        app.update()
        time.sleep(0.005)
    assert predicate()


@pytest.fixture
def indexed_viewer(tmp_path, monkeypatch):
    old, new, _ = write_pair(tmp_path)
    data = RFMappingData(new)
    started = threading.Event()
    release = threading.Event()
    original = archive_module._compact_array
    reads = []

    def slow(a, shape):
        reads.append(float(a.flat[0]))
        if len(reads) == 1:
            started.set()
            assert release.wait(10)
        return original(a, shape)

    monkeypatch.setattr(archive_module, "_compact_array", slow)
    monkeypatch.setattr(gui, "viewer_settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(
        gui,
        "load_viewer_settings",
        lambda _: ViewerSettings(
            show_tuning_curve=False,
            show_waveform=False,
            show_probe_layout=False,
            rf_filter_units_with_zero_bins=True,
        ),
    )
    app = gui.RFMViewer(data)
    root = app._app_root
    try:
        yield app, started, release, reads, old
    finally:
        release.set()
        if data.unit_archive._worker:
            data.unit_archive._worker.join(5)
        app.destroy()
        try:
            root.destroy()
        except gui.tk.TclError:
            pass


def test_first_plot_then_priority_navigation_and_progress(indexed_viewer):
    app, started, release, reads, _ = indexed_viewer
    assert app._viewer_ready and app.data.unit_archive.cache_count == 1
    assert app.canvases["rf"].find_all()
    app.update()
    assert started.wait(3) and app.unit_cache_frame.winfo_ismapped()
    beat = []
    app.after(5, lambda: beat.append(True))
    before = time.monotonic()
    app._set_selected_unit_id(3)
    app._update_all()
    assert time.monotonic() - before < 0.25
    assert app._selected_local_unit_index() is None
    assert "Loading cluster 3" in app._unit_loading_message()
    pump(app, lambda: bool(beat))
    assert app.data.unit_archive.cache_count == 1
    app.notebook.select(2)
    app._draw_active_tab()
    assert len(app.data.time_bin_edges) == app.data.n_bins + 1
    release.set()
    pump(
        app,
        lambda: app.data.unit_archive.cache_count == 4 and not app._unit_cache_waiting,
    )
    assert app._selected_local_unit_index() == 3
    assert reads == [12.0, 15.0, 4.0]
    assert float(app.unit_cache_progress["value"]) == 4
    pump(app, lambda: not app.unit_cache_frame.winfo_ismapped())


def test_switch_document_cancels_old_reader_and_ignores_late_results(indexed_viewer):
    app, started, release, _, old = indexed_viewer
    app.update()
    assert started.wait(3)
    previous = app.data
    before = time.monotonic()
    app._load_json_path(old)
    assert time.monotonic() - before < 0.5
    assert app.data.path == old and app.data.unit_archive is None
    release.set()
    previous.unit_archive._worker.join(5)
    app.update()
    assert previous.unit_archive.cache_count == 1
    assert not app.unit_cache_frame.winfo_ismapped()


def test_close_window_does_not_wait_for_disk(indexed_viewer):
    app, started, release, _, _ = indexed_viewer
    app.update()
    assert started.wait(3)
    before = time.monotonic()
    app.destroy()
    assert time.monotonic() - before < 0.25
    release.set()


def test_figures_wait_without_reading_uncached_units(indexed_viewer):
    app, started, release, _, _ = indexed_viewer
    app.update()
    assert started.wait(3)
    assert "disabled" in app.export_toolbar_button.state()
    before = time.monotonic()
    app._open_figure_exporter()
    assert time.monotonic() - before < 0.1
    assert app._figure_export_window is None
    release.set()
    pump(app, lambda: "disabled" not in app.export_toolbar_button.state())
