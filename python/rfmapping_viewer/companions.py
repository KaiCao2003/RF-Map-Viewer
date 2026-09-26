"""Tuning curves and probe geometry for the stable viewer."""

from __future__ import annotations

import csv
import json
import math
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence
from rfmapping_viewer.hd_tuning import load_hd_tuning

from rfmapping_viewer.constants import (
    DEFAULT_HD_DISPLAY_BINS,
    GAUSSIAN_TRUNCATE,
    HD_RAW_BIN_COUNT,
    PROBE_CLICK_HEIGHT_UM,
    PROBE_CLICK_WIDTH_UM,
    PROBE_POSITION_FILENAMES,
    TUNING_CURVE_FILENAMES,
    _RECORDING_SESSION_RE,
)
from rfmapping_viewer.paths import _resolve_existing_file
from rfmapping_viewer.settings import normalize_hd_bin_count


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class TuningCurveMetadata:
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


@dataclass(frozen=True)
class TuningCurveData:
    path: Path
    curves: Mapping[int, tuple[float, ...]]
    spike_counts: Mapping[int, tuple[float, ...]] = field(default_factory=dict)
    occupancy_time_s: tuple[float, ...] | None = None
    hd_classes: Mapping[int, int | None] = field(default_factory=dict)
    metadata: TuningCurveMetadata | None = None

    @classmethod
    def load(cls, path: Path) -> TuningCurveData:
        resolved = Path(path).expanduser().resolve()
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid tuning-curve JSON: {exc}") from exc
        if not isinstance(payload, dict) or not payload:
            raise ValueError("Tuning-curve JSON must be a non-empty cluster mapping.")
        if {
            "unit_id",
            "spike_counts",
            "firing_rate_hz",
            "unit_data",
            "occupancy_time_s",
        }.issubset(payload):
            return cls._load_columnar(resolved)
        if "schema_version" in payload:
            if type(payload["schema_version"]) is not int or payload["schema_version"] != 2:
                raise ValueError(
                    f"Unsupported tuning-curve schema version: {payload['schema_version']!r}."
                )
            return cls._load_schema_v2(resolved, payload)
        return cls._load_legacy(resolved, payload)

    @classmethod
    def _load_columnar(cls, resolved: Path) -> TuningCurveData:
        """Adapt the current columnar HD model to the live-view interface."""

        data = load_hd_tuning(resolved)
        curves = {
            unit.unit_id: tuple(float(value) for value in unit.raw_rates_hz)
            for unit in data
        }
        spike_counts = {
            unit.unit_id: tuple(float(value) for value in unit.spike_counts)
            for unit in data
        }
        hd_classes = {unit.unit_id: unit.hd_class for unit in data}
        try:
            metadata = cls._load_metadata(dict(data.metadata))
        except ValueError:
            # Plot data remain valid even when a newer metadata-only field has
            # no legacy presentation counterpart.
            metadata = None
        return cls(
            path=resolved,
            curves=curves,
            spike_counts=spike_counts,
            occupancy_time_s=tuple(float(value) for value in data.occupancy_time_s),
            hd_classes=hd_classes,
            metadata=metadata,
        )

    @classmethod
    def _load_legacy(cls, resolved: Path, payload: Mapping[object, object]) -> TuningCurveData:
        curves: dict[int, tuple[float, ...]] = {}
        for raw_cluster_id, raw_rates in payload.items():
            try:
                cluster_id = int(raw_cluster_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid cluster ID: {raw_cluster_id!r}") from exc
            if cluster_id in curves:
                raise ValueError(f"Duplicate cluster ID after normalization: {cluster_id}")
            if not isinstance(raw_rates, list) or len(raw_rates) != HD_RAW_BIN_COUNT:
                length = len(raw_rates) if isinstance(raw_rates, list) else "non-list"
                raise ValueError(
                    f"Cluster {cluster_id} must contain exactly {HD_RAW_BIN_COUNT} rates; got {length}."
                )
            rates: list[float] = []
            for index, raw_rate in enumerate(raw_rates):
                if isinstance(raw_rate, bool) or not isinstance(raw_rate, (int, float)):
                    raise ValueError(f"Cluster {cluster_id} rate {index + 1} is not numeric.")
                rate = float(raw_rate)
                if not math.isfinite(rate) or rate < 0.0:
                    raise ValueError(
                        f"Cluster {cluster_id} rate {index + 1} must be finite and non-negative."
                    )
                rates.append(rate)
            curves[cluster_id] = tuple(rates)
        return cls(path=resolved, curves=curves)

    @staticmethod
    def _metadata_string(
        payload: Mapping[object, object], key: str, context: str
    ) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Schema v2 {context}.{key} must be a string or null.")
        return value

    @staticmethod
    def _metadata_float(
        payload: Mapping[object, object], key: str, context: str
    ) -> float | None:
        value = payload.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Schema v2 {context}.{key} must be numeric or null.")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"Schema v2 {context}.{key} must be finite.")
        return number

    @staticmethod
    def _metadata_int(
        payload: Mapping[object, object], key: str, context: str
    ) -> int | None:
        value = payload.get(key)
        if value is None:
            return None
        if type(value) is not int:
            raise ValueError(f"Schema v2 {context}.{key} must be an integer or null.")
        return int(value)

    @staticmethod
    def _metadata_bool(
        payload: Mapping[object, object], key: str, context: str
    ) -> bool | None:
        value = payload.get(key)
        if value is None:
            return None
        if type(value) is not bool:
            raise ValueError(f"Schema v2 {context}.{key} must be boolean or null.")
        return bool(value)

    @staticmethod
    def _metadata_int_tuple(
        payload: Mapping[object, object], key: str, context: str
    ) -> tuple[int, ...] | None:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, list) or any(type(item) is not int for item in value):
            raise ValueError(
                f"Schema v2 {context}.{key} must be an integer list or null."
            )
        return tuple(int(item) for item in value)

    @classmethod
    def _load_metadata(cls, raw_metadata: object) -> TuningCurveMetadata | None:
        if raw_metadata is None:
            return None
        if not isinstance(raw_metadata, dict):
            raise ValueError("Schema v2 metadata must be an object or null.")
        classification_raw = raw_metadata.get("classification")
        if classification_raw is None:
            classification = None
        elif not isinstance(classification_raw, dict):
            raise ValueError(
                "Schema v2 metadata.classification must be an object or null."
            )
        else:
            context = "metadata.classification"
            classification = TuningCurveClassificationProvenance(
                method=cls._metadata_string(classification_raw, "method", context),
                class_0=cls._metadata_string(classification_raw, "class_0", context),
                class_1=cls._metadata_string(classification_raw, "class_1", context),
                class_2=cls._metadata_string(classification_raw, "class_2", context),
                class_null=cls._metadata_string(
                    classification_raw, "class_null", context
                ),
                rayleigh_alpha=cls._metadata_float(
                    classification_raw, "rayleigh_alpha", context
                ),
                rayleigh_test=cls._metadata_string(
                    classification_raw, "rayleigh_test", context
                ),
                shuffle_alpha=cls._metadata_float(
                    classification_raw, "shuffle_alpha", context
                ),
                num_shuffle=cls._metadata_int(
                    classification_raw, "num_shuffle", context
                ),
                shuffle_seed=cls._metadata_int(
                    classification_raw, "shuffle_seed", context
                ),
            )

        ttl_raw = raw_metadata.get("ttl_qc")
        if ttl_raw is None:
            ttl_qc = None
        elif not isinstance(ttl_raw, dict):
            raise ValueError("Schema v2 metadata.ttl_qc must be an object or null.")
        else:
            context = "metadata.ttl_qc"
            ttl_qc = TuningCurveTTLProvenance(
                ttl_pulse_count=cls._metadata_int(
                    ttl_raw, "ttl_pulse_count", context
                ),
                first_exposure_s=cls._metadata_float(
                    ttl_raw, "first_exposure_s", context
                ),
                last_exposure_s=cls._metadata_float(
                    ttl_raw, "last_exposure_s", context
                ),
                median_period_s=cls._metadata_float(
                    ttl_raw, "median_period_s", context
                ),
                measured_rate_hz=cls._metadata_float(
                    ttl_raw, "measured_rate_hz", context
                ),
                camera_input_channel=cls._metadata_int(
                    ttl_raw, "camera_input_channel", context
                ),
                camera_ttl_threshold=cls._metadata_float(
                    ttl_raw, "camera_ttl_threshold", context
                ),
                camera_ttl_active_high=cls._metadata_bool(
                    ttl_raw, "camera_ttl_active_high", context
                ),
                motive_frame_count_raw=cls._metadata_int(
                    ttl_raw, "motive_frame_count_raw", context
                ),
                matched_motive_frame_count=cls._metadata_int(
                    ttl_raw, "matched_motive_frame_count", context
                ),
                dropped_motive_frame_ids=cls._metadata_int_tuple(
                    ttl_raw, "dropped_motive_frame_ids", context
                ),
                frame_alignment_policy_requested=cls._metadata_string(
                    ttl_raw, "frame_alignment_policy_requested", context
                ),
                frame_alignment_policy_applied=cls._metadata_string(
                    ttl_raw, "frame_alignment_policy_applied", context
                ),
                frame_timestamp_mapping=cls._metadata_string(
                    ttl_raw, "frame_timestamp_mapping", context
                ),
            )

        context = "metadata"
        return TuningCurveMetadata(
            session=cls._metadata_string(raw_metadata, "session", context),
            probe=cls._metadata_string(raw_metadata, "probe", context),
            kilosort_dir=cls._metadata_string(raw_metadata, "kilosort_dir", context),
            timebase=cls._metadata_string(raw_metadata, "timebase", context),
            adc_time_origin_raw_s=cls._metadata_float(
                raw_metadata, "adc_time_origin_raw_s", context
            ),
            timestamp_reference=cls._metadata_string(
                raw_metadata, "timestamp_reference", context
            ),
            angle_convention_note=cls._metadata_string(
                raw_metadata, "angle_convention_note", context
            ),
            num_angle_bins=cls._metadata_int(
                raw_metadata, "num_angle_bins", context
            ),
            feature_fs_hz=cls._metadata_float(
                raw_metadata, "feature_fs_hz", context
            ),
            classification=classification,
            ttl_qc=ttl_qc,
        )

    @classmethod
    def _load_schema_v2(cls, resolved: Path, payload: Mapping[object, object]) -> TuningCurveData:
        metadata = cls._load_metadata(payload.get("metadata"))
        raw_edges = payload.get("angle_bin_edges_deg")
        if not isinstance(raw_edges, list) or len(raw_edges) != HD_RAW_BIN_COUNT + 1:
            raise ValueError(
                f"Schema v2 angle_bin_edges_deg must contain {HD_RAW_BIN_COUNT + 1} values."
            )
        edges: list[float] = []
        for index, raw_edge in enumerate(raw_edges):
            if isinstance(raw_edge, bool) or not isinstance(raw_edge, (int, float)):
                raise ValueError(f"Schema v2 angle edge {index + 1} is not numeric.")
            edge = float(raw_edge)
            if not math.isfinite(edge):
                raise ValueError(f"Schema v2 angle edge {index + 1} must be finite.")
            edges.append(edge)
        if not all(after > before for before, after in zip(edges, edges[1:])):
            raise ValueError("Schema v2 angle_bin_edges_deg must be strictly increasing.")
        expected_width = 360.0 / HD_RAW_BIN_COUNT
        if not all(
            math.isclose(edge, index * expected_width, rel_tol=0.0, abs_tol=1e-8)
            for index, edge in enumerate(edges)
        ):
            raise ValueError("Schema v2 angle bins must span 0–360° in 180 equal bins.")

        raw_occupancy = payload.get("occupancy_time_s")
        if not isinstance(raw_occupancy, list) or len(raw_occupancy) != HD_RAW_BIN_COUNT:
            raise ValueError(
                f"Schema v2 occupancy_time_s must contain {HD_RAW_BIN_COUNT} values."
            )
        occupancy: list[float] = []
        for index, raw_value in enumerate(raw_occupancy):
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ValueError(f"Schema v2 occupancy time {index + 1} is not numeric.")
            value = float(raw_value)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(
                    f"Schema v2 occupancy time {index + 1} must be finite and non-negative."
                )
            occupancy.append(value)
        if not any(value > 0.0 for value in occupancy):
            raise ValueError("Schema v2 occupancy_time_s must contain positive occupancy.")

        raw_units = payload.get("units")
        if not isinstance(raw_units, list) or not raw_units:
            raise ValueError("Schema v2 units must be a non-empty list.")
        curves: dict[int, tuple[float, ...]] = {}
        spike_counts: dict[int, tuple[int, ...]] = {}
        hd_classes: dict[int, int | None] = {}
        for unit_index, raw_unit in enumerate(raw_units):
            if not isinstance(raw_unit, dict):
                raise ValueError(f"Schema v2 unit {unit_index + 1} must be an object.")
            raw_unit_id = raw_unit.get("unit_id")
            if type(raw_unit_id) is not int:
                raise ValueError(f"Schema v2 unit {unit_index + 1} has an invalid unit_id.")
            unit_id = int(raw_unit_id)
            if unit_id in curves:
                raise ValueError(f"Duplicate schema v2 unit_id: {unit_id}")

            raw_counts = raw_unit.get("spike_counts")
            raw_rates = raw_unit.get("firing_rate_hz")
            if not isinstance(raw_counts, list) or len(raw_counts) != HD_RAW_BIN_COUNT:
                raise ValueError(
                    f"Unit {unit_id} spike_counts must contain {HD_RAW_BIN_COUNT} values."
                )
            if not isinstance(raw_rates, list) or len(raw_rates) != HD_RAW_BIN_COUNT:
                raise ValueError(
                    f"Unit {unit_id} firing_rate_hz must contain {HD_RAW_BIN_COUNT} values."
                )

            counts: list[int] = []
            rates: list[float] = []
            for bin_index, (raw_count, raw_rate, occupied_s) in enumerate(
                zip(raw_counts, raw_rates, occupancy)
            ):
                if type(raw_count) is not int or raw_count < 0:
                    raise ValueError(
                        f"Unit {unit_id} spike count {bin_index + 1} must be a non-negative integer."
                    )
                count = int(raw_count)
                if occupied_s == 0.0:
                    if count != 0 or raw_rate is not None:
                        raise ValueError(
                            f"Unit {unit_id} bin {bin_index + 1} has zero occupancy and must contain count 0 / rate null."
                        )
                    rate = math.nan
                else:
                    if isinstance(raw_rate, bool) or not isinstance(raw_rate, (int, float)):
                        raise ValueError(
                            f"Unit {unit_id} firing rate {bin_index + 1} is not numeric."
                        )
                    rate = float(raw_rate)
                    expected_rate = count / occupied_s
                    if (
                        not math.isfinite(rate)
                        or rate < 0.0
                        or not math.isclose(rate, expected_rate, rel_tol=1e-7, abs_tol=1e-9)
                    ):
                        raise ValueError(
                            f"Unit {unit_id} firing rate {bin_index + 1} does not match count / occupancy."
                        )
                counts.append(count)
                rates.append(rate)

            hd_class = raw_unit.get("hd_class")
            if hd_class is not None and (type(hd_class) is not int or hd_class not in {0, 1, 2}):
                raise ValueError(f"Unit {unit_id} hd_class must be 0, 1, 2, or null.")
            curves[unit_id] = tuple(rates)
            spike_counts[unit_id] = tuple(counts)
            hd_classes[unit_id] = hd_class
        return cls(
            path=resolved,
            curves=curves,
            spike_counts=spike_counts,
            occupancy_time_s=tuple(occupancy),
            hd_classes=hd_classes,
            metadata=metadata,
        )

    def rates_for(self, cluster_id: int) -> tuple[float, ...] | None:
        return self.curves.get(int(cluster_id))

    def hd_class_for(self, cluster_id: int) -> int | None:
        return self.hd_classes.get(int(cluster_id))

    def processed_for(
        self,
        cluster_id: int,
        display_bins: int,
        *,
        smoothing: bool,
        sigma: float,
    ) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
        cluster_id = int(cluster_id)
        rates = self.rates_for(cluster_id)
        if rates is None:
            return None
        counts = self.spike_counts.get(cluster_id)
        if counts is not None and self.occupancy_time_s is not None:
            display_bins = normalize_hd_bin_count(display_bins)
            if smoothing:
                return smooth_tuning_counts(
                    counts,
                    self.occupancy_time_s,
                    display_bins,
                    sigma,
                )
            return aggregate_tuning_counts(
                counts,
                self.occupancy_time_s,
                display_bins,
            )
        return processed_tuning_curve(
            rates,
            display_bins,
            smoothing=smoothing,
            sigma=sigma,
        )


