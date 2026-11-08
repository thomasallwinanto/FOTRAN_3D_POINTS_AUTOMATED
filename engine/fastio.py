#!/usr/bin/env python3
"""
fastio.py  -  the fast file layer shared by every tool in engine\\

    load_table(path)          read a whitespace text point cloud, fast, with an
                              optional .npy cache for big files
    write_table(path, arr)    write one, ~1.5x faster than np.savetxt
    write_ic_native(...)      write a GADGET-2 IC directly from Python, in
                              exactly the byte layout produce_ic_mesh.f90 uses
    compare_files(a, b)       byte-compare two files (engine self-test)

Why a native IC writer?  The Fortran route is:
    Python writes 180 MB of text -> Fortran reads it TWICE -> writes the IC
    -> writes another 320 MB control file.
The native writer goes straight from the in-memory array to the binary, so a
4-million-particle run drops from minutes to seconds and needs no gfortran.
It rounds positions to 6 decimals first - the same thing the text hand-off to
Fortran does - so the result is the same file. Admin > "Check engines match"
proves that on your machine by running both and comparing byte for byte.
"""

import hashlib
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = (os.path.dirname(HERE)
        if os.path.basename(HERE).lower() == "engine" else HERE)
CACHE_DIR = os.path.join(ROOT, "output", "cache")

# files at least this big get a binary cache next to the runs (output\cache\)
CACHE_MIN_BYTES = 20 * 1024 * 1024
CACHE_ENABLED = True          # the GUI / --no-cache can switch this off

HEADER_BYTES = 256
MAX_RECORD = 2 ** 31 - 1      # gfortran splits bigger records; we do not


# ----------------------------------------------------------------------
# reading
# ----------------------------------------------------------------------
def _numpy_is_fast():
    try:
        major, minor = (int(v) for v in np.__version__.split(".")[:2])
        return (major, minor) >= (1, 23)       # C-level loadtxt arrived in 1.23
    except Exception:
        return False


def _parse_text(path):
    """Fastest parser available on this machine."""
    if not _numpy_is_fast():
        try:
            import pandas as pd
            return pd.read_csv(path, sep=r"\s+", header=None, comment="#",
                               dtype=np.float64, engine="c").to_numpy()
        except ImportError:
            pass
    return np.loadtxt(path, comments="#", dtype=np.float64, ndmin=2)


def _cache_path(path):
    st = os.stat(path)
    key = f"{os.path.abspath(path).lower()}|{st.st_size}|{int(st.st_mtime)}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(CACHE_DIR, f"{stem}_{digest}.npy")


def load_table(path, use_cache=None, quiet=False):
    """Whitespace-separated numbers -> float64 array (n_rows, n_cols).

    Big files are cached as .npy in output\\cache\\ keyed on path, size and
    modification time, so the second run on LUNAR_SURFACE.dat takes a blink
    instead of several seconds. Edit the file and the cache is simply missed."""
    if use_cache is None:
        use_cache = CACHE_ENABLED
    big = os.path.getsize(path) >= CACHE_MIN_BYTES
    cp = None
    if use_cache and big:
        try:
            cp = _cache_path(path)
            if os.path.exists(cp):
                arr = np.load(cp)
                if not quiet:
                    print(f"  [cache] {os.path.basename(path)} loaded from "
                          f"output\\cache ({arr.shape[0]:,} rows)")
                return arr
        except Exception:
            cp = None
    arr = _parse_text(path)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if cp:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            # drop stale caches of the same file before writing the new one
            stem = os.path.basename(cp).rsplit("_", 1)[0]
            for f in os.listdir(CACHE_DIR):
                if f.startswith(stem + "_") and f.endswith(".npy"):
                    try:
                        os.remove(os.path.join(CACHE_DIR, f))
                    except OSError:
                        pass
            np.save(cp, arr)
        except Exception:
            pass
    return arr


def cache_info():
    """(file count, total bytes) of output\\cache\\."""
    if not os.path.isdir(CACHE_DIR):
        return 0, 0
    files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR)
             if f.endswith((".npy", ".npz"))]
    return len(files), sum(os.path.getsize(f) for f in files)


def clear_cache():
    n = 0
    if os.path.isdir(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            if f.endswith((".npy", ".npz")):
                try:
                    os.remove(os.path.join(CACHE_DIR, f))
                    n += 1
                except OSError:
                    pass
    return n


# ----------------------------------------------------------------------
# writing text
# ----------------------------------------------------------------------
def write_table(path, arr, fmt, header=None, chunk=200_000):
    """Same bytes as np.savetxt(path, arr, fmt=fmt), roughly 1.5x faster.
    fmt is either one format string for the whole row or a list per column."""
    if isinstance(fmt, (list, tuple)):
        fmt = " ".join(fmt)
    line = fmt + "\n"
    with open(path, "w", newline="\n") as fh:
        if header:
            fh.write(header + "\n")
        for s in range(0, len(arr), chunk):
            block = arr[s:s + chunk].tolist()
            fh.write("".join(line % tuple(r) for r in block))
    return path


def write_particles(path, data, precision=6):
    """particles.dat: X Y Z Object_ID G_type Wall, six columns."""
    p = precision
    return write_table(path, data, f"%.{p}f %.{p}f %.{p}f %d %d %d")


# ----------------------------------------------------------------------
# native GADGET-2 writer
# ----------------------------------------------------------------------
def _record(fh, payload):
    n = len(payload) if isinstance(payload, (bytes, bytearray)) else payload.nbytes
    if n > MAX_RECORD:
        raise ValueError("record larger than 2 GB - use the Fortran engine "
                         "for runs this big")
    marker = struct.pack("<i", n)
    fh.write(marker)
    if isinstance(payload, (bytes, bytearray)):
        fh.write(payload)
    else:
        payload.tofile(fh)
    fh.write(marker)


def as_fortran_positions(xyz, precision=6):
    """What the Fortran ends up holding after reading '%.6f' text into real*4:
    round to `precision` decimals, then to float32."""
    scale = 10.0 ** precision
    return (np.rint(np.asarray(xyz, dtype=np.float64) * scale) / scale
            ).astype(np.float32)


def particle_mass(spacing, density):
    """(spacing**3) * density in real*4, as the compiled produce_ic.exe does it:
    gfortran -O2 evaluates s**3 correctly rounded to real*4, then multiplies.
    Checked against the test_mesh.ic in output\\runs (1.5624999e-05 kg)."""
    s = np.float32(spacing)
    cube = np.float32(np.float64(s) ** 3)
    return np.float32(cube * np.float32(density))


def write_ic_native(path, data, spacing, density, precision=6,
                    control_path=None):
    """data: (n, 6) array  X Y Z Object_ID G_type Wall.
    Writes the exact record layout of produce_ic_mesh.f90:
        header(256) pos vel id(=Wall) mass u object g_type plate hinge"""
    n = len(data)
    if n == 0:
        raise ValueError("no particles to write")
    if 12 * n > MAX_RECORD:
        raise ValueError(f"{n:,} particles is too many for the native writer "
                         f"(2 GB record limit) - use the Fortran engine")

    pos = np.ascontiguousarray(as_fortran_positions(data[:, :3], precision))
    obj = data[:, 3].astype(np.int32)
    gty = data[:, 4].astype(np.int32)
    wal = data[:, 5].astype(np.int32)
    zeros_i = np.zeros(n, np.int32)

    npart = [n, 0, 0, 0, 0, 0]
    header = (struct.pack("<6i", *npart) + struct.pack("<6d", *([0.0] * 6)) +
              struct.pack("<2d", 0.0, 0.0) + struct.pack("<2i", 0, 0) +
              struct.pack("<6i", *npart) + struct.pack("<34i", *([0] * 34)))
    assert len(header) == HEADER_BYTES

    with open(path, "wb") as fh:
        _record(fh, header)
        _record(fh, pos)
        _record(fh, np.zeros((n, 3), np.float32))                  # vel
        _record(fh, wal)                                           # id = wall
        _record(fh, np.full(n, particle_mass(spacing, density), np.float32))
        _record(fh, np.full(n, 1.0e-6, np.float32))                # u
        _record(fh, obj)
        _record(fh, gty)
        _record(fh, zeros_i)                                       # plate
        _record(fh, zeros_i)                                       # hinge

    if control_path:
        idx = np.arange(1, n + 1)
        write_table(control_path,
                    np.column_stack([idx, pos.astype(np.float64), obj]),
                    "%12d %16.9f %16.9f %16.9f %12d",
                    header=" # ID  X  Y  Z  Object_ID")
    return path


def compare_files(a, b, chunk=1 << 24):
    """True if two files are byte-identical."""
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(chunk), fb.read(chunk)
            if x != y:
                return False
            if not x:
                return True


def human(nbytes):
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.0f} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024.0


if __name__ == "__main__":
    for p in sys.argv[1:]:
        a = load_table(p)
        print(p, a.shape)
