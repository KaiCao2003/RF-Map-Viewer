from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from Utils.rfmap import RFMap, RFMapList, load_rf_maps


def _payload(
    counts: np.ndarray | None = None,
    *,
    unit_pool: list[int] | None = None,
    time_bin_edges: list[float] | None = None,
) -> dict[str, object]:
    if counts is None:
        counts = np.arange(24, dtype=float).reshape(2, 2, 2, 3)
    if unit_pool is None:
        unit_pool = [41, 7]
    if time_bin_edges is None:
        time_bin_edges = [-0.1, 0.0, 0.1, 0.2]

    return {
        "unitsSpikeCounts": counts.tolist(),
        "unitsSpikeCountsSize": list(counts.shape),
        "unitPool": unit_pool,
        "xPositions": [-10.0, 10.0],
        "yPositions": [-5.0, 5.0],
        "timeBinEdges": time_bin_edges,
        "stimulusPresentationCounts": [[2, 3], [4, 5]],
        "sessionName": "fixture-session",
    }


def _write_payload(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "rfmapping.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_rf_maps_returns_one_object_per_unit_in_source_order(
    tmp_path: Path,
) -> None:
    counts = np.arange(24, dtype=float).reshape(2, 2, 2, 3)
    rf_maps = load_rf_maps(_write_payload(tmp_path, _payload(counts)))

    assert isinstance(rf_maps, RFMapList)
    assert len(rf_maps) == 2
    assert all(isinstance(rf_map, RFMap) for rf_map in rf_maps)
    assert [rf_map.unit_id for rf_map in rf_maps] == [41, 7]

    first = rf_maps.by_index(0)
    second = rf_maps.by_index(1)
    assert first is rf_maps.by_unit_id(41)
    assert second is rf_maps.by_unit_id(7)
    np.testing.assert_array_equal(first.spike_counts, counts[0])
    np.testing.assert_array_equal(second.spike_counts, counts[1])

    assert first.spike_counts.shape == (2, 2, 3)
    np.testing.assert_array_equal(first.x_positions, [-10.0, 10.0])
    np.testing.assert_array_equal(first.y_positions, [-5.0, 5.0])
    np.testing.assert_array_equal(first.time_bin_edges_s, [-0.1, 0.0, 0.1, 0.2])
    np.testing.assert_array_equal(first.presentation_counts, [[2, 3], [4, 5]])
    assert first.metadata["sessionName"] == "fixture-session"


def test_rf_map_list_retrieval_errors_are_unambiguous(tmp_path: Path) -> None:
    rf_maps = load_rf_maps(_write_payload(tmp_path, _payload()))

    with pytest.raises(IndexError):
        rf_maps.by_index(-1)
    with pytest.raises(IndexError):
        rf_maps.by_index(2)
    with pytest.raises(KeyError):
        rf_maps.by_unit_id(0)


def test_rf_map_list_never_confuses_an_index_with_a_unit_id(
    tmp_path: Path,
) -> None:
    payload = _payload(unit_pool=[1, 0])
    rf_maps = load_rf_maps(_write_payload(tmp_path, payload))

    assert rf_maps.by_index(1).unit_id == 0
    assert rf_maps.by_unit_id(1) is rf_maps.by_index(0)
    assert rf_maps.by_unit_id(1) is not rf_maps.by_index(1)


def test_rf_map_list_uses_original_indices_after_reordering(tmp_path: Path) -> None:
    loaded = load_rf_maps(_write_payload(tmp_path, _payload()))
    reordered = RFMapList([loaded[1], loaded[0]], loaded.source_path)

    assert reordered[0] is loaded.by_index(1)
    assert reordered[-1] is loaded.by_index(0)
    assert reordered.by_index(0) is loaded.by_index(0)
    assert reordered.by_index(1) is loaded.by_index(1)


def test_load_rf_maps_allows_missing_presentation_counts(tmp_path: Path) -> None:
    payload = _payload()
    payload.pop("stimulusPresentationCounts")

    rf_maps = load_rf_maps(_write_payload(tmp_path, payload))

    assert all(rf_map.presentation_counts is None for rf_map in rf_maps)


