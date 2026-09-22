"""Format compatibility, lazy validation, and background cache behavior."""

import json
import os
import threading
import time
from pathlib import Path

import numpy as np
import pytest

import rfmapping_viewer.rf_archive as archive_module
from rfmapping_viewer.rf_archive import IndexedRFMapList
from rfmapping_viewer.rf_dataset import is_indexed_rfmap, load_rf_maps
from rfmapping_viewer.rf_model import RFMappingData


def write_pair(directory: Path, shape=(4, 2, 3, 5)):
    n, y, x, t = shape
    counts = np.arange(np.prod(shape), dtype=float).reshape(shape) % 19 + 1
    ids = [41, 7, 200, 3][:n]
    raw = dict(
        unitsSpikeCountsSize=list(shape),
        unitPool=ids,
        xPositions=list(range(x)),
        yPositions=list(range(y)),
        timeBinEdges=np.linspace(-0.1, 0.4, t + 1).tolist(),
        occupancyTimeSec=np.full((y, x), 0.75).tolist(),
        responseUnits="spike_count",
        responseNormalization="none",
        spikeCountDefinition="each_qualifying_trial_contributes_once_per_final_spatial_bin",
        occupancyTimeDefinition="sum_of_qualifying_trial_durations_per_final_spatial_bin",
        occupancyTimeSecSize=[y, x],
        stimulusGeometry="vertical_bar_full_height" if y == 1 else "square",
    )
    old = directory / "old.rfmap"
    old.write_text(json.dumps({**raw, "unitsSpikeCounts": counts.tolist()}))
    shared = {
        key: np.asarray(raw.pop(key), dtype=np.int64 if key == "unitPool" else float)
        for key in (
            "unitPool",
            "xPositions",
            "yPositions",
            "timeBinEdges",
            "occupancyTimeSec",
        )
    }
    raw.update(
        format="rfmap",
        formatVersion=2,
        storage="indexed_npz",
        unitArrayAxes=["y", "x", "time"],
        unitArrayKeyPattern="unit_{unit_id}",
    )
    arrays = {
        **shared,
        "metadata": np.frombuffer(json.dumps(raw).encode(), dtype=np.uint8),
        **{f"unit_{uid}": np.asfortranarray(counts[i]) for i, uid in enumerate(ids)},
    }
    new = directory / "new.rfmap"
    with new.open("wb") as f:
        np.savez_compressed(f, **arrays)
    return old, new, arrays


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


@pytest.mark.parametrize(
    "shape", [(4, 2, 3, 5), (1, 1, 1, 1), (1, 1, 5, 4), (1, 4, 1, 5), (2, 3, 4, 1)]
)
def test_both_formats_match_counts_rates_axes_and_windows(tmp_path, shape):
    old, new, _ = write_pair(tmp_path, shape)
    assert not is_indexed_rfmap(old) and is_indexed_rfmap(new)
    expected = RFMappingData(old)
    actual = RFMappingData(new)
    try:
        assert actual.unit_archive.cache_count == 1
        assert len(actual.counts) == shape[0]
        assert expected.unit_pool == actual.unit_pool
        for index in range(shape[0]):
            a, b = expected.rf_map(index), actual.rf_map(index)
            for attr in (
                "spike_counts",
                "x_positions",
                "y_positions",
                "time_bin_edges_s",
                "occupancy_time_s",
            ):
                np.testing.assert_array_equal(getattr(a, attr), getattr(b, attr))
            assert b.shape == shape[1:] and not b.spike_counts.flags.writeable
            assert b.unit_index == index and b.unit_id == expected.unit_pool[index]
            for mode in ("Spike count", "Mean firing rate (Hz)"):
                np.testing.assert_array_equal(
                    expected.response_matrix(index, 0, shape[-1] - 1, mode),
                    actual.response_matrix(index, 0, shape[-1] - 1, mode),
                )
        full = load_rf_maps(new)
        assert full.unit_ids == expected.unit_pool
    finally:
        actual.close()


