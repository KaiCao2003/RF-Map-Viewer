from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray


type FloatArray = NDArray[np.floating[Any]]
type IntArray = NDArray[np.integer[Any]]


@dataclass(frozen=True)
class TemplatePtpSummary:
    ptp_by_channel: FloatArray
    best_channel_indices: IntArray
    max_ptp_by_unit: FloatArray


@dataclass(frozen=True)
class ValidatedData:
    channel_map: IntArray
    channel_positions: FloatArray
    channel_shank_ids: IntArray


def compute_template_ptp_summary(template_array: FloatArray) -> TemplatePtpSummary:
    ptp_by_channel: FloatArray = np.ptp(template_array, axis=1)
    best_channel_indices: IntArray = np.argmax(ptp_by_channel, axis=1)
    return TemplatePtpSummary(
        ptp_by_channel=ptp_by_channel,
        best_channel_indices=best_channel_indices,
        max_ptp_by_unit=ptp_by_channel[np.arange(len(ptp_by_channel)), best_channel_indices],
    )


def validate_data(
        *,
        kilosort_dir: str | Path,
        recording_file: str | Path,
) -> ValidatedData:
    raw_dtype = "int16"
    raw_num_channels = 384

    kilosort_dir = Path(kilosort_dir)
    recording_file = Path(recording_file)

    required_files: tuple[str, ...] = (
        "params.py",
        "spike_times.npy",
        "spike_clusters.npy",
        "channel_map.npy",
        "channel_positions.npy",
        "ops.npy",
    )
    missing = [
        name
        for name in required_files
        if not (kilosort_dir / name).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            f"Missing Kilosort files in {kilosort_dir}: {missing}"
        )
    if not recording_file.is_file():
        raise FileNotFoundError(f"Raw file not found: {recording_file}")

    bytes_per_frame = np.dtype(raw_dtype).itemsize * raw_num_channels
    raw_byte_count = recording_file.stat().st_size
    if raw_byte_count % bytes_per_frame != 0:
        raise ValueError(
            "Raw binary size is not divisible by "
            "dtype size × channel count."
        )

    channel_map = np.load(kilosort_dir / "channel_map.npy")
    channel_positions = np.load(kilosort_dir / "channel_positions.npy")
    if channel_map.shape != (raw_num_channels,) or channel_positions.shape != (raw_num_channels, 2):
        raise ValueError(
            "channel_map.npy and channel_positions.npy do not match "
            f"the expected {raw_num_channels} channels."
        )

    probe_ops = np.load(kilosort_dir / "ops.npy", allow_pickle=True).item()["probe"]
    ops_channel_map = np.asarray(probe_ops["chanMap"]).squeeze().astype(int)
    channel_shank_ids = np.asarray(probe_ops["kcoords"]).squeeze().astype(int)
    if not np.array_equal(ops_channel_map, channel_map):
        raise ValueError("Kilosort chanMap and channel_map.npy do not agree.")
    if channel_shank_ids.shape != (raw_num_channels,):
        raise ValueError("Kilosort kcoords do not match the channel count.")

    return ValidatedData(
        channel_map=channel_map,
        channel_positions=channel_positions,
        channel_shank_ids=channel_shank_ids,
    )
