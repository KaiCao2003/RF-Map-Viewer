import tempfile
import threading
import time
import unittest
from itertools import combinations
from pathlib import Path
from unittest import mock

from gui_test_support import tk_test_root
import rfmapping_viewer.crosscorrelogram_window as window_module
from rfmapping_viewer.tk_support import TK_AVAILABLE, tk


@unittest.skipUnless(TK_AVAILABLE, "Tk is unavailable")
class CrossCorrelogramWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk_test_root()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        default_root = mock.patch.object(tk, "_default_root", self.root)
        default_root.start()
        self.addCleanup(default_root.stop)
        self.root.report_callback_exception = mock.Mock()
        self.addCleanup(self._close_windows)
        self.session_dir = Path("/recording/m20/260922/260922_1")

    def _close_windows(self) -> None:
        for window in self.root.winfo_children():
            window.destroy()
        self.root.update_idletasks()

    def wait_until(self, predicate) -> None:
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertTrue(predicate())
        self.root.report_callback_exception.assert_not_called()

    def test_initial_context_loads_units_off_main_thread(self) -> None:
        worker_threads = []

        def available(_session, _probe):
            worker_threads.append(threading.get_ident())
            return [101, 302, 368]

        with mock.patch.object(window_module, "available_unit_ids", side_effect=available):
            window = window_module.CrossCorrelogramWindow(
                self.root, self.session_dir, "A", [302, 368],
            )
            self.wait_until(lambda: not window._busy)

        self.assertEqual([variable.get() for variable in window.unit_vars], ["302", "368", ""])
        self.assertNotEqual(worker_threads, [threading.get_ident()])
        self.assertEqual(str(window.probe_combo.cget("state")), "readonly")
        self.assertFalse(window.plot_button.instate(["disabled"]))
        self.assertTrue(window.save_button.instate(["disabled"]))
        self.assertIsNone(window._canvas)
        window._close()

    def test_three_units_render_all_pairs_and_save_opaque_png(self) -> None:
        import pandas as pd
        from PIL import Image

        ccg = pd.DataFrame(
            {pair: [1.0, 3.0, 2.0] for pair in combinations([368, 302, 101], 2)},
            index=[-0.001, 0.0, 0.001],
        )
        window = window_module.CrossCorrelogramWindow(self.root)
        window.session_var.set(str(self.session_dir))
        for variable, value in zip(window.unit_vars, [368, 302, 101]):
            variable.set(str(value))
        with mock.patch.object(window_module, "compute_crosscorrelograms", return_value=ccg) as compute:
            window._plot()
            self.assertTrue(window.plot_button.instate(["disabled"]))
            self.wait_until(lambda: not window._busy)

        compute.assert_called_once_with(self.session_dir, "A", [368, 302, 101], 0.001, 0.05)
        self.assertEqual(len(window._figure.axes), 3)
        self.assertEqual(window._figure.get_facecolor(), (1.0, 1.0, 1.0, 1.0))
        self.assertFalse(window.save_button.instate(["disabled"]))
        self.root.update()
        window._canvas.draw()
        renderer = window._canvas.get_renderer()
        bounds = [axis.get_tightbbox(renderer) for axis in window._figure.axes]
        for upper, lower in zip(bounds, bounds[1:]):
            self.assertGreater(upper.y0, lower.y1)
        self.assertGreaterEqual(bounds[-1].y0, 0)
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "ccg.png"
            with mock.patch.object(window_module.filedialog, "asksaveasfilename", return_value=str(image_path)):
                window._save()
            with Image.open(image_path) as image:
                self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (255, 255, 255, 255))
                self.assertEqual(image.convert("RGBA").getchannel("A").getextrema(), (255, 255))
        window._close()

    def test_worker_error_restores_controls(self) -> None:
        with mock.patch.object(window_module, "available_unit_ids", side_effect=FileNotFoundError("missing spikes")), mock.patch.object(window_module.messagebox, "showerror") as showerror:
            window = window_module.CrossCorrelogramWindow(self.root, self.session_dir)
            self.wait_until(lambda: not window._busy)

        self.assertIn("missing spikes", showerror.call_args.args[1])
        self.assertFalse(window.plot_button.instate(["disabled"]))
        self.assertIsNone(window._poll_after)
        window._close()

    def test_closing_during_load_leaves_worker_without_tk_callbacks(self) -> None:
        for close_method in ("_close", "destroy"):
            with self.subTest(close_method=close_method):
                finish = threading.Event()
                entered = threading.Event()

                def available(_session, _probe):
                    entered.set()
                    finish.wait(5)
                    return [302, 368]

                with mock.patch.object(window_module, "available_unit_ids", side_effect=available):
                    window = window_module.CrossCorrelogramWindow(self.root, self.session_dir)
                    self.assertTrue(entered.wait(1))
                    pending = window._poll_after
                    getattr(window, close_method)()
                    self.assertIsNone(window._poll_after)
                    self.assertNotIn(pending, self.root.tk.call("after", "info"))
                    finish.set()
                    self.wait_until(lambda: not window._results.empty())

                self.root.update()
                self.root.report_callback_exception.assert_not_called()


if __name__ == "__main__":
    unittest.main()
