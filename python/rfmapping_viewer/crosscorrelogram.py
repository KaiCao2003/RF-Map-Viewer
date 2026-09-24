"""Small, independent Pynapple cross-correlogram utility for a recording session."""

from __future__ import annotations

import math
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from matplotlib.figure import Figure
    from pandas import DataFrame


def _spike_clusters_path(session_dir: Path, probe_name: str) -> Path:
    recording_number = int(session_dir.name.rsplit("_", 1)[1])
    return (
        session_dir / "kilosort" / f"Probe{probe_name}"
        / f"kilosort_{recording_number}" / "spike_clusters.npy"
    )


def available_unit_ids(session_dir: Path, probe_name: str) -> list[int]:
    import numpy as np

    clusters = np.load(_spike_clusters_path(session_dir, probe_name), mmap_mode="r")
    return np.unique(clusters).tolist()


def compute_crosscorrelograms(
    session_dir: Path,
    probe_name: str,
    unit_ids: Sequence[int],
    bin_size: float = 0.001,
    window_size: float = 0.05,
) -> DataFrame:
    """Return target firing rates (Hz), with lag in seconds and ordered unit pairs."""
    unit_ids = tuple(unit_ids)
    if len(unit_ids) not in (2, 3) or len(set(unit_ids)) != len(unit_ids):
        raise ValueError("Select two or three distinct units.")
    for label, value in (("Bin size", bin_size), ("Window size", window_size)):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{label} must be positive and finite.")

    import numpy as np

    # ADC timestamps are already seconds and share spike order with Kilosort.
    spike_times = np.load(
        session_dir / "data" / f"probe{probe_name}" / "adc_spike_time.npy",
        mmap_mode="r",
    ).ravel()
    spike_clusters = np.load(
        _spike_clusters_path(session_dir, probe_name), mmap_mode="r"
    ).ravel()
    if spike_times.size != spike_clusters.size:
        raise ValueError("Spike timestamps and cluster assignments have different lengths.")
    selected_spikes = {
        unit_id: spike_times[spike_clusters == unit_id] for unit_id in unit_ids
    }
    for unit_id, times in selected_spikes.items():
        if not times.size:
            raise ValueError(f"Unit {unit_id} has no spikes in this session.")

    import pandas as pd
    import pynapple as nap

    group = nap.TsGroup({
        unit_id: nap.Ts(t=times, time_units="s")
        for unit_id, times in selected_spikes.items()
    })
    # Separate groups preserve the chosen reference even when unit IDs descend.
    return pd.concat([
        nap.compute_crosscorrelogram(
            (group[[first]], group[[second]]),
            binsize=bin_size,
            windowsize=window_size,
            norm=False,
        )
        for first, second in combinations(unit_ids, 2)
    ], axis=1)


def make_crosscorrelogram_figure(
    ccg: DataFrame,
    session_dir: Path,
    probe_name: str,
    bin_size: float,
    window_size: float,
) -> Figure:
    import matplotlib as mpl
    from matplotlib.figure import Figure

    mpl.rcParams["savefig.facecolor"] = "white"
    mpl.rcParams["savefig.transparent"] = False
    with mpl.rc_context({
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": "#c7cbd1",
        "axes.labelcolor": "black",
        "text.color": "black",
        "xtick.color": "black",
        "ytick.color": "black",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.axisbelow": True,
        "axes.grid": False,
        "axes.labelsize": 10,
        "axes.titlesize": 11,
        "axes.titleweight": "normal",
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
    }):
        figure = Figure(
            figsize=(8, 3.2 * len(ccg.columns)), facecolor="white", layout="constrained"
        )
        axes = figure.subplots(len(ccg.columns), 1, squeeze=False)
        for ax, (first, second) in zip(axes[:, 0], ccg.columns):
            values = ccg[(first, second)]
            ax.bar(values.index * 1000, values.values, width=bin_size * 1000, color="#25282d")
            ax.grid(axis="y", color="#eaecf0", linewidth=0.7)
            ax.tick_params(length=3, width=0.6)
            ax.axvline(0, color="#8b919b", linestyle="--", linewidth=0.8)
            ax.set(
                xlabel=f"Lag from unit {first} to unit {second} (ms)",
                ylabel="Firing rate (Hz)",
                title=f"{first} → {second}",
                xlim=(-window_size * 1000, window_size * 1000),
                ylim=(0, None),
            )
        figure.suptitle(
            f"{session_dir.parent.parent.name} · {session_dir.name} · Probe{probe_name}",
            fontsize=12,
            color="#535b67",
        )
    return figure