def test_sum_between_s_returns_a_new_single_bin_rf_map(tmp_path: Path) -> None:
    counts = np.arange(24, dtype=float).reshape(2, 2, 2, 3)
    original = load_rf_maps(_write_payload(tmp_path, _payload(counts))).by_index(1)

    summed = original.sum_between_s(0.0, 0.2)

    assert isinstance(summed, RFMap)
    assert summed is not original
    assert summed.unit_id == 7
    assert summed.spike_counts.shape == (2, 2, 1)
    np.testing.assert_array_equal(
        summed.spike_counts[..., 0],
        counts[1, ..., 1:3].sum(axis=-1),
    )
    np.testing.assert_array_equal(summed.time_bin_edges_s, [0.0, 0.2])

    # Summing must not mutate the source object.
    np.testing.assert_array_equal(original.spike_counts, counts[1])
    np.testing.assert_array_equal(
        original.time_bin_edges_s,
        [-0.1, 0.0, 0.1, 0.2],
    )


@pytest.mark.parametrize("edge", [-0.1, 0.0, 0.1, 0.2])
def test_sum_between_s_equal_endpoints_returns_one_zero_bin(
    tmp_path: Path,
    edge: float,
) -> None:
    original = load_rf_maps(_write_payload(tmp_path, _payload())).by_index(0)

    summed = original.sum_between_s(edge, edge)

    assert summed.spike_counts.shape == (2, 2, 1)
    np.testing.assert_array_equal(summed.spike_counts, np.zeros((2, 2, 1)))
    np.testing.assert_array_equal(summed.time_bin_edges_s, [edge, edge])


def test_sum_between_s_uses_strict_edges_with_small_float_tolerance(
    tmp_path: Path,
) -> None:
    original = load_rf_maps(_write_payload(tmp_path, _payload())).by_index(0)

    tolerated = original.sum_between_s(5e-13, 0.2 - 5e-13)
    np.testing.assert_array_equal(
        tolerated.spike_counts[..., 0],
        original.spike_counts[..., 1:3].sum(axis=-1),
    )

    with pytest.raises(ValueError):
        original.sum_between_s(2e-12, 0.2)
    with pytest.raises(ValueError):
        original.sum_between_s(0.0, 0.2 - 2e-12)
    with pytest.raises(ValueError):
        original.sum_between_s(0.05, 0.2)


def test_sum_between_s_prefers_exact_edge_and_rejects_ambiguous_tolerance(
    tmp_path: Path,
) -> None:
    counts = np.arange(8, dtype=float).reshape(1, 2, 2, 2)
    rf_map = load_rf_maps(
        _write_payload(
            tmp_path,
            _payload(
                counts,
                unit_pool=[5],
                time_bin_edges=[0.0, 1e-12, 2e-12],
            ),
        )
    ).by_index(0)

    exact = rf_map.sum_between_s(1e-12, 2e-12)
    np.testing.assert_array_equal(exact.spike_counts[..., 0], counts[0, ..., 1])

    with pytest.raises(ValueError, match="multiple timeBinEdges"):
        rf_map.sum_between_s(0.5e-12, 2e-12)


def test_sum_between_s_rejects_reversed_window(tmp_path: Path) -> None:
    original = load_rf_maps(_write_payload(tmp_path, _payload())).by_index(0)

    with pytest.raises(ValueError):
        original.sum_between_s(0.1, 0.0)


def test_detect_bumps_uses_one_scalar_baseline_for_all_pixels_and_times(
    tmp_path: Path,
) -> None:
    # Across the first two time bins, the eight values sum to 80. The scalar
    # baseline is therefore 10 and the default 1.2 ratio makes 12 the strict
    # threshold for every pixel and every time bin.
    unit_counts = np.asarray(
        [
            [[8.0, 12.0, 12.0], [9.0, 11.0, 12.0001]],
            [[10.0, 10.0, 0.0], [11.0, 9.0, 13.0]],
        ]
    )
    counts = unit_counts[np.newaxis, ...]
    path = _write_payload(
        tmp_path,
        _payload(
            counts,
            unit_pool=[314],
            time_bin_edges=[-0.2, -0.1, 0.0, 0.1],
        ),
    )
    rf_map = load_rf_maps(path).by_unit_id(314)

    mask = rf_map.detect_bumps(baseline_start_s=-0.2, baseline_end_s=0.0)

    assert mask.dtype == np.uint8
    assert mask.shape == (2, 2, 3)
    np.testing.assert_array_equal(
        mask,
        np.asarray(
            [
                [[0, 0, 0], [0, 0, 1]],
                [[0, 0, 0], [0, 0, 1]],
            ],
            dtype=np.uint8,
        ),
    )


