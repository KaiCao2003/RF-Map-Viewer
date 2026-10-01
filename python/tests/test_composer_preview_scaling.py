from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import rfmapping_viewer.figure_composer as composer_module
from rfmapping_viewer.figure_composer import FigureExportWindow, GUIFigureDataProvider
from rfmapping_viewer.figure_export import ExportPage, PlotKind, PlotSpec, render_live_preview
from rfmapping_viewer.rf_model import RFMappingData
from test_gui_figure_export import _snapshot, _write_fixture, _write_waveform_fixture


def _composer(data: RFMappingData) -> SimpleNamespace:
    composer = SimpleNamespace(
        data=data,
        snapshot=_snapshot(),
        _provider_lock=threading.Lock(),
        _preview_provider_lock=threading.Lock(),
        _base_data_provider=None,
        _preview_data_provider=None,
        _provenance_metadata=None,
        _context_cache={},
    )
    composer._recipe_key = lambda units, pages: FigureExportWindow._recipe_key(composer, units, pages)
    composer._verify_export_inputs = lambda: FigureExportWindow._verify_export_inputs(composer)
    composer._resolved_export_pages = lambda pages, scale, waveform=None: FigureExportWindow._resolved_export_pages(
        composer, pages, scale, waveform,
    )
    return composer


@pytest.mark.parametrize("kind", (PlotKind.RF_CARTESIAN, PlotKind.RF_POLAR))
def test_normalized_rf_uses_each_units_maximum_and_preserves_values(tmp_path: Path, kind) -> None:
    data = RFMappingData(_write_fixture(tmp_path))
    provider = GUIFigureDataProvider(data, _snapshot(), shared_rf_scale=(0.0, 7.0))
    normalized = PlotSpec(kind, options={"normalize_per_unit": True})
    shared = PlotSpec(kind, options={"normalize_per_unit": False})

    first, second = provider(17, normalized), provider(42, normalized)
    assert (first.options["vmin"], first.options["vmax"]) == (0.0, 5.0)
    assert (second.options["vmin"], second.options["vmax"]) == (0.0, 7.0)
    assert first.data == provider(17, shared).data
    assert provider(17, shared).options["vmax"] == 7.0
    assert first.options["value_unit"] == provider(17, shared).options["value_unit"]


def test_normalized_waveform_uses_local_amplitude(tmp_path: Path) -> None:
    rf_path, _artifact = _write_waveform_fixture(tmp_path)
    provider = GUIFigureDataProvider(RFMappingData(rf_path), _snapshot(), shared_waveform_limit=100.0)
    normalized = PlotSpec(PlotKind.WAVEFORM_LOCAL_AVERAGE, options={"normalize_per_unit": True})
    shared = replace(normalized, options={"normalize_per_unit": False})
    first, second = provider(17, normalized), provider(42, normalized)

    assert first.options["vmax"] == first.data["amplitude_limit_uv"]
    assert second.options["vmax"] == 2 * first.options["vmax"]
    assert first.options["vmin"] == -first.options["vmax"]
    assert provider(17, shared).options["vmax"] == 100.0


def test_export_normalization_records_actual_limits_and_keeps_source_verification(tmp_path: Path) -> None:
    composer = _composer(RFMappingData(_write_fixture(tmp_path)))
    normalized = (ExportPage("RF", (PlotSpec(PlotKind.RF_CARTESIAN, options={"normalize_per_unit": True}),)),)
    shared = (ExportPage("RF", (PlotSpec(PlotKind.RF_CARTESIAN, options={"normalize_per_unit": False}),)),)

    pages, metadata, provider = FigureExportWindow._freeze_context(composer, (17, 42), normalized)
    assert metadata["normalization"]["perUnit"] is True
    assert [scale["rf"]["vmax"] for scale in metadata["perUnitScales"]] == [5.0, 7.0]
    assert "sharedRFScale" not in metadata
    assert provider(17, pages[0].plots[0]).options["vmax"] == 5.0
    shared_pages, shared_metadata, shared_provider = FigureExportWindow._freeze_context(composer, (17, 42), shared)
    assert shared_metadata["normalization"]["perUnit"] is False
    assert shared_metadata["sharedRFScale"]["vmax"] == 7.0
    assert shared_provider(17, shared_pages[0].plots[0]).options["vmax"] == 7.0

    composer.data.path.write_text(composer.data.path.read_text() + " ")
    with pytest.raises(RuntimeError, match="changed after it was loaded"):
        FigureExportWindow._freeze_context(composer, (17, 42), normalized)


def test_preview_request_uses_current_unit_and_only_current_page() -> None:
    pages = (ExportPage("Current page", (PlotSpec(PlotKind.RF_CARTESIAN),)),)
    requests = []

    def export_pages(*, page_index):
        requests.append(page_index)
        return pages

    composer = SimpleNamespace(
        current_unit_id=42,
        _selected_page_index=lambda: 2,
        _export_pages=export_pages,
        preview_label=SimpleNamespace(winfo_width=lambda: 800, winfo_height=lambda: 600),
    )
    units, requested_pages, index, _width, _height = FigureExportWindow._preview_request(composer)
    assert units == (42,)
    assert requested_pages is pages
    assert index == 2
    assert requests == [2]


def test_preview_does_not_hash_or_prepare_other_units(tmp_path: Path, monkeypatch) -> None:
    data = RFMappingData(_write_fixture(tmp_path))
    composer = _composer(data)
    raw = (ExportPage("RF", (PlotSpec(PlotKind.RF_CARTESIAN, options={"normalize_per_unit": False}),)),)
    prepared_units = []
    original = data.spatial_group_response_frames

    def prepare(unit_index, *args, **kwargs):
        prepared_units.append(data.unit_pool[unit_index])
        return original(unit_index, *args, **kwargs)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Preview must not freeze exports, hash sources, or scan selected units")

    monkeypatch.setattr(data, "spatial_group_response_frames", prepare)
    monkeypatch.setattr(data, "capture_waveform_inputs", forbidden)
    monkeypatch.setattr(composer_module, "_hash_frozen_file", forbidden)
    monkeypatch.setattr(GUIFigureDataProvider, "shared_rf_bounds", forbidden)
    composer._verify_export_inputs = forbidden
    # The independent preview lock also allows an export to freeze in parallel.
    with composer._provider_lock:
        pages, metadata, provider = FigureExportWindow._preview_context(composer, (42,), raw, lambda: False)
    plan = FigureExportWindow._preview_plan(composer, (42,), pages, metadata)
    image = render_live_preview(plan, 42, 0, data_provider=provider)
    image.close()
    assert prepared_units == [42]
    assert provider(42, pages[0].plots[0]).options["normalize_per_unit"] is True
    assert metadata["preview"]["unitId"] == 42


def test_recipe_cache_changes_for_frame_title_and_normalization() -> None:
    first = PlotSpec(PlotKind.RF_CARTESIAN, options={"frame": (0, 0, 2, 2), "normalize_per_unit": True})
    variants = (
        replace(first, title="New title"),
        replace(first, options={**first.options, "normalize_per_unit": False}),
        replace(first, options={**first.options, "frame": (0, 1, 2, 2)}),
    )
    keys = {
        FigureExportWindow._recipe_key(None, (17,), (ExportPage("RF", (plot,)),))
        for plot in (first, *variants)
    }
    assert len(keys) == 4
