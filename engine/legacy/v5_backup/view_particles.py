#!/usr/bin/env python3
"""
.ic  ->  .png     stand-alone viewer for the pipeline's output.

Turns a GADGET-2 IC file (or a particles.dat point cloud) into a PNG figure.
Works completely independently of run_pipeline.py - you can point it at any
.ic file, from any run, at any time.

    view_ic.bat                       double-click: pick a run from a menu
    drag a .ic onto view_ic.bat       renders it and opens the PNG

    python view_particles.py                          menu of runs in output/
    python view_particles.py path\to\test_mesh.ic     one specific file
    python view_particles.py a.ic b.ic c.ic           several at once
    python view_particles.py output\20260806_1445_x   a run folder
    python view_particles.py path\to\particles.dat    the point cloud instead

Output: <name>_preview.png next to the input - four panels (front, side, top,
3D) with every body in a different colour, plus the numbers that matter
printed to the console.

Useful flags
    --open               open each PNG when it is finished
    --interactive        rotatable 3D window instead of writing a PNG
    --max-points 300000  decimation cap; big runs are randomly thinned to this
    --color id|object|z  what drives the colour  (default: object)
    --export csv         write points.csv    -> drag into ParaView / Blender
    --export vtk         write points.vtk    -> ParaView, keeps Object IDs
    --export xyz         write points.xyz    -> MeshLab, CloudCompare
    --no-show-ids        skip the per-body legend when there are many bodies

Requires: numpy + matplotlib   (pip install matplotlib)
"""

import argparse
import os
import re
import struct
import subprocess
import sys

import numpy as np

HEADER_BYTES = 256

# --- where things live -------------------------------------------------
# This script sits in engine\; everything else hangs off the folder above.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = (os.path.dirname(HERE)
        if os.path.basename(HERE).lower() == "engine" else HERE)

IC_INBOX    = os.path.join(ROOT, "input", "ic")        # drop .ic files here
RUNS_ROOT   = os.path.join(ROOT, "output", "runs")
IMAGES_ROOT = os.path.join(ROOT, "output", "images")
POINTS_ROOT = os.path.join(ROOT, "output", "points")

# File names the pipeline always uses, so they say nothing about which run a
# file came from - the parent folder is the meaningful name in that case.
GENERIC_NAMES = {"test_mesh", "particles", "probe3D_mesh"}


def output_name(path, suffix, ext):
    """Build a distinguishing output name. test_mesh.ic inside a run folder
    becomes <run name>_preview.png, not a fourth copy of test_mesh_preview.png."""
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem in GENERIC_NAMES:
        parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
        if parent:
            stem = parent
    return f"{stem}{suffix}{ext}"


def unique_path(path, force):
    """Never silently overwrite: add _2, _3 ... unless --force was given."""
    if force or not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base}_{i}{ext}"):
        i += 1
    return f"{base}_{i}{ext}"


# ----------------------------------------------------------------------
# readers
# ----------------------------------------------------------------------
def _read_record(fh):
    """Read one Fortran unformatted sequential record. Returns bytes."""
    raw = fh.read(4)
    if len(raw) < 4:
        raise EOFError("unexpected end of file")
    n = struct.unpack("<i", raw)[0]
    body = fh.read(n)
    trailer = fh.read(4)
    if len(trailer) < 4:
        raise EOFError("truncated record")
    n2 = struct.unpack("<i", trailer)[0]
    if n != n2:
        raise ValueError(f"record marker mismatch ({n} vs {n2}) - "
                         f"is this really a GADGET-2 IC written by produce_ic?")
    return body


# The nine data records produce_ic_mesh.f90 writes after the header, in order.
# name, numpy dtype, components per particle
IC_RECORDS = [
    ("pos",    "<f4", 3),
    ("vel",    "<f4", 3),
    ("type",   "<i4", 1),      # written as `id` in the Fortran; constant 2
    ("mass",   "<f4", 1),
    ("u",      "<f4", 1),
    ("object", "<i4", 1),
    ("g_type", "<i4", 1),
    ("plate",  "<i4", 1),
    ("hinge",  "<i4", 1),
]


