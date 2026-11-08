r"""
The four role pages of 3D VOX Studio (app.py).

    QuickPage     Quick Start  - inputs, gap, Generate
    EngineerPage  Engineer     - every option, presets, command line
    AnalystPage   Analyst      - inspect, render, 3D view, export, unpack, compare
    AdminPage     Admin        - setup check, Fortran, engine self-test, runs, disk
"""

import datetime
import os
import re
import shutil
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import numpy as np

from app import (APP_NAME, C, ROOT, InputSelector, ScrollPage, human,
                 open_path, package_versions, section, size_of, tip)


def page_header(page, title, text):
    f = tk.Frame(page.inner, bg=C["bg"])
    f.pack(fill="x", padx=16, pady=(4, 12))
    tk.Label(f, text=title, bg=C["bg"], fg=C["text"],
             font=("Segoe UI Semibold", 18)).pack(anchor="w")
    sub = tk.Label(f, text=text, bg=C["bg"], fg=C["muted"], justify="left",
                   font=("Segoe UI", 10), wraplength=480, anchor="w")
    sub.pack(fill="x", anchor="w")
    f.bind("<Configure>", lambda e: sub.configure(wraplength=max(200, e.width - 10)))


def row(parent):
    f = tk.Frame(parent, bg=C["panel"])
    f.pack(fill="x", pady=3)
    return f


def label(parent, text, width=None, muted=False):
    return tk.Label(parent, text=text, bg=C["panel"],
                    fg=C["muted"] if muted else C["text"], anchor="w",
                    width=width, font=("Segoe UI", 9))


def as_float(text, name):
    try:
        return float(str(text).strip().replace(",", "."))
    except ValueError:
        raise ValueError(f"{name}: '{text}' is not a number")


# ======================================================================
# QUICK START
# ======================================================================
class QuickPage(ScrollPage):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        last = app.settings.get("last", {})
        page_header(self, "Quick Start",
                    "Three steps. Sensible defaults for everything else - switch "
                    "to Engineer for the full set of options.")

        body = section(self.inner, "1   Choose what goes into the IC file",
                       "double-click to add - the first one is the ground")
        InputSelector(body, app).pack(fill="both", expand=True)

        body = section(self.inner, "2   Placement")
        r = row(body)
        label(r, "Gap above the ground (mm)", 26).pack(side="left")
        g = last.get("gap")
        self.gap = tk.StringVar(value=f"{g:g}" if isinstance(g, (int, float)) else "10")
        tip(ttk.Spinbox(r, from_=0, to=1000, increment=1, width=8,
                        textvariable=self.gap),
            "How far each body floats above the surface right underneath it"
            ).pack(side="left")

        r = row(body)
        label(r, "Where on the ground", 26).pack(side="left")
        self.where = tk.StringVar(value="center")
        for val, txt in (("center", "Middle"), ("keep", "Keep own X/Y"),
                         ("spot", "At X,Y:")):
            ttk.Radiobutton(r, text=txt, value=val, variable=self.where,
                            command=self._spot_state).pack(side="left", padx=(0, 10))
        self.spot = tk.StringVar(value=last.get("at") or "0,0")
        self.spot_entry = tip(ttk.Entry(r, textvariable=self.spot, width=12),
                              "Metres, in the ground's own coordinates. Bodies "
                              "are nudged back if they would hang off the edge.")
        self.spot_entry.pack(side="left")
        self._spot_state()

        r = row(body)
        label(r, "Facing", 26).pack(side="left")
        self.face = tk.BooleanVar(value=bool(last.get("quick_face")))
        tip(ttk.Checkbutton(r, text="Turn the rover 90\u00b0 to face along the "
                                    "ramp (--yaw 90)", variable=self.face),
            "The LEO rover's wheelbase runs across the ramp as modelled, so "
            "without this it would have to crab up the slope sideways.").pack(
            side="left")

        r = row(body)
        self.flip = tk.BooleanVar(value=False)
        tip(ttk.Checkbutton(r, text="Flip - the ground came out upside down last "
                                    "time", variable=self.flip),
            "Turns the whole scene over. The quick fix when the preview shows "
            "the terrain the wrong way up.").pack(side="left")

        body = section(self.inner, "3   Generate")
        r = row(body)
        self.lean = tk.BooleanVar(value=app.ui.get("quick_lean", False))
        tip(ttk.Checkbutton(r, text="Save disk space  (keep only the IC, manifest "
                                    "and preview)", variable=self.lean),
            "Skips particles.dat in the run folder. It can always be recreated "
            "from the IC with Analyst > Unpack.").pack(side="left")
        r = row(body)
        self.go = tip(ttk.Button(r, text="Generate IC file", style="Accent.TButton",
                                 command=self.generate),
                      "Ctrl+Enter")
        self.go.pack(side="left", pady=(6, 2))
        self.summary = tk.Label(r, text="", bg=C["panel"], fg=C["muted"],
                                justify="left", font=("Segoe UI", 9))
        self.summary.pack(side="left", padx=14)

        self.result = tk.Frame(body, bg=C["panel"])
        self.result.pack(fill="x", pady=(8, 0))
        self.on_selection()

    def _spot_state(self):
        self.spot_entry.configure(state="normal" if self.where.get() == "spot"
                                  else "disabled")

    def on_selection(self):
        s = self.app.selected
        if not s:
            txt = "Add at least one input."
        elif len(s) == 1:
            txt = f"1 input: {os.path.basename(s[0])}"
        else:
            txt = (f"{len(s)} inputs - ground: {os.path.basename(s[0])}\n"
                   f"{len(s) - 1} placed on top")
        self.summary.configure(text=txt)

    def set_busy(self, busy):
        self.go.configure(state="disabled" if busy else "normal")

    def generate(self):
        if not self.app.selected:
            messagebox.showinfo(APP_NAME, "Add at least one input first "
                                          "(double-click it in the left list).")
            return
        try:
            gap = as_float(self.gap.get(), "Gap")
            if gap < 0:
                raise ValueError("Gap cannot be negative")
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        argv = list(self.app.selected) + ["--gap", f"{gap:g}", "--no-open"]
        if self.where.get() == "spot":
            argv += ["--align", "center", "--at", self.spot.get().strip()]
        else:
            argv += ["--align", self.where.get()]
        if self.face.get():
            argv += ["--yaw", "90"]
        self.app.ui["quick_face"] = self.face.get()
        if self.flip.get():
            argv.append("--flip")
        if self.lean.get():
            argv.append("--lean")
        self.app.ui["quick_lean"] = self.lean.get()
        start_pipeline(self.app, argv, "Generate IC (Quick Start)", self.show_result)

    def show_result(self, res):
        for w in self.result.winfo_children():
            w.destroy()
        if not res:
            return
        tk.Label(self.result, text=f"Done: {res['particles']:,} particles from "
                                   f"{res['bodies']} input(s) in {res['seconds']:.1f} s",
                 bg=C["panel"], fg=C["ok"], font=("Segoe UI Semibold", 10)
                 ).pack(anchor="w")
        tk.Label(self.result, text=res["run_dir"], bg=C["panel"], fg=C["muted"],
                 font=("Segoe UI", 8)).pack(anchor="w")
        r = row(self.result)
        ttk.Button(r, text="Open run folder",
                   command=lambda: open_path(res["run_dir"])).pack(side="left")
        ttk.Button(r, text="Rotate in 3D", command=lambda: open_3d_viewer(
            self.app, res["ic"])).pack(side="left", padx=6)
        ttk.Button(r, text="Manifest",
                   command=lambda: open_path(res["manifest"])).pack(side="left")


