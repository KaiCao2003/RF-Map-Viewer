"""Saved-result path preferences apply to settings, open documents and reloads."""

from dataclasses import replace
import json

import pytest

import rfmapping_gui as gui
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.rf_result_overlay import RFResultOverlayCache
from rfmapping_viewer.rf_results import DEFAULT_RF_RESULT_LAYOUT_PATTERN, RFResultSource
from rfmapping_viewer.settings import ViewerSettings, load_viewer_settings, save_viewer_settings
from rfmapping_viewer.tk_support import TK_AVAILABLE, ttk
from test_document_reload_tk import complete_reload, paused_reload, wait_for
from test_rf_result_overlay_gui import app
from test_rf_results import write_result


CUSTOM_PATTERN = r".+/mapping"
requires_tk = pytest.mark.skipif(not TK_AVAILABLE, reason="Tk is unavailable")


def copy_short_results(app):
    for old_name, new_name in (
        ("mapping.npz", "rf_2d_ext.npz"),
        ("mapping_1d.npz", "rf_1d_ext.npz"),
    ):
        folder = app.data.path.parent
        (folder / new_name).write_bytes((folder / old_name).read_bytes())


def test_layout_pattern_settings_round_trip_and_old_preferences(tmp_path):
    assert ViewerSettings.from_mapping({}).rf_result_layout_pattern == DEFAULT_RF_RESULT_LAYOUT_PATTERN
    settings = replace(ViewerSettings(), rf_result_layout_pattern=CUSTOM_PATTERN)
    assert ViewerSettings.from_mapping(settings.to_mapping()) == settings
    path = tmp_path / "preferences.json"
    save_viewer_settings(settings, path)
    assert load_viewer_settings(path) == settings


@pytest.mark.parametrize("pattern", [None, 12, "", "  ", "[", r"(?P=missing)"])
def test_invalid_saved_layout_pattern_uses_default_without_resetting_other_settings(pattern):
    settings = ViewerSettings.from_mapping({
        "rf_result_layout_pattern": pattern, "show_waveform": False,
    })
    assert settings.rf_result_layout_pattern == DEFAULT_RF_RESULT_LAYOUT_PATTERN
    assert not settings.show_waveform


@requires_tk
def test_paths_settings_preview_validation_and_restore_default(app):
    app._show_settings()
    window = app._app_root._rfm_settings_window
    window.notebook.select(window._tab_widget_by_name["Paths"])
    window.update()
    assert window.rf_result_layout_pattern_entry.winfo_ismapped()
    assert "mapping.npz" in window.rf_result_path_preview_var.get()

    window.rf_result_layout_pattern_var.set(CUSTOM_PATTERN)
    preview = window.rf_result_path_preview_var.get()
    for name in ("rf_2d_ext.npz", "rf_1d_ext.npz", "rf_2d_inh.npz", "rf_1d_inh.npz"):
        assert name in preview
    window._commit(close=False)
    assert app.settings.rf_result_layout_pattern == CUSTOM_PATTERN
    assert load_viewer_settings(app._app_root._rfm_settings_path).rf_result_layout_pattern == CUSTOM_PATTERN

    window.rf_result_layout_pattern_var.set("[")
    assert "invalid" in window.rf_result_path_preview_var.get()
    window._commit(close=False)
    assert "regex" in window._tab_error_vars["Paths"].get()
    assert window.notebook.select() == str(window._tab_widget_by_name["Paths"])
    assert app.settings.rf_result_layout_pattern == CUSTOM_PATTERN
    assert load_viewer_settings(app._app_root._rfm_settings_path).rf_result_layout_pattern == CUSTOM_PATTERN

    restore = next(
        child for child in window.rf_result_layout_pattern_entry.master.winfo_children()
        if isinstance(child, ttk.Button) and child.cget("text") == "Restore default rule"
    )
    restore.invoke()
    assert window.rf_result_layout_pattern_var.get() == DEFAULT_RF_RESULT_LAYOUT_PATTERN
    assert "mapping.npz" in window.rf_result_path_preview_var.get()
    window._commit(close=False)
    assert app.settings.rf_result_layout_pattern == DEFAULT_RF_RESULT_LAYOUT_PATTERN


