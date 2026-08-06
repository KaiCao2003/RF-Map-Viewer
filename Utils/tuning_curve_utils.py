import json
from pathlib import Path

import numpy as np
import pandas as pd
import pynapple as nap
from tqdm import tqdm



def get_exposure_timestamps(session_info: dict, camera_input_channel: int,
                            camera_ttl_threshold: int | float, *,
                            camera_ttl_active_high: bool = True) -> tuple[np.ndarray, float, dict]:
    continuous_folder = (
        Path(session_info["base_path"])
        / session_info["record_nodes"]
        / session_info["experiment_id"]
        / session_info["recording_name"]
        / "continuous"
    )
    ADC_name = session_info["continuous_ADC_folder"]
    ADC_input_channel_number = int(session_info["ADC_input_channel"])
    ADC_folder = continuous_folder / ADC_name
    timestamp_path = ADC_folder / "timestamps.npy"
    continuous_path = ADC_folder / "continuous.dat"

    ADC_continuous_timestamp_data_raw = np.load(timestamp_path, mmap_mode="r")
    assert ADC_continuous_timestamp_data_raw.ndim == 1
    num_ADC_samples = len(ADC_continuous_timestamp_data_raw)
    timestamp_dtype = ADC_continuous_timestamp_data_raw.dtype
    timestamp_offset = int(ADC_continuous_timestamp_data_raw.offset)
    ADC_time_origin_s = float(ADC_continuous_timestamp_data_raw[0])
    ADC_continuous_timestamp_data_raw._mmap.close()
    del ADC_continuous_timestamp_data_raw

    signal_dtype = np.dtype(np.int16)
    assert 0 <= camera_input_channel < ADC_input_channel_number
    assert continuous_path.stat().st_size == (
        num_ADC_samples * ADC_input_channel_number * signal_dtype.itemsize
    ), "ADC continuous.dat size does not match timestamps/channels."

    rise_time_parts = []
    fall_time_parts = []
    previous_active = False
    first_rise_index = None
    chunk_size = 1_000_000

    for sample_start in range(0, num_ADC_samples, chunk_size):
        sample_count = min(chunk_size, num_ADC_samples - sample_start)
        signal_map = np.memmap(
            continuous_path,
            dtype=signal_dtype,
            mode="r",
            offset=sample_start * ADC_input_channel_number * signal_dtype.itemsize,
            shape=(sample_count, ADC_input_channel_number),
        )
        time_map = np.memmap(
            timestamp_path,
            dtype=timestamp_dtype,
            mode="r",
            offset=timestamp_offset + sample_start * timestamp_dtype.itemsize,
            shape=sample_count,
        )

        camera_data = signal_map[:, camera_input_channel]
        camera_active = (
            camera_data >= camera_ttl_threshold
            if camera_ttl_active_high
            else camera_data < camera_ttl_threshold
        )
        state = np.empty(camera_active.size + 1, dtype=bool)
        state[0] = previous_active
        state[1:] = camera_active
        changes = np.flatnonzero(state[1:] != state[:-1])

        if changes.size:
            new_state = camera_active[changes]
            rise_local = changes[new_state]
            fall_local = changes[~new_state]
            if rise_local.size:
                if first_rise_index is None:
                    first_rise_index = sample_start + int(rise_local[0])
                rise_time_parts.append(np.asarray(time_map[rise_local], dtype=float))
            if fall_local.size:
                fall_time_parts.append(np.asarray(time_map[fall_local], dtype=float))

        previous_active = bool(camera_active[-1])
        del camera_data, camera_active, state
        time_map._mmap.close()
        signal_map._mmap.close()

    assert not previous_active, "Motive TTL pulse reaches the end of the ADC stream."
    assert first_rise_index is not None and first_rise_index > 0, (
        "Motive TTL pulse starts at the ADC boundary."
    )
    assert rise_time_parts and fall_time_parts, "No complete Motive TTL pulse was detected."
    rise_times = np.concatenate(rise_time_parts)
    fall_times = np.concatenate(fall_time_parts)
    assert rise_times.size == fall_times.size and np.all(rise_times < fall_times), (
        "Could not pair Motive TTL rising/falling edges."
    )

    exposure_timestamps = (rise_times + fall_times) / 2 - ADC_time_origin_s
    exposure_periods = np.diff(exposure_timestamps)
    assert exposure_periods.size, "At least two Motive TTL pulses are required."
    median_period_s = float(np.median(exposure_periods))
    pulse_steps = np.rint(exposure_periods / median_period_s).astype(int)
    assert np.all(pulse_steps == 1), "Internal Motive TTL pulse(s) are missing or duplicated."

    ttl_qc = {
        "ttl_pulse_count": int(len(exposure_timestamps)),
        "first_exposure_s": float(exposure_timestamps[0]),
        "last_exposure_s": float(exposure_timestamps[-1]),
        "median_period_s": median_period_s,
        "measured_rate_hz": float(1 / np.mean(exposure_periods)),
        "camera_input_channel": int(camera_input_channel),
        "camera_ttl_threshold": float(camera_ttl_threshold),
        "camera_ttl_active_high": bool(camera_ttl_active_high),
    }
    return exposure_timestamps, ADC_time_origin_s, ttl_qc