def discover_tuning_curve_path(
    rf_json_path: Path,
    session_index: int | None = None,
) -> Path | None:
    """Find a tuning curve for the RF document's day/probe.

    When ``session_index`` is provided, only that positive-numbered session is
    considered.  ``None`` retains the legacy earliest-available lookup for
    callers that do not expose the GUI's explicit session setting.
    """

    if session_index is not None and (
        type(session_index) is not int or session_index <= 0
    ):
        raise ValueError("Tuning Curve Session must be a positive integer.")

    rf_json_path = Path(rf_json_path).expanduser()
    probe_name = probe_name_for_json(rf_json_path)
    if probe_name is None:
        return None
    session_pattern = re.compile(r"^(?P<date>\d{6,8})_(?P<index>\d+)$")
    session_dir: Path | None = None
    session_match: re.Match[str] | None = None
    for candidate in (rf_json_path.parent, *rf_json_path.parents):
        match = session_pattern.fullmatch(candidate.name)
        if match is not None:
            session_dir = candidate
            session_match = match
            break
    if session_dir is None or session_match is None:
        return None

    recording_date = session_match.group("date")
    sessions: list[tuple[int, Path]] = []
    try:
        siblings = session_dir.parent.iterdir()
    except OSError:
        return None
    for sibling in siblings:
        if not sibling.is_dir():
            continue
        match = session_pattern.fullmatch(sibling.name)
        if match is None or match.group("date") != recording_date:
            continue
        sessions.append((int(match.group("index")), sibling))
    for index, session in sorted(sessions):
        if session_index is not None and index != session_index:
            continue
        directory = session / "data" / "tuning_curves" / probe_name
        for filename in TUNING_CURVE_FILENAMES:
            resolved = _resolve_existing_file(directory / filename)
            if resolved is not None:
                return resolved
    return None


