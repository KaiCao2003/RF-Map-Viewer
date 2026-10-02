from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

import rfmapping_gui as gui
from gui_test_support import current_rf_payload, tk_test_root
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.rf_results import RFResultSource
from rfmapping_viewer.settings import ViewerSettings, save_viewer_settings
from rfmapping_viewer.tk_support import TK_AVAILABLE, tk, ttk
from test_rf_results import write_result


@pytest.fixture
def app(tmp_path):
    tk_test_root()
    payload = current_rf_payload({
        "unitsSpikeCounts": np.full((2, 2, 3, 3), 10, dtype=int).tolist(),
        "unitsSpikeCountsSize": [2, 2, 3, 3], "unitPool": [7, 8],
        "xPositions": [-90, 0, 90], "yPositions": [-15, 15],
        "timeBinEdges": [-.1, 0, .1, .2],
    })
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(payload))
    settings = replace(
        ViewerSettings(), rf_filter_units_with_zero_bins=False,
        show_tuning_curve=False, auto_load_tuning_curve=False,
        show_probe_layout=False, auto_load_probe_layout=False, show_waveform=False,
        rf_result_overlay_mode="Both", rf_result_overlay_width=3.5,
        rf_result_overlay_2d_color="#125e20", rf_result_overlay_1d_color="#ab8200",
        rf_result_overlay_overlap_color="#cf6c15",
    )
    preferences = tmp_path / "settings.json"
    save_viewer_settings(settings, preferences)
    data = RFMappingData(path)
    source = RFResultSource.from_document(data)
    mask = np.array([[[1, 0, 0], [0, 0, 1]], [[0, 0, 0], [0, 0, 0]]], dtype=np.uint8)
    center = np.zeros_like(mask)
    center[0, 1, 2] = 1
    write_result(source, ids=(7, 8), mask=mask, center=center)
    write_result(source, "1d", ids=(7, 8), mask=np.array([[[1, 0, 0]], [[0, 0, 0]]], dtype=np.uint8))
    with (
        mock.patch.object(gui, "viewer_settings_path", return_value=preferences),
        mock.patch.object(gui, "list_recent_documents", return_value=[]),
        mock.patch.object(gui, "record_recent_document"),
    ):
        viewer = gui.RFMViewer(data)
    viewer.geometry("1320x840")
    viewer.results_pane.dimension.set("Both")
    if viewer._optional_autoload_after is not None:
        viewer.after_cancel(viewer._optional_autoload_after)
        viewer._optional_autoload_after = None
    viewer.update()
    try:
        yield viewer
    finally:
        try:
            if viewer.winfo_exists():
                viewer.destroy()
            if viewer._app_root.winfo_exists():
                viewer._app_root.destroy()
        except tk.TclError:
            pass


def draw(app, tab):
    app._select_tab_key(tab)
    app._sync_context_controls()
    app.update_idletasks()
    app._draw_active_tab(update_optional_views=False)
    app.update_idletasks()


def canvas_bounds(canvas):
    return (canvas.winfo_rootx(), canvas.winfo_rooty(), canvas.winfo_width(), canvas.winfo_height())


def native_overlay(app):
    canvas = app.canvases["rf"]
    return {
        next(tag for tag in canvas.gettags(item) if tag.startswith("rf-source-")): (
            canvas.type(item), canvas.coords(item), canvas.itemcget(item, "outline"),
            canvas.itemcget(item, "fill"), float(canvas.itemcget(item, "width")),
        )
        for item in canvas.find_withtag("rf-result-overlay")
    }


pytestmark = pytest.mark.skipif(not TK_AVAILABLE, reason="Tk is unavailable")


def test_selected_rf_results_and_delay_canvases_are_visible_hit_targets(app):
    for tab in ("rf", "results", "delay", "results", "rf"):
        draw(app, tab)
        app.lift()
        app.update()
        canvas = app.canvases["rf" if tab in {"rf", "results"} else "delay"]
        assert canvas.winfo_ismapped()
        x = canvas.winfo_rootx() + canvas.winfo_width() // 2
        y = canvas.winfo_rooty() + canvas.winfo_height() // 2
        assert app.winfo_containing(x, y) is canvas, f"{tab} canvas is covered by another notebook page"
        if tab == "delay":
            assert not app.rf_split_container.winfo_ismapped()


def test_new_settings_window_takes_focus_and_supports_keyboard_traversal(app):
    app.focus_force()
    app.update()
    app._show_settings()
    app.update()
    settings = app._app_root._rfm_settings_window
    assert app.focus_get().winfo_toplevel() is settings
    focused = settings.focus_get()
    focused.event_generate("<Tab>")
    settings.update()
    focused = settings.focus_get()
    assert focused is not settings
    assert focused.winfo_toplevel() is settings
    initial = settings.notebook.index("current")
    focused.event_generate("<Control-Tab>")
    settings.update()
    assert settings.notebook.index("current") == (initial + 1) % len(settings.notebook.tabs())


