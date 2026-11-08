#!/usr/bin/env python3
r"""
3D VOX Studio  -  the desktop window for the whole toolchain.

    START.bat                double-click to open it
    drag files onto START.bat   opens with those files already selected

Four roles, picked from the left-hand menu (or Role in the menu bar):

    Quick Start   pick models, type a gap, press Generate. Nothing else.
    Engineer      every placement / material / engine option, presets,
                  and the equivalent command line
    Analyst       inspect, render, rotate in 3D, export, unpack, compare
    Admin         setup check, compile Fortran, engine self-test, run
                  manager, disk clean-up, cache

Everything here calls the same code as the three .bat files, so a run made
from the window and one made from the console are identical.
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.basename(HERE).lower() == "engine" else HERE
sys.path.insert(0, HERE)

APP_NAME = "3D VOX Studio"
VERSION = "6.0"

ROLES = [
    ("quick",    "Quick Start", "Pick inputs, press Generate"),
    ("engineer", "Engineer",    "All options, presets, engines"),
    ("analyst",  "Analyst",     "Inspect, 3D view, export, compare"),
    ("admin",    "Admin",       "Setup, Fortran, runs, disk"),
]

# ----------------------------------------------------------------------
# colours / fonts
# ----------------------------------------------------------------------
C = {
    "bg": "#f4f5f7", "panel": "#ffffff", "side": "#1d2230", "side_fg": "#c9cfdb",
    "side_sel": "#2f6fed", "side_hover": "#2a3142", "text": "#1d2230",
    "muted": "#6b7385", "accent": "#2f6fed", "accent_dk": "#2257c5",
    "ok": "#1a8a4a", "bad": "#c2382f", "warn": "#b7791f", "log_bg": "#12151b",
    "log_fg": "#d7dce5", "border": "#dfe2e8",
}


def open_path(path):
    """Open a file or folder with whatever the OS uses."""
    try:
        if os.name == "nt":
            os.startfile(path)                                     # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception as exc:                                       # noqa: BLE001
        messagebox.showerror(APP_NAME, f"Could not open\n{path}\n\n{exc}")


def size_of(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


# ----------------------------------------------------------------------
# dependency check that works before numpy is importable
# ----------------------------------------------------------------------
PACKAGES = [
    ("numpy", True, "everything"),
    ("trimesh", False, "voxelizing 3D models (.glb/.stl/.obj)"),
    ("scipy", False, "filling voxelized models"),
    ("matplotlib", False, "preview pictures and the 3D viewer"),
    ("pandas", False, "optional: faster reading on old numpy"),
]


def package_versions():
    import importlib
    out = []
    for name, required, why in PACKAGES:
        try:
            mod = importlib.import_module(name)
            out.append((name, True, getattr(mod, "__version__", "?"), required, why))
        except Exception:                                          # noqa: BLE001
            out.append((name, False, "", required, why))
    return out


# ----------------------------------------------------------------------
# small widgets
# ----------------------------------------------------------------------
class Tooltip:
    """Hover help. Keeps the screens uncluttered but self-explaining."""

    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self.tip = self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None

    def _show(self):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, justify="left", bg="#fffbe6", fg="#333",
                 relief="solid", bd=1, wraplength=360, padx=8, pady=5,
                 font=("Segoe UI", 9)).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


def tip(widget, text):
    Tooltip(widget, text)
    return widget


class QueueWriter:
    """stdout replacement: lines go to the GUI log through a queue."""

    def __init__(self, q, tag="out"):
        self.q, self.tag = q, tag

    def write(self, s):
        if s:
            self.q.put(("log", s, self.tag))

    def flush(self):
        pass

    def isatty(self):
        return False


def section(parent, title, subtitle=None):
    """A white card with a heading."""
    card = tk.Frame(parent, bg=C["panel"], highlightbackground=C["border"],
                    highlightthickness=1)
    card.pack(fill="x", padx=16, pady=(0, 12))
    head = tk.Frame(card, bg=C["panel"])
    head.pack(fill="x", padx=14, pady=(10, 2))
    tk.Label(head, text=title, bg=C["panel"], fg=C["text"],
             font=("Segoe UI Semibold", 11)).pack(side="left")
    if subtitle:
        tk.Label(head, text=subtitle, bg=C["panel"], fg=C["muted"],
                 font=("Segoe UI", 9)).pack(side="left", padx=(10, 0))
    body = tk.Frame(card, bg=C["panel"])
    body.pack(fill="both", expand=True, padx=14, pady=(4, 12))
    return body


class ScrollPage(tk.Frame):
    """A page whose content scrolls vertically when the window is small."""

    def __init__(self, parent):
        super().__init__(parent, bg=C["bg"])
        self.canvas = tk.Canvas(self, bg=C["bg"], highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=C["bg"])
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._win, width=e.width))
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", self._bind_wheel)
            w.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))

    def _unbind_wheel(self, _e):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _wheel(self, e):
        if isinstance(e.widget, (tk.Listbox, tk.Text, ttk.Treeview)):
            return
        self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")


# ----------------------------------------------------------------------
# the input picker used by Quick Start and Engineer (they share one list)
# ----------------------------------------------------------------------
class InputSelector(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=C["panel"])
        self.app = app

        left = tk.Frame(self, bg=C["panel"])
        mid = tk.Frame(self, bg=C["panel"])
        right = tk.Frame(self, bg=C["panel"])
        left.pack(side="left", fill="both", expand=True)
        mid.pack(side="left", fill="y", padx=8)
        right.pack(side="left", fill="both", expand=True)

        tk.Label(left, text="Available  (input\\models, input\\ic)", bg=C["panel"],
                 fg=C["muted"], font=("Segoe UI", 9)).pack(anchor="w")
        self.avail = tk.Listbox(left, selectmode="extended", height=9,
                                activestyle="none", exportselection=False,
                                font=("Consolas", 9), relief="solid", bd=1,
                                highlightthickness=0, selectbackground=C["accent"])
        self.avail.pack(fill="both", expand=True)
        self.avail.bind("<Double-Button-1>", lambda e: self.add())

        for txt, cmd, hint in (
                ("Add  >", self.add, "Add the highlighted inputs (or double-click)"),
                ("<  Remove", self.remove, "Take the highlighted inputs out"),
                ("Move up", lambda: self.move(-1), "The FIRST input is the ground"),
                ("Move down", lambda: self.move(1), ""),
                ("Clear", self.clear, "Empty the selection")):
            tip(ttk.Button(mid, text=txt, command=cmd, width=11), hint).pack(pady=2)
        ttk.Separator(mid).pack(fill="x", pady=6)
        tip(ttk.Button(mid, text="Browse...", command=self.browse, width=11),
            "Pick a model or .dat from anywhere on disk").pack(pady=2)
        tip(ttk.Button(mid, text="Refresh", command=app.refresh_inputs, width=11),
            "Re-read input\\models and input\\ic  (F5)").pack(pady=2)

        tk.Label(right, text="Selected  -  #1 is the ground", bg=C["panel"],
                 fg=C["muted"], font=("Segoe UI", 9)).pack(anchor="w")
        self.sel = tk.Listbox(right, selectmode="extended", height=9,
                              activestyle="none", exportselection=False,
                              font=("Consolas", 9), relief="solid", bd=1,
                              highlightthickness=0, selectbackground=C["accent"])
        self.sel.pack(fill="both", expand=True)
        self.sel.bind("<Double-Button-1>", lambda e: self.remove())
        self.sel.bind("<Delete>", lambda e: self.remove())
        app.selectors.append(self)

    # -- actions on the shared list -------------------------------------
    def add(self):
        for i in self.avail.curselection():
            p = self.app.available[i]
            if p not in self.app.selected:
                self.app.selected.append(p)
        self.app.selection_changed()

    def remove(self):
        idx = set(self.sel.curselection())
        self.app.selected[:] = [p for i, p in enumerate(self.app.selected)
                                if i not in idx]
        self.app.selection_changed()

    def move(self, d):
        cur = list(self.sel.curselection())
        if len(cur) != 1:
            return
        i, j = cur[0], cur[0] + d
        s = self.app.selected
        if 0 <= j < len(s):
            s[i], s[j] = s[j], s[i]
            self.app.selection_changed()
            self.sel.selection_set(j)

    def clear(self):
        self.app.selected.clear()
        self.app.selection_changed()

    def browse(self):
        paths = filedialog.askopenfilenames(
            title="Pick 3D models or point clouds",
            filetypes=[("Models and clouds",
                        "*.glb *.gltf *.stl *.obj *.ply *.off *.dae *.3mf *.dat *.txt *.csv *.xyz"),
                       ("All files", "*.*")])
        for p in paths:
            p = os.path.abspath(p)
            if p not in self.app.extra_inputs:
                self.app.extra_inputs.append(p)
            if p not in self.app.selected:
                self.app.selected.append(p)
        self.app.refresh_inputs()

    # -- redraw -----------------------------------------------------------
    def redraw(self):
        self.avail.delete(0, "end")
        for p in self.app.available:
            self.avail.insert("end", self.app.describe_input(p))
        self.sel.delete(0, "end")
        for n, p in enumerate(self.app.selected, 1):
            role = "ground" if n == 1 and len(self.app.selected) > 1 else ""
            self.sel.insert("end", f"{n:>2}. {os.path.basename(p)[:34]:<34} {role}")


# ----------------------------------------------------------------------
# the application
# ----------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self, preselect=()):
        super().__init__()
        import numpy                                               # noqa: F401
        import fastio
        import run_pipeline as rp
        import view_particles as vp
        import ic_to_points as itp
        self.fastio, self.rp, self.vp, self.itp = fastio, rp, vp, itp

        self.cfg = rp.read_fortran_config()
        self.settings = rp.load_settings()
        self.ui = self.settings.setdefault("ui", {})

        self.title(f"{APP_NAME}  {VERSION}")
        self.configure(bg=C["bg"])
        self.geometry(self.ui.get("geometry", "1400x880"))
        self.minsize(1100, 680)

        self.q = queue.Queue()
        self.busy = False
        self.selectors = []
        self.available, self.selected, self.extra_inputs = [], [], []
        self.preview_img = None
        self.preview_path = None
        self.last_run = None

        self._style()
        self._menu()
        self._layout()

        for p in preselect:
            p = os.path.abspath(p)
            if os.path.isfile(p):
                self.extra_inputs.append(p)
                self.selected.append(p)
        self.refresh_inputs()

        rp.CHOOSER = self.choose_from_worker
        self.show_role(self.ui.get("role", "quick"))
        self.after(60, self._poll)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind_all("<F5>", lambda e: self.refresh_all())
        self.bind_all("<Control-Return>", lambda e: self.generate_current())
        self.log(f"{APP_NAME} {VERSION} ready.  Root folder: {ROOT}\n", "muted")
        self.log(f"Generator config from {os.path.basename(self.cfg['source'])}: "
                 f"spacing {self.cfg['spacing']} m, density {self.cfg['density']} "
                 f"kg/m^3, {self.cfg['read_columns']} columns.\n", "muted")
        last_runs = self.vp.list_runs()
        if last_runs:
            prev = os.path.join(last_runs[0], "preview.png")
            if os.path.exists(prev):
                self.show_preview(prev, f"Latest run: {os.path.basename(last_runs[0])}")

    def report_callback_exception(self, exc, val, tb):
        """A button handler crashed: show it instead of dying silently."""
        err = "".join(traceback.format_exception(exc, val, tb))
        try:
            self.log(err, "err")
        except Exception:                                          # noqa: BLE001
            pass
        messagebox.showerror(APP_NAME, err[-1500:])

    # ------------------------------------------------------------------
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        base = ("Segoe UI", 9)
        st.configure(".", font=base, background=C["panel"])
        st.configure("TButton", padding=(8, 4), background="#eef0f4",
                     bordercolor=C["border"])
        st.map("TButton", background=[("active", "#e2e6ee")])
        st.configure("Accent.TButton", font=("Segoe UI Semibold", 11),
                     padding=(18, 9), background=C["accent"], foreground="white",
                     bordercolor=C["accent"])
        st.map("Accent.TButton",
               background=[("disabled", "#9db7ef"), ("active", C["accent_dk"])],
               foreground=[("disabled", "#eef")])
        st.configure("TLabel", background=C["panel"], foreground=C["text"])
        st.configure("TCheckbutton", background=C["panel"])
        st.configure("TRadiobutton", background=C["panel"])
        st.configure("TLabelframe", background=C["panel"])
        st.configure("Treeview", rowheight=22, font=("Segoe UI", 9))
        st.configure("Treeview.Heading", font=("Segoe UI Semibold", 9))
        st.configure("Horizontal.TProgressbar", thickness=6,
                     background=C["accent"], troughcolor="#e6e9ef")

    def _menu(self):
        mb = tk.Menu(self)
        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Open input\\models folder",
                      command=lambda: self.open_folder("input", "models"))
        m.add_command(label="Open input\\ic folder",
                      command=lambda: self.open_folder("input", "ic"))
        m.add_command(label="Open output folder",
                      command=lambda: self.open_folder("output"))
        m.add_separator()
        m.add_command(label="Refresh everything", accelerator="F5",
                      command=self.refresh_all)
        m.add_separator()
        m.add_command(label="Exit", command=self.on_close)
        mb.add_cascade(label="File", menu=m)

        self.role_var = tk.StringVar(value="quick")
        m = tk.Menu(mb, tearoff=0)
        for key, label, desc in ROLES:
            m.add_radiobutton(label=f"{label}   -   {desc}", value=key,
                              variable=self.role_var,
                              command=lambda k=key: self.show_role(k))
        mb.add_cascade(label="Role", menu=m)

        m = tk.Menu(mb, tearoff=0)
        m.add_command(label="Read me", command=lambda: open_path(
            os.path.join(ROOT, "READ ME.txt")))
        m.add_command(label="Quick guide", command=self.quick_guide)
        m.add_separator()
        m.add_command(label="About", command=lambda: messagebox.showinfo(
            APP_NAME, f"{APP_NAME} {VERSION}\n\n3D models and point clouds  ->  "
                      f"GADGET-2 IC files.\n\nFolder: {ROOT}"))
        mb.add_cascade(label="Help", menu=m)
        self.config(menu=mb)

    def _layout(self):
        # --- sidebar: the role menu ------------------------------------
        side = tk.Frame(self, bg=C["side"], width=210)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="3D VOX", bg=C["side"], fg="white",
                 font=("Segoe UI Semibold", 17)).pack(anchor="w", padx=18, pady=(18, 0))
        tk.Label(side, text="models  ->  GADGET-2 IC", bg=C["side"],
                 fg=C["side_fg"], font=("Segoe UI", 9)).pack(anchor="w", padx=18)
        tk.Label(side, text="ROLE", bg=C["side"], fg="#7d8699",
                 font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=18, pady=(22, 4))
        self.role_buttons = {}
        for key, label, desc in ROLES:
            f = tk.Frame(side, bg=C["side"], cursor="hand2")
            f.pack(fill="x", padx=8, pady=2)
            a = tk.Label(f, text=label, bg=C["side"], fg="white", anchor="w",
                         font=("Segoe UI Semibold", 11), cursor="hand2")
            b = tk.Label(f, text=desc, bg=C["side"], fg=C["side_fg"], anchor="w",
                         font=("Segoe UI", 8), cursor="hand2")
            a.pack(fill="x", padx=10, pady=(7, 0))
            b.pack(fill="x", padx=10, pady=(0, 7))
            for w in (f, a, b):
                w.bind("<Button-1>", lambda e, k=key: self.show_role(k))
                w.bind("<Enter>", lambda e, k=key: self._hover(k, True))
                w.bind("<Leave>", lambda e, k=key: self._hover(k, False))
            self.role_buttons[key] = (f, a, b)

        foot = tk.Frame(side, bg=C["side"])
        foot.pack(side="bottom", fill="x", padx=18, pady=14)
        self.engine_lbl = tk.Label(foot, text="", bg=C["side"], fg=C["side_fg"],
                                   font=("Segoe UI", 8), justify="left", anchor="w")
        self.engine_lbl.pack(anchor="w")
        self._update_side_status()

        # --- right column: preview + log -------------------------------
        right = tk.Frame(self, bg=C["bg"], width=470)
        right.pack(side="right", fill="both")
        right.pack_propagate(False)

        pv = tk.Frame(right, bg=C["panel"], highlightbackground=C["border"],
                      highlightthickness=1)
        pv.pack(fill="both", expand=True, padx=(0, 12), pady=(12, 6))
        top = tk.Frame(pv, bg=C["panel"])
        top.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(top, text="Preview", bg=C["panel"], fg=C["text"],
                 font=("Segoe UI Semibold", 11)).pack(side="left")
        tip(ttk.Button(top, text="Open image", command=self._open_preview),
            "Open the full-size PNG").pack(side="right")
        tip(ttk.Button(top, text="Run folder", command=self._open_run),
            "Open the folder of the latest run").pack(side="right", padx=4)
        self.preview_caption = tk.Label(pv, text="Nothing yet - generate or render "
                                        "something.", bg=C["panel"], fg=C["muted"],
                                        font=("Segoe UI", 9), anchor="w")
        self.preview_caption.pack(fill="x", padx=10)
        self.preview_lbl = tk.Label(pv, bg="#12151b")
        self.preview_lbl.pack(fill="both", expand=True, padx=10, pady=10)
        self.preview_lbl.bind("<Configure>", lambda e: self._fit_preview())
        self.preview_lbl.bind("<Double-Button-1>", lambda e: self._open_preview())

        lg = tk.Frame(right, bg=C["panel"], highlightbackground=C["border"],
                      highlightthickness=1)
        lg.pack(fill="both", expand=True, padx=(0, 12), pady=(6, 12))
        top = tk.Frame(lg, bg=C["panel"])
        top.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(top, text="Log", bg=C["panel"], fg=C["text"],
                 font=("Segoe UI Semibold", 11)).pack(side="left")
        ttk.Button(top, text="Clear", command=self._clear_log).pack(side="right")
        ttk.Button(top, text="Save log...", command=self._save_log).pack(side="right", padx=4)
        self.status = tk.Label(lg, text="Idle", bg=C["panel"], fg=C["muted"],
                               font=("Segoe UI", 9), anchor="w")
        self.status.pack(fill="x", padx=10)
        self.progress = ttk.Progressbar(lg, mode="indeterminate",
                                        style="Horizontal.TProgressbar")
        self.progress.pack(fill="x", padx=10)
        box = tk.Frame(lg, bg=C["log_bg"])
        box.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.logtext = tk.Text(box, bg=C["log_bg"], fg=C["log_fg"],
                               insertbackground="white", font=("Consolas", 9),
                               relief="flat", wrap="word", height=12,
                               padx=8, pady=6)
        sb = ttk.Scrollbar(box, command=self.logtext.yview)
        self.logtext.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        self.logtext.pack(fill="both", expand=True)
        self.logtext.tag_configure("err", foreground="#ff8a80")
        self.logtext.tag_configure("ok", foreground="#7ee2a8")
        self.logtext.tag_configure("muted", foreground="#8f98aa")
        self.logtext.tag_configure("head", foreground="#8ab4ff")

        # --- centre: the role pages -------------------------------------
        self.centre = tk.Frame(self, bg=C["bg"])
        self.centre.pack(side="left", fill="both", expand=True, pady=12)
        self.pages = {}
        from app_pages import QuickPage, EngineerPage, AnalystPage, AdminPage
        for key, cls in (("quick", QuickPage), ("engineer", EngineerPage),
                         ("analyst", AnalystPage), ("admin", AdminPage)):
            self.pages[key] = cls(self.centre, self)

    def _hover(self, key, on):
        if key == self.role_var.get():
            return
        colour = C["side_hover"] if on else C["side"]
        for w in self.role_buttons[key]:
            w.configure(bg=colour)

    def _update_side_status(self):
        exe, state, gf = self.rp.fortran_status()
        fort = {"ok": "ready", "stale": "needs recompile",
                "missing": "not built"}[state]
        self.engine_lbl.configure(
            text=f"Fortran exe : {fort}\ngfortran    : {'found' if gf else 'not found'}\n"
                 f"Spacing     : {self.cfg['spacing']} m\n"
                 f"Density     : {self.cfg['density']:g} kg/m^3")

    def show_role(self, key):
        if key not in self.pages:
            key = "quick"
        self.role_var.set(key)
        for k, (f, a, b) in self.role_buttons.items():
            colour = C["side_sel"] if k == key else C["side"]
            for w in (f, a, b):
                w.configure(bg=colour)
        for k, page in self.pages.items():
            if k == key:
                page.pack(fill="both", expand=True)
                if hasattr(page, "on_show"):
                    page.on_show()
            else:
                page.pack_forget()
        self.ui["role"] = key

    def generate_current(self):
        page = self.pages.get(self.role_var.get())
        if hasattr(page, "generate"):
            page.generate()

    # ------------------------------------------------------------------
    # inputs
    # ------------------------------------------------------------------
    def refresh_inputs(self):
        found = self.rp.discover_inputs()
        extra = [p for p in self.extra_inputs if os.path.exists(p) and p not in found]
        self.available = found + extra
        self.selected[:] = [p for p in self.selected if os.path.exists(p)]
        for s in self.selectors:
            s.redraw()

    def selection_changed(self):
        for s in self.selectors:
            s.redraw()
        for page in self.pages.values():
            if hasattr(page, "on_selection"):
                page.on_selection()

    def describe_input(self, p):
        try:
            mb = os.path.getsize(p) / (1024 * 1024)
        except OSError:
            mb = 0
        kind = "cloud" if self.rp.is_cloud(p) else "model"
        return f"{os.path.basename(p)[:34]:<34} {kind:<6}{mb:7.1f} MB"

    def refresh_all(self):
        self.refresh_inputs()
        self._update_side_status()
        for page in self.pages.values():
            if hasattr(page, "refresh"):
                page.refresh()

    def open_folder(self, *parts):
        path = os.path.join(ROOT, *parts)
        os.makedirs(path, exist_ok=True)
        open_path(path)

    # ------------------------------------------------------------------
    # log / preview
    # ------------------------------------------------------------------
    def log(self, text, tag=None):
        self.logtext.configure(state="normal")
        self.logtext.insert("end", text, tag or ())
        self.logtext.see("end")
        self.logtext.configure(state="disabled")

    def _clear_log(self):
        self.logtext.configure(state="normal")
        self.logtext.delete("1.0", "end")
        self.logtext.configure(state="disabled")

    def _save_log(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            initialfile="3dvox_log.txt")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.logtext.get("1.0", "end"))

    def show_preview(self, png, caption=None):
        if not png or not os.path.exists(png):
            return
        try:
            self._preview_full = tk.PhotoImage(file=png)
        except tk.TclError as exc:
            self.log(f"(cannot display {png}: {exc})\n", "muted")
            return
        self.preview_path = png
        self.preview_caption.configure(text=caption or os.path.basename(png))
        self._fit_preview()

    def _fit_preview(self):
        img = getattr(self, "_preview_full", None)
        if img is None:
            return
        w = max(self.preview_lbl.winfo_width() - 4, 50)
        h = max(self.preview_lbl.winfo_height() - 4, 50)
        f = max(1, -(-img.width() // w), -(-img.height() // h))
        self.preview_img = img.subsample(f, f) if f > 1 else img
        self.preview_lbl.configure(image=self.preview_img)

    def _open_preview(self):
        if self.preview_path:
            open_path(self.preview_path)

    def _open_run(self):
        runs = self.vp.list_runs()
        target = self.last_run or (runs[0] if runs else None)
        if target:
            open_path(target)
        else:
            self.open_folder("output", "runs")

    # ------------------------------------------------------------------
    # background jobs
    # ------------------------------------------------------------------
    def run_job(self, title, fn, on_done=None):
        """Run fn() on a worker thread, its prints streaming into the log.
        One job at a time; SystemExit messages become error dialogs."""
        if self.busy:
            messagebox.showinfo(APP_NAME, "Still working on the previous job - "
                                          "wait for it to finish.")
            return
        self.busy = True
        self.status.configure(text=f"Working: {title}", fg=C["accent"])
        self.progress.start(12)
        for page in self.pages.values():
            if hasattr(page, "set_busy"):
                page.set_busy(True)
        self.log(f"\n=== {title}  ({time.strftime('%H:%M:%S')}) ===\n", "head")
        started = time.time()

        def worker():
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = QueueWriter(self.q, None)
            sys.stderr = QueueWriter(self.q, "err")
            result, error = None, None
            try:
                result = fn()
            except SystemExit as exc:
                if exc.code not in (None, 0):
                    error = str(exc.code)
            except Exception:                                      # noqa: BLE001
                error = traceback.format_exc()
            finally:
                sys.stdout, sys.stderr = old_out, old_err
            self.q.put(("done", (title, result, error, time.time() - started,
                                 on_done), None))

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, title, result, error, secs, on_done):
        self.busy = False
        self.progress.stop()
        for page in self.pages.values():
            if hasattr(page, "set_busy"):
                page.set_busy(False)
        if error:
            self.status.configure(text=f"Failed: {title}", fg=C["bad"])
            self.log(error.rstrip() + "\n", "err")
            messagebox.showerror(APP_NAME, error.strip()[-1500:])
        else:
            self.status.configure(text=f"Done: {title}  ({secs:.1f} s)", fg=C["ok"])
            self.log(f"--- done in {secs:.1f} s ---\n", "ok")
            if on_done:
                try:
                    on_done(result)
                except Exception:                                  # noqa: BLE001
                    self.log(traceback.format_exc(), "err")

    def _poll(self):
        try:
            while True:
                kind, payload, tag = self.q.get_nowait()
                if kind == "log":
                    self.log(payload, tag)
                elif kind == "done":
                    self._finish(*payload)
                elif kind == "ask":
                    self._answer(*payload)
                elif kind == "call":
                    payload()
        except queue.Empty:
            pass
        self.after(60, self._poll)

    # the pipeline asks a question from the worker thread -> dialog here
    def choose_from_worker(self, question, options, default=0):
        box = {"answer": default}
        ev = threading.Event()
        self.q.put(("ask", (question, options, default, box, ev), None))
        ev.wait()
        return box["answer"]

    def _answer(self, question, options, default, box, ev):
        win = tk.Toplevel(self)
        win.title("A question from the pipeline")
        win.configure(bg=C["panel"])
        win.transient(self)
        win.grab_set()
        tk.Label(win, text=question, bg=C["panel"], fg=C["text"], justify="left",
                 wraplength=520, font=("Segoe UI", 10)).pack(padx=18, pady=(16, 10),
                                                             anchor="w")
        var = tk.IntVar(value=default)
        for i, o in enumerate(options):
            ttk.Radiobutton(win, text=o, value=i, variable=var).pack(
                anchor="w", padx=24, pady=2)

        def ok():
            box["answer"] = var.get()
            win.destroy()
            ev.set()

        ttk.Button(win, text="OK", style="Accent.TButton", command=ok).pack(pady=14)
        win.protocol("WM_DELETE_WINDOW", ok)
        win.wait_window()

    def call_in_ui(self, fn):
        """From a worker: run fn on the Tk thread."""
        self.q.put(("call", fn, None))

    # ------------------------------------------------------------------
    def quick_guide(self):
        messagebox.showinfo(
            "Quick guide",
            "1.  Put 3D models in input\\models and point clouds (.dat) in "
            "input\\ic  (File menu opens both).\n\n"
            "2.  Quick Start: double-click inputs to add them. The FIRST one is "
            "the ground; the rest are set down on it.\n\n"
            "3.  Type the gap in mm and press Generate  (Ctrl+Enter).\n\n"
            "4.  The preview appears on the right. If the ground is upside "
            "down, tick 'Flip' and generate again.\n\n"
            "Need more control? Switch role to Engineer. Want to look inside an "
            "IC file? Analyst. Something not working? Admin > Setup check.")

    def save_ui(self):
        self.ui["geometry"] = self.geometry()
        s = self.rp.load_settings()          # keep presets written meanwhile
        s["ui"] = self.ui
        self.rp.save_settings(s)

    def on_close(self):
        if self.busy and not messagebox.askyesno(
                APP_NAME, "A job is still running. Quit anyway?"):
            return
        try:
            self.save_ui()
        except Exception:                                          # noqa: BLE001
            pass
        self.destroy()


# ----------------------------------------------------------------------
def main():
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:                                              # noqa: BLE001
        pass

    missing = [p for p, ok, _v, req, _w in package_versions() if req and not ok]
    if missing:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(
            APP_NAME, "Python package(s) missing: " + ", ".join(missing) +
            "\n\nOpen a command prompt and run:\n\n"
            "    pip install numpy trimesh scipy matplotlib\n\nthen start again.")
        r.destroy()
        return 1
    try:
        app = App(preselect=sys.argv[1:])
    except Exception:                                              # noqa: BLE001
        err = traceback.format_exc()
        log = os.path.join(HERE, "app_error.log")
        try:
            with open(log, "w", encoding="utf-8") as fh:
                fh.write(err)
        except OSError:
            pass
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(APP_NAME, "The window could not start:\n\n" +
                             err[-1200:] + f"\n\n(saved to {log})")
        r.destroy()
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
