"""Exercise native window chrome on the macOS release runtime."""

import ctypes
import sys
import unittest

from gui_test_support import tk_test_root
from rfmapping_viewer.tk_support import set_macos_welcome_chrome, tk


@unittest.skipUnless(sys.platform == "darwin", "requires native macOS Tk")
class MacOSWelcomeChromeTests(unittest.TestCase):
    def test_welcome_chrome_before_mapping_and_document_restoration(self):
        import _tkinter

        root = tk_test_root()
        if root.tk.call("tk", "windowingsystem") != "aqua":
            self.skipTest("requires Aqua")
        if int(str(root.tk.call("package", "present", "Tk")).split(".")[0]) < 9:
            self.skipTest("requires Tk 9")
        window = tk.Toplevel(root)
        window.withdraw()
        self.addCleanup(window.destroy)
        window.geometry("480x632")
        window.minsize(480, 632)
        window.resizable(False, False)
        tk.Frame(window, background="white").pack(fill="both", expand=True)
        window.title("Welcome to RF Map Viewer")

        # Match startup: the helper must work before deiconify or update.
        self.assertTrue(set_macos_welcome_chrome(window, True))
        saved = window._rfm_welcome_chrome
        library = ctypes.CDLL(_tkinter.__file__)
        library.Tk_MacOSXGetNSWindowForDrawable.argtypes = (ctypes.c_void_p,)
        library.Tk_MacOSXGetNSWindowForDrawable.restype = ctypes.c_void_p
        native = library.Tk_MacOSXGetNSWindowForDrawable(window.winfo_id())
        self.assertTrue(native)
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.sel_registerName.argtypes = (ctypes.c_char_p,)
        objc.sel_registerName.restype = ctypes.c_void_p
        selector = objc.sel_registerName
        address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        get_integer = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(address)
        get_boolean = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(address)
        get_button = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(address)

        def buttons_hidden():
            buttons = [get_button(native, selector(b"standardWindowButton:"), index) for index in range(3)]
            return tuple(not button or get_boolean(button, selector(b"isHidden")) for button in buttons)

        window.deiconify()
        window.update()
        self.assertIn("fullsizecontentview", window.tk.splitlist(window.attributes("-stylemask")))
        self.assertEqual(get_integer(native, selector(b"titleVisibility")), 1)
        self.assertEqual(buttons_hidden(), (True, True, True))
        self.assertTrue(set_macos_welcome_chrome(window, False))
        self.assertEqual(window.attributes("-stylemask"), saved["stylemask"])
        self.assertEqual(get_integer(native, selector(b"titleVisibility")), saved["title_visibility"])
        self.assertEqual(buttons_hidden(), saved["buttons"])
        window.resizable(True, True)
        window.update()
        mask = window.tk.splitlist(window.attributes("-stylemask"))
        self.assertIn("resizable", mask)
        self.assertNotIn("fullsizecontentview", mask)
