import csv
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d
import pandas as pd
from matplotlib import pyplot as plt


LIGHT_PLOT_STYLE = {
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "text.color": "black",
    "axes.labelcolor": "black",
    "axes.edgecolor": "black",
    "axes.titlecolor": "black",
    "xtick.color": "black",
    "ytick.color": "black",
    "grid.color": "#d1d5db",
    "legend.facecolor": "white",
    "legend.edgecolor": "black",
    "legend.framealpha": 1.0,
}


def apply_light_plot_style() -> None:
    plt.rcParams.update(LIGHT_PLOT_STYLE)


apply_light_plot_style()


def get_tuning_curve_for_cluster(
        tuning_curves,
        cluster_id: int,
) -> pd.Series:
    """
    Return a single tuning curve as a pandas Series indexed by angle in degrees.

    Supports both:
    - xarray.DataArray with dims ('unit', angle_dim) or (angle_dim, 'unit')
    - pandas DataFrame with angle index and unit columns
    """
    cluster_id = int(cluster_id)

    if hasattr(tuning_curves, "sel") and hasattr(tuning_curves, "dims") and hasattr(tuning_curves, "coords"):
        if "unit" not in tuning_curves.dims:
            raise ValueError("xarray tuning_curves must contain a 'unit' dimension.")

        angle_dims = [dim for dim in tuning_curves.dims if dim != "unit"]
        if len(angle_dims) != 1:
            raise ValueError(
                "xarray tuning_curves must have exactly one non-'unit' dimension."
            )

        angle_dim = angle_dims[0]
        curve = tuning_curves.sel(unit=cluster_id)
        angles_deg = np.asarray(curve.coords[angle_dim].values, dtype=float)
        rates = np.asarray(curve.values, dtype=float)
        return pd.Series(rates, index=angles_deg, name=cluster_id, dtype=float)

    if isinstance(tuning_curves, pd.Series):
        curve = tuning_curves.copy()
        curve.name = cluster_id if curve.name is None else curve.name
        return curve.astype(float)

    if isinstance(tuning_curves, pd.DataFrame):
        candidate_keys = [cluster_id, str(cluster_id)]
        for key in candidate_keys:
            if key in tuning_curves.columns:
                curve = tuning_curves[key]
                return pd.Series(
                    np.asarray(curve.values, dtype=float),
                    index=np.asarray(tuning_curves.index.values, dtype=float),
                    name=cluster_id,
                    dtype=float,
                )
        raise KeyError(f"cluster_id {cluster_id} not found in tuning_curves columns.")

    raise TypeError(
        "tuning_curves must be an xarray DataArray, pandas DataFrame, or pandas Series."
    )


