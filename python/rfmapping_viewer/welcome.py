"""Nonmodal starting point for RF documents and application utilities."""

from __future__ import annotations

import sys
from pathlib import Path
from tkinter import font as tkfont
from typing import Callable, Sequence

from PIL import Image, ImageDraw, ImageTk

from rfmapping_viewer.tk_support import tk, ttk


def _rounded_rectangle(canvas, x1, y1, x2, y2, radius, *, fill):
    width, height = int(x2 - x1), int(y2 - y1)
    if width < 2 or height < 2:
        return
    key = (width, height, radius, fill)
    if not hasattr(canvas, "_rounded_images"):
        canvas._rounded_images = {}
    if key not in canvas._rounded_images:
        # Tk's polygon corners are aliased on macOS; supersample the surface.
        bitmap = Image.new("RGB", (width * 4, height * 4), canvas.cget("background"))
        ImageDraw.Draw(bitmap).rounded_rectangle((0, 0, width * 4 - 1, height * 4 - 1),
                                                radius=radius * 4, fill=fill)
        canvas._rounded_images[key] = ImageTk.PhotoImage(
            bitmap.resize((width, height), Image.Resampling.LANCZOS), master=canvas)
    canvas.create_image(x1, y1, anchor="nw", image=canvas._rounded_images[key])


class WelcomeButton(ttk.Button):
    def __init__(self, master, *, command, close=False):
        root = master._root()
        style = ttk.Style(root)
        name = "WelcomeClose" if close else "WelcomeOpen"
        element = name + ".border"
        if element not in style.element_names():
            # Skin only the bezel; ttk retains button semantics and bindings.
            width = 36 if close else 360
            images = []
            for pressed, focused in ((False, False), (True, False), (False, True)):
                color = "#dedede" if pressed else "#f8f8f8" if close else "#ececec"
                bitmap = Image.new("RGB", (width * 4, 144), "white")
                draw = ImageDraw.Draw(bitmap)
                draw.rounded_rectangle((0, 0, width * 4 - 1, 143), radius=72,
                                       fill=color, outline="#7ca8e8" if focused else color, width=6)
                if close:
                    draw.line((48, 48, 96, 96), fill="#a5a5a5", width=6)
                    draw.line((96, 48, 48, 96), fill="#a5a5a5", width=6)
                images.append(ImageTk.PhotoImage(bitmap.resize((width, 36), Image.Resampling.LANCZOS), master=root))
            setattr(root, "_" + name + "_images", images)
            style.element_create(element, "image", images[0], ("pressed", images[1]),
                                 ("focus", images[2]), sticky="nswe")
            layout = {"sticky": "nswe"}
            if not close:
                layout["children"] = [("Button.label", {"sticky": "nswe"})]
            style.layout(name + ".TButton", [(element, layout)])
            family = root.tk.call("font", "actual", "TkDefaultFont", "-family")
            style.configure(name + ".TButton", font=(family, -13), foreground="#242424", anchor="center")
            style.map(name + ".TButton", foreground=[("pressed", "#242424")])
        super().__init__(master, text="Close Window" if close else "Open…", command=command,
                         style=name + ".TButton", takefocus=True)
        self.bind("<Return>", lambda event: self.invoke())


