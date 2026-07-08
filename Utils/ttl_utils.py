from typing import Any
import numpy as np
import matplotlib.pyplot as plt


def pre_process_signals(signals, *, return_rising_edge: bool = True, return_falling_edge: bool = False,
                        return_signal_pairs: bool = False) -> np.ndarray:
    """
    Pre-process TTL signals to extract rising edges, falling edges, or signal pairs.

    Parameters
    ----------
    signals
    return_rising_edge
    return_falling_edge
    return_signal_pairs

    Returns
    -------

    """

    if (return_rising_edge + return_falling_edge + return_signal_pairs) != 1:
        raise ValueError("Exactly one of the return_* flags must be True.")

    if not (isinstance(signals, (list, np.ndarray))):
        raise TypeError("signals must be a list or np.ndarray")

    signals = np.asarray(signals) if type(signals) != np.ndarray else signals

    if return_signal_pairs and signals.ndim != 2:
        raise ValueError("signals must be a 2D array when return_signal_pairs is True.")

    if signals.ndim == 1:
        pass
    elif signals.ndim == 2:
        if return_falling_edge:
            signals = signals[:, -1]
        if return_signal_pairs:
            pass  # keep as is
        signals = signals[:, 0]
    else:
        raise ValueError(
            f"signal must be 1D or 2D (got {signals.ndim}D array with shape {signals.shape})"
        )

    return signals


def classify_pulses(pulse_pairs, expected_gap=3000, tolerance=1000) -> list[int]:
    """
    Classify consecutive pulse pairs into groups:
      1 = single, 2 = double, 3 = triple.
    Assumes input order is already chronological.
    """
    stage_labels = []
    pair_index = 0
    num_pairs = len(pulse_pairs)

    while pair_index < num_pairs:
        # Start new group with current pair
        group_indices = [pair_index]

        # Look ahead at up to two subsequent gaps
        for next_index in range(pair_index, min(pair_index + 2, num_pairs - 1)):
            gap_between_pairs = pulse_pairs[next_index + 1, 0] - pulse_pairs[next_index, 1]
            if abs(gap_between_pairs - expected_gap) <= tolerance:
                group_indices.append(next_index + 1)
            else:
                break

        group_size = len(group_indices)

        if group_size == 1:
            stage_type = 1
        elif group_size == 2:
            stage_type = 2
        elif group_size == 3:
            stage_type = 3
        else:
            stage_type = -1  # Unexpected case

        # Assign stage type to all pairs in this group
        for idx in group_indices:
            stage_labels.append(stage_type)

        pair_index += group_size

    return stage_labels


def group_intervals(pulse_pairs, recording_range: tuple[int, int], expected_gap=3000, tolerance=1000) -> dict:
    """
    Calls classify_pulses and groups consecutive pairs with the same stage.
    Returns dict: {1: [...], 2: [...], 3: [...], -1: [...]}
    Each interval is (start_of_group, start_of_next_group - 1).
    The last group ends at its own last pair's end.
    """
    stage_labels = classify_pulses(pulse_pairs, expected_gap, tolerance)
    grouped_intervals = {1: [], 2: [], 3: [], -1: []}

    recording_start = recording_range[0]
    recording_end = recording_range[1]

    num_pairs = len(pulse_pairs)
    pair_index = 0
    while pair_index < num_pairs:
        current_stage = stage_labels[pair_index]
        group_start_time = pulse_pairs[pair_index, 0]

        # advance to last pair in this group
        last_in_group = pair_index
        while last_in_group + 1 < num_pairs and stage_labels[last_in_group + 1] == current_stage:
            last_in_group += 1

        # default end is the last pair's end
        if last_in_group + 1 < num_pairs:
            group_end_time = pulse_pairs[last_in_group + 1, 0] - 1  # up to before next group's start
        else:
            group_end_time = pulse_pairs[last_in_group, 1]  # last group ends at its own end

        grouped_intervals[current_stage].append((group_start_time, group_end_time))
        pair_index = last_in_group + 1

    first_stage = min((key for key, value in grouped_intervals.items() if value),
                      key=lambda k: grouped_intervals[k][0][0])

    last_stage = max((key for key, value in grouped_intervals.items() if value),
                     key=lambda k: grouped_intervals[k][-1][-1])

    if grouped_intervals[first_stage][0][0] > recording_start and first_stage != -1:
        grouped_intervals[(first_stage - 1) if first_stage > 1 else 3].insert(
            0, (recording_start, grouped_intervals[first_stage][0][0] - 1))

    if grouped_intervals[last_stage][-1][-1] < recording_end and last_stage != -1:
        grouped_intervals[last_stage].append(
            (grouped_intervals[last_stage][-1][1] + 1, recording_end))

    print(grouped_intervals)

    return grouped_intervals


