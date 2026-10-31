"""Inspector statistics follow saved analysis scope and the current RF source."""

from dataclasses import replace
import json
import sys
from unittest import mock

import numpy as np
import pytest

import rfmapping_viewer.rf_results_view as results_view
from rfmapping_viewer.rf_results import RFResultSource
from rfmapping_viewer.tk_support import TK_AVAILABLE, ttk
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


def card_labels(card):
    labels = []
    for child in card.winfo_children():
        if isinstance(child, ttk.Label):
            labels.append(child)
        labels.extend(card_labels(child))
    return labels


def card_text(card):
    return "\n".join(str(label.cget("text")) for label in card_labels(card))


def write_inhibitory_result(app, dimension, *, manifest_changes=None, mask=None):
    result = app.results_pane.cache.result(dimension, "excitatory")
    manifest = {**result.manifest, "parameters": {
        **result.manifest["parameters"], "alternative": "less",
        "cluster_forming_z": 3.0 if dimension == "2d" else 0.75,
    }}
    manifest.update(manifest_changes or {})
    write_result(app.results_pane.source, dimension, "inhibitory", ids=(7, 8),
                 manifest_changes=manifest, mask=mask)


def show_all_results(app):
    app.results_pane.cache.clear()
    app.settings = replace(app.settings, rf_result_overlay_enabled=True, rf_result_overlay_layers=(
        "excitatory_2d", "excitatory_1d", "inhibitory_2d", "inhibitory_1d",
    ))
    draw(app, "rf")
    app._show_inspector()
    app.update()
    return app.inspector_window


def assert_cards_fit(inspector):
    visible_top = inspector._results_canvas.winfo_rooty()
    visible_bottom = visible_top + inspector._results_canvas.winfo_height()
    visible_left = inspector.winfo_rootx()
    visible_right = visible_left + inspector.winfo_width()
    for card in inspector.summary_cards:
        for label in card_labels(card):
            assert label.winfo_ismapped(), label.cget("text")
            assert visible_top <= label.winfo_rooty()
            assert label.winfo_rooty() + label.winfo_height() <= visible_bottom, label.cget("text")
            assert visible_left <= label.winfo_rootx()
            assert label.winfo_rootx() + label.winfo_width() <= visible_right, label.cget("text")


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
    with mock.patch.object(results_view, "result_statistics", wraps=results_view.result_statistics) as compute:
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


def test_inspector_defaults_to_readable_results_and_hides_diagnostics(stats_app):
    app = stats_app
    draw(app, "results")
    app._show_inspector()
    app.update()
    inspector = app.inspector_window

    assert inspector.notebook.index("current") == 1
    assert "7" in inspector.results_header.cget("text")
    assert len(inspector.summary_cards) == 2
    first, second = map(card_text, inspector.summary_cards)
    assert "mean + 1.8 × SD" in first
    assert "mean + 1 × SD" in second
    assert "Mean" in first and "SD" in first
    assert "6 Hz" in first and "3.266 Hz" in first
    assert "12 Hz" in second and "6.532 Hz" in second
    assert "0–200 ms" in first
    assert "detected" in first.lower()
    assert "Parameters:" not in first + second
    assert str(app.data.path.parent) not in first + second
    assert not inspector.result_text.winfo_ismapped()
    assert not inspector.details_frame.winfo_ismapped()

    inspector.details_button.invoke()
    app.update()
    assert inspector.result_text.winfo_ismapped()
    assert "Parameters:" in inspector_text(app)
    assert "Original detector mean/SD and temporal aggregation were not saved." in inspector_text(app)
    inspector.details_button.invoke()
    app.update()
    assert not inspector.result_text.winfo_ismapped()
    assert all(card.winfo_ismapped() for card in inspector.summary_cards)


def test_inspector_is_owned_by_viewer_without_stealing_viewer_focus(stats_app):
    app = stats_app
    app._show_inspector()
    app.update()
    inspector = app.inspector_window
    assert str(inspector.transient()) == str(app)
    assert not inspector.tk.getboolean(inspector.attributes("-topmost"))

    app.lift()
    app.focus_force()
    app.update()
    assert app.focus_get().winfo_toplevel() is app
    assert inspector.winfo_ismapped()
    if sys.platform in {"win32", "darwin"}:
        assert inspector.tk.getboolean(inspector.tk.call("wm", "stackorder", inspector, "isabove", app))

    inspector.withdraw()
    app.update()
    app.lift()
    app.update()
    assert not inspector.winfo_ismapped()
    assert app.winfo_ismapped()


