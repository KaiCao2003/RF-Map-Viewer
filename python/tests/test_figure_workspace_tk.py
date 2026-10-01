"""Exercise real pointer gestures and the composition/export boundary."""

from types import SimpleNamespace

import pytest
from PIL import Image

from gui_test_support import Variable, tk_test_root
from rfmapping_viewer.figure_composer import FigureExportWindow
from rfmapping_viewer.figure_export import DEFAULT_PAGE_SIZE, PlotKind
from rfmapping_viewer.figure_layout import validate_frames
from rfmapping_viewer.figure_workspace import FigurePageCanvas
from rfmapping_viewer.tk_support import tk


@pytest.fixture
def workspace():
    root = tk_test_root()
    window = tk.Toplevel(root)
    window.geometry("1000x800")
    changes, selected, errors = [], [], []
    canvas = FigurePageCanvas(
        window,
        on_select=selected.append,
        on_change=lambda frames, index: changes.append((frames, index)),
        on_error=errors.append,
    )
    canvas.pack(fill="both", expand=True)
    canvas.set_frames(((0, 0, 2, 2), (2, 0, 2, 2)), ("RF", "HD"), 0)
    image = Image.new("RGB", DEFAULT_PAGE_SIZE, "white")
    canvas.set_image(image)
    image.close()
    root.update()
    yield root, canvas, changes, selected, errors
    window.destroy()
    root.update()


def test_pointer_drag_tracks_immediately_and_commits_once(workspace):
    root, canvas, changes, selected, errors = workspace
    first = canvas._box(canvas.frames[0])
    second = canvas._box(canvas.frames[1])
    x, y = round(first[0] + 25), round(first[1] + 25)
    delta = round(second[0] - first[0])
    canvas.event_generate("<ButtonPress-1>", x=x, y=y)
    canvas.event_generate("<B1-Motion>", x=x + delta, y=y)
    root.update()
    assert selected == [0]
    assert canvas.find_withtag("gesture")
    assert changes == []
    canvas.event_generate("<ButtonRelease-1>", x=x + delta, y=y)
    root.update()
    assert len(changes) == 1
    frames, index = changes[0]
    assert index == 0
    assert frames == ((2, 0, 2, 2), (0, 0, 2, 2))
    assert validate_frames(frames) == frames
    assert errors == []
    assert canvas.grab_current() is None


def test_corner_resize_snaps_to_preset_and_escape_cancels(workspace):
    root, canvas, changes, _selected, errors = workspace
    left, top, right, bottom = canvas._box(canvas.frames[0])
    x, y = round(right - 3), round(bottom - 3)
    # Grow from Small to Wide while preserving the grab offset.
    width = round(right - left + 10)
    canvas.event_generate("<ButtonPress-1>", x=x, y=y)
    canvas.event_generate("<B1-Motion>", x=x + width, y=y)
    canvas.event_generate("<ButtonRelease-1>", x=x + width, y=y)
    root.update()
    assert changes[0][0][0][2:] == (4, 2)
    assert errors == []
    changes.clear()
    canvas.event_generate("<ButtonPress-1>", x=round(left + 20), y=round(top + 20))
    canvas.event_generate("<B1-Motion>", x=round(left + 70), y=round(top + 20))
    canvas._cancel()
    canvas.event_generate("<ButtonRelease-1>", x=round(left + 70), y=round(top + 20))
    assert changes == []
    assert canvas.grab_current() is None


def test_export_uses_current_frames_and_can_preview_past_empty_pages():
    composer = SimpleNamespace(
        pages=[
            {"name": "Unfinished", "plots": [], "frames": []},
            {"name": "RF", "plots": [PlotKind.RF_POLAR], "frames": [(0, 0, 4, 4)]},
        ],
        normalize_per_unit_var=Variable(True),
    )
    pages = FigureExportWindow._export_pages(composer, page_index=1)
    assert len(pages) == 1
    assert pages[0].plots[0].options["frame"] == (0, 0, 4, 4)
    assert pages[0].plots[0].options["normalize_per_unit"] is True
    with pytest.raises(ValueError, match="has no views"):
        FigureExportWindow._export_pages(composer)


def test_all_export_selection_does_not_request_preview():
    composer = SimpleNamespace(
        unit_ids=(7, 8, 9),
        current_unit_id=8,
        _refresh_unit_rows=lambda **_kwargs: None,
        _schedule_preview=lambda: pytest.fail(
            "Selecting output units must not render previews"
        ),
    )
    FigureExportWindow._select_all_units(composer)
    assert composer._selected_unit_indices == {0, 1, 2}
    assert composer.current_unit_id == 8
