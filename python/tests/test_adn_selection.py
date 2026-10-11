import tempfile
import unittest
from pathlib import Path

import numpy as np

from rfmapping_viewer.adn_selection import (
    ADN_DTYPE,
    adn_selection_path,
    load_adn_units,
    save_adn_units,
    unit_ids_in_polygon,
)
from rfmapping_viewer.companions import ProbeGeometry, ProbeUnitPosition


def geometry(*units: tuple[int, float | None, float | None]) -> ProbeGeometry:
    return ProbeGeometry(
        "ProbeA", Path("positions.csv"), None, (),
        tuple(ProbeUnitPosition(*unit) for unit in units),
    )


class ADNSelectionTests(unittest.TestCase):
    def test_polygon_uses_physical_positions_and_includes_boundary(self) -> None:
        probe = geometry(
            (42, 5, 5), (8, 0, 5), (100, 10, 10), (17, 10.01, 5),
            (42, 5, 5), (99, -1, 1),
        )
        vertices = [(0, 0), (10, 0), (10, 10), (0, 10)]

        self.assertEqual(unit_ids_in_polygon(probe, vertices), [42, 8, 100])
        self.assertEqual(unit_ids_in_polygon(probe, vertices[::-1]), [42, 8, 100])
        self.assertEqual(unit_ids_in_polygon(probe, vertices + vertices[:1]), [42, 8, 100])

    def test_slanted_boundary_does_not_include_points_beyond_edge(self) -> None:
        probe = geometry((1, 5, 5), (2, 10, 0), (3, 10.01, -0.01), (4, 5, 5.01))

        self.assertEqual(
            unit_ids_in_polygon(probe, [(0, 0), (10, 0), (0, 10)]),
            [1, 2],
        )

    def test_concave_polygon_and_unlocated_units(self) -> None:
        probe = geometry(
            (1, 1, 1), (2, 3, 3), (3, 1, 3), (4, None, None),
            (5, np.nan, np.nan), (6, np.inf, 1),
        )
        vertices = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)]

        self.assertEqual(unit_ids_in_polygon(probe, vertices), [1, 3])
        self.assertEqual(unit_ids_in_polygon(geometry(), vertices), [])
        with self.assertRaises(ValueError):
            unit_ids_in_polygon(probe, [(0, 0), (1, 1)])
        with self.assertRaises(ValueError):
            unit_ids_in_polygon(probe, [(0, 0), (1, np.nan), (1, 1)])

    def test_session_path_uses_open_recording(self) -> None:
        source = Path("/recordings/m21/261006_3/data/rfmapping/good/window/ProbeA/regular.rfmap")
        self.assertEqual(
            adn_selection_path(source),
            Path("/recordings/m21/261006_3/data/adn_neuron.npy"),
        )
        self.assertIsNone(adn_selection_path(Path("/exports/regular_261006_3_A.rfmap")))

    def test_save_replaces_current_probe_and_preserves_other_probes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "261006_3" / "data" / "adn_neuron.npy"
            self.assertEqual(load_adn_units(path, "ProbeA"), set())
            save_adn_units(path, "ProbeA", [42, 17, 42])
            save_adn_units(path, "ProbeB", [42, 8])
            save_adn_units(path, "ProbeA", [99, 17])

            self.assertEqual(load_adn_units(path, "ProbeA"), {17, 99})
            self.assertEqual(load_adn_units(path, "ProbeB"), {8, 42})
            rows = np.load(path, allow_pickle=False)
            self.assertEqual(rows.dtype, ADN_DTYPE)
            self.assertEqual(rows[rows["probe"] == "ProbeA"]["unit_id"].tolist(), [17, 99])
            save_adn_units(path, "ProbeA", [])
            self.assertEqual(load_adn_units(path, "ProbeA"), set())
            self.assertEqual(load_adn_units(path, "ProbeB"), {8, 42})
            save_adn_units(path, "ProbeB", [])
            self.assertEqual(np.load(path, allow_pickle=False).shape, (0,))

    def test_wrong_file_schema_is_rejected_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "adn_neuron.npy"
            np.save(path, np.asarray([17, 42]), allow_pickle=False)
            before = path.read_bytes()

            with self.assertRaises(ValueError):
                load_adn_units(path, "ProbeA")
            with self.assertRaises(ValueError):
                save_adn_units(path, "ProbeA", [99])
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
