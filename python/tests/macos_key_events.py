"""Send Cocoa key events through Tk's native event translation in macOS tests."""

import ctypes


class NSPoint(ctypes.Structure):
    _fields_ = (("x", ctypes.c_double), ("y", ctypes.c_double))


def send_key(characters: str, keycode: int, modifiers: int = 0) -> None:
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
        send(application, b"sendEvent:", None, (ctypes.c_void_p,), event)
