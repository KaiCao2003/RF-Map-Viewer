from __future__ import annotations

import base64
import errno
import hashlib
import io
import json
import os
import re
import shutil
import stat
import threading
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader

import rfmapping_viewer.figure_export as figure_export_module

from rfmapping_viewer.figure_export import (
    SVG_RENDERING_CONTRACT,
    DestinationExistsError,
    ExportPage,
    ExportPlan,
    FigureExportValidationError,
    FigureFormat,
    PLOT_KIND_REGISTRY,
    PillowFigureRenderer,
    PlotKind,
    PlotSpec,
    automatic_grid,
    export_figures,
    iter_generated_pages,
    render_live_preview,
    shared_scalar_scale,
)


SCALAR_MATRIX = [
    [0.0, 1.0, 2.0, 3.0],
    [4.0, 5.0, 6.0, 7.0],
    [8.0, 9.0, 10.0, 11.0],
]
RGB_MATRIX = [
    [[255, 0, 0], [0, 255, 0], [0, 0, 255]],
    [[1.0, 1.0, 0.0], [0.0, 1.0, 1.0], [1.0, 0.0, 1.0]],
]
HD_CURVE = {
    "angles_deg": [0.0, 60.0, 120.0, 180.0, 240.0, 300.0],
    "rates": [2.0, 4.0, 7.0, 5.0, 3.0, 1.0],
}
PROBE_POINTS = {
    "points": [
        {"x": 0.0, "y": 0.0, "label": "A", "color": "#dc2626"},
        {"x": 20.0, "y": 100.0, "label": "B"},
        {"x": 0.0, "y": 200.0, "label": "C"},
    ]
}


def _data_for(unit_id: int, spec: PlotSpec):
    if spec.kind in {PlotKind.RGB_CARTESIAN, PlotKind.RGB_POLAR}:
        return RGB_MATRIX
    if spec.kind is PlotKind.TIMELINE_CURRENT:
        return {
            "times": [-0.1, 0.0, 0.1, 0.2],
            "totals": [1.0, 4.0, 8.0, 3.0],
            "selected": [0.0, 2.0, 5.0, 1.0],
            "frames": [
                SCALAR_MATRIX,
                [[value + unit_id for value in row] for row in SCALAR_MATRIX],
            ],
        }
    if spec.kind in {PlotKind.HD_LINE, PlotKind.HD_POLAR}:
        return HD_CURVE
    if spec.kind is PlotKind.PROBE_LAYOUT:
        return PROBE_POINTS
    return [[value + unit_id for value in row] for row in SCALAR_MATRIX]


def _page(name: str = "Summary", *kinds: PlotKind) -> ExportPage:
    if not kinds:
        kinds = (PlotKind.RF_CARTESIAN,)
    return ExportPage(name, [PlotSpec(kind) for kind in kinds])


def _plan(
    destination: Path,
    *,
    figure_format: FigureFormat = FigureFormat.PNG,
    units: tuple[int, ...] = (7,),
    pages: tuple[ExportPage, ...] | None = None,
) -> ExportPlan:
    if pages is None:
        pages = (_page(),)
    return ExportPlan(figure_format, units, pages, destination)


def test_plot_kind_registry_has_all_stable_view_identifiers() -> None:
    assert tuple(PLOT_KIND_REGISTRY) == tuple(kind.value for kind in PlotKind)
    assert set(PLOT_KIND_REGISTRY) == {
        "rf.cartesian",
        "rf.polar",
        "delay.cartesian",
        "delay.polar",
        "rgb.cartesian",
        "rgb.polar",
        "timeline.current",
        "hd.line",
        "hd.polar",
        "probe",
    }
    assert all(
        definition.kind.value == stable_id and definition.label
        for stable_id, definition in PLOT_KIND_REGISTRY.items()
    )
    with pytest.raises(TypeError):
        PLOT_KIND_REGISTRY["new_kind"] = PLOT_KIND_REGISTRY["rf.cartesian"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, (1, 1)), (2, (1, 2)), (3, (2, 2)), (4, (2, 2)), (5, (2, 3)), (10, (3, 4))],
)
def test_automatic_grid_is_compact(count: int, expected: tuple[int, int]) -> None:
    assert automatic_grid(count) == expected


def test_export_models_normalize_sequences_and_format(tmp_path: Path) -> None:
    page = ExportPage(" Summary ", [PlotSpec("rf.cartesian")])
    plan = ExportPlan(".PNG", [11, 22], [page], tmp_path / "figures")

    assert page.name == "Summary"
    assert isinstance(page.plots, tuple)
    assert page.plots[0].kind is PlotKind.RF_CARTESIAN
    assert plan.format is FigureFormat.PNG
    assert plan.unit_ids == (11, 22)
    assert plan.pages == (page,)


def test_export_plan_metadata_is_json_safe_and_deeply_immutable(tmp_path: Path) -> None:
    source = {
        "source": {"path": "session.json", "sha256": "a" * 64},
        "windowMs": [0.0, 100.0],
    }
    plan = ExportPlan(
        "png",
        [11],
        [_page()],
        tmp_path / "figures",
        metadata=source,
    )

    source["source"]["path"] = "changed.json"
    source["windowMs"].append(200.0)
    assert plan.metadata["source"]["path"] == "session.json"
    assert plan.metadata["windowMs"] == (0.0, 100.0)
    with pytest.raises(TypeError):
        plan.metadata["new"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.metadata["source"]["path"] = "changed.json"  # type: ignore[index]

    with pytest.raises(FigureExportValidationError, match="finite"):
        ExportPlan(
            "png",
            [1],
            [_page()],
            tmp_path / "nan",
            metadata={"value": float("nan")},
        )
    with pytest.raises(FigureExportValidationError, match="keys must be strings"):
        ExportPlan(
            "png",
            [1],
            [_page()],
            tmp_path / "bad-key",
            metadata={1: "not JSON"},  # type: ignore[dict-item]
        )


def test_shared_scalar_scale_is_reusable_and_validated() -> None:
    scale = shared_scalar_scale(
        [
            [[None, -2.0], [1.0, float("nan")]],
            {"matrix": [[10.0, 20.0]]},
        ]
    )

    assert dict(scale) == {"vmin": -2.0, "vmax": 20.0}
    with pytest.raises(TypeError):
        scale["vmin"] = 0.0  # type: ignore[index]
    assert dict(shared_scalar_scale([[[1.0]]], vmin=0, vmax=5)) == {
        "vmin": 0.0,
        "vmax": 5.0,
    }
    with pytest.raises(FigureExportValidationError, match="vmax"):
        shared_scalar_scale([[[1.0]]], vmin=2, vmax=1)


def test_export_models_strictly_require_units_pages_and_plots(tmp_path: Path) -> None:
    with pytest.raises(FigureExportValidationError, match="at least one plot"):
        ExportPage("Empty", [])
    with pytest.raises(FigureExportValidationError, match="at least one unit"):
        ExportPlan("png", [], [_page()], tmp_path / "out")
    with pytest.raises(FigureExportValidationError, match="at least one page"):
        ExportPlan("png", [1], [], tmp_path / "out")
    with pytest.raises(FigureExportValidationError, match="duplicates"):
        ExportPlan("png", [1, 1], [_page()], tmp_path / "out")
    with pytest.raises(FigureExportValidationError, match="unique"):
        ExportPlan(
            "png",
            [1],
            [_page("Same"), _page("Same")],
            tmp_path / "out",
        )
    with pytest.raises(FigureExportValidationError, match="ends in .pdf"):
        ExportPlan("pdf", [1], [_page()], tmp_path / "not-a-pdf")
    with pytest.raises(FigureExportValidationError, match="unknown figure format"):
        ExportPlan("jpeg", [1], [_page()], tmp_path / "out")
    with pytest.raises(FigureExportValidationError, match="must not be empty"):
        ExportPlan("png", [1], [_page()], "   ")
    with pytest.raises(FigureExportValidationError, match="must be a path"):
        ExportPlan("png", [1], [_page()], None)  # type: ignore[arg-type]


def test_page_templates_expand_unit_major_for_every_unit(tmp_path: Path) -> None:
    pages = (_page("First"), _page("Second", PlotKind.HD_LINE))
    plan = _plan(tmp_path / "out", units=(41, 7), pages=pages)

    generated = list(iter_generated_pages(plan))

    assert [(item.unit_id, item.page.name) for item in generated] == [
        (41, "First"),
        (41, "Second"),
        (7, "First"),
        (7, "Second"),
    ]
    assert [(item.unit_position, item.page_index) for item in generated] == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    ]


def test_renderer_supports_every_registered_plot_kind() -> None:
    page = ExportPage(
        "Every view",
        [PlotSpec(kind) for kind in PlotKind],
    )
    renderer = PillowFigureRenderer((1200, 900))

    preview = renderer.render_preview(3, page, data_provider=_data_for)

    assert preview.mode == "RGB"
    assert preview.size == (1200, 900)
    colors = preview.getcolors(maxcolors=2_000_000)
    assert colors is not None and len(colors) > 30


def test_page_header_uses_ascii_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    texts: list[str] = []
    original_text = ImageDraw.ImageDraw.text

    def recording_text(self, xy, text, *args, **kwargs):
        texts.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", recording_text)
    PillowFigureRenderer((500, 400)).render_page(
        7,
        ExportPage("Summary", [PlotSpec(PlotKind.RF_CARTESIAN, SCALAR_MATRIX)]),
    )

    assert "Unit 7 - Summary" in texts
    assert all("—" not in text for text in texts)


def test_plot_subtitle_is_rendered_as_muted_context_and_reserves_header_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subtitle = "0–100 ms · mean aggregation · smoothing 1"
    recorded: list[tuple[str, object, object]] = []
    original_text = ImageDraw.ImageDraw.text
    original_map = figure_export_module._draw_cartesian_map  # type: ignore[attr-defined]
    plot_boxes: list[tuple[int, int, int, int]] = []

    def recording_text(self, xy, text, *args, **kwargs):
        recorded.append((str(text), xy, kwargs.get("fill")))
        return original_text(self, xy, text, *args, **kwargs)

    def recording_map(draw, box, spec, *, rgb):
        plot_boxes.append(box)
        return original_map(draw, box, spec, rgb=rgb)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", recording_text)
    monkeypatch.setattr(
        figure_export_module,
        "_draw_cartesian_map",
        recording_map,
    )
    page = ExportPage(
        "RF",
        [
            PlotSpec(
                PlotKind.RF_CARTESIAN,
                SCALAR_MATRIX,
                options={"subtitle": subtitle},
            )
        ],
    )

    PillowFigureRenderer((600, 500)).render_page(7, page)

    subtitle_entries = [entry for entry in recorded if entry[0] == subtitle]
    assert len(subtitle_entries) == 1
    assert subtitle_entries[0][2] == "#64748b"
    assert plot_boxes and plot_boxes[0][1] > subtitle_entries[0][1][1]