def read_ic(path, full=False):
    """Parse test_mesh.ic.

    Returns pos / object / mass / n / source always. With full=True also
    returns vel, type, u, g_type, plate and hinge, plus the decoded header."""
    out = {"source": path}
    with open(path, "rb") as fh:
        header = _read_record(fh)
        if len(header) != HEADER_BYTES:
            raise ValueError(f"header is {len(header)} bytes, expected {HEADER_BYTES}")
        npart = struct.unpack("<6i", header[0:24])
        n = npart[0]

        for name, dtype, ncomp in IC_RECORDS:
            body = _read_record(fh)
            wanted = full or name in ("pos", "mass", "object")
            if not wanted:
                continue
            arr = np.frombuffer(body, dtype=dtype)
            out[name] = arr.reshape(-1, ncomp) if ncomp > 1 else arr

        if full:
            out["header"] = {
                "npart": npart,
                "massarr": struct.unpack("<6d", header[24:72]),
                "a": struct.unpack("<d", header[72:80])[0],
                "redshift": struct.unpack("<d", header[80:88])[0],
                "flags": struct.unpack("<2i", header[88:96]),
                "nall": struct.unpack("<6i", header[96:120]),
                "unused_all_zero": not any(struct.unpack("<34i", header[120:256])),
            }
            trailing = fh.read()
            if trailing:
                raise ValueError(f"{len(trailing)} unexpected bytes after the last "
                                 f"record - the file may have extra data this "
                                 f"reader does not know about")

    if len(out["pos"]) != n:
        raise ValueError(f"header says {n} particles, position record holds "
                         f"{len(out['pos'])}")

    out["pos"] = out["pos"].astype(np.float64)
    out["object"] = out["object"].astype(np.int64)
    out["mass"] = out["mass"].astype(np.float64)
    out["n"] = n
    return out


def read_dat(path):
    """Parse particles.dat. Columns beyond the first four are ignored here -
    ic_to_points.py is what reads and writes the wide form."""
    data = np.loadtxt(path)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if data.shape[1] < 4:
        raise ValueError(f"expected at least 4 columns (X Y Z Object_ID), "
                         f"got {data.shape[1]}")
    return {"pos": data[:, :3], "object": data[:, 3].astype(np.int64),
            "mass": None, "n": len(data), "source": path}


def load_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".ic":
        return read_ic(path)
    if ext in (".dat", ".txt", ".csv", ".xyz"):
        return read_dat(path)
    # unknown extension: sniff the first bytes for a plausible record marker
    with open(path, "rb") as fh:
        head = fh.read(4)
    if len(head) == 4 and struct.unpack("<i", head)[0] == HEADER_BYTES:
        return read_ic(path)
    return read_dat(path)


def file_in_folder(folder):
    """The thing worth drawing inside a run folder: the IC, else the cloud."""
    for name in ("test_mesh.ic", "particles.dat"):
        candidate = os.path.join(folder, name)
        if os.path.exists(candidate):
            return candidate
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith(".ic"):
            return os.path.join(folder, f)
    return None


def resolve_input(arg):
    """Accept a file path or a run folder and return the file to draw."""
    if os.path.isfile(arg):
        return arg
    if os.path.isdir(arg):
        found = file_in_folder(arg)
        if found:
            return found
        sys.exit(f"No .ic or particles.dat inside '{arg}'.")
    sys.exit(f"Not found: '{arg}'")


def list_runs():
    """Every run folder under output\\runs\\, newest first."""
    if not os.path.isdir(RUNS_ROOT):
        return []
    runs = [os.path.join(RUNS_ROOT, d) for d in os.listdir(RUNS_ROOT)
            if os.path.isdir(os.path.join(RUNS_ROOT, d))]
    return sorted(runs, key=os.path.getmtime, reverse=True)


