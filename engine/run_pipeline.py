#!/usr/bin/env python3
"""
ONE-SHOT PIPELINE:  3D models and/or point clouds  ->  GADGET-2 IC file

Runs the entire chain automatically:
    1. Load the inputs      3D meshes are voxelized; .dat point clouds are
                            taken as they are
    2. Place the bodies    later inputs are set down on the first one
    3. Compile the Fortran  produce_ic[.exe]   (only if the .f90 changed)
    4. Run the generator    test_mesh.ic  +  probe3D_mesh.txt
    5. Draw a preview       preview.png        (--no-preview to skip)

Folder layout (this file lives in engine\\):
    input\\models\\    3D models to voxelize
    input\\ic\\        point clouds / IC files
    output\\runs\\     one timestamped folder per run
    engine\\          this file, produce_ic_mesh.f90, produce_ic.exe

Usage:
    python run_pipeline.py                      pick from a menu
    python run_pipeline.py model.glb            one model
    python run_pipeline.py a.dat b.dat          two point clouds, stacked
    python run_pipeline.py a.glb b.dat          mix freely
    python run_pipeline.py --all                everything in input\\models\\

Placement (default --layout on-surface):
    The FIRST input is the ground. Every other input is moved inside the
    ground's footprint and set down on the actual surface underneath it -
    on a hill, in a crater, on whatever was placed before it - not floating
    above the highest point of the scene and not off the end of it.
      --gap 10          clearance above the surface, in millimetres
      --align center|keep
      --at 2.5,4.0      put them at a specific spot on the ground
      --layout stack-z | row-y | row-x | none      the older behaviours

Up axis:
    Every input in a run is rotated together so the world up axis becomes +Z.
    'auto' reads the axis off the shape of the ground body: a terrain slab is
    thin along its normal. This applies to point clouds as well as meshes -
    rotating only the meshes leaves a .dat terrain lying in a different plane
    from the model standing on it.
      --up auto | x | y | z | -x | -y | -z      (write --up=-y, not --up -y)
      --flip                                   turn the whole scene over

particles.dat is written with six columns:  X Y Z Object_ID G_type Wall
because produce_ic_mesh.f90 reads six values per line.

Requires: python3 + trimesh + numpy + scipy, and gfortran on the PATH
(only for the first run / after editing the .f90).
"""

import argparse
import datetime
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fastio                                                   # noqa: E402

# --- where things live -------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = (os.path.dirname(HERE)
        if os.path.basename(HERE).lower() == "engine" else HERE)

MODELS_FOLDER = os.path.join(ROOT, "input", "models")
IC_INBOX      = os.path.join(ROOT, "input", "ic")
RUNS_ROOT     = os.path.join(ROOT, "output", "runs")
MODELS_LABEL  = os.path.join("input", "models")

DEFAULT_OUTPUT  = "test_mesh.ic"
MANIFEST_FILE   = "manifest.txt"
FORTRAN_SOURCE  = "produce_ic_mesh.f90"

# Fallbacks only - the real values are read out of the .f90 (see read_fortran_config)
FALLBACK_SPACING = 0.0025
FALLBACK_DENSITY = 1000.0

DEFAULT_GAP_MM = 10.0

# Tags written into columns 5 and 6 for particles the voxelizer creates.
# -100 is what combined_rover_particles_with_wall.dat uses for plain body
# material; 2 is the Wall value every particle in that file carries, and it
# lands in the IC's `id` record exactly as the old hard-coded id=2 did.
DEFAULT_GTYPE = -100
DEFAULT_WALL  = 2

MODEL_EXTENSIONS = (".glb", ".gltf", ".stl", ".obj", ".ply", ".off", ".dae", ".3mf")
CLOUD_EXTENSIONS = (".dat", ".txt", ".csv", ".xyz")

LAYOUT_AXIS = {"stack-z": 2, "row-y": 1, "row-x": 0}
AXIS_NAME   = {0: "X", 1: "Y", 2: "Z"}


SETTINGS_FILE = os.path.join(HERE, "settings.json")

# The GUI sets this to a function(question, [option texts], default_index)
# -> chosen index, so questions that would normally be typed in the console
# pop up as a dialog instead.
CHOOSER = None


def is_cloud(path):
    return path.lower().endswith(CLOUD_EXTENSIONS)


def _interactive():
    """True when someone can answer a question: a console or the GUI."""
    if CHOOSER is not None:
        return True
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def _console():
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


# ----------------------------------------------------------------------
# remembered settings and named presets  (engine\settings.json)
# ----------------------------------------------------------------------
def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("presets", {})
            data.setdefault("last", {})
            return data
    except Exception:
        pass
    return {"presets": {}, "last": {}}


def save_settings(data):
    try:
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except Exception as exc:
        print(f"  [note] could not save settings: {exc}")


# the options a preset may carry (argparse dest names)
PRESET_KEYS = ("layout", "gap", "align", "at", "up", "flip", "gtype", "wall",
               "spacing", "density", "engine", "keep_particles",
               "keep_control", "no_preview")


# ----------------------------------------------------------------------
# Reading the Fortran's own configuration out of its source
# ----------------------------------------------------------------------
def read_fortran_config():
    """produce_ic_mesh.f90 hard-codes its spacing, density and filenames.
    Rather than duplicating those numbers here (and silently disagreeing with
    them), read them straight out of the source so the Python side can never
    drift from what the generator will actually do."""
    src_path = os.path.join(HERE, FORTRAN_SOURCE)
    cfg = {
        "spacing": FALLBACK_SPACING,
        "density": FALLBACK_DENSITY,
        "input_points": "particles.dat",
        "outputfile": "TEST_MESH",
        "outcontrol": "probe3D_mesh.txt",
        "supports_args": False,
        "read_columns": 6,
        "source": src_path,
        "parsed": False,
    }
    if not os.path.exists(src_path):
        return cfg

    text = open(src_path, "r", errors="replace").read()
    # strip Fortran comments so a commented-out line never wins
    lines = []
    for line in text.splitlines():
        i = line.find("!")
        lines.append(line if i < 0 else line[:i])
    code = "\n".join(lines)

    def last_assign(name, pattern):
        hits = re.findall(pattern, code, re.IGNORECASE)
        return hits[-1] if hits else None

    v = last_assign("spacing", r"spacing_abstand\s*=\s*([0-9.eEdD+-]+)")
    if v:
        cfg["spacing"] = float(v.replace("d", "e").replace("D", "e"))
    v = last_assign("density", r"density_standard\s*=\s*([0-9.eEdD+-]+)")
    if v:
        cfg["density"] = float(v.replace("d", "e").replace("D", "e"))
    for key, var in (("input_points", "input_points"),
                     ("outputfile", "outputfile"),
                     ("outcontrol", "outcontrol")):
        v = last_assign(key, var + r"\s*=\s*'([^']*)'")
        if v:
            cfg[key] = v

    cfg["supports_args"] = "get_command_argument" in code.lower()

    m = re.search(r"read\s*\(\s*10\s*,\s*\*[^)]*\)\s*([^\n]+)", code, re.IGNORECASE)
    if m:
        cfg["read_columns"] = len([t for t in m.group(1).split(",") if t.strip()])

    cfg["parsed"] = True
    return cfg


# ----------------------------------------------------------------------
def make_run_dir(models):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    first = os.path.splitext(os.path.basename(models[0]))[0]
    tag = re.sub(r"[^A-Za-z0-9_-]+", "_", first).strip("_") or "run"
    if len(models) > 1:
        tag = f"{tag}_and_{len(models) - 1}_more"
    run_name = f"{stamp}_{tag}"

    os.makedirs(RUNS_ROOT, exist_ok=True)
    run_dir = os.path.join(RUNS_ROOT, run_name)
    suffix = 1
    while os.path.exists(run_dir):
        run_dir = os.path.join(RUNS_ROOT, f"{run_name}_{suffix}")
        suffix += 1
    os.makedirs(run_dir)
    return run_dir


# ----------------------------------------------------------------------
# STEP 0 -- choosing the inputs
# ----------------------------------------------------------------------
def discover_inputs():
    """Models in input\\models\\ plus point clouds in input\\ic\\."""
    out = []
    if os.path.isdir(MODELS_FOLDER):
        out += sorted(os.path.join(MODELS_FOLDER, f)
                      for f in os.listdir(MODELS_FOLDER)
                      if f.lower().endswith(MODEL_EXTENSIONS))
    if os.path.isdir(IC_INBOX):
        out += sorted(os.path.join(IC_INBOX, f)
                      for f in os.listdir(IC_INBOX)
                      if f.lower().endswith(CLOUD_EXTENSIONS))
    return out


