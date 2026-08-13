#!/usr/bin/env python3
"""Read-only acceptance checks for the RF, probe, HD, and Timeline Web paths."""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import importlib.util
import json
import math
import mmap
import os
import re
import stat
import sys
import types
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

APP_ROOT = Path(__file__).resolve().parent.parent
RF_ROOT = Path("/mnt/senzailab")
WEB_PACKAGE_ROOT = (
    APP_ROOT / "backend/rfmapping_web"
    if (APP_ROOT / "backend/rfmapping_web").is_dir()
    else APP_ROOT / "rfmapping_web"
)
FRONTEND_ROOT = (
    APP_ROOT / "frontend"
    if (APP_ROOT / "frontend").is_dir()
    else APP_ROOT / "web"
)

M17_DAY = RF_ROOT / "Kai/#Recording/m17/260729"
M17_HD = M17_DAY / "260729_1/data/tuning_curves/ProbeA/tuning_curves.json"
M17_PROBE_DATA = M17_DAY / "260729_2/data"
M17_PROBE_RF = M17_PROBE_DATA / (
    "rfmapping/good/-100_400_1ms/ProbeA/regular_unitsSpikeCounts_260729_2.json"
)
M17_CHANNELS = M17_PROBE_DATA / "waveform/ProbeA/channels.csv"
M17_POSITIONS = M17_PROBE_DATA / "spike_position/ProbeA/positions.csv"
M17_ROTATION_RF = M17_DAY / (
    "260729_4/data/rfmapping/good/-100_400_1ms/ProbeA/"
    "rotation_30_unitsSpikeCounts_260729_4.json"
)

M15_DAY = RF_ROOT / "Kai/#Recording/m15/260630"
M15_HD = M15_DAY / "260630_1/data/tuning_curves/ProbeA/tuning_curves.json"
M15_DATA = M15_DAY / "260630_3/data"
M15_RF = M15_DATA / (
    "rfmapping/good/-100_200_1ms/ProbeA/regular_unitsSpikeCounts_260630_3.json"
)
M15_CHANNELS = M15_DATA / "waveform/ProbeA/channels.csv"
M15_POSITIONS = M15_DATA / "spike_position/ProbeA/positions.csv"

M14_DATA = RF_ROOT / "Kai/#Recording/m14/260615/260615_3/data"
M14_RF = M14_DATA / (
    "rfmapping/good/-100_200_1ms/ProbeA/regular_unitsSpikeCounts_260615_3.json"
)
M14_CHANNELS = M14_DATA / "waveform/ProbeA/channels.csv"
M14_POSITIONS = M14_DATA / "spike_position/ProbeA/positions.csv"

M18_DATA = RF_ROOT / "Kai/#Recording/m18/260812/260812_3/data"
M18_RF = M18_DATA / (
    "rfmapping/good/-100_400_1ms/ProbeA/regular_unitsSpikeCounts_260812_3.json"
)
M18_CHANNELS = M18_DATA / "waveform/ProbeA/channels.csv"
M18_POSITIONS = M18_DATA / "spike_position/ProbeA/positions.csv"

SOURCE_FILES = (
    M17_HD,
    M17_PROBE_RF,
    M17_CHANNELS,
    M17_POSITIONS,
    M17_ROTATION_RF,
    M15_HD,
    M15_RF,
    M15_CHANNELS,
    M15_POSITIONS,
    M14_RF,
    M14_CHANNELS,
    M14_POSITIONS,
    M18_RF,
    M18_CHANNELS,
    M18_POSITIONS,
)

EXPECTED_M17_PROBE_BYTES = 130_480_048
EXPECTED_M17_PROBE_SHAPE = [620, 7, 30, 500]
EXPECTED_M17_ROTATION_BYTES = 139_143_404
EXPECTED_M17_ROTATION_SHAPE = [596, 7, 30, 500]
EXPECTED_M17_HD_BYTES = 2_438_987
EXPECTED_M17_HD_UNITS = 634
EXPECTED_M17_PROBE_HD_OVERLAP = 609
EXPECTED_M17_ROTATION_HD_OVERLAP = 584

EXPECTED_M15_RF_BYTES = 18_465_726
EXPECTED_M15_RF_SHAPE = [146, 7, 30, 300]
EXPECTED_M15_COLUMNAR_HD_BYTES = 946_358
EXPECTED_M15_HD_UNITS = 147
EXPECTED_WEB_VERSION = "1.9.0-web"

EXPECTED_M14_RF_BYTES = 35_783_459
EXPECTED_M14_RF_SHAPE = [220, 9, 30, 300]

EXPECTED_M18_RF_BYTES = 40_412_516
EXPECTED_M18_RF_SHAPE = [192, 7, 30, 500]
EXPECTED_M18_UNPOSITIONED_UNITS = {50, 118}

HD_RAW_BINS = 180
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
MAIN_TABS = ("rf", "delay", "timeline")
FIGURE_PLOT_IDS = (
    "rf.cartesian",
    "rf.polar",
    "delay.cartesian",
    "delay.polar",
    "rgb.cartesian",
    "rgb.polar",
    "timeline.current",
    "hd.line",
    "hd.polar",
    "probe",
)
SOURCE_UI_FORBIDDEN = (
    "BroadcastChannel",
    "Window pairing",
    "Sync viewer",
    "MenuBar",
    "MenuItem",
    'className="menu-bar"',
    'className="menu-root"',
    ".menu-bar",
    ".menu-root",
    ".menu-popover",
    ".menu-item",
    ".menu-separator",
    ".pair-heading",
)
BUNDLE_UI_FORBIDDEN = (
    "BroadcastChannel",
    "Window pairing",
    "Sync viewer",
    "menu-bar",
    "menu-root",
    "menu-popover",
    "menu-item",
    "menu-separator",
    "pair-heading",
)


