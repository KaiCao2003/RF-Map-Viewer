"""A read-only pane for upstream saved RF masks, centers and QC."""

from __future__ import annotations

import numpy as np

from rfmapping_viewer.rf_results import RFResultSource, SavedRFResult, load_saved_rf_result
from rfmapping_viewer.tk_support import tk, ttk


def _cell_edges(positions) -> np.ndarray:
    values = np.asarray(positions, dtype=float)
    if values.size == 1:
        # A singleton has no recorded bin width; this gives its marker a visible
        # display footprint without changing the recorded center coordinate.
        return np.array([values[0] - .5, values[0] + .5])
    midpoints = (values[:-1] + values[1:]) / 2
    return np.r_[values[0] - (midpoints[0] - values[0]), midpoints,
                 values[-1] + (values[-1] - midpoints[-1])]


def draw_saved_rf_result(figure, result: SavedRFResult, unit_id: int) -> bool:
    """Plot one saved detection in physical source coordinates, without analysis."""
    import matplotlib as mpl
    from matplotlib.colors import ListedColormap

    mpl.rcParams["savefig.facecolor"] = "white"
    mpl.rcParams["savefig.transparent"] = False
    figure.clear()
    figure.set_facecolor("white")
    axes = figure.add_subplot(111, facecolor="white")
    axes.tick_params(colors="#1d1d1f")
    for spine in axes.spines.values():
        spine.set_color("#1d1d1f")
    saved = result.for_unit(unit_id)
    if saved is None:
        axes.set_axis_off()
        axes.text(.5, .5, f"No saved result for unit {unit_id}", ha="center", va="center",
                  color="#1d1d1f", transform=axes.transAxes)
        return False
    mask, center = saved
    color = "#007aff" if result.rf_type == "excitatory" else "#a344c4"
    if result.dimension == "1d":
        positions = np.asarray(result.source.x_positions if result.axis == "x" else result.source.y_positions)
        axes.step(positions, mask.astype(int), where="mid", color=color, label="Saved RF mask")
        axes.fill_between(positions, mask, step="mid", color=color, alpha=.15)
        axes.scatter(positions[center.astype(bool)], mask[center.astype(bool)],
                     marker="x", s=80, color="#1d1d1f", label="Saved center", zorder=3)
        axes.set(xlabel=f"{result.axis.upper()} position", ylabel="RF membership", ylim=(-.08, 1.18), yticks=[0, 1])
        axes.legend(facecolor="white", framealpha=1, edgecolor="#d2d2d7", labelcolor="#1d1d1f", loc="upper right")
    else:
        axes.pcolormesh(_cell_edges(result.source.x_positions), _cell_edges(result.source.y_positions), mask,
                        shading="flat", cmap=ListedColormap(["white", color]), vmin=0, vmax=1)
        if mask.shape[0] == 1:
            axes.set_box_aspect(7 / 30)
        rows, columns = np.nonzero(center)
        axes.scatter(np.asarray(result.source.x_positions)[columns], np.asarray(result.source.y_positions)[rows],
                     marker="x", s=90, linewidths=2, color="#1d1d1f", label="Saved center")
        axes.set(xlabel="X position", ylabel="Y position")
        axes.legend(facecolor="white", framealpha=1, edgecolor="#d2d2d7", labelcolor="#1d1d1f", loc="upper right")
    axes.xaxis.label.set_color("#1d1d1f")
    axes.yaxis.label.set_color("#1d1d1f")
    interval = result.manifest["time_range_s"]
    axes.set_title(f"Unit {unit_id} · {result.dimension.upper()} {result.rf_type} · "
                   f"{interval[0] * 1000:g}–{interval[1] * 1000:g} ms", color="#1d1d1f", pad=14)
    return True