def plot_tuning_curve_for_cluster(
        tuning_curves,
        cluster_id: int,
        *,
        is_smooth: bool = False,
        is_return_line_plot: bool = True,
        is_return_polar_plot: bool = False,
        clockwise: bool = True,
        line_figsize: tuple[float, float] = (7.0, 4.0),
        polar_figsize: tuple[float, float] = (6.0, 6.0),
        color: str = "C0",
        fill_polar: bool = True,
        ring_levels: Sequence[int | float] = (1,),
        line_label: str | None = None,
        title_prefix: str = "HD tuning curve",
        is_save: bool = False,
        smooth_sigma: float = 1.5,
):
    """
    Plot one cluster's tuning curve as a line plot, polar plot, or both.

    Returns a dict with the extracted curve and any created figure/axes objects.
    """
    if not is_return_line_plot and not is_return_polar_plot:
        raise ValueError(
            "At least one of is_return_line_plot or is_return_polar_plot must be True."
        )

    curve = get_tuning_curve_for_cluster(tuning_curves, cluster_id).dropna()

    curve = pd.Series(
        gaussian_filter1d(curve.values, sigma=smooth_sigma, mode="wrap"),
        index=curve.index,
        name=curve.name,
    ) if is_smooth else curve

    angles_deg = curve.index.to_numpy(dtype=float)
    rates = curve.to_numpy(dtype=float)

    result = {
        "curve": curve,
        "angles_deg": angles_deg,
        "rates": rates,
        "line_fig": None,
        "line_ax": None,
        "polar_fig": None,
        "polar_ax": None,
    }

    if is_return_line_plot:
        line_fig, line_ax = plt.subplots(figsize=line_figsize)
        line_ax.plot(angles_deg, rates, color=color, label=line_label or f"cluster {cluster_id}")
        line_ax.set_xlim(0.0, 360.0)
        line_ax.set_xlabel("Head direction (deg)")
        line_ax.set_ylabel("Firing rate (Hz)")
        line_ax.set_title(f"{title_prefix} - cluster {cluster_id}")
        if line_label is not None:
            line_ax.legend()
        line_fig.tight_layout()
        result["line_fig"] = line_fig
        result["line_ax"] = line_ax

    if is_return_polar_plot:
        polar_fig = plt.figure(figsize=polar_figsize)
        polar_ax = polar_fig.add_subplot(111, polar=True)

        angles_rad = np.deg2rad(angles_deg)
        if angles_rad.size > 0:
            plot_angles_rad = np.concatenate([angles_rad, angles_rad[:1]])
            plot_rates = np.concatenate([rates, rates[:1]])
        else:
            plot_angles_rad = angles_rad
            plot_rates = rates

        polar_ax.plot(plot_angles_rad, plot_rates, color=color, linewidth=2)
        if fill_polar:
            polar_ax.fill(plot_angles_rad, plot_rates, alpha=0.3, color=color)
        polar_ax.set_rticks([])

        mu = float(np.nanmean(rates)) if rates.size else np.nan
        sd = float(np.nanstd(rates, ddof=0)) if rates.size else np.nan
        theta_full = np.linspace(0.0, 2.0 * np.pi, 361)
        label_theta = np.deg2rad(6.0)

        radii: list[float] = []
        if np.isfinite(mu):
            radii.append(mu)
            for level in ring_levels:
                level = float(level)
                if level <= 0 or not np.isfinite(sd):
                    continue
                radii.append(mu + level * sd)
                lower = mu - level * sd
                if lower > 0:
                    radii.append(lower)

        seen_radii: list[float] = []
        for radius in radii:
            if not np.isfinite(radius):
                continue
            if any(np.isclose(radius, seen) for seen in seen_radii):
                continue
            seen_radii.append(radius)
            polar_ax.plot(
                theta_full,
                np.full_like(theta_full, radius),
                linestyle="--",
                linewidth=1,
                color="0.5",
            )
            polar_ax.text(
                label_theta,
                radius,
                f"{radius:.2f}",
                ha="left",
                va="center",
                fontsize=9,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.5),
            )

        polar_ax.set_theta_zero_location("N")

        polar_ax.set_theta_direction(-1) if clockwise else polar_ax.set_theta_direction(1)

        polar_ax.set_title(f"{title_prefix} - cluster {cluster_id}", va="bottom")

        if is_save:
            polar_fig.savefig(f"{title_prefix} - cluster {cluster_id}.png")

        result["polar_fig"] = polar_fig
        result["polar_ax"] = polar_ax



    return result


def safe_std(values: np.ndarray) -> float:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size <= 1:
        return 0.0
    return float(np.nanstd(array, ddof=0))


def safe_mean(values: np.ndarray) -> float:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return np.nan
    return float(np.nanmean(array))


def _get_phase_colors(labels: list[str], color_list: list[str] | None) -> list[str]:
    if color_list is not None and len(color_list) != len(labels):
        raise ValueError("color_list must match the number of labels.")

    default_cycle = plt.rcParams.get("axes.prop_cycle", None)
    default_colors = (
        default_cycle.by_key().get("color", [])
        if default_cycle is not None else []
    )
    if not default_colors:
        default_colors = [f"C{i}" for i in range(max(len(labels), 1))]

    if color_list is None:
        if len(default_colors) < len(labels):
            default_colors.extend(
                f"C{i}" for i in range(len(default_colors), len(labels))
            )
        return default_colors[:len(labels)]

    return list(color_list)


