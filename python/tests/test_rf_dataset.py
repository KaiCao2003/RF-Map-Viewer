from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from rfmapping_viewer.rf_dataset import load_rf_maps


def _write_dataset(tmp_path: Path, **updates: object) -> Path:
    payload: dict[str, object] = {
        "unitsSpikeCounts": [
            [[[1, 2], [3, 4]]],
            [[[5, 6], [7, 8]]],
        ],
        "unitsSpikeCountsSize": [2, 1, 2, 2],
        "unitPool": [41, 7],
        "xPositions": [-10, 10],
        "yPositions": [0],
        "timeBinEdges": [-0.1, 0.0, 0.1],
        "occupancyTimeSec": [[0.2, 0.4]],
        "responseUnits": "spike_count",
        "responseNormalization": "none",
        "spikeCountDefinition": "each_qualifying_trial_contributes_once_per_final_spatial_bin",
        "occupancyTimeDefinition": "sum_of_qualifying_trial_durations_per_final_spatial_bin",
        "metadataVersion": 3,
    }
    payload.update(updates)
    payload.setdefault("occupancyTimeSecSize", payload["unitsSpikeCountsSize"][1:3])
    path = tmp_path / "rf.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_lookup_and_half_open_sum(tmp_path: Path) -> None:
    maps = load_rf_maps(_write_dataset(tmp_path))

    assert maps.unit_ids == [41, 7]
    assert maps.by_index(1).unit_id == 7
    assert maps.by_unit_id(41).unit_index == 0
    np.testing.assert_array_equal(
        maps.by_unit_id(41).sum(-0.1, 0.1).spike_counts[..., 0],
        [[3, 7]],
    )
    np.testing.assert_array_equal(
        maps.by_unit_id(41).sum(-0.1, 0.0).spike_counts[..., 0],
        [[1, 3]],
    )
    assert maps[0].metadata["metadataVersion"] == 3
    assert not maps[0].spike_counts.flags.writeable
    np.testing.assert_array_equal(maps[0].occupancy_time_s, [[0.2, 0.4]])
    assert not maps[0].occupancy_time_s.flags.writeable


@pytest.mark.parametrize(
    ("maximum", "expected_dtype"),
    [
        (255, np.dtype(np.uint8)),
        (256, np.dtype(np.uint16)),
        (65_536, np.dtype(np.uint32)),
        (2**32, np.dtype(np.uint64)),
    ],
)
def test_load_compacts_counts_to_smallest_safe_unsigned_dtype(
    tmp_path: Path,
    maximum: int,
    expected_dtype: np.dtype,
) -> None:
    maps = load_rf_maps(
        _write_dataset(
            tmp_path,
            unitsSpikeCounts=[
                [[[maximum, 2], [3, 4]]],
                [[[5, 6], [7, 8]]],
            ],
        )
    )

    assert maps[0].spike_counts.dtype == expected_dtype
    assert int(maps[0].spike_counts[0, 0, 0]) == maximum
    assert not maps[0].spike_counts.flags.writeable


def test_load_accepts_integral_json_floats_and_rejects_mixed_bool(
    tmp_path: Path,
) -> None:
    maps = load_rf_maps(
        _write_dataset(
            tmp_path,
            unitsSpikeCounts=[
                [[[1.0, 2], [3, 4]]],
                [[[5, 6], [7, 8]]],
            ],
        )
    )
    assert maps[0].spike_counts.dtype == np.dtype(np.uint8)

    with pytest.raises(ValueError, match="JSON numbers, not bool"):
        load_rf_maps(
            _write_dataset(
                tmp_path,
                unitsSpikeCounts=[
                    [[[True, 2], [3, 4]]],
                    [[[5, 6], [7, 8]]],
                ],
            )
        )


def test_accepts_scalar_positions_for_singleton_spatial_axes(
    tmp_path: Path,
) -> None:
    vertical = load_rf_maps(_write_dataset(tmp_path, yPositions=0))
    np.testing.assert_array_equal(vertical[0].y_positions, [0.0])

    horizontal = load_rf_maps(
        _write_dataset(
            tmp_path,
            unitsSpikeCounts=[
                [[[1, 2]], [[3, 4]]],
                [[[5, 6]], [[7, 8]]],
            ],
            unitsSpikeCountsSize=[2, 2, 1, 2],
            xPositions=0,
            yPositions=[-10, 10],
            occupancyTimeSec=[0.2, 0.4],
        )
    )
    np.testing.assert_array_equal(horizontal[0].x_positions, [0.0])
    assert horizontal[0].shape == (2, 1, 2)


def test_rejects_scalar_position_for_non_singleton_spatial_axis(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError, match="xPositions must be a one-dimensional array"
    ):
        load_rf_maps(_write_dataset(tmp_path, xPositions=0))


@pytest.mark.parametrize("value", [True, float("nan"), float("inf")])
def test_rejects_invalid_scalar_singleton_position(
    tmp_path: Path,
    value: object,
) -> None:
    with pytest.raises(ValueError, match="yPositions"):
        load_rf_maps(_write_dataset(tmp_path, yPositions=value))


