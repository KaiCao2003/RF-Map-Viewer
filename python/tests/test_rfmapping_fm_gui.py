from __future__ import annotations

import numpy as np
import pytest

from rfmapping_fm_gui import colorize_matrix, finite_display_range


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
