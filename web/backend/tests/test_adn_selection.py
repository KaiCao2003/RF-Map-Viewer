from pathlib import Path

import numpy as np
import pytest

from rfmapping_web.adn_selection import ADN_DTYPE, adn_selection_path, load_adn_units, save_adn_units
from rfmapping_web.paths import PathAccessError
from test_rfmapping_web import app, authenticated_client, settings, write_json


def test_annotations_preserve_other_probes_and_clear_one_probe(tmp_path):
    path = tmp_path / "data" / "adn_neuron.npy"
    save_adn_units(path, "ProbeA", [22, 11, 11])
    save_adn_units(path, "ProbeB", [22, 33])
    save_adn_units(path, "ProbeA", [])
    assert load_adn_units(path, "ProbeA") == []
    assert load_adn_units(path, "ProbeB") == [22, 33]
    assert np.load(path, allow_pickle=False).dtype == ADN_DTYPE


def test_annotation_path_rejects_symlinks_and_outside_session(tmp_path):
    root = tmp_path / "root"
    data = root / "261012_1" / "data"
    data.mkdir(parents=True)
    source = data / "ProbeA" / "mapping.rfmap"
    assert adn_selection_path(source, root) == data / "adn_neuron.npy"
    assert adn_selection_path(root / "mapping.rfmap", root) is None
    outside = tmp_path / "protected.npy"
    outside.write_bytes(b"keep")
    (data / "adn_neuron.npy").symlink_to(outside)
    with pytest.raises(PathAccessError):
        adn_selection_path(source, root)
    assert outside.read_bytes() == b"keep"


def test_get_and_explicit_save_use_open_recording_session(app, settings):
    data = settings.rf_root / "mouse" / "261012_1" / "data"
    source = write_json(data / "rfmapping" / "ProbeA" / "mapping.rfmap")
    positions = data / "spike_position" / "ProbeA" / "positions.csv"
    positions.parent.mkdir(parents=True)
    positions.write_text("unit_index,unit_id,x_um,y_um\n0,11,0,100\n1,22,20,200\n")
    original = source.read_bytes(), positions.read_bytes()
    output = data / "adn_neuron.npy"
    with authenticated_client(app) as client:
        opened = client.post("/api/datasets/open", json={"path": str(source)})
        assert opened.status_code == 200, opened.text
        endpoint = f"/api/datasets/{opened.json()['id']}/adn-selection"
        loaded = client.get(endpoint)
        assert loaded.status_code == 200, loaded.text
        assert loaded.json() == {"available": True, "path": str(output), "probe": "ProbeA", "unitIds": []}
        assert not output.exists()
        save_adn_units(output, "ProbeB", [44])
        saved = client.put(endpoint, json={"unitIds": [22, 11, 22]})
        assert saved.status_code == 200, saved.text
        assert client.get(endpoint).json()["unitIds"] == [11, 22]
        assert load_adn_units(output, "ProbeB") == [44]
        assert client.put(endpoint, json={"unitIds": [True]}).status_code == 422
    assert (source.read_bytes(), positions.read_bytes()) == original


def test_malformed_annotation_is_not_replaced(tmp_path):
    path = tmp_path / "adn_neuron.npy"
    np.save(path, np.array([1, 2]))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="probe"):
        save_adn_units(path, "ProbeA", [11])
    assert path.read_bytes() == before
