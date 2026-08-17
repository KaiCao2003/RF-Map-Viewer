from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .paths import (
    has_supported_probe_suffix,
    has_supported_tuning_suffix,
    is_within,
)


HD_RAW_BIN_COUNT = 180
TUNING_TOP_LEVEL_KEYS = (
    "metadata",
    "angle_bin_edges_deg",
    "occupancy_samples",
    "occupancy_time_s",
    "unit_id",
    "spike_counts",
    "firing_rate_hz",
    "unit_data",
)
TUNING_UNIT_DATA_KEYS = (
    "hd_class",
    "rate_mvl",
    "spike_angle_mrl",
    "rayleigh_score",
    "rayleigh_p",
    "rayleigh_significant",
    "shuffle_p",
    "shuffle_significant",
)
PROBE_RE = re.compile(r"^probe[ab]$", re.IGNORECASE)
SESSION_RE = re.compile(r"^(?P<date>\d{6,8})_(?P<index>\d+)$")


@dataclass(frozen=True)
class TuningUnit:
    unit_id: int
    rates: tuple[float | None, ...]
    spike_counts: tuple[int, ...]
    hd_class: int | None = None


@dataclass(frozen=True)
class TuningCurveData:
    path: Path
    units: tuple[TuningUnit, ...]
    occupancy_time_s: tuple[float, ...]
    metadata: dict[str, Any] | None = None

    @property
    def units_by_id(self) -> dict[int, TuningUnit]:
        return {unit.unit_id: unit for unit in self.units}


@dataclass(frozen=True)
class CompanionSet:
    probe: str | None
    channels_path: Path | None
    positions_path: Path | None
    tuning_path: Path | None
    # Old image export endpoints remain available for compatibility, but these
    # Ad-hoc image artifacts do not make the current HD tuning view available.
    hd_summary_path: Path | None = None
    hd_curve_path: Path | None = None
    hd_image_roots: tuple[Path, ...] = ()

    @property
    def has_probe(self) -> bool:
        return self.positions_path is not None

    @property
    def has_hd(self) -> bool:
        return self.tuning_path is not None