class RecentDocumentList(tk.Canvas):
    """A compact, keyboard-accessible list with filename and path typography."""

    _row_height = 48

    def __init__(self, master, *, open_recent):
        super().__init__(master, background="#f7f7f7", highlightthickness=0, takefocus=True)
        self.paths: tuple[Path, ...] = ()
        self._icons = {}
        self.selected: int | None = None
        self._open_recent = open_recent
        family = self.tk.call("font", "actual", "TkDefaultFont", "-family")
        self._name_font = tkfont.Font(self, family=family, size=-13)
        self._path_font = tkfont.Font(self, family=family, size=-11)
        self.bind("<Configure>", self._draw)
        self.bind("<FocusIn>", self._draw)
        self.bind("<FocusOut>", self._draw)
        self.bind("<Button-1>", self._click)
        self.bind("<Double-1>", self._double_click)
        self.bind("<Return>", self.open_selected)
        self.bind("<Up>", lambda event: self._step(-1))
        self.bind("<Down>", lambda event: self._step(1))
        self.bind("<Home>", lambda event: self.select(0))
        self.bind("<End>", lambda event: self.select(len(self.paths) - 1))
        self._tk9_scrolling = int(str(self.tk.call("package", "present", "Tk")).split(".")[0]) >= 9
        self.bind("<MouseWheel>", self._scroll)
        if self._tk9_scrolling:
            self.bind("<TouchpadScroll>", lambda event: self._scroll(event, precise=True))
        self.bind("<Button-4>", lambda event: self.yview_scroll(-16 if self._tk9_scrolling else -1, "units"))
        self.bind("<Button-5>", lambda event: self.yview_scroll(16 if self._tk9_scrolling else 1, "units"))
        self.configure(yscrollincrement=1 if self._tk9_scrolling else 16)

    @property
    def selected_path(self) -> Path | None:
        return self.paths[self.selected] if self.selected is not None else None

    def refresh(self, paths: Sequence[Path]) -> None:
        previous = self.selected_path
        self.paths = tuple(paths)
        if "nsimage" in self.tk.call("image", "types"):
            self._icons = {
                path: tk.Image("nsimage", master=self, cnf={
                    "source": str(path), "as": "path", "width": 24, "height": 24,
                }) for path in self.paths
            }
        self.selected = None
        self.configure(scrollregion=(0, 0, 0, len(self.paths) * self._row_height))
        self.yview_moveto(0)
        self.select(self.paths.index(previous) if previous in self.paths else 0)

    def select(self, index: int) -> str:
        self.selected = min(max(index, 0), len(self.paths) - 1) if self.paths else None
        if self.selected is not None and self.winfo_ismapped():
            top = self.selected * self._row_height
            bottom = top + self._row_height
            height = self.winfo_height()
            if top < self.canvasy(0):
                self.yview_moveto(top / (len(self.paths) * self._row_height))
            elif bottom > self.canvasy(height):
                self.yview_moveto((bottom - height) / (len(self.paths) * self._row_height))
        self._draw()
        return "break"

    def open_selected(self, _event=None) -> str:
        if self.selected_path is not None:
            self._open_recent(self.selected_path)
        return "break"

    def _step(self, delta: int) -> str:
        return self.select((self.selected or 0) + delta)

    def _click(self, event) -> None:
        self.focus_set()
        index = int(self.canvasy(event.y) // self._row_height)
        if index < len(self.paths):
            self.select(index)

    def _double_click(self, event) -> str:
        index = int(self.canvasy(event.y) // self._row_height)
        if index < len(self.paths):
            self.select(index)
            self.open_selected()
        return "break"

    def _scroll(self, event, *, precise=False) -> str:
        if precise:
            # Tk 9 packs signed horizontal/vertical pixel deltas into %D.
            _, delta_y = self.tk.call("tk::PreciseScrollDeltas", event.delta)
            delta = -int(delta_y)
        elif self._tk9_scrolling:
            delta = -int(event.delta / 120) * 16
        else:
            delta = -event.delta if sys.platform == "darwin" else -int(event.delta / 120)
        self.yview_scroll(delta, "units")
        return "break"

    @staticmethod
    def _abbreviate(text: str, font: tkfont.Font, width: int, *, keep_end=False) -> str:
        if font.measure(text) <= width:
            return text
        # Binary search avoids repeatedly measuring an entire long network path.
        low, high = 0, len(text)
        while low < high:
            count = (low + high + 1) // 2
            candidate = "…" + text[-count:] if keep_end else text[:count] + "…"
            if font.measure(candidate) <= width:
                low = count
            else:
                high = count - 1
        if not low:
            return "…"
        return "…" + text[-low:] if keep_end else text[:low] + "…"

    def _draw(self, _event=None) -> None:
        self.delete("all")
        width = self.winfo_width()
        for index, path in enumerate(self.paths):
            y = index * self._row_height
            if index == self.selected:
                _rounded_rectangle(self, 0, y, width, y + 47, 10,
                                   fill="#dce8f8" if self.focus_get() == self else "#dedede")
            # The folded document outline keeps rows recognizable without extra copy.
            if path in self._icons:
                self.create_image(12, y + 12, anchor="nw", image=self._icons[path])
            else:
                self.create_polygon(14, y + 11, 27, y + 11, 33, y + 17, 33, y + 36,
                                    14, y + 36, fill="white", outline="#a7a7ad", width=1)
                self.create_line(27, y + 11, 27, y + 17, 33, y + 17, fill="#a7a7ad")
                self.create_line(18, y + 24, 29, y + 24, fill="#c2c2c7")
                self.create_line(18, y + 29, 27, y + 29, fill="#c2c2c7")
            available = max(width - 58, 0)
            self.create_text(48, y + 16, anchor="w", fill="#252528", font=self._name_font,
                             text=self._abbreviate(path.name, self._name_font, available))
            parent = path.parent
            home = Path.home()
            display = str(Path("~") / parent.relative_to(home)) if parent.is_relative_to(home) else str(parent)
            self.create_text(48, y + 33, anchor="w", fill="#74747a", font=self._path_font,
                             text=self._abbreviate(display, self._path_font, available, keep_end=True))


class WelcomeFrame(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        open_document: Callable[[], None],
        open_recent: Callable[[Path], None],
        close_window: Callable[[], None],
    ) -> None:
        super().__init__(master, background="white")
        family = self.tk.call("font", "actual", "TkDefaultFont", "-family")
        self.close_button = WelcomeButton(self, command=close_window, close=True)
        self.close_button.place(x=20, y=20, width=36, height=36)
        bundled = getattr(sys, "frozen", False)
        icon_path = (Path(sys.executable).parent.parent / "Resources" / "RFMappingViewer.icns"
                     if bundled
                     else Path(__file__).parent.parent / "assets" / "rf-mapping-viewer-icon-1024.png")
        if icon_path.is_file():
            if "nsimage" in self.tk.call("image", "types"):
                # Use the system-rendered bundle icon, matching the Swift welcome.
                self._icon = tk.Image("nsimage", master=self, cnf={
                    "source": str(icon_path.parents[2] if bundled else icon_path),
                    "as": "path" if bundled else "file", "width": 128, "height": 128,
                })
            else:
                with Image.open(icon_path) as icon:
                    self._icon = ImageTk.PhotoImage(icon.convert("RGBA").resize((128, 128), Image.Resampling.LANCZOS), master=self)
            tk.Label(self, image=self._icon, background="white", borderwidth=0).place(relx=0.5, y=62, anchor="n")
        # Pixel sizes avoid Tk's 96-dpi point conversion enlarging the macOS type.
        tk.Label(self, text="RF Map Viewer", background="white", foreground="#202023",
                 font=(family, -18, "bold"), borderwidth=0, padx=0, pady=0).place(
                     relx=0.5, y=190, anchor="n", height=22)
        self.open_button = WelcomeButton(self, command=open_document)
        self.open_button.place(relx=0.5, y=242, anchor="n", width=360, height=36)

        recent = tk.Canvas(self, background="white", highlightthickness=0)
        recent.place(relx=0.5, y=294, anchor="n", width=360, height=278)
        _rounded_rectangle(recent, 0, 0, 360, 278, 16, fill="#f7f7f7")
        self.recent_list = RecentDocumentList(recent, open_recent=open_recent)
        self.recent_list.place(x=8, y=8, width=344, height=262)
        self._scrollbar = ttk.Scrollbar(recent, orient="vertical", command=self.recent_list.yview)
        self.recent_list.configure(yscrollcommand=self._update_scrollbar)
        self.refresh_recent_documents(())

    def refresh_recent_documents(self, paths: Sequence[Path]) -> None:
        self.recent_list.refresh(paths)

    def _update_scrollbar(self, first, last) -> None:
        self._scrollbar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            self._scrollbar.place_forget()
            self.recent_list.place_configure(width=344)
        else:
            self._scrollbar.place(x=338, y=12, height=254)
            self.recent_list.place_configure(width=326)
