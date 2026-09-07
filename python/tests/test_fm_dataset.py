from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from rfmapping_fm_gui import self_test
from rfmapping_viewer.fm_dataset import (
    BAR_FORMAT_NAME,
    BAR_STIMULUS_GEOMETRY,
    SQUARE_FORMAT_NAME,
    STIMULUS_BAR,
    STIMULUS_SQUARE,
    aggregate_rate_hz,
    load_free_moving_rfmap,
    spatial_mean_timeline_hz,
)


def write_fm_rfmap(
    path: Path,
    stimulus_kind: str = STIMULUS_SQUARE,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
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
        file.attrs["format"] = (
            SQUARE_FORMAT_NAME
            if stimulus_kind == STIMULUS_SQUARE
            else BAR_FORMAT_NAME
        )
        file.attrs["logical_dimension_order"] = "unit,elevation,azimuth,time"
        file.attrs["complete"] = np.uint8(1)
        file.attrs["viewpoint_model"] = "rigid_body_origin"
        file.attrs["camera_frame_times_path"] = "/tmp/camera-frame-times.npy"
        if stimulus_kind == STIMULUS_BAR:
            file.attrs["stimulus_geometry"] = BAR_STIMULUS_GEOMETRY
            file.attrs["bar_width_handling"] = (
                "pooled; each trial uses its recorded Square_Size"
            )
            file.attrs["bar_widths_present_deg"] = np.array([3.0, 6.0, 12.0])
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

    dataset = load_free_moving_rfmap(path, STIMULUS_SQUARE)
    unit = dataset.load_unit(1)

    assert dataset.logical_rate_shape == (2, 2, 3, 4)
    assert dataset.stimulus_kind == STIMULUS_SQUARE
    assert dataset.format_name == SQUARE_FORMAT_NAME
    assert dataset.stimulus_geometry is None
    assert dataset.bar_widths_present_deg == ()
    assert dataset.stored_rate_shape == (4, 3, 2, 2)
    assert dataset.unit_ids.tolist() == [41, 7]
    assert unit.unit_id == 7
    expected_rate = logical_rate[1].copy()
    expected_rate[exposure == 0.0, :] = 0.0
    np.testing.assert_array_equal(unit.rate_hz, expected_rate)
    np.testing.assert_array_equal(dataset.exposure_sec, exposure)
    assert dataset.effective_trial_count[0, 0] == 0.0
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


def test_zero_exposure_is_zero_on_the_complete_sphere(tmp_path: Path) -> None:
    path = tmp_path / "free-moving.rfmap"
    _logical_rate, exposure, edges = write_fm_rfmap(path)
    dataset = load_free_moving_rfmap(path, STIMULUS_SQUARE)
    unit = dataset.load_unit(0)

    assert np.all(unit.rate_hz[exposure == 0.0, :] == 0.0)
    matrix = aggregate_rate_hz(unit.rate_hz, edges, 0, 4, exposure)
    assert matrix.shape == exposure.shape
    assert np.all(np.isfinite(matrix))
    assert np.all(matrix[exposure == 0.0] == 0.0)


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


def test_spatial_timeline_does_not_average_unobserved_zero_bins() -> None:
    rate = np.array([[[0.0, 0.0]], [[6.0, 8.0]]])
    exposure = np.array([[0.0], [2.0]])

    timeline = spatial_mean_timeline_hz(rate, exposure)

    np.testing.assert_allclose(timeline, [6.0, 8.0])


def test_loads_latest_vertical_bar_rfmap_contract(tmp_path: Path) -> None:
    path = tmp_path / "bar.rfmap"
    logical_rate, exposure, _ = write_fm_rfmap(path, STIMULUS_BAR)

    dataset = load_free_moving_rfmap(path, STIMULUS_BAR)
    unit = dataset.load_unit(1)

    assert dataset.stimulus_kind == STIMULUS_BAR
    assert dataset.format_name == BAR_FORMAT_NAME
    assert dataset.stimulus_geometry == BAR_STIMULUS_GEOMETRY
    assert dataset.bar_widths_present_deg == (3.0, 6.0, 12.0)
    assert dataset.provenance["camera_frame_times_path"] == (
        "/tmp/camera-frame-times.npy"
    )
    expected_rate = logical_rate[1].copy()
    expected_rate[exposure == 0.0, :] = 0.0
    np.testing.assert_array_equal(unit.rate_hz, expected_rate)


@pytest.mark.parametrize(
    ("file_kind", "selected_kind"),
    (
        (STIMULUS_SQUARE, STIMULUS_BAR),
        (STIMULUS_BAR, STIMULUS_SQUARE),
    ),
)
def test_rejects_selected_stimulus_that_does_not_match_file(
    tmp_path: Path,
    file_kind: str,
    selected_kind: str,
) -> None:
    path = tmp_path / "mismatch.rfmap"
    write_fm_rfmap(path, file_kind)

    with pytest.raises(
        ValueError,
        match=rf"selected {selected_kind}.*{file_kind} RF map",
    ):
        load_free_moving_rfmap(path, selected_kind)


def test_rejects_bar_file_without_latest_geometry_contract(tmp_path: Path) -> None:
    path = tmp_path / "bar.rfmap"
    write_fm_rfmap(path, STIMULUS_BAR)
    with h5py.File(path, "r+") as file:
        del file.attrs["stimulus_geometry"]

    with pytest.raises(ValueError, match="Bar RF root attribute.*stimulus_geometry"):
        load_free_moving_rfmap(path, STIMULUS_BAR)


def test_rejects_legacy_json_disguised_as_rfmap(tmp_path: Path) -> None:
    path = tmp_path / "legacy.rfmap"
    path.write_text('{"unitsSpikeCounts": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="HDF5"):
        load_free_moving_rfmap(path, STIMULUS_SQUARE)


def test_rejects_wrong_format_and_incomplete_file(tmp_path: Path) -> None:
    path = tmp_path / "wrong.rfmap"
    write_fm_rfmap(path)
    with h5py.File(path, "r+") as file:
        file.attrs["format"] = "legacy_json"
    with pytest.raises(ValueError, match="Unsupported RF format"):
        load_free_moving_rfmap(path, STIMULUS_SQUARE)

    write_fm_rfmap(path)
    with h5py.File(path, "r+") as file:
        file.attrs["complete"] = np.uint8(0)
    with pytest.raises(ValueError, match="not marked complete"):
        load_free_moving_rfmap(path, STIMULUS_SQUARE)


def test_headless_app_self_test_exercises_the_3d_sphere_renderer(tmp_path: Path) -> None:
    path = tmp_path / "free-moving.rfmap"
    write_fm_rfmap(path)

    result = self_test(path, STIMULUS_SQUARE)

    assert result["version"] == "1.10.0-alpha.3"
    assert result["format"] == SQUARE_FORMAT_NAME
    assert result["stimulusKind"] == STIMULUS_SQUARE
    assert result["views"] == ["2D map", "3D sphere"]
    assert result["threeDSpherePixels"] > 3000
