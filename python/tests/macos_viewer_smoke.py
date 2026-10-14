"""Exercise Cocoa menus with one application interpreter, as in the bundled app."""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rfmapping_gui as gui
from macos_key_events import (
    close_window, fullscreen_state, fullscreen_transition_notifications,
    send_click, send_double_click, send_key,
    set_fullscreen_content_limit, toggle_fullscreen, window_number,
)
from rfmapping_viewer.rf_model import RFMappingData


def settle(app) -> None:
    # Allow the viewer's delayed redraw and Cocoa's display pass to complete.
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        app.update()
        time.sleep(0.01)


def capture_rf_canvas(app, path: Path):
    result = subprocess.run(
        ["/usr/sbin/screencapture", "-x", "-o", "-l", str(window_number(app)), str(path)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"Native screen capture failed: {result.stdout}{result.stderr}"
    with Image.open(path) as screenshot:
        scale = screenshot.width / app.winfo_width()
        content_top = screenshot.height - app.winfo_height() * scale
        canvas = app.canvases["rf"]
        left = (canvas.winfo_rootx() - app.winfo_rootx()) * scale
        top = content_top + (canvas.winfo_rooty() - app.winfo_rooty()) * scale
        return screenshot.convert("RGB").crop(tuple(round(value) for value in (
            left + 5 * scale, top + 5 * scale,
            left + (canvas.winfo_width() - 5) * scale,
            top + (canvas.winfo_height() - 5) * scale,
        )))


def check_native_waveform(app) -> None:
    evidence = Path(__file__).resolve().parents[1] / ".test-artifacts" / "waveform-zoom" / f"window-{window_number(app)}"
    evidence.mkdir(parents=True, exist_ok=True)
    events = []
    states = []
    trace_tag = f"NativeWaveformTrace:{app}"
    original_tags = {}

    def record_event(event):
        events.append({
            "type": str(event.type), "widget": str(event.widget),
            "keysym": str(event.keysym), "keycode": str(event.keycode),
            "x": event.x, "y": event.y, "x_root": event.x_root, "y_root": event.y_root,
            "state": str(event.state), "focus": str(app.focus_get()),
            "zoomed": app._waveform_zoomed,
        })

    sequences = ("<KeyPress>", "<KeyRelease>", "<ButtonPress-1>", "<ButtonRelease-1>",
                 "<FocusIn>", "<FocusOut>", "<Map>", "<Unmap>")
    commands = [app._app_root.bind_class(trace_tag, sequence, record_event) for sequence in sequences]
    pending = [app._app_root]
    while pending:
        widget = pending.pop()
        original_tags[widget] = widget.bindtags()
        widget.bindtags((trace_tag, *original_tags[widget]))
        pending.extend(widget.winfo_children())

    def checkpoint(phase):
        widgets = (app.waveform_canvas, app.waveform_zoom_overlay, app.waveform_zoom_canvas,
                   app.waveform_zoom_done_button, app.canvases["rf"])
        states.append({
            "phase": phase, "zoomed": app._waveform_zoomed,
            "focus": str(app.focus_get()), "display_focus": str(app.focus_displayof()),
            "grab": str(app.grab_current()),
            "widgets": [{
                "path": str(widget), "mapped": bool(widget.winfo_ismapped()),
                "manager": widget.winfo_manager(), "geometry": widget.winfo_geometry(),
                "bindtags": widget.bindtags(),
                "center_hit": str(app.winfo_containing(
                    widget.winfo_rootx() + widget.winfo_width() // 2,
                    widget.winfo_rooty() + widget.winfo_height() // 2,
                )),
            } for widget in widgets],
        })

    try:
        with mock.patch.object(app, "_request_waveform_payload"):
            app._waveform_generation += 1
            app._waveform_loading_key = None
            app._waveform_error_key = None
            app.waveform_payload = {
                "matrix": [[-1.0, 0.0, 1.0], [1.0, 0.0, -1.0]],
                "times_ms": [-0.1, 0.0, 0.1], "channel_labels": ["ch 1", "ch 2"],
                "best_channel_row": 0, "amplitude_limit_uv": 1.0, "max_ptp_uv": 2.0,
            }
            app._waveform_payload_key = (
                app._selected_unit_id_value(), app.waveform_channel_mode_var.get(),
            )
            app.show_waveform_var.set(True)
            app._select_tab_key("rf")
            app._draw_waveform()
            settle(app)
            checkpoint("before-double-click")
            assert app.waveform_canvas.winfo_ismapped(), "Compact waveform is not visible"
            assert len(app.waveform_canvas.find_all()) > 20, "Fixture did not draw waveform data"
            subtitle = app.waveform_subtitle_label
            assert int(subtitle.grid_info()["row"]) == 0, "Waveform header wrapped to a second row"
            assert subtitle.winfo_width() >= subtitle.winfo_reqwidth(), "Waveform subtitle is clipped"
            assert app.waveform_canvas.winfo_height() >= 220, "Waveform plot is too short"
            before = capture_rf_canvas(app, evidence / "before.png")

            send_double_click(app.waveform_canvas)
            settle(app)
            checkpoint("after-double-click")
            assert app._waveform_zoomed, "Native double-click did not enlarge the waveform"
            assert app.waveform_zoom_canvas.winfo_ismapped()
            assert len(app.waveform_zoom_canvas.find_all()) > 20
            assert app.focus_get() is app.waveform_zoom_canvas, "Zoom did not establish keyboard focus"
            zoomed = capture_rf_canvas(app, evidence / "zoomed.png")
            assert ImageChops.difference(before, zoomed).getbbox(), "Zoom did not cover the RF plot"

            with (
                mock.patch.object(app, "_clear_spatial_filter") as clear_spatial,
                mock.patch.object(app, "_clear_timeline_selection") as clear_timeline,
            ):
                # No focus override: Escape must follow the real double-click's focus.
                send_key("\x1b", 53)
                settle(app)
                checkpoint("after-escape")
                assert not app._waveform_zoomed, "One native Escape did not close waveform zoom"
                assert not app.waveform_zoom_overlay.winfo_ismapped()
                clear_spatial.assert_not_called()
                clear_timeline.assert_not_called()
            after = capture_rf_canvas(app, evidence / "after-escape.png")
            difference = ImageChops.difference(before, after)
            difference.save(evidence / "rf-difference.png")
            red, green, blue = difference.split()
            histogram = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()
            print(json.dumps({
                "native_waveform_evidence": str(evidence),
                "rf_crop_size": before.size, "difference_bounds": difference.getbbox(),
                "changed_pixel_fraction": 1 - histogram[0] / (before.width * before.height),
            }))
            assert difference.getbbox() is None, "Waveform pixels remain over the restored RF plot"
    except BaseException:
        print(json.dumps({
            "native_waveform_evidence": str(evidence), "states": states, "events": events[-80:],
            "callback_errors": repr(app._app_root.report_callback_exception.call_args_list),
        }, indent=2), file=sys.stderr)
        raise
    finally:
        for widget, tags in original_tags.items():
            widget.bindtags(tags)
        for sequence in sequences:
            app._app_root.unbind_class(trace_tag, sequence)
        for command in commands:
            app._app_root.deletecommand(command)


def check_native_adn_close(app, utilities) -> None:
    states = []
    evidence = Path(__file__).resolve().parents[1] / ".test-artifacts" / "adn-close"
    evidence.mkdir(parents=True, exist_ok=True)
    result_path = evidence / f"window-{window_number(app)}.json"
    with tempfile.TemporaryDirectory() as directory:
        positions = Path(directory) / "ProbeA" / "positions.csv"
        positions.parent.mkdir()
        unit_ids = app._unit_navigation_ids()
        assert len(unit_ids) > 1, "ADN navigation fixture needs multiple units"
        positions.write_text(
            "unit_index,unit_id,x_um,y_um\n" + "".join(
                f"{index},{unit_id},{index * 20},{100 + index * 30}\n"
                for index, unit_id in enumerate(unit_ids)
            ), encoding="utf-8",
        )
        output = Path(directory) / "adn_neuron.npy"
        geometry = gui.load_probe_geometry("ProbeA", positions, None)
        with (
            mock.patch.object(app, "probe_geometry", geometry),
            mock.patch.object(app, "_adn_path", output),
        ):
            try:
                for focus_kind in ("checkbox", "text"):
                    for close_kind in ("button", "titlebar"):
                        # Reproduce the owner input which swallowed arrows on return.
                        app.unit_combo.focus_set()
                        settle(app)
                        assert app.focus_get() is app.unit_combo
                        before = app._selected_unit_id_value()
                        expected = unit_ids[(unit_ids.index(before) + 1) % len(unit_ids)]
                        state = {
                            "utility_focus": focus_kind, "close": close_kind,
                            "before_unit": before, "before_focus": str(app.focus_get()),
                        }
                        states.append(state)
                        utilities.invoke("Annotate ADN…")
                        settle(app)
                        dialog = app._adn_window
                        assert dialog is not None and dialog.winfo_exists()
                        assert dialog.output_path == output
                        controls = []
                        pending = list(dialog.winfo_children())
                        while pending:
                            control = pending.pop()
                            controls.append(control)
                            pending.extend(control.winfo_children())
                        checkbox = next(
                            control for control in controls
                            if isinstance(control, gui.ttk.Checkbutton)
                            and control.cget("text") == "Class 1"
                        )
                        target = checkbox if focus_kind == "checkbox" else dialog.unit_list
                        send_click(target)
                        settle(app)
                        state["interaction_focus"] = str(app.focus_get())
                        if focus_kind == "checkbox":
                            assert not dialog.class_visible_vars[1].get(), "Native click did not toggle the class filter"
                        else:
                            assert app.focus_get() is target, "Native interaction did not focus the ADN text"
                        if close_kind == "button":
                            button = next(
                                control for control in controls
                                if isinstance(control, gui.ttk.Button)
                                and control.cget("text") == "Close"
                            )
                            send_click(button)
                        else:
                            close_window(dialog)
                        settle(app)
                        state["closed"] = not bool(dialog.winfo_exists())
                        state["after_close_focus"] = str(app.focus_get())
                        assert state["closed"], "Native close did not dismiss ADN"
                        window_number(app)  # The owner must regain Cocoa's key window.
                        # No focus correction: arrows must follow the close action.
                        send_key("\uf703", 124, (1 << 21) | (1 << 23))
                        settle(app)
                        state["after_right_unit"] = app._selected_unit_id_value()
                        state["after_right_focus"] = str(app.focus_get())
                        assert state["after_right_unit"] == expected, "Right did not navigate after ADN close"
                        send_key("\uf702", 123, (1 << 21) | (1 << 23))
                        settle(app)
                        state["after_left_unit"] = app._selected_unit_id_value()
                        state["after_left_focus"] = str(app.focus_get())
                        assert state["after_left_unit"] == before, "Left did not navigate after ADN close"
                        assert app.focus_get() is app.canvases[app._active_tab_key()]
                        assert not output.exists(), "ADN close unexpectedly saved an annotation"
            finally:
                result_path.write_text(json.dumps(states, indent=2), encoding="utf-8")
                print(json.dumps({"native_adn_close": states}, indent=2), flush=True)


def check_native_fullscreen(app) -> None:
    evidence = Path(__file__).resolve().parents[1] / ".test-artifacts" / "fullscreen"
    evidence.mkdir(parents=True, exist_ok=True)
    states = []
    # Keep the normal fixture inside the CI display's usable area. AppKit
    # constrains oversized windows when restoring them from full screen.
    app.minsize(800, 500)
    app.geometry("900x600+40+40")
    app.lift()
    app.focus_force()
    settle(app)
    before = fullscreen_state(app)
    states.append({"phase": "before", **before})

    def wait_for_geometry(predicate):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            app.update()
            state = fullscreen_state(app)
            if predicate(state):
                settle(app)
                state = fullscreen_state(app)
                if predicate(state):
                    return state
            time.sleep(0.02)
        raise AssertionError(f"Native fullscreen geometry did not settle: {state}")

    def fills_screen(state):
        return (
            state["fullscreen"]
            and all(abs(actual - expected) <= 2 for actual, expected in zip(state["frame"], state["screen"]))
            and all(abs(actual - expected) <= 2 for actual, expected in zip(state["tk"], state["content"]))
            and state["content"][0] >= state["screen"][2] - 2
            and state["content"][1] >= state["screen"][3] - 40
        )

    try:
        # Reproduce a window created on a smaller display before moving it to
        # the current display; the old bridge silently left this cap in place.
        set_fullscreen_content_limit(app, before["screen"][2] / 2, before["screen"][3] / 2)
        states.append({"phase": "stale-display-limit", **fullscreen_state(app)})
        assert gui.allow_macos_fullscreen_resize(app), "Native fullscreen sizing helper did not apply"
        states.append({"phase": "limit-cleared", **fullscreen_state(app)})
        capture_rf_canvas(app, evidence / "before.png")
        with fullscreen_transition_notifications(app, entering=True) as completed:
            toggle_fullscreen(app)
            full = wait_for_geometry(lambda state: completed() > 0 and fills_screen(state))
            full["completed_notifications"] = completed()
        states.append({"phase": "fullscreen", **full})
        capture_rf_canvas(app, evidence / "fullscreen.png")
        with Image.open(evidence / "fullscreen.png") as screenshot:
            rgb = screenshot.convert("RGB")
            # Solid unpainted margins are distinct from a dark plot or label.
            edge_pixels = [rgb.getpixel((rgb.width - 24, y)) for y in range(60, rgb.height - 30, 20)]
            edge_pixels += [rgb.getpixel((x, rgb.height - 24)) for x in range(30, rgb.width - 30, 20)]
            assert sum(max(pixel) < 16 for pixel in edge_pixels) < len(edge_pixels) * 0.1, (
                "Native fullscreen has unpainted black right or bottom margins"
            )
        with fullscreen_transition_notifications(app, entering=False) as completed:
            toggle_fullscreen(app)
            restored = wait_for_geometry(lambda state: (
                completed() > 0 and not state["fullscreen"]
                and all(abs(actual - expected) <= 2 for actual, expected in zip(state["frame"], before["frame"]))
                and all(abs(actual - expected) <= 2 for actual, expected in zip(state["tk"], before["tk"]))
            ))
            restored["completed_notifications"] = completed()
        states.append({"phase": "restored", **restored})
        capture_rf_canvas(app, evidence / "restored.png")
    finally:
        states.append({"phase": "final", **fullscreen_state(app)})
        (evidence / "geometry.json").write_text(json.dumps(states, indent=2), encoding="utf-8")
        print(json.dumps({"native_fullscreen_evidence": str(evidence), "geometry": states}, indent=2), flush=True)
        if fullscreen_state(app)["fullscreen"]:
            toggle_fullscreen(app)
            wait_for_geometry(lambda state: not state["fullscreen"])


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
                previous_data = child.data
                selected_unit = child._selected_unit_id_value()
                send_key("r", 15, 1 << 20)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    child.update()
                    if child.data is not previous_data and not child._reload_active:
                        break
                    time.sleep(0.01)
                assert child.data is not previous_data, "Native Command+R did not reload"
                assert not child._reload_active, "Native reload did not finish"
                assert child._selected_unit_id_value() == selected_unit, "Reload reset the selected unit"
                assert child.polar_layout_var.get() and child.display_expanded_var.get(), "Reload reset display controls"
                print(json.dumps({"native_reload_unit": selected_unit, "index": child.unit_idx.get()}))
                with mock.patch.object(child, "_open_figure_exporter") as export:
                    send_key("e", 14, 1 << 20)
                    child.update()
                    export.assert_called_once_with()
                with mock.patch.object(child, "_show_settings") as settings:
                    send_key(",", 43, 1 << 20)
                    child.update()
                    settings.assert_called()

                check_native_waveform(child)

                apple = child._menu.nametowidget(child._menu.entrycget(0, "menu"))
                utilities = apple.nametowidget(apple.entrycget(0, "menu"))
                check_native_adn_close(child, utilities)
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
            check_native_fullscreen(app)
            root.report_callback_exception.assert_not_called()
        finally:
            app._quit_application()


if __name__ == "__main__":
    check_native_keys(Path(sys.argv[1]))
