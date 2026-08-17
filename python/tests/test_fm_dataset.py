from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from rfmapping_viewer.fm_dataset import (
    aggregate_rate_hz,
    load_free_moving_rfmap,
    spatial_mean_timeline_hz,
)


def write_fm_rfmap(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    unit_ids = np.array([41, 7], dtype=np.int64)
    elevation = np.array([-30.0, 30.0])
    azimuth = np.array([-120.0, 0.0, 120.0])
    time_edges = np.array([-0.1, 0.0, 0.1, 0.3, 0.4])
    logical_rate = np.arange(2 * 2 * 3 * 4, dtype=np.float32).reshape(2, 2, 3, 4)
    logical_rate[0, 0, 0, 0] = np.nan
    exposure = np.array([[0.0, 0.1, 0.2], [0.3, 0.4, 0.5]])
    effective = np.array([[np.nan, 1.0, 2.0], [3.0, 4.0, 5.0]])
    calibration = {
        "schema_version": "rf-calib-1.0",
        "rigid_body_name": "hp4",
        "world_up_axis": "Z",
        "screen": {"radius_mm": 600.0, "height_mm": 1800.0},
        "head": {"viewpoint_model": "rigid_body_origin"},
    }

    with h5py.File(path, "w") as file:
        file.attrs["format"] = "rfmapping_fm_hdf5_v1"
        file.attrs["logical_dimension_order"] = "unit,elevation,azimuth,time"
        file.attrs["complete"] = np.uint8(1)
        file.attrs["viewpoint_model"] = "rigid_body_origin"
        file.create_dataset("/units/id", data=unit_ids.reshape(1, -1))
        file.create_dataset("/axes/elevation_centers_deg", data=elevation.reshape(1, -1))
        file.create_dataset("/axes/azimuth_centers_deg", data=azimuth.reshape(1, -1))
        file.create_dataset("/axes/time_edges_sec", data=time_edges.reshape(1, -1))
        file.create_dataset("/rf/exposure_sec", data=exposure.T)
        file.create_dataset("/rf/effective_trial_count", data=effective.T)
        file.create_dataset(
            "/rf/rate_hz",
            data=np.transpose(logical_rate, (3, 2, 1, 0)),
        )
        encoded = json.dumps(calibration).encode("utf-8")
        file.create_dataset(
            "/calibration/json_utf8",
            data=np.frombuffer(encoded, dtype=np.uint8).reshape(1, -1),
        )
    return logical_rate, exposure, time_edges


def test_loads_matlab_reversed_hdf5_dimensions_and_one_unit(tmp_path: Path) -> None:
    path = tmp_path / "free-moving.rfmap"
    logical_rate, exposure, _ = write_fm_rfmap(path)

    dataset = load_free_moving_rfmap(path)
    unit = dataset.load_unit(1)

    assert dataset.logical_rate_shape == (2, 2, 3, 4)
    assert dataset.stored_rate_shape == (4, 3, 2, 2)
    assert dataset.unit_ids.tolist() == [41, 7]
    assert unit.unit_id == 7
    np.testing.assert_array_equal(unit.rate_hz, logical_rate[1])
    np.testing.assert_array_equal(dataset.exposure_sec, exposure)
    assert dataset.calibration["rigid_body_name"] == "hp4"
    assert not unit.rate_hz.flags.writeable


def test_time_weighted_rate_and_exposure_mask(tmp_path: Path) -> None:
    path = tmp_path / "free-moving.rfmap"
    logical_rate, exposure, edges = write_fm_rfmap(path)

    matrix = aggregate_rate_hz(
        logical_rate[0], edges, 1, 3, exposure, minimum_exposure_sec=0.25
    )

    expected = (logical_rate[0, ..., 1] * 0.1 + logical_rate[0, ..., 2] * 0.2) / 0.3
    expected[exposure < 0.25] = np.nan
    np.testing.assert_allclose(matrix, expected, equal_nan=True)


def test_spatial_timeline_excludes_low_exposure_and_nan() -> None:
    rate = np.array(
        [
            [[1.0, 2.0], [3.0, 4.0]],
            [[5.0, np.nan], [7.0, 8.0]],
        ]
    )
    exposure = np.array([[0.0, 1.0], [2.0, 3.0]])

    timeline = spatial_mean_timeline_hz(rate, exposure, minimum_exposure_sec=1.5)

    np.testing.assert_allclose(timeline, [6.0, 8.0])


def test_rejects_legacy_json_disguised_as_rfmap(tmp_path: Path) -> None:
    path = tmp_path / "legacy.rfmap"
    path.write_text('{"unitsSpikeCounts": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="HDF5"):
        load_free_moving_rfmap(path)


def test_rejects_wrong_format_and_incomplete_file(tmp_path: Path) -> None:
    path = tmp_path / "wrong.rfmap"
    write_fm_rfmap(path)
    with h5py.File(path, "r+") as file:
        file.attrs["format"] = "legacy_json"
    with pytest.raises(ValueError, match="Unsupported RF format"):
        load_free_moving_rfmap(path)

    write_fm_rfmap(path)
    with h5py.File(path, "r+") as file:
        file.attrs["complete"] = np.uint8(0)
    with pytest.raises(ValueError, match="not marked complete"):
        load_free_moving_rfmap(path)