def intervals_ranges_index_to_frames(pairs: np.ndarray, intervals_by_stage: dict):
    """
    Convert time intervals per stage into index ranges over the pairs list.

    Parameters
    ----------
    pairs : (N,2) int array
        Each row is [start, end] of a pair. Must be in chronological order.
    intervals_by_stage : dict
        Output from group_intervals(), e.g. {1:[(s,e),...], 2:[...], 3:[...], -1:[...]}
        Each (s,e) is a time span: start_of_group .. next_group_start-1 (last group uses its end).

    Returns
    -------
    dict -> stage:int -> list[(start_idx, end_idx)]
        Indices are inclusive, referring to rows of `pairs`.
    """
    starts = pairs[:, 0].astype(np.int64)
    out = {1: [], 2: [], 3: [], -1: []}

    for stage in out.keys():
        intervals = intervals_by_stage.get(stage, [])
        if not intervals:
            continue
        s_arr = np.fromiter((s for s, _ in intervals), dtype=np.int64)
        e_arr = np.fromiter((e for _, e in intervals), dtype=np.int64)

        # leftmost idx with start >= s ; rightmost idx with start <= e
        left = np.searchsorted(starts, s_arr, side='left')
        right = np.searchsorted(starts, e_arr, side='right') - 1

        mask = right >= left
        out[stage] = [(int(l), int(r)) for l, r, m in zip(left, right, mask) if m]

    return out


def filtering_rate(angular_speed: np.ndarray, step: int = 5, limit: int = 200) -> None:
    filter_ratio_list = []

    for i in range(0, 200, step):
        print(f"step: {i}")
        angular_speed_deg_per_s_bool = angular_speed > i
        filter_ratio = angular_speed_deg_per_s_bool.sum() / angular_speed_deg_per_s_bool.size * 100
        print(f"filter_ratio: {filter_ratio}")
        filter_ratio_list.append(filter_ratio)

    plt.plot(np.arange(0, limit, step), filter_ratio_list)


def combine_close_spikes(hd_interval_sorted: dict, rz_values, frames_inbetween_spikes: int = 5,
                         spike_def_threshold: int = 100, is_return_ratio=False) -> dict[Any, Any]:
    count = 0
    total_spike = 0
    combined_hd_interval_sorted = {}

    for stage in hd_interval_sorted.keys():
        combined_hd_interval_sorted[stage] = {}
        for interval in hd_interval_sorted[stage].keys():
            combined_hd_interval_sorted[stage][interval] = []
            for index in range(len(hd_interval_sorted[stage][interval]) - 1):
                time_inbetween = hd_interval_sorted[stage][interval][index + 1][0] - \
                                 hd_interval_sorted[stage][interval][index][1]

                first_peak_end_frame = hd_interval_sorted[stage][interval][index][-1]
                second_peak_start_frame = hd_interval_sorted[stage][interval][index + 1][0]

                min_between_peaks = rz_values[first_peak_end_frame:second_peak_start_frame + 1].min()

                # Comparison check
                comparison1 = time_inbetween > frames_inbetween_spikes
                comparison2 = min_between_peaks > (spike_def_threshold / 5)
                # comparison2 = True

                if comparison1 or comparison2:
                    to_be_extended = hd_interval_sorted[stage][interval][index]
                else:
                    # combined two between peaks
                    to_be_extended = (hd_interval_sorted[stage][interval][index][0],
                                      hd_interval_sorted[stage][interval][index + 1][1])
                    # print(f"Index: {index}: {hd_interval_sorted[stage][interval][index]} + {hd_interval_sorted[stage][interval][index+1]} -> {to_be_extended}")
                    count += 1

                combined_hd_interval_sorted[stage][interval].extend(to_be_extended)
            total_spike += len(hd_interval_sorted[stage].get(interval))

    if is_return_ratio:
        print(f"{count / total_spike * 100:.2f}% spikes combined.")

    return combined_hd_interval_sorted


