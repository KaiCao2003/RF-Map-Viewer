import re
import pandas as pd
import numpy as np

from pathlib import Path
from collections import defaultdict
from numpy import ndarray
from os import access, W_OK

from Utils.recording import detect_exposure_time
from Utils.ttl_utils import classify_and_group_TTL_pulses, group_ttl_pulses_segmented
from Utils.json_tools import read_formatted_json, write_formatted_json


def parse_looming_log_grouped(file_path: str | Path) -> dict:
    """
    Parse LoomingDisplayer log and return grouped dict:
    {
      'Center': [cycle_numbers],
      'Empty': [...],
      'Left 45°': [...],
      'Right 45°': [...]
    }
    """
    pattern = re.compile(r"Cycle\s+(\d+)\s+offset\s+([A-Za-z]+)(?:\s+45\.0°)?(?:\s+\(index\s+(\d+)\))?")
    groups = defaultdict(list)

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            m = pattern.search(line)
            if not m:
                continue

            cycle = int(m.group(1))
            offset = m.group(2)
            index = m.group(3)

            if offset.lower() == "right":
                label = "Right"
            elif offset.lower() == "left":
                label = "Left"
            elif offset.lower() == "empty":
                # index==1 means Center, otherwise Empty
                label = "Center" if index == "1" else "Empty"
            else:
                label = offset

            groups[label].append(cycle)

            result: dict = {k: sorted(v) for k, v in groups.items()}

    # Convert defaultdict to normal dict and sort cycle lists
    return result


def process_cycles(data):
    """
    Process cycle data to extract full cycle ranges and sub-cycles.

    Args:
        data: Dictionary with keys as cycle names and values as lists of cycle ranges

    Returns:
        Dictionary with cycle information including full ranges and sub-cycles
    """
    # First, collect all cycles with their start times and metadata
    all_cycles = []

    for key, cycles in data.items():
        for cycle_idx, cycle in enumerate(cycles):
            start_time = cycle[0][0]  # First sub-cycle's start time
            all_cycles.append({
                'key': key,
                'cycle_idx': cycle_idx,
                'start_time': start_time,
                'sub_cycles': cycle
            })

    # Sort all cycles by start time
    all_cycles.sort(key=lambda x: x['start_time'])

    # Build the result dictionary - initialize with empty lists
    result = {}
    for key in data.keys():
        result[key] = {
            'full': [],
            'sub': []
        }

    for i, cycle in enumerate(all_cycles):
        # Determine the end time of this cycle
        if i < len(all_cycles) - 1:
            # End is one less than the next cycle's start
            cycle_end = all_cycles[i + 1]['start_time'] - 1
        else:
            # Last cycle: end at the last sub-cycle's end
            cycle_end = cycle['sub_cycles'][-1][-1]

        cycle_start = cycle['start_time']

        # Get the original key
        original_key = cycle['key']

        # Add full cycle range
        result[original_key]['full'].append([cycle_start, cycle_end])

        # Process sub-cycles for this cycle
        sub_cycles_for_this_cycle = []
        for j, sub_cycle in enumerate(cycle['sub_cycles']):
            sub_start = sub_cycle[0]

            # Determine sub-cycle end
            if j < len(cycle['sub_cycles']) - 1:
                # End is one less than the next sub-cycle's start
                sub_end = cycle['sub_cycles'][j + 1][0] - 1
            else:
                # Last sub-cycle ends at the cycle's end
                sub_end = cycle_end

            sub_cycles_for_this_cycle.append([sub_start, sub_end])

        # Add sub-cycles for this cycle as a nested list
        result[original_key]['sub'].append(sub_cycles_for_this_cycle)

    return result

