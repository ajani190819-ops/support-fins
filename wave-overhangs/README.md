# Wave Overhangs — OrcaSlicer plugin lane

A self-contained OrcaSlicer **slicing-pipeline plugin** that changes the
**pattern of a flat overhang's bottom surface**: instead of one flat sheet of
material hanging in mid-air, the underside prints as **ripples** — dashed
grooves cut into the overhang's skin, arranged as concentric rings that
**conform to the supported perimeter and run outward, following whatever shape
that perimeter has** (around corners, around hole rims), until they run out of
overhang.

**The part keeps its shape.** Nothing is added anywhere, and the only material
removed is the grooves themselves: each one thinner than `groove_width_mm`
(default 0.25 mm), one layer deep, never within `edge_band_mm` of the part's
surface, with solid bridges left between dashes so the skin stays one piece.
Because a groove is one layer deep and the layer above prints the model's full
footprint, the printed **volume is identical to the model's** whenever the
overhang has solid above it — the grooves only texture the underside.

This folder was `new-project/` (the repo's placeholder for new work); it is now
this project. It borrows its architecture from the [Support Fins Orca
lane](../support-fins/plugins/orca/) — same PEP 723 plugin file, same
`Step.posSlice` seam, same offline test harness — but it is a separate feature
that only ever *subtracts* thin grooves, and it has no build step: the single
`.py` file in `src/` is the installable plugin.

## What it looks like

A 32 mm tabletop standing on an 8 mm stem, looking up at the ceiling's skin
from below (rendered by `demo/ripple_svg.py`'s geometry, as ASCII):

```text
    .....##...###....##......#####....................................####.......###....##...##.....
    .......##...###....###......##..#############..#####################.....####....##...###...##..
    ....###..###...###...####........####...#############......####.........###...###...###..###....
    ..##...##...##...###....####........................................###.....###........##...##..
    ......#..#....##...###....####..................................######....###...##...##..##.....
    ...##..##..###..###...###....#########.##############......########......#...###.......##..##...
    ..#..##..###..##..###....##.....#############..#################.....###...###..##..###..##.....
    ....##..##...#..##...##...####....................................####...##...##......##..##....
    ...##......##..##..##..###..####................................####..##...##..##..##..##.......
    ..#...##..#..##..##..##..###....#############..#############..###...###..##..##......#..##..##..
    .....##..##.##..##...#..#...##...#####.##############.............##..##..##..##..##.##.........
    .....##.##..##.....##..##.##..##................................##..##..#..##..#..##..##.##.....
    .....#..##.....##..##.##..##..#..##..............................#..##..##.##..##..#..#...#.....
    .....#......#..##..##.##..##..#..............................##..#......##.##..##..#..##..#.....
    .....#..##..#..##..##.##..#...#..##..........................##..#..##..##.....##..#..##..#.....
    .....#..##..#..##..##.....##..#..##..........................##.....##..##.##..##.....##..#.....
    .....#..##..#..##.....##..##..#..##..........................##..#..##.....##..##..#..##........
    .....#..##..#......##.##..##..#..##...........................#..#..##..##.##......#..##..#.....
    .....#......#..##..##.##..##..#..##..........................##..#...#..##.##..##..#......#.....
    ........##..#..##..##.##..##.....##..........................##..#..##..##..#..##..#..##..#.....
    .....#..##..#..##..##.##......#..##..........................##..#..##..##.##..##..#..##..#.....
    .....#..##..#..##..##.....##..#..##..........................##.....##..##.##..##..#..##..#.....
    .....##.##..##..#...#..##.##..##................................##..##.....##..#..##..##.##.....
    .....##..##.....##..##..##..##...####..##############..########...##..##..##..#...##.##..##.....
    ..##..##.....##..##..##..###...###..#############...#############...###..##..##..##..#..##..##..
    ...#...#...##..##..##..###..###.................................####..##...##..##..##..##..##...
    ....##..##..##..##...##...#.......................................####...##...#...##..##..##....
    ..#..##..###..##..###.....#.....##########..##############..####.......#...###..##..##...##.....
    ...##..##..###..##.....##....##########..#############..###########....###....##..###..##..#....
    .....##..##...#.....##....######................................##..##....###...##...##..##.....
    ..##...##........###....####........................................####....###...##...##...##..
    ....###....#...###...####.........#############...############.........###....###...###..##.....
    .......###..###....####.......##############..#############..#######.....####....#....###...##..
    .....##...###...###........###....................................#####......###...###..........
```

`#` is a groove dash, `.` is printed skin. The rings run outward from the
supported band around the stem in every direction at once, following its shape
(squares with cleanly rounded corners here — arcs around a round stem, L-shapes
around an L), each ring dashed so solid bridges tie every ridge to the next,
and the outer rim stays solid so the part's outline and walls are untouched.

## How it works

At `orca.slicing.Step.posSlice` — the seam the Support Fins plugin uses, after
Orca has sliced the part into per-layer polygons but before perimeters, infill,
supports and G-code — the plugin:

1. **finds** each layer's overhang region the footprint way (what this slice
   has that the one below did not) and keeps only regions **wide enough to be
   flat** — a surface steeper than `threshold_deg` from horizontal grows less
   than `h / tan(threshold)` per layer and is never touched;
2. keeps a **solid band** along the supported perimeter and a **solid rim** at
   the overhang's outer edge (`solid_band_mm`, `edge_band_mm`);
3. cuts **groove rings** at `ring_pitch_mm` spacing along the offset contours
   of the supported footprint — each groove is a capsule following its contour
   exactly (a clean arc, not a faceted approximation: ring fronts are drawn at
   64 segments per quadrant, ~1.5 µm of deviation at 20 mm), so the ripples
   inherit the perimeter's shape;
4. dashes each groove (`dash_mm` / `bridge_mm`) and checks the skin is still
   one connected piece — if a dash would ever sever it, that layer is left
   unpatterned;
5. leaves every other layer **bit-identical** to what Orca sliced.

Orca's own perimeter generator then traces the groove arcs, so the pattern
prints as ordinary walls and infill — no custom toolpaths, no G-code editing.

### Why grooves, and why dashed

The wave-overhang forks print their rings as custom toolpaths inside one
layer, each ring squished against the previous one — lateral anchoring. A
slice-polygon plugin cannot author toolpaths: per-layer polygons are its only
language, and polygons that touch get unioned by the slicer, so "adjacent
rings" cannot be expressed as separate paths. Grooves are what *can* be
expressed there: they force Orca's perimeters to trace the ripple arcs, and
the dashes keep every ridge tied to the supported band so the pattern never
floats. True same-layer wave toolpaths are the lane of
[`support-fins/plugins/orca-wave/`](../support-fins/plugins/orca-wave/) in
this repo, which splices G-code.

### Ramp mode (opt in)

`"mode": "ramp"` keeps the earlier support-free experiment: grow the printed
footprint one ring per *layer* so every ring rests on the ring below, turning
the flat ceiling into a printable terraced ramp:

```text
z=  20.2 |@@@@@@@#############################@@@@@@@@|   seed band + beyond-reach stock
z=  22.2 |@@@@@@@@@@@@@@################@@@@@@@@@@@@@@|
z=  24.8 |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@|   re-covered: a terraced wave
```

It is the only mode that can print a truly flat wide overhang without
supports, but it **removes the wedge under the ramp from the part** — the part
changes shape (and its outer wall steps with the terraces). That trade is why
skin mode is the default. Ramp mode is held to its own guarantee instead:
nothing added, nothing lost at all, first layer and steep surfaces untouched.

## Guarantees

Each of these is pinned by a test in `tests/`:

* **Nothing is added** — every layer's printed polygons are a subset of that
  layer's model slices.
* **Nothing is removed except thin grooves** — every removed piece is no wider
  than `groove_width_mm` (an explicit anti-chunk erosion test), only on layers
  that introduce a flat overhang, never touching the outer rim or the
  supported band.
* **The skin stays one connected piece** (bridges between dashes, plus a
  connectivity check that refuses to pattern a layer it would sever).
* **The first layer (bed / raft) is never touched**, layers without a fresh
  flat overhang are bit-identical, and surfaces steeper than `threshold_deg`
  are never touched.
* In **ramp mode** instead: nothing added, nothing lost at all — the union of
  printed layers equals the model's.
* Any error is reported as a `RecoverableError` instead of breaking the slice,
  and a conservation check refuses to edit layers if the plan ever breaks its
  own accounting.

## Install

Use a recent OrcaSlicer with **File > Plugins** (the Python plugin system, the
same requirement as the Support Fins plugin).

1. In Orca: **File > Plugins**, then **Install local plugin**.
2. Pick `wave-overhangs/src/wave_overhangs_orca.py` — the source file is the
   plugin; Orca installs numpy and shapely from its PEP 723 header on first
   load.
3. Enable the plugin, then run **Wave Overhangs - Check setup** from the
   Plugins dialog: it imports the deps and self-tests the geometry core on a
   synthetic tabletop (grooves found, thin, connected, shape unchanged).
4. In the process preset, under **Others > Slicing Pipeline Plugin**, choose
   **Wave Overhangs**.
5. Slice. The ripples appear in the Preview, not in Prepare.

## Configuration

The default capability config:

```json
{
  "enabled": true,
  "apply_to": "all",
  "mode": "skin",
  "threshold_deg": 30.0,
  "min_area_mm2": 1.0,
  "ring_pitch_mm": 1.2,
  "groove_width_mm": 0.25,
  "dash_mm": 4.0,
  "bridge_mm": 1.2,
  "edge_band_mm": 0.8,
  "solid_band_mm": 0.6,
  "ramp_angle_deg": 45.0,
  "max_reach_mm": 10.0,
  "anchor_mm": 0.1
}
```

| key | meaning |
|---|---|
| `mode` | `"skin"` patterns the overhang's underside and keeps the shape; `"ramp"` terraces it into a printable ramp at the cost of the wedge under the ramp |
| `apply_to` | `"all"` ripples every part; `"no-supports"` skips parts whose Orca **Enable support** is on |
| `threshold_deg` | surfaces flatter than this (from horizontal) are candidates; anything steeper prints untouched. 30° leaves a 45° slope alone |
| `ring_pitch_mm` | skin: centre-to-centre spacing of the ripple rings — the knob for how tight the ripples are |
| `groove_width_mm` | skin: groove width. Keep it above ~0.12 mm or Orca's `slice_closing_radius` (0.049 mm) will close the groove shut |
| `dash_mm` / `bridge_mm` | skin: groove dash length and the solid bridge between dashes (the anchors that keep the skin whole) |
| `edge_band_mm` | skin: solid rim kept at the overhang's outer boundary and hole rims |
| `solid_band_mm` | skin: solid band kept along the supported perimeter before the first ring |
| `ramp_angle_deg` / `max_reach_mm` / `anchor_mm` | ramp mode only: ramp steepness, how far the wave may climb, seed band width |
| `min_area_mm2` | ignore overhang patches smaller than this |

## Diagnostics

Each sliced object appends one JSON line to `wave_overhangs_log.jsonl` next to
the installed plugin: mode, rings and dashes cut, grooved area, per-layer
conservation accounting, timing, errors. Best effort — never fails a slice.

## Seeing it before you print

```bash
python3 demo/ripple_svg.py                                # tabletop demo, skin mode
python3 demo/ripple_svg.py --mode ramp                    # the terrace variant
python3 demo/ripple_svg.py tests/models/tshape.stl --pose -90
python3 demo/ripple_svg.py part.stl --ring-pitch 2 --out out/part.svg
```

writes an SVG (`out/<name>.svg`) with a per-layer plan view (model slice
dashed, printed skin filled — the groove dashes show as slits) and a
cross-section profile (skin mode: the model's outline, exactly; ramp mode: the
terraces). It runs the plugin's own geometry core, so the picture is the real
plan.

## Tests

```bash
python -m pytest -q tests/
```

* skin mode, on synthetic polygon stacks: shape preservation (nothing added,
  removals thinner than a groove, rim untouched), groove arcs following the
  perimeter (each dash's distance-to-perimeter spans one groove width, on the
  pitch grid), skin connectivity, hole rims rippling, grooved-area accounting
  (the printed volume loses nothing when the ceiling has layers above it);
* skin-mode invariants swept over the Support Fins prototype stress models
  (`tests/models/`, committed copies) in three poses each;
* ramp mode: its full original suite (ring-per-layer conformance, deadline and
  reach bounds, exact conservation) plus the same stress sweep;
* end to end through `tests/fake_orca.py` (a stand-in for Orca's embedded
  module that slices real STLs with trimesh): flat-overhang models get their
  underside rippled with **exactly one layer changed**, steep models print
  bit-identically, config is honoured, errors come back as `RecoverableError`.

Needs `pytest numpy shapely trimesh` (plus `scipy networkx rtree` for
trimesh's section→polygon path), same as the Support Fins plugin's suite.

## Limits

* Skin mode changes the surface **pattern**; it does not by itself make a
  truly flat wide overhang printable — that is ramp mode's trade, or supports.
  What the grooves do give the skin is relief joints and a shorter unsupported
  span per ridge.
* The pattern needs room: an overhang band narrower than the edge band (a
  gradual slope's daily growth, a 1 mm lip) is all rim and stays solid.
* The ripples appear in Preview only. Vase mode: don't.

## Relation to the rest of the repo

* The [Support Fins Orca lane](../support-fins/plugins/orca/) is the sibling
  project: fins **add** breakaway geometry under overhangs; this plugin
  **patterns** the overhang's own underside. Fins for near-vertical faces you
  can stand a wall against, ripples for flat ceilings.
* [`support-fins/plugins/orca-wave/`](../support-fins/plugins/orca-wave/) is
  the other wave-overhang plugin in this repo, and the closest relative. It
  reproduces the fork's **toolpath** form: same-layer wave rings computed at
  `posSlice` and spliced into the exported G-code at `psGCodePostProcess`
  with their own flow, speed and fan — experimental, with the G-code seams not
  yet validated on a real slice. This plugin is the **geometry** form: ripple
  grooves as pure slice polygons, no G-code post-processing at all, everything
  downstream Orca's own logic, tested end to end with shape-preservation
  guarantees. Same idea, different seam.
* The wave-overhang forks: [PrusaSlicer-WaveOverhangs] and
  [OrcaSlicer-WaveOverhangs] (native, non-planar rings), and the reference
  post-processor by [Andersons et
  al.](https://github.com/andersonsjanis/Wave-overhangs). Both plugins in this
  repo are plugin-system expressions of that strategy — no fork, no custom
  toolpaths in the engine.

[PrusaSlicer-WaveOverhangs]: https://github.com/stmcculloch/PrusaSlicer-WaveOverhangs
[OrcaSlicer-WaveOverhangs]: https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs
