import numpy as np


def _as_float_vector(values) -> np.ndarray:
    return np.asarray(values, dtype=float).reshape(-1)


class AlignList:
    """Use probe timestamps directly without ADC sync/interpolation."""

    def __init__(
        self,
        adc_event_timestamps: list[int] | np.ndarray,
        probe_event_timestamps: list[int] | np.ndarray,
        adc_continuous_timestamps: list[int] | np.ndarray,
        probe_continuous_timestamps: list[int] | np.ndarray,
    ):
        self.adc_event_timestamps = np.asarray(adc_event_timestamps).reshape(-1)
        self.probe_event_timestamps = np.asarray(probe_event_timestamps).reshape(-1)
        self.adc_continuous_timestamps = np.asarray(adc_continuous_timestamps)
        self.probe_continuous_timestamps = np.asarray(probe_continuous_timestamps)

    def align_probe_timestamps_to_adc(self, probe_timestamps, *, adjusted: bool = False) -> np.ndarray:
        return _as_float_vector(probe_timestamps)

    def align_spike_samples_to_adc(self, spike_samples) -> np.ndarray:
        spike_indices = np.asarray(spike_samples).squeeze().astype(np.int64)
        if spike_indices.size and (
            spike_indices.min() < 0
            or spike_indices.max() >= self.probe_continuous_timestamps.size
        ):
            raise IndexError(
                "spike sample index is outside probe_continuous_timestamps: "
                f"min={spike_indices.min()}, max={spike_indices.max()}, "
                f"n_timestamps={self.probe_continuous_timestamps.size}"
            )

        return np.asarray(self.probe_continuous_timestamps[spike_indices], dtype=float)
