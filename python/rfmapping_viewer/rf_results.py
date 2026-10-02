"""Read saved RF detections without importing or rerunning the analysis code."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

import numpy as np


@dataclass(frozen=True)
class RFResultSource:
    path: Path
    unit_ids: tuple[int, ...]
    x_positions: tuple[float, ...]
    y_positions: tuple[float, ...]
    time_bin_edges: tuple[float, ...]

    @classmethod
    def from_document(cls, data) -> RFResultSource:
        return cls(
            Path(data.path).resolve(), tuple(data.unit_pool),
            tuple(data.x_positions), tuple(data.y_positions), tuple(data.time_bin_edges),
        )


@dataclass(frozen=True)
class SavedRFResult:
    path: Path
    source: RFResultSource
    dimension: str
    rf_type: str
    unit_ids: tuple[int, ...]
    mask: np.ndarray
    center: np.ndarray
    manifest: dict[str, Any]
    cache_key: str
    summary: dict[str, Any] | None
    summary_warning: str | None
    axis: str | None
    projected_from_2d: bool

    def for_unit(self, unit_id: int) -> tuple[np.ndarray, np.ndarray] | None:
        """Match recorded IDs; an omitted/QC-filtered unit has no saved result."""
        try:
            index = self.unit_ids.index(unit_id)
        except ValueError:
            return None
        mask, center = self.mask[index], self.center[index]
        if self.dimension == "1d":
            # Legacy 1-D results store the 2-D detection. This is its documented
            # projection, not a fresh detection or a projection of raw responses.
            collapse = 0 if self.axis == "x" else 1
            mask, center = mask.any(axis=collapse), center.any(axis=collapse)
            mask.setflags(write=False)
            center.setflags(write=False)
        return mask, center

    def unit_qc(self, unit_id: int) -> dict[str, Any] | None:
        if self.summary is None:
            return None
        qc = self.summary["bin_qc"]
        try:
            index = qc["unit_ids"].index(unit_id)
        except ValueError:
            return None
        return {name: qc[name][index] for name in ("zero_bins", "valid_bins", "keep")}


def rf_result_path(source_path: str | Path, *, dimension="2d", rf_type="excitatory") -> Path:
    """Match the upstream four-sidecar naming contract exactly."""
    if dimension not in {"1d", "2d"} or rf_type not in {"excitatory", "inhibitory"}:
        raise ValueError("Choose 1d/2d and excitatory/inhibitory")
    source = Path(source_path)
    if source.suffix.lower() == ".npz":
        raise ValueError("Select the source RF document, not a result .npz")
    stem = source.stem + ("_inhibitory" if rf_type == "inhibitory" else "")
    return source.with_name(stem + ("_1d" if dimension == "1d" else "") + ".npz")


def _text(array: np.ndarray, label: str) -> str:
    if array.shape != () or array.dtype.kind != "U" or not array.item():
        raise ValueError(f"{label} must be a nonempty Unicode scalar")
    return array.item()


def _ids(value, label: str) -> tuple[int, ...]:
    array = np.asarray(value)
    if array.ndim != 1 or array.dtype.kind not in "iu" or not array.size:
        raise ValueError(f"{label} must contain integer unit IDs")
    result = tuple(int(item) for item in array)
    if len(set(result)) != len(result):
        raise ValueError(f"{label} contains duplicate unit IDs")
    return result


def _summary(source: RFResultSource, path: Path, ids: tuple[int, ...], manifest, dimension, rf_type):
    stem = source.path.stem + ("_inhibitory" if rf_type == "inhibitory" else "")
    summary_path = source.path.with_name(stem + "_analysis.json")
    if not summary_path.exists():
        return None
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    # A recording and its sidecars may be copied from /mnt to /Volumes. The
    # saved path is provenance, not an instruction to read the original root.
    if not isinstance(summary, dict) or Path(summary.get("source_path", "")).name != source.path.name:
        raise ValueError("Analysis summary source_path filename does not match this RF document")
    parameters = summary.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError("Analysis summary parameters must be an object")
    if parameters.get("rf_type") != rf_type:
        raise ValueError("Analysis summary polarity does not match the selected result")
    requested_time = parameters.get("time_range_s")
    if (not isinstance(requested_time, list) or len(requested_time) != 2
            or not np.allclose(requested_time, manifest["time_range_s"], rtol=0, atol=1e-12)):
        raise ValueError("Analysis summary time range differs from the saved result")
    saved_parameters = manifest["parameters"]
    for name in ("drop_bins", "wrap_x", "is_shuffle", "exclude_zero_bins"):
        if parameters.get(name) != saved_parameters.get(name):
            raise ValueError(f"Analysis summary {name} differs from the saved result")
    if parameters.get(f"cluster_forming_z_{dimension}") != saved_parameters.get("cluster_forming_z"):
        raise ValueError("Analysis summary threshold differs from the saved result")
    if set(_ids(summary.get("kept_unit_ids"), "kept_unit_ids")) != set(ids):
        raise ValueError("Analysis summary kept units differ from the saved result")
    outputs = summary.get("output_paths", {})
    if not isinstance(outputs, dict):
        raise ValueError("Analysis summary output_paths must be an object")
    if Path(outputs.get(f"result_{dimension}", "")).name != path.name:
        raise ValueError("Analysis summary output path differs from this saved result")
    qc = summary.get("bin_qc", {})
    if not isinstance(qc, dict):
        raise ValueError("Analysis summary bin_qc must be an object")
    qc_ids = _ids(qc.get("unit_ids"), "QC unit_ids")
    if not set(qc_ids).issubset(source.unit_ids):
        raise ValueError("Analysis summary QC unit IDs are absent from this RF document")
    for name in ("zero_bins", "valid_bins", "keep"):
        values = qc.get(name)
        if not isinstance(values, list) or len(values) != len(qc_ids):
            raise ValueError(f"Analysis summary QC {name} is not aligned with unit IDs")
        expected = bool if name == "keep" else int
        if any(type(value) is not expected or (name != "keep" and value < 0) for value in values):
            raise ValueError(f"Analysis summary QC {name} has invalid values")
    if {uid for uid, keep in zip(qc_ids, qc["keep"]) if keep} != set(ids):
        raise ValueError("Analysis summary QC keep flags differ from the saved units")
    return summary


def load_saved_rf_result(source: RFResultSource, *, dimension="2d", rf_type="excitatory") -> SavedRFResult:
    path = rf_result_path(source.path, dimension=dimension, rf_type=rf_type)
    fields = {"schema_version", "mask_2d", "center_2d", "unit_ids", "manifest_json", "cache_key"}
    try:
        with np.load(path, allow_pickle=False) as archive:
            if set(archive.files) != fields:
                raise ValueError("Saved RF result fields do not match schema v1")
            version = archive["schema_version"]
            if version.shape != () or version.dtype.kind not in "iu" or version.item() != 1:
                raise ValueError("Unsupported saved RF result schema; expected version 1")
            manifest_text = _text(archive["manifest_json"], "manifest_json")
            manifest = json.loads(manifest_text)
            if not isinstance(manifest, dict) or json.dumps(
                manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
            ) != manifest_text:
                raise ValueError("Saved result manifest must be a canonical JSON object")
            cache_key = _text(archive["cache_key"], "cache_key")
            ids = _ids(archive["unit_ids"], "unit_ids")
            arrays = []
            for name in ("mask_2d", "center_2d"):
                array = archive[name]
                if array.ndim == 2:
                    array = array[np.newaxis]
                if array.ndim != 3 or 0 in array.shape or array.dtype.kind not in "biuf" or not np.isin(array, [0, 1]).all():
                    raise ValueError(f"{name} must be a binary (unit, y, x) array")
                arrays.append(np.array(array, dtype=np.uint8))
    except FileNotFoundError:
        raise
    except (OSError, ValueError, KeyError, BadZipFile) as error:
        raise ValueError(f"Cannot read {path.name}: {error}") from error
    mask, center = arrays
    if mask.shape != center.shape or mask.shape[0] != len(ids):
        raise ValueError("Saved masks, centers and unit IDs are not aligned")
    if np.any(center > mask) or not np.array_equal(center.sum(axis=(1, 2)), mask.any(axis=(1, 2))):
        raise ValueError("Each nonempty RF needs exactly one center inside its mask")
    if not set(ids).issubset(source.unit_ids):
        raise ValueError("Saved unit IDs are absent from this RF document")
    if manifest.get("schema_name") != "rfmapping-rf-result":
        raise ValueError("Unknown saved RF result manifest")
    if manifest.get("grid_shape") != list(mask.shape[1:]) or manifest.get("storage_shape") != list(mask.shape):
        raise ValueError("Manifest grid/storage shape differs from the saved arrays")
    time_range = manifest.get("time_range_s")
    if (not isinstance(time_range, list) or len(time_range) != 2
            or any(type(value) not in (int, float) or not np.isfinite(value) for value in time_range)
            or not source.time_bin_edges[0] <= time_range[0] < time_range[1] <= source.time_bin_edges[-1]):
        raise ValueError("Saved time range is outside this RF document")
    if any(not np.isclose(source.time_bin_edges, boundary, rtol=0, atol=1e-12).any() for boundary in time_range):
        raise ValueError("Saved time range does not match this RF document's bin edges")
    parameters = manifest.get("parameters")
    if not isinstance(parameters, dict) or parameters.get("alternative") != ("less" if rf_type == "inhibitory" else "greater"):
        raise ValueError("Saved detection polarity differs from the selected result")
    summary_warning = None
    try:
        summary = _summary(source, path, ids, manifest, dimension, rf_type)
    except (OSError, ValueError, TypeError, KeyError) as error:
        # The NPZ is the saved result. A separately overwritten analysis report
        # must not invalidate that result or lend it another run's QC values.
        summary = None
        summary_warning = f"Analysis summary ignored: {error}"
    axis = manifest.get("collapse_axis")
    projected = dimension == "1d" and axis is None
    native_shape = (len(source.y_positions), len(source.x_positions))
    if dimension == "2d":
        expected_shape = native_shape
        if axis is not None:
            raise ValueError("A 2-D result cannot have a collapsed axis")
    elif projected:
        if summary is None or summary["parameters"].get("collapse_from_2d") is not True:
            raise ValueError("A legacy 1-D projection requires its analysis summary")
        axis, expected_shape = "x", native_shape
    elif axis == "x":
        expected_shape = (1, native_shape[1])
    elif axis == "y":
        expected_shape = (native_shape[0], 1)
    else:
        raise ValueError("Saved 1-D result has an unknown collapsed axis")
    if mask.shape[1:] != expected_shape:
        raise ValueError("Saved result grid does not match this RF document's axes")
    for array in arrays:
        array.setflags(write=False)
    return SavedRFResult(path, source, dimension, rf_type, ids, mask, center,
                         manifest, cache_key, summary, summary_warning, axis, projected)
