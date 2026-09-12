"""Persistent viewer preferences and validation."""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from rfmapping_viewer.constants import (
    DEFAULT_HD_DISPLAY_BINS,
    DEFAULT_HD_SMOOTH_SIGMA,
    DEFAULT_RF_SUM_END_MS,
    DEFAULT_RF_SUM_START_MS,
    DEFAULT_TUNING_CURVE_SESSION,
    HD_BIN_DIVISORS,
    HD_RAW_BIN_COUNT,
    PALETTES,
    POLAR_RADIUS_MODES,
    SETTINGS_SCHEMA_VERSION,
    TUNING_LAYOUTS,
    TUNING_PLOT_MODES,
    VALUE_MODES,
    VALUE_MODE_RATE,
    WAVEFORM_CHANNEL_MODES,
)


def viewer_settings_path(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-user settings path without requiring a GUI."""

    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    if platform == "darwin":
        return home / "Library" / "Application Support" / "RF Map Viewer" / "settings.json"
    if platform.startswith("win"):
        appdata = environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "RF Map Viewer" / "settings.json"
    xdg_config = environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else home / ".config"
    return base / "rf-map-viewer" / "settings.json"


def normalize_hd_bin_count(value: int) -> int:
    """Clamp a requested display count to the greatest divisor of 180 below it."""

    requested = max(1, min(HD_RAW_BIN_COUNT, int(value)))
    return max(divisor for divisor in HD_BIN_DIVISORS if divisor <= requested)


@dataclass(frozen=True)
class ViewerSettings:
    schema_version: int = SETTINGS_SCHEMA_VERSION
    show_tuning_curve: bool = True
    auto_load_tuning_curve: bool = True
    tuning_curve_session: int = DEFAULT_TUNING_CURVE_SESSION
    show_waveform: bool = True
    show_probe_layout: bool = True
    auto_load_probe_layout: bool = True
    rf_sum_start_ms: float = DEFAULT_RF_SUM_START_MS
    rf_sum_end_ms: float = DEFAULT_RF_SUM_END_MS
    rf_subtract: bool = False
    rf_difference_start_ms: float = 80.0
    rf_difference_end_ms: float = 160.0
    rf_subtract_start_ms: float = 0.0
    rf_subtract_end_ms: float = 80.0
    rf_filter_units_with_zero_bins: bool = True
    rf_zero_bin_threshold: int = 1
    rf_time_resolution_ms: float = 1.0
    rf_value_mode: str = VALUE_MODE_RATE
    rf_x_bins: int = 0
    rf_y_bins: int = 0
    rf_smooth_radius: int = 0
    rf_flip_y: bool = False
    rf_palette: str = "Gray"
    rf_polar_radius: str = POLAR_RADIUS_MODES[1]
    rf_polar_layout: bool = False
    rf_rgb_mode: bool = False
    default_viewer_tab: str = "rf"
    waveform_channel_mode: str = "same_x_column"
    tuning_plot_mode: str = "Auto"
    tuning_layout: str = TUNING_LAYOUTS[0]
    tuning_display_bins: int = DEFAULT_HD_DISPLAY_BINS
    tuning_smoothing: bool = True
    tuning_smooth_sigma: float = DEFAULT_HD_SMOOTH_SIGMA
    tuning_compare_scale: bool = False

    @classmethod
    def from_mapping(cls, payload: object) -> ViewerSettings:
        defaults = cls()
        if not isinstance(payload, Mapping):
            return defaults
        schema = payload.get("schema_version", SETTINGS_SCHEMA_VERSION)
        if type(schema) is not int or schema != SETTINGS_SCHEMA_VERSION:
            return defaults

        def boolean(name: str) -> bool:
            value = payload.get(name, getattr(defaults, name))
            return value if type(value) is bool else getattr(defaults, name)

        def finite_float(name: str, *, positive: bool = False) -> float:
            value = payload.get(name, getattr(defaults, name))
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return getattr(defaults, name)
            result = float(value)
            if not math.isfinite(result) or (positive and result <= 0.0):
                return getattr(defaults, name)
            return result

        def integer(name: str, low: int, high: int) -> int:
            value = payload.get(name, getattr(defaults, name))
            if type(value) is not int:
                return getattr(defaults, name)
            return max(low, min(high, value))

        def positive_integer(name: str) -> int:
            value = payload.get(name, getattr(defaults, name))
            if type(value) is not int or value <= 0:
                return getattr(defaults, name)
            return value

        start_ms = finite_float("rf_sum_start_ms")
        end_ms = finite_float("rf_sum_end_ms")
        if start_ms >= end_ms:
            start_ms = defaults.rf_sum_start_ms
            end_ms = defaults.rf_sum_end_ms
        difference_ranges: dict[str, float] = {}
        for prefix in ("rf_difference", "rf_subtract"):
            first, last = finite_float(f"{prefix}_start_ms"), finite_float(f"{prefix}_end_ms")
            if first >= last:
                first = getattr(defaults, f"{prefix}_start_ms")
                last = getattr(defaults, f"{prefix}_end_ms")
            difference_ranges.update({f"{prefix}_start_ms": first, f"{prefix}_end_ms": last})

        value_mode = payload.get("rf_value_mode", defaults.rf_value_mode)
        if value_mode not in VALUE_MODES:
            value_mode = defaults.rf_value_mode
        palette = payload.get("rf_palette", defaults.rf_palette)
        if palette not in PALETTES:
            palette = defaults.rf_palette
        polar_radius = payload.get("rf_polar_radius", defaults.rf_polar_radius)
        if polar_radius not in POLAR_RADIUS_MODES:
            polar_radius = defaults.rf_polar_radius
        viewer_tab = payload.get("default_viewer_tab", defaults.default_viewer_tab)
        if viewer_tab not in {"rf", "delay", "timeline"}:
            viewer_tab = defaults.default_viewer_tab
        waveform_channel_mode = payload.get(
            "waveform_channel_mode", defaults.waveform_channel_mode
        )
        if waveform_channel_mode not in WAVEFORM_CHANNEL_MODES:
            waveform_channel_mode = defaults.waveform_channel_mode
        tuning_mode = payload.get("tuning_plot_mode", defaults.tuning_plot_mode)
        if tuning_mode not in TUNING_PLOT_MODES:
            tuning_mode = defaults.tuning_plot_mode
        tuning_layout = payload.get("tuning_layout", defaults.tuning_layout)
        if tuning_layout not in TUNING_LAYOUTS:
            tuning_layout = defaults.tuning_layout
        raw_hd_bins = payload.get("tuning_display_bins", defaults.tuning_display_bins)
        if type(raw_hd_bins) is not int:
            raw_hd_bins = defaults.tuning_display_bins

        return cls(
            show_tuning_curve=boolean("show_tuning_curve"),
            auto_load_tuning_curve=boolean("auto_load_tuning_curve"),
            tuning_curve_session=positive_integer("tuning_curve_session"),
            show_waveform=boolean("show_waveform"),
            show_probe_layout=boolean("show_probe_layout"),
            auto_load_probe_layout=boolean("auto_load_probe_layout"),
            rf_sum_start_ms=start_ms,
            rf_sum_end_ms=end_ms,
            rf_subtract=boolean("rf_subtract"),
            **difference_ranges,
            rf_filter_units_with_zero_bins=boolean(
                "rf_filter_units_with_zero_bins"
            ),
            rf_zero_bin_threshold=integer(
                "rf_zero_bin_threshold", 1, 100_000
            ),
            rf_time_resolution_ms=finite_float("rf_time_resolution_ms", positive=True),
            rf_value_mode=value_mode,
            rf_x_bins=integer("rf_x_bins", 0, 100_000),
            rf_y_bins=integer("rf_y_bins", 0, 100_000),
            rf_smooth_radius=integer("rf_smooth_radius", 0, 3),
            rf_flip_y=boolean("rf_flip_y"),
            rf_palette=palette,
            rf_polar_radius=polar_radius,
            rf_polar_layout=boolean("rf_polar_layout"),
            rf_rgb_mode=boolean("rf_rgb_mode"),
            default_viewer_tab=viewer_tab,
            waveform_channel_mode=waveform_channel_mode,
            tuning_plot_mode=tuning_mode,
            tuning_layout=tuning_layout,
            tuning_display_bins=normalize_hd_bin_count(raw_hd_bins),
            tuning_smoothing=boolean("tuning_smoothing"),
            tuning_smooth_sigma=finite_float("tuning_smooth_sigma", positive=True),
            tuning_compare_scale=boolean("tuning_compare_scale"),
        )

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def load_viewer_settings(path: Path | None = None) -> ViewerSettings:
    settings_path = viewer_settings_path() if path is None else Path(path)
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ViewerSettings()
    return ViewerSettings.from_mapping(payload)


def save_viewer_settings(settings: ViewerSettings, path: Path | None = None) -> Path:
    settings_path = viewer_settings_path() if path is None else Path(path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=settings_path.parent,
            prefix=f".{settings_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            json.dump(settings.to_mapping(), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        os.replace(temporary_path, settings_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return settings_path
