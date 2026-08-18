"""Read-only model for free-moving RFmapping HDF5 ``.rfmap`` files.

The MATLAB writer records its logical dimension order as
``unit,elevation,azimuth,time``.  MATLAB's HDF5 bridge reverses those
dimensions on disk, so h5py observes ``time,azimuth,elevation,unit``.  This
module validates that contract and exposes one unit at a time in the logical
``(elevation, azimuth, time)`` order used by the viewer.
"""

from __future__ import annotations

import json
import math
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import h5py
import numpy as np
from numpy.typing import NDArray

STIMULUS_SQUARE = "Square"
STIMULUS_BAR = "Bar"
STIMULUS_KINDS = (STIMULUS_SQUARE, STIMULUS_BAR)
SQUARE_FORMAT_NAME = "rfmapping_fm_hdf5_v1"
BAR_FORMAT_NAME = "rfmapping_fm_bar_hdf5_v1"
BAR_STIMULUS_GEOMETRY = "vertical_bar_full_source_height"
FORMAT_NAME_BY_STIMULUS_KIND = MappingProxyType(
    {
        STIMULUS_SQUARE: SQUARE_FORMAT_NAME,
        STIMULUS_BAR: BAR_FORMAT_NAME,
    }
)
LOGICAL_DIMENSION_ORDER = "unit,elevation,azimuth,time"


def _readonly(values: Any, *, dtype: Any | None = None) -> NDArray[Any]:
    result = np.asarray(values, dtype=dtype)
    result.setflags(write=False)
    return result


def _text(value: Any, label: str) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, np.ndarray) and value.shape == ():
        value = value.item()
        if isinstance(value, bytes):
            value = value.decode("utf-8")
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a UTF-8 string")
    return value


def _vector(file: h5py.File, path: str, *, dtype: Any) -> NDArray[Any]:
    if path not in file:
        raise ValueError(f"Required RF dataset is missing: {path}")
    values = np.asarray(file[path][...], dtype=dtype).reshape(-1)
    if values.size == 0:
        raise ValueError(f"Required RF dataset is empty: {path}")
    return _readonly(values, dtype=dtype)


def _matrix_in_logical_order(
    file: h5py.File,
    path: str,
    logical_shape: tuple[int, int],
) -> NDArray[np.float64]:
    if path not in file:
        raise ValueError(f"Required RF dataset is missing: {path}")
    raw = np.asarray(file[path][...], dtype=np.float64)
    expected_stored_shape = tuple(reversed(logical_shape))
    if raw.shape != expected_stored_shape:
        raise ValueError(
            f"{path} has stored shape {raw.shape}; expected MATLAB HDF5 shape "
            f"{expected_stored_shape} for logical shape {logical_shape}"
        )
    result = raw.T
    result.setflags(write=False)
    return result


def _finite_increasing(values: NDArray[np.float64], label: str) -> None:
    if not np.all(np.isfinite(values)):
        raise ValueError(f"{label} must contain only finite values")
    if not np.all(np.diff(values) > 0):
        raise ValueError(f"{label} must be strictly increasing")


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int

    @classmethod
    def capture(cls, path: Path) -> "FileIdentity":
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"RF input must be a regular file: {path}")
        return cls(
            device=int(info.st_dev),
            inode=int(info.st_ino),
            size=int(info.st_size),
            mtime_ns=int(info.st_mtime_ns),
        )

    def verify(self, path: Path) -> None:
        current = FileIdentity.capture(path)
        if current != self:
            raise RuntimeError(f"RF input changed after it was opened: {path}")


@dataclass(frozen=True, slots=True)
class FreeMovingUnitMap:
    unit_index: int
    unit_id: int
    rate_hz: NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class FreeMovingRFMap:
    path: Path
    identity: FileIdentity
    stimulus_kind: str
    format_name: str
    stimulus_geometry: str | None
    bar_widths_present_deg: tuple[float, ...]
    unit_ids: NDArray[np.int64]
    elevation_centers_deg: NDArray[np.float64]
    azimuth_centers_deg: NDArray[np.float64]
    time_edges_sec: NDArray[np.float64]
    exposure_sec: NDArray[np.float64]
    effective_trial_count: NDArray[np.float64]
    calibration: Mapping[str, Any]
    provenance: Mapping[str, str]
    stored_rate_shape: tuple[int, int, int, int]

    @property
    def unit_count(self) -> int:
        return int(self.unit_ids.size)

    @property
    def elevation_count(self) -> int:
        return int(self.elevation_centers_deg.size)

    @property
    def azimuth_count(self) -> int:
        return int(self.azimuth_centers_deg.size)

    @property
    def time_bin_count(self) -> int:
        return int(self.time_edges_sec.size - 1)

    @property
    def logical_rate_shape(self) -> tuple[int, int, int, int]:
        return (
            self.unit_count,
            self.elevation_count,
            self.azimuth_count,
            self.time_bin_count,
        )

    def load_unit(self, unit_index: int) -> FreeMovingUnitMap:
        if isinstance(unit_index, bool) or not isinstance(unit_index, int):
            raise TypeError("unit_index must be an integer")
        if unit_index < 0 or unit_index >= self.unit_count:
            raise IndexError(
                f"unit_index {unit_index} is outside 0..{self.unit_count - 1}"
            )
        self.identity.verify(self.path)
        with h5py.File(self.path, "r") as file:
            dataset = file["/rf/rate_hz"]
            raw = np.asarray(dataset[..., unit_index], dtype=np.float32)
        self.identity.verify(self.path)
        expected = (
            self.time_bin_count,
            self.azimuth_count,
            self.elevation_count,
        )
        if raw.shape != expected:
            raise RuntimeError(
                f"Unit slice has stored shape {raw.shape}; expected {expected}"
            )
        rate_hz = np.transpose(raw, (2, 1, 0)).copy()
        if np.any(np.isinf(rate_hz)):
            raise ValueError("/rf/rate_hz contains infinite values")
        finite = np.isfinite(rate_hz)
        if np.any(rate_hz[finite] < 0):
            raise ValueError("/rf/rate_hz contains negative firing rates")
        rate_hz[self.exposure_sec == 0.0, :] = 0.0
        rate_hz.setflags(write=False)
        return FreeMovingUnitMap(
            unit_index=unit_index,
            unit_id=int(self.unit_ids[unit_index]),
            rate_hz=rate_hz,
        )


def _expected_format_name(stimulus_kind: str) -> str:
    try:
        return FORMAT_NAME_BY_STIMULUS_KIND[stimulus_kind]
    except KeyError as exc:
        choices = ", ".join(STIMULUS_KINDS)
        raise ValueError(f"stimulus_kind must be one of: {choices}") from exc


