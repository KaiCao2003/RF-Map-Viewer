"""Descriptive saved-window statistics follow recorded baseline provenance."""

from copy import deepcopy
from dataclasses import replace
import json

import numpy as np
import pytest

from gui_test_support import current_rf_payload
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.rf_results import RFResultSource, SavedRFResult
from rfmapping_viewer.rf_result_statistics import result_statistics, result_statistics_text


@pytest.fixture
def make_data(tmp_path):
    opened = []

    def create(rates=((0, 10, 20), (30, 40, 50)), *, missing=()):
        rates = np.asarray(rates)
        presentations = np.arange(1, rates.size + 1).reshape(rates.shape)
        # The two selected lag bins have unequal widths and equal counts.
        # Their unweighted mean rate differs from the saved-window rate.
        totals = np.rint(rates * presentations * .2).astype(int)
        counts = np.stack((np.full(rates.shape, 100), totals // 2, totals - totals // 2), axis=-1)
        occupancy = np.ones(rates.shape)
        for y, x in missing:
            occupancy[y, x] = 0
            presentations[y, x] = 0
            counts[y, x] = 0
        payload = current_rf_payload({
            "unitPool": [42], "xPositions": list(range(rates.shape[1])),
            "yPositions": [100 + 100 * y for y in range(rates.shape[0])],
            "unitsSpikeCountsSize": [1, *counts.shape],
            "unitsSpikeCounts": counts[None].tolist(),
            "timeBinEdges": [-.1, 0, .05, .2],
        }, occupancy.tolist(), presentations.tolist())
        path = tmp_path / f"source_{len(opened)}.json"
        path.write_text(json.dumps(payload))
        data = RFMappingData(path)
        opened.append(data)
        return data

    yield create
    for data in opened:
        data.close()


def saved_result(data, *, reference="global", dimension="2d", axis="x", projection=False):
    source = RFResultSource.from_document(data)
    native_shape = (data.n_y, data.n_x)
    shape = native_shape if dimension == "2d" else ((1, data.n_x) if axis == "x" else (data.n_y, 1))
    manifest = {
        "detector_algorithm": "bottom-two-rows-mean-sd-v1" if reference == "bottom_row" else "pooled-spatial-z-v3-zero-filled",
        "time_range_s": [0, .2], "response_units": "Hz",
        "response_normalization": "presentation_count_time",
        "x_positions": list(source.x_positions), "y_positions": list(source.y_positions),
        "parameters": {"reference": reference, "is_shuffle": False, "exclude_zero_bins": False,
                       "cluster_forming_z": 1.8, "alternative": "greater", "surround_decay": 0},
    }
    if reference == "bottom_row":
        manifest["baseline"] = {
            "row_indices": [data.n_y - 2, data.n_y - 1], "y_positions": list(source.y_positions[-2:]),
            "bin_count": 2 * data.n_x, "mean_scope": "bottom_two_rows",
            "sd_scope": "bottom_two_rows", "sd_ddof": 0,
        }
    if dimension == "1d":
        manifest["collapse_axis"] = axis
        manifest["y_positions" if axis == "x" else "x_positions"] = (
            list(source.y_positions[:1] if axis == "x" else source.x_positions[:1]) if projection else [0.]
        )
        if projection:
            manifest["projection_source_cache_key"] = "original-2d-key"
        else:
            manifest["spatial_aggregation"] = [{
                "operation": "sum", "retained_axis": axis, "rf_only": False, "missing_as_zero": True,
                "selected_indices": list(range(data.n_y if axis == "x" else data.n_x)),
                "x_positions": list(source.x_positions), "y_positions": list(source.y_positions),
            }]
    return SavedRFResult(
        path=data.path.with_suffix(".npz"), source=source, dimension=dimension,
        rf_type="excitatory", unit_ids=(42,), mask=np.zeros((1, *shape), dtype=np.uint8),
        center=np.zeros((1, *shape), dtype=np.uint8), manifest=manifest, cache_key="key",
        summary=None, summary_warning=None, axis=axis if dimension == "1d" else None,
        projected_from_2d=False,
    )


def test_global_uses_saved_halfopen_window_native_hz_and_population_sd(make_data):
    data = make_data()
    result = saved_result(data)
    original_counts = data.rf_map_by_unit_id(42).spike_counts.copy()
    original_manifest = deepcopy(result.manifest)
    text = result_statistics_text(result, data, 42)
    # [0, 10, 20, 30, 40, 50] Hz: mean=25, variance=1750/6.
    assert "Saved threshold: mean + 1.8 × SD" in text
    assert "Computed mean: 25 Hz; SD: 17.0783 Hz (population, ddof=0)" in text
    assert "global native RF grid; 6 bins" in text
    assert "Current-source mean-rate statistics:" in text
    assert "Time aggregation: mean firing rate over the saved window." in text
    assert "Original detector mean/SD and temporal aggregation were not saved." in text
    np.testing.assert_array_equal(data.rf_map_by_unit_id(42).spike_counts, original_counts)
    assert result.manifest == original_manifest


def test_structured_statistics_share_the_diagnostic_values_without_recalculation(make_data, monkeypatch):
    data = make_data()
    result = saved_result(data)
    statistics = result_statistics(result, data, 42)
    assert statistics.threshold == "mean + 1.8 × SD"
    assert statistics.candidate_threshold is False
    assert statistics.mean_hz == pytest.approx(25)
    assert statistics.sd_hz == pytest.approx(np.std([0, 10, 20, 30, 40, 50]))
    assert statistics.threshold_hz == pytest.approx(25 + 1.8 * np.std([0, 10, 20, 30, 40, 50]))
    assert statistics.threshold_text == "mean + 1.8 × SD = 55.7409 Hz"
    assert statistics.scope == "global native RF grid"
    assert statistics.bin_count == 6
    assert statistics.error is None
    monkeypatch.setattr(data, "rf_map_by_unit_id", lambda _unit_id: pytest.fail("statistics were calculated twice"))
    assert "Computed mean: 25 Hz; SD: 17.0783 Hz" in result_statistics_text(
        result, data, 42, statistics=statistics,
    )


def test_structured_unavailable_statistics_keep_saved_parameters(make_data):
    data = make_data()
    result = saved_result(data)
    result.manifest["parameters"].update(is_shuffle=True, alternative="less", surround_decay=.5)
    statistics = result_statistics(result, data, 42)
    assert statistics.threshold == "mean − 1.8 × SD"
    assert statistics.candidate_threshold is True
    assert statistics.surround_decay == .5
    assert statistics.mean_hz is None and statistics.sd_hz is None
    assert statistics.threshold_hz is None
    assert statistics.threshold_text == "mean − 1.8 × SD"
    assert "shuffle" in statistics.error


def test_bottom_baseline_uses_last_two_stored_rows_not_lowest_y(make_data):
    data = make_data(((100, 100), (0, 10), (20, 30)))
    text = result_statistics_text(saved_result(data, reference="bottom_row"), data, 42)
    assert "Computed mean: 15 Hz; SD: 11.1803 Hz" in text
    assert "last two stored rows (all columns); 4 bins" in text


def test_missing_bins_are_included_as_zero(make_data):
    data = make_data(((10, 20), (30, 40)), missing=((1, 1),))
    text = result_statistics_text(saved_result(data), data, 42)
    assert "Computed mean: 15 Hz; SD: 11.1803 Hz" in text
    assert "4 bins; missing bins counted as zero" in text


def test_constant_baseline_reports_zero_population_sd(make_data):
    data = make_data(((10, 10), (10, 10)))
    text = result_statistics_text(saved_result(data, reference="bottom_row"), data, 42)
    assert "Computed mean: 10 Hz" in text
    # Decimal exposure can leave roundoff across otherwise equal native rates.
    sd = float(text.split("SD: ", 1)[1].split(" Hz", 1)[0])
    assert sd == pytest.approx(0, abs=1e-12)


def test_2d_singleton_does_not_ignore_computed_spatial_provenance(make_data):
    data = make_data(((10, 20, 30),))
    result = saved_result(data)
    result.manifest["spatial_aggregation"] = [{
        "operation": "sum", "retained_axis": "x", "rf_only": True,
        "selected_indices": [], "missing_as_zero": True,
    }]
    text = result_statistics_text(result, data, 42)
    assert "Computed mean / SD unavailable: 2D result has a computed spatial aggregation" in text


@pytest.mark.parametrize("axis,mean,sd", [("x", "50", "16.3299"), ("y", "75", "45")])
def test_independent_1d_sums_native_rates_before_statistics(make_data, axis, mean, sd):
    data = make_data()
    text = result_statistics_text(saved_result(data, dimension="1d", axis=axis), data, 42)
    assert f"Computed mean: {mean} Hz; SD: {sd} Hz" in text
    assert f"global 1D sum onto {axis.upper()}" in text


@pytest.mark.parametrize("axis", ["x", "y"])
def test_projected_1d_keeps_original_2d_baseline(make_data, axis):
    data = make_data(((100, 100), (0, 10), (20, 30)))
    result = saved_result(data, reference="bottom_row", dimension="1d", axis=axis, projection=True)
    text = result_statistics_text(result, data, 42)
    assert "Computed mean: 15 Hz; SD: 11.1803 Hz" in text
    assert "projected from 2D; 4 bins" in text


def test_legacy_projection_uses_original_grid_when_algorithm_is_known(make_data):
    data = make_data()
    result = replace(saved_result(data), dimension="1d", axis="x", projected_from_2d=True)
    text = result_statistics_text(result, data, 42)
    assert "Computed mean: 25 Hz; SD: 17.0783 Hz" in text
    assert "projected from 2D" in text


def test_native_singleton_does_not_require_a_computed_spatial_sum(make_data):
    data = make_data(((0, 10, 20),))
    result = saved_result(data, dimension="1d")
    result.manifest["y_positions"] = list(result.source.y_positions)
    result.manifest["spatial_aggregation"] = ["vertical_bar_pooled"]
    text = result_statistics_text(result, data, 42)
    assert "Computed mean: 10 Hz; SD: 8.16497 Hz" in text


def test_native_singleton_does_not_bypass_saved_rf_only_aggregation(make_data):
    data = make_data(((0, 10, 20),))
    result = saved_result(data, dimension="1d")
    result.manifest["spatial_aggregation"][0].update(rf_only=True, selected_indices=[])
    text = result_statistics_text(result, data, 42)
    assert "Computed mean / SD unavailable: independent 1D spatial-sum provenance is unsupported" in text


def test_saved_inhibitory_multiplier_and_surround_are_not_recomputed(make_data):
    data = make_data()
    result = saved_result(data)
    result.manifest["parameters"].update(alternative="less", cluster_forming_z=3, surround_decay=.5)
    text = result_statistics_text(result, data, 42)
    assert "Saved threshold: mean − 3 × SD" in text
    assert "Saved surround decay: 0.5 × SD" in text
    assert "threshold:" not in text.split("Computed mean:")[1]


@pytest.mark.parametrize("field,value,reason", [
    ("detector_algorithm", "old-unknown-algorithm", "algorithm or reference is unsupported"),
    ("response_units", "spike_count", "units or normalization"),
    ("response_normalization", "occupancyTimeSec", "units or normalization"),
    ("x_positions", [3, 2, 1], "X coordinates"),
])
def test_unsupported_provenance_preserves_saved_threshold(make_data, field, value, reason):
    data = make_data()
    result = saved_result(data)
    result.manifest[field] = value
    text = result_statistics_text(result, data, 42)
    assert "Saved threshold: mean + 1.8 × SD" in text
    assert "Computed mean / SD unavailable:" in text and reason in text


@pytest.mark.parametrize("field,value,reason", [
    ("is_shuffle", True, "shuffle"), ("is_shuffle", None, "unspecified detection"),
    ("exclude_zero_bins", True, "zero-bin exclusion"), ("reference", None, "reference is unsupported"),
])
def test_unsupported_parameter_semantics_are_unavailable(make_data, field, value, reason):
    data = make_data()
    result = saved_result(data)
    result.manifest["parameters"][field] = value
    text = result_statistics_text(result, data, 42)
    assert "Computed mean / SD unavailable:" in text and reason in text
    if field == "is_shuffle" and value is True:
        assert "Saved candidate threshold:" in text


@pytest.mark.parametrize("key,value", [
    ("rf_only", True), ("missing_as_zero", False), ("selected_indices", [99]),
    ("selected_indices", [0, 0]), ("x_positions", [99]),
])
def test_ambiguous_1d_aggregation_is_unavailable(make_data, key, value):
    data = make_data()
    result = saved_result(data, dimension="1d")
    result.manifest["spatial_aggregation"][0][key] = value
    assert "Computed mean / SD unavailable:" in result_statistics_text(result, data, 42)


def test_absent_unit_source_and_presentations_are_unavailable(make_data):
    data = make_data()
    result = saved_result(data)
    assert "unit is absent" in result_statistics_text(result, data, 99)
    assert "RF source is unavailable" in result_statistics_text(result, None, 42)
    unit = data.rf_map_by_unit_id(42)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(data, "rf_map_by_unit_id", lambda _unit_id: replace(unit, presentation_counts=None))
        assert "no saved stimulus presentation counts" in result_statistics_text(result, data, 42)


def test_invalid_saved_window_and_baseline_are_unavailable(make_data):
    data = make_data()
    result = saved_result(data, reference="bottom_row")
    result.manifest["baseline"]["row_indices"] = [1, 2]
    assert "baseline provenance" in result_statistics_text(result, data, 42)
    result = saved_result(data)
    result.manifest["time_range_s"] = [0, .11]
    assert "Computed mean / SD unavailable:" in result_statistics_text(result, data, 42)


@pytest.mark.parametrize("multiplier", [None, True, 0, -1, float("nan"), float("inf")])
def test_invalid_saved_multiplier_never_uses_a_default(make_data, multiplier):
    data = make_data()
    result = saved_result(data)
    result.manifest["parameters"]["cluster_forming_z"] = multiplier
    text = result_statistics_text(result, data, 42)
    assert "Saved threshold: unavailable" in text
    assert "Computed mean: 25 Hz" in text


def recorded_result(data):
    result = saved_result(data, reference="bottom_row")
    result.manifest["detector_algorithm"] = "gui-smoothing1-bottom-two-rows-mean-sd-v1"
    result.manifest["baseline"].update(
        unit_ids=[99, 42], mean_hz=[100., 25.076493055555538],
        sd_hz=[9., .4643229358161412], threshold_hz=[999., 999.],
        response_stage="after_gui_smoothing1",
    )
    return result


def test_saved_baseline_statistics_use_recorded_unit_ids_without_reading_rf(make_data, monkeypatch):
    data = make_data()
    result = recorded_result(data)
    original = deepcopy(result.manifest)
    monkeypatch.setattr(data, "rf_map_by_unit_id", lambda _unit_id: pytest.fail("Saved statistics must not read RF"))
    statistics = result_statistics(result, data, 42)
    assert statistics.origin == "saved"
    assert statistics.processing_label == "Smoothing 1"
    assert statistics.mean_hz == pytest.approx(25.076493055555538)
    assert statistics.sd_hz == pytest.approx(.4643229358161412)
    assert statistics.scope == "last two stored rows (all columns)"
    assert statistics.basis_key
    text = result_statistics_text(result, None, 42, statistics=statistics)
    assert "Saved mean: 25.0765 Hz; SD: 0.464323 Hz" in text
    assert "not recomputed" in text
    assert "Computed mean" not in text
    assert "999" not in text
    assert result.manifest == original


@pytest.mark.parametrize("key,value", [
    ("unit_ids", [42, 42]), ("unit_ids", [True, 42]), ("unit_ids", [99, 100]),
    ("mean_hz", [1.]), ("mean_hz", [1., float("nan")]), ("mean_hz", [1., True]),
    ("sd_hz", [1., -1.]), ("sd_hz", [1., float("inf")]), ("sd_hz", None),
])
def test_invalid_saved_statistics_are_unavailable_without_computed_substitution(make_data, monkeypatch, key, value):
    data = make_data()
    result = recorded_result(data)
    result.manifest["baseline"][key] = value
    monkeypatch.setattr(data, "rf_map_by_unit_id", lambda _unit_id: pytest.fail("Invalid saved values must not be replaced"))
    statistics = result_statistics(result, data, 42)
    assert statistics.origin == "saved"
    assert statistics.mean_hz is None and statistics.sd_hz is None
    assert statistics.error
    assert "Saved mean / SD unavailable:" in result_statistics_text(result, data, 42, statistics=statistics)


def test_saved_statistics_require_hz_and_allow_zero_sd(make_data):
    data = make_data()
    result = recorded_result(data)
    result.manifest["baseline"]["sd_hz"][1] = 0
    assert result_statistics(result, None, 42).sd_hz == 0
    result.manifest["response_units"] = "spike_count"
    assert "Hz units" in result_statistics(result, data, 42).error


@pytest.mark.parametrize("direction,multiplier,mean,sd,expected", [
    ("greater", 4.5, 7., 2., 16.),
    ("less", 3., 7., 2., 1.),
    ("less", 3., 1., 2., -5.),
    ("greater", 4.5, 7., 0., 7.),
])
def test_saved_smoothed_threshold_uses_aligned_mean_sd_and_signed_multiplier(
    make_data, monkeypatch, direction, multiplier, mean, sd, expected,
):
    data = make_data()
    result = recorded_result(data)
    result.manifest["parameters"].update(alternative=direction, cluster_forming_z=multiplier)
    result.manifest["baseline"]["mean_hz"][1] = mean
    result.manifest["baseline"]["sd_hz"][1] = sd
    monkeypatch.setattr(data, "rf_map_by_unit_id", lambda _unit_id: pytest.fail("Must use the saved smoothed baseline"))
    statistics = result_statistics(result, data, 42)
    assert statistics.threshold_hz == expected
    assert statistics.threshold_text.endswith(f"= {expected:g} Hz")
    assert statistics.threshold_text in result_statistics_text(result, None, 42, statistics=statistics)
