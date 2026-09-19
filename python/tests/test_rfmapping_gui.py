import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import rfmapping_viewer.figure_export as figure_export_module
import rfmapping_gui as gui
import rfmapping_viewer.constants as constants_module
import rfmapping_viewer.paths as paths_module
import rfmapping_viewer.settings as settings_module
import rfmapping_viewer.settings_window as settings_window_module
from gui_test_support import Variable, base_payload, viewer_stub, write_payload


class AppIdentityTests(unittest.TestCase):
    def test_display_version_omits_internal_edition(self) -> None:
        self.assertEqual(constants_module.APP_DISPLAY_VERSION, constants_module.APP_VERSION)
        self.assertNotIn(constants_module.APP_EDITION, constants_module.APP_DISPLAY_VERSION)


class ShortcutModifierTests(unittest.TestCase):
    def test_aqua_navigation_accepts_native_function_and_keypad_flags(self) -> None:
        viewer = SimpleNamespace(
            tk=mock.Mock(), _shortcut_uses_editing_widget=lambda _event: False,
        )
        viewer.tk.call.return_value = "aqua"
        for state in (0, 0x0020, 0x0040, 0x0060, 0x0062):
            with self.subTest(state=hex(state)):
                action = mock.Mock()
                event = SimpleNamespace(keysym="Right", state=state)
                self.assertEqual(gui.RFMViewer._run_navigation_shortcut(viewer, event, action), "break")
                action.assert_called_once_with()

    def test_command_modifiers_remain_platform_specific(self) -> None:
        viewer = SimpleNamespace(
            tk=mock.Mock(), _shortcut_uses_editing_widget=lambda _event: False,
        )
        for platform, states in (
            ("aqua", (0x0004, 0x0008, 0x0010)),
            ("x11", (0x0004, 0x0008, 0x0040, 0x0080, 0x20000)),
        ):
            viewer.tk.call.return_value = platform
            for state in states:
                with self.subTest(platform=platform, state=hex(state)):
                    action = mock.Mock()
                    event = SimpleNamespace(keysym="p", state=state)
                    self.assertIsNone(gui.RFMViewer._run_navigation_shortcut(viewer, event, action))
                    action.assert_not_called()


