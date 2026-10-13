"""Keep the installed viewer's probe-hover preference during Python updates."""

from dataclasses import replace
from types import SimpleNamespace

from rfmapping_viewer.settings import ViewerSettings, load_viewer_settings
from test_adn_gui import make_viewer


def test_probe_hover_preference_round_trips():
    assert ViewerSettings().show_probe_unit_info
    disabled = replace(ViewerSettings(), show_probe_unit_info=False)
    assert ViewerSettings.from_mapping(disabled.to_mapping()) == disabled


def test_probe_hover_setting_saves_clears_tooltip_and_can_be_reenabled(make_viewer):
    viewer = make_viewer()
    point = viewer._probe_to_canvas(7.5, 120.0)
    assert point is not None
    event = SimpleNamespace(x=point[0], y=point[1])
    viewer._on_probe_motion(event)
    assert viewer.probe_canvas.find_withtag("hover")

    viewer._show_settings()
    settings = viewer._app_root._rfm_settings_window
    assert settings.show_probe_unit_info_var.get()
    settings.show_probe_unit_info_var.set(False)
    settings._commit(close=False)
    assert not viewer.settings.show_probe_unit_info
    assert viewer.probe_canvas.find_withtag("hover") == ()
    viewer._on_probe_motion(event)
    assert viewer.probe_canvas.find_withtag("hover") == ()
    assert not load_viewer_settings(viewer._app_root._rfm_settings_path).show_probe_unit_info

    settings.show_probe_unit_info_var.set(True)
    settings._commit(close=False)
    viewer._on_probe_motion(event)
    assert viewer.probe_canvas.find_withtag("hover")
    viewer._on_probe_motion(SimpleNamespace(x=event.x + 20, y=event.y))
    assert viewer.probe_canvas.find_withtag("hover") == ()