def _safe_file(path: Path, root: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return None
    return resolved if resolved.is_file() and is_within(resolved, root) else None


def _safe_directory(path: Path, root: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return None
    return resolved if resolved.is_dir() and is_within(resolved, root) else None


def _ancestors(source: Path, root: Path) -> list[Path]:
    result: list[Path] = []
    current = source.parent
    while is_within(current, root):
        result.append(current)
        if current == root:
            break
        current = current.parent
    return result


def probe_name_for_json(path: Path) -> str | None:
    """Infer ProbeA/ProbeB exactly as the desktop viewer does."""

    filename_match = re.search(r"(?:^|[\s_-])([ab])$", path.stem, re.IGNORECASE)
    if filename_match:
        return f"Probe{filename_match.group(1).upper()}"
    for part in (path.name, *(parent.name for parent in path.parents)):
        match = re.search(r"probe[\s_-]*([ab])(?:\b|[_-])", part, re.IGNORECASE)
        if match:
            return f"Probe{match.group(1).upper()}"
    return None


def _first_file(paths: Iterable[Path], root: Path) -> Path | None:
    for path in paths:
        found = _safe_file(path, root)
        if found is not None:
            return found
    return None


def discover_tuning_curve_path(rf_json_path: Path, scope_root: Path) -> Path | None:
    """Find the first numeric session's tuning JSON for this date and probe."""

    root = scope_root.resolve(strict=True)
    probe_name = probe_name_for_json(rf_json_path)
    if probe_name is None:
        return None
    session_dir: Path | None = None
    session_match: re.Match[str] | None = None
    for candidate in (rf_json_path.parent, *rf_json_path.parents):
        match = SESSION_RE.fullmatch(candidate.name)
        if match is not None and is_within(candidate, root):
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
        try:
            is_directory = sibling.is_dir()
        except OSError:
            continue
        if not is_directory:
            continue
        match = SESSION_RE.fullmatch(sibling.name)
        if match is None or match.group("date") != recording_date:
            continue
        sessions.append((int(match.group("index")), sibling))
    for _index, session in sorted(sessions):
        tuning_directory = session / "data" / "tuning_curves" / probe_name
        for filename in ("tuning_curves.tc", "tuning_curves.json"):
            resolved = _safe_file(tuning_directory / filename, root)
            if resolved is not None:
                return resolved
    return None


def _geometry_path_pairs(
    base: Path, probe_name: str
) -> tuple[tuple[Path, Path | None], ...]:
    return (
        (
            base / "spike_position" / probe_name / "positions.probe",
            base / "waveform" / probe_name / "channels.csv",
        ),
        (
            base / "spike_position" / probe_name / "positions.csv",
            base / "waveform" / probe_name / "channels.csv",
        ),
        (base / probe_name / "positions.probe", base / probe_name / "channels.csv"),
        (base / probe_name / "positions.csv", base / probe_name / "channels.csv"),
        (base / "positions.probe", base / "channels.csv"),
        (base / "positions.csv", base / "channels.csv"),
    )


def _discover_probe_paths(
    source: Path, root: Path, probe_name: str
) -> tuple[Path | None, Path | None]:
    parents = tuple(source.parents)
    session = next(
        (
            parent
            for parent in parents
            if SESSION_RE.fullmatch(parent.name) and is_within(parent, root)
        ),
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
    else:
        boundary = next(
            (
                parent
                for parent in parents
                if parent.name == "data" and is_within(parent, root)
            ),
            source.parent,
        )
    bases: list[Path] = []
    for parent in parents:
        bases.append(parent)
        if parent == boundary:
            break
    # Probe geometry is session-specific. Resolve candidates against this
    # bounded scientific scope, never merely against the much broader RF root.
    for base in bases:
        for positions, channels in _geometry_path_pairs(base, probe_name):
            positions_resolved = _safe_file(positions, boundary)
            if positions_resolved is None:
                continue
            return (
                positions_resolved,
                _safe_file(channels, boundary) if channels else None,
            )
    return None, None


def _legacy_artifacts(
    source: Path, root: Path, probe: str
) -> tuple[Path | None, Path | None, tuple[Path, ...]]:
    anchors = _ancestors(source, root)
    summary = _first_file(
        (anchor / f"tc_summary_{probe}.csv" for anchor in anchors), root
    )
    curve_candidates: list[Path] = []
    image_candidates: list[Path] = []
    for anchor in anchors:
        curve_candidates.extend(
            (
                anchor / "hd_tuning_curves.csv",
                anchor / "tuning_curves" / probe / "hd_tuning_curves.csv",
                anchor / "tc_curve" / probe / "hd_tuning_curves.csv",
            )
        )
        image_candidates.extend(
            (
                anchor / "tc_curve" / probe,
                anchor / "tuning_curves" / probe,
            )
        )
    curve = _first_file(curve_candidates, root)
    image_roots: list[Path] = []
    for candidate in image_candidates:
        found = _safe_directory(candidate, root)
        if found is None:
            continue
        try:
            has_hd_png = any(
                child.is_file()
                and child.suffix.casefold() == ".png"
                and "hd" in child.name.casefold()
                for child in found.iterdir()
            )
        except OSError:
            has_hd_png = False
        if has_hd_png:
            image_roots.append(found)
    return summary, curve, tuple(dict.fromkeys(image_roots))


def discover_companions(source: Path, scope_root: Path) -> CompanionSet:
    root = scope_root.resolve(strict=True)
    probe = probe_name_for_json(source)
    if probe is None:
        return CompanionSet(None, None, None, None)
    positions, channels = _discover_probe_paths(source, root, probe)
    tuning = discover_tuning_curve_path(source, root)
    summary, curve, image_roots = _legacy_artifacts(source, root, probe)
    return CompanionSet(
        probe=probe,
        channels_path=channels,
        positions_path=positions,
        tuning_path=tuning,
        hd_summary_path=summary,
        hd_curve_path=curve,
        hd_image_roots=image_roots,
    )


def infer_channels_path(
    positions_path: Path, scope_root: Path, probe_name: str
) -> Path | None:
    root = scope_root.resolve(strict=True)
    sibling = _safe_file(positions_path.with_name("channels.csv"), root)
    if sibling is not None:
        return sibling
    for ancestor in positions_path.parents:
        if ancestor.name == "spike_position":
            return _safe_file(
                ancestor.parent / "waveform" / probe_name / "channels.csv", root
            )
        if ancestor == root:
            break
    return None


def companion_for_positions(
    companions: CompanionSet, positions_path: Path, scope_root: Path
) -> CompanionSet:
    root = scope_root.resolve(strict=True)
    resolved = _safe_file(positions_path, root)
    if resolved is None:
        raise ValueError(f"Probe-position file not found: {positions_path}")
    if not has_supported_probe_suffix(resolved):
        raise ValueError("Probe-position file must end with .probe or .csv")
    parent_probe = resolved.parent.name
    probe = (
        f"Probe{parent_probe[-1].upper()}"
        if PROBE_RE.fullmatch(parent_probe)
        else companions.probe or "Probe"
    )
    return CompanionSet(
        probe=probe,
        channels_path=infer_channels_path(resolved, root, probe),
        positions_path=resolved,
        tuning_path=companions.tuning_path,
        hd_summary_path=companions.hd_summary_path,
        hd_curve_path=companions.hd_curve_path,
        hd_image_roots=companions.hd_image_roots,
    )


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        return fields, list(reader)


def _require_columns(
    path: Path, fields: Sequence[str], required: Sequence[str]
) -> None:
    missing = [name for name in required if name not in fields]
    if missing:
        raise ValueError(f"{path.name} is missing columns: {', '.join(missing)}")


def _finite_csv_float(value: str, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _probe_unit_coordinates(
    x_value: str, y_value: str
) -> tuple[float | None, float | None]:
    """Return one finite unit position or an explicit missing-coordinate pair."""

    try:
        x = float(x_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid unit x: {x_value!r}") from exc
    try:
        y = float(y_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid unit y: {y_value!r}") from exc
    # SpikeInterface writes ``nan,nan`` when a unit has no estimated location.
    # Preserve that unit as unavailable geometry so the viewer can keep the
    # probe background visible and label the selected position as NaN.
    if math.isnan(x) and math.isnan(y):
        return None, None
    if not math.isfinite(x):
        raise ValueError("unit x must be finite")
    if not math.isfinite(y):
        raise ValueError("unit y must be finite")
    return x, y


def _csv_integer(value: str, label: str) -> int:
    parsed = _finite_csv_float(value, label)
    if not parsed.is_integer():
        raise ValueError(f"{label} must be an integer")
    return int(parsed)


def _load_probe_channels(path: Path) -> list[dict[str, int | float]]:
    fields, rows = _read_csv(path)
    _require_columns(
        path,
        fields,
        (
            "channel_index",
            "channel_id",
            "raw_channel_index",
            "x_um",
            "y_um",
            "shank_id",
        ),
    )
    channels: list[dict[str, int | float]] = []
    for row_number, row in enumerate(rows, start=2):
        try:
            # Parse the fields that the exact viewer validates even though the
            # Web response only needs the public channel ID and geometry.
            _csv_integer(row["channel_index"], "channel_index")
            _csv_integer(row["raw_channel_index"], "raw_channel_index")
            channel_id = _csv_integer(row["channel_id"], "channel_id")
            x = _finite_csv_float(row["x_um"], "channel x")
            y = _finite_csv_float(row["y_um"], "channel y")
            shank = _csv_integer(row["shank_id"], "shank_id")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid channels.csv value on row {row_number}: {exc}"
            ) from exc
        channels.append({"channelId": channel_id, "x": x, "y": y, "shank": shank})
    return channels


def load_probe_geometry(
    companions: CompanionSet,
    unit_pool: Sequence[int] | None = None,
) -> dict[str, Any] | None:
    if not companions.has_probe or companions.probe is None:
        return None
    assert companions.positions_path is not None
    fields, rows = _read_csv(companions.positions_path)
    _require_columns(
        companions.positions_path,
        fields,
        ("unit_index", "unit_id", "x_um", "y_um"),
    )
    units: list[dict[str, int | float | None]] = []
    seen_unit_ids: set[int] = set()
    for row_number, row in enumerate(rows, start=2):
        try:
            _csv_integer(row["unit_index"], "unit_index")
            unit_id = _csv_integer(row["unit_id"], "unit_id")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid {companions.positions_path.name} value on row {row_number}: {exc}"
            ) from exc
        if unit_id in seen_unit_ids:
            raise ValueError(
                f"Duplicate unit_id {unit_id} in {companions.positions_path.name}"
            )
        seen_unit_ids.add(unit_id)
        try:
            coordinates = _probe_unit_coordinates(row["x_um"], row["y_um"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid {companions.positions_path.name} value on row {row_number}: {exc}"
            ) from exc
        x, y = coordinates
        units.append({"unitId": unit_id, "x": x, "y": y})

    if unit_pool is not None:
        allowed = set(unit_pool)
        units = [unit for unit in units if unit["unitId"] in allowed]
        if not units:
            raise ValueError(
                f"{companions.positions_path.name} contains no unit IDs from this RF dataset's unitPool"
            )

    channels: list[dict[str, int | float]] = []
    if companions.channels_path is not None:
        try:
            channels = _load_probe_channels(companions.channels_path)
        except (OSError, ValueError):
            # Keep usable unit positions when an optional channel
            # file is absent or malformed.
            channels = []
    return {"probe": companions.probe, "channels": channels, "units": units}


def _json_number(value: Any, label: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric.")
    try:
        parsed = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be finite.") from exc
    if not math.isfinite(parsed) or (nonnegative and parsed < 0.0):
        suffix = " finite and non-negative" if nonnegative else " finite"
        raise ValueError(f"{label} must be{suffix}.")
    return parsed


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid non-finite JSON value: {value}.")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate tuning-curve JSON key: {key}.")
        result[key] = value
    return result


def _require_finite_json(value: Any, context: str = "tuning-curve JSON") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{context} contains a non-finite number.")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_finite_json(item, f"{context}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _require_finite_json(item, f"{context}.{key}")


def _metadata_string(payload: Mapping[str, Any], key: str, context: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Tuning-curve {context}.{key} must be a string or null.")
    return value


def _metadata_float(payload: Mapping[str, Any], key: str, context: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    return _json_number(value, f"Tuning-curve {context}.{key}")


def _metadata_int(payload: Mapping[str, Any], key: str, context: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"Tuning-curve {context}.{key} must be an integer or null.")
    return int(value)


def _metadata_bool(payload: Mapping[str, Any], key: str, context: str) -> bool | None:
    value = payload.get(key)
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"Tuning-curve {context}.{key} must be boolean or null.")
    return bool(value)


def _metadata_int_list(
    payload: Mapping[str, Any], key: str, context: str
) -> list[int] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or any(type(item) is not int for item in value):
        raise ValueError(
            f"Tuning-curve {context}.{key} must be an integer list or null."
        )
    return [int(item) for item in value]


def _validated_metadata(raw_metadata: Any) -> dict[str, Any] | None:
    if raw_metadata is None:
        return None
    if not isinstance(raw_metadata, dict):
        raise ValueError("Tuning-curve metadata must be an object or null.")
    # Keep source metadata intact for the Info view. The contract defines types
    # for the fields below, so validate and normalize those fields while
    # preserving forward-compatible extras such as epoch/headplate details.
    result: dict[str, Any] = dict(raw_metadata)
    string_fields = (
        "session",
        "probe",
        "kilosort_dir",
        "timebase",
        "timestamp_reference",
        "angle_convention_note",
    )
    for key in string_fields:
        if key in raw_metadata:
            result[key] = _metadata_string(raw_metadata, key, "metadata")
    for key in ("adc_time_origin_raw_s", "feature_fs_hz"):
        if key in raw_metadata:
            result[key] = _metadata_float(raw_metadata, key, "metadata")
    if "num_angle_bins" in raw_metadata:
        result["num_angle_bins"] = _metadata_int(
            raw_metadata, "num_angle_bins", "metadata"
        )

    classification_raw = raw_metadata.get("classification")
    if classification_raw is not None:
        if not isinstance(classification_raw, dict):
            raise ValueError(
                "Tuning-curve metadata.classification must be an object or null."
            )
        context = "metadata.classification"
        classification: dict[str, Any] = dict(classification_raw)
        for key in (
            "method",
            "class_0",
            "class_1",
            "class_2",
            "class_null",
            "rayleigh_test",
        ):
            if key in classification_raw:
                classification[key] = _metadata_string(classification_raw, key, context)
        for key in ("rayleigh_alpha", "shuffle_alpha"):
            if key in classification_raw:
                classification[key] = _metadata_float(classification_raw, key, context)
        for key in ("num_shuffle", "shuffle_seed"):
            if key in classification_raw:
                classification[key] = _metadata_int(classification_raw, key, context)
        result["classification"] = classification
    elif "classification" in raw_metadata:
        result["classification"] = None

    ttl_raw = raw_metadata.get("ttl_qc")
    if ttl_raw is not None:
        if not isinstance(ttl_raw, dict):
            raise ValueError("Tuning-curve metadata.ttl_qc must be an object or null.")
        context = "metadata.ttl_qc"
        ttl_qc: dict[str, Any] = dict(ttl_raw)
        for key in (
            "ttl_pulse_count",
            "camera_input_channel",
            "motive_frame_count_raw",
            "matched_motive_frame_count",
        ):
            if key in ttl_raw:
                ttl_qc[key] = _metadata_int(ttl_raw, key, context)
        for key in (
            "first_exposure_s",
            "last_exposure_s",
            "median_period_s",
            "measured_rate_hz",
            "camera_ttl_threshold",
        ):
            if key in ttl_raw:
                ttl_qc[key] = _metadata_float(ttl_raw, key, context)
        if "camera_ttl_active_high" in ttl_raw:
            ttl_qc["camera_ttl_active_high"] = _metadata_bool(
                ttl_raw, "camera_ttl_active_high", context
            )
        if "dropped_motive_frame_ids" in ttl_raw:
            ttl_qc["dropped_motive_frame_ids"] = _metadata_int_list(
                ttl_raw, "dropped_motive_frame_ids", context
            )
        for key in (
            "frame_alignment_policy_requested",
            "frame_alignment_policy_applied",
            "frame_timestamp_mapping",
        ):
            if key in ttl_raw:
                ttl_qc[key] = _metadata_string(ttl_raw, key, context)
        result["ttl_qc"] = ttl_qc
    elif "ttl_qc" in raw_metadata:
        result["ttl_qc"] = None
    return result


def _load_tuning_curve_payload(
    path: Path, payload: Mapping[str, Any]
) -> TuningCurveData:
    metadata = _validated_metadata(payload.get("metadata"))
    if metadata is None:
        raise ValueError("Tuning-curve metadata must be an object.")
    if metadata.get("num_angle_bins") not in (None, HD_RAW_BIN_COUNT):
        raise ValueError(
            f"Tuning-curve metadata.num_angle_bins must equal {HD_RAW_BIN_COUNT}."
        )
    metadata_fs = metadata.get("feature_fs_hz")
    if metadata_fs is not None and float(metadata_fs) <= 0.0:
        raise ValueError("Tuning-curve metadata.feature_fs_hz must be positive.")
    classification = metadata.get("classification")
    rayleigh_alpha = 0.05
    shuffle_alpha = 0.01
    if isinstance(classification, dict):
        if classification.get("rayleigh_alpha") is not None:
            rayleigh_alpha = float(classification["rayleigh_alpha"])
        if classification.get("shuffle_alpha") is not None:
            shuffle_alpha = float(classification["shuffle_alpha"])
    if not 0.0 <= rayleigh_alpha <= 1.0:
        raise ValueError(
            "Tuning-curve metadata.classification.rayleigh_alpha must be between 0 and 1."
        )
    if not 0.0 <= shuffle_alpha <= 1.0:
        raise ValueError(
            "Tuning-curve metadata.classification.shuffle_alpha must be between 0 and 1."
        )

    raw_edges = payload.get("angle_bin_edges_deg")
    if not isinstance(raw_edges, list) or len(raw_edges) != HD_RAW_BIN_COUNT + 1:
        raise ValueError(
            f"Tuning-curve angle_bin_edges_deg must contain {HD_RAW_BIN_COUNT + 1} values."
        )
    edges: list[float] = []
    for index, raw_edge in enumerate(raw_edges):
        if isinstance(raw_edge, bool) or not isinstance(raw_edge, (int, float)):
            raise ValueError(f"Tuning-curve angle edge {index + 1} is not numeric.")
        edge = _json_number(raw_edge, f"Tuning-curve angle edge {index + 1}")
        edges.append(edge)
    if not all(after > before for before, after in zip(edges, edges[1:])):
        raise ValueError(
            "Tuning-curve angle_bin_edges_deg must be strictly increasing."
        )
    expected_width = 360.0 / HD_RAW_BIN_COUNT
    if not all(
        math.isclose(edge, index * expected_width, rel_tol=0.0, abs_tol=1e-8)
        for index, edge in enumerate(edges)
    ):
        raise ValueError("Tuning-curve angle bins must span 0–360° in 180 equal bins.")

    raw_occupancy_samples = payload.get("occupancy_samples")
    if (
        not isinstance(raw_occupancy_samples, list)
        or len(raw_occupancy_samples) != HD_RAW_BIN_COUNT
    ):
        raise ValueError(
            f"Tuning-curve occupancy_samples must contain {HD_RAW_BIN_COUNT} values."
        )
    occupancy_samples: list[int] = []
    for index, raw_value in enumerate(raw_occupancy_samples):
        if type(raw_value) is not int or raw_value < 0:
            raise ValueError(
                f"Tuning-curve occupancy sample {index + 1} must be a non-negative integer."
            )
        occupancy_samples.append(int(raw_value))

    raw_occupancy = payload.get("occupancy_time_s")
    if not isinstance(raw_occupancy, list) or len(raw_occupancy) != HD_RAW_BIN_COUNT:
        raise ValueError(
            f"Tuning-curve occupancy_time_s must contain {HD_RAW_BIN_COUNT} values."
        )
    occupancy: list[float] = []
    for index, raw_value in enumerate(raw_occupancy):
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(f"Tuning-curve occupancy time {index + 1} is not numeric.")
        value = _json_number(
            raw_value,
            f"Tuning-curve occupancy time {index + 1}",
            nonnegative=True,
        )
        occupancy.append(value)
    if not any(value > 0.0 for value in occupancy):
        raise ValueError(
            "Tuning-curve occupancy_time_s must contain positive occupancy."
        )

    sampling_rates: list[float] = []
    for index, (samples, occupied_s) in enumerate(zip(occupancy_samples, occupancy)):
        if (samples == 0) != (occupied_s == 0.0):
            raise ValueError(
                f"Tuning-curve occupancy bin {index + 1} has inconsistent samples and time."
            )
        if samples > 0:
            try:
                sampling_rate = samples / occupied_s
            except OverflowError as exc:
                raise ValueError(
                    f"Tuning-curve occupancy bin {index + 1} implies an invalid sampling rate."
                ) from exc
            if not math.isfinite(sampling_rate) or sampling_rate <= 0.0:
                raise ValueError(
                    f"Tuning-curve occupancy bin {index + 1} implies an invalid sampling rate."
                )
            sampling_rates.append(sampling_rate)
    reference_fs = sampling_rates[0]
    if not all(
        math.isclose(value, reference_fs, rel_tol=1e-9, abs_tol=1e-9)
        for value in sampling_rates[1:]
    ):
        raise ValueError(
            "Tuning-curve occupancy_samples and occupancy_time_s imply inconsistent sampling rates."
        )
    if metadata_fs is not None and not math.isclose(
        float(metadata_fs), reference_fs, rel_tol=1e-9, abs_tol=1e-9
    ):
        raise ValueError(
            "Tuning-curve metadata.feature_fs_hz does not match occupancy samples/time."
        )

    raw_unit_ids = payload.get("unit_id")
    if not isinstance(raw_unit_ids, list) or not raw_unit_ids:
        raise ValueError("Tuning-curve unit_id must be a non-empty list.")
    unit_ids: list[int] = []
    for unit_index, raw_unit_id in enumerate(raw_unit_ids):
        if type(raw_unit_id) is not int or raw_unit_id < 0:
            raise ValueError(
                f"Tuning-curve unit_id value {unit_index + 1} must be a non-negative integer."
            )
        unit_ids.append(int(raw_unit_id))
    if len(set(unit_ids)) != len(unit_ids):
        raise ValueError("Tuning-curve unit_id values must be unique.")

    raw_counts_matrix = payload.get("spike_counts")
    raw_rates_matrix = payload.get("firing_rate_hz")
    num_units = len(unit_ids)
    if not isinstance(raw_counts_matrix, list) or len(raw_counts_matrix) != num_units:
        raise ValueError(
            "Tuning-curve spike_counts row count must match unit_id length."
        )
    if not isinstance(raw_rates_matrix, list) or len(raw_rates_matrix) != num_units:
        raise ValueError(
            "Tuning-curve firing_rate_hz row count must match unit_id length."
        )

    raw_unit_data = payload.get("unit_data")
    if not isinstance(raw_unit_data, dict):
        raise ValueError("Tuning-curve unit_data must be an object.")
    missing_unit_data = [
        key for key in TUNING_UNIT_DATA_KEYS if key not in raw_unit_data
    ]
    unexpected_unit_data = [
        key for key in raw_unit_data if key not in TUNING_UNIT_DATA_KEYS
    ]
    if missing_unit_data:
        raise ValueError(
            f"Missing tuning-curve unit_data keys: {', '.join(missing_unit_data)}."
        )
    if unexpected_unit_data:
        raise ValueError(
            f"Unexpected tuning-curve unit_data keys: {', '.join(unexpected_unit_data)}."
        )
    unit_data: dict[str, list[Any]] = {}
    for key in TUNING_UNIT_DATA_KEYS:
        raw_column = raw_unit_data[key]
        if not isinstance(raw_column, list) or len(raw_column) != num_units:
            raise ValueError(f"Tuning-curve unit_data.{key} length must match unit_id.")
        unit_data[key] = raw_column

    def optional_number(
        key: str,
        unit_index: int,
        *,
        maximum: float | None = None,
    ) -> float | None:
        raw_value = unit_data[key][unit_index]
        if raw_value is None:
            return None
        value = _json_number(
            raw_value,
            f"Unit {unit_ids[unit_index]} {key}",
            nonnegative=True,
        )
        if maximum is not None and value > maximum:
            if not math.isclose(value, maximum, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError(
                    f"Unit {unit_ids[unit_index]} {key} must not exceed {maximum}."
                )
            value = maximum
        return value

    units: list[TuningUnit] = []
    for unit_index, unit_id in enumerate(unit_ids):
        raw_counts = raw_counts_matrix[unit_index]
        raw_rates = raw_rates_matrix[unit_index]
        if not isinstance(raw_counts, list) or len(raw_counts) != HD_RAW_BIN_COUNT:
            raise ValueError(
                f"Unit {unit_id} spike_counts must contain {HD_RAW_BIN_COUNT} values."
            )
        if not isinstance(raw_rates, list) or len(raw_rates) != HD_RAW_BIN_COUNT:
            raise ValueError(
                f"Unit {unit_id} firing_rate_hz must contain {HD_RAW_BIN_COUNT} values."
            )
        counts: list[int] = []
        rates: list[float | None] = []
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
                rate: float | None = None
            else:
                if isinstance(raw_rate, bool) or not isinstance(raw_rate, (int, float)):
                    raise ValueError(
                        f"Unit {unit_id} firing rate {bin_index + 1} is not numeric."
                    )
                rate = _json_number(
                    raw_rate,
                    f"Unit {unit_id} firing rate {bin_index + 1}",
                    nonnegative=True,
                )
                try:
                    expected_rate = count / occupied_s
                except OverflowError as exc:
                    raise ValueError(
                        f"Unit {unit_id} bin {bin_index + 1} implies an invalid firing rate."
                    ) from exc
                if not math.isfinite(expected_rate) or not math.isclose(
                    rate, expected_rate, rel_tol=1e-7, abs_tol=1e-9
                ):
                    raise ValueError(
                        f"Unit {unit_id} firing rate {bin_index + 1} does not match count / occupancy."
                    )
            counts.append(count)
            rates.append(rate)

        optional_number("rate_mvl", unit_index, maximum=1.0)
        optional_number("spike_angle_mrl", unit_index, maximum=1.0)
        rayleigh_score = optional_number("rayleigh_score", unit_index)
        rayleigh_p = optional_number("rayleigh_p", unit_index, maximum=1.0)
        shuffle_p = optional_number("shuffle_p", unit_index, maximum=1.0)
        if (rayleigh_score is None) != (rayleigh_p is None):
            raise ValueError(
                f"Unit {unit_id} rayleigh_score and rayleigh_p must both be null or numeric."
            )
        raw_rayleigh_significant = unit_data["rayleigh_significant"][unit_index]
        raw_shuffle_significant = unit_data["shuffle_significant"][unit_index]
        if (
            raw_rayleigh_significant is not None
            and type(raw_rayleigh_significant) is not bool
        ):
            raise ValueError(
                f"Unit {unit_id} rayleigh_significant must be boolean or null."
            )
        if (
            raw_shuffle_significant is not None
            and type(raw_shuffle_significant) is not bool
        ):
            raise ValueError(
                f"Unit {unit_id} shuffle_significant must be boolean or null."
            )
        expected_rayleigh_significant = (
            None if rayleigh_p is None else rayleigh_p < rayleigh_alpha
        )
        expected_shuffle_significant = (
            None if shuffle_p is None else shuffle_p <= shuffle_alpha
        )
        if raw_rayleigh_significant != expected_rayleigh_significant:
            raise ValueError(
                f"Unit {unit_id} rayleigh_significant does not match rayleigh_p."
            )
        if raw_shuffle_significant != expected_shuffle_significant:
            raise ValueError(
                f"Unit {unit_id} shuffle_significant does not match shuffle_p."
            )

        hd_class = unit_data["hd_class"][unit_index]
        if hd_class is not None and (
            type(hd_class) is not int or hd_class not in {0, 1, 2}
        ):
            raise ValueError(f"Unit {unit_id} hd_class must be 0, 1, 2, or null.")
        expected_hd_class = (
            None
            if raw_rayleigh_significant is None or raw_shuffle_significant is None
            else 2
            if raw_rayleigh_significant and raw_shuffle_significant
            else 1
            if raw_rayleigh_significant or raw_shuffle_significant
            else 0
        )
        if hd_class != expected_hd_class:
            raise ValueError(
                f"Unit {unit_id} hd_class does not match its significance results."
            )
        units.append(
            TuningUnit(
                unit_id=unit_id,
                rates=tuple(rates),
                spike_counts=tuple(counts),
                hd_class=hd_class,
            )
        )
    return TuningCurveData(
        path=path,
        units=tuple(units),
        occupancy_time_s=tuple(occupancy),
        metadata=metadata,
    )


def load_tuning_curve(path: Path) -> TuningCurveData:
    resolved = Path(path).expanduser().resolve()
    if not has_supported_tuning_suffix(resolved):
        raise ValueError("Tuning-curve file must end with .tc or .json.")
    try:
        payload = json.loads(
            resolved.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid tuning-curve JSON: {exc}") from exc
    _require_finite_json(payload)
    if not isinstance(payload, dict) or not payload:
        raise ValueError("Tuning-curve JSON must be a non-empty object.")
    missing = [key for key in TUNING_TOP_LEVEL_KEYS if key not in payload]
    unexpected = [key for key in payload if key not in TUNING_TOP_LEVEL_KEYS]
    if missing:
        raise ValueError(f"Missing tuning-curve keys: {', '.join(missing)}.")
    if unexpected:
        raise ValueError(f"Unexpected tuning-curve keys: {', '.join(unexpected)}.")
    return _load_tuning_curve_payload(resolved, payload)


def tuning_unit_payload(unit: TuningUnit) -> dict[str, Any]:
    return {
        "unitId": unit.unit_id,
        "rates": list(unit.rates),
        "spikeCounts": list(unit.spike_counts),
        "hdClass": unit.hd_class,
    }


def tuning_dataset_payload(data: TuningCurveData) -> dict[str, Any]:
    return {
        "available": True,
        "sourcePath": str(data.path),
        "metadata": data.metadata,
        "occupancyTimeS": list(data.occupancy_time_s),
        "units": [tuning_unit_payload(unit) for unit in data.units],
    }


def tuning_cluster_payload(data: TuningCurveData, cluster_id: int) -> dict[str, Any]:
    unit = data.units_by_id.get(int(cluster_id))
    return {
        "available": unit is not None,
        "sourcePath": str(data.path),
        "rates": list(unit.rates) if unit is not None else None,
        "spikeCounts": list(unit.spike_counts) if unit is not None else None,
        "occupancyTimeS": list(data.occupancy_time_s),
        "hdClass": unit.hd_class if unit is not None else None,
        "metadata": data.metadata,
    }


def unavailable_tuning_dataset_payload() -> dict[str, Any]:
    return {
        "available": False,
        "sourcePath": None,
        "metadata": None,
        "occupancyTimeS": None,
        "units": [],
    }


def unavailable_tuning_cluster_payload() -> dict[str, Any]:
    return {
        "available": False,
        "sourcePath": None,
        "rates": None,
        "spikeCounts": None,
        "occupancyTimeS": None,
        "hdClass": None,
        "metadata": None,
    }


# Compatibility readers for the old PNG/CSV endpoints. They are deliberately
# not used by the current tuning endpoints or by capabilities.
def _required_column(row: dict[str, str], *names: str) -> str:
    by_fold = {key.casefold(): key for key in row}
    for name in names:
        if name.casefold() in by_fold:
            return by_fold[name.casefold()]
    raise ValueError(f"Missing CSV column: {'/'.join(names)}")


def _coerce_csv_value(value: str) -> Any:
    stripped = value.strip()
    if stripped == "":
        return None
    if stripped.casefold() == "true":
        return True
    if stripped.casefold() == "false":
        return False
    try:
        parsed = float(stripped)
    except ValueError:
        return stripped
    if not math.isfinite(parsed):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def load_hd_summary(path: Path | None, cluster_id: int) -> dict[str, Any] | None:
    if path is None:
        return None
    _fields, rows = _read_csv(path)
    for row in rows:
        try:
            key = _required_column(row, "cluster_id", "unit_id", "cluster")
            if _csv_integer(row[key], key) == cluster_id:
                return {name: _coerce_csv_value(value) for name, value in row.items()}
        except ValueError:
            continue
    return None


def load_hd_curve(path: Path | None, cluster_id: int) -> dict[str, list[float]] | None:
    if path is None:
        return None
    _fields, rows = _read_csv(path)
    if not rows:
        return None
    columns = {key.casefold(): key for key in rows[0]}
    angle_key = next(
        (
            columns[name]
            for name in ("angle_deg", "angle", "direction_deg", "direction", "hd")
            if name in columns
        ),
        next(iter(rows[0]), None),
    )
    id_key = next(
        (
            columns[name]
            for name in ("cluster_id", "unit_id", "cluster", "unit")
            if name in columns
        ),
        None,
    )
    rate_key = next(
        (
            columns[name]
            for name in ("rate", "firing_rate", "spikes_per_second", "value")
            if name in columns
        ),
        None,
    )
    if id_key is None or rate_key is None:
        wanted = {
            str(cluster_id).casefold(),
            f"cluster_{cluster_id}".casefold(),
            f"cluster {cluster_id}".casefold(),
            f"unit_{cluster_id}".casefold(),
            f"unit {cluster_id}".casefold(),
        }
        rate_key = next(
            (original for folded, original in columns.items() if folded in wanted),
            None,
        )
        id_key = None
    if angle_key is None or rate_key is None:
        return None
    angles: list[float] = []
    rates: list[float] = []
    for row in rows:
        try:
            if id_key is not None and _csv_integer(row[id_key], id_key) != cluster_id:
                continue
            angles.append(_finite_csv_float(row[angle_key], "HD angle"))
            rates.append(_finite_csv_float(row[rate_key], "HD rate"))
        except (KeyError, ValueError):
            continue
    return {"angles": angles, "rates": rates} if angles else None


def find_hd_image(companions: CompanionSet, cluster_id: int) -> Path | None:
    matcher = re.compile(
        rf"^hd.*(?:cluster|unit)[ _-]*{re.escape(str(cluster_id))}\.png$",
        re.IGNORECASE,
    )
    for root in companions.hd_image_roots:
        try:
            candidates = sorted(root.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            continue
        for candidate in candidates:
            if candidate.is_file() and matcher.fullmatch(candidate.name):
                safe = _safe_file(candidate, root)
                if safe is not None:
                    return safe
    return None