def test_four_result_summaries_fit_default_inspector_size(stats_app):
    app = stats_app
    for dimension in ("2d", "1d"):
        write_inhibitory_result(app, dimension)
    inspector = show_all_results(app)

    assert len(inspector.summary_cards) == 2
    assert_cards_fit(inspector)
    for card in inspector.summary_cards:
        assert len(card.result_rows) == 2
        assert len(card.statistics_rows) == 1
        assert sum(label.cget("text").startswith("Mean") for label in card_labels(card)) == 1
    assert "mean − 3 × SD" in card_text(inspector.summary_cards[0])
    assert "mean − 0.75 × SD" in card_text(inspector.summary_cards[1])

    inspector.geometry("440x400")
    app.update()
    inspector._results_canvas.yview_moveto(0)
    card_labels(inspector.summary_cards[0])[0].event_generate("<MouseWheel>", delta=-120)
    app.update()
    assert inspector._results_canvas.yview()[0] > 0
    inspector._results_canvas.yview_moveto(1)
    app.update()
    last_label = card_labels(inspector.summary_cards[-1])[-1]
    visible_top = inspector._results_canvas.winfo_rooty()
    visible_bottom = visible_top + inspector._results_canvas.winfo_height()
    assert visible_top <= last_label.winfo_rooty()
    assert last_label.winfo_rooty() + last_label.winfo_height() <= visible_bottom


def test_shared_statistics_keep_each_polarity_threshold_and_detection_status(stats_app):
    app = stats_app
    write_inhibitory_result(app, "2d", mask=np.zeros((2, 2, 3), dtype=np.uint8))
    write_inhibitory_result(app, "1d")
    inspector = show_all_results(app)
    two_d, one_d = inspector.summary_cards

    assert len(two_d.statistics_rows) == len(one_d.statistics_rows) == 1
    assert "6 Hz" in card_text(two_d.statistics_rows[0])
    assert "12 Hz" in card_text(one_d.statistics_rows[0])
    excitatory, inhibitory = map(card_text, two_d.result_rows)
    assert "Excitatory" in excitatory and "mean + 1.8 × SD" in excitatory
    assert "1 detected bin" in excitatory
    assert "Inhibitory" in inhibitory and "mean − 3 × SD" in inhibitory
    assert "No RF detected" in inhibitory
    assert not inspector.details_frame.winfo_ismapped()
    inspector.details_button.invoke()
    app.update()
    assert "2D excitatory" in inspector_text(app)
    assert "2D inhibitory" in inspector_text(app)


def test_smoothed_polarities_share_one_baseline_and_show_two_numeric_thresholds(stats_app):
    app = stats_app
    original = app.results_pane.cache.result("2d", "excitatory")
    baseline = {
        "row_indices": [0, 1], "y_positions": list(app.results_pane.source.y_positions),
        "bin_count": 6, "mean_scope": "bottom_two_rows", "sd_scope": "bottom_two_rows", "sd_ddof": 0,
        "response_stage": "after_gui_smoothing1", "unit_ids": [7, 8],
        "mean_hz": [6., 12.], "sd_hz": [.5, 1.],
    }
    for polarity, alternative, multiplier in (("excitatory", "greater", 4.5), ("inhibitory", "less", 3.)):
        write_result(app.results_pane.source, "2d", polarity, ids=(7, 8), manifest_changes={
            **original.manifest, "detector_algorithm": "gui-smoothing1-bottom-two-rows-mean-sd-v1",
            "parameters": {**original.manifest["parameters"], "reference": "bottom_row",
                           "alternative": alternative, "cluster_forming_z": multiplier},
            "baseline": baseline,
            "spatial_preprocessing": {"algorithm": "gui-weighted-spatial-smoothing-v1", "iterations": 1},
        })
    write_inhibitory_result(app, "1d")
    inspector = show_all_results(app)
    two_d = inspector.summary_cards[0]
    assert len(two_d.statistics_rows) == 1
    assert sum(label.cget("text").startswith("Mean") for label in card_labels(two_d)) == 1
    assert "Smoothing 1" in card_text(two_d.statistics_rows[0])
    excitatory, inhibitory = map(card_text, two_d.result_rows)
    assert "mean + 4.5 × SD = 8.25 Hz" in excitatory
    assert "mean − 3 × SD = 4.5 Hz" in inhibitory
    assert_cards_fit(inspector)

    app._set_selected_unit_id(8)
    app._update_all()
    app.update()
    two_d = inspector.summary_cards[0]
    assert len(two_d.statistics_rows) == 1
    excitatory, inhibitory = map(card_text, two_d.result_rows)
    assert "mean + 4.5 × SD = 16.5 Hz" in excitatory
    assert "mean − 3 × SD = 9 Hz" in inhibitory
    assert "8.25 Hz" not in excitatory


def test_different_saved_windows_have_separate_statistic_groups(stats_app):
    app = stats_app
    write_inhibitory_result(app, "2d", manifest_changes={"time_range_s": [0.1, 0.2]})
    write_inhibitory_result(app, "1d")
    inspector = show_all_results(app)

    assert len(inspector.summary_cards) == 3
    two_d = [card for card in inspector.summary_cards if "2D" in card_text(card)]
    assert len(two_d) == 2
    assert all(len(card.statistics_rows) == 1 for card in two_d)
    assert any("0–200 ms" in card_text(card) and "6 Hz" in card_text(card) for card in two_d)
    assert any("100–200 ms" in card_text(card) and "9 Hz" in card_text(card) for card in two_d)
    # Different windows can create more cards than fit on a small screen.
    # Every label must remain reachable through the existing scroll canvas.
    inspector.geometry("500x400")
    app.update()
    canvas = inspector._results_canvas
    for card in inspector.summary_cards:
        for label in card_labels(card):
            offset = canvas.canvasy(0) + label.winfo_rooty() - canvas.winfo_rooty()
            canvas.yview_moveto(offset / inspector.result_cards_frame.winfo_height())
            app.update()
            assert label.winfo_ismapped(), label.cget("text")
            assert canvas.winfo_rooty() <= label.winfo_rooty()
            assert label.winfo_rooty() + label.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height()
            assert canvas.winfo_rootx() <= label.winfo_rootx()
            assert label.winfo_rootx() + label.winfo_width() <= canvas.winfo_rootx() + canvas.winfo_width()


