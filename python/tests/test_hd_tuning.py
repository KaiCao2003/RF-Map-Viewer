from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from rfmapping_viewer.hd_tuning import (
    HDTuningData,
    TuningCurveClassificationProvenance,
    TuningCurveData,
    TuningCurveMetadata,
    TuningCurveTTLProvenance,
    load_hd_tuning,
    normalize_hd_bin_count,
)


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
        # Keep rate rows independent from count rows so mutation-based
        # validation tests cannot accidentally alter both scientific fields.
        "firing_rate_hz": [first_counts.copy(), second_counts.copy()],
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


def test_loads_legacy_unit_rate_mapping_and_preserves_missing_observation_mode(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        {
            "7": [float(index % 5) for index in range(180)],
            "42": [2.0] * 180,
        },
    )

    data = load_hd_tuning(path)

    assert data.unit_ids == (7, 42)
    assert data.path.is_absolute()
    assert tuple(data.curves) == (7, 42)
    assert data.spike_counts == {}
    assert data.metadata is None
    assert data.rates_for(7) is not None
    assert data.hd_class_for(7) is None
    assert HDTuningData.load(path).unit_ids == (7, 42)
    assert TuningCurveData is HDTuningData
    curve = data.processed_curve(42, display_bins=30, smoothing=False)
    np.testing.assert_allclose(curve.rates_hz, np.full(30, 2.0))


def test_loads_nested_schema_v2_and_keeps_zero_occupancy_missing(
    tmp_path: Path,
) -> None:
    occupancy = [1.0] * 180
    occupancy[1] = 0.0
    counts = [2] * 180
    counts[1] = 0
    rates: list[float | None] = [2.0] * 180
    rates[1] = None
    path = _write(
        tmp_path,
        {
            "schema_version": 2,
            "metadata": {"probe": "ProbeB"},
            "angle_bin_edges_deg": np.linspace(0, 360, 181).tolist(),
            "occupancy_time_s": occupancy,
            "units": [
                {
                    "unit_id": 99,
                    "spike_counts": counts,
                    "firing_rate_hz": rates,
                    "hd_class": 2,
                }
            ],
        },
    )

    data = load_hd_tuning(path)

    assert data.unit_ids == (99,)
    assert data.by_unit_id(99).hd_class == 2
    assert np.isnan(data.by_unit_id(99).raw_rates_hz[1])
    curve = data.processed_curve(99, display_bins=180, smoothing=False)
    assert np.isnan(curve.rates_hz[1])

    # Counts and exposure are smoothed together.  A raw missing bin may gain
    # support from its observed neighbours, but is never treated as a 0 Hz
    # observation.
    smoothed = data.processed_curve(99, display_bins=180, smoothing=True, sigma=1.5)
    np.testing.assert_allclose(smoothed.rates_hz, np.full(180, 2.0))


def test_columnar_zero_occupancy_is_missing_and_validated(tmp_path: Path) -> None:
    payload = _payload()
    payload["occupancy_time_s"][1] = 0.0  # type: ignore[index]
    for counts, rates in zip(payload["spike_counts"], payload["firing_rate_hz"]):  # type: ignore[arg-type]
        counts[1] = 0
        rates[1] = None

    data = load_hd_tuning(_write(tmp_path, payload))

    assert np.isnan(data.rates_for(7)[1])  # type: ignore[index]
    assert np.isnan(data.processed_curve(7, display_bins=180, smoothing=False).rates_hz[1])
    # Rebinning pools counts and exposure; the six-bin display group remains
    # observed because five constituent bins have exposure.
    assert np.isfinite(data.processed_curve(7, display_bins=30, smoothing=False).rates_hz[0])

    nonzero_count = _payload()
    nonzero_count["occupancy_time_s"][1] = 0.0  # type: ignore[index]
    nonzero_count["spike_counts"][0][1] = 1  # type: ignore[index]
    nonzero_count["firing_rate_hz"][0][1] = None  # type: ignore[index]
    with pytest.raises(ValueError, match="zero occupancy"):
        load_hd_tuning(_write(tmp_path, nonzero_count))

    false_zero_rate = _payload()
    false_zero_rate["occupancy_time_s"][1] = 0.0  # type: ignore[index]
    for counts, rates in zip(  # type: ignore[arg-type]
        false_zero_rate["spike_counts"], false_zero_rate["firing_rate_hz"]
    ):
        counts[1] = 0
        rates[1] = 0.0
    with pytest.raises(ValueError, match="rate null"):
        load_hd_tuning(_write(tmp_path, false_zero_rate))


@pytest.mark.parametrize("kind", ["nested", "columnar"])
def test_observation_formats_require_positive_occupancy(
    tmp_path: Path, kind: str
) -> None:
    if kind == "columnar":
        payload = _payload()
        payload["occupancy_time_s"] = [0.0] * 180
        payload["spike_counts"] = [[0] * 180, [0] * 180]
        payload["firing_rate_hz"] = [[None] * 180, [None] * 180]
    else:
        payload = {
            "schema_version": 2,
            "angle_bin_edges_deg": np.linspace(0, 360, 181).tolist(),
            "occupancy_time_s": [0.0] * 180,
            "units": [
                {
                    "unit_id": 7,
                    "spike_counts": [0] * 180,
                    "firing_rate_hz": [None] * 180,
                    "hd_class": 0,
                }
            ],
        }
    with pytest.raises(ValueError, match="positive occupancy"):
        load_hd_tuning(_write(tmp_path, payload))


