# Import necessary libraries
from typing import Any
import numpy as np
from numpy import memmap, dtype, ndarray
import pandas as pd


def load_binary(file_path: str, n_channels: int, channel: int,
                dtype: np.dtype = np.int16) -> ndarray[tuple[Any, ...], Any]:
    """
    Load a single channel from an interleaved binary file using memory mapping.

    This function is efficient for reading large binary files as it doesn't load
    the entire file into memory at once. It reads a specific channel from a file
    where data from multiple channels is interleaved.

    Args:
        file_path (str): The path to the binary data file (e.g., 'continuous.dat').
        n_channels (int): The total number of channels in the binary file.
        channel (int): The 0-based index of the channel to extract.
        dtype (np.dtype, optional): The data type of the binary file. 
                                    Defaults to np.int16.

    Returns:
        memmap[Any, dtype[Any]]: A memory-mapped numpy array containing the data 
                                 for the specified channel.
    """
    # Memory-map the file for efficient reading.
    # 'r' mode opens the file in read-only mode.
    data = np.memmap(file_path, dtype=dtype, mode='r')

    # Slice the data to extract the specified channel.
    # This works by starting at the channel index and stepping by the number of channels.
    channel_data = data[channel::n_channels]

    return channel_data


def get_interval_pairs(
        interval_table_df: pd.DataFrame,
        *,
        phase_key: str | None = None,
        phase_name: str | None = None,
) -> list[list[int]]:
    if not {"start", "end"}.issubset(interval_table_df.columns):
        raise KeyError('interval_table must contain adjusted timestamp columns "start" and "end".')

    if phase_key is not None:
        subset = interval_table_df.loc[
            interval_table_df["interval_type"] == phase_key,
            ["start", "end"],
        ]
    else:
        raise ValueError("Either phase_key or phase_name must be provided.")

    return subset.astype(float).values.tolist()
