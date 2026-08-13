from __future__ import annotations

"""Validated, per-unit head-direction tuning data shared by the live viewer
and figure exporters.

Three on-disk formats are intentionally supported.  Current recordings use a
columnar document, Python 1.8 accepted a nested ``schema_version == 2``
document, and older recordings store a simple ``{unit_id: [180 rates]}``
mapping.  Keeping the compatibility logic here prevents the live panel and the
export composer from disagreeing about which scientific inputs are readable.
"""

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

HD_RAW_BIN_COUNT = 180
DEFAULT_HD_DISPLAY_BINS = 30
DEFAULT_HD_SMOOTH_SIGMA = 1.5
_SESSION_RE = re.compile(r"^(?P<date>\d{6,8})_(?P<index>\d+)$")
TUNING_CURVE_FILENAMES = ("tuning_curves.tc", "tuning_curves.json")


def _readonly(values: Any, *, dtype: Any) -> NDArray[Any]:
    array = np.asarray(values, dtype=dtype)
    array.setflags(write=False)
    return array


def _one_dimensional_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list) or any(isinstance(item, (list, dict)) for item in value):
        raise ValueError(f"{label} must be a one-dimensional array")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


@dataclass(frozen=True, slots=True)
class HDTuningUnit:
    unit_id: int
    spike_counts: NDArray[np.float64]
    raw_rates_hz: NDArray[np.float64]
    hd_class: int | None
    metrics: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ProcessedHDCurve:
    angles_deg: NDArray[np.float64]
    rates_hz: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class TuningCurveClassificationProvenance:
    method: str | None = None
    class_0: str | None = None
    class_1: str | None = None
    class_2: str | None = None
    class_null: str | None = None
    rayleigh_alpha: float | None = None
    rayleigh_test: str | None = None
    shuffle_alpha: float | None = None
    num_shuffle: int | None = None
    shuffle_seed: int | None = None


@dataclass(frozen=True, slots=True)
class TuningCurveTTLProvenance:
    ttl_pulse_count: int | None = None
    first_exposure_s: float | None = None
    last_exposure_s: float | None = None
    median_period_s: float | None = None
    measured_rate_hz: float | None = None
    camera_input_channel: int | None = None
    camera_ttl_threshold: float | None = None
    camera_ttl_active_high: bool | None = None
    motive_frame_count_raw: int | None = None
    matched_motive_frame_count: int | None = None
    dropped_motive_frame_ids: tuple[int, ...] | None = None
    frame_alignment_policy_requested: str | None = None
    frame_alignment_policy_applied: str | None = None
    frame_timestamp_mapping: str | None = None


@dataclass(frozen=True, slots=True)
class TuningCurveMetadata(Mapping[str, Any]):
    """Typed provenance for the live panel while retaining mapping access."""

    session: str | None = None
    probe: str | None = None
    kilosort_dir: str | None = None
    timebase: str | None = None
    adc_time_origin_raw_s: float | None = None
    timestamp_reference: str | None = None
    angle_convention_note: str | None = None
    num_angle_bins: int | None = None
    feature_fs_hz: float | None = None
    classification: TuningCurveClassificationProvenance | None = None
    ttl_qc: TuningCurveTTLProvenance | None = None
    _raw: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
        compare=False,
    )

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]

    def __iter__(self):
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)


def _strict_integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return int(value)


def _strict_nonnegative_integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return int(value)


def _validated_edges(value: Any) -> NDArray[np.float64]:
    raw_edges = _one_dimensional_list(value, "angle_bin_edges_deg")
    edges = _readonly(
        [_finite_number(item, "angle_bin_edges_deg value") for item in raw_edges],
        dtype=float,
    )
    if edges.shape != (HD_RAW_BIN_COUNT + 1,) or not np.all(np.diff(edges) > 0):
        raise ValueError("angle_bin_edges_deg must contain 181 strictly increasing edges")
    expected = np.linspace(0.0, 360.0, HD_RAW_BIN_COUNT + 1)
    if not np.allclose(edges, expected, rtol=0.0, atol=1e-8):
        raise ValueError(
            "angle_bin_edges_deg must span 0–360 degrees in 180 equal bins"
        )
    return edges