def parse_selection(text, n):
    text = text.strip().lower()
    if text in ("all", "a", "*"):
        return list(range(n))
    if text in ("", "q", "quit", "cancel"):
        return []
    chosen = []
    for tok in re.split(r"[,\s]+", text):
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", tok)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            values = range(lo, hi + 1)
        elif tok.isdigit():
            values = [int(tok)]
        else:
            raise ValueError(f"'{tok}' is not a number, a range like 2-5, or 'all'")
        for v in values:
            if not 1 <= v <= n:
                raise ValueError(f"{v} is out of range (valid: 1-{n})")
            if (v - 1) not in chosen:
                chosen.append(v - 1)
    return chosen


def ask(prompt, default, cast, validate=None):
    """One typed question with a default shown in brackets."""
    while True:
        try:
            raw = input(f"  {prompt} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if raw == "":
            return default
        try:
            value = cast(raw)
        except ValueError:
            print(f"  !! '{raw}' is not valid - try again.")
            continue
        if validate:
            problem = validate(value)
            if problem:
                print(f"  !! {problem}")
                continue
        return value


def prompt_for_inputs(candidates):
    print()
    print("Available inputs:")
    print("  " + "-" * 68)
    for i, path in enumerate(candidates, 1):
        size_mb = os.path.getsize(path) / (1024 * 1024)
        kind = "point cloud" if is_cloud(path) else "3D model"
        where = os.path.basename(os.path.dirname(path))
        print(f"  [{i:>2}]  {os.path.basename(path):<40} {kind:<12}"
              f" {size_mb:7.1f} MB   ({where})")
    print("  " + "-" * 68)
    print()
    print("Which ones should be generated TOGETHER into one IC file?")
    print("    numbers   1,3      or   1 3")
    print("    ranges    1-3")
    print("    'all'     everything listed")
    print("    blank     cancel")
    print("  (the order you type them is the stacking order, bottom first)")
    print()

    while True:
        try:
            raw = input("  Selection > ")
        except (EOFError, KeyboardInterrupt):
            print()
            return []
        try:
            picked = parse_selection(raw, len(candidates))
        except ValueError as exc:
            print(f"  !! {exc}  -  try again.")
            continue
        if not picked:
            return []
        print()
        print(f"  Selected {len(picked)}, bottom first:")
        for n, idx in enumerate(picked, 1):
            print(f"     {n}. {os.path.basename(candidates[idx])}")
        return [candidates[idx] for idx in picked]


def choose_inputs(args):
    if args.models:
        return args.models
    candidates = discover_inputs()
    if not candidates:
        sys.exit(
            f"Nothing to work with.\n"
            f"Put 3D models in '{MODELS_LABEL}' or point clouds in "
            f"'{os.path.join('input', 'ic')}' and run again."
        )
    if args.all or not _console():
        if not args.all:
            print(f"[no console for the picker - using all {len(candidates)} input(s)]")
        return candidates
    chosen = prompt_for_inputs(candidates)
    if not chosen:
        sys.exit("Cancelled - nothing was generated.")
    return chosen


def prompt_for_layout(args, n_bodies):
    """Ask how far apart the bodies should be, and how to line them up.
    Only asked when it matters: more than one body, a console, no flag given."""
    gap_mm = args.gap
    align = args.align
    remembered = load_settings()["last"].get("gap")
    default_gap = remembered if isinstance(remembered, (int, float)) else DEFAULT_GAP_MM

    if n_bodies < 2 or args.layout == "none":
        return (gap_mm if gap_mm is not None else default_gap,
                align or "center")

    on_surface = args.layout == "on-surface"
    interactive = _console()

    if gap_mm is None:
        if interactive:
            print()
            if on_surface:
                print(f"  The first input is the ground. The other "
                      f"{n_bodies - 1} will be set down on its surface.")
                gap_mm = ask("How far above the surface should they sit, in mm?",
                             default_gap, float,
                             lambda v: "cannot be negative" if v < 0 else None)
            else:
                print(f"  {n_bodies} bodies will be stacked bottom to top along "
                      f"{AXIS_NAME[LAYOUT_AXIS[args.layout]]}.")
                gap_mm = ask("How far apart should they be, in mm?",
                             default_gap, float,
                             lambda v: "gap cannot be negative" if v < 0 else None)
        else:
            gap_mm = default_gap

    if align is None:
        if interactive:
            print()
            if on_surface:
                print("  Where on the ground should they go?")
                print("     1  middle of the ground   (recommended)")
                print("     2  keep their own X/Y, clamped to stay on the ground")
                print("     3  a spot I type in")
                choice = ask("Choose", 1, int,
                             lambda v: "pick 1, 2 or 3" if v not in (1, 2, 3) else None)
                if choice == 3:
                    args.at = ask("X,Y in metres", "0,0", str)
                align = {1: "center", 2: "keep", 3: "center"}[choice]
            else:
                print("  How should they line up horizontally?")
                print("     1  centred over each other   (a neat vertical stack)")
                print("     2  keep their own X/Y        (may lean or overhang)")
                print("     3  align bounding-box corners")
                choice = ask("Choose", 1, int,
                             lambda v: "pick 1, 2 or 3" if v not in (1, 2, 3) else None)
                align = {1: "center", 2: "keep", 3: "corner"}[choice]
        else:
            align = "center"

    return gap_mm, align


# ----------------------------------------------------------------------
# STEP 1 -- inputs -> particle cloud
# ----------------------------------------------------------------------
def get_meshes_from_file(input_file):
    import trimesh
    if not os.path.exists(input_file):
        print(f"Error: File '{input_file}' not found.")
        return []
    print(f"Loading: {input_file}...")
    try:
        loaded = trimesh.load(input_file)
    except Exception as e:
        print(f"Error loading {input_file}: {e}")
        return []

    meshes = []
    if isinstance(loaded, trimesh.Scene):
        print(f"  -> Detected Scene with {len(loaded.geometry)} geometries.")
        for node_name in loaded.graph.nodes_geometry:
            transform, geometry_name = loaded.graph.get(node_name)
            mesh = loaded.geometry[geometry_name].copy()
            mesh.apply_transform(transform)
            meshes.append(mesh)
    else:
        meshes.append(loaded)
        print("  -> Detected single Mesh.")
    return meshes


def rotate_yaw(points, deg, pivot_xy=None):
    """Spin points about the vertical (Z) axis, in the pipeline's own frame.

    A rover's wheelbase runs along one horizontal axis and a ramp climbs along
    another; if they meet at 90 degrees the rover has to crab up the slope
    sideways. This turns a body on the spot so it faces the way you want."""
    if not deg:
        return points
    t = math.radians(deg)
    c, sn = math.cos(t), math.sin(t)
    if pivot_xy is None:
        pivot_xy = ((points[:, 0].min() + points[:, 0].max()) / 2.0,
                    (points[:, 1].min() + points[:, 1].max()) / 2.0)
    x = points[:, 0] - pivot_xy[0]
    y = points[:, 1] - pivot_xy[1]
    out = points.copy()
    out[:, 0] = x * c - y * sn + pivot_xy[0]
    out[:, 1] = x * sn + y * c + pivot_xy[1]
    return out


def rotate_to_z_up(points, axis, sign=1):
    """Rotate so the given signed world axis becomes +Z.

    Applied to EVERY input in a run, models and point clouds alike - they all
    come out of the same modelling world, so they all need the same rotation.
    Rotating only the meshes (which is what earlier versions did) leaves a
    .dat terrain lying in a different plane from the model standing on it.

    sign = -1 means that axis points DOWN in the source files, so the scene
    is additionally turned over (180 degrees about X, a proper rotation)."""
    out = points
    if axis == "y":                     # +90 about X:  (x,y,z) -> (x,-z,y)
        out = np.column_stack([points[:, 0], -points[:, 2], points[:, 1]])
    elif axis == "x":                   # -90 about Y:  (x,y,z) -> (-z,y,x)
        out = np.column_stack([-points[:, 2], points[:, 1], points[:, 0]])
    else:
        out = points.copy()
    if sign < 0:                        # 180 about X:  (x,y,z) -> (x,-y,-z)
        out[:, 1] = -out[:, 1]
        out[:, 2] = -out[:, 2]
    return out


SLAB_RATIO = 0.35      # thinnest extent below this fraction of the largest = a slab


def analyse_ground(points, name):
    """Which axis is up for this scene, and which way along it.

    The AXIS is reliable: a ground slab or heightfield is thin along its
    normal. The DIRECTION is only knowable when the body is solid - then one
    face is a flat base (a spike in the height histogram at one end) and the
    relief is on the other. A single-layer heightfield has no solid side at
    all, so nothing in the geometry says which way it was meant to face and
    we have to ask."""
    ext = points.max(axis=0) - points.min(axis=0)
    thin = int(np.argmin(ext))
    info = {
        "axis": AXIS_NAME[thin].lower(),
        "sign": 1,
        "slab": bool(ext.max() > 0 and ext[thin] / ext.max() < SLAB_RATIO),
        "ambiguous": True,
        "reason": "",
        "extents": ext,
        "name": name,
    }
    if not info["slab"]:
        return info

    v = points[:, thin]
    lo, hi = float(v.min()), float(v.max())
    if hi - lo <= 0:
        return info
    hist, edges = np.histogram(v, bins=41)
    peak = int(np.argmax(hist))
    modal = float((edges[peak] + edges[peak + 1]) / 2)
    where = (modal - lo) / (hi - lo)          # 0 = at the bottom, 1 = at the top
    share = hist[peak] / len(v)

    if share > 0.25 and where < 0.15:
        info.update(sign=1, ambiguous=False,
                    reason="its flat face is at the low end, so that is the base")
    elif share > 0.25 and where > 0.85:
        info.update(sign=-1, ambiguous=False,
                    reason="its flat face is at the high end, so it is upside down")
    else:
        below, above = modal - lo, hi - modal
        info["reason"] = (
            f"ground level sits at {AXIS_NAME[thin]}={modal:+.4f} with "
            f"{below * 1000:.0f} mm of relief on one side and "
            f"{above * 1000:.0f} mm on the other")
        info["below"], info["above"] = below, above
    return info


def ask_ground_direction(info, interactive):
    """A heightfield can face either way and the geometry cannot tell us.
    Ask, showing what each choice means, rather than guessing silently."""
    ax = info["axis"].upper()
    if not info["ambiguous"]:
        return info["sign"]
    if not interactive:
        return info["sign"]

    below = info.get("below", 0.0) * 1000
    above = info.get("above", 0.0) * 1000
    if CHOOSER is not None:
        idx = CHOOSER(
            f"'{info['name']}' is a surface, and nothing in its geometry says "
            f"which way up it was modelled.\n{info['reason']}.",
            [f"+{ax} is up  (the {below:.0f} mm feature is a dip, the "
             f"{above:.0f} mm one sticks up)",
             f"-{ax} is up  (turn the scene over: the {below:.0f} mm feature "
             f"becomes a mound)"], 0)
        return 1 if idx == 0 else -1
    print()
    print(f"  '{info['name']}' is a surface, and nothing in its geometry says")
    print(f"  which way up it was modelled. {info['reason']}.")
    print(f"     1  +{ax} is up   -> the {below:.0f} mm feature is a dip, "
          f"the {above:.0f} mm one sticks up")
    print(f"     2  -{ax} is up   -> the scene is turned over: the {below:.0f} mm "
          f"feature becomes a mound")
    choice = ask("Choose", 1, int,
                 lambda v: "pick 1 or 2" if v not in (1, 2) else None)
    return 1 if choice == 1 else -1


def detect_up_axis(bodies, inputs, interactive=True):
    """Returns (axis, sign, reason)."""
    if bodies:
        info = analyse_ground(bodies[0]["points"], bodies[0]["name"])
        if info["slab"]:
            sign = ask_ground_direction(info, interactive)
            ext = info["extents"]
            why = (f"'{info['name']}' is slab-shaped "
                   f"({ext[0]:.3f} x {ext[1]:.3f} x {ext[2]:.3f} m), so its thin "
                   f"{info['axis'].upper()} axis is up")
            if not info["ambiguous"]:
                why += f" and {info['reason']}"
            return info["axis"], sign, why
    if any(f.lower().endswith((".glb", ".gltf")) for f in inputs):
        return "y", 1, "glTF/GLB inputs are Y-up by specification"
    return "z", 1, "no slab-shaped ground and no glTF input, assuming Z is already up"


def voxelize_solid_rays(mesh, pitch, max_work=4e8):
    """Solid voxelization by ray stabbing. Pure numpy - no rtree, no shapely.

    trimesh's default voxelizer refines the mesh until every edge is shorter
    than the pitch. A 1.8 m slab at 2.5 mm needs ~700 subdivisions per edge
    and dies with "max_iter exceeded", which used to drop that part from the
    scene with only a line in the log. This fires one ray per grid column
    along the longest axis, finds where it crosses the surface and fills
    between alternate crossings, so the cost is columns x triangles - tiny
    for exactly the big low-poly shapes that defeat the default."""
    lo, hi = np.asarray(mesh.bounds[0]), np.asarray(mesh.bounds[1])
    n = np.maximum(np.ceil((hi - lo) / pitch).astype(int), 1)
    axis = int(np.argmax(n))
    u, v = [a for a in (0, 1, 2) if a != axis]

    tris = np.asarray(mesh.triangles, dtype=np.float64)
    n_rays = int(n[u]) * int(n[v])
    if len(tris) == 0:
        return np.empty((0, 3))
    if n_rays * len(tris) > max_work:
        raise MemoryError(f"{n_rays:,} columns x {len(tris):,} triangles is "
                          f"too much work for the ray voxelizer")

    gu = lo[u] + (np.arange(n[u]) + 0.5) * pitch
    gv = lo[v] + (np.arange(n[v]) + 0.5) * pitch
    PU, PV = np.meshgrid(gu, gv, indexing="ij")
    PU, PV = PU.ravel(), PV.ravel()

    a, b, c = tris[:, 0, :], tris[:, 1, :], tris[:, 2, :]
    au, av, aw = a[:, u], a[:, v], a[:, axis]
    e0u, e0v = b[:, u] - au, b[:, v] - av
    e1u, e1v = c[:, u] - au, c[:, v] - av
    w0, w1 = b[:, axis] - aw, c[:, axis] - aw
    denom = e0u * e1v - e1u * e0v
    live = np.abs(denom) > 1e-14

    hits_ray, hits_t = [], []
    for i in np.nonzero(live)[0]:
        du, dv = PU - au[i], PV - av[i]
        ss = (du * e1v[i] - dv * e1u[i]) / denom[i]
        tt = (dv * e0u[i] - du * e0v[i]) / denom[i]
        inside = (ss >= 0.0) & (tt >= 0.0) & (ss + tt <= 1.0)
        if not inside.any():
            continue
        idx = np.nonzero(inside)[0]
        hits_ray.append(idx)
        hits_t.append(aw[i] + ss[idx] * w0[i] + tt[idx] * w1[i])
    if not hits_ray:
        return np.empty((0, 3))

    hits_ray = np.concatenate(hits_ray)
    hits_t = np.concatenate(hits_t)
    order = np.lexsort((hits_t, hits_ray))
    hits_ray, hits_t = hits_ray[order], hits_t[order]

    centres = lo[axis] + (np.arange(n[axis]) + 0.5) * pitch
    rays = np.arange(n_rays)
    starts = np.searchsorted(hits_ray, rays, side="left")
    ends = np.searchsorted(hits_ray, rays, side="right")

    out = []
    for r in np.nonzero(ends > starts)[0]:
        ts = hits_t[starts[r]:ends[r]]
        if len(ts) < 2:
            continue
        inside = (np.searchsorted(ts, centres, side="right") % 2) == 1
        if not inside.any():
            continue
        k = np.nonzero(inside)[0]
        pts = np.empty((len(k), 3))
        pts[:, u] = PU[r]
        pts[:, v] = PV[r]
        pts[:, axis] = centres[k]
        out.append(pts)
    return np.vstack(out) if out else np.empty((0, 3))


def _voxelize_mesh(job):
    """One mesh -> filled voxel centres. Top-level so worker processes can
    run it. Returns (points or None, watertight, note)."""
    mesh, spacing = job
    wt = bool(getattr(mesh, "is_watertight", False))
    try:
        pts = mesh.voxelized(pitch=spacing).fill().points
        return np.asarray(pts, dtype=np.float64), wt, ""
    except Exception as exc:                                    # noqa: BLE001
        first = str(exc)
    try:
        pts = voxelize_solid_rays(mesh, spacing)
        if len(pts):
            return pts, wt, f"RECOVERED|trimesh said '{first}'"
        return None, wt, f"{first} (and the ray fallback found nothing)"
    except Exception as exc2:                                   # noqa: BLE001
        return None, wt, f"{first} / ray fallback: {exc2}"


PARALLEL = True        # --serial / the GUI can switch this off


def _vox_cache_path(filename, spacing):
    st = os.stat(filename)
    key = (f"{os.path.abspath(filename).lower()}|{st.st_size}|"
           f"{int(st.st_mtime)}|{spacing:.10g}|vox1")
    import hashlib
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    stem = os.path.splitext(os.path.basename(filename))[0]
    return os.path.join(fastio.CACHE_DIR, f"{stem}_{h}_vox.npz")


def voxelize_one_file(filename, spacing, first_obj_id):
    """Voxelize every mesh in one model file. Object IDs continue from
    first_obj_id so several inputs merge without clashing.

    Speed-ups (same particles either way):
      - meshes are voxelized in parallel on all CPU cores but one
      - the result is cached in output\\cache\\ per file + spacing, so running
        the same model again skips voxelization entirely"""
    cache = None
    if fastio.CACHE_ENABLED:
        try:
            cache = _vox_cache_path(filename, spacing)
            if os.path.exists(cache):
                z = np.load(cache)
                xyz, local = z["xyz"], z["local"]
                n_obj = int(z["n_obj"])
                print(f"Loading: {filename}...")
                print(f"  [cache] voxels for spacing {spacing} m loaded from "
                      f"output\\cache ({len(xyz):,} particles, {n_obj} objects)")
                return xyz, local + first_obj_id, first_obj_id + n_obj
        except Exception:
            cache = cache if cache else None

    meshes = get_meshes_from_file(filename)
    jobs = [(m, spacing) for m in meshes]
    results = None
    cpus = os.cpu_count() or 1
    workers = cpus - 1 if cpus > 2 else cpus
    if PARALLEL and len(jobs) > 1 and workers > 1:
        try:
            from concurrent.futures import ProcessPoolExecutor
            print(f"  -> voxelizing {len(jobs)} meshes on {min(workers, len(jobs))} "
                  f"CPU cores...")
            with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as ex:
                results = list(ex.map(_voxelize_mesh, jobs, chunksize=1))
        except Exception as exc:                                # noqa: BLE001
            print(f"  [note] parallel voxelizing unavailable ({exc}); "
                  f"running on one core")
            results = None
    if results is None:
        results = [_voxelize_mesh(j) for j in jobs]

    chunks_xyz, chunks_id = [], []
    obj_id = first_obj_id
    for points, watertight, err in results:
        if not watertight:
            print(f"  [Warning] Object {obj_id} is not watertight. "
                  f"Filling might be partial.")
        if err.startswith("RECOVERED|"):
            print(f"  [note] Object {obj_id}: trimesh's voxelizer failed, used "
                  f"the ray fallback instead ({err.split('|', 1)[1]})")
        elif points is None:
            print(f"  [Error] Voxelizing Object {obj_id}: {err}")
            print(f"  !! THIS PART IS MISSING FROM THE SCENE - the run carries "
                  f"on without it. Check the particle count.")
        if points is None:
            continue
        if len(points) == 0:
            print(f"  [Warning] Object {obj_id} generated 0 points "
                  f"(maybe too small for spacing?).")
            continue
        chunks_xyz.append(points)
        chunks_id.append(np.full(len(points), obj_id, dtype=np.int64))
        obj_id += 1

    if not chunks_xyz:
        return np.empty((0, 3)), np.empty(0, dtype=np.int64), obj_id
    xyz, ids = np.vstack(chunks_xyz), np.concatenate(chunks_id)
    if cache:
        try:
            os.makedirs(fastio.CACHE_DIR, exist_ok=True)
            np.savez(cache, xyz=xyz, local=ids - first_obj_id,
                     n_obj=obj_id - first_obj_id)
        except Exception:
            pass
    return xyz, ids, obj_id


def load_cloud_file(filename, first_obj_id):
    """Read an existing point cloud. Accepts 4, 5 or 6 columns; missing
    G_type / Wall columns are filled with the defaults. Object IDs are
    renumbered to continue from first_obj_id so two clouds never collide."""
    print(f"Loading point cloud: {filename}...")
    data = fastio.load_table(filename)
    if data.shape[1] < 4:
        print(f"  [Error] expected at least 4 columns (X Y Z Object_ID), "
              f"got {data.shape[1]} - skipped.")
        return np.empty((0, 3)), np.empty(0, np.int64), np.empty(0, np.int64), \
               np.empty(0, np.int64), first_obj_id

    xyz = data[:, :3].astype(np.float64)
    raw_obj = data[:, 3].astype(np.int64)
    gtype = (data[:, 4].astype(np.int64) if data.shape[1] > 4
             else np.full(len(data), DEFAULT_GTYPE, np.int64))
    wall = (data[:, 5].astype(np.int64) if data.shape[1] > 5
            else np.full(len(data), DEFAULT_WALL, np.int64))

    # Renumber Object IDs so two inputs never collide - but leave any
    # non-positive ID exactly as it is. Values like -100 are markers (a fixed
    # boundary, a static body); renumbering one to 1 silently throws that
    # meaning away.
    # (vectorised: a per-particle dict lookup took ~10 s on 4 M points)
    uniq, inverse = np.unique(raw_obj, return_inverse=True)
    positives = [int(o) for o in uniq if o > 0]
    markers = [int(o) for o in uniq if o <= 0]
    new_ids = uniq.copy()
    pos_mask = uniq > 0
    new_ids[pos_mask] = first_obj_id + np.arange(int(pos_mask.sum()))
    obj = new_ids[inverse.ravel()].astype(np.int64)
    next_id = first_obj_id + len(positives)

    if positives:
        print(f"  -> {len(xyz):,} particles, {data.shape[1]} columns, "
              f"{len(positives)} object(s) renumbered to "
              f"{first_obj_id}-{next_id - 1}.")
    else:
        print(f"  -> {len(xyz):,} particles, {data.shape[1]} columns.")
    if markers:
        print(f"  -> Object_ID {', '.join(str(m) for m in markers)} kept as-is "
              f"(non-positive IDs are treated as markers, not renumbered).")
    if data.shape[1] < 6:
        print(f"  -> columns 5/6 missing, filled with G_type={DEFAULT_GTYPE}, "
              f"Wall={DEFAULT_WALL}.")
    return xyz, obj, gtype, wall, next_id


# ----------------------------------------------------------------------
# STEP 2 -- lay the bodies out so they can never collide
# ----------------------------------------------------------------------
def place_on_surface(bodies, gap_m, align, at_xy, spacing):
    """Body 1 is the ground. Every later body is moved INSIDE the ground's
    footprint and lowered until it rests on the actual surface underneath it,
    plus the gap.

    The drop uses a height map rather than bounding boxes, so a body lands on
    the terrain where it actually stands - on a hill, in a crater, on top of
    whatever was placed before it - instead of floating above the highest
    point of the whole scene, or being pushed off the end of it."""
    ground = bodies[0]
    ground["offset"] = np.zeros(3)
    g_lo = ground["points"].min(axis=0)
    g_hi = ground["points"].max(axis=0)
    print(f"  ground '{ground['name']}' covers X {g_lo[0]:.3f}..{g_hi[0]:.3f}, "
          f"Y {g_lo[1]:.3f}..{g_hi[1]:.3f}, surface Z {g_lo[2]:.3f}..{g_hi[2]:.3f}"
          f"  (--at uses these coordinates)")

    # cell size: the voxel pitch is the natural resolution, but clamp the grid
    # so an enormous terrain cannot blow up memory
    cell = max(spacing, max(g_hi[0] - g_lo[0], g_hi[1] - g_lo[1]) / 4000.0)

    placed = [ground["points"]]                    # everything already in the scene
    notes = []

    for b in bodies[1:]:
        pts = b["points"]
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        size = hi - lo

        # --- 1. horizontal position, forced to stay inside the ground -----
        move = np.zeros(3)
        if align == "keep":
            target = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2])
        elif at_xy is not None:
            target = np.asarray(at_xy, dtype=float)
        else:                                       # centre of the ground
            target = np.array([(g_lo[0] + g_hi[0]) / 2, (g_lo[1] + g_hi[1]) / 2])

        for ax in (0, 1):
            half = size[ax] / 2.0
            room_lo, room_hi = g_lo[ax] + half, g_hi[ax] - half
            if room_lo > room_hi:                   # body wider than the ground
                notes.append(f"'{b['name']}' is wider than the ground along "
                             f"{AXIS_NAME[ax]} ({size[ax]:.3f} m vs "
                             f"{g_hi[ax] - g_lo[ax]:.3f} m) - centred, it will "
                             f"overhang")
                clamped = (g_lo[ax] + g_hi[ax]) / 2.0
            else:
                clamped = float(np.clip(target[ax], room_lo, room_hi))
                if at_xy is not None and abs(clamped - target[ax]) > 1e-9:
                    notes.append(f"--at {AXIS_NAME[ax]}={target[ax]:g} would put "
                                 f"'{b['name']}' off the edge; moved to "
                                 f"{clamped:.3f} so it stays on the ground "
                                 f"(usable range {room_lo:.3f} to {room_hi:.3f})")
            move[ax] = clamped - (lo[ax] + hi[ax]) / 2.0

        moved_xy = pts[:, :2] + move[:2]

        # --- 2. drop it onto whatever is underneath -----------------------
        fx_lo, fy_lo = moved_xy.min(axis=0)
        fx_hi, fy_hi = moved_xy.max(axis=0)
        nx = max(1, int(np.ceil((fx_hi - fx_lo) / cell)) + 1)
        ny = max(1, int(np.ceil((fy_hi - fy_lo) / cell)) + 1)

        def cells(xy):
            ix = np.clip(((xy[:, 0] - fx_lo) / cell).astype(np.int64), 0, nx - 1)
            iy = np.clip(((xy[:, 1] - fy_lo) / cell).astype(np.int64), 0, ny - 1)
            return ix * ny + iy

        # highest existing surface in each cell of the footprint
        surface = np.full(nx * ny, -np.inf)
        for prev in placed:
            m = ((prev[:, 0] >= fx_lo - cell) & (prev[:, 0] <= fx_hi + cell) &
                 (prev[:, 1] >= fy_lo - cell) & (prev[:, 1] <= fy_hi + cell))
            if not m.any():
                continue
            sub = prev[m]
            np.maximum.at(surface, cells(sub[:, :2]), sub[:, 2])

        # lowest point of the incoming body in each cell
        under = np.full(nx * ny, np.inf)
        np.minimum.at(under, cells(moved_xy), pts[:, 2])

        both = np.isfinite(surface) & np.isfinite(under)
        if both.any():
            rise = float((surface[both] - under[both]).max())
            contact = int(both.sum())
        else:
            # nothing underneath at all - rest on the top of the ground bbox
            rise = float(g_hi[2] - lo[2])
            contact = 0
            notes.append(f"'{b['name']}' has no ground directly beneath it; "
                         f"resting it on the ground's top plane instead")
        move[2] = rise + gap_m

        b["offset"] = move
        b["_contact_cells"] = contact
        placed.append(pts + move)

    for n in notes:
        print(f"  [note] {n}")

    return (f"dropped onto the surface of '{ground['name']}', "
            f"{gap_m * 1000:.1f} mm clearance, horizontal placement '{align}'")