def _validate_index_interval_groups(
        labels: list[str],
        data_list: list[list[tuple[int, int]]],
) -> list[list[tuple[int, int]]]:
    if len(labels) != len(data_list):
        raise ValueError("labels and data_list must have the same length.")

    validated_groups: list[list[tuple[int, int]]] = []
    for phase_label, intervals in zip(labels, data_list):
        validated_intervals: list[tuple[int, int]] = []
        for trial_index, (start, end) in enumerate(intervals, start=1):
            start_index = int(start)
            end_index = int(end)
            if start_index > end_index:
                raise ValueError(
                    f"Invalid interval for {phase_label} trial {trial_index}: "
                    f"start ({start_index}) is greater than end ({end_index})."
                )
            validated_intervals.append((start_index, end_index))
        validated_groups.append(validated_intervals)

    return validated_groups


def _validate_numeric_interval_groups(
        labels: list[str],
        data_list: list[list[tuple[int | float, int | float]]],
) -> list[list[tuple[float, float]]]:
    if len(labels) != len(data_list):
        raise ValueError("labels and data_list must have the same length.")

    validated_groups: list[list[tuple[float, float]]] = []
    for phase_label, intervals in zip(labels, data_list):
        validated_intervals: list[tuple[float, float]] = []
        for trial_index, interval in enumerate(intervals, start=1):
            if len(interval) != 2:
                raise ValueError(
                    f"Invalid interval for {phase_label} trial {trial_index}: "
                    f"expected a pair, got {interval!r}."
                )
            start_value = float(interval[0])
            end_value = float(interval[1])
            if not (np.isfinite(start_value) and np.isfinite(end_value)):
                raise ValueError(
                    f"Invalid interval for {phase_label} trial {trial_index}: "
                    f"({start_value}, {end_value}) contains a non-finite value."
                )
            if start_value > end_value:
                raise ValueError(
                    f"Invalid interval for {phase_label} trial {trial_index}: "
                    f"start ({start_value}) is greater than end ({end_value})."
                )
            validated_intervals.append((start_value, end_value))
        validated_groups.append(validated_intervals)

    return validated_groups