def _validated_occupancy(value: Any) -> NDArray[np.float64]:
    raw_occupancy = _one_dimensional_list(value, "occupancy_time_s")
    occupancy = _readonly(
        [_finite_number(item, "occupancy_time_s value") for item in raw_occupancy],
        dtype=float,
    )
    if occupancy.shape != (HD_RAW_BIN_COUNT,) or np.any(occupancy < 0):
        raise ValueError("occupancy_time_s must contain 180 non-negative values")
    if not np.any(occupancy > 0):
        raise ValueError("occupancy_time_s must contain positive occupancy")
    return occupancy


def _metadata_string(
    payload: Mapping[str, Any], key: str, context: str
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{context}.{key} must be a string or null")
    return value


def _metadata_float(
    payload: Mapping[str, Any], key: str, context: str
) -> float | None:
    value = payload.get(key)
    return None if value is None else _finite_number(value, f"{context}.{key}")


def _metadata_int(
    payload: Mapping[str, Any], key: str, context: str
) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return _strict_integer(value, f"{context}.{key}")
    except ValueError as exc:
        raise ValueError(f"{context}.{key} must be an integer or null") from exc


def _metadata_bool(
    payload: Mapping[str, Any], key: str, context: str
) -> bool | None:
    value = payload.get(key)
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"{context}.{key} must be boolean or null")
    return bool(value)


def _metadata_int_tuple(
    payload: Mapping[str, Any], key: str, context: str
) -> tuple[int, ...] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or any(type(item) is not int for item in value):
        raise ValueError(f"{context}.{key} must be an integer list or null")
    return tuple(int(item) for item in value)


def _load_metadata(raw_metadata: Any) -> TuningCurveMetadata | None:
    if raw_metadata is None:
        return None
    if not isinstance(raw_metadata, dict):
        raise ValueError("metadata must be an object or null")

    classification_raw = raw_metadata.get("classification")
    if classification_raw is None:
        classification = None
    elif not isinstance(classification_raw, dict):
        raise ValueError("metadata.classification must be an object or null")
    else:
        context = "metadata.classification"
        classification = TuningCurveClassificationProvenance(
            method=_metadata_string(classification_raw, "method", context),
            class_0=_metadata_string(classification_raw, "class_0", context),
            class_1=_metadata_string(classification_raw, "class_1", context),
            class_2=_metadata_string(classification_raw, "class_2", context),
            class_null=_metadata_string(classification_raw, "class_null", context),
            rayleigh_alpha=_metadata_float(
                classification_raw, "rayleigh_alpha", context
            ),
            rayleigh_test=_metadata_string(
                classification_raw, "rayleigh_test", context
            ),
            shuffle_alpha=_metadata_float(
                classification_raw, "shuffle_alpha", context
            ),
            num_shuffle=_metadata_int(classification_raw, "num_shuffle", context),
            shuffle_seed=_metadata_int(classification_raw, "shuffle_seed", context),
        )

    ttl_raw = raw_metadata.get("ttl_qc")
    if ttl_raw is None:
        ttl_qc = None
    elif not isinstance(ttl_raw, dict):
        raise ValueError("metadata.ttl_qc must be an object or null")
    else:
        context = "metadata.ttl_qc"
        ttl_qc = TuningCurveTTLProvenance(
            ttl_pulse_count=_metadata_int(ttl_raw, "ttl_pulse_count", context),
            first_exposure_s=_metadata_float(ttl_raw, "first_exposure_s", context),
            last_exposure_s=_metadata_float(ttl_raw, "last_exposure_s", context),
            median_period_s=_metadata_float(ttl_raw, "median_period_s", context),
            measured_rate_hz=_metadata_float(ttl_raw, "measured_rate_hz", context),
            camera_input_channel=_metadata_int(
                ttl_raw, "camera_input_channel", context
            ),
            camera_ttl_threshold=_metadata_float(
                ttl_raw, "camera_ttl_threshold", context
            ),
            camera_ttl_active_high=_metadata_bool(
                ttl_raw, "camera_ttl_active_high", context
            ),
            motive_frame_count_raw=_metadata_int(
                ttl_raw, "motive_frame_count_raw", context
            ),
            matched_motive_frame_count=_metadata_int(
                ttl_raw, "matched_motive_frame_count", context
            ),
            dropped_motive_frame_ids=_metadata_int_tuple(
                ttl_raw, "dropped_motive_frame_ids", context
            ),
            frame_alignment_policy_requested=_metadata_string(
                ttl_raw, "frame_alignment_policy_requested", context
            ),
            frame_alignment_policy_applied=_metadata_string(
                ttl_raw, "frame_alignment_policy_applied", context
            ),
            frame_timestamp_mapping=_metadata_string(
                ttl_raw, "frame_timestamp_mapping", context
            ),
        )

    context = "metadata"
    return TuningCurveMetadata(
        session=_metadata_string(raw_metadata, "session", context),
        probe=_metadata_string(raw_metadata, "probe", context),
        kilosort_dir=_metadata_string(raw_metadata, "kilosort_dir", context),
        timebase=_metadata_string(raw_metadata, "timebase", context),
        adc_time_origin_raw_s=_metadata_float(
            raw_metadata, "adc_time_origin_raw_s", context
        ),
        timestamp_reference=_metadata_string(
            raw_metadata, "timestamp_reference", context
        ),
        angle_convention_note=_metadata_string(
            raw_metadata, "angle_convention_note", context
        ),
        num_angle_bins=_metadata_int(raw_metadata, "num_angle_bins", context),
        feature_fs_hz=_metadata_float(raw_metadata, "feature_fs_hz", context),
        classification=classification,
        ttl_qc=ttl_qc,
        _raw=MappingProxyType(dict(raw_metadata)),
    )


