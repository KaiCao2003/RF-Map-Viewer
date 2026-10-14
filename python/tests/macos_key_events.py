"""Send Cocoa input through Tk's native event translation in macOS tests."""

import ctypes
from contextlib import contextmanager
import time


class NSPoint(ctypes.Structure):
    _fields_ = (("x", ctypes.c_double), ("y", ctypes.c_double))


class NSSize(ctypes.Structure):
    _fields_ = (("width", ctypes.c_double), ("height", ctypes.c_double))


class NSRect(ctypes.Structure):
    _fields_ = (("origin", NSPoint), ("size", NSSize))


def _native_dispatch(*, require_key_window=True):
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    objc.objc_getClass.argtypes = (ctypes.c_char_p,)
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = (ctypes.c_char_p,)
    objc.sel_registerName.restype = ctypes.c_void_p
    address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value

    def send(receiver, selector, result_type, argument_types=(), *arguments):
        call = ctypes.CFUNCTYPE(
            result_type, ctypes.c_void_p, ctypes.c_void_p, *argument_types,
        )(address)
        return call(receiver, objc.sel_registerName(selector), *arguments)

    application = send(objc.objc_getClass(b"NSApplication"), b"sharedApplication", ctypes.c_void_p)
    window = send(application, b"keyWindow", ctypes.c_void_p)
    assert window or not require_key_window, "The viewer did not become the native key window"
    return objc, send, application, window


def send_key(characters: str, keycode: int, modifiers: int = 0) -> None:
    objc, send, application, window = _native_dispatch()
    number = send(window, b"windowNumber", ctypes.c_long)
    string = send(
        objc.objc_getClass(b"NSString"), b"stringWithUTF8String:", ctypes.c_void_p,
        (ctypes.c_char_p,), characters.encode("utf-8"),
    )
    for event_type in (10, 11):  # NSEventTypeKeyDown, NSEventTypeKeyUp
        event = send(
            objc.objc_getClass(b"NSEvent"),
            b"keyEventWithType:location:modifierFlags:timestamp:windowNumber:context:"
            b"characters:charactersIgnoringModifiers:isARepeat:keyCode:",
            ctypes.c_void_p,
            (ctypes.c_ulong, NSPoint, ctypes.c_ulong, ctypes.c_double, ctypes.c_long,
             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool, ctypes.c_ushort),
            event_type, NSPoint(0, 0), modifiers, 0.0, number, None, string, string, False, keycode,
        )
        assert event, "Cocoa did not create the keyboard event"
        # Tk translates native events while draining the application queue;
        # calling sendEvent: directly bypasses tkProcessKeyEvent entirely.
        send(application, b"postEvent:atStart:", None, (ctypes.c_void_p, ctypes.c_bool), event, False)


def _native_widget_dispatch(widget, *, require_key_window=True):
    import _tkinter

    objc, send, application, window = _native_dispatch(require_key_window=require_key_window)
    toplevel = widget.winfo_toplevel()
    library = ctypes.CDLL(_tkinter.__file__)
    library.Tk_MacOSXGetNSWindowForDrawable.argtypes = (ctypes.c_void_p,)
    library.Tk_MacOSXGetNSWindowForDrawable.restype = ctypes.c_void_p
    native_window = library.Tk_MacOSXGetNSWindowForDrawable(toplevel.winfo_id())
    if require_key_window:
        assert native_window == window, "The widget is not in the native key window"
    return objc, send, application, native_window


def window_number(widget) -> int:
    _objc, send, _application, window = _native_widget_dispatch(widget)
    return send(window, b"windowNumber", ctypes.c_long)


def close_window(widget) -> None:
    objc, send, _application, window = _native_widget_dispatch(widget)
    # Dispatch while Tk drains the Cocoa loop, so Python callbacks enter through Tk.
    send(
        window, b"performSelector:withObject:afterDelay:", None,
        (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_double),
        objc.sel_registerName(b"performClose:"), None, 0.0,
    )


def fullscreen_state(widget) -> dict:
    _objc, send, _application, window = _native_widget_dispatch(widget, require_key_window=False)
    view = send(window, b"contentView", ctypes.c_void_p)
    screen = send(window, b"screen", ctypes.c_void_p)
    frame = send(window, b"frame", NSRect)
    bounds = send(view, b"bounds", NSRect)
    screen_frame = send(screen, b"frame", NSRect)
    maximum = send(window, b"maxFullScreenContentSize", NSSize)
    return {
        "fullscreen": bool(send(window, b"styleMask", ctypes.c_ulong) & (1 << 14)),
        "frame": [frame.origin.x, frame.origin.y, frame.size.width, frame.size.height],
        "content": [bounds.size.width, bounds.size.height],
        "screen": [screen_frame.origin.x, screen_frame.origin.y,
                   screen_frame.size.width, screen_frame.size.height],
        "maximum": [maximum.width, maximum.height],
        "tk": [widget.winfo_width(), widget.winfo_height()],
        "wm_maxsize": list(widget.maxsize()),
    }


