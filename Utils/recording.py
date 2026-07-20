# Import necessary libraries
from __future__ import annotations

import numpy as np
from typing import Union
from tqdm import tqdm

try:
    import torch
except ModuleNotFoundError:
    torch = None


# Note: The original file had a type hint for Literal, but it was not used.
# from typing import Literal


def detect_recording(values: np.ndarray,
                     threshold: int | float = 20000,
                     min_len: int = 200,
                     is_inverted: bool = True) -> np.ndarray:
    """
    Detects continuous periods of recording based on a signal falling below a threshold.

    This function identifies segments in the input `values` where the signal is
    continuously below the specified `threshold` for at least `min_len` samples.
    It returns the gaps *between* these recording periods.

    Args:
        values (np.ndarray): The input signal array.
        threshold (int, optional): The value below which the signal is considered to be "on". 
                                   Defaults to 20000.
        min_len (int, optional): The minimum number of consecutive samples below the threshold
                                 to be considered a valid recording segment. Defaults to 200.

    Returns:
        np.ndarray: A 2D numpy array of shape (N, 2) where each row represents the
                    start and end indices of a gap between recording sessions.
                    RETURNS INDEX OF THE CAMERA DATA

    Parameters
    ----------
    values
    threshold
    min_len
    is_inverted
    """
    # Create a boolean mask where the signal is below the threshold
    m = values < threshold if is_inverted else values > threshold

    # Pad the mask to correctly identify start and end points at the boundaries
    padded = np.concatenate(([False], m, [False]))

    # Find the start and end indices of the recording periods
    starts = np.flatnonzero(~padded[:-1] & padded[1:])
    ends = np.flatnonzero(padded[:-1] & ~padded[1:])

    # Calculate the length of each recording period
    lens = ends - starts

    # Filter out periods that are shorter than the minimum length
    keep = lens >= min_len
    starts = starts[keep]
    ends = ends[keep]

    # Stack the start and end indices into a 2D array
    gaps = np.column_stack((starts, ends)).astype(np.int64)

    # Calculate the gaps between the recording periods
    between = np.column_stack((gaps[:-1, 1], gaps[1:, 0]))

    return between


def detect_exposure_time(time: np.ndarray,
                         signal: np.ndarray,
                         threshold: int | float = 20000,
                         time_interval: bool = True,
                         is_inverted: bool = True, *,
                         detection_target_label: str = None) -> np.ndarray:
    """
    Detects exposure intervals from a signal based on a threshold.

    This function can return either the indices of the intervals or the
    corresponding time intervals.

    Args:
        time (np.ndarray): An array of timestamps corresponding to the signal.
        signal (np.ndarray): The input signal array.
        threshold (float, optional): The threshold to determine exposure. Defaults to 20000.
        time_interval (bool, optional): If True, returns time intervals. Otherwise, returns
                                        index intervals. Defaults to True.

        is_inverted (bool, optional): If True, return HIGH values as exposure time. Otherwise, return LOW values as exposure time.


    Returns:
        np.ndarray: A 2D array of shape [N, 2] containing either time or index intervals.
                    If `time_interval` is True, returns [start_time, end_time].
                    If `time_interval` is False, returns [start_idx, end_idx).

    Parameters
    ----------
    signal
    time
    threshold
    time_interval
    is_inverted
    """
    detection_target_label = "" if detection_target_label is None else f"{detection_target_label} "


    # Create a boolean mask for when the signal is at or above the threshold
    with tqdm(total=6 if time_interval else 5, desc=f"Processing {detection_target_label}files", unit="%",
              bar_format="{l_bar}{bar}| {n}/{total} [{percentage:3.0f}%]") as pbar:

        m = signal >= threshold if is_inverted else signal < threshold
        pbar.update(1)

        # Pad the mask to handle intervals at the beginning or end of the signal
        padded = np.concatenate(([False], m, [False]))
        pbar.update(1)

        # Find start indices where the signal crosses the threshold upwards
        starts = np.flatnonzero(~padded[:-1] & padded[1:])
        pbar.update(1)

        # Find end indices where the signal crosses the threshold downwards
        ends = np.flatnonzero(padded[:-1] & ~padded[1:])
        pbar.update(1)

        # Stack start and end indices into a 2D array
        idx_intervals = np.column_stack([starts, ends]).astype(np.int64)
        pbar.update(1)


        if starts.size == 0:
            print(f"{detection_target_label}No recording detected based on the provided signal and threshold.")
            return None  # Return an empty array if no recording is detected

        print(f"{detection_target_label}recording start frame (index): {starts[0]}")

        if time_interval:
            # Convert index intervals to time intervals
            time_intervals = np.column_stack([time[starts], time[ends - 1]])
            pbar.update(1)

            return time_intervals
        else:
            return idx_intervals


