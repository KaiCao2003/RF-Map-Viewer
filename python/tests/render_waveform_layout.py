"""Save the minimum-size viewer and its waveform pane for visual review."""

from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest import mock

import numpy as np
from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rfmapping_gui as gui
from gui_test_support import base_payload, tk_test_root, write_payload
from rfmapping_viewer.rf_model import RFMappingData
from rfmapping_viewer.settings import ViewerSettings, save_viewer_settings


def render(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    tk_test_root()
    data_directory, path = write_payload(base_payload())
    with tempfile.TemporaryDirectory() as settings_directory:
        preferences = Path(settings_directory) / "settings.json"
        save_viewer_settings(replace(
            ViewerSettings(), rf_filter_units_with_zero_bins=False,
            auto_load_probe_layout=False, auto_load_tuning_curve=False,
        ), preferences)
        with (
            mock.patch.object(gui, "viewer_settings_path", return_value=preferences),
            mock.patch.object(gui, "list_recent_documents", return_value=[]),
            mock.patch.object(gui, "record_recent_document"),
            mock.patch.object(gui.RFMViewer, "_request_waveform_payload"),
        ):
            viewer = gui.RFMViewer(RFMappingData(path))
            try:
                times = np.linspace(-0.5, 0.75, 81)
                trace = -90 * np.exp(-(times / 0.08) ** 2)
                trace += 35 * np.exp(-((times - 0.2) / 0.12) ** 2)
                viewer.waveform_payload = {
                    "matrix": np.asarray([trace * scale for scale in (0.3, 0.6, 1, 0.7, 0.4)]),
                    "times_ms": times,
                    "channel_labels": [f"ch {channel}" for channel in range(51, 56)],
                    "best_channel_row": 2,
                    "amplitude_limit_uv": 90.0,
                    "max_ptp_uv": 125.0,
                }
                viewer._waveform_payload_key = (
                    viewer._selected_unit_id_value(), viewer.waveform_channel_mode_var.get(),
                )
                viewer._sidebar_auto_visibility = False
                viewer.probe_collapsed_var.set(False)
                viewer.geometry("1120x720+0+0")
                viewer._select_tab_key("rf")
                viewer._draw_waveform()
                deadline = time.monotonic() + 0.4
                while time.monotonic() < deadline:
                    viewer.update()
                    time.sleep(0.01)
                label = viewer.waveform_subtitle_label
                assert int(label.grid_info()["row"]) == 0
                assert label.winfo_width() >= label.winfo_reqwidth()
                assert viewer.waveform_canvas.winfo_height() >= 220
                for name, widget in (("viewer", viewer), ("waveform", viewer.waveform_pane)):
                    left, top = widget.winfo_rootx(), widget.winfo_rooty()
                    ImageGrab.grab(bbox=(
                        left, top, left + widget.winfo_width(), top + widget.winfo_height(),
                    )).save(output / f"{name}.png")
                print(json.dumps({
                    "waveform": viewer.waveform_canvas.winfo_geometry(),
                    "subtitle": label.cget("text"),
                    "subtitle_width": label.winfo_width(),
                    "subtitle_required_width": label.winfo_reqwidth(),
                    "output": str(output.resolve()),
                }))
            finally:
                viewer._quit_application()
                data_directory.cleanup()


if __name__ == "__main__":
    render(Path(sys.argv[1]))
