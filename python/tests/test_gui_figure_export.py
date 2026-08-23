from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rfmapping_gui

from rfmapping_viewer.figure_export import (
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
    composer_unit_checkbox_hit,
    composer_unit_selection_after_click,
)


def test_atomic_csv_publish_replaces_only_after_fsync(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "displayed.csv"
    destination.write_text("old\n", encoding="utf-8")
    calls: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def tracked_fsync(descriptor: int) -> None:
        calls.append("fsync")
        real_fsync(descriptor)

    def tracked_replace(*args, **kwargs) -> None:
        calls.append("replace")
        real_replace(*args, **kwargs)

    monkeypatch.setattr(rfmapping_gui.os, "fsync", tracked_fsync)
    monkeypatch.setattr(rfmapping_gui.os, "replace", tracked_replace)
    rfmapping_gui._atomic_write_csv(destination, lambda writer: writer.writerow(["new"]))

    assert destination.read_text(encoding="utf-8") == "new\n"
    assert calls == ["fsync", "fsync", "replace", "fsync"]
    assert not tuple(tmp_path.glob(".displayed.csv.tmp-*"))


def test_atomic_csv_write_failure_preserves_existing_file(tmp_path: Path) -> None:
    destination = tmp_path / "displayed.csv"
    destination.write_bytes(b"previous export\n")

    def fail_after_header(writer) -> None:
        writer.writerow(["partial"])
        raise OSError("injected write failure")

    with np.testing.assert_raises_regex(OSError, "injected write failure"):
        rfmapping_gui._atomic_write_csv(destination, fail_after_header)

    assert destination.read_bytes() == b"previous export\n"
    assert not tuple(tmp_path.glob(".displayed.csv.tmp-*"))


def test_atomic_csv_rejects_symlink_destination(tmp_path: Path) -> None:
    victim = tmp_path / "victim.csv"
    victim.write_text("do not replace\n", encoding="utf-8")
    destination = tmp_path / "displayed.csv"
    destination.symlink_to(victim)

    with np.testing.assert_raises_regex(ValueError, "regular file"):
        rfmapping_gui._atomic_write_csv(
            destination, lambda writer: writer.writerow(["new"]),
        )
    assert victim.read_text(encoding="utf-8") == "do not replace\n"


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
        "responseUnits": "spike_count",
        "responseNormalization": "none",
        "spikeCountDefinition": (
            "each_qualifying_trial_contributes_once_per_final_spatial_bin"
        ),
        "occupancyTimeSec": [[0.2, 0.2, 0.2], [0.2, 0.2, 0.2]],
        "occupancyTimeSecSize": [2, 3],
        "occupancyTimeDefinition": (
            "sum_of_qualifying_trial_durations_per_final_spatial_bin"
        ),
    }
    path = tmp_path / "unitsSpikeCounts_fixture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_probe_fixture(tmp_path: Path) -> tuple[Path, Path]:
    data_root = tmp_path / "260630_3" / "data"
    rf_parent = data_root / "rfmapping" / "good" / "-100_400_1ms" / "ProbeA"
    rf_parent.mkdir(parents=True)
    rf_path = _write_fixture(rf_parent)
    positions_path = data_root / "spike_position" / "ProbeA" / "positions.csv"
    positions_path.parent.mkdir(parents=True)
    positions_path.write_text(
        "unit_index,unit_id,x_um,y_um\n"
        "0,17,7.5,120.0\n"
        "1,42,15.0,240.0\n",
        encoding="utf-8",
    )
    channels_path = data_root / "waveform" / "ProbeA" / "channels.csv"
    channels_path.parent.mkdir(parents=True)
    channels_path.write_text(
        "channel_index,channel_id,raw_channel_index,x_um,y_um,shank_id\n"
        "0,10,0,0.0,0.0,0\n"
        "1,11,1,20.0,20.0,0\n",
        encoding="utf-8",
    )
    return rf_path, positions_path


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