def test_plot_subtitle_must_be_a_string() -> None:
    with pytest.raises(FigureExportValidationError, match="subtitle"):
        PlotSpec(
            PlotKind.RF_CARTESIAN,
            SCALAR_MATRIX,
            options={"subtitle": 100},
        )


def test_long_plot_subtitle_wraps_inside_a_narrow_panel_without_mutating_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subtitle = (
        "0–100 ms response window count aggregation spatial grouping 30×7 to "
        "15×4 smoothing radius 2 shared quantitative scale"
    )
    image = Image.new("RGB", (280, 440), "white")
    draw = ImageDraw.Draw(image)
    rendered: list[tuple[str, object, object, object]] = []
    plot_boxes: list[tuple[int, int, int, int]] = []
    original_text = ImageDraw.ImageDraw.text

    def recording_text(self, xy, text, *args, **kwargs):
        rendered.append((str(text), xy, kwargs.get("font"), kwargs.get("fill")))
        return original_text(self, xy, text, *args, **kwargs)

    def recording_map(_draw, box, _spec, *, rgb):
        assert rgb is False
        plot_boxes.append(box)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", recording_text)
    monkeypatch.setattr(
        figure_export_module,
        "_draw_cartesian_map",
        recording_map,
    )
    spec = PlotSpec(
        PlotKind.RF_CARTESIAN,
        SCALAR_MATRIX,
        options={"subtitle": subtitle},
    )

    PillowFigureRenderer._draw_plot(draw, (10, 10, 270, 430), spec)

    subtitle_lines = [entry for entry in rendered if entry[3] == "#64748b"]
    assert len(subtitle_lines) == 2
    assert subtitle_lines[-1][0].endswith("…")
    for text, xy, font, _fill in subtitle_lines:
        bounds = draw.textbbox(xy, text, font=font, anchor="lm")
        assert bounds[0] >= 22
        assert bounds[2] <= 258
    assert plot_boxes[0][1] > subtitle_lines[-1][1][1]
    assert spec.options["subtitle"] == subtitle


def test_plot_options_are_json_safe_and_deeply_immutable() -> None:
    source = {"ticks": [0.0, 1.0], "nested": {"unit": "ms"}}
    spec = PlotSpec(PlotKind.RF_CARTESIAN, SCALAR_MATRIX, options=source)

    source["ticks"].append(2.0)
    source["nested"]["unit"] = "seconds"
    assert spec.options["ticks"] == (0.0, 1.0)
    assert spec.options["nested"]["unit"] == "ms"
    with pytest.raises(TypeError):
        spec.options["nested"]["unit"] = "seconds"  # type: ignore[index]
    with pytest.raises(FigureExportValidationError, match="finite"):
        PlotSpec(
            PlotKind.RF_CARTESIAN,
            SCALAR_MATRIX,
            options={"vmin": float("nan")},
        )


def test_cartesian_maps_preserve_square_spatial_cells() -> None:
    image = Image.new("RGB", (420, 420), "white")
    draw = ImageDraw.Draw(image)
    red = [255, 0, 0]
    spec = PlotSpec(
        PlotKind.RGB_CARTESIAN,
        [[red, red, red, red], [red, red, red, red]],
    )

    figure_export_module._draw_cartesian_map(  # type: ignore[attr-defined]
        draw,
        (10, 10, 410, 410),
        spec,
        rgb=True,
    )

    red_pixels = [
        (x, y)
        for y in range(image.height)
        for x in range(image.width)
        if image.getpixel((x, y)) == (255, 0, 0)
    ]
    x_values = [point[0] for point in red_pixels]
    y_values = [point[1] for point in red_pixels]
    rendered_width = max(x_values) - min(x_values) + 1
    rendered_height = max(y_values) - min(y_values) + 1
    assert rendered_width / rendered_height == pytest.approx(2.0, rel=0.03)
    assert min(y_values) > 90
    assert max(y_values) < 330


def test_cartesian_singleton_y_uses_legacy_30_by_7_visual_aspect() -> None:
    image = Image.new("RGB", (420, 420), "white")
    red = [255, 0, 0]
    spec = PlotSpec(
        PlotKind.RGB_CARTESIAN,
        [[red] * 120],
        options={"show_axes": False},
    )

    figure_export_module._draw_cartesian_map(  # type: ignore[attr-defined]
        ImageDraw.Draw(image),
        (10, 10, 410, 410),
        spec,
        rgb=True,
    )

    red_pixels = [
        (x, y)
        for y in range(image.height)
        for x in range(image.width)
        if image.getpixel((x, y)) == (255, 0, 0)
    ]
    x_values = [point[0] for point in red_pixels]
    y_values = [point[1] for point in red_pixels]
    rendered_width = max(x_values) - min(x_values) + 1
    rendered_height = max(y_values) - min(y_values) + 1
    assert rendered_width / rendered_height == pytest.approx(30.0 / 7.0, rel=0.03)


def test_scalar_map_draws_physical_axes_units_and_quantitative_colorbar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    original_axes = figure_export_module._draw_cartesian_axes  # type: ignore[attr-defined]
    original_colorbar = figure_export_module._draw_scalar_colorbar  # type: ignore[attr-defined]

    def recording_axes(draw, box, x_values, y_values, *, x_unit, y_unit):
        observed["axes"] = (tuple(x_values), tuple(y_values), x_unit, y_unit)
        return original_axes(
            draw,
            box,
            x_values,
            y_values,
            x_unit=x_unit,
            y_unit=y_unit,
        )

    def recording_colorbar(draw, box, low, high, palette, unit):
        observed["colorbar"] = (low, high, palette, unit)
        return original_colorbar(draw, box, low, high, palette, unit)

    monkeypatch.setattr(figure_export_module, "_draw_cartesian_axes", recording_axes)
    monkeypatch.setattr(
        figure_export_module,
        "_draw_scalar_colorbar",
        recording_colorbar,
    )
    spec = PlotSpec(
        PlotKind.RF_CARTESIAN,
        {"matrix": [[1.0, 2.0], [3.0, 4.0]]},
        options={
            "x_values": [-10.0, 20.0],
            "y_values": [5.0, 15.0],
            "x_unit": "deg",
            "y_unit": "deg",
            "value_unit": "Hz",
            "palette": "inferno",
            "vmin": 0.0,
            "vmax": 8.0,
        },
    )

    image = Image.new("RGB", (600, 500), "white")
    figure_export_module._draw_cartesian_map(  # type: ignore[attr-defined]
        ImageDraw.Draw(image),
        (10, 10, 590, 490),
        spec,
        rgb=False,
    )

    assert observed["axes"] == ((-10.0, 20.0), (5.0, 15.0), "deg", "deg")
    assert observed["colorbar"] == (0.0, 8.0, "inferno", "Hz")


def test_scalar_map_rejects_coordinate_count_mismatch() -> None:
    page = ExportPage(
        "Bad coordinates",
        [
            PlotSpec(
                PlotKind.RF_CARTESIAN,
                [[1.0, 2.0]],
                options={"x_values": [1.0]},
            )
        ],
    )

    with pytest.raises(FigureExportValidationError, match="exactly 2"):
        PillowFigureRenderer((500, 400)).render_page(1, page)


def test_polar_map_centers_gui_visual_angle_span_on_twelve_oclock() -> None:
    image = Image.new("RGB", (420, 420), "white")
    draw = ImageDraw.Draw(image)
    spec = PlotSpec(
        PlotKind.RGB_POLAR,
        [[[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 0]]],
        options={"total_degrees": 180.0},
    )

    figure_export_module._draw_polar_map(  # type: ignore[attr-defined]
        draw,
        (10, 10, 410, 410),
        spec,
        rgb=True,
    )

    # GUI column zero starts on the upper-left edge and the whole 180-degree
    # stimulus span occupies the upper semicircle, centered on 12 o'clock.
    assert image.getpixel((99, 164)) == (255, 0, 0)
    assert image.getpixel((321, 164)) == (255, 255, 0)
    assert image.getpixel((210, 350)) == (255, 255, 255)


def test_render_errors_name_the_exact_unit_page_and_plot() -> None:
    page = ExportPage("Broken", [PlotSpec(PlotKind.RF_CARTESIAN, [[1], [1, 2]])])

    with pytest.raises(
        FigureExportValidationError,
        match=r"unit 9, page 'Broken', plot 1 \(rf\.cartesian\)",
    ):
        PillowFigureRenderer((500, 400)).render_page(9, page)


def test_data_provider_cannot_change_the_page_template_plot_kind() -> None:
    page = _page("RF", PlotKind.RF_CARTESIAN)

    with pytest.raises(FigureExportValidationError, match="changed plot kind"):
        PillowFigureRenderer((500, 400)).render_page(
            9,
            page,
            data_provider=lambda _unit, _spec: PlotSpec(PlotKind.HD_LINE, HD_CURVE),
        )


def test_timeline_validates_composite_curve_lengths() -> None:
    page = ExportPage(
        "Timeline",
        [
            PlotSpec(
                PlotKind.TIMELINE_CURRENT,
                {"times": [0.0, 0.1], "totals": [1.0], "frames": [SCALAR_MATRIX]},
            )
        ],
    )

    with pytest.raises(FigureExportValidationError, match="equal lengths"):
        PillowFigureRenderer((500, 400)).render_page(4, page)


def test_timeline_frames_honor_current_polar_settings() -> None:
    page = ExportPage(
        "Polar timeline",
        [
            PlotSpec(
                PlotKind.TIMELINE_CURRENT,
                _data_for(3, PlotSpec(PlotKind.TIMELINE_CURRENT)),
                options={
                    "polar": True,
                    "total_degrees": 180.0,
                    "reverse_rings": True,
                },
            )
        ],
    )

    preview = PillowFigureRenderer((600, 500)).render_page(3, page)

    assert preview.size == (600, 500)
    assert len(preview.getcolors(maxcolors=500_000) or []) > 20


