"""On-demand details for the selected RF cell and saved detections."""

import sys

from rfmapping_viewer.tk_support import tk, ttk


class RFInspector(tk.Toplevel):
    def __init__(self, owner):
        super().__init__(owner)
        self.withdraw()
        self.title("Inspector — RF Map Viewer")
        self.geometry("440x600")
        self.minsize(340, 360)
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
        results = ttk.Frame(tabs, padding=10)
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)
        tabs.add(results, text="Saved results")
        self.result_text = tk.Text(results, wrap="word", state="disabled", borderwidth=0,
                                   background="white", foreground="#1d1d1f", padx=8, pady=8)
        self.result_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(results, orient="vertical", command=self.result_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.result_text.configure(yscrollcommand=scroll.set)
        self.selection = selection
        self._details = ""

    def _resize_selection(self, event):
        width = max(220, event.width - 28)
        self.unit_label.configure(wraplength=width)
        self.response_label.configure(wraplength=width)

    def set_results(self, text):
        if text == self._details:
            return
        self._details = text
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", text)
        self.result_text.configure(state="disabled")

    def show(self):
        self.deiconify()
        self.lift()
        self.notebook.focus_force()

    def _close(self, _event=None):
        self.withdraw()
        return "break"
