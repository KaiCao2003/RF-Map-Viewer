"""Standalone, session-based cross-correlogram utility."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Sequence

from rfmapping_viewer.crosscorrelogram import (
    available_unit_ids,
    compute_crosscorrelograms,
    make_crosscorrelogram_figure,
)
from rfmapping_viewer.tk_support import filedialog, messagebox, tk, ttk


class CrossCorrelogramWindow(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        session_dir: Path | None = None,
        probe_name: str = "A",
        unit_ids: Sequence[int] = (),
    ):
        super().__init__(master)
        self.title("Cross-correlograms")
        self.geometry("1080x780")
        self.minsize(900, 650)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.session_var = tk.StringVar(self, value=str(session_dir or ""))
        self.probe_var = tk.StringVar(self, value=probe_name)
        selected = list(unit_ids[:3])
        self.unit_vars = [
            tk.StringVar(self, value=str(selected[index]) if index < len(selected) else "")
            for index in range(3)
        ]
        self.bin_var = tk.StringVar(self, value="1")
        self.window_var = tk.StringVar(self, value="50")
        self.status_var = tk.StringVar(self, value="Choose a session folder to begin.")
        self.subtitle_var = tk.StringVar(self, value="Spike timing between selected units")
        self._results: queue.SimpleQueue = queue.SimpleQueue()
        self._poll_after: str | None = None
        self._busy = False
        self._figure = None
        self._canvas = None
        self._controls = []
        self._build()
        if session_dir is not None:
            self._load_units()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        family = self.tk.call("font", "actual", "TkDefaultFont", "-family")
        font_size = abs(int(self.tk.call("font", "actual", "TkDefaultFont", "-size")))
        body_font = (family, font_size)
        small_font = (family, max(font_size - 1, 9))
        self.configure(background="white")
        style.configure("CCG.TFrame", background="white")
        style.configure("CCG.Inspector.TFrame", background="#f5f5f7")
        for name, background in (("CCG", "white"), ("CCG.Inspector", "#f5f5f7")):
            style.configure(f"{name}.TLabel", background=background, foreground="#1d1d1f", font=body_font)
            style.configure(f"{name}.Muted.TLabel", background=background, foreground="#6e6e73", font=small_font)
            style.configure(f"{name}.Section.TLabel", background=background, foreground="#1d1d1f", font=(family, font_size, "bold"))
        style.configure("CCG.Title.TLabel", background="white", foreground="#1d1d1f", font=(family, font_size + 6, "bold"))
        style.configure("CCG.Empty.TLabel", background="white", foreground="#1d1d1f", font=(family, font_size + 4, "bold"))
        style.configure("CCG.TEntry", padding=(8, 6), fieldbackground="white", foreground="#1d1d1f", bordercolor="#d2d2d7", lightcolor="#d2d2d7", darkcolor="#d2d2d7")
        style.configure("CCG.TCombobox", padding=(8, 5), fieldbackground="white", foreground="#1d1d1f", background="white", arrowcolor="#6e6e73", bordercolor="#d2d2d7", lightcolor="#d2d2d7", darkcolor="#d2d2d7")
        style.map("CCG.TCombobox", fieldbackground=[("disabled", "#ececef"), ("readonly", "white")], foreground=[("disabled", "#86868b"), ("readonly", "#1d1d1f")])
        style.configure("CCG.TButton", font=body_font, padding=(10, 7), background="white", foreground="#1d1d1f", borderwidth=1, bordercolor="#d2d2d7", lightcolor="white", darkcolor="white", relief="flat")
        style.map("CCG.TButton", background=[("disabled", "#f5f5f7"), ("pressed", "#e8e8ed"), ("active", "#f0f0f4")], foreground=[("disabled", "#a1a1a6")])
        style.configure("CCG.Primary.TButton", font=(family, font_size, "bold"), padding=(12, 10), background="#007aff", foreground="white", bordercolor="#007aff", lightcolor="#007aff", darkcolor="#007aff", borderwidth=0)
        style.map("CCG.Primary.TButton", background=[("disabled", "#b7d6fa"), ("pressed", "#0062cc"), ("active", "#006ee6")], foreground=[("disabled", "white"), ("!disabled", "white")])
        style.configure("CCG.Horizontal.TProgressbar", background="#007aff", troughcolor="#e8e8ed", borderwidth=0, thickness=3)

    def _build(self) -> None:
        self._configure_styles()
        self.columnconfigure(2, weight=1)
        self.rowconfigure(0, weight=1)
        inspector = ttk.Frame(self, width=280, padding=20, style="CCG.Inspector.TFrame")
        inspector.grid(row=0, column=0, sticky="nsew")
        inspector.grid_propagate(False)
        inspector.columnconfigure(0, weight=1)
        inspector.rowconfigure(5, weight=1)
        tk.Frame(self, width=1, background="#dedee3").grid(row=0, column=1, sticky="ns")

        recording = ttk.Frame(inspector, style="CCG.Inspector.TFrame")
        recording.grid(row=0, column=0, sticky="ew")
        recording.columnconfigure(0, weight=1)
        ttk.Label(recording, text="Recording", style="CCG.Inspector.Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 12))
        ttk.Label(recording, text="Session folder", style="CCG.Inspector.Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.session_entry = ttk.Entry(recording, textvariable=self.session_var, style="CCG.TEntry", width=20)
        self.session_entry.grid(row=2, column=0, sticky="ew")
        self.session_entry.bind("<Return>", lambda _event: self._load_units())
        actions = ttk.Frame(recording, style="CCG.Inspector.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 12))
        actions.columnconfigure((0, 1), weight=1)
        browse = ttk.Button(actions, text="Choose…", command=self._choose_session, style="CCG.TButton", width=8)
        browse.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        load_button = ttk.Button(actions, text="Load units", command=self._load_units, style="CCG.TButton", width=8)
        load_button.grid(row=0, column=1, sticky="ew")
        probe = ttk.Frame(recording, style="CCG.Inspector.TFrame")
        probe.grid(row=4, column=0, sticky="ew")
        probe.columnconfigure(1, weight=1)
        ttk.Label(probe, text="Probe", style="CCG.Inspector.TLabel").grid(row=0, column=0, sticky="w")
        self.probe_combo = ttk.Combobox(
            probe, textvariable=self.probe_var, values=("A", "B"), state="readonly", width=7, style="CCG.TCombobox",
        )
        self.probe_combo.grid(row=0, column=1, sticky="e")
        self.probe_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_units())
        tk.Frame(inspector, height=1, background="#dedee3").grid(row=1, column=0, sticky="ew", pady=20)

        units = ttk.Frame(inspector, style="CCG.Inspector.TFrame")
        units.grid(row=2, column=0, sticky="ew")
        units.columnconfigure(1, weight=1)
        ttk.Label(units, text="Units", style="CCG.Inspector.Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.unit_combos = []
        for index, variable in enumerate(self.unit_vars):
            label = f"Unit {index + 1}" if index < 2 else "Unit 3 · optional"
            ttk.Label(units, text=label, style="CCG.Inspector.TLabel").grid(row=index + 1, column=0, sticky="w", padx=(0, 12), pady=4)
            combo = ttk.Combobox(units, textvariable=variable, width=7, style="CCG.TCombobox")
            combo.grid(row=index + 1, column=1, sticky="e", pady=4)
            self.unit_combos.append(combo)
        tk.Frame(inspector, height=1, background="#dedee3").grid(row=3, column=0, sticky="ew", pady=20)

        timing = ttk.Frame(inspector, style="CCG.Inspector.TFrame")
        timing.grid(row=4, column=0, sticky="ew")
        timing.columnconfigure((0, 1), weight=1)
        ttk.Label(timing, text="Timing", style="CCG.Inspector.Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(timing, text="Bin (ms)", style="CCG.Inspector.Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 6))
        ttk.Label(timing, text="Window ± (ms)", style="CCG.Inspector.Muted.TLabel").grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(0, 6))
        bin_entry = ttk.Entry(timing, textvariable=self.bin_var, width=7, style="CCG.TEntry")
        bin_entry.grid(row=2, column=0, sticky="ew", padx=(0, 6))
        window_entry = ttk.Entry(timing, textvariable=self.window_var, width=7, style="CCG.TEntry")
        window_entry.grid(row=2, column=1, sticky="ew", padx=(6, 0))
        self.plot_button = ttk.Button(inspector, text="Plot pairs", command=self._plot, style="CCG.Primary.TButton")
        self.plot_button.grid(row=6, column=0, sticky="ew", pady=(20, 0))
        if ttk.Style(self).theme_use() == "aqua":
            self.plot_button.configure(default="active")

        workspace = ttk.Frame(self, style="CCG.TFrame")
        workspace.grid(row=0, column=2, sticky="nsew")
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(workspace, padding=24, style="CCG.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, text="Pairwise cross-correlograms", style="CCG.Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(toolbar, textvariable=self.subtitle_var, style="CCG.Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.save_button = ttk.Button(toolbar, text="Save figure…", command=self._save, state="disabled", style="CCG.TButton")
        self.save_button.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
        tk.Frame(workspace, height=1, background="#e8e8ed").grid(row=1, column=0, sticky="ew")
        self._controls = [
            self.session_entry, browse, self.probe_combo, *self.unit_combos,
            load_button, bin_entry, window_entry, self.plot_button,
        ]

        self.plot_frame = ttk.Frame(workspace, style="CCG.TFrame")
        self.plot_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=12)
        self._placeholder = ttk.Frame(self.plot_frame, style="CCG.TFrame")
        self._placeholder.pack(fill="both", expand=True)
        empty = ttk.Frame(self._placeholder, style="CCG.TFrame")
        empty.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(empty, text="Compare spike timing", style="CCG.Empty.TLabel").pack()
        ttk.Label(
            empty, text="Choose a recording and select two or three units.\nEach unique pair gets its own correlogram.",
            style="CCG.Muted.TLabel", justify="center",
        ).pack(pady=(10, 0))
        footer = ttk.Frame(workspace, padding=(24, 0, 24, 18), style="CCG.TFrame")
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, style="CCG.Muted.TLabel").grid(row=0, column=0, sticky="w")
        self._progress = ttk.Progressbar(
            footer, mode="indeterminate", length=80, style="CCG.Horizontal.TProgressbar",
        )
        self._progress.grid(row=0, column=1, sticky="e", padx=(16, 0))
        self._progress.grid_remove()

    def _choose_session(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Choose recording session folder", initialdir=self.session_var.get() or None,
        )
        if selected:
            self.session_var.set(selected)
            self._load_units()

    def _session(self) -> Path:
        value = self.session_var.get().strip()
        if not value:
            raise ValueError("Choose a session folder.")
        return Path(value).expanduser()

    def _load_units(self) -> None:
        if self._busy:
            return
        try:
            arguments = (self._session(), self.probe_var.get())
        except ValueError as error:
            messagebox.showerror("Cross-correlograms", str(error), parent=self)
            return
        self.status_var.set("Loading units…")
        self._start_worker("units", arguments)

    def _plot(self) -> None:
        if self._busy:
            return
        try:
            session_dir = self._session()
            values = [variable.get().strip() for variable in self.unit_vars]
            if not all(values[:2]):
                raise ValueError("Select Unit 1 and Unit 2. Unit 3 is optional.")
            unit_ids = [int(value) for value in values if value]
            bin_size = float(self.bin_var.get()) / 1000
            window_size = float(self.window_var.get()) / 1000
        except ValueError as error:
            messagebox.showerror("Cross-correlograms", str(error), parent=self)
            return
        self.status_var.set("Computing cross-correlograms…")
        self._start_worker(
            "plot", (session_dir, self.probe_var.get(), unit_ids, bin_size, window_size),
        )

    def _start_worker(self, action: str, arguments: tuple) -> None:
        self._set_busy(True)
        results = self._results

        def run() -> None:
            try:
                if action == "units":
                    result = available_unit_ids(*arguments)
                else:
                    result = compute_crosscorrelograms(*arguments)
                results.put((action, arguments, result, None))
            except Exception as error:
                results.put((action, arguments, None, error))

        # Workers only publish data; Tk calls and canvas updates stay on the main thread.
        threading.Thread(target=run, name="crosscorrelogram", daemon=True).start()
        self._poll_after = self.after(50, self._poll_worker)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for control in self._controls:
            control.state(["disabled"] if busy else ["!disabled"])
        self.save_button.state(["disabled"] if busy or self._figure is None else ["!disabled"])
        if busy:
            self._progress.grid()
            self._progress.start(12)
        else:
            self._progress.stop()
            self._progress.grid_remove()

    def _poll_worker(self) -> None:
        self._poll_after = None
        try:
            action, arguments, result, error = self._results.get_nowait()
        except queue.Empty:
            self._poll_after = self.after(50, self._poll_worker)
            return
        try:
            if error is not None:
                raise error
            if action == "units":
                self._populate_units(result)
                self.session_entry.xview_moveto(1.0)
                self.status_var.set(f"{len(result)} units in {arguments[0].name} · Probe {arguments[1]}")
                self.subtitle_var.set(f"{arguments[0].name}  ·  Probe {arguments[1]}")
            else:
                session_dir, probe_name, unit_ids, bin_size, window_size = arguments
                figure = make_crosscorrelogram_figure(
                    result, session_dir, probe_name, bin_size, window_size,
                )
                self._show_figure(figure)
                self.status_var.set(
                    f"{session_dir.name} · Probe {probe_name} · Units {', '.join(map(str, unit_ids))}"
                )
                pair_label = "1 pair" if len(result.columns) == 1 else "3 pairs"
                self.subtitle_var.set(f"{session_dir.name}  ·  Probe {probe_name}  ·  {pair_label}")
        except Exception as error:
            self.status_var.set("Could not load cross-correlograms.")
            messagebox.showerror("Cross-correlograms", str(error), parent=self)
        finally:
            self._set_busy(False)

    def _populate_units(self, unit_ids: list[int]) -> None:
        values = [str(unit_id) for unit_id in unit_ids]
        selected = []
        for index, (variable, combo) in enumerate(zip(self.unit_vars, self.unit_combos)):
            combo.configure(values=([""] + values) if index == 2 else values)
            value = variable.get().strip()
            if value not in values or value in selected:
                value = next((item for item in values if item not in selected), "") if index < 2 else ""
            variable.set(value)
            selected.append(value)

    def _show_figure(self, figure) -> None:
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        if self._canvas is not None:
            self._canvas.get_tk_widget().destroy()
            self._figure.clear()
        self._placeholder.pack_forget()
        self._figure = figure
        self._canvas = FigureCanvasTkAgg(figure, master=self.plot_frame)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)
        self._canvas.draw()

    def _save(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title="Save cross-correlograms", initialfile="crosscorrelograms.png",
            defaultextension=".png",
            filetypes=(("PNG image", "*.png"), ("PDF document", "*.pdf"), ("SVG image", "*.svg")),
        )
        if path:
            try:
                self._figure.savefig(path, dpi=200, facecolor="white", transparent=False)
            except Exception as error:
                messagebox.showerror("Save cross-correlograms", str(error), parent=self)

    def _close(self) -> None:
        self.destroy()

    def destroy(self) -> None:
        self._progress.stop()
        if self._poll_after is not None:
            self.after_cancel(self._poll_after)
            self._poll_after = None
        super().destroy()
