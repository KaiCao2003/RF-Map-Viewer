from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import rfmapping_gui

from Utils.figure_export import (
    ExportPage,
    ExportPlan,
    FigureFormat,
    PlotKind,
    PlotSpec,
    render_live_preview,
)
from rfmapping_gui import (
    GUIFigureDataProvider,
    POLAR_RADIUS_MODES,
    RFMappingData,
    VALUE_MODE_COUNT,
    FigureExportWindow,
    FigureViewerSnapshot,
)


def _write_fixture(tmp_path: Path) -> Path:
    counts = np.asarray(
        [
            [
                [[0, 1, 2, 3], [1, 0, 4, 1], [0, 0, 0, 0]],
                [[4, 3, 2, 1], [1, 2, 1, 2], [0, 5, 0, 5]],
            ],
            [
                [[2, 0, 2, 0], [3, 1, 0, 1], [1, 1, 1, 1]],
                [[0, 2, 0, 2], [5, 4, 3, 2], [2, 1, 2, 1]],
            ],
        ],
        dtype=np.int64,
    )
    payload = {
        "unitsSpikeCounts": counts.tolist(),
        "unitsSpikeCountsSize": list(counts.shape),
        "unitPool": [17, 42],
        "xPositions": [-12.0, 0.0, 12.0],
        "yPositions": [-6.0, 6.0],
        "timeBinEdges": [-0.1, 0.0, 0.1, 0.2, 0.3],
        "stimulusPresentationCounts": [[2, 2, 2], [2, 2, 2]],
    }
    path = tmp_path / "unitsSpikeCounts_fixture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _snapshot(*, timeline_polar: bool = False) -> FigureViewerSnapshot:
    return FigureViewerSnapshot(
        value_mode=VALUE_MODE_COUNT,
        rf_source_start=1,
        rf_source_end=2,
        time_groups=((0, 0), (1, 1), (2, 2), (3, 3)),
        x_groups=((0, 0), (1, 1), (2, 2)),
        y_groups=((0, 0), (1, 1)),
        smooth_radius=0,
        palette="Viridis",
        polar_radius=POLAR_RADIUS_MODES[0],
        timeline_polar=timeline_polar,
        selected_cell=(0, 0, 1, 1),
        total_degrees=36.0,
        timeline_range_start=1,
        timeline_range_end=2,
        timeline_active_bin=2,
    )


def test_gui_provider_prepares_every_registered_view_without_mutating_rf_data(
    tmp_path: Path,
) -> None:
    data = RFMappingData(_write_fixture(tmp_path))
    original = data.rf_map_by_unit_id(42).spike_counts.copy()
    provider = GUIFigureDataProvider(data, _snapshot(timeline_polar=True))

    prepared = {
        kind: provider(42, PlotSpec(kind))
        for kind in PlotKind
    }

    assert prepared[PlotKind.RF_CARTESIAN].data == [[2.0, 1.0, 2.0], [2.0, 7.0, 3.0]]
    assert len(prepared[PlotKind.RF_POLAR].data) == 2
    assert len(prepared[PlotKind.DELAY_CARTESIAN].data) == 2
    assert len(prepared[PlotKind.RGB_CARTESIAN].data) == 2
    # The GUI's RGB response channel uses the full timeline, independently of
    # the RF tab's selected 0--200 ms range: 5 / 14 maps to red=91 here.
    assert prepared[PlotKind.RGB_CARTESIAN].data[0][1][0] == 91

    timeline = prepared[PlotKind.TIMELINE_CURRENT]
    assert timeline.options["polar"] is True
    assert len(timeline.data["times"]) == 4
    assert len(timeline.data["totals"]) == 4
    assert len(timeline.data["selected"]) == 4
    assert len(timeline.data["frames"]) == 4
    assert timeline.data["selection_start_index"] == 1
    assert timeline.data["selection_end_index"] == 2
    assert timeline.data["active_index"] == 2

    assert "unavailable" in prepared[PlotKind.HD_LINE].data
    assert "unavailable" in prepared[PlotKind.HD_POLAR].data
    assert "unavailable" in prepared[PlotKind.PROBE_LAYOUT].data
    np.testing.assert_array_equal(data.rf_map_by_unit_id(42).spike_counts, original)


