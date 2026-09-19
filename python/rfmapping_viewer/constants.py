"""Stable viewer defaults and display contracts."""

from __future__ import annotations

import os
import re
from pathlib import Path


DEFAULT_JSON_DIR = Path("data")
DEFAULT_JSON = DEFAULT_JSON_DIR / "unitsSpikeCounts_260701_1.json"
RF_DOCUMENT_EXTENSIONS = (".rfmap", ".json")
TUNING_CURVE_EXTENSIONS = (".tc", ".json")
PROBE_POSITION_EXTENSIONS = (".probe", ".csv")
TUNING_CURVE_FILENAMES = tuple(
    f"tuning_curves{extension}" for extension in TUNING_CURVE_EXTENSIONS
)
PROBE_POSITION_FILENAMES = tuple(
    f"positions{extension}" for extension in PROBE_POSITION_EXTENSIONS
)
RF_DOCUMENT_FILETYPES = (
    ("RF mapping files", "*.rfmap *.json"),
    ("RF Map document", "*.rfmap"),
    ("JSON document", "*.json"),
    ("All files", "*.*"),
)
TUNING_CURVE_FILETYPES = (
    ("Tuning curve files", "*.tc *.json"),
    ("Tuning curve document", "*.tc"),
    ("JSON document", "*.json"),
    ("All files", "*.*"),
)
PROBE_POSITION_FILETYPES = (
    ("Probe position files", "*.probe *.csv"),
    ("Probe position document", "*.probe"),
    ("CSV document", "*.csv"),
    ("All files", "*.*"),
)
APP_VERSION = "1.10.1"
APP_EDITION = "Full"
APP_DISPLAY_VERSION = APP_VERSION
INNER_BLANK_ROWS = 4
POLAR_PAD_ROWS = 1
SINGLETON_Y_REFERENCE_COLUMNS = 30
SINGLETON_Y_REFERENCE_ROWS = 7
STARTUP_EVENT_WAIT_MS = 350
ASYNC_DOCUMENT_LOAD_BYTES = 8 * 1024 * 1024
RF_COUNT_CACHE_UNIT_LIMIT = 4
DEFAULT_RF_SUM_START_MS = 0.0
DEFAULT_RF_SUM_END_MS = 200.0
SETTINGS_SCHEMA_VERSION = 1
HD_RAW_BIN_COUNT = 180
DEFAULT_HD_DISPLAY_BINS = 30
DEFAULT_HD_SMOOTH_SIGMA = 1.5
DEFAULT_TUNING_CURVE_SESSION = 1
GAUSSIAN_TRUNCATE = 4.0
MACOS_FULLSCREEN_MAX_SIZE = float.fromhex("0x1.fffffep+127")
HD_BIN_DIVISORS = tuple(
    count for count in range(1, HD_RAW_BIN_COUNT + 1) if HD_RAW_BIN_COUNT % count == 0
)
TUNING_PLOT_MODES = ("Auto", "Polar", "Line")
TUNING_LAYOUTS = ("Side by side", "Stacked")
VALUE_MODE_COUNT = "Spike count"
VALUE_MODE_RATE = "Mean firing rate (Hz)"
VALUE_MODES = (VALUE_MODE_COUNT, VALUE_MODE_RATE)
PALETTES = ("Gray", "Viridis", "Inferno")
POLAR_RADIUS_MODES = ("MATLAB row 1 inner", "Display bottom inner")
WAVEFORM_CHANNEL_MODE_LABELS = {
    "same_x_column": "Same x column",
    "same_shank": "Same shank",
}
_USE_PATH_CSV_PUBLICATION = os.name == "nt"
WAVEFORM_CHANNEL_MODES = tuple(WAVEFORM_CHANNEL_MODE_LABELS)
WAVEFORM_CHANNEL_MODE_BY_LABEL = {
    label: mode for mode, label in WAVEFORM_CHANNEL_MODE_LABELS.items()
}
AxisGroup = tuple[int, int]
CellRef = tuple[int, int, int, int]
PROBE_CLICK_WIDTH_UM = 160.0
PROBE_CLICK_HEIGHT_UM = 75.0
PAIR_SYNC_ALL_FIELDS = frozenset(
    {
        "unit",
        "value_mode",
        "active_time",
        "timeline_selection",
        "rf_range",
        "time_resolution",
        "x_bins",
        "y_bins",
        "smoothing",
        "flip_y",
        "palette",
        "polar_radius",
        "spatial_format",
        "delay_rgb",
        "selected_cell",
        "timeline_scroll",
        "selected_tab",
        "tuning_display",
        "optional_views",
    }
)
_RECORDING_SESSION_RE = re.compile(r"^\d{6}_\d+$")
