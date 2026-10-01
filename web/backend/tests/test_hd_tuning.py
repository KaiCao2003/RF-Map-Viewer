from __future__ import annotations

import json
from pathlib import Path

import pytest

from rfmapping_web.companions import (
    load_tuning_curve,
    tuning_cluster_payload,
    tuning_dataset_payload,
)


def columnar_payload() -> dict:
    return {
        "metadata": {
            "probe": "ProbeA",
            "classification": {
                "class_3": "rayleigh and shuffle significant; kappa >= cutoff",
                "kappa_cutoff": 0.075,
            },
        },
        "angle_bin_edges_deg": [index * 2.0 for index in range(181)],
        "occupancy_time_s": [0.0, *([1.0] * 179)],
        "unit_id": [42, 7],
        "spike_counts": [[0, *([2] * 179)], [0, *([3] * 179)]],
        "firing_rate_hz": [[None, *([2.0] * 179)], [None, *([3.0] * 179)]],
        "unit_data": {"hd_class": [3, 2], "von_mises_kappa": [0.1, 0.05]},
    }


def nested_payload() -> dict:
    columnar = columnar_payload()
    return {
        "schema_version": 2,
        "metadata": columnar["metadata"],
        "angle_bin_edges_deg": columnar["angle_bin_edges_deg"],
        "occupancy_time_s": columnar["occupancy_time_s"],
        "units": [
            {
                "unit_id": unit_id,
                "spike_counts": columnar["spike_counts"][index],
                "firing_rate_hz": columnar["firing_rate_hz"][index],
                "hd_class": columnar["unit_data"]["hd_class"][index],
            }
            for index, unit_id in enumerate(columnar["unit_id"])
        ],
    }


def write_payload(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "tuning_curves.tc"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("make_payload", [columnar_payload, nested_payload])
def test_observation_formats_keep_saved_classes_and_missing_bins(
    tmp_path: Path, make_payload,
) -> None:
    data = load_tuning_curve(write_payload(tmp_path, make_payload()))
    response = tuning_dataset_payload(data)

    assert [unit["unitId"] for unit in response["units"]] == [42, 7]
    assert [unit["hdClass"] for unit in response["units"]] == [3, 2]
    assert response["units"][0]["rates"][0] is None
    assert response["units"][0]["spikeCounts"][0] == 0
    assert response["occupancyTimeS"][0] == 0.0
    assert response["metadata"]["classification"]["kappa_cutoff"] == 0.075
    assert "kappa >= cutoff" in response["metadata"]["classification"]["class_3"]
    assert tuning_cluster_payload(data, 42)["hdClass"] == 3


def test_legacy_rates_keep_observations_absent_in_both_api_payloads(
    tmp_path: Path,
) -> None:
    raw = {"42": [2.0] * 180, "7": [3.0] * 180}
    data = load_tuning_curve(write_payload(tmp_path, raw))
    response = tuning_dataset_payload(data)
    unit = tuning_cluster_payload(data, 42)

    assert [item["unitId"] for item in response["units"]] == [42, 7]
    assert response["metadata"] is None
    assert response["occupancyTimeS"] is None
    assert response["units"][0]["spikeCounts"] is None
    assert unit["rates"] == raw["42"]
    assert unit["hdClass"] is None
    assert unit["spikeCounts"] is None
    assert unit["occupancyTimeS"] is None
    assert tuning_cluster_payload(data, 99)["available"] is False


def test_columnar_optional_columns_and_forward_compatible_fields(
    tmp_path: Path,
) -> None:
    payload = columnar_payload()
    payload["unit_data"] = {"new_metric": [0.2, 0.3]}
    payload["metadata"] = None
    payload["source_note"] = "preserved by scientific producer"
    data = load_tuning_curve(write_payload(tmp_path, payload))

    assert data.metadata is None
    assert [unit.hd_class for unit in data.units] == [None, None]


@pytest.mark.parametrize("make_payload", [columnar_payload, nested_payload])
@pytest.mark.parametrize("field,bad_value", [("class_3", 3), ("kappa_cutoff", "0.075")])
def test_classification_provenance_requires_supported_types(
    tmp_path: Path, make_payload, field: str, bad_value,
) -> None:
    payload = make_payload()
    payload["metadata"]["classification"][field] = bad_value

    with pytest.raises(ValueError, match=field):
        load_tuning_curve(write_payload(tmp_path, payload))


@pytest.mark.parametrize("bad_class", [4, True, 3.0, "3"])
def test_nested_schema_rejects_invalid_saved_class(
    tmp_path: Path, bad_class,
) -> None:
    payload = nested_payload()
    payload["units"][0]["hd_class"] = bad_class

    with pytest.raises(ValueError, match="hd_class"):
        load_tuning_curve(write_payload(tmp_path, payload))


@pytest.mark.parametrize("case", ["duplicate", "zero-occupancy", "rate-mismatch"])
def test_nested_schema_validates_unit_pairing_and_observations(
    tmp_path: Path, case: str,
) -> None:
    payload = nested_payload()
    if case == "duplicate":
        payload["units"][1]["unit_id"] = 42
    elif case == "zero-occupancy":
        payload["units"][0]["firing_rate_hz"][0] = 0.0
    else:
        payload["units"][0]["firing_rate_hz"][1] = 4.0

    with pytest.raises(ValueError):
        load_tuning_curve(write_payload(tmp_path, payload))


@pytest.mark.parametrize("raw", [
    {"7": [2.0] * 180, "07": [2.0] * 180},
    {"7": [-1.0, *([2.0] * 179)]},
    {"7": [2.0] * 179},
])
def test_legacy_schema_rejects_invalid_ids_rates_or_shape(tmp_path: Path, raw) -> None:
    with pytest.raises(ValueError):
        load_tuning_curve(write_payload(tmp_path, raw))
