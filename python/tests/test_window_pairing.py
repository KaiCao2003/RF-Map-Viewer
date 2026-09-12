import unittest
from dataclasses import replace
from types import MethodType, SimpleNamespace
from unittest import mock
import rfmapping_gui as gui
import rfmapping_viewer.constants as constants_module
import rfmapping_viewer.display as display_module
import rfmapping_viewer.viewer_state as viewer_state_module
from gui_test_support import Variable, app_stub, unit_data, viewer_stub


class WindowPairingTests(unittest.TestCase):

    class FakeToggle:
        def __init__(self) -> None:
            self.states = []

        def state(self, values) -> None:
            self.states.append(values)

    class FakeLabel:
        def __init__(self) -> None:
            self.text = ""

        def configure(self, **kwargs) -> None:
            self.text = kwargs["text"]

    @staticmethod
    def bind(viewer, *method_names: str) -> None:
        for method_name in method_names:
            setattr(
                viewer,
                method_name,
                MethodType(getattr(gui.RFMViewer, method_name), viewer),
            )

    @classmethod
    def window(cls, units: list[int], *, ready: bool = True):
        return viewer_stub(
            _viewer_ready=ready,
            data=unit_data(unit_pool=list(units)),
            pair_windows_var=Variable(False),
            pair_windows_toggle=cls.FakeToggle(),
            pair_status_label=cls.FakeLabel(),
            _pair_last_local_state=None,
        )

    @staticmethod
    def state(**overrides) -> viewer_state_module.ViewerSyncState:
        values = {
            "unit_id": 20,
            "value_mode": constants_module.VALUE_MODE_COUNT,
            "timeline_bin_center_ms": 50.0,
            "timeline_selection_start_ms": 20.0,
            "timeline_selection_end_ms": 80.0,
            "timeline_anchor_center_ms": 80.0,
            "rf_start_ms": 0.0,
            "rf_end_ms": 20.0,
            "time_resolution_ms": 10.0,
            "x_bins": 6,
            "y_bins": 5,
            "smooth_radius": 1,
            "flip_y": False,
            "palette": "Gray",
            "polar_radius": constants_module.POLAR_RADIUS_MODES[1],
            "polar_layout": False,
            "rgb_mode": False,
            "selected_cell_y_midpoint": 4.0,
            "selected_cell_x_midpoint": 5.0,
            "timeline_scroll_fraction": 0.25,
            "selected_tab": "timeline",
        }
        values.update(overrides)
        return viewer_state_module.ViewerSyncState(**values)

    def test_pairing_status_warns_but_allows_different_unit_lists(self) -> None:
        first = self.window([10, 20])
        loading = self.window([10, 20], ready=False)
        root = app_stub(
            _rfm_viewer_windows=[first, loading],
            _rfm_pairing_enabled=False,
        )
        first._app_root = root
        loading._app_root = root
        self.bind(first, "_ready_pairing_viewers", "_pairing_eligibility")
        first._unit_lists_match = gui.RFMViewer._unit_lists_match

        gui.RFMViewer._refresh_pairing_controls(first)
        self.assertEqual(
            first.pair_status_label.text,
            "Open another loaded viewer window to enable sync.",
        )
        self.assertEqual(first.pair_windows_toggle.states[-1], ["disabled"])

        second = self.window([10, 20])
        second._app_root = root
        root._rfm_viewer_windows.append(second)
        gui.RFMViewer._refresh_pairing_controls(first)
        self.assertEqual(
            first.pair_status_label.text,
            "2 loaded windows have matching unit lists.",
        )
        self.assertEqual(second.pair_windows_toggle.states[-1], ["!disabled"])

        second.data.unit_pool = [20, 10]
        gui.RFMViewer._refresh_pairing_controls(first)
        self.assertIn("Unit lists differ", first.pair_status_label.text)
        self.assertEqual(first.pair_windows_toggle.states[-1], ["!disabled"])

        second.data.unit_pool = [10, 20]
        root._rfm_pairing_enabled = True
        gui.RFMViewer._refresh_pairing_controls(first)
        self.assertEqual(
            second.pair_status_label.text,
            "2 windows paired. Changes in any paired window sync to the others.",
        )

        second.data.unit_pool = [20, 30]
        gui.RFMViewer._refresh_pairing_controls(first)
        self.assertEqual(
            first.pair_status_label.text,
            "2 windows paired. Unit lists differ; these files may be from different "
            "sessions. Missing units display N/A.",
        )
        self.assertEqual(first.pair_windows_toggle.states[-1], ["!disabled"])

    def test_paired_navigation_uses_sorted_union_and_marks_missing_unit(self) -> None:
        main = self.window([1, 3, 5, 7])
        sync_one = self.window([1, 3, 5, 6, 7])
        sync_two = self.window([2, 3, 5, 7])
        root = app_stub(
            _rfm_viewer_windows=[main, sync_one, sync_two],
            _rfm_pairing_enabled=True,
        )
        for window in root._rfm_viewer_windows:
            window._app_root = root

        main.data.n_units = len(main.data.unit_pool)
        main.unit_idx = Variable(0)
        main._selected_unit_id = 1
        main._last_supported_unit_id = 1
        main.selected_cell = None
        main._update_all = mock.Mock()
        main._publish_pairing_state_if_changed = mock.Mock()
        self.bind(
            main,
            "_ready_pairing_viewers",
            "_pairing_eligibility",
            "_pairing_unit_ids",
            "_unit_navigation_ids",
            "_local_unit_index",
            "_selected_unit_id_value",
            "_set_selected_unit_id",
        )

        self.assertEqual(main._pairing_unit_ids(), [1, 2, 3, 5, 6, 7])
        gui.RFMViewer._step_unit(main, 1)
        self.assertEqual(main._selected_unit_id, 2)
        self.assertEqual(main.unit_idx.get(), -1)
        gui.RFMViewer._step_unit(main, 1)
        self.assertEqual(main._selected_unit_id, 3)
        self.assertEqual(main.unit_idx.get(), 1)

    def test_pairing_state_maps_unit_id_instead_of_local_index(self) -> None:
        incoming = self.state(unit_id=20)
        viewer = viewer_stub(
            _viewer_ready=True,
            _pair_apply_in_progress=False,
            data=unit_data(unit_pool=[30, 20], n_units=2),
            unit_idx=Variable(0),
            _selected_unit_id=30,
            _last_supported_unit_id=30,
            _normalize_control_values=mock.Mock(),
            _timeline_preview_cache_key="cached",
            _timeline_preview_images={1: object()},
            _update_all=mock.Mock(),
            _capture_pairing_state=mock.Mock(return_value=incoming),
            _pair_last_local_state=None,
        )
        self.bind(
            viewer,
            "_local_unit_index",
            "_selected_unit_id_value",
            "_set_selected_unit_id",
        )

        gui.RFMViewer._apply_pairing_state(viewer, incoming, frozenset({"unit"}))
        self.assertEqual(viewer._selected_unit_id, 20)
        self.assertEqual(viewer.unit_idx.get(), 1)

        missing = replace(incoming, unit_id=99)
        viewer._capture_pairing_state.return_value = missing
        gui.RFMViewer._apply_pairing_state(viewer, missing, frozenset({"unit"}))
        self.assertEqual(viewer._selected_unit_id, 99)
        self.assertEqual(viewer.unit_idx.get(), -1)

    def test_removed_only_selected_unit_advances_to_next_union_id(self) -> None:
        canonical = self.state(unit_id=2)
        first = self.window([1, 3])
        removed = self.window([2, 3])
        third = self.window([3, 5])
        root = app_stub(
            _rfm_viewer_windows=[first, removed, third],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=canonical,
            _rfm_pairing_broadcasting=False,
        )
        for window in root._rfm_viewer_windows:
            window._app_root = root
            window._apply_pairing_state = mock.Mock()
        first._refresh_pairing_controls = mock.Mock()
        self.bind(
            first,
            "_ready_pairing_viewers",
            "_pairing_eligibility",
            "_pairing_unit_ids",
            "_disable_window_pairing",
        )
        first._next_union_unit_id = gui.RFMViewer._next_union_unit_id

        root._rfm_viewer_windows.remove(removed)
        gui.RFMViewer._pair_ready_viewer_set_changed(first)

        self.assertEqual(root._rfm_pairing_state.unit_id, 3)
        first._apply_pairing_state.assert_called_once_with(
            root._rfm_pairing_state,
            frozenset({"unit"}),
        )
        third._apply_pairing_state.assert_called_once_with(
            root._rfm_pairing_state,
            frozenset({"unit"}),
        )

    def test_reloaded_adopted_window_receives_full_state_when_unit_is_normalized(self) -> None:
        canonical = self.state(unit_id=2, palette="Inferno", rf_end_ms=200.0)
        first = self.window([1, 3])
        reloaded = self.window([3, 5])
        root = app_stub(
            _rfm_viewer_windows=[first, reloaded],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=canonical,
            _rfm_pairing_broadcasting=False,
        )
        for window in root._rfm_viewer_windows:
            window._app_root = root
            window._apply_pairing_state = mock.Mock()
        first._refresh_pairing_controls = mock.Mock()
        self.bind(
            first,
            "_ready_pairing_viewers",
            "_pairing_eligibility",
            "_pairing_unit_ids",
            "_disable_window_pairing",
        )
        first._next_union_unit_id = gui.RFMViewer._next_union_unit_id

        gui.RFMViewer._pair_ready_viewer_set_changed(first, adopt_viewer=reloaded)

        normalized = root._rfm_pairing_state
        self.assertEqual(normalized.unit_id, 3)
        self.assertEqual(normalized.palette, "Inferno")
        self.assertEqual(normalized.rf_end_ms, 200.0)
        first._apply_pairing_state.assert_called_once_with(
            normalized,
            frozenset({"unit"}),
        )
        reloaded._apply_pairing_state.assert_called_once_with(normalized)

    def test_leaving_pairing_restores_last_supported_local_unit(self) -> None:
        viewer = viewer_stub(
            _viewer_ready=False,
            data=unit_data(unit_pool=[1, 3], n_units=2),
            unit_idx=Variable(-1),
            _selected_unit_id=2,
            _last_supported_unit_id=1,
        )
        self.bind(
            viewer,
            "_local_unit_index",
            "_selected_unit_id_value",
            "_selected_local_unit_index",
            "_set_selected_unit_id",
        )

        gui.RFMViewer._restore_local_unit_selection(viewer)

        self.assertEqual(viewer._selected_unit_id, 1)
        self.assertEqual(viewer.unit_idx.get(), 0)

    def test_enabling_uses_toggling_window_as_initial_source(self) -> None:
        state = self.state()
        source = self.window([10, 20])
        target = self.window([10, 20])
        root = app_stub(
            _rfm_viewer_windows=[source, target],
            _rfm_pairing_enabled=False,
            _rfm_pairing_state=None,
            _rfm_pairing_broadcasting=False,
        )
        source._app_root = target._app_root = root
        source.pair_windows_var.set(True)
        source._capture_pairing_state = mock.Mock(return_value=state)
        source._refresh_pairing_controls = mock.Mock()
        target._apply_pairing_state = mock.Mock()
        self.bind(source, "_ready_pairing_viewers", "_pairing_eligibility")

        gui.RFMViewer._on_pair_windows_toggled(source)

        self.assertTrue(root._rfm_pairing_enabled)
        self.assertEqual(root._rfm_pairing_state, state)
        self.assertEqual(source._pair_last_local_state, state)
        target._apply_pairing_state.assert_called_once_with(state)
        self.assertFalse(root._rfm_pairing_broadcasting)

    def test_new_window_adopts_canonical_even_when_unit_is_missing(self) -> None:
        canonical = self.state()
        first = self.window([10, 20])
        newcomer = self.window([10, 20])
        root = app_stub(
            _rfm_viewer_windows=[first, newcomer],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=canonical,
            _rfm_pairing_broadcasting=False,
        )
        first._app_root = newcomer._app_root = root
        newcomer._apply_pairing_state = mock.Mock()
        first._refresh_pairing_controls = mock.Mock()
        self.bind(
            first,
            "_ready_pairing_viewers",
            "_pairing_eligibility",
            "_pairing_unit_ids",
            "_disable_window_pairing",
        )
        first._next_union_unit_id = gui.RFMViewer._next_union_unit_id

        gui.RFMViewer._pair_ready_viewer_set_changed(first, adopt_viewer=newcomer)
        newcomer._apply_pairing_state.assert_called_once_with(canonical)
        self.assertTrue(root._rfm_pairing_enabled)

        newcomer._apply_pairing_state.reset_mock()
        newcomer.data.unit_pool = [10, 99]
        gui.RFMViewer._pair_ready_viewer_set_changed(first, adopt_viewer=newcomer)
        newcomer._apply_pairing_state.assert_called_once_with(canonical)
        self.assertTrue(root._rfm_pairing_enabled)
        self.assertEqual(root._rfm_pairing_state, canonical)

    def test_pairing_survives_three_to_two_closure_but_not_two_to_one(self) -> None:
        canonical = self.state()
        first, second, third = (self.window([10, 20]) for _ in range(3))
        root = app_stub(
            _rfm_viewer_windows=[first, second, third],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=canonical,
            _rfm_pairing_broadcasting=False,
        )
        first._app_root = second._app_root = third._app_root = root
        first._refresh_pairing_controls = mock.Mock()
        self.bind(
            first,
            "_ready_pairing_viewers",
            "_pairing_eligibility",
            "_pairing_unit_ids",
            "_disable_window_pairing",
        )
        first._next_union_unit_id = gui.RFMViewer._next_union_unit_id

        root._rfm_viewer_windows.remove(third)
        gui.RFMViewer._pair_ready_viewer_set_changed(first)
        self.assertTrue(root._rfm_pairing_enabled)

        root._rfm_viewer_windows.remove(second)
        gui.RFMViewer._pair_ready_viewer_set_changed(first)
        self.assertFalse(root._rfm_pairing_enabled)

    def test_clamped_peer_palette_change_does_not_overwrite_canonical_ranges(self) -> None:
        canonical = self.state()
        clamped_baseline = self.state(
            timeline_bin_center_ms=10.0,
            timeline_selection_start_ms=0.0,
            timeline_selection_end_ms=20.0,
            timeline_anchor_center_ms=20.0,
            rf_start_ms=-10.0,
            rf_end_ms=10.0,
            selected_cell_y_midpoint=1.0,
            selected_cell_x_midpoint=1.0,
        )
        changed = replace(clamped_baseline, palette="Inferno")
        source = self.window([10, 20])
        target = self.window([10, 20])
        root = app_stub(
            _rfm_viewer_windows=[source, target],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=canonical,
            _rfm_pairing_broadcasting=False,
        )
        source._app_root = target._app_root = root
        source._pair_last_local_state = clamped_baseline
        source._capture_pairing_state = mock.Mock(return_value=changed)
        target._apply_pairing_state = mock.Mock()
        target._apply_pairing_scroll_fraction = mock.Mock()
        self.bind(source, "_ready_pairing_viewers", "_pairing_eligibility")

        gui.RFMViewer._publish_pairing_state_if_changed(source)

        self.assertEqual(root._rfm_pairing_state.palette, "Inferno")
        self.assertEqual(
            root._rfm_pairing_state.timeline_selection_start_ms,
            canonical.timeline_selection_start_ms,
        )
        self.assertEqual(
            root._rfm_pairing_state.timeline_bin_center_ms,
            canonical.timeline_bin_center_ms,
        )
        self.assertEqual(root._rfm_pairing_state.rf_start_ms, canonical.rf_start_ms)
        self.assertEqual(
            root._rfm_pairing_state.selected_cell_y_midpoint,
            canonical.selected_cell_y_midpoint,
        )
        target._apply_pairing_state.assert_called_once_with(
            changed,
            frozenset({"palette"}),
        )
        target._apply_pairing_scroll_fraction.assert_not_called()

    def test_scroll_only_change_uses_direct_peer_fast_path(self) -> None:
        baseline = self.state(timeline_scroll_fraction=0.1)
        changed = replace(baseline, timeline_scroll_fraction=0.7)
        source = self.window([10, 20])
        target = self.window([10, 20])
        root = app_stub(
            _rfm_viewer_windows=[source, target],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=baseline,
            _rfm_pairing_broadcasting=False,
        )
        source._app_root = target._app_root = root
        source._pair_last_local_state = baseline
        source._capture_pairing_state = mock.Mock(return_value=changed)
        target._apply_pairing_state = mock.Mock()
        target._apply_pairing_scroll_fraction = mock.Mock()
        self.bind(source, "_ready_pairing_viewers", "_pairing_eligibility")

        gui.RFMViewer._publish_pairing_state_if_changed(source)

        target._apply_pairing_scroll_fraction.assert_called_once_with(0.7)
        target._apply_pairing_state.assert_not_called()
        self.assertEqual(root._rfm_pairing_state.timeline_scroll_fraction, 0.7)

    def test_root_broadcast_guard_prevents_feedback_loop(self) -> None:
        source = self.window([10, 20])
        root = app_stub(
            _rfm_viewer_windows=[source],
            _rfm_pairing_enabled=True,
            _rfm_pairing_state=self.state(),
            _rfm_pairing_broadcasting=True,
        )
        source._app_root = root
        source._capture_pairing_state = mock.Mock()

        gui.RFMViewer._publish_pairing_state_if_changed(source)

        source._capture_pairing_state.assert_not_called()

    def test_direct_scroll_apply_never_redraws(self) -> None:
        baseline = self.state(timeline_scroll_fraction=0.1)
        canvas = mock.Mock()
        canvas.yview.return_value = (0.55, 1.0)
        viewer = viewer_stub(
            _viewer_ready=True,
            _pair_apply_in_progress=False,
            _timeline_scroll_fraction=0.1,
            _restoring_timeline_scroll=False,
            _pair_last_local_state=baseline,
            canvases={"timeline": canvas},
            _capture_pairing_state=mock.Mock(),
            _update_all=mock.Mock(),
        )

        gui.RFMViewer._apply_pairing_scroll_fraction(viewer, 0.6)

        canvas.yview_moveto.assert_called_once_with(0.33)
        viewer._update_all.assert_not_called()
        viewer._capture_pairing_state.assert_not_called()
        self.assertEqual(viewer._pair_last_local_state.timeline_scroll_fraction, 0.6)

    def test_exact_bottom_timeline_scroll_is_remembered(self) -> None:
        canvas = mock.Mock()
        canvas.yview.return_value = (0.72, 1.0)
        viewer = SimpleNamespace(
            _restoring_timeline_scroll=False,
            _timeline_scroll_fraction=0.1,
            canvases={"timeline": canvas},
        )

        gui.RFMViewer._timeline_scroll_set(viewer, "0.72", "1.0")
        self.assertEqual(viewer._timeline_scroll_fraction, 1.0)

        viewer._timeline_scroll_fraction = 0.1
        gui.RFMViewer._remember_timeline_scroll(viewer)
        self.assertEqual(viewer._timeline_scroll_fraction, 1.0)

    def test_scroll_progress_is_independent_of_viewport_span(self) -> None:
        self.assertEqual(display_module.timeline_scroll_progress(0.375, 0.625), 0.5)

        short_viewport = mock.Mock()
        short_viewport.yview.return_value = (0.0, 0.2)
        tall_viewport = mock.Mock()
        tall_viewport.yview.return_value = (0.0, 0.5)
        short_viewer = SimpleNamespace(
            canvases={"timeline": short_viewport},
            _timeline_scroll_fraction=0.5,
            _restoring_timeline_scroll=False,
        )
        tall_viewer = SimpleNamespace(
            canvases={"timeline": tall_viewport},
            _timeline_scroll_fraction=0.5,
            _restoring_timeline_scroll=False,
        )

        gui.RFMViewer._restore_timeline_scroll(short_viewer)
        gui.RFMViewer._restore_timeline_scroll(tall_viewer)

        short_viewport.yview_moveto.assert_called_once_with(0.4)
        tall_viewport.yview_moveto.assert_called_once_with(0.25)

    def test_unscrollable_timeline_preserves_desired_progress(self) -> None:
        canvas = mock.Mock()
        canvas.yview.return_value = (0.0, 1.0)
        viewer = SimpleNamespace(
            _restoring_timeline_scroll=False,
            _timeline_scroll_fraction=0.65,
            canvases={"timeline": canvas},
        )

        gui.RFMViewer._timeline_scroll_set(viewer, "0.0", "1.0")
        gui.RFMViewer._remember_timeline_scroll(viewer)
        gui.RFMViewer._restore_timeline_scroll(viewer)

        self.assertEqual(viewer._timeline_scroll_fraction, 0.65)
        canvas.yview_moveto.assert_not_called()

    def test_physical_time_and_cell_midpoints_map_to_target_groups(self) -> None:
        bounds = [(-40.0, -20.0), (-20.0, 0.0), (0.0, 20.0), (20.0, 40.0)]
        viewer = SimpleNamespace(
            _time_groups=lambda: [(0, 1), (2, 3), (4, 5), (6, 7)],
            _time_group_bounds_ms=lambda index: bounds[index],
        )
        viewer._time_group_index_for_ms = MethodType(
            gui.RFMViewer._time_group_index_for_ms, viewer
        )

        self.assertEqual(gui.RFMViewer._time_group_index_for_ms(viewer, 12.0), 2)
        self.assertEqual(gui.RFMViewer._time_group_index_for_ms(viewer, 100.0), 3)
        self.assertEqual(gui.RFMViewer._time_group_range_for_ms(viewer, -5.0, 25.0), (1, 3))

        viewer._display_y_groups = lambda: [(4, 5), (2, 3), (0, 1)]
        viewer._x_groups = lambda: [(0, 2), (3, 5)]
        viewer._axis_group_for_midpoint = gui.RFMViewer._axis_group_for_midpoint
        self.assertEqual(
            gui.RFMViewer._cell_for_pairing_midpoint(viewer, 2.6, 4.2),
            (2, 3, 3, 5),
        )
        self.assertEqual(
            gui.RFMViewer._cell_for_pairing_midpoint(viewer, 99.0, -99.0),
            (4, 5, 0, 2),
        )

    def test_partial_apply_changes_only_requested_fields(self) -> None:
        incoming = self.state(palette="Viridis")
        viewer = viewer_stub(
            _viewer_ready=True,
            _pair_apply_in_progress=False,
            palette_var=Variable("Gray"),
            _normalize_control_values=mock.Mock(),
            _timeline_preview_cache_key="cached",
            _timeline_preview_images={1: object()},
            _update_all=mock.Mock(),
            _capture_pairing_state=mock.Mock(return_value=incoming),
            _pair_last_local_state=None,
        )

        gui.RFMViewer._apply_pairing_state(viewer, incoming, frozenset({"palette"}))

        self.assertEqual(viewer.palette_var.get(), "Viridis")
        viewer._normalize_control_values.assert_called_once_with()
        viewer._update_all.assert_called_once_with()
        self.assertFalse(viewer._pair_apply_in_progress)

    def test_time_resolution_delta_preserves_peer_local_physical_timeline(self) -> None:
        incoming = self.state(time_resolution_ms=20.0)
        time_resolution = Variable("10")
        bin_var = Variable(2)
        range_start = Variable(2)
        range_end = Variable(3)

        def bounds(index: int) -> tuple[float, float]:
            size = float(time_resolution.get())
            return index * size, (index + 1) * size

        viewer = viewer_stub(
            _viewer_ready=True,
            _pair_apply_in_progress=False,
            time_res_ms_var=time_resolution,
            bin_var=bin_var,
            range_start_var=range_start,
            range_end_var=range_end,
            _timeline_range_anchor=3,
            _time_group_center_ms=lambda index: sum(bounds(index)) / 2.0,
            _timeline_selected_time_bounds_ms=lambda: (
                bounds(min(range_start.get(), range_end.get()))[0],
                bounds(max(range_start.get(), range_end.get()))[1],
            ),
            _time_group_index_for_ms=lambda value: max(
                0, min(4, int(float(value) // float(time_resolution.get())))
            ),
            _time_group_range_for_ms=lambda start, end: (
                max(0, min(4, int(float(start) // float(time_resolution.get())))),
                max(
                    0,
                    min(
                        4,
                        int((float(end) - 1e-9) // float(time_resolution.get())),
                    ),
                ),
            ),
            _normalize_control_values=mock.Mock(),
            _timeline_preview_cache_key="cached",
            _timeline_preview_images={1: object()},
            _update_all=mock.Mock(),
            _capture_pairing_state=mock.Mock(return_value=incoming),
            _pair_last_local_state=None,
        )

        gui.RFMViewer._apply_pairing_state(
            viewer,
            incoming,
            frozenset({"time_resolution"}),
        )

        self.assertEqual(float(time_resolution.get()), 20.0)
        self.assertEqual(bin_var.get(), 1)
        self.assertEqual((range_start.get(), range_end.get()), (1, 1))
        self.assertEqual(viewer._timeline_range_anchor, 1)

    def test_spatial_bin_delta_remaps_peer_local_selected_midpoint(self) -> None:
        incoming = self.state(x_bins=2, y_bins=2)
        viewer = viewer_stub(
            _viewer_ready=True,
            _pair_apply_in_progress=False,
            data=SimpleNamespace(n_x=6, n_y=6),
            x_bins_var=Variable(6),
            y_bins_var=Variable(6),
            selected_cell=(2, 3, 4, 5),
            _normalize_control_values=mock.Mock(),
            _display_y_groups=lambda: display_module.axis_groups_for_target(6, 2),
            _x_groups=lambda: display_module.axis_groups_for_target(6, 2),
            _axis_group_for_midpoint=gui.RFMViewer._axis_group_for_midpoint,
            _timeline_preview_cache_key="cached",
            _timeline_preview_images={1: object()},
            _update_all=mock.Mock(),
            _capture_pairing_state=mock.Mock(return_value=incoming),
            _pair_last_local_state=None,
        )
        viewer._cell_for_pairing_midpoint = MethodType(
            gui.RFMViewer._cell_for_pairing_midpoint, viewer
        )

        gui.RFMViewer._apply_pairing_state(
            viewer,
            incoming,
            frozenset({"x_bins", "y_bins"}),
        )

        self.assertEqual(viewer.selected_cell, (3, 5, 3, 5))

    def test_control_normalization_keeps_selected_cell_in_current_groups(self) -> None:
        viewer = SimpleNamespace(
            bin_var=Variable(0),
            range_start_var=Variable(0),
            range_end_var=Variable(0),
            _timeline_range_anchor=None,
            selected_cell=(2, 3, 4, 5),
            _time_groups=mock.Mock(return_value=[(0, 0)]),
            _source_bins_for_time_controls=mock.Mock(),
            _source_bins_for_subtract_controls=mock.Mock(),
            _x_target_bins=mock.Mock(return_value=2),
            _y_target_bins=mock.Mock(return_value=2),
            _smooth_radius=mock.Mock(return_value=0),
            _sync_time_control_ranges=mock.Mock(),
            _cell_for_pairing_midpoint=mock.Mock(return_value=(3, 5, 3, 5)),
        )

        gui.RFMViewer._normalize_control_values(viewer)

        viewer._cell_for_pairing_midpoint.assert_called_once_with(2.5, 4.5)
        self.assertEqual(viewer.selected_cell, (3, 5, 3, 5))
