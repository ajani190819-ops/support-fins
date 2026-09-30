# Wave Overhangs — OrcaSlicer plugin lane

A self-contained OrcaSlicer **slicing-pipeline plugin** that rebuilds flat
overhangs as **ripples**: terraces that conform to the supported perimeter and
grow outward, layer by layer, following whatever shape that perimeter has — so
the overhang prints without support material.

This folder was `new-project/` (the repo's placeholder for new work); it is now
this project. It borrows its architecture from the [Support Fins Orca
lane](../support-fins/plugins/orca/) — same PEP 723 plugin file, same
`Step.posSlice` seam, same offline test harness — but it is a separate feature
with its own geometry, and it deliberately **shrinks** slices instead of adding
to them.

## What it does

A flat overhang — the underside of a ceiling, a tabletop standing on a stem,
the roof of a tunnel — normally prints as one layer of material hanging in
mid-air, and sags. With the capability enabled, at `Step.posSlice` the plugin:

1. **finds** each layer's overhang region the footprint way (what this slice has
   that the one below did not) and keeps only the regions **wide enough to be
   flat** — a surface steeper than `threshold_deg` from horizontal grows less
   than `h / tan(threshold)` per layer and is never touched;
2. prints only a **seed band** along the supported perimeter on the overhang's
   first layer, and defers the rest;
3. lets each following layer's footprint grow **one ring further out** along
   that same perimeter (`h / tan(ramp_angle)` of lateral growth per layer), so
   every new ring rests on the ring the layer below just printed;
4. stops deferring past what the solid above allows (a ripple never climbs out
   the top of the model), past `max_reach_mm`, or when the wave simply cannot
   get there in time — that leftover prints on the overhang's own layer exactly
   as stock Orca would have sliced it.

The ceiling becomes a terraced ramp. A cross-section says it best — a 32 mm
tabletop on an 8 mm stem, `#` is model-only (deferred), `@` is printed:

```text
z=  20.2 |@@@@@@@#############################@@@@@@@@...@@@@@#############################@@@@@@@|   <- seed band + beyond-reach stock
z=  20.8 |@@@@@@@@@##########################@@@@@@@@...@@@@@@##########################@@@@@@@@@|
z=  22.2 |@@@@@@@@@@@@@@################@@@@@@@@@@@@...@@@@@@@@@@@@################@@@@@@@@@@@@@@|
z=  24.2 |@@@@@@@@@@@@@@@@@@@@####@@@@@@@@@@@@@@@...@@@####@@@@@@@@@@@@@@@@@@@@|
z=  24.8 |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@...@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@|   <- re-covered
```

The ripples run **outward from every supported perimeter at once** — the outer
edge *and* hole rims — and meet in the middle, which is what makes the
cross-section a wave. In the plan view each layer's new band is the offset
contour of the perimeter it grew from: square over a square stem, L-shaped over
an L, arcs around holes.