def test_settings_wheel_scrolls_over_form_controls_before_their_classes_and_cleans_up(app):
    combo_binding = app.tk.call("bind", "TCombobox", "<MouseWheel>")
    app._show_settings()
    settings = app._app_root._rfm_settings_window
    settings.geometry("720x640")
    settings.notebook.select(settings._tab_widget_by_name["RF Map"])
    settings.update()
    canvas = settings._tab_canvases[str(settings.notebook.select())]
    form = canvas.winfo_children()[0]
    widgets = [canvas, form]
    for kind in (ttk.Label, ttk.Entry, ttk.Combobox, ttk.Spinbox):
        widgets.append(next(widget for widget in form.winfo_children() if isinstance(widget, kind)))
    values = (settings.rf_palette_var.get(), settings.rf_smooth_radius_var.get())
    for widget in widgets:
        canvas.yview_moveto(0)
        assert widget.bindtags()[0] == settings._form_scroll_tag
        widget.event_generate("<MouseWheel>", delta=-120)
        settings.update_idletasks()
        assert canvas.yview()[0] > 0, f"Wheel over {widget.winfo_class()} did not scroll the form"
        assert (settings.rf_palette_var.get(), settings.rf_smooth_radius_var.get()) == values
    tag = settings._form_scroll_tag
    sequences = settings._form_scroll_sequences.copy()
    commands = settings._form_scroll_commands.copy()
    assert tag not in app.value_mode_combo.bindtags()
    assert app.tk.call("bind", "TCombobox", "<MouseWheel>") == combo_binding
    settings.destroy()
    assert all(not app.tk.call("bind", tag, sequence) for sequence in sequences)
    assert all(not app.tk.call("info", "commands", command) for command in commands)
    assert app.tk.call("bind", "TCombobox", "<MouseWheel>") == combo_binding


def test_settings_precise_touchpad_scroll_uses_only_vertical_pixel_delta(app):
    if not app.tk.call("info", "commands", "tk::PreciseScrollDeltas"):
        pytest.skip("requires Tk 9 precise trackpad events")
    app._show_settings()
    settings = app._app_root._rfm_settings_window
    settings.geometry("720x640")
    settings.notebook.select(settings._tab_widget_by_name["RF Map"])
    settings.update()
    canvas = settings._tab_canvases[str(settings.notebook.select())]
    form = canvas.winfo_children()[0]
    combo = next(widget for widget in form.winfo_children() if isinstance(widget, ttk.Combobox))
    value = combo.get()
    canvas.yview_moveto(0)
    # Tk packs signed x/y deltas into the high/low 16 bits respectively.
    combo.event_generate("<TouchpadScroll>", delta=(-20 & 0xffff))
    settings.update_idletasks()
    assert canvas.yview()[0] > 0
    assert combo.get() == value
    position = canvas.yview()
    combo.event_generate("<TouchpadScroll>", delta=(20 << 16))
    settings.update_idletasks()
    assert canvas.yview() == position
    canvas.event_generate("<TouchpadScroll>", delta=20)
    settings.update_idletasks()
    assert canvas.yview()[0] < position[0]


def test_settings_precise_scroll_moves_actual_canvas_by_scaled_pixels(app):
    app._show_settings()
    settings = app._app_root._rfm_settings_window
    settings.geometry("720x640")
    settings.notebook.select(settings._tab_widget_by_name["RF Map"])
    settings.update()
    canvas = settings._tab_canvases[str(settings.notebook.select())]
    form = canvas.winfo_children()[0]
    combo = next(widget for widget in form.winfo_children() if isinstance(widget, ttk.Combobox))
    created = []
    # Exercise the real Canvas even on Tk 8.6, which has no native precise
    # event/parser. Canvas accepts moveto fractions, not scroll "pixels".
    for name, function in (
        ("tk::PreciseScrollDeltas", lambda _packed: (0, -20)),
        ("tk::ScaleNum", lambda value: float(value) * 2),
    ):
        if not app.tk.call("info", "commands", name):
            app.tk.createcommand(name, function)
            created.append(name)
    try:
        canvas.yview_moveto(0)
        before = canvas.canvasy(0)
        value = combo.get()
        expected = app.tk.getdouble(app.tk.call("tk::ScaleNum", 20))
        event = SimpleNamespace(widget=combo, delta=(-20 & 0xffff))
        assert settings._scroll_form(event, precise=True) == "break"
        settings.update_idletasks()
        assert canvas.canvasy(0) - before == pytest.approx(expected, abs=1)
        assert combo.get() == value
    finally:
        for name in created:
            app.tk.deletecommand(name)


