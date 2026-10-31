"""Saved detections must match coordinate values, not only grid dimensions."""

from dataclasses import replace

import pytest

from rfmapping_viewer.rf_result_overlay import RFResultOverlayCache
from rfmapping_viewer.rf_results import load_saved_rf_result
from test_rf_results import source, write_result, write_summary


def write_geometry_result(source, *, dimension="2d", axis="x", projection=False,
                          collapsed_coordinate=0.0):
    coordinates = {
        "x_positions": list(source.x_positions),
        "y_positions": list(source.y_positions),
    }
    if dimension == "1d" and not projection:
        coordinates["y_positions" if axis == "x" else "x_positions"] = [collapsed_coordinate]
    path, manifest = write_result(
        source, dimension, axis=axis, projection=projection,
        manifest_changes=coordinates,
    )
    if projection:
        write_summary(source, path, manifest, dimension=dimension, projection=True)
    return path


@pytest.mark.parametrize("dimension,projection", [("2d", False), ("1d", True)])
@pytest.mark.parametrize("field", ["x_positions", "y_positions"])
@pytest.mark.parametrize("change", ["reverse", "shift"])
def test_saved_full_grid_rejects_same_size_coordinate_changes(source, dimension, projection, field, change):
    write_geometry_result(source, dimension=dimension, projection=projection)
    original = getattr(source, field)
    changed = original[::-1] if change == "reverse" else tuple(value + 1 for value in original)

    with pytest.raises(ValueError, match=field):
        load_saved_rf_result(replace(source, **{field: changed}), dimension=dimension)


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("change", ["reverse", "shift"])
def test_saved_1d_rejects_changed_retained_coordinates(source, axis, change):
    write_geometry_result(source, dimension="1d", axis=axis)
    field = f"{axis}_positions"
    original = getattr(source, field)
    changed = original[::-1] if change == "reverse" else tuple(value + 1 for value in original)

    with pytest.raises(ValueError, match=field):
        load_saved_rf_result(replace(source, **{field: changed}), dimension="1d")


@pytest.mark.parametrize("dimension,projection", [("2d", False), ("1d", True)])
def test_saved_full_grid_accepts_matching_coordinates(source, dimension, projection):
    write_geometry_result(source, dimension=dimension, projection=projection)

    assert load_saved_rf_result(source, dimension=dimension).for_unit(42) is not None


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("collapsed_origin", ["zero", "first_native"])
def test_saved_1d_accepts_producer_collapsed_coordinate_conventions(source, axis, collapsed_origin):
    collapsed = source.y_positions if axis == "x" else source.x_positions
    write_geometry_result(
        source, dimension="1d", axis=axis,
        collapsed_coordinate=0.0 if collapsed_origin == "zero" else collapsed[0],
    )

    assert load_saved_rf_result(source, dimension="1d").for_unit(42) is not None


def test_mismatched_saved_grid_is_unavailable_to_overlays_without_changing_files(source):
    path = write_geometry_result(source)
    original = path.read_bytes()
    cache = RFResultOverlayCache(replace(source, x_positions=source.x_positions[::-1]))

    overlay = cache.get(42, mode="2D")

    assert not overlay.available
    assert overlay.unavailable == ("2D",)
    assert "x_positions" in cache.error("2d", "excitatory")
    assert path.read_bytes() == original