def group_pulses_by_log(*, ADC_continuous_sample_number_data: ndarray = None, ADC_data: ndarray = None,
                        log_file_path: str | Path = None,
                        threshold: int = 20000,
                        time_interval: bool = False,
                        is_signal_inverted: bool = True,
                        exposure_sampling_number_list_pairs: list[list[int]] = None,
                        offsets_grouped: dict = None,
                        cycle_index: list[int] = None,
                        process_cycle: bool = False,
                        load_from_json: str | Path = None,
                        write_to_json: str | Path = None) -> dict:
    log_file_path = Path(log_file_path)
    print(log_file_path)

    if load_from_json:
        data = read_formatted_json(load_from_json)
        exposure_sampling_number_list_pairs = data['exposure_sampling_number_list_pairs']
        offsets_grouped = data['offsets_grouped']
        cycle_index = data['cycle_index']
    else:
        if not exposure_sampling_number_list_pairs:
            exposure_sampling_number_list_pairs = detect_exposure_time(ADC_continuous_sample_number_data, ADC_data,
                                                                       threshold=threshold, time_interval=time_interval,
                                                                       is_inverted=is_signal_inverted).tolist()

        if not offsets_grouped:
            offsets_grouped = parse_looming_log_grouped(log_file_path)

        if not cycle_index:
            cycle_index = classify_and_group_TTL_pulses(exposure_sampling_number_list_pairs)['indexes']

    result = group_ttl_pulses_segmented(offsets_grouped, cycle_index, exposure_sampling_number_list_pairs)

    if process_cycle:
        result = process_cycles(result)

    if write_to_json:
        write_to_json = Path(write_to_json)
        if not write_to_json.exists() and access(write_to_json, W_OK):
            write_to_json = ""

        write_formatted_json(["exposure_sampling_number_list_pairs", "offsets_grouped", "cycle_index", "result"],
                             [exposure_sampling_number_list_pairs, offsets_grouped, cycle_index, result],
                             filename="group_pulses_by_log",
                             path=write_to_json)

        print("Saved grouped pulses to JSON:", write_to_json)

    return result


def compare_overlap_positional_chunked(
        df1: pd.DataFrame,
        df2: pd.DataFrame,
        start1=(0, 0),  # (row, col) in df1, 0-indexed
        start2=(0, 0),  # (row, col) in df2, 0-indexed
        float_tol=None,  # None => exact; or e.g. 1e-9
        row_block=100_000,  # chunk size over rows
        max_report=50  # limit console printouts
):
    r1, c1 = start1
    r2, c2 = start2

    # Slices to the end
    s1 = df1.iloc[r1:, c1:]
    s2 = df2.iloc[r2:, c2:]

    # Overlap only
    nrows = min(len(s1), len(s2))
    ncols = min(s1.shape[1], s2.shape[1])
    if nrows == 0 or ncols == 0:
        print("No overlapping area to compare.")
        return True, pd.DataFrame()

    # Pre-limit to overlapping region
    s1 = s1.iloc[:nrows, :ncols]
    s2 = s2.iloc[:nrows, :ncols]

    # Results
    mismatches = []
    printed = 0

    # Process in row chunks
    for start in range(0, nrows, row_block):
        stop = min(start + row_block, nrows)
        a = s1.iloc[start:stop, :]
        b = s2.iloc[start:stop, :]

        # Base equality (strings/objects) with NaN==NaN
        A = a.to_numpy()
        B = b.to_numpy()
        base_equal = (A == B) | (pd.isna(A) & pd.isna(B))

        if float_tol is not None:
            # Columnwise numeric coercion keeps the 2-D shape
            a_num = a.apply(pd.to_numeric, errors="coerce").to_numpy()
            b_num = b.apply(pd.to_numeric, errors="coerce").to_numpy()

            both_num = ~np.isnan(a_num) & ~np.isnan(b_num)
            num_close = np.abs(a_num - b_num) <= float_tol

            equal = np.where(both_num, num_close, base_equal)
        else:
            equal = base_equal

        mm = ~equal
        if mm.any():
            ii, jj = np.where(mm)
            for i, j in zip(ii, jj):
                v1 = A[i, j]
                v2 = B[i, j]
                df1_row = r1 + (start + i)
                df1_col = c1 + j
                df2_row = r2 + (start + i)
                df2_col = c2 + j
                mismatches.append({
                    "df1_row": df1_row, "df1_col": df1_col, "df1_value": v1,
                    "df2_row": df2_row, "df2_col": df2_col, "df2_value": v2
                })
                if printed < max_report:
                    print(f"- df1({df1_row},{df1_col})={repr(v1)}  "
                          f"vs  df2({df2_row},{df2_col})={repr(v2)}")
                    printed += 1

    if not mismatches:
        print(f"✅ Identical over overlap: {nrows} rows × {ncols} cols.")
        return True, pd.DataFrame(columns=[
            "df1_row", "df1_col", "df1_value", "df2_row", "df2_col", "df2_value"
        ])

    if printed >= max_report:
        remaining = len(mismatches) - max_report
        if remaining > 0:
            print(f"... ({remaining} more mismatches not shown)")

    return False, pd.DataFrame(mismatches)