def test_gui_provider_renders_discovered_probe_geometry_from_frozen_session(
    tmp_path: Path,
) -> None:
    rf_path, positions_path = _write_probe_fixture(tmp_path)
    data = RFMappingData(rf_path)
    provider = GUIFigureDataProvider(data, _snapshot())

    # Companion files are captured when the non-modal composer provider is
    # created; later changes on disk cannot alter its preview/export recipe.
    positions_path.write_text(
        "unit_index,unit_id,x_um,y_um\n0,999,999.0,999.0\n",
        encoding="utf-8",
    )
    result = provider(42, PlotSpec(PlotKind.PROBE_LAYOUT))

    assert result.title == "ProbeA layout"
    assert result.data == {
        "points": [
            {"x": 0.0, "y": 0.0, "label": "", "color": "#94a3b8"},
            {"x": 20.0, "y": 20.0, "label": "", "color": "#94a3b8"},
            {"x": 15.0, "y": 240.0, "label": "42", "color": "#dc2626"},
        ]
    }

    page = ExportPage("Probe", (PlotSpec(PlotKind.PROBE_LAYOUT),))
    plan = ExportPlan(
        FigureFormat.PDF,
        (42,),
        (page,),
        tmp_path / "unused-probe-preview.pdf",
    )
    image = render_live_preview(plan, 42, 0, data_provider=provider)
    assert image.getbbox() is not None
    assert not (tmp_path / "unused-probe-preview.pdf").exists()


def test_multi_unit_probe_pages_each_contain_only_their_current_unit_marker(
    tmp_path: Path,
) -> None:
    rf_path, _positions_path = _write_probe_fixture(tmp_path)
    provider = GUIFigureDataProvider(RFMappingData(rf_path), _snapshot())

    page_markers: dict[int, list[dict[str, object]]] = {}
    for unit_id in (17, 42):
        payload = provider(unit_id, PlotSpec(PlotKind.PROBE_LAYOUT)).data
        page_markers[unit_id] = [
            point
            for point in payload["points"]
            if point["label"]
        ]

    assert page_markers == {
        17: [{"x": 7.5, "y": 120.0, "label": "17", "color": "#dc2626"}],
        42: [{"x": 15.0, "y": 240.0, "label": "42", "color": "#dc2626"}],
    }


def test_probe_discovery_stops_at_recording_data_boundary(
    tmp_path: Path,
) -> None:
    date_root = tmp_path / "260630"
    rf_parent = (
        date_root
        / "260630_3"
        / "data"
        / "rfmapping"
        / "good"
        / "-100_400_1ms"
        / "ProbeA"
    )
    rf_parent.mkdir(parents=True)
    rf_path = _write_fixture(rf_parent)

    # This is a plausible legacy file belonging to another recording below
    # the same date directory.  An upward filesystem walk used to bind it to
    # the 260630_3 RF payload.
    unrelated = date_root / "positions.csv"
    unrelated.write_text(
        "unit_index,unit_id,x_um,y_um\n0,17,999.0,999.0\n",
        encoding="utf-8",
    )

    assert rfmapping_gui.discover_probe_geometry_paths(rf_path) is None
    data = RFMappingData(rf_path)
    assert data.probe_geometry() is None
    assert data.probe_geometry_error is None


def test_probe_discovery_keeps_adjacent_legacy_layout(
    tmp_path: Path,
) -> None:
    rf_parent = tmp_path / "legacy-export" / "ProbeA"
    rf_parent.mkdir(parents=True)
    rf_path = _write_fixture(rf_parent)
    positions_path = rf_parent / "positions.csv"
    positions_path.write_text(
        "unit_index,unit_id,x_um,y_um\n0,17,7.5,120.0\n",
        encoding="utf-8",
    )

    discovered = rfmapping_gui.discover_probe_geometry_paths(rf_path)

    assert discovered == ("ProbeA", positions_path.resolve(), None)
    geometry = RFMappingData(rf_path).probe_geometry()
    assert geometry is not None
    assert tuple(unit.unit_id for unit in geometry.units) == (17,)


