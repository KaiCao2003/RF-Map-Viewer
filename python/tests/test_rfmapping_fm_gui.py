from __future__ import annotations

from types import SimpleNamespace
from concurrent.futures import Future, ThreadPoolExecutor
import queue
import threading
from unittest import mock

import numpy as np
import pytest

import rfmapping_fm_gui as gui
from rfmapping_fm_gui import (
    APP_RELEASE_VERSION,
    VIEW_2D,
    VIEW_3D,
    VIEWS,
    FreeMovingRFViewer,
    _nearest_center_indices,
    colorize_matrix,
    finite_display_range,
    head_angles_from_sphere_point,
    project_head_angles_to_sphere,
    render_spherical_texture,
    spatial_display_aspect,
    sphere_direction_from_normalized,
)
from rfmapping_viewer.fm_dataset import STIMULUS_BAR, STIMULUS_SQUARE


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


def test_singleton_y_uses_legacy_30_by_7_visual_aspect() -> None:
    assert spatial_display_aspect(120, 1) == pytest.approx(30.0 / 7.0)


def test_multirow_2d_map_aspect_is_unchanged() -> None:
    assert spatial_display_aspect(120, 7) == pytest.approx(120.0 / 7.0)


def test_colorize_rejects_invalid_palette_and_range() -> None:
    with pytest.raises(ValueError, match="Unknown palette"):
        colorize_matrix(np.zeros((1, 1)), "Nope", 0.0, 1.0)
    with pytest.raises(ValueError, match="increasing"):
        colorize_matrix(np.zeros((1, 1)), "Gray", 1.0, 1.0)


def test_alpha3_exposes_2d_and_3d_view_modes() -> None:
    assert APP_RELEASE_VERSION == "1.10.0-alpha.3"
    assert VIEWS == (VIEW_2D, VIEW_3D)


def test_open_flow_selects_stimulus_before_file_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []

    class ViewerDouble:
        def _ask_stimulus_kind(self) -> str:
            events.append("choose-stimulus")
            return STIMULUS_BAR

        def open_document(self, path: str, stimulus_kind: str) -> None:
            events.append(("load", path, stimulus_kind))

    def askopenfilename(**options: object) -> str:
        events.append(("file-picker", options["title"]))
        return "/tmp/bar.rfmap"

    monkeypatch.setattr(
        gui,
        "filedialog",
        SimpleNamespace(askopenfilename=askopenfilename),
    )

    FreeMovingRFViewer.choose_document(ViewerDouble())  # type: ignore[arg-type]

    assert events == [
        "choose-stimulus",
        ("file-picker", "Open Bar free-moving RF map"),
        ("load", "/tmp/bar.rfmap", STIMULUS_BAR),
    ]


def test_external_open_requires_stimulus_choice_before_load() -> None:
    events: list[object] = []

    class ViewerDouble:
        def _ask_stimulus_kind(self) -> str:
            events.append("choose-stimulus")
            return STIMULUS_SQUARE

        def open_document(self, path: str, stimulus_kind: str) -> None:
            events.append(("load", path, stimulus_kind))

    FreeMovingRFViewer._open_external_document(  # type: ignore[arg-type]
        ViewerDouble(), "/tmp/square.rfmap"
    )

    assert events == [
        "choose-stimulus",
        ("load", "/tmp/square.rfmap", STIMULUS_SQUARE),
    ]


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