def list_inbox():
    """Loose .ic files the user dropped into input\\ic\\, newest first."""
    if not os.path.isdir(IC_INBOX):
        return []
    files = [os.path.join(IC_INBOX, f) for f in os.listdir(IC_INBOX)
             if f.lower().endswith((".ic", ".dat"))]
    return sorted(files, key=os.path.getmtime, reverse=True)


def list_candidates():
    """Everything the menus can offer: input\\ic\\ first, then the runs.
    Returns a list of (label, file path) pairs."""
    out = [(f"input\\ic\\{os.path.basename(f)}", f) for f in list_inbox()]
    for folder in list_runs():
        target = file_in_folder(folder)
        if target:
            out.append((os.path.basename(folder), target))
    return out


def parse_selection(text, n):
    """'1,3' / '1 3' / '2-5' / 'all' -> 0-based indices. '' -> [0] (newest)."""
    text = text.strip().lower()
    if text in ("all", "a", "*"):
        return list(range(n))
    if text == "":
        return [0]
    if text in ("q", "quit", "cancel"):
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


def prompt_for_candidates(action="drawn"):
    """Menu of everything in input\\ic\\ and output\\runs\\."""
    items = list_candidates()
    if not items:
        sys.exit("Nothing to work with yet.\n"
                 "Put an .ic file in input\\ic\\, or run the pipeline first.")

    print()
    print("Available IC files:")
    print("  " + "-" * 70)
    for i, (label, path) in enumerate(items, 1):
        size = os.path.getsize(path) / (1024 * 1024)
        print(f"  [{i:>2}]  {label:<52} {size:7.1f} MB")
    print("  " + "-" * 70)
    print()
    print(f"Which one(s) should be {action}?")
    print("    numbers   1,3      ranges  1-3      'all'")
    print("    blank     the first one listed")
    print()

    while True:
        try:
            raw = input("  Selection > ")
        except (EOFError, KeyboardInterrupt):
            print()
            return []
        try:
            picked = parse_selection(raw, len(items))
        except ValueError as exc:
            print(f"  !! {exc}  -  try again.")
            continue
        if not picked:
            return []
        return [items[i][1] for i in picked]


def open_file(path):
    """Show the PNG in whatever image viewer the OS has."""
    try:
        if os.name == "nt":
            os.startfile(path)                                     # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception:
        pass          # no viewer available; the PNG is still on disk


# ----------------------------------------------------------------------
# reporting
# ----------------------------------------------------------------------
def report(d):
    pos, obj = d["pos"], d["object"]
    lo, hi = pos.min(axis=0), pos.max(axis=0)
    ids, counts = np.unique(obj, return_counts=True)

    print()
    print("=" * 64)
    print(f"  {os.path.basename(d['source'])}")
    print("=" * 64)
    print(f"  particles        : {d['n']:,}")
    print(f"  distinct objects : {len(ids):,}  (IDs {ids.min()} - {ids.max()})")
    print(f"  bounding box (m) : X {lo[0]:+.4f} .. {hi[0]:+.4f}")
    print(f"                     Y {lo[1]:+.4f} .. {hi[1]:+.4f}")
    print(f"                     Z {lo[2]:+.4f} .. {hi[2]:+.4f}")
    print(f"  extents      (m) : {hi[0]-lo[0]:.4f} x {hi[1]-lo[1]:.4f} x {hi[2]-lo[2]:.4f}")
    if d["mass"] is not None:
        print(f"  mass / particle  : {d['mass'][0]:.6e} kg")
        print(f"  total mass       : {d['mass'].sum():.6f} kg")
    biggest = np.argsort(counts)[::-1][:5]
    print(f"  largest objects  : " + ", ".join(
        f"ID {ids[i]} ({counts[i]:,})" for i in biggest))
    if len(ids) > 1:
        print()
        print("  per-object Y extent (checks the anti-collision layout held):")
        shown = 0
        for oid in ids:
            m = obj == oid
            y = pos[m, 1]
            print(f"    ID {oid:<6} n={m.sum():<9,} Y {y.min():+.4f} .. {y.max():+.4f}")
            shown += 1
            if shown >= 12 and len(ids) > 14:
                print(f"    ... and {len(ids)-shown} more")
                break
    print("=" * 64)