def distribute_plateau(series: np.ndarray, is_angle: bool = True) -> np.ndarray:
    """
    Replace plateau + jump patterns in a time series with evenly distributed deltas.

    Parameters
    ----------
    series : ndarray
        Input time series (angle in degrees or linear position).
    is_angle : bool, default False
        If True, unwrapping is applied (for angular series in degrees).

    Returns
    -------
    deltas : ndarray
        Per-frame differences with plateau jumps distributed evenly.

    Example
    -------
    angle_series = [1,2,3,4,4,4,4,4,4,16]
    distribute_plateau_deltas(angle_series, is_angle=False)
    # -> [1,2,3,4,6,9,11,13,16]
    """
    values = np.asarray(series, dtype=float)
    print("==========================")
    if is_angle:
        values = np.rad2deg(np.unwrap(np.deg2rad(values)))
    if values.size < 2:
        return np.array([], dtype=float)

    frame_deltas = np.diff(values)
    idx = 0
    while idx < frame_deltas.size:
        if frame_deltas[idx] == 0:
            plateau_start = idx
            while idx < frame_deltas.size and frame_deltas[idx] == 0:
                idx += 1
            plateau_end = idx
            if plateau_end < frame_deltas.size and frame_deltas[plateau_end] != 0:
                start_val = values[plateau_start]
                end_val = values[plateau_end + 1]
                steps = plateau_end - plateau_start + 1
                distributed_data = np.linspace(start_val, end_val, steps, dtype=int)
                print(plateau_start, plateau_end, start_val, end_val, distributed_data)
                print("==========================")
                values[plateau_start:plateau_end + 1] = distributed_data
        else:
            idx += 1

    for i in range(values.size):
        if values[i] == 0 or values[i] == np.nan or values[i] == None:
            print(series[i], values[i], i)
    return values


def straighten_signal(signal: list | np.ndarray, threshold: int = 300, *, return_in_dict=False) -> dict[
                                                                                                       str, np.ndarray] | np.ndarray:
    signal_rising_edge = pre_process_signals(signal)

    # On frames are those with sparse TTL pulses > 300 samples
    diff_bool = np.diff(signal_rising_edge) < threshold
    edges = np.flatnonzero(np.r_[True, diff_bool[1:] != diff_bool[:-1]])

    if return_in_dict:
        # Label runs by looking at the boolean value at each run start
        run_labels = diff_bool[edges[:-1]]
        true_block = signal_rising_edge[edges[:-1]][~run_labels]  # False = long interval, ON frames
        false_block = signal_rising_edge[edges[:-1]][run_labels]  # True = short interval, OFF frames
        result = {
            "off_frames": true_block,
            "on_frames": false_block,
        }
    else:
        result = signal_rising_edge[edges]

    return result


def classify_and_group_TTL_pulses(exposure_pairs: list[list[int]],
                                  *,
                                  short_threshold: int = 5000,
                                  medium_threshold: int = 8500,
                                  long_threshold: int = 13000) -> dict:
    """
    Classify pulses by duration and group them into events.

    Returns:
        dict: Contains 'start', 'broadlets', 'singlets', 'triplets', 'num', 'groups'
    """

    durations = [end - start for start, end in exposure_pairs]

    result = {
        'start': None,
        'broadlets': [],
        'singlets': [],
        'triplets': [],
        'indexes': [],
        'groups': {},
        'num': 0
    }

    indexes = []
    i = 0
    group_num = 0

    while i < len(exposure_pairs):
        dur = durations[i]

        # Check if this is a start indicator (first pulse, ~2000)
        if i == 0 and dur < 3000:
            result['start'] = 0
            result['groups'][group_num] = {
                'type': 'start_indicator',
                'indices': [i],
                'durations': [dur]
            }

            indexes.append(i)
            group_num += 1
            i += 1
            continue

        # Check for triplet pattern: short + medium + long
        if (i + 2 < len(durations) and
                durations[i] < short_threshold <= durations[i + 1] < medium_threshold <= durations[i + 2]):
            result['triplets'].append(group_num)
            result['groups'][group_num] = {
                'type': 'triplet',
                'indices': [i, i + 1, i + 2],
                'durations': [durations[i], durations[i + 1], durations[i + 2]]
            }

            indexes.append(i)
            group_num += 1
            i += 3
            continue

        # Check for broadlet: single long pulse
        if dur > long_threshold:
            result['broadlets'].append(group_num)
            result['groups'][group_num] = {
                'type': 'broadlet',
                'indices': [i],
                'durations': [dur]
            }

            indexes.append(i)
            group_num += 1
            i += 1
            continue

        # Default: singlet (single short pulse)
        result['singlets'].append(group_num)
        result['groups'][group_num] = {
            'type': 'singlet',
            'indices': [i],
            'durations': [dur]
        }

        indexes.append(i)
        group_num += 1
        i += 1

    result['indexes'] = indexes
    result['num'] = group_num

    return result