def load_free_moving_rfmap(
    path: str | Path,
    stimulus_kind: str,
) -> FreeMovingRFMap:
    expected_format_name = _expected_format_name(stimulus_kind)
    source = Path(path).expanduser().resolve(strict=True)
    if source.suffix.lower() != ".rfmap":
        raise ValueError("Free-moving RF input must use the .rfmap extension")
    identity = FileIdentity.capture(source)

    try:
        file = h5py.File(source, "r")
    except OSError as exc:
        raise ValueError(
            "Free-moving .rfmap must be an HDF5 file written by RFmapping_core_fm"
        ) from exc

    with file:
        for attribute in ("format", "logical_dimension_order", "complete"):
            if attribute not in file.attrs:
                raise ValueError(f"Required RF root attribute is missing: {attribute}")
        format_name = _text(file.attrs.get("format"), "root format attribute")
        if format_name != expected_format_name:
            actual_kind = next(
                (
                    kind
                    for kind, known_format in FORMAT_NAME_BY_STIMULUS_KIND.items()
                    if known_format == format_name
                ),
                None,
            )
            if actual_kind is not None:
                raise ValueError(
                    f"You selected {stimulus_kind}, but this is a {actual_kind} "
                    f"RF map ({format_name}). Choose {actual_kind} before loading it."
                )
            raise ValueError(
                f"Unsupported RF format {format_name!r}; selected {stimulus_kind} "
                f"requires {expected_format_name!r}"
            )

        stimulus_geometry: str | None = None
        bar_widths_present_deg: tuple[float, ...] = ()
        if stimulus_kind == STIMULUS_BAR:
            for attribute in (
                "stimulus_geometry",
                "bar_width_handling",
                "bar_widths_present_deg",
            ):
                if attribute not in file.attrs:
                    raise ValueError(
                        f"Required Bar RF root attribute is missing: {attribute}"
                    )
            stimulus_geometry = _text(
                file.attrs["stimulus_geometry"],
                "root stimulus_geometry attribute",
            )
            if stimulus_geometry != BAR_STIMULUS_GEOMETRY:
                raise ValueError(
                    f"Unsupported Bar stimulus geometry {stimulus_geometry!r}; "
                    f"expected {BAR_STIMULUS_GEOMETRY!r}"
                )
            bar_width_handling = _text(
                file.attrs["bar_width_handling"],
                "root bar_width_handling attribute",
            )
            if bar_width_handling != "pooled; each trial uses its recorded Square_Size":
                raise ValueError(
                    f"Unsupported Bar width handling {bar_width_handling!r}"
                )
            bar_width_values = np.asarray(
                file.attrs["bar_widths_present_deg"], dtype=np.float64
            ).reshape(-1)
            if (
                bar_width_values.size == 0
                or not np.all(np.isfinite(bar_width_values))
                or np.any(bar_width_values <= 0)
                or np.unique(bar_width_values).size != bar_width_values.size
            ):
                raise ValueError(
                    "root bar_widths_present_deg must contain unique positive values"
                )
            bar_widths_present_deg = tuple(
                float(value) for value in np.sort(bar_width_values)
            )
        dimension_order = _text(
            file.attrs.get("logical_dimension_order"),
            "root logical_dimension_order attribute",
        )
        if dimension_order != LOGICAL_DIMENSION_ORDER:
            raise ValueError(
                f"Unsupported logical dimension order {dimension_order!r}"
            )
        complete = int(np.asarray(file.attrs.get("complete")).reshape(()))
        if complete != 1:
            raise ValueError("RF map is not marked complete")

        unit_ids = _vector(file, "/units/id", dtype=np.int64)
        if np.unique(unit_ids).size != unit_ids.size:
            raise ValueError("/units/id must contain unique unit IDs")
        azimuth = _vector(file, "/axes/azimuth_centers_deg", dtype=np.float64)
        elevation = _vector(file, "/axes/elevation_centers_deg", dtype=np.float64)
        time_edges = _vector(file, "/axes/time_edges_sec", dtype=np.float64)
        _finite_increasing(azimuth, "azimuth centers")
        _finite_increasing(elevation, "elevation centers")
        _finite_increasing(time_edges, "time edges")
        if time_edges.size < 2:
            raise ValueError("time edges must contain at least two values")

        spatial_shape = (int(elevation.size), int(azimuth.size))
        exposure = _matrix_in_logical_order(
            file, "/rf/exposure_sec", spatial_shape
        )
        effective_trials = _matrix_in_logical_order(
            file, "/rf/effective_trial_count", spatial_shape
        )
        if not np.all(np.isfinite(exposure)) or np.any(exposure < 0):
            raise ValueError("/rf/exposure_sec must be finite and non-negative")
        finite_trials = np.isfinite(effective_trials)
        if np.any(effective_trials[finite_trials] < 0):
            raise ValueError(
                "/rf/effective_trial_count finite values must be non-negative"
            )
        effective_trials = np.array(effective_trials, copy=True)
        effective_trials[exposure == 0.0] = 0.0
        effective_trials.setflags(write=False)

        if "/rf/rate_hz" not in file:
            raise ValueError("Required RF dataset is missing: /rf/rate_hz")
        rate_dataset = file["/rf/rate_hz"]
        if rate_dataset.dtype.kind not in "fiu":
            raise ValueError("/rf/rate_hz must be numeric")
        logical_shape = (
            int(unit_ids.size),
            int(elevation.size),
            int(azimuth.size),
            int(time_edges.size - 1),
        )
        expected_stored_shape = tuple(reversed(logical_shape))
        if rate_dataset.shape != expected_stored_shape:
            raise ValueError(
                f"/rf/rate_hz has stored shape {rate_dataset.shape}; expected "
                f"{expected_stored_shape} for logical shape {logical_shape}"
            )

        if "/calibration/json_utf8" not in file:
            raise ValueError("Required RF dataset is missing: /calibration/json_utf8")
        calibration_bytes = np.asarray(
            file["/calibration/json_utf8"][...], dtype=np.uint8
        ).reshape(-1)
        try:
            calibration_raw = json.loads(calibration_bytes.tobytes().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Embedded calibration JSON is invalid") from exc
        if not isinstance(calibration_raw, dict):
            raise ValueError("Embedded calibration JSON must be an object")
        if calibration_raw.get("schema_version") != "rf-calib-1.0":
            raise ValueError("Embedded calibration must use rf-calib-1.0")
        if calibration_raw.get("world_up_axis") != "Z":
            raise ValueError("Embedded calibration must use world_up_axis Z")
        rigid_body_name = calibration_raw.get("rigid_body_name")
        if not isinstance(rigid_body_name, str) or not rigid_body_name:
            raise ValueError("Embedded calibration must name its rigid body")
        screen = calibration_raw.get("screen")
        if not isinstance(screen, dict):
            raise ValueError("Embedded calibration screen must be an object")
        for field in ("radius_mm", "height_mm"):
            value = screen.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) <= 0
            ):
                raise ValueError(f"Embedded calibration screen.{field} must be positive")
        head = calibration_raw.get("head")
        if not isinstance(head, dict) or head.get("viewpoint_model") != "rigid_body_origin":
            raise ValueError(
                "Embedded calibration must use head viewpoint_model rigid_body_origin"
            )

        provenance_names = (
            "calib_path",
            "motive_csv_path",
            "camera_pulse_data_path",
            "camera_pulse_timestamps_path",
            "camera_frame_times_path",
            "trial_boundaries_path",
            "stimulus_mat_path",
            "spike_time_path",
            "spike_cluster_path",
            "cluster_info_path",
            "viewpoint_model",
        )
        provenance = {
            name: _text(file.attrs[name], f"root {name} attribute")
            for name in provenance_names
            if name in file.attrs
        }

    identity.verify(source)
    return FreeMovingRFMap(
        path=source,
        identity=identity,
        stimulus_kind=stimulus_kind,
        format_name=format_name,
        stimulus_geometry=stimulus_geometry,
        bar_widths_present_deg=bar_widths_present_deg,
        unit_ids=unit_ids,
        elevation_centers_deg=elevation,
        azimuth_centers_deg=azimuth,
        time_edges_sec=time_edges,
        exposure_sec=exposure,
        effective_trial_count=effective_trials,
        calibration=MappingProxyType(calibration_raw),
        provenance=MappingProxyType(provenance),
        stored_rate_shape=expected_stored_shape,
    )


