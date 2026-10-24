#!/usr/bin/env python3
"""ork_batch_gui.py -- tkinter front end for ork_batch.py.

    python ork_batch_gui.py                    (or double-click ork_batch_gui.bat)
    python ork_batch_gui.py study.json         open a config
    python ork_batch_gui.py ref1.ork ref2.ork  start with reference files loaded

Everything runs through the same orkbatch library the CLI uses: Preview runs the
CLI's planning/validation steps, and Generate runs `ork_batch.py generate -c <config>`,
so a study set up here behaves the same on the command line (File > Save config).
"""

import contextlib
import hashlib
import io
import json
import os
import queue
import re
import sys
import tempfile
import threading
import traceback
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
except ImportError:
    print("tkinter isn't available in this Python install (reinstall Python from python.org with Tcl/Tk).",
          file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from orkbatch import __version__, cli  # noqa: E402
from orkbatch.engine import BatchError, build_variant, check_entries, plan  # noqa: E402
from orkbatch.orkio import OrkDocument, OrkLoadError  # noqa: E402
from orkbatch.params import BODY_TAGS, FIN_TAGS, ParamError, list_params, resolve_param  # noqa: E402
from orkbatch.selectors import ComponentIndex, SelectorError, comp_name  # noqa: E402
from orkbatch.sweep import _ENTRY_RE, _TAIL_RE, EntryError, dedupe_labels, expand, parse_entry  # noqa: E402
from orkbatch.units import LENGTH_UNITS, MASS_UNITS, Units, fmt_value  # noqa: E402

DEFAULT_OUT = str(HERE / "output" / "gui_batch")
PREVIEW_ROW_LIMIT = 5000
OPS = ("=", "+=", "-=", "*=")
OP_NAMES = {"set": "=", "abs": "=", "add": "+=", "delta": "+=", "sub": "-=", "subtract": "-=",
            "scale": "*=", "mul": "*="}

BOOL_OPTIONS = [  # (config key, label, default)
    ("clear_mass_override", "Clear mass overrides on modified parts (OpenRocket recomputes their mass)", False),
    ("preserve_nose_volume", "Preserve nose-cone internal volume (lengthen the tube behind it)", False),
    ("keep_sims", "Keep stored simulation results (default: strip them, keep the setups)", False),
    ("allow_invalid", "Write variants that fail geometry checks", False),
    ("dedupe", "Skip variants identical to an earlier one", True),
    ("manifest", "Write variants_manifest.csv + batch_config.json", True),
    ("overwrite", "Overwrite existing files without asking", False),
]
CHEAT_SHEET = """SELECTORS (which component)
  nosecone, bodytube[2], bodytube[*], fins, fins[1], *
  @Fiberglass Tube        by name        @"Name/with/slash"
  #585ea21c               by id prefix
  @Fiberglass Tube/masscomponent[0]     '/' searches inside the previous match

PARAMETERS: any value shown in the Parameters list, plus
  diameter / forediameter / aftdiameter   (2 x radius)
  sweepangle (fins)    trim (nose cone: length -= value)
  TAG@ATTR e.g. material@density

OPERATIONS:  =  absolute    +=  add    -=  subtract    *=  multiply

VALUES
  0.3   0.3in   7.62mm   2lb          single value (unit suffix optional)
  10,12,14     square,airfoil          list
  10:16:1                              range start:stop:step (inclusive)
  10:16:n4                             4 evenly spaced points

COMBINATION
  Sweeps form a full grid. Sweeps sharing a Link group step together.
"""


# ------------------------------------------------------------------- helpers

def entry_to_text(e):
    s = f"{e['target']}{e['op']}{e['spec']}"
    if e.get("label"):
        s += f" as {e['label']}"
    if e.get("link"):
        s += f" link {e['link']}"
    return s


def text_to_entry(text):
    body, label, link = text.strip(), "", ""
    while True:
        m = _TAIL_RE.search(body)
        if not m:
            break
        if m.group(1).lower() == "as":
            label = m.group(2)
        else:
            link = m.group(2)
        body = body[: m.start()]
    m = _ENTRY_RE.match(body)
    if not m:
        raise EntryError(f"Can't parse '{text}'")
    return {"target": m.group("target").strip(), "op": m.group("op"),
            "spec": m.group("spec").strip(), "label": label, "link": link}


def obj_to_entry(obj):
    """Config entry (string or object form) -> GUI entry dict."""
    if isinstance(obj, str):
        return text_to_entry(obj)
    op = OP_NAMES.get(obj.get("op", "="), obj.get("op", "="))
    unit = obj.get("unit", "")

    def tok(v):
        return f"{v}{unit}" if unit and not isinstance(v, str) else str(v)

    if "spec" in obj:
        spec = str(obj["spec"])
    elif "values" in obj:
        vals = obj["values"] if isinstance(obj["values"], list) else [obj["values"]]
        spec = ",".join(tok(v) for v in vals)
    elif "value" in obj:
        spec = tok(obj["value"])
    elif "range" in obj:
        spec = ":".join(tok(x) for x in obj["range"])
    elif "linspace" in obj:
        r = obj["linspace"]
        spec = f"{tok(r[0])}:{tok(r[1])}:n{int(r[2])}"
    else:
        raise EntryError(f"config entry {obj} has no values")
    return {"target": obj["target"], "op": op, "spec": spec,
            "label": obj.get("label") or obj.get("as") or "", "link": obj.get("link") or obj.get("group") or ""}


def capture_die(fn, *a, **kw):
    """Run a cli helper that may call die(); turn that into a RuntimeError with the message."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            return fn(*a, **kw)
    except SystemExit:
        raise RuntimeError(err.getvalue().strip().removeprefix("error: ") or "failed")


class QueueWriter(io.TextIOBase):
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(("text", s))
        return len(s)


def best_selector(index, el):
    """Shortest selector that picks exactly this element."""
    if el.tag in FIN_TAGS and sum(1 for c in index.components if c.tag in FIN_TAGS) == 1:
        return "fins"
    sel_tag, sel_name = index.canonical(el)
    if "[" not in sel_tag:
        return sel_tag
    return sel_name or sel_tag


# ---------------------------------------------------------------- entry dialog

class EntryDialog(tk.Toplevel):
    """Add / edit one sweep."""

    def __init__(self, app, entry, title):
        super().__init__(app.root)
        self.app = app
        self.result = None
        self.title(title)
        self.transient(app.root)
        self.resizable(True, False)
        e = dict({"target": "", "op": "=", "spec": "", "label": "", "link": ""}, **entry)
        sel, _, param = e["target"].rpartition(".")

        self.sel = tk.StringVar(value=sel)
        self.param = tk.StringVar(value=param)
        self.op = tk.StringVar(value=e["op"])
        self.spec = tk.StringVar(value=e["spec"])
        self.label = tk.StringVar(value=e["label"])
        self.link = tk.StringVar(value=e["link"])
        self.r_start, self.r_stop, self.r_step, self.r_n = (tk.StringVar() for _ in range(4))

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="Component (selector)").grid(row=r, column=0, sticky="w", pady=3)
        self.sel_box = ttk.Combobox(f, textvariable=self.sel, values=app.selector_suggestions(), width=46)
        self.sel_box.grid(row=r, column=1, columnspan=2, sticky="ew", pady=3)
        r += 1
        ttk.Label(f, text="Parameter").grid(row=r, column=0, sticky="w", pady=3)
        self.param_box = ttk.Combobox(f, textvariable=self.param, width=46, postcommand=self._fill_params)
        self.param_box.grid(row=r, column=1, columnspan=2, sticky="ew", pady=3)
        r += 1
        self.current = ttk.Label(f, text="", foreground="#555")
        self.current.grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1
        ttk.Label(f, text="Operation").grid(row=r, column=0, sticky="w", pady=3)
        opf = ttk.Frame(f)
        opf.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        for op, txt in (("=", "= absolute"), ("+=", "+= add"), ("-=", "-= subtract"), ("*=", "*= multiply")):
            ttk.Radiobutton(opf, text=txt, value=op, variable=self.op).pack(side="left", padx=(0, 10))
        r += 1
        ttk.Label(f, text="Value(s)").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=self.spec, width=48).grid(row=r, column=1, columnspan=2, sticky="ew", pady=3)
        r += 1
        ttk.Label(f, text="e.g.  12   12in   305mm   10,12,14   10:16:1 (step)   10:16:n4 (count)   airfoil",
                  foreground="#555").grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1
        self.range_frame = ttk.LabelFrame(f, text="Range builder", padding=6)
        self.range_frame.grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        for i, (txt, var, w) in enumerate((("start", self.r_start, 8), ("stop", self.r_stop, 8),
                                           ("step", self.r_step, 7), ("or count", self.r_n, 5))):
            ttk.Label(self.range_frame, text=txt).grid(row=0, column=2 * i, padx=(6, 2))
            ttk.Entry(self.range_frame, textvariable=var, width=w).grid(row=0, column=2 * i + 1)
        ttk.Button(self.range_frame, text="Use step", command=self._use_step).grid(row=0, column=8, padx=(10, 2))
        ttk.Button(self.range_frame, text="Use count", command=self._use_count).grid(row=0, column=9, padx=2)
        r += 1
        ttk.Label(f, text="Label (optional)").grid(row=r, column=0, sticky="w", pady=3)
        self.label_entry = ttk.Entry(f, textvariable=self.label, width=20)
        self.label_entry.grid(row=r, column=1, sticky="w", pady=3)
        r += 1
        ttk.Label(f, text="Link group (optional)").grid(row=r, column=0, sticky="w", pady=3)
        self.link_entry = ttk.Entry(f, textvariable=self.link, width=20)
        self.link_entry.grid(row=r, column=1, sticky="w", pady=3)
        ttk.Label(f, text="sweeps in the same group step together instead of forming a grid",
                  foreground="#555").grid(row=r, column=2, sticky="w")
        r += 1
        self.check = ttk.Label(f, text="", wraplength=560, justify="left")
        self.check.grid(row=r, column=0, columnspan=3, sticky="w", pady=(10, 4))
        r += 1
        bf = ttk.Frame(f)
        bf.grid(row=r, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bf, text="OK", command=self._ok).pack(side="right", padx=4)

        for v in (self.sel, self.param, self.op, self.spec, self.label, self.link):
            v.trace_add("write", lambda *a: self._validate())
        self.bind("<Return>", lambda ev: self._ok())
        self.bind("<Escape>", lambda ev: self.destroy())
        self._fill_params()
        self._validate()
        self.update_idletasks()
        self.geometry(f"+{app.root.winfo_rootx() + 120}+{app.root.winfo_rooty() + 90}")
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.sel_box.focus_set()

    def _fill_params(self):
        self.param_box["values"] = self.app.param_suggestions(self.sel.get().strip())

    def _use_step(self):
        if self.r_start.get() and self.r_stop.get() and self.r_step.get():
            self.spec.set(f"{self.r_start.get().strip()}:{self.r_stop.get().strip()}:{self.r_step.get().strip()}")

    def _use_count(self):
        if self.r_start.get() and self.r_stop.get() and self.r_n.get():
            self.spec.set(f"{self.r_start.get().strip()}:{self.r_stop.get().strip()}:n{self.r_n.get().strip()}")

    def _entry(self):
        return {"target": f"{self.sel.get().strip()}.{self.param.get().strip()}",
                "op": self.op.get(), "spec": self.spec.get().strip(),
                "label": self.label.get().strip(), "link": self.link.get().strip()}

    def _validate(self):
        self.current.config(text=self.app.describe_target(self.sel.get().strip(), self.param.get().strip()))
        e = self._entry()
        for name in ("label", "link"):
            if e[name] and not e[name].isidentifier():
                self.check.config(text=f"{name.capitalize()} must be a simple name (letters, digits, _).",
                                  foreground="#b00020")
                return False
        if not self.sel.get().strip() or not self.param.get().strip() or not e["spec"]:
            self.check.config(text="Fill in component, parameter and value(s).", foreground="#555")
            return False
        try:
            parsed = parse_entry(entry_to_text(e), self.app.units())
        except (EntryError, ValueError) as ex:
            self.check.config(text=str(ex), foreground="#b00020")
            return False
        shown = [fmt_value(v) for v in parsed.display]
        if len(shown) > 8:
            shown = shown[:4] + ["..."] + shown[-3:]
        unit = f" {parsed.unit}" if parsed.unit else ""
        what = "value" if len(parsed.values) == 1 else "values"
        text = f"OK: {len(parsed.values)} {what}: {', '.join(shown)}{unit}"
        if parsed.op != "=" or e["target"] != parsed.target:
            text += f"   (means {parsed.target} {parsed.op} ...)"
        text += f"   label '{parsed.label}'"
        self.check.config(text=text, foreground="#1b5e20")
        return True

    def _ok(self):
        if self._validate():
            self.result = self._entry()
            self.destroy()


# ------------------------------------------------------------------- main app

class App:
    def __init__(self, root, argv):
        self.root = root
        self.q = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.entries = []          # list of entry dicts
        self.refs = []             # reference .ork paths
        self.docs = {}             # path -> (OrkDocument, ComponentIndex)
        self.current = None        # (path, doc, index) shown in the inspector
        self.comp_items = {}       # tree iid -> element
        self.config_path = None
        self.last_out_dir = None
        self.run_lines = []
        self._partial = ""

        self._init_vars()
        self._build_menu()
        self._build_ui()
        self._set_title()
        cli.confirm = self.gui_confirm
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(60, self._poll)

        for a in argv:
            if a.lower().endswith(".json"):
                self.open_config(a)
            elif Path(a).is_dir() or a.lower().endswith(".ork"):
                self.add_refs([a] if not Path(a).is_dir() else [str(p) for p in sorted(Path(a).glob("*.ork"))])

    # ------------------------------------------------------------- variables
    def _init_vars(self):
        self.v = {
            "out_dir": tk.StringVar(value=DEFAULT_OUT), "prefix": tk.StringVar(),
            "name_template": tk.StringVar(), "unit": tk.StringVar(value="in"),
            "mass_unit": tk.StringVar(value="kg"), "combine": tk.StringVar(value="grid"),
        }
        for key, _label, default in BOOL_OPTIONS:
            self.v[key] = tk.BooleanVar(value=default)
        self.verbose = tk.BooleanVar(value=True)
        self.v["unit"].trace_add("write", lambda *a: self._units_changed())
        self.v["mass_unit"].trace_add("write", lambda *a: self._units_changed())
        self.v["combine"].trace_add("write", lambda *a: self.refresh_edits())

    def units(self):
        return Units(self.v["unit"].get(), self.v["mass_unit"].get())

    def _set_title(self):
        name = Path(self.config_path).name if self.config_path else "untitled study"
        self.root.title(f"ORK Batch Generator {__version__} - {name}")

    # ------------------------------------------------------------------ menu
    def _build_menu(self):
        m = tk.Menu(self.root)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="New study", command=self.new_study)
        fm.add_command(label="Open config...", command=self.open_config, accelerator="Ctrl+O")
        fm.add_command(label="Save config", command=self.save_config, accelerator="Ctrl+S")
        fm.add_command(label="Save config as...", command=lambda: self.save_config(ask=True))
        fm.add_separator()
        fm.add_command(label="Add reference files...", command=self.browse_refs)
        fm.add_separator()
        fm.add_command(label="Exit", command=self.on_close)
        m.add_cascade(label="File", menu=fm)
        tm = tk.Menu(m, tearoff=0)
        tm.add_command(label="Preview (dry run)", command=self.preview, accelerator="F5")
        tm.add_command(label="Generate batch", command=self.generate, accelerator="F6")
        tm.add_separator()
        tm.add_command(label="Compare two .ork files (diff)...", command=self.diff_dialog)
        tm.add_separator()
        tm.add_command(label="Show equivalent command line", command=self.show_command)
        tm.add_command(label="Open output folder", command=self.open_out_dir)
        tm.add_command(label="Clear log", command=lambda: self.log.delete("1.0", "end"))
        m.add_cascade(label="Tools", menu=tm)
        hm = tk.Menu(m, tearoff=0)
        hm.add_command(label="Syntax cheat sheet", command=self.cheat_sheet)
        hm.add_command(label="Open README", command=lambda: self._startfile(HERE / "README.md"))
        hm.add_command(label="About", command=lambda: messagebox.showinfo(
            "About", f"ORK Batch Generator {__version__}\nGUI for ork_batch.py\n\n{HERE}", parent=self.root))
        m.add_cascade(label="Help", menu=hm)
        self.root.config(menu=m)
        self.root.bind("<Control-o>", lambda e: self.open_config())
        self.root.bind("<Control-s>", lambda e: self.save_config())
        self.root.bind("<F5>", lambda e: self.preview())
        self.root.bind("<F6>", lambda e: self.generate())

    # -------------------------------------------------------------------- UI
    def _build_ui(self):
        # bottom bar first, so it keeps its space when the window is small
        bar = ttk.Frame(self.root, padding=(6, 6))
        bar.pack(side="bottom", fill="x")
        self.preview_btn = ttk.Button(bar, text="Preview (F5)", command=self.preview)
        self.preview_btn.pack(side="left")
        self.gen_btn = ttk.Button(bar, text="Generate batch (F6)", command=self.generate)
        self.gen_btn.pack(side="left", padx=6)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left")
        ttk.Button(bar, text="Open output folder", command=self.open_out_dir).pack(side="right")
        self.progress = ttk.Progressbar(bar, mode="determinate", length=220)
        self.progress.pack(side="right", padx=8)
        self.status = tk.StringVar(value="Add a reference .ork file to begin.")
        ttk.Label(bar, textvariable=self.status).pack(side="left", padx=12, fill="x", expand=True)

        outer = ttk.PanedWindow(self.root, orient="horizontal")
        outer.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        left = ttk.PanedWindow(outer, orient="vertical")
        outer.add(left, weight=2)
        self.nb = ttk.Notebook(outer)
        outer.add(self.nb, weight=3)

        # reference files
        rf = ttk.LabelFrame(left, text="Reference .ork files", padding=6)
        left.add(rf, weight=1)
        lf = ttk.Frame(rf)
        lf.pack(fill="both", expand=True)
        self.ref_list = tk.Listbox(lf, height=4, exportselection=False, activestyle="none")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.ref_list.yview)
        self.ref_list.config(yscrollcommand=sb.set)
        self.ref_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.ref_list.bind("<<ListboxSelect>>", lambda e: self._ref_selected())
        bf = ttk.Frame(rf)
        bf.pack(fill="x", pady=(4, 0))
        ttk.Button(bf, text="Add files...", command=self.browse_refs).pack(side="left")
        ttk.Button(bf, text="Add folder...", command=self.browse_ref_folder).pack(side="left", padx=4)
        ttk.Button(bf, text="Remove", command=self.remove_ref).pack(side="left")
        ttk.Button(bf, text="Clear", command=self.clear_refs).pack(side="left", padx=4)

        # components
        cf = ttk.LabelFrame(left, text="Components   (right-click for actions)", padding=6)
        left.add(cf, weight=3)
        self.comp_tree = ttk.Treeview(cf, columns=("type", "selector"), show="tree headings", selectmode="browse")
        self.comp_tree.heading("#0", text="Name")
        self.comp_tree.heading("type", text="Type")
        self.comp_tree.heading("selector", text="Selector")
        self.comp_tree.column("#0", width=210)
        self.comp_tree.column("type", width=110, stretch=False)
        self.comp_tree.column("selector", width=150)
        self._scrolled(cf, self.comp_tree)
        self.comp_tree.bind("<<TreeviewSelect>>", lambda e: self._comp_selected())
        self.comp_tree.bind("<Button-3>", self._comp_menu)

        # parameters
        pf = ttk.LabelFrame(left, text="Parameters   (double-click to sweep, right-click for more)", padding=6)
        left.add(pf, weight=3)
        self.param_tree = ttk.Treeview(pf, columns=("param", "value", "unit", "note"), show="headings",
                                       selectmode="browse")
        for c, w in (("param", 150), ("value", 110), ("unit", 50), ("note", 90)):
            self.param_tree.heading(c, text=c.capitalize())
            self.param_tree.column(c, width=w, stretch=c in ("param", "note"))
        self._scrolled(pf, self.param_tree)
        self.param_tree.bind("<Double-1>", lambda e: self._param_action())
        self.param_tree.bind("<Button-3>", self._param_menu)

        self._build_edits_tab()
        self._build_options_tab()
        self._build_preview_tab()
        self._build_log_tab()

    def _scrollable_tab(self, title):
        """A notebook tab whose content scrolls vertically (for small screens)."""
        holder = ttk.Frame(self.nb)
        self.nb.add(holder, text=title)
        bg = ttk.Style().lookup("TFrame", "background") or None
        canvas = tk.Canvas(holder, highlightthickness=0, borderwidth=0, background=bg)
        vs = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, padding=8)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")

        holder.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        holder.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return inner

    def _scrolled(self, parent, tree, horizontal=False):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        ys = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ys.set)
        tree.grid(in_=frame, row=0, column=0, sticky="nsew")
        tree.lift(frame)  # the tree was created before `frame`; without this the frame hides it
        ys.grid(row=0, column=1, sticky="ns")
        if horizontal:
            xs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            tree.configure(xscrollcommand=xs.set)
            xs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

    def _build_edits_tab(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Sweeps")
        tb = ttk.Frame(tab)
        tb.pack(fill="x")
        ttk.Button(tb, text="Add sweep...", command=self.add_entry).pack(side="left")
        ttk.Button(tb, text="Edit...", command=self.edit_entry).pack(side="left", padx=(12, 0))
        ttk.Button(tb, text="Duplicate", command=self.duplicate_entry).pack(side="left", padx=4)
        ttk.Button(tb, text="Remove", command=self.remove_entry).pack(side="left")
        ttk.Button(tb, text="Move up", command=lambda: self.move_entry(-1)).pack(side="left", padx=(12, 0))
        ttk.Button(tb, text="Move down", command=lambda: self.move_entry(1)).pack(side="left", padx=4)

        cols = ("target", "op", "spec", "label", "link", "count")
        self.edits_tree = ttk.Treeview(tab, columns=cols, show="headings", selectmode="browse")
        for c, txt, w in (("target", "Target", 240), ("op", "Op", 40),
                          ("spec", "Value(s)", 170), ("label", "Label", 90), ("link", "Link", 60),
                          ("count", "Values / status", 200)):
            self.edits_tree.heading(c, text=txt)
            self.edits_tree.column(c, width=w, stretch=c in ("target", "spec", "count"))
        self.edits_tree.tag_configure("bad", foreground="#b00020")
        holder = ttk.Frame(tab)
        holder.pack(fill="both", expand=True, pady=6)
        self._scrolled(holder, self.edits_tree)
        self.edits_tree.bind("<Double-1>", lambda e: self.edit_entry())
        self.edits_tree.bind("<Delete>", lambda e: self.remove_entry())

        self.estimate = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.estimate, font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        ttk.Label(tab, foreground="#555", justify="left", text=(
            "Sweeps are applied in list order. Each output starts from a fresh copy of its "
            "reference file.\nTip: select a parameter on the left and double-click it to sweep it. "
            "Help > Syntax cheat sheet lists selectors and value formats.")).pack(anchor="w", pady=(4, 0))

    def _build_options_tab(self):
        tab = self._scrollable_tab("Options")
        tab.columnconfigure(0, weight=1)
        hint = {"foreground": "#555", "wraplength": 620, "justify": "left"}

        out = ttk.LabelFrame(tab, text="Output", padding=8)
        out.grid(row=0, column=0, sticky="ew")
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Output folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(out, textvariable=self.v["out_dir"], width=30).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(out, text="Browse...", command=self.browse_out).grid(row=0, column=2)
        ttk.Label(out, text="Filename prefix").grid(row=1, column=0, sticky="w", pady=3)
        pf = ttk.Frame(out)
        pf.grid(row=1, column=1, columnspan=2, sticky="w", padx=4)
        ttk.Entry(pf, textvariable=self.v["prefix"], width=20).pack(side="left")
        ttk.Label(pf, text="  blank = reference file name", foreground="#555").pack(side="left")
        ttk.Label(out, text="Name template").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(out, textvariable=self.v["name_template"], width=30).grid(row=2, column=1, columnspan=2,
                                                                          sticky="ew", padx=4)
        self.template_help = ttk.Label(out, text="", **hint)
        self.template_help.grid(row=3, column=1, columnspan=2, sticky="w", padx=4)

        uc = ttk.LabelFrame(tab, text="Units and combination", padding=8)
        uc.grid(row=1, column=0, sticky="ew", pady=6)
        ttk.Label(uc, text="Length unit").grid(row=0, column=0, sticky="w")
        ttk.Combobox(uc, textvariable=self.v["unit"], values=sorted(LENGTH_UNITS), state="readonly",
                     width=5).grid(row=0, column=1, padx=(4, 18), sticky="w")
        ttk.Label(uc, text="Mass unit").grid(row=0, column=2, sticky="w")
        ttk.Combobox(uc, textvariable=self.v["mass_unit"], values=sorted(MASS_UNITS), state="readonly",
                     width=5).grid(row=0, column=3, padx=4, sticky="w")
        ttk.Label(uc, text="Combine sweeps").grid(row=1, column=0, sticky="w", pady=(6, 0))
        rf = ttk.Frame(uc)
        rf.grid(row=1, column=1, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Radiobutton(rf, text="grid (every combination)", value="grid",
                        variable=self.v["combine"]).pack(side="left", padx=(4, 12))
        ttk.Radiobutton(rf, text="zip (all step together)", value="zip",
                        variable=self.v["combine"]).pack(side="left")
        bo = ttk.LabelFrame(tab, text="Behavior", padding=8)
        bo.grid(row=2, column=0, sticky="ew")
        for i, (key, label, _d) in enumerate(BOOL_OPTIONS):
            ttk.Checkbutton(bo, text=label, variable=self.v[key]).grid(row=i, column=0, sticky="w")
        ttk.Checkbutton(bo, text="Log per-file notes (volume compensation, cleared overrides...)",
                        variable=self.verbose).grid(row=len(BOOL_OPTIONS), column=0, sticky="w")

    def _build_preview_tab(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Preview")
        self.preview_summary = tk.StringVar(value="Press Preview (F5) to plan and validate the batch without writing.")
        ttk.Label(tab, textvariable=self.preview_summary, wraplength=900, justify="left").pack(anchor="w")
        holder = ttk.Frame(tab)
        holder.pack(fill="both", expand=True, pady=6)
        self.preview_tree = ttk.Treeview(holder, show="headings")
        self.preview_tree.tag_configure("bad", foreground="#b00020")
        self.preview_tree.tag_configure("dup", foreground="#888")
        self._scrolled(holder, self.preview_tree, horizontal=True)

    def _build_log_tab(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Log")
        self.log = scrolledtext.ScrolledText(tab, font=("Consolas", 9), wrap="none")
        self.log.pack(fill="both", expand=True)

    # ------------------------------------------------------- reference files
    def browse_refs(self):
        paths = filedialog.askopenfilenames(title="Add reference .ork files", parent=self.root,
                                            filetypes=[("OpenRocket", "*.ork"), ("All files", "*.*")])
        self.add_refs(list(paths))

    def browse_ref_folder(self):
        d = filedialog.askdirectory(title="Add every .ork in a folder", parent=self.root)
        if d:
            files = [str(p) for p in sorted(Path(d).glob("*.ork"))]
            if not files:
                messagebox.showinfo("No files", "That folder has no .ork files.", parent=self.root)
            self.add_refs(files)

    def add_refs(self, paths):
        added = None
        for p in paths:
            p = str(Path(p).resolve())
            if p in self.refs:
                continue
            try:
                self._load_doc(p)
            except OrkLoadError as e:
                messagebox.showerror("Can't load file", str(e), parent=self.root)
                continue
            self.refs.append(p)
            self.ref_list.insert("end", Path(p).name)
            added = len(self.refs) - 1
        if added is not None:
            self.ref_list.selection_clear(0, "end")
            self.ref_list.selection_set(added)
            self._ref_selected()
        self.refresh_edits()

    def _load_doc(self, path):
        if path not in self.docs:
            doc = OrkDocument(path)
            self.docs[path] = (doc, ComponentIndex(doc.root))
        return self.docs[path]

    def remove_ref(self):
        sel = self.ref_list.curselection()
        if not sel:
            return
        i = sel[0]
        self.ref_list.delete(i)
        del self.refs[i]
        if self.refs:
            self.ref_list.selection_set(min(i, len(self.refs) - 1))
        self._ref_selected()
        self.refresh_edits()

    def clear_refs(self):
        self.refs.clear()
        self.ref_list.delete(0, "end")
        self._ref_selected()
        self.refresh_edits()

    def _ref_selected(self):
        sel = self.ref_list.curselection()
        self.comp_tree.delete(*self.comp_tree.get_children())
        self.param_tree.delete(*self.param_tree.get_children())
        self.comp_items = {}
        if not sel:
            self.current = None
            return
        path = self.refs[sel[0]]
        doc, index = self._load_doc(path)
        self.current = (path, doc, index)
        iids = {}
        for i, c in enumerate(index.components):
            owner = index.owner(c)
            parent = iids.get(id(owner), "")
            iid = f"c{i}"
            self.comp_tree.insert(parent, "end", iid=iid, text=comp_name(c), open=True,
                                  values=(c.tag, best_selector(index, c)))
            iids[id(c)] = iid
            self.comp_items[iid] = c
        self.status.set(f"{Path(path).name}: {len(index.components)} components, "
                        f"{doc.simulation_count()} simulation(s).")

    def _comp_selected(self):
        self.param_tree.delete(*self.param_tree.get_children())
        el = self._selected_comp()
        if el is None:
            return
        units = self.units()
        for name, acc in list_params(el):
            v = acc.read()
            if v is None:
                shown, unit = ("auto" if acc.is_auto() else ""), ""
            elif isinstance(v, str):
                shown, unit = v, ""
            else:
                shown, unit = fmt_value(units.from_si(acc.kind, v)), units.label(acc.kind)
            note = ", ".join(n for n, on in (("auto", acc.is_auto()), ("virtual", acc.virtual)) if on)
            self.param_tree.insert("", "end", iid=name, values=(name, shown, unit, note))
        if el.tag == "nosecone":
            self.param_tree.insert("", "end", iid="trim", values=("trim", "", units.label("length"),
                                                                  "virtual: length -= value"))

    def _selected_comp(self):
        sel = self.comp_tree.selection()
        return self.comp_items.get(sel[0]) if sel else None

    def _comp_menu(self, event):
        iid = self.comp_tree.identify_row(event.y)
        if not iid:
            return
        self.comp_tree.selection_set(iid)
        el = self.comp_items[iid]
        index = self.current[2]
        sel = best_selector(index, el)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Sweep a parameter of this component...",
                         command=lambda: self.add_entry({"target": f"{sel}."}))
        same = [c for c in index.components if c.tag == el.tag]
        if len(same) > 1:
            menu.add_command(label=f"Sweep a parameter of ALL {el.tag}s...",
                             command=lambda: self.add_entry({"target": f"{el.tag}[*]."}))
        menu.add_separator()
        menu.add_command(label="Copy selector", command=lambda: self._copy(sel))
        menu.tk_popup(event.x_root, event.y_root)

    def _param_menu(self, event):
        iid = self.param_tree.identify_row(event.y)
        if not iid:
            return
        self.param_tree.selection_set(iid)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Sweep this parameter...", command=self._param_action)
        menu.add_command(label="Copy target", command=lambda: self._copy(self._param_target()))
        menu.tk_popup(event.x_root, event.y_root)

    def _param_target(self):
        el = self._selected_comp()
        sel = self.param_tree.selection()
        if el is None or not sel:
            return None
        return f"{best_selector(self.current[2], el)}.{sel[0]}"

    def _param_action(self):
        target = self._param_target()
        if target:
            self.add_entry({"target": target})

    def _copy(self, text):
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status.set(f"Copied: {text}")

    # ------------------------------------------------ suggestions for dialogs
    def selector_suggestions(self):
        """One unambiguous selector per component, tip to tail (OpenRocket's component-tree order),
        followed by 'all of this type' selectors for types that occur more than once."""
        if not self.current:
            return []
        index = self.current[2]
        out = [best_selector(index, c) for c in index.components]
        tags = []
        for c in index.components:
            if c.tag not in tags and sum(1 for o in index.components if o.tag == c.tag) > 1:
                tags.append(c.tag)
        return out + [f"{t}[*]" for t in tags]

    def param_suggestions(self, selector):
        if not self.current or not selector:
            return []
        try:
            els = self.current[2].resolve(selector)
        except SelectorError:
            return []
        names = [n for n, _ in list_params(els[0])]
        if els[0].tag == "nosecone":
            names.append("trim")
        return names

    def describe_target(self, selector, param):
        if not selector:
            return ""
        if not self.current:
            return "(load a reference file to check this target)"
        name = Path(self.current[0]).name
        try:
            els = self.current[2].resolve(selector)
        except SelectorError as e:
            return f"{name}: {e}"
        if not param:
            return f"{name}: matches " + ", ".join(comp_name(e) for e in els[:4]) + (" ..." if len(els) > 4 else "")
        p = "length" if param.lower() == "trim" else param
        from orkbatch.params import canonical_param
        try:
            accs = [resolve_param(e, canonical_param(p)) for e in els]
        except ParamError as e:
            return f"{name}: {e}"
        units = self.units()
        vals = []
        for e, a in zip(els, accs):
            v = a.read()
            shown = "auto" if v is None else v if isinstance(v, str) else \
                f"{fmt_value(units.from_si(a.kind, v))} {units.label(a.kind)}".strip()
            vals.append(f"{comp_name(e)} = {shown}")
        more = f" (+{len(vals) - 3} more)" if len(vals) > 3 else ""
        return f"Reference value in {name}: " + "; ".join(vals[:3]) + more

    # ---------------------------------------------------------- edits list
    def add_entry(self, initial=None):
        d = EntryDialog(self, dict(initial or {}), "Add sweep")
        self.root.wait_window(d)
        if d.result:
            self.entries.append(d.result)
            self.refresh_edits(select=len(self.entries) - 1)
            self.nb.select(0)

    def _selected_entry(self):
        sel = self.edits_tree.selection()
        return int(sel[0]) if sel else None

    def edit_entry(self):
        i = self._selected_entry()
        if i is None:
            return
        d = EntryDialog(self, self.entries[i], "Edit")
        self.root.wait_window(d)
        if d.result:
            self.entries[i] = d.result
            self.refresh_edits(select=i)

    def duplicate_entry(self):
        i = self._selected_entry()
        if i is not None:
            self.entries.insert(i + 1, dict(self.entries[i], label=""))
            self.refresh_edits(select=i + 1)

    def remove_entry(self):
        i = self._selected_entry()
        if i is not None:
            del self.entries[i]
            self.refresh_edits(select=min(i, len(self.entries) - 1))

    def move_entry(self, d):
        i = self._selected_entry()
        if i is None or not (0 <= i + d < len(self.entries)):
            return
        self.entries[i], self.entries[i + d] = self.entries[i + d], self.entries[i]
        self.refresh_edits(select=i + d)

    def refresh_edits(self, select=None):
        tree = self.edits_tree
        tree.delete(*tree.get_children())
        units = self.units()
        varies, bad = [], 0
        for i, e in enumerate(self.entries):
            try:
                p = parse_entry(entry_to_text(e), units)
                n = len(p.values)
                status = (f"{n} values" if n > 1 else "1 value") + (f" ({p.unit})" if p.unit else "")
                varies.append(p)
                tags = ()
            except (EntryError, ValueError) as ex:
                status, tags = f"error: {ex}", ("bad",)
                bad += 1
            tree.insert("", "end", iid=str(i), tags=tags,
                        values=(e["target"], e["op"], e["spec"],
                                e.get("label", ""), e.get("link", ""), status))
        if select is not None and 0 <= select < len(self.entries):
            tree.selection_set(str(select))
            tree.see(str(select))
        try:
            dedupe_labels(varies)
            combos = len(expand(varies, self.v["combine"].get())) if varies else 0
            nref = max(len(self.refs), 1)
            text = (f"{combos} combination(s) x {len(self.refs)} reference file(s) = {combos * nref} "
                    f"variant(s)" if varies else "No sweeps yet.")
            if bad:
                text += f"   |   {bad} entry(ies) have errors"
            self.estimate.set(text)
            labels = [p.label for p in varies]
            self.template_help.config(text="Fields: {prefix} {base} {i} " + " ".join("{%s}" % lab for lab in labels)
                                      + "   e.g. {base}_{trim}Trim   (blank = automatic names)")
        except EntryError as ex:
            self.estimate.set(f"Problem: {ex}")

    def _units_changed(self):
        self.refresh_edits()
        self._comp_selected()

    # -------------------------------------------------------------- settings
    def gather(self):
        """GUI state -> config dict (same schema as ork_batch.py config files)."""
        s = {
            "base": list(self.refs),
            "out_dir": str(Path(self.v["out_dir"].get().strip() or DEFAULT_OUT).resolve()),
            "prefix": self.v["prefix"].get().strip() or None,
            "name_template": self.v["name_template"].get().strip() or None,
            "unit": self.v["unit"].get(), "mass_unit": self.v["mass_unit"].get(),
            "combine": self.v["combine"].get(),
            "vary": [entry_to_text(e) for e in self.entries],
        }
        for key, _l, _d in BOOL_OPTIONS:
            s[key] = bool(self.v[key].get())
        return s

    def apply_settings(self, cfg):
        self.clear_refs()
        self.entries = []
        errors = []
        for obj in cfg.get("vary", []) or []:
            try:
                self.entries.append(obj_to_entry(obj))
            except (EntryError, KeyError) as e:
                errors.append(str(e))
        for key, var in self.v.items():
            if isinstance(var, tk.BooleanVar):
                default = next(d for k, _l, d in BOOL_OPTIONS if k == key)
                var.set(bool(cfg.get(key, default)))
        self.v["out_dir"].set(cfg.get("out_dir") or DEFAULT_OUT)
        for key in ("prefix", "name_template"):
            self.v[key].set(cfg.get(key) or "")
        self.v["unit"].set(cfg.get("unit", "in"))
        self.v["mass_unit"].set(cfg.get("mass_unit", "kg"))
        self.v["combine"].set(cfg.get("combine", "grid"))
        missing = [b for b in cfg.get("base", []) if not Path(b).exists()]
        self.add_refs([b for b in cfg.get("base", []) if Path(b).exists()])
        self.refresh_edits()
        if missing:
            errors.append("Reference file(s) not found:\n  " + "\n  ".join(missing))
        if errors:
            messagebox.showwarning("Loaded with problems", "\n".join(errors), parent=self.root)

    def new_study(self):
        if (self.entries or self.refs) and not messagebox.askyesno("New study", "Discard the current setup?",
                                                                   parent=self.root):
            return
        self.config_path = None
        self.apply_settings({})
        self._set_title()

    def open_config(self, path=None):
        path = path or filedialog.askopenfilename(title="Open config", parent=self.root,
                                                  filetypes=[("Config", "*.json"), ("All files", "*.*")],
                                                  initialdir=str(HERE / "examples"))
        if not path:
            return
        try:
            cfg = capture_die(cli.load_config, path)
        except RuntimeError as e:
            messagebox.showerror("Can't open config", str(e), parent=self.root)
            return
        self.apply_settings(cfg)
        self.config_path = str(path)
        self._set_title()
        self.status.set(f"Loaded {path}")

    def save_config(self, ask=False):
        s = self.gather()
        if s is None:
            return
        path = self.config_path
        if ask or not path or Path(path).name == "batch_config.json":
            path = filedialog.asksaveasfilename(title="Save config", parent=self.root, defaultextension=".json",
                                                filetypes=[("Config", "*.json")], initialdir=str(HERE))
        if not path:
            return
        s = {"_comment": f"Saved by ork_batch_gui.py {__version__}. Run: python ork_batch.py generate -c <this file>",
             **{k: v for k, v in s.items() if v not in (None, [], "")}}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        self.config_path = path
        self._set_title()
        self.status.set(f"Saved {path}")

    def show_command(self):
        s = self.gather()
        if s is None:
            return

        def q(x):
            return f'"{x}"' if re.search(r'[\s@<>|&*^"]', x) or not x else x

        parts = ["python", q(str(HERE / "ork_batch.py")), "generate"]
        if s["base"]:
            parts += ["-b"] + [q(b) for b in s["base"]]
        parts += sum((["-v", q(x)] for x in s["vary"]), [])
        parts += ["-o", q(s["out_dir"]), "--unit", s["unit"], "--mass-unit", s["mass_unit"]]
        for key, flag in (("prefix", "--prefix"), ("name_template", "--name-template")):
            if s.get(key) is not None:
                parts += [flag, q(str(s[key]))]
        if s["combine"] != "grid":
            parts += ["--combine", s["combine"]]
        for key, _l, default in BOOL_OPTIONS:
            if s[key] != default:
                parts.append(("--" if s[key] else "--no-") + key.replace("_", "-"))
        cmd = " ".join(parts)
        win = tk.Toplevel(self.root)
        win.title("Equivalent command line")
        t = tk.Text(win, height=8, width=110, wrap="word", font=("Consolas", 9))
        t.pack(fill="both", expand=True, padx=8, pady=8)
        t.insert("1.0", cmd)
        ttk.Button(win, text="Copy", command=lambda: self._copy(cmd)).pack(pady=(0, 8))

    # ----------------------------------------------------------- preview
    def _preflight(self):
        if self.busy:
            return None
        if not self.refs:
            messagebox.showerror("No reference file", "Add at least one reference .ork file.", parent=self.root)
            return None
        bad = []
        for e in self.entries:
            try:
                parse_entry(entry_to_text(e), self.units())
            except (EntryError, ValueError):
                bad.append(e)
        if bad:
            messagebox.showerror("Fix the sweeps first", f"{len(bad)} sweep(s) have errors (shown in red).",
                                 parent=self.root)
            return None
        s = self.gather()
        if s is None:
            return None
        if not s["vary"]:
            messagebox.showerror("Nothing to sweep", "Add at least one sweep.", parent=self.root)
            return None
        return s

    def preview(self):
        s = self._preflight()
        if s is None:
            return
        self.set_busy(True, cancellable=False)
        self.status.set("Planning and validating...")
        threading.Thread(target=self._preview_worker, args=(s,), daemon=True).start()

    def _preview_worker(self, s):
        try:
            settings = dict(cli.DEFAULTS)
            settings.update({k: v for k, v in s.items() if v is not None})
            units = Units(settings["unit"], settings["mass_unit"])
            varies = capture_die(cli.parse_entries, settings, units)
            opts = cli.make_options(settings)
            bases = capture_die(cli.load_bases, capture_die(cli.expand_bases, settings["base"]), opts.keep_sims)
            for b in bases:
                check_entries(b, varies, opts)
            variants = plan(bases, varies, opts)
            seen, rows = {}, []
            for v in variants:
                try:
                    build_variant(v, varies, opts, units)
                except (SelectorError, ParamError) as e:
                    v.problems.append(str(e))
                status = "ok"
                if v.problems:
                    status = "invalid (would be written)" if opts.allow_invalid else "skip: invalid"
                if not v.problems or opts.allow_invalid:
                    key = (str(v.base.path), hashlib.sha1(v.base.doc.serialize(v.root)).hexdigest())
                    if settings.get("dedupe", True) and key in seen:
                        status = f"skip: same as {seen[key]}"
                    else:
                        seen[key] = v.name
                labels = [fmt_value(v.labels.get(e.label)) for e in varies]
                rows.append((v.name + ".ork", v.base.path.name, labels, status,
                             "; ".join(v.problems), "; ".join(v.notes)))
                v.root = None
            heads = [e.label + (f" ({e.unit})" if e.unit else "") for e in varies]
            self.q.put(("preview", heads, rows, settings["out_dir"]))
        except (RuntimeError, BatchError, EntryError, OrkLoadError) as e:
            self.q.put(("preview_error", str(e)))
        except Exception:
            self.q.put(("preview_error", traceback.format_exc()))

    def _show_preview(self, heads, rows, out_dir):
        tree = self.preview_tree
        tree.delete(*tree.get_children())
        cols = ["#", "file", "reference"] + heads + ["status", "problems", "notes"]
        tree.config(columns=[f"c{i}" for i in range(len(cols))])
        for i, c in enumerate(cols):
            tree.heading(f"c{i}", text=c)
            w = {"#": 50, "file": 340, "reference": 120, "status": 150, "problems": 260, "notes": 300}.get(c, 90)
            tree.column(f"c{i}", width=w, stretch=False, anchor="w")
        for n, (name, ref, labels, status, probs, notes) in enumerate(rows[:PREVIEW_ROW_LIMIT], 1):
            tag = ("bad",) if status.startswith("skip: invalid") or probs else ("dup",) if status.startswith("skip") \
                else ()
            tree.insert("", "end", values=[n, name, ref] + labels + [status, probs, notes], tags=tag)
        ok = sum(1 for r in rows if not r[3].startswith("skip"))
        invalid = sum(1 for r in rows if r[3] == "skip: invalid")
        dup = sum(1 for r in rows if r[3].startswith("skip: same"))
        existing = sum(1 for r in rows if (Path(out_dir) / r[0]).exists())
        text = (f"{len(rows)} planned variant(s): {ok} would be written, {invalid} skipped as invalid, "
                f"{dup} skipped as duplicates.   Output: {out_dir}")
        if existing:
            text += f"\n{existing} of these files already exist in the output folder."
        if len(rows) > PREVIEW_ROW_LIMIT:
            text += f"\nShowing the first {PREVIEW_ROW_LIMIT} rows."
        self.preview_summary.set(text)
        self.nb.select(2)
        self.status.set(f"Preview: {ok} file(s) would be written.")

    # ---------------------------------------------------------- generation
    def generate(self):
        s = self._preflight()
        if s is None:
            return
        fd, cfg = tempfile.mkstemp(prefix="ork_batch_gui_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in s.items() if v is not None}, f, indent=2)
        self.last_out_dir = s["out_dir"]
        self.log.insert("end", f"\n=== Generate -> {s['out_dir']}\n")
        self.nb.select(3)
        argv = ["generate", "-c", cfg] + (["--verbose"] if self.verbose.get() else [])

        def done(code):
            try:
                os.remove(cfg)
            except OSError:
                pass
            if code == 0:
                summary = next((ln for ln in reversed(self.run_lines) if ln.startswith("Done")), "Done.")
                if any(ln.startswith("Cancelled") for ln in self.run_lines):
                    summary = "Cancelled. " + summary
                self.status.set(summary.strip())
                if messagebox.askyesno("Batch finished", f"{summary.strip()}\n\nOpen the output folder?",
                                       parent=self.root):
                    self.open_out_dir()
            else:
                self._show_run_error("Generation failed")

        self.run_cli(argv, done)

    def run_cli(self, argv, on_done):
        self.set_busy(True)
        self.cancel_event.clear()
        cli.cancel_event = self.cancel_event
        self.run_lines = []
        self.progress.config(value=0, maximum=1)

        def work():
            w = QueueWriter(self.q)
            code = 0
            try:
                with contextlib.redirect_stdout(w), contextlib.redirect_stderr(w):
                    cli.main(argv)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
            except Exception:
                w.write(traceback.format_exc())
                code = 1
            self.q.put(("done", code, on_done))

        threading.Thread(target=work, daemon=True).start()

    def _show_run_error(self, title):
        errs = [ln for ln in self.run_lines if ln.startswith("error:") or ln.startswith("  - ")]
        text = "\n".join(errs) if errs else "See the Log tab for details."
        self.status.set(title)
        messagebox.showerror(title, text.replace("error: ", "", 1)[:3000], parent=self.root)

    def cancel(self):
        self.cancel_event.set()
        self.status.set("Cancelling after the current file...")

    def gui_confirm(self, question, assume_yes=False):
        """Replacement for cli.confirm: ask with a dialog on the Tk thread."""
        if assume_yes:
            return True
        if threading.current_thread() is threading.main_thread():
            return messagebox.askyesno("Confirm", question, parent=self.root)
        box = {"event": threading.Event(), "question": question, "answer": False}
        self.q.put(("confirm", box))  # answered by _poll on the Tk thread
        box["event"].wait()
        return box["answer"]

    def set_busy(self, busy, cancellable=True):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.preview_btn.config(state=state)
        self.gen_btn.config(state=state)
        self.cancel_btn.config(state="normal" if busy and cancellable else "disabled")
        self.root.config(cursor="watch" if busy else "")

    # ---------------------------------------------------------------- poll
    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "text":
                    self._log_text(item[1])
                elif kind == "done":
                    self.set_busy(False)
                    if item[2]:
                        item[2](item[1])
                elif kind == "confirm":
                    box = item[1]
                    box["answer"] = messagebox.askyesno("Confirm", box["question"], parent=self.root)
                    box["event"].set()
                elif kind == "preview":
                    self.set_busy(False)
                    self._show_preview(*item[1:])
                elif kind == "preview_error":
                    self.set_busy(False)
                    self.status.set("Preview failed.")
                    messagebox.showerror("Preview failed", item[1][:3000], parent=self.root)
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def _log_text(self, s):
        self.log.insert("end", s)
        self.log.see("end")
        self._partial += s
        *lines, self._partial = self._partial.split("\n")
        for ln in lines:
            self.run_lines.append(ln)
            m = re.match(r"^\[\s*(\d+)/(\d+)\]", ln)
            if m:
                self.progress.config(maximum=int(m.group(2)), value=int(m.group(1)))
                self.status.set(ln[:120])

    # --------------------------------------------------------------- misc
    def browse_out(self):
        d = filedialog.askdirectory(title="Output folder", parent=self.root,
                                    initialdir=self.v["out_dir"].get() or str(HERE))
        if d:
            self.v["out_dir"].set(d)

    def open_out_dir(self):
        d = Path(self.last_out_dir or self.v["out_dir"].get() or DEFAULT_OUT)
        if not d.exists():
            messagebox.showinfo("Not there yet", f"{d} doesn't exist yet.", parent=self.root)
            return
        self._startfile(d)

    def _startfile(self, path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
        except OSError as e:
            messagebox.showerror("Can't open", str(e), parent=self.root)

    def cheat_sheet(self):
        win = tk.Toplevel(self.root)
        win.title("Syntax cheat sheet")
        t = tk.Text(win, width=84, height=30, font=("Consolas", 10))
        t.pack(fill="both", expand=True, padx=8, pady=8)
        t.insert("1.0", CHEAT_SHEET)
        t.config(state="disabled")

    def diff_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Compare two .ork files")
        win.transient(self.root)
        a = tk.StringVar(value=self.current[0] if self.current else "")
        b = tk.StringVar()
        f = ttk.Frame(win, padding=8)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        for r, (txt, var) in enumerate((("A (reference)", a), ("B (variant)", b))):
            ttk.Label(f, text=txt).grid(row=r, column=0, sticky="w")
            ttk.Entry(f, textvariable=var, width=80).grid(row=r, column=1, sticky="ew", padx=4, pady=2)
            ttk.Button(f, text="Browse...", command=lambda v=var: v.set(filedialog.askopenfilename(
                parent=win, filetypes=[("OpenRocket", "*.ork")], initialdir=self.last_out_dir or str(HERE))
                or v.get())).grid(row=r, column=2)
        out = scrolledtext.ScrolledText(f, height=24, width=100, font=("Consolas", 9))
        out.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        f.rowconfigure(3, weight=1)

        def run():
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    cli.main(["diff", a.get(), b.get(), "--unit", self.v["unit"].get(),
                              "--mass-unit", self.v["mass_unit"].get()])
            except SystemExit:
                pass
            out.delete("1.0", "end")
            out.insert("1.0", buf.getvalue())

        ttk.Button(f, text="Compare", command=run).grid(row=2, column=1, sticky="w", pady=4)

    def on_close(self):
        if self.busy and not messagebox.askyesno("Quit", "A batch is still running. Quit anyway?",
                                                 parent=self.root):
            return
        self.cancel_event.set()
        self.root.destroy()


def main():
    try:  # crisp text on high-DPI Windows displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{min(1500, sw - 60)}x{min(950, sh - 100)}+20+20")
    root.minsize(900, 560)
    try:
        import sv_ttk  # optional theme, used if installed
        sv_ttk.set_theme("light")
    except Exception:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    App(root, sys.argv[1:])
    root.mainloop()


if __name__ == "__main__":
    main()
