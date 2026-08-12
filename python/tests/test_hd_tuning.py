from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from rfmapping_viewer.hd_tuning import load_hd_tuning, normalize_hd_bin_count


def _payload() -> dict[str, object]:
    occupancy = [1.0] * 180
    first_counts = [0] * 180
    first_counts[0] = 10
    second_counts = [2] * 180
    return {
        "metadata": {"probe": "ProbeA"},
        "angle_bin_edges_deg": np.linspace(0, 360, 181).tolist(),
        "occupancy_samples": [1] * 180,
        "occupancy_time_s": occupancy,
        "unit_id": [7, 42],
        "spike_counts": [first_counts, second_counts],
        "firing_rate_hz": [first_counts, second_counts],
        "unit_data": {
            "hd_class": [1, None],
            "rate_mvl": [0.5, 0.1],
        },
    }


def _write(tmp_path: Path, payload: dict[str, object] | None = None) -> Path:
    path = tmp_path / "tuning_curves.json"
    path.write_text(json.dumps(payload or _payload()), encoding="utf-8")
    return path


def test_load_and_lookup_units(tmp_path: Path) -> None:
    data = load_hd_tuning(_write(tmp_path))

    assert data.unit_ids == (7, 42)
    assert data.by_unit_id(7).hd_class == 1
    assert data.by_unit_id(42).hd_class is None
    with pytest.raises(KeyError):
        data.by_unit_id(99)


def test_processed_curve_aggregates_counts_and_occupancy(tmp_path: Path) -> None:
    data = load_hd_tuning(_write(tmp_path))

    curve = data.processed_curve(42, display_bins=30, smoothing=False)

    assert curve.angles_deg.shape == (30,)
    np.testing.assert_allclose(curve.rates_hz, np.full(30, 2.0))
    assert curve.angles_deg[0] == pytest.approx(6.0)


def test_smoothed_curve_is_circular_and_conserves_total_signal(tmp_path: Path) -> None:
    data = load_hd_tuning(_write(tmp_path))

    curve = data.processed_curve(7, display_bins=180, smoothing=True, sigma=1.5)

    assert curve.rates_hz[0] > curve.rates_hz[45]
    assert curve.rates_hz[-1] > curve.rates_hz[45]
    assert curve.rates_hz.sum() == pytest.approx(10.0)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(180, 180), (31, 30), (29, 20), (0, 1), (999, 180)],
)
def test_normalize_hd_bin_count_uses_largest_supported_divisor(
    requested: int,
    expected: int,
) -> None:
    assert normalize_hd_bin_count(requested) == expected


def test_rejects_wrong_column_shape(tmp_path: Path) -> None:
    payload = _payload()
    payload["spike_counts"] = [[1] * 179, [1] * 179]

    with pytest.raises(ValueError, match="shape"):
        load_hd_tuning(_write(tmp_path, payload))
