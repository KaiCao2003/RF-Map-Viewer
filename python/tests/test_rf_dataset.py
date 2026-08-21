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
        "stimulusPresentationCounts": [[2, 4]],
        "metadataVersion": 3,
    }
    payload.update(updates)
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
            stimulusPresentationCounts=[[2], [4]],
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


def test_rejects_counts_where_presentations_are_zero(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="zero where spike counts are nonzero"):
        load_rf_maps(
            _write_dataset(tmp_path, stimulusPresentationCounts=[[0, 4]])
        )