def test_identical_numbers_from_different_references_are_not_shared(stats_app):
    app = stats_app
    result = app.results_pane.cache.result("2d", "excitatory")
    write_inhibitory_result(app, "2d", manifest_changes={
        "detector_algorithm": "bottom-two-rows-mean-sd-v1",
        "parameters": {**result.manifest["parameters"], "alternative": "less", "reference": "bottom_row"},
        "baseline": {
            "row_indices": [0, 1], "y_positions": list(app.results_pane.source.y_positions),
            "bin_count": 6, "mean_scope": "bottom_two_rows", "sd_scope": "bottom_two_rows", "sd_ddof": 0,
        },
    })
    write_inhibitory_result(app, "1d")
    inspector = show_all_results(app)
    two_d = inspector.summary_cards[0]

    assert len(two_d.statistics_rows) == 2
    assert all("6 Hz" in card_text(row) for row in two_d.statistics_rows)
    text = card_text(two_d)
    assert "full RF grid" in text
    assert "last 2" in text
    assert "Excitatory" in text and "Inhibitory" in text
    assert len(inspector.summary_cards[1].statistics_rows) == 1
    assert_cards_fit(inspector)


def test_saved_smoothed_statistics_stay_distinct_from_computed_native_statistics(stats_app):
    app = stats_app
    write_inhibitory_result(app, "2d")
    write_inhibitory_result(app, "1d")
    result = app.results_pane.cache.result("2d", "excitatory")
    sd = float(np.std([2.0, 6.0, 10.0]))
    write_result(app.results_pane.source, "2d", ids=(7, 8), manifest_changes={
        **result.manifest,
        "detector_algorithm": "gui-smoothing1-bottom-two-rows-mean-sd-v1",
        "parameters": {**result.manifest["parameters"], "reference": "bottom_row"},
        "baseline": {
            "row_indices": [0, 1], "y_positions": list(app.results_pane.source.y_positions),
            "bin_count": 6, "mean_scope": "bottom_two_rows", "sd_scope": "bottom_two_rows", "sd_ddof": 0,
            "response_stage": "after_gui_smoothing1", "unit_ids": [7, 8],
            "mean_hz": [6.0, 12.0], "sd_hz": [sd, 2 * sd],
        },
    })
    inspector = show_all_results(app)
    two_d, one_d = inspector.summary_cards

    assert len(two_d.statistics_rows) == 2
    saved, computed = map(card_text, two_d.statistics_rows)
    assert "Saved" in saved and "Smoothing 1" in saved
    assert "Computed" in computed and "Native RF" in computed
    assert all("6 Hz" in text and "3.266 Hz" in text for text in (saved, computed))
    assert len(one_d.statistics_rows) == 1
    assert_cards_fit(inspector)


def test_summary_refresh_clears_previous_unit_values(stats_app):
    app = stats_app
    write_inhibitory_result(app, "2d")
    write_inhibitory_result(app, "1d")
    inspector = show_all_results(app)
    assert "6 Hz" in card_text(inspector.summary_cards[0])
    app._set_selected_unit_id(8)
    app._update_all()
    app.update()
    assert "8" in inspector.results_header.cget("text")
    assert "12 Hz" in card_text(inspector.summary_cards[0])
    assert "No RF detected" in card_text(inspector.summary_cards[0])
    assert "24 Hz" in card_text(inspector.summary_cards[1])
    assert all(len(card.statistics_rows) == 1 for card in inspector.summary_cards)
    assert all(len(card.result_rows) == 2 for card in inspector.summary_cards)

    app.results_pane.unit_id = None
    app.results_pane.update_details(include_statistics=True)
    inspector.set_results(app.results_pane.details.get(), summaries=app.results_pane.summaries)
    app.update()
    assert not inspector.summary_cards
    assert "8" not in inspector.results_header.cget("text")


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
    write_inhibitory_result(app, "2d")
    write_inhibitory_result(app, "1d")
    inspector = show_all_results(app)
    assert "Computed mean: 6 Hz;" in inspector_text(app)
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
    assert len(inspector.summary_cards) == 2
    assert all(len(card.statistics_rows) == 1 for card in inspector.summary_cards)
    assert "12 Hz" in card_text(inspector.summary_cards[0].statistics_rows[0])
    assert "24 Hz" in card_text(inspector.summary_cards[1].statistics_rows[0])