def detect_exposure_time_torch(
        device: torch.device,
        time: Union[torch.Tensor, "np.ndarray"],
        signal: Union[torch.Tensor, "np.ndarray"],
        threshold: float = 20000.0,
        time_interval: bool = True,
        is_inverted: bool = True
) -> torch.Tensor:
    """
    Finds intervals where a signal is at or above a threshold, using PyTorch for computation.

    This function is a PyTorch-based equivalent of `detect_exposure_time`.
    It can leverage a specified device (e.g., a GPU) for faster computation.

    Args:
        device (torch.device): The device to perform computations on (e.g., 'cpu' or 'cuda').
        time (Union[torch.Tensor, "np.ndarray"]): A 1D tensor or numpy array of timestamps.
        signal (Union[torch.Tensor, "np.ndarray"]): A 1D tensor or numpy array of signal values.
        threshold (float, optional): The signal threshold. Defaults to 20000.0.
        time_interval (bool, optional): If True, returns time intervals; otherwise, returns
                                        index intervals. Defaults to True.

    Returns:
        torch.Tensor: A 2D tensor of shape [N, 2]. If `time_interval` is True, it returns SAMPLE NUMBER, and it contains
                      [start_time, end_time] pairs. Otherwise, it returns INDEX , and it contains
                      [start_idx, end_idx) pairs. The tensor is returned on the CPU.
    """
    if torch is None:
        raise ModuleNotFoundError("torch is required to use detect_exposure_time_torch.")

    # Ensure inputs are PyTorch tensors on the specified device

    t = torch.as_tensor(time, device=device)
    s = torch.as_tensor(signal, device=device)

    # Create a boolean mask for the signal being at or above the threshold
    m = (s >= threshold) if is_inverted else (s <= threshold)

    # Convert the boolean mask to int8 for difference calculation
    mi = m.to(torch.int8)

    # Pad with zeros to detect edges at the boundaries of the signal
    mi_pad = torch.cat((mi.new_zeros(1), mi, mi.new_zeros(1)))

    # Compute the difference to find where the signal crosses the threshold
    d = mi_pad[1:] - mi_pad[:-1]

    # Find the indices of starts (1) and ends (-1) of exposure periods
    starts = torch.nonzero(d == 1, as_tuple=False).flatten()
    ends = torch.nonzero(d == -1, as_tuple=False).flatten()

    if time_interval:
        # If time intervals are requested, select the corresponding times
        # Note: `ends - 1` makes the time interval inclusive of the end time.
        out = torch.stack((t.index_select(0, starts),
                           t.index_select(0, ends - 1)), dim=1)
        return out.cpu()  # Return the result on the CPU
    else:
        # Otherwise, return the index intervals
        idx = torch.stack((starts.to(torch.int64), ends.to(torch.int64)), dim=1)
        return idx.cpu()  # Return the result on the CPU


def fill_nan_with_mean(arr: np.ndarray) -> np.ndarray:
    """
    Replace NaN values in a NumPy array with the mean of their nearest
    previous and next non-NaN values (linear interpolation).
    """
    arr = np.asarray(arr, dtype=float)  # Ensure float type for NaN support
    nans = np.isnan(arr)
    if not np.any(nans):
        return arr  # No NaNs — return as-is

    # Indices of valid and NaN entries
    x = np.arange(len(arr))
    valid = ~nans

    # Interpolate linearly
    arr[nans] = np.interp(x[nans], x[valid], arr[valid])
    return arr