class RFResultsPane(ttk.Frame):
    def __init__(self, parent, data=None):
        super().__init__(parent, padding=12)
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        self.source = None
        self.unit_id = None
        self._results = {}
        self.dimension = tk.StringVar(self, "2d")
        self.rf_type = tk.StringVar(self, "excitatory")
        self.status = tk.StringVar(self, "Open an RF document to inspect saved results.")
        self.details = tk.StringVar(self)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        controls = ttk.Frame(self)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for label, variable, choices in (("Dimension", self.dimension, ("2d", "1d")),
                                          ("Polarity", self.rf_type, ("excitatory", "inhibitory"))):
            ttk.Label(controls, text=label).pack(side="left", padx=(0, 6))
            control = ttk.Combobox(controls, textvariable=variable, values=choices, state="readonly", width=13)
            control.pack(side="left", padx=(0, 16))
            control.bind("<<ComboboxSelected>>", lambda _event: self._render())
        ttk.Button(controls, text="Reload saved results", command=self.refresh).pack(side="right")
        ttk.Label(self, textvariable=self.status, wraplength=850).grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.figure = Figure(figsize=(7, 4), dpi=100, facecolor="white", layout="constrained")
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().configure(background="white", highlightthickness=0)
        self.canvas.get_tk_widget().grid(row=2, column=0, sticky="nsew")
        ttk.Label(self, textvariable=self.details, wraplength=850, justify="left").grid(
            row=3, column=0, sticky="w", pady=(8, 0))
        self.set_document(data)

    def set_document(self, data) -> None:
        self.source = RFResultSource.from_document(data) if data is not None else None
        self.unit_id = None
        self._results.clear()
        self._render()

    def set_unit(self, unit_id: int | None) -> None:
        if unit_id != self.unit_id:
            self.unit_id = unit_id
            self._render()

    def refresh(self) -> None:
        self._results.clear()
        self._render()

    def destroy(self) -> None:
        # TkAgg schedules renders on its widget; closing a document must cancel
        # the callback before Tk deletes the registered Python command.
        if self.canvas._idle_draw_id is not None:
            self.canvas.get_tk_widget().after_cancel(self.canvas._idle_draw_id)
            self.canvas._idle_draw_id = None
        super().destroy()

    def _empty(self, message: str) -> None:
        self.status.set(message)
        self.details.set("")
        self.figure.clear()
        self.canvas.draw_idle()

    def _render(self) -> None:
        if self.source is None:
            self._empty("Open an RF document to inspect saved results.")
            return
        if self.unit_id is None:
            self._empty("Select a unit to inspect its saved RF result.")
            return
        key = (self.dimension.get(), self.rf_type.get())
        if key not in self._results:
            try:
                self._results[key] = load_saved_rf_result(self.source, dimension=key[0], rf_type=key[1])
            except FileNotFoundError as error:
                self._results[key] = f"No saved {key[0].upper()} {key[1]} result: {error.filename}"
            except (OSError, ValueError, TypeError, KeyError) as error:
                self._results[key] = f"Saved RF result unavailable: {error}"
        result = self._results[key]
        if isinstance(result, str):
            self._empty(result)
            return
        found = draw_saved_rf_result(self.figure, result, self.unit_id)
        self.status.set(str(result.path) if found else f"Unit {self.unit_id} is absent from this saved result (it may have failed analysis QC).")
        parameters = result.manifest["parameters"]
        qc = result.unit_qc(self.unit_id)
        qc_text = (f"QC: {qc['zero_bins']} zero bins; {qc['valid_bins']} valid bins; "
                   f"{'kept' if qc['keep'] else 'excluded'}." if qc else "No saved per-unit QC summary.")
        method = "Projection of saved 2-D detection" if result.projected_from_2d else result.manifest.get("detector_algorithm", "Saved detection")
        provenance = f"Analysis source: {result.summary['source_path']}\n" if result.summary else ""
        warning = f"{result.summary_warning}\n" if result.summary_warning else ""
        self.details.set(
            f"{method} · z={parameters.get('cluster_forming_z', '—')} · "
            f"drop bins={parameters.get('drop_bins', '—')} · wrap X={parameters.get('wrap_x', '—')}\n"
            f"{qc_text}  Saved schema v1 contains masks and centers, without response amplitudes.\n"
            f"{provenance}"
            f"{warning}"
            "Unit IDs, grid size and time range checked. Physical axes use this RF document; "
            "the saved result has no axes or source-file fingerprint to verify them."
        )
        self.canvas.draw_idle()