class MacOSLifecycleTests(unittest.TestCase):
    def test_no_argument_main_opens_file_chooser_viewer_without_sample_data(self) -> None:
        viewer = mock.Mock()
        with (
            mock.patch.object(gui, "TK_AVAILABLE", True),
            mock.patch.object(gui, "RFMViewer", return_value=viewer) as viewer_type,
        ):
            self.assertEqual(gui.main([]), 0)

        viewer_type.assert_called_once_with()
        viewer.mainloop.assert_called_once_with()

    def test_macos_handlers_include_open_application_open_document_and_quit(self) -> None:
        class FakeTk:
            def __init__(self) -> None:
                self.commands = {}

            def createcommand(self, name, callback) -> None:
                self.commands[name] = callback

        class FakeViewer:
            def __init__(self) -> None:
                self.tk = FakeTk()
                self._app_root = mock.Mock()
                self.protocols = {}
                self.bindings = {}
                self._quit_application = lambda *_args: None
                self._close_window = lambda *_args: None
                self._dispatch_open_json = lambda *_args: None
                self._dispatch_settings = lambda *_args: None
                self._dispatch_macos_open_application = lambda *_args: None
                self._dispatch_macos_open_documents = lambda *_args: None
                self._open_support_documentation = lambda *_args: None

            def protocol(self, name, callback) -> None:
                self.protocols[name] = callback

            def bind_all(self, event, callback) -> None:
                self.bindings[event] = callback

        viewer = FakeViewer()
        with mock.patch.object(gui.sys, "platform", "darwin"):
            gui.RFMViewer._install_application_handlers(viewer)

        self.assertIs(viewer.protocols["WM_DELETE_WINDOW"], viewer._close_window)
        self.assertIs(viewer.tk.commands["::tk::mac::OpenApplication"], viewer._dispatch_macos_open_application)
        self.assertIs(viewer.tk.commands["::tk::mac::OpenDocument"], viewer._dispatch_macos_open_documents)
        self.assertIs(viewer.tk.commands["::tk::mac::Quit"], viewer._quit_application)
        self.assertIs(
            viewer.tk.commands["::tk::mac::ShowHelp"],
            viewer._open_support_documentation,
        )

    def test_open_document_creates_independent_windows(self) -> None:
        class FakeViewer:
            def __init__(self) -> None:
                self._viewer_ready = True
                self.opened = []

            def _open_json_window(self, path: Path) -> None:
                self.opened.append(path)

        viewer = FakeViewer()
        gui.RFMViewer._on_macos_open_documents(viewer, "/tmp/a.json", "/tmp/b.json")
        self.assertEqual(viewer.opened, [Path("/tmp/a.json"), Path("/tmp/b.json")])

    def test_open_dialog_routes_ready_document_to_new_window(self) -> None:
        class FakeViewer:
            def __init__(self) -> None:
                self._viewer_ready = True
                self.data = mock.Mock(path=Path("/tmp/current.json"))
                self.opened = []

            def _open_json_window(self, path: Path) -> None:
                self.opened.append(path)

        viewer = FakeViewer()
        fake_dialog = mock.Mock()
        fake_dialog.askopenfilename.return_value = "/tmp/next.json"
        with mock.patch.object(gui, "filedialog", fake_dialog):
            gui.RFMViewer._open_json(viewer)
        self.assertEqual(viewer.opened, [Path("/tmp/next.json")])

    def test_initial_open_document_replaces_deferred_file_picker(self) -> None:
        class FakeViewer:
            def __init__(self) -> None:
                self._viewer_ready = False
                self._startup_after = "file-picker"
                self._startup_generation = 0
                self._startup_cancel_event = None
                self._startup_poll_after = None
                self.cancelled = []
                self.scheduled = []

            def after_cancel(self, callback_id) -> None:
                self.cancelled.append(callback_id)

            def after_idle(self, callback):
                self.scheduled.append(callback)
                return "document-load"

            def _load_startup_document(self, path: Path) -> None:
                self.loaded = path

            def _open_json_window(self, path: Path) -> None:
                self.opened = path

        viewer = FakeViewer()
        viewer._cancel_startup_callback = lambda: gui.RFMViewer._cancel_startup_callback(viewer)
        gui.RFMViewer._on_macos_open_documents(viewer, "/tmp/requested.json")

        self.assertEqual(viewer.cancelled, ["file-picker"])
        self.assertEqual(viewer._startup_after, "document-load")
        viewer.scheduled[0]()
        self.assertEqual(viewer.loaded, Path("/tmp/requested.json"))

    def test_quit_is_idempotent_and_destroys_root(self) -> None:
        class FakeRoot:
            def __init__(self) -> None:
                self._rfm_quitting = False
                self.destroy_calls = 0

            def destroy(self) -> None:
                self.destroy_calls += 1

        class FakeViewer:
            def __init__(self) -> None:
                self._quitting = False
                self._app_root = FakeRoot()

        viewer = FakeViewer()
        gui.RFMViewer._quit_application(viewer)
        gui.RFMViewer._quit_application(viewer)
        self.assertTrue(viewer._quitting)
        self.assertEqual(viewer._app_root.destroy_calls, 1)

    def test_bundle_prohibits_detached_duplicate_instances(self) -> None:
        build_script = Path(gui.__file__).resolve().parent / "script" / "build_python_macos_app.sh"
        source = build_script.read_text(encoding="utf-8")
        self.assertIn('Add :LSMultipleInstancesProhibited bool true', source)
        self.assertNotIn('Add :LSMultipleInstancesProhibited bool false', source)


    def test_support_documentation_uses_adjacent_readme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "rfmapping_gui.py"
            readme = root / "README.md"
            module.touch()
            readme.write_text("# RF Map Viewer\n", encoding="utf-8")

            self.assertEqual(
                paths_module.support_documentation_path(module_path=module, frozen=False),
                readme.resolve(),
            )

    def test_frozen_support_documentation_uses_bundle_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / "RF Map Viewer.app" / "Contents"
            executable = contents / "MacOS" / "RF Map Viewer"
            readme = contents / "Resources" / "README.md"
            executable.parent.mkdir(parents=True)
            readme.parent.mkdir(parents=True)
            executable.touch()
            readme.write_text("# RF Map Viewer\n", encoding="utf-8")

            self.assertEqual(
                paths_module.support_documentation_path(
                    module_path=Path(directory) / "missing" / "rfmapping_gui.py",
                    executable_path=executable,
                    frozen=True,
                ),
                readme.resolve(),
            )

    def test_support_documentation_opens_local_file_uri(self) -> None:
        readme = Path(tempfile.gettempdir()) / "RF Map Viewer README.md"
        viewer = SimpleNamespace()
        with (
            mock.patch.object(gui, "support_documentation_path", return_value=readme),
            mock.patch.object(gui.webbrowser, "open", return_value=True) as open_document,
        ):
            gui.RFMViewer._open_support_documentation(viewer)

        open_document.assert_called_once_with(readme.as_uri())

    def test_settings_request_is_deferred_until_the_viewer_is_ready(self) -> None:
        class FakeViewer:
            def __init__(self) -> None:
                self._viewer_ready = False
                self._show_settings_when_ready = False

            def _active_viewer(self):
                return self

        viewer = FakeViewer()
        gui.RFMViewer._show_settings(viewer)
        self.assertTrue(viewer._show_settings_when_ready)

    def test_settings_commit_does_not_persist_to_an_unready_viewer(self) -> None:
        active = viewer_stub(
            _viewer_ready=False,
            _apply_viewer_settings=mock.Mock(),
        )
        window = SimpleNamespace(
            error_var=mock.Mock(),
            _clear_tab_errors=mock.Mock(),
            _validated_settings=mock.Mock(return_value=settings_module.ViewerSettings()),
            owner=SimpleNamespace(_active_viewer=lambda: active),
        )

        settings_window_module.SettingsWindow._commit(window, close=False)

        active._apply_viewer_settings.assert_not_called()
        window.error_var.set.assert_called_with(
            "The viewer is still opening. Try again when it is ready."
        )