def _plot_phase_bar_from_values(
        labels: list[str],
        phase_trial_values: list[np.ndarray],
        *,
        is_show_up_error_bar: bool = True,
        is_show_down_error_bar: bool = True,
        is_show_per_trial_data: bool = True,
        color_list: list[str] | None = None,
        legend: bool = False,
        title: str | None = None,
        ylabel: str | None = None,
        is_show_grid: bool = True,
        is_save: str | None = None,
) -> list[np.ndarray]:
    apply_light_plot_style()

    if len(labels) != len(phase_trial_values):
        raise ValueError("labels and phase_trial_values must have the same length.")
    phase_colors = _get_phase_colors(labels, color_list)
    phase_arrays = [np.asarray(values, dtype=float) for values in phase_trial_values]

    means = [safe_mean(values) for values in phase_arrays]
    stds = np.asarray([safe_std(values) for values in phase_arrays], dtype=float)
    x_positions = np.arange(len(labels)) * 0.5

    fig_width = max(5.0, len(labels) * 1.25 + 1.5)
    _, axis = plt.subplots(figsize=(fig_width, 5.0))

    lower = stds if is_show_down_error_bar else np.zeros_like(stds)
    upper = stds if is_show_up_error_bar else np.zeros_like(stds)
    yerr = np.vstack([lower, upper])

    axis.bar(
        x_positions,
        means,
        width=0.4,
        yerr=yerr,
        capsize=0,
        color=phase_colors,
        edgecolor="black",
        linewidth=0.8,
        alpha=0.35,
        zorder=1,
    )

    cap_halfwidth = 0.04
    finite_means = np.asarray(means, dtype=float)
    valid_mask = np.isfinite(finite_means)
    if is_show_up_error_bar and np.any(valid_mask):
        axis.hlines(
            finite_means[valid_mask] + upper[valid_mask],
            x_positions[valid_mask] - cap_halfwidth,
            x_positions[valid_mask] + cap_halfwidth,
            color="black",
            linewidth=0.8,
            zorder=4,
        )
    if is_show_down_error_bar and np.any(valid_mask):
        axis.hlines(
            finite_means[valid_mask] - lower[valid_mask],
            x_positions[valid_mask] - cap_halfwidth,
            x_positions[valid_mask] + cap_halfwidth,
            color="black",
            linewidth=0.8,
            zorder=4,
        )

    if is_show_per_trial_data:
        for center, phase_values, color in zip(x_positions, phase_arrays, phase_colors):
            if phase_values.size == 0:
                continue
            jitter_width = 0.08
            jitter = (
                np.linspace(-jitter_width, jitter_width, phase_values.size)
                if phase_values.size > 1 else np.array([0.0])
            )
            axis.scatter(
                np.full(phase_values.size, center) + jitter,
                phase_values,
                color=color,
                edgecolor="black",
                linewidth=0.7,
                s=42,
                alpha=1.0,
                zorder=3,
            )

    axis.set_xticks(x_positions)
    axis.set_xticklabels(labels, rotation=20, ha="right")
    if title:
        axis.set_title(title)
    if ylabel:
        axis.set_ylabel(ylabel)
    if is_show_grid:
        axis.grid(alpha=0.25, axis="y")
    else:
        axis.grid(False)
    axis.margins(x=0.08)

    if legend:
        legend_handles = [
            plt.Line2D([0], [0], color=color, marker="s", linestyle="", markersize=9)
            for color in phase_colors
        ]
        axis.legend(legend_handles, labels, loc="upper left")

    plt.tight_layout()
    if is_save is not None:
        plt.savefig(is_save)
    plt.show()

    return phase_arrays


def _iter_event_rows(
        event_source: str | Path | Sequence[Mapping[str, object]] | object,
):
    if isinstance(event_source, (str, Path)):
        event_path = Path(event_source)
        with event_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError(f"Event CSV is missing a header row: {event_path}")
            yield from reader
        return

    if hasattr(event_source, "to_dict"):
        try:
            records = event_source.to_dict("records")
        except TypeError:
            records = event_source.to_dict(orient="records")
        for row in records:
            if not isinstance(row, Mapping):
                raise TypeError("event_source.to_dict('records') must return mapping rows.")
            yield dict(row)
        return

    if isinstance(event_source, Sequence) and not isinstance(event_source, (str, bytes, bytearray)):
        for row in event_source:
            if not isinstance(row, Mapping):
                raise TypeError("event_source sequences must contain mapping rows.")
            yield dict(row)
        return

    raise TypeError(
        "event_source must be a CSV path, a DataFrame-like object, or a sequence of mapping rows."
    )


