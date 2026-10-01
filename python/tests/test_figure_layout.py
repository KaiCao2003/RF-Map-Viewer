"""Composition geometry and final-render parity for freely arranged plots."""

import base64
import io
import re

import pytest
from PIL import Image
from pypdf import PdfReader

from rfmapping_viewer.figure_export import (
    ExportPage,
    ExportPlan,
    FigureExportValidationError,
    PillowFigureRenderer,
    PlotKind,
    PlotSpec,
    export_figures,
)
from rfmapping_viewer.figure_layout import (
    FRAME_PRESETS,
    arrange_frames,
    automatic_frames,
    first_available_frame,
    frame_pixel_box,
    page_grid_box,
    validate_frames,
)


def test_automatic_frames_and_preset_capacity():
    assert automatic_frames(0) == ()
    assert automatic_frames(1) == ((0, 0, 6, 6),)
    assert automatic_frames(2) == ((0, 0, 3, 6), (3, 0, 3, 6))
    assert automatic_frames(4)[-1] == (3, 3, 3, 3)
    assert len(validate_frames(automatic_frames(9))) == 9
    assert FRAME_PRESETS["Wide"] == (4, 2)
    assert FRAME_PRESETS["Tall"] == (2, 4)
    with pytest.raises(ValueError, match="nine"):
        automatic_frames(10)
    with pytest.raises(ValueError, match="No room"):
        first_available_frame(automatic_frames(9))


def test_drag_preserves_neighbours_and_swaps_occupied_frames():
    frames = ((0, 0, 2, 2), (2, 0, 2, 2), (4, 4, 2, 2))
    moved = arrange_frames(frames, 0, (2, 0, 2, 2))
    assert moved == ((2, 0, 2, 2), (0, 0, 2, 2), (4, 4, 2, 2))
    assert validate_frames(moved) == moved
    assert frames[0] == (0, 0, 2, 2)


def test_resize_reflows_neighbours_without_overlap():
    frames = tuple((index % 3 * 2, index // 3 * 2, 2, 2) for index in range(6))
    expanded = arrange_frames(frames, 0, (0, 0, 4, 4))
    assert expanded[0] == (0, 0, 4, 4)
    assert validate_frames(expanded) == expanded
    assert expanded[2] == frames[2]
    assert expanded[5] == frames[5]


def test_full_page_rejects_impossible_size_and_position():
    frames = automatic_frames(9)
    with pytest.raises(ValueError, match="No room"):
        arrange_frames(frames, 0, (0, 0, 4, 4))
    # The single-grid-cell strips beside this anchored plot cannot accommodate
    # all eight remaining 2 × 2 plots, even though their total area is unchanged.
    with pytest.raises(ValueError, match="cannot fit here"):
        arrange_frames(frames, 0, (1, 1, 2, 2))
    assert frames == automatic_frames(9)


@pytest.mark.parametrize("frame", [None, (0, 0, 2), (0, 0, 2.0, 2), (True, 0, 2, 2), (-1, 0, 2, 2), (5, 0, 2, 2)])
def test_invalid_plot_frame_has_export_validation_error(frame):
    with pytest.raises(FigureExportValidationError, match="frame"):
        PlotSpec(PlotKind.RF_CARTESIAN, options={"frame": frame})


def test_export_page_rejects_overlap_or_partially_positioned_plots():
    positioned = PlotSpec(PlotKind.RF_CARTESIAN, options={"frame": (0, 0, 4, 4)})
    with pytest.raises(FigureExportValidationError, match="overlap"):
        ExportPage("Overlap", (positioned, positioned))
    with pytest.raises(FigureExportValidationError, match="every plot"):
        ExportPage("Missing position", (positioned, PlotSpec(PlotKind.HD_LINE)))


def test_renderer_uses_exact_widget_bounds_and_opaque_white_canvas(monkeypatch):
    page_size = (1200, 900)
    frames = ((0, 0, 4, 2), (4, 0, 2, 4), (0, 2, 4, 4))
    page = ExportPage("Widgets", tuple(PlotSpec(PlotKind.RF_CARTESIAN, [[1]], options={"frame": frame}) for frame in frames))
    panels = []
    colors = ("red", "green", "blue")

    def draw_panel(draw, panel, spec):
        panels.append(panel)
        draw.rectangle(panel, fill=colors[len(panels) - 1])

    monkeypatch.setattr(PillowFigureRenderer, "_draw_plot", staticmethod(draw_panel))
    image = PillowFigureRenderer(page_size).render_page(7, page)
    assert panels == [frame_pixel_box(frame, page_size) for frame in frames]
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.getpixel((panels[0][0] + 1, panels[0][1] + 1)) == (255, 0, 0)
    assert image.getpixel((panels[1][0] + 1, panels[1][1] + 1)) == (0, 128, 0)
    assert image.getpixel((panels[2][0] + 1, panels[2][1] + 1)) == (0, 0, 255)
    grid = page_grid_box(page_size)
    assert all(grid[0] < box[0] < box[2] < grid[2] for box in panels)
    assert all(grid[1] < box[1] < box[3] < grid[3] for box in panels)


def test_mixed_widget_layout_is_identical_in_preview_png_svg_and_pdf(tmp_path):
    frames = ((0, 0, 4, 2), (4, 0, 2, 4), (0, 2, 4, 4))
    plots = tuple(
        PlotSpec(PlotKind.RF_CARTESIAN, [[0, 1], [2, 3]], options={"frame": frame})
        for frame in frames
    )
    page = ExportPage("Composition", plots)
    renderer = PillowFigureRenderer((1200, 900))
    expected = renderer.render_preview(17, page)
    png_result = export_figures(ExportPlan("png", [17], [page], tmp_path / "png"), renderer=renderer)
    png_path = next(path for path in png_result.files if path.suffix == ".png")
    with Image.open(png_path) as png:
        assert png.tobytes() == expected.tobytes()
    svg_result = export_figures(ExportPlan("svg", [17], [page], tmp_path / "svg"), renderer=renderer)
    svg_path = next(path for path in svg_result.files if path.suffix == ".svg")
    payload = re.search(r"base64,([^\"]+)", svg_path.read_text()).group(1)
    with Image.open(io.BytesIO(base64.b64decode(payload))) as svg_image:
        assert svg_image.tobytes() == expected.tobytes()
    pdf_result = export_figures(ExportPlan("pdf", [17], [page], tmp_path / "composition.pdf"), renderer=renderer)
    assert pdf_result.page_count == 1
    pdf = PdfReader(tmp_path / "composition.pdf")
    embedded = pdf.pages[0]["/Resources"]["/XObject"]["/image"].get_object()
    assert embedded.get_data() == expected.tobytes()