class ShortcutBehaviorTests(unittest.TestCase):

    def test_timeline_step_moves_single_bin_selection(self) -> None:
        viewer = mock.Mock()
        viewer._time_group_count.return_value = 12
        viewer.bin_var = Variable(5)
        viewer.range_start_var = Variable(0)
        viewer.range_end_var = Variable(11)
        viewer.range_start_ms_var = Variable("0")
        viewer.range_end_ms_var = Variable("20")
        viewer._timeline_range_anchor = None

        gui.RFMViewer._step_timeline_bin(viewer, -1)

        self.assertEqual(viewer.bin_var.get(), 4)
        self.assertEqual((viewer.range_start_var.get(), viewer.range_end_var.get()), (4, 4))
        self.assertEqual(viewer._timeline_range_anchor, 4)
        self.assertEqual((viewer.range_start_ms_var.get(), viewer.range_end_ms_var.get()), ("0", "20"))
        viewer._sync_time_range_controls.assert_called_once_with()
        viewer._update_all.assert_called_once_with()

    def test_timeline_selection_sync_never_changes_rf_sum_controls(self) -> None:
        viewer = mock.Mock()
        viewer._time_group_count.return_value = 12
        viewer.range_start_var = Variable(9)
        viewer.range_end_var = Variable(4)
        viewer.range_start_ms_var = Variable("0")
        viewer.range_end_ms_var = Variable("20")

        gui.RFMViewer._sync_time_range_controls(viewer)

        self.assertEqual((viewer.range_start_var.get(), viewer.range_end_var.get()), (9, 4))
        self.assertEqual((viewer.range_start_ms_var.get(), viewer.range_end_ms_var.get()), ("0", "20"))

    def test_time_resolution_step_is_exactly_one_ms_before_data_clamping(self) -> None:
        viewer = mock.Mock()
        viewer.time_res_ms_var = Variable("8")
        viewer._base_bin_ms.return_value = 1.0
        viewer._total_time_ms.return_value = 30.0

        gui.RFMViewer._step_time_resolution(viewer, 1.0)

        self.assertEqual(viewer.time_res_ms_var.get(), "9")
        viewer._on_time_resolution_changed.assert_called_once_with()

    def test_time_resolution_change_preserves_partial_timeline_source_bounds(self) -> None:
        viewer = mock.Mock()
        viewer.data = mock.Mock(n_bins=8)
        viewer.bin_var = Variable(4)
        viewer.range_start_var = Variable(2)
        viewer.range_end_var = Variable(5)
        viewer._timeline_range_anchor = 5
        viewer._last_time_groups = [(index, index) for index in range(8)]
        new_groups = [(0, 1), (2, 3), (4, 5), (6, 7)]

        def normalize() -> None:
            viewer._last_time_groups = new_groups

        viewer._normalize_control_values.side_effect = normalize

        gui.RFMViewer._on_time_resolution_changed(viewer)

        self.assertEqual((viewer.range_start_var.get(), viewer.range_end_var.get()), (1, 2))
        self.assertEqual(viewer.bin_var.get(), 2)
        self.assertIsNone(viewer._timeline_range_anchor)
        viewer._update_all.assert_called_once_with()

    def test_show_full_timeline_range_resets_timeline_selection(self) -> None:
        viewer = mock.Mock()
        viewer._time_group_count.return_value = 12
        viewer.bin_var = Variable(6)
        viewer.range_start_var = Variable(4)
        viewer.range_end_var = Variable(8)
        viewer._timeline_range_anchor = 8

        gui.RFMViewer._clear_timeline_selection(viewer)

        self.assertEqual(viewer.bin_var.get(), 0)
        self.assertEqual((viewer.range_start_var.get(), viewer.range_end_var.get()), (0, 11))
        self.assertIsNone(viewer._timeline_range_anchor)


    def test_time_resolution_step_is_one_source_bin_before_data_clamping(self) -> None:
        viewer = mock.Mock()
        viewer.time_res_ms_var = Variable("8")
        viewer._base_bin_ms.return_value = 2.5
        viewer._total_time_ms.return_value = 30.0

        gui.RFMViewer._step_time_resolution(viewer, 1.0)

        self.assertEqual(viewer.time_res_ms_var.get(), "10.5")
        viewer._on_time_resolution_changed.assert_called_once_with()


