"""Session ADN annotations shared with the desktop viewers."""

from pathlib import Path
import re
import threading

import numpy as np

from .paths import PathAccessError, is_within

ADN_DTYPE = np.dtype([("probe", "U16"), ("unit_id", "i8")])
_SESSION_RE = re.compile(r"^\d{6}_\d+$")
_SELECTION_LOCK = threading.Lock()


def adn_selection_path(source: Path, root: Path) -> Path | None:
    for parent in source.parents:
        if not is_within(parent, root):
            break
        if _SESSION_RE.fullmatch(parent.name):
            path = parent / "data" / "adn_neuron.npy"
            # The annotation is the only writable sidecar. Reject escaping data
            # directories and existing annotation symlinks before any write.
            if path.is_symlink() or not is_within(path.resolve(), root):
                raise PathAccessError("ADN annotation is outside the allowed data root")
            return path
    return None


def _load_selection(path: Path) -> np.ndarray:
    if not path.exists():
        return np.empty(0, dtype=ADN_DTYPE)
    rows = np.load(path, allow_pickle=False)
    if rows.ndim != 1 or rows.dtype != ADN_DTYPE:
        raise ValueError("ADN selection must contain probe (U16) and unit_id (int64) fields.")
    return rows


def load_adn_units(path: Path, probe: str) -> list[int]:
    with _SELECTION_LOCK:
        rows = _load_selection(path)
        return sorted(set(map(int, rows["unit_id"][rows["probe"] == probe])))


def save_adn_units(path: Path, probe: str, unit_ids: list[int]) -> list[int]:
    ids = sorted(set(unit_ids))
    with _SELECTION_LOCK:
        rows = _load_selection(path)
        selected = np.empty(len(ids), dtype=ADN_DTYPE)
        selected["probe"] = probe
        selected["unit_id"] = ids
        rows = np.concatenate((rows[rows["probe"] != probe], selected))
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, rows, allow_pickle=False)
    return ids