def test_continuous_drag_renders_before_motion_stops() -> None:
    viewer = FreeMovingRFViewer.__new__(FreeMovingRFViewer)
    viewer._closed = False
    viewer._render_after = None
    viewer._sphere_drag = (0.0, 0.0, 0.0, 0.0)
    viewer.view_var = SimpleNamespace(get=lambda: VIEW_3D)
    pending = {}
    frames = []
    clock = 0
    serial = 0

    def after(delay, callback):
        nonlocal serial
        serial += 1
        pending[serial] = (clock + delay, callback)
        return serial

    def render():
        viewer._render_after = None
        frames.append((clock, viewer._sphere_yaw_deg))

    viewer.after = after
    viewer.after_cancel = lambda key: pending.pop(key)
    viewer._render = render
    for clock in range(0, 1000, 8):
        for key, (deadline, callback) in list(pending.items()):
            if deadline <= clock:
                pending.pop(key)
                callback()
        viewer._heat_drag(SimpleNamespace(x=clock, y=0))
    assert len(frames) >= 30
    assert len({yaw for _, yaw in frames}) == len(frames)
    assert len(pending) == 1
    clock += 20
    next(iter(pending.values()))[1]()
    assert frames[-1][1] == viewer._sphere_yaw_deg


def test_unit_loading_skips_obsolete_reads_and_clears_stale_map() -> None:
    started = threading.Event()
    release = threading.Event()
    reads = []

    def load_unit(index):
        reads.append(index)
        if index == 0:
            started.set()
            assert release.wait(5)
        return SimpleNamespace(unit_index=index, unit_id=index)

    viewer = FreeMovingRFViewer.__new__(FreeMovingRFViewer)
    viewer.dataset = SimpleNamespace(unit_ids=list(range(20)), unit_count=20, load_unit=load_unit)
    viewer._closed = False
    viewer.unit_index = -1
    viewer.unit_map = SimpleNamespace(unit_id=-1)
    viewer._unit_future = None
    viewer._load_generation = 0
    viewer._result_queue = queue.Queue()
    viewer._executor = ThreadPoolExecutor(max_workers=1)
    widget = mock.Mock()
    widget.winfo_width.return_value = widget.winfo_height.return_value = 600
    viewer.unit_combo = viewer.previous_unit_button = viewer.next_unit_button = viewer.heat_canvas = widget
    viewer.status_var = mock.Mock()
    viewer.schedule_render = mock.Mock()
    viewer.after = mock.Mock()
    try:
        viewer._request_unit(0)
        assert started.wait(5)
        for index in range(1, 20):
            viewer._request_unit(index)
        assert viewer.unit_map is None
        assert viewer._display_matrix is None
        release.set()
        viewer._executor.shutdown(wait=True)
        viewer._poll_unit_results()
        assert reads == [0, 19]
        assert viewer.unit_map.unit_id == 19
        assert viewer._unit_future is None
        viewer.schedule_render.assert_called_once()
    finally:
        release.set()
        viewer._executor.shutdown(wait=True, cancel_futures=True)


def test_opening_new_document_replaces_pending_unit_zero(monkeypatch) -> None:
    viewer = FreeMovingRFViewer.__new__(FreeMovingRFViewer)
    old_future = Future()
    viewer._unit_future = old_future
    viewer._load_generation = 4
    widget = mock.Mock()
    for name in ("status_var", "file_var", "document_var", "unit_combo", "start_scale", "stop_scale"):
        setattr(viewer, name, widget)
    viewer.update_idletasks = mock.Mock()
    viewer._update_time_text = viewer._update_calibration_text = mock.Mock()
    viewer._request_unit = mock.Mock()
    dataset = SimpleNamespace(
        path="new.rfmap", stimulus_kind=STIMULUS_SQUARE, unit_ids=[7],
        unit_count=1, elevation_count=1, azimuth_count=1, time_bin_count=2,
        time_edges_sec=np.array([0, 0.1, 0.2]),
    )
    monkeypatch.setattr(gui, "load_free_moving_rfmap", lambda *_: dataset)
    viewer.open_document("new.rfmap", STIMULUS_SQUARE)
    assert old_future.cancelled()
    assert viewer._unit_future is None
    assert viewer._load_generation == 5
    assert viewer.dataset is dataset
    assert viewer.unit_map is None
    viewer._request_unit.assert_called_once_with(0)
