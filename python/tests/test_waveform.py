from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from rfmapping_viewer.waveform import (
    WAVEFORM_CHANNEL_MODES,
    WaveformArtifactError,
    WaveformArtifactStore,
    baseline_correct_template,
    discover_waveform_artifact,
    select_local_channel_indices,
)


CHANNELS = (
    (0, 100, 0, 0.0, 120.0, 0),
    (1, 101, 1, 0.0, 90.0, 0),
    (2, 102, 2, 0.0, 60.0, 0),
    (3, 103, 3, 0.0, 30.0, 0),
    (4, 104, 4, 0.0, 0.0, 0),
    (5, 105, 5, 20.0, 61.0, 0),
    (6, 106, 6, 40.0, 60.0, 1),
)
TIMES_MS = (-0.5, -0.25, 0.0, 0.25)


def _write_csv(path: Path, header: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _template_values(unit_id: int) -> np.ndarray:
    sample_scale = np.arange(1.0, len(TIMES_MS) + 1.0)[:, None]
    channel_scale = np.arange(1.0, len(CHANNELS) + 1.0)[None, :]
    return sample_scale * channel_scale + float(unit_id)


def _write_template(path: Path, unit_id: int) -> None:
    values = _template_values(unit_id)
    header = (
        "sample_index",
        *(f"chidx_{index:03d}_uv" for index in range(len(CHANNELS))),
    )
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for sample_index, row in enumerate(values):
            writer.writerow((sample_index, *row))


def _write_artifact(
    root: Path,
    *,
    unit_ids: tuple[int, ...] = (7, 8, 9),
    unit_scope: str = "good",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_name": "rfmapping-spikeinterface-waveforms",
        "schema_version": 4,
        "generated_at_utc": "2026-08-25T00:00:00+00:00",
        "session": {"name": "fixture", "probe": "A"},
        "recording": {
            "sampling_frequency_hz": 30_000.0,
            "num_frames": 1_800_000,
            "duration_minutes": 1.0,
        },
        "units": {"scope": unit_scope, "count": len(unit_ids)},
        "waveform": {
            "selection_method": "uniform",
            "max_spikes_per_unit": 500,
            "seed": 0,
            "pre_ms": 0.5,
            "post_ms": 0.25,
            "nbefore": 2,
            "num_samples": len(TIMES_MS),
        },
        "files": {"units": "units.csv", "spike_positions": None},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _write_csv(
        root / "channels.csv",
        (
            "channel_index",
            "channel_id",
            "raw_channel_index",
            "x_um",
            "y_um",
            "shank_id",
        ),
        list(CHANNELS),
    )
    _write_csv(
        root / "waveform_time.csv",
        ("sample_index", "sample_offset", "time_ms"),
        [
            (sample_index, sample_index - 2, time_ms)
            for sample_index, time_ms in enumerate(TIMES_MS)
        ],
    )
    unit_rows: list[tuple[object, ...]] = []
    for unit_index, unit_id in enumerate(unit_ids):
        unit_dir = root / f"Unit{unit_id}"
        unit_dir.mkdir()
        _write_template(unit_dir / "template_uv.csv.gz", unit_id)
        unit_rows.append(
            (
                unit_index,
                unit_id,
                "good",
                1000 + unit_index,
                100 + unit_index,
                90.0,
                2,
                102,
                0.0,
                60.0,
                42.0,
                f"Unit{unit_id}",
            )
        )
    _write_csv(
        root / "units.csv",
        (
            "unit_index",
            "unit_id",
            "quality",
            "total_spike_count",
            "selected_spike_count",
            "time_coverage_percent",
            "best_channel_index",
            "best_channel_id",
            "best_channel_x_um",
            "best_channel_y_um",
            "max_ptp_uv",
            "unit_data_dir",
        ),
        unit_rows,
    )
    return root


def _replace_csv_value(
    path: Path,
    *,
    row_index: int,
    column: str,
    value: object,
) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        rows = list(reader)
    rows[row_index][column] = str(value)
    _write_csv(
        path,
        fieldnames,
        [tuple(row[name] for name in fieldnames) for row in rows],
    )


def test_payload_reuses_notebook_selection_baseline_and_order(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path / "ProbeA")
    store = WaveformArtifactStore.open(artifact)

    payload = store.payload_for(7, "same_x_column")

    assert WAVEFORM_CHANNEL_MODES == ("same_x_column", "same_shank")
    assert tuple(payload.channel_labels) == (
        "ch 100",
        "ch 101",
        "ch 102",
        "ch 103",
        "ch 104",
    )
    assert [channel.channel_index for channel in payload.channels] == [0, 1, 2, 3, 4]
    assert payload.values_uv.shape == (5, 4)
    expected = np.outer(np.arange(1.0, 6.0), (-0.5, 0.5, 1.5, 2.5))
    np.testing.assert_allclose(payload.values_uv, expected)
    # The scientific collection scales from the complete baseline-corrected
    # template, rather than only the five displayed rows.
    assert payload.amplitude_limit_uv == pytest.approx(17.5)
    assert payload.best_channel_index == 2
    assert payload.best_channel_row == 2
    assert payload.best_channel.channel_id == 102
    assert payload.summary.unit_id == 7
    assert payload.matrix is payload.values_uv
    assert payload.times_ms is payload.time_ms
    assert len(payload.time_edges_ms) == len(payload.time_ms) + 1
    assert not payload.values_uv.flags.writeable
    assert not payload.time_ms.flags.writeable
    with pytest.raises(ValueError):
        payload.values_uv[0, 0] = 99.0
    assert store.source_paths_for_unit(7) == (
        artifact / "manifest.json",
        artifact / "channels.csv",
        artifact / "waveform_time.csv",
        artifact / "units.csv",
        artifact / "Unit7" / "template_uv.csv.gz",
    )


def test_selector_is_best_plus_nearest_four_not_forced_two_by_two() -> None:
    locations = np.asarray([(row[3], row[4]) for row in CHANNELS], dtype=float)
    shanks = np.asarray([row[5] for row in CHANNELS], dtype=np.int64)

    same_x = select_local_channel_indices(
        locations, shanks, 1, "same_x_column", 5
    )
    # Best y=90 has one selected site above and three below.  This preserves
    # the notebook's current edge behaviour instead of synthesizing 2+2 slots.
    assert same_x.tolist() == [0, 1, 2, 3, 4]

    same_shank = select_local_channel_indices(
        locations, shanks, 2, "same_shank", 5
    )
    assert same_shank.tolist() == [0, 1, 5, 2, 3]
    only_site = select_local_channel_indices(
        locations, shanks, 6, "same_shank", 5
    )
    assert only_site.tolist() == [6]
    assert not same_shank.flags.writeable


@pytest.mark.parametrize("mode", ["same_y", "nearest", ""])
def test_selector_rejects_unknown_modes(mode: str) -> None:
    locations = np.asarray([(row[3], row[4]) for row in CHANNELS], dtype=float)
    shanks = np.asarray([row[5] for row in CHANNELS], dtype=np.int64)
    with pytest.raises(WaveformArtifactError, match="mode must be"):
        select_local_channel_indices(locations, shanks, 2, mode, 5)  # type: ignore[arg-type]


def test_baseline_requires_a_sample_at_or_before_window() -> None:
    with pytest.raises(WaveformArtifactError, match="No waveform samples"):
        baseline_correct_template(
            np.ones((2, 1)),
            np.asarray((0.0, 0.25)),
            baseline_end_ms=-0.25,
        )


def test_missing_rf_unit_is_an_individual_unavailable_result(tmp_path: Path) -> None:
    store = WaveformArtifactStore.open(_write_artifact(tmp_path / "ProbeA", unit_ids=(7,)))

    with pytest.raises(KeyError, match="Unit 99 is not available"):
        store.payload_for(99)
    assert store.cached_unit_ids == ()


def test_unit_templates_are_lazy_and_use_bounded_lru(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path / "ProbeA")
    corrupt_path = artifact / "Unit9" / "template_uv.csv.gz"
    corrupt_path.write_bytes(b"not a gzip stream")

    store = WaveformArtifactStore.open(artifact, template_cache_size=2)
    assert store.cached_unit_ids == ()
    first_7 = store.load_unit_template(7)
    first_8 = store.load_unit_template(8)
    assert store.load_unit_template(7) is first_7
    assert store.cached_unit_ids == (8, 7)
    with pytest.raises(WaveformArtifactError, match="valid gzip"):
        store.load_unit_template(9)
    assert store.cached_unit_ids == (8, 7)

    _write_template(corrupt_path, 9)
    store.load_unit_template(9)
    assert store.cached_unit_ids == (7, 9)
    assert store.load_unit_template(8) is not first_8


@pytest.mark.parametrize("bad_version", [3, 4.0, True, "4"])
def test_manifest_schema_version_is_strict(tmp_path: Path, bad_version: object) -> None:
    artifact = _write_artifact(tmp_path / "ProbeA")
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = bad_version
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(WaveformArtifactError, match="schema version"):
        WaveformArtifactStore.open(artifact)


@pytest.mark.parametrize("unit_scope", ["all", "good", "present_good"])
def test_manifest_accepts_schema_v4_unit_scopes(
    tmp_path: Path,
    unit_scope: str,
) -> None:
    artifact = _write_artifact(
        tmp_path / unit_scope / "ProbeA",
        unit_ids=(7,),
        unit_scope=unit_scope,
    )
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if unit_scope == "present_good":
        # Current session-split exports use canonical concatenated templates
        # and label the good units retained in this session as present_good.
        manifest["canonical_source"] = {
            "session": "260824_12345",
            "template_method": "kilosort_cluster_template_unwhitened",
        }
        manifest["session_spikes"] = {
            "all": "session_split_sorting",
            "selected": "uniform_session_local_selection",
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    store = WaveformArtifactStore.open(artifact)

    assert store.unit_scope == unit_scope


def test_manifest_rejects_unknown_unit_scope(tmp_path: Path) -> None:
    artifact = _write_artifact(
        tmp_path / "ProbeA",
        unit_ids=(7,),
        unit_scope="present",
    )

    with pytest.raises(WaveformArtifactError, match="manifest.units.scope"):
        WaveformArtifactStore.open(artifact)


def test_shared_csv_headers_and_indices_are_strict(tmp_path: Path) -> None:
    bad_header = _write_artifact(tmp_path / "bad_header" / "ProbeA")
    channels_path = bad_header / "channels.csv"
    rows = list(CHANNELS)
    _write_csv(
        channels_path,
        (
            "channel_index",
            "channel_id",
            "raw_channel_index",
            "x_um",
            "y_um",
            "shank_id",
            "extra",
        ),
        [(*row, "unexpected") for row in rows],
    )
    with pytest.raises(WaveformArtifactError, match="header must be"):
        WaveformArtifactStore.open(bad_header)

    bad_index = _write_artifact(tmp_path / "bad_index" / "ProbeA")
    _replace_csv_value(
        bad_index / "channels.csv", row_index=1, column="channel_index", value=4
    )
    with pytest.raises(WaveformArtifactError, match="contiguous"):
        WaveformArtifactStore.open(bad_index)


def test_time_axis_and_best_channel_cross_checks_are_strict(tmp_path: Path) -> None:
    bad_time = _write_artifact(tmp_path / "bad_time" / "ProbeA")
    _replace_csv_value(
        bad_time / "waveform_time.csv", row_index=2, column="time_ms", value=-0.3
    )
    with pytest.raises(WaveformArtifactError, match="strictly increasing"):
        WaveformArtifactStore.open(bad_time)

    bad_best = _write_artifact(tmp_path / "bad_best" / "ProbeA")
    _replace_csv_value(
        bad_best / "units.csv", row_index=0, column="best_channel_id", value=999
    )
    with pytest.raises(WaveformArtifactError, match="best_channel_id"):
        WaveformArtifactStore.open(bad_best)


def test_unit_paths_are_confined_even_through_symlinks(tmp_path: Path) -> None:
    traversing = _write_artifact(tmp_path / "traversing" / "ProbeA")
    _replace_csv_value(
        traversing / "units.csv",
        row_index=0,
        column="unit_data_dir",
        value="../outside",
    )
    with pytest.raises(WaveformArtifactError, match="stay within"):
        WaveformArtifactStore.open(traversing)

    linked = _write_artifact(tmp_path / "linked" / "ProbeA")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (linked / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name == "nt":
            pytest.skip("Windows runner does not grant symlink creation privilege")
        raise
    _replace_csv_value(
        linked / "units.csv", row_index=0, column="unit_data_dir", value="escape"
    )
    with pytest.raises(WaveformArtifactError, match="stay within"):
        WaveformArtifactStore.open(linked)


def test_template_header_is_checked_on_lazy_load(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path / "ProbeA", unit_ids=(7,))
    template_path = artifact / "Unit7" / "template_uv.csv.gz"
    with gzip.open(template_path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("sample_index", "wrong_channel"))
        writer.writerow((0, 1.0))

    store = WaveformArtifactStore.open(artifact)
    with pytest.raises(WaveformArtifactError, match="header does not match"):
        store.load_unit_template(7)


def test_template_row_count_is_checked_on_lazy_load(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path / "ProbeA", unit_ids=(7,))
    template_path = artifact / "Unit7" / "template_uv.csv.gz"
    header = (
        "sample_index",
        *(f"chidx_{index:03d}_uv" for index in range(len(CHANNELS))),
    )
    with gzip.open(template_path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerow((0, *([1.0] * len(CHANNELS))))

    store = WaveformArtifactStore.open(artifact)
    with pytest.raises(WaveformArtifactError, match="row count"):
        store.load_unit_template(7)


def test_discovery_is_probe_specific_and_bounded_to_session_data(tmp_path: Path) -> None:
    data_dir = tmp_path / "260625_3" / "data"
    rf_path = data_dir / "rf_maps" / "example_A.json"
    rf_path.parent.mkdir(parents=True)
    rf_path.write_text("{}", encoding="utf-8")
    artifact = _write_artifact(data_dir / "waveform" / "ProbeA")

    assert discover_waveform_artifact(rf_path) == artifact.resolve()
    assert discover_waveform_artifact(artifact / "manifest.json") == artifact.resolve()
    assert discover_waveform_artifact(data_dir / "rf_maps" / "unknown.json") is None
