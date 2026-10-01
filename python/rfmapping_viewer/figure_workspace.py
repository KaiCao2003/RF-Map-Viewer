"""Direct manipulation of figure frames, using the cached page preview."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PIL import Image, ImageTk

from rfmapping_viewer.figure_export import DEFAULT_PAGE_SIZE
from rfmapping_viewer.figure_layout import (
    FRAME_PRESETS,
    GRID_COLUMNS,
    GRID_ROWS,
    Frame,
    arrange_frames,
    frame_pixel_box,
    page_grid_box,
)
from rfmapping_viewer.tk_support import tk


class FigurePageCanvas(tk.Canvas):
    """Move cached plot pixels immediately; render the new recipe on release."""

    def __init__(
        self,
        master,
        *,
        on_select: Callable[[int], None],
        on_change: Callable[[tuple[Frame, ...], int], None],
        on_error: Callable[[str], None],
    ):
        super().__init__(
            master,
            background="#eef0f4",
            highlightthickness=0,
            borderwidth=0,
            takefocus=True,
            width=640,
            height=480,
        )
        self.on_select = on_select
        self.on_change = on_change
        self.on_error = on_error
        self.frames: tuple[Frame, ...] = ()
        self.titles: tuple[str, ...] = ()
        self.selected: int | None = None
        self._image: Image.Image | None = None
        self._photo = None
        self._ghost_photo = None
        self._message = "Preparing preview…"
        self._scale = 1.0
        self._origin = (0.0, 0.0)
        self._gesture: dict | None = None
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Escape>", self._cancel)
        self.bind("<Left>", lambda event: self._key_move(event, -1, 0))
        self.bind("<Right>", lambda event: self._key_move(event, 1, 0))
        self.bind("<Up>", lambda event: self._key_move(event, 0, -1))
        self.bind("<Down>", lambda event: self._key_move(event, 0, 1))

    def set_frames(self, frames: Sequence[Frame], titles: Sequence[str], selected=None):
        self._cancel()
        # Keep the released layout visible while its full-resolution render is
        # in flight. Dragging and window resizing never run the plot renderer.
        if (
            self._image is not None
            and tuple(titles) == self.titles
            and len(frames) == len(self.frames)
            and tuple(frames) != self.frames
        ):
            composed = Image.new("RGB", DEFAULT_PAGE_SIZE, "white")
            header = self._image.crop(
                (0, 0, DEFAULT_PAGE_SIZE[0], page_grid_box(DEFAULT_PAGE_SIZE)[1])
            )
            composed.paste(header, (0, 0))
            header.close()
            for old, new in zip(self.frames, frames):
                crop = self._image.crop(frame_pixel_box(old, DEFAULT_PAGE_SIZE))
                left, top, right, bottom = frame_pixel_box(new, DEFAULT_PAGE_SIZE)
                resized = crop.resize(
                    (right - left, bottom - top), Image.Resampling.BILINEAR
                )
                crop.close()
                composed.paste(resized, (left, top))
                resized.close()
            self._image.close()
            self._image = composed
        self.frames = tuple(frames)
        self.titles = tuple(titles)
        self.selected = selected
        self._draw()

    def set_image(self, image: Image.Image) -> None:
        if self._image is not None:
            self._image.close()
        self._image = image.copy()
        self._message = ""
        self._draw()

    def set_message(self, message: str) -> None:
        if self._image is not None:
            self._image.close()
            self._image = None
        self._message = message
        self._draw()

    def _box(self, frame: Frame) -> tuple[float, float, float, float]:
        x0, y0 = self._origin
        left, top, right, bottom = frame_pixel_box(frame, DEFAULT_PAGE_SIZE)
        return (
            x0 + left * self._scale,
            y0 + top * self._scale,
            x0 + right * self._scale,
            y0 + bottom * self._scale,
        )

    def _draw(self) -> None:
        if self._gesture is not None:
            return
        self.delete("all")
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        page_width, page_height = DEFAULT_PAGE_SIZE
        self._scale = min(
            max(1, width - 36) / page_width, max(1, height - 36) / page_height
        )
        shown_width, shown_height = round(page_width * self._scale), round(
            page_height * self._scale
        )
        x0, y0 = (width - shown_width) / 2, (height - shown_height) / 2
        self._origin = x0, y0
        self.create_rectangle(
            x0 + 3,
            y0 + 5,
            x0 + shown_width + 3,
            y0 + shown_height + 5,
            fill="#d9dde5",
            outline="",
        )
        self.create_rectangle(
            x0, y0, x0 + shown_width, y0 + shown_height, fill="white", outline="#d8dde5"
        )
        if self._image is not None:
            resized = self._image.resize(
                (max(1, shown_width), max(1, shown_height)), Image.Resampling.LANCZOS
            )
            self._photo = ImageTk.PhotoImage(resized, master=self)
            resized.close()
            self.create_image(x0, y0, image=self._photo, anchor="nw")
        else:
            self._photo = None
            for index, frame in enumerate(self.frames):
                box = self._box(frame)
                self.create_rectangle(*box, fill="#f8f9fb", outline="#e2e5eb")
                self.create_text(
                    (box[0] + box[2]) / 2,
                    (box[1] + box[3]) / 2,
                    text=self.titles[index],
                    fill="#667085",
                    width=max(40, box[2] - box[0] - 12),
                )
            if self._message:
                self.create_text(
                    width / 2,
                    height / 2,
                    text=self._message,
                    fill="#667085",
                    font=("TkDefaultFont", 11),
                    width=max(100, width - 80),
                    justify="center",
                )
        self._draw_selection()

    def _draw_selection(self) -> None:
        self.delete("selection")
        if self.selected is None or self.selected >= len(self.frames):
            return
        left, top, right, bottom = self._box(self.frames[self.selected])
        self.create_rectangle(
            left, top, right, bottom, outline="#007aff", width=2, tags="selection"
        )
        self.create_rectangle(
            right - 10,
            bottom - 10,
            right + 3,
            bottom + 3,
            fill="#007aff",
            outline="white",
            width=2,
            tags="selection",
        )

    def _press(self, event) -> None:
        self.focus_set()
        for index in reversed(range(len(self.frames))):
            left, top, right, bottom = self._box(self.frames[index])
            if left - 4 <= event.x <= right + 4 and top - 4 <= event.y <= bottom + 4:
                self.selected = index
                self.on_select(index)
                self._draw_selection()
                self._gesture = {
                    "index": index,
                    "start": (event.x, event.y),
                    "box": (left, top, right, bottom),
                    "frame": self.frames[index],
                    "resize": event.x >= right - 16 and event.y >= bottom - 16,
                    "target": self.frames[index],
                    "moved": False,
                }
                self.grab_set()
                return

    def _motion(self, event) -> None:
        gesture = self._gesture
        if gesture is None:
            return
        dx, dy = event.x - gesture["start"][0], event.y - gesture["start"][1]
        if not gesture["moved"] and abs(dx) + abs(dy) < 4:
            return
        gesture["moved"] = True
        column, row, span_x, span_y = gesture["frame"]
        grid_left, grid_top, grid_right, grid_bottom = page_grid_box(DEFAULT_PAGE_SIZE)
        cell_x = (grid_right - grid_left) / GRID_COLUMNS * self._scale
        cell_y = (grid_bottom - grid_top) / GRID_ROWS * self._scale
        left, top, right, bottom = gesture["box"]
        if gesture["resize"]:
            wanted_x = span_x + dx / cell_x
            wanted_y = span_y + dy / cell_y
            span_x, span_y = min(
                FRAME_PRESETS.values(),
                key=lambda size: (size[0] - wanted_x) ** 2 + (size[1] - wanted_y) ** 2,
            )
            column, row = min(column, GRID_COLUMNS - span_x), min(
                row, GRID_ROWS - span_y
            )
            ghost_box = (
                left,
                top,
                max(left + 30, right + dx),
                max(top + 30, bottom + dy),
            )
        else:
            column = max(0, min(GRID_COLUMNS - span_x, column + round(dx / cell_x)))
            row = max(0, min(GRID_ROWS - span_y, row + round(dy / cell_y)))
            ghost_box = (left + dx, top + dy, right + dx, bottom + dy)
        gesture["target"] = (column, row, span_x, span_y)
        self.delete("gesture")
        self.create_rectangle(*gesture["box"], fill="white", outline="", tags="gesture")
        if self._image is not None:
            crop = self._image.crop(
                frame_pixel_box(gesture["frame"], DEFAULT_PAGE_SIZE)
            )
            size = (
                max(1, round(ghost_box[2] - ghost_box[0])),
                max(1, round(ghost_box[3] - ghost_box[1])),
            )
            resized = crop.resize(size, Image.Resampling.BILINEAR)
            crop.close()
            self._ghost_photo = ImageTk.PhotoImage(resized, master=self)
            resized.close()
            self.create_image(
                ghost_box[0],
                ghost_box[1],
                image=self._ghost_photo,
                anchor="nw",
                tags="gesture",
            )
        else:
            self.create_rectangle(
                *ghost_box, fill="#f8f9fb", outline="", tags="gesture"
            )
        self.create_rectangle(*ghost_box, outline="#007aff", width=2, tags="gesture")
        try:
            arrange_frames(self.frames, gesture["index"], gesture["target"])
            color = "#007aff"
        except ValueError:
            color = "#c43b32"
        self.create_rectangle(
            *self._box(gesture["target"]),
            outline=color,
            width=2,
            dash=(5, 4),
            tags="gesture",
        )

    def _release(self, _event=None) -> None:
        gesture = self._gesture
        if gesture is None:
            return
        self._gesture = None
        self.grab_release()
        self._ghost_photo = None
        self._draw()
        if not gesture["moved"] or gesture["target"] == gesture["frame"]:
            return
        try:
            frames = arrange_frames(self.frames, gesture["index"], gesture["target"])
        except ValueError:
            self.on_error("Frame does not fit.")
        else:
            self.on_change(frames, gesture["index"])

    def _cancel(self, _event=None) -> str:
        if self._gesture is not None:
            self._gesture = None
            self.grab_release()
            self._ghost_photo = None
            self._draw()
        return "break"

    def _key_move(self, _event, dx: int, dy: int) -> str:
        if self.selected is None:
            return "break"
        column, row, span_x, span_y = self.frames[self.selected]
        target = (
            max(0, min(GRID_COLUMNS - span_x, column + dx)),
            max(0, min(GRID_ROWS - span_y, row + dy)),
            span_x,
            span_y,
        )
        try:
            frames = arrange_frames(self.frames, self.selected, target)
        except ValueError:
            self.on_error("No space at that position.")
        else:
            self.on_change(frames, self.selected)
        return "break"

    def destroy(self) -> None:
        self._cancel()
        if self._image is not None:
            self._image.close()
            self._image = None
        super().destroy()
