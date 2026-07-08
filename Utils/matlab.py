from __future__ import annotations

"""Utilities for parsing MATLAB protocol scripts and annotating detected intervals."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np
import pandas as pd

from Utils.json_tools import read_formatted_json

_START_RE = re.compile(r"^start\s*\(\s*\)\s*;?$")
_END_RE = re.compile(r"^end\s*;?$")
_INTEGER_RE = re.compile(r"^[+-]?\d+$")
_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_]\w*)\s*=\s*(.+?)\s*;?$")
_CALL_RE = re.compile(r"^([A-Za-z_]\w*)\s*\((.*)\)\s*;?$")

__all__ = [
    "PhaseSpec",
    "Phase",
    "PhaseList",
    "IntervalTypeRule",
    "COMBO_PHASE_PATTERN",
    "DEFAULT_PHASE_SPEC",
    "DEFAULT_INTERVAL_TYPE_RULES",
    "build_raw_interval_table",
    "build_expected_protocol_intervals",
    "apply_protocol_interval_correction",
    "build_annotated_interval_table",
]


@dataclass(frozen=True, slots=True)
class IntervalTypeRule:
    """A duration-based rule used to seed raw interval labels before protocol matching."""

    label: str
    min_seconds: float
    max_seconds: float

    def frame_bounds(self, fps: float) -> tuple[float, float]:
        fps = float(fps)
        return self.min_seconds * fps, self.max_seconds * fps


class PhaseSpec:
    def __init__(self, phase: list[str] | tuple[str, ...] | set[str]):
        self.phase_names: tuple[str, ...] = tuple(phase)
        self._phase_set = set(self.phase_names)

    def has_phase(self, name: str) -> bool:
        return name in self._phase_set


class Phase:
    def __init__(self, phase, time=None, bg=None, color=None):
        self.phase: str = phase
        self.time: int | float | None = time
        self.bg: str | None = bg
        self.color: list[int] | list[float] | None = color

    def __str__(self):
        return f"Phase(phase={self.phase}, time={self.time}, bg={self.bg}, color={self.color})"

    def __repr__(self):
        return self.__str__()

    @staticmethod
    def _parse_number(x: str) -> int | float:
        x = x.strip()
        if _INTEGER_RE.fullmatch(x):
            return int(x)
        return float(x)

    @staticmethod
    def _parse_vector(x: str) -> list[int | float]:
        x = x.strip()[1:-1].strip()
        if not x:
            return []
        x = x.replace(",", " ")
        return [Phase._parse_number(v) for v in x.split()]

    @staticmethod
    def _parse_string(x: str) -> str:
        quote = x[0]
        inner = x[1:-1]
        if quote == "'":
            return inner.replace("''", "'")
        return inner

    @staticmethod
    def _parse_atom(x: str, variables: dict[str, object]):
        x = x.strip()

        if not x:
            return x

        if (x.startswith('"') and x.endswith('"')) or (x.startswith("'") and x.endswith("'")):
            return Phase._parse_string(x)

        if x.startswith("[") and x.endswith("]"):
            return Phase._parse_vector(x)

        if x in variables:
            return variables[x]

        try:
            return Phase._parse_number(x)
        except ValueError:
            return x

    @classmethod
    def _from_values(cls, phase_name: str, values: list[object]) -> "Phase":
        phase = cls(phase_name)

        for value in values:
            if isinstance(value, (int, float)) and phase.time is None:
                phase.time = value
            elif isinstance(value, str) and phase.bg is None:
                phase.bg = value
            elif isinstance(value, list) and phase.color is None:
                phase.color = value

        return phase

    @staticmethod
    def _split_arguments(text: str) -> list[str]:
        args: list[str] = []
        current: list[str] = []
        bracket_depth = 0
        paren_depth = 0
        quote_char: str | None = None
        idx = 0

        while idx < len(text):
            char = text[idx]

            if quote_char is not None:
                current.append(char)
                if char == quote_char:
                    if quote_char == "'" and idx + 1 < len(text) and text[idx + 1] == "'":
                        current.append(text[idx + 1])
                        idx += 1
                    else:
                        quote_char = None
                idx += 1
                continue

            if char in {"'", '"'}:
                quote_char = char
                current.append(char)
            elif char == "[":
                bracket_depth += 1
                current.append(char)
            elif char == "]":
                bracket_depth = max(0, bracket_depth - 1)
                current.append(char)
            elif char == "(":
                paren_depth += 1
                current.append(char)
            elif char == ")":
                paren_depth = max(0, paren_depth - 1)
                current.append(char)
            elif char == "," and bracket_depth == 0 and paren_depth == 0:
                token = "".join(current).strip()
                if token:
                    args.append(token)
                current = []
            else:
                current.append(char)
            idx += 1

        token = "".join(current).strip()
        if token:
            args.append(token)

        return args

    @classmethod
    def from_line(cls, line: str, variables: dict[str, object], spec: PhaseSpec | None = None):
        line = line.strip()
        match = _CALL_RE.fullmatch(line)
        if match is None:
            return None

        func_name = match.group(1)
        if spec is not None and not spec.has_phase(func_name):
            return None

        raw_args = cls._split_arguments(match.group(2))
        parsed_args = [cls._parse_atom(arg, variables) for arg in raw_args]
        return cls._from_values(func_name, parsed_args)

    @staticmethod
    def _strip_comments(line: str) -> str:
        out: list[str] = []
        quote_char: str | None = None
        idx = 0

        while idx < len(line):
            char = line[idx]
            if quote_char is not None:
                out.append(char)
                if char == quote_char:
                    if quote_char == "'" and idx + 1 < len(line) and line[idx + 1] == "'":
                        out.append(line[idx + 1])
                        idx += 1
                    else:
                        quote_char = None
                idx += 1
                continue

            if char in {"'", '"'}:
                quote_char = char
                out.append(char)
            elif char == "%":
                break
            else:
                out.append(char)
            idx += 1

        return "".join(out).strip()

    @classmethod
    def _collect_statements(cls, text: str) -> list[str]:
        statements: list[str] = []
        current = ""

        for raw_line in text.splitlines():
            line = cls._strip_comments(raw_line)
            if not line:
                continue

            if line.endswith("..."):
                current += line[:-3].rstrip() + " "
                continue

            statement = (current + line).strip()
            current = ""
            if statement:
                statements.append(statement)

        if current.strip():
            statements.append(current.strip())

        return statements

    @classmethod
    def parse_m_file(cls, text: str, spec: PhaseSpec | None = None) -> list["Phase"]:
        variables: dict[str, object] = {}
        phases: list[Phase] = []
        statements = cls._collect_statements(text)

        has_start_marker = any(_START_RE.fullmatch(statement) for statement in statements)
        inside_protocol = not has_start_marker

        for statement in statements:
            if not inside_protocol and _START_RE.fullmatch(statement):
                inside_protocol = True
                continue

            if inside_protocol and has_start_marker and _END_RE.fullmatch(statement):
                break

            assignment_match = _ASSIGNMENT_RE.fullmatch(statement)
            if assignment_match is not None:
                name = assignment_match.group(1)
                value = assignment_match.group(2)
                variables[name] = cls._parse_atom(value, variables)
                continue

            if not inside_protocol:
                continue

            phase = cls.from_line(statement, variables, spec=spec)
            if phase is not None:
                phases.append(phase)

        return phases


class PhaseList(list):
    phases: list[Phase]

    def __init__(self, phases: list[Phase]):
        super().__init__(phases)

    def to_str_list(self) -> list[str]:
        out: list[str] = []

        for phase in self:
            repeats = 1 if phase.phase == "pictures" else int(phase.time or 0)
            out.extend([phase.phase] * repeats)

        return out

    def to_list(self):
        out: list[list[object]] = []
        for phase in self:
            repeats = 1 if phase.phase == "pictures" else int(phase.time or 0)
            row = (
                [phase.phase, phase.time, phase.bg, phase.color]
                if phase.phase == "pictures"
                else [phase.phase, 1, phase.bg, phase.color]
            )
            out.extend([row.copy() for _ in range(repeats)])
        return out


DEFAULT_PHASE_SPEC = PhaseSpec(
    [
        "tracking",
        "pictures",
        "combo",
        "rotation",
        "sweep",
        "sweep_hold",
        "flash",
        "static",
    ]
)

COMBO_PHASE_PATTERN: tuple[str, ...] = (
    "rotation_off",
    "rotation_on",
    "dot_off",
    "dot_on",
)

DEFAULT_INTERVAL_TYPE_RULES: tuple[IntervalTypeRule, ...] = (
    IntervalTypeRule("combo", 14.0, 15.5),
    IntervalTypeRule("combo", 29.5, 30.5),
    IntervalTypeRule("sweep_hold", 19.5, 20.5),
    IntervalTypeRule("baseline", 299.5, 301.5),
    IntervalTypeRule("baseline", 1799.0, 1801.0),
)


def _classify_raw_interval_types(
        interval_table: pd.DataFrame,
        fps: float,
        *,
        interval_rules: Sequence[IntervalTypeRule] | None = None,
) -> pd.DataFrame:
    classified = interval_table.copy()
    classified["interval_type"] = "other"

    rules = DEFAULT_INTERVAL_TYPE_RULES if interval_rules is None else tuple(interval_rules)
    for rule in rules:
        min_frames, max_frames = rule.frame_bounds(fps)
        classified.loc[
            classified["duration_frames"].between(min_frames, max_frames),
            "interval_type",
        ] = rule.label

    return classified


def build_raw_interval_table(
        on_list_frames: list[list[int]],
        fps: float,
        *,
        min_duration_frames: int | None = None,
        interval_rules: Sequence[IntervalTypeRule] | None = None,
) -> pd.DataFrame:
    """Build the raw interval table and seed coarse interval labels by duration.

    The initial labels are only used as anchors for later MATLAB protocol matching,
    so callers can override ``interval_rules`` when extending the protocol family.
    """

    on_list_flattened = [int(value) for pair in on_list_frames for value in pair]
    if len(on_list_flattened) < 2:
        return pd.DataFrame(
            columns=[
                "start_frame",
                "end_frame",
                "start",
                "end",
                "duration_frames",
                "duration_s",
                "interval_type",
            ]
        )

    interval_table = pd.DataFrame(on_list_flattened[:-1], columns=["start_frame"])
    interval_table["end_frame"] = on_list_flattened[1:]
    interval_table["duration_frames"] = interval_table["end_frame"] - interval_table["start_frame"]
    interval_table["start"] = interval_table["start_frame"] / float(fps)
    interval_table["end"] = interval_table["end_frame"] / float(fps)
    interval_table["duration_s"] = interval_table["duration_frames"] / float(fps)

    threshold = int(round(float(fps))) if min_duration_frames is None else int(min_duration_frames)
    interval_table = interval_table.loc[interval_table["duration_frames"] >= threshold].reset_index(drop=True)
    return _classify_raw_interval_types(
        interval_table,
        fps,
        interval_rules=interval_rules,
    )


def _load_optional_json(path: str | Path) -> dict:
    path = Path(path)
    return read_formatted_json(path) if path.exists() else {}


def _get_float(config: dict, *keys: str, default: float) -> float:
    for key in keys:
        value = config.get(key)
        if value is not None:
            return float(value)
    return float(default)


def _get_int(config: dict, *keys: str, default: int) -> int:
    for key in keys:
        value = config.get(key)
        if value is not None:
            return int(value)
    return int(default)


def _get_sequence(config: dict, *keys: str) -> list[str]:
    for key in keys:
        value = config.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            items = [item.strip() for item in re.split(r"[,\s]+", value) if item.strip()]
            if items:
                return items
        elif isinstance(value, (list, tuple)):
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                return items
    return []


def _interval_type_for_phase(protocol_phase: str, expected_duration_s: float) -> str:
    if protocol_phase in COMBO_PHASE_PATTERN:
        return protocol_phase
    if protocol_phase in {"tracking", "rotation", "static"}:
        return "combo"
    if protocol_phase == "sweep":
        return "sweep"
    if protocol_phase == "sweep_hold":
        return "sweep_hold"
    if protocol_phase == "pictures" and expected_duration_s >= 60.0:
        return "baseline"
    if protocol_phase == "pictures":
        return "pictures"
    return "other"


def build_expected_protocol_intervals(
        m_path: str | Path,
        session_dir: str | Path,
        fps: float,
        phase_spec: PhaseSpec | None = None,
):
    m_path = Path(m_path)
    session_dir = Path(session_dir)
    if not m_path.exists():
        raise FileNotFoundError(f"Protocol file not found: {m_path}")

    config_dir = session_dir / "configs"
    combined_config = _load_optional_json(config_dir / "config_combined.json")
    sweep_config = _load_optional_json(config_dir / "config_sweep.json")

    active_seconds = _get_float(combined_config, "active_seconds", default=15.0)
    hold_seconds = _get_float(combined_config, "hold_seconds", default=30.0)
    total_round_pairs = _get_int(combined_config, "total_round_pairs", default=1) or 1

    sweep_hold_seconds = _get_float(sweep_config, "hold_duration_sec", default=20.0)
    sweep_transition_seconds = _get_float(
        sweep_config,
        "transition_duration_sec",
        "sweep_duration_sec",
        "step_duration_sec",
        "duration_sec",
        default=sweep_hold_seconds,
    )
    sweep_cycle = _get_int(sweep_config, "cycle", default=1) or 1
    sweep_sequence = _get_sequence(
        sweep_config,
        "background_sequence",
        "backgrounds",
        "phase_keys",
        "labels",
        "sequence",
    )
    sweep_start_background = (
            sweep_config.get("start_background")
            or sweep_config.get("initial_background")
            or sweep_config.get("baseline_background")
    )

    parsed_phases = Phase.parse_m_file(
        m_path.read_text(encoding="utf-8"),
        spec=phase_spec or DEFAULT_PHASE_SPEC,
    )
    phase_list = PhaseList(parsed_phases).to_list()
    expected_rows: list[dict[str, object]] = []
    current_bg = sweep_start_background

    def add_expected(
            protocol_phase: str,
            expected_duration_s: float,
            *,
            stimulus_name=None,
            stimulus_color=None,
            protocol_note: str | None = None,
            phase_family: str | None = None,
            phase_name: str | None = None,
            phase_key: str | None = None,
            bg=None,
    ) -> None:
        interval_type_protocol = _interval_type_for_phase(protocol_phase, expected_duration_s)
        expected_rows.append(
            {
                "expected_position": len(expected_rows),
                "expected_index": len(expected_rows) + 1,
                "protocol_phase": protocol_phase,
                "interval_type_protocol": interval_type_protocol,
                "expected_duration_s": float(expected_duration_s),
                "expected_duration_frames": int(round(float(expected_duration_s) * float(fps))),
                "stimulus_name": stimulus_name,
                "stimulus_color": stimulus_color,
                "protocol_note": protocol_note,
                "phase_family": phase_family,
                "phase_name": phase_name,
                "phase_key": phase_key,
                "bg": bg,
            }
        )

    for phase in parsed_phases:
        phase_name = phase.phase
        phase_time = float(phase.time or 0)

        if phase_name == "pictures":
            is_baseline = phase_time >= 60.0
            phase_family = "baseline" if is_baseline else "pictures"
            phase_export_name = "baseline" if is_baseline else "pictures"
            phase_key = "baseline" if is_baseline else f"pictures_{phase.bg}" if phase.bg else "pictures"
            add_expected(
                "pictures",
                phase_time,
                stimulus_name=phase.bg,
                stimulus_color=phase.color,
                protocol_note="pictures",
                phase_family=phase_family,
                phase_name=phase_export_name,
                phase_key=phase_key,
                bg=[phase.bg] if phase.bg else None,
            )
            if phase.bg:
                current_bg = phase.bg
            continue

        if phase_name == "combo":
            repeat_count = int(round(phase_time)) * total_round_pairs
            for combo_index in range(repeat_count):
                cycle_number = combo_index + 1
                combo_durations = (
                    active_seconds,
                    hold_seconds,
                    active_seconds,
                    hold_seconds,
                )
                for combo_label, combo_duration in zip(COMBO_PHASE_PATTERN, combo_durations):
                    add_expected(
                        combo_label,
                        combo_duration,
                        protocol_note=f"combo_{combo_label}_{cycle_number:02d}",
                        phase_family="combo",
                        phase_name=combo_label,
                        phase_key=combo_label,
                    )
            continue

        if phase_name == "sweep":
            repeat_count = int(round(phase_time)) * sweep_cycle
            for sweep_index in range(repeat_count):
                next_bg = None
                if sweep_sequence:
                    next_bg = sweep_sequence[sweep_index % len(sweep_sequence)]
                elif phase.bg:
                    next_bg = phase.bg
                elif current_bg:
                    next_bg = current_bg
                else:
                    next_bg = f"{sweep_index + 1:02d}"

                add_expected(
                    "sweep",
                    sweep_transition_seconds,
                    stimulus_name=next_bg,
                    stimulus_color=phase.color,
                    protocol_note=f"sweep_{sweep_index + 1:02d}",
                    phase_family="sweep",
                    phase_name="sweep",
                    phase_key=f"sweep_{next_bg}",
                    bg=[current_bg, next_bg] if current_bg is not None else [next_bg],
                )
                add_expected(
                    "sweep_hold",
                    sweep_hold_seconds,
                    stimulus_name=next_bg,
                    stimulus_color=phase.color,
                    protocol_note=f"sweep_hold_{sweep_index + 1:02d}",
                    phase_family="sweep",
                    phase_name="sweep_hold",
                    phase_key=f"sweep_{next_bg}_hold",
                    bg=[next_bg],
                )
                current_bg = next_bg
            continue

        add_expected(
            phase_name,
            phase_time,
            stimulus_name=phase.bg,
            stimulus_color=phase.color,
            protocol_note=phase_name,
            phase_family=phase_name,
            phase_name=phase_name,
            phase_key=phase_name,
            bg=[phase.bg] if phase.bg else None,
        )
        if phase.bg:
            current_bg = phase.bg

    expected_protocol_table = pd.DataFrame(expected_rows)
    if expected_protocol_table.empty:
        expected_protocol_table = pd.DataFrame(
            columns=[
                "expected_position",
                "expected_index",
                "protocol_phase",
                "interval_type_protocol",
                "expected_duration_s",
                "expected_duration_frames",
                "stimulus_name",
                "stimulus_color",
                "protocol_note",
                "phase_family",
                "phase_name",
                "phase_key",
                "bg",
            ]
        )

    return expected_protocol_table, phase_list


def apply_protocol_interval_correction(
        interval_table: pd.DataFrame,
        m_path: str | Path,
        session_dir: str | Path,
        fps: float,
        phase_spec: PhaseSpec | None = None,
        fallback_pct: float = 0.05,
):
    corrected = interval_table.copy()
    corrected["interval_type_precise"] = corrected["interval_type"]
    corrected["interval_type_protocol"] = pd.Series(pd.NA, index=corrected.index, dtype="object")
    corrected["protocol_phase"] = pd.Series(pd.NA, index=corrected.index, dtype="object")
    corrected["expected_index"] = pd.Series(pd.NA, index=corrected.index, dtype="Int64")
    corrected["expected_duration_s"] = np.nan
    corrected["expected_duration_frames"] = pd.Series(pd.NA, index=corrected.index, dtype="Int64")
    corrected["duration_error_pct"] = np.nan
    corrected["protocol_match_found"] = False
    corrected["protocol_match_method"] = "unmatched"
    corrected["protocol_ambiguous"] = False
    corrected["stimulus_name"] = pd.Series(pd.NA, index=corrected.index, dtype="object")
    corrected["stimulus_color"] = pd.Series(pd.NA, index=corrected.index, dtype="object")
    corrected["protocol_note"] = pd.Series(pd.NA, index=corrected.index, dtype="object")
    corrected["phase_family"] = pd.Series(pd.NA, index=corrected.index, dtype="object")
    corrected["phase_name"] = pd.Series(pd.NA, index=corrected.index, dtype="object")
    corrected["phase_key"] = pd.Series(pd.NA, index=corrected.index, dtype="object")
    corrected["bg"] = pd.Series(pd.NA, index=corrected.index, dtype="object")

    expected_protocol_table, phase_list = build_expected_protocol_intervals(
        m_path,
        session_dir,
        fps,
        phase_spec=phase_spec,
    )
    if expected_protocol_table.empty:
        corrected["interval_type_resolved"] = corrected["interval_type_precise"]
        return corrected, expected_protocol_table, phase_list

    fallback_limit_pct = float(fallback_pct) * 100.0
    next_expected_position = 0
    compatibility_map = {
        "combo": set(COMBO_PHASE_PATTERN),
        "combo_on": set(COMBO_PHASE_PATTERN),
        "combo_off": set(COMBO_PHASE_PATTERN),
        "sweep_hold": {"sweep", "sweep_hold"},
        "pictures": {"pictures", "baseline"},
    }

    for row_index, row in corrected.iterrows():
        remaining = expected_protocol_table.loc[
            expected_protocol_table["expected_position"] >= next_expected_position
            ].copy()
        if remaining.empty:
            break

        observed_duration_s = float(row["duration_s"])
        remaining["duration_error_pct_candidate"] = np.where(
            remaining["expected_duration_s"] > 0,
            np.abs(observed_duration_s - remaining["expected_duration_s"])
            / remaining["expected_duration_s"]
            * 100.0,
            np.inf,
        )
        fit_candidates = remaining.loc[
            remaining["duration_error_pct_candidate"] <= fallback_limit_pct
            ].copy()

        precise_label = str(row["interval_type_precise"])
        if precise_label != "other":
            compatible_labels = compatibility_map.get(precise_label, {precise_label})
            precise_fit_candidates = fit_candidates.loc[
                fit_candidates["interval_type_protocol"].isin(compatible_labels)
            ].copy()
            if not precise_fit_candidates.empty:
                fit_candidates = precise_fit_candidates

        if fit_candidates.empty:
            continue

        fit_candidates = fit_candidates.sort_values(
            ["expected_position", "duration_error_pct_candidate"],
            kind="stable",
        )
        best_candidate = fit_candidates.iloc[0]
        best_expected_position = int(best_candidate["expected_position"])
        best_error_pct = float(best_candidate["duration_error_pct_candidate"])
        forward_distance = best_expected_position - next_expected_position

        ambiguous_candidates = fit_candidates.loc[
            (fit_candidates["expected_position"] - next_expected_position == forward_distance)
            & np.isclose(fit_candidates["duration_error_pct_candidate"], best_error_pct)
            ]
        if len(ambiguous_candidates) > 1:
            corrected.at[row_index, "protocol_match_method"] = "ambiguous"
            corrected.at[row_index, "protocol_ambiguous"] = True
            continue

        corrected.at[row_index, "interval_type_protocol"] = best_candidate["interval_type_protocol"]
        corrected.at[row_index, "protocol_phase"] = best_candidate["protocol_phase"]
        corrected.at[row_index, "expected_index"] = int(best_candidate["expected_index"])
        corrected.at[row_index, "expected_duration_s"] = float(best_candidate["expected_duration_s"])
        corrected.at[row_index, "expected_duration_frames"] = int(best_candidate["expected_duration_frames"])
        corrected.at[row_index, "duration_error_pct"] = best_error_pct
        corrected.at[row_index, "protocol_match_found"] = True
        corrected.at[row_index, "stimulus_name"] = best_candidate["stimulus_name"]
        corrected.at[row_index, "stimulus_color"] = best_candidate["stimulus_color"]
        corrected.at[row_index, "protocol_note"] = best_candidate["protocol_note"]
        corrected.at[row_index, "phase_family"] = best_candidate["phase_family"]
        corrected.at[row_index, "phase_name"] = best_candidate["phase_name"]
        corrected.at[row_index, "phase_key"] = best_candidate["phase_key"]
        corrected.at[row_index, "bg"] = best_candidate["bg"]

        if precise_label == best_candidate["interval_type_protocol"]:
            corrected.at[row_index, "protocol_match_method"] = "ordered_match"
        else:
            corrected.at[row_index, "protocol_match_method"] = "fallback_5pct"

        next_expected_position = best_expected_position + 1

    corrected["interval_type_resolved"] = corrected["interval_type_precise"]
    rescue_mask = (
            corrected["protocol_match_found"]
            & corrected["interval_type_protocol"].notna()
            & corrected["interval_type_protocol"].ne("other")
            & corrected["duration_error_pct"].le(fallback_limit_pct)
            & ~corrected["protocol_ambiguous"]
    )
    corrected.loc[rescue_mask, "interval_type_resolved"] = corrected.loc[
        rescue_mask,
        "interval_type_protocol",
    ]

    return corrected, expected_protocol_table, phase_list


def build_annotated_interval_table(
        interval_table: pd.DataFrame,
        m_path: str | Path,
        session_dir: str | Path,
        fps: float,
        phase_spec: PhaseSpec | None = None,
        fallback_pct: float = 0.05,
):
    corrected, expected_protocol_table, phase_list = apply_protocol_interval_correction(
        interval_table,
        m_path,
        session_dir,
        fps,
        phase_spec=phase_spec,
        fallback_pct=fallback_pct,
    )

    export = corrected.copy()
    export["interval_index"] = export.index.astype(int)
    if not {"start", "end"}.issubset(export.columns):
        raise KeyError('interval_table must contain adjusted timestamp columns "start" and "end".')

    fallback_baseline_mask = export["phase_key"].isna() & export["interval_type_resolved"].eq("baseline")
    export.loc[fallback_baseline_mask, "phase_family"] = "baseline"
    export.loc[fallback_baseline_mask, "phase_name"] = "baseline"
    export.loc[fallback_baseline_mask, "phase_key"] = "baseline"

    export_columns = [
        "interval_index",
        "start_frame",
        "end_frame",
        "start",
        "end",
        "duration_frames",
        "duration_s",
        "interval_type",
        "interval_type_precise",
        "interval_type_protocol",
        "interval_type_resolved",
        "protocol_phase",
        "expected_index",
        "expected_duration_s",
        "expected_duration_frames",
        "duration_error_pct",
        "protocol_match_found",
        "protocol_match_method",
        "protocol_ambiguous",
        "phase_family",
        "phase_name",
        "phase_key",
        "stimulus_name",
        "stimulus_color",
        "protocol_note",
        "bg",
    ]

    return export[export_columns], corrected, expected_protocol_table, phase_list