@requires_tk
def test_applying_pattern_rechecks_cached_results_and_failures_without_changing_unit(app):
    old_cache = app.results_pane.cache
    assert old_cache.result("2d", "excitatory").path.name == "mapping.npz"
    assert old_cache.result("2d", "inhibitory") is None
    copy_short_results(app)
    path, _manifest = write_result(app.results_pane.source, rf_type="inhibitory", ids=(7, 8))
    path.rename(path.with_name("rf_2d_inh.npz"))
    selected = app._selected_unit_id_value()

    assert app._apply_viewer_settings(
        replace(app.settings, rf_result_layout_pattern=CUSTOM_PATTERN), persist=True, broadcast=True,
    )

    cache = app.results_pane.cache
    assert cache is not old_cache
    assert app.results_pane.source.layout_pattern == CUSTOM_PATTERN
    assert cache.result("2d", "excitatory").path.name == "rf_2d_ext.npz"
    assert cache.result("2d", "inhibitory").path.name == "rf_2d_inh.npz"
    assert app._selected_unit_id_value() == selected
    assert app.results_pane.unit_id == selected


@requires_tk
def test_path_rule_updates_other_open_unpaired_document_without_changing_its_display(app):
    copy_short_results(app)
    other = gui.RFMViewer(RFMappingData(app.data.path), master=app._app_root)
    try:
        app._app_root._rfm_pairing_enabled = False
        other.settings = replace(other.settings, rf_palette="Viridis")
        other.palette_var.set("Viridis")
        app.update()
        assert app._apply_viewer_settings(
            replace(app.settings, rf_result_layout_pattern=CUSTOM_PATTERN), persist=True, broadcast=True,
        )
        assert other.settings.rf_result_layout_pattern == CUSTOM_PATTERN
        assert other.results_pane.source.layout_pattern == CUSTOM_PATTERN
        assert other.results_pane.cache.result("2d", "excitatory").path.name == "rf_2d_ext.npz"
        assert other.settings.rf_palette == "Viridis"
        assert other.palette_var.get() == "Viridis"
    finally:
        other.destroy()


@requires_tk
def test_document_reload_captures_custom_rule_and_loads_its_sidecars(app):
    copy_short_results(app)
    assert app._apply_viewer_settings(
        replace(app.settings, rf_result_layout_pattern=CUSTOM_PATTERN), persist=True, broadcast=True,
    )
    selected = app._selected_unit_id_value()
    app._reload_current_document()
    assert app._reload_request.result_layout_pattern == CUSTOM_PATTERN
    wait_for(app, lambda: not app._reload_active)
    assert app.results_pane.source.layout_pattern == CUSTOM_PATTERN
    assert app.results_pane.cache.result("2d", "excitatory").path.name == "rf_2d_ext.npz"
    assert app._selected_unit_id_value() == selected


@requires_tk
@pytest.mark.parametrize("changed_grid", [False, True])
def test_rule_changed_during_reload_is_not_replaced_by_stale_result_cache(app, monkeypatch, changed_grid):
    if changed_grid:
        payload = json.loads(app.data.path.read_text())
        payload["xPositions"] = [-60, 0, 60]
        payload["timeBinEdges"] = [-.2, -.1, 0, .1]
        app.data.path.write_text(json.dumps(payload))
    candidate = RFMappingData(app.data.path)
    requests = paused_reload(monkeypatch, candidate)
    app._reload_current_document()
    wait_for(app, lambda: app._reload_candidate is candidate)
    request = requests[0][0]
    assert request.result_layout_pattern == DEFAULT_RF_RESULT_LAYOUT_PATTERN
    stale_cache = RFResultOverlayCache(RFResultSource.from_document(candidate))
    if changed_grid:
        assert stale_cache.result("2d", "excitatory") is None
    else:
        assert stale_cache.result("2d", "excitatory").path.name == "mapping.npz"
    copy_short_results(app)
    assert app._apply_viewer_settings(
        replace(app.settings, rf_result_layout_pattern=CUSTOM_PATTERN), persist=True, broadcast=True,
    )

    complete_reload(
        app, request, candidate, result_cache=stale_cache,
        loaded=frozenset() if changed_grid else frozenset({"results"}),
    )

    assert app.results_pane.cache is not stale_cache
    assert app.settings.rf_result_layout_pattern == CUSTOM_PATTERN
    assert app.results_pane.source == RFResultSource.from_document(candidate, layout_pattern=CUSTOM_PATTERN)
    if changed_grid:
        # The copied short sidecars still describe the old 0–200 ms interval.
        assert app.results_pane.cache.result("2d", "excitatory") is None
        assert "outside this RF document" in app.results_pane.cache.error("2d", "excitatory")
    else:
        assert app.results_pane.cache.result("2d", "excitatory").path.name == "rf_2d_ext.npz"