def test_detect_bumps_warns_when_baseline_itself_contains_a_bump(
    tmp_path: Path,
) -> None:
    unit_counts = np.asarray(
        [
            [[0.0, 1.0], [0.0, 1.0]],
            [[0.0, 1.0], [10.0, 1.0]],
        ]
    )
    counts = unit_counts[np.newaxis, ...]
    rf_map = load_rf_maps(
        _write_payload(
            tmp_path,
            _payload(
                counts,
                unit_pool=[88],
                time_bin_edges=[-0.1, 0.0, 0.1],
            ),
        )
    ).by_unit_id(88)

    with pytest.warns(UserWarning, match="threshold"):
        mask = rf_map.detect_bumps()

    assert mask.dtype == np.uint8
    assert mask[1, 1, 0] == 1


@pytest.mark.parametrize("threshold_ratio", [1.0, 0.0, -1.0, np.nan, np.inf])
def test_detect_bumps_rejects_invalid_threshold_ratio(
    tmp_path: Path,
    threshold_ratio: float,
) -> None:
    rf_map = load_rf_maps(_write_payload(tmp_path, _payload())).by_index(0)

    with pytest.raises(ValueError):
        rf_map.detect_bumps(threshold_ratio=threshold_ratio)


def test_detect_bumps_requires_a_nonempty_edge_aligned_baseline(
    tmp_path: Path,
) -> None:
    rf_map = load_rf_maps(_write_payload(tmp_path, _payload())).by_index(0)

    with pytest.raises(ValueError):
        rf_map.detect_bumps(baseline_start_s=0.0, baseline_end_s=0.0)
    with pytest.raises(ValueError):
        rf_map.detect_bumps(baseline_start_s=-0.05, baseline_end_s=0.0)
    with pytest.raises(ValueError):
        rf_map.detect_bumps(baseline_start_s=0.1, baseline_end_s=0.0)


def test_detect_spatial_bumps_uses_a_2d_maximum_filter_per_time_bin(
    tmp_path: Path,
) -> None:
    counts = np.zeros((1, 3, 3, 2), dtype=float)
    counts[0, ..., 1] = np.asarray(
        [
            [1.0, 2.0, 1.0],
            [2.0, 9.0, 8.0],
            [1.0, 8.0, 7.0],
        ]
    )
    payload = _payload(
        counts,
        unit_pool=[501],
        time_bin_edges=[-0.1, 0.0, 0.1],
    )
    payload["xPositions"] = [-10.0, 0.0, 10.0]
    payload["yPositions"] = [-10.0, 0.0, 10.0]
    payload["stimulusPresentationCounts"] = np.ones((3, 3), dtype=int).tolist()
    rf_map = load_rf_maps(_write_payload(tmp_path, payload)).by_unit_id(501)

    mask = rf_map.detect_spatial_bumps(spatial_size=3)

    expected = np.zeros((3, 3, 2), dtype=np.uint8)
    expected[1, 1, 1] = 1
    np.testing.assert_array_equal(mask, expected)


@pytest.mark.parametrize("spatial_size", [0, 2, -1, True, (3, 2), (3,), (3, 3, 3)])
def test_detect_spatial_bumps_rejects_invalid_filter_sizes(
    tmp_path: Path,
    spatial_size: object,
) -> None:
    rf_map = load_rf_maps(_write_payload(tmp_path, _payload())).by_index(0)

    with pytest.raises(ValueError, match="spatial_size"):
        rf_map.detect_spatial_bumps(spatial_size=spatial_size)  # type: ignore[arg-type]
