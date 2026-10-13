from dataclasses import replace

import numpy as np
import pytest

from rfmapping_viewer.rf_results import load_saved_rf_result
from rfmapping_viewer.rf_results_view import draw_saved_rf_result
from test_rf_results import source, write_result


@pytest.mark.parametrize("y_positions", [(-15., 15.), (15., -15.)])
@pytest.mark.parametrize("x_positions", [(-90., 0., 90.), (90., 0., -90.)])
@pytest.mark.parametrize("flip_y", [False, True])
def test_saved_map_preserves_source_coordinate_order_and_explicit_y_flip(
    source, x_positions, y_positions, flip_y,
):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    source = replace(source, x_positions=x_positions, y_positions=y_positions)
    path, _ = write_result(source)
    original = path.read_bytes()
    result = load_saved_rf_result(source)
    figure = Figure()
    FigureCanvasAgg(figure)
    assert draw_saved_rf_result(figure, result, 42, flip_y=flip_y)
    figure.canvas.draw()

    axes = figure.axes[0]
    points = axes.transData.transform([
        [x_positions[0], y_positions[0]],
        [x_positions[-1], y_positions[-1]],
    ])
    assert points[0, 0] < points[1, 0]
    # Matplotlib screen coordinates increase upward; the Tk canvas increases
    # downward. Compare physical screen locations, not numeric axis direction.
    assert bool(points[0, 1] > points[1, 1]) is (not flip_y)
    np.testing.assert_allclose(axes.collections[1].get_offsets(), [[x_positions[-1], y_positions[-1]]])
    np.testing.assert_array_equal(axes.collections[0].get_array().reshape(2, 3), [[0, 0, 0], [0, 0, 1]])
    assert path.read_bytes() == original
