from __future__ import annotations

import numpy as np
import pytest

from rfmapping_fm_gui import (
    APP_RELEASE_VERSION,
    VIEW_2D,
    VIEW_3D,
    VIEWS,
    _nearest_center_indices,
    colorize_matrix,
    finite_display_range,
    head_angles_from_sphere_point,
    project_head_angles_to_sphere,
    render_spherical_texture,
    sphere_direction_from_normalized,
)


def test_colorize_preserves_shape_and_marks_nan() -> None:
    matrix = np.array([[0.0, 1.0, np.nan]])

    rgb = colorize_matrix(matrix, "Viridis", 0.0, 1.0)

    assert rgb.shape == (1, 3, 3)
    assert tuple(rgb[0, 2]) == (28, 32, 39)
    assert not np.array_equal(rgb[0, 0], rgb[0, 1])


def test_display_range_is_robust_and_nonempty() -> None:
    low, high = finite_display_range(np.array([[0.0, 1.0, 1000.0]]))
    assert low == 0.0
    assert 1.0 < high < 1000.0
    assert finite_display_range(np.array([[np.nan]])) == (0.0, 1.0)


def test_colorize_rejects_invalid_palette_and_range() -> None:
    with pytest.raises(ValueError, match="Unknown palette"):
        colorize_matrix(np.zeros((1, 1)), "Nope", 0.0, 1.0)
    with pytest.raises(ValueError, match="increasing"):
        colorize_matrix(np.zeros((1, 1)), "Gray", 1.0, 1.0)


def test_alpha2_exposes_2d_and_3d_view_modes() -> None:
    assert APP_RELEASE_VERSION == "1.10.0-alpha.2"
    assert VIEWS == (VIEW_2D, VIEW_3D)


def test_sphere_center_tracks_head_centric_yaw_and_pitch() -> None:
    assert head_angles_from_sphere_point(0.0, 0.0, 0.0, 0.0) == pytest.approx(
        (0.0, 0.0)
    )
    assert head_angles_from_sphere_point(0.0, 0.0, 67.0, 24.0) == pytest.approx(
        (67.0, 24.0)
    )
    assert head_angles_from_sphere_point(1.01, 0.0, 0.0, 0.0) is None


def test_head_angle_projection_and_sphere_pick_are_inverse_at_visible_points() -> None:
    x, y, depth = project_head_angles_to_sphere(35.0, 20.0, 12.0, -8.0)

    assert float(depth) > 0.0
    picked = head_angles_from_sphere_point(
        float(x), float(y), 12.0, -8.0
    )
    assert picked == pytest.approx((35.0, 20.0))


def test_spherical_texture_uses_axis_colors_and_transparent_corners() -> None:
    azimuth = np.array([-90.0, 0.0, 90.0])
    elevation = np.array([-45.0, 0.0, 45.0])
    rgb = np.zeros((3, 3, 3), dtype=np.uint8)
    rgb[1, 1] = (10, 20, 30)
    rgb[1, 2] = (200, 100, 50)

    front = render_spherical_texture(rgb, azimuth, elevation, 5, 0.0, 0.0)
    right = render_spherical_texture(rgb, azimuth, elevation, 5, 90.0, 0.0)

    assert front.shape == (5, 5, 4)
    assert tuple(front[2, 2]) == (10, 20, 30, 255)
    assert tuple(right[2, 2]) == (200, 100, 50, 255)
    assert front[0, 0, 3] == 0
    assert front[0, 4, 3] == 0


def test_sphere_direction_and_circular_axis_cover_the_azimuth_seam() -> None:
    direction = sphere_direction_from_normalized(
        np.array([0.0, 2.0]), np.array([0.0, 0.0]), 180.0, 0.0
    )
    assert direction[0] == pytest.approx((0.0, 0.0, -1.0), abs=1e-12)
    assert np.all(np.isnan(direction[1]))

    centers = np.array([-135.0, -45.0, 45.0, 135.0])
    indices = _nearest_center_indices(
        centers, np.array([-179.0, 179.0]), circular=True
    )
    assert indices.tolist() == [0, 3]