def set_fullscreen_content_limit(widget, width: float, height: float) -> None:
    _objc, send, _application, window = _native_widget_dispatch(widget)
    send(window, b"setMaxFullScreenContentSize:", None, (NSSize,), NSSize(width, height))


def toggle_fullscreen(widget) -> None:
    objc, send, _application, window = _native_widget_dispatch(widget, require_key_window=False)
    # Enter through Cocoa's event loop, as the native green window button does.
    send(
        window, b"performSelector:withObject:afterDelay:", None,
        (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_double),
        objc.sel_registerName(b"toggleFullScreen:"), None, 0.0,
    )


@contextmanager
def fullscreen_transition_notifications(widget, *, entering: bool):
    """Count completed Cocoa transitions without calling Python from AppKit."""
    objc, send, _application, window = _native_widget_dispatch(widget, require_key_window=False)
    center = send(objc.objc_getClass(b"NSNotificationCenter"), b"defaultCenter", ctypes.c_void_p)
    observer = send(objc.objc_getClass(b"NSMutableArray"), b"new", ctypes.c_void_p)
    appkit = ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
    name = "NSWindowDidEnterFullScreenNotification" if entering else "NSWindowDidExitFullScreenNotification"
    notification = ctypes.c_void_p.in_dll(appkit, name).value
    send(
        center, b"addObserver:selector:name:object:", None,
        (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p),
        observer, objc.sel_registerName(b"addObject:"), notification, window,
    )
    try:
        yield lambda: send(observer, b"count", ctypes.c_ulong)
    finally:
        send(center, b"removeObserver:", None, (ctypes.c_void_p,), observer)
        send(observer, b"release", None)


def send_click(widget, *, count: int = 1) -> None:
    objc, send, application, window = _native_widget_dispatch(widget)
    toplevel = widget.winfo_toplevel()
    view = send(window, b"contentView", ctypes.c_void_p)
    bounds = send(view, b"bounds", NSRect)
    flipped = send(view, b"isFlipped", ctypes.c_bool)
    root_x = widget.winfo_rootx() + widget.winfo_width() // 2
    root_y = widget.winfo_rooty() + widget.winfo_height() // 2
    x = root_x - toplevel.winfo_rootx()
    y = root_y - toplevel.winfo_rooty()
    point = NSPoint(bounds.origin.x + x, bounds.origin.y + (y if flipped else bounds.size.height - y))
    location = send(
        view, b"convertPoint:toView:", NSPoint,
        (NSPoint, ctypes.c_void_p), point, None,
    )
    number = send(window, b"windowNumber", ctypes.c_long)
    # Aqua hit-tests using NSEvent.mouseLocation, not the posted event's location.
    graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    graphics.CGWarpMouseCursorPosition.argtypes = (NSPoint,)
    graphics.CGWarpMouseCursorPosition.restype = ctypes.c_int32
    result = graphics.CGWarpMouseCursorPosition(NSPoint(root_x, root_y))
    assert result == 0, f"Cocoa cursor positioning failed: {result}"
    widget.update()
    pointer = widget.winfo_pointerxy()
    assert pointer == (root_x, root_y), f"Native pointer is at {pointer}, expected {(root_x, root_y)}"
    assert widget.winfo_containing(*pointer) is widget, "Native pointer does not target the widget"
    for click_count in range(1, count + 1):
        for event_type in (1, 2):  # NSEventTypeLeftMouseDown, NSEventTypeLeftMouseUp
            event = send(
                objc.objc_getClass(b"NSEvent"),
                b"mouseEventWithType:location:modifierFlags:timestamp:windowNumber:context:"
                b"eventNumber:clickCount:pressure:",
                ctypes.c_void_p,
                (ctypes.c_ulong, NSPoint, ctypes.c_ulong, ctypes.c_double, ctypes.c_long,
                 ctypes.c_void_p, ctypes.c_long, ctypes.c_long, ctypes.c_float),
                event_type, location, 0, time.monotonic(), number, None,
                0, click_count, 1.0 if event_type == 1 else 0.0,
            )
            assert event, "Cocoa did not create the mouse event"
            send(application, b"postEvent:atStart:", None, (ctypes.c_void_p, ctypes.c_bool), event, False)
        # Preserve the focus and widget changes caused by the first real click.
        widget.update()


def send_double_click(widget) -> None:
    send_click(widget, count=2)
