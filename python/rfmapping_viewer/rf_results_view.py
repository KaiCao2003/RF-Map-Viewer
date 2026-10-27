"""A read-only pane for upstream saved RF masks, centers and QC."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from rfmapping_viewer.constants import SINGLETON_Y_REFERENCE_COLUMNS, SINGLETON_Y_REFERENCE_ROWS
from rfmapping_viewer.rf_results import DEFAULT_RF_RESULT_LAYOUT_PATTERN, RFResultSource, SavedRFResult
from rfmapping_viewer.rf_result_overlay import RFResultOverlayCache
from rfmapping_viewer.rf_result_statistics import ResultStatistics, result_statistics, result_statistics_text
from rfmapping_viewer.tk_support import tk, ttk


@dataclass(frozen=True)
class ResultSummary:
    dimension: str
    rf_type: str
    unit_id: int
    status_kind: str
    status: str
    window: str = ""
    centers: str = ""
    detected_bins: int | None = None
    statistics: ResultStatistics | None = None
    warning: str = ""
    time_range_s: tuple[float, float] | None = None


@dataclass(frozen=True)
class ResultSummaryGroup:
    dimension: str
    window: str
    rows: tuple[ResultSummary, ...]
    shared_statistics: ResultStatistics | None


def group_result_summaries(summaries) -> list[ResultSummaryGroup]:
    """Share values only when both provenance and numerical statistics agree."""
    grouped = {}
    for summary in summaries:
        key = (summary.unit_id, summary.dimension, summary.time_range_s)
        grouped.setdefault(key, []).append(summary)
    groups = []
    for rows in grouped.values():
        first = rows[0]
        shared = first.statistics
        if (shared is None or shared.error is not None or not shared.basis_key
                or shared.mean_hz is None or shared.sd_hz is None):
            shared = None
        elif any(
            row.statistics is None or row.statistics.error is not None
            or row.statistics.origin != shared.origin or row.statistics.basis_key != shared.basis_key
            or row.statistics.mean_hz != shared.mean_hz or row.statistics.sd_hz != shared.sd_hz
            for row in rows[1:]
        ):
            shared = None
        groups.append(ResultSummaryGroup(first.dimension, first.window, tuple(rows), shared))
    return groups


def _result_summary(result: SavedRFResult, unit_id: int, statistics: ResultStatistics | None) -> ResultSummary:
    saved = result.for_unit(unit_id)
    qc = result.unit_qc(unit_id)
    detected_bins = int(saved[0].sum()) if saved is not None else None
    if qc is not None and not qc["keep"]:
        status_kind, status = "excluded", "Excluded by QC"
    elif saved is None:
        status_kind, status = "missing_unit", "Unit not saved"
    elif detected_bins == 0:
        status_kind, status = "empty", "No RF detected"
    else:
        status_kind, status = "detected", f"{detected_bins} detected " + ("bin" if detected_bins == 1 else "bins")
    centers = []
    if saved is not None:
        if result.dimension == "2d":
            centers = [f"({result.source.x_positions[x]:g}, {result.source.y_positions[y]:g})"
                       for y, x in zip(*saved[1].nonzero())]
        else:
            positions = result.source.x_positions if result.axis == "x" else result.source.y_positions
            centers = [f"{result.axis.upper()} {positions[index]:g}" for index in saved[1].nonzero()[0]]
    center_text = "; ".join(centers[:3])
    if len(centers) > 3:
        center_text += f"; +{len(centers) - 3} more"
    first, last = result.manifest["time_range_s"]
    return ResultSummary(
        result.dimension, result.rf_type, unit_id, status_kind, status,
        window=f"{first * 1000:g}–{last * 1000:g} ms", centers=center_text,
        detected_bins=detected_bins, statistics=statistics, warning=result.summary_warning or "",
        time_range_s=(float(first), float(last)),
    )


def _cell_edges(positions) -> np.ndarray:
    values = np.asarray(positions, dtype=float)
    if values.size == 1:
        # A singleton has no recorded bin width; this gives its marker a visible
        # display footprint without changing the recorded center coordinate.
        return np.array([values[0] - .5, values[0] + .5])
    midpoints = (values[:-1] + values[1:]) / 2
    return np.r_[values[0] - (midpoints[0] - values[0]), midpoints,
                 values[-1] + (values[-1] - midpoints[-1])]


def draw_saved_rf_result(figure, result: SavedRFResult, unit_id: int, *, flip_y: bool = False) -> bool:
    """Plot saved coordinates in the viewer's source-row order, without analysis."""
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
        x_edges = _cell_edges(result.source.x_positions)
        y_edges = _cell_edges(result.source.y_positions)
        axes.pcolormesh(x_edges, y_edges, mask,
                        shading="flat", cmap=ListedColormap(["white", color]), vmin=0, vmax=1)
        rows_count, columns_count = mask.shape
        axes.set_xlim(x_edges[0], x_edges[-1])
        if rows_count > 1:
            # The Tk RF canvas starts with source row 0 at the top. Numeric
            # autoscaling would silently reverse ascending source Y positions.
            axes.set_ylim((y_edges[0], y_edges[-1]) if flip_y else (y_edges[-1], y_edges[0]))
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

    def __init__(self, parent, data=None, *, controls_parent=None, on_change=None, on_reload=None,
                 layout_pattern=DEFAULT_RF_RESULT_LAYOUT_PATTERN):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.on_change = on_change
        self.layout_pattern = layout_pattern
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
        ttk.Button(self.controls, text="Reload", command=on_reload or self.refresh).pack(side="right", padx=(10, 0))
        self.set_document(data)

    def set_document(self, data):
        self.data = data
        self.source = RFResultSource.from_document(
            data, layout_pattern=self.layout_pattern,
        ) if data is not None else None
        self.cache = RFResultOverlayCache(self.source) if self.source is not None else None
        self.unit_id = None
        self.status.set("")
        self.details.set("")
        self.summaries: list[ResultSummary] = []

    def set_layout_pattern(self, pattern: str) -> None:
        if pattern == self.layout_pattern:
            return
        self.layout_pattern = pattern
        if self.source is not None:
            self.source = replace(self.source, layout_pattern=pattern)
            self.cache = RFResultOverlayCache(self.source)
        self._selection_changed()

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

    def update_details(self, *, mode=None, rf_type=None, layers=None, include_statistics=False):
        self.summaries = []
        if self.cache is None or self.unit_id is None:
            self.status.set("No unit selected")
            self.details.set("")
            return
        if layers is None:
            mode = self.dimension.get() if mode is None else mode
            rf_type = self.rf_type.get() if rf_type is None else rf_type
            dimensions = {"None": (), "2D": ("2d",), "1D": ("1d",), "Both": ("2d", "1d")}[mode]
            selections = [(rf_type, dimension) for dimension in dimensions]
        else:
            selections = [layer.split("_") for layer in layers]
        intervals, unavailable, details = [], [], []
        for rf_type, dimension in selections:
            result = self.cache.result(dimension, rf_type)
            if result is None:
                unavailable.append(f"{rf_type} {dimension.upper()}")
                error = self.cache.error(dimension, rf_type) or "No saved result"
                details.append(error)
                self.summaries.append(ResultSummary(
                    dimension, rf_type, self.unit_id, "unavailable", "Result unavailable", warning=error,
                ))
                continue
            interval = result.manifest["time_range_s"]
            text = f"{interval[0] * 1000:g}–{interval[1] * 1000:g} ms"
            if text not in intervals:
                intervals.append(text)
            saved = result.for_unit(self.unit_id)
            if saved is None:
                unavailable.append(f"{rf_type} {dimension.upper()}")
            parameters = result.manifest["parameters"]
            qc = result.unit_qc(self.unit_id)
            details.append(f"{dimension.upper()} {rf_type} · {text}\n{result.path}")
            details.append(f"Unit {self.unit_id}: " + (f"{int(saved[0].sum())} detected bins" if saved is not None else "not in saved results"))
            statistics = result_statistics(result, self.data, self.unit_id) if include_statistics else None
            self.summaries.append(_result_summary(result, self.unit_id, statistics))
            if saved is not None:
                if include_statistics:
                    details.append(result_statistics_text(result, self.data, self.unit_id, statistics=statistics))
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
