#!/usr/bin/env python3
"""Create a tiny MATLAB-layout free-moving RF map for frozen-app smoke tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import h5py
import numpy as np


def create_fixture(path: Path, stimulus_kind: str) -> None:
    logical_rate = np.array(
        [[[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], [[7.0, 8.0, 9.0], [10.0, 11.0, 12.0]]]],
        dtype=np.float32,
    )
    calibration = {
        "schema_version": "rf-calib-1.0",
        "rigid_body_name": "smoke",
        "world_up_axis": "Z",
        "screen": {"radius_mm": 600.0, "height_mm": 1800.0},
        "head": {"viewpoint_model": "rigid_body_origin"},
    }
    encoded = json.dumps(calibration, separators=(",", ":")).encode("utf-8")
    with h5py.File(path, "w") as file:
        file.attrs["format"] = (
            "rfmapping_fm_hdf5_v1"
            if stimulus_kind == "square"
            else "rfmapping_fm_bar_hdf5_v1"
        )
        file.attrs["logical_dimension_order"] = "unit,elevation,azimuth,time"
        file.attrs["complete"] = np.uint8(1)
        file.attrs["viewpoint_model"] = "rigid_body_origin"
        if stimulus_kind == "bar":
            file.attrs["stimulus_geometry"] = "vertical_bar_full_source_height"
            file.attrs["bar_width_handling"] = (
                "pooled; each trial uses its recorded Square_Size"
            )
            file.attrs["bar_widths_present_deg"] = np.array([3.0, 6.0, 12.0])
        file.create_dataset("/units/id", data=np.array([[101]], dtype=np.int64))
        file.create_dataset(
            "/axes/elevation_centers_deg", data=np.array([[-45.0, 45.0]])
        )
        file.create_dataset(
            "/axes/azimuth_centers_deg", data=np.array([[-90.0, 90.0]])
        )
        file.create_dataset(
            "/axes/time_edges_sec", data=np.array([[-0.1, 0.0, 0.1, 0.2]])
        )
        file.create_dataset("/rf/exposure_sec", data=np.full((2, 2), 0.2))
        file.create_dataset(
            "/rf/effective_trial_count", data=np.full((2, 2), 2.0)
        )
        file.create_dataset(
            "/rf/rate_hz", data=np.transpose(logical_rate, (3, 2, 1, 0))
        )
        file.create_dataset(
            "/calibration/json_utf8",
            data=np.frombuffer(encoded, dtype=np.uint8).reshape(1, -1),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stimulus", choices=("square", "bar"), required=True)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    create_fixture(args.output, args.stimulus)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