def test_timeline_frames_share_one_gui_equivalent_color_bound(monkeypatch) -> None:
    observed_bounds: list[tuple[float, float]] = []
    original = figure_export_module._draw_cartesian_map  # type: ignore[attr-defined]

    def recording_draw(draw, box, spec, *, rgb):
        observed_bounds.append((spec.options["vmin"], spec.options["vmax"]))
        return original(draw, box, spec, rgb=rgb)

    monkeypatch.setattr(figure_export_module, "_draw_cartesian_map", recording_draw)
    page = ExportPage(
        "Timeline",
        [
            PlotSpec(
                PlotKind.TIMELINE_CURRENT,
                {
                    "times": [0.0, 1.0],
                    "totals": [1.0, 2.0],
                    "frames": [[[0.0, 1.0]], [[0.0, 100.0]]],
                },
            )
        ],
    )

    PillowFigureRenderer((500, 400)).render_page(3, page)

    assert observed_bounds == [(0.0, 100.0), (0.0, 100.0)]


@pytest.mark.parametrize(
    ("polar", "draw_name"),
    [
        (False, "_draw_cartesian_map"),
        (True, "_draw_polar_map"),
    ],
)
def test_real_shape_500_frame_timeline_is_one_quantitative_categorical_atlas(
    monkeypatch: pytest.MonkeyPatch,
    polar: bool,
    draw_name: str,
) -> None:
    frames = [
        [
            [float(index * 1000 + row * 30 + column) for column in range(30)]
            for row in range(7)
        ]
        for index in range(500)
    ]
    time_edges = [index * 0.01 for index in range(500)] + [4.995]
    times = [
        (time_edges[index] + time_edges[index + 1]) / 2.0
        for index in range(500)
    ]
    observed_options: list[Mapping[str, object]] = []
    observed_frames: list[tuple[int, int, float, float]] = []
    colorbar_units: list[str] = []
    texts: list[str] = []
    original_draw = getattr(figure_export_module, draw_name)
    original_colorbar = figure_export_module._draw_scalar_colorbar
    original_text = ImageDraw.ImageDraw.text

    def recording_draw(draw, box, spec, *, rgb):
        observed_options.append(spec.options)
        matrix = spec.data
        observed_frames.append(
            (len(matrix), len(matrix[0]), matrix[0][0], matrix[-1][-1])
        )
        return original_draw(draw, box, spec, rgb=rgb)

    def recording_colorbar(draw, box, low, high, palette, unit):
        colorbar_units.append(unit)
        return original_colorbar(draw, box, low, high, palette, unit)

    def recording_text(self, xy, text, *args, **kwargs):
        texts.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(figure_export_module, draw_name, recording_draw)
    monkeypatch.setattr(
        figure_export_module,
        "_draw_scalar_colorbar",
        recording_colorbar,
    )
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", recording_text)
    page = ExportPage(
        "500-bin timeline",
        [
            PlotSpec(
                PlotKind.TIMELINE_CURRENT,
                {
                    "frames": frames,
                    "times": times,
                    "time_edges": time_edges,
                    "time_unit": "s",
                    "totals": [float(index) for index in range(500)],
                },
                options={
                    "polar": polar,
                    "show_axes": True,
                    "show_colorbar": True,
                    "value_unit": "spikes/s",
                },
            )
        ],
    )

    image = PillowFigureRenderer().render_page(3, page)

    assert image.size == (1600, 1200)
    assert len(observed_options) == 500
    assert observed_frames[0] == (7, 30, 0.0, 209.0)
    assert observed_frames[-1] == (7, 30, 499000.0, 499209.0)
    assert all(options["show_axes"] is False for options in observed_options)
    assert all(options["show_colorbar"] is False for options in observed_options)
    assert colorbar_units == ["spikes/s"]
    assert any(
        text.startswith("categorical time-bin atlas; bounds [0, 4.995) s")
        for text in texts
    )
    assert any("equal-width tiles, row-major time order" in text for text in texts)


def test_timeline_selected_curve_uses_its_own_gui_axis() -> None:
    image = Image.new("RGB", (400, 240), "white")
    draw = ImageDraw.Draw(image)

    figure_export_module._draw_timeline_curves(  # type: ignore[attr-defined]
        draw,
        (20, 20, 380, 220),
        {
            "times": [0.0, 1.0],
            "totals": [0.0, 100.0],
            "selected": [0.0, 1.0],
        },
    )

    # The selected maximum reaches the top of its red axis even though the blue
    # all-position curve is two orders of magnitude larger.
    assert any(
        image.getpixel((x, y)) == (220, 38, 38)
        for x in range(275, 284)
        for y in range(40, 85)
    )


def test_timeline_places_curves_selection_and_active_marker_on_real_times() -> None:
    image = Image.new("RGB", (600, 320), "white")
    draw = ImageDraw.Draw(image)
    data = {
        "times": [0.0, 10.0, 100.0],
        "totals": [0.0, 0.0, 0.0],
        "selection_start_index": 1,
        "selection_end_index": 1,
        "active_index": 1,
    }

    figure_export_module._draw_timeline_curves(  # type: ignore[attr-defined]
        draw,
        (20, 20, 580, 300),
        data,
    )

    # Centers are 0, 10, and 100 ms, with midpoint-derived outer edges -5 and
    # 145 ms.  The 10-ms marker is therefore near x=103, not the index-spaced
    # midpoint near x=300.
    purple = (124, 58, 237)
    assert any(
        image.getpixel((x, 160)) == purple
        for x in range(100, 107)
    )
    assert all(
        image.getpixel((x, 160)) != purple
        for x in range(296, 305)
    )


def test_timeline_requires_strictly_increasing_real_times() -> None:
    page = ExportPage(
        "Timeline",
        [
            PlotSpec(
                PlotKind.TIMELINE_CURRENT,
                {"times": [0.0, 0.0], "totals": [1.0, 2.0]},
            )
        ],
    )

    with pytest.raises(FigureExportValidationError, match="strictly increasing"):
        PillowFigureRenderer((500, 400)).render_page(1, page)


def test_timeline_uses_explicit_edges_for_a_short_final_bin() -> None:
    image = Image.new("RGB", (600, 320), "white")
    figure_export_module._draw_timeline_curves(  # type: ignore[attr-defined]
        ImageDraw.Draw(image),
        (20, 20, 580, 300),
        {
            "times": [3.5, 10.5, 15.5],
            "time_edges": [0.0, 7.0, 14.0, 17.0],
            "totals": [0.0, 0.0, 0.0],
            "selection_start_index": 2,
            "selection_end_index": 2,
            "active_index": 2,
        },
    )

    # The short final group begins at the authoritative 14-ms edge.  With the
    # old center-derived midpoint it would incorrectly begin at 13 ms.
    purple = (124, 58, 237)
    assert any(
        image.getpixel((x, 160)) == purple
        for x in range(456, 463)
    )
    assert all(
        image.getpixel((x, 160)) != purple
        for x in range(405, 413)
    )


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        ([0.0, 7.0, 14.0], "one more"),
        ([0.0, 7.0, 7.0, 17.0], "strictly increasing"),
    ],
)
def test_timeline_validates_explicit_time_edges(
    edges: list[float],
    message: str,
) -> None:
    page = ExportPage(
        "Timeline",
        [
            PlotSpec(
                PlotKind.TIMELINE_CURRENT,
                {
                    "times": [3.5, 10.5, 15.5],
                    "time_edges": edges,
                    "totals": [1.0, 2.0, 3.0],
                },
            )
        ],
    )

    with pytest.raises(FigureExportValidationError, match=message):
        PillowFigureRenderer((500, 400)).render_page(1, page)


@pytest.mark.parametrize(
    "options",
    [
        {"total_degrees": 0},
        {"total_degrees": 361},
        {"ring_order": "sideways"},
        {"reverse_rings": "yes"},
        {"clockwise": "false"},
        {"show_axes": "yes"},
        {"inner_blank_rows": -1},
    ],
)
def test_polar_map_settings_are_strictly_validated(options: dict[str, object]) -> None:
    page = ExportPage(
        "Polar",
        [PlotSpec(PlotKind.RF_POLAR, SCALAR_MATRIX, options=options)],
    )

    with pytest.raises(FigureExportValidationError):
        PillowFigureRenderer((500, 400)).render_page(1, page)


def test_polar_map_can_preserve_the_gui_inner_blank_radius() -> None:
    page = ExportPage(
        "Polar with center",
        [
            PlotSpec(
                PlotKind.RF_POLAR,
                SCALAR_MATRIX,
                options={"inner_blank_rows": 4, "inner_color": "#f8fafc"},
            )
        ],
    )

    image = PillowFigureRenderer((500, 400)).render_page(1, page)

    assert image.getpixel((250, 230)) == (248, 250, 252)