class HDTuningData:
    """Validated tuning data with current and Python 1.8 lookup APIs."""

    __slots__ = (
        "path",
        "angle_bin_edges_deg",
        "occupancy_time_s",
        "metadata",
        "_units",
        "_units_by_id",
    )

    def __init__(
        self,
        path: str | Path,
        curves: Mapping[int, Sequence[float]] | None = None,
        spike_counts: Mapping[int, Sequence[int]] | None = None,
        occupancy_time_s: Sequence[float] | NDArray[np.float64] | None = None,
        hd_classes: Mapping[int, int | None] | None = None,
        metadata: TuningCurveMetadata | Mapping[str, Any] | None = None,
        *,
        angle_bin_edges_deg: NDArray[np.float64] | None = None,
        units: Sequence[HDTuningUnit] | None = None,
    ) -> None:
        """Build from the current unit model or the Python 1.8 constructor.

        Loaders use ``angle_bin_edges_deg`` plus ``units``.  The restored live
        panel and its Tk fixtures also construct ``TuningCurveData(path,
        curves, ...)`` directly, so that spelling is kept as a compatibility
        route into the same immutable model.
        """

        self.path = Path(path)
        if isinstance(metadata, TuningCurveMetadata) or metadata is None:
            parsed_metadata = metadata
        elif isinstance(metadata, Mapping):
            parsed_metadata = _load_metadata(dict(metadata))
        else:
            raise ValueError("metadata must be an object or null")

        if units is None:
            if curves is None:
                raise TypeError("curves are required by the compatibility constructor")
            parsed_occupancy = (
                _readonly(np.full(HD_RAW_BIN_COUNT, math.nan), dtype=float)
                if occupancy_time_s is None
                else _readonly(occupancy_time_s, dtype=float)
            )
            if parsed_occupancy.shape != (HD_RAW_BIN_COUNT,):
                raise ValueError(
                    f"occupancy_time_s must contain {HD_RAW_BIN_COUNT} values"
                )
            parsed_units: list[HDTuningUnit] = []
            count_map = spike_counts or {}
            class_map = hd_classes or {}
            for raw_unit_id, raw_rates in curves.items():
                unit_id = int(raw_unit_id)
                rates = _readonly(raw_rates, dtype=float)
                if rates.shape != (HD_RAW_BIN_COUNT,):
                    raise ValueError(
                        f"Unit {unit_id} must contain {HD_RAW_BIN_COUNT} rates"
                    )
                if unit_id in count_map:
                    counts = _readonly(count_map[unit_id], dtype=float)
                    if counts.shape != (HD_RAW_BIN_COUNT,):
                        raise ValueError(
                            f"Unit {unit_id} must contain {HD_RAW_BIN_COUNT} spike counts"
                        )
                else:
                    counts = _readonly(
                        np.full(HD_RAW_BIN_COUNT, math.nan), dtype=float
                    )
                parsed_units.append(
                    HDTuningUnit(
                        unit_id,
                        counts,
                        rates,
                        class_map.get(unit_id),
                        MappingProxyType({}),
                    )
                )
            units = parsed_units
            angle_bin_edges_deg = _default_edges()
            occupancy_time_s = parsed_occupancy
        elif angle_bin_edges_deg is None or occupancy_time_s is None:
            raise TypeError(
                "angle_bin_edges_deg and occupancy_time_s are required with units"
            )

        self.angle_bin_edges_deg = _readonly(angle_bin_edges_deg, dtype=float)
        self.occupancy_time_s = _readonly(occupancy_time_s, dtype=float)
        self.metadata = parsed_metadata
        self._units = tuple(units)
        self._units_by_id = {unit.unit_id: unit for unit in self._units}

    def __len__(self) -> int:
        return len(self._units)

    def __iter__(self):
        return iter(self._units)

    @classmethod
    def load(cls, path: str | Path) -> HDTuningData:
        """Compatibility constructor used by the restored live HD panel."""

        return load_hd_tuning(path)

    @property
    def unit_ids(self) -> tuple[int, ...]:
        return tuple(unit.unit_id for unit in self._units)

    @property
    def curves(self) -> Mapping[int, tuple[float, ...]]:
        return MappingProxyType(
            {
                unit.unit_id: tuple(float(value) for value in unit.raw_rates_hz)
                for unit in self._units
            }
        )

    @property
    def spike_counts(self) -> Mapping[int, tuple[int, ...]]:
        return MappingProxyType(
            {
                unit.unit_id: tuple(int(value) for value in unit.spike_counts)
                for unit in self._units
                if np.all(np.isfinite(unit.spike_counts))
            }
        )

    @property
    def hd_classes(self) -> Mapping[int, int | None]:
        return MappingProxyType(
            {unit.unit_id: unit.hd_class for unit in self._units}
        )

    def by_unit_id(self, unit_id: int) -> HDTuningUnit:
        try:
            return self._units_by_id[int(unit_id)]
        except (KeyError, TypeError, ValueError) as exc:
            raise KeyError(
                f"HD unit {unit_id!r} is unavailable. Available unit IDs: "
                f"{list(self.unit_ids)}"
            ) from exc

    def processed_curve(
        self,
        unit_id: int,
        *,
        display_bins: int = DEFAULT_HD_DISPLAY_BINS,
        smoothing: bool = True,
        sigma: float = DEFAULT_HD_SMOOTH_SIGMA,
    ) -> ProcessedHDCurve:
        unit = self.by_unit_id(unit_id)
        normalized_bins = normalize_hd_bin_count(display_bins)
        counts = np.asarray(unit.spike_counts, dtype=float)
        occupancy = np.asarray(self.occupancy_time_s, dtype=float)
        has_observations = (
            counts.shape == (HD_RAW_BIN_COUNT,)
            and occupancy.shape == (HD_RAW_BIN_COUNT,)
            and np.any(occupancy > 0)
        )
        if not has_observations:
            rates = np.asarray(unit.raw_rates_hz, dtype=float)
            if smoothing:
                if isinstance(sigma, bool) or not math.isfinite(float(sigma)) or float(sigma) <= 0:
                    raise ValueError("HD smoothing sigma must be positive and finite")
                raw_sigma = float(sigma) * HD_RAW_BIN_COUNT / DEFAULT_HD_DISPLAY_BINS
                rates = smooth_circular_missing_aware(rates, raw_sigma)
            group_size = HD_RAW_BIN_COUNT // normalized_bins
            grouped = rates.reshape(normalized_bins, group_size)
            grouped_rates = np.asarray(
                [
                    float(np.nanmean(group)) if np.any(np.isfinite(group)) else math.nan
                    for group in grouped
                ],
                dtype=float,
            )
            width = 360.0 / normalized_bins
            angles = (np.arange(normalized_bins, dtype=float) + 0.5) * width
            angles.setflags(write=False)
            grouped_rates.setflags(write=False)
            return ProcessedHDCurve(angles, grouped_rates)
        if smoothing:
            if isinstance(sigma, bool) or not math.isfinite(float(sigma)) or float(sigma) <= 0:
                raise ValueError("HD smoothing sigma must be positive and finite")
            raw_sigma = float(sigma) * HD_RAW_BIN_COUNT / DEFAULT_HD_DISPLAY_BINS
            counts = smooth_circular(counts, raw_sigma)
            occupancy = smooth_circular(occupancy, raw_sigma)
        group_size = HD_RAW_BIN_COUNT // normalized_bins
        grouped_counts = counts.reshape(normalized_bins, group_size).sum(axis=1)
        grouped_occupancy = occupancy.reshape(normalized_bins, group_size).sum(axis=1)
        rates = np.divide(
            grouped_counts,
            grouped_occupancy,
            out=np.full(normalized_bins, math.nan, dtype=float),
            where=grouped_occupancy > 1e-12,
        )
        width = 360.0 / normalized_bins
        angles = (np.arange(normalized_bins, dtype=float) + 0.5) * width
        angles.setflags(write=False)
        rates.setflags(write=False)
        return ProcessedHDCurve(angles, rates)

    # Compatibility API used by the restored Python 1.8 live HD panel.
    def rates_for(self, unit_id: int) -> tuple[float, ...] | None:
        try:
            unit = self.by_unit_id(unit_id)
        except KeyError:
            return None
        return tuple(float(value) for value in unit.raw_rates_hz)

    def hd_class_for(self, unit_id: int) -> int | None:
        try:
            return self.by_unit_id(unit_id).hd_class
        except KeyError:
            return None

    def processed_for(
        self,
        unit_id: int,
        display_bins: int,
        *,
        smoothing: bool,
        sigma: float,
    ) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
        try:
            curve = self.processed_curve(
                unit_id,
                display_bins=display_bins,
                smoothing=smoothing,
                sigma=sigma,
            )
        except KeyError:
            return None
        return (
            tuple(float(value) for value in curve.angles_deg),
            tuple(float(value) for value in curve.rates_hz),
        )


