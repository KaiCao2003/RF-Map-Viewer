"""ADN class filters affect visible dots and new selections, not saved IDs."""

from types import SimpleNamespace

import pytest

from gui_test_support import tk_test_root
from rfmapping_viewer.adn_annotation_window import ADNAnnotationWindow, HD_CLASS_STYLES
from rfmapping_viewer.adn_selection import load_adn_units, save_adn_units
from rfmapping_viewer.companions import ProbeGeometry, ProbeUnitPosition
from rfmapping_viewer.tk_support import ttk


@pytest.fixture
def annotation(tmp_path):
    root = tk_test_root()
    geometry = ProbeGeometry(
        probe_name="ProbeA",
        positions_path=tmp_path / "positions.csv",
        channels_path=None,
        channels=(),
        units=tuple(
            ProbeUnitPosition(unit_id, 10.0 * (unit_id - 9), 120.0)
            for unit_id in range(10, 16)
        ),
    )
    output_path = tmp_path / "adn_neuron.npy"
    save_adn_units(output_path, "ProbeA", {13})
    saved = []
    dialog = ADNAnnotationWindow(
        root, geometry, output_path, saved.append,
        hd_classes={10: 0, 11: 1, 12: 2, 13: 3, 14: None},
    )
    dialog.update()
    dialog._draw()
    yield dialog, saved
    dialog.destroy()


def annotation_event(dialog, x_um, y_um):
    x, y = dialog._to_canvas(x_um, y_um)
    return SimpleNamespace(
        x=x - dialog.canvas.canvasx(0),
        y=y - dialog.canvas.canvasy(0),
    )


def enclose_all_units(dialog):
    events = [
        annotation_event(dialog, *point)
        for point in [(0, 100), (80, 100), (80, 140), (0, 140)]
    ]
    dialog._start_lasso(events[0])
    for event in events[1:]:
        dialog._extend_lasso(event)
    dialog._finish_lasso(events[0])


def test_dots_keep_class_colors_when_selected(annotation):
    dialog, _saved = annotation
    for unit_id in range(10, 16):
        dot, = dialog.canvas.find_withtag(f"unit-{unit_id}")
        expected_color = HD_CLASS_STYLES[dialog.hd_classes.get(unit_id)][1]
        assert dialog.canvas.itemcget(dot, "fill") == expected_color
    assert len(dialog.canvas.find_withtag("selection-ring")) == 1
    assert len({style[1] for style in HD_CLASS_STYLES.values()}) == 5


def test_lasso_excludes_hidden_classes_and_preserves_hidden_selection(annotation):
    dialog, saved = annotation
    transform = dialog._transform
    dialog.class_visible_vars[2].set(False)
    dialog.class_visible_vars[3].set(False)
    dialog._on_class_filter_changed()
    assert dialog.selected_ids == {13}
    assert dialog._transform == transform
    assert {point[2].unit_id for point in dialog._unit_points} == {10, 11, 14, 15}
    assert dialog.canvas.find_withtag("unit-12") == ()
    assert dialog.canvas.find_withtag("unit-13") == ()
    dialog._hover(annotation_event(dialog, 30, 120))
    assert dialog.canvas.find_withtag("hover") == ()

    enclose_all_units(dialog)
    assert dialog.selected_ids == {10, 11, 13, 14, 15}
    dialog._save()
    assert saved == [{10, 11, 13, 14, 15}]
    assert load_adn_units(dialog.output_path, "ProbeA") == {10, 11, 13, 14, 15}
    dialog._undo()
    assert dialog.selected_ids == {13}
    dialog.class_visible_vars[3].set(True)
    dialog._on_class_filter_changed()
    assert dialog.selected_ids == {13}
    assert len(dialog.canvas.find_withtag("selection-ring")) == 1


def test_unknown_filter_includes_missing_and_null_classes(annotation):
    dialog, _saved = annotation
    dialog.class_visible_vars[None].set(False)
    dialog._on_class_filter_changed()
    assert dialog._visible_unit_ids() == {10, 11, 12, 13}
    enclose_all_units(dialog)
    assert dialog.selected_ids == {10, 11, 12, 13}
    assert "Showing 4 / 6 units" in dialog.selection_var.get()


def test_hiding_every_class_does_not_clear_selection(annotation):
    dialog, _saved = annotation
    for visible in dialog.class_visible_vars.values():
        visible.set(False)
    dialog._on_class_filter_changed()
    assert dialog._unit_points == []
    assert dialog.canvas.find_withtag("unit-dot") == ()
    enclose_all_units(dialog)
    assert dialog.selected_ids == {13}
    assert "Showing 0 / 6 units" in dialog.selection_var.get()


def test_minimum_window_size_keeps_annotation_actions_visible(annotation):
    dialog, _saved = annotation
    dialog.geometry("740x500")
    dialog.update()
    inspector = dialog.unit_list.master
    buttons = [child for child in inspector.winfo_children() if isinstance(child, ttk.Button)]
    assert {button.cget("text") for button in buttons} == {"Undo", "Clear selection", "Save ADN", "Close"}
    for button in buttons:
        top = button.winfo_rooty() - dialog.winfo_rooty()
        assert 0 <= top < top + button.winfo_height() <= dialog.winfo_height()
    assert dialog.unit_list.winfo_height() > 0
