"""Nonmodal starting point for RF documents and application utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from rfmapping_viewer.tk_support import tk, ttk


class WelcomeFrame(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        open_document: Callable[[], None],
        open_recent: Callable[[Path], None],
        clear_recent: Callable[[], None],
    ) -> None:
        super().__init__(master, style="Welcome.TFrame")
        self._open_recent = open_recent
        self._recent_paths: tuple[Path, ...] = ()
        self._configure_styles()
        self.columnconfigure(2, weight=1)
        self.rowconfigure(0, weight=1)

        introduction = ttk.Frame(self, width=310, style="Welcome.TFrame")
        introduction.grid(row=0, column=0, sticky="nsew")
        introduction.grid_propagate(False)
        content = ttk.Frame(introduction, padding=28, style="Welcome.TFrame")
        content.place(relx=0.5, rely=0.46, anchor="center", relwidth=1)
        ttk.Label(content, text="RF Map Viewer", style="Welcome.Title.TLabel").pack()
        self.open_button = ttk.Button(
            content, text="Open RF Map…", command=open_document,
            style="Welcome.TButton", width=22,
        )
        self.open_button.pack(pady=(28, 0))
        tk.Frame(self, width=1, background="#dedee3").grid(row=0, column=1, sticky="ns")

        recent = ttk.Frame(self, padding=(24, 24, 24, 20), style="Welcome.Recent.TFrame")
        recent.grid(row=0, column=2, sticky="nsew")
        recent.columnconfigure(0, weight=1)
        recent.rowconfigure(1, weight=1)
        header = ttk.Frame(recent, style="Welcome.Recent.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Recent Documents", style="Welcome.Section.TLabel").grid(row=0, column=0, sticky="w")
        self.clear_button = ttk.Button(
            header, text="Clear Recent", command=clear_recent, style="Welcome.TButton",
        )
        self.clear_button.grid(row=0, column=1, sticky="e", padx=(12, 0))

        listing = ttk.Frame(recent, style="Welcome.Recent.TFrame")
        listing.grid(row=1, column=0, sticky="nsew")
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.recent_list = ttk.Treeview(
            listing, show="tree", selectmode="browse", height=5,
            style="Welcome.Treeview",
        )
        self.recent_list.column("#0", width=370, minwidth=200, stretch=True)
        self.recent_list.grid(row=0, column=0, sticky="nsew")
        self._scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.recent_list.yview)
        self._scrollbar.grid(row=0, column=1, sticky="ns")
        self.recent_list.configure(yscrollcommand=self._scrollbar.set)
        self.recent_list.bind("<<TreeviewSelect>>", self._selection_changed)
        self.recent_list.bind("<Return>", self._open_selected)
        self.recent_list.bind("<Double-1>", self._double_click)
        self._empty_label = ttk.Label(
            listing, text="No Recent Documents",
            style="Welcome.Recent.Muted.TLabel", justify="center",
        )
        self.open_recent_button = ttk.Button(
            recent, text="Open", command=self._open_selected, style="Welcome.TButton",
        )
        self.open_recent_button.grid(row=2, column=0, sticky="e", pady=(16, 0))
        self.refresh_recent_documents(())

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        family = self.tk.call("font", "actual", "TkDefaultFont", "-family")
        font_size = abs(int(self.tk.call("font", "actual", "TkDefaultFont", "-size")))
        body_font = (family, font_size)
        small_font = (family, max(font_size - 1, 9))
        style.configure("Welcome.TFrame", background="white")
        style.configure("Welcome.Recent.TFrame", background="#f5f5f7")
        style.configure("Welcome.Title.TLabel", background="white", foreground="#1d1d1f", font=(family, 23, "bold"))
        style.configure("Welcome.Section.TLabel", background="#f5f5f7", foreground="#1d1d1f", font=(family, font_size, "bold"))
        style.configure("Welcome.Recent.Muted.TLabel", background="#f5f5f7", foreground="#6e6e73", font=small_font)
        style.configure("Welcome.TButton", font=body_font, padding=(10, 6))
        style.configure(
            "Welcome.Treeview", background="#f5f5f7", fieldbackground="#f5f5f7",
            foreground="#1d1d1f", font=body_font, rowheight=52, borderwidth=0,
        )
        style.map("Welcome.Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#1d1d1f")])

    def refresh_recent_documents(self, paths: Sequence[Path]) -> None:
        selected = self.recent_list.selection()
        previous = self._recent_paths[int(selected[0])] if selected else None
        self._recent_paths = tuple(paths)
        children = self.recent_list.get_children()
        if children:
            self.recent_list.delete(*children)
        for index, path in enumerate(self._recent_paths):
            self.recent_list.insert("", "end", iid=str(index), text=f"{path.name}\n{self._display_path(path.parent)}")
        if self._recent_paths:
            self._empty_label.place_forget()
            self._scrollbar.grid()
            index = self._recent_paths.index(previous) if previous in self._recent_paths else 0
            self.recent_list.selection_set(str(index))
            self.recent_list.focus(str(index))
            self.recent_list.see(str(index))
            self.clear_button.state(["!disabled"])
        else:
            self._empty_label.place(relx=0.5, rely=0.5, anchor="center")
            self._scrollbar.grid_remove()
            self.clear_button.state(["disabled"])
        self._selection_changed()

    @staticmethod
    def _display_path(path: Path) -> str:
        home = Path.home()
        return str(Path("~") / path.relative_to(home)) if path.is_relative_to(home) else str(path)

    def _selection_changed(self, _event=None) -> None:
        selected = self.recent_list.selection()
        self.open_recent_button.state(["!disabled"] if selected else ["disabled"])

    def _open_selected(self, _event=None) -> str:
        selected = self.recent_list.selection()
        if selected:
            self._open_recent(self._recent_paths[int(selected[0])])
        return "break"

    def _double_click(self, event) -> str:
        row = self.recent_list.identify_row(event.y)
        if row:
            self.recent_list.selection_set(row)
            self._open_selected()
        return "break"
