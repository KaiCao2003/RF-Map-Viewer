"""Exercise Cocoa menus with one application interpreter, as in the bundled app."""

import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rfmapping_gui as gui
from macos_key_events import send_key
from rfmapping_viewer.rf_model import RFMappingData


def check_native_keys(path: Path) -> None:
    with tempfile.TemporaryDirectory() as directory, mock.patch.object(
        gui, "viewer_settings_path", return_value=Path(directory) / "settings.json",
    ):
        app = gui.RFMViewer(RFMappingData(path))
        root = app._app_root
        root.report_callback_exception = mock.Mock()
        try:
            # Replacing document and utility menus must leave native dispatch usable.
            for _ in range(2):
                child = gui.RFMViewer(RFMappingData(path), master=root)
                app.update()
                # Opening the document must establish focus without focus_force().
                send_key("p", 35)
                child.update()
                assert child.polar_layout_var.get(), "P did not reach the document"
                send_key("d", 2)
                child.update()
                assert child.display_expanded_var.get(), "D did not reach the document"
                send_key("\uf703", 124, (1 << 21) | (1 << 23))
                child.update()
                assert child.unit_idx.get() == 1, "Right did not select the next unit"
                with mock.patch.object(child, "_open_figure_exporter") as export:
                    send_key("e", 14, 1 << 20)
                    child.update()
                    export.assert_called_once_with()
                with mock.patch.object(child, "_show_settings") as settings:
                    send_key(",", 43, 1 << 20)
                    child.update()
                    settings.assert_called()

                apple = child._menu.nametowidget(child._menu.entrycget(0, "menu"))
                utilities = apple.nametowidget(apple.entrycget(0, "menu"))
                utilities.invoke("Cross-correlogram…")
                utility = root._rfm_crosscorrelogram_window
                assert utility is not None and utility.winfo_exists()
                utility.lift()
                utility.focus_force()
                utility.update()
                with mock.patch.object(child, "_show_settings") as settings:
                    send_key(",", 43, 1 << 20)
                    utility.update()
                    settings.assert_called()
                utility.destroy()
                child.destroy()
                app.update()
            root.report_callback_exception.assert_not_called()
        finally:
            app._quit_application()


if __name__ == "__main__":
    check_native_keys(Path(sys.argv[1]))