@pytest.mark.parametrize("polar,flip,grouped,expanded", [
    (False, False, False, False), (False, True, True, True),
    (True, False, False, True), (True, True, True, False),
])
def test_rf_and_results_keep_identical_canvas_and_grid_through_switches_and_resize(app, polar, flip, grouped, expanded):
    app.polar_layout_var.set(polar)
    app.flip_y_var.set(flip)
    app.x_bins_var.set(1 if grouped else 3)
    app.y_bins_var.set(1 if grouped else 2)
    app.display_expanded_var.set(expanded)
    canvas = app.canvases["rf"]
    for size in ("1120x720", "1540x940"):
        app.geometry(size)
        draw(app, "rf")
        bounds = canvas_bounds(canvas)
        layout = app._canvas_layouts["rf"].copy()
        outlines = native_overlay(app)
        draw(app, "results")
        assert app.canvases["rf"] is canvas
        assert canvas_bounds(canvas) == bounds
        assert app._canvas_layouts["rf"] == layout
        result_shapes = native_overlay(app)
        assert result_shapes.keys() == outlines.keys()
        for source, expected in outlines.items():
            actual = result_shapes[source]
            assert actual[:3] == expected[:3]
            assert expected[3] == ""
            assert actual[3] == actual[2]
            assert actual[4] == expected[4]
        draw(app, "rf")
        assert canvas_bounds(canvas) == bounds
        assert app._canvas_layouts["rf"] == layout


@pytest.mark.parametrize("mode,expected", [
    ("None", {}),
    ("2D", {"rf-source-0-0": "#125e20", "rf-source-1-2": "#125e20"}),
    ("1D", {"rf-source-0-0": "#ab8200", "rf-source-1-0": "#ab8200"}),
    ("Both", {"rf-source-0-0": "#cf6c15", "rf-source-1-0": "#ab8200", "rf-source-1-2": "#125e20"}),
])
def test_rf_overlay_modes_color_native_bins_and_preserve_saved_files(app, mode, expected):
    source_files = [app.data.path, app.data.path.with_suffix(".npz"), app.data.path.with_name("mapping_1d.npz")]
    original = {path: path.read_bytes() for path in source_files}
    app.settings = replace(app.settings, rf_result_overlay_mode=mode)
    draw(app, "rf")
    items = native_overlay(app)
    assert items.keys() == expected.keys()
    for source, color in expected.items():
        kind, _coords, outline, fill, width = items[source]
        assert kind == "rectangle"
        assert outline == color
        assert fill == ""
        assert width == 3.5
    assert {path: path.read_bytes() for path in source_files} == original


def test_pooled_display_bins_preserve_native_boundaries_without_false_overlap(app):
    source = app.results_pane.source
    mask = np.array([[[0, 0, 1], [0, 0, 0]], [[0, 0, 0], [0, 0, 0]]], dtype=np.uint8)
    write_result(source, ids=(7, 8), mask=mask)
    app.results_pane.cache.clear()
    app.x_bins_var.set(1)
    app.y_bins_var.set(1)
    draw(app, "rf")
    items = native_overlay(app)
    assert set(items) == {"rf-source-0-0", "rf-source-1-0", "rf-source-0-2"}
    assert {item[2] for item in items.values()} == {"#125e20", "#ab8200"}
    assert not app.canvases["rf"].find_withtag("rf-result-3")
    layout = app._canvas_layouts["rf"]
    for source, (_kind, coords, _outline, _fill, _width) in items.items():
        _, _, row, column = source.split("-")
        row, column = int(row), int(column)
        assert coords == pytest.approx([
            layout["x0"] + column * layout["grid_w"] / 3,
            layout["y0"] + row * layout["grid_h"] / 2,
            layout["x0"] + (column + 1) * layout["grid_w"] / 3,
            layout["y0"] + (row + 1) * layout["grid_h"] / 2,
        ])


@pytest.mark.parametrize("polar", [False, True])
def test_flip_y_reverses_the_native_overlay_vertical_or_radial_order(app, polar):
    app.polar_layout_var.set(polar)
    app.x_bins_var.set(1)
    app.y_bins_var.set(1)
    order = []
    for flip in (False, True):
        app.flip_y_var.set(flip)
        draw(app, "rf")
        shapes = native_overlay(app)
        if polar:
            layout = app._canvas_layouts["rf"]
            distances = []
            for source in ("rf-source-0-0", "rf-source-1-0"):
                points = np.asarray(shapes[source][1]).reshape(-1, 2)
                distances.append(np.hypot(points[:, 0] - layout["cx"], points[:, 1] - layout["cy"]).mean())
            order.append(distances[0] > distances[1])
        else:
            order.append(shapes["rf-source-0-0"][1][1] < shapes["rf-source-1-0"][1][1])
    assert order == [True, False]