def test_archive_reads_only_first_unit_and_reuses_cached_objects(tmp_path, monkeypatch):
    _, new, _ = write_pair(tmp_path)
    with np.load(new, allow_pickle=False) as z:
        archive_type = type(z)
    calls = []
    original = archive_type.__getitem__

    def read(self, key):
        calls.append(key)
        return original(self, key)

    monkeypatch.setattr(archive_type, "__getitem__", read)
    data = RFMappingData(new)
    try:
        assert [k for k in calls if k.startswith("unit_")] == ["unit_41"]
        first = data.rf_map(0)
        assert data.rf_map_by_unit_id(41) is first
        assert data.rf_map_by_unit_id(3).unit_index == 3
        assert data.rf_map_by_unit_id(3) is data.rf_map(3)
        assert [k for k in calls if k.startswith("unit_")] == ["unit_41", "unit_3"]
    finally:
        data.close()


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -1.0, 0.25, float(2**64)])
def test_invalid_later_unit_fails_when_requested(tmp_path, invalid):
    _, new, arrays = write_pair(tmp_path)
    arrays["unit_7"][0, 0, 0] = invalid
    with new.open("wb") as f:
        np.savez_compressed(f, **arrays)
    maps = IndexedRFMapList(new)
    try:
        assert maps.cache_count == 1
        with pytest.raises(ValueError, match="unit 7"):
            maps.by_index(1)
        assert maps.cache_count == 1
    finally:
        maps.close()


