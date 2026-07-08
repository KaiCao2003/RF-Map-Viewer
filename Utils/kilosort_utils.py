from pathlib import Path

import numpy as np
import pynapple as nap
import pandas as pd
import xarray
from tqdm import tqdm

from Utils.json_tools import read_formatted_json, write_formatted_json

try:
    from numba import njit, prange, get_num_threads, set_num_threads

    print(get_num_threads())
    set_num_threads(32)

except Exception:  # pragma: no cover - fallback is exercised only without numba.
    njit = None
    prange = range


def _compress_times_to_epoch_clock(
        times: np.ndarray,
        ep_starts: np.ndarray,
        ep_ends: np.ndarray,
        cum_lengths: np.ndarray,
) -> np.ndarray:
    """Map real timestamps into a contiguous epoch clock."""
    if times.size == 0 or ep_starts.size == 0:
        return np.empty(0, dtype=float)

    segment_idx = np.searchsorted(ep_ends, times, side="left")
    valid = segment_idx < ep_starts.size
    if not np.any(valid):
        return np.empty(0, dtype=float)

    valid_positions = np.flatnonzero(valid)
    valid_segments = segment_idx[valid]
    valid[valid_positions] = times[valid_positions] >= ep_starts[valid_segments]

    if not np.any(valid):
        return np.empty(0, dtype=float)

    segment_idx = segment_idx[valid]
    times = times[valid]
    return cum_lengths[segment_idx] + (times - ep_starts[segment_idx])


