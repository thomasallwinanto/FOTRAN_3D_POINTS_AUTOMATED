# Architecture

```
START.bat ─────────────► engine/app.py          desktop shell (Tkinter)
                         engine/app_pages.py    Quick Start · Engineer · Analyst · Admin

1_RUN_PIPELINE.bat ────► engine/run_pipeline.py orchestrator
2_VIEW_IC.bat ─────────► engine/view_particles.py
3_IC_TO_POINTS.bat ────► engine/ic_to_points.py

                         engine/fastio.py       native IC writer + voxel cache
                         engine/produce_ic_mesh.f90   Fortran IC generator
```

## Pipeline stages

| # | Stage | Detail |
|---|---|---|
| 1 | Load | Meshes are voxelised; `.dat` clouds are used as-is. Object IDs are renumbered per input so they never collide; non-positive IDs are preserved as boundary markers. |
| 2 | Place | One up-axis rotation for the whole scene, then bodies are dropped onto the real surface beneath them via a height map, clamped inside the ground footprint. Per-column clearance is verified. |
| 3 | Compile | `produce_ic_mesh.f90` is rebuilt only when the source is newer than the binary. Skipped entirely by the native engine. |
| 4 | Generate | GADGET-2 unformatted binary, from Fortran or the native Python writer. |
| 5 | Preview | Four-panel orthographic + 3D render, written into the run folder. |

## The IC binary

Fortran unformatted sequential records, each wrapped in 4-byte length markers:

```
rec  1  header   256 B   npart, massarr, a, redshift, flags, nall, unused
rec  2  pos      12N     rec  7  object   4N
rec  3  vel      12N     rec  8  g_type   4N
rec  4  id        4N     rec  9  plate    4N
rec  5  masses    4N     rec 10  hinge    4N
rec  6  u         4N
                         total = 256 + 10x8 + 52N bytes
```

Particle count can therefore be derived from file size alone, which the tools use
as a cheap integrity check.

## Point-cloud format

Six columns, matching what `produce_ic_mesh.f90` reads:

```
X  Y  Z  Object_ID  G_type  Wall
```

`G_type` lands in the IC's `g_type` record, `Wall` in its `id` record. A
four-column file is unsafe with the six-column generator: Fortran's list-directed
read silently pulls the two missing values off the next line.

## Coordinate frames

Source files are commonly Y-up (glTF/GLB are Y-up by specification). The pipeline
rotates **every** input in a run by the same transform so the world up axis
becomes +Z. The axis is detected from the ground body — a terrain slab is thin
along its normal — and the direction is taken from the height histogram when the
body is solid, or asked for when it is a single-layer heightfield, where the
geometry genuinely cannot say.
