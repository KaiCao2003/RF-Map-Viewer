from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import pytest

from gui_test_support import tk_test_root
from rfmapping_viewer.settings import ViewerSettings, load_viewer_settings, save_viewer_settings
from rfmapping_viewer.tk_support import TK_AVAILABLE, tk


def test_overlay_defaults_and_persisted_custom_style(tmp_path):
    defaults = ViewerSettings()
    assert defaults.rf_result_overlay_mode == "None"
    assert defaults.rf_result_overlay_polarity == "excitatory"
    assert (defaults.rf_result_overlay_2d_color, defaults.rf_result_overlay_1d_color,
            defaults.rf_result_overlay_overlap_color) == ("#34c759", "#ffcc00", "#ff9500")
    custom = replace(
        defaults, rf_result_overlay_mode="Both", rf_result_overlay_polarity="inhibitory",
        rf_result_overlay_width=3.5, rf_result_overlay_2d_color="#135724",
        rf_result_overlay_1d_color="#246813", rf_result_overlay_overlap_color="#abcdef",
    )
    path = tmp_path / "settings.json"
    save_viewer_settings(custom, path)
    assert load_viewer_settings(path) == custom


@pytest.mark.parametrize("field,value", [
    ("rf_result_overlay_mode", "unsupported"),
    ("rf_result_overlay_polarity", "both"),
    ("rf_result_overlay_width", 0),
    ("rf_result_overlay_width", float("nan")),
    ("rf_result_overlay_width", True),
    ("rf_result_overlay_2d_color", "green"),
    ("rf_result_overlay_1d_color", "#123"),
    ("rf_result_overlay_overlap_color", "#zz0000"),
])
def test_invalid_overlay_preference_recovers_independently(field, value):
    restored = ViewerSettings.from_mapping({field: value, "rf_palette": "Inferno"})
    assert getattr(restored, field) == getattr(ViewerSettings(), field)
    assert restored.rf_palette == "Inferno"


@pytest.mark.skipif(not TK_AVAILABLE, reason="Tk is unavailable")
def test_settings_overlay_controls_validate_and_enable_together():
    from rfmapping_viewer.settings_window import SettingsValidationError, SettingsWindow

    root = tk_test_root()
    owner = tk.Toplevel(root)
    owner._app_root = root
    root._rfm_settings = ViewerSettings()
    owner._active_viewer = lambda: SimpleNamespace(data=SimpleNamespace(spatial_bin_count=6))
    owner._bind_unit_filter_shortcut = lambda _window: None
    with mock.patch.object(tk, "_default_root", root):
        window = SettingsWindow(owner)
    try:
        assert window.rf_result_overlay_width_entry.instate(["disabled"])
        window.rf_result_overlay_mode_var.set("Both")
        assert window.rf_result_overlay_width_entry.instate(["!disabled"])
        window.rf_result_overlay_polarity_var.set("inhibitory")
        window.rf_result_overlay_width_var.set("3.5")
        window.rf_result_overlay_overlap_color_var.set("#ABcDef")
        settings = window._validated_settings()
        assert settings.rf_result_overlay_mode == "Both"
        assert settings.rf_result_overlay_polarity == "inhibitory"
        assert settings.rf_result_overlay_width == 3.5
        assert settings.rf_result_overlay_overlap_color == "#abcdef"
        window.rf_result_overlay_width_var.set("0")
        with pytest.raises(SettingsValidationError, match="positive and finite") as error:
            window._validated_settings()
        assert error.value.tab_name == "RF Map"
        window.rf_result_overlay_width_var.set("2")
        window.rf_result_overlay_2d_color_var.set("invalid")
        with pytest.raises(SettingsValidationError, match="2D color"):
            window._validated_settings()
        with mock.patch("tkinter.colorchooser.askcolor", return_value=((1, 2, 3), "#010203")):
            window._choose_overlay_color(window.rf_result_overlay_2d_color_var, "2D color")
        assert window.rf_result_overlay_2d_color_var.get() == "#010203"
    finally:
        window.destroy()
        owner.destroy()