def aggregate_tuning_curve(
    rates: Sequence[float],
    display_bins: int,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Average available raw HD rates and return display centers and rates."""

    if len(rates) != HD_RAW_BIN_COUNT:
        raise ValueError(f"Expected {HD_RAW_BIN_COUNT} raw HD rates; got {len(rates)}.")
    display_bins = normalize_hd_bin_count(display_bins)
    group_size = HD_RAW_BIN_COUNT // display_bins
    values: list[float] = []
    for start in range(0, HD_RAW_BIN_COUNT, group_size):
        group = tuple(
            float(value)
            for value in rates[start : start + group_size]
            if math.isfinite(float(value))
        )
        values.append(sum(group) / len(group) if group else math.nan)
    bin_width_deg = 360.0 / display_bins
    centers = tuple((index + 0.5) * bin_width_deg for index in range(display_bins))
    return centers, tuple(values)


def aggregate_tuning_counts(
    spike_counts: Sequence[int],
    occupancy_time_s: Sequence[float],
    display_bins: int,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Aggregate counts and occupancy before converting to firing rate."""

    centers, counts, occupancy = aggregate_tuning_observations(
        spike_counts,
        occupancy_time_s,
        display_bins,
    )
    return centers, tuple(
        count / occupied_s if occupied_s > 0.0 else math.nan
        for count, occupied_s in zip(counts, occupancy)
    )


def aggregate_tuning_observations(
    spike_counts: Sequence[float],
    occupancy_time_s: Sequence[float],
    display_bins: int,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """Return grouped angle centers, spike counts, and occupancy seconds."""

    if len(spike_counts) != HD_RAW_BIN_COUNT:
        raise ValueError(f"Expected {HD_RAW_BIN_COUNT} spike-count bins; got {len(spike_counts)}.")
    if len(occupancy_time_s) != HD_RAW_BIN_COUNT:
        raise ValueError(
            f"Expected {HD_RAW_BIN_COUNT} occupancy-time bins; got {len(occupancy_time_s)}."
    )
    display_bins = normalize_hd_bin_count(display_bins)
    group_size = HD_RAW_BIN_COUNT // display_bins
    counts: list[float] = []
    occupancy: list[float] = []
    for start in range(0, HD_RAW_BIN_COUNT, group_size):
        stop = start + group_size
        counts.append(sum(float(value) for value in spike_counts[start:stop]))
        occupancy.append(
            sum(float(value) for value in occupancy_time_s[start:stop])
        )
    bin_width_deg = 360.0 / display_bins
    centers = tuple((index + 0.5) * bin_width_deg for index in range(display_bins))
    return centers, tuple(counts), tuple(occupancy)


def tuning_smoothing_sigma(sigma: float, display_bins: int) -> float:
    """Keep smoothing at a fixed angular width as the display bin count changes."""

    sigma = float(sigma)
    if not math.isfinite(sigma) or sigma <= 0.0:
        raise ValueError("Tuning-curve smoothing sigma must be positive and finite.")
    display_bins = normalize_hd_bin_count(display_bins)
    return sigma * display_bins / DEFAULT_HD_DISPLAY_BINS


@lru_cache(maxsize=64)
def _circular_gaussian_kernel(sigma: float) -> tuple[tuple[int, float], ...]:
    """Return SciPy-compatible order-zero Gaussian weights and offsets."""

    radius = int(GAUSSIAN_TRUNCATE * sigma + 0.5)
    offsets = range(-radius, radius + 1)
    weights = [math.exp(-0.5 * (offset / sigma) ** 2) for offset in offsets]
    weight_total = sum(weights)
    return tuple(
        (offset, weight / weight_total)
        for offset, weight in zip(range(-radius, radius + 1), weights)
    )


def smooth_tuning_curve(rates: Sequence[float], sigma: float) -> tuple[float, ...]:
    sigma = float(sigma)
    if not math.isfinite(sigma) or sigma <= 0.0:
        raise ValueError("Tuning-curve smoothing sigma must be positive and finite.")
    values = tuple(float(value) for value in rates)
    if not values:
        return ()
    kernel = _circular_gaussian_kernel(sigma)
    count = len(values)
    return tuple(
        sum(
            weight * values[(index + offset) % count]
            for offset, weight in kernel
        )
        for index in range(count)
    )


def smooth_tuning_counts(
    spike_counts: Sequence[int],
    occupancy_time_s: Sequence[float],
    display_bins: int,
    sigma: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Smooth raw counts and occupancy, aggregate them, then compute rates."""

    sigma_bins = tuning_smoothing_sigma(sigma, HD_RAW_BIN_COUNT)
    smoothed_counts = smooth_tuning_curve(spike_counts, sigma_bins)
    smoothed_occupancy = smooth_tuning_curve(occupancy_time_s, sigma_bins)
    centers, counts, occupancy = aggregate_tuning_observations(
        smoothed_counts,
        smoothed_occupancy,
        display_bins,
    )
    return centers, tuple(
        count / occupied_s if occupied_s > 1e-12 else math.nan
        for count, occupied_s in zip(counts, occupancy)
    )


def smooth_tuning_rates_missing_aware(
    rates: Sequence[float],
    sigma: float,
) -> tuple[float, ...]:
    """Circularly smooth raw rates without treating missing bins as zero Hz."""

    values = tuple(float(value) for value in rates)
    observed = tuple(1.0 if math.isfinite(value) else 0.0 for value in values)
    numerator = smooth_tuning_curve(
        tuple(value if math.isfinite(value) else 0.0 for value in values),
        sigma,
    )
    denominator = smooth_tuning_curve(observed, sigma)
    return tuple(
        value / weight if weight > 1e-12 else math.nan
        for value, weight in zip(numerator, denominator)
    )


def tuning_rate_peak(rates: Sequence[float]) -> float:
    return max(
        (float(rate) for rate in rates if math.isfinite(float(rate))),
        default=0.0,
    )


def processed_tuning_curve(
    rates: Sequence[float],
    display_bins: int,
    *,
    smoothing: bool,
    sigma: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    display_bins = normalize_hd_bin_count(display_bins)
    source_rates = (
        smooth_tuning_rates_missing_aware(
            rates,
            tuning_smoothing_sigma(sigma, HD_RAW_BIN_COUNT),
        )
        if smoothing
        else rates
    )
    return aggregate_tuning_curve(source_rates, display_bins)


def center_tuning_curve_on_zero(
    angles_deg: Sequence[float],
    rates: Sequence[float],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Wrap clockwise HD onto the RF -180..180 axis without mirroring it."""

    if len(angles_deg) != len(rates):
        raise ValueError("Tuning-curve angles and rates must have the same length.")
    centered = sorted(
        (
            ((float(angle) + 180.0) % 360.0) - 180.0,
            float(rate),
        )
        for angle, rate in zip(angles_deg, rates)
    )
    return (
        tuple(angle for angle, _rate in centered),
        tuple(rate for _angle, rate in centered),
    )


def head_direction_unit_vector(angle_deg: float) -> tuple[float, float]:
    """Map HD degrees to Canvas coordinates: 0 north, positive clockwise."""

    radians = math.radians(float(angle_deg))
    return math.sin(radians), -math.cos(radians)


@dataclass(frozen=True, slots=True)
class ProbeChannel:
    """One physical probe channel loaded from a companion ``channels.csv``."""

    channel_id: int
    x_um: float
    y_um: float
    shank_id: int
    channel_index: int = 0
    raw_channel_index: int = 0


@dataclass(frozen=True, slots=True)
class ProbeUnitPosition:
    """One unit location loaded from the required companion ``positions.csv``."""

    unit_id: int
    x_um: float | None
    y_um: float | None
    unit_index: int = 0


@dataclass(frozen=True)
class SpatialRegion:
    """A physical probe-space selection used to filter RF units."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @classmethod
    def from_corners(
        cls, x0: float, y0: float, x1: float, y1: float
    ) -> SpatialRegion:
        return cls(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    @classmethod
    def centered(
        cls,
        x_um: float,
        y_um: float,
        width_um: float = PROBE_CLICK_WIDTH_UM,
        height_um: float = PROBE_CLICK_HEIGHT_UM,
    ) -> SpatialRegion:
        return cls.from_corners(
            x_um - width_um / 2.0,
            y_um - height_um / 2.0,
            x_um + width_um / 2.0,
            y_um + height_um / 2.0,
        )

    def contains(self, x_um: float, y_um: float) -> bool:
        return self.x_min <= x_um <= self.x_max and self.y_min <= y_um <= self.y_max


@dataclass(frozen=True, slots=True)
class ProbeGeometry:
    """Immutable probe geometry captured for a figure-composer session."""

    probe_name: str
    positions_path: Path
    channels_path: Path | None
    channels: tuple[ProbeChannel, ...]
    units: tuple[ProbeUnitPosition, ...]

    @property
    def units_by_id(self) -> dict[int, ProbeUnitPosition]:
        return {unit.unit_id: unit for unit in self.units}

    @property
    def positioned_units(self) -> tuple[ProbeUnitPosition, ...]:
        return tuple(
            unit
            for unit in self.units
            if unit.x_um is not None and unit.y_um is not None
        )

    def unit_ids_in_region(
        self,
        region: SpatialRegion,
        available_ids: Sequence[int],
    ) -> list[int]:
        positions = self.units_by_id
        return [
            int(unit_id)
            for unit_id in available_ids
            if int(unit_id) in positions
            and positions[int(unit_id)].x_um is not None
            and positions[int(unit_id)].y_um is not None
            and region.contains(
                float(positions[int(unit_id)].x_um),
                float(positions[int(unit_id)].y_um),
            )
        ]


def _finite_csv_float(value: str | None, label: str) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _probe_unit_coordinates(
    x_value: str | None,
    y_value: str | None,
) -> tuple[float | None, float | None]:
    """Accept only a finite position or SpikeInterface's explicit nan,nan."""

    try:
        x_um = float(x_value)  # type: ignore[arg-type]
        y_um = float(y_value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("unit x_um and y_um must be numeric") from exc
    if math.isnan(x_um) and math.isnan(y_um):
        return None, None
    if not math.isfinite(x_um) or not math.isfinite(y_um):
        raise ValueError(
            "unit x_um and y_um must both be finite or both be nan"
        )
    return x_um, y_um


def _csv_integer(value: str | None, label: str) -> int:
    parsed = _finite_csv_float(value, label)
    if not parsed.is_integer():
        raise ValueError(f"{label} must be an integer")
    return int(parsed)


def _read_probe_csv(
    path: Path,
    required_columns: tuple[str, ...],
) -> tuple[dict[str, int], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        if fieldnames is None:
            raise ValueError(f"{path.name} is missing a header")
        duplicate_columns = sorted(
            {name for name in fieldnames if fieldnames.count(name) > 1}
        )
        if duplicate_columns:
            raise ValueError(
                f"{path.name} contains duplicate columns: {', '.join(duplicate_columns)}"
            )
        missing = [column for column in required_columns if column not in fieldnames]
        if missing:
            raise ValueError(
                f"{path.name} is missing required columns: {', '.join(missing)}"
            )
        rows = list(reader)
    return {name: index for index, name in enumerate(fieldnames)}, rows


def probe_name_for_json(path: Path) -> str | None:
    """Infer ProbeA/ProbeB from RF filenames and containing directories."""

    filename_match = re.search(r"(?:^|[\s_-])([ab])$", path.stem, re.IGNORECASE)
    if filename_match:
        return f"Probe{filename_match.group(1).upper()}"
    for part in (path.name, *(parent.name for parent in path.parents)):
        match = re.search(r"probe[\s_-]*([ab])(?:\b|[_-])", part, re.IGNORECASE)
        if match:
            return f"Probe{match.group(1).upper()}"
    return None


def probe_name_for_rf(path: str | Path) -> str | None:
    """Use the full legacy/current filename vocabulary for probe inference."""

    return probe_name_for_json(Path(path))


def _geometry_path_pairs(
    base: Path, probe_name: str
) -> tuple[tuple[Path, Path | None], ...]:
    layouts = (
        (
            base / "spike_position" / probe_name,
            base / "waveform" / probe_name / "channels.csv",
        ),
        (base / probe_name, base / probe_name / "channels.csv"),
        (base, base / "channels.csv"),
    )
    return tuple(
        (directory / filename, channels)
        for directory, channels in layouts
        for filename in PROBE_POSITION_FILENAMES
    )


def _probe_geometry_search_roots(
    json_path: Path,
    data_root: Path | None,
) -> list[Path]:
    roots: list[Path] = []
    if data_root is not None:
        roots.append(data_root.expanduser())
    elif configured := os.environ.get("RF_MAPPING_PROBE_DATA_ROOT"):
        roots.append(Path(configured).expanduser())

    source = json_path.expanduser()
    parents = tuple(source.parents)
    session = next(
        (parent for parent in parents if _RECORDING_SESSION_RE.fullmatch(parent.name)),
        None,
    )
    if session is not None:
        boundary = next(
            (
                parent
                for parent in parents
                if parent.name == "data" and parent.parent == session
            ),
            session,
        )
        for parent in parents:
            roots.append(parent)
            if parent == boundary:
                break
    elif data_boundary := next(
        (parent for parent in parents if parent.name == "data"),
        None,
    ):
        for parent in parents:
            roots.append(parent)
            if parent == data_boundary:
                break
    else:
        # Compact fixtures and manual exports may keep geometry one or two
        # directory levels above the JSON, but never require a walk to root.
        roots.extend(parents[:2])

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def discover_probe_geometry_paths(
    rf_path: str | Path,
    *,
    data_root: Path | None = None,
) -> tuple[str, Path, Path | None] | None:
    """Discover session probe geometry beside an RF mapping JSON.

    The active pipeline stores unit positions below ``data/spike_position``
    and physical channels below ``data/waveform``. Both ``positions.probe``
    and the legacy ``positions.csv`` name are accepted in every supported
    layout; ``channels.csv`` remains the optional physical-channel companion.
    """

    source = Path(rf_path).expanduser()
    probe_name = probe_name_for_json(source)
    if probe_name is None:
        return None
    first_malformed: tuple[str, Path, Path | None] | None = None
    for base in _probe_geometry_search_roots(source, data_root):
        for positions_path, channels_path in _geometry_path_pairs(base, probe_name):
            resolved_positions = _resolve_existing_file(positions_path)
            if resolved_positions is None:
                continue
            candidate = (
                probe_name,
                resolved_positions,
                _resolve_existing_file(channels_path) if channels_path else None,
            )
            try:
                _read_probe_csv(
                    resolved_positions,
                    ("unit_index", "unit_id", "x_um", "y_um"),
                )
            except (OSError, ValueError):
                # Keep the first malformed candidate so RFMappingData can
                # surface its precise validation error if no valid fallback
                # exists, while still allowing a later trusted root to win.
                if first_malformed is None:
                    first_malformed = candidate
                continue
            return candidate
    return first_malformed


def load_probe_geometry(
    probe_name_or_positions: str | Path,
    positions_path: Path | None = None,
    channels_path: Path | None = None,
    *,
    probe_name: str | None = None,
    infer_sibling_channels: bool = True,
) -> ProbeGeometry:
    """Load validated unit positions and optional channel sites from CSV."""

    if probe_name is not None:
        # Legacy API: load_probe_geometry(positions, channels, probe_name=...).
        if channels_path is not None:
            raise TypeError("channels_path was provided twice")
        channels_path = positions_path
        positions_path = Path(probe_name_or_positions)
        normalized_probe_name = probe_name
    else:
        # Canonical API: load_probe_geometry(probe_name, positions, channels).
        if positions_path is None:
            # Compact legacy API without an explicit probe name.
            positions_path = Path(probe_name_or_positions)
            normalized_probe_name = "Probe"
        else:
            normalized_probe_name = str(probe_name_or_positions)

    positions_resolved = _resolve_existing_file(positions_path)
    if positions_resolved is None:
        raise ValueError(f"CSV file not found: {positions_path}")
    if channels_path is None and infer_sibling_channels:
        sibling = positions_resolved.with_name("channels.csv")
        channels_path = sibling if sibling.is_file() else None

    _fields, position_rows = _read_probe_csv(
        positions_resolved,
        ("unit_index", "unit_id", "x_um", "y_um"),
    )
    units: list[ProbeUnitPosition] = []
    seen_unit_ids: set[int] = set()
    for row_number, row in enumerate(position_rows, start=2):
        try:
            unit_index = _csv_integer(row.get("unit_index"), "unit_index")
            unit_id = _csv_integer(row.get("unit_id"), "unit_id")
            x_um, y_um = _probe_unit_coordinates(
                row.get("x_um"), row.get("y_um")
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid positions.csv value on row {row_number}: {exc}"
            ) from exc
        if unit_id in seen_unit_ids:
            raise ValueError(f"Duplicate unit_id {unit_id} in positions.csv")
        seen_unit_ids.add(unit_id)
        units.append(ProbeUnitPosition(unit_id, x_um, y_um, unit_index))

    channels: list[ProbeChannel] = []
    validated_channels_path = (
        _resolve_existing_file(channels_path) if channels_path is not None else None
    )
    if validated_channels_path is not None:
        try:
            _fields, channel_rows = _read_probe_csv(
                validated_channels_path,
                (
                    "channel_index",
                    "channel_id",
                    "raw_channel_index",
                    "x_um",
                    "y_um",
                    "shank_id",
                ),
            )
            for row_number, row in enumerate(channel_rows, start=2):
                try:
                    channel_index = _csv_integer(row.get("channel_index"), "channel_index")
                    raw_channel_index = _csv_integer(row.get("raw_channel_index"), "raw_channel_index")
                    channel_id = _csv_integer(row.get("channel_id"), "channel_id")
                    x_um = _finite_csv_float(row.get("x_um"), "channel x_um")
                    y_um = _finite_csv_float(row.get("y_um"), "channel y_um")
                    shank_id = _csv_integer(row.get("shank_id"), "shank_id")
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid channels.csv value on row {row_number}: {exc}"
                    ) from exc
                channels.append(
                    ProbeChannel(
                        channel_id,
                        x_um,
                        y_um,
                        shank_id,
                        channel_index,
                        raw_channel_index,
                    )
                )
        except (OSError, ValueError):
            # Unit positions are sufficient for a useful probe plot.  An
            # optional stale/malformed channel file must not suppress them.
            channels = []
            validated_channels_path = None

    return ProbeGeometry(
        probe_name=normalized_probe_name,
        positions_path=positions_resolved,
        channels_path=validated_channels_path,
        channels=tuple(channels),
        units=tuple(units),
    )


def discover_probe_geometry(
    json_path: Path,
    *,
    data_root: Path | None = None,
) -> ProbeGeometry | None:
    """Load probe geometry using the bounded current-session policy.

    ``data_root`` remains an explicit opt-in for compact fixtures and manually
    curated layouts; normal discovery never walks outside the RF data scope.
    """

    if data_root is not None:
        probe_name = probe_name_for_rf(json_path)
        if probe_name is None:
            return None
        for positions, channels in _geometry_path_pairs(data_root, probe_name):
            if positions.is_file():
                return load_probe_geometry(
                    probe_name,
                    positions.resolve(),
                    channels.resolve() if channels.is_file() else None,
                )
        return None

    probe_name = probe_name_for_json(json_path)
    if probe_name is None:
        return None
    positions_only: ProbeGeometry | None = None
    for root in _probe_geometry_search_roots(json_path, data_root):
        for positions, channels in _geometry_path_pairs(root, probe_name):
            positions_resolved = _resolve_existing_file(positions)
            if positions_resolved is None:
                continue
            channels_resolved = (
                _resolve_existing_file(channels) if channels is not None else None
            )
            try:
                geometry = load_probe_geometry(
                    probe_name,
                    positions_resolved,
                    channels_resolved,
                )
            except (OSError, ValueError):
                if channels_resolved is None:
                    continue
                try:
                    geometry = load_probe_geometry(
                        probe_name,
                        positions_resolved,
                        None,
                        infer_sibling_channels=False,
                    )
                except (OSError, ValueError):
                    continue
            if geometry.channels:
                return geometry
            if positions_only is None:
                positions_only = geometry
    return positions_only