@dataclass(frozen=True)
class Fingerprint:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


def note(message: str) -> None:
    print(message, flush=True)


def fail(message: str) -> None:
    raise RuntimeError(message)


def load_supported_tuning(path: Path) -> Any:
    package_name = "_rfmapping_validation"
    package_root = WEB_PACKAGE_ROOT
    companions_name = f"{package_name}.companions"
    prior_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == package_name or name.startswith(f"{package_name}.")
    }
    package = types.ModuleType(package_name)
    package.__path__ = [str(package_root)]  # type: ignore[attr-defined]
    package.__package__ = package_name
    spec = importlib.util.spec_from_file_location(
        companions_name,
        package_root / "companions.py",
    )
    if spec is None or spec.loader is None:
        fail("unable to load the Web tuning-curve validator")
    companions = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    sys.modules[companions_name] = companions
    try:
        spec.loader.exec_module(companions)
        return companions.load_tuning_curve(path)
    finally:
        for name in tuple(sys.modules):
            if name == package_name or name.startswith(f"{package_name}."):
                sys.modules.pop(name, None)
        sys.modules.update(prior_modules)


def fingerprint(path: Path) -> Fingerprint:
    info = path.stat()
    return Fingerprint(
        device=info.st_dev,
        inode=info.st_ino,
        mode=info.st_mode,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
    )


def validate_source_paths() -> dict[Path, Fingerprint]:
    root = RF_ROOT.resolve(strict=True)
    snapshots: dict[Path, Fingerprint] = {}
    for path in SOURCE_FILES:
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            fail(f"source escapes {root}: {resolved}")
            raise AssertionError from exc
        info = fingerprint(resolved)
        if not stat.S_ISREG(info.mode) or not os.access(resolved, os.R_OK):
            fail(f"source is not a readable regular file: {resolved}")
        snapshots[resolved] = info
    return snapshots


def mapped_json_value(mapped: mmap.mmap, key: str, max_bytes: int = 1_048_576) -> Any:
    marker = json.dumps(key, separators=(",", ":")).encode("utf-8") + b":"
    position = mapped.find(marker)
    if position < 0:
        fail(f"RF JSON is missing {key}")
    start = position + len(marker)
    chunk = mapped[start : min(len(mapped), start + max_bytes)].decode("utf-8")
    try:
        value, _end = json.JSONDecoder().raw_decode(chunk)
    except json.JSONDecodeError as exc:
        fail(f"unable to decode {key}: {exc}")
        raise AssertionError from exc
    return value


