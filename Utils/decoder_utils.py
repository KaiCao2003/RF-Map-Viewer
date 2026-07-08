from __future__ import annotations

import pickle
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pynapple as nap
import xarray as xr


def make_decoder_id(prefix: str = "hd_bayes") -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"{prefix}_{timestamp}_{suffix}"


def intervalset_to_array(intervals: nap.IntervalSet | np.ndarray | list) -> np.ndarray:
    if isinstance(intervals, nap.IntervalSet):
        values = intervals.values
    else:
        values = intervals

    arr = np.asarray(values, dtype=float)
    arr = np.atleast_2d(arr)

    if arr.size == 0:
        return np.empty((0, 2), dtype=float)
    if arr.shape[1] != 2:
        raise ValueError("Intervals must have shape (n_intervals, 2).")

    valid = arr[:, 1] > arr[:, 0]
    return arr[valid]


def array_to_intervalset(intervals: np.ndarray) -> nap.IntervalSet:
    arr = intervalset_to_array(intervals)
    if len(arr) == 0:
        raise ValueError("Cannot build an empty IntervalSet.")
    return nap.IntervalSet(start=arr[:, 0], end=arr[:, 1])


def clip_interval_set(
    intervals: nap.IntervalSet | np.ndarray | list,
    start: float | None = None,
    end: float | None = None,
) -> nap.IntervalSet:
    arr = intervalset_to_array(intervals)
    if len(arr) == 0:
        raise ValueError("No intervals were provided.")

    clipped_start = arr[:, 0] if start is None else np.maximum(arr[:, 0], float(start))
    clipped_end = arr[:, 1] if end is None else np.minimum(arr[:, 1], float(end))
    clipped = np.column_stack([clipped_start, clipped_end])
    clipped = clipped[clipped[:, 1] > clipped[:, 0]]

    if len(clipped) == 0:
        raise ValueError("Requested range does not overlap the supplied intervals.")

    return array_to_intervalset(clipped)


