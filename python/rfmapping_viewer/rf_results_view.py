"""A read-only pane for upstream saved RF masks, centers and QC."""

from __future__ import annotations

import numpy as np

from rfmapping_viewer.constants import SINGLETON_Y_REFERENCE_COLUMNS, SINGLETON_Y_REFERENCE_ROWS
from rfmapping_viewer.rf_results import RFResultSource, SavedRFResult
from rfmapping_viewer.rf_result_overlay import RFResultOverlayCache
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
        rows_count, columns_count = mask.shape
        # Match RF's square display bins and its singleton-y presentation.
        axes.set_box_aspect(
            SINGLETON_Y_REFERENCE_ROWS / SINGLETON_Y_REFERENCE_COLUMNS
            if rows_count == 1 else rows_count / columns_count
        )
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
    """Saved-result selectors for the viewer's shared spatial canvas."""

    def __init__(self, parent, data=None, *, controls_parent=None, on_change=None):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.on_change = on_change
        self.source = None
        self.cache = None
        self.unit_id = None
        self.dimension = tk.StringVar(self, "2D")
        self.rf_type = tk.StringVar(self, "excitatory")
        self.status = tk.StringVar(self)
        self.details = tk.StringVar(self)
        self.controls = ttk.Frame(controls_parent or self, style="Panel.TFrame")
        for variable, choices, width in (
            (self.dimension, ("2D", "1D", "Both"), 5),
            (self.rf_type, ("excitatory", "inhibitory"), 12),
        ):
            control = ttk.Combobox(self.controls, textvariable=variable,
                                  values=choices, state="readonly", width=width)
            control.pack(side="left", padx=(0, 10))
            control.bind("<<ComboboxSelected>>", self._selection_changed)
        ttk.Label(self.controls, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        ttk.Button(self.controls, text="Reload", command=self.refresh).pack(side="right", padx=(10, 0))
        self.set_document(data)

    def set_document(self, data):
        self.source = RFResultSource.from_document(data) if data is not None else None
        self.cache = RFResultOverlayCache(self.source) if self.source is not None else None
        self.unit_id = None
        self.status.set("")
        self.details.set("")

    def set_unit(self, unit_id):
        self.unit_id = unit_id
        self.update_details()

    def _selection_changed(self, _event=None):
        self.update_details()
        if self.on_change is not None:
            self.on_change()

    def refresh(self):
        if self.cache is not None:
            self.cache.clear()
        self._selection_changed()

    def update_details(self, *, mode=None, rf_type=None):
        if self.cache is None or self.unit_id is None:
            self.status.set("No unit selected")
            self.details.set("")
            return
        mode = self.dimension.get() if mode is None else mode
        rf_type = self.rf_type.get() if rf_type is None else rf_type
        dimensions = {"None": (), "2D": ("2d",), "1D": ("1d",), "Both": ("2d", "1d")}[mode]
        intervals, unavailable, details = [], [], []
        for dimension in dimensions:
            result = self.cache.result(dimension, rf_type)
            if result is None:
                unavailable.append(dimension.upper())
                details.append(self.cache.error(dimension, rf_type) or "No saved result")
                continue
            interval = result.manifest["time_range_s"]
            text = f"{interval[0] * 1000:g}–{interval[1] * 1000:g} ms"
            if text not in intervals:
                intervals.append(text)
            saved = result.for_unit(self.unit_id)
            if saved is None:
                unavailable.append(dimension.upper())
            parameters = result.manifest["parameters"]
            qc = result.unit_qc(self.unit_id)
            details.append(f"{dimension.upper()} {rf_type} · {text}\n{result.path}")
            details.append(f"Unit {self.unit_id}: " + (f"{int(saved[0].sum())} detected bins" if saved is not None else "not in saved results"))
            if saved is not None:
                if dimension == "2d":
                    centers = [(self.source.x_positions[x], self.source.y_positions[y])
                               for y, x in zip(*saved[1].nonzero())]
                    details.append(f"Centers (x, y): {centers} · × on plot")
                else:
                    positions = self.source.x_positions if result.axis == "x" else self.source.y_positions
                    centers = [positions[index] for index in saved[1].nonzero()[0]]
                    details.append(f"Centers ({result.axis}): {centers} · △ at axis edge")
            details.append(f"Method: {result.manifest.get('detector_algorithm', 'saved detection')}\nParameters: {parameters}")
            if qc:
                details.append(f"QC: {qc['zero_bins']} zero bins; {qc['valid_bins']} valid bins; " + ("kept" if qc['keep'] else "excluded"))
            if result.summary_warning:
                details.append(result.summary_warning)
        if intervals:
            details.append("Coordinates use this RF document; source fingerprint is unavailable.")
        status = " · ".join(intervals)
        if unavailable:
            status += (" · " if status else "") + "/".join(unavailable) + " unavailable"
        self.status.set(status)
        self.details.set("\n\n".join(details))