def start_pipeline(app, argv, title, then=None):
    """Shared by Quick Start and Engineer."""
    rp = app.rp

    def job():
        args = rp.parse_args(argv, app.cfg)
        return rp.run(args, app.cfg)

    def done(res):
        app.settings = rp.load_settings()
        if res:
            app.last_run = res["run_dir"]
            if res.get("preview"):
                app.show_preview(res["preview"], os.path.basename(res["run_dir"]))
        app.refresh_all()
        if then:
            then(res)

    app.run_job(title, job, done)


# ======================================================================
# ENGINEER
# ======================================================================
class EngineerPage(ScrollPage):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        cfg = app.cfg
        page_header(self, "Engineer",
                    "Every option of the pipeline. Save a combination as a preset "
                    "and it is one click next time - or copy the command line "
                    "for scripts.")

        # presets first: they fill in everything below
        body = section(self.inner, "Presets")
        r = row(body)
        self.preset = tk.StringVar()
        self.preset_box = ttk.Combobox(r, textvariable=self.preset, width=20,
                                       state="readonly")
        self.preset_box.pack(side="left")
        tip(ttk.Button(r, text="Load", command=self.load_preset),
            "Fill every field below from the preset").pack(side="left", padx=4)
        ttk.Button(r, text="Save as...", command=self.save_preset).pack(side="left")
        ttk.Button(r, text="Delete", command=self.delete_preset).pack(side="left", padx=4)
        tip(ttk.Button(r, text="Last used", command=self.load_last),
            "Fill in the settings of the most recent run").pack(side="left")

        body = section(self.inner, "Inputs", "the first one is the ground")
        InputSelector(body, app).pack(fill="both", expand=True)

        # --- placement --------------------------------------------------
        body = section(self.inner, "Placement")
        grid = tk.Frame(body, bg=C["panel"])
        grid.pack(fill="x")
        self.v = {}

        def field(r, c, text, key, widget, hint=""):
            label(grid, text, 15).grid(row=r, column=c * 2, sticky="w", pady=3)
            widget.grid(row=r, column=c * 2 + 1, sticky="w", padx=(0, 14), pady=3)
            if hint:
                tip(widget, hint)

        self.v["layout"] = tk.StringVar(value="on-surface")
        field(0, 0, "Layout", "layout", ttk.Combobox(
            grid, textvariable=self.v["layout"], width=14, state="readonly",
            values=("on-surface", "stack-z", "row-y", "row-x", "none")),
            "on-surface: #1 is the ground, the rest are dropped onto its real "
            "surface.\nstack-z: bounding-box stack.  row-x/row-y: side by side.\n"
            "none: raw coordinates.")
        self.v["gap"] = tk.StringVar(value="10")
        field(0, 1, "Gap (mm)", "gap", ttk.Spinbox(
            grid, from_=0, to=10000, textvariable=self.v["gap"], width=10))
        self.v["align"] = tk.StringVar(value="center")
        field(1, 0, "Horizontal", "align", ttk.Combobox(
            grid, textvariable=self.v["align"], width=14, state="readonly",
            values=("center", "keep", "corner")),
            "center: middle of the ground\nkeep: own X/Y (clamped onto the "
            "ground)\ncorner: bounding-box corners (stack/row layouts)")
        self.v["at"] = tk.StringVar(value="")
        field(1, 1, "At X,Y (m)", "at", ttk.Entry(
            grid, textvariable=self.v["at"], width=12),
            "Optional. A specific spot on the ground, e.g. 2.5,4.0")
        self.v["yaw"] = tk.StringVar(value="0")
        field(2, 0, "Yaw (deg)", "yaw", ttk.Combobox(
            grid, textvariable=self.v["yaw"], width=10,
            values=("0", "90", "-90", "180")),
            "Spins everything after the first input about the vertical axis, "
            "on the spot.\n90 turns a rover whose wheelbase sits across the "
            "ramp so it faces up the slope instead.\n-90 points the other end "
            "up the ramp.")
        self.v["up"] = tk.StringVar(value="auto")
        field(3, 0, "Up axis", "up", ttk.Combobox(
            grid, textvariable=self.v["up"], width=14, state="readonly",
            values=("auto", "x", "y", "z", "-x", "-y", "-z")),
            "auto reads it off the ground's shape (a slab is thin along its "
            "normal). A minus sign means that axis points down.")
        self.v["flip"] = tk.BooleanVar(value=False)
        field(3, 1, "Flip scene", "flip", ttk.Checkbutton(
            grid, variable=self.v["flip"]), "Turn the whole scene over")

        # --- material ---------------------------------------------------
        body = section(self.inner, "Material and tags",
                       f"the .f90 says spacing {cfg['spacing']} m, "
                       f"density {cfg['density']:g} kg/m^3")
        grid = tk.Frame(body, bg=C["panel"])
        grid.pack(fill="x")
        self.v["spacing"] = tk.StringVar(value=f"{cfg['spacing']:g}")
        field(0, 0, "Spacing (m)", "spacing", ttk.Entry(
            grid, textvariable=self.v["spacing"], width=12),
            "Voxel pitch. The native engine takes any value; the Fortran engine "
            "only the one hard-coded in the .f90.")
        self.v["density"] = tk.StringVar(value=f"{cfg['density']:g}")
        field(0, 1, "Density (kg/m^3)", "density", ttk.Entry(
            grid, textvariable=self.v["density"], width=12))
        self.v["gtype"] = tk.StringVar(value="-100")
        field(1, 0, "G_type (models)", "gtype", ttk.Entry(
            grid, textvariable=self.v["gtype"], width=12),
            "Column 5 for particles made by voxelizing a model. .dat inputs keep "
            "their own values.")
        self.v["wall"] = tk.StringVar(value="2")
        field(1, 1, "Wall (models)", "wall", ttk.Entry(
            grid, textvariable=self.v["wall"], width=12),
            "Column 6 for voxelized models - lands in the IC's id record.")
        self.mass_lbl = label(body, "", muted=True)
        self.mass_lbl.pack(anchor="w", pady=(4, 0))
        for k in ("spacing", "density"):
            self.v[k].trace_add("write", lambda *_: self._check())

        # --- engine & outputs ------------------------------------------
        body = section(self.inner, "Engine and outputs")
        r = row(body)
        label(r, "Engine", 10).pack(side="left")
        self.v["engine"] = tk.StringVar(value="native")
        tip(ttk.Radiobutton(r, text="Fast native  (seconds, no gfortran)",
                            value="native", variable=self.v["engine"],
                            command=self._check),
            "Python writes the GADGET-2 binary directly - the same bytes "
            "produce_ic.exe writes (Admin > Check engines match proves it).")\
            .pack(side="left")
        r = row(body)
        label(r, "", 10).pack(side="left")
        tip(ttk.Radiobutton(r, text="Fortran reference  (produce_ic.exe)",
                            value="fortran", variable=self.v["engine"],
                            command=self._check),
            "The original route: text file -> produce_ic.exe -> IC").pack(
            side="left")
        r = row(body)
        label(r, "IC file name", 10).pack(side="left")
        self.v["output"] = tk.StringVar(value="test_mesh.ic")
        ttk.Entry(r, textvariable=self.v["output"], width=24).pack(side="left")

        r = row(body)
        label(r, "Keep", 10).pack(side="left")
        r2 = row(body)
        label(r2, "Speed", 10).pack(side="left")
        self.v["keep_particles"] = tk.BooleanVar(value=True)
        self.v["keep_control"] = tk.BooleanVar(value=False)
        self.v["preview"] = tk.BooleanVar(value=True)
        self.v["cache"] = tk.BooleanVar(value=True)
        self.v["parallel"] = tk.BooleanVar(value=True)
        for key, txt, hint in (
                ("keep_particles", "particles.dat", "Keep the 6-column text cloud"),
                ("keep_control", "probe3D_mesh.txt",
                 "The control file. ~80 bytes per particle - 300+ MB on big runs"),
                ("preview", "preview.png", ""),
                ("cache", "use cache", "Re-use parsed big clouds and voxelized "
                                       "models from output\\cache"),
                ("parallel", "all CPU cores", "Voxelize meshes in parallel")):
            parent = r2 if key in ("cache", "parallel") else r
            tip(ttk.Checkbutton(parent, text=txt, variable=self.v[key]), hint).pack(
                side="left", padx=(0, 14))

        self.warn = tk.Label(body, text="", bg=C["panel"], fg=C["bad"],
                             justify="left", font=("Segoe UI", 9))
        self.warn.pack(anchor="w", pady=(4, 0))

        body = section(self.inner, "Run")
        r = row(body)
        self.go = tip(ttk.Button(r, text="Generate IC file", style="Accent.TButton",
                                 command=self.generate), "Ctrl+Enter")
        self.go.pack(side="left")
        tip(ttk.Button(r, text="Copy command line", command=self.copy_cmd),
            "The same run as a console command - for batch files and "
            "scripts").pack(side="left", padx=10)

        self.refresh()
        self.load_last(quiet=True)
        self._check()

    # ------------------------------------------------------------------
    def refresh(self):
        names = sorted(self.app.rp.load_settings()["presets"])
        self.preset_box.configure(values=names)
        if self.preset.get() not in names:
            self.preset.set(names[0] if names else "")

    def set_busy(self, busy):
        self.go.configure(state="disabled" if busy else "normal")

    def _check(self):
        cfg = self.app.cfg
        msg = ""
        try:
            sp = as_float(self.v["spacing"].get(), "Spacing")
            de = as_float(self.v["density"].get(), "Density")
            m = self.app.fastio.particle_mass(sp, de)
            self.mass_lbl.configure(text=f"particle mass = spacing^3 x density = "
                                         f"{float(m):.8g} kg")
            if self.v["engine"].get() == "fortran" and (
                    abs(sp - cfg["spacing"]) > 1e-12 or abs(de - cfg["density"]) > 1e-9):
                msg = ("The Fortran engine only knows the spacing/density written "
                       "in the .f90 - pick the native engine or change them back.")
            exe, state, gf = self.app.rp.fortran_status()
            if self.v["engine"].get() == "fortran" and state != "ok" and not gf:
                msg = ("produce_ic.exe is " + ("missing" if state == "missing" else
                       "older than the .f90") + " and gfortran is not installed.")
        except ValueError as exc:
            msg = str(exc)
        self.warn.configure(text=msg)

    def values(self):
        out = {}
        for k, var in self.v.items():
            out[k] = var.get()
        return out

    def build_argv(self):
        v = self.values()
        cfg = self.app.cfg
        argv = list(self.app.selected)
        gap = as_float(v["gap"], "Gap")
        if gap < 0:
            raise ValueError("Gap cannot be negative")
        argv += ["--layout", v["layout"], "--gap", f"{gap:g}", "--align", v["align"]]
        if v["at"].strip():
            argv += ["--at", v["at"].strip()]
        _yaw = as_float(v["yaw"] or "0", "Yaw")
        if _yaw:
            argv += ["--yaw", f"{_yaw:g}"]
        argv.append(f"--up={v['up']}")
        if v["flip"]:
            argv.append("--flip")
        sp = as_float(v["spacing"], "Spacing")
        de = as_float(v["density"], "Density")
        if sp <= 0 or de <= 0:
            raise ValueError("Spacing and density must be positive")
        if abs(sp - cfg["spacing"]) > 1e-12:
            argv += ["--spacing", f"{sp:g}"]
        if abs(de - cfg["density"]) > 1e-9:
            argv += ["--density", f"{de:g}"]
        for k in ("gtype", "wall"):
            try:
                int(v[k])
            except ValueError:
                raise ValueError(f"{k}: '{v[k]}' is not a whole number")
        argv += ["--gtype", str(int(v["gtype"])), "--wall", str(int(v["wall"]))]
        argv += ["--engine", v["engine"]]
        if v["output"].strip() and v["output"].strip() != "test_mesh.ic":
            argv += ["--output", v["output"].strip()]
        if not v["keep_particles"]:
            argv.append("--no-particles")
        argv.append("--control" if v["keep_control"] else "--no-control")
        if not v["preview"]:
            argv.append("--no-preview")
        if not v["cache"]:
            argv.append("--no-cache")
        if not v["parallel"]:
            argv.append("--serial")
        argv.append("--no-open")
        return argv

    def generate(self):
        if not self.app.selected:
            messagebox.showinfo(APP_NAME, "Add at least one input first.")
            return
        try:
            argv = self.build_argv()
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        start_pipeline(self.app, argv, "Generate IC (Engineer)")

    def copy_cmd(self):
        try:
            argv = [a for a in self.build_argv() if a != "--no-open"]
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        def q(a):
            return f'"{a}"' if (" " in a or "\\" in a or "/" in a) else a
        cmd = "python engine\\run_pipeline.py " + " ".join(q(a) for a in argv)
        self.clipboard_clear()
        self.clipboard_append(cmd)
        self.app.log("\nCommand line (copied to clipboard):\n" + cmd + "\n", "head")

    # --- presets --------------------------------------------------------
    def _apply(self, vals):
        cfg = self.app.cfg
        m = {"layout": "layout", "align": "align", "up": "up", "engine": "engine"}
        for k, key in m.items():
            if vals.get(k):
                self.v[key].set(vals[k])
        if isinstance(vals.get("gap"), (int, float)):
            self.v["gap"].set(f"{vals['gap']:g}")
        self.v["at"].set(vals.get("at") or "")
        self.v["yaw"].set(f"{vals.get('yaw') or 0:g}")
        self.v["flip"].set(bool(vals.get("flip")))
        self.v["spacing"].set(f"{vals.get('spacing') or cfg['spacing']:g}")
        self.v["density"].set(f"{vals.get('density') or cfg['density']:g}")
        for k in ("gtype", "wall"):
            if vals.get(k) is not None:
                self.v[k].set(str(vals[k]))
        if vals.get("keep_particles") is not None:
            self.v["keep_particles"].set(bool(vals["keep_particles"]))
        if vals.get("keep_control") is not None:
            self.v["keep_control"].set(bool(vals["keep_control"]))
        if vals.get("no_preview") is not None:
            self.v["preview"].set(not vals["no_preview"])
        self._check()

    def _current_preset(self):
        v = self.values()
        cfg = self.app.cfg
        sp = as_float(v["spacing"], "Spacing")
        de = as_float(v["density"], "Density")
        return {"layout": v["layout"], "gap": as_float(v["gap"], "Gap"),
                "align": v["align"], "at": v["at"].strip() or None, "up": v["up"],
                "yaw": as_float(v["yaw"] or "0", "Yaw"),
                "flip": bool(v["flip"]), "gtype": int(v["gtype"]),
                "wall": int(v["wall"]),
                "spacing": None if abs(sp - cfg["spacing"]) < 1e-12 else sp,
                "density": None if abs(de - cfg["density"]) < 1e-9 else de,
                "engine": v["engine"], "keep_particles": bool(v["keep_particles"]),
                "keep_control": bool(v["keep_control"]),
                "no_preview": not v["preview"]}

    def load_preset(self):
        name = self.preset.get()
        presets = self.app.rp.load_settings()["presets"]
        if name in presets:
            self._apply(presets[name])
            self.app.log(f"Preset '{name}' loaded.\n", "muted")

    def load_last(self, quiet=False):
        last = self.app.rp.load_settings().get("last") or {}
        if last:
            self._apply(last)
            if not quiet:
                self.app.log("Last-used settings loaded.\n", "muted")

    def save_preset(self):
        try:
            vals = self._current_preset()
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        name = simpledialog.askstring(APP_NAME, "Preset name:",
                                      initialvalue=self.preset.get() or "my preset",
                                      parent=self)
        if not name:
            return
        s = self.app.rp.load_settings()
        s["presets"][name.strip()] = vals
        self.app.rp.save_settings(s)
        self.refresh()
        self.preset.set(name.strip())
        self.app.log(f"Preset '{name.strip()}' saved. Console use: "
                     f"--preset \"{name.strip()}\"\n", "ok")

    def delete_preset(self):
        name = self.preset.get()
        if not name or not messagebox.askyesno(APP_NAME, f"Delete preset '{name}'?"):
            return
        s = self.app.rp.load_settings()
        s["presets"].pop(name, None)
        self.app.rp.save_settings(s)
        self.refresh()