def mean_resultant_length(angles_deg: np.ndarray) -> float:
    angles_deg = np.asarray(angles_deg, dtype=float)
    angles_deg = angles_deg[np.isfinite(angles_deg)]
    if not angles_deg.size:
        return np.nan

    mean_vector = np.mean(np.exp(1j * np.deg2rad(angles_deg)))
    return float(np.abs(mean_vector))


def rayleigh_test(spike_counts: np.ndarray, occupancy_time_s: np.ndarray,
                  angle_centers_deg: np.ndarray) -> tuple[float, float]:
    spike_counts = np.asarray(spike_counts, dtype=float)
    occupancy_time_s = np.asarray(occupancy_time_s, dtype=float)
    angle_centers_deg = np.asarray(angle_centers_deg, dtype=float)
    valid = (
        np.isfinite(spike_counts)
        & np.isfinite(occupancy_time_s)
        & np.isfinite(angle_centers_deg)
        & (occupancy_time_s > 0)
    )
    spike_counts = spike_counts[valid]
    occupancy_time_s = occupancy_time_s[valid]
    angle_centers_deg = angle_centers_deg[valid]
    total_spikes = float(np.sum(spike_counts))
    if total_spikes == 0 or len(spike_counts) < 3:
        return np.nan, np.nan

    angles_rad = np.deg2rad(angle_centers_deg)
    design = np.column_stack((np.cos(angles_rad), np.sin(angles_rad)))
    occupancy_weights = occupancy_time_s / np.sum(occupancy_time_s)
    design_mean = occupancy_weights @ design
    centered_design = design - design_mean

    # Score test for cos/sin modulation in a Poisson model with log occupancy
    # as offset. With uniform occupancy this reduces to the Rayleigh score test.
    score = spike_counts @ centered_design
    information = total_spikes * (
        centered_design.T @ (centered_design * occupancy_weights[:, None])
    )
    if np.linalg.matrix_rank(information) < 2:
        return np.nan, np.nan

    rayleigh_score = float(score @ np.linalg.solve(information, score))
    rayleigh_p = float(np.exp(-rayleigh_score / 2))
    return rayleigh_score, rayleigh_p


