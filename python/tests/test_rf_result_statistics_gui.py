"""Inspector statistics follow saved analysis scope and the current RF source."""

from dataclasses import replace
import json
from unittest import mock

import numpy as np
import pytest

import rfmapping_viewer.rf_results_view as results_view
from rfmapping_viewer.rf_results import RFResultSource
from rfmapping_viewer.tk_support import TK_AVAILABLE
from test_document_reload_tk import wait_for
from test_rf_result_overlay_gui import app, draw
from test_rf_results import write_result


pytestmark = pytest.mark.skipif(not TK_AVAILABLE, reason="Tk is unavailable")


@pytest.fixture
def stats_app(app):
    payload = json.loads(app.data.path.read_text())
    counts = np.zeros((2, 2, 3, 3), dtype=int)
    counts[..., 0] = 1000
    for index, factor in enumerate((1, 2)):
        counts[index, :, :, 1] = np.array([1, 3, 5]) * factor
        counts[index, :, :, 2] = np.array([1, 3, 5]) * factor * 3
    payload["unitsSpikeCounts"] = counts.tolist()
    app.data.path.write_text(json.dumps(payload))
    app._load_json_path(app.data.path)
    source = RFResultSource.from_document(app.data)
    parameters = dict(
        alternative="greater", cluster_forming_z=1.8, drop_bins=2, wrap_x=True,
        is_shuffle=False, exclude_zero_bins=False, reference="global",
    )
    manifest = dict(
        parameters=parameters, response_units="Hz",
        response_normalization="presentation_count_time",
        x_positions=list(source.x_positions), y_positions=list(source.y_positions),
    )
    write_result(source, ids=(7, 8), manifest_changes=manifest)
    aggregation = dict(
        operation="sum", retained_axis="x", selected_indices=[0, 1],
        missing_as_zero=True, rf_only=False,
        x_positions=list(source.x_positions), y_positions=list(source.y_positions),
    )
    write_result(source, "1d", ids=(7, 8), manifest_changes={
        **manifest, "parameters": {**parameters, "cluster_forming_z": 1.0},
        "y_positions": [0.0], "spatial_aggregation": [aggregation, aggregation],
    })
    app.results_pane.cache.clear()
    app.results_pane.dimension.set("Both")
    app._set_selected_unit_id(7)
    app._update_all()
    app.update()
    return app


def inspector_text(app):
    return app.inspector_window.result_text.get("1.0", "end-1c")


def show_saved_results(app):
    app._show_inspector()
    app.inspector_window.notebook.select(1)
    app.update()
    return inspector_text(app)


def test_inspector_reads_saved_threshold_and_separate_2d_1d_statistics(stats_app):
    app = stats_app
    paths = [app.data.path, app.data.path.with_suffix(".npz"),
             app.data.path.with_name("mapping_1d.npz")]
    before = {path: path.read_bytes() for path in paths}
    draw(app, "results")
    text = show_saved_results(app)

    assert "Saved threshold: mean + 1.8 × SD" in text
    assert "Saved threshold: mean + 1 × SD" in text
    assert "Computed mean: 6 Hz; SD: 3.26599 Hz" in text
    assert "Computed mean: 12 Hz; SD: 6.53197 Hz" in text
    assert "global native RF grid; 6 bins" in text
    assert "global 1D sum onto X; 3 bins" in text
    assert "Original detector mean/SD and temporal aggregation were not saved." in text
    assert app.inspector_window.result_text.cget("state") == "disabled"
    assert {path: path.read_bytes() for path in paths} == before


def test_hidden_inspector_does_not_calculate_and_reopens_with_overlays_disabled(stats_app):
    app = stats_app
    with mock.patch.object(results_view, "result_statistics_text", wraps=results_view.result_statistics_text) as compute:
        draw(app, "rf")
        draw(app, "results")
        compute.assert_not_called()
        text = show_saved_results(app)
        assert compute.called
        assert "Computed mean: 6 Hz" in text
        app.inspector_window.withdraw()
        compute.reset_mock()
        app.settings = replace(app.settings, rf_result_overlay_enabled=False)
        draw(app, "rf")
        compute.assert_not_called()
        text = show_saved_results(app)
        assert "Computed mean: 6 Hz" in text
        assert "Saved threshold: mean + 1.8 × SD" in text


def test_inspector_uses_saved_window_and_refreshes_selected_unit(stats_app):
    app = stats_app
    draw(app, "rf")
    original = show_saved_results(app)
    app.range_start_var.set("0")
    app.range_end_var.set("100")
    app.x_bins_var.set(1)
    app.smooth_radius_var.set(2)
    app.flip_y_var.set(True)
    app.palette_var.set("Viridis")
    app._on_control_changed()
    app.update()
    assert inspector_text(app) == original

    app._set_selected_unit_id(8)
    app._update_all()
    app.update()
    text = inspector_text(app)
    assert "Unit 8:" in text
    assert "Computed mean: 12 Hz; SD: 6.53197 Hz" in text
    assert "Computed mean: 24 Hz; SD: 13.0639 Hz" in text
    assert "Computed mean: 6 Hz;" not in text


def test_unknown_statistics_provenance_keeps_saved_masks_and_threshold(stats_app):
    app = stats_app
    source = app.results_pane.source
    write_result(source, ids=(7, 8), manifest_changes={"detector_algorithm": "future-detector"})
    app.results_pane.cache.clear()
    draw(app, "results")
    text = show_saved_results(app)
    assert "Saved threshold: mean + 1.8 × SD" in text
    assert "Computed mean / SD unavailable:" in text
    result = app.results_pane.cache.result("2d", "excitatory")
    assert result is not None
    assert result.for_unit(7)[0].sum() == 1
    assert app.canvases["rf"].find_withtag("rf-result-overlay")


def test_document_reload_refreshes_inspector_source_and_computed_values(stats_app):
    app = stats_app
    draw(app, "results")
    assert "Computed mean: 6 Hz;" in show_saved_results(app)
    previous = app.data
    payload = json.loads(app.data.path.read_text())
    payload["unitsSpikeCounts"] = (np.asarray(payload["unitsSpikeCounts"]) * 2).tolist()
    app.data.path.write_text(json.dumps(payload))
    app._reload_current_document()
    wait_for(app, lambda: not app._reload_active)
    assert app.data is not previous
    assert app.results_pane.data is app.data
    text = inspector_text(app)
    assert "Computed mean: 12 Hz; SD: 6.53197 Hz" in text
    assert "Computed mean: 24 Hz; SD: 13.0639 Hz" in text
    assert "Computed mean: 6 Hz;" not in text
