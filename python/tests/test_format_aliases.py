import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import rfmapping_gui as gui
from rfmapping_viewer.hd_tuning import discover_hd_tuning_path, load_hd_tuning
from rfmapping_viewer.rf_dataset import load_rf_maps


def test_stable_support_modules_do_not_import_freemoving_hdf5() -> None:
    script = """
import importlib.abc
import sys

class BlockH5py(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "h5py" or fullname.startswith("h5py."):
            raise ModuleNotFoundError("h5py is intentionally absent from the stable viewer")
        return None

sys.meta_path.insert(0, BlockH5py())
import rfmapping_viewer.figure_export
import rfmapping_viewer.hd_tuning
import rfmapping_viewer.rf_dataset
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def _rf_payload() -> dict[str, object]:
    return {
        "unitsSpikeCounts": [[[[1]]]],
        "unitsSpikeCountsSize": [1, 1, 1, 1],
        "unitPool": 42,
        "xPositions": [0.0],
        "yPositions": [0.0],
        "timeBinEdges": [0.0, 0.1],
        "responseUnits": "spike_count",
        "responseNormalization": "none",
        "spikeCountDefinition": (
            "each_qualifying_trial_contributes_once_per_final_spatial_bin"
        ),
        "occupancyTimeSec": 0.1,
        "occupancyTimeSecSize": [1, 1],
        "occupancyTimeDefinition": (
            "sum_of_qualifying_trial_durations_per_final_spatial_bin"
        ),
    }


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_probe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "unit_index,unit_id,x_um,y_um\n0,42,10.0,20.0\n",
        encoding="utf-8",
    )
    return path


def test_alias_loaders_use_the_existing_json_and_csv_contracts(tmp_path: Path) -> None:
    rf_path = _write_json(tmp_path / "map.rfmap", _rf_payload())
    tuning_path = _write_json(
        tmp_path / "curve.tc",
        {"42": [float(index) for index in range(gui.HD_RAW_BIN_COUNT)]},
    )
    probe_path = _write_probe(tmp_path / "positions.probe")

    assert load_rf_maps(rf_path).unit_ids == [42]
    assert gui.RFMappingData(rf_path).unit_pool == [42]
    assert load_hd_tuning(tuning_path).unit_ids == (42,)
    assert gui.TuningCurveData.load(tuning_path).rates_for(42) is not None
    geometry = gui.load_probe_geometry(probe_path)
    assert geometry.units[0].unit_id == 42


def test_rf_discovery_includes_rfmap_and_legacy_json_only(tmp_path: Path) -> None:
    rfmap = _write_json(tmp_path / "map.rfmap", _rf_payload())
    legacy = _write_json(tmp_path / "legacy.json", _rf_payload())
    reserved = _write_json(tmp_path / "TUNING_CURVES.JSON", _rf_payload())
    _write_json(tmp_path / "curve.tc", {"42": [0.0] * gui.HD_RAW_BIN_COUNT})
    _write_probe(tmp_path / "positions.probe")

    assert set(gui.discover_json_files(tmp_path)) == {
        rfmap.resolve(),
        legacy.resolve(),
    }
    assert gui.RFMappingData(reserved).unit_pool == [42]


def test_tuning_discovery_prefers_tc_within_the_same_session(tmp_path: Path) -> None:
    rf_path = _write_json(
        tmp_path
        / "260730_3"
        / "data"
        / "rfmapping"
        / "ProbeA"
        / "map.rfmap",
        _rf_payload(),
    )
    directory = tmp_path / "260730_1" / "data" / "tuning_curves" / "ProbeA"
    alias = _write_json(directory / "tuning_curves.tc", {})
    legacy = _write_json(directory / "tuning_curves.json", {})

    assert gui.discover_tuning_curve_path(rf_path) == alias.resolve()
    assert discover_hd_tuning_path(rf_path) == alias

    alias.unlink()
    assert gui.discover_tuning_curve_path(rf_path) == legacy.resolve()
    assert discover_hd_tuning_path(rf_path) == legacy


def test_probe_discovery_prefers_probe_and_falls_back_to_csv(tmp_path: Path) -> None:
    data_root = tmp_path / "260730_1" / "data"
    rf_path = _write_json(
        data_root / "rfmapping" / "ProbeA" / "map.rfmap",
        _rf_payload(),
    )
    directory = data_root / "spike_position" / "ProbeA"
    alias = _write_probe(directory / "positions.probe")
    legacy = _write_probe(directory / "positions.csv")

    discovered = gui.discover_probe_geometry_paths(rf_path)
    assert discovered is not None
    assert discovered[1] == alias.resolve()

    alias.unlink()
    discovered = gui.discover_probe_geometry_paths(rf_path)
    assert discovered is not None
    assert discovered[1] == legacy.resolve()


def test_document_routing_and_dialog_filters_cover_new_and_legacy_names() -> None:
    assert gui.document_kind("MAP.RFMAP") == "rf"
    assert gui.document_kind("curve.TC") == "tuning"
    assert gui.document_kind("positions.PROBE") == "probe"
    assert gui.document_kind("legacy.json") == "rf"
    assert gui.document_kind("notes.txt") == "unsupported"
    assert gui.RF_DOCUMENT_FILETYPES[0][1] == "*.rfmap *.json"
    assert gui.TUNING_CURVE_FILETYPES[0][1] == "*.tc *.json"
    assert gui.PROBE_POSITION_FILETYPES[0][1] == "*.probe *.csv"

    viewer = SimpleNamespace(
        _viewer_ready=True,
        opened=[],
        attached=[],
        _open_json_window=lambda path: None,
        _open_external_companion=lambda path: viewer.attached.append(path),
    )
    gui.RFMViewer._on_macos_open_documents(
        viewer,
        "/tmp/tuning.tc",
        "/tmp/positions.probe",
        "/tmp/notes.txt",
    )
    assert viewer.attached == [Path("/tmp/tuning.tc"), Path("/tmp/positions.probe")]
