from __future__ import annotations

from collections.abc import Mapping

import pytest
from PIL import Image, ImageChops, ImageDraw

import rfmapping_web.shared_figure_export as figure_export_module
from rfmapping_web.shared_figure_export import (
    ExportPage,
    FigureExportValidationError,
    PillowFigureRenderer,
    PlotKind,
    PlotSpec,
    shared_scalar_scale,
    shared_symmetric_scale,
)


SCALAR_MATRIX = [
    [0.0, 1.0, 2.0, 3.0],
    [4.0, 5.0, 6.0, 7.0],
    [8.0, 9.0, 10.0, 11.0],
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
WAVEFORM_PAYLOAD = {
    "matrix": [
        [-1.0, -2.0, -3.0, -2.0, 0.0, 2.0, 1.0, 0.0],
        [-2.0, -4.0, -6.0, -3.0, 1.0, 5.0, 2.0, 0.0],
        [-3.0, -7.0, -9.0, -4.0, 2.0, 12.0, 4.0, 1.0],
        [-2.0, -5.0, -7.0, -3.0, 1.0, 7.0, 3.0, 0.0],
        [-1.0, -3.0, -4.0, -2.0, 0.0, 3.0, 1.0, 0.0],
    ],
    "times_ms": [-0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25],
    "time_edges_ms": [
        -0.625, -0.375, -0.125, 0.125, 0.375,
        0.625, 0.875, 1.125, 1.375,
    ],
    "channel_labels": ["ch 7", "ch 8", "ch 9", "ch 10", "ch 11"],
    "best_channel_row": 2,
}


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


def test_shared_symmetric_scale_is_reusable_and_validated() -> None:
    scale = shared_symmetric_scale(
        [
            {"matrix": [[-2.0, 1.0], [float("nan"), None]]},
            [[10.0, -4.0]],
        ]
    )

    assert dict(scale) == {"vmin": -10.0, "vmax": 10.0}
    with pytest.raises(TypeError):
        scale["vmin"] = 0.0  # type: ignore[index]
    assert dict(shared_symmetric_scale([[[100.0]]], limit=4.0)) == {
        "vmin": -4.0,
        "vmax": 4.0,
    }
    with pytest.raises(FigureExportValidationError, match="greater than"):
        shared_symmetric_scale([[[1.0]]], limit=-1.0)


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


def test_waveform_heatmap_draws_quantitative_annotations_and_best_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_colorbar: list[tuple[float, float, str, str]] = []
    observed_text: list[str] = []
    observed_zero_segments: list[tuple[object, ...]] = []
    observed_markers: list[object] = []
    original_colorbar = figure_export_module._draw_scalar_colorbar
    original_text = ImageDraw.ImageDraw.text
    original_line = ImageDraw.ImageDraw.line
    original_ellipse = ImageDraw.ImageDraw.ellipse

    def recording_colorbar(draw, box, low, high, palette, unit):
        observed_colorbar.append((low, high, palette, unit))
        return original_colorbar(draw, box, low, high, palette, unit)

    def recording_text(self, xy, text, *args, **kwargs):
        observed_text.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    def recording_line(self, xy, *args, **kwargs):
        if kwargs.get("fill") == "#111827":
            observed_zero_segments.append(tuple(xy))
        return original_line(self, xy, *args, **kwargs)

    def recording_ellipse(self, xy, *args, **kwargs):
        if kwargs.get("fill") == "#dc2626":
            observed_markers.append(xy)
        return original_ellipse(self, xy, *args, **kwargs)

    monkeypatch.setattr(
        figure_export_module,
        "_draw_scalar_colorbar",
        recording_colorbar,
    )
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", recording_text)
    monkeypatch.setattr(ImageDraw.ImageDraw, "line", recording_line)
    monkeypatch.setattr(ImageDraw.ImageDraw, "ellipse", recording_ellipse)
    image = Image.new("RGB", (700, 460), "white")
    spec = PlotSpec(
        PlotKind.WAVEFORM_LOCAL_AVERAGE,
        WAVEFORM_PAYLOAD,
        options={"vmin": -20.0, "vmax": 8.0},
    )

    figure_export_module._draw_waveform_heatmap(
        ImageDraw.Draw(image),
        (10, 10, 690, 450),
        spec,
    )

    assert observed_colorbar == [(-20.0, 20.0, "rdbu_r", "µV")]
    assert set(WAVEFORM_PAYLOAD["channel_labels"]) <= set(observed_text)
    assert {"channel", "Time from spike alignment (ms)"} <= set(observed_text)
    assert len(observed_zero_segments) >= 2
    assert len({round(segment[0]) for segment in observed_zero_segments}) == 1
    assert len(observed_markers) == 1


def test_waveform_palette_is_diverging_and_zero_centered() -> None:
    negative = figure_export_module._palette(-1.0, -1.0, 1.0, "rdbu_r")
    zero = figure_export_module._palette(0.0, -1.0, 1.0, "rdbu_r")
    positive = figure_export_module._palette(1.0, -1.0, 1.0, "rdbu_r")

    assert negative[2] > negative[0]
    assert max(zero) - min(zero) == 0
    assert positive[0] > positive[2]


def test_waveform_tick_labels_are_thinned_until_they_do_not_overlap() -> None:
    positions = tuple(float(index * 3) for index in range(61))
    widths = tuple(42.0 for _index in positions)

    indices = figure_export_module._non_overlapping_tick_indices(
        positions,
        widths,
        padding=8.0,
    )

    assert indices[0] == 0
    assert indices[-1] == 60
    assert len(indices) < 6
    for left_index, right_index in zip(indices, indices[1:]):
        label_gap = positions[right_index] - positions[left_index]
        required_gap = (widths[left_index] + widths[right_index]) / 2.0 + 8.0
        assert label_gap >= required_gap


def test_waveform_heatmap_fills_panel_with_non_square_time_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cell_boxes: list[tuple[float, float, float, float]] = []
    original_rectangle = ImageDraw.ImageDraw.rectangle

    def recording_rectangle(self, xy, *args, **kwargs):
        if isinstance(kwargs.get("fill"), tuple):
            cell_boxes.append(tuple(xy))
        return original_rectangle(self, xy, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "rectangle", recording_rectangle)
    payload = {
        "matrix": [
            [float(column - 30) for column in range(60)]
            for _row in range(5)
        ],
        "times_ms": [column / 30.0 - 0.5 for column in range(60)],
        "channel_labels": [f"ch {index}" for index in range(5)],
        "best_channel_row": 2,
    }
    image = Image.new("RGB", (620, 420), "white")

    figure_export_module._draw_waveform_heatmap(
        ImageDraw.Draw(image),
        (10, 10, 610, 410),
        PlotSpec(
            PlotKind.WAVEFORM_LOCAL_AVERAGE,
            payload,
            options={
                "show_axes": False,
                "show_colorbar": False,
                "show_zero_time": False,
            },
        ),
    )

    assert len(cell_boxes) == 5 * 60
    first_cell = cell_boxes[0]
    cell_width = first_cell[2] - first_cell[0]
    cell_height = first_cell[3] - first_cell[1]
    assert cell_height > cell_width * 5


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {**WAVEFORM_PAYLOAD, "times_ms": [-0.5, 0.0]},
            "exactly 8",
        ),
        (
            {
                **WAVEFORM_PAYLOAD,
                "times_ms": [-0.5, -0.25, 0.0, 0.25, 0.5, 0.5, 1.0, 1.25],
            },
            "strictly increasing",
        ),
        (
            {**WAVEFORM_PAYLOAD, "channel_labels": ["ch 7"]},
            "exactly 5",
        ),
        (
            {**WAVEFORM_PAYLOAD, "best_channel_row": 5},
            "inside 0..4",
        ),
        (
            {**WAVEFORM_PAYLOAD, "time_edges_ms": [-0.625, -0.375]},
            "one more",
        ),
    ],
)
def test_waveform_heatmap_strictly_validates_normalized_payload(
    payload: Mapping[str, object],
    message: str,
) -> None:
    page = ExportPage(
        "Invalid waveform",
        [PlotSpec(PlotKind.WAVEFORM_LOCAL_AVERAGE, payload)],
    )

    with pytest.raises(FigureExportValidationError, match=message):
        PillowFigureRenderer((700, 500)).render_page(1, page)


def test_waveform_live_preview_and_page_render_share_exact_pixels() -> None:
    page = ExportPage(
        "Waveform",
        [PlotSpec(PlotKind.WAVEFORM_LOCAL_AVERAGE, WAVEFORM_PAYLOAD)],
    )
    renderer = PillowFigureRenderer((700, 500))

    preview = renderer.render_preview(9, page)
    final_page = renderer.render_page(9, page)

    assert ImageChops.difference(preview, final_page).getbbox() is None


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