def test_zero_spike_bin_count_uses_native_grid_and_half_open_window(
    tmp_path: Path,
) -> None:
    maps = load_rf_maps(
        _write_dataset(
            tmp_path,
            unitsSpikeCounts=[
                [[[0, 5], [3, 0]]],
                [[[0, 0], [0, 8]]],
            ],
            timeBinEdges=[-0.1, 0.0, 0.2],
        )
    )

    first = maps.by_unit_id(41)
    assert first.zero_spike_spatial_bin_count(-0.1, 0.0) == 1
    assert first.zero_spike_spatial_bin_count(0.0, 0.2) == 1
    assert first.zero_spike_spatial_bin_count(-0.1, 0.2) == 0
    assert maps.by_unit_id(7).zero_spike_spatial_bin_count(-0.1, 0.2) == 1


def test_rejects_shape_and_unit_id_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="shape"):
        load_rf_maps(
            _write_dataset(tmp_path, unitsSpikeCountsSize=[2, 1, 3, 2])
        )

    with pytest.raises(ValueError, match="unique"):
        load_rf_maps(_write_dataset(tmp_path, unitPool=[41, 41]))


def test_rejects_counts_where_occupancy_is_zero(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="zero where unitsSpikeCounts is nonzero"):
        load_rf_maps(_write_dataset(tmp_path, occupancyTimeSec=[[0, 0.4]]))


def test_rejects_all_zero_occupancy(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one positive"):
        load_rf_maps(
            _write_dataset(
                tmp_path,
                unitsSpikeCounts=[
                    [[[0, 0], [0, 0]]],
                    [[[0, 0], [0, 0]]],
                ],
                occupancyTimeSec=[[0, 0]],
            )
        )


def test_accepts_matlab_scalar_unit_and_occupancy(tmp_path: Path) -> None:
    maps = load_rf_maps(
        _write_dataset(
            tmp_path,
            unitsSpikeCounts=[[[[1, 2]]]],
            unitsSpikeCountsSize=[1, 1, 1, 2],
            unitPool=41,
            xPositions=0,
            yPositions=0,
            occupancyTimeSec=0.25,
        )
    )

    assert maps.unit_ids == [41]
    np.testing.assert_array_equal(maps[0].occupancy_time_s, [[0.25]])


def test_missing_occupancy_is_not_reconstructed(tmp_path: Path) -> None:
    path = _write_dataset(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["occupancyTimeSec"]
    payload["stimulusPresentationCounts"] = [[2, 4]]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(KeyError, match="occupancyTimeSec"):
        load_rf_maps(path)


@pytest.mark.parametrize(
    "updates",
    [
        {"occupancyTimeSec": [[0.2]]},
        {"occupancyTimeSec": [[-0.1, 0.4]]},
        {"occupancyTimeSec": [[float("nan"), 0.4]]},
    ],
)
def test_rejects_invalid_occupancy(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="occupancy"):
        load_rf_maps(_write_dataset(tmp_path, **updates))


def test_rejects_fractional_raw_spike_counts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="integer spike counts"):
        load_rf_maps(
            _write_dataset(
                tmp_path,
                unitsSpikeCounts=[
                    [[[1, 2.5], [3, 4]]],
                    [[[5, 6], [7, 8]]],
                ],
            )
        )


@pytest.mark.parametrize(
    "field",
    ["responseUnits", "responseNormalization"],
)
def test_requires_declared_raw_count_semantics(
    tmp_path: Path, field: str,
) -> None:
    path = _write_dataset(tmp_path)
    payload = json.loads(path.read_text())
    del payload[field]
    path.write_text(json.dumps(payload))
    with pytest.raises((ValueError, KeyError), match=field):
        load_rf_maps(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("responseUnits", "spike_rate"),
        ("responseUnits", None),
        ("responseNormalization", "occupancy"),
        ("responseNormalization", None),
        ("spikeCountDefinition", "all_overlapping_trials"),
        ("spikeCountDefinition", None),
        ("occupancyTimeDefinition", "number_of_trials"),
        ("occupancyTimeDefinition", None),
        ("occupancyTimeSecSize", [2, 1]),
        ("occupancyTimeSecSize", None),
    ],
)
def test_rejects_incompatible_explicit_count_and_occupancy_contract(
    tmp_path: Path, field: str, value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        load_rf_maps(_write_dataset(tmp_path, **{field: value}))


def test_integer_unit_ids_preserve_values_above_float_precision(tmp_path: Path) -> None:
    ids = [2**53, 2**53 + 1]
    maps = load_rf_maps(_write_dataset(tmp_path, unitPool=ids))
    assert maps.unit_ids == ids
    assert maps.by_unit_id(ids[1]).unit_index == 1


def test_large_mixed_numeric_counts_and_sums_remain_lossless(tmp_path: Path) -> None:
    maximum = 2**64 - 1
    maps = load_rf_maps(_write_dataset(
        tmp_path,
        unitsSpikeCounts=[[[[maximum, 1.0], [0, 0]]], [[[0, 0], [0, 0]]]],
    ))
    assert int(maps[0].spike_counts[0, 0, 0]) == maximum
    assert maps[0].sum(-0.1, 0.1).spike_counts[0, 0, 0] == 2**64
    assert maps[0].zero_spike_spatial_bin_count(-0.1, 0.1) == 1