# ----------------------------------------------------------------------
# exporting for real viewers
# ----------------------------------------------------------------------
def export(d, kind, out_base):
    pos, obj = d["pos"], d["object"]
    if kind == "csv":
        path = out_base + ".csv"
        with open(path, "w") as fh:
            fh.write("x,y,z,object_id\n")
            np.savetxt(fh, np.column_stack([pos, obj]), fmt="%.6f,%.6f,%.6f,%d")
    elif kind == "xyz":
        path = out_base + ".xyz"
        np.savetxt(path, pos, fmt="%.6f %.6f %.6f")
    elif kind == "vtk":
        path = out_base + ".vtk"
        n = len(pos)
        with open(path, "w") as fh:
            fh.write("# vtk DataFile Version 3.0\n3D VOX particles\nASCII\n")
            fh.write("DATASET UNSTRUCTURED_GRID\n")
            fh.write(f"POINTS {n} float\n")
            np.savetxt(fh, pos, fmt="%.6f %.6f %.6f")
            fh.write(f"\nCELLS {n} {2*n}\n")
            np.savetxt(fh, np.column_stack([np.ones(n, int), np.arange(n)]), fmt="%d %d")
            fh.write(f"\nCELL_TYPES {n}\n")
            np.savetxt(fh, np.ones(n, int), fmt="%d")
            fh.write(f"\nPOINT_DATA {n}\nSCALARS object_id int 1\nLOOKUP_TABLE default\n")
            np.savetxt(fh, obj, fmt="%d")
    else:
        raise ValueError(kind)
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"  exported: {path}  ({size_mb:.1f} MB)")
    return path


# ----------------------------------------------------------------------
# drawing
# ----------------------------------------------------------------------
def decimate(d, cap):
    n = d["n"]
    if cap <= 0 or n <= cap:
        return d["pos"], d["object"], 1.0
    rng = np.random.default_rng(0)          # fixed seed: same picture every time
    keep = rng.choice(n, cap, replace=False)
    keep.sort()
    print(f"  [thinned {n:,} -> {cap:,} points for the plot]")
    return d["pos"][keep], d["object"][keep], cap / n


def colours_for(obj, pos, mode):
    """Returns (colour_argument, cmap, palette, palette_size).
    For object mode the colours are resolved to explicit RGBA so that each
    Object ID gets exactly its palette entry - letting matplotlib normalise
    the IDs instead would stretch 3 objects across the whole colormap and
    make the plot disagree with the legend."""
    import matplotlib.pyplot as plt
    if mode == "z":
        return pos[:, 2], plt.get_cmap("viridis"), None, 0

    ids = np.unique(obj)
    n = len(ids)
    if n <= 10:
        palette, size = plt.get_cmap("tab10"), 10
    elif n <= 20:
        palette, size = plt.get_cmap("tab20"), 20
    else:
        # Hundreds of bodies: tab20 contains dark browns and greys that vanish
        # on a dark background. Sample a bright cyclic map instead, and stride
        # through it so neighbouring Object IDs land far apart in hue.
        size = 32
        base = plt.get_cmap("hsv")
        stride = 13                       # coprime with 32 -> visits all slots
        cols = np.array([base(((i * stride) % size) / size) for i in range(size)])
        cols[:, :3] = 0.35 + 0.65 * cols[:, :3]      # lift the darkest hues
        palette = lambda i, _c=cols: _c[int(i) % size]   # noqa: E731

    lut = {oid: i % size for i, oid in enumerate(ids)}
    idx = np.fromiter((lut[o] for o in obj), dtype=np.int16, count=len(obj))
    if n <= 20:
        return palette(idx), None, palette, size
    return cols[idx], None, palette, size


