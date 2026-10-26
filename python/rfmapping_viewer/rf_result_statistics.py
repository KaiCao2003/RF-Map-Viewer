"""Describe saved thresholds and source-based baseline statistics without detection."""

from __future__ import annotations

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


def result_statistics_text(result: SavedRFResult, data, unit_id: int) -> str:
    """Return Inspector text; unavailable statistics never invalidate saved masks."""
    parameters = result.manifest["parameters"]
    multiplier = parameters.get("cluster_forming_z")
    direction = parameters.get("alternative")
    label = "Saved candidate threshold" if parameters.get("is_shuffle") is True else "Saved threshold"
    if _positive_number(multiplier) and direction in {"greater", "less"}:
        sign = "+" if direction == "greater" else "−"
        lines = [f"{label}: mean {sign} {multiplier:g} × SD"]
    else:
        lines = [f"{label}: unavailable (no valid saved SD multiplier)"]
    decay = parameters.get("surround_decay")
    if _positive_number(decay):
        lines.append(f"Saved surround decay: {decay:g} × SD (one-step seed neighbors)")
    try:
        values, scope = _baseline_values(result, data, unit_id)
        mean, sd = float(values.mean()), float(values.std(ddof=0))
        lines.extend((
            "Current-source mean-rate statistics:",
            f"Computed mean: {mean:.6g} Hz; SD: {sd:.6g} Hz (population, ddof=0)",
            f"Baseline: {scope}; {values.size} bins; missing bins counted as zero.",
            "Time aggregation: mean firing rate over the saved window. "
            "Original detector mean/SD and temporal aggregation were not saved.",
        ))
    except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError, RuntimeError) as error:
        lines.append(f"Computed mean / SD unavailable: {error}.")
    return "\n".join(lines)
