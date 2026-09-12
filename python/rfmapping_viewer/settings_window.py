"""Native editor for viewer preferences."""

from __future__ import annotations

import math
import sys
from typing import Sequence

from rfmapping_viewer.constants import (
    DEFAULT_HD_DISPLAY_BINS,
    PALETTES,
    POLAR_RADIUS_MODES,
    TUNING_LAYOUTS,
    TUNING_PLOT_MODES,
    VALUE_MODES,
    WAVEFORM_CHANNEL_MODE_BY_LABEL,
    WAVEFORM_CHANNEL_MODE_LABELS,
)
from rfmapping_viewer.display import format_ms
from rfmapping_viewer.settings import ViewerSettings, normalize_hd_bin_count
from rfmapping_viewer.tk_support import tk, ttk

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rfmapping_gui import RFMViewer


class SettingsValidationError(ValueError):
    """Validation failure associated with one Settings tab."""

    def __init__(self, tab_name: str, message: str):
        super().__init__(message)
        self.tab_name = tab_name


class SettingsWindow(tk.Toplevel):
    """Single native-style settings window shared by all viewer windows."""

    TAB_NAMES = ("General", "RF Map", "Waveform", "Tuning Curve")

    def __init__(self, owner: RFMViewer):
        self.owner = owner
        self._app_root = owner._app_root
        super().__init__(self._app_root)
        self.title("RF Map Viewer Settings")
        self.geometry("720x810")
        self.minsize(680, 800)
        self.transient(owner)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._create_variables(owner._app_root._rfm_settings)
        self._build()
        self._select_remembered_tab()
        self._sync_dependent_controls()
        if sys.platform == "darwin":
            owner._bind_unit_filter_shortcut(self)

    def transient(self, master: tk.Misc | None = None) -> str | None:
        """Normalize Tk's queried window object to its stable path string."""

        result = super().transient(master)
        if master is None and result:
            return str(result)
        return result

    def _create_variables(self, settings: ViewerSettings) -> None:
        self.show_tuning_curve_var = tk.BooleanVar(value=settings.show_tuning_curve)
        self.auto_load_tuning_curve_var = tk.BooleanVar(value=settings.auto_load_tuning_curve)
        self.tuning_curve_session_var = tk.StringVar(
            value=str(settings.tuning_curve_session)
        )
        self.show_waveform_var = tk.BooleanVar(value=settings.show_waveform)
        self.show_probe_layout_var = tk.BooleanVar(value=settings.show_probe_layout)
        self.auto_load_probe_layout_var = tk.BooleanVar(value=settings.auto_load_probe_layout)
        self.rf_sum_start_var = tk.StringVar(value=format_ms(settings.rf_sum_start_ms))
        self.rf_sum_end_var = tk.StringVar(value=format_ms(settings.rf_sum_end_ms))
        self.rf_window_mode_var = tk.StringVar(value="A − B" if settings.rf_subtract else "Sum")
        self.rf_difference_start_var = tk.StringVar(value=format_ms(settings.rf_difference_start_ms))
        self.rf_difference_end_var = tk.StringVar(value=format_ms(settings.rf_difference_end_ms))
        self.rf_subtract_start_var = tk.StringVar(value=format_ms(settings.rf_subtract_start_ms))
        self.rf_subtract_end_var = tk.StringVar(value=format_ms(settings.rf_subtract_end_ms))
        self.rf_filter_units_with_zero_bins_var = tk.BooleanVar(
            value=settings.rf_filter_units_with_zero_bins
        )
        self.rf_zero_bin_threshold_var = tk.StringVar(
            value=str(settings.rf_zero_bin_threshold)
        )
        self.rf_time_resolution_var = tk.StringVar(value=format_ms(settings.rf_time_resolution_ms))
        self.rf_value_mode_var = tk.StringVar(value=settings.rf_value_mode)
        self.rf_x_bins_var = tk.StringVar(
            value="Native" if settings.rf_x_bins == 0 else str(settings.rf_x_bins)
        )
        self.rf_y_bins_var = tk.StringVar(
            value="Native" if settings.rf_y_bins == 0 else str(settings.rf_y_bins)
        )
        self.rf_smooth_radius_var = tk.IntVar(value=settings.rf_smooth_radius)
        self.rf_flip_y_var = tk.BooleanVar(value=settings.rf_flip_y)
        self.rf_palette_var = tk.StringVar(value=settings.rf_palette)
        self.rf_polar_radius_var = tk.StringVar(value=settings.rf_polar_radius)
        self.rf_layout_var = tk.StringVar(
            value="Polar" if settings.rf_polar_layout else "Rectangle"
        )
        self.rf_rgb_mode_var = tk.BooleanVar(value=settings.rf_rgb_mode)
        viewer_tab_labels = {
            "rf": "RF",
            "delay": "Delay / RGB",
            "timeline": "Timeline",
        }
        self.default_viewer_tab_var = tk.StringVar(
            value=viewer_tab_labels.get(settings.default_viewer_tab, "RF")
        )
        self.waveform_channel_mode_var = tk.StringVar(
            value=WAVEFORM_CHANNEL_MODE_LABELS.get(
                settings.waveform_channel_mode,
                WAVEFORM_CHANNEL_MODE_LABELS["same_x_column"],
            )
        )
        self.tuning_plot_mode_var = tk.StringVar(value=settings.tuning_plot_mode)
        self.tuning_layout_var = tk.StringVar(value=settings.tuning_layout)
        self.tuning_display_bins_var = tk.StringVar(value=str(settings.tuning_display_bins))
        self.tuning_smoothing_var = tk.BooleanVar(value=settings.tuning_smoothing)
        self.tuning_compare_scale_var = tk.BooleanVar(value=settings.tuning_compare_scale)
        self.tuning_smooth_sigma_var = tk.StringVar(
            value=f"{settings.tuning_smooth_sigma * 360.0 / DEFAULT_HD_DISPLAY_BINS:g}"
        )
        self.error_var = tk.StringVar(value="")
        self._tab_error_vars = {
            name: tk.StringVar(value="") for name in self.TAB_NAMES
        }

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        outer = ttk.Frame(self, padding=(16, 14, 16, 12))
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.notebook.enable_traversal()
        self._tab_name_by_widget: dict[str, str] = {}
        self._tab_widget_by_name: dict[str, str] = {}
        general = self._new_tab("General")
        rf_map = self._new_tab("RF Map")
        waveform = self._new_tab("Waveform")
        tuning = self._new_tab("Tuning Curve")
        self._build_general_tab(general)
        self._build_rf_tab(rf_map)
        self._build_waveform_tab(waveform)
        self._build_tuning_tab(tuning)
        self.notebook.bind("<<NotebookTabChanged>>", self._remember_selected_tab)

        footer = ttk.Frame(outer)
        footer.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            textvariable=self.error_var,
            foreground="#b42318",
            wraplength=280,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Cancel", command=self._close).grid(
            row=0, column=1, padx=(12, 8)
        )
        ttk.Button(footer, text="Save", command=self._save).grid(row=0, column=2)

        for variable in (
            self.show_tuning_curve_var,
            self.auto_load_tuning_curve_var,
            self.show_waveform_var,
            self.show_probe_layout_var,
            self.rf_filter_units_with_zero_bins_var,
            self.tuning_smoothing_var,
        ):
            variable.trace_add("write", lambda *_args: self._sync_dependent_controls())

    def _new_tab(self, name: str) -> ttk.Frame:
        tab = ttk.Frame(self.notebook, padding=(18, 16))
        # Keep forms anchored to the leading edge instead of centering their
        # controls in the available Settings width.
        tab.columnconfigure(0, minsize=164)
        tab.columnconfigure(1, weight=0)
        tab.columnconfigure(2, weight=1)
        self.notebook.add(tab, text=name)
        self._tab_name_by_widget[str(tab)] = name
        self._tab_widget_by_name[name] = str(tab)
        ttk.Label(
            tab,
            textvariable=self._tab_error_vars[name],
            foreground="#b42318",
            wraplength=500,
            justify="left",
        ).grid(row=99, column=0, columnspan=2, sticky="w", pady=(16, 0))
        return tab

    @staticmethod
    def _section_label(parent: ttk.Frame, text: str, row: int) -> None:
        ttk.Label(
            parent,
            text=text,
            font=("TkDefaultFont", 11, "bold"),
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8 if row else 0, 8))

    def _build_general_tab(self, tab: ttk.Frame) -> None:
        self._section_label(tab, "Views and loading", 0)
        ttk.Checkbutton(
            tab,
            text="Show HD tuning curve beside the RF map",
            variable=self.show_tuning_curve_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.auto_tuning_check = ttk.Checkbutton(
            tab,
            text="Automatically find and load tuning_curves.tc or .json",
            variable=self.auto_load_tuning_curve_var,
        )
        self.auto_tuning_check.grid(row=2, column=0, columnspan=2, sticky="w", padx=(22, 0), pady=(0, 14))
        ttk.Checkbutton(
            tab,
            text="Show probe layout in the sidebar",
            variable=self.show_probe_layout_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.auto_probe_check = ttk.Checkbutton(
            tab,
            text="Automatically find and load probe geometry",
            variable=self.auto_load_probe_layout_var,
        )
        self.auto_probe_check.grid(row=4, column=0, columnspan=2, sticky="w", padx=(22, 0))
        ttk.Label(
            tab,
            text=(
                "Hidden views are not discovered, read, or rendered. Turning off automatic "
                "loading does not remove a file that is already attached."
            ),
            foreground="#667085",
            wraplength=500,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(20, 0))

    def _labeled_entry(
        self,
        tab: ttk.Frame,
        row: int,
        label: str,
        variable: tk.Variable,
        *,
        width: int = 12,
    ) -> ttk.Entry:
        ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=5)
        entry = ttk.Entry(tab, textvariable=variable, width=width)
        entry.grid(row=row, column=1, sticky="w", pady=5)
        return entry

    def _labeled_combo(
        self,
        tab: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        values: Sequence[str],
        *,
        width: int = 24,
    ) -> ttk.Combobox:
        ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", pady=5)
        combo = ttk.Combobox(
            tab,
            state="readonly",
            values=tuple(values),
            textvariable=variable,
            width=width,
        )
        combo.grid(row=row, column=1, sticky="w", pady=5)
        return combo

    def _build_rf_tab(self, tab: ttk.Frame) -> None:
        self._section_label(tab, "Timing", 0)
        timing = ttk.Frame(tab)
        timing.grid(row=1, column=0, columnspan=2, sticky="w")
        self._labeled_combo(timing, 0, "Default RF mode", self.rf_window_mode_var, ("Sum", "A − B"), width=12)
        range_frame = ttk.Frame(timing)
        range_frame.grid(row=1, column=1, sticky="w", pady=5)
        ttk.Label(timing, text="Sum defaults (ms)").grid(row=1, column=0, sticky="w", padx=(0, 16), pady=5)
        ttk.Entry(range_frame, textvariable=self.rf_sum_start_var, width=8).grid(row=0, column=0)
        ttk.Label(range_frame, text="to").grid(row=0, column=1, padx=6)
        ttk.Entry(range_frame, textvariable=self.rf_sum_end_var, width=8).grid(row=0, column=2)
        ttk.Label(timing, text="A − B defaults (ms)").grid(row=2, column=0, sticky="w", padx=(0, 16), pady=5)
        difference_frame = ttk.Frame(timing)
        difference_frame.grid(row=2, column=1, sticky="w", pady=5)
        for column, item in enumerate((
            "(", self.rf_difference_start_var, "–", self.rf_difference_end_var,
            ") − (", self.rf_subtract_start_var, "–", self.rf_subtract_end_var, ")",
        )):
            if isinstance(item, tk.StringVar):
                ttk.Entry(difference_frame, textvariable=item, width=6).grid(row=0, column=column)
            else:
                ttk.Label(difference_frame, text=item).grid(row=0, column=column, padx=3)
        self._labeled_entry(tab, 2, "Target time width (ms)", self.rf_time_resolution_var)
        self._labeled_combo(tab, 3, "Value", self.rf_value_mode_var, VALUE_MODES)

        self._section_label(tab, "Unit filtering", 4)
        ttk.Checkbutton(
            tab,
            text="Hide units with zero-spike RF bins in the current RF window",
            variable=self.rf_filter_units_with_zero_bins_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(tab, text="Hide at this many zero bins").grid(
            row=6, column=0, sticky="w", pady=5
        )
        self.rf_zero_bin_threshold_entry = ttk.Entry(
            tab,
            textvariable=self.rf_zero_bin_threshold_var,
            width=12,
        )
        self.rf_zero_bin_threshold_entry.grid(row=6, column=1, sticky="w", pady=5)
        ttk.Label(
            tab,
            text=(
                "Counts native spatial RF bins before display rebinning or smoothing. "
                "Uses window A in A − B mode. ⌘⇧. toggles this filter."
            ),
            foreground="#667085",
            wraplength=440,
            justify="left",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self._section_label(tab, "Spatial display", 8)
        bins_frame = ttk.Frame(tab)
        bins_frame.grid(row=9, column=1, sticky="w", pady=5)
        ttk.Label(tab, text="Display bins").grid(row=9, column=0, sticky="w", pady=5)
        ttk.Label(bins_frame, text="X").grid(row=0, column=0)
        ttk.Entry(bins_frame, textvariable=self.rf_x_bins_var, width=8).grid(row=0, column=1, padx=(4, 12))
        ttk.Label(bins_frame, text="Y").grid(row=0, column=2)
        ttk.Entry(bins_frame, textvariable=self.rf_y_bins_var, width=8).grid(row=0, column=3, padx=(4, 0))
        ttk.Label(bins_frame, text="Native = all", foreground="#667085").grid(
            row=0, column=4, padx=(12, 0)
        )
        self._labeled_combo(tab, 10, "Layout", self.rf_layout_var, ("Rectangle", "Polar"))
        self._labeled_combo(tab, 11, "Palette", self.rf_palette_var, PALETTES)
        self._labeled_combo(tab, 12, "Polar radius", self.rf_polar_radius_var, POLAR_RADIUS_MODES)
        ttk.Label(tab, text="RF smoothing radius").grid(row=13, column=0, sticky="w", pady=5)
        ttk.Spinbox(
            tab,
            from_=0,
            to=3,
            increment=1,
            textvariable=self.rf_smooth_radius_var,
            width=10,
        ).grid(row=13, column=1, sticky="w", pady=5)
        toggles = ttk.Frame(tab)
        toggles.grid(row=14, column=1, sticky="w", pady=5)
        ttk.Checkbutton(toggles, text="Flip Y", variable=self.rf_flip_y_var).grid(row=0, column=0, padx=(0, 18))
        ttk.Checkbutton(toggles, text="RGB composite", variable=self.rf_rgb_mode_var).grid(row=0, column=1)
        self._labeled_combo(
            tab,
            15,
            "Initial tab",
            self.default_viewer_tab_var,
            ("RF", "Delay / RGB", "Timeline"),
        )

    def _build_waveform_tab(self, tab: ttk.Frame) -> None:
        self._section_label(tab, "Local average waveform", 0)
        ttk.Checkbutton(
            tab,
            text="Show a compact waveform in the left sidebar",
            variable=self.show_waveform_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(
            tab,
            text=(
                "Double-click the waveform to enlarge it; double-click again "
                "or press Esc to return."
            ),
            foreground="#667085",
            wraplength=500,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.waveform_channel_mode_combo = self._labeled_combo(
            tab,
            3,
            "Nearby channels",
            self.waveform_channel_mode_var,
            tuple(WAVEFORM_CHANNEL_MODE_LABELS.values()),
        )
        ttk.Label(
            tab,
            text=(
                "The display follows the notebook: the best-PTP channel plus the "
                "four nearest channels matching this rule, ordered from larger to "
                "smaller probe Y. It is not forced to two channels above and two below."
            ),
            foreground="#667085",
            wraplength=500,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _build_tuning_tab(self, tab: ttk.Frame) -> None:
        self._section_label(tab, "Data source", 0)
        self.tuning_curve_session_entry = self._labeled_entry(
            tab,
            1,
            "Tuning Curve Session",
            self.tuning_curve_session_var,
        )
        ttk.Label(
            tab,
            text=(
                "Automatic loading reads the same-date DATE_SESSION folder. "
                "The session must be a positive integer."
            ),
            foreground="#667085",
            wraplength=440,
            justify="left",
        ).grid(row=2, column=1, sticky="w", pady=(0, 12))

        self._section_label(tab, "Head-direction display", 3)
        self._labeled_combo(
            tab,
            4,
            "Plot style",
            self.tuning_plot_mode_var,
            TUNING_PLOT_MODES,
        )
        ttk.Label(
            tab,
            text="Auto follows the RF map's Rectangle or Polar layout.",
            foreground="#667085",
            wraplength=440,
        ).grid(row=5, column=1, sticky="w", pady=(0, 10))
        self._labeled_combo(
            tab,
            6,
            "RF + tuning arrangement",
            self.tuning_layout_var,
            TUNING_LAYOUTS,
        )
        self._labeled_entry(tab, 7, "Displayed HD bins", self.tuning_display_bins_var)
        ttk.Label(
            tab,
            text="On Save, the value is rounded down to a divisor of 180 (for example, 8 → 6).",
            foreground="#667085",
            wraplength=440,
            justify="left",
        ).grid(row=8, column=1, sticky="w", pady=(0, 12))
        ttk.Checkbutton(
            tab,
            text="Compare cells in this file on one shared 0–peak Hz scale",
            variable=self.tuning_compare_scale_var,
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Checkbutton(
            tab,
            text="Smooth the 180-bin source curve",
            variable=self.tuning_smoothing_var,
        ).grid(row=10, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(tab, text="Gaussian σ (degrees)").grid(
            row=11,
            column=0,
            sticky="w",
            pady=5,
        )
        self.tuning_sigma_entry = ttk.Entry(
            tab,
            textvariable=self.tuning_smooth_sigma_var,
            width=12,
        )
        self.tuning_sigma_entry.grid(row=11, column=1, sticky="w", pady=5)
        ttk.Label(
            tab,
            text=(
                "Circular Gaussian smoothing uses mode=wrap on the raw 180-bin curve "
                "before display aggregation, preserving one angular width at every resolution."
            ),
            foreground="#667085",
            wraplength=440,
            justify="left",
        ).grid(row=12, column=0, columnspan=2, sticky="w", pady=(14, 0))

    def _select_remembered_tab(self) -> None:
        remembered = getattr(self._app_root, "_rfm_settings_tab", "General")
        for tab_id in self.notebook.tabs():
            if self._tab_name_by_widget.get(str(tab_id)) == remembered:
                self.notebook.select(tab_id)
                return

    def _remember_selected_tab(self, _event: object | None = None) -> None:
        selected = str(self.notebook.select())
        self._app_root._rfm_settings_tab = self._tab_name_by_widget.get(
            selected, "General"
        )
        self.after_idle(self._refresh_selected_tab_text)

    def _refresh_selected_tab_text(self) -> None:
        """Work around stale controls in initially hidden ttk tabs on macOS Tk."""

        try:
            selected = self.nametowidget(self.notebook.select())
        except (KeyError, tk.TclError):
            return
        pending = list(selected.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, (ttk.Label, ttk.Checkbutton)):
                try:
                    if not widget.cget("textvariable"):
                        widget.configure(text=widget.cget("text"))
                except tk.TclError:
                    continue
            elif isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Spinbox)):
                try:
                    # Aqua occasionally leaves a previously hidden field blank
                    # until it receives focus. Re-applying the variable asks the
                    # native theme to paint the current value immediately.
                    variable = widget.cget("textvariable")
                    if variable:
                        widget.configure(textvariable=variable)
                except tk.TclError:
                    continue
        try:
            selected.update_idletasks()
        except tk.TclError:
            pass

    def _clear_tab_errors(self) -> None:
        for name, variable in self._tab_error_vars.items():
            variable.set("")
            tab_id = self._tab_widget_by_name.get(name)
            if tab_id is not None:
                self.notebook.tab(tab_id, text=name)

    def _show_validation_error(self, error: SettingsValidationError) -> None:
        self._clear_tab_errors()
        tab_name = error.tab_name if error.tab_name in self.TAB_NAMES else "General"
        self._tab_error_vars[tab_name].set(str(error))
        tab_id = self._tab_widget_by_name[tab_name]
        self.notebook.tab(tab_id, text=f"{tab_name} •")
        self.notebook.select(tab_id)

    def _sync_dependent_controls(self) -> None:
        self.auto_tuning_check.state(
            ["!disabled"] if self.show_tuning_curve_var.get() else ["disabled"]
        )
        self.tuning_curve_session_entry.state(
            ["!disabled"]
            if self.show_tuning_curve_var.get()
            and self.auto_load_tuning_curve_var.get()
            else ["disabled"]
        )
        self.auto_probe_check.state(
            ["!disabled"] if self.show_probe_layout_var.get() else ["disabled"]
        )
        self.waveform_channel_mode_combo.state(
            ["!disabled"] if self.show_waveform_var.get() else ["disabled"]
        )
        self.rf_zero_bin_threshold_entry.state(
            ["!disabled"]
            if self.rf_filter_units_with_zero_bins_var.get()
            else ["disabled"]
        )
        self.tuning_sigma_entry.state(
            ["!disabled"] if self.tuning_smoothing_var.get() else ["disabled"]
        )

    @staticmethod
    def _positive_float(raw: str, label: str) -> float:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number.") from exc
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{label} must be positive and finite.")
        return value

    @staticmethod
    def _native_or_positive_int(raw: str, label: str) -> int:
        cleaned = raw.strip()
        if cleaned.casefold() in {"native", "auto"}:
            return 0
        try:
            value = int(cleaned)
        except ValueError as exc:
            raise ValueError(f"{label} must be “Native” or a positive integer.") from exc
        if value <= 0:
            raise ValueError(f"{label} must be “Native” or a positive integer.")
        return value

    def _validated_settings(self) -> ViewerSettings:
        try:
            start_ms = float(self.rf_sum_start_var.get())
            end_ms = float(self.rf_sum_end_var.get())
        except ValueError as exc:
            raise SettingsValidationError(
                "RF Map", "RF sum range must contain two numbers."
            ) from exc
        if not math.isfinite(start_ms) or not math.isfinite(end_ms) or start_ms >= end_ms:
            raise SettingsValidationError(
                "RF Map", "RF sum range must be finite and start before end."
            )
        difference_ranges: dict[str, float] = {}
        for prefix, label, start_var, end_var in (
            ("rf_difference", "Window A", self.rf_difference_start_var, self.rf_difference_end_var),
            ("rf_subtract", "Window B", self.rf_subtract_start_var, self.rf_subtract_end_var),
        ):
            try:
                first, last = float(start_var.get()), float(end_var.get())
            except ValueError as exc:
                raise SettingsValidationError("RF Map", f"{label} must contain two numbers.") from exc
            if not math.isfinite(first) or not math.isfinite(last) or first >= last:
                raise SettingsValidationError("RF Map", f"{label} must be finite and start before end.")
            difference_ranges.update({f"{prefix}_start_ms": first, f"{prefix}_end_ms": last})
        if self.rf_window_mode_var.get() not in ("Sum", "A − B"):
            raise SettingsValidationError("RF Map", "Choose Sum or A − B for the default RF mode.")
        try:
            time_resolution = self._positive_float(
                self.rf_time_resolution_var.get(), "Time resolution"
            )
            x_bins = self._native_or_positive_int(self.rf_x_bins_var.get(), "X bins")
            y_bins = self._native_or_positive_int(self.rf_y_bins_var.get(), "Y bins")
            smooth_radius = max(0, min(3, int(self.rf_smooth_radius_var.get())))
        except (tk.TclError, ValueError) as exc:
            raise SettingsValidationError("RF Map", str(exc)) from exc
        try:
            zero_bin_threshold = int(self.rf_zero_bin_threshold_var.get().strip())
        except ValueError as exc:
            raise SettingsValidationError(
                "RF Map", "Zero-bin threshold must be a positive integer."
            ) from exc
        if zero_bin_threshold <= 0:
            raise SettingsValidationError(
                "RF Map", "Zero-bin threshold must be a positive integer."
            )
        active = self.owner._active_viewer()
        maximum_zero_bins = active.data.spatial_bin_count
        if zero_bin_threshold > maximum_zero_bins:
            raise SettingsValidationError(
                "RF Map",
                f"Zero-bin threshold is too large; max is {maximum_zero_bins}.",
            )

        value_mode = self.rf_value_mode_var.get()
        palette = self.rf_palette_var.get()
        polar_radius = self.rf_polar_radius_var.get()
        layout = self.rf_layout_var.get()
        tab_keys = {
            "RF": "rf",
            "Delay / RGB": "delay",
            "Timeline": "timeline",
        }
        initial_tab = self.default_viewer_tab_var.get()
        waveform_channel_mode = WAVEFORM_CHANNEL_MODE_BY_LABEL.get(
            self.waveform_channel_mode_var.get()
        )
        if value_mode not in VALUE_MODES:
            raise SettingsValidationError("RF Map", "Choose a supported RF value mode.")
        if palette not in PALETTES:
            raise SettingsValidationError("RF Map", "Choose a supported RF palette.")
        if polar_radius not in POLAR_RADIUS_MODES:
            raise SettingsValidationError("RF Map", "Choose a supported polar-radius mode.")
        if layout not in {"Rectangle", "Polar"}:
            raise SettingsValidationError("RF Map", "Choose Rectangle or Polar layout.")
        if initial_tab not in tab_keys:
            raise SettingsValidationError("RF Map", "Choose a supported initial tab.")
        if waveform_channel_mode is None:
            raise SettingsValidationError(
                "Waveform", "Choose Same x column or Same shank."
            )

        try:
            tuning_curve_session = int(self.tuning_curve_session_var.get().strip())
        except ValueError as exc:
            raise SettingsValidationError(
                "Tuning Curve",
                "Tuning Curve Session must be a positive integer.",
            ) from exc
        if tuning_curve_session <= 0:
            raise SettingsValidationError(
                "Tuning Curve",
                "Tuning Curve Session must be a positive integer.",
            )

        smoothing = bool(self.tuning_smoothing_var.get())
        try:
            sigma_degrees = self._positive_float(
                self.tuning_smooth_sigma_var.get(), "Tuning smoothing sigma"
            )
            sigma = sigma_degrees * DEFAULT_HD_DISPLAY_BINS / 360.0
        except ValueError as exc:
            if smoothing:
                raise SettingsValidationError("Tuning Curve", str(exc)) from exc
            current = getattr(self._app_root, "_rfm_settings", ViewerSettings())
            sigma = float(current.tuning_smooth_sigma)
            if not math.isfinite(sigma) or sigma <= 0.0:
                sigma = ViewerSettings().tuning_smooth_sigma
            self.tuning_smooth_sigma_var.set(
                f"{sigma * 360.0 / DEFAULT_HD_DISPLAY_BINS:g}"
            )
        try:
            raw_hd_bins = int(self.tuning_display_bins_var.get().strip())
        except ValueError as exc:
            raise SettingsValidationError(
                "Tuning Curve", "Displayed HD bins must be an integer."
            ) from exc
        if raw_hd_bins <= 0:
            raise SettingsValidationError(
                "Tuning Curve", "Displayed HD bins must be a positive integer."
            )
        hd_bins = normalize_hd_bin_count(raw_hd_bins)
        self.tuning_display_bins_var.set(str(hd_bins))
        tuning_mode = self.tuning_plot_mode_var.get()
        if tuning_mode not in TUNING_PLOT_MODES:
            raise SettingsValidationError(
                "Tuning Curve", "Choose Auto, Polar, or Line plot style."
            )
        tuning_layout = self.tuning_layout_var.get()
        if tuning_layout not in TUNING_LAYOUTS:
            raise SettingsValidationError(
                "Tuning Curve", "Choose Side by side or Stacked arrangement."
            )
        return ViewerSettings(
            show_tuning_curve=bool(self.show_tuning_curve_var.get()),
            auto_load_tuning_curve=bool(self.auto_load_tuning_curve_var.get()),
            tuning_curve_session=tuning_curve_session,
            show_waveform=bool(self.show_waveform_var.get()),
            show_probe_layout=bool(self.show_probe_layout_var.get()),
            auto_load_probe_layout=bool(self.auto_load_probe_layout_var.get()),
            rf_sum_start_ms=start_ms,
            rf_sum_end_ms=end_ms,
            rf_subtract=self.rf_window_mode_var.get() == "A − B",
            **difference_ranges,
            rf_filter_units_with_zero_bins=bool(
                self.rf_filter_units_with_zero_bins_var.get()
            ),
            rf_zero_bin_threshold=zero_bin_threshold,
            rf_time_resolution_ms=time_resolution,
            rf_value_mode=value_mode,
            rf_x_bins=x_bins,
            rf_y_bins=y_bins,
            rf_smooth_radius=smooth_radius,
            rf_flip_y=bool(self.rf_flip_y_var.get()),
            rf_palette=palette,
            rf_polar_radius=polar_radius,
            rf_polar_layout=layout == "Polar",
            rf_rgb_mode=bool(self.rf_rgb_mode_var.get()),
            default_viewer_tab=tab_keys[initial_tab],
            waveform_channel_mode=waveform_channel_mode,
            tuning_plot_mode=tuning_mode,
            tuning_layout=tuning_layout,
            tuning_display_bins=hd_bins,
            tuning_smoothing=smoothing,
            tuning_smooth_sigma=sigma,
            tuning_compare_scale=bool(self.tuning_compare_scale_var.get()),
        )

    def _commit(self, *, close: bool) -> None:
        self.error_var.set("")
        self._clear_tab_errors()
        try:
            settings = self._validated_settings()
        except SettingsValidationError as exc:
            self._show_validation_error(exc)
            return
        except (KeyError, tk.TclError, ValueError) as exc:
            selected = self._tab_name_by_widget.get(
                str(self.notebook.select()), "General"
            )
            self._show_validation_error(SettingsValidationError(selected, str(exc)))
            return
        active = self.owner._active_viewer()
        if not getattr(active, "_viewer_ready", False):
            self.error_var.set("The viewer is still opening. Try again when it is ready.")
            return
        if not active._apply_viewer_settings(settings, persist=True, broadcast=True):
            self.error_var.set("Settings could not be saved.")
            return
        self.error_var.set("")
        if close:
            self._close()

    def _save(self) -> None:
        self._commit(close=True)

    def _close(self) -> None:
        if getattr(self._app_root, "_rfm_settings_window", None) is self:
            self._app_root._rfm_settings_window = None
        try:
            self.destroy()
        except tk.TclError:
            pass
