"""Describe saved thresholds and source-based baseline statistics without detection."""

from __future__ import annotations

from dataclasses import dataclass
import json

import numpy as np

from rfmapping_viewer.rf_results import SavedRFResult


def _positive_number(value) -> bool:
    return type(value) in (int, float) and np.isfinite(value) and value > 0


def _matching_coordinates(saved, current) -> bool:
    try:
        return np.array_equal(np.asarray(saved, dtype=float), np.asarray(current, dtype=float))
    except (TypeError, ValueError):
        return False


def _baseline_values(result: SavedRFResult, data, unit_id: int) -> tuple[np.ndarray, str]:
    manifest = result.manifest
    parameters = manifest["parameters"]
    if unit_id not in result.unit_ids:
        raise ValueError("unit is absent from this saved result")
    if parameters.get("is_shuffle") is not False:
        raise ValueError("shuffle or unspecified detection cannot be reconstructed from the RF map")
    if parameters.get("exclude_zero_bins") is not False:
        raise ValueError("zero-bin exclusion is unsupported or unspecified")
    algorithm, reference = manifest.get("detector_algorithm"), parameters.get("reference")
    if (algorithm, reference) not in {
        ("bottom-two-rows-mean-sd-v1", "bottom_row"),
        ("pooled-spatial-z-v3-zero-filled", "global"),
    }:
        raise ValueError("saved baseline algorithm or reference is unsupported")
    if (manifest.get("response_units"), manifest.get("response_normalization")) != (
        "Hz", "presentation_count_time",
    ):
        raise ValueError("saved response units or normalization do not identify presentation-based Hz")
    if data is None:
        raise ValueError("the RF source is unavailable")
    unit = data.rf_map_by_unit_id(unit_id)
    if unit.presentation_counts is None:
        raise ValueError("source has no saved stimulus presentation counts")
    if (not _matching_coordinates(unit.x_positions, result.source.x_positions)
            or not _matching_coordinates(unit.y_positions, result.source.y_positions)):
        raise ValueError("source coordinates changed since these results were loaded")
    first, last = manifest["time_range_s"]
    summed = unit.sum(first, last)
    counts = summed.spike_counts[..., 0].astype(float)
    exposure = unit.presentation_counts * (last - first)
    # The producer zero-fills unavailable native bins before its nonshuffle
    # spatial mean/SD. Occupancy identifies unavailable bins, not the exposure.
    values = np.divide(
        counts, exposure, out=np.zeros_like(counts),
        where=(unit.occupancy_time_s > 0) & (exposure > 0),
    )
    if not np.isfinite(values).all():
        raise ValueError("source response contains nonfinite values")
    projection_key = manifest.get("projection_source_cache_key")
    projected = result.projected_from_2d or (
        isinstance(projection_key, str) and bool(projection_key)
    )
    aggregations = manifest.get("spatial_aggregation")
    if result.dimension == "2d" and isinstance(aggregations, list) and any(
        isinstance(item, dict) for item in aggregations
    ):
        raise ValueError("2D result has a computed spatial aggregation that cannot be matched to the native source")
    native = result.dimension == "2d" or projected
    if not native and values.shape == result.mask.shape[1:]:
        # A native bar/singleton RF already has one spatial axis. It needs no
        # second collapse when provenance only describes stimulus coverage.
        coverage_only = aggregations is None or (
            isinstance(aggregations, list) and len(aggregations) == len(result.unit_ids)
            and isinstance(aggregations[result.unit_ids.index(unit_id)], str)
        )
        native = 1 in values.shape and coverage_only
    if native:
        x_positions, y_positions = unit.x_positions, unit.y_positions
        if projection_key and not result.projected_from_2d:
            if result.axis == "x":
                y_positions = y_positions[:1]
            else:
                x_positions = x_positions[:1]
        if not _matching_coordinates(manifest.get("x_positions"), x_positions):
            raise ValueError("saved X coordinates do not match the source")
        if not _matching_coordinates(manifest.get("y_positions"), y_positions):
            raise ValueError("saved Y coordinates do not match the source")
        scope = "global native RF grid"
    else:
        if not isinstance(aggregations, list) or len(aggregations) != len(result.unit_ids):
            raise ValueError("independent 1D result has no aligned spatial-sum provenance")
        aggregation = aggregations[result.unit_ids.index(unit_id)]
        if (not isinstance(aggregation, dict) or aggregation.get("operation") != "sum"
                or aggregation.get("rf_only") is not False
                or aggregation.get("missing_as_zero") is not True
                or aggregation.get("retained_axis") != result.axis):
            raise ValueError("independent 1D spatial-sum provenance is unsupported")
        if (not _matching_coordinates(aggregation.get("x_positions"), unit.x_positions)
                or not _matching_coordinates(aggregation.get("y_positions"), unit.y_positions)):
            raise ValueError("1D spatial-sum coordinates do not match the source")
        collapsed_axis = 0 if result.axis == "x" else 1
        indices = aggregation.get("selected_indices")
        if (not isinstance(indices, list) or not indices
                or any(type(index) is not int or not 0 <= index < values.shape[collapsed_axis]
                       for index in indices) or len(set(indices)) != len(indices)):
            raise ValueError("1D spatial-sum indices are invalid")
        values = np.take(values, indices, axis=collapsed_axis).sum(axis=collapsed_axis, keepdims=True)
        scope = f"global 1D sum onto {result.axis.upper()}"
    if reference == "bottom_row":
        baseline = manifest.get("baseline")
        if (not native or values.shape[0] < 2 or not isinstance(baseline, dict)
                or baseline.get("row_indices") != [values.shape[0] - 2, values.shape[0] - 1]
                or baseline.get("mean_scope") != "bottom_two_rows"
                or baseline.get("sd_scope") != "bottom_two_rows"
                or baseline.get("sd_ddof") != 0
                or baseline.get("bin_count") != 2 * values.shape[1]
                or not _matching_coordinates(baseline.get("y_positions"), unit.y_positions[-2:])):
            raise ValueError("bottom-row baseline provenance does not match the source")
        values = values[-2:, :]
        scope = "last two stored rows (all columns)"
    if projected:
        scope += "; projected from 2D"
    return values, scope