def classify_and_group_photodiode_pulses(
        exposure_time_pairs,
        *,
        short_threshold=4000,
        long_threshold=14000,
        thresholds=None
):
    """
    Classify each pulse as 'short', 'medium', or 'long'.

    thresholds: optional tuple (short_threshold, long_threshold)
                Example: (100, 200)
                → short  < 100
                → medium 100–199
                → long   >= 200

    Returns a dict:
        {
            "short":  [...indexes...],
            "medium": [...indexes...],
            "long":   [...indexes...],
            "indexes": {pulse_index: category, ...},
            "summary": {"short": N, "medium": N, "long": N}
        }
    """

    # Allow thresholds tuple override
    if thresholds is not None:
        short_threshold, long_threshold = thresholds

    # Compute pulse durations
    pulse_durations = [
        end_time - start_time for start_time, end_time in exposure_time_pairs
    ]

    def classify(duration):
        if duration < short_threshold:
            return "short"
        elif duration < long_threshold:
            return "medium"
        else:
            return "long"

    # Initialize result containers
    result = {
        "short": [],
        "medium": [],
        "long": [],
        "indexes": {},
        "summary": {"short": 0, "medium": 0, "long": 0}
    }

    # Classify each pulse
    for pulse_index, duration in enumerate(pulse_durations):
        category = classify(duration)

        # Add index to corresponding category
        result[category].append(pulse_index)

        # Record category in global index map
        result["indexes"][pulse_index] = category

        # Update counts
        result["summary"][category] += 1

    return result


def group_ttl_pulses_segmented(
        dot_offsets: dict[str, list[int]],
        cycle_index_list: list[int],
        exposure_list: list[list[int]]
) -> dict[str, list[int]]:
    """
    Segment exposure_list by anchor ranges from cycle_index_list for each label in dot_offsets.

    For each label and each index i in dot_offsets[label]:
      - start = cycle_index_list[i]
      - stop  = cycle_index_list[i+1] if exists, else len(exposure_list)
      - segment = exposure_list[start:stop]
        (e.g., if start=8, stop=11 → [[8,9],[9,10],[10,11]])

    If cycle_index_list contains invalid or out-of-bounds values, this will raise,
    so such inconsistencies can be caught early.
    """
    grouped_ttl: dict[str, list[int]] = {key: [] for key in dot_offsets}
    n_pairs = len(exposure_list)
    for label, indices in dot_offsets.items():
        segments = []

        for idx in indices:
            start = cycle_index_list[idx]

            stop = cycle_index_list[idx + 1] if idx + 1 < len(cycle_index_list) else n_pairs

            if start < 0 or stop > n_pairs or stop <= start:
                raise ValueError(
                    f"Invalid cycle index range: start={start}, stop={stop}, total={n_pairs}"
                )

            seg = exposure_list[start:stop]
            segments.append(seg)

        grouped_ttl[label] = segments

    return grouped_ttl


def fill_short_gaps(mask, min_gap=600):
    mask = np.asarray(mask, bool)
    out = mask.copy()

    # indices where mask is True
    idx = np.flatnonzero(out)
    if idx.size < 2:
        return out  # nothing to bridge

    # distance between consecutive True indices minus 1 = gap length
    gaps = np.diff(idx) - 1

    # positions where gap length is in (0, max_gap]
    crack_idxs = np.where((gaps > 0) & (gaps <= min_gap))[0]

    # fill each of these short gaps
    for ci in crack_idxs:
        start = idx[ci] + 1  # first 0 after a 1
        end = idx[ci + 1]  # next 1 (exclusive)
        out[start:end] = True

    return out
