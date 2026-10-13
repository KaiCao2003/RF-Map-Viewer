from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import rfmapping_gui as gui
from test_adn_gui import make_viewer


def test_adn_utility_menu_routes_to_active_viewer(make_viewer):
    dormant = make_viewer()
    active = make_viewer(session="261007_3", master=dormant._app_root)
    active._on_window_focus()
    parent = dormant._menu
    if gui.sys.platform == "darwin":
        parent = parent.nametowidget(parent.entrycget("RF Map Viewer", "menu"))
    utilities = parent.nametowidget(parent.entrycget("Utilities", "menu"))

    with (
        mock.patch.object(dormant, "_open_adn_annotation") as open_dormant,
        mock.patch.object(active, "_open_adn_annotation") as open_active,
    ):
        utilities.invoke("Annotate ADN…")
        open_active.assert_called_once_with()
        open_dormant.assert_not_called()


def test_adn_utility_loads_configured_hd_classes_when_main_panel_is_hidden(make_viewer):
    viewer = make_viewer()
    viewer.settings = replace(viewer.settings, tuning_curve_session=6)
    assert not viewer.show_tuning_curve_var.get()
    assert viewer.tuning_curve_data is None
    tuning_path = viewer.data.path.parent / "configured_hd.tc"
    tuning = SimpleNamespace(hd_classes={7: 1, 8: 3, 99: 2})

    with (
        mock.patch.object(gui, "discover_tuning_curve_path", return_value=tuning_path) as discover,
        mock.patch.object(gui.TuningCurveData, "load", return_value=tuning) as load,
    ):
        viewer._open_adn_annotation()

    discover.assert_called_once_with(viewer.data.path, 6)
    load.assert_called_once_with(tuning_path)
    assert viewer._adn_window.hd_classes == tuning.hd_classes
    assert viewer._adn_window._visible_unit_ids() == {7, 8, 99}
    assert not viewer.show_tuning_curve_var.get()
    assert viewer.tuning_curve_data is None
