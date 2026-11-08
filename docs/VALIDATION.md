# Validation

The checks the tools run, and the measured results.

## Round trip
`.ic` → `.dat` → `.ic` through the Fortran generator reproduces the original
binary **byte for byte**, verified on real files of 14,878,888 and 11,039,364
bytes. `ic_to_points` also re-reads every file it writes and compares all columns
against the binary before reporting success.

## Custom voxeliser against analytic volume
The ray-stabbing solid voxeliser was checked against the exact volume of known
boxes at 2.5 mm pitch:

| body | produced | analytic | error | time |
|---|---|---|---|---|
| 1.8 x 0.9 x 0.07 m, rotated 20° | 7,259,040 | 7,257,600 | +0.02% | 2.0 s |
| 0.9 x 0.9 x 0.07 m | 3,628,800 | 3,628,800 | 0.00% | 0.2 s |

## Placement clearance
Placement is verified per grid column, not by bounding box, because bodies
deliberately share Z ranges. A 5 mm requested clearance measured exactly 5.00 mm
across 527 contact columns with zero interpenetrating columns.

## Binary layout
File sizes are checked against `256 + 10x8 + 52N`. Record markers are compared at
both ends of every record while reading, so a truncated or foreign file is
rejected rather than silently misread.

## Interactive paths
Menus and prompts are exercised over a pseudo-terminal: multi-select, ranges,
invalid input, out-of-range input, and cancellation.