def plan_layout(bodies, mode, gap_m, align):
    """Place each body along the layout axis with a fixed gap between
    bounding boxes. For stack-z the first body rests on Z=0 and each
    subsequent one sits on top of the previous. Adds 'offset' to each body."""
    if mode == "none":
        for b in bodies:
            b["offset"] = np.zeros(3)
        return "none (raw input coordinates kept)"
    if mode == "ground-only":
        for b in bodies:
            b["offset"] = np.array([0.0, 0.0, -b["points"][:, 2].min()])
        return "single body rested on Z=0"

    axis = LAYOUT_AXIS[mode]
    others = [i for i in (0, 1, 2) if i != axis]

    for b in bodies:
        lo = b["points"].min(axis=0)
        hi = b["points"].max(axis=0)
        b["size"] = hi - lo
        local = np.zeros(3)
        for ax in others:
            if ax == 2:
                local[2] = -lo[2]                    # always rest on the ground
            elif align == "center":
                local[ax] = -0.5 * (lo[ax] + hi[ax])
            elif align == "corner":
                local[ax] = -lo[ax]
            # align == "keep": leave it alone
        b["_axis_lo"] = lo[axis]
        b["_local"] = local

    cursor = 0.0
    for i, b in enumerate(bodies):
        if i > 0:
            cursor += gap_m
        offset = b["_local"].copy()
        offset[axis] = cursor - b["_axis_lo"]        # put this body's base at cursor
        b["offset"] = offset
        cursor += b["size"][axis]

    if mode != "stack-z":
        # a horizontal row reads better centred on the origin; a stack should
        # stay standing on the ground plane
        shift = -cursor / 2.0
        for b in bodies:
            b["offset"][axis] += shift

    for b in bodies:
        del b["_local"], b["_axis_lo"]

    what = ("stacked bottom to top along +Z" if mode == "stack-z"
            else f"{mode} along +{AXIS_NAME[axis]}")
    return (f"{what}, {gap_m * 1000:.1f} mm gap, horizontal alignment "
            f"'{align}', bottom body on Z=0")


