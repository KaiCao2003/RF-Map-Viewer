from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import rfmapping_gui as gui
import rfmapping_viewer.figure_composer as composer_module
from rfmapping_viewer.figure_composer import FigureExportWindow
from rfmapping_viewer.figure_export import PlotKind, render_live_preview
from rfmapping_viewer.rf_model import RFMappingData
from test_gui_figure_export import _snapshot, _write_fixture


@pytest.fixture
def viewer(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "viewer_settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(gui, "list_recent_documents", lambda: [])
    monkeypatch.setattr(gui, "record_recent_document", lambda _path: None)
    app = gui.RFMViewer(RFMappingData(_write_fixture(tmp_path)))
    if app._optional_autoload_after is not None:
        app.after_cancel(app._optional_autoload_after)
        app._optional_autoload_after = None
        app._optional_autoload_generation += 1
    app.settings = replace(app.settings, rf_filter_units_with_zero_bins=False)
    yield app
    app._quit_application()


def rendered_preview(window):
    raw = window._export_pages()
    pages, metadata, provider = window._preview_context((42,), raw, lambda: False)
    plan = window._preview_plan((42,), pages, metadata)
    image = render_live_preview(plan, 42, 0, data_provider=provider)
    try:
        return image.tobytes()
    finally:
        image.close()


@pytest.mark.parametrize("subtract", (None, (0, 0)))
def test_layout_dialog_round_trip_recreates_sum_and_timeline(
    viewer, tmp_path, monkeypatch, subtract,
):
    viewer._open_figure_exporter()
    original = viewer._figure_export_window
    original.snapshot = replace(
        _snapshot(), time_groups=((0, 1), (2, 3)), timeline_range_start=0,
        timeline_range_end=1, timeline_active_bin=1,
        rf_subtract_source_range=subtract,
        visible_unit_ids=original.snapshot.visible_unit_ids,
    )
    original.pages = [{"name": "RF and time", "plots": [PlotKind.RF_CARTESIAN, PlotKind.TIMELINE_CURRENT],
                       "frames": [(0, 0, 2, 2), (2, 2, 4, 4)]}]
    original._refresh_pages(select=0)
    original._refresh_current_plots()
    original.normalize_per_unit_var.set(False)
    original.format_var.set("PNG")
    expected_snapshot = original.snapshot
    expected_pages = original.pages
    expected_image = rendered_preview(original)
    layout_path = tmp_path / "custom.rfmlayout"
    source_before = viewer.data.path.read_bytes()
    save_dialogs = []

    def save_destination(**options):
        save_dialogs.append(options)
        return str(layout_path)

    monkeypatch.setattr(composer_module.filedialog, "asksaveasfilename", save_destination)
    original.layout_menu.invoke(1)
    original.layout_menu.invoke(1)
    assert [dialog["initialfile"] for dialog in save_dialogs] == ["Figure layout", "custom"]
    assert all(dialog["defaultextension"] == ".rfmlayout" for dialog in save_dialogs)
    original._close()

    viewer._open_figure_exporter()
    restored = viewer._figure_export_window
    assert restored is not original
    assert restored.snapshot.time_groups != expected_snapshot.time_groups
    restored._select_all_units()
    restored._set_preview_unit(42)
    restored.destination_var.set("previous.pdf")
    rendered_preview(restored)
    restored._freeze_context((17, 42), restored._export_pages())
    old_provider = restored._preview_data_provider
    assert restored._context_cache
    def load_source(**options):
        assert options["filetypes"] == (("All files", "*.*"),)
        return str(layout_path)

    monkeypatch.setattr(composer_module.filedialog, "askopenfilename", load_source)
    restored.layout_menu.invoke(0)

    assert restored.snapshot == replace(
        expected_snapshot, unit_filter_enabled=restored.snapshot.unit_filter_enabled,
        zero_bin_threshold=restored.snapshot.zero_bin_threshold,
        tuning_curve_session=restored.snapshot.tuning_curve_session,
    )
    assert restored.pages == expected_pages
    assert restored.current_unit_id == 42
    assert restored._selected_unit_ids() == (17, 42)
    assert restored.format_var.get() == "PNG"
    assert not restored.normalize_per_unit_var.get()
    assert restored.destination_var.get() == ""
    assert restored._preview_data_provider is None
    assert restored._base_data_provider is None
    assert restored._provenance_metadata is None
    assert not restored._context_cache
    assert rendered_preview(restored) == expected_image
    assert restored._preview_data_provider is not old_provider
    _pages, metadata, _provider = restored._freeze_context((17, 42), restored._export_pages())
    snapshot_metadata = metadata["snapshot"]
    assert snapshot_metadata["rfTimeRangeMs"] == [0.0, 200.0]
    assert snapshot_metadata["timeGroups"] == [[0, 1], [2, 3]]
    assert snapshot_metadata["timelineRange"] == [0, 1]
    assert snapshot_metadata["timelineActiveBin"] == 1
    assert snapshot_metadata["rfWindowOperation"] == ("sum" if subtract is None else "A - B")
    assert viewer.data.path.read_bytes() == source_before


def test_incompatible_layout_keeps_existing_recipe(viewer, tmp_path, monkeypatch):
    viewer._open_figure_exporter()
    window = viewer._figure_export_window
    path = tmp_path / "different.rfmlayout"
    monkeypatch.setattr(composer_module.filedialog, "asksaveasfilename", lambda **_kwargs: str(path))
    window._save_layout()
    saved = json.loads(path.read_text())
    saved["axes"]["time_bin_edges"][0] -= 0.01
    path.write_text(json.dumps(saved))
    original_snapshot, original_pages = window.snapshot, window.pages
    errors = []
    monkeypatch.setattr(composer_module.filedialog, "askopenfilename", lambda **_kwargs: str(path))
    monkeypatch.setattr(composer_module.messagebox, "showerror", lambda title, text, **_kwargs: errors.append(text))
    window._load_layout()
    assert len(errors) == 1
    assert window.snapshot is original_snapshot
    assert window.pages is original_pages


def test_loading_layout_waits_for_export(viewer, monkeypatch):
    viewer._open_figure_exporter()
    window = viewer._figure_export_window
    messages = []
    monkeypatch.setattr(composer_module.messagebox, "showinfo", lambda title, text, **_kwargs: messages.append(title))
    monkeypatch.setattr(composer_module.filedialog, "askopenfilename", lambda **_kwargs: pytest.fail("opened layout during export"))
    window._export_busy = True
    try:
        window._load_layout()
        assert messages == ["Export is running"]
    finally:
        window._export_busy = False
