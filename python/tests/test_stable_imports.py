"""Stable data and display modules remain usable without a GUI runtime."""

from pathlib import Path
import subprocess
import sys


def test_data_and_display_import_without_tk_or_alpha_dependencies():
    subprocess.run(
        [sys.executable, "-c", """
import sys
sys.modules["tkinter"] = None
sys.modules["h5py"] = None
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.settings import ViewerSettings
from rfmapping_viewer.display import physical_time_groups
assert "rfmapping_gui" not in sys.modules
assert "rfmapping_viewer.tk_support" not in sys.modules
assert "rfmapping_viewer.figure_composer" not in sys.modules
assert "rfmapping_viewer.fm_dataset" not in sys.modules
assert ViewerSettings().rf_sum_start_ms == 0.0
assert physical_time_groups([0.0, 1.0, 2.0], 1.0) == [(0, 0), (1, 1)]
"""],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        timeout=15,
    )
