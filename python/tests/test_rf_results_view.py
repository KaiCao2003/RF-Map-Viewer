from types import SimpleNamespace
from copy import deepcopy
from dataclasses import replace
from unittest import mock

import pytest

from gui_test_support import tk_test_root
from rfmapping_viewer.tk_support import TK_AVAILABLE, ttk
from test_rf_results import source, write_result, write_summary
from test_rf_result_statistics import make_data, saved_result, recorded_result
from rfmapping_viewer.rf_result_statistics import result_statistics
from rfmapping_viewer.rf_results_view import _result_summary, group_result_summaries


def statistics_summary(result, data):
    return _result_summary(result, 42, result_statistics(result, data, 42))


@pytest.mark.parametrize("dimension", ["1d", "2d"])
def test_equivalent_statistics_are_shared_while_thresholds_stay_per_polarity(make_data, dimension):
    data = make_data()
    excitatory = saved_result(data, dimension=dimension)
    inhibitory = replace(excitatory, rf_type="inhibitory", manifest=deepcopy(excitatory.manifest))
    inhibitory.manifest["parameters"].update(alternative="less", cluster_forming_z=3, drop_bins=5)
    group, = group_result_summaries([statistics_summary(result, data) for result in (excitatory, inhibitory)])
    assert group.dimension == dimension
    assert group.window == "0–200 ms"
    assert group.shared_statistics is not None
    assert len(group.rows) == 2
    assert group.rows[0].statistics.threshold == "mean + 1.8 × SD"
    assert group.rows[1].statistics.threshold == "mean − 3 × SD"


def test_equal_numbers_from_different_baselines_are_not_shared(make_data):
    data = make_data(((10, 10, 10), (10, 10, 10)))
    global_result = saved_result(data)
    bottom_result = saved_result(data, reference="bottom_row")
    group, = group_result_summaries([statistics_summary(result, data) for result in (global_result, bottom_result)])
    assert group.rows[0].statistics.mean_hz == group.rows[1].statistics.mean_hz
    assert group.rows[0].statistics.sd_hz == group.rows[1].statistics.sd_hz
    assert group.shared_statistics is None


def test_equal_1d_statistics_with_different_spatial_sums_are_not_shared(make_data):
    data = make_data(((10, 10, 10), (10, 10, 10)))
    first = saved_result(data, dimension="1d")
    second = replace(first, manifest=deepcopy(first.manifest))
    first.manifest["spatial_aggregation"][0]["selected_indices"] = [0]
    second.manifest["spatial_aggregation"][0]["selected_indices"] = [1]
    group, = group_result_summaries([statistics_summary(result, data) for result in (first, second)])
    assert group.rows[0].statistics.mean_hz == group.rows[1].statistics.mean_hz
    assert group.shared_statistics is None


def test_saved_and_computed_statistics_remain_distinct_even_if_numbers_match(make_data):
    data = make_data()
    computed = saved_result(data, reference="bottom_row")
    recorded = recorded_result(data)
    values = result_statistics(computed, data, 42)
    recorded.manifest["baseline"]["mean_hz"][1] = values.mean_hz
    recorded.manifest["baseline"]["sd_hz"][1] = values.sd_hz
    group, = group_result_summaries([statistics_summary(result, data) for result in (computed, recorded)])
    assert group.shared_statistics is None
    assert [row.statistics.origin for row in group.rows] == ["computed", "saved"]


def test_matching_saved_baseline_statistics_can_be_shared_but_changed_values_cannot(make_data):
    data = make_data()
    first = recorded_result(data)
    second = replace(first, manifest=deepcopy(first.manifest), rf_type="inhibitory")
    second.manifest["parameters"].update(alternative="less", cluster_forming_z=3)
    second.manifest["baseline"]["threshold_hz"] = [1., 2.]
    summaries = [statistics_summary(result, data) for result in (first, second)]
    assert group_result_summaries(summaries)[0].shared_statistics is not None
    second.manifest["baseline"]["sd_hz"][1] += .1
    assert group_result_summaries([statistics_summary(result, data) for result in (first, second)])[0].shared_statistics is None


def test_saved_numbers_without_baseline_provenance_remain_separate(make_data):
    data = make_data()
    first = saved_result(data)
    first.manifest["baseline"] = {"unit_ids": [42], "mean_hz": [25.], "sd_hz": [1.]}
    second = replace(first, manifest=deepcopy(first.manifest), rf_type="inhibitory")
    group, = group_result_summaries([statistics_summary(result, data) for result in (first, second)])
    assert all(row.statistics.mean_hz == 25. for row in group.rows)
    assert group.shared_statistics is None