@pytest.mark.parametrize(
    "change", ["missing", "version", "shape", "zero_occupancy", "bool", "axis"]
)
def test_rejects_invalid_indexed_contract(tmp_path, change):
    _, new, arrays = write_pair(tmp_path)
    if change == "missing":
        del arrays["unit_200"]
    elif change == "version":
        meta = json.loads(arrays["metadata"].tobytes())
        meta["formatVersion"] = 3
        arrays["metadata"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
    elif change == "shape":
        arrays["unit_41"] = arrays["unit_41"][0]
    elif change == "zero_occupancy":
        arrays["occupancyTimeSec"][0, 0] = 0
    elif change == "bool":
        arrays["unit_41"] = arrays["unit_41"].astype(bool)
    elif change == "axis":
        arrays["timeBinEdges"][1] = arrays["timeBinEdges"][0]
    with new.open("wb") as f:
        np.savez_compressed(f, **arrays)
    with pytest.raises(ValueError):
        IndexedRFMapList(new)


@pytest.mark.parametrize(
    "field",
    ["responseUnits", "responseNormalization"],
)
def test_indexed_metadata_requires_raw_count_semantics(tmp_path, field):
    _, new, arrays = write_pair(tmp_path)
    meta = json.loads(arrays["metadata"].tobytes())
    del meta[field]
    arrays["metadata"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
    with new.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    with pytest.raises((ValueError, KeyError), match=field):
        IndexedRFMapList(new)


@pytest.mark.parametrize(
    "shape", [(4, 2, 3, 5), (1, 1, 1, 1), (4, 1, 30, 500), (1, 4, 1, 5)]
)
def test_both_formats_can_omit_descriptions_and_occupancy_size(tmp_path, shape):
    old, new, arrays = write_pair(tmp_path, shape)
    expected = RFMappingData(old)
    payload = json.loads(old.read_text())
    meta = json.loads(arrays["metadata"].tobytes())
    omitted = ("spikeCountDefinition", "occupancyTimeDefinition", "occupancyTimeSecSize")
    for field in omitted:
        del payload[field]
        del meta[field]
    # MATLAB JSON collapses singleton occupancy axes and emits integer counts.
    payload["occupancyTimeSec"] = np.asarray(payload["occupancyTimeSec"]).squeeze().tolist()
    payload["unitsSpikeCounts"] = np.asarray(payload["unitsSpikeCounts"], dtype=int).tolist()
    old.write_text(json.dumps(payload))
    arrays["metadata"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
    with new.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    for path in (old, new):
        actual = RFMappingData(path)
        try:
            assert actual.unit_pool == expected.unit_pool
            np.testing.assert_array_equal(actual.time_bin_edges, expected.time_bin_edges)
            assert actual.n_bins == shape[-1]
            for index in range(shape[0]):
                a, b = actual.rf_map(index), expected.rf_map(index)
                np.testing.assert_array_equal(a.spike_counts, b.spike_counts)
                np.testing.assert_array_equal(a.occupancy_time_s, b.occupancy_time_s)
                assert all(field not in a.metadata for field in omitted)
                for mode in ("Spike count", "Mean firing rate (Hz)"):
                    np.testing.assert_array_equal(
                        actual.response_matrix(index, 0, shape[-1] - 1, mode),
                        expected.response_matrix(index, 0, shape[-1] - 1, mode),
                    )
        finally:
            actual.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [("responseUnits", "spike_rate"), ("responseUnits", None),
     ("responseNormalization", "occupancy"), ("responseNormalization", None),
     ("spikeCountDefinition", "all_overlapping_trials"),
     ("spikeCountDefinition", None),
     ("occupancyTimeDefinition", "number_of_trials"),
     ("occupancyTimeDefinition", None), ("occupancyTimeSecSize", [3, 2]),
     ("occupancyTimeSecSize", None)],
)
def test_indexed_metadata_rejects_incompatible_explicit_semantics(tmp_path, field, value):
    _, new, arrays = write_pair(tmp_path)
    meta = json.loads(arrays["metadata"].tobytes())
    meta[field] = value
    arrays["metadata"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
    with new.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    with pytest.raises(ValueError, match=field):
        IndexedRFMapList(new)


@pytest.mark.parametrize("storage", ["json", "indexed"])
@pytest.mark.parametrize("change", ["shape", "zero_occupancy"])
def test_occupancy_is_validated_without_optional_metadata(tmp_path, storage, change):
    old, new, arrays = write_pair(tmp_path)
    payload = json.loads(old.read_text())
    meta = json.loads(arrays["metadata"].tobytes())
    for field in ("spikeCountDefinition", "occupancyTimeDefinition", "occupancyTimeSecSize"):
        del payload[field]
        del meta[field]
    arrays["metadata"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
    if change == "shape":
        arrays["occupancyTimeSec"] = arrays["occupancyTimeSec"].T
    else:
        arrays["occupancyTimeSec"][0, 0] = 0
    payload["occupancyTimeSec"] = arrays["occupancyTimeSec"].tolist()
    old.write_text(json.dumps(payload))
    with new.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
    with pytest.raises(ValueError, match="occupancyTimeSec"):
        load_rf_maps(old if storage == "json" else new)


def test_preload_prioritizes_latest_selection_and_caches_once(tmp_path, monkeypatch):
    _, new, _ = write_pair(tmp_path)
    maps = IndexedRFMapList(new)
    started = threading.Event()
    release = threading.Event()
    original = archive_module._compact_array
    reads = []

    def read(a, shape):
        reads.append(float(a.flat[0]))
        if len(reads) == 1:
            started.set()
            assert release.wait(5)
        return original(a, shape)

    monkeypatch.setattr(archive_module, "_compact_array", read)
    try:
        maps.start_preload()
        assert started.wait(5)
        maps.request(2)
        maps.request(3)
        assert maps.cache_count == 1
        release.set()
        wait_for(lambda: maps.cache_count == 4)
        # Source starts at 1,12,4,15 for units 0,1,2,3.
        assert reads == [12.0, 15.0, 4.0]
        assert maps.by_unit_id(3) is maps.by_index(3)
    finally:
        release.set()
        maps.close()
        if maps._worker:
            maps._worker.join(5)


def test_cancel_does_not_wait_for_network_read_or_publish_late_unit(
    tmp_path, monkeypatch
):
    _, new, _ = write_pair(tmp_path)
    maps = IndexedRFMapList(new)
    started = threading.Event()
    release = threading.Event()
    original = archive_module._compact_array

    def read(a, shape):
        started.set()
        assert release.wait(5)
        return original(a, shape)

    monkeypatch.setattr(archive_module, "_compact_array", read)
    maps.start_preload()
    assert started.wait(5)
    before = time.monotonic()
    maps.close()
    assert time.monotonic() - before < 0.1
    release.set()
    maps._worker.join(5)
    assert maps.cache_count == 1 and maps._archive.zip is None


def test_error_retains_loaded_units_and_retry_completes(tmp_path, monkeypatch):
    _, new, _ = write_pair(tmp_path)
    maps = IndexedRFMapList(new)
    original = archive_module._compact_array

    def fail(a, shape):
        raise OSError("temporary read failure")

    monkeypatch.setattr(archive_module, "_compact_array", fail)
    maps.start_preload()
    wait_for(lambda: maps.error is not None)
    maps._worker.join(5)
    assert maps.cache_count == 1 and maps.by_index(0).unit_id == 41
    monkeypatch.setattr(archive_module, "_compact_array", original)
    maps.start_preload(retry=True)
    wait_for(lambda: maps.cache_count == 4)
    assert maps.error is None
    maps.close()


def test_changed_source_cannot_mix_units_from_different_files(tmp_path):
    _, new, _ = write_pair(tmp_path)
    maps = IndexedRFMapList(new)
    info = new.stat()
    os.utime(new, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
    try:
        with pytest.raises(ValueError, match="changed"):
            maps.by_index(1)
        assert maps.cache_count == 1
    finally:
        maps.close()


def test_indexed_export_matches_json_for_uncached_unit(tmp_path):
    from test_gui_figure_export import _snapshot
    from rfmapping_viewer.figure_composer import GUIFigureDataProvider
    from rfmapping_viewer.figure_export import (
        ExportPage,
        ExportPlan,
        FigureFormat,
        PlotKind,
        PlotSpec,
        render_live_preview,
    )

    old, new, _ = write_pair(tmp_path, shape=(4, 2, 3, 4))
    a, b = RFMappingData(old), RFMappingData(new)
    try:
        assert b.unit_archive.cache_count == 1
        providers = [GUIFigureDataProvider(data, _snapshot()) for data in (a, b)]
        plan = ExportPlan(
            FigureFormat.PNG,
            (7,),
            (ExportPage("RF and timeline", (PlotSpec(PlotKind.RF_CARTESIAN),)),),
            tmp_path / "preview.png",
        )
        images = [render_live_preview(plan, 7, 0, data_provider=p) for p in providers]
        np.testing.assert_array_equal(np.asarray(images[0]), np.asarray(images[1]))
        assert b.unit_archive.cache_count == 2
    finally:
        b.close()


def test_packaging_smoke_fixture_restores_legacy_singleton_axes(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    script = (root / "script/build_python_stable_macos_app.sh").read_text()
    section = script[script.index("INDEXED_SMOKE="):]
    code = section.split("<<'PYTHON'\n", 1)[1].split("\nPYTHON", 1)[0]
    output = tmp_path / "smoke.rfmap"
    json_output = tmp_path / "optional-metadata-smoke.rfmap"
    subprocess.run(
        [sys.executable, "-", str(root / "tests/fixtures/release_smoke_rf.json"),
         str(output), str(json_output)],
        input=code, text=True, check=True,
    )
    assert is_indexed_rfmap(output)
    assert not is_indexed_rfmap(json_output)
    for path in (output, json_output):
        maps = load_rf_maps(path)
        assert maps[0].n_y == 1
        assert all(
            field not in maps[0].metadata
            for field in ("spikeCountDefinition", "occupancyTimeDefinition", "occupancyTimeSecSize")
        )
        subprocess.run([sys.executable, str(root / "rfmapping_gui.py"), "--self-test", str(path)], check=True)
