"""ADN annotations remain separate from the viewer's displayed RF population."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import pytest
import numpy as np

import rfmapping_gui as gui
from rfmapping_viewer.adn_selection import load_adn_units, save_adn_units
from rfmapping_viewer.figure_composer import FigureViewerSnapshot, _figure_snapshot_metadata
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.settings import ViewerSettings
from gui_test_support import current_rf_payload, tk_test_root


@pytest.fixture
def make_viewer(tmp_path):
    tk_test_root()
    viewers = []
    settings = ViewerSettings(
        auto_load_probe_layout=False,
        auto_load_tuning_curve=False,
        show_tuning_curve=False,
        show_waveform=False,
        rf_filter_units_with_zero_bins=False,
        rf_sum_start_ms=0.0,
        rf_sum_end_ms=100.0,
    )

    def create(session="261006_3", probe="A", *, master=None):
        session_path = tmp_path / "m21" / session
        path = session_path / "data" / "rf" / f"regular_{session}_{probe}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current_rf_payload({
            "unitsSpikeCounts": [[[[2, 2], [0, 0]]], [[[3, 3], [4, 4]]]],
            "unitsSpikeCountsSize": [2, 1, 2, 2],
            "unitPool": [8, 7],
            "xPositions": [-1, 1],
            "yPositions": [0],
            "timeBinEdges": [0.0, 0.05, 0.1],
        })), encoding="utf-8")
        positions = session_path / "data" / "spike_position" / f"Probe{probe}" / "positions.csv"
        positions.parent.mkdir(parents=True, exist_ok=True)
        positions.write_text(
            "unit_index,unit_id,x_um,y_um\n"
            "0,8,300,300\n1,7,7.5,120\n2,99,20,130\n",
            encoding="utf-8",
        )
        viewer = gui.RFMViewer(RFMappingData(path), master=master)
        viewers.append(viewer)
        assert viewer._load_probe_geometry_path(positions)
        viewer.update()
        viewer._draw_probe_canvas()
        return viewer

    with (
        mock.patch.object(gui, "viewer_settings_path", return_value=tmp_path / "settings.json"),
        mock.patch.object(gui, "load_viewer_settings", return_value=settings),
        mock.patch.object(gui, "list_recent_documents", return_value=[]),
        mock.patch.object(gui, "record_recent_document"),
        mock.patch.object(gui.RFMViewer, "_schedule_optional_autoload"),
    ):
        yield create
        roots = {viewer._app_root for viewer in viewers}
        for root in roots:
            windows = list(root._rfm_viewer_windows)
            if windows:
                windows[0]._quit_application()


def select_adn(viewer, unit_ids):
    save_adn_units(viewer._adn_path, viewer.probe_geometry.probe_name, unit_ids)
    viewer.only_adn_var.set(True)
    viewer._on_only_adn_toggled()


def test_only_adn_intersects_quality_filter_and_preserves_annotation(make_viewer):
    viewer = make_viewer()
    select_adn(viewer, [7, 8, 99])
    assert viewer._local_visible_unit_ids() == [8, 7]
    assert viewer.adn_unit_ids == {7, 8, 99}

    viewer.settings = replace(viewer.settings, rf_filter_units_with_zero_bins=True)
    viewer._reconcile_unit_filter_selection()
    viewer._sync_unit_combo()
    assert viewer._local_visible_unit_ids() == [7]
    assert viewer._unit_combo_unit_ids == [7]
    assert viewer.adn_unit_ids == {7, 8, 99}

    viewer.only_adn_var.set(False)
    viewer._on_only_adn_toggled()
    assert viewer._local_visible_unit_ids() == [7]
    assert viewer.adn_unit_ids == {7, 8, 99}


def test_only_adn_changes_navigation_probe_dots_and_figure_snapshot(make_viewer):
    viewer = make_viewer()
    select_adn(viewer, [7, 99])
    assert viewer._unit_navigation_ids() == [7]
    assert viewer._unit_combo_unit_ids == [7]
    assert viewer._selected_unit_id_value() == 7
    snapshot = FigureViewerSnapshot.capture(viewer)
    assert snapshot.visible_unit_ids == (7,)
    assert snapshot.only_adn is True
    assert snapshot.adn_unit_ids == (7, 99)
    assert _figure_snapshot_metadata(viewer.data, snapshot)["adnFilter"] == {
        "enabled": True, "unitIds": [7, 99], "probe": "ProbeA",
    }

    dots = [
        item for item in viewer.probe_canvas.find_withtag("probe-static")
        if viewer.probe_canvas.type(item) == "oval"
    ]
    assert len(dots) == 1
    viewer._step_unit(1)
    assert viewer._selected_unit_id_value() == 7


def test_saved_adn_without_current_rf_units_is_an_empty_view(make_viewer):
    viewer = make_viewer()
    select_adn(viewer, [99])
    assert viewer.adn_unit_ids == {99}
    assert viewer._local_visible_unit_ids() == []
    assert viewer._unit_combo_unit_ids == []
    assert viewer.unit_idx.get() == -1
    assert FigureViewerSnapshot.capture(viewer).visible_unit_ids == ()


def test_probe_hover_reports_unit_id_and_physical_coordinates(make_viewer):
    viewer = make_viewer()
    point = viewer._probe_to_canvas(7.5, 120.0)
    assert point is not None
    viewer._on_probe_motion(SimpleNamespace(x=point[0], y=point[1]))
    texts = [
        viewer.probe_canvas.itemcget(item, "text")
        for item in viewer.probe_canvas.find_withtag("hover")
        if viewer.probe_canvas.type(item) == "text"
    ]
    assert texts == ["ID: 7\nx: 7.5 µm\ny: 120 µm"]
    left, top, right, bottom = viewer.probe_canvas.bbox("hover")
    assert 0 <= left < right <= viewer.probe_canvas.winfo_width()
    assert 0 <= top < bottom <= viewer.probe_canvas.winfo_height()
    viewer.probe_canvas.event_generate("<Leave>")
    assert viewer.probe_canvas.find_withtag("hover") == ()


def test_only_adn_hides_non_adn_probe_hover_target(make_viewer):
    viewer = make_viewer()
    select_adn(viewer, [7])
    point = viewer._probe_to_canvas(300.0, 300.0)
    assert point is not None
    viewer._on_probe_motion(SimpleNamespace(x=point[0], y=point[1]))
    assert viewer.probe_canvas.find_withtag("hover") == ()


def test_annotation_dialog_receives_full_geometry_not_rf_subset(make_viewer):
    viewer = make_viewer()
    assert [unit.unit_id for unit in viewer.probe_geometry.units] == [8, 7]
    select_adn(viewer, [7])
    with mock.patch.object(gui, "ADNAnnotationWindow") as dialog:
        viewer._open_adn_annotation()
    args = dialog.call_args.args
    assert args[0] is viewer
    assert [unit.unit_id for unit in args[1].units] == [8, 7, 99]
    assert args[2] == viewer._adn_path
    assert viewer.adn_unit_ids == {7}


def test_saving_adn_updates_only_same_session_and_probe(make_viewer):
    viewer = make_viewer()
    same = make_viewer(master=viewer._app_root)
    other_probe = make_viewer(probe="B", master=viewer._app_root)
    other_session = make_viewer(session="261007_3", master=viewer._app_root)
    other_probe.adn_unit_ids = {8}
    other_session.adn_unit_ids = {8}

    viewer._on_adn_saved({7, 99})
    assert viewer.adn_unit_ids == {7, 99}
    assert same.adn_unit_ids == {7, 99}
    assert other_probe.adn_unit_ids == {8}
    assert other_session.adn_unit_ids == {8}


def test_paired_selection_cannot_clear_only_adn_filter(make_viewer):
    viewer = make_viewer()
    paired = make_viewer(master=viewer._app_root)
    select_adn(viewer, [7])
    viewer.pair_windows_var.set(True)
    viewer._on_pair_windows_toggled()
    assert viewer._app_root._rfm_pairing_enabled
    assert not paired.only_adn_var.get()
    assert 8 in paired._unit_navigation_ids()

    incoming = replace(paired._capture_pairing_state(), unit_id=8)
    viewer._apply_pairing_state(incoming, frozenset({"unit"}))
    assert viewer.only_adn_var.get()
    assert viewer.adn_unit_ids == {7}
    assert viewer._unit_navigation_ids() == [7]
    assert viewer._selected_unit_id_value() == 8
    assert viewer._selected_local_unit_index() is None


def open_annotation(viewer):
    viewer._open_adn_annotation()
    dialog = viewer._adn_window
    assert dialog is not None
    dialog.update()
    dialog._draw()
    return dialog


def annotation_event(dialog, x_um, y_um):
    x, y = dialog._to_canvas(x_um, y_um)
    return SimpleNamespace(
        x=x - dialog.canvas.canvasx(0),
        y=y - dialog.canvas.canvasy(0),
    )


def draw_lasso(dialog, vertices):
    events = [annotation_event(dialog, *point) for point in vertices]
    dialog._start_lasso(events[0])
    for event in events[1:]:
        dialog._extend_lasso(event)
    assert dialog.canvas.find_withtag("lasso")
    dialog._finish_lasso(events[0])
    assert dialog.canvas.find_withtag("lasso") == ()


def test_annotation_lassos_add_units_and_undo_restores_selection(make_viewer):
    dialog = open_annotation(make_viewer())
    draw_lasso(dialog, [(0, 110), (12, 110), (12, 125), (0, 125)])
    assert dialog.selected_ids == {7}
    assert dialog.canvas.find_withtag("contour")

    draw_lasso(dialog, [(290, 290), (310, 290), (310, 310), (290, 310)])
    assert dialog.selected_ids == {7, 8}
    dialog._undo()
    assert dialog.selected_ids == {7}
    dialog._clear()
    assert dialog.selected_ids == set()
    assert dialog.canvas.find_withtag("contour") == ()
    dialog._undo()
    assert dialog.selected_ids == {7}
    assert dialog.unit_list.get("1.0", "end").strip() == "7"


def test_annotation_save_reloads_ids_and_updates_viewer_without_losing_other_probe(make_viewer):
    viewer = make_viewer()
    select_adn(viewer, [])
    save_adn_units(viewer._adn_path, "ProbeB", [8])
    dialog = open_annotation(viewer)
    draw_lasso(dialog, [(0, 110), (30, 110), (30, 140), (0, 140)])
    assert dialog.selected_ids == {7, 99}
    dialog._save()

    rows = np.load(viewer._adn_path, allow_pickle=False)
    assert set(zip(rows["probe"], rows["unit_id"])) == {
        ("ProbeA", 7), ("ProbeA", 99), ("ProbeB", 8),
    }
    assert load_adn_units(viewer._adn_path, "ProbeA") == {7, 99}
    assert viewer.adn_unit_ids == {7, 99}
    assert viewer._unit_navigation_ids() == [7]
    assert "Saved 2 ADN units" in dialog.status_var.get()
    dialog.destroy()

    reopened = open_annotation(viewer)
    assert reopened.selected_ids == {7, 99}
    assert reopened.unit_list.get("1.0", "end").strip() == "7, 99"


def test_annotation_zoom_and_scroll_preserve_hover_and_lasso_coordinates(make_viewer):
    dialog = open_annotation(make_viewer())
    dialog._set_zoom(4.0)
    dialog.canvas.yview_moveto(1.0)
    dialog.update()
    assert dialog.canvas.canvasy(0) > 0

    event = annotation_event(dialog, 20.0, 130.0)
    assert 0 <= event.y <= dialog.canvas.winfo_height()
    dialog._hover(event)
    texts = [
        dialog.canvas.itemcget(item, "text")
        for item in dialog.canvas.find_withtag("hover")
        if dialog.canvas.type(item) == "text"
    ]
    assert texts == ["ID: 99\nx: 20 µm\ny: 130 µm"]

    draw_lasso(dialog, [(0, 110), (30, 110), (30, 140), (0, 140)])
    assert dialog.selected_ids == {7, 99}
    dialog._set_zoom(1.0)
    assert dialog.selected_ids == {7, 99}
    assert dialog.canvas.find_withtag("contour")


def test_annotation_precise_trackpad_scroll_moves_depth_and_clears_hover(make_viewer):
    viewer = make_viewer()
    if not viewer.tk.call("info", "commands", "tk::PreciseScrollDeltas"):
        pytest.skip("requires Tk 9 precise trackpad events")
    dialog = open_annotation(viewer)
    dialog._set_zoom(4.0)
    dialog.canvas.yview_moveto(0.5)
    dialog.update()
    dialog.canvas.create_text(10, 10, text="hover", tags=("hover",))
    before = dialog.canvas.canvasy(0)

    dialog.canvas.event_generate("<TouchpadScroll>", delta=(-20 & 0xffff))
    down = dialog.canvas.canvasy(0)
    assert down > before
    assert dialog.canvas.find_withtag("hover") == ()

    dialog.canvas.event_generate("<TouchpadScroll>", delta=(20 << 16))
    assert dialog.canvas.canvasy(0) == down
    dialog.canvas.event_generate("<TouchpadScroll>", delta=20)
    assert dialog.canvas.canvasy(0) < down
    assert dialog.selected_ids == set()


def test_source_switch_closes_annotation_and_resets_only_adn(make_viewer):
    viewer = make_viewer()
    next_session = make_viewer(session="261007_3", master=viewer._app_root)
    select_adn(viewer, [7])
    original_adn_path = viewer._adn_path
    dialog = open_annotation(viewer)
    viewer._load_json_path(next_session.data.path)

    assert not dialog.winfo_exists()
    assert viewer._adn_window is None
    assert not viewer.only_adn_var.get()
    assert viewer.adn_unit_ids == set()
    assert viewer._adn_path == next_session._adn_path
    assert viewer._adn_path != original_adn_path
    assert viewer._unit_navigation_ids() == [8, 7]
    assert load_adn_units(original_adn_path, "ProbeA") == {7}