def test_probe_discovery_keeps_bounded_data_layout_without_session_name(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "fixture" / "data"
    rf_parent = data_root / "rfmapping" / "good" / "ProbeA"
    rf_parent.mkdir(parents=True)
    rf_path = _write_fixture(rf_parent)
    positions_path = data_root / "spike_position" / "ProbeA" / "positions.csv"
    positions_path.parent.mkdir(parents=True)
    positions_path.write_text(
        "unit_index,unit_id,x_um,y_um\n0,17,7.5,120.0\n",
        encoding="utf-8",
    )

    discovered = rfmapping_gui.discover_probe_geometry_paths(rf_path)

    assert discovered == ("ProbeA", positions_path.resolve(), None)
    geometry = RFMappingData(rf_path).probe_geometry()
    assert geometry is not None
    assert tuple(unit.unit_id for unit in geometry.units) == (17,)


def test_probe_geometry_rejects_positions_without_rf_unit_overlap(
    tmp_path: Path,
) -> None:
    rf_path, positions_path = _write_probe_fixture(tmp_path)
    positions_path.write_text(
        "unit_index,unit_id,x_um,y_um\n0,999,7.5,120.0\n",
        encoding="utf-8",
    )

    data = RFMappingData(rf_path)

    assert data.probe_geometry() is None
    assert data.probe_geometry_error is not None
    assert "no unit IDs" in data.probe_geometry_error
    payload = GUIFigureDataProvider(data, _snapshot())(
        42,
        PlotSpec(PlotKind.PROBE_LAYOUT),
    ).data
    assert "unavailable" in payload
    assert "no unit IDs" in payload["unavailable"]


def test_probe_payload_filters_non_rf_units_and_requires_selected_unit(
    tmp_path: Path,
) -> None:
    rf_path, positions_path = _write_probe_fixture(tmp_path)
    positions_path.write_text(
        "unit_index,unit_id,x_um,y_um\n"
        "0,17,7.5,120.0\n"
        "1,999,999.0,999.0\n",
        encoding="utf-8",
    )
    provider = GUIFigureDataProvider(RFMappingData(rf_path), _snapshot())

    available = provider(17, PlotSpec(PlotKind.PROBE_LAYOUT)).data
    labels = [point["label"] for point in available["points"] if point["label"]]
    assert labels == ["17"]

    missing = provider(42, PlotSpec(PlotKind.PROBE_LAYOUT)).data
    assert missing == {
        "unavailable": (
            "Probe position is unavailable for RF unit 42; "
            "the selected unit is absent from positions.csv."
        )
    }


def test_probe_nan_position_keeps_unit_channels_and_export_annotation(
    tmp_path: Path,
) -> None:
    rf_path, positions_path = _write_probe_fixture(tmp_path)
    positions_path.write_text(
        "unit_index,unit_id,x_um,y_um\n"
        "0,17,7.5,120.0\n"
        "1,42,nan,nan\n",
        encoding="utf-8",
    )
    data = RFMappingData(rf_path)
    geometry = data.probe_geometry()

    assert geometry is not None
    assert tuple(unit.unit_id for unit in geometry.units) == (17, 42)
    assert geometry.units_by_id[42].x_um is None
    assert geometry.units_by_id[42].y_um is None
    region = rfmapping_gui.SpatialRegion.from_corners(-1000, -1000, 1000, 1000)
    assert geometry.unit_ids_in_region(region, [17, 42]) == [17]

    payload = GUIFigureDataProvider(data, _snapshot())(
        42,
        PlotSpec(PlotKind.PROBE_LAYOUT),
    ).data
    assert payload["missingPosition"] is True
    assert payload["points"] == [
        {"x": 0.0, "y": 0.0, "label": "", "color": "#94a3b8"},
        {"x": 20.0, "y": 20.0, "label": "", "color": "#94a3b8"},
    ]


def test_probe_nan_position_exports_without_channel_background(
    tmp_path: Path,
) -> None:
    rf_path, positions_path = _write_probe_fixture(tmp_path)
    positions_path.write_text(
        "unit_index,unit_id,x_um,y_um\n"
        "0,42,nan,nan\n",
        encoding="utf-8",
    )
    channels_path = (
        positions_path.parents[2] / "waveform" / "ProbeA" / "channels.csv"
    )
    channels_path.unlink()

    payload = GUIFigureDataProvider(RFMappingData(rf_path), _snapshot())(
        42,
        PlotSpec(PlotKind.PROBE_LAYOUT),
    ).data

    assert payload == {"points": [], "missingPosition": True}


@pytest.mark.parametrize(
    ("x_value", "y_value"),
    (("nan", "20"), ("10", "nan"), ("inf", "inf"), ("bad", "bad")),
)
def test_probe_rejects_malformed_missing_coordinate_pairs(
    tmp_path: Path,
    x_value: str,
    y_value: str,
) -> None:
    rf_path, positions_path = _write_probe_fixture(tmp_path)
    positions_path.write_text(
        "unit_index,unit_id,x_um,y_um\n"
        f"0,17,{x_value},{y_value}\n",
        encoding="utf-8",
    )

    data = RFMappingData(rf_path)

    assert data.probe_geometry() is None
    assert data.probe_geometry_error is not None
    assert "positions.csv value on row 2" in data.probe_geometry_error


def test_figure_composer_unit_ids_and_name_stay_bound_to_frozen_session(
    tmp_path: Path,
) -> None:
    frozen_data = RFMappingData(_write_fixture(tmp_path))
    composer = SimpleNamespace(
        data=frozen_data,
        unit_ids=(17, 42),
        _selected_unit_indices={1},
        # A parent viewer can move to another JSON while the non-modal composer
        # remains open; these live IDs must never leak into the frozen recipe.
        viewer=SimpleNamespace(data=SimpleNamespace(rf_maps=[SimpleNamespace(unit_id=999)])),
    )

    selected = FigureExportWindow._selected_unit_ids(composer)
    basename = FigureExportWindow._default_base_name(composer)

    assert selected == (42,)
    assert basename == "unitsSpikeCounts_fixture_figures"


def test_figure_composer_unit_clicks_match_finder_selection_semantics() -> None:
    selected, anchor = composer_unit_selection_after_click((), 2, None, 7)
    assert selected == (2,)
    assert anchor == 2

    selected, anchor = composer_unit_selection_after_click(
        selected,
        5,
        anchor,
        7,
        command=True,
    )
    assert selected == (2, 5)
    assert anchor == 5

    selected, anchor = composer_unit_selection_after_click(
        selected,
        2,
        anchor,
        7,
        command=True,
    )
    assert selected == (5,)
    assert anchor == 2

    selected, anchor = composer_unit_selection_after_click(selected, 1, anchor, 7)
    assert selected == (1,)
    assert anchor == 1

    selected, anchor = composer_unit_selection_after_click(
        selected,
        4,
        anchor,
        7,
        shift=True,
    )
    assert selected == (1, 2, 3, 4)
    assert anchor == 1

    selected, anchor = composer_unit_selection_after_click(
        selected,
        6,
        anchor,
        7,
        command=True,
        shift=True,
    )
    assert selected == (1, 2, 3, 4, 5, 6)
    assert anchor == 1


def test_figure_composer_checkbox_hitbox_toggles_without_replacing_selection() -> None:
    assert composer_unit_checkbox_hit(8, 8, 24) is True
    assert composer_unit_checkbox_hit(31, 8, 24) is True
    assert composer_unit_checkbox_hit(32, 8, 24) is False
    assert composer_unit_checkbox_hit(80, 8, 24) is False

    selected, anchor = composer_unit_selection_after_click((), 2, None, 7)
    assert selected == (2,)

    # A no-modifier click inside the checkbox hitbox is routed as an additive
    # toggle by FigureExportWindow._on_unit_list_click.
    checkbox_toggle = composer_unit_checkbox_hit(12, 8, 24)
    selected, anchor = composer_unit_selection_after_click(
        selected,
        5,
        anchor,
        7,
        command=checkbox_toggle,
    )
    assert selected == (2, 5)
    assert anchor == 5

    selected, anchor = composer_unit_selection_after_click(
        selected,
        2,
        anchor,
        7,
        command=checkbox_toggle,
    )
    assert selected == (5,)
    assert anchor == 2

    # The same physical click in the row text area remains an ordinary
    # replace-selection click.
    row_toggle = composer_unit_checkbox_hit(80, 8, 24)
    selected, anchor = composer_unit_selection_after_click(
        selected,
        1,
        anchor,
        7,
        command=row_toggle,
    )
    assert selected == (1,)
    assert anchor == 1


def test_figure_composer_selected_units_always_follow_json_unit_pool_order() -> None:
    composer = SimpleNamespace(
        unit_ids=(90, 4, 77, 2),
        _selected_unit_indices={3, 0, 2},
    )

    assert FigureExportWindow._selected_unit_ids(composer) == (90, 77, 2)


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
    (tmp_path / "tuning_curves.json").write_text("{}", encoding="utf-8")

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


def test_frozen_file_hash_rejects_changed_source(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text("original", encoding="utf-8")
    identity = rfmapping_gui.FrozenFileIdentity.capture(source)
    assert rfmapping_gui._hash_frozen_file(identity) == __import__("hashlib").sha256(b"original").hexdigest()

    source.write_text("modified after load", encoding="utf-8")
    with np.testing.assert_raises_regex(RuntimeError, "changed after it was loaded"):
        rfmapping_gui._hash_frozen_file(identity)


def test_gui_shared_rf_scale_is_selection_scoped_and_frozen_in_plot_options(
    tmp_path: Path,
) -> None:
    data = RFMappingData(_write_fixture(tmp_path))
    provider = GUIFigureDataProvider(data, _snapshot())
    scale_one = provider.shared_rf_bounds((17,))
    scale_both = provider.shared_rf_bounds((17, 42))
    assert scale_one == (0.0, 5.0)
    assert scale_both == (0.0, 7.0)

    composer = SimpleNamespace(
        pages=[{"name": "RF", "plots": [PlotKind.RF_CARTESIAN, PlotKind.RF_POLAR]}],
        snapshot=_snapshot(),
        data=data,
    )
    raw = FigureExportWindow._export_pages(composer)
    resolved = FigureExportWindow._resolved_export_pages(composer, raw, scale_both)
    assert all(plot.options["vmin"] == 0.0 for plot in resolved[0].plots)
    assert all(plot.options["vmax"] == 7.0 for plot in resolved[0].plots)
    assert all(plot.options["value_unit"] == "spikes" for plot in resolved[0].plots)


def test_polar_export_labels_follow_provider_rows_for_both_radius_modes_and_flip(
    tmp_path: Path,
) -> None:
    data = RFMappingData(_write_fixture(tmp_path))
    raw = (ExportPage("Polar", (PlotSpec(PlotKind.RF_POLAR),)),)
    base_snapshot = _snapshot()

    for flip_y in (False, True):
        y_groups = base_snapshot.y_groups
        if flip_y:
            y_groups = tuple(reversed(y_groups))
        for radius_mode in POLAR_RADIUS_MODES:
            snapshot = replace(
                base_snapshot,
                y_groups=y_groups,
                polar_radius=radius_mode,
            )
            composer = SimpleNamespace(snapshot=snapshot, data=data)
            resolved = FigureExportWindow._resolved_export_pages(composer, raw, None)
            resolved_plot = resolved[0].plots[0]
            provider = GUIFigureDataProvider(data, snapshot)
            cartesian = provider(17, PlotSpec(PlotKind.RF_CARTESIAN))
            polar = provider(17, resolved_plot)

            if radius_mode == POLAR_RADIUS_MODES[0]:
                row_indices = sorted(
                    range(len(y_groups)),
                    key=lambda index: y_groups[index][0],
                )
            else:
                row_indices = list(range(len(y_groups) - 1, -1, -1))
            expected_y_values = [
                (data.y_positions[y_groups[index][0]] + data.y_positions[y_groups[index][1]])
                / 2.0
                for index in row_indices
            ]

            assert resolved_plot.options["y_values"] == tuple(expected_y_values)
            assert polar.data == [cartesian.data[index] for index in row_indices]


def test_freeze_context_revalidates_cached_source(tmp_path: Path) -> None:
    data = RFMappingData(_write_fixture(tmp_path))
    snapshot = _snapshot()
    composer = SimpleNamespace(
        data=data,
        snapshot=snapshot,
        _provider_lock=threading.Lock(),
        _base_data_provider=None,
        _provenance_metadata=None,
        _context_cache={},
        pages=[{"name": "RF", "plots": [PlotKind.RF_CARTESIAN]}],
        _recipe_key=lambda unit_ids, pages: (unit_ids, tuple(p.name for p in pages)),
    )
    composer._verify_export_inputs = lambda: FigureExportWindow._verify_export_inputs(composer)
    composer._export_pages = lambda: FigureExportWindow._export_pages(composer)
    composer._resolved_export_pages = lambda pages, scale: FigureExportWindow._resolved_export_pages(composer, pages, scale)
    raw = composer._export_pages()
    FigureExportWindow._freeze_context(composer, (17,), raw)

    data.path.write_text(data.path.read_text() + " ", encoding="utf-8")
    with np.testing.assert_raises_regex(RuntimeError, "changed after it was loaded"):
        FigureExportWindow._freeze_context(composer, (17,), raw)


def test_active_export_registry_tracks_non_daemon_future_until_completion() -> None:
    root = SimpleNamespace()
    viewer = object()
    release = threading.Event()
    future = rfmapping_gui._export_executor(root).submit(lambda: release.wait(timeout=2.0))
    rfmapping_gui._register_export_job(root, viewer, future)
    assert rfmapping_gui._active_export_jobs(root, viewer) == (future,)
    release.set()
    future.result(timeout=1.0)
    # A completed worker remains active until Tk consumes and reports its result.
    assert rfmapping_gui._active_export_jobs(root, viewer) == (future,)
    rfmapping_gui._unregister_export_job(root, future)
    assert rfmapping_gui._active_export_jobs(root, viewer) == ()
    rfmapping_gui._shutdown_export_executor(root)


def test_atomic_csv_detects_destination_created_during_write(tmp_path: Path) -> None:
    destination = tmp_path / "displayed.csv"

    def race() -> None:
        destination.write_text("other writer\n", encoding="utf-8")

    with np.testing.assert_raises_regex(RuntimeError, "destination changed"):
        rfmapping_gui._atomic_write_csv(
            destination,
            lambda writer: writer.writerow(["ours"]),
            before_publish=race,
        )
    assert destination.read_text(encoding="utf-8") == "other writer\n"
    assert not tuple(tmp_path.glob(".displayed.csv.tmp-*"))


def test_atomic_csv_detects_existing_file_modified_during_write(tmp_path: Path) -> None:
    destination = tmp_path / "displayed.csv"
    destination.write_text("old\n", encoding="utf-8")

    def race() -> None:
        destination.write_text("changed in place with different size\n", encoding="utf-8")

    with np.testing.assert_raises_regex(RuntimeError, "destination changed"):
        rfmapping_gui._atomic_write_csv(
            destination,
            lambda writer: writer.writerow(["ours"]),
            before_publish=race,
        )
    assert destination.read_text(encoding="utf-8") == "changed in place with different size\n"
    assert not tuple(tmp_path.glob(".displayed.csv.tmp-*"))


def test_atomic_csv_replace_failure_preserves_existing_file(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "displayed.csv"
    destination.write_text("old\n", encoding="utf-8")

    def fail_replace(*_args, **_kwargs) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(rfmapping_gui.os, "replace", fail_replace)
    with np.testing.assert_raises_regex(OSError, "replace failure"):
        rfmapping_gui._atomic_write_csv(
            destination, lambda writer: writer.writerow(["new"]),
        )
    assert destination.read_text(encoding="utf-8") == "old\n"
    assert not tuple(tmp_path.glob(".displayed.csv.tmp-*"))


def test_atomic_csv_ignores_unsupported_directory_fsync(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "displayed.csv"
    real_fsync = os.fsync

    def selective_fsync(descriptor: int) -> None:
        if __import__("stat").S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(__import__("errno").EINVAL, "directory fsync unsupported")
        real_fsync(descriptor)

    monkeypatch.setattr(rfmapping_gui.os, "fsync", selective_fsync)
    rfmapping_gui._atomic_write_csv(
        destination, lambda writer: writer.writerow(["new"]),
    )
    assert destination.read_text(encoding="utf-8") == "new\n"


def test_atomic_csv_accepts_replace_lost_success_reply(tmp_path: Path, monkeypatch) -> None:
    destination = tmp_path / "displayed.csv"
    destination.write_text("old\n", encoding="utf-8")
    real_replace = os.replace

    def replace_then_raise(*args, **kwargs) -> None:
        real_replace(*args, **kwargs)
        raise OSError(__import__("errno").EIO, "lost replace success reply")

    monkeypatch.setattr(rfmapping_gui.os, "replace", replace_then_raise)
    rfmapping_gui._atomic_write_csv(
        destination, lambda writer: writer.writerow(["complete new"]),
    )
    assert destination.read_text(encoding="utf-8") == "complete new\n"
    assert not tuple(tmp_path.glob(".displayed.csv.tmp-*"))


def test_atomic_csv_reports_post_publish_durability_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / "displayed.csv"
    destination.write_text("old\n", encoding="utf-8")
    real_fsync = os.fsync

    def fail_directory_fsync(descriptor: int) -> None:
        if __import__("stat").S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(__import__("errno").EIO, "injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(rfmapping_gui.os, "fsync", fail_directory_fsync)
    with np.testing.assert_raises_regex(
        RuntimeError,
        "atomically published.*durability could not be confirmed",
    ):
        rfmapping_gui._atomic_write_csv(
            destination, lambda writer: writer.writerow(["complete new"]),
        )
    assert destination.read_text(encoding="utf-8") == "complete new\n"
    assert not tuple(tmp_path.glob(".displayed.csv.tmp-*"))
