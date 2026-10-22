from types import SimpleNamespace
from unittest import mock

import pytest

import rfmapping_gui as gui


@pytest.mark.parametrize("state", [0, 0x0008, 0x0020, 0x40000, 0x40028, 0x4002A, 0x4002B])
@pytest.mark.parametrize("keysym", ["Right", "Up", "p", "P", "3", "minus"])
def test_windows_navigation_accepts_lock_and_extended_key_flags(state, keysym):
    viewer = SimpleNamespace(
        tk=mock.Mock(), _shortcut_uses_editing_widget=lambda _event: False,
    )
    viewer.tk.call.return_value = "win32"
    action = mock.Mock()
    event = SimpleNamespace(keysym=keysym, state=state)

    assert gui.RFMViewer._run_navigation_shortcut(viewer, event, action, 1) == "break"
    action.assert_called_once_with(1)


@pytest.mark.parametrize("modifier", [0x0004, 0x0010, 0x20000])
@pytest.mark.parametrize("lock_flags", [0, 0x40028])
def test_windows_control_and_alt_do_not_trigger_plain_shortcuts(modifier, lock_flags):
    viewer = SimpleNamespace(
        tk=mock.Mock(), _shortcut_uses_editing_widget=lambda _event: False,
    )
    viewer.tk.call.return_value = "win32"
    action = mock.Mock()
    event = SimpleNamespace(keysym="p", state=modifier | lock_flags)

    assert gui.RFMViewer._run_navigation_shortcut(viewer, event, action) is None
    action.assert_not_called()


@pytest.mark.parametrize("platform,primary", [("win32", "Control"), ("darwin", "Command")])
def test_filter_shortcut_uses_platform_modifier_and_active_document(platform, primary):
    active_viewer = mock.Mock()
    owner = SimpleNamespace(_active_viewer=lambda: active_viewer)
    window = mock.Mock()
    with mock.patch.object(gui.sys, "platform", platform):
        gui.RFMViewer._bind_unit_filter_shortcut(owner, window)

    bindings = dict(call.args for call in window.bind.call_args_list)
    for keysym in ("period", "greater"):
        assert bindings[f"<{primary}-Shift-{keysym}>"](None) == "break"
    assert active_viewer._toggle_zero_bin_filter.call_count == 2


def test_windows_shortcut_help_uses_ctrl_labels():
    viewer = object()
    with (
        mock.patch.object(gui.sys, "platform", "win32"),
        mock.patch.object(gui.messagebox, "showinfo") as showinfo,
    ):
        gui.RFMViewer._show_shortcuts(viewer)

    message = showinfo.call_args.args[1]
    for key in ("O", "R", "E", "Shift+E", "W", "Shift+."):
        assert f"Ctrl+{key}" in message
    assert "Command" not in message