def draw(d, out_png, cap, color_mode, interactive, show_ids):
    try:
        import matplotlib
        if not interactive:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d import Axes3D    # noqa: F401
    except ImportError:
        sys.exit("matplotlib is not installed.  pip install matplotlib")

    pos, obj, frac = decimate(d, cap)
    c, cmap, palette, psize_pal = colours_for(obj, pos, color_mode)
    n_obj = len(np.unique(obj))

    lo, hi = pos.min(axis=0), pos.max(axis=0)
    span = (hi - lo).max() * 0.55 or 1.0
    mid = (hi + lo) / 2.0

    # point size shrinks as the cloud grows, so dense runs stay readable
    psize = float(np.clip(90000.0 / max(len(pos), 1), 0.35, 6.0))

    fg, bg, grid = "#e7eaf0", "#12151b", "#2a2f3a"
    fig = plt.figure(figsize=(13, 11), facecolor=bg)
    fig.suptitle(os.path.basename(d["source"]) +
                 f"    {d['n']:,} particles    {n_obj:,} objects",
                 color=fg, fontsize=15, y=0.965)

    views = [("Front  (X-Z)", 0, 2), ("Side  (Y-Z)", 1, 2), ("Top  (X-Y)", 0, 1)]
    for k, (title, ax_h, ax_v) in enumerate(views, start=1):
        ax = fig.add_subplot(2, 2, k, facecolor=bg)
        ax.scatter(pos[:, ax_h], pos[:, ax_v], c=c, cmap=cmap, s=psize,
                   linewidths=0, rasterized=True)
        ax.set_title(title, color=fg, fontsize=11, pad=8)
        ax.set_xlabel("XYZ"[ax_h] + " (m)", color=fg, fontsize=9)
        ax.set_ylabel("XYZ"[ax_v] + " (m)", color=fg, fontsize=9)
        ax.set_xlim(mid[ax_h] - span, mid[ax_h] + span)
        ax.set_ylim(mid[ax_v] - span, mid[ax_v] + span)
        ax.set_aspect("equal")
        ax.tick_params(colors=fg, labelsize=8)
        ax.grid(True, color=grid, linewidth=0.5, alpha=0.6)
        for s in ax.spines.values():
            s.set_color(grid)

    ax = fig.add_subplot(2, 2, 4, projection="3d", facecolor=bg)
    ax.scatter(pos[:, 0], pos[:, 1], pos[:, 2], c=c, cmap=cmap, s=psize,
               linewidths=0, depthshade=False, rasterized=True)
    ax.set_title("3D", color=fg, fontsize=11, pad=8)
    for setter, i in ((ax.set_xlim, 0), (ax.set_ylim, 1), (ax.set_zlim, 2)):
        setter(mid[i] - span, mid[i] + span)
    for lbl, setter in zip("XYZ", (ax.set_xlabel, ax.set_ylabel, ax.set_zlabel)):
        setter(f"{lbl} (m)", color=fg, fontsize=9)
    ax.tick_params(colors=fg, labelsize=7)
    try:
        ax.set_box_aspect((1, 1, 1))
    except Exception:
        pass
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.set_pane_color((0.07, 0.08, 0.11, 1.0))
        axis.line.set_color(grid)
        axis._axinfo["grid"]["color"] = grid
        axis._axinfo["grid"]["linewidth"] = 0.5
    ax.view_init(elev=22, azim=-58)

    note = f"colour = {color_mode}"
    if frac < 1.0:
        note += f"    showing {frac:.0%} of points"
    fig.text(0.5, 0.02, note, color="#98a1b3", fontsize=9, ha="center")

    if show_ids and 1 < n_obj <= 12 and palette is not None:
        import matplotlib.patches as mpatches
        ids = np.unique(obj)
        handles = [mpatches.Patch(color=palette(i % psize_pal), label=f"Object {oid}")
                   for i, oid in enumerate(ids)]
        fig.legend(handles=handles, loc="lower right", frameon=False,
                   labelcolor=fg, fontsize=9, bbox_to_anchor=(0.98, 0.03))

    fig.tight_layout(rect=[0, 0.035, 1, 0.95])

    if interactive:
        plt.show()
        return None
    fig.savefig(out_png, dpi=125, facecolor=bg)
    plt.close(fig)
    print(f"  figure : {out_png}")
    return out_png


# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Turn a GADGET-2 .ic file (or particles.dat) into a PNG.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("paths", nargs="*",
                    help="one or more .ic / .dat files or run folders. "
                         "With none, you get a menu of the runs in output/")
    ap.add_argument("--max-points", type=int, default=300000,
                    help="decimate to at most this many points for the plot (0 = all)")
    ap.add_argument("--color", choices=("object", "id", "z"), default="object",
                    help="what drives the point colour")
    ap.add_argument("--interactive", action="store_true",
                    help="open a rotatable window instead of writing a PNG")
    ap.add_argument("--open", action="store_true", dest="open_png",
                    help="open each finished PNG in the image viewer")
    ap.add_argument("--export", choices=("csv", "vtk", "xyz"),
                    help="also write a file for ParaView / Blender / MeshLab")
    ap.add_argument("--out", default=None,
                    help="output PNG path (only valid with a single input)")
    ap.add_argument("--beside", action="store_true",
                    help="write the PNG next to the input file instead of "
                         "into output\\images\\")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing PNG instead of adding _2, _3 ...")
    ap.add_argument("--no-show-ids", action="store_true",
                    help="suppress the per-object legend")
    args = ap.parse_args()

    if args.paths:
        targets = [resolve_input(p) for p in args.paths]
    elif sys.stdin.isatty():
        targets = prompt_for_candidates("drawn")
        if not targets:
            sys.exit("Cancelled - nothing was drawn.")
    else:
        items = list_candidates()
        if not items:
            sys.exit("Nothing in input\\ic\\ or output\\runs\\ yet.")
        targets = [items[0][1]]
        print(f"[first available] {targets[0]}")

    if args.out and len(targets) > 1:
        sys.exit("--out only works with a single input file.")

    color_mode = "object" if args.color == "id" else args.color
    made = []

    for i, path in enumerate(targets, 1):
        if len(targets) > 1:
            print(f"\n########## {i} of {len(targets)} ##########")
        print(f"Reading {path} ...")
        try:
            d = load_any(path)
        except Exception as exc:
            print(f"  !! could not read {os.path.basename(path)}: {exc}")
            continue
        report(d)

        if args.export:
            os.makedirs(POINTS_ROOT, exist_ok=True)
            stem = os.path.splitext(output_name(path, "_export", ""))[0]
            export(d, args.export, os.path.join(POINTS_ROOT, stem))

        if args.out:
            out_png = args.out
        elif args.beside:
            out_png = unique_path(
                os.path.join(os.path.dirname(os.path.abspath(path)),
                             output_name(path, "_preview", ".png")), args.force)
        else:
            os.makedirs(IMAGES_ROOT, exist_ok=True)
            out_png = unique_path(
                os.path.join(IMAGES_ROOT, output_name(path, "", ".png")),
                args.force)

        png = draw(d, out_png, args.max_points, color_mode, args.interactive,
                   not args.no_show_ids)
        if png:
            made.append(png)
            if args.open_png:
                open_file(png)

    if len(made) > 1:
        print(f"\n{len(made)} figures written.")
    if not made and not args.interactive:
        sys.exit("Nothing was drawn.")


if __name__ == "__main__":
    main()