def normalize_hd_bin_count(value: int) -> int:
    if isinstance(value, bool):
        raise ValueError("HD display bins must be an integer")
    try:
        requested = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("HD display bins must be an integer") from exc
    requested = max(1, min(HD_RAW_BIN_COUNT, requested))
    for candidate in range(requested, 0, -1):
        if HD_RAW_BIN_COUNT % candidate == 0:
            return candidate
    return 1


def smooth_circular(values: NDArray[np.float64], sigma: float) -> NDArray[np.float64]:
    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError("HD smoothing sigma must be positive and finite")
    radius = math.floor(4.0 * sigma + 0.5)
    offsets = np.arange(-radius, radius + 1, dtype=int)
    weights = np.exp(-0.5 * (offsets.astype(float) / sigma) ** 2)
    weights /= weights.sum()
    result = np.zeros_like(values, dtype=float)
    for offset, weight in zip(offsets, weights):
        result += weight * np.roll(values, -int(offset))
    return result


def smooth_circular_missing_aware(
    values: NDArray[np.float64], sigma: float
) -> NDArray[np.float64]:
    """Circular Gaussian smoothing that does not turn missing bins into 0 Hz."""

    finite = np.isfinite(values)
    numerator = smooth_circular(np.where(finite, values, 0.0), sigma)
    denominator = smooth_circular(finite.astype(float), sigma)
    return np.divide(
        numerator,
        denominator,
        out=np.full(values.shape, math.nan, dtype=float),
        where=denominator > 1e-12,
    )