def test_polar_singleton_y_spans_the_legacy_seven_row_radius() -> None:
    image = Image.new("RGB", (420, 420), "white")
    spec = PlotSpec(
        PlotKind.RGB_POLAR,
        [[[255, 0, 0]] * 120],
        options={
            "inner_blank_rows": 4,
            "show_axes": False,
            "show_colorbar": False,
        },
    )

    figure_export_module._draw_polar_map(  # type: ignore[attr-defined]
        ImageDraw.Draw(image),
        (10, 10, 410, 410),
        spec,
        rgb=True,
    )

    assert image.getpixel((270, 210)) == (248, 250, 252)
    assert image.getpixel((290, 210)) == (255, 0, 0)


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        (PlotKind.RF_POLAR, SCALAR_MATRIX),
        (
            PlotKind.RGB_POLAR,
            [
                [[255, 0, 0]] * 4,
                [[0, 255, 0]] * 4,
                [[0, 0, 255]] * 4,
            ],
        ),
    ],
)
def test_polar_maps_label_angle_and_radius_coordinates_inside_panel(
    kind: PlotKind,
    data: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[tuple[str, object, object, str]] = []
    original_text = ImageDraw.ImageDraw.text

    def recording_text(self, xy, text, *args, **kwargs):
        rendered.append(
            (str(text), xy, kwargs.get("font"), str(kwargs.get("anchor", "la")))
        )
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", recording_text)
    image = Image.new("RGB", (620, 520), "white")
    draw = ImageDraw.Draw(image)
    options = {
        "x_values": [-90.0, -30.0, 30.0, 90.0],
        "y_values": [5.0, 10.0, 15.0],
        "x_unit": "deg",
        "y_unit": "deg",
        "total_degrees": 180.0,
        "clockwise": False,
        "ring_order": "outer_to_inner",
    }
    if kind is PlotKind.RGB_POLAR:
        options["show_colorbar"] = False

    figure_export_module._draw_polar_map(  # type: ignore[attr-defined]
        draw,
        (10, 10, 610, 510),
        PlotSpec(kind, data, options=options),
        rgb=kind is PlotKind.RGB_POLAR,
    )

    texts = [entry[0] for entry in rendered]
    assert "-90 deg" in texts
    assert "30 deg" in texts
    assert "90 deg" in texts
    assert "angle: counterclockwise; rings: outer to inner" in texts
    assert any(text in {"5 deg", "15 deg"} for text in texts)
    for text, xy, font, anchor in rendered:
        if text not in {
            "-90 deg",
            "30 deg",
            "90 deg",
            "5 deg",
            "15 deg",
            "angle: counterclockwise; rings: outer to inner",
        }:
            continue
        bounds = draw.textbbox(xy, text, font=font, anchor=anchor)
        assert bounds[0] >= 10
        assert bounds[1] >= 10
        assert bounds[2] <= 610
        assert bounds[3] <= 510


@pytest.mark.parametrize("kind", [PlotKind.HD_LINE, PlotKind.HD_POLAR])
def test_hd_curves_render_quantitative_axes_inside_panel(
    kind: PlotKind,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[tuple[str, object, object, str]] = []
    original_text = ImageDraw.ImageDraw.text

    def recording_text(self, xy, text, *args, **kwargs):
        rendered.append(
            (str(text), xy, kwargs.get("font"), str(kwargs.get("anchor", "la")))
        )
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", recording_text)
    image = Image.new("RGB", (520, 420), "white")
    draw = ImageDraw.Draw(image)
    spec = PlotSpec(kind, HD_CURVE, options={"x_unit": "deg", "y_unit": "Hz"})
    if kind is PlotKind.HD_LINE:
        figure_export_module._draw_line(  # type: ignore[attr-defined]
            draw,
            (10, 10, 510, 410),
            spec,
        )
        required = {"x (deg)", "y (Hz)", "0", "300"}
    else:
        figure_export_module._draw_polar_line(  # type: ignore[attr-defined]
            draw,
            (10, 10, 510, 410),
            spec,
        )
        required = {"0°", "90°", "180°", "270°", "0 Hz", "7 Hz"}

    texts = {entry[0] for entry in rendered}
    assert required <= texts
    for text, xy, font, anchor in rendered:
        if text not in required:
            continue
        bounds = draw.textbbox(xy, text, font=font, anchor=anchor)
        assert bounds[0] >= 10
        assert bounds[1] >= 10
        assert bounds[2] <= 510
        assert bounds[3] <= 410


@pytest.mark.parametrize("kind", [PlotKind.DELAY_CARTESIAN, PlotKind.DELAY_POLAR])
def test_scalar_maps_render_none_and_nan_as_missing_cells(kind: PlotKind) -> None:
    page = ExportPage(
        "Sparse delay",
        [PlotSpec(kind, [[None, 1.0], [float("nan"), 3.0]])],
    )

    image = PillowFigureRenderer((500, 400)).render_page(1, page)

    assert image.size == (500, 400)
    assert len(image.getcolors(maxcolors=500_000) or []) > 5
    assert (237, 240, 243) in {color for _count, color in image.getcolors(maxcolors=500_000) or []}


def test_missing_cell_color_is_validated() -> None:
    page = ExportPage(
        "Bad color",
        [
            PlotSpec(
                PlotKind.RF_CARTESIAN,
                [[None]],
                options={"missing_color": "not-a-color"},
            )
        ],
    )

    with pytest.raises(FigureExportValidationError, match="valid color"):
        PillowFigureRenderer((500, 400)).render_page(1, page)


@pytest.mark.parametrize("kind", [PlotKind.RGB_CARTESIAN, PlotKind.RGB_POLAR])
def test_rgb_maps_render_none_as_neutral_no_data(kind: PlotKind) -> None:
    page = ExportPage(
        "Sparse RGB",
        [PlotSpec(kind, [[None, [255, 0, 0]], [[0, 255, 0], None]])],
    )

    image = PillowFigureRenderer((500, 400)).render_page(1, page)

    assert (237, 240, 243) in {color for _count, color in image.getcolors(maxcolors=500_000) or []}


def test_probe_layout_preserves_equal_physical_scale() -> None:
    image = Image.new("RGB", (600, 500), "white")
    spec = PlotSpec(
        PlotKind.PROBE_LAYOUT,
        {
            "points": [
                {"x": 0.0, "y": 0.0, "color": "#ff0000"},
                {"x": 20.0, "y": 100.0, "color": "#00ff00"},
            ]
        },
        options={"show_axes": False, "show_scale_bar": False},
    )

    figure_export_module._draw_probe_layout(  # type: ignore[attr-defined]
        ImageDraw.Draw(image),
        (10, 10, 590, 490),
        spec,
    )

    def center(color: tuple[int, int, int]) -> tuple[float, float]:
        pixels = [
            (x, y)
            for y in range(image.height)
            for x in range(image.width)
            if image.getpixel((x, y)) == color
        ]
        return (
            sum(x for x, _y in pixels) / len(pixels),
            sum(y for _x, y in pixels) / len(pixels),
        )

    red_x, red_y = center((255, 0, 0))
    green_x, green_y = center((0, 255, 0))
    assert abs(green_y - red_y) / abs(green_x - red_x) == pytest.approx(
        5.0,
        rel=0.03,
    )


def test_probe_layout_labels_axes_and_scale_bar_in_physical_units() -> None:
    class RecordingDraw:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.lines: list[tuple[object, ...]] = []

        def ellipse(self, *_args, **_kwargs) -> None:
            pass

        def rectangle(self, *_args, **_kwargs) -> None:
            pass

        def line(self, *args, **_kwargs) -> None:
            self.lines.append(args)

        def text(self, _xy, value, **_kwargs) -> None:
            self.texts.append(str(value))

    draw = RecordingDraw()
    figure_export_module._draw_probe_layout(  # type: ignore[attr-defined]
        draw,
        (10, 10, 590, 490),
        PlotSpec(PlotKind.PROBE_LAYOUT, PROBE_POINTS),
    )

    assert "x (µm)" in draw.texts
    assert any(text.endswith(" µm") for text in draw.texts)
    assert len(draw.lines) >= 3


def test_probe_layout_renders_nan_annotation_without_selected_unit_point() -> None:
    page = ExportPage(
        "Missing probe position",
        [
            PlotSpec(
                PlotKind.PROBE_LAYOUT,
                {
                    "points": [
                        {"x": 0.0, "y": 0.0, "label": "", "color": "#94a3b8"}
                    ],
                    "missingPosition": True,
                },
            )
        ],
    )

    image = PillowFigureRenderer((500, 400)).render_page(2, page)

    colors = image.getcolors(maxcolors=500_000)
    assert colors is not None
    rendered_colors = {color for _count, color in colors}
    assert (180, 35, 24) in rendered_colors
    assert (148, 163, 184) in rendered_colors


def test_probe_layout_renders_nan_annotation_without_any_finite_points() -> None:
    page = ExportPage(
        "Missing-only probe geometry",
        [
            PlotSpec(
                PlotKind.PROBE_LAYOUT,
                {"points": [], "missingPosition": True},
            )
        ],
    )

    image = PillowFigureRenderer((500, 400)).render_page(2, page)

    colors = image.getcolors(maxcolors=500_000)
    assert colors is not None
    assert (180, 35, 24) in {color for _count, color in colors}


@pytest.mark.parametrize(
    "plot",
    [
        PlotSpec(PlotKind.HD_LINE, options={"unavailable_message": "No HD data"}),
        PlotSpec(PlotKind.PROBE_LAYOUT, {"unavailable": "No probe geometry"}),
    ],
)
def test_unavailable_capabilities_render_an_explicit_placeholder(plot: PlotSpec) -> None:
    page = ExportPage("Capabilities", [plot])

    preview = PillowFigureRenderer((500, 400)).render_page(2, page)

    colors = preview.getcolors(maxcolors=500_000)
    assert colors is not None and len(colors) > 3


def test_png_export_is_multiunit_multipage_and_matches_live_preview(
    tmp_path: Path,
) -> None:
    pages = (
        _page("RF + delay", PlotKind.RF_CARTESIAN, PlotKind.DELAY_CARTESIAN),
        _page("HD", PlotKind.HD_LINE, PlotKind.HD_POLAR),
    )
    destination = tmp_path / "png-export"
    plan = _plan(destination, units=(3, 8), pages=pages)
    renderer = PillowFigureRenderer((700, 500))
    preview = render_live_preview(
        plan, 3, 0, data_provider=_data_for, renderer=renderer
    )

    result = export_figures(plan, data_provider=_data_for, renderer=renderer)

    assert result.format is FigureFormat.PNG
    assert result.page_count == 4
    assert [path.name for path in result.files] == [
        "0001__unit-3__page-01-RF-delay.png",
        "0001__unit-3__page-02-HD.png",
        "0002__unit-8__page-01-RF-delay.png",
        "0002__unit-8__page-02-HD.png",
    ]
    assert all(path.is_file() for path in result.files)
    with Image.open(result.files[0]) as exported:
        difference = ImageChops.difference(preview, exported.convert("RGB"))
        assert difference.getbbox() is None


@pytest.mark.parametrize("figure_format", [FigureFormat.PNG, FigureFormat.SVG])
def test_directory_manifest_records_versioned_provenance_metadata(
    tmp_path: Path,
    figure_format: FigureFormat,
) -> None:
    destination = tmp_path / figure_format.value
    metadata = {
        "source": {
            "path": "/recording/session.json",
            "sha256": "d" * 64,
        },
        "display": {
            "valueMode": "rate",
            "windowMs": [0.0, 100.0],
            "smoothRadius": 1,
        },
    }
    plan = ExportPlan(
        figure_format,
        (7,),
        (_page(),),
        destination,
        metadata=metadata,
    )

    export_figures(plan, data_provider=_data_for)

    manifest = json.loads(
        (destination / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["manifestVersion"] == 2
    assert manifest["spec"]["metadata"] == metadata


def test_verified_v1_directory_can_be_safely_migrated_on_overwrite(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "legacy"
    plan = _plan(destination)
    export_figures(plan, data_provider=_data_for)
    manifest_path = destination / "manifest.json"
    legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy["manifestVersion"] = 1
    legacy["spec"].pop("metadata")
    manifest_path.write_text(
        json.dumps(legacy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    replacement = ExportPlan(
        FigureFormat.PNG,
        (7,),
        (_page(),),
        destination,
        metadata={"migrated": True},
    )
    export_figures(replacement, data_provider=_data_for, overwrite=True)

    migrated = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert migrated["manifestVersion"] == 2
    assert migrated["spec"]["metadata"] == {"migrated": True}


@pytest.mark.parametrize(
    "tamper",
    [
        "extra-key",
        "nan-metadata",
        "nan-option",
        "invalid-subtitle",
        "boolean-version",
    ],
)
def test_overwrite_rejects_tampered_manifest_provenance_schema(
    tmp_path: Path,
    tamper: str,
) -> None:
    destination = tmp_path / tamper
    plan = ExportPlan(
        FigureFormat.PNG,
        (7,),
        (_page(),),
        destination,
        metadata={"source": {"sha256": "c" * 64}},
    )
    export_figures(plan, data_provider=_data_for)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if tamper == "extra-key":
        manifest["spec"]["unexpected"] = True
    elif tamper == "nan-metadata":
        manifest["spec"]["metadata"]["notFinite"] = float("nan")
    elif tamper == "nan-option":
        manifest["spec"]["pages"][0]["plots"][0]["options"]["bad"] = float(
            "nan"
        )
    elif tamper == "invalid-subtitle":
        manifest["spec"]["pages"][0]["plots"][0]["options"]["subtitle"] = 10
    else:
        manifest["manifestVersion"] = True
    manifest_path.write_text(
        json.dumps(manifest, allow_nan=True),
        encoding="utf-8",
    )

    with pytest.raises(figure_export_module.FigureExportError):
        export_figures(plan, data_provider=_data_for, overwrite=True)


def test_pdf_export_contains_one_page_per_unit_template_pair(tmp_path: Path) -> None:
    destination = tmp_path / "report.pdf"
    pages = (_page("RF"), _page("HD", PlotKind.HD_LINE))
    plan = _plan(
        destination,
        figure_format=FigureFormat.PDF,
        units=(1, 2, 3),
        pages=pages,
    )

    result = export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((500, 400)),
    )

    assert result.files == (destination,)
    assert result.page_count == 6
    contents = destination.read_bytes()
    assert contents.startswith(b"%PDF-")
    # The streaming writer emits one authoritative page tree.
    page_counts = re.findall(rb"/Type\s*/Pages\s*/Count\s+(\d+)", contents)
    assert page_counts and int(page_counts[-1]) == 6
    assert b"/Title (\xfe\xff" + "report".encode("utf-16-be") in contents
    reader = PdfReader(destination)
    assert len(reader.pages) == 6
    assert all(
        float(page.mediabox.width) > 0 and float(page.mediabox.height) > 0
        for page in reader.pages
    )


def test_pdf_embeds_lossless_preview_pixels_and_verifiable_recipe(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "lossless.pdf"
    metadata = {
        "source": {"path": "session.json", "sha256": "b" * 64},
        "display": {"windowMs": [-50.0, 100.0], "valueUnit": "Hz"},
    }
    plan = ExportPlan(
        FigureFormat.PDF,
        (7,),
        (_page(),),
        destination,
        metadata=metadata,
    )
    renderer = PillowFigureRenderer((500, 400))
    preview = render_live_preview(
        plan,
        7,
        0,
        data_provider=_data_for,
        renderer=renderer,
    )

    export_figures(plan, data_provider=_data_for, renderer=renderer)

    reader = PdfReader(destination)
    image_object = (
        reader.pages[0]["/Resources"]["/XObject"]["/image"].get_object()
    )
    assert image_object["/Filter"] == "/FlateDecode"
    assert image_object.get_data() == preview.convert("RGB").tobytes()
    document_text = reader.metadata["/RFMExportManifest"]
    digest = reader.metadata["/RFMExportManifestSHA256"]
    assert hashlib.sha256(document_text.encode("utf-8")).hexdigest() == digest
    document = json.loads(document_text)
    assert document["manifestVersion"] == 2
    assert document["producer"] == "rfmapping.python.figure-export"
    assert document["format"] == "pdf"
    assert document["spec"]["metadata"] == metadata
    assert document["rendering"]["encoding"] == "FlateDecode DeviceRGB 8-bit"
    preview.close()


def test_pdf_export_avoids_pillow_incremental_trailer_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pillow 10.2 raises ``trailer loop found`` after four PDF appends."""

    original_save = Image.Image.save
    append_attempts = 0

    def reject_fourth_incremental_append(self, fp, format=None, **params):
        nonlocal append_attempts
        if str(format).upper() == "PDF" and params.get("append"):
            append_attempts += 1
            if append_attempts >= 4:
                raise RuntimeError("trailer loop found")
        return original_save(self, fp, format=format, **params)

    monkeypatch.setattr(Image.Image, "save", reject_fourth_incremental_append)
    destination = tmp_path / "six-pages.pdf"
    plan = _plan(
        destination,
        figure_format=FigureFormat.PDF,
        units=(1, 2, 3),
        pages=(_page("RF"), _page("HD", PlotKind.HD_LINE)),
    )

    result = export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((500, 400)),
    )

    assert append_attempts == 0
    assert result.page_count == 6
    assert len(PdfReader(destination).pages) == 6


def test_concurrent_pdf_overwrite_allows_exactly_one_publication(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "concurrent.pdf"
    plan = _plan(destination, figure_format=FigureFormat.PDF)
    export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((320, 240)),
    )
    render_barrier = threading.Barrier(2)

    class BarrierRenderer(PillowFigureRenderer):
        def __init__(self, color: tuple[int, int, int]):
            super().__init__((320, 240))
            self.color = color

        def render_page(self, *args, **kwargs):
            render_barrier.wait(timeout=10)
            return Image.new("RGB", self.page_size, self.color)

    def overwrite(color: tuple[int, int, int]):
        try:
            result = export_figures(
                plan,
                renderer=BarrierRenderer(color),
                overwrite=True,
            )
        except figure_export_module.FigureExportError as exc:
            return "conflict", str(exc)
        return "success", result.page_count

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(overwrite, ((220, 30, 30), (30, 30, 220)))
        )

    assert [status for status, _detail in outcomes].count("success") == 1
    assert [status for status, _detail in outcomes].count("conflict") == 1
    conflict = next(detail for status, detail in outcomes if status == "conflict")
    assert "changed while pages were rendering" in conflict
    assert len(PdfReader(destination, strict=True).pages) == 1
    assert not list(tmp_path.glob(".concurrent.pdf.backup-*"))
    assert not list(tmp_path.glob(".concurrent.pdf.tmp-*"))


def test_pdf_replace_failure_before_mutation_cleans_hardlink_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "replace-before.pdf"
    plan = _plan(destination, figure_format=FigureFormat.PDF)
    export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((320, 240)),
    )
    original = destination.read_bytes()
    original_replace = figure_export_module.os.replace
    injected = False

    def fail_staged_replace(source, target, *args, **kwargs):
        nonlocal injected
        if (
            not injected
            and str(source).startswith(f".{destination.name}.tmp-")
            and target == destination.name
        ):
            injected = True
            raise OSError(errno.EIO, "injected replace-before-mutation failure")
        return original_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(figure_export_module.os, "replace", fail_staged_replace)
    with pytest.raises(OSError, match="replace-before-mutation"):
        export_figures(
            plan,
            data_provider=_shifted_provider(50.0),
            renderer=PillowFigureRenderer((320, 240)),
            overwrite=True,
        )

    assert injected
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".replace-before.pdf.backup-*"))
    assert not list(tmp_path.glob(".replace-before.pdf.tmp-*"))


def test_pdf_backup_fsync_failure_never_replaces_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "backup-fsync.pdf"
    plan = _plan(destination, figure_format=FigureFormat.PDF)
    export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((320, 240)),
    )
    original = destination.read_bytes()
    original_link = figure_export_module.os.link
    original_replace = figure_export_module.os.replace
    original_fsync = figure_export_module._fsync_directory_fd
    backup_linked = False
    injected = False
    destructive_replace_called = False

    def observe_link(source, target, *args, **kwargs):
        nonlocal backup_linked
        result = original_link(source, target, *args, **kwargs)
        if str(target).startswith(f".{destination.name}.backup-"):
            backup_linked = True
        return result

    def fail_backup_fsync(directory_fd):
        nonlocal injected
        if backup_linked and not injected:
            injected = True
            raise OSError(errno.EIO, "injected backup durability fsync failure")
        return original_fsync(directory_fd)

    def observe_replace(source, target, *args, **kwargs):
        nonlocal destructive_replace_called
        if str(source).startswith(f".{destination.name}.tmp-"):
            destructive_replace_called = True
        return original_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(figure_export_module.os, "link", observe_link)
    monkeypatch.setattr(figure_export_module.os, "replace", observe_replace)
    monkeypatch.setattr(
        figure_export_module,
        "_fsync_directory_fd",
        fail_backup_fsync,
    )
    with pytest.raises(OSError, match="backup durability fsync"):
        export_figures(
            plan,
            data_provider=_shifted_provider(50.0),
            renderer=PillowFigureRenderer((320, 240)),
            overwrite=True,
        )

    assert injected
    assert not destructive_replace_called
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".backup-fsync.pdf.backup-*"))
    assert not list(tmp_path.glob(".backup-fsync.pdf.tmp-*"))


def test_pdf_cifs_backup_verification_does_not_trust_link_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination_name = "destination.pdf"
    backup_name = ".destination.pdf.backup-test.pdf"
    destination = tmp_path / destination_name
    backup = tmp_path / backup_name
    destination.write_bytes(b"same verified PDF contents")
    os.link(destination, backup)
    original_lstat = figure_export_module._entry_lstat
    original_fstat = figure_export_module.os.fstat
    synthetic_inodes = {
        destination_name: destination.stat().st_ino + 10_000,
        backup_name: destination.stat().st_ino + 20_000,
    }
    fstat_call = 0
    observed_fd_link_counts: list[int] = []
    observed_path_link_counts: list[tuple[str, int]] = []

    def path_scoped_lstat(parent, name: str):
        result = original_lstat(parent, name)
        if result is None or name not in synthetic_inodes:
            return result
        values = list(result)
        values[1] = synthetic_inodes[name]
        values[3] = 1
        synthetic = os.stat_result(values)
        observed_path_link_counts.append((name, synthetic.st_nlink))
        return synthetic

    def descriptor_scoped_fstat(descriptor: int):
        nonlocal fstat_call
        result = original_fstat(descriptor)
        name = destination_name if fstat_call == 0 else backup_name
        fstat_call += 1
        values = list(result)
        values[1] = synthetic_inodes[name]
        values[3] = 1
        synthetic = os.stat_result(values)
        observed_fd_link_counts.append(synthetic.st_nlink)
        return synthetic

    with figure_export_module._open_parent_directory(tmp_path) as parent:
        monkeypatch.setattr(figure_export_module, "_entry_lstat", path_scoped_lstat)
        monkeypatch.setattr(
            figure_export_module.os,
            "fstat",
            descriptor_scoped_fstat,
        )
        destination_stat = path_scoped_lstat(parent, destination_name)
        backup_stat = path_scoped_lstat(parent, backup_name)
        assert destination_stat is not None and backup_stat is not None
        verified = figure_export_module._verified_pdf_backup_link(
            parent,
            destination_name,
            backup_name,
            expected_identity=figure_export_module._EntryIdentity.from_stat(
                destination_stat
            ),
            backup_stat=backup_stat,
        )

    assert verified
    assert observed_fd_link_counts == [1, 1]
    assert observed_path_link_counts
    assert all(link_count == 1 for _name, link_count in observed_path_link_counts)


def test_pdf_replace_mutates_then_raises_restores_old_cifs_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "mutated-eio.pdf"
    plan = _plan(destination, figure_format=FigureFormat.PDF)
    export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((320, 240)),
    )
    original = destination.read_bytes()
    original_stat = destination.stat()
    original_replace = figure_export_module.os.replace
    original_lstat = figure_export_module._entry_lstat
    mutated = False

    def mutate_then_fail(source, target, *args, **kwargs):
        nonlocal mutated
        if (
            not mutated
            and str(source).startswith(f".{destination.name}.tmp-")
            and target == destination.name
        ):
            original_replace(source, target, *args, **kwargs)
            mutated = True
            raise OSError(errno.EIO, "injected CIFS post-mutation EIO")
        return original_replace(source, target, *args, **kwargs)

    def cifs_path_scoped_destination_identity(parent, name: str):
        result = original_lstat(parent, name)
        if mutated and result is not None and name == destination.name:
            values = list(result)
            values[1] = original_stat.st_ino
            values[2] = original_stat.st_dev
            return os.stat_result(values)
        return result

    monkeypatch.setattr(figure_export_module.os, "replace", mutate_then_fail)
    monkeypatch.setattr(
        figure_export_module,
        "_entry_lstat",
        cifs_path_scoped_destination_identity,
    )
    with pytest.raises(OSError, match="post-mutation EIO"):
        export_figures(
            plan,
            data_provider=_shifted_provider(50.0),
            renderer=PillowFigureRenderer((320, 240)),
            overwrite=True,
        )

    assert mutated
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".mutated-eio.pdf.backup-*"))
    assert not list(tmp_path.glob(".mutated-eio.pdf.tmp-*"))


def test_pdf_backup_unlink_failure_before_mutation_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "unlink-before.pdf"
    plan = _plan(destination, figure_format=FigureFormat.PDF)
    export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((320, 240)),
    )
    original = destination.read_bytes()
    original_unlink = figure_export_module.os.unlink
    injected = False

    def fail_backup_unlink(path, *args, **kwargs):
        nonlocal injected
        if not injected and str(path).startswith(f".{destination.name}.backup-"):
            injected = True
            raise OSError(errno.EIO, "injected backup unlink failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(figure_export_module.os, "unlink", fail_backup_unlink)
    with pytest.raises(
        figure_export_module.FigureExportError,
        match="cleanup failed; publication was rolled back",
    ):
        export_figures(
            plan,
            data_provider=_shifted_provider(50.0),
            renderer=PillowFigureRenderer((320, 240)),
            overwrite=True,
        )

    assert injected
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".unlink-before.pdf.backup-*"))
    assert not list(tmp_path.glob(".unlink-before.pdf.tmp-*"))


@pytest.mark.parametrize("failure_point", ["unlink-after-mutation", "cleanup-fsync"])
def test_pdf_post_commit_cleanup_failure_reports_success_without_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    destination = tmp_path / f"post-commit-{failure_point}.pdf"
    plan = _plan(destination, figure_format=FigureFormat.PDF)
    export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((320, 240)),
    )
    original = destination.read_bytes()
    original_unlink = figure_export_module.os.unlink
    original_fsync = figure_export_module._fsync_directory_fd
    backup_removed = False
    injected = False

    class ReplacementRenderer(PillowFigureRenderer):
        def render_page(self, *args, **kwargs):
            return Image.new("RGB", self.page_size, (12, 34, 210))

    def observe_backup_unlink(path, *args, **kwargs):
        nonlocal backup_removed, injected
        is_backup = str(path).startswith(f".{destination.name}.backup-")
        result = original_unlink(path, *args, **kwargs)
        if is_backup:
            backup_removed = True
            if failure_point == "unlink-after-mutation" and not injected:
                injected = True
                raise OSError(errno.EIO, "injected post-unlink failure")
        return result

    def fail_cleanup_fsync(directory_fd):
        nonlocal injected
        if failure_point == "cleanup-fsync" and backup_removed and not injected:
            injected = True
            raise OSError(errno.EIO, "injected cleanup fsync failure")
        return original_fsync(directory_fd)

    monkeypatch.setattr(figure_export_module.os, "unlink", observe_backup_unlink)
    monkeypatch.setattr(
        figure_export_module,
        "_fsync_directory_fd",
        fail_cleanup_fsync,
    )
    result = export_figures(
        plan,
        renderer=ReplacementRenderer((320, 240)),
        overwrite=True,
    )

    assert injected
    assert result.page_count == 1
    assert destination.read_bytes() != original
    assert len(PdfReader(destination, strict=True).pages) == 1
    assert not list(tmp_path.glob(f".{destination.name}.backup-*"))
    assert not list(tmp_path.glob(f".{destination.name}.tmp-*"))


@pytest.mark.parametrize(
    "failure_errno",
    [errno.EIO, getattr(errno, "ENOTSUP", errno.EOPNOTSUPP)],
)
def test_publish_lock_acquire_failure_closes_fd_without_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_errno: int,
) -> None:
    calls: list[int] = []
    lock_fd: int | None = None

    def fail_lock(descriptor: int, operation: int) -> None:
        nonlocal lock_fd
        calls.append(operation)
        lock_fd = descriptor
        raise OSError(failure_errno, "injected flock acquisition failure")

    monkeypatch.setattr(figure_export_module.fcntl, "flock", fail_lock)
    with figure_export_module._open_parent_directory(tmp_path) as parent:
        with pytest.raises(OSError) as captured:
            with figure_export_module._directory_publish_lock(parent, "report.pdf"):
                raise AssertionError("lock body must not run")

    assert captured.value.errno == failure_errno
    assert calls == [figure_export_module.fcntl.LOCK_EX]
    assert lock_fd is not None
    with pytest.raises(OSError) as closed:
        os.fstat(lock_fd)
    assert closed.value.errno == errno.EBADF


def test_svg_export_uses_valid_embedded_png_with_preview_parity(tmp_path: Path) -> None:
    destination = tmp_path / "svg-export"
    plan = _plan(
        destination,
        figure_format=FigureFormat.SVG,
        pages=(_page("Polar", PlotKind.RF_POLAR),),
    )
    renderer = PillowFigureRenderer((520, 360))
    preview = render_live_preview(
        plan, 7, 0, data_provider=_data_for, renderer=renderer
    )

    result = export_figures(plan, data_provider=_data_for, renderer=renderer)

    assert result.page_count == 1
    svg = result.files[0].read_text(encoding="utf-8")
    root = ET.fromstring(svg)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    match = re.search(r'href="data:image/png;base64,([A-Za-z0-9+/=]+)"', svg)
    assert match is not None
    with Image.open(io.BytesIO(base64.b64decode(match.group(1)))) as embedded:
        difference = ImageChops.difference(preview, embedded.convert("RGB"))
        assert difference.getbbox() is None
    assert "embedded data URI" in SVG_RENDERING_CONTRACT


@pytest.mark.parametrize(
    ("figure_format", "destination_name"),
    [(FigureFormat.PNG, "existing"), (FigureFormat.SVG, "existing"), (FigureFormat.PDF, "existing.pdf")],
)
def test_export_never_silently_overwrites(
    tmp_path: Path,
    figure_format: FigureFormat,
    destination_name: str,
) -> None:
    destination = tmp_path / destination_name
    if figure_format is FigureFormat.PDF:
        destination.write_bytes(b"keep me")
    else:
        destination.mkdir()
        (destination / "keep.txt").write_text("keep me", encoding="utf-8")
    plan = _plan(destination, figure_format=figure_format)

    with pytest.raises(DestinationExistsError, match="overwrite=True"):
        export_figures(plan, data_provider=_data_for)

    if figure_format is FigureFormat.PDF:
        assert destination.read_bytes() == b"keep me"
    else:
        assert (destination / "keep.txt").read_text(encoding="utf-8") == "keep me"


def test_explicit_overwrite_replaces_the_complete_destination(tmp_path: Path) -> None:
    destination = tmp_path / "figures"
    plan = _plan(destination)
    export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((500, 400)),
    )

    result = export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((500, 400)),
        overwrite=True,
    )

    assert result.files[0].is_file()
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["producer"] == "rfmapping.python.figure-export"
    assert set(path.name for path in destination.iterdir()) == {
        "manifest.json",
        result.files[0].name,
    }


@pytest.mark.parametrize(
    ("figure_format", "name"),
    [(FigureFormat.PNG, "shared"), (FigureFormat.SVG, "shared-svg")],
)
def test_directory_exports_publish_group_shared_safe_modes_after_validation(
    tmp_path: Path,
    figure_format: FigureFormat,
    name: str,
) -> None:
    destination = tmp_path / name
    plan = _plan(destination, figure_format=figure_format)

    result = export_figures(plan, data_provider=_data_for)
    os.chmod(destination, 0o700)
    for member in destination.iterdir():
        os.chmod(member, 0o600)
    result = export_figures(plan, data_provider=_data_for, overwrite=True)

    assert stat.S_IMODE(destination.stat().st_mode) == 0o770
    assert stat.S_IMODE((destination / "manifest.json").stat().st_mode) == 0o660
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o660 for path in result.files)
    lock = tmp_path / f".{name}.figure-export.lock"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o660


def test_pdf_publishes_group_shared_safe_mode_after_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "shared.pdf"
    plan = _plan(destination, figure_format=FigureFormat.PDF)

    export_figures(plan, data_provider=_data_for)
    os.chmod(destination, 0o600)
    export_figures(plan, data_provider=_data_for, overwrite=True)

    assert stat.S_IMODE(destination.stat().st_mode) == 0o660
    lock = tmp_path / ".shared.pdf.figure-export.lock"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o660


def test_directory_manifest_accumulates_only_small_integrity_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / "many-pages"
    plan = _plan(
        destination,
        units=(1, 2, 3),
        pages=(_page("A"), _page("B")),
    )
    original = figure_export_module._manifest_document  # type: ignore[attr-defined]
    observed: list[tuple[int, str]] = []

    def inspect_integrity(plan_arg, generated_pages, rendered_integrity):
        observed.extend(rendered_integrity)
        assert all(
            isinstance(item, tuple)
            and isinstance(item[0], int)
            and isinstance(item[1], str)
            for item in rendered_integrity
        )
        assert not any(isinstance(item, (bytes, bytearray)) for item in rendered_integrity)
        return original(plan_arg, generated_pages, rendered_integrity)

    monkeypatch.setattr(figure_export_module, "_manifest_document", inspect_integrity)

    export_figures(plan, data_provider=_data_for, renderer=PillowFigureRenderer((500, 400)))

    assert len(observed) == 6
    assert all(size > 0 and re.fullmatch(r"[0-9a-f]{64}", digest) for size, digest in observed)


def test_overwrite_refuses_raw_session_directory_even_when_explicit(tmp_path: Path) -> None:
    destination = tmp_path / "recording-session"
    destination.mkdir()
    raw_source = destination / "spike_clusters.npy"
    raw_source.write_bytes(b"source-of-truth")

    with pytest.raises(figure_export_module.FigureExportError, match="unverified"):
        export_figures(
            _plan(destination),
            data_provider=_data_for,
            renderer=PillowFigureRenderer((500, 400)),
            overwrite=True,
        )

    assert raw_source.read_bytes() == b"source-of-truth"
    assert set(destination.iterdir()) == {raw_source}


def test_directory_publish_does_not_clobber_empty_destination_created_mid_render(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "raced-destination"
    plan = _plan(destination)

    class RacingRenderer(PillowFigureRenderer):
        def render_page(self, *args, **kwargs):
            destination.mkdir()
            return super().render_page(*args, **kwargs)

    with pytest.raises(DestinationExistsError):
        export_figures(
            plan,
            data_provider=_data_for,
            renderer=RacingRenderer((500, 400)),
        )

    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert not list(tmp_path.glob(f".{destination.name}.tmp-*"))


def test_directory_overwrite_uses_one_atomic_exchange(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / "figures"
    plan = _plan(destination)
    export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((500, 400)),
    )
    original = figure_export_module._atomic_directory_rename  # type: ignore[attr-defined]
    exchange_observations: list[tuple[bool, bool, bool, bool]] = []

    def observing_rename(
        staged: Path,
        target: Path,
        *,
        exchange: bool,
        parent_fd: int | None = None,
    ) -> None:
        before = (staged.is_dir(), target.is_dir())
        original(staged, target, exchange=exchange, parent_fd=parent_fd)
        after = (staged.is_dir(), target.is_dir())
        if exchange:
            exchange_observations.append((*before, *after))

    monkeypatch.setattr(
        figure_export_module,
        "_atomic_directory_rename",
        observing_rename,
    )

    export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((500, 400)),
        overwrite=True,
    )

    assert exchange_observations == [(True, True, True, True)]
    assert (destination / "manifest.json").is_file()


def test_directory_overwrite_cifs_fallback_cleans_journal_and_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / "figures"
    plan = _plan(destination)
    export_figures(plan, data_provider=_data_for, renderer=PillowFigureRenderer((500, 400)))
    original = figure_export_module._atomic_directory_rename  # type: ignore[attr-defined]

    def reject_exchange(
        staged: Path,
        target: Path,
        *,
        exchange: bool,
        parent_fd: int | None = None,
    ) -> None:
        if exchange:
            raise OSError(errno.EINVAL, "CIFS does not support RENAME_EXCHANGE")
        original(staged, target, exchange=False, parent_fd=parent_fd)

    monkeypatch.setattr(figure_export_module, "_atomic_directory_rename", reject_exchange)

    result = export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((500, 400)),
        overwrite=True,
    )

    assert result.files[0].is_file()
    assert not (tmp_path / ".figures.figure-export-journal.json").exists()
    assert not list(tmp_path.glob(".figures.backup-*"))
    assert not list(tmp_path.glob(".figures.tmp-*"))


def test_directory_overwrite_cifs_fallback_restores_old_on_publish_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / "figures"
    plan = _plan(destination)
    first = export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((500, 400)),
    )
    old_page = first.files[0].read_bytes()
    old_manifest = (destination / "manifest.json").read_bytes()
    original = figure_export_module._atomic_directory_rename  # type: ignore[attr-defined]
    failed_publish = False

    def fail_new_publish(
        staged: Path,
        target: Path,
        *,
        exchange: bool,
        parent_fd: int | None = None,
    ) -> None:
        nonlocal failed_publish
        if exchange:
            raise OSError(errno.EINVAL, "CIFS does not support RENAME_EXCHANGE")
        if (
            not failed_publish
            and staged.name.startswith(".figures.tmp-")
            and target.name == destination.name
        ):
            failed_publish = True
            raise OSError(errno.EIO, "simulated publish failure")
        original(staged, target, exchange=False, parent_fd=parent_fd)

    monkeypatch.setattr(figure_export_module, "_atomic_directory_rename", fail_new_publish)

    with pytest.raises(OSError, match="simulated publish failure"):
        export_figures(
            plan,
            data_provider=_data_for,
            renderer=PillowFigureRenderer((500, 400)),
            overwrite=True,
        )

    assert failed_publish
    assert first.files[0].read_bytes() == old_page
    assert (destination / "manifest.json").read_bytes() == old_manifest
    assert not (tmp_path / ".figures.figure-export-journal.json").exists()
    assert not list(tmp_path.glob(".figures.backup-*"))
    assert not list(tmp_path.glob(".figures.tmp-*"))


def _shifted_provider(offset: float):
    def provider(unit_id: int, spec: PlotSpec):
        data = _data_for(unit_id, spec)
        if spec.kind is PlotKind.RF_CARTESIAN:
            return [[float(value) + offset for value in row] for row in data]
        return data

    return provider


def _write_interrupted_publish_journal(
    parent: Path,
    destination_name: str,
    staged_name: str,
    backup_name: str,
    state: str,
) -> None:
    old_path = parent / backup_name
    new_path = parent / staged_name
    if not new_path.exists():
        new_path = parent / destination_name
    old_identity = figure_export_module._EntryIdentity.from_stat(  # type: ignore[attr-defined]
        old_path.stat()
    )
    new_identity = figure_export_module._EntryIdentity.from_stat(  # type: ignore[attr-defined]
        new_path.stat()
    )
    with figure_export_module._open_parent_directory(parent) as handle:  # type: ignore[attr-defined]
        figure_export_module._write_publish_journal(  # type: ignore[attr-defined]
            handle,
            destination_name,
            staged_name=staged_name,
            backup_name=backup_name,
            state=state,
            old_identity=old_identity,
            new_identity=new_identity,
        )


def test_interrupted_fallback_recovers_old_directory(tmp_path: Path) -> None:
    destination = tmp_path / "figures"
    replacement = tmp_path / "replacement"
    old_result = export_figures(
        _plan(destination),
        data_provider=_shifted_provider(0.0),
        renderer=PillowFigureRenderer((500, 400)),
    )
    old_contents = old_result.files[0].read_bytes()
    export_figures(
        _plan(replacement),
        data_provider=_shifted_provider(50.0),
        renderer=PillowFigureRenderer((500, 400)),
    )
    backup_name = ".figures.backup-" + "a" * 32
    staged_name = ".figures.tmp-" + "b" * 32
    destination.rename(tmp_path / backup_name)
    replacement.rename(tmp_path / staged_name)
    _write_interrupted_publish_journal(
        tmp_path,
        destination.name,
        staged_name,
        backup_name,
        "old_moved",
    )

    figure_export_module._recover_directory_publish(destination)  # type: ignore[attr-defined]

    assert old_result.files[0].read_bytes() == old_contents
    assert not (tmp_path / staged_name).exists()
    assert not (tmp_path / backup_name).exists()
    assert not (tmp_path / ".figures.figure-export-journal.json").exists()


def test_interrupted_fallback_finishes_valid_new_directory(tmp_path: Path) -> None:
    destination = tmp_path / "figures"
    replacement = tmp_path / "replacement"
    export_figures(
        _plan(destination),
        data_provider=_shifted_provider(0.0),
        renderer=PillowFigureRenderer((500, 400)),
    )
    replacement_result = export_figures(
        _plan(replacement),
        data_provider=_shifted_provider(50.0),
        renderer=PillowFigureRenderer((500, 400)),
    )
    new_contents = replacement_result.files[0].read_bytes()
    backup_name = ".figures.backup-" + "c" * 32
    staged_name = ".figures.tmp-" + "d" * 32
    destination.rename(tmp_path / backup_name)
    replacement.rename(destination)
    _write_interrupted_publish_journal(
        tmp_path,
        destination.name,
        staged_name,
        backup_name,
        "new_moved",
    )

    figure_export_module._recover_directory_publish(destination)  # type: ignore[attr-defined]

    assert next(destination.glob("*.png")).read_bytes() == new_contents
    assert not (tmp_path / backup_name).exists()
    assert not (tmp_path / ".figures.figure-export-journal.json").exists()


def test_recovery_journal_identity_gate_never_moves_raw_destination(
    tmp_path: Path,
) -> None:
    old_export = tmp_path / "old-export"
    new_export = tmp_path / "new-export"
    export_figures(_plan(old_export), data_provider=_shifted_provider(0.0))
    export_figures(_plan(new_export), data_provider=_shifted_provider(25.0))
    destination = tmp_path / "figures"
    destination.mkdir()
    raw_source = destination / "spike_clusters.npy"
    raw_source.write_bytes(b"source-of-truth")
    backup_name = ".figures.backup-" + "e" * 32
    staged_name = ".figures.tmp-" + "f" * 32
    old_export.rename(tmp_path / backup_name)
    new_export.rename(tmp_path / staged_name)
    _write_interrupted_publish_journal(
        tmp_path,
        destination.name,
        staged_name,
        backup_name,
        "old_moved",
    )

    with pytest.raises(figure_export_module.FigureExportError, match="identity"):
        figure_export_module._recover_directory_publish(destination)  # type: ignore[attr-defined]

    assert raw_source.read_bytes() == b"source-of-truth"
    assert (tmp_path / backup_name).is_dir()
    assert (tmp_path / staged_name).is_dir()
    assert (tmp_path / ".figures.figure-export-journal.json").is_file()


def test_overwrite_rejects_tampered_export_but_allows_new_recipe(tmp_path: Path) -> None:
    destination = tmp_path / "figures"
    plan = _plan(destination)
    result = export_figures(plan, data_provider=_data_for)
    result.files[0].write_bytes(b"tampered")

    with pytest.raises(figure_export_module.FigureExportError, match="size|checksum"):
        export_figures(plan, data_provider=_data_for, overwrite=True)

    assert result.files[0].read_bytes() == b"tampered"
    # A different recipe may intentionally replace a complete, verified output.
    other = tmp_path / "other"
    export_figures(_plan(other), data_provider=_data_for)
    changed = _plan(other, pages=(_page("Different", PlotKind.HD_LINE),))
    changed_result = export_figures(changed, data_provider=_data_for, overwrite=True)
    assert changed_result.files[0].is_file()
    manifest = json.loads((other / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["spec"]["pages"][0]["name"] == "Different"


@pytest.mark.parametrize("replacement_kind", ["symlink", "directory"])
def test_destination_replacement_during_render_fails_closed(
    tmp_path: Path,
    replacement_kind: str,
) -> None:
    destination = tmp_path / "figures"
    plan = _plan(destination)
    export_figures(plan, data_provider=_data_for)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "source.bin"
    sentinel.write_bytes(b"do-not-touch")

    class ReplacingRenderer(PillowFigureRenderer):
        replaced = False

        def render_page(self, *args, **kwargs):
            if not self.replaced:
                self.replaced = True
                shutil.rmtree(destination)
                if replacement_kind == "symlink":
                    destination.symlink_to(outside, target_is_directory=True)
                else:
                    destination.mkdir()
                    (destination / "raw.bin").write_bytes(b"raw")
            return super().render_page(*args, **kwargs)

    with pytest.raises(figure_export_module.FigureExportError):
        export_figures(
            plan,
            data_provider=_data_for,
            renderer=ReplacingRenderer((500, 400)),
            overwrite=True,
        )

    assert sentinel.read_bytes() == b"do-not-touch"
    if replacement_kind == "directory":
        assert (destination / "raw.bin").read_bytes() == b"raw"


def test_parent_directory_replacement_during_render_fails_closed(tmp_path: Path) -> None:
    parent = tmp_path / "export-parent"
    parent.mkdir()
    moved_parent = tmp_path / "original-parent"
    destination = parent / "figures"

    class ReplacingParentRenderer(PillowFigureRenderer):
        replaced = False

        def render_page(self, *args, **kwargs):
            if not self.replaced:
                self.replaced = True
                parent.rename(moved_parent)
                parent.mkdir()
            return super().render_page(*args, **kwargs)

    with pytest.raises(figure_export_module.FigureExportError, match="parent.*replaced"):
        export_figures(
            _plan(destination),
            data_provider=_data_for,
            renderer=ReplacingParentRenderer((500, 400)),
        )

    assert not destination.exists()
    assert not list(moved_parent.glob(".figures.tmp-*"))


def test_pdf_parent_replacement_during_render_rolls_back_new_file(tmp_path: Path) -> None:
    parent = tmp_path / "pdf-parent"
    parent.mkdir()
    moved_parent = tmp_path / "original-pdf-parent"
    destination = parent / "report.pdf"

    class ReplacingParentRenderer(PillowFigureRenderer):
        replaced = False

        def render_page(self, *args, **kwargs):
            if not self.replaced:
                self.replaced = True
                parent.rename(moved_parent)
                parent.mkdir()
            return super().render_page(*args, **kwargs)

    with pytest.raises(figure_export_module.FigureExportError, match="parent.*replaced"):
        export_figures(
            _plan(destination, figure_format=FigureFormat.PDF),
            data_provider=_data_for,
            renderer=ReplacingParentRenderer((500, 400)),
        )

    assert not destination.exists()
    assert not (moved_parent / destination.name).exists()
    assert not list(moved_parent.glob(".report.pdf.tmp-*"))


@pytest.mark.parametrize(
    ("figure_format", "destination_name"),
    [(FigureFormat.PNG, "failed"), (FigureFormat.PDF, "failed.pdf")],
)
def test_render_failure_leaves_no_partial_destination_or_staging_files(
    tmp_path: Path,
    figure_format: FigureFormat,
    destination_name: str,
) -> None:
    destination = tmp_path / destination_name
    plan = _plan(
        destination,
        figure_format=figure_format,
        units=(1, 2),
        pages=(_page("RF"),),
    )

    def fail_on_second_unit(unit_id: int, spec: PlotSpec):
        if unit_id == 2:
            raise RuntimeError("provider failed")
        return _data_for(unit_id, spec)

    with pytest.raises(RuntimeError, match="provider failed"):
        export_figures(
            plan,
            data_provider=fail_on_second_unit,
            renderer=PillowFigureRenderer((500, 400)),
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(f".{destination.name}.tmp-*"))


@pytest.mark.parametrize(
    ("figure_format", "destination_name"),
    [(FigureFormat.PNG, "new-directory"), (FigureFormat.PDF, "new.pdf")],
)
def test_before_publish_failure_never_exposes_a_new_destination(
    tmp_path: Path,
    figure_format: FigureFormat,
    destination_name: str,
) -> None:
    destination = tmp_path / destination_name
    plan = _plan(destination, figure_format=figure_format)
    calls = 0

    def reject_publish() -> None:
        nonlocal calls
        calls += 1
        assert not destination.exists()
        raise RuntimeError("source changed before publish")

    with pytest.raises(RuntimeError, match="source changed"):
        export_figures(
            plan,
            data_provider=_data_for,
            before_publish=reject_publish,
        )

    assert calls == 1
    assert not destination.exists()
    assert not list(tmp_path.glob(f".{destination.name}.tmp-*"))


@pytest.mark.parametrize(
    ("figure_format", "destination_name"),
    [(FigureFormat.PNG, "old-directory"), (FigureFormat.PDF, "old.pdf")],
)
def test_before_publish_failure_preserves_verified_overwrite_target(
    tmp_path: Path,
    figure_format: FigureFormat,
    destination_name: str,
) -> None:
    destination = tmp_path / destination_name
    plan = _plan(destination, figure_format=figure_format)
    export_figures(plan, data_provider=_data_for)
    if figure_format is FigureFormat.PDF:
        original = destination.read_bytes()
    else:
        original = {
            member.name: member.read_bytes()
            for member in destination.iterdir()
        }

    with pytest.raises(RuntimeError, match="source changed"):
        export_figures(
            plan,
            data_provider=_data_for,
            overwrite=True,
            before_publish=lambda: (_ for _ in ()).throw(
                RuntimeError("source changed before publish")
            ),
        )

    if figure_format is FigureFormat.PDF:
        assert destination.read_bytes() == original
    else:
        assert {
            member.name: member.read_bytes()
            for member in destination.iterdir()
        } == original
    assert not list(tmp_path.glob(f".{destination.name}.tmp-*"))


def test_pdf_overwrite_rejects_same_inode_content_mutation_before_commit(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "same-inode-content.pdf"
    plan = _plan(destination, figure_format=FigureFormat.PDF)
    export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((320, 240)),
    )
    original = destination.read_bytes()
    original_inode = destination.stat().st_ino
    external = bytes(byte ^ 0x5A for byte in original)

    def mutate_existing_file() -> None:
        destination.write_bytes(external)
        assert destination.stat().st_ino == original_inode

    with pytest.raises(
        figure_export_module.FigureExportError,
        match="changed while pages were rendering",
    ):
        export_figures(
            plan,
            data_provider=_shifted_provider(50.0),
            renderer=PillowFigureRenderer((320, 240)),
            overwrite=True,
            before_publish=mutate_existing_file,
        )

    assert destination.read_bytes() == external
    assert destination.stat().st_ino == original_inode
    assert not list(tmp_path.glob(f".{destination.name}.tmp-*"))
    assert not list(tmp_path.glob(f".{destination.name}.backup-*"))


def test_pdf_overwrite_rejects_same_inode_metadata_mutation_before_commit(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "same-inode-metadata.pdf"
    plan = _plan(destination, figure_format=FigureFormat.PDF)
    export_figures(
        plan,
        data_provider=_data_for,
        renderer=PillowFigureRenderer((320, 240)),
    )
    original = destination.read_bytes()
    original_stat = destination.stat()
    changed_mtime_ns = original_stat.st_mtime_ns + 2_000_000_000

    def mutate_existing_metadata() -> None:
        os.utime(
            destination,
            ns=(original_stat.st_atime_ns, changed_mtime_ns),
        )
        assert destination.stat().st_ino == original_stat.st_ino

    with pytest.raises(
        figure_export_module.FigureExportError,
        match="changed while pages were rendering",
    ):
        export_figures(
            plan,
            data_provider=_shifted_provider(50.0),
            renderer=PillowFigureRenderer((320, 240)),
            overwrite=True,
            before_publish=mutate_existing_metadata,
        )

    assert destination.read_bytes() == original
    changed_stat = destination.stat()
    assert changed_stat.st_ino == original_stat.st_ino
    assert changed_stat.st_mtime_ns == changed_mtime_ns
    assert not list(tmp_path.glob(f".{destination.name}.tmp-*"))
    assert not list(tmp_path.glob(f".{destination.name}.backup-*"))


def test_before_publish_must_be_callable(tmp_path: Path) -> None:
    with pytest.raises(FigureExportValidationError, match="before_publish"):
        export_figures(
            _plan(tmp_path / "out"),
            data_provider=_data_for,
            before_publish="not callable",  # type: ignore[arg-type]
        )


def test_preview_selection_must_belong_to_plan(tmp_path: Path) -> None:
    plan = _plan(tmp_path / "out", units=(2,))

    with pytest.raises(FigureExportValidationError, match="not selected"):
        render_live_preview(plan, 99, 0, data_provider=_data_for)
    with pytest.raises(FigureExportValidationError, match="outside"):
        render_live_preview(plan, 2, 1, data_provider=_data_for)