# ======================================================================
# ANALYST
# ======================================================================
def open_3d_viewer(app, path, cap=150_000):
    """Load in the background, then show a rotatable 3D window."""
    vp = app.vp

    def job():
        d = vp.load_any(path)
        pos, obj, _f = vp.decimate(d, cap)
        return d, pos, obj

    def show(res):
        d, pos, obj = res
        try:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                                           NavigationToolbar2Tk)
        except ImportError:
            messagebox.showerror(APP_NAME, "matplotlib is not installed.")
            return
        win = tk.Toplevel(app)
        win.title(f"3D  -  {os.path.basename(path)}   ({d['n']:,} particles)")
        win.geometry("980x800")
        fig = Figure(figsize=(9, 7.5), facecolor="#12151b")
        ax = fig.add_subplot(111, projection="3d", facecolor="#12151b")
        mode = tk.StringVar(value="body")

        def draw():
            ax.clear()
            if mode.get() == "z":
                c, cmap = pos[:, 2], "viridis"
            elif mode.get() == "body":
                gid, _lbl = vp.group_by_body(obj, path)
                c = vp.colours_for(obj if gid is None else gid, pos,
                                   "object")[0]
                cmap = None
            else:
                c, cmap = vp.colours_for(obj, pos, "object")[0], None
            s = float(np.clip(60000.0 / max(len(pos), 1), 0.3, 5))
            ax.scatter(pos[:, 0], pos[:, 1], pos[:, 2], c=c, cmap=cmap, s=s,
                       linewidths=0, depthshade=False)
            lo, hi = pos.min(axis=0), pos.max(axis=0)
            # Frame the real extents instead of a cube: a long flat scene
            # should fill the window so its slope is actually visible.
            pad = 0.05 * ((hi - lo).max() or 1)
            ax.set_xlim(lo[0] - pad, hi[0] + pad)
            ax.set_ylim(lo[1] - pad, hi[1] + pad)
            ax.set_zlim(lo[2] - pad, hi[2] + pad)
            try:
                ext = (hi - lo) + 2 * pad
                ext = np.maximum(ext, ext.max() * 0.06)
                ax.set_box_aspect(tuple(ext / ext.max()))
            except Exception:
                pass
            for lbl, setter in zip("XYZ", (ax.set_xlabel, ax.set_ylabel, ax.set_zlabel)):
                setter(f"{lbl} (m)", color="#e7eaf0")
            ax.tick_params(colors="#e7eaf0", labelsize=7)
            for a in (ax.xaxis, ax.yaxis, ax.zaxis):
                a.set_pane_color((0.07, 0.08, 0.11, 1.0))
            canvas.draw_idle()

        bar = tk.Frame(win, bg=C["panel"])
        bar.pack(fill="x")
        tk.Label(bar, text="Drag to rotate, right-drag to zoom.   Colour:",
                 bg=C["panel"]).pack(side="left", padx=8, pady=4)
        for val, txt in (("body", "by body"), ("object", "by object"),
                         ("z", "by height")):
            ttk.Radiobutton(bar, text=txt, value=val, variable=mode,
                            command=draw).pack(side="left")
        for txt, el, az in (("Front", 0, -90), ("Side", 0, 0), ("Top", 90, -90),
                            ("3/4", 22, -58)):
            ttk.Button(bar, text=txt, width=6, command=lambda e=el, a=az: (
                ax.view_init(elev=e, azim=a), canvas.draw_idle())).pack(
                side="right", padx=2)
        canvas = FigureCanvasTkAgg(fig, master=win)
        NavigationToolbar2Tk(canvas, win).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        ax.view_init(elev=22, azim=-58)
        draw()
        if d["n"] > len(pos):
            app.log(f"3D view shows {len(pos):,} of {d['n']:,} points "
                    f"(random, same every time).\n", "muted")

    app.run_job(f"Load {os.path.basename(path)} for 3D", job, show)


