import numpy as np

from Utils.ttl_utils import fill_short_gaps


def Sync(spike_times, event_times, durations=np.array([-1, 1])):
    spike_times = np.asarray(spike_times).reshape(-1)
    event_times = np.asarray(event_times).reshape(-1)
    sample_window = np.asarray(durations)
    synchronized_spike_times = []
    event_indices = []
    for event_time_index in range(len(event_times)):
        relative_spike_times = spike_times - event_times[event_time_index]
        spikes_in_window = (relative_spike_times >= sample_window[0]) & (relative_spike_times <= sample_window[1])
        synchronized_spike_times.extend(relative_spike_times[spikes_in_window])
        event_indices.extend([event_time_index + 1] * int(np.sum(spikes_in_window)))
    return np.asarray(synchronized_spike_times), np.asarray(event_indices)

def SyncHist(synchronized_spike_times, event_indices, durations=np.array([-1, 1]), number_of_bins=100, mode="mean"):
    synchronized_spike_times = np.asarray(synchronized_spike_times).reshape(-1)
    event_indices = np.asarray(event_indices).reshape(-1)
    if len(synchronized_spike_times) == 0:
        return np.array([]), np.array([])
    bin_width = (durations[1] - durations[0]) / number_of_bins
    bin_centers = np.arange(durations[0], durations[1], bin_width) + bin_width / 2
    valid_time_mask = (synchronized_spike_times >= durations[0]) & (synchronized_spike_times < durations[1])
    binned_time_indices = np.floor((synchronized_spike_times[valid_time_mask] - durations[0]) / (durations[1] - durations[0]) * number_of_bins).astype(int)
    spike_counts = np.bincount(binned_time_indices, minlength=number_of_bins)[:number_of_bins]
    trial_count = np.max(event_indices)
    if mode == "mean":
        spike_histogram = spike_counts / (trial_count * bin_width)
    elif mode == "sum":
        spike_histogram = spike_counts
    else:
        spike_histogram = spike_counts / trial_count
    return spike_histogram, bin_centers

def Threshold(time_value_pairs, comparison_operator, threshold_value, **kwargs):
    minimum_duration = kwargs.get("min", 0)
    maximum_interruption = kwargs.get("max", 0)
    time_value_pairs = np.asarray(time_value_pairs)
    time_values = time_value_pairs[:, 0]
    signal_values = time_value_pairs[:, 1]
    if comparison_operator == ">":
        threshold_mask = signal_values > threshold_value
    elif comparison_operator == ">=":
        threshold_mask = signal_values >= threshold_value
    elif comparison_operator == "<=":
        threshold_mask = signal_values <= threshold_value
    else:
        threshold_mask = signal_values < threshold_value
    threshold_transitions = np.diff(threshold_mask.astype(int))
    period_start_indices = np.where(threshold_transitions == 1)[0]
    period_end_indices = np.where(threshold_transitions == -1)[0]
    if threshold_mask[0]:
        period_start_indices = np.concatenate([[0], period_start_indices])
    if threshold_mask[-1]:
        period_end_indices = np.concatenate([period_end_indices, [len(threshold_mask) - 1]])
    if len(period_start_indices) > 1 and len(period_end_indices) > 0:
        interruption_durations = time_values[period_start_indices[1:]] - time_values[period_end_indices[:-1]]
        ignored_interruptions = np.where(interruption_durations <= maximum_interruption)[0]
        if len(ignored_interruptions) != 0:
            period_start_indices = np.delete(period_start_indices, ignored_interruptions + 1)
            period_end_indices = np.delete(period_end_indices, ignored_interruptions)
    periods = np.column_stack([time_values[period_start_indices], time_values[period_end_indices]])
    period_durations = periods[:, 1] - periods[:, 0]
    keep_period_mask = period_durations >= minimum_duration
    period_start_indices = period_start_indices[keep_period_mask]
    period_end_indices = period_end_indices[keep_period_mask]
    periods = periods[keep_period_mask, :]
    threshold_in_mask = np.zeros(len(threshold_mask), dtype=bool)
    for period_index in range(len(period_start_indices)):
        threshold_in_mask[period_start_indices[period_index]:period_end_indices[period_index] + 1] = True
    return periods, threshold_in_mask

def interp1(source_points, source_values, query_points):
    source_points = np.asarray(source_points).reshape(-1)
    source_values = np.asarray(source_values).reshape(-1)
    query_points = np.asarray(query_points)
    query_values = np.interp(query_points, source_points, source_values)
    left_extrapolation_mask = query_points < source_points[0]
    right_extrapolation_mask = query_points > source_points[-1]
    if np.any(left_extrapolation_mask):
        slope = (source_values[1] - source_values[0]) / (source_points[1] - source_points[0])
        query_values[left_extrapolation_mask] = source_values[0] + slope * (query_points[left_extrapolation_mask] - source_points[0])
    if np.any(right_extrapolation_mask):
        slope = (source_values[-1] - source_values[-2]) / (source_points[-1] - source_points[-2])
        query_values[right_extrapolation_mask] = source_values[-1] + slope * (query_points[right_extrapolation_mask] - source_points[-1])
    return query_values

def gauss_2d_model(parameters, x_coordinates, y_coordinates):
    gaussian_parameters = parameters
    return gaussian_parameters[0] * np.exp(-((((x_coordinates - gaussian_parameters[1]) * np.cos(gaussian_parameters[5]) + (y_coordinates - gaussian_parameters[3]) * np.sin(gaussian_parameters[5])) ** 2) / (2 * gaussian_parameters[2] ** 2) + ((-(x_coordinates - gaussian_parameters[1]) * np.sin(gaussian_parameters[5]) + (y_coordinates - gaussian_parameters[3]) * np.cos(gaussian_parameters[5])) ** 2) / (2 * (gaussian_parameters[2] * gaussian_parameters[6]) ** 2))) + gaussian_parameters[4]