def test_columnar_validates_rates_counts_classes_and_metadata(tmp_path: Path) -> None:
    mismatch = _payload()
    mismatch["firing_rate_hz"][0][0] = 999.0  # type: ignore[index]
    with pytest.raises(ValueError, match="does not match count / occupancy"):
        load_hd_tuning(_write(tmp_path, mismatch))

    fractional_count = _payload()
    fractional_count["spike_counts"][0][0] = 1.5  # type: ignore[index]
    with pytest.raises(ValueError, match="non-negative integer"):
        load_hd_tuning(_write(tmp_path, fractional_count))

    invalid_class = _payload()
    invalid_class["unit_data"]["hd_class"][0] = 3  # type: ignore[index]
    with pytest.raises(ValueError, match="hd_class"):
        load_hd_tuning(_write(tmp_path, invalid_class))

    invalid_metadata = _payload()
    invalid_metadata["metadata"] = {"timestamp_reference": 120}
    with pytest.raises(ValueError, match="timestamp_reference must be a string"):
        load_hd_tuning(_write(tmp_path, invalid_metadata))


def test_metadata_and_compatibility_api_match_the_restored_live_panel(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["metadata"] = {
        "timestamp_reference": "Exposure TTL rising edge",
        "timebase": "Open Ephys ADC seconds",
        "feature_fs_hz": 119.82,
        "classification": {"method": "Rayleigh + shuffle", "num_shuffle": 1000},
        "ttl_qc": {"ttl_pulse_count": 12345, "camera_ttl_active_high": True},
    }
    data = load_hd_tuning(_write(tmp_path, payload))

    assert data.metadata is not None
    assert data.metadata.timestamp_reference == "Exposure TTL rising edge"
    assert data.metadata.classification is not None
    assert data.metadata.classification.num_shuffle == 1000
    assert data.metadata.ttl_qc is not None
    assert data.metadata.ttl_qc.camera_ttl_active_high is True
    assert tuple(data.curves) == (7, 42)
    assert tuple(data.spike_counts) == (7, 42)
    assert data.hd_classes[7] == 1
    assert data.rates_for(404) is None
    assert data.hd_class_for(404) is None
    assert data.processed_for(404, 30, smoothing=False, sigma=1.5) is None
    processed = data.processed_for(42, 30, smoothing=False, sigma=1.5)
    assert processed is not None
    assert processed[0][0] == pytest.approx(6.0)
    assert processed[1] == pytest.approx((2.0,) * 30)


def test_python_18_direct_constructor_remains_compatible(tmp_path: Path) -> None:
    metadata = TuningCurveMetadata(
        timestamp_reference="Exposure TTL rising edge",
        classification=TuningCurveClassificationProvenance(num_shuffle=1000),
        ttl_qc=TuningCurveTTLProvenance(ttl_pulse_count=12345),
    )
    path = tmp_path / "manual.json"
    data = TuningCurveData(
        path,
        {7: (2.0,) * 180},
        spike_counts={7: (2,) * 180},
        occupancy_time_s=(1.0,) * 180,
        hd_classes={7: 2},
        metadata=metadata,
    )

    assert isinstance(data, HDTuningData)
    assert data.path == path
    assert data.rates_for(7) == (2.0,) * 180
    assert data.hd_class_for(7) == 2
    assert data.metadata is metadata
    assert data.processed_for(7, 30, smoothing=False, sigma=1.5) == (
        tuple(6.0 + 12.0 * index for index in range(30)),
        (2.0,) * 30,
    )


@pytest.mark.parametrize("kind", ["nested", "columnar"])
def test_angle_geometry_must_match_the_180_bin_hd_convention(
    tmp_path: Path, kind: str
) -> None:
    if kind == "columnar":
        payload = _payload()
        payload["angle_bin_edges_deg"] = np.linspace(-180, 180, 181).tolist()
    else:
        payload = {
            "schema_version": 2,
            "angle_bin_edges_deg": np.linspace(-180, 180, 181).tolist(),
            "occupancy_time_s": [1.0] * 180,
            "units": [
                {
                    "unit_id": 7,
                    "spike_counts": [1] * 180,
                    "firing_rate_hz": [1.0] * 180,
                }
            ],
        }
    with pytest.raises(ValueError, match="0.*360"):
        load_hd_tuning(_write(tmp_path, payload))


def test_nested_schema_version_and_integer_fields_remain_strict(tmp_path: Path) -> None:
    base = {
        "schema_version": 2,
        "angle_bin_edges_deg": np.linspace(0, 360, 181).tolist(),
        "occupancy_time_s": [1.0] * 180,
        "units": [
            {
                "unit_id": 7,
                "spike_counts": [1] * 180,
                "firing_rate_hz": [1.0] * 180,
            }
        ],
    }
    float_version = json.loads(json.dumps(base))
    float_version["schema_version"] = 2.0
    with pytest.raises(ValueError, match="Unsupported.*schema version"):
        load_hd_tuning(_write(tmp_path, float_version))

    float_id = json.loads(json.dumps(base))
    float_id["units"][0]["unit_id"] = 7.0
    with pytest.raises(ValueError, match="unit_id"):
        load_hd_tuning(_write(tmp_path, float_id))

    float_count = json.loads(json.dumps(base))
    float_count["units"][0]["spike_counts"][0] = 1.0
    with pytest.raises(ValueError, match="non-negative integer"):
        load_hd_tuning(_write(tmp_path, float_count))


def test_malformed_legacy_mapping_reports_legacy_contract(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly 180 rates"):
        load_hd_tuning(_write(tmp_path, {"7": [1.0] * 179}))

    with pytest.raises(ValueError, match="Duplicate legacy HD unit ID"):
        load_hd_tuning(_write(tmp_path, {"1": [1.0] * 180, "01": [2.0] * 180}))