def _flatten_feature_segments(
        feature_tsd: nap.Tsd,
        ep_starts: np.ndarray,
        ep_ends: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Flatten feature samples while retaining epoch-local slice boundaries."""
    feature_t = feature_tsd.index.to_numpy()
    feature_values = np.asarray(feature_tsd.values, dtype=float).reshape(-1)

    segment_starts = np.zeros(ep_starts.size, dtype=np.int64)
    segment_ends = np.zeros(ep_starts.size, dtype=np.int64)
    time_segments: list[np.ndarray] = []
    value_segments: list[np.ndarray] = []

    offset = 0
    for k, (start, end) in enumerate(zip(ep_starts, ep_ends)):
        start_idx = np.searchsorted(feature_t, start, side="left")
        end_idx = np.searchsorted(feature_t, end, side="right")
        segment_t = feature_t[start_idx:end_idx]
        segment_values = feature_values[start_idx:end_idx]

        segment_starts[k] = offset
        offset += len(segment_t)
        segment_ends[k] = offset

        if len(segment_t) > 0:
            time_segments.append(segment_t)
            value_segments.append(segment_values)

    if time_segments:
        flat_times = np.concatenate(time_segments).astype(float, copy=False)
        flat_values = np.concatenate(value_segments).astype(float, copy=False)
    else:
        flat_times = np.empty(0, dtype=float)
        flat_values = np.empty(0, dtype=float)

    return flat_times, flat_values, segment_starts, segment_ends


def _bin_edges_from_tuning_curves(
        hd_tuning_curves: xarray.DataArray,
        num_bins: int,
) -> np.ndarray:
    bin_edges = hd_tuning_curves.attrs.get("bin_edges")
    if bin_edges is None:
        return np.linspace(0.0, 360.0, num_bins + 1)

    if isinstance(bin_edges, (list, tuple)):
        bin_edges = bin_edges[0]
    bin_edges = np.asarray(bin_edges, dtype=float).reshape(-1)

    if bin_edges.size != num_bins + 1:
        return np.linspace(0.0, 360.0, num_bins + 1)

    return bin_edges


def _compute_shuffle_r_numpy(
        compressed_times: np.ndarray,
        shifts: np.ndarray,
        total_valid_len: float,
        ep_starts: np.ndarray,
        cum_lengths: np.ndarray,
        feature_times: np.ndarray,
        feature_values: np.ndarray,
        feature_segment_starts: np.ndarray,
        feature_segment_ends: np.ndarray,
        bin_edges: np.ndarray,
        occupancy_counts: np.ndarray,
        cos_angles: np.ndarray,
        sin_angles: np.ndarray,
) -> np.ndarray:
    out = np.full(shifts.size, np.nan, dtype=float)
    if total_valid_len <= 0 or compressed_times.size == 0:
        return out
    if not np.all(np.isfinite(occupancy_counts) & (occupancy_counts > 0)):
        return out

    n_bins = occupancy_counts.size
    for j, shift in enumerate(shifts):
        shifted_compressed = (compressed_times + shift) % total_valid_len
        segment_idx = np.searchsorted(cum_lengths, shifted_compressed, side="right") - 1
        valid_segment = (segment_idx >= 0) & (segment_idx < ep_starts.size)
        if not np.any(valid_segment):
            continue

        shifted_times = ep_starts[segment_idx[valid_segment]] + (
                shifted_compressed[valid_segment] - cum_lengths[segment_idx[valid_segment]]
        )
        segment_idx = segment_idx[valid_segment]

        nearest_values = np.full(shifted_times.size, np.nan, dtype=float)
        for segment in np.unique(segment_idx):
            seg_mask = segment_idx == segment
            start = feature_segment_starts[segment]
            end = feature_segment_ends[segment]
            if end <= start:
                continue

            segment_times = feature_times[start:end]
            local_times = shifted_times[seg_mask]
            positions = np.searchsorted(segment_times, local_times, side="left") + start
            before = np.maximum(positions - 1, start)
            after = np.minimum(positions, end - 1)

            choose_after = positions == start
            in_middle = (positions > start) & (positions < end)
            choose_after[in_middle] = (
                    feature_times[after[in_middle]] - local_times[in_middle]
                    <= local_times[in_middle] - feature_times[before[in_middle]]
            )
            nearest_idx = np.where(choose_after, after, before)
            nearest_values[seg_mask] = feature_values[nearest_idx]

        nearest_values = nearest_values[np.isfinite(nearest_values)]
        if nearest_values.size == 0:
            continue

        counts = np.histogram(nearest_values, bins=bin_edges)[0].astype(float)
        rates = counts / occupancy_counts
        rate_sum = np.sum(rates)
        if rate_sum > 0:
            real = np.sum(rates * cos_angles)
            imag = np.sum(rates * sin_angles)
            out[j] = np.sqrt(real * real + imag * imag) / rate_sum

    return out


if njit is not None:
    @njit(cache=True, nogil=True)
    def _find_epoch_from_compressed(value, cum_lengths):
        if value < 0.0 or value >= cum_lengths[-1]:
            return -1

        left = 0
        right = cum_lengths.shape[0]
        while left < right:
            mid = (left + right) // 2
            if cum_lengths[mid] <= value:
                left = mid + 1
            else:
                right = mid

        epoch_idx = left - 1
        if epoch_idx < 0 or epoch_idx >= cum_lengths.shape[0] - 1:
            return -1
        return epoch_idx


    @njit(cache=True, nogil=True)
    def _nearest_feature_index(time_value, feature_times, start, end):
        if end <= start:
            return -1

        left = start
        right = end
        while left < right:
            mid = (left + right) // 2
            if feature_times[mid] < time_value:
                left = mid + 1
            else:
                right = mid

        if left <= start:
            return start
        if left >= end:
            return end - 1

        before = left - 1
        after = left
        if feature_times[after] - time_value <= time_value - feature_times[before]:
            return after
        return before


    @njit(cache=True, nogil=True)
    def _bin_index(value, bin_edges):
        n_bins = bin_edges.shape[0] - 1
        if not np.isfinite(value) or value < bin_edges[0] or value > bin_edges[n_bins]:
            return -1
        if value == bin_edges[n_bins]:
            return n_bins - 1

        left = 0
        right = bin_edges.shape[0]
        while left < right:
            mid = (left + right) // 2
            if bin_edges[mid] <= value:
                left = mid + 1
            else:
                right = mid

        idx = left - 1
        if idx < 0 or idx >= n_bins:
            return -1
        return idx


    @njit(cache=True, parallel=True, nogil=True)
    def _compute_shuffle_r_numba(
            compressed_times,
            shifts,
            total_valid_len,
            ep_starts,
            cum_lengths,
            feature_times,
            feature_values,
            feature_segment_starts,
            feature_segment_ends,
            bin_edges,
            occupancy_counts,
            cos_angles,
            sin_angles,
    ):
        n_shuffles = shifts.shape[0]
        n_bins = occupancy_counts.shape[0]
        out = np.full(n_shuffles, np.nan)

        if total_valid_len <= 0.0 or compressed_times.shape[0] == 0:
            return out

        for b in range(n_bins):
            if not np.isfinite(occupancy_counts[b]) or occupancy_counts[b] <= 0.0:
                return out

        for j in prange(n_shuffles):
            counts = np.zeros(n_bins)

            for spike_idx in range(compressed_times.shape[0]):
                shifted_compressed = compressed_times[spike_idx] + shifts[j]
                shifted_compressed -= np.floor(shifted_compressed / total_valid_len) * total_valid_len

                epoch_idx = _find_epoch_from_compressed(shifted_compressed, cum_lengths)
                if epoch_idx < 0:
                    continue

                shifted_time = ep_starts[epoch_idx] + (
                        shifted_compressed - cum_lengths[epoch_idx]
                )
                feature_idx = _nearest_feature_index(
                    shifted_time,
                    feature_times,
                    feature_segment_starts[epoch_idx],
                    feature_segment_ends[epoch_idx],
                )
                if feature_idx < 0:
                    continue

                bin_idx = _bin_index(feature_values[feature_idx], bin_edges)
                if bin_idx >= 0:
                    counts[bin_idx] += 1.0

            rate_sum = 0.0
            real = 0.0
            imag = 0.0
            for b in range(n_bins):
                rate = counts[b] / occupancy_counts[b]
                rate_sum += rate
                real += rate * cos_angles[b]
                imag += rate * sin_angles[b]

            if rate_sum > 0.0:
                out[j] = np.sqrt(real * real + imag * imag) / rate_sum

        return out
else:
    _compute_shuffle_r_numba = None


def locate_spikes(cluster_KSLabel_sort: list | np.ndarray, spike_clusters: np.ndarray, spike_times: np.ndarray) -> list:
    """
    For each value in 'cluster_KSLabel_sort', find positions in 'spike_clusters' where it occurs,
    and collect corresponding time points, in index, from 'spike_times'.

    Args:
        cluster_KSLabel_sort (list or np.ndarray): Values to look up.
        spike_clusters (list or np.ndarray): Search space (must have same shape as c).
        spike_times (list or np.ndarray): Values paired with b.

    Returns:
        list of [value, indices_list, cvals_list]
        [id of cluster,
        [list of indexes that spikes detected in index],
        [list of time points that spikes detected in time]]

    """
    # Ensure arrays
    cluster_KSLabel_sort = np.asarray(cluster_KSLabel_sort) if not isinstance(cluster_KSLabel_sort,
                                                                              np.ndarray) else cluster_KSLabel_sort

    if spike_clusters.shape != spike_times.shape:
        print(spike_clusters)
        raise ValueError("spike_clusters and spike_times must have the same shape")

    # Sort b once
    order = np.argsort(spike_clusters, kind="stable")
    spike_clusters_sorted = spike_clusters[order]
    spike_times_sorted = spike_times[order]

    vals_spike_clusters, starts_spike_clusters, counts_spike_clusters = np.unique(spike_clusters_sorted,
                                                                                  return_index=True, return_counts=True)

    out = []
    for v in cluster_KSLabel_sort:
        pos = np.searchsorted(vals_spike_clusters, v)
        if pos < vals_spike_clusters.size and vals_spike_clusters[pos] == v:
            s = starts_spike_clusters[pos]
            e = s + counts_spike_clusters[pos]
            idxs = order[s:e]
            cvals = spike_times_sorted[s:e]
            out.append([int(v), idxs.tolist(), cvals.tolist()])
        else:
            out.append([int(v), [], []])

    return out


def filter_by_range(pairs: list, start: int, end: int) -> list:
    """
    Filter each [value, indices, cvals] block by keeping only those
    where cvals lie between [start, end] inclusive.

    Args:
        pairs: list of [value, indices_list, cvals_list]
        start: int (lower bound)
        end: int (upper bound)

    Returns:
        list of [value, filtered_indices, filtered_cvals]
    """
    out = []
    for val, idxs, cvals in pairs:
        idxs = np.array(idxs)
        cvals = np.array(cvals)
        mask = (cvals >= start) & (cvals <= end)
        out.append([val, idxs[mask].tolist(), cvals[mask].tolist()])
    return out


def convert_time_list_to_nap_tsd(time_list: list[list[float]] | np.ndarray, *, auto_fix: bool = True,
                                 return_in_list: bool = False) -> list | nap.IntervalSet:
    starts = [float(interval[0]) for interval in time_list]
    ends = [float(interval[1]) for interval in time_list]

    if auto_fix:
        epsilon = 1e-6  # 1 microsecond if your unit is seconds

        ends_fixed = ends.copy()
        for i in range(len(ends_fixed) - 1):
            if ends_fixed[i] == starts[i + 1]:
                ends_fixed[i] -= epsilon
        ends = ends_fixed

    return nap.IntervalSet(start=starts, end=ends) if not return_in_list else [starts, ends]


def sync_spike_time_to_adc_time(
        spike_times_path: str | Path,
        probe_continuous_timestamps_path: str | Path,
        *,
        output_path: str | Path | None = None,
) -> np.ndarray:
    """Generate probe timestamps directly from Kilosort sample numbers."""
    spike_samples = np.load(Path(spike_times_path)).squeeze().astype(np.int64)
    probe_continuous_timestamps = np.load(Path(probe_continuous_timestamps_path), mmap_mode="r")
    if spike_samples.size and (
            spike_samples.min() < 0
            or spike_samples.max() >= probe_continuous_timestamps.shape[0]
    ):
        raise IndexError(
            "spike sample index is outside probe_continuous_timestamps: "
            f"min={spike_samples.min()}, max={spike_samples.max()}, "
            f"n_timestamps={probe_continuous_timestamps.shape[0]}"
        )

    probe_spike_times = np.asarray(probe_continuous_timestamps[spike_samples], dtype=float)

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(output_path, probe_spike_times)

    return probe_spike_times


def gen_tuning_curves(base_dir: str | Path, kilosort_dir: str | Path, probe_name: str, session_info: dict,
                      interval_pairs, HD_tsd, kilosort_info_filename: str, num_of_bins_in_hd: int):
    with tqdm(total=7, desc="Reading files", unit="%",
              bar_format="{l_bar}{bar}| {n}/{total} [{percentage:3.0f}%]") as pbar:
        base_dir = Path(base_dir)
        data_dir = base_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        kilosort_dir = Path(kilosort_dir)

        spike_times_dir: Path = data_dir / f"Probe{probe_name}" / "adc_spike_time.npy"
        kilosort_raw_spike_times_dir: Path = kilosort_dir / "spike_times.npy"
        probe_continuous_timestamps_dir: Path = (
                Path(session_info["base_path"])
                / session_info["record_nodes"]
                / session_info["experiment_id"]
                / session_info["recording_name"]
                / "continuous"
                / session_info[f"continuous_probe_{probe_name}_folder"]
                / "timestamps.npy"
        )
        cluster_KSLabel_dir: Path = kilosort_dir / "cluster_KSLabel.tsv"
        spike_clusters_dir: Path = kilosort_dir / "spike_clusters.npy"

        cluster_KSLabel = pd.read_csv(cluster_KSLabel_dir, sep='\t')
        pbar.update(1)

        sync_spike_time_to_adc_time(
            kilosort_raw_spike_times_dir,
            probe_continuous_timestamps_dir,
            output_path=spike_times_dir,
        )

        spike_times = np.load(spike_times_dir)
        print(spike_times.shape)
        print(spike_times[-1])
        pbar.update(1)

        spike_clusters = np.load(spike_clusters_dir)
        print(spike_clusters.shape)
        pbar.update(1)

        kilosort_info_path = data_dir / f"{kilosort_info_filename}_Probe{probe_name}.json"
        if not kilosort_info_path.is_file():
            write_formatted_json(
                ["adc_spike_time_path", "kilosort_dir", "cluster_KSLabel_dir", "spike_clusters_dir"],
                [spike_times_dir, kilosort_dir, cluster_KSLabel_dir, spike_clusters_dir],
                filename=f"{kilosort_info_filename}_Probe{probe_name}",
                path=data_dir,
            )
        pbar.update(1)

        cluster_KSLabel_sort = [[label, grp["cluster_id"].tolist()]
                                for label, grp in cluster_KSLabel.groupby("KSLabel")]
        located_spikes_before_filter = locate_spikes(cluster_KSLabel_sort[0][1], spike_clusters,
                                                     spike_times)  # in time, start from 0, where the recording started

        pbar.update(1)

        spikes_dict = {
            int(cluster_id): nap.Ts(t=np.asarray(times, dtype=float), time_units="s")
            for cluster_id, _, times in located_spikes_before_filter
            if len(times) > 0  # drop empty clusters; remove this line if you want to keep them
        }
        pbar.update(1)

        # Create the TsGroup
        tsgroup = nap.TsGroup(spikes_dict)

        # time_support = nap.IntervalSet(start=0.56, end=49084.21)  # video time, in second, start from 0 to the end of the len of the video
        time_support = convert_time_list_to_nap_tsd(interval_pairs, return_in_list=False)

        hd_tuning_curves = nap.compute_tuning_curves(
            data=tsgroup,
            features=HD_tsd,
            bins=num_of_bins_in_hd,
            epochs=time_support,
            range=(0, 360),
        )

        pbar.update(1)
        print(type(tsgroup), type(time_support), type(hd_tuning_curves))
        return tsgroup, time_support, hd_tuning_curves, cluster_KSLabel_sort


def shuffle_neuro_data(base_dir: str, probe_name: str, hd_tuning_curves: xarray.DataArray, hd_tsd: nap.Tsd,
                       time_support: nap.IntervalSet, tsgroup: nap.TsGroup, num_shuffle: int, *,
                       is_overwrite_shuffle_data: bool, save_path: str | Path = None) -> pd.DataFrame:
    if save_path is None:
        save_path = Path(f"{base_dir}/data/tc_summary_Probe{probe_name}.csv")
    else:
        save_path = Path(save_path)

    if (save_path).is_file() and not is_overwrite_shuffle_data:
        print("Tuning Curve Summary exists, loading...")
        return pd.read_csv(save_path)

    # ----------------------------------------------------------------------

    else:
        angle_dim = [d for d in hd_tuning_curves.dims if d != "unit"][0]
        all_cluster_ids = hd_tuning_curves.coords["unit"].values
        angles_deg = hd_tuning_curves.coords[angle_dim].values
        angles_rad = np.deg2rad(angles_deg)
        rates_mat = hd_tuning_curves.values
        n_units = len(all_cluster_ids)
        num_of_bins_in_hd = hd_tuning_curves.sizes[angle_dim]
        bin_width = 360 / num_of_bins_in_hd

        peak_idx_all = hd_tuning_curves.argmax(dim=angle_dim).values
        preferred_hd_peakbin_all = angles_deg[peak_idx_all]
        peak_rate_all = hd_tuning_curves.max(dim=angle_dim).values
        overall_rate_all = np.asarray(hd_tuning_curves.attrs["rates"], dtype=float)
        occupancy_counts = np.asarray(hd_tuning_curves.attrs["occupancy"], dtype=float).reshape(-1)
        p_i = occupancy_counts / occupancy_counts.sum()

        ic_all = np.full(n_units, np.nan, dtype=float)
        for i in range(n_units):
            lambda_i = rates_mat[i]
            lambda_bar = overall_rate_all[i]
            valid = (p_i > 0) & (lambda_i > 0) & (lambda_bar > 0)
            ic_all[i] = np.sum(
                p_i[valid] * (lambda_i[valid] / lambda_bar) * np.log2(lambda_i[valid] / lambda_bar)
            )

        mi_df = nap.compute_mutual_information(hd_tuning_curves)

        R_all = np.full(n_units, np.nan, dtype=float)
        preferred_hd_vector_all = np.full(n_units, np.nan, dtype=float)
        for i in range(n_units):
            lambda_i = rates_mat[i]
            if np.sum(lambda_i) > 0:
                mean_vector = np.sum(lambda_i * np.exp(1j * angles_rad)) / np.sum(lambda_i)
                R_all[i] = np.abs(mean_vector)
                preferred_hd_vector_all[i] = np.rad2deg(np.angle(mean_vector)) % 360

        directional_masks_all = np.zeros((n_units, num_of_bins_in_hd), dtype=bool)
        directional_range_width_deg_all = np.full(n_units, np.nan, dtype=float)
        for i in range(n_units):
            lambda_i = rates_mat[i]
            rough_background = np.median(lambda_i)
            above_bg = lambda_i > rough_background
            peak_idx = np.argmax(lambda_i)

            left_idx = peak_idx
            while above_bg[(left_idx - 1) % num_of_bins_in_hd]:
                left_idx = (left_idx - 1) % num_of_bins_in_hd
                if left_idx == peak_idx:
                    break

            right_idx = peak_idx
            while above_bg[(right_idx + 1) % num_of_bins_in_hd]:
                right_idx = (right_idx + 1) % num_of_bins_in_hd
                if right_idx == peak_idx:
                    break

            directional_mask = np.zeros(num_of_bins_in_hd, dtype=bool)
            if left_idx <= right_idx:
                directional_mask[left_idx:right_idx + 1] = True
            if left_idx > right_idx:
                directional_mask[left_idx:] = True
            if left_idx > right_idx:
                directional_mask[:right_idx + 1] = True

            directional_masks_all[i] = directional_mask
            directional_range_width_deg_all[i] = directional_mask.sum() * bin_width

        guard_bins = int(18 / bin_width)
        background_rate_all = np.full(n_units, np.nan, dtype=float)
        for i in range(n_units):
            lambda_i = rates_mat[i]
            directional_mask = directional_masks_all[i].copy()
            exclude_mask = directional_mask.copy()

            for shift in range(1, guard_bins + 1):
                exclude_mask |= np.roll(directional_mask, shift)
            for shift in range(1, guard_bins + 1):
                exclude_mask |= np.roll(directional_mask, -shift)

            background_mask = ~exclude_mask
            if np.sum(background_mask) > 0:
                background_rate_all[i] = np.mean(lambda_i[background_mask])

        hd_ep = hd_tsd.restrict(time_support)
        dt = np.median(np.diff(hd_ep.index.to_numpy()))
        occupancy_time = occupancy_counts * dt

        n_obs_all = np.full(n_units, np.nan, dtype=float)
        z_rayleigh_all = np.full(n_units, np.nan, dtype=float)
        p_rayleigh_all = np.full(n_units, np.nan, dtype=float)
        for i in range(n_units):
            lambda_i = rates_mat[i]
            R = R_all[i]
            spike_count_per_bin = lambda_i * occupancy_time
            n_obs = np.sum(spike_count_per_bin)
            z_rayleigh = n_obs * (R ** 2)
            p_rayleigh = np.exp(-z_rayleigh)
            n_obs_all[i] = n_obs
            z_rayleigh_all[i] = z_rayleigh
            p_rayleigh_all[i] = p_rayleigh

        rayleigh_significant_all = p_rayleigh_all < 0.05
        R_ge_05_all = R_all >= 0.5

        # ----------------------

        ep_starts = np.asarray(time_support.start, dtype=float)
        ep_ends = np.asarray(time_support.end, dtype=float)
        ep_lengths = ep_ends - ep_starts
        cum_lengths = np.concatenate([[0.0], np.cumsum(ep_lengths)])
        total_valid_len = cum_lengths[-1]

        shuffle_threshold_99_all = np.full(n_units, np.nan, dtype=float)
        shuffle_significant_all = np.full(n_units, False, dtype=bool)
        shuffle_R_all = np.full((n_units, num_shuffle), np.nan, dtype=float)

        shuffle_bin_edges = _bin_edges_from_tuning_curves(hd_tuning_curves, num_of_bins_in_hd)
        feature_times, feature_values, feature_segment_starts, feature_segment_ends = _flatten_feature_segments(
            hd_tsd,
            ep_starts,
            ep_ends,
        )
        cos_angles = np.cos(angles_rad)
        sin_angles = np.sin(angles_rad)
        compute_shuffle_r = _compute_shuffle_r_numba or _compute_shuffle_r_numpy

        with tqdm(total=(n_units * num_shuffle), desc="Shuffling", unit="%",
                  bar_format="{l_bar}{bar}| {n}/{total} [{percentage:3.0f}%]") as pbar:
            for i in range(n_units):
                cluster_id = all_cluster_ids[i]
                observed_R = R_all[i]
                spk_times = tsgroup[cluster_id].restrict(time_support).index.to_numpy()

                if len(spk_times) == 0 or num_shuffle <= 0 or total_valid_len <= 0:
                    pbar.update(num_shuffle)
                    continue

                compressed_times = _compress_times_to_epoch_clock(
                    spk_times,
                    ep_starts,
                    ep_ends,
                    cum_lengths,
                )

                if len(compressed_times) == 0:
                    pbar.update(num_shuffle)
                    continue

                shifts = np.random.uniform(0, total_valid_len, size=num_shuffle)
                shuffle_R_all[i] = compute_shuffle_r(
                    compressed_times,
                    shifts,
                    total_valid_len,
                    ep_starts,
                    cum_lengths,
                    feature_times,
                    feature_values,
                    feature_segment_starts,
                    feature_segment_ends,
                    shuffle_bin_edges,
                    occupancy_counts,
                    cos_angles,
                    sin_angles,
                )

                if not np.all(np.isnan(shuffle_R_all[i])):
                    shuffle_threshold_99_all[i] = np.nanpercentile(shuffle_R_all[i], 99)
                shuffle_significant_all[i] = observed_R > shuffle_threshold_99_all[i]
                pbar.update(num_shuffle)

        hd_summary = pd.DataFrame({
            "cluster_id": all_cluster_ids,
            "preferred_hd_peakbin": preferred_hd_peakbin_all,
            "preferred_hd_vector": preferred_hd_vector_all,
            "peak_rate": peak_rate_all,
            "overall_rate": overall_rate_all,
            "background_rate": background_rate_all,
            "directional_range_width_deg": directional_range_width_deg_all,
            "IC_bits_per_spike_manual": ic_all,
            "R": R_all,
            "n_obs": n_obs_all,
            "z_rayleigh": z_rayleigh_all,
            "p_rayleigh": p_rayleigh_all,
            "rayleigh_significant": rayleigh_significant_all,
            "R_ge_0p5": R_ge_05_all,
            "shuffle_threshold_99": shuffle_threshold_99_all,
            "shuffle_significant": shuffle_significant_all,
        })

        hd_summary["IC_bits_per_sec_pynapple"] = mi_df["bits/sec"].values
        hd_summary["IC_bits_per_spike_pynapple"] = mi_df["bits/spike"].values

        hd_summary.to_csv(save_path, index=False)

        return hd_summary


def get_neuro_summary(base_dir: str | Path, kilosort_dir: str | Path, probe_name: str,
                      session_info: dict,
                      interval_pairs,
                      HD_tsd: nap.Tsd,
                      kilosort_info_filename: str,
                      num_of_bins_in_hd: int,
                      num_shuffle: int, *,
                      is_overwrite_shuffle_data: bool,
                      is_return_tsgroup: bool = False,
                      is_return_time_support: bool = False,
                      is_return_hd_tcs: bool = False,
                      is_return_cluster_KSLabel: bool = False,
                      is_return_hd_cells: bool = False,
                      is_return_classical_hd_cells: bool = False,
                      save_path: str | Path = None,
                      ) -> dict:

    probe_dir = Path(kilosort_dir) / f"Probe{probe_name}"
    kilosort_folders = list(probe_dir.glob("kilosort_*"))
    assert len(kilosort_folders) == 1, f"Expected exactly one kilosort folder in {probe_dir}, found {len(kilosort_folders)}"

    kilosort_dir = kilosort_folders[0]


    tsgroup, time_support, hd_tuning_curves, cluster_KSLabel_sort = gen_tuning_curves(
        base_dir=base_dir,
        kilosort_dir=kilosort_dir,
        probe_name=probe_name,
        session_info=session_info,
        interval_pairs=interval_pairs,
        HD_tsd=HD_tsd,
        kilosort_info_filename=kilosort_info_filename,
        num_of_bins_in_hd=num_of_bins_in_hd, )

    hd_summary = shuffle_neuro_data(
        base_dir=base_dir,
        probe_name=probe_name,
        hd_tuning_curves=hd_tuning_curves,
        hd_tsd=HD_tsd,
        time_support=time_support,
        tsgroup=tsgroup,
        num_shuffle=num_shuffle,
        is_overwrite_shuffle_data=is_overwrite_shuffle_data,
        save_path=save_path)

    result: dict = {"hd_summary": hd_summary}
    if is_return_tsgroup:
        result["tsgroup"] = tsgroup
    if is_return_time_support:
        result["time_support"] = time_support
    if is_return_hd_tcs:
        result["hd_tuning_curves"] = hd_tuning_curves
    if is_return_cluster_KSLabel:
        result["cluster_KSLabel"] = cluster_KSLabel_sort
    if is_return_hd_cells:
        result["hd_cells"] = hd_summary[
            (hd_summary["rayleigh_significant"]) |
            (hd_summary["shuffle_significant"])
            ].copy()
    if is_return_classical_hd_cells:
        result["classical_hd_cells"] = hd_summary[
            (hd_summary["shuffle_significant"]) &
            (hd_summary["R"] >= 0.5)
            ].copy()

    return result


def gen_adc_spike_time(session_info: dict, probe: str, base_dir: str, num_of_rec: int) -> tuple[bool, str | Path]:
    probe = probe.upper()

    base_data_dir = session_info['base_path']
    record_nodes: str = session_info['record_nodes']
    recording_name: str = session_info['recording_name']
    experiment_id: str = session_info['experiment_id']
    path_between = f'/{record_nodes}/{experiment_id}/'

    continuous_folder = base_data_dir + path_between + recording_name + '/continuous/'
    kilosort_dir = f"{base_dir}/kilosort/Probe{probe}/kilosort_{num_of_rec}"

    print("Loading probe npy data...")
    probe_name: str = session_info[f'continuous_probe_{probe}_folder']
    probe_continuous_timestamp_file = f'{continuous_folder}/{probe_name}/timestamps.npy'
    probe_continuous_timestamp_data = np.load(probe_continuous_timestamp_file, mmap_mode='r')
    probe_continuous_timestamp_data = probe_continuous_timestamp_data - probe_continuous_timestamp_data[0]
    print(f"Probe{probe} continuous shape: {probe_continuous_timestamp_data.shape}")
    probe_duration = probe_continuous_timestamp_data[-1]
    print(f"Probe{probe} duration: {probe_duration} ")

    spike_times = np.load(f"{kilosort_dir}/spike_times.npy")
    spike_time_adc = probe_continuous_timestamp_data[spike_times]

    file_path = Path(base_dir) / "data" / f"probe{probe}" / "adc_spike_time.npy"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(file_path, spike_time_adc)
    return True, file_path