class AnalystPage(ScrollPage):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        page_header(self, "Analyst",
                    "Look inside any IC or point cloud: numbers, pictures, a "
                    "rotatable 3D view, exports for ParaView / Blender, and "
                    "side-by-side comparison.")

        body = section(self.inner, "File")
        r = row(body)
        label(r, "File A", 8).pack(side="left")
        self.a = tk.StringVar()
        self.a_box = ttk.Combobox(r, textvariable=self.a, width=64)
        self.a_box.pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Browse...", command=lambda: self.browse(self.a)).pack(
            side="left", padx=4)
        r = row(body)
        label(r, "File B", 8).pack(side="left")
        self.b = tk.StringVar()
        self.b_box = tip(ttk.Combobox(r, textvariable=self.b, width=64),
                         "Only needed for Compare")
        self.b_box.pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Browse...", command=lambda: self.browse(self.b)).pack(
            side="left", padx=4)
        label(body, "Lists input\\ic and every run in output\\runs, newest first. "
                    "You can also paste any path.", muted=True).pack(anchor="w")

        body = section(self.inner, "Look")
        r = row(body)
        self.buttons = []
        for txt, cmd, hint in (
                ("Inspect", self.inspect, "Particle count, bounding box, mass, "
                                          "objects and every field the IC carries"),
                ("Render PNG", self.render, "Front / side / top / 3D picture into "
                                            "output\\images"),
                ("Rotate in 3D", self.view3d,
                 "Interactive window you can drag around (up to 150,000 "
                 "points, so it stays smooth)")):
            b = tip(ttk.Button(r, text=txt, command=cmd), hint)
            b.pack(side="left", padx=(0, 6))
            self.buttons.append(b)
        r = row(body)
        label(r, "Colour", 8).pack(side="left")
        self.color = tk.StringVar(value="body")
        tip(ttk.Radiobutton(r, text="by body", value="body",
                            variable=self.color),
            "One colour per input: each part of the ground, then each thing "
            "placed on it.\nFalls back to by-object if the run has no "
            "manifest.txt beside it.").pack(side="left")
        ttk.Radiobutton(r, text="by object", value="object",
                        variable=self.color).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(r, text="by height", value="z",
                        variable=self.color).pack(side="left", padx=(6, 20))
        label(r, "Max points").pack(side="left")
        self.cap = tk.StringVar(value="300000")
        tip(ttk.Entry(r, textvariable=self.cap, width=9),
            "Bigger files are randomly thinned to this for drawing (0 = all)"
            ).pack(side="left", padx=4)

        body = section(self.inner, "Convert")
        r = row(body)
        label(r, "Export for", 10).pack(side="left")
        for kind, hint in (("vtk", "ParaView - keeps Object IDs. Colour by "
                                   "object_id, Point Gaussian"),
                           ("csv", "Excel, pandas, Blender"),
                           ("xyz", "MeshLab, CloudCompare")):
            b = tip(ttk.Button(r, text=kind.upper(), width=6,
                               command=lambda k=kind: self.export(k)), hint)
            b.pack(side="left", padx=2)
            self.buttons.append(b)
        r = row(body)
        label(r, "Unpack IC", 10).pack(side="left")
        self.mode = tk.StringVar(value="standard")
        for val, txt, hint in (("standard", "6 columns", "X Y Z Object_ID G_type "
                                                        "Wall - feeds straight back "
                                                        "into the generator"),
                               ("minimal", "4 columns", "X Y Z Object_ID"),
                               ("full", "13 columns", "every field incl. velocity, "
                                                      "mass, energy")):
            tip(ttk.Radiobutton(r, text=txt, value=val, variable=self.mode),
                hint).pack(side="left")
        self.header = tk.BooleanVar(value=False)
        r = row(body)
        label(r, "", 10).pack(side="left")
        self.verify = tk.BooleanVar(value=True)
        tip(ttk.Checkbutton(r, text="verify", variable=self.verify),
            "Read the result back and compare it with the binary").pack(side="left")
        tip(ttk.Checkbutton(r, text="header line", variable=self.header),
            "For Excel/pandas. produce_ic.exe cannot read it.").pack(
            side="left", padx=8)
        b = ttk.Button(r, text="Unpack to .dat", command=self.unpack)
        b.pack(side="left", padx=8)
        self.buttons.append(b)

        body = section(self.inner, "Compare A with B")
        r = row(body)
        b = tip(ttk.Button(r, text="Compare", command=self.compare),
                "Byte-identical? Same particle count, bounding box, objects, "
                "tags? Largest position difference?")
        b.pack(side="left")
        self.buttons.append(b)
        label(r, "  e.g. native vs Fortran engine, or two versions of a run",
              muted=True).pack(side="left")

        body = section(self.inner, "Folders")
        r = row(body)
        ttk.Button(r, text="output\\images", command=lambda: app.open_folder(
            "output", "images")).pack(side="left")
        ttk.Button(r, text="output\\points", command=lambda: app.open_folder(
            "output", "points")).pack(side="left", padx=6)
        ttk.Button(r, text="output\\runs", command=lambda: app.open_folder(
            "output", "runs")).pack(side="left")
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self):
        items = self.app.vp.list_candidates()
        self._items = {lbl: p for lbl, p in items}
        labels = [lbl for lbl, _p in items]
        self.a_box.configure(values=labels)
        self.b_box.configure(values=labels)
        if not self.a.get() and labels:
            self.a.set(labels[0])

    def on_show(self):
        self.refresh()

    def set_busy(self, busy):
        for b in self.buttons:
            b.configure(state="disabled" if busy else "normal")

    def browse(self, var):
        p = filedialog.askopenfilename(filetypes=[
            ("IC or point cloud", "*.ic *.dat *.txt *.csv *.xyz"), ("All", "*.*")])
        if p:
            var.set(os.path.abspath(p))

    def _resolve(self, text):
        text = text.strip()
        if not text:
            return None
        p = self._items.get(text, text)
        if os.path.isdir(p):
            p = self.app.vp.file_in_folder(p)
        return p if p and os.path.isfile(p) else None

    def _need_a(self):
        p = self._resolve(self.a.get())
        if not p:
            messagebox.showinfo(APP_NAME, "Pick a file A first (or paste a path).")
        return p

    def view3d(self):
        p = self._need_a()
        if p:
            cap = self._cap()
            open_3d_viewer(self.app, p, min(cap, 150_000) if cap > 0 else 150_000)

    def _cap(self):
        try:
            return int(self.cap.get())
        except ValueError:
            return 300000

    # ------------------------------------------------------------------
    def inspect(self):
        p = self._need_a()
        if not p:
            return
        vp, itp = self.app.vp, self.app.itp

        def job():
            d = vp.load_any(p)
            vp.report(d)
            if p.lower().endswith(".ic"):
                full = vp.read_ic(p, full=True)
                sp = itp.infer_grid(full["pos"].astype(np.float64),
                                    full["object"].astype(np.int64))
                if sp:
                    m0 = float(full["mass"][0])
                    print(f"  spacing (from the grid) : {sp:g} m")
                    print(f"  density (mass/spacing^3): {m0 / sp ** 3:.6g} kg/m^3")
                itp.describe_extras(full, "id")
            print(f"  file size        : {human(os.path.getsize(p))}")
        self.app.run_job(f"Inspect {os.path.basename(p)}", job)

    def render(self):
        p = self._need_a()
        if not p:
            return
        vp = self.app.vp
        cap, colour = self._cap(), self.color.get()

        def job():
            d = vp.load_any(p)
            os.makedirs(vp.IMAGES_ROOT, exist_ok=True)
            suffix = {"body": "", "object": "_byobject",
                      "z": "_height"}.get(colour, "")
            out = vp.unique_path(os.path.join(
                vp.IMAGES_ROOT, vp.output_name(p, suffix, ".png")), False)
            return vp.draw(d, out, cap, colour, False, True)

        self.app.run_job(f"Render {os.path.basename(p)}", job,
                         lambda png: self.app.show_preview(png, os.path.basename(png)))

    def export(self, kind):
        p = self._need_a()
        if not p:
            return
        vp = self.app.vp

        def job():
            d = vp.load_any(p)
            os.makedirs(vp.POINTS_ROOT, exist_ok=True)
            stem = os.path.splitext(vp.output_name(p, "_export", ""))[0]
            base = os.path.join(vp.POINTS_ROOT, stem)
            i = 2
            while os.path.exists(f"{base}.{kind}"):
                base = os.path.join(vp.POINTS_ROOT, f"{stem}_{i}")
                i += 1
            return vp.export(d, kind, base)
        self.app.run_job(f"Export {kind.upper()}", job)

    def unpack(self):
        p = self._need_a()
        if not p:
            return
        if not p.lower().endswith(".ic"):
            messagebox.showinfo(APP_NAME, "Unpack works on .ic files.")
            return
        vp, itp = self.app.vp, self.app.itp
        mode, header, verify = self.mode.get(), self.header.get(), self.verify.get()

        def job():
            cols = itp.build_columns(mode, "id")
            tag = {"standard": "", "minimal": "_minimal", "full": "_full"}[mode]
            os.makedirs(vp.POINTS_ROOT, exist_ok=True)
            out = vp.unique_path(os.path.join(vp.POINTS_ROOT,
                                              vp.output_name(p, tag, ".dat")), False)
            res = itp.unpack(vp, p, out, 6, verify, False, cols, header, "id")
            if not res:
                raise SystemExit("Unpack failed - see the log.")
            return res
        self.app.run_job(f"Unpack {os.path.basename(p)}", job)

    def compare(self):
        a = self._need_a()
        b = self._resolve(self.b.get())
        if not a:
            return
        if not b:
            messagebox.showinfo(APP_NAME, "Pick a file B to compare with.")
            return
        vp, fastio = self.app.vp, self.app.fastio

        def job():
            print(f"A: {a}\nB: {b}")
            if fastio.compare_files(a, b):
                print("\n  IDENTICAL - the two files are byte-for-byte the same.")
                return
            print("\n  files differ at byte level - comparing contents...")
            da, db = vp.load_any(a), vp.load_any(b)
            print(f"  particles       : {da['n']:,}  vs  {db['n']:,}"
                  + ("" if da["n"] == db["n"] else "   <-- DIFFERENT"))
            for name, d in (("A", da), ("B", db)):
                lo, hi = d["pos"].min(axis=0), d["pos"].max(axis=0)
                print(f"  bbox {name}          : X {lo[0]:+.4f}..{hi[0]:+.4f}  "
                      f"Y {lo[1]:+.4f}..{hi[1]:+.4f}  Z {lo[2]:+.4f}..{hi[2]:+.4f}")
            ia, ca = np.unique(da["object"], return_counts=True)
            ib, cb = np.unique(db["object"], return_counts=True)
            print(f"  objects         : {len(ia)}  vs  {len(ib)}")
            only_a = np.setdiff1d(ia, ib)
            only_b = np.setdiff1d(ib, ia)
            if len(only_a):
                print(f"  only in A       : IDs {only_a[:20].tolist()}")
            if len(only_b):
                print(f"  only in B       : IDs {only_b[:20].tolist()}")
            common = np.intersect1d(ia, ib)
            diffs = [(o, int(ca[ia == o][0]), int(cb[ib == o][0])) for o in common
                     if ca[ia == o][0] != cb[ib == o][0]]
            if diffs:
                print(f"  count differs   : {len(diffs)} object(s), e.g. " +
                      ", ".join(f"ID {o}: {x:,} vs {y:,}" for o, x, y in diffs[:5]))
            if da["n"] == db["n"]:
                dp = np.abs(da["pos"] - db["pos"]).max()
                same_obj = np.array_equal(da["object"], db["object"])
                print(f"  max |dpos|      : {dp:.3e} m  (particle by particle)")
                print(f"  object column   : {'identical' if same_obj else 'DIFFERS'}")
                if da.get("mass") is not None and db.get("mass") is not None:
                    print(f"  mass            : {da['mass'][0]:.9g}  vs  "
                          f"{db['mass'][0]:.9g}")
                if a.lower().endswith(".ic") and b.lower().endswith(".ic"):
                    fa, fb = vp.read_ic(a, full=True), vp.read_ic(b, full=True)
                    for k in ("vel", "type", "u", "g_type", "plate", "hinge"):
                        same = np.array_equal(fa[k], fb[k])
                        print(f"  {k:<15} : {'identical' if same else 'DIFFERS'}")
        self.app.run_job("Compare A with B", job)