def intersect_interval_arrays(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    left_arr = intervalset_to_array(left)
    right_arr = intervalset_to_array(right)
    intersections = []

    for left_start, left_end in left_arr:
        for right_start, right_end in right_arr:
            start = max(left_start, right_start)
            end = min(left_end, right_end)
            if end > start:
                intersections.append((start, end))

    if not intersections:
        return np.empty((0, 2), dtype=float)
    return np.asarray(intersections, dtype=float)


def intersect_interval_set(
    left: nap.IntervalSet | np.ndarray | list,
    right: nap.IntervalSet | np.ndarray | list,
) -> nap.IntervalSet:
    intersections = intersect_interval_arrays(
        intervalset_to_array(left),
        intervalset_to_array(right),
    )
    if len(intersections) == 0:
        raise ValueError("Interval sets do not overlap.")
    return array_to_intervalset(intersections)


def interval_total_duration(intervals: nap.IntervalSet | np.ndarray | list) -> float:
    arr = intervalset_to_array(intervals)
    return float(np.sum(arr[:, 1] - arr[:, 0])) if len(arr) else 0.0


def _merge_intervals(intervals: np.ndarray, gap_tolerance: float = 1e-9) -> np.ndarray:
    arr = intervalset_to_array(intervals)
    if len(arr) == 0:
        return arr

    arr = arr[np.argsort(arr[:, 0])]
    merged = [arr[0].copy()]
    for start, end in arr[1:]:
        last = merged[-1]
        if start <= last[1] + gap_tolerance:
            last[1] = max(last[1], end)
        else:
            merged.append(np.array([start, end], dtype=float))
    return np.asarray(merged, dtype=float)


def tile_interval_set(
    intervals: nap.IntervalSet | np.ndarray | list,
    window_size: float,
    min_window_size: float | None = None,
) -> pd.DataFrame:
    if window_size <= 0:
        raise ValueError("window_size must be positive.")
    if min_window_size is None:
        min_window_size = min(window_size, 1e-9)
    if min_window_size <= 0:
        raise ValueError("min_window_size must be positive.")

    rows = []
    for interval_index, (interval_start, interval_end) in enumerate(
        intervalset_to_array(intervals)
    ):
        current = float(interval_start)
        window_index = 0
        while current < interval_end:
            window_end = min(current + window_size, interval_end)
            duration = window_end - current
            if duration >= min_window_size:
                rows.append(
                    {
                        "interval_index": interval_index,
                        "window_index": window_index,
                        "start": current,
                        "end": window_end,
                        "duration": duration,
                    }
                )
            current = window_end
            window_index += 1

    if not rows:
        raise ValueError("No windows could be built from the supplied intervals.")
    return pd.DataFrame(rows)


def random_split_interval_set(
    intervals: nap.IntervalSet | np.ndarray | list,
    train_fraction: float = 0.7,
    window_size: float = 5.0,
    seed: int | None = 0,
    min_window_size: float | None = None,
) -> tuple[nap.IntervalSet, nap.IntervalSet, pd.DataFrame]:
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be between 0 and 1.")

    split_df = tile_interval_set(
        intervals,
        window_size=window_size,
        min_window_size=min_window_size,
    )
    n_windows = len(split_df)
    if n_windows < 2:
        raise ValueError("At least two split windows are required for train/test.")

    rng = np.random.default_rng(seed)
    train_count = int(round(train_fraction * n_windows))
    train_count = int(np.clip(train_count, 1, n_windows - 1))

    train_indices = set(rng.choice(n_windows, size=train_count, replace=False).tolist())
    split_df["split"] = [
        "train" if row_index in train_indices else "test"
        for row_index in range(n_windows)
    ]

    train_arr = _merge_intervals(
        split_df.loc[split_df["split"] == "train", ["start", "end"]].to_numpy()
    )
    test_arr = _merge_intervals(
        split_df.loc[split_df["split"] == "test", ["start", "end"]].to_numpy()
    )

    train_ep = array_to_intervalset(train_arr)
    test_ep = array_to_intervalset(test_arr)
    return train_ep, test_ep, split_df


def interval_table_to_seconds(
    interval_table: pd.DataFrame,
    *,
    frame_rate: float,
    recording_start_time: float = 0.0,
    start_frame_col: str = "start_frame",
    end_frame_col: str = "end_frame",
    label_col: str = "phase_key",
) -> pd.DataFrame:
    if not {"start", "end"}.issubset(interval_table.columns):
        raise KeyError('interval_table must contain adjusted timestamp columns "start" and "end".')

    if label_col not in interval_table.columns:
        fallback_cols = [
            col
            for col in (
                "interval_type_resolved",
                "interval_type",
                "phase_key",
                "phase_name",
                "phase_family",
            )
            if col in interval_table.columns
        ]
        if not fallback_cols:
            raise KeyError(
                f"label_col {label_col!r} was not found and no fallback label column exists."
            )
        label_col = fallback_cols[0]

    interval_df = interval_table.copy()
    interval_df["start"] = interval_df["start"].astype(float)
    interval_df["end"] = interval_df["end"].astype(float)
    interval_df["duration"] = interval_df["end"] - interval_df["start"]
    interval_df["interval_label"] = interval_df[label_col].astype(str)
    interval_df = interval_df.loc[interval_df["duration"] > 0].reset_index(drop=True)
    return interval_df


def restrict_interval_dataframe(
    interval_df: pd.DataFrame,
    epochs: nap.IntervalSet | np.ndarray | list,
    *,
    start_col: str = "start",
    end_col: str = "end",
) -> pd.DataFrame:
    epoch_arr = intervalset_to_array(epochs)
    rows = []

    for _, row in interval_df.iterrows():
        row_arr = np.asarray([[float(row[start_col]), float(row[end_col])]])
        for start, end in intersect_interval_arrays(row_arr, epoch_arr):
            new_row = row.copy()
            new_row[start_col] = start
            new_row[end_col] = end
            new_row["duration"] = end - start
            rows.append(new_row)

    if not rows:
        return interval_df.iloc[0:0].copy()
    return pd.DataFrame(rows).reset_index(drop=True)


def first_labeled_interval(
    interval_df: pd.DataFrame,
    *,
    label: str = "baseline",
    label_col: str = "interval_label",
    duration_s: float | None = None,
    start_col: str = "start",
    end_col: str = "end",
) -> nap.IntervalSet:
    if label_col not in interval_df.columns:
        raise KeyError(f"{label_col!r} is not in interval_df.")

    matching = interval_df.loc[interval_df[label_col].astype(str) == str(label)].copy()
    matching = matching.sort_values(start_col).reset_index(drop=True)
    if matching.empty:
        raise ValueError(f"No interval labeled {label!r} was found.")

    start = float(matching.loc[0, start_col])
    interval_end = float(matching.loc[0, end_col])
    end = interval_end if duration_s is None else min(start + float(duration_s), interval_end)
    if end <= start:
        raise ValueError(f"First {label!r} interval has non-positive duration.")

    return nap.IntervalSet(start=start, end=end)


def circular_difference(values: np.ndarray, reference: np.ndarray, period: float) -> np.ndarray:
    return (values - reference + period / 2.0) % period - period / 2.0


def summarize_decode_error(
    decoded_values: np.ndarray,
    true_values: np.ndarray,
    circular_period: float | None = 360.0,
    thresholds: tuple[float, ...] = (15.0, 30.0, 45.0, 90.0),
) -> tuple[dict[str, float], np.ndarray]:
    decoded_values = np.asarray(decoded_values, dtype=float)
    true_values = np.asarray(true_values, dtype=float)

    valid = np.isfinite(decoded_values) & np.isfinite(true_values)
    if circular_period is None:
        signed_error = decoded_values - true_values
    else:
        signed_error = circular_difference(decoded_values, true_values, circular_period)

    abs_error = np.abs(signed_error)
    valid_error = abs_error[valid]
    valid_signed_error = signed_error[valid]

    metrics: dict[str, float] = {
        "n_time_bins": float(len(decoded_values)),
        "n_valid_time_bins": float(np.count_nonzero(valid)),
        "valid_fraction": float(np.mean(valid)) if len(valid) else np.nan,
        "mean_abs_error": float(np.nanmean(valid_error)) if len(valid_error) else np.nan,
        "median_abs_error": float(np.nanmedian(valid_error)) if len(valid_error) else np.nan,
        "rmse": float(np.sqrt(np.nanmean(valid_signed_error**2))) if len(valid_error) else np.nan,
    }

    for threshold in thresholds:
        key = f"within_{str(threshold).replace('.', 'p')}_deg"
        metrics[key] = (
            float(np.mean(valid_error <= threshold)) if len(valid_error) else np.nan
        )

    return metrics, signed_error


def _unit_ids_from_tuning_curves(tuning_curves: xr.DataArray) -> list[int]:
    return [int(unit_id) for unit_id in np.asarray(tuning_curves.coords["unit"].values)]


def align_decoder_data(
    data: nap.TsGroup | nap.TsdFrame,
    unit_ids: list[int],
) -> nap.TsGroup | nap.TsdFrame:
    if isinstance(data, nap.TsGroup):
        available_ids = {int(unit_id) for unit_id in np.asarray(data.index)}
    elif isinstance(data, nap.TsdFrame):
        available_ids = {int(unit_id) for unit_id in np.asarray(data.columns)}
    else:
        raise TypeError("data must be a pynapple TsGroup or TsdFrame.")

    missing = sorted(set(unit_ids) - available_ids)
    if missing:
        raise KeyError(f"Decoder units missing from data: {missing}")

    return data[unit_ids]


def decode_feature_dataframe(
    decoder: "PynappleBayesDecoder",
    data: nap.TsGroup | nap.TsdFrame,
    epochs: nap.IntervalSet,
    *,
    feature: nap.Tsd | None = None,
    circular_period: float | None = 360.0,
) -> tuple[pd.DataFrame, Any, Any]:
    decoded, posterior = decoder.decode(data=data, epochs=epochs)

    posterior_values = np.asarray(posterior.values)
    posterior_max = np.nanmax(posterior_values.reshape(len(decoded.values), -1), axis=1)

    trace_df = pd.DataFrame(
        {
            "time": decoded.index.to_numpy(),
            "decoded": decoded.values,
            "posterior_max": posterior_max,
        }
    )

    if feature is not None:
        true_feature = decoded.value_from(feature, ep=epochs, mode="closest")
        if len(decoded.values) != len(true_feature.values):
            raise ValueError("Decoded and true feature series are not aligned.")

        _, signed_error = summarize_decode_error(
            decoded_values=decoded.values,
            true_values=true_feature.values,
            circular_period=circular_period,
        )
        trace_df["true"] = true_feature.values
        trace_df["signed_error"] = signed_error
        trace_df["abs_error"] = np.abs(signed_error)

    return trace_df, decoded, posterior


def _shade_split_backgrounds(
    ax,
    split_df: pd.DataFrame,
    *,
    split_color_map: dict[str, str],
    split_alpha: float,
    start_col: str,
    end_col: str,
    split_col: str,
):
    for split_name, group in split_df.groupby(split_col, sort=False):
        color = split_color_map.get(str(split_name), "#dddddd")
        for _, row in group.iterrows():
            ax.axvspan(
                float(row[start_col]),
                float(row[end_col]),
                color=color,
                alpha=split_alpha,
                linewidth=0,
                zorder=0,
            )


def _shade_labeled_backgrounds(
    ax,
    interval_df: pd.DataFrame,
    *,
    color_map: dict[str, str],
    alpha: float,
    start_col: str,
    end_col: str,
    label_col: str,
):
    for _, row in interval_df.iterrows():
        label = str(row[label_col])
        color = color_map.get(label, "#dddddd")
        ax.axvspan(
            float(row[start_col]),
            float(row[end_col]),
            color=color,
            alpha=alpha,
            linewidth=0,
            zorder=0,
        )


def plot_decoder_diagnostics(
    trace_df: pd.DataFrame,
    split_df: pd.DataFrame,
    *,
    background_df: pd.DataFrame | None = None,
    background_color_map: dict[str, str] | None = None,
    background_alpha: float = 0.13,
    background_label_col: str = "interval_label",
    y_range: tuple[float, float] | None = (0.0, 360.0),
    split_color_map: dict[str, str] | None = None,
    split_alpha: float = 0.20,
    start_col: str = "start",
    end_col: str = "end",
    split_col: str = "split",
    title: str | None = None,
    figsize: tuple[float, float] = (18.0, 7.0),
):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    if split_color_map is None:
        split_color_map = {
            "train": "#ff7ab6",
            "test": "#8ec5ff",
        }
    if background_color_map is None:
        background_color_map = {
            "baseline": "#cbd5e1",
            "rotation_off": "#fdba74",
            "rotation_on": "#fb923c",
            "dot_off": "#93c5fd",
            "dot_on": "#3b82f6",
            "static": "#facc15",
            "tracking": "#86efac",
            "rotation": "#fb923c",
            "sweep": "#c4b5fd",
            "sweep_hold": "#ddd6fe",
        }

    fig, axes = plt.subplots(
        2,
        1,
        figsize=figsize,
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    trace_ax, quality_ax = axes

    for ax in axes:
        if background_df is not None and not background_df.empty:
            _shade_labeled_backgrounds(
                ax,
                background_df,
                color_map=background_color_map,
                alpha=background_alpha,
                start_col=start_col,
                end_col=end_col,
                label_col=background_label_col,
            )

        _shade_split_backgrounds(
            ax,
            split_df,
            split_color_map=split_color_map,
            split_alpha=split_alpha,
            start_col=start_col,
            end_col=end_col,
            split_col=split_col,
        )

    if "true" in trace_df:
        trace_ax.plot(
            trace_df["time"],
            trace_df["true"],
            color="#111111",
            linewidth=1.0,
            label="True",
            zorder=3,
        )

    trace_ax.plot(
        trace_df["time"],
        trace_df["decoded"],
        color="#006d77",
        linewidth=1.0,
        label="Decoded",
        zorder=4,
    )

    if y_range is not None:
        trace_ax.set_ylim(y_range)
    trace_ax.set_ylabel("Feature")
    if title:
        trace_ax.set_title(title)

    if "abs_error" in trace_df:
        quality_ax.plot(
            trace_df["time"],
            trace_df["abs_error"],
            color="#9b2226",
            linewidth=0.9,
            label="Abs error",
            zorder=3,
        )
        quality_ax.set_ylabel("Abs error")
    else:
        quality_ax.plot(
            trace_df["time"],
            trace_df["posterior_max"],
            color="#6a4c93",
            linewidth=0.9,
            label="Posterior max",
            zorder=3,
        )
        quality_ax.set_ylabel("Posterior max")

    if "posterior_max" in trace_df and "abs_error" in trace_df:
        posterior_ax = quality_ax.twinx()
        posterior_ax.plot(
            trace_df["time"],
            trace_df["posterior_max"],
            color="#6a4c93",
            linewidth=0.8,
            alpha=0.75,
            label="Posterior max",
            zorder=2,
        )
        posterior_ax.set_ylabel("Posterior max")
        posterior_ax.set_ylim(0.0, 1.05)

    split_handles = [
        Patch(
            facecolor=color,
            edgecolor="none",
            alpha=split_alpha,
            label=f"{split_name} interval",
        )
        for split_name, color in split_color_map.items()
        if split_name in set(split_df[split_col].astype(str))
    ]
    background_handles = []
    if background_df is not None and not background_df.empty:
        present_background_labels = list(dict.fromkeys(background_df[background_label_col].astype(str)))
        background_handles = [
            Patch(
                facecolor=background_color_map.get(label, "#dddddd"),
                edgecolor="none",
                alpha=background_alpha,
                label=f"{label} background",
            )
            for label in present_background_labels
        ]

    trace_handles, trace_labels = trace_ax.get_legend_handles_labels()
    trace_ax.legend(
        handles=background_handles + split_handles + trace_handles,
        labels=[h.get_label() for h in background_handles + split_handles] + trace_labels,
        loc="upper right",
        ncol=min(4, len(background_handles) + len(split_handles) + len(trace_handles)),
    )
    quality_ax.legend(loc="upper right")
    quality_ax.set_xlabel("Time (s)")

    for ax in axes:
        ax.grid(alpha=0.20)

    fig.tight_layout()
    return fig, axes


@dataclass
class PynappleBayesDecoder:
    tuning_curves: xr.DataArray
    feature_name: str = "head_direction_deg"
    bins: int | list[int] | tuple[int, ...] = 60
    feature_range: tuple[float, float] | list[tuple[float, float]] = (0.0, 360.0)
    bin_size: float = 0.1
    time_units: str = "s"
    uniform_prior: bool = True
    sliding_window_size: int | None = None
    decoder_id: str = field(default_factory=make_decoder_id)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def unit_ids(self) -> list[int]:
        return _unit_ids_from_tuning_curves(self.tuning_curves)

    def decode(
        self,
        data: nap.TsGroup | nap.TsdFrame,
        epochs: nap.IntervalSet,
        *,
        bin_size: float | None = None,
        uniform_prior: bool | None = None,
        sliding_window_size: int | None | str = "decoder_default",
    ):
        decode_bin_size = self.bin_size if bin_size is None else bin_size
        decode_uniform_prior = self.uniform_prior if uniform_prior is None else uniform_prior
        if sliding_window_size == "decoder_default":
            decode_sliding_window_size = self.sliding_window_size
        else:
            decode_sliding_window_size = sliding_window_size

        aligned_data = align_decoder_data(data, self.unit_ids)
        return nap.decode_bayes(
            tuning_curves=self.tuning_curves,
            data=aligned_data,
            epochs=epochs,
            bin_size=decode_bin_size,
            sliding_window_size=decode_sliding_window_size,
            time_units=self.time_units,
            uniform_prior=decode_uniform_prior,
        )

    def score(
        self,
        data: nap.TsGroup | nap.TsdFrame,
        feature: nap.Tsd,
        epochs: nap.IntervalSet,
        *,
        circular_period: float | None = 360.0,
        thresholds: tuple[float, ...] = (15.0, 30.0, 45.0, 90.0),
        return_outputs: bool = False,
    ):
        decoded, posterior = self.decode(data=data, epochs=epochs)
        true_feature = decoded.value_from(feature, ep=epochs, mode="closest")

        if len(decoded.values) != len(true_feature.values):
            raise ValueError("Decoded and true feature series are not aligned.")

        metrics, signed_error = summarize_decode_error(
            decoded_values=decoded.values,
            true_values=true_feature.values,
            circular_period=circular_period,
            thresholds=thresholds,
        )

        posterior_values = np.asarray(posterior.values)
        posterior_max = np.nanmax(posterior_values.reshape(len(decoded.values), -1), axis=1)
        metrics["mean_posterior_max"] = float(np.nanmean(posterior_max))
        metrics["median_posterior_max"] = float(np.nanmedian(posterior_max))

        per_bin_df = pd.DataFrame(
            {
                "time": decoded.index.to_numpy(),
                "decoded": decoded.values,
                "true": true_feature.values,
                "signed_error": signed_error,
                "abs_error": np.abs(signed_error),
                "posterior_max": posterior_max,
            }
        )

        if return_outputs:
            return metrics, decoded, posterior, per_bin_df
        return metrics

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "PynappleBayesDecoder":
        with open(Path(path), "rb") as f:
            decoder = pickle.load(f)
        if not isinstance(decoder, cls):
            raise TypeError(f"{path} does not contain a {cls.__name__}.")
        return decoder


def fit_pynapple_bayes_decoder(
    data: nap.TsGroup | nap.TsdFrame,
    feature: nap.Tsd,
    train_epochs: nap.IntervalSet,
    *,
    bins: int | list[int] | tuple[int, ...] = 60,
    feature_range: tuple[float, float] | list[tuple[float, float]] = (0.0, 360.0),
    bin_size: float = 0.1,
    time_units: str = "s",
    uniform_prior: bool = True,
    sliding_window_size: int | None = None,
    feature_name: str = "head_direction_deg",
    decoder_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> PynappleBayesDecoder:
    tuning_curves = nap.compute_tuning_curves(
        data=data,
        features=feature,
        bins=bins,
        range=feature_range,
        epochs=train_epochs,
    )

    return PynappleBayesDecoder(
        tuning_curves=tuning_curves,
        feature_name=feature_name,
        bins=bins,
        feature_range=feature_range,
        bin_size=bin_size,
        time_units=time_units,
        uniform_prior=uniform_prior,
        sliding_window_size=sliding_window_size,
        decoder_id=decoder_id or make_decoder_id(),
        metadata=metadata or {},
    )


def append_log_row(row: dict[str, Any], log_path: str | Path) -> pd.DataFrame:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    new_row_df = pd.DataFrame([row])
    if log_path.exists():
        log_df = pd.read_csv(log_path)
        log_df = pd.concat([log_df, new_row_df], ignore_index=True, sort=False)
    else:
        log_df = new_row_df

    log_df.to_csv(log_path, index=False)
    return log_df


@dataclass
class DecoderRunResult:
    decoder: PynappleBayesDecoder
    metrics: dict[str, Any]
    log_df: pd.DataFrame
    split_df: pd.DataFrame
    per_bin_df: pd.DataFrame
    decoder_path: Path
    split_path: Path
    per_bin_path: Path
    decoded: Any
    posterior: Any


def train_score_save_decoder(
    data: nap.TsGroup | nap.TsdFrame,
    feature: nap.Tsd,
    train_test_epochs: nap.IntervalSet,
    output_dir: str | Path,
    *,
    probe_name: str | None = None,
    decoder_id: str | None = None,
    train_fraction: float = 0.7,
    split_window_size: float = 5.0,
    split_seed: int | None = 0,
    bins: int | list[int] | tuple[int, ...] = 60,
    feature_range: tuple[float, float] | list[tuple[float, float]] = (0.0, 360.0),
    bin_size: float = 0.1,
    time_units: str = "s",
    uniform_prior: bool = True,
    sliding_window_size: int | None = None,
    feature_name: str = "head_direction_deg",
    circular_period: float | None = 360.0,
    thresholds: tuple[float, ...] = (15.0, 30.0, 45.0, 90.0),
    cluster_ids: list[int] | None = None,
    metadata: dict[str, Any] | None = None,
    log_filename: str = "decoder_log.csv",
) -> DecoderRunResult:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if cluster_ids is not None:
        data = align_decoder_data(data, [int(cluster_id) for cluster_id in cluster_ids])

    train_ep, test_ep, split_df = random_split_interval_set(
        train_test_epochs,
        train_fraction=train_fraction,
        window_size=split_window_size,
        seed=split_seed,
    )

    run_metadata = dict(metadata or {})
    run_metadata.update(
        {
            "probe_name": probe_name,
            "train_fraction": train_fraction,
            "split_window_size": split_window_size,
            "split_seed": split_seed,
            "train_duration": interval_total_duration(train_ep),
            "test_duration": interval_total_duration(test_ep),
        }
    )

    decoder = fit_pynapple_bayes_decoder(
        data=data,
        feature=feature,
        train_epochs=train_ep,
        bins=bins,
        feature_range=feature_range,
        bin_size=bin_size,
        time_units=time_units,
        uniform_prior=uniform_prior,
        sliding_window_size=sliding_window_size,
        feature_name=feature_name,
        decoder_id=decoder_id,
        metadata=run_metadata,
    )

    metrics, decoded, posterior, per_bin_df = decoder.score(
        data=data,
        feature=feature,
        epochs=test_ep,
        circular_period=circular_period,
        thresholds=thresholds,
        return_outputs=True,
    )

    decoder_path = output_dir / f"{decoder.decoder_id}.pkl"
    split_path = output_dir / f"{decoder.decoder_id}_split.csv"
    per_bin_path = output_dir / f"{decoder.decoder_id}_timebins.csv"

    decoder.save(decoder_path)

    split_df = split_df.copy()
    split_df.insert(0, "decoder_id", decoder.decoder_id)
    split_df.to_csv(split_path, index=False)

    per_bin_df = per_bin_df.copy()
    per_bin_df.insert(0, "decoder_id", decoder.decoder_id)
    per_bin_df.insert(1, "probe_name", probe_name)
    per_bin_df.to_csv(per_bin_path, index=False)

    log_row = {
        "decoder_id": decoder.decoder_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "probe_name": probe_name,
        "decoder_path": str(decoder_path),
        "split_path": str(split_path),
        "per_bin_path": str(per_bin_path),
        "feature_name": feature_name,
        "n_units": len(decoder.unit_ids),
        "unit_ids": ",".join(map(str, decoder.unit_ids)),
        "bins": bins,
        "feature_range": feature_range,
        "bin_size": bin_size,
        "time_units": time_units,
        "uniform_prior": uniform_prior,
        "sliding_window_size": sliding_window_size,
        "train_fraction": train_fraction,
        "split_window_size": split_window_size,
        "split_seed": split_seed,
        "train_duration": interval_total_duration(train_ep),
        "test_duration": interval_total_duration(test_ep),
        **metrics,
    }
    log_df = append_log_row(log_row, output_dir / log_filename)

    return DecoderRunResult(
        decoder=decoder,
        metrics=metrics,
        log_df=log_df,
        split_df=split_df,
        per_bin_df=per_bin_df,
        decoder_path=decoder_path,
        split_path=split_path,
        per_bin_path=per_bin_path,
        decoded=decoded,
        posterior=posterior,
    )