def _is_truthy(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if value is None:
        return False
    if isinstance(value, (int, float, np.integer, np.floating)):
        if isinstance(value, float) and np.isnan(value):
            return False
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def summarize_head_turns_from_intervals(
        labels: list[str],
        data_list: list[list[tuple[int | float, int | float]]],
        event_source: str | Path | Sequence[Mapping[str, object]] | object,
        *,
        target_data: list | np.ndarray | None = None,
        event_start_column: str = "start_frame_actual",
        event_end_column: str = "end_frame",
        skip_column: str | None = "is_skipped",
        count_mode: str = "start",
        value_mode: str = "count",
        fps: float | None = None,
) -> dict[str, object]:
    """
    Count head-turn events inside phase/trial intervals and return plot-ready values.

    ``labels`` and ``data_list`` follow the same grouped-by-phase layout as
    ``plot_phase_bar_from_intervals``. Each event is taken from ``event_source``,
    which may be a CSV path or a DataFrame-like object.

    ``count_mode="start"`` counts events whose start timestamp falls inside each
    inclusive interval. ``count_mode="overlap"`` counts events that overlap the
    interval at all and therefore uses both ``event_start_column`` and
    ``event_end_column``.

    ``value_mode="count"`` returns raw counts per trial. ``value_mode="rate"``
    and ``value_mode="count_per_s"`` divide those counts by the interval duration
    in seconds and therefore require ``fps``.

    When ``target_data`` is provided, the plotted/statistical trial value is the
    mean of ``target_data`` restricted to head-turn event samples inside each
    trial interval. Raw event counts are still returned in ``phase_trial_counts``.
    """
    validated_groups = _validate_numeric_interval_groups(labels, data_list)

    normalized_count_mode = str(count_mode).strip().lower()
    if normalized_count_mode not in {"start", "overlap"}:
        raise ValueError("count_mode must be either 'start' or 'overlap'.")

    normalized_value_mode = str(value_mode).strip().lower()
    if normalized_value_mode not in {"count", "rate", "count_per_s"}:
        raise ValueError("value_mode must be 'count', 'rate', or 'count_per_s'.")
    if normalized_value_mode != "count":
        if fps is None:
            raise ValueError("fps is required when value_mode is 'rate' or 'count_per_s'.")
        if float(fps) <= 0:
            raise ValueError("fps must be positive when provided.")

    target_array: np.ndarray | None = None
    if target_data is not None:
        target_array = np.asarray(target_data, dtype=float)
        if target_array.ndim != 1:
            raise ValueError("target_data must be a 1D list or numpy array when provided.")

    event_starts: list[float] = []
    event_ends: list[float] = []
    event_intervals: list[tuple[float, float]] = []
    kept_event_count = 0
    for row in _iter_event_rows(event_source):
        if skip_column is not None and _is_truthy(row.get(skip_column)):
            continue
        if event_start_column not in row:
            raise KeyError(f"Missing event start column: {event_start_column}")

        start_value = float(row[event_start_column])
        if not np.isfinite(start_value):
            continue
        raw_end_value = row.get(event_end_column, start_value)
        end_value = float(raw_end_value)
        if not np.isfinite(end_value):
            end_value = start_value
        end_value = max(start_value, end_value)

        event_starts.append(start_value)
        event_ends.append(end_value)
        event_intervals.append((start_value, end_value))
        kept_event_count += 1

    event_start_array = np.sort(np.asarray(event_starts, dtype=float))
    event_end_array = np.sort(np.asarray(event_ends, dtype=float))
    event_intervals.sort(key=lambda pair: pair[0])

    phase_trial_counts: list[np.ndarray] = []
    phase_trial_values: list[np.ndarray] = []
    phase_trial_durations_s: list[np.ndarray] = []
    phase_trial_event_sample_counts: list[np.ndarray] = []
    phase_stats: list[dict[str, object]] = []

    for phase_label, intervals in zip(labels, validated_groups):
        count_values: list[int] = []
        phase_values: list[float] = []
        duration_values_s: list[float] = []
        event_sample_counts: list[int] = []

        for start_value, end_value in intervals:
            if normalized_count_mode == "start":
                left = np.searchsorted(event_start_array, start_value, side="left")
                right = np.searchsorted(event_start_array, end_value, side="right")
                event_count = int(right - left)
            else:
                started_by_end = np.searchsorted(event_start_array, end_value, side="right")
                ended_before_start = np.searchsorted(event_end_array, start_value, side="left")
                event_count = int(started_by_end - ended_before_start)

            count_values.append(event_count)

            if target_array is None:
                event_sample_counts.append(0)
                if normalized_value_mode == "count":
                    phase_values.append(float(event_count))
                    duration_values_s.append(np.nan)
                    continue

                duration_frames = end_value - start_value + 1.0
                duration_s = duration_frames / float(fps)
                duration_values_s.append(duration_s)
                phase_values.append(float(event_count / duration_s) if duration_s > 0 else np.nan)
                continue

            trial_start_index = int(np.ceil(start_value))
            trial_end_index = int(np.floor(end_value))
            if trial_start_index < 0 or trial_end_index >= target_array.size:
                raise IndexError(
                    f"Interval for {phase_label} is out of bounds for target_data: "
                    f"({trial_start_index}, {trial_end_index}) with target_data length {target_array.size}."
                )

            segment_bounds: list[tuple[int, int]] = []
            for event_start_value, event_end_value in event_intervals:
                if normalized_count_mode == "start":
                    if not (start_value <= event_start_value <= end_value):
                        continue
                else:
                    if event_end_value < start_value or event_start_value > end_value:
                        continue

                segment_start = max(trial_start_index, int(np.ceil(event_start_value)))
                segment_end = min(trial_end_index, int(np.floor(event_end_value)))
                if segment_start > segment_end:
                    continue

                segment_bounds.append((segment_start, segment_end))

            duration_values_s.append(np.nan)
            if not segment_bounds:
                event_sample_counts.append(0)
                phase_values.append(np.nan)
                continue

            merged_bounds: list[list[int]] = []
            for segment_start, segment_end in segment_bounds:
                if not merged_bounds or segment_start > merged_bounds[-1][1] + 1:
                    merged_bounds.append([segment_start, segment_end])
                else:
                    merged_bounds[-1][1] = max(merged_bounds[-1][1], segment_end)

            event_sample_counts.append(
                int(sum(segment_end - segment_start + 1 for segment_start, segment_end in merged_bounds))
            )
            event_values = np.concatenate(
                [target_array[segment_start:segment_end + 1] for segment_start, segment_end in merged_bounds]
            )
            finite_values = event_values[np.isfinite(event_values)]
            phase_values.append(float(np.nanmean(finite_values)) if finite_values.size else np.nan)

        count_array = np.asarray(count_values, dtype=int)
        value_array = np.asarray(phase_values, dtype=float)
        duration_array = np.asarray(duration_values_s, dtype=float)
        event_sample_count_array = np.asarray(event_sample_counts, dtype=int)

        phase_trial_counts.append(count_array)
        phase_trial_values.append(value_array)
        phase_trial_durations_s.append(duration_array)
        phase_trial_event_sample_counts.append(event_sample_count_array)
        phase_stats.append(
            {
                "label": phase_label,
                "n_trials": int(value_array.size),
                "mean": safe_mean(value_array),
                "std": safe_std(value_array),
                "total_count": int(count_array.sum()),
                "total_event_samples": int(event_sample_count_array.sum()),
            }
        )

    return {
        "labels": list(labels),
        "data_list": validated_groups,
        "phase_trial_counts": phase_trial_counts,
        "phase_trial_values": phase_trial_values,
        "phase_trial_durations_s": phase_trial_durations_s,
        "phase_trial_event_sample_counts": phase_trial_event_sample_counts,
        "phase_means": np.asarray([row["mean"] for row in phase_stats], dtype=float),
        "phase_stds": np.asarray([row["std"] for row in phase_stats], dtype=float),
        "phase_stats": phase_stats,
        "analysis_mode": "head_turn_target_mean" if target_array is not None else "head_turn_frequency",
        "count_mode": normalized_count_mode,
        "value_mode": normalized_value_mode,
        "event_start_column": event_start_column,
        "event_end_column": event_end_column,
        "kept_event_count": kept_event_count,
        "has_target_data": target_array is not None,
    }


def plot_phase_bar_from_intervals(
        labels: list[str],
        data_list: list[list[list[int]]] | list[list[tuple[int, int]]],
        target_data: list | np.ndarray,
        *,
        is_show_up_error_bar: bool = True,
        is_show_down_error_bar: bool = True,
        is_show_per_trial_data: bool = True,
        color_list: list[str] | None = None,
        legend: bool = False,
        title: str | None = None,
        is_show_grid: bool = True,
        is_save: str | None = None,
):
    """
    Plot phase means as bars with per-trial dots using inclusive intervals.

    Each ``(start, end)`` pair is treated as an inclusive slice into ``target_data``.
    For example, ``(1, 3)`` maps to ``target_data[1:4]``. Each trial dot is the
    mean of that interval, and each phase bar is the mean of its trial values.
    """
    validated_groups = _validate_index_interval_groups(labels, data_list)

    target_array = np.asarray(target_data, dtype=float)
    if target_array.ndim != 1:
        raise ValueError("target_data must be a 1D list or numpy array.")

    phase_trial_values: list[np.ndarray] = []
    for phase_label, intervals in zip(labels, validated_groups):
        trial_values = []
        for trial_index, (start_index, end_index) in enumerate(intervals, start=1):
            if start_index < 0 or end_index >= target_array.size:
                raise IndexError(
                    f"Interval for {phase_label} trial {trial_index} is out of bounds: "
                    f"({start_index}, {end_index}) for target_data of length {target_array.size}."
                )

            interval_values = target_array[start_index:end_index + 1]
            finite_values = interval_values[np.isfinite(interval_values)]
            if finite_values.size == 0:
                continue
            trial_values.append(float(np.nanmean(finite_values)))

        phase_trial_values.append(np.asarray(trial_values, dtype=float))

    return _plot_phase_bar_from_values(
        labels,
        phase_trial_values,
        is_show_up_error_bar=is_show_up_error_bar,
        is_show_down_error_bar=is_show_down_error_bar,
        is_show_per_trial_data=is_show_per_trial_data,
        color_list=color_list,
        legend=legend,
        title=title,
        is_show_grid=is_show_grid,
        is_save=is_save,
    )


def plot_head_turn_bar_from_intervals(
        labels: list[str],
        data_list: list[list[list[int | float]]],
        event_source: str | Path | Sequence[Mapping[str, object]] | object,
        *,
        target_data: list | np.ndarray | None = None,
        event_start_column: str = "start_frame_actual",
        event_end_column: str = "end_frame",
        skip_column: str | None = "is_skipped",
        count_mode: str = "start",
        value_mode: str = "count",
        fps: float | None = None,
        is_show_up_error_bar: bool = True,
        is_show_down_error_bar: bool = True,
        is_show_per_trial_data: bool = True,
        color_list: list[str] | None = None,
        legend: bool = False,
        title: str | None = None,
        ylabel: str | None = None,
        is_show_grid: bool = True,
        is_save: str | None = None,
) -> dict[str, object]:
    """
    Plot head-turn counts or rates with the same bar-plus-trial-dot style.

    The function returns the same summary dictionary produced by
    ``summarize_head_turns_from_intervals`` so the per-trial values can be reused
    for downstream statistics.
    """
    summary = summarize_head_turns_from_intervals(
        labels,
        data_list,
        event_source,
        target_data=target_data,
        event_start_column=event_start_column,
        event_end_column=event_end_column,
        skip_column=skip_column,
        count_mode=count_mode,
        value_mode=value_mode,
        fps=fps,
    )

    if ylabel is None:
        if summary["has_target_data"]:
            ylabel = "Mean target value during head turns"
        else:
            ylabel = "Head turn count" if summary["value_mode"] == "count" else "Head turn count/s"

    plotted_values = _plot_phase_bar_from_values(
        labels,
        summary["phase_trial_values"],
        is_show_up_error_bar=is_show_up_error_bar,
        is_show_down_error_bar=is_show_down_error_bar,
        is_show_per_trial_data=is_show_per_trial_data,
        color_list=color_list,
        legend=legend,
        title=title,
        ylabel=ylabel,
        is_show_grid=is_show_grid,
        is_save=is_save,
    )
    summary["phase_trial_values"] = plotted_values
    return summary