# ======================================================================
# ADMIN
# ======================================================================
class AdminPage(ScrollPage):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        page_header(self, "Admin",
                    "Keep the toolchain healthy: check the setup, rebuild the "
                    "Fortran generator, prove both engines agree, and keep the "
                    "output folder from eating the disk.")

        body = section(self.inner, "Setup check")
        self.check_box = tk.Frame(body, bg=C["panel"])
        self.check_box.pack(fill="x")
        r = row(body)
        ttk.Button(r, text="Run check again", command=self.setup_check).pack(side="left")
        tip(ttk.Button(r, text="Copy pip install command", command=self.copy_pip),
            "Paste it into a command prompt").pack(side="left", padx=6)

        body = section(self.inner, "Fortran generator")
        self.fort_lbl = label(body, "")
        self.fort_lbl.pack(anchor="w")
        r = row(body)
        self.buttons = []
        b = tip(ttk.Button(r, text="Recompile produce_ic.exe", command=self.compile),
                "gfortran -O2. Needed after editing produce_ic_mesh.f90")
        b.pack(side="left")
        self.buttons.append(b)
        b = tip(ttk.Button(r, text="Check engines match", command=self.selftest),
                "Writes a 50,000-particle test scene with BOTH engines and "
                "compares the two IC files byte for byte")
        b.pack(side="left", padx=6)
        self.buttons.append(b)
        ttk.Button(r, text="Edit .f90", command=lambda: open_path(
            app.cfg["source"])).pack(side="left")

        body = section(self.inner, "Runs", "output\\runs")
        cols = ("date", "particles", "size", "files")
        tv = self.tree = ttk.Treeview(body, columns=cols, height=9,
                                      selectmode="extended")
        tv.heading("#0", text="Run")
        tv.column("#0", width=290)
        for c, w, t in (("date", 115, "Date"), ("particles", 95, "Particles"),
                        ("size", 75, "Size"), ("files", 200, "Files")):
            tv.heading(c, text=t)
            tv.column(c, width=w, anchor="e" if c in ("particles", "size") else "w")
        tv.pack(fill="both", expand=True)
        tv.bind("<Double-Button-1>", lambda e: self.open_run())
        tv.bind("<<TreeviewSelect>>", lambda e: self.preview_run())
        self.total_lbl = label(body, "", muted=True)
        self.total_lbl.pack(anchor="w", pady=(4, 0))
        r = row(body)
        ttk.Button(r, text="Open", command=self.open_run).pack(side="left")
        tip(ttk.Button(r, text="Slim selected", command=self.slim),
            "Delete particles.dat and probe3D_mesh.txt from the selected runs. "
            "The IC, manifest and preview stay - the text files can be "
            "recreated from the IC.").pack(side="left", padx=6)
        tip(ttk.Button(r, text="Delete selected", command=self.delete_runs),
            "Removes the whole run folder").pack(side="left")
        ttk.Button(r, text="Refresh", command=self.refresh).pack(side="left", padx=6)

        body = section(self.inner, "Cache and settings")
        self.cache_lbl = label(body, "")
        self.cache_lbl.pack(anchor="w")
        r = row(body)
        tip(ttk.Button(r, text="Clear cache", command=self.clear_cache),
            "output\\cache holds parsed big clouds and voxelized models so "
            "repeat runs are instant. Safe to delete any time.").pack(side="left")
        ttk.Button(r, text="Reset remembered settings",
                   command=self.reset_settings).pack(side="left", padx=6)
        ttk.Button(r, text="Open engine folder",
                   command=lambda: open_path(os.path.dirname(app.cfg["source"]))
                   ).pack(side="left")

        self._checked = False
        self.refresh()

    def on_show(self):
        if not self._checked:
            self.setup_check()
        self.refresh()

    def set_busy(self, busy):
        for b in self.buttons:
            b.configure(state="disabled" if busy else "normal")

    # --- setup ------------------------------------------------------------
    def setup_check(self):
        self._checked = True
        for w in self.check_box.winfo_children():
            w.destroy()
        rows = [("Python", True, sys.version.split()[0], sys.executable)]
        for name, ok, ver, req, why in package_versions():
            rows.append((name, ok, ver if ok else ("MISSING" if req else "not installed"),
                         why))
        exe, state, gf = self.app.rp.fortran_status()
        rows.append(("gfortran", bool(gf), gf or "not on PATH",
                     "only for the Fortran engine / recompiling"))
        rows.append(("produce_ic.exe", state == "ok",
                     {"ok": "up to date", "stale": "OLDER than the .f90",
                      "missing": "not built"}[state], "Fortran engine"))
        cfg = self.app.cfg
        rows.append(("produce_ic_mesh.f90", cfg["parsed"],
                     f"spacing {cfg['spacing']}, density {cfg['density']:g}, "
                     f"{cfg['read_columns']} cols", "read automatically"))
        free = shutil.disk_usage(ROOT).free
        rows.append(("free disk", free > 2 * 1024 ** 3, human(free),
                     "a 4 M-particle run needs ~0.25-0.8 GB"))
        for i, (name, ok, val, why) in enumerate(rows):
            optional = (not ok) and why and ("only" in why or "optional" in why
                                             or "Fortran" in why)
            mark, col = (("OK", C["ok"]) if ok else
                         (("--", C["warn"]) if optional else ("NO", C["bad"])))
            tk.Label(self.check_box, text=mark, fg=col, bg=C["panel"], width=4,
                     font=("Segoe UI Semibold", 9)).grid(row=i, column=0, sticky="w")
            tk.Label(self.check_box, text=name, bg=C["panel"], width=20, anchor="w",
                     font=("Segoe UI", 9)).grid(row=i, column=1, sticky="w")
            tk.Label(self.check_box, text=val, bg=C["panel"], anchor="w",
                     font=("Consolas", 9)).grid(row=i, column=2, sticky="w", padx=6)
            tk.Label(self.check_box, text=why, bg=C["panel"], fg=C["muted"],
                     anchor="w", font=("Segoe UI", 8)).grid(row=i, column=3, sticky="w")
        self._fortran_label()

    def copy_pip(self):
        cmd = "pip install numpy trimesh scipy matplotlib pandas"
        self.clipboard_clear()
        self.clipboard_append(cmd)
        self.app.log(f"Copied: {cmd}\n", "muted")

    def _fortran_label(self):
        exe, state, gf = self.app.rp.fortran_status()
        self.fort_lbl.configure(
            text=f"{os.path.basename(exe) if exe else 'produce_ic.exe'}: "
                 f"{ {'ok': 'up to date', 'stale': 'older than the .f90', 'missing': 'not built'}[state] }"
                 f"      gfortran: {gf or 'not found'}")

    def compile(self):
        rp = self.app.rp

        def job():
            if not shutil.which("gfortran"):
                raise SystemExit("gfortran is not on the PATH. Install MinGW-w64 "
                                 "(or 'sudo apt install gfortran') and try again.")
            return rp.ensure_fortran_binary(force=True)

        def done(_r):
            self._fortran_label()
            self.app._update_side_status()
        self.app.run_job("Recompile produce_ic", job, done)

    def selftest(self):
        rp, fastio = self.app.rp, self.app.fastio
        cfg = self.app.cfg

        def job():
            exe, state, gf = rp.fortran_status()
            if state == "missing" and not gf:
                raise SystemExit("No produce_ic.exe and no gfortran - there is no "
                                 "Fortran engine to compare against.")
            exe = rp.ensure_fortran_binary()
            work = os.path.join(fastio.CACHE_DIR, "selftest")
            if os.path.isdir(work):
                shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work)
            rng = np.random.default_rng(42)
            n = 50_000
            xyz = rng.uniform(-3, 3, (n, 3)).round(6)
            data = np.column_stack([xyz, rng.integers(-100, 70, n),
                                    rng.integers(-100, 50, n),
                                    rng.integers(-2, 3, n)]).astype(np.float64)
            dat = os.path.join(work, cfg["input_points"])
            fastio.write_particles(dat, data)
            print(f"  test scene: {n:,} particles, random tags")
            nat = os.path.join(work, "native.ic")
            fastio.write_ic_native(nat, data, cfg["spacing"], cfg["density"])
            print("  native engine  -> native.ic")
            import subprocess
            r = subprocess.run([exe], cwd=work, capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit("produce_ic failed:\n" + r.stdout + r.stderr)
            fort = os.path.join(work, cfg["outputfile"])
            print(f"  Fortran engine -> {cfg['outputfile']}")
            same = fastio.compare_files(nat, fort)
            if same:
                print(f"\n  PASS - both engines wrote the same "
                      f"{os.path.getsize(nat):,} bytes.")
            else:
                print("\n  FAIL - the files differ. Use the Fortran engine and "
                      "report this.")
            shutil.rmtree(work, ignore_errors=True)
            if not same:
                raise SystemExit("Engines do NOT match - see the log.")
            return same
        self.app.run_job("Check engines match", job,
                         lambda ok: messagebox.showinfo(
                             APP_NAME, "PASS - the native engine writes exactly "
                                       "the same bytes as produce_ic.exe."))

    # --- runs -------------------------------------------------------------
    def refresh(self):
        tv = self.tree
        tv.delete(*tv.get_children())
        total = 0
        for folder in self.app.vp.list_runs():
            name = os.path.basename(folder)
            files = sorted(os.listdir(folder))
            size = sum(os.path.getsize(os.path.join(folder, f)) for f in files
                       if os.path.isfile(os.path.join(folder, f)))
            total += size
            m = re.match(r"(\d{8})_(\d{6})", name)
            date = (datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
                    .strftime("%Y-%m-%d %H:%M") if m else "")
            parts = ""
            man = os.path.join(folder, "manifest.txt")
            if os.path.exists(man):
                try:
                    with open(man, encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            if line.startswith("total particles"):
                                parts = f"{int(line.split(':')[1]):,}"
                                break
                except Exception:                                  # noqa: BLE001
                    pass
            short = ", ".join(f.replace("probe3D_mesh.txt", "control")
                              .replace("particles.dat", "dat") for f in files)
            tv.insert("", "end", iid=folder, text=name,
                      values=(date, parts, human(size), short))
        n, cb = self.app.fastio.cache_info()
        self.total_lbl.configure(text=f"{len(tv.get_children())} runs, "
                                      f"{human(total)} in total")
        self.cache_lbl.configure(text=f"output\\cache: {n} file(s), {human(cb)}")
        self._fortran_label()

    def _selected(self):
        return list(self.tree.selection())

    def open_run(self):
        for f in self._selected()[:3]:
            open_path(f)

    def preview_run(self):
        sel = self._selected()
        if len(sel) == 1:
            p = os.path.join(sel[0], "preview.png")
            if os.path.exists(p):
                self.app.show_preview(p, os.path.basename(sel[0]))

    def slim(self):
        sel = self._selected()
        if not sel:
            return
        victims = [os.path.join(f, n) for f in sel
                   for n in ("particles.dat", "probe3D_mesh.txt")
                   if os.path.exists(os.path.join(f, n))]
        if not victims:
            messagebox.showinfo(APP_NAME, "Nothing to slim - those runs have no "
                                          "text files left.")
            return
        freed = sum(os.path.getsize(v) for v in victims)
        if not messagebox.askyesno(
                APP_NAME, f"Delete {len(victims)} text file(s) from {len(sel)} "
                          f"run(s) and free {human(freed)}?\n\nThe IC files stay. "
                          f"This cannot be undone."):
            return
        for v in victims:
            try:
                os.remove(v)
            except OSError as exc:
                self.app.log(f"could not delete {v}: {exc}\n", "err")
        self.app.log(f"Slimmed {len(sel)} run(s), freed {human(freed)}.\n", "ok")
        self.refresh()

    def delete_runs(self):
        sel = self._selected()
        if not sel:
            return
        size = sum(size_of(f) for f in sel)
        if not messagebox.askyesno(
                APP_NAME, f"Permanently delete {len(sel)} run folder(s) "
                          f"({human(size)})?\n\n" +
                          "\n".join(os.path.basename(f) for f in sel[:8]) +
                          ("\n..." if len(sel) > 8 else "")):
            return
        for f in sel:
            shutil.rmtree(f, ignore_errors=True)
        self.app.log(f"Deleted {len(sel)} run(s), freed {human(size)}.\n", "ok")
        self.app.refresh_all()

    def clear_cache(self):
        n = self.app.fastio.clear_cache()
        self.app.log(f"Cache cleared ({n} file(s)).\n", "ok")
        self.refresh()

    def reset_settings(self):
        if not messagebox.askyesno(APP_NAME, "Forget the last-used settings and "
                                             "window layout? Presets are kept."):
            return
        s = self.app.rp.load_settings()
        s["last"], s["ui"] = {}, {}
        self.app.rp.save_settings(s)
        self.app.ui = {}
        self.app.log("Remembered settings reset.\n", "ok")