def verify_surface_clearance(bodies, gap_m, spacing):
    """For on-surface placement the bodies deliberately share Z ranges, so a
    bounding-box test says nothing. Measure the real vertical clearance in the
    cells where each body actually sits over something."""
    if len(bodies) < 2:
        return True, ""
    ground = bodies[0]["points"] + bodies[0]["offset"]
    g_lo, g_hi = ground.min(axis=0), ground.max(axis=0)
    cell = max(spacing, max(g_hi[0] - g_lo[0], g_hi[1] - g_lo[1]) / 4000.0)

    placed = [ground]
    worst = None
    for b in bodies[1:]:
        pts = b["points"] + b["offset"]
        fx_lo, fy_lo = pts[:, 0].min(), pts[:, 1].min()
        fx_hi, fy_hi = pts[:, 0].max(), pts[:, 1].max()
        nx = max(1, int(np.ceil((fx_hi - fx_lo) / cell)) + 1)
        ny = max(1, int(np.ceil((fy_hi - fy_lo) / cell)) + 1)

        def cells(xy):
            ix = np.clip(((xy[:, 0] - fx_lo) / cell).astype(np.int64), 0, nx - 1)
            iy = np.clip(((xy[:, 1] - fy_lo) / cell).astype(np.int64), 0, ny - 1)
            return ix * ny + iy

        surface = np.full(nx * ny, -np.inf)
        for prev in placed:
            m = ((prev[:, 0] >= fx_lo - cell) & (prev[:, 0] <= fx_hi + cell) &
                 (prev[:, 1] >= fy_lo - cell) & (prev[:, 1] <= fy_hi + cell))
            if m.any():
                sub = prev[m]
                np.maximum.at(surface, cells(sub[:, :2]), sub[:, 2])
        under = np.full(nx * ny, np.inf)
        np.minimum.at(under, cells(pts[:, :2]), pts[:, 2])

        both = np.isfinite(surface) & np.isfinite(under)
        if both.any():
            clear = float((under[both] - surface[both]).min())
            if worst is None or clear < worst[0]:
                worst = (clear, b["name"])
            if clear < -1e-9:
                return False, (f"'{b['name']}' sinks {-clear * 1000:.2f} mm "
                               f"into the surface below it")
        placed.append(pts)

    if worst is None:
        return True, "no body overlaps another in plan view"
    return True, (f"smallest vertical clearance {worst[0] * 1000:.1f} mm "
                  f"under '{worst[1]}'")