def test_gui_provider_and_shared_renderer_render_all_views_on_one_live_page(
    tmp_path: Path,
) -> None:
    data = RFMappingData(_write_fixture(tmp_path))
    provider = GUIFigureDataProvider(data, _snapshot())
    page = ExportPage("Every view", tuple(PlotSpec(kind) for kind in PlotKind))
    plan = ExportPlan(
        FigureFormat.PDF,
        (17, 42),
        (page,),
        tmp_path / "unused-live-preview.pdf",
    )

    image = render_live_preview(plan, 17, 0, data_provider=provider)

    assert image.mode == "RGB"
    assert image.width > 1000
    assert image.height > 700
    assert image.getbbox() is not None
    assert not (tmp_path / "unused-live-preview.pdf").exists()


def test_gui_provider_returns_explicit_placeholder_for_missing_unit(
    tmp_path: Path,
) -> None:
    data = RFMappingData(_write_fixture(tmp_path))
    provider = GUIFigureDataProvider(data, _snapshot())

    result = provider(999, PlotSpec(PlotKind.RF_CARTESIAN))

    assert result.data == {
        "unavailable": "Unit 999 is unavailable in this RF dataset."
    }


def test_figure_composer_unit_ids_and_name_stay_bound_to_frozen_session(
    tmp_path: Path,
) -> None:
    frozen_data = RFMappingData(_write_fixture(tmp_path))
    selection = SimpleNamespace(curselection=lambda: (1,))
    composer = SimpleNamespace(
        data=frozen_data,
        unit_ids=(17, 42),
        unit_list=selection,
        # A parent viewer can move to another JSON while the non-modal composer
        # remains open; these live IDs must never leak into the frozen recipe.
        viewer=SimpleNamespace(data=SimpleNamespace(rf_maps=[SimpleNamespace(unit_id=999)])),
    )

    selected = FigureExportWindow._selected_unit_ids(composer)
    basename = FigureExportWindow._default_base_name(composer)

    assert selected == (42,)
    assert basename == "unitsSpikeCounts_fixture_figures"


def test_figure_composer_reorders_page_templates() -> None:
    calls: list[object] = []
    composer = SimpleNamespace(
        pages=[{"name": "First"}, {"name": "Second"}, {"name": "Third"}],
        _selected_page_index=lambda: 1,
        _refresh_pages=lambda *, select: calls.append(("pages", select)),
        _refresh_current_plots=lambda: calls.append("plots"),
        _schedule_preview=lambda: calls.append("preview"),
    )

    FigureExportWindow._move_page(composer, -1)

    assert [page["name"] for page in composer.pages] == ["Second", "First", "Third"]
    assert calls == [("pages", 0), "plots", "preview"]


def test_hd_lazy_load_is_published_atomically_across_preview_and_export_threads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data = RFMappingData(_write_fixture(tmp_path))
    started = threading.Event()
    release = threading.Event()
    sentinel = object()
    load_calls = 0

    monkeypatch.setattr(
        rfmapping_gui,
        "discover_hd_tuning_path",
        lambda _rf_path: tmp_path / "tuning_curves.json",
    )

    def blocking_load(_path):
        nonlocal load_calls
        load_calls += 1
        started.set()
        assert release.wait(timeout=2.0)
        return sentinel

    monkeypatch.setattr(rfmapping_gui, "load_hd_tuning", blocking_load)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(data.hd_tuning)
        assert started.wait(timeout=1.0)
        second = executor.submit(data.hd_tuning)
        # The second caller must wait for the complete result instead of seeing
        # checked=True with a still-empty tuning object.
        assert not second.done()
        release.set()
        assert first.result(timeout=1.0) is sentinel
        assert second.result(timeout=1.0) is sentinel

    assert load_calls == 1