def test_inspector_is_on_demand_reusable_and_closes_with_document(app):
    inspector = app.inspector_window
    assert inspector.state() == "withdrawn"
    app.selected_cell = (0, 0, 0, 0)
    draw(app, "results")
    app._show_inspector()
    app.update()
    assert inspector.state() == "normal"
    assert app.focus_get().winfo_toplevel() is inspector
    notebook = next(widget for widget in inspector.winfo_children() if isinstance(widget, ttk.Notebook))
    inspector.focus_get().event_generate("<Control-Tab>")
    inspector.update()
    assert notebook.index("current") == 1
    assert "7" in inspector.unit_label.cget("text")
    assert "bin" in inspector.response_label.cget("text")
    details = inspector.result_text.get("1.0", "end")
    assert "2D excitatory" in details and "1D excitatory" in details
    assert inspector.result_text.cget("state") == "disabled"
    app.tk.call(inspector.protocol("WM_DELETE_WINDOW"))
    assert inspector.state() == "withdrawn"
    app._show_inspector()
    assert app.inspector_window is inspector
    assert inspector.state() == "normal"
    with mock.patch.object(app, "_quit_if_no_windows"):
        app.destroy()
    assert not inspector.winfo_exists()


@pytest.mark.parametrize("polar", [False, True])
def test_results_show_saved_2d_centers_and_one_1d_marker_at_axis_edge(app, polar):
    app.polar_layout_var.set(polar)
    app.x_bins_var.set(1)
    app.y_bins_var.set(1)
    draw(app, "results")
    canvas = app.canvases["rf"]
    lines = canvas.find_withtag("rf-center-2d")
    triangles = canvas.find_withtag("rf-center-1d")
    assert len(lines) == 4  # One saved center with white and dark X strokes.
    assert len(triangles) == 1  # One saved 1-D center; broadcasting adds no centers.
    centers = []
    for item in lines:
        if canvas.itemcget(item, "fill") != "#1d1d1f":
            continue
        x0, y0, x1, y1 = canvas.coords(item)
        center = ((x0 + x1) / 2, (y0 + y1) / 2)
        if center not in centers:
            centers.append(center)
    layout = app._canvas_layouts["rf"]
    if not polar:
        overlays = native_overlay(app)
        for source in ("rf-source-1-2",):
            x0, y0, x1, y1 = overlays[source][1]
            expected = ((x0 + x1) / 2, (y0 + y1) / 2)
            assert any(np.allclose(center, expected, rtol=0, atol=1e-9) for center in centers)
        triangle = np.asarray(canvas.coords(triangles[0])).reshape(-1, 2)
        assert triangle[:, 1].min() > layout["y0"] + layout["grid_h"]
    else:
        overlays = native_overlay(app)
        for source, direction in (("rf-source-1-2", 1),):
            points = np.asarray(overlays[source][1]).reshape(-1, 2)
            radii = np.hypot(points[:, 0] - layout["cx"], points[:, 1] - layout["cy"])
            expected = (layout["cx"] + direction * (radii.min() + radii.max()) / 2, layout["cy"])
            assert any(np.allclose(center, expected) for center in centers)
        triangle = np.asarray(canvas.coords(triangles[0])).reshape(-1, 2)
        radii = np.hypot(triangle[:, 0] - layout["cx"], triangle[:, 1] - layout["cy"])
        all_points = np.concatenate([np.asarray(item[1]).reshape(-1, 2) for item in overlays.values()])
        outer = np.hypot(all_points[:, 0] - layout["cx"], all_points[:, 1] - layout["cy"]).max()
        assert radii.min() > outer
    draw(app, "rf")
    assert not canvas.find_withtag("rf-center-2d")
    assert not canvas.find_withtag("rf-center-1d")


def test_results_do_not_reuse_the_previous_units_mask_when_selected_unit_is_absent(app):
    draw(app, "results")
    assert app.canvases["rf"].find_withtag("rf-result-overlay")
    app._set_selected_unit_id(99)
    app._draw_active_tab(update_optional_views=False)
    canvas = app.canvases["rf"]
    assert not canvas.find_withtag("rf-result-overlay")
    assert not canvas.find_withtag("rf-center-2d")
    assert "rf" not in app._canvas_layouts
    texts = [canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text"]
    assert "N/A" in texts
    app._set_selected_unit_id(7)
    draw(app, "results")
    assert canvas.find_withtag("rf-result-overlay")