@dataclass(frozen=True)
class ResultStatistics:
    threshold: str | None
    candidate_threshold: bool
    surround_decay: float | None
    mean_hz: float | None = None
    sd_hz: float | None = None
    scope: str = ""
    bin_count: int | None = None
    error: str | None = None
    origin: str = "computed"
    processing_label: str = "Native RF"
    basis_key: str = ""
    threshold_hz: float | None = None

    @property
    def threshold_text(self) -> str | None:
        if self.threshold_hz is None:
            return self.threshold
        return f"{self.threshold} = {self.threshold_hz:.6g} Hz"


def _statistics_basis(result: SavedRFResult, unit_id: int, origin: str) -> str:
    """Identify the response/baseline recipe, excluding threshold/mask settings."""
    manifest = result.manifest
    baseline = manifest.get("baseline")
    if origin == "saved" and (
        not isinstance(baseline, dict)
        or not isinstance(manifest.get("detector_algorithm"), str)
        or not manifest["detector_algorithm"]
        or not all(isinstance(baseline.get(key), str) and baseline[key]
                   for key in ("mean_scope", "sd_scope"))
        or type(baseline.get("sd_ddof")) is not int or baseline["sd_ddof"] < 0
    ):
        # Recorded numbers remain displayable without enough provenance to
        # establish that two saved baselines represent the same statistic.
        return ""
    if isinstance(baseline, dict):
        baseline = {key: value for key, value in baseline.items()
                    if key not in {"unit_ids", "mean_hz", "sd_hz", "threshold_hz"}}
    aggregation = manifest.get("spatial_aggregation")
    if isinstance(aggregation, list) and len(aggregation) == len(result.unit_ids):
        aggregation = aggregation[result.unit_ids.index(unit_id)]
    parameters = {key: value for key, value in manifest["parameters"].items()
                  if key not in {"alternative", "rf_type", "cluster_forming_z", "surround_decay", "drop_bins",
                                 "fill_single_holes", "min_hole_neighbors"}}
    basis = {
        "origin": origin, "source": str(result.source.path),
        "algorithm": manifest.get("detector_algorithm"), "parameters": parameters,
        "baseline": baseline, "spatial_aggregation": aggregation,
        "projected_from_2d": result.projected_from_2d or bool(manifest.get("projection_source_cache_key")),
        "axis": result.axis,
        **{key: manifest.get(key) for key in (
            "time_range_s", "response_units", "response_normalization", "response_stage",
            "x_positions", "y_positions", "spatial_preprocessing",
        )},
    }
    return json.dumps(basis, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _saved_baseline_statistics(result: SavedRFResult, unit_id: int) -> dict | None:
    baseline = result.manifest.get("baseline")
    if not isinstance(baseline, dict) or not {"mean_hz", "sd_hz"}.intersection(baseline):
        return None
    if unit_id not in result.unit_ids:
        raise ValueError("unit is absent from this saved result")
    if result.manifest.get("response_units") != "Hz":
        raise ValueError("saved baseline statistics do not identify Hz units")
    ids, means, sds = (baseline.get(key) for key in ("unit_ids", "mean_hz", "sd_hz"))
    if (not isinstance(ids, list) or not ids or any(type(value) is not int for value in ids)
            or len(set(ids)) != len(ids) or not isinstance(means, list) or not isinstance(sds, list)
            or len(ids) != len(means) or len(ids) != len(sds)):
        raise ValueError("saved mean/SD arrays are not aligned with unique baseline unit IDs")
    if any(type(value) not in (int, float) or not np.isfinite(value) for value in means + sds):
        raise ValueError("saved mean/SD values must be finite numbers")
    if any(value < 0 for value in sds):
        raise ValueError("saved SD values must be nonnegative")
    if unit_id not in ids:
        raise ValueError("unit has no saved baseline mean/SD")
    index = ids.index(unit_id)
    scope = "last two stored rows (all columns)" if (
        baseline.get("mean_scope") == baseline.get("sd_scope") == "bottom_two_rows"
    ) else "saved baseline"
    processing = "Smoothing 1" if baseline.get("response_stage") == "after_gui_smoothing1" else "Saved baseline"
    count = baseline.get("bin_count")
    return dict(mean_hz=float(means[index]), sd_hz=float(sds[index]), scope=scope,
                bin_count=count if type(count) is int and count > 0 else None,
                processing_label=processing)


def result_statistics(result: SavedRFResult, data, unit_id: int) -> ResultStatistics:
    """Keep saved parameters separate from descriptive current-source statistics."""
    parameters = result.manifest["parameters"]
    multiplier = parameters.get("cluster_forming_z")
    direction = parameters.get("alternative")
    threshold = None
    if _positive_number(multiplier) and direction in {"greater", "less"}:
        sign = "+" if direction == "greater" else "−"
        threshold = f"mean {sign} {multiplier:g} × SD"
    decay = parameters.get("surround_decay")
    saved = dict(threshold=threshold, candidate_threshold=parameters.get("is_shuffle") is True,
                 surround_decay=float(decay) if _positive_number(decay) else None)

    def threshold_hz(mean, sd):
        if threshold is None:
            return None
        return float(mean + (1 if direction == "greater" else -1) * multiplier * sd)

    baseline = result.manifest.get("baseline")
    has_saved_statistics = isinstance(baseline, dict) and bool({"mean_hz", "sd_hz"}.intersection(baseline))
    origin = "saved" if has_saved_statistics else "computed"
    try:
        recorded = _saved_baseline_statistics(result, unit_id)
        if recorded is not None:
            return ResultStatistics(**saved, **recorded, origin="saved",
                                    threshold_hz=threshold_hz(recorded["mean_hz"], recorded["sd_hz"]),
                                    basis_key=_statistics_basis(result, unit_id, "saved"))
        values, scope = _baseline_values(result, data, unit_id)
        mean, sd = float(values.mean()), float(values.std(ddof=0))
        return ResultStatistics(**saved, mean_hz=mean, sd_hz=sd, threshold_hz=threshold_hz(mean, sd),
                                scope=scope, bin_count=values.size,
                                basis_key=_statistics_basis(result, unit_id, "computed"))
    except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError, RuntimeError) as error:
        return ResultStatistics(**saved, error=str(error), origin=origin,
                                processing_label="Saved baseline" if origin == "saved" else "Native RF")