def _json_float(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def tuning_curve(base_dir, kilosort_dir, probe_name, session_info, interval_pairs, HD_tsd,
                 adc_time_origin_s, num_of_bins_in_hd, num_shuffle, shuffle_seed, *,
                 is_save: bool = False, save_path: str | Path | None = None,
                 metadata: dict | None = None) -> dict:
    from Utils.kilosort_utils import (
        _compress_times_to_epoch_clock,
        _compute_shuffle_r_numba,
        _compute_shuffle_r_numpy,
        _flatten_feature_segments,
        convert_time_list_to_nap_tsd,
    )

    base_dir = Path(base_dir)
    kilosort_dir = Path(kilosort_dir)
    continuous_folder = (
        Path(session_info["base_path"])
        / session_info["record_nodes"]
        / session_info["experiment_id"]
        / session_info["recording_name"]
        / "continuous"
    )
    probe_continuous_timestamps_dir = (
        continuous_folder
        / session_info[f"continuous_probe_{probe_name}_folder"]
        / "timestamps.npy"
    )

    cluster_KSLabel = pd.read_csv(kilosort_dir / "cluster_KSLabel.tsv", sep="\t")
    good_unit_ids = cluster_KSLabel.loc[
        cluster_KSLabel["KSLabel"].astype(str).str.lower() == "good",
        "cluster_id",
    ].astype(int).to_numpy()

    spike_samples = np.load(
        kilosort_dir / "spike_times.npy", mmap_mode="r"
    ).reshape(-1)
    spike_clusters = np.load(
        kilosort_dir / "spike_clusters.npy", mmap_mode="r"
    ).reshape(-1)
    probe_continuous_timestamps = np.load(probe_continuous_timestamps_dir, mmap_mode="r")
    assert spike_samples.shape == spike_clusters.shape

    good_spike_mask = np.isin(spike_clusters, good_unit_ids)
    good_spike_samples = np.asarray(spike_samples[good_spike_mask], dtype=np.int64)
    good_spike_clusters = np.asarray(spike_clusters[good_spike_mask], dtype=np.int64)
    del good_spike_mask, spike_samples, spike_clusters
    assert not good_spike_samples.size or (
        good_spike_samples.min() >= 0
        and good_spike_samples.max() < len(probe_continuous_timestamps)
    ), "Kilosort spike sample is outside probe timestamps."

    good_spike_times = (
        np.asarray(probe_continuous_timestamps[good_spike_samples], dtype=float)
        - float(adc_time_origin_s)
    )
    del good_spike_samples, probe_continuous_timestamps
    cluster_order = np.argsort(good_spike_clusters, kind="stable")
    sorted_clusters = good_spike_clusters[cluster_order]
    sorted_spike_times = good_spike_times[cluster_order]
    del cluster_order, good_spike_clusters, good_spike_times
    cluster_ids, cluster_starts, cluster_counts = np.unique(
        sorted_clusters,
        return_index=True,
        return_counts=True,
    )
    spikes_dict = {
        int(cluster_id): nap.Ts(
            t=sorted_spike_times[start:start + count],
            time_units="s",
        )
        for cluster_id, start, count in zip(cluster_ids, cluster_starts, cluster_counts)
    }
    del sorted_clusters, sorted_spike_times
    tsgroup = nap.TsGroup(spikes_dict)
    time_support = convert_time_list_to_nap_tsd(interval_pairs, return_in_list=False)

    hd_spike_counts = nap.compute_tuning_curves(
        data=tsgroup,
        features=HD_tsd,
        bins=num_of_bins_in_hd,
        epochs=time_support,
        range=(0, 360),
        return_counts=True,
    )
    angle_dim = [dim for dim in hd_spike_counts.dims if dim != "unit"][0]
    unit_ids = hd_spike_counts.coords["unit"].values.astype(int)
    angle_centers_deg = hd_spike_counts.coords[angle_dim].values.astype(float)
    angle_bin_edges_deg = np.asarray(hd_spike_counts.attrs["bin_edges"][0], dtype=float)
    occupancy_samples = np.asarray(hd_spike_counts.attrs["occupancy"], dtype=float).reshape(-1)
    feature_fs_hz = float(hd_spike_counts.attrs["fs"])
    assert occupancy_samples.size == num_of_bins_in_hd
    assert np.all(np.isfinite(occupancy_samples) & (occupancy_samples >= 0))
    assert np.allclose(occupancy_samples, np.rint(occupancy_samples))
    assert np.isfinite(feature_fs_hz) and feature_fs_hz > 0
    occupancy_time_s = occupancy_samples / feature_fs_hz
    spike_counts = hd_spike_counts.values.astype(int)
    firing_rates = np.full(spike_counts.shape, np.nan, dtype=float)
    np.divide(
        spike_counts,
        occupancy_time_s,
        out=firing_rates,
        where=occupancy_time_s > 0,
    )

    ep_starts = np.asarray(time_support.start, dtype=float)
    ep_ends = np.asarray(time_support.end, dtype=float)
    ep_lengths = ep_ends - ep_starts
    cum_lengths = np.concatenate([[0.0], np.cumsum(ep_lengths)])
    total_valid_len = float(cum_lengths[-1])
    feature_times, feature_values, feature_segment_starts, feature_segment_ends = _flatten_feature_segments(
        HD_tsd,
        ep_starts,
        ep_ends,
    )
    angles_rad = np.deg2rad(angle_centers_deg)
    cos_angles = np.cos(angles_rad)
    sin_angles = np.sin(angles_rad)
    shuffle_occupancy_samples = occupancy_samples.copy()
    shuffle_occupancy_samples[shuffle_occupancy_samples == 0] = 1
    compute_shuffle_r = _compute_shuffle_r_numba or _compute_shuffle_r_numpy
    rng = np.random.default_rng(shuffle_seed)

    units = []
    with tqdm(total=len(unit_ids), desc="Classifying HD cells", unit="unit") as pbar:
        for unit_index, unit_id in enumerate(unit_ids):
            unit_rates = firing_rates[unit_index]
            valid_rates = np.isfinite(unit_rates)
            rate_sum = float(np.sum(unit_rates[valid_rates]))
            rate_mvl = np.nan
            if rate_sum > 0:
                rate_mvl = float(np.abs(
                    np.sum(unit_rates[valid_rates] * np.exp(1j * angles_rad[valid_rates]))
                ) / rate_sum)

            unit_spikes = tsgroup[int(unit_id)].restrict(time_support)
            spike_angles = unit_spikes.value_from(HD_tsd).values
            spike_angle_mrl = mean_resultant_length(spike_angles)
            rayleigh_score, rayleigh_p = rayleigh_test(
                spike_counts[unit_index],
                occupancy_time_s,
                angle_centers_deg,
            )
            rayleigh_significant = bool(rayleigh_p < 0.05) if np.isfinite(rayleigh_p) else None

            compressed_times = _compress_times_to_epoch_clock(
                unit_spikes.index.to_numpy(),
                ep_starts,
                ep_ends,
                cum_lengths,
            )
            shifts = rng.uniform(0, total_valid_len, size=num_shuffle)
            shuffle_R = compute_shuffle_r(
                compressed_times,
                shifts,
                total_valid_len,
                ep_starts,
                cum_lengths,
                feature_times,
                feature_values,
                feature_segment_starts,
                feature_segment_ends,
                angle_bin_edges_deg,
                shuffle_occupancy_samples,
                cos_angles,
                sin_angles,
            )
            finite_shuffle_R = shuffle_R[np.isfinite(shuffle_R)]
            if num_shuffle:
                assert finite_shuffle_R.size == num_shuffle, (
                    f"Shuffle failed for unit {unit_id}: "
                    f"{finite_shuffle_R.size}/{num_shuffle} finite values."
                )
            shuffle_p = np.nan
            if np.isfinite(rate_mvl) and finite_shuffle_R.size:
                shuffle_p = (
                    1 + np.count_nonzero(finite_shuffle_R >= rate_mvl)
                ) / (len(finite_shuffle_R) + 1)
            shuffle_significant = bool(shuffle_p <= 0.01) if np.isfinite(shuffle_p) else None

            hd_class = None
            if rayleigh_significant is not None and shuffle_significant is not None:
                hd_class = (
                    2 if rayleigh_significant and shuffle_significant
                    else 1 if rayleigh_significant or shuffle_significant
                    else 0
                )

            unit_firing_rates = [
                float(value) if np.isfinite(value) else None
                for value in unit_rates
            ]
            units.append({
                "unit_id": int(unit_id),
                "spike_counts": spike_counts[unit_index].astype(int).tolist(),
                "firing_rate_hz": unit_firing_rates,
                "hd_class": hd_class,
                "rate_mvl": _json_float(rate_mvl),
                "spike_angle_mrl": _json_float(spike_angle_mrl),
                "rayleigh_score": _json_float(rayleigh_score),
                "rayleigh_p": _json_float(rayleigh_p),
                "rayleigh_significant": rayleigh_significant,
                "shuffle_p": _json_float(shuffle_p),
                "shuffle_significant": shuffle_significant,
            })
            pbar.update(1)

    output_metadata = {
        "session": base_dir.name,
        "probe": probe_name,
        "kilosort_dir": str(kilosort_dir),
        "timebase": "open_ephys_adc_t0_relative_seconds",
        "adc_time_origin_raw_s": float(adc_time_origin_s),
        "timestamp_reference": "motive_exposure_ttl_midpoint",
        "angle_convention_note": (
            "head_direction_deg must be calibrated to GUI convention: 0 degrees up, "
            "positive counter-clockwise. This notebook only applies modulo 360."
        ),
        "num_angle_bins": int(num_of_bins_in_hd),
        "feature_fs_hz": feature_fs_hz,
        "epoch_intervals_s": np.asarray(interval_pairs, dtype=float).tolist(),
        "classification": {
            "method": "occupancy_adjusted_rayleigh_or_circular_shift_v1",
            "class_0": "neither significant",
            "class_1": "exactly one significant",
            "class_2": "rayleigh and shuffle significant",
            "class_null": "one or both significance tests unavailable",
            "rayleigh_alpha": 0.05,
            "rayleigh_test": (
                "Poisson cos/sin score test with log occupancy-time offset; "
                "chi-square with 2 degrees of freedom"
            ),
            "shuffle_alpha": 0.01,
            "num_shuffle": int(num_shuffle),
            "shuffle_seed": int(shuffle_seed),
        },
    }
    if metadata:
        output_metadata.update(metadata)

    tuning_curves = {
        "schema_version": 2,
        "metadata": output_metadata,
        "angle_bin_edges_deg": angle_bin_edges_deg.tolist(),
        "occupancy_samples": occupancy_samples.astype(int).tolist(),
        "occupancy_time_s": occupancy_time_s.tolist(),
        "units": units,
    }

    if is_save:
        if save_path is None:
            save_path = base_dir / "data" / "tuning_curves" / f"Probe{probe_name}" / "tuning_curves.json"
        else:
            save_path = Path(save_path)

        serialized_tuning_curves = json.dumps(tuning_curves, indent=2, allow_nan=False)
        saved_tuning_curves = json.loads(serialized_tuning_curves)
        assert saved_tuning_curves["schema_version"] == 2
        assert len(saved_tuning_curves["angle_bin_edges_deg"]) == num_of_bins_in_hd + 1
        assert len(saved_tuning_curves["occupancy_samples"]) == num_of_bins_in_hd
        assert len(saved_tuning_curves["occupancy_time_s"]) == num_of_bins_in_hd
        assert len(saved_tuning_curves["units"]) == len(unit_ids)
        assert all(len(unit["spike_counts"]) == num_of_bins_in_hd
                   for unit in saved_tuning_curves["units"])
        assert all(len(unit["firing_rate_hz"]) == num_of_bins_in_hd
                   for unit in saved_tuning_curves["units"])

        save_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = save_path.with_suffix(save_path.suffix + ".tmp")
        with open(temporary_path, "w") as file:
            file.write(serialized_tuning_curves)
        temporary_path.replace(save_path)

    return tuning_curves