This is the wave-overhang strategy of Andersons et al. (*"Wave-inspired
path-planning for support-free horizontal overhangs in FDM"*) and the
[OrcaSlicer-WaveOverhangs](https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs)
fork, expressed at the only seam a Python plugin can write to. The forks print
their rings as custom paths inside one layer; a plugin cannot write toolpaths,
so this is the **planar form: one ring per layer**. See *Limits* below.

## Why this seam

The same one Support Fins uses: `orca.slicing.Step.posSlice`, after Orca has
sliced the part into per-layer polygons but before perimeters, infill, supports
and G-code. Everything downstream is then Orca's own logic applied to the
rippled solid — walls, infill, bridge detection, overhang speeds. Two nice
consequences:

* **Wave-aware support, for free.** Supports left on are generated from the
  geometry that remains after the ripples, so they only appear where the wave
  could not reach. Turn supports on and let the two cooperate.
* **Overhang speeds apply.** Each ring is a genuine small overhang, so Orca's
  overhang-speed logic sees it and slows down for it if you configured that.

Unlike Support Fins, this plugin never reads the mesh — the wave works purely
on slice polygons (consecutive-layer diffs), so there is no frame calibration
to get wrong and no engine to bundle. The single .py file is the installable
plugin; there is **no build step**.

## Guarantees

Each of these is pinned by a test in `tests/`:

* **Nothing is added** — every layer's printed polygons are a subset of that
  layer's model slices.
* **Nothing is lost** — the union of printed layers equals the union of the
  model's slices. Every deferred point either prints when its ripple reaches
  it or is flushed back in at its last opportunity.
* **The first layer (bed / raft) is never touched.**
* **Steep surfaces are never touched** — anything steeper than `threshold_deg`
  from horizontal prints exactly as sliced (vertical walls bit-identically).
* The plugin edits nothing if the plan fails its own conservation check, and
  any error is reported as a `RecoverableError` instead of breaking the slice.

What *does* change is the overhang's underside: the terraced wedge under the
ramp is material **removed** from the model (at most `max_reach_mm` of reach
times the ramp's climb, and never more than the solid above allows). The
ripples also show on the outer wall near the overhang, where it steps out with
the terraces. That is the trade, and it is the same trade the forks make.

## Install

Use a recent OrcaSlicer with **File > Plugins** (the Python plugin system, the
same requirement as the Support Fins plugin).

1. In Orca: **File > Plugins**, then **Install local plugin**.
2. Pick `wave-overhangs/src/wave_overhangs_orca.py` — the source file is the
   plugin; Orca installs numpy and shapely from its PEP 723 header on first
   load.
3. Enable the plugin, then run **Wave Overhangs - Check setup** from the
   Plugins dialog: it imports the deps, self-tests the geometry core on a
   synthetic tabletop, and reports the objects on the plate.
4. In the process preset, under **Others > Slicing Pipeline Plugin**, choose
   **Wave Overhangs**.
5. Slice. The ripples appear in the Preview, not in Prepare.

## Configuration

The default capability config:

```json
{
  "enabled": true,
  "apply_to": "all",
  "threshold_deg": 30.0,
  "ramp_angle_deg": 45.0,
  "max_reach_mm": 10.0,
  "min_area_mm2": 1.0,
  "anchor_mm": 0.1
}
```

| key | meaning |
|---|---|
| `apply_to` | `"all"` ripples every part; `"no-supports"` skips parts whose Orca **Enable support** is on (ripples and supports cooperate by default, so `"all"` is the useful setting) |
| `threshold_deg` | surfaces flatter than this (from horizontal) get ripples; anything steeper prints untouched. 30° leaves a 45° slope alone and ripples a 20° one |
| `ramp_angle_deg` | the ripple ramp's steepness from horizontal. 45° is conservative; pushing past ~55° wants Orca's overhang speeds and your part cooling (the forks print waves at a few mm/s for the same reason) |
| `max_reach_mm` | how far a ripple may climb out from the perimeter. Also capped by the solid above: a 4 mm-thick roof at a 45° ramp can only ripple 4 mm out |
| `min_area_mm2` | overhang patches smaller than this are ignored (they bridge) |
| `anchor_mm` | seed band width; the floor on ring width when the ramp is very steep |

## Diagnostics

Each sliced object appends one JSON line to `wave_overhangs_log.jsonl` next to
the installed plugin: layer count, rippled area, ring depth, area left for
Orca, conservation check, timing, errors. Best effort — never fails a slice.

## Seeing it before you print

```bash
python3 demo/ripple_svg.py                       # built-in tabletop demo
python3 demo/ripple_svg.py tests/models/tshape.stl --pose -90
python3 demo/ripple_svg.py part.stl --ramp 55 --reach 20
```

writes an SVG (`out/<name>.svg`) with a per-layer plan view (model slice
dashed, printed footprint filled) and a cross-section profile showing the
terraces. It runs the plugin's own geometry core, so the picture is the real
plan.

## Tests

```bash
python3 -m pytest -q tests/
```

* geometry core, on synthetic polygon stacks: ring-per-layer conformance to the
  perimeter (envelope-exact), first-layer integrity, steep-slope and
  shrinking-footprint non-interference, hole rims rippling inward, threshold
  and reach and deadline behaviour, and the no-loss / no-add invariant on
  everything;
* end to end through `tests/fake_orca.py` (a stand-in for Orca's embedded
  module that slices real STLs with trimesh): flat-overhang models get
  rippled, steep models print bit-identically, config is honoured, errors come
  back as `RecoverableError`, conservation failures refuse to edit layers;
* no-material-lost sweep over the stress models from the Support Fins
  prototype (`tests/models/`, committed copies) in three poses each.

Needs `pytest numpy shapely trimesh` (plus `scipy networkx rtree` for
trimesh's section→polygon path), same as the Support Fins plugin's suite.

## Limits

* **Planar ripples, not the fork's same-layer rings.** One ring per layer is
  the only form a slice-polygon plugin can express. The result is the same
  idea (laterally anchored, perimeter-conforming growth) but the ramp climbs
  one layer height per ring instead of squishing rings at one Z.
* **Reach is bounded by the solid above.** A ripple defers material to *later*
  layers, so it needs model above it: a thin roof ripples only a little, and
  the rest is left to Orca (bridging, or supports if on). Wide-thick overhangs
  ripple furthest.
* The ripples appear in Preview only, print at your configured wall / overhang
  speeds, and vase mode is off the table.
* Wave overhangs in general want material that cools fast (PLA yes, PETG/ABS
  expect droop) — that is the technique's physics, not this implementation.

## Relation to the rest of the repo

* The [Support Fins Orca lane](../support-fins/plugins/orca/) is the sibling
  project: fins **add** breakaway geometry under overhangs, ripples **defer**
  the overhang's own material. They answer the same problem for different
  shapes — fins for near-vertical faces you can stand a wall against, ripples
  for flat ceilings with solid above them.
* [`support-fins/plugins/orca-wave/`](../support-fins/plugins/orca-wave/) is
  the other wave-overhang plugin in this repo, and the two are complementary
  routes to the same strategy. It reproduces the fork's **toolpath** form:
  wave rings inside a single layer, computed at `posSlice` and spliced into
  the exported G-code at `psGCodePostProcess` with their own flow, speed and
  fan — experimental, with the G-code seams not yet validated on a real slice.
  This plugin is the **geometry** form: one ring per layer, expressed purely
  as slice polygons, no G-code post-processing at all — everything downstream
  is Orca's own perimeter/infill/flow/speed logic, and the write-back path is
  tested end to end with conservation guarantees. Same idea, different seam:
  fork-faithful waves vs. printable ripples.
* The wave-overhang forks: [PrusaSlicer-WaveOverhangs] and
  [OrcaSlicer-WaveOverhangs] (fork, native, non-planar rings), and the
  reference post-processor by [Andersons et
  al.](https://github.com/andersonsjanis/Wave-overhangs). Both plugins in
  this repo are plugin-system expressions of that strategy — no fork, no
  custom toolpaths in the engine, no G-code post-processing (the lane this
  repo already rejected for fins).

[PrusaSlicer-WaveOverhangs]: https://github.com/stmcculloch/PrusaSlicer-WaveOverhangs
[OrcaSlicer-WaveOverhangs]: https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs
