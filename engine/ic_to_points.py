#!/usr/bin/env python3
r"""
.ic  ->  particles.dat     unpack a GADGET-2 IC binary back to a point cloud.

The reverse of step 4 of the pipeline. Reads the binary and writes six columns:

    X  Y  Z  Object_ID  G_type  Wall

The first four are the classic particles.dat format. G_type is the group /
material flag, Wall_ID is the wall identifier (the 'plate' record - use
--wall hinge for the other one).

The IC carries more than this: velocity, per-particle mass, internal energy
and the GADGET type tag. Every run PRINTS all of them with their values, so
nothing is hidden; --full writes them as columns too.

produce_ic.exe reads four values per line and skips the rest, so any of these
forms remains a valid input to the generator.

    3_IC_TO_POINTS.bat                double-click: pick from a menu of
                                      input\\ic\\ and output\\runs\\
    drag a .ic onto that .bat         unpacks it

    python ic_to_points.py test_mesh.ic
    python ic_to_points.py a.ic b.ic              several at once
    python ic_to_points.py ..\\output\\runs\\<run>   a whole run folder

Output goes to output\\points\\ by default, named after the run it came from.
Nothing is ever overwritten without --force.

Flags
    --wall plate|hinge  which reserved record is the wall identifier
    --minimal           only X Y Z Object_ID
    --full              every field, 13 columns
    --beside            write next to the input instead of into output\\points\\
    --out FILE          exact output path (single input only)
    --force             allow overwriting an existing output file
    --header            prepend a '# X Y Z ...' line (produce_ic cannot read it)
    --precision 6       decimal places, matching the pipeline's own format
    --no-verify         skip reading the result back and comparing it

Requires: numpy, and view_particles.py in the same folder (for the reader).
"""

import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def load_reader():
    """The .ic record parser lives in view_particles.py - one implementation,
    used by both tools, so they can never drift apart."""
    sys.path.insert(0, HERE)
    try:
        import view_particles
    except ImportError as exc:
        sys.exit(f"CRITICAL ERROR: could not import view_particles.py from "
                 f"{HERE} ({exc}).\nIt must sit next to this script.")
    return view_particles


def resolve_inputs(vp, paths):
    """Expand file paths and run folders into a list of .ic files."""
    out = []
    for p in paths:
        if os.path.isfile(p):
            out.append(p)
        elif os.path.isdir(p):
            found = vp.file_in_folder(p)
            if found is None:
                print(f"  [skipped] nothing to unpack in '{p}'")
                continue
            if not found.lower().endswith(".ic"):
                print(f"  [skipped] '{p}' has no .ic file (only {os.path.basename(found)})")
                continue
            out.append(found)
        else:
            sys.exit(f"Not found: '{p}'")
    return out


def pick_inputs(vp):
    """Menu of every .ic in input\\ic\\ and every run in output\\runs\\."""
    return [p for p in vp.prompt_for_candidates("unpacked to a point cloud")
            if p.lower().endswith(".ic")]


def infer_grid(pos, obj):
    """Recover the voxel spacing from the point grid, and from that the density
    implied by the per-particle mass. Neither is stored in the IC directly -
    only their product, as mass = spacing^3 * density."""
    ids = np.unique(obj)
    best = None
    # a handful of the largest bodies is plenty and keeps this fast
    ids, counts = np.unique(obj, return_counts=True)
    order = ids[np.argsort(counts)[::-1][:5]]
    for oid in order:
        p = pos[obj == oid]
        for axis in range(3):
            u = np.unique(np.round(p[:, axis].astype(np.float64), 9))
            if len(u) < 2:
                continue
            diffs = np.diff(u)
            diffs = diffs[diffs > 1e-9]
            if len(diffs):
                m = float(diffs.min())
                if best is None or m < best:
                    best = m
    if best is None:
        return None
    # Positions are stored as real*4, so the measured gap comes back as
    # 0.004999999 rather than 0.005. The float32 error is proportional to the
    # coordinate magnitude, not the gap, so 5 significant figures is the right
    # place to snap: enough to keep a genuinely odd spacing, coarse enough to
    # swallow the representation error and give a number you can retype.
    return float(f"{best:.5g}")


# Everything the IC carries, in the order it is written to the wide .dat.
# The first four columns are frozen as X Y Z Object_ID: produce_ic_mesh.f90
# reads exactly four values per line with a list-directed READ and skips the
# rest of the record, so a wide file stays a drop-in input to the generator.
POS = [
    # label,      source,   component, format,  what it is
    ("X",         "pos",    0, None,  "position, metres"),
    ("Y",         "pos",    1, None,  "position, metres"),
    ("Z",         "pos",    2, None,  "position, metres"),
    ("Object_ID", "object", 0, "%d",  "which part of the model it came from"),
]
G_TYPE = ("G_type", "g_type", 0, "%d",
          "group flag - carries the surface/marker coding on your wheels")
# produce_ic_mesh.f90 does  id(i) = wall  -  the wall identifier from column 6
# of the input lands in the IC's `id` record, NOT in plate or hinge.
WALL = {
    "id":    ("Wall", "type",  0, "%d",
              "wall identifier, e.g. -1 or 2 (the .f90's  id(i) = wall)"),
    "plate": ("Wall", "plate", 0, "%d",
              "wall identifier read from the 'plate' record instead"),
    "hinge": ("Wall", "hinge", 0, "%d",
              "wall identifier read from the 'hinge' record instead"),
}
EXTRAS = [
    ("VX",    "vel",   0, "%.9g", "velocity, m/s - set to 0 by the generator"),
    ("VY",    "vel",   1, "%.9g", "velocity, m/s - set to 0 by the generator"),
    ("VZ",    "vel",   2, "%.9g", "velocity, m/s - set to 0 by the generator"),
    ("Mass",  "mass",  0, "%.9g", "kg per particle = spacing^3 x density"),
    ("U",     "u",     0, "%.9g", "internal energy, fixed at 1e-6"),
]


def build_columns(mode, wall):
    """The columns written, in order. The first four are frozen as
    X Y Z Object_ID: produce_ic_mesh.f90 reads exactly four values per line
    with a list-directed READ and skips the rest, so any of these stays a
    valid input to the generator."""
    if mode == "minimal":
        return list(POS)
    if mode == "full":
        rest = [k for k in ("type", "plate", "hinge")
                if k != WALL[wall][1]]
        labels = {"type": "Type", "plate": "Plate", "hinge": "Hinge"}
        return (POS + EXTRAS + [G_TYPE, WALL[wall]] +
                [(labels[k], k, 0, "%d", "the remaining reserved record")
                 for k in rest])
    return POS + [G_TYPE, WALL[wall]]        # default: the 6-column form


def column_values(d, source, comp):
    arr = d[source]
    return arr[:, comp] if arr.ndim > 1 else arr


def describe_extras(d, wall):
    """Report what every record beyond X/Y/Z carries, written or not."""
    rest = [k for k in ("type", "plate", "hinge") if k != WALL[wall][1]]
    labels = {"type": "Type", "plate": "Plate", "hinge": "Hinge"}
    rows = EXTRAS + [G_TYPE, WALL[wall]] + [
        (labels[k], k, 0, "%d", "the remaining reserved record") for k in rest]
    print("  every field this IC carries beyond X Y Z Object_ID:")
    for label, source, comp, _fmt, meaning in rows:
        v = column_values(d, source, comp)
        uniq = np.unique(v)
        if len(uniq) == 1:
            state = f"constant {uniq[0]:.9g}"
        else:
            state = f"varies, {len(uniq):,} distinct, {v.min():.6g}..{v.max():.6g}"
        print(f"    {label:<10} {state:<38} {meaning}")

    wall_v = column_values(d, WALL[wall][1], 0)
    if len(np.unique(wall_v)) == 1 and np.unique(wall_v)[0] == 0:
        print(f"    note: Wall is all zero here - this IC predates the "
              f"six-column .f90, or its")
        print(f"          input had no wall column.")


def unpack(vp, path, out_path, precision, verify, force, cols, header, wall):
    print(f"Reading {path} ...")
    try:
        d = vp.read_ic(path, full=True)
    except Exception as exc:
        print(f"  !! not a readable GADGET-2 IC: {exc}")
        return None

    pos, obj, mass = d["pos"], d["object"], d["mass"]
    n = len(pos)

    if os.path.exists(out_path) and not force:
        print(f"  !! '{out_path}' already exists. Use --force to overwrite.")
        return None

    stack = np.column_stack([column_values(d, s, c) for _l, s, c, _f, _m in cols])
    fmt = [f"%.{precision}f" if f is None else f for _l, _s, _c, f, _m in cols]

    vp.fastio.write_table(out_path, stack, fmt,
                          header=("# " + "  ".join(l for l, *_ in cols))
                          if header else None)

    lo, hi = pos.min(axis=0), pos.max(axis=0)
    spacing = infer_grid(pos, obj)

    print(f"  particles     : {n:,}")
    print(f"  objects       : {len(np.unique(obj)):,}  "
          f"(IDs {obj.min()} - {obj.max()})")
    print(f"  bounding box  : X {lo[0]:+.4f}..{hi[0]:+.4f}  "
          f"Y {lo[1]:+.4f}..{hi[1]:+.4f}  Z {lo[2]:+.4f}..{hi[2]:+.4f}")
    if mass is not None and len(mass):
        m0 = float(mass[0])
        print(f"  mass/particle : {m0:.6e} kg   (total {mass.sum():.4f} kg)")
        if spacing:
            print(f"  spacing       : {spacing:.6g} m   (recovered from the grid)")
            density = float(f"{m0 / (spacing ** 3):.6g}")
            print(f"  density       : {density:g} kg/m^3   "
                  f"(implied by mass / spacing^3)")
            print(f"  -> to regenerate: produce_ic.exe "
                  f"\"{os.path.basename(out_path)}\" out.ic "
                  f"{spacing:g} {density:g} control.txt")

    print()
    describe_extras(d, wall)
    print()

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"  columns       : {len(cols)}  ({', '.join(l for l, *_ in cols)})")
    print(f"  written       : {out_path}  ({size_mb:.1f} MB)")
    if header:
        print("  NOTE: --header adds a '# ...' line that produce_ic.exe cannot "
              "read. Drop it before feeding this back into the generator.")

    if verify:
        back = vp.fastio.load_table(out_path, use_cache=False)
        tol = 0.5 * 10 ** (-precision) + 1e-9
        problems = []
        if len(back) != n:
            problems.append(f"rows {len(back)} != {n}")
        if back.shape[1] != len(cols):
            problems.append(f"columns {back.shape[1]} != {len(cols)}")
        if not problems:
            for i, (label, source, comp, f, _m) in enumerate(cols):
                original = column_values(d, source, comp)
                if f == "%d":
                    ok = np.array_equal(back[:, i].astype(np.int64),
                                        np.asarray(original, dtype=np.int64))
                elif f is None:
                    ok = bool(np.all(np.abs(back[:, i] - original) <= tol))
                else:
                    # %.9g is lossless for float32; compare exactly after the cast
                    ok = bool(np.all(back[:, i].astype(np.float32) ==
                                     np.asarray(original, dtype=np.float32)))
                if not ok:
                    problems.append(f"column {label}")
        if problems:
            print(f"  !! VERIFY FAILED - {'; '.join(problems)}")
            return None
        print(f"  verified      : {len(back):,} rows x {len(cols)} columns read "
              f"back, positions within {tol:.1e} m, every other column exact")

    return out_path


def main():
    ap = argparse.ArgumentParser(
        description="Unpack a GADGET-2 .ic file back into a particles.dat "
                    "point cloud (X Y Z Object_ID).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("paths", nargs="*",
                    help="one or more .ic files or run folders. With none, "
                         "you get a menu of the runs in output/")
    ap.add_argument("--out", default=None,
                    help="exact output path (single input only)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite the output file if it already exists")
    ap.add_argument("--precision", type=int, default=6,
                    help="decimal places, matching the pipeline's own format")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip reading the result back and comparing it")
    ap.add_argument("--wall", choices=("id", "plate", "hinge"), default="id",
                    help="which IC record holds the wall identifier. The .f90 "
                         "writes column 6 of its input into the 'id' record, "
                         "so 'id' is the right one")
    ap.add_argument("--minimal", action="store_true",
                    help="write only X Y Z Object_ID")
    ap.add_argument("--full", action="store_true",
                    help="write every field the IC carries (13 columns), "
                         "including velocity, mass, energy and the type tag")
    ap.add_argument("--beside", action="store_true",
                    help="write the .dat next to the input file instead of "
                         "into output\\points\\")
    ap.add_argument("--header", action="store_true",
                    help="prepend a '# X Y Z ...' column-name line. Handy for "
                         "pandas/Excel, but produce_ic.exe cannot read it")
    args = ap.parse_args()

    if args.precision < 1 or args.precision > 12:
        sys.exit("CRITICAL ERROR: --precision must be between 1 and 12.")

    vp = load_reader()

    if args.paths:
        targets = resolve_inputs(vp, args.paths)
    elif sys.stdin.isatty():
        targets = pick_inputs(vp)
        if not targets:
            sys.exit("Cancelled - nothing was unpacked.")
    else:
        items = [p for _l, p in vp.list_candidates() if p.lower().endswith(".ic")]
        if not items:
            sys.exit("No .ic files in input\\ic\\ or output\\runs\\ yet.")
        targets = [items[0]]
        print(f"[first available] {targets[0]}")

    if not targets:
        sys.exit("Nothing to do.")
    if args.out and len(targets) > 1:
        sys.exit("--out only works with a single input file.")

    mode = "minimal" if args.minimal else ("full" if args.full else "standard")
    cols = build_columns(mode, args.wall)

    done = []
    for i, path in enumerate(targets, 1):
        if len(targets) > 1:
            print(f"\n########## {i} of {len(targets)} ##########")
        # The column set changes the file's contents, so it belongs in the
        # name - otherwise a --full run silently collides with a --minimal one.
        tag = {"standard": "", "minimal": "_minimal", "full": "_full"}[mode]
        if args.out:
            out_path = args.out
        elif args.beside:
            out_path = vp.unique_path(os.path.join(
                os.path.dirname(os.path.abspath(path)),
                vp.output_name(path, "_particles" + tag, ".dat")), args.force)
        else:
            os.makedirs(vp.POINTS_ROOT, exist_ok=True)
            out_path = vp.unique_path(
                os.path.join(vp.POINTS_ROOT, vp.output_name(path, tag, ".dat")),
                args.force)
        result = unpack(vp, path, out_path, args.precision,
                        not args.no_verify, args.force, cols,
                        args.header, args.wall)
        if result:
            done.append(result)

    print()
    if not done:
        sys.exit("Nothing was written.")
    print(f"{len(done)} point cloud(s) written.")


if __name__ == "__main__":
    main()
