"""On-demand details for the selected RF cell and saved detections."""

import sys

from rfmapping_viewer.tk_support import scroll_canvas_precise, tk, ttk


class RFInspector(tk.Toplevel):
    def __init__(self, owner):
        super().__init__(owner)
        self.withdraw()
        # An owned window stays above its viewer without covering other apps.
        self.transient(owner)
        self.title("Inspector — RF Map Viewer")
        self.geometry("500x650")
        self.minsize(440, 400)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.bind("<Command-w>" if sys.platform == "darwin" else "<Control-w>", self._close)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        tabs = ttk.Notebook(self)
        self.notebook = tabs
        tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        tabs.enable_traversal()
        selection = ttk.Frame(tabs, style="Panel.TFrame", padding=14)
        selection.columnconfigure(0, weight=1)
        tabs.add(selection, text="Selection")
        ttk.Label(selection, text="Unit", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.unit_label = ttk.Label(selection, style="Muted.TLabel", wraplength=370, justify="left")
        self.unit_label.grid(row=1, column=0, sticky="ew", pady=(6, 18))
        ttk.Label(selection, text="Response", style="Title.TLabel").grid(row=2, column=0, sticky="w")
        self.response_label = ttk.Label(selection, style="Muted.TLabel", wraplength=370, justify="left")
        self.response_label.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        selection.bind("<Configure>", self._resize_selection)
        results = ttk.Frame(tabs, style="Panel.TFrame", padding=10)
        results.columnconfigure(0, weight=1)
        results.rowconfigure(1, weight=1)
        tabs.add(results, text="Saved results")
        tabs.select(results)
        self.results_header = ttk.Label(results, style="Title.TLabel", text="Saved results")
        self.results_header.grid(row=0, column=0, sticky="w", pady=(0, 8))
        summary = ttk.Frame(results, style="Panel.TFrame")
        summary.grid(row=1, column=0, sticky="nsew")
        summary.columnconfigure(0, weight=1)
        summary.rowconfigure(0, weight=1)
        self._results_canvas = tk.Canvas(summary, highlightthickness=0, background="white")
        self._results_canvas.grid(row=0, column=0, sticky="nsew")
        summary_scroll = ttk.Scrollbar(summary, orient="vertical", command=self._results_canvas.yview)
        summary_scroll.grid(row=0, column=1, sticky="ns")
        self._results_canvas.configure(yscrollcommand=summary_scroll.set)
        self.result_cards_frame = ttk.Frame(self._results_canvas, style="Panel.TFrame")
        self.result_cards_frame.columnconfigure(0, weight=1)
        cards_window = self._results_canvas.create_window(0, 0, anchor="nw", window=self.result_cards_frame)
        self._results_canvas.bind("<Configure>", lambda event: self._results_canvas.itemconfigure(cards_window, width=event.width))
        self.result_cards_frame.bind("<Configure>", lambda _event: self._results_canvas.configure(scrollregion=self._results_canvas.bbox("all")))
        self._bind_result_scroll(self._results_canvas)
        self._bind_result_scroll(self.result_cards_frame)
        self.statistics_note = ttk.Label(
            results, text="Mean/SD: computed current-source rates; not saved detector statistics.",
            style="Muted.TLabel", wraplength=440, justify="left",
        )
        self.statistics_note.grid(row=2, column=0, sticky="ew", pady=(8, 4))
        self.details_button = ttk.Button(results, text="Show technical details", command=self._toggle_details)
        self.details_button.grid(row=3, column=0, sticky="w")
        self.details_frame = ttk.Frame(results)
        self.details_frame.columnconfigure(0, weight=1)
        self.details_frame.rowconfigure(0, weight=1)
        self.result_text = tk.Text(self.details_frame, wrap="word", state="disabled", borderwidth=0,
                                  height=9, background="white", foreground="#1d1d1f", padx=8, pady=8)
        self.result_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self.details_frame, orient="vertical", command=self.result_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.result_text.configure(yscrollcommand=scroll.set)
        self.selection = selection
        self._details = ""
        self._summary_key = None
        self.summary_cards = []
        self._details_visible = False
        results.bind("<Configure>", lambda event: self.statistics_note.configure(wraplength=max(200, event.width - 20)))

    def _resize_selection(self, event):
        width = max(220, event.width - 28)
        self.unit_label.configure(wraplength=width)
        self.response_label.configure(wraplength=width)

    def set_results(self, text, *, summaries=(), unit_id=None):
        if text != self._details:
            self._details = text
            self.result_text.configure(state="normal")
            self.result_text.delete("1.0", "end")
            self.result_text.insert("1.0", text)
            self.result_text.configure(state="disabled")
        key = (tuple(summaries), unit_id)
        if key == self._summary_key:
            return
        previous_unit = self._summary_key[1] if self._summary_key is not None else None
        self._summary_key = key
        self.results_header.configure(text=f"Unit {unit_id}" if unit_id is not None else "Saved results")
        for widget in self.result_cards_frame.winfo_children():
            widget.destroy()
        self.summary_cards = []
        for result in summaries:
            self._add_result_card(result)
        if not summaries:
            ttk.Label(self.result_cards_frame, text="No unit selected" if unit_id is None else "No result layers selected",
                      style="Muted.TLabel").grid(sticky="w", pady=10)
        if summaries:
            self.statistics_note.grid()
        else:
            self.statistics_note.grid_remove()
        if unit_id != previous_unit:
            self._results_canvas.yview_moveto(0)

    def _add_result_card(self, result):
        card = ttk.Frame(self.result_cards_frame, style="Panel.TFrame", padding=(0, 4, 4, 4))
        card.grid(row=len(self.summary_cards), column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        self.summary_cards.append(card)

        def label(text, row, column=0, *, span=2, style="Muted.TLabel", sticky="w"):
            widget = ttk.Label(card, text=text, style=style, justify="left")
            widget.grid(row=row, column=column, columnspan=span, sticky=sticky)
            self._bind_result_scroll(widget)
            return widget

        label(f"{result.dimension.upper()} {result.rf_type.capitalize()}", 0, span=1, style="Value.TLabel")
        label(result.status, 0, 1, span=1, sticky="e")
        stats = result.statistics
        threshold = stats.threshold if stats is not None else None
        threshold_label = "Candidate" if stats is not None and stats.candidate_threshold else "Threshold"
        label(f"{threshold_label}   {threshold or '—'}", 1, span=1, style="Panel.TLabel")
        label(result.window, 1, 1, span=1, sticky="e")
        mean = f"{stats.mean_hz:.5g} Hz" if stats is not None and stats.mean_hz is not None else "—"
        sd = f"{stats.sd_hz:.5g} Hz" if stats is not None and stats.sd_hz is not None else "—"
        label(f"Mean   {mean}", 2, span=1, style="Value.TLabel")
        label(f"SD   {sd}", 2, 1, span=1, style="Value.TLabel")
        notes = []
        if stats is not None and stats.error:
            notes.append("Mean/SD unavailable")
        if result.warning:
            notes.append("Result warning")
        if notes:
            note = label(" · ".join(notes) + " — see details", 3)
            card.bind("<Configure>", lambda event: note.configure(wraplength=max(200, event.width - 8)))
        ttk.Separator(card).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._bind_result_scroll(card)

    def _toggle_details(self):
        self._details_visible = not self._details_visible
        if self._details_visible:
            self.details_frame.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        else:
            self.details_frame.grid_remove()
        self.details_button.configure(text="Hide technical details" if self._details_visible else "Show technical details")

    def _bind_result_scroll(self, widget):
        widget.bind("<MouseWheel>", self._scroll_results)
        widget.bind("<Button-4>", lambda _event: self._scroll_result_units(-1))
        widget.bind("<Button-5>", lambda _event: self._scroll_result_units(1))
        if int(str(self.tk.call("package", "present", "Tk")).split(".")[0]) >= 9:
            widget.bind("<TouchpadScroll>", lambda event: scroll_canvas_precise(self._results_canvas, event.delta))

    def _scroll_result_units(self, units):
        if self._results_canvas.yview() != (0.0, 1.0):
            self._results_canvas.yview_scroll(units, "units")
        return "break"

    def _scroll_results(self, event):
        delta = -event.delta if sys.platform == "darwin" else -int(event.delta / 120)
        return self._scroll_result_units(delta)

    def show(self):
        self.deiconify()
        self.lift()
        self.notebook.focus_force()

    def _close(self, _event=None):
        self.withdraw()
        return "break"
