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
