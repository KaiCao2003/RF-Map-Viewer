"""Synthetic RF fixtures shared by the stable viewer tests."""

import atexit
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from rfmapping_viewer.settings import ViewerSettings


_tk_root = None


def tk_test_root():
    """Keep Cocoa's first application window/interpreter alive for the suite."""
    from rfmapping_viewer.tk_support import tk

    global _tk_root
    if _tk_root is None:
        _tk_root = tk.Tk()
        _tk_root.withdraw()
        # Viewer tests create their own interpreters; implicit images/variables
        # must use that viewer's root, not this hidden application lifetime root.
        tk._default_root = None
        atexit.register(_tk_root.destroy)
    return _tk_root


class Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def app_stub(**state):
    return SimpleNamespace(**{
        "_rfm_viewer_windows": [],
        "_rfm_pairing_enabled": False,
        "_rfm_pairing_broadcasting": False,
        "_rfm_pairing_state": None,
        "_rfm_quitting": False,
        **state,
    })


def viewer_stub(**state):
    return SimpleNamespace(**{
        "settings": ViewerSettings(rf_filter_units_with_zero_bins=False),
        "_app_root": app_stub(),
        "_viewer_ready": True,
        "_pair_apply_in_progress": False,
        "spatial_region": None,
        "probe_geometry": None,
        "selected_cell": None,
        "_base_bin_cache": None,
        "_time_groups_cache": None,
        **state,
    })


def unit_data(unit_pool, **fields):
    units = {unit_id: SimpleNamespace(unit_index=index) for index, unit_id in enumerate(unit_pool)}
    return SimpleNamespace(**{
        "unit_pool": list(unit_pool),
        "unit_archive": None,
        "n_units": len(unit_pool),
        "rf_map_by_unit_id": units.__getitem__,
        **fields,
    })


def write_payload(payload: dict) -> tuple[tempfile.TemporaryDirectory, Path]:
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "rf.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return directory, path


def current_rf_payload(
    payload: dict,
    occupancy_time_s: list[list[float]] | float | None = None,
) -> dict:
    n_y, n_x = payload["unitsSpikeCountsSize"][1:3]
    if occupancy_time_s is None:
        occupancy_time_s = [
            [1.0 for _x in range(n_x)]
            for _y in range(n_y)
        ]
    payload.update(
        responseUnits="spike_count",
        responseNormalization="none",
        spikeCountDefinition="each_qualifying_trial_contributes_once_per_final_spatial_bin",
        occupancyTimeSec=occupancy_time_s,
        occupancyTimeSecSize=[n_y, n_x],
        occupancyTimeDefinition="sum_of_qualifying_trial_durations_per_final_spatial_bin",
    )
    return payload


def base_payload() -> dict:
    return current_rf_payload({
        "unitsSpikeCounts": [[[[10, 20, 30], [5, 10, 15]]]],
        "unitsSpikeCountsSize": [1, 1, 2, 3],
        "unitPool": [42],
        "xPositions": [-1, 1],
        "yPositions": [0],
        "timeBinEdges": [-0.1, 0.0, 0.05, 0.2],
    }, [[1.0, 0.75]])