def rf_metadata(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            envelope = b'{"unitsSpikeCounts":['
            if mapped[: len(envelope)] != envelope or mapped[-1:] != b"}":
                fail(f"{path.name} does not have the expected JSON envelope")
            return {
                "shape": mapped_json_value(mapped, "unitsSpikeCountsSize"),
                "unitPool": mapped_json_value(mapped, "unitPool"),
                "xPositions": mapped_json_value(mapped, "xPositions"),
                "yPositions": mapped_json_value(mapped, "yPositions"),
                "timeBinEdges": mapped_json_value(mapped, "timeBinEdges"),
            }


def validate_rf_file(
    path: Path,
    *,
    expected_bytes: int,
    expected_shape: list[int],
    start_s: float,
    end_s: float,
    label: str,
) -> dict[str, Any]:
    size = path.stat().st_size
    if size != expected_bytes:
        fail(f"{label} size changed: {size} != {expected_bytes}")
    metadata = rf_metadata(path)
    if metadata["shape"] != expected_shape:
        fail(f"{label} shape changed: {metadata['shape']} != {expected_shape}")
    unit_pool = metadata["unitPool"]
    x_positions = metadata["xPositions"]
    y_positions = metadata["yPositions"]
    time_edges = metadata["timeBinEdges"]
    n_units, n_y, n_x, n_bins = expected_shape
    if len(unit_pool) != n_units or len(set(unit_pool)) != n_units:
        fail(f"{label} unitPool must contain {n_units} unique cluster IDs")
    if len(x_positions) != n_x or len(y_positions) != n_y:
        fail(f"{label} x/y coordinate lengths do not match its declared shape")
    if len(time_edges) != n_bins + 1:
        fail(f"{label} expected {n_bins + 1} complete timeline edges")
    if (
        abs(float(time_edges[0]) - start_s) > 1e-12
        or abs(float(time_edges[-1]) - end_s) > 1e-12
    ):
        fail(f"{label} did not retain the full {start_s} through {end_s} s timeline")
    if any(
        float(left) >= float(right) for left, right in zip(time_edges, time_edges[1:])
    ):
        fail(f"{label} timeBinEdges are not strictly increasing")
    note(
        f"PASS {label}: {size:,} bytes, shape {expected_shape}, "
        f"all {n_bins} Timeline bins"
    )
    return metadata


def csv_rows(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = set(reader.fieldnames or ())
        missing = required - names
        if missing:
            fail(f"{path.name} is missing columns: {sorted(missing)}")
        return list(reader)


def validate_probe_files(
    channels_path: Path,
    positions_path: Path,
    *,
    expected_channels: int,
    expected_units: int,
    label: str,
    expected_unpositioned_units: set[int] | None = None,
) -> None:
    channels = csv_rows(channels_path, {"channel_id", "x_um", "y_um", "shank_id"})
    positions = csv_rows(positions_path, {"unit_id", "x_um", "y_um"})
    if len(channels) != expected_channels or len(positions) != expected_units:
        fail(
            f"{label} geometry changed: channels={len(channels)}, "
            f"positions={len(positions)}"
        )
    if len({row["channel_id"] for row in channels}) != expected_channels:
        fail(f"{label} contains duplicate channel IDs")
    if len({row["unit_id"] for row in positions}) != expected_units:
        fail(f"{label} contains duplicate unit IDs")
    unpositioned: set[int] = set()
    for row in positions:
        try:
            unit_id = int(row["unit_id"])
            x = float(row["x_um"])
            y = float(row["y_um"])
        except (TypeError, ValueError) as exc:
            fail(f"{label} contains malformed unit geometry: {row!r}")
            raise AssertionError from exc
        if math.isnan(x) and math.isnan(y):
            unpositioned.add(unit_id)
        elif not math.isfinite(x) or not math.isfinite(y):
            fail(f"{label} cluster {unit_id} has an incomplete/non-finite position")
    expected_missing = expected_unpositioned_units or set()
    if unpositioned != expected_missing:
        fail(
            f"{label} unpositioned units changed: "
            f"{sorted(unpositioned)} != {sorted(expected_missing)}"
        )
    note(
        f"PASS {label}: {expected_channels} channels, "
        f"{expected_units - len(unpositioned)}/{expected_units} positioned units"
    )


def _finite_number_list(value: Any, expected: int, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != expected:
        fail(f"{label} must contain {expected} bins")
    converted: list[float] = []
    for index, item in enumerate(value):
        if type(item) not in (int, float) or not math.isfinite(float(item)):
            fail(f"{label}[{index}] is not finite")
        converted.append(float(item))
    return converted


def load_legacy_hd() -> dict[int, list[float]]:
    if M17_HD.stat().st_size != EXPECTED_M17_HD_BYTES:
        fail(f"m17 legacy HD size changed: {M17_HD.stat().st_size}")
    with M17_HD.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or len(payload) != EXPECTED_M17_HD_UNITS:
        fail(f"m17 legacy HD must contain {EXPECTED_M17_HD_UNITS} units")
    units: dict[int, list[float]] = {}
    for raw_id, raw_rates in payload.items():
        try:
            unit_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            fail(f"m17 legacy HD has a non-integer unit ID: {raw_id!r}")
            raise AssertionError from exc
        units[unit_id] = _finite_number_list(
            raw_rates, HD_RAW_BINS, f"m17 legacy cluster {unit_id} rates"
        )
    if len(units) != EXPECTED_M17_HD_UNITS or 0 not in units:
        fail("m17 legacy HD unit IDs are not unique or omit cluster 0")
    note("PASS m17 legacy HD source: 634 units x 180 raw rate bins")
    return units


def load_m15_hd() -> dict[str, Any]:
    with M15_HD.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        fail("m15 HD source must be an object")

    is_columnar = len(payload) == len(TUNING_TOP_LEVEL_KEYS) and set(payload) == set(
        TUNING_TOP_LEVEL_KEYS
    )
    if not is_columnar:
        fail(
            "m15 HD source must contain exactly the current eight-field "
            "columnar contract"
        )
    if is_columnar:
        if M15_HD.stat().st_size != EXPECTED_M15_COLUMNAR_HD_BYTES:
            fail(f"m15 columnar HD size changed: {M15_HD.stat().st_size}")
        loaded = load_supported_tuning(M15_HD)
        if len(loaded.units) != EXPECTED_M15_HD_UNITS or 0 not in loaded.units_by_id:
            fail("m15 columnar HD did not pass the Web tuning-curve contract")
        metadata = payload.get("metadata")
        occupancy_samples = payload.get("occupancy_samples")
        occupancy = payload.get("occupancy_time_s")
        unit_ids = payload.get("unit_id")
        counts = payload.get("spike_counts")
        rates = payload.get("firing_rate_hz")
        unit_data = payload.get("unit_data")
        if not isinstance(metadata, dict) or not isinstance(
            metadata.get("classification"), dict
        ):
            fail("m15 columnar HD metadata/classification provenance is missing")
        _finite_number_list(occupancy_samples, HD_RAW_BINS, "m15 occupancy_samples")
        _finite_number_list(occupancy, HD_RAW_BINS, "m15 occupancy_time_s")
        if (
            not isinstance(unit_ids, list)
            or len(unit_ids) != EXPECTED_M15_HD_UNITS
            or any(type(unit_id) is not int for unit_id in unit_ids)
            or len(set(unit_ids)) != EXPECTED_M15_HD_UNITS
            or 0 not in unit_ids
        ):
            fail("m15 columnar HD contains invalid unit_id values")
        for matrix, label in ((counts, "spike_counts"), (rates, "firing_rate_hz")):
            if (
                not isinstance(matrix, list)
                or len(matrix) != EXPECTED_M15_HD_UNITS
                or any(
                    not isinstance(row, list) or len(row) != HD_RAW_BINS
                    for row in matrix
                )
            ):
                fail(f"m15 columnar HD has invalid {label} dimensions")
        if (
            not isinstance(unit_data, dict)
            or len(unit_data) != len(TUNING_UNIT_DATA_KEYS)
            or set(unit_data) != set(TUNING_UNIT_DATA_KEYS)
            or any(
                not isinstance(unit_data[key], list)
                or len(unit_data[key]) != EXPECTED_M15_HD_UNITS
                for key in TUNING_UNIT_DATA_KEYS
            )
        ):
            fail("m15 columnar HD has invalid unit_data columns")
        note("PASS m15 columnar HD source: 8 top-level fields, 147 units x 180 bins")
        return payload


def validate_frontend_assets(*, require_bundle: bool) -> None:
    source_root = FRONTEND_ROOT / "src"
    if not source_root.is_dir():
        fail(f"frontend source directory is missing: {source_root}")
    source_files = (
        sorted(source_root.rglob("*.ts"))
        + sorted(source_root.rglob("*.tsx"))
        + sorted(source_root.rglob("*.css"))
    )
    source_text = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    for forbidden in SOURCE_UI_FORBIDDEN:
        if forbidden in source_text:
            fail(f"frontend source still contains removed UI/sync code: {forbidden}")

    tabs_path = source_root / "viewTabs.ts"
    tabs_text = tabs_path.read_text(encoding="utf-8")
    tab_keys = tuple(re.findall(r"\bkey\s*:\s*[\"']([^\"']+)[\"']", tabs_text))
    if tab_keys != MAIN_TABS:
        fail(f"main tabs must be exactly {MAIN_TABS}, found {tab_keys}")
    types_text = (source_root / "types.ts").read_text(encoding="utf-8")
    view_tab_match = re.search(r"export\s+type\s+ViewTab\s*=\s*([^;]+);", types_text)
    view_tab_values = (
        tuple(re.findall(r"[\"']([^\"']+)[\"']", view_tab_match.group(1)))
        if view_tab_match is not None
        else ()
    )
    if view_tab_values != MAIN_TABS:
        fail(f"ViewTab must be exactly {MAIN_TABS}, found {view_tab_values}")
    if re.search(r"selectedTab\s*={2,3}\s*[\"'](?:probe|hd)[\"']", source_text):
        fail("Probe or HD is still mounted as a mutually exclusive selectedTab")

    figure_export_path = source_root / "figureExport.ts"
    composer_path = source_root / "components/FigureExportComposer.tsx"
    if not figure_export_path.is_file() or not composer_path.is_file():
        fail("frontend source is missing the figure export model or composer")
    figure_export_text = figure_export_path.read_text(encoding="utf-8")
    composer_text = composer_path.read_text(encoding="utf-8")
    for plot_id in FIGURE_PLOT_IDS:
        if plot_id not in figure_export_text:
            fail(f"figure export registry is missing {plot_id!r}")
    for required in (
        "previewFigureExport",
        "exportFigurePlan",
        "Figure Export Composer",
    ):
        if required not in composer_text:
            fail(f"figure export composer is missing {required!r}")

    app_text = (source_root / "App.tsx").read_text(encoding="utf-8")
    sidebar_probe = app_text.find("<ProbeLayout")
    workspace = app_text.find('<main className="workspace"')
    rf_companion = app_text.find("rf-hd-layout")
    rf_plot = app_text.find('<SpatialPlot kind="rf"', rf_companion)
    hd_panel = app_text.find("<HdPanel", rf_companion)
    delay_view = app_text.find('selectedTab === "delay"', rf_companion)
    if min(sidebar_probe, workspace, rf_companion, rf_plot, hd_panel, delay_view) < 0:
        fail("frontend source is missing the Probe sidebar or combined RF+HD pane")
    if sidebar_probe > workspace:
        fail("Probe Layout is not mounted in the persistent left sidebar")
    if not (rf_companion < rf_plot < hd_panel < delay_view):
        fail("RF and HD are not mounted together before the other main-tab views")

    plots_text = (source_root / "components/Plots.tsx").read_text(encoding="utf-8")
    if "export function TimelinePlot" not in plots_text:
        fail("TimelinePlot implementation is missing")
    timeline_text = plots_text.split("export function TimelinePlot", 1)[1]
    if not re.search(r"timeGroups\(meta,\s*state\.timeResolutionMs\)", timeline_text):
        fail(
            "Timeline maps are not visibly derived from the complete metadata time axis"
        )
    if "const TimelineMapRow" not in plots_text:
        fail("TimelineMapRow implementation is missing")
    timeline_row_text = plots_text.split("const TimelineMapRow", 1)[1].split(
        "export function TimelinePlot", 1
    )[0]
    if (
        "IntersectionObserver" not in timeline_row_text
        or "timelineIntervalLabel(start, end" not in timeline_row_text
    ):
        fail("Timeline does not virtualize all rows with readable start-end labels")

    timeline_layout_text = (source_root / "timelineLayout.ts").read_text(
        encoding="utf-8"
    )

    def integer_constant(text: str, name: str) -> int:
        match = re.search(rf"\bconst\s+{re.escape(name)}\s*=\s*(\d+)\s*;", text)
        if match is None:
            fail(f"frontend source is missing {name}")
        return int(match.group(1))

    max_columns = integer_constant(timeline_layout_text, "MAX_COLUMNS")
    target_map_height = integer_constant(timeline_layout_text, "TARGET_GRID_HEIGHT")
    minimum_map_height = integer_constant(
        timeline_layout_text, "MIN_READABLE_GRID_HEIGHT"
    )
    chart_height = integer_constant(plots_text, "TIMELINE_CHART_HEIGHT")
    plot_height = integer_constant(plots_text, "TIMELINE_CHART_PLOT_HEIGHT")
    if not (1 <= max_columns <= 4):
        fail(f"Timeline allows {max_columns} columns; readable layouts allow at most 4")
    if target_map_height < 160 or minimum_map_height < 120:
        fail(
            f"Timeline maps are too small: target={target_map_height}, "
            f"minimum={minimum_map_height}"
        )
    if chart_height < 240 or plot_height < 120:
        fail(f"Timeline chart is too small: canvas={chart_height}, plot={plot_height}")
    styles_text = (source_root / "styles.css").read_text(encoding="utf-8")
    if re.search(r"grid-template-rows\s*:\s*28px\s+minmax", styles_text):
        fail("app shell still reserves a fake desktop-menu row")
    chart_css = re.search(
        r"\.timeline-chart\s*\{[^}]*\bheight\s*:\s*(\d+)px", styles_text
    )
    if chart_css is not None and int(chart_css.group(1)) != chart_height:
        fail(
            f"Timeline chart canvas is {chart_height}px but its layout box is "
            f"{chart_css.group(1)}px"
        )
    note(
        "PASS frontend source: persistent Probe + combined RF/HD, 3 main tabs, "
        "no menu/window sync, full-axis readable Timeline, figure composer"
    )

    dist_root = FRONTEND_ROOT / "dist"
    bundle_files = (
        (
            sorted(dist_root.rglob("*.js"))
            + sorted(dist_root.rglob("*.css"))
            + sorted(dist_root.rglob("*.html"))
        )
        if dist_root.is_dir()
        else []
    )
    if not bundle_files:
        if require_bundle:
            fail(f"built frontend assets are missing under {dist_root}")
        note(f"PASS bundle check deferred to release gate ({dist_root} is not built yet)")
        return

    bundle_text = "\n".join(path.read_text(encoding="utf-8") for path in bundle_files)
    for forbidden in BUNDLE_UI_FORBIDDEN:
        if forbidden in bundle_text:
            fail(f"built frontend still contains removed UI/sync code: {forbidden}")
    old_tab_patterns = (
        r"key:\s*[\"']probe[\"']\s*,\s*label:\s*[\"']Probe Layout[\"']\s*,\s*short:\s*[\"']4[\"']",
        r"key:\s*[\"']hd[\"']\s*,\s*label:\s*[\"']HD TC[\"']\s*,\s*short:\s*[\"']5[\"']",
    )
    if any(re.search(pattern, bundle_text) for pattern in old_tab_patterns):
        fail("built frontend still contains a Probe or HD main-tab definition")
    for label in ("RF", "Delay / RGB", "Timeline"):
        if label not in bundle_text:
            fail(f"built frontend is missing the {label!r} main-tab label")
    for required in ("rf.cartesian", "timeline.current", "Figure Export Composer"):
        if required not in bundle_text:
            fail(f"built frontend is missing figure export marker {required!r}")
    note(
        "PASS built frontend: 3 main-tab labels, figure composer, and no "
        "removed menu/sync code"
    )


class ApiClient:
    def __init__(self, base_url: str, host_header: str | None):
        self.base_url = base_url.rstrip("/")
        self.host_header = host_header
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )

    def login(self) -> None:
        answer = os.environ.get("MOUSELINE_LOGIN_ANSWER", "")
        if (
            not answer.strip()
            or answer.strip().casefold() == "replace-with-private-answer"
        ):
            fail("MOUSELINE_LOGIN_ANSWER is unavailable for API validation")
        return_path = urllib.parse.urlsplit(self.base_url).path.rstrip("/") + "/"
        data = urllib.parse.urlencode(
            {"answer": answer, "next": return_path}
        ).encode("utf-8")
        headers = {
            "Accept": "text/html",
            "Content-Type": "application/x-www-form-urlencoded",
            "Sec-Fetch-Site": "same-origin",
        }
        if self.host_header:
            headers["Host"] = self.host_header
        request = urllib.request.Request(
            f"{self.base_url}/login",
            data=data,
            headers=headers,
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                response.read(2048)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", errors="replace")
            fail(f"HTTP {exc.code} during API login: {detail}")
        if not any(cookie.name == "rfmapping_session" for cookie in self.cookie_jar):
            fail("API login did not issue an RFmapping session cookie")

    def request(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> tuple[Any, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = None
        headers = {"Accept": "application/json"}
        if self.host_header:
            headers["Host"] = self.host_header
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
            csrf_token = next(
                (
                    cookie.value
                    for cookie in self.cookie_jar
                    if cookie.name == "rfmapping_csrf"
                ),
                "",
            )
            if not csrf_token:
                fail("API request is missing the RFmapping CSRF cookie")
            headers["X-CSRF-Token"] = csrf_token
            headers["Sec-Fetch-Site"] = "same-origin"
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", errors="replace")
            fail(f"HTTP {exc.code} from {url}: {detail}")
            raise AssertionError from exc
        return response, response.headers

    def json(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        response, _headers = self.request(path, payload=payload, timeout=timeout)
        with response:
            value = json.load(response)
        if not isinstance(value, dict):
            fail(f"expected JSON object from {path}")
        return value

    def expect_error(
        self, path: str, *, status: int, timeout: int = 60
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Accept": "application/json"}
        if self.host_header:
            headers["Host"] = self.host_header
        request = urllib.request.Request(url, headers=headers)
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code != status:
                fail(f"HTTP {exc.code} from {url}; expected {status}: {body[:2048]}")
            try:
                value = json.loads(body)
            except json.JSONDecodeError as decode_exc:
                fail(f"HTTP {status} from {url} did not return JSON: {body[:2048]}")
                raise AssertionError from decode_exc
            if not isinstance(value, dict):
                fail(f"HTTP {status} response is not an object: {path}")
            return value
        with response:
            response.read(2048)
        fail(f"Expected HTTP {status} from {url}, but the request succeeded")
        raise AssertionError


def validate_remote_listing(client: ApiClient, expected: Path, kind: str) -> None:
    query = urllib.parse.urlencode(
        {"path": str(expected.parent), "kind": kind, "limit": 100}
    )
    listing = client.json(f"api/fs/list?{query}")
    entries = listing.get("entries")
    if not isinstance(entries, list) or not any(
        isinstance(item, dict) and item.get("path") == str(expected) for item in entries
    ):
        fail(f"remote chooser kind={kind} did not list {expected}")


def validate_probe_payload(
    payload: dict[str, Any],
    *,
    expected_channels: int,
    expected_units: int,
    label: str,
    expected_unpositioned_units: set[int] | None = None,
) -> None:
    if str(payload.get("probe", "")).casefold() != "probea":
        fail(f"{label} returned the wrong probe: {payload.get('probe')}")
    channels = payload.get("channels")
    units = payload.get("units")
    if not isinstance(channels, list) or len(channels) != expected_channels:
        fail(f"{label} did not return all {expected_channels} channels")
    if not isinstance(units, list) or len(units) != expected_units:
        fail(f"{label} did not return all {expected_units} expected units")
    unpositioned: set[int] = set()
    for unit in units:
        if not isinstance(unit, dict) or type(unit.get("unitId")) is not int:
            fail(f"{label} returned a malformed unit row: {unit!r}")
        unit_id = unit["unitId"]
        x = unit.get("x")
        y = unit.get("y")
        if x is None and y is None:
            unpositioned.add(unit_id)
        elif (
            type(x) not in (int, float)
            or type(y) not in (int, float)
            or not math.isfinite(float(x))
            or not math.isfinite(float(y))
        ):
            fail(f"{label} returned incomplete/non-finite coordinates for {unit_id}")
    expected_missing = expected_unpositioned_units or set()
    if unpositioned != expected_missing:
        fail(
            f"{label} returned the wrong unpositioned units: "
            f"{sorted(unpositioned)} != {sorted(expected_missing)}"
        )


def _numeric_lists_equal(actual: Any, expected: Iterable[Any], label: str) -> None:
    expected_list = list(expected)
    if not isinstance(actual, list) or len(actual) != len(expected_list):
        fail(
            f"{label} length changed: {len(actual) if isinstance(actual, list) else None}"
        )
    for index, (left, right) in enumerate(zip(actual, expected_list)):
        if left is None or right is None:
            if left is not right:
                fail(f"{label}[{index}] null handling differs from source")
            continue
        if type(left) not in (int, float) or not math.isclose(
            float(left), float(right), rel_tol=1e-12, abs_tol=1e-12
        ):
            fail(f"{label}[{index}] differs from source: {left!r} != {right!r}")


def validate_hd_collection(
    payload: dict[str, Any], *, source: Path, expected_units: int
) -> dict[int, dict[str, Any]]:
    if payload.get("available") is not True or payload.get("sourcePath") != str(source):
        fail(f"HD collection did not auto-discover {source}")
    if "schemaVersion" in payload:
        fail("HD collection still exposes removed schemaVersion metadata")
    rows = payload.get("units")
    if not isinstance(rows, list) or len(rows) != expected_units:
        fail(
            f"HD collection returned {len(rows) if isinstance(rows, list) else None} units"
        )
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get("unitId")) is not int:
            fail("HD collection contains an invalid unit row")
        if not isinstance(row.get("rates"), list) or len(row["rates"]) != HD_RAW_BINS:
            fail(f"HD cluster {row.get('unitId')} does not expose all 180 raw rates")
        by_id[row["unitId"]] = row
    if len(by_id) != expected_units:
        fail("HD collection contains duplicate unit IDs")
    return by_id


def validate_full_timeline_payload(
    client: ApiClient, dataset_id: str, cluster_id: int, shape: list[int], label: str
) -> None:
    _n_units, n_y, n_x, n_bins = shape
    expected_bytes = n_y * n_x * n_bins * 8
    # RF display controls are deliberately supplied. The unit data contract must
    # still return the full time axis used by Timeline, not a 0..200 ms slice.
    query = urllib.parse.urlencode({"rfStartMs": 0, "rfEndMs": 200})
    response, headers = client.request(
        f"api/datasets/{dataset_id}/units/{cluster_id}?{query}", timeout=120
    )
    with response:
        body = response.read(expected_bytes + 1)
    if headers.get_content_type() != "application/octet-stream":
        fail(f"{label} unit endpoint did not return binary data")
    expected_header = f"{n_y},{n_x},{n_bins}"
    if (
        headers.get("X-RF-Dtype") != "<f8"
        or headers.get("X-RF-Shape") != expected_header
    ):
        fail(f"{label} unit payload headers do not retain all {n_bins} bins")
    if len(body) != expected_bytes:
        fail(f"{label} returned {len(body)} bytes instead of {expected_bytes}")
    note(
        f"PASS {label} Timeline contract: all {n_bins} bins returned despite RF 0..200 ms controls"
    )


def open_dataset(
    client: ApiClient, path: Path, expected_shape: list[int]
) -> dict[str, Any]:
    metadata = client.json(
        "api/datasets/open", payload={"path": str(path)}, timeout=3600
    )
    if metadata.get("shape") != expected_shape:
        fail(f"API returned unexpected shape for {path.name}: {metadata.get('shape')}")
    if len(metadata.get("timeBinEdges", [])) != expected_shape[3] + 1:
        fail(f"API omitted part of the full timeline for {path.name}")
    if not isinstance(metadata.get("id"), str):
        fail(f"API metadata omitted the dataset ID for {path.name}")
    return metadata


def validate_unsupported_tuning_rejected(
    client: ApiClient, dataset_id: str, label: str
) -> None:
    payload = client.expect_error(
        f"api/datasets/{dataset_id}/hd", status=422, timeout=120
    )
    detail = payload.get("detail")
    if not isinstance(detail, str) or "Missing tuning-curve keys" not in detail:
        fail(f"{label} returned the wrong tuning rejection: {detail!r}")


def validate_remote_m17(client: ApiClient) -> None:
    validate_remote_listing(client, M17_PROBE_RF, "rf-json")
    validate_remote_listing(client, M17_HD, "tuning-json")
    validate_remote_listing(client, M17_POSITIONS, "positions-csv")

    integrated = open_dataset(client, M17_PROBE_RF, EXPECTED_M17_PROBE_SHAPE)
    capabilities = integrated.get("capabilities")
    if (
        not isinstance(capabilities, dict)
        or capabilities.get("probe") is not True
        or capabilities.get("hd") is not True
    ):
        fail(f"m17 260729_2 did not discover Probe and same-day HD: {capabilities}")
    dataset_id = integrated["id"]
    probe = client.json(f"api/datasets/{dataset_id}/probe", timeout=120)
    validate_probe_payload(
        probe, expected_channels=384, expected_units=620, label="m17 260729_2 /probe"
    )
    validate_unsupported_tuning_rejected(client, dataset_id, "m17 260729_2")
    validate_full_timeline_payload(
        client, dataset_id, 0, EXPECTED_M17_PROBE_SHAPE, "m17 260729_2"
    )
    note("PASS m17 integrated API: RF + 384/620 Probe; legacy tuning rejected")

    rotation = open_dataset(client, M17_ROTATION_RF, EXPECTED_M17_ROTATION_SHAPE)
    rotation_capabilities = rotation.get("capabilities")
    if (
        not isinstance(rotation_capabilities, dict)
        or rotation_capabilities.get("probe") is not False
        or rotation_capabilities.get("hd") is not True
    ):
        fail(
            f"m17 260729_4 must report missing Probe and discovered HD: {rotation_capabilities}"
        )
    validate_unsupported_tuning_rejected(client, rotation["id"], "m17 260729_4")
    note("PASS m17 rotation API: 596 RF units, missing Probe, legacy tuning rejected")


def validate_remote_m15(client: ApiClient, m15_hd: dict[str, Any]) -> None:
    metadata = open_dataset(client, M15_RF, EXPECTED_M15_RF_SHAPE)
    capabilities = metadata.get("capabilities")
    if (
        not isinstance(capabilities, dict)
        or capabilities.get("probe") is not True
        or capabilities.get("hd") is not True
    ):
        fail(f"m15 columnar sample did not discover Probe and HD: {capabilities}")
    dataset_id = metadata["id"]
    probe = client.json(f"api/datasets/{dataset_id}/probe", timeout=120)
    validate_probe_payload(
        probe, expected_channels=384, expected_units=146, label="m15 260630_3 /probe"
    )
    collection = client.json(f"api/datasets/{dataset_id}/hd", timeout=120)
    rows = validate_hd_collection(
        collection, source=M15_HD, expected_units=EXPECTED_M15_HD_UNITS
    )
    rf_units = set(metadata.get("unitPool", []))
    if len(rf_units & set(rows)) != 146 or not rf_units.issubset(rows):
        fail("m15 columnar RF/HD overlap must remain 146 of 146 RF units")

    zero_index = m15_hd["unit_id"].index(0)
    cluster = client.json(f"api/datasets/{dataset_id}/hd/0", timeout=120)
    if (
        cluster.get("available") is not True
        or cluster.get("sourcePath") != str(M15_HD)
        or "schemaVersion" in cluster
        or cluster.get("metadata") != m15_hd["metadata"]
        or cluster.get("hdClass") != m15_hd["unit_data"]["hd_class"][zero_index]
    ):
        fail("m15 columnar cluster metadata/class does not match the source JSON")
    _numeric_lists_equal(
        cluster.get("occupancyTimeS"), m15_hd["occupancy_time_s"], "m15 occupancy"
    )
    _numeric_lists_equal(
        cluster.get("spikeCounts"),
        m15_hd["spike_counts"][zero_index],
        "m15 cluster 0 counts",
    )
    _numeric_lists_equal(
        cluster.get("rates"),
        m15_hd["firing_rate_hz"][zero_index],
        "m15 cluster 0 rates",
    )
    note(
        "PASS m15 columnar API: metadata/class + 180 occupancy/count/rate bins, overlap 146/146"
    )


def validate_remote_m14(client: ApiClient) -> None:
    metadata = open_dataset(client, M14_RF, EXPECTED_M14_RF_SHAPE)
    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, dict) or capabilities.get("probe") is not True:
        fail("m14 API did not discover the real ProbeA geometry")
    probe = client.json(f"api/datasets/{metadata['id']}/probe", timeout=120)
    validate_probe_payload(
        probe, expected_channels=384, expected_units=220, label="m14 260615_3 /probe"
    )
    note("PASS m14 API: RF plus ProbeA with 384 channels and 220 units")


def validate_remote_m18(client: ApiClient) -> None:
    metadata = open_dataset(client, M18_RF, EXPECTED_M18_RF_SHAPE)
    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, dict) or capabilities.get("probe") is not True:
        fail("m18 API did not discover the real ProbeA geometry")
    probe = client.json(f"api/datasets/{metadata['id']}/probe", timeout=120)
    validate_probe_payload(
        probe,
        expected_channels=384,
        expected_units=192,
        expected_unpositioned_units=EXPECTED_M18_UNPOSITIONED_UNITS,
        label="m18 260812_3 /probe",
    )
    note("PASS m18 API: 384-channel background plus NaN positions for clusters 50/118")


def validate_api(base_url: str, host_header: str | None) -> None:
    client = ApiClient(base_url, host_header)
    client.login()
    health = client.json("api/health")
    if (
        health.get("status") != "ok"
        or health.get("version") != EXPECTED_WEB_VERSION
        or health.get("rfRoot") != str(RF_ROOT)
    ):
        fail(f"unexpected health response: {health}")
    load_legacy_hd()
    m15_hd = load_m15_hd()
    validate_remote_m17(client)
    validate_remote_m15(client, m15_hd)
    validate_remote_m14(client)
    validate_remote_m18(client)


def validate_files() -> tuple[dict[int, list[float]], dict[str, Any]]:
    m17_probe = validate_rf_file(
        M17_PROBE_RF,
        expected_bytes=EXPECTED_M17_PROBE_BYTES,
        expected_shape=EXPECTED_M17_PROBE_SHAPE,
        start_s=-0.1,
        end_s=0.4,
        label="m17 260729_2 RF",
    )
    m17_rotation = validate_rf_file(
        M17_ROTATION_RF,
        expected_bytes=EXPECTED_M17_ROTATION_BYTES,
        expected_shape=EXPECTED_M17_ROTATION_SHAPE,
        start_s=-0.1,
        end_s=0.4,
        label="m17 260729_4 rotation RF",
    )
    m15 = validate_rf_file(
        M15_RF,
        expected_bytes=EXPECTED_M15_RF_BYTES,
        expected_shape=EXPECTED_M15_RF_SHAPE,
        start_s=-0.1,
        end_s=0.2,
        label="m15 260630_3 RF",
    )
    validate_rf_file(
        M14_RF,
        expected_bytes=EXPECTED_M14_RF_BYTES,
        expected_shape=EXPECTED_M14_RF_SHAPE,
        start_s=-0.1,
        end_s=0.2,
        label="m14 260615_3 RF",
    )
    validate_rf_file(
        M18_RF,
        expected_bytes=EXPECTED_M18_RF_BYTES,
        expected_shape=EXPECTED_M18_RF_SHAPE,
        start_s=-0.1,
        end_s=0.4,
        label="m18 260812_3 RF",
    )
    validate_probe_files(
        M17_CHANNELS,
        M17_POSITIONS,
        expected_channels=384,
        expected_units=620,
        label="m17 260729_2 ProbeA",
    )
    validate_probe_files(
        M15_CHANNELS,
        M15_POSITIONS,
        expected_channels=384,
        expected_units=146,
        label="m15 260630_3 ProbeA",
    )
    validate_probe_files(
        M14_CHANNELS,
        M14_POSITIONS,
        expected_channels=384,
        expected_units=220,
        label="m14 260615_3 ProbeA",
    )
    validate_probe_files(
        M18_CHANNELS,
        M18_POSITIONS,
        expected_channels=384,
        expected_units=192,
        expected_unpositioned_units=EXPECTED_M18_UNPOSITIONED_UNITS,
        label="m18 260812_3 ProbeA",
    )
    legacy = load_legacy_hd()
    m15_hd = load_m15_hd()
    legacy_ids = set(legacy)
    if len(set(m17_probe["unitPool"]) & legacy_ids) != EXPECTED_M17_PROBE_HD_OVERLAP:
        fail("m17 260729_2 raw RF/HD overlap changed from 609")
    if (
        len(set(m17_rotation["unitPool"]) & legacy_ids)
        != EXPECTED_M17_ROTATION_HD_OVERLAP
    ):
        fail("m17 260729_4 raw RF/HD overlap changed from 584")
    m15_hd_ids = set(m15_hd["unit_id"])
    if len(set(m15["unitPool"]) & m15_hd_ids) != 146:
        fail("m15 raw RF/HD overlap changed from 146/146")
    note(
        "PASS raw RF/HD joins: m17 legacy fixture tracked; m15 columnar overlap 146/146"
    )
    return legacy, m15_hd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--files-only",
        action="store_true",
        help="validate source artifacts and frontend source without a running server",
    )
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="validate frontend source and require a built bundle; used by release.sh",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:3005/rfmapping",
        help="direct app URL or proxied URL such as http://127.0.0.1/rfmapping",
    )
    parser.add_argument(
        "--host-header",
        help="optional Host header for local Nginx validation",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.static_only:
        validate_frontend_assets(require_bundle=True)
        return 0

    before = validate_source_paths()
    validate_frontend_assets(require_bundle=False)
    validate_files()
    if not args.files_only:
        validate_api(args.base_url, args.host_header)
    after = {path: fingerprint(path) for path in before}
    changed = [str(path) for path in before if before[path] != after[path]]
    if changed:
        fail(f"source metadata changed during read-only validation: {changed}")
    note("PASS all source-of-truth files were unchanged")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