def scale_cycles(data, scale_factor=250):
    """
    Scale all numeric values in the cycle data by dividing by scale_factor and rounding to int.

    Args:
        data: Dictionary with cycle information
        scale_factor: Factor to divide by (default 100)

    Returns:
        Dictionary with same structure but scaled values
    """
    result = {}

    for key, value in data.items():
        result[key] = {
            'full': [[int(round(start / scale_factor)), int(round(end / scale_factor))]
                     for start, end in value['full']],
            'sub': [[[int(round(sub_start / scale_factor)), int(round(sub_end / scale_factor))]
                     for sub_start, sub_end in cycle_subs]
                    for cycle_subs in value['sub']]
        }

    return result


def reorder_subcycles(data):
    """
    Reorder sub-cycles by moving the long cycle from previous cycle to current cycle.
    Also redefines the full cycle ranges based on new sub-cycle arrangements.

    Args:
        data: Dictionary with 'full' and 'sub' cycle information

    Returns:
        Dictionary with reordered sub-cycles and updated full ranges
    """
    result = {}

    for key, value in data.items():
        full_cycles = value['full']
        sub_cycles = value['sub']

        new_full = []
        new_sub = []

        for i in range(len(sub_cycles)):
            current_subs = sub_cycles[i]

            # Handle the case where current cycle does NOT have 3 sub-cycles
            if len(current_subs) != 3:
                cycle_start = current_subs[0][0]
                cycle_end = current_subs[-1][1]

                # Last 550 frames as long cycle
                long_start = cycle_end - 549
                long_end = cycle_end

                # 33 frames before long as median cycle
                median_end = long_start - 1
                median_start = median_end - 32

                # Rest as small cycle
                small_start = cycle_start
                small_end = median_start - 1

                current_subs = [
                    [small_start, small_end],
                    [median_start, median_end],
                    [long_start, long_end]
                ]

            # Now reorder: take long from previous, then short and median from current
            if i == 0:
                # First cycle: keep as is [s1, m1, l1]
                reordered = current_subs
            else:
                # Get long cycle from previous cycle (last sub-cycle)
                prev_long = new_sub[i - 1][-1]

                # Current cycle: [s_current, m_current, l_current]
                # Reorder to: [l_prev, s_current, m_current]
                reordered = [
                    prev_long,
                    current_subs[0],  # short
                    current_subs[1]  # median
                ]

                # Update previous cycle's sub to remove the long cycle
                new_sub[i - 1] = new_sub[i - 1][:-1]

                # Update previous cycle's full range
                if len(new_sub[i - 1]) > 0:
                    new_full[i - 1] = [new_sub[i - 1][0][0], new_sub[i - 1][-1][1]]

            new_sub.append(reordered)

            # Calculate new full range based on reordered sub-cycles
            if len(reordered) > 0:
                new_full.append([reordered[0][0], reordered[-1][1]])
            else:
                new_full.append(full_cycles[i])

        result[key] = {
            'full': new_full,
            'sub': new_sub
        }

    return result


def interp_replace(
    target: list | np.ndarray,
    start: int,
    end: int,
    new_length: int,
) -> np.ndarray:
    """
    Replace target[start:end+1] with linear interpolation of new_length.

    Both endpoint frames are retained. new_length is the total replacement
    length, including both endpoint frames.
    """

    target = np.asarray(target)

    if not 0 <= start < end < len(target):
        raise ValueError("requires 0 <= start < end < len(target)")

    if new_length < 2:
        raise ValueError("new_length must be at least 2 to keep both endpoints")

    interpolated = np.linspace(
        target[start],
        target[end],
        num=new_length,
    )

    return np.concatenate(
        [
            target[:start],
            interpolated,
            target[end + 1 :],
        ]
    )