def aggregate_rate_hz(
    rate_hz: NDArray[np.floating[Any]],
    time_edges_sec: NDArray[np.floating[Any]],
    start_bin: int,
    stop_bin: int,
    exposure_sec: NDArray[np.floating[Any]],
    minimum_exposure_sec: float = 0.0,
) -> NDArray[np.float64]:
    """Time-weighted mean rate over half-open bins ``[start_bin, stop_bin)``."""

    values = np.asarray(rate_hz, dtype=np.float64)
    edges = np.asarray(time_edges_sec, dtype=np.float64)
    exposure = np.asarray(exposure_sec, dtype=np.float64)
    if values.ndim != 3:
        raise ValueError("rate_hz must have elevation, azimuth, time dimensions")
    if edges.shape != (values.shape[2] + 1,):
        raise ValueError("time_edges_sec length does not match rate_hz")
    if exposure.shape != values.shape[:2]:
        raise ValueError("exposure_sec shape does not match the spatial RF grid")
    if not (0 <= start_bin < stop_bin <= values.shape[2]):
        raise ValueError("time-bin range must be non-empty and inside rate_hz")
    if not math.isfinite(minimum_exposure_sec) or minimum_exposure_sec < 0:
        raise ValueError("minimum_exposure_sec must be finite and non-negative")

    selected = values[..., start_bin:stop_bin]
    weights = np.diff(edges)[start_bin:stop_bin]
    valid = np.isfinite(selected)
    numerator = np.sum(np.where(valid, selected * weights, 0.0), axis=2)
    denominator = np.sum(np.where(valid, weights, 0.0), axis=2)
    result = np.full(values.shape[:2], np.nan, dtype=np.float64)
    np.divide(numerator, denominator, out=result, where=denominator > 0)
    result[exposure == 0.0] = 0.0
    result[exposure < minimum_exposure_sec] = np.nan
    result.setflags(write=False)
    return result


def spatial_mean_timeline_hz(
    rate_hz: NDArray[np.floating[Any]],
    exposure_sec: NDArray[np.floating[Any]],
    minimum_exposure_sec: float = 0.0,
) -> NDArray[np.float64]:
    values = np.asarray(rate_hz, dtype=np.float64)
    exposure = np.asarray(exposure_sec, dtype=np.float64)
    if values.ndim != 3 or exposure.shape != values.shape[:2]:
        raise ValueError("rate_hz and exposure_sec shapes are inconsistent")
    spatial_mask = exposure > 0.0
    if minimum_exposure_sec > 0.0:
        spatial_mask &= exposure >= minimum_exposure_sec
    valid = np.isfinite(values) & spatial_mask[..., None]
    numerator = np.sum(np.where(valid, values, 0.0), axis=(0, 1))
    denominator = np.sum(valid, axis=(0, 1))
    result = np.full(values.shape[2], np.nan, dtype=np.float64)
    np.divide(numerator, denominator, out=result, where=denominator > 0)
    result.setflags(write=False)
    return result


__all__ = [
    "BAR_FORMAT_NAME",
    "BAR_STIMULUS_GEOMETRY",
    "FORMAT_NAME_BY_STIMULUS_KIND",
    "LOGICAL_DIMENSION_ORDER",
    "SQUARE_FORMAT_NAME",
    "STIMULUS_BAR",
    "STIMULUS_KINDS",
    "STIMULUS_SQUARE",
    "FileIdentity",
    "FreeMovingRFMap",
    "FreeMovingUnitMap",
    "aggregate_rate_hz",
    "load_free_moving_rfmap",
    "spatial_mean_timeline_hz",
]