def verify_no_collision(bodies, mode):
    if mode == "none" or len(bodies) < 2:
        return True, ""
    axis = LAYOUT_AXIS[mode]
    spans = []
    for b in bodies:
        pts = b["points"] + b["offset"]
        spans.append((pts[:, axis].min(), pts[:, axis].max(), b["name"]))
    spans.sort(key=lambda s: s[0])

    worst = None
    for (lo_a, hi_a, name_a), (lo_b, hi_b, name_b) in zip(spans, spans[1:]):
        clearance = lo_b - hi_a
        if worst is None or clearance < worst[0]:
            worst = (clearance, name_a, name_b)
        if clearance <= 0:
            return False, (f"'{name_a}' and '{name_b}' overlap by "
                           f"{-clearance * 1000:.2f} mm along {AXIS_NAME[axis]}")
    return True, (f"smallest clearance {worst[0] * 1000:.1f} mm "
                  f"between '{worst[1]}' and '{worst[2]}'")


def build_particle_cloud(inputs, spacing, mode, gap_m, align, up, gtype, wall,
                         at_xy=None, flip=False, yaw=0.0):
    print(f"--- STEP 1/5: Loading {len(inputs)} input(s) ---")
    print(f"Spacing: {spacing} m")

    bodies = []
    next_id = 1
    for filename in inputs:
        start_id = next_id
        if is_cloud(filename):
            points, ids, g, w, next_id = load_cloud_file(filename, start_id)
            source_kind = "cloud"
        else:
            points, ids, next_id = voxelize_one_file(filename, spacing, start_id)
            g = np.full(len(points), gtype, np.int64)
            w = np.full(len(points), wall, np.int64)
            source_kind = "model"
            if len(points):
                print(f"  -> {os.path.basename(filename)}: {len(points):,} particles "
                      f"in {next_id - start_id} object(s), "
                      f"IDs {start_id}-{next_id - 1}.")

        if len(points) == 0:
            print(f"  [Warning] '{os.path.basename(filename)}' produced no "
                  f"particles - skipped.")
            continue

        bodies.append({
            "name": os.path.basename(filename),
            "path": filename,
            "kind": source_kind,
            "points": points,
            "ids": ids,
            "gtype": g,
            "wall": w,
            "id_range": (start_id, next_id - 1) if next_id > start_id else None,
        })

    if not bodies:
        return np.empty((0, 6)), [], "z"

    # --- one up-axis rotation for the WHOLE run -------------------------
    if up == "auto":
        up_axis, up_sign, why = detect_up_axis(bodies, inputs,
                                               _interactive())
        label = ("-" if up_sign < 0 else "") + up_axis.upper()
        print(f"Up axis: {label} - {why}.")
    else:
        up_sign = -1 if up.startswith("-") else 1
        up_axis = up.lstrip("+-")
        label = ("-" if up_sign < 0 else "") + up_axis.upper()
        print(f"Up axis: {label} (from --up)")

    if flip:
        up_sign = -up_sign
        label = ("-" if up_sign < 0 else "") + up_axis.upper()
        print(f"  --flip: turning the scene over, up axis is now {label}")
    print("  If the ground comes out upside down, re-run with --flip")

    if up_axis != "z" or up_sign < 0:
        for b in bodies:
            b["points"] = rotate_to_z_up(b["points"], up_axis, up_sign)
        turned = ", and the scene turned over" if up_sign < 0 else ""
        print(f"  -> every input rotated so {label} becomes +Z{turned}; "
              f"models stand upright and terrain lies flat.")

    if yaw:
        for b in bodies[1:]:
            b["points"] = rotate_yaw(b["points"], yaw)
        print(f"  -> yaw {yaw:g} deg applied on the spot to "
              + ", ".join(b["name"] for b in bodies[1:]))

    print(f"--- STEP 2/5: Placing {len(bodies)} body/bodies ---")
    if mode == "on-surface" and len(bodies) >= 2:
        desc = place_on_surface(bodies, gap_m, align, at_xy, spacing)
        print(f"Layout: {desc}")
        ok, msg = verify_surface_clearance(bodies, gap_m, spacing)
    else:
        if mode == "on-surface":
            mode = "ground-only" if len(bodies) < 2 else "stack-z"
        desc = plan_layout(bodies, mode, gap_m, align)
        print(f"Layout: {desc}")
        ok, msg = verify_no_collision(bodies, mode)
    if not ok:
        sys.exit(f"CRITICAL ERROR: layout failed - {msg}")
    if msg:
        print(f"Clearance check: PASSED - {msg}")

    stacked = []
    for b in bodies:
        moved = b["points"] + b["offset"]
        b["final_bounds"] = (moved.min(axis=0), moved.max(axis=0))
        b["n_particles"] = len(moved)
        stacked.append(np.column_stack([moved, b["ids"], b["gtype"], b["wall"]]))
        del b["points"], b["ids"], b["gtype"], b["wall"]

    final = np.vstack(stacked)
    print(f"Total Particles: {len(final):,}")
    return final, bodies, up_axis


