"""Missing HD must not hide an RF unit's waveform or probe selection."""

import json
import time
from unittest import mock

import pytest

import rfmapping_gui as gui
from gui_test_support import tk_test_root
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.settings import ViewerSettings
from test_companion_sessions import sessions


@pytest.mark.parametrize("storage", ["json", "indexed"])
@pytest.mark.parametrize("tuning_state", ["missing_file", "missing_unit", "hidden"])
def test_rf_waveform_and_selected_position_display_without_tc(
    sessions, storage, tuning_state, tmp_path,
):
    tk_test_root()
    if tuning_state == "missing_file":
        for source in sessions.values():
            source["tuning"].unlink()
    elif tuning_state == "missing_unit":
        sessions[3]["tuning"].write_text(json.dumps({"7": [1.0] * 180}))
    settings = ViewerSettings(
        rf_filter_units_with_zero_bins=False,
        auto_load_probe_layout=True, show_probe_layout=True,
        auto_load_tuning_curve=True, show_tuning_curve=tuning_state != "hidden",
        tuning_curve_session=3, show_waveform=True,
    )
    with (
        mock.patch.object(gui, "viewer_settings_path", return_value=tmp_path / "settings.json"),
        mock.patch.object(gui, "load_viewer_settings", return_value=settings),
        mock.patch.object(gui, "list_recent_documents", return_value=[]),
        mock.patch.object(gui, "record_recent_document"),
    ):
        viewer = gui.RFMViewer(RFMappingData(sessions[1][storage]))
        try:
            viewer.geometry("1120x800")
            viewer._select_tab_key("rf")
            viewer._set_selected_unit_id(41)
            viewer._update_all()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                viewer.update()
                if viewer.probe_geometry is not None and viewer.waveform_payload is not None:
                    break
                time.sleep(0.005)
            viewer._update_all()
            viewer.update()
            assert viewer._waveform_error is None
            assert viewer.waveform_payload["unit_id"] == 41
            assert viewer.waveform_pane.winfo_ismapped()
            assert len(viewer.waveform_canvas.find_all()) > 20
            assert viewer.probe_geometry.positions_path == sessions[1]["positions"]
            assert viewer.probe_canvas.find_withtag("probe-selection")
            assert [point[2].unit_id for point in viewer._probe_hit_points] == [41]
            if tuning_state == "missing_unit":
                assert viewer.tuning_curve_data.rates_for(41) is None
                assert any(
                    "No tuning curve for cluster 41" in viewer.tuning_curve_canvas.itemcget(item, "text")
                    for item in viewer.tuning_curve_canvas.find_all()
                    if viewer.tuning_curve_canvas.type(item) == "text"
                )
            else:
                assert viewer.tuning_curve_data is None
        finally:
            viewer._quit_application()
