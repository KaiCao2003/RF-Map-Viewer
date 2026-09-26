"""Nonmodal starting point for RF documents and application utilities."""

from __future__ import annotations

import sys
from pathlib import Path
from tkinter import font as tkfont
from typing import Callable, Sequence

from PIL import Image, ImageTk

from rfmapping_viewer.tk_support import tk, ttk


class RecentDocumentList(tk.Canvas):
    """A compact, keyboard-accessible list with filename and path typography."""

    _row_height = 64

    def __init__(self, master, *, open_recent, selection_changed):
        super().__init__(master, background="white", highlightthickness=0, takefocus=True)
        self.paths: tuple[Path, ...] = ()
        self.selected: int | None = None
        self._open_recent = open_recent
        self._selection_changed = selection_changed
        family = self.tk.call("font", "actual", "TkDefaultFont", "-family")
        self._name_font = tkfont.Font(self, family=family, size=12, weight="bold")
        self._path_font = tkfont.Font(self, family=family, size=10)
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
        self.bind("<MouseWheel>", self._scroll)
        self.bind("<Button-4>", lambda event: self.yview_scroll(-1, "units"))
        self.bind("<Button-5>", lambda event: self.yview_scroll(1, "units"))
        self.configure(yscrollincrement=16)

    @property
    def selected_path(self) -> Path | None:
        return self.paths[self.selected] if self.selected is not None else None

    def refresh(self, paths: Sequence[Path]) -> None:
        previous = self.selected_path
        self.paths = tuple(paths)
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
        self._selection_changed()
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

    def _scroll(self, event) -> str:
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
                color = "#e7f0fd" if self.focus_get() == self else "#f0f0f2"
                self.create_polygon(
                    10, y + 2, width - 10, y + 2, width - 3, y + 9,
                    width - 3, y + 55, width - 10, y + 62, 10, y + 62,
                    3, y + 55, 3, y + 9, smooth=True, splinesteps=16,
                    fill=color, outline="",
                )
            # The folded document outline keeps rows recognizable without extra copy.
            self.create_polygon(17, y + 17, 32, y + 17, 39, y + 24, 39, y + 46,
                                17, y + 46, fill="white", outline="#a7a7ad", width=1)
            self.create_line(32, y + 17, 32, y + 24, 39, y + 24, fill="#a7a7ad")
            self.create_line(22, y + 32, 34, y + 32, fill="#c2c2c7")
            self.create_line(22, y + 37, 31, y + 37, fill="#c2c2c7")
            available = max(width - 72, 0)
            self.create_text(53, y + 22, anchor="w", fill="#252528", font=self._name_font,
                             text=self._abbreviate(path.name, self._name_font, available))
            parent = path.parent
            home = Path.home()
            display = str(Path("~") / parent.relative_to(home)) if parent.is_relative_to(home) else str(parent)
            self.create_text(53, y + 42, anchor="w", fill="#74747a", font=self._path_font,
                             text=self._abbreviate(display, self._path_font, available, keep_end=True))


class WelcomeFrame(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        open_document: Callable[[], None],
        open_recent: Callable[[Path], None],
        clear_recent: Callable[[], None],
    ) -> None:
        super().__init__(master, background="white")
        self.columnconfigure(2, weight=1)
        self.rowconfigure(0, weight=1)
        family = self.tk.call("font", "actual", "TkDefaultFont", "-family")

        introduction = tk.Frame(self, width=318, background="#f6f6f7")
        introduction.grid(row=0, column=0, sticky="nsew")
        introduction.grid_propagate(False)
        content = tk.Frame(introduction, background="#f6f6f7")
        content.place(relx=0.5, rely=0.47, anchor="center")
        icon_path = (Path(sys.executable).parent.parent / "Resources" / "RFMappingViewer.icns"
                     if getattr(sys, "frozen", False)
                     else Path(__file__).parent.parent / "assets" / "rf-mapping-viewer-icon-1024.png")
        if icon_path.is_file():
            with Image.open(icon_path) as icon:
                self._icon = ImageTk.PhotoImage(icon.convert("RGBA").resize((128, 128), Image.Resampling.LANCZOS), master=self)
            tk.Label(content, image=self._icon, background="#f6f6f7", borderwidth=0).pack()
        tk.Label(content, text="RF Map Viewer", background="#f6f6f7", foreground="#202023",
                 font=(family, 21, "bold")).pack(pady=(8, 0))
        self.open_button = ttk.Button(content, text="Open RF Map…", command=open_document, width=19)
        self.open_button.pack(pady=(28, 0))
        tk.Frame(self, width=1, background="#dedee2").grid(row=0, column=1, sticky="ns")

        recent = tk.Frame(self, background="white", padx=20, pady=24)
        recent.grid(row=0, column=2, sticky="nsew")
        recent.columnconfigure(0, weight=1)
        recent.rowconfigure(1, weight=1)
        tk.Label(recent, text="Recent", font=(family, 12, "bold"), foreground="#606066",
                 background="white", anchor="w").grid(row=0, column=0, sticky="ew", padx=10, pady=(0, 14))
        listing = tk.Frame(recent, background="white")
        listing.grid(row=1, column=0, sticky="nsew")
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.recent_list = RecentDocumentList(listing, open_recent=open_recent, selection_changed=self._selection_changed)
        self.recent_list.grid(row=0, column=0, sticky="nsew")
        self._scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.recent_list.yview)
        self.recent_list.configure(yscrollcommand=self._update_scrollbar)
        self._empty_label = tk.Label(listing, text="No Recent Documents", font=(family, 12),
                                     foreground="#8a8a90", background="white")
        footer = tk.Frame(recent, background="white")
        footer.grid(row=2, column=0, sticky="ew", pady=(18, 0))
        footer.columnconfigure(0, weight=1)
        self.clear_button = ttk.Button(footer, text="Clear Recent", command=clear_recent)
        self.clear_button.grid(row=0, column=0, sticky="w")
        self.open_recent_button = ttk.Button(footer, text="Open", command=self.recent_list.open_selected, width=8)
        self.open_recent_button.grid(row=0, column=1, sticky="e")
        self.refresh_recent_documents(())

    def refresh_recent_documents(self, paths: Sequence[Path]) -> None:
        self.recent_list.refresh(paths)
        if paths:
            self._empty_label.place_forget()
            self.clear_button.grid()
            self.open_recent_button.grid()
        else:
            self._empty_label.place(relx=0.5, rely=0.5, anchor="center")
            self.clear_button.grid_remove()
            self.open_recent_button.grid_remove()

    def _selection_changed(self) -> None:
        self.open_recent_button.state(["!disabled"] if self.recent_list.selected_path else ["disabled"])

    def _update_scrollbar(self, first, last) -> None:
        self._scrollbar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            self._scrollbar.grid_remove()
        else:
            self._scrollbar.grid(row=0, column=1, sticky="ns")