def write_particles(final_data, output_file, precision=6):
    """Six columns, because produce_ic_mesh.f90 reads six values per line."""
    fastio.write_particles(output_file, final_data, precision)
    print(f"Saved to '{output_file}'  "
          f"(Format: X Y Z Object_ID G_type Wall)")


def write_manifest(path, bodies, cfg, spacing, density, layout_desc, total,
                   run_dir, engine="fortran", yaw_deg=0.0):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("3D VOX - run manifest\n")
        fh.write("=" * 78 + "\n")
        fh.write(f"generated       : {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        fh.write(f"output folder   : {run_dir}\n")
        fh.write(f"spacing         : {spacing} m\n")
        fh.write(f"density         : {density} kg/m^3\n")
        fh.write(f"particle mass   : {spacing ** 3 * density:.6e} kg\n")
        fh.write(f"layout          : {layout_desc}\n")
        if yaw_deg:
            fh.write(f"yaw             : {yaw_deg:g} deg on every body after the first\n")
        fh.write(f"inputs merged   : {len(bodies)}\n")
        fh.write(f"total particles : {total}\n")
        if engine == "native":
            fh.write("generator       : native Python writer (fastio.py), same "
                     "record layout as produce_ic_mesh.f90\n")
        else:
            fh.write(f"generator       : {os.path.basename(cfg['source'])} "
                     f"(reads {cfg['read_columns']} columns, "
                     f"{'takes' if cfg['supports_args'] else 'ignores'} "
                     f"command-line arguments)\n")
        fh.write("=" * 78 + "\n\n")

        fh.write(f"{'source file':<32}{'kind':<8}{'object IDs':>13}"
                 f"{'particles':>12}{'  offset applied (m)':>28}\n")
        fh.write("-" * 92 + "\n")
        for b in bodies:
            rng = f"{b['id_range'][0]}-{b['id_range'][1]}" if b["id_range"] else "markers"
            off = b["offset"]
            fh.write(f"{b['name'][:31]:<32}{b['kind']:<8}{rng:>13}"
                     f"{b['n_particles']:>12}"
                     f"   ({off[0]:+.4f}, {off[1]:+.4f}, {off[2]:+.4f})\n")

        fh.write("\n" + "-" * 92 + "\n")
        fh.write("final bounding boxes after layout (metres), bottom of the stack first\n")
        fh.write("-" * 92 + "\n")
        for b in sorted(bodies, key=lambda x: x["final_bounds"][0][2]):
            lo, hi = b["final_bounds"]
            fh.write(f"{b['name'][:31]:<32}"
                     f"X {lo[0]:+.4f}..{hi[0]:+.4f}   "
                     f"Y {lo[1]:+.4f}..{hi[1]:+.4f}   "
                     f"Z {lo[2]:+.4f}..{hi[2]:+.4f}\n")
    print(f"Manifest written to '{path}'")


# ----------------------------------------------------------------------
# STEP 3 -- make sure the Fortran generator is compiled
# ----------------------------------------------------------------------
def fortran_status():
    """(exe path or None, 'ok' | 'stale' | 'missing', gfortran path or None)"""
    source = os.path.join(HERE, FORTRAN_SOURCE)
    exe_name = "produce_ic.exe" if os.name == "nt" else "produce_ic"
    exe_path = os.path.join(HERE, exe_name)
    gf = shutil.which("gfortran")
    if not os.path.exists(exe_path):
        return None, "missing", gf
    if os.path.exists(source) and os.path.getmtime(exe_path) < os.path.getmtime(source):
        return exe_path, "stale", gf
    return exe_path, "ok", gf


def ensure_fortran_binary(force=False):
    source = os.path.join(HERE, FORTRAN_SOURCE)
    exe_name = "produce_ic.exe" if os.name == "nt" else "produce_ic"
    exe_path = os.path.join(HERE, exe_name)

    up_to_date = (
        not force
        and os.path.exists(exe_path)
        and os.path.exists(source)
        and os.path.getmtime(exe_path) >= os.path.getmtime(source)
    )
    if up_to_date:
        print("--- STEP 3/5: Fortran binary is up to date (skipping compile) ---")
        return exe_path

    gfortran = shutil.which("gfortran")
    if gfortran is None:
        if os.path.exists(exe_path):
            print("--- STEP 3/5: gfortran not found ---")
            print("  !! WARNING: produce_ic_mesh.f90 is NEWER than the compiled")
            print("     binary, but gfortran is not on the PATH. The OLD binary")
            print("     will be used, so your edits to the .f90 are NOT active.")
            print("     Install gfortran (MinGW-w64 on Windows) and run again.")
            return exe_path
        sys.exit(
            "CRITICAL ERROR: gfortran is not installed / not on PATH and no "
            "compiled 'produce_ic' binary exists.\n"
            "Install gfortran (e.g. via MinGW-w64 on Windows, "
            "'sudo apt install gfortran' on Linux, 'brew install gcc' on macOS) "
            "and run again."
        )

    print("--- STEP 3/5: Compiling Fortran generator ---")
    cmd = [gfortran, "-O2", "-o", exe_path, source]
    print("  " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"CRITICAL ERROR: Fortran compilation failed:\n{result.stderr}")
    print(f"  -> Built {exe_name}")
    return exe_path


# ----------------------------------------------------------------------
# STEP 4 -- run the generator
# ----------------------------------------------------------------------
def run_fortran(cfg, exe_path, run_dir, particles_path, output_path,
                control_path, spacing, density):
    print("--- STEP 4/5: Generating GADGET-2 IC file ---")

    if cfg["supports_args"]:
        cmd = [exe_path, particles_path, output_path,
               f"{spacing:.10g}", f"{density:.10g}", control_path]
        result = subprocess.run(cmd, cwd=HERE)
        if result.returncode != 0:
            sys.exit("CRITICAL ERROR: IC generation failed (see messages above).")
        return

    # The .f90 has no command-line arguments: it reads a fixed filename from
    # the current directory and writes fixed filenames back. So run it INSIDE
    # the run folder, where the point cloud already has the name it wants,
    # then rename its output to what the rest of the pipeline expects.
    wanted_in = cfg["input_points"]
    src_name = os.path.basename(particles_path)
    if src_name != wanted_in:
        shutil.copyfile(particles_path, os.path.join(run_dir, wanted_in))
        print(f"  (copied {src_name} -> {wanted_in}, the name the .f90 expects)")

    print(f"  running {os.path.basename(exe_path)} in {run_dir}")
    result = subprocess.run([exe_path], cwd=run_dir)
    if result.returncode != 0:
        sys.exit("CRITICAL ERROR: IC generation failed (see messages above).")

    produced = os.path.join(run_dir, cfg["outputfile"])
    if not os.path.exists(produced):
        sys.exit(f"CRITICAL ERROR: the generator did not write "
                 f"'{cfg['outputfile']}' in {run_dir}.")
    if os.path.abspath(produced) != os.path.abspath(output_path):
        shutil.move(produced, output_path)
        print(f"  renamed {cfg['outputfile']} -> {os.path.basename(output_path)}")

    made_control = os.path.join(run_dir, cfg["outcontrol"])
    if (os.path.exists(made_control)
            and os.path.abspath(made_control) != os.path.abspath(control_path)):
        shutil.move(made_control, control_path)


# ----------------------------------------------------------------------
# STEP 5 -- preview
# ----------------------------------------------------------------------
def make_preview(ic_path, open_it):
    print("--- STEP 5/5: Drawing preview ---")
    try:
        sys.path.insert(0, HERE)
        import view_particles
    except Exception as exc:
        print(f"  [skipped] could not load view_particles.py ({exc})")
        return None
    try:
        data = view_particles.load_any(ic_path)
        png = os.path.join(os.path.dirname(ic_path), "preview.png")
        view_particles.draw(data, png, 300000, "body", False, True)
    except (ImportError, SystemExit):
        print("  [skipped] matplotlib is not installed - run "
              "'pip install matplotlib' to get preview images.")
        return None
    except Exception as exc:
        print(f"  [skipped] preview failed: {exc}")
        return None

    if open_it:
        try:
            if os.name == "nt":
                os.startfile(png)                                  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", png])
            else:
                subprocess.Popen(["xdg-open", png],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except Exception:
            pass
    return png


# ----------------------------------------------------------------------
# command line  /  the entry point the GUI calls
# ----------------------------------------------------------------------
def build_parser(cfg=None):
    cfg = cfg or read_fortran_config()
    parser = argparse.ArgumentParser(
        description="3D models and/or point clouds -> one GADGET-2 IC file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("models", nargs="*",
                        help="3D models (GLB/STL/OBJ/...) and/or point clouds "
                             "(.dat). With none you get a menu.")
    parser.add_argument("--engine", choices=("native", "fortran"),
                        default="native",
                        help="native: write the IC straight from Python "
                             "(fast, no gfortran, same bytes). fortran: go "
                             "through produce_ic.exe (the reference)")
    parser.add_argument("--spacing", type=float, default=None,
                        help="particle spacing in metres "
                             f"(default: {cfg['spacing']}, read from the .f90)")
    parser.add_argument("--density", type=float, default=None,
                        help="material density in kg/m^3 "
                             f"(default: {cfg['density']}, read from the .f90)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="name of the final IC file")
    parser.add_argument("--all", action="store_true",
                        help="skip the picker and use every available input")
    parser.add_argument("--layout",
                        choices=("on-surface", "stack-z", "row-y", "row-x", "none"),
                        default="on-surface",
                        help="on-surface: body 1 is the ground, the rest are "
                             "dropped onto it inside its footprint")
    parser.add_argument("--gap", type=float, default=None,
                        help="gap between bodies in MILLIMETRES "
                             "(you are asked if you do not pass this)")
    parser.add_argument("--align", choices=("center", "keep", "corner"),
                        default=None,
                        help="where on the ground the bodies go")
    parser.add_argument("--at", default=None, metavar="X,Y",
                        help="place bodies at this spot on the ground, in "
                             "metres, e.g. --at 2.5,4.0")
    parser.add_argument("--up",
                        choices=("auto", "x", "y", "z", "-x", "-y", "-z"),
                        default="auto",
                        help="which axis is up in the source files. A minus "
                             "sign means that axis points DOWN. Write --up=-y, "
                             "or just use --flip")
    parser.add_argument("--yaw", type=float, default=0.0, metavar="DEG",
                        help="spin every body after the first about the "
                             "vertical axis, on the spot. --yaw 90 turns a "
                             "rover to face along the ramp instead of across it")
    parser.add_argument("--flip", action="store_true",
                        help="turn the whole scene over. The simple fix when "
                             "the ground comes out upside down")
    parser.add_argument("--gtype", type=int, default=DEFAULT_GTYPE,
                        help="value written to column 5 for voxelized models")
    parser.add_argument("--wall", type=int, default=DEFAULT_WALL,
                        help="value written to column 6 for voxelized models")
    parser.add_argument("--keep-coords", action="store_true",
                        help="no recentring, no grounding, no layout")
    parser.add_argument("--lean", action="store_true",
                        help="save disk: keep only the IC, manifest and "
                             "preview (drops particles.dat and the control file)")
    parser.add_argument("--no-particles", dest="keep_particles",
                        action="store_false",
                        help="do not keep particles.dat in the run folder")
    parser.add_argument("--control", dest="keep_control", action="store_true",
                        default=None,
                        help="write probe3D_mesh.txt (default: yes for the "
                             "fortran engine, no for native - it is ~300 MB "
                             "on big runs)")
    parser.add_argument("--no-control", dest="keep_control",
                        action="store_false")
    parser.add_argument("--no-cache", action="store_true",
                        help="do not use / write the output\\cache speed-up "
                             "for big point clouds")
    parser.add_argument("--preset", default=None,
                        help="load a saved preset (see --list-presets)")
    parser.add_argument("--save-preset", default=None, metavar="NAME",
                        help="save this run's settings under NAME")
    parser.add_argument("--list-presets", action="store_true")
    parser.add_argument("--serial", action="store_true",
                        help="voxelize on one CPU core only")
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    return parser


def parse_args(argv=None, cfg=None):
    """Parse argv, with a --preset filling in defaults first so anything
    typed on the command line still wins."""
    cfg = cfg or read_fortran_config()
    parser = build_parser(cfg)
    pre, _ = parser.parse_known_args(argv)
    if pre.list_presets:
        presets = load_settings()["presets"]
        if not presets:
            print("No presets saved yet. Use --save-preset NAME on a run.")
        for name, vals in presets.items():
            print(f"  {name:<20} " + "  ".join(f"{k}={v}" for k, v in vals.items()))
        sys.exit(0)
    if pre.preset:
        presets = load_settings()["presets"]
        if pre.preset not in presets:
            sys.exit(f"CRITICAL ERROR: no preset called '{pre.preset}'. "
                     f"Saved: {', '.join(presets) or 'none'}")
        parser.set_defaults(**{k: v for k, v in presets[pre.preset].items()
                               if k in PRESET_KEYS})
        print(f"[preset] '{pre.preset}' loaded")
    return parser.parse_args(argv)


def _parse_at(text):
    try:
        parts = [float(v) for v in text.replace(";", ",").split(",")]
        if len(parts) != 2:
            raise ValueError
        return parts
    except ValueError:
        sys.exit("CRITICAL ERROR: --at wants two numbers, e.g. --at 2.5,4.0")


def run(args, cfg=None):
    """Wrapper: a run that fails leaves no empty/half-written folder behind."""
    made = {}
    try:
        return _run(args, cfg, made)
    except BaseException:
        rd = made.get("run_dir")
        if rd and os.path.isdir(rd) and not os.path.exists(
                os.path.join(rd, getattr(args, "output", DEFAULT_OUTPUT))):
            shutil.rmtree(rd, ignore_errors=True)
            print(f"  (removed the unfinished run folder {os.path.basename(rd)})")
        raise


def _run(args, cfg, made):
    """Do one pipeline run. Returns a dict describing what was made.
    Errors end in SystemExit with a readable message (the GUI catches it)."""
    t_start = time.time()
    cfg = cfg or read_fortran_config()
    fastio.CACHE_ENABLED = not getattr(args, "no_cache", False)
    global PARALLEL
    PARALLEL = not getattr(args, "serial", False)

    if args.keep_coords:
        args.layout = "none"
    if args.lean:
        args.keep_particles = False
        args.keep_control = False
    if args.keep_control is None:
        args.keep_control = (args.engine == "fortran")

    at_xy = _parse_at(args.at) if args.at else None

    # --- spacing / density ------------------------------------------------
    spacing = args.spacing if args.spacing is not None else cfg["spacing"]
    density = args.density if args.density is not None else cfg["density"]
    if args.engine == "fortran" and not cfg["supports_args"]:
        mismatch = []
        if abs(spacing - cfg["spacing"]) > 1e-12:
            mismatch.append(f"spacing {spacing} vs {cfg['spacing']} in the .f90")
        if abs(density - cfg["density"]) > 1e-9:
            mismatch.append(f"density {density} vs {cfg['density']} in the .f90")
        if mismatch:
            sys.exit(
                "CRITICAL ERROR: " + "; ".join(mismatch) + ".\n"
                f"{os.path.basename(cfg['source'])} hard-codes these values, so "
                f"the mass it writes would NOT match the grid.\n"
                f"Either use the native engine (it takes any spacing/density) "
                f"or edit spacing_abstand / density_standard in the .f90.")

    inputs = choose_inputs(args)
    missing = [m for m in inputs if not os.path.exists(m)]
    if missing:
        sys.exit("CRITICAL ERROR: input file(s) not found: " + ", ".join(missing))

    gap_mm, align = prompt_for_layout(args, len(inputs))
    gap_m = gap_mm / 1000.0
    if args.at and at_xy is None:          # the prompt may have set it
        at_xy = _parse_at(args.at)

    # remember what was used, so the next run needs no typing
    settings = load_settings()
    used = {k: getattr(args, k, None) for k in PRESET_KEYS}
    used["gap"], used["align"] = gap_mm, align
    settings["last"] = used
    if args.save_preset:
        settings["presets"][args.save_preset] = used
        print(f"[preset] saved as '{args.save_preset}'")
    save_settings(settings)

    run_dir = make_run_dir(inputs)
    made["run_dir"] = run_dir
    particles_path = os.path.join(run_dir, cfg["input_points"])
    output_path    = os.path.join(run_dir, args.output)
    control_path   = os.path.join(run_dir, cfg["outcontrol"])
    manifest_path  = os.path.join(run_dir, MANIFEST_FILE)

    print()
    print("=========================================")
    print(" 3D VOX  ->  GADGET-2 IC : ONE-SHOT MODE ")
    print("=========================================")
    print("Inputs        : " + ", ".join(os.path.basename(m) for m in inputs))
    print("Output folder : " + run_dir)
    print(f"Engine        : {args.engine}"
          + ("  (Python writes the IC directly)" if args.engine == "native"
             else f"  ({os.path.basename(cfg['source'])}, reads "
                  f"{cfg['read_columns']} columns)"))
    print(f"Spacing       : {spacing} m     Density: {density} kg/m^3")
    print()

    timings = []
    t = time.time()
    final_data, bodies, up_axis = build_particle_cloud(
        inputs, spacing, args.layout, gap_m, align, args.up,
        args.gtype, args.wall, at_xy, args.flip, args.yaw)
    if len(final_data) == 0:
        sys.exit("CRITICAL ERROR: no particles were produced.")
    timings.append(("load + place", time.time() - t))

    layout_desc = ("none (raw input coordinates kept)"
                   if args.layout == "none"
                   else f"{args.layout}, {gap_mm:g} mm gap, align {align}")
    write_manifest(manifest_path, bodies, cfg, spacing, density,
                   layout_desc, len(final_data), run_dir, args.engine,
                   args.yaw)

    t = time.time()
    if args.engine == "native":
        print("--- STEP 3/5: Native writer (no Fortran needed) ---")
        if args.keep_particles:
            write_particles(final_data, particles_path)
        print("--- STEP 4/5: Writing GADGET-2 IC file ---")
        fastio.write_ic_native(output_path, final_data, spacing, density,
                               control_path=control_path if args.keep_control
                               else None)
        print(f"  -> {os.path.basename(output_path)}  "
              f"({fastio.human(os.path.getsize(output_path))})")
    else:
        write_particles(final_data, particles_path)
        exe_path = ensure_fortran_binary()
        run_fortran(cfg, exe_path, run_dir, particles_path, output_path,
                    control_path, spacing, density)
        if not args.keep_particles and os.path.exists(particles_path):
            os.remove(particles_path)
        if not args.keep_control and os.path.exists(control_path):
            os.remove(control_path)
    timings.append(("write IC", time.time() - t))

    preview_path = None
    if not args.no_preview:
        t = time.time()
        preview_path = make_preview(output_path, not args.no_open)
        timings.append(("preview", time.time() - t))

    total = time.time() - t_start
    run_bytes = sum(os.path.getsize(os.path.join(run_dir, f))
                    for f in os.listdir(run_dir))
    print()
    print("=========================================")
    print("PIPELINE COMPLETE")
    print(f"  Output folder  : {run_dir}")
    print(f"  Inputs merged  : {len(bodies)}")
    print(f"  Particles      : {len(final_data):,}")
    print(f"  IC file        : {output_path}")
    if args.keep_control and os.path.exists(control_path):
        print(f"  Control file   : {control_path}")
    if args.keep_particles and os.path.exists(particles_path):
        print(f"  Point cloud    : {particles_path}")
    print(f"  Manifest       : {manifest_path}")
    if preview_path:
        print(f"  Preview        : {preview_path}")
    print(f"  Disk used      : {fastio.human(run_bytes)}")
    print(f"  Time           : {total:.1f} s  (" +
          ", ".join(f"{k} {v:.1f} s" for k, v in timings) + ")")
    print("=========================================")

    return {"run_dir": run_dir, "ic": output_path, "preview": preview_path,
            "manifest": manifest_path, "particles": len(final_data),
            "bodies": len(bodies), "seconds": total}


def main(argv=None):
    cfg = read_fortran_config()
    args = parse_args(argv, cfg)
    run(args, cfg)


if __name__ == "__main__":
    main()