def test_different_time_windows_form_separate_groups_and_unavailable_rows_are_kept(make_data):
    data = make_data(((0, 0, 0), (0, 0, 0)))
    first = saved_result(data)
    second = replace(first, manifest=deepcopy(first.manifest), rf_type="inhibitory")
    second.manifest["time_range_s"] = [0, .05]
    summaries = [statistics_summary(result, data) for result in (first, second)]
    assert len(group_result_summaries(summaries)) == 2
    second.manifest["time_range_s"] = [0, .2]
    second.manifest["detector_algorithm"] = "unknown"
    group, = group_result_summaries([statistics_summary(result, data) for result in (first, second)])
    assert len(group.rows) == 2
    assert group.rows[1].statistics.error
    assert group.shared_statistics is None


@pytest.mark.skipif(not TK_AVAILABLE, reason="Tk is unavailable")
def test_saved_result_selectors_share_cache_and_reload_without_a_second_canvas(source):
    from rfmapping_viewer.rf_results_view import RFResultsPane

    root = tk_test_root()
    host = ttk.Frame(root)
    controls_host = ttk.Frame(host)
    changed = mock.Mock()
    data = SimpleNamespace(
        path=source.path, unit_pool=source.unit_ids, x_positions=source.x_positions,
        y_positions=source.y_positions, time_bin_edges=source.time_bin_edges,
    )
    pane = RFResultsPane(host, data, controls_parent=controls_host, on_change=changed)
    try:
        assert pane.controls.master is controls_host
        assert not hasattr(pane, "canvas")
        pane.set_unit(42)
        assert pane.status.get() == "excitatory 2D unavailable"
        write_result(source)
        pane.refresh()
        changed.assert_called_once_with()
        assert pane.cache.get(42, mode="2D").available
        assert pane.status.get() == "0–200 ms"
        assert "Unit 42" in pane.details.get()
        pane.dimension.set("Both")
        pane._selection_changed()
        assert pane.status.get() == "0–200 ms · excitatory 1D unavailable"
        assert changed.call_count == 2
        pane.set_document(None)
        pane.set_unit(None)
        assert pane.cache is None
        assert pane.status.get() == "No unit selected"
        assert pane.details.get() == ""
        assert pane.summaries == []
    finally:
        host.destroy()
    root.update_idletasks()


@pytest.mark.skipif(not TK_AVAILABLE, reason="Tk is unavailable")
def test_result_summaries_distinguish_negative_missing_and_qc_filtered_results(source):
    from rfmapping_viewer.rf_results_view import RFResultsPane

    root = tk_test_root()
    data = SimpleNamespace(
        path=source.path, unit_pool=source.unit_ids, x_positions=source.x_positions,
        y_positions=source.y_positions, time_bin_edges=source.time_bin_edges,
    )
    pane = RFResultsPane(root, data)
    try:
        pane.set_unit(42)
        summary, = pane.summaries
        assert summary.status_kind == "unavailable"
        assert summary.status == "Result unavailable"
        assert str(source.path.parent) not in summary.status
        assert summary.warning
        path, manifest = write_result(source)
        pane.refresh()
        summary, = pane.summaries
        assert summary.status_kind == "detected"
        assert summary.detected_bins == 1
        assert summary.window == "0–200 ms"
        assert summary.centers == "(90, 15)"
        assert summary.statistics is None
        pane.update_details(include_statistics=True)
        summary, = pane.summaries
        assert summary.statistics.threshold == "mean + 1.8 × SD"
        assert summary.statistics.mean_hz is None
        assert "reference is unsupported" in summary.statistics.error
        assert summary.status_kind == "detected"
        pane.set_unit(17)
        summary, = pane.summaries
        assert summary.status_kind == "empty"
        assert summary.status == "No RF detected"
        assert summary.detected_bins == 0
        pane.set_unit(99)
        summary, = pane.summaries
        assert summary.status_kind == "missing_unit"
        assert summary.detected_bins is None
        write_summary(source, path, manifest)
        pane.refresh()
        summary, = pane.summaries
        assert summary.status_kind == "excluded"
        assert summary.status == "Excluded by QC"
        pane.update_details(layers=[])
        assert pane.summaries == []
    finally:
        pane.destroy()
    root.update_idletasks()
