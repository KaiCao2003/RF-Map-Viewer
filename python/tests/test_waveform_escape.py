from dataclasses import replace
from unittest import mock

import pytest

import rfmapping_gui as gui
from gui_test_support import base_payload, tk_test_root, write_payload
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.settings import ViewerSettings, save_viewer_settings


@pytest.fixture
def app(tmp_path):
    tk_test_root()
    directory, path = write_payload(base_payload())
    preferences = tmp_path / "settings.json"
    save_viewer_settings(replace(
        ViewerSettings(), rf_filter_units_with_zero_bins=False,
        show_probe_layout=False, auto_load_probe_layout=False,
        show_tuning_curve=False, auto_load_tuning_curve=False,
    ), preferences)
    with (
        mock.patch.object(gui, "viewer_settings_path", return_value=preferences),
        mock.patch.object(gui, "list_recent_documents", return_value=[]),
        mock.patch.object(gui, "record_recent_document"),
        mock.patch.object(gui.RFMViewer, "_request_waveform_payload"),
    ):
        viewer = gui.RFMViewer(RFMappingData(path))
        viewer.waveform_payload = {
            "matrix": [[-1.0, 1.0]], "times_ms": [-0.1, 0.1],
            "channel_labels": ["ch 1"], "best_channel_row": 0,
            "amplitude_limit_uv": 1.0, "max_ptp_uv": 2.0,
        }
        viewer.geometry("1320x840")
        viewer._select_tab_key("rf")
        viewer._draw_waveform()
        viewer.update()
        try:
            yield viewer
        finally:
            viewer._quit_application()
            directory.cleanup()


@pytest.mark.parametrize("focused_widget", (
    "waveform_zoom_canvas", "waveform_zoom_done_button", "unit_combo", "range_start_spin",
))
def test_one_escape_closes_waveform_without_clearing_filters(app, focused_widget):
    app._open_waveform_zoom()
    app.update()
    widget = getattr(app, focused_widget)
    widget.focus_force()
    app.update()
    assert app.focus_get() is widget
    assert app._waveform_zoomed
    assert app.waveform_zoom_overlay.winfo_ismapped()

    with (
        mock.patch.object(app, "_clear_spatial_filter") as clear_spatial,
        mock.patch.object(app, "_clear_timeline_selection") as clear_timeline,
    ):
        widget.event_generate("<Escape>")
        app.update()
        assert not app._waveform_zoomed
        assert app.waveform_zoom_overlay.winfo_manager() == ""
        clear_spatial.assert_not_called()
        clear_timeline.assert_not_called()


def test_escape_keeps_input_editing_behavior_outside_waveform_zoom(app):
    widget = app.range_start_spin
    widget.focus_force()
    app.update()
    with mock.patch.object(app, "_handle_escape") as clear_selection:
        widget.event_generate("<Escape>")
        app.update()
        clear_selection.assert_not_called()
