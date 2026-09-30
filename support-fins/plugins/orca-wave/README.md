# Wave Overhangs — OrcaSlicer plugin lane (EXPERIMENTAL)

A slicing-pipeline plugin that reproduces the
[WaveOverhangs](https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs) idea —
printing steep overhangs support-free by filling the unsupported region with
wave-propagated toolpaths — **as a plugin instead of a slicer fork**.

> **Status: experimental spike.** The wave-toolpath generator and the G-code
> emitter are pure Python and unit-tested offline. The two seams that touch a
> real OrcaSlicer build (object→bed coordinate mapping and G-code splicing) are
> implemented defensively but **not yet validated on a real slice** — see
> "What still needs real-Orca validation" below. Treat output as untrusted until
> you've checked it in the G-code preview.

## Why a fork exists, and what a plugin can/can't do

Wave overhangs is fundamentally a **toolpath** feature: Orca detects the
unsupported overhang and *replaces the extrusion paths there* with expanding wave
rings printed slowly with full cooling. In the C++ fork this lives in perimeter /
G-code generation. Orca's Python plugin API can **mutate slice geometry** and
**edit the exported G-code**, but it can't author custom extrusion paths with
their own flow/speed/fan inside the engine. So this plugin uses the only route
that can reproduce true waves:

1. **`Step.posSlice` (per object)** — read each layer's sliced polygons and the
   layer below, compute the overhang region and the wave polylines
   (`wave_core.plan_layer`), and stash them keyed by layer Z. Optionally **carve**
   the overhang out of the layer's slices so Orca doesn't also fill it.
2. **`Step.psGCodePostProcess`** — walk the exported G-code and splice the stashed
   wave moves into each matching layer, with the wave speed / fan / flow.

## The algorithm (`src/wave_core.py`)

Pure geometry, no Orca bindings, so it's unit-testable:

- **Overhang region** = this layer's area minus the layer-below area (grown by a
  tolerance). Empty on the first layer.
- **Wavefront propagation** = buffer the *supported* region outward in
  `line_spacing` steps; each offset's boundary, clipped to the overhang, is one
  wave track. Buffering a polygon rounds corners and flows around holes, so the
  fronts **diffract** like ripples (there's a test for the hole case).
- **Pattern** — `monotonic` (near→far), `zigzag` (flip alternate fronts into a
  back-and-forth path), or `smart` (start each front from its supported end).
- **G-code emitter** — relative-E extrusion with `line_width × layer_height ×
  flow_ratio` flow, forced fan, and the wave print/travel speeds.

## Build & install

```bash
cd support-fins
python3 plugins/orca-wave/build.py      # -> plugins/orca-wave/build/wave_overhangs_orca.py
```

The build inlines `wave_core.py` into a single file (Orca installs `numpy` and
`shapely` itself from the PEP 723 header on first load). Install the built file
via **File > Plugins > Install local plugin**, then **fully restart Orca** (deps
load on first startup — same audit-safe pattern as Support Fins). Select
**Wave Overhangs** under **Others > Slicing Pipeline Plugin** in your process
preset. Run **Wave Overhangs - Check setup** first.

## Configuration

```json
{
  "enabled": true,
  "apply_to": "no-supports",
  "carve_overhang": true,
  "overhang_tol": 0.05,
  "min_overhang_area": 0.5,
  "line_spacing": 0.35,
  "line_width": 0.40,
  "perimeter_overlap": 0.10,
  "pattern": "smart",
  "flow_ratio": 1.0,
  "print_speed": 2.0,
  "travel_speed": 120.0,
  "fan": 1.0,
  "max_iterations": 400,
  "xy_offset": ""
}
```

These mirror the fork's tunables. `xy_offset` ("x,y" mm) manually overrides the
object→bed mapping for calibration.

## What still needs real-Orca validation

1. **Object → bed XY mapping** (`_bed_offset`). Slices are in the object frame;
   G-code is absolute bed coordinates. The current code tries the first instance's
   offset and falls back to 0; verify against a known part and use `xy_offset` if
   needed. **This is the #1 thing to check.**
2. **Carving vs. perimeters.** `carve_overhang` removes the overhang from the
   layer's slices so Orca skips it. The fork keeps N "wave overhang perimeters"
   around the region — not yet implemented here.
3. **Layer matching in G-code** — `_parse_layer_z` keys off `;Z:` / `;HEIGHT:` /
   bare `Z` moves; confirm your Orca build emits one of those.
4. **Multi-object / multi-instance plates** — the stash is keyed per object; bed
   offset per instance needs testing.

Diagnostics are appended to `wave_overhangs_log.jsonl` next to the plugin.

## Tests

```bash
cd support-fins
python3 -m pytest -q plugins/orca-wave/tests/
```

Offline, shapely-only. Covers overhang detection, wavefront propagation (incl.
diffraction around a hole and reaching the far edge), pattern ordering, and G-code
flow/fan/speed.

## Roadmap

- [x] Wave-toolpath core + G-code emitter (tested)
- [x] Single-file plugin (posSlice stash + carve, psGCodePostProcess splice)
- [ ] Validate object→bed mapping on a real slice
- [ ] Keep N wave-overhang perimeters instead of full carve
- [ ] Remove Orca's original overhang moves from the G-code (vs. relying on carve)
- [ ] Min-wave-width splitting; corner reinforcement; per-region cooling