def result_statistics_text(result: SavedRFResult, data, unit_id: int, *,
                           statistics: ResultStatistics | None = None) -> str:
    """Full provenance remains available behind the Inspector's compact summary."""
    statistics = result_statistics(result, data, unit_id) if statistics is None else statistics
    label = "Saved candidate threshold" if statistics.candidate_threshold else "Saved threshold"
    lines = [f"{label}: " + (statistics.threshold_text or "unavailable (no valid saved SD multiplier)")]
    if statistics.surround_decay is not None:
        lines.append(f"Saved surround decay: {statistics.surround_decay:g} × SD (one-step seed neighbors)")
    if statistics.error is None and statistics.origin == "saved":
        lines.extend((
            f"Saved mean: {statistics.mean_hz:.6g} Hz; SD: {statistics.sd_hz:.6g} Hz",
            f"Baseline: {statistics.scope}; {statistics.processing_label}.",
            "Mean/SD read from the saved baseline for this unit; not recomputed from the current RF source.",
        ))
    elif statistics.error is None:
        lines.extend((
            "Current-source mean-rate statistics:",
            f"Computed mean: {statistics.mean_hz:.6g} Hz; SD: {statistics.sd_hz:.6g} Hz (population, ddof=0)",
            f"Baseline: {statistics.scope}; {statistics.bin_count} bins; missing bins counted as zero.",
            "Time aggregation: mean firing rate over the saved window. "
            "Original detector mean/SD and temporal aggregation were not saved.",
        ))
    else:
        label = "Saved" if statistics.origin == "saved" else "Computed"
        lines.append(f"{label} mean / SD unavailable: {statistics.error}.")
    return "\n".join(lines)
