"""Send Cocoa input through Tk's native event translation in macOS tests."""

import ctypes
import time


class NSPoint(ctypes.Structure):
    _fields_ = (("x", ctypes.c_double), ("y", ctypes.c_double))


class NSSize(ctypes.Structure):
    _fields_ = (("width", ctypes.c_double), ("height", ctypes.c_double))


class NSRect(ctypes.Structure):
    _fields_ = (("origin", NSPoint), ("size", NSSize))


def _native_dispatch():
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
    assert window, "The viewer did not become the native key window"
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


def _native_widget_dispatch(widget):
    import _tkinter

    objc, send, application, window = _native_dispatch()
    toplevel = widget.winfo_toplevel()
    library = ctypes.CDLL(_tkinter.__file__)
    library.Tk_MacOSXGetNSWindowForDrawable.argtypes = (ctypes.c_void_p,)
    library.Tk_MacOSXGetNSWindowForDrawable.restype = ctypes.c_void_p
    assert library.Tk_MacOSXGetNSWindowForDrawable(toplevel.winfo_id()) == window, (
        "The widget is not in the native key window"
    )
    return objc, send, application, window


def window_number(widget) -> int:
    _objc, send, _application, window = _native_widget_dispatch(widget)
    return send(window, b"windowNumber", ctypes.c_long)


def close_window(widget) -> None:
    _objc, send, _application, window = _native_widget_dispatch(widget)
    send(window, b"performClose:", None, (ctypes.c_void_p,), None)


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
