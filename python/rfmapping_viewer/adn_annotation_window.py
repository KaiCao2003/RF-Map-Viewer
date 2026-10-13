"""Probe-space lasso UI for a session's saved ADN unit selection."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rfmapping_viewer.adn_selection import (
    load_adn_units,
    save_adn_units,
    unit_ids_in_polygon,
)
from rfmapping_viewer.companions import ProbeGeometry, ProbeUnitPosition
from rfmapping_viewer.tk_support import messagebox, scroll_canvas_precise, tk, ttk


def draw_unit_tooltip(canvas: tk.Canvas, event: tk.Event, unit: ProbeUnitPosition) -> None:
    pointer_x, pointer_y = canvas.canvasx(event.x), canvas.canvasy(event.y)
    text = canvas.create_text(
        pointer_x + 12, pointer_y + 12, anchor="nw", tags=("hover",),
        text=f"Unit ID: {unit.unit_id}\nx: {unit.x_um:g} µm\ny: {unit.y_um:g} µm",
        fill="#1d1d1f", font=("TkDefaultFont", 11),
    )
    left, top, right, bottom = canvas.bbox(text)
    dx = min(0, canvas.canvasx(canvas.winfo_width()) - right - 8)
    dy = min(0, canvas.canvasy(canvas.winfo_height()) - bottom - 8)
    canvas.move(text, dx, dy)
    left, top, right, bottom = canvas.bbox(text)
    background = canvas.create_rectangle(
        left - 5, top - 4, right + 5, bottom + 4,
        fill="white", outline="#98a2b3", tags=("hover",),
    )
    canvas.tag_lower(background, text)


class ADNAnnotationWindow(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        geometry: ProbeGeometry,
        output_path: Path,
        on_saved: Callable[[set[int]], None],
    ) -> None:
        selected = load_adn_units(output_path, geometry.probe_name)
        super().__init__(parent)
        self.title(f"Annotate ADN · {geometry.probe_name}")
        self.geometry("1000x800")
        self.minsize(740, 500)
        self.configure(background="white")
        self.probe_geometry = geometry
        self.output_path = output_path
        self.on_saved = on_saved
        self.selected_ids = selected
        self._contours: list[list[tuple[float, float]]] = []
        self._history: list[tuple[set[int], list[list[tuple[float, float]]]]] = []
        self._lasso: list[tuple[float, float]] = []
        self._unit_points: list[tuple[float, float, ProbeUnitPosition]] = []
        self._transform: tuple[float, float, float, float] | None = None
        self._zoom = 1.0
        self._plot_height = 1.0
        self._left = 70.0
        self._top = 28.0
        self.selection_var = tk.StringVar(self)
        self.status_var = tk.StringVar(self, value=str(output_path))
        self.zoom_var = tk.StringVar(self, value="Depth: 1×")
        self._build()
        self._update_selection()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        toolbar = tk.Frame(self, background="white", padx=16, pady=12)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(
            toolbar,
            text="Draw a closed loop to add ADN units. Hover a dot for its position.",
            background="white", foreground="#1d1d1f",
        ).pack(side="left")
        ttk.Button(toolbar, text="−", width=3, command=lambda: self._set_zoom(self._zoom / 1.5)).pack(side="right")
        tk.Label(toolbar, textvariable=self.zoom_var, background="white", foreground="#1d1d1f").pack(side="right", padx=8)
        ttk.Button(toolbar, text="+", width=3, command=lambda: self._set_zoom(self._zoom * 1.5)).pack(side="right")

        plot = tk.Frame(self, background="white")
        plot.grid(row=1, column=0, sticky="nsew", padx=(12, 0))
        plot.columnconfigure(0, weight=1)
        plot.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(plot, background="white", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(plot, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set, yscrollincrement=20)
        self.canvas.bind("<Configure>", self._draw)
        self.canvas.bind("<ButtonPress-1>", self._start_lasso)
        self.canvas.bind("<B1-Motion>", self._extend_lasso)
        self.canvas.bind("<ButtonRelease-1>", self._finish_lasso)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda _event: self.canvas.delete("hover"))
        self.canvas.bind("<MouseWheel>", self._scroll)
        if self.tk.call("info", "commands", "tk::PreciseScrollDeltas"):
            self.canvas.bind("<TouchpadScroll>", lambda event: self._scroll(event, precise=True))
        self.canvas.bind("<Button-4>", lambda _event: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda _event: self.canvas.yview_scroll(3, "units"))

        inspector = tk.Frame(self, background="white", padx=16)
        inspector.grid(row=1, column=1, sticky="ns")
        tk.Label(inspector, textvariable=self.selection_var, background="white", foreground="#1d1d1f").pack(anchor="w", pady=(0, 8))
        self.unit_list = tk.Text(
            inspector, width=23, height=12, wrap="word", state="disabled",
            background="white", foreground="#1d1d1f", relief="solid", borderwidth=1,
        )
        self.unit_list.pack(fill="both", expand=True)
        tk.Label(inspector, text="Orange = selected ADN", background="white", foreground="#1d1d1f").pack(anchor="w", pady=8)
        self.undo_button = ttk.Button(inspector, text="Undo", command=self._undo)
        self.undo_button.pack(fill="x", pady=(8, 4))
        ttk.Button(inspector, text="Clear selection", command=self._clear).pack(fill="x", pady=4)
        ttk.Button(inspector, text="Save ADN", command=self._save).pack(fill="x", pady=(12, 4))
        ttk.Button(inspector, text="Close", command=self.destroy).pack(fill="x", pady=(4, 12))
        tk.Label(
            self, textvariable=self.status_var, background="white", foreground="#475467",
            anchor="w", justify="left", wraplength=900,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=12)

    def _set_zoom(self, zoom: float) -> None:
        self._zoom = min(8.0, max(1.0, zoom))
        self.zoom_var.set(f"Depth: {self._zoom:g}×")
        self._draw()

    def _scroll(self, event: tk.Event, *, precise: bool = False) -> str:
        if precise:
            scroll_canvas_precise(self.canvas, event.delta)
        else:
            delta = int(event.delta)
            steps = int(-delta / 120) if abs(delta) >= 120 else -delta
            self.canvas.yview_scroll(steps, "units")
        self.canvas.delete("hover")
        return "break"

    def _to_canvas(self, x_um: float, y_um: float) -> tuple[float, float]:
        x_min, y_max, x_scale, y_scale = self._transform
        return self._left + (x_um - x_min) * x_scale, self._top + (y_max - y_um) * y_scale

    def _from_event(self, event: tk.Event) -> tuple[float, float]:
        x_min, y_max, x_scale, y_scale = self._transform
        return (
            x_min + (self.canvas.canvasx(event.x) - self._left) / x_scale,
            y_max - (self.canvas.canvasy(event.y) - self._top) / y_scale,
        )

    def _draw(self, _event: tk.Event | None = None) -> None:
        canvas = self.canvas
        canvas.delete("all")
        self._unit_points.clear()
        units = self.probe_geometry.positioned_units
        points = [(channel.x_um, channel.y_um) for channel in self.probe_geometry.channels]
        points.extend((float(unit.x_um), float(unit.y_um)) for unit in units)
        if not points:
            self._transform = None
            canvas.create_text(120, 60, text="No finite unit positions", fill="#1d1d1f")
            return
        xs, ys = zip(*points)
        x_min, x_max = min(xs) - 20.0, max(xs) + 40.0
        y_min, y_max = min(ys) - 20.0, max(ys) + 20.0
        width = max(canvas.winfo_width(), 300)
        self._plot_height = max(canvas.winfo_height() - 70, 200) * self._zoom
        x_scale = (width - self._left - 30) / (x_max - x_min)
        y_scale = self._plot_height / (y_max - y_min)
        self._transform = x_min, y_max, x_scale, y_scale
        bottom = self._top + self._plot_height
        canvas.configure(scrollregion=(0, 0, width, bottom + 42))
        canvas.create_line(self._left, self._top, self._left, bottom, width - 30, bottom, fill="#344054")
        for index in range(6):
            value = y_max - (y_max - y_min) * index / 5
            y = self._top + self._plot_height * index / 5
            canvas.create_line(self._left - 4, y, self._left, y, fill="#344054")
            canvas.create_text(self._left - 8, y, text=f"{value:g}", anchor="e", fill="#1d1d1f")
        canvas.create_text(15, self._top + self._plot_height / 2, text="y (µm) · larger = shallower", angle=90, fill="#1d1d1f")
        canvas.create_text(width / 2, bottom + 28, text="x (µm)", fill="#1d1d1f")
        for index in range(5):
            value = x_min + (x_max - x_min) * index / 4
            x, _ = self._to_canvas(value, y_min)
            canvas.create_line(x, bottom, x, bottom + 4, fill="#344054")
            canvas.create_text(x, bottom + 12, text=f"{value:g}", fill="#1d1d1f")
        for channel in self.probe_geometry.channels:
            x, y = self._to_canvas(channel.x_um, channel.y_um)
            canvas.create_rectangle(x - 2, y - 2, x + 2, y + 2, fill="#e4e7ec", outline="")
        for unit in units:
            x, y = self._to_canvas(float(unit.x_um), float(unit.y_um))
            self._unit_points.append((x, y, unit))
            fill = "#f79009" if unit.unit_id in self.selected_ids else "#1570ef"
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=fill, outline="white")
            canvas.create_text(x + 6, y, text=str(unit.unit_id), anchor="w", fill="#344054", font=("TkDefaultFont", 9))
        for contour in self._contours:
            self._draw_contour(contour, "contour", closed=True)
        self._draw_contour(self._lasso, "lasso", closed=False)

    def _draw_contour(self, vertices: list[tuple[float, float]], tag: str, *, closed: bool) -> None:
        if len(vertices) < 2:
            return
        points = vertices + vertices[:1] if closed else vertices
        coordinates = [coordinate for point in points for coordinate in self._to_canvas(*point)]
        self.canvas.create_line(*coordinates, fill="#d92d20", width=2, tags=(tag,))

    def _start_lasso(self, event: tk.Event) -> None:
        if self._transform is None:
            return
        self.canvas.delete("hover")
        self._lasso = [self._from_event(event)]

    def _extend_lasso(self, event: tk.Event) -> None:
        if not self._lasso:
            return
        self._lasso.append(self._from_event(event))
        self.canvas.delete("lasso")
        self._draw_contour(self._lasso, "lasso", closed=False)

    def _finish_lasso(self, event: tk.Event) -> None:
        if not self._lasso:
            return
        self._lasso.append(self._from_event(event))
        if len(self._lasso) >= 3:
            included = unit_ids_in_polygon(self.probe_geometry, self._lasso)
            self._history.append((self.selected_ids.copy(), self._contours.copy()))
            self.selected_ids.update(included)
            self._contours.append(self._lasso)
            self.status_var.set(f"Loop includes {len(included)} units · unsaved")
        self._lasso = []
        self._update_selection()
        self._draw()

    def _update_selection(self) -> None:
        self.selection_var.set(f"{len(self.selected_ids)} ADN units · {self.probe_geometry.probe_name}")
        self.unit_list.configure(state="normal")
        self.unit_list.delete("1.0", "end")
        self.unit_list.insert("1.0", ", ".join(str(unit_id) for unit_id in sorted(self.selected_ids)))
        self.unit_list.configure(state="disabled")
        self.undo_button.state(["!disabled"] if self._history else ["disabled"])

    def _undo(self) -> None:
        if not self._history:
            return
        self.selected_ids, self._contours = self._history.pop()
        self.status_var.set("Selection changed · unsaved")
        self._update_selection()
        self._draw()

    def _clear(self) -> None:
        self._history.append((self.selected_ids.copy(), self._contours.copy()))
        self.selected_ids.clear()
        self._contours.clear()
        self.status_var.set("Selection cleared · unsaved")
        self._update_selection()
        self._draw()

    def _save(self) -> None:
        try:
            save_adn_units(self.output_path, self.probe_geometry.probe_name, self.selected_ids)
        except (OSError, ValueError) as error:
            messagebox.showerror("Save ADN", str(error), parent=self)
            return
        self.on_saved(self.selected_ids.copy())
        self.status_var.set(f"Saved {len(self.selected_ids)} ADN units · {self.output_path}")

    def _hover(self, event: tk.Event) -> None:
        self.canvas.delete("hover")
        if self._lasso or not self._unit_points:
            return
        pointer_x, pointer_y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        x, y, unit = min(self._unit_points, key=lambda point: (point[0] - pointer_x) ** 2 + (point[1] - pointer_y) ** 2)
        if (x - pointer_x) ** 2 + (y - pointer_y) ** 2 > 64:
            return
        draw_unit_tooltip(self.canvas, event, unit)