class CommandLineTests(unittest.TestCase):
    def test_data_smoke_tolerates_windowed_executable_without_stdio(self) -> None:
        directory, path = write_payload(base_payload())
        self.addCleanup(directory.cleanup)
        with (
            mock.patch.object(gui.sys, "stdout", None),
            mock.patch.object(gui.sys, "stderr", None),
        ):
            self.assertEqual(gui.main(["--self-test", str(path)]), 0)

    def test_tkdnd_smoke_flag_runs_without_requiring_a_json_path(self) -> None:
        with mock.patch.object(gui, "run_tkdnd_self_test") as smoke:
            self.assertEqual(gui.main(["--self-test-dnd"]), 0)
        smoke.assert_called_once_with()

    def test_tkdnd_smoke_tolerates_windowed_executable_without_stdio(self) -> None:
        with (
            mock.patch.object(gui.sys, "stdout", None),
            mock.patch.object(gui.sys, "stderr", None),
            mock.patch.object(gui, "run_tkdnd_self_test") as smoke,
        ):
            self.assertEqual(gui.main(["--self-test-dnd"]), 0)
        smoke.assert_called_once_with()

    def test_tkdnd_smoke_failure_returns_nonzero(self) -> None:
        with mock.patch.object(
            gui,
            "run_tkdnd_self_test",
            side_effect=RuntimeError("missing TkDND"),
        ):
            self.assertEqual(gui.main(["--self-test-dnd"]), 1)

    def test_tkdnd_smoke_failure_without_stdio_still_returns_nonzero(self) -> None:
        with (
            mock.patch.object(gui.sys, "stdout", None),
            mock.patch.object(gui.sys, "stderr", None),
            mock.patch.object(
                gui,
                "run_tkdnd_self_test",
                side_effect=RuntimeError("missing TkDND"),
            ),
        ):
            self.assertEqual(gui.main(["--self-test-dnd"]), 1)

    def test_figure_export_smoke_writes_pdf_png_manifest_and_csv(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output_root = Path(directory.name) / "packaged-export"

        self.assertEqual(
            gui.main(["--self-test-export", str(output_root)]),
            0,
        )

        self.assertTrue((output_root / "figure-export-smoke.pdf").is_file())
        self.assertTrue(
            (output_root / "figure-export-smoke" / "manifest.json").is_file()
        )
        self.assertEqual(
            (output_root / "displayed-data-smoke.csv").read_text(encoding="utf-8"),
            "unit_id,value\n1,3\n",
        )

    def test_figure_export_smoke_without_stdio_uses_exit_status(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output_root = Path(directory.name) / "packaged-export"
        with (
            mock.patch.object(gui.sys, "stdout", None),
            mock.patch.object(gui.sys, "stderr", None),
        ):
            self.assertEqual(
                gui.main(["--self-test-export", str(output_root)]),
                0,
            )

    def test_figure_export_smoke_exercises_windows_path_backends(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output_root = Path(directory.name) / "packaged-export"
        with (
            mock.patch.object(constants_module, "_USE_PATH_CSV_PUBLICATION", True),
            mock.patch.object(
                figure_export_module,
                "_USE_PATH_PUBLICATION",
                True,
            ),
        ):
            self.assertEqual(
                gui.main(["--self-test-export", str(output_root)]),
                0,
            )

        self.assertTrue((output_root / "figure-export-smoke.pdf").is_file())
        self.assertTrue(
            (output_root / "figure-export-smoke" / "manifest.json").is_file()
        )
        self.assertTrue((output_root / "displayed-data-smoke.csv").is_file())
