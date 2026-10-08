from types import SimpleNamespace

import numpy as np
import pytest

from rfmapping_viewer.tk_support import TK_AVAILABLE
from test_rf_result_overlay_gui import app, draw, native_overlay


pytestmark = pytest.mark.skipif(not TK_AVAILABLE, reason="Tk is unavailable")


@pytest.mark.parametrize("flip_y", [False, True])
@pytest.mark.parametrize("grouped", [False, True])
def test_rf_results_native_rows_centers_and_hit_testing_follow_rf_orientation(app, flip_y, grouped):
    app.flip_y_var.set(flip_y)
    app.polar_layout_var.set(False)
    app.x_bins_var.set(1 if grouped else 3)
    app.y_bins_var.set(1 if grouped else 2)

    for tab in ("rf", "results"):
        draw(app, tab)
        shapes = native_overlay(app)
        first_row = shapes["rf-source-0-0"][1]
        last_row = shapes["rf-source-1-2"][1]
        assert bool(first_row[1] < last_row[1]) is (not flip_y)
        assert first_row[0] < last_row[0]
        if tab == "results":
            canvas = app.canvases["rf"]
            for marker in canvas.find_withtag("rf-center-2d"):
                points = np.asarray(canvas.coords(marker)).reshape(-1, 2)
                center_x, center_y = points.mean(axis=0)
                assert center_x == pytest.approx((last_row[0] + last_row[2]) / 2)
                assert center_y == pytest.approx((last_row[1] + last_row[3]) / 2)
                selected = app._canvas_to_cell("rf", SimpleNamespace(x=center_x, y=center_y))
                assert selected == ((0, 1, 0, 2) if grouped else (1, 1, 2, 2))
            assert canvas.find_withtag("rf-center-2d")