def _default_edges() -> NDArray[np.float64]:
    return _readonly(np.linspace(0.0, 360.0, HD_RAW_BIN_COUNT + 1), dtype=float)


def _legacy_document(source: Path, raw: Mapping[Any, Any]) -> HDTuningData:
    units: list[HDTuningUnit] = []
    seen: set[int] = set()
    for raw_unit_id, raw_rates in raw.items():
        try:
            unit_id = int(raw_unit_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid legacy HD unit ID: {raw_unit_id!r}") from exc
        if unit_id in seen:
            raise ValueError(f"Duplicate legacy HD unit ID after normalization: {unit_id}")
        rates_list = _one_dimensional_list(raw_rates, f"legacy unit {unit_id}")
        if len(rates_list) != HD_RAW_BIN_COUNT:
            raise ValueError(
                f"Legacy HD unit {unit_id} must contain exactly {HD_RAW_BIN_COUNT} rates"
            )
        rates = _readonly(
            [
                _finite_number(value, f"legacy unit {unit_id} rate")
                for value in rates_list
            ],
            dtype=float,
        )
        if np.any(rates < 0):
            raise ValueError(f"Legacy HD unit {unit_id} rates must be non-negative")
        seen.add(unit_id)
        units.append(
            HDTuningUnit(
                unit_id,
                _readonly(np.full(HD_RAW_BIN_COUNT, math.nan), dtype=float),
                rates,
                None,
                MappingProxyType({}),
            )
        )
    if not units:
        raise ValueError("Legacy tuning-curve JSON must contain at least one unit")
    return HDTuningData(
        path=source,
        angle_bin_edges_deg=_default_edges(),
        occupancy_time_s=_readonly(np.full(HD_RAW_BIN_COUNT, math.nan), dtype=float),
        units=units,
        metadata=None,
    )


def _nested_schema_v2_document(
    source: Path, raw: Mapping[str, Any]
) -> HDTuningData:
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 2:
        raise ValueError(
            f"Unsupported tuning-curve schema version: {raw.get('schema_version')!r}"
        )
    edges = _validated_edges(raw.get("angle_bin_edges_deg"))
    occupancy = _validated_occupancy(raw.get("occupancy_time_s"))
    raw_units = raw.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        raise ValueError("Schema v2 units must be a non-empty array")
    units: list[HDTuningUnit] = []
    seen: set[int] = set()
    for index, item in enumerate(raw_units):
        if not isinstance(item, dict):
            raise ValueError(f"Schema v2 unit {index + 1} must be an object")
        unit_id = _strict_integer(
            item.get("unit_id"), f"schema v2 unit {index + 1} unit_id"
        )
        if unit_id in seen:
            raise ValueError(f"Duplicate schema v2 unit_id: {unit_id}")
        count_values = _one_dimensional_list(
            item.get("spike_counts"), f"unit {unit_id} spike_counts"
        )
        rate_values = _one_dimensional_list(
            item.get("firing_rate_hz"), f"unit {unit_id} firing_rate_hz"
        )
        if len(count_values) != HD_RAW_BIN_COUNT or len(rate_values) != HD_RAW_BIN_COUNT:
            raise ValueError(f"Schema v2 unit {unit_id} arrays must contain 180 values")
        counts = np.empty(HD_RAW_BIN_COUNT, dtype=float)
        rates = np.empty(HD_RAW_BIN_COUNT, dtype=float)
        for bin_index, (raw_count, raw_rate, occupied) in enumerate(
            zip(count_values, rate_values, occupancy)
        ):
            count = _strict_nonnegative_integer(
                raw_count, f"unit {unit_id} spike count"
            )
            counts[bin_index] = count
            if occupied <= 0:
                if count != 0 or raw_rate is not None:
                    raise ValueError(
                        f"Unit {unit_id} bin {bin_index + 1} with zero occupancy must contain count 0 / rate null"
                    )
                rates[bin_index] = math.nan
            else:
                rate = _finite_number(raw_rate, f"unit {unit_id} firing rate")
                if rate < 0 or not math.isclose(
                    rate, count / occupied, rel_tol=1e-7, abs_tol=1e-9
                ):
                    raise ValueError(
                        f"Unit {unit_id} firing rate {bin_index + 1} does not match count / occupancy"
                    )
                rates[bin_index] = rate
        hd_class_raw = item.get("hd_class")
        hd_class = (
            None
            if hd_class_raw is None
            else _strict_integer(hd_class_raw, "hd_class")
        )
        if hd_class not in {None, 0, 1, 2}:
            raise ValueError("hd_class must be 0, 1, 2, or null")
        seen.add(unit_id)
        counts.setflags(write=False)
        rates.setflags(write=False)
        metrics = MappingProxyType(
            {
                key: value
                for key, value in item.items()
                if key not in {"unit_id", "spike_counts", "firing_rate_hz", "hd_class"}
            }
        )
        units.append(HDTuningUnit(unit_id, counts, rates, hd_class, metrics))
    metadata = _load_metadata(raw.get("metadata"))
    return HDTuningData(
        path=source,
        angle_bin_edges_deg=edges,
        occupancy_time_s=occupancy,
        units=units,
        metadata=metadata,
    )


def _columnar_document(source: Path, raw: Mapping[str, Any]) -> HDTuningData:
    required = {
        "metadata",
        "angle_bin_edges_deg",
        "occupancy_time_s",
        "unit_id",
        "spike_counts",
        "firing_rate_hz",
        "unit_data",
    }
    missing = sorted(required.difference(raw))
    if missing:
        raise ValueError(f"Missing tuning-curve keys: {', '.join(missing)}")

    edges = _validated_edges(raw["angle_bin_edges_deg"])
    occupancy = _validated_occupancy(raw["occupancy_time_s"])

    unit_ids = tuple(
        _strict_integer(value, "unit_id value")
        for value in _one_dimensional_list(raw["unit_id"], "unit_id")
    )
    if not unit_ids or len(set(unit_ids)) != len(unit_ids):
        raise ValueError("unit_id must contain unique unit IDs")
    n_units = len(unit_ids)
    count_rows = raw["spike_counts"]
    rate_rows = raw["firing_rate_hz"]
    if (
        not isinstance(count_rows, list)
        or not isinstance(rate_rows, list)
        or len(count_rows) != n_units
        or len(rate_rows) != n_units
    ):
        raise ValueError(
            "spike_counts and firing_rate_hz must have shape "
            f"({n_units}, {HD_RAW_BIN_COUNT})"
        )

    unit_data = raw["unit_data"]
    if not isinstance(unit_data, dict):
        raise ValueError("unit_data must be an object of per-unit columns")
    for key, column in unit_data.items():
        if not isinstance(column, list) or len(column) != n_units:
            raise ValueError(f"unit_data.{key} must contain {n_units} values")
    classes = unit_data.get("hd_class", [None] * n_units)
    units: list[HDTuningUnit] = []
    for index, unit_id in enumerate(unit_ids):
        count_values = _one_dimensional_list(
            count_rows[index], f"unit {unit_id} spike_counts"
        )
        rate_values = _one_dimensional_list(
            rate_rows[index], f"unit {unit_id} firing_rate_hz"
        )
        if (
            len(count_values) != HD_RAW_BIN_COUNT
            or len(rate_values) != HD_RAW_BIN_COUNT
        ):
            raise ValueError(
                "spike_counts and firing_rate_hz must have shape "
                f"({n_units}, {HD_RAW_BIN_COUNT})"
            )

        counts = np.empty(HD_RAW_BIN_COUNT, dtype=float)
        rates = np.empty(HD_RAW_BIN_COUNT, dtype=float)
        for bin_index, (raw_count, raw_rate, occupied) in enumerate(
            zip(count_values, rate_values, occupancy)
        ):
            count = _strict_nonnegative_integer(
                raw_count, f"unit {unit_id} spike count"
            )
            counts[bin_index] = count
            if occupied == 0.0:
                if count != 0 or raw_rate is not None:
                    raise ValueError(
                        f"Unit {unit_id} bin {bin_index + 1} with zero occupancy "
                        "must contain count 0 / rate null"
                    )
                rates[bin_index] = math.nan
            else:
                rate = _finite_number(raw_rate, f"unit {unit_id} firing rate")
                if rate < 0 or not math.isclose(
                    rate, count / occupied, rel_tol=1e-7, abs_tol=1e-9
                ):
                    raise ValueError(
                        f"Unit {unit_id} firing rate {bin_index + 1} "
                        "does not match count / occupancy"
                    )
                rates[bin_index] = rate

        hd_class_raw = classes[index]
        hd_class = (
            None
            if hd_class_raw is None
            else _strict_integer(hd_class_raw, "hd_class value")
        )
        if hd_class not in {None, 0, 1, 2}:
            raise ValueError("hd_class must be 0, 1, 2, or null")
        counts.setflags(write=False)
        rates.setflags(write=False)
        metrics = MappingProxyType(
            {key: column[index] for key, column in unit_data.items()}
        )
        units.append(HDTuningUnit(unit_id, counts, rates, hd_class, metrics))

    metadata = _load_metadata(raw["metadata"])
    return HDTuningData(
        path=source,
        angle_bin_edges_deg=edges,
        occupancy_time_s=occupancy,
        units=units,
        metadata=metadata,
    )


def load_hd_tuning(path: str | Path) -> HDTuningData:
    source = Path(path).expanduser().resolve()
    with source.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Tuning-curve JSON must contain an object")
    if "schema_version" in raw:
        return _nested_schema_v2_document(source, raw)
    columnar_markers = {
        "metadata",
        "angle_bin_edges_deg",
        "occupancy_time_s",
        "unit_id",
        "spike_counts",
        "firing_rate_hz",
        "unit_data",
    }
    if columnar_markers.intersection(raw):
        return _columnar_document(source, raw)
    return _legacy_document(source, raw)


def probe_name_for_rf(path: str | Path) -> str | None:
    source = Path(path)
    for part in (source.stem, *(parent.name for parent in source.parents)):
        match = re.search(r"probe[\s_-]*([ab])(?:\b|[_-])", part, re.IGNORECASE)
        if match:
            return f"Probe{match.group(1).upper()}"
    return None


def discover_hd_tuning_path(rf_path: str | Path) -> Path | None:
    """Find the first same-date session's tuning JSON for this probe."""

    source = Path(rf_path)
    probe = probe_name_for_rf(source)
    if probe is None:
        return None
    session: Path | None = None
    recording_date: str | None = None
    for candidate in (source.parent, *source.parents):
        match = _SESSION_RE.fullmatch(candidate.name)
        if match:
            session = candidate
            recording_date = match.group("date")
            break
    if session is None or recording_date is None:
        return None
    try:
        siblings = sorted(
            (
                (int(match.group("index")), child)
                for child in session.parent.iterdir()
                if child.is_dir()
                and (match := _SESSION_RE.fullmatch(child.name)) is not None
                and match.group("date") == recording_date
            ),
            key=lambda item: item[0],
        )
    except OSError:
        return None
    for _index, sibling in siblings:
        directory = sibling / "data" / "tuning_curves" / probe
        for filename in TUNING_CURVE_FILENAMES:
            candidate = directory / filename
            if candidate.is_file():
                return candidate
    return None


# Python 1.8 named the live-panel model ``TuningCurveData``.  An alias keeps
# isinstance checks and classmethod construction compatible without creating a
# second model that could diverge from figure-export data.
TuningCurveData = HDTuningData


__all__ = [
    "DEFAULT_HD_DISPLAY_BINS",
    "DEFAULT_HD_SMOOTH_SIGMA",
    "HD_RAW_BIN_COUNT",
    "HDTuningData",
    "HDTuningUnit",
    "ProcessedHDCurve",
    "TuningCurveClassificationProvenance",
    "TuningCurveData",
    "TuningCurveMetadata",
    "TuningCurveTTLProvenance",
    "discover_hd_tuning_path",
    "load_hd_tuning",
    "normalize_hd_bin_count",
    "probe_name_for_rf",
    "smooth_circular",
    "smooth_circular_missing_aware",
]
