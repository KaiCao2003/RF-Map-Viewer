"""Tk availability and native window integration."""

from __future__ import annotations

import ctypes
import sys

from rfmapping_viewer.constants import MACOS_FULLSCREEN_MAX_SIZE


try:
    from tkinter import filedialog, messagebox, ttk
    import tkinter as tk
    TK_AVAILABLE = True
except ModuleNotFoundError:
    filedialog = messagebox = ttk = None
    TK_AVAILABLE = False

    class _MissingTk:
        Tk = object
        Toplevel = object
        Misc = object
        TclError = ValueError

    tk = _MissingTk()


class _NSSize(ctypes.Structure):
    _fields_ = (("width", ctypes.c_double), ("height", ctypes.c_double))


def set_macos_welcome_chrome(window: tk.Misc, enabled: bool) -> bool:
    """Extend Tk 9 welcome content through the native titlebar, then restore it."""

    if sys.platform != "darwin" or window.tk.call("tk", "windowingsystem") != "aqua":
        return False
    if int(str(window.tk.call("package", "present", "Tk")).split(".")[0]) < 9:
        return False
    saved = getattr(window, "_rfm_welcome_chrome", None)
    if enabled and saved is not None:
        return True
    if not enabled and saved is None:
        return False

    try:
        # The Tk attribute query realizes the native window without pumping
        # Python callbacks through update(), which could open a document here.
        stylemask = window.tk.call("wm", "attributes", window._w, "-stylemask")
        process = ctypes.CDLL(None)
        process.TkMacOSXDrawable.argtypes = (ctypes.c_void_p,)
        process.TkMacOSXDrawable.restype = ctypes.c_void_p
        native_window = process.TkMacOSXDrawable(window.winfo_id())
        if not native_window:
            return False
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.sel_registerName.argtypes = (ctypes.c_char_p,)
        objc.sel_registerName.restype = ctypes.c_void_p
        address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        if not address:
            return False
        selector = objc.sel_registerName
        get_integer = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(address)
        get_boolean = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(address)
        set_integer = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(address)
        set_boolean = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)(address)
        get_button = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(address)

        if enabled:
            saved = {
                "stylemask": stylemask,
                "title_visibility": get_integer(native_window, selector(b"titleVisibility")),
                "movable": get_boolean(native_window, selector(b"isMovableByWindowBackground")),
                "buttons": tuple(
                    get_boolean(get_button(native_window, selector(b"standardWindowButton:"), index), selector(b"isHidden"))
                    for index in range(3)
                ),
                "background": window.cget("background"),
            }
        # Let Tk change the style so its own content geometry stays in sync.
        mask = ("titled", "closable", "fullsizecontentview") if enabled else saved["stylemask"]
        window.tk.call("wm", "attributes", window._w, "-stylemask", mask)
        set_integer(native_window, selector(b"setTitleVisibility:"), 1 if enabled else saved["title_visibility"])
        set_boolean(native_window, selector(b"setMovableByWindowBackground:"), enabled or saved["movable"])
        for index in range(3):
            button = get_button(native_window, selector(b"standardWindowButton:"), index)
            set_boolean(button, selector(b"setHidden:"), enabled or saved["buttons"][index])
        window.configure(background="white" if enabled else saved["background"])
        if enabled:
            window._rfm_welcome_chrome = saved
        else:
            del window._rfm_welcome_chrome
    except (AttributeError, OSError, TypeError, tk.TclError):
        return False
    return True


def allow_macos_fullscreen_resize(window: tk.Misc) -> bool:
    """Remove Tk 8.6's initial-display size cap from native full screen."""

    if sys.platform != "darwin":
        return False
    try:
        window.update_idletasks()
        process = ctypes.CDLL(None)
        process.TkMacOSXDrawable.argtypes = (ctypes.c_void_p,)
        process.TkMacOSXDrawable.restype = ctypes.c_void_p
        native_window = process.TkMacOSXDrawable(window.winfo_id())
        if not native_window:
            return False

        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.sel_registerName.argtypes = (ctypes.c_char_p,)
        objc.sel_registerName.restype = ctypes.c_void_p
        message_address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        if not message_address:
            return False
        send_size = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_void_p,
            _NSSize,
        )(message_address)
        selector = objc.sel_registerName(b"setMaxFullScreenContentSize:")
        maximum = MACOS_FULLSCREEN_MAX_SIZE
        send_size(native_window, selector, _NSSize(maximum, maximum))
    except (AttributeError, OSError, TypeError, tk.TclError):
        return False
    return True
