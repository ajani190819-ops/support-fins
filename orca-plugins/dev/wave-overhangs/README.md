# Wave Overhangs — OrcaSlicer plugin lane (EXPERIMENTAL)

A slicing-pipeline plugin that reproduces the
[WaveOverhangs](https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs) idea —
printing steep overhangs support-free by filling the unsupported region with
wave-propagated toolpaths — **as a plugin instead of a slicer fork**.

> **Status: experimental spike.** The wave-toolpath generator and the G-code
> emitter are pure Python and unit-tested offline. As of **0.0.2** both
> Orca-facing seams are also driven end-to-end against a fake `orca` module
> (`tests/test_orca_seams.py`) — that suite was written to diagnose "the plugin
> does nothing" and caught three separate no-op bugs; see
> [Fixed in 0.0.2](#fixed-in-002--why-it-did-nothing).
>
> What is still **not validated on a real slice** is the object→bed coordinate
> mapping, which no offline test can settle — see "What still needs real-Orca
> validation" below. Treat output as untrusted until you've checked it in the
> G-code preview.
>
> **Setup takes two preset fields, not one** — see [Install](#install).

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
load on first startup — same audit-safe pattern as Support Fins). Run
**Wave Overhangs - Check setup** first.

Then select **Wave Overhangs** in your process preset under
**Others → Slicing Pipeline Plugin**. That single field is the whole wiring.

The plugin has two seams:

| Step | What it does |
| --- | --- |
| `Step.posSlice` | plans the waves, and (once safe) carves the overhang out of the slices |
| `Step.psGCodePostProcess` | splices the wave moves into the exported G-code |

Both seams come from that one selection: per the plugin-development wiki,
`Print.cpp` *and* `PostProcessor.cpp` each "resolve the preset's capability
refs" — the same refs — so one picker wires up every step. There is no second
`post_process_plugin` field to set (the string does not appear anywhere in the
official plugin documentation).

Rather than assume the export seam ran, the plugin **measures which steps
actually fire** and persists that in `wave_overhangs_state.json` next to
itself. `Wave Overhangs - Check setup` reports it in plain language.

Carving is gated on that measurement: until the splice has been observed
running at least once, the overhang is left alone, so a configuration that
never reaches the export seam degrades to "stock Orca output" instead of
"overhang deleted and nothing printed in its place". Once the splice is seen,
carving switches on by itself.

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

## Seeing the waves (they are NOT in Orca's Preview)

Orca builds its on-screen Preview *before* plugin G-code post-processing runs, so
the wave toolpaths — which are spliced into the exported G-code — **do not appear
in Orca's Preview**. That is a hard limitation of doing this as a plugin (Orca has
no wave toolpath generator to visualise). To actually look at the arcs, use the
bundled previewer, which writes an SVG you can open in any browser:

```bash
# a sample overhang (with a hole, to show the fronts diffracting around it)
python3 plugins/orca-wave/tools/preview_waves.py --demo -o waves.svg

# the real thing: read your EXPORTED .gcode and draw the injected wave lines
python3 plugins/orca-wave/tools/preview_waves.py --gcode myprint.gcode -o waves.svg
```

`tools/example-waves.svg` is a checked-in sample of the demo output.

## Object → bed coordinate mapping (self-calibrating)

Slices are in the object's frame; G-code is in absolute bed coordinates. Instead of
guessing an instance transform, the plugin **self-calibrates**: a fully-supported
layer prints the same outline in both frames, so aligning that layer's min corner
recovers the (pure-translation) offset. This logic lives in
`wave_core.splice_gcode` and is unit-tested (`test_splice_auto_calibrates...`).
Override it with the `xy_offset` config ("x,y" mm) if a build needs it. After a
slice, `wave_overhangs_log.jsonl` records `calibration` and `applied_offset`.

## What still needs real-Orca validation

1. **Confirm the applied offset** on a real export: run a slice, then
   `preview_waves.py --gcode <export>` and check the arcs sit over the overhang.
   If not, set `xy_offset` and re-slice.
2. **Carving vs. perimeters.** `carve_overhang` removes the overhang from the
   layer's slices so Orca skips it. The fork keeps N "wave overhang perimeters"
   around the region — not yet implemented here.
3. **Layer matching in G-code** — `wave_core.parse_layer_z` keys off `;Z:` /
   `;HEIGHT:` / bare `Z` moves; confirm your Orca build emits one of those (check
   `spliced_layers` in the log).
4. **Multi-object / multi-instance plates** — the stash and single calibration
   assume one object; multi-object needs per-object offsets.

Diagnostics are appended to `wave_overhangs_log.jsonl` next to the plugin.

## Tests

```bash
cd support-fins
python3 -m pytest -q plugins/orca-wave/tests/
```

Offline, shapely-only. Two suites:

- **`test_wave_core.py`** (14) — the pure geometry: overhang detection,
  wavefront propagation (incl. diffraction around a hole and reaching the far
  edge), pattern ordering, and G-code flow/fan/speed.
- **`test_orca_seams.py`** (10) — the plugin's two Orca-facing seams, driven
  through `fake_orca.py`: `posSlice` plans and carves a synthetic flat
  overhang, `psGCodePostProcess` splices the result into an exported file,
  re-slicing drops the stale stash, and a half-configured preset refuses to
  carve. These seams used to be entirely uncovered, which is how three
  no-op bugs shipped at once (see below).

Run the second suite against the *built* single-file plugin too — `build.py`
inlines `wave_core`, so the artifact users install must behave identically:

```bash
WAVE_PLUGIN_PATH=$PWD/../../wave-overhangs/wave_overhangs_orca.py \
  python3 -m pytest -q plugins/orca-wave/tests/test_orca_seams.py
```

### Fixed in 0.0.3 — don't guess at preset fields

0.0.2 decided whether it was safe to carve by reading a `post_process_plugin`
setting, on the assumption that a matching "Post-processing plugin" preset
field existed. **It does not.** OrcaSlicer exposes a single *Slicing Pipeline
Plugin* picker, and `post_process_plugin` appears nowhere in the official
plugin documentation. 0.0.2 would therefore have disabled carving permanently
and printed help text pointing at a setting the user cannot find.

0.0.3 replaces the introspection with measurement. The plugin records which
steps actually ran (`wave_overhangs_state.json`), carves only once the G-code
splice has been *observed* working, and the setup check reports the recorded
facts instead of an inferred diagnosis. This is correct on every build,
including ones that ship UI we have never seen.

### Fixed in 0.0.2 — why it did nothing

| Bug | Effect |
| --- | --- |
| `region.slices.set(e, …)` passed a bare `ExPolygon` where the binding needs a sequence | raised `TypeError`, swallowed by a bare `except` in `_carve_layer` → **carving never happened** |
| `psGCodePostProcess` is driven by the **`post_process_plugin`** preset field, not the slicing-pipeline field | users wired up only one of the two → **the splice never ran**, so every wave move was computed and discarded |
| `_PLAN` was only cleared in the splice's `finally` | a slice whose export seam never fired left the stash populated → **stale waves leaked into the next export** |

Also fixed: `if not manual and _CALIB is None or (...)` parsed as
`(not manual and _CALIB is None) or (...)` — `and` binds tighter than `or`.

## Roadmap

- [x] Wave-toolpath core + G-code emitter (tested)
- [x] Single-file plugin (posSlice stash + carve, psGCodePostProcess splice)
- [x] Self-calibrating object→bed mapping (unit tested)
- [x] Standalone SVG previewer (demo + read-back from exported G-code)
- [ ] Confirm calibration on a real slice across a few printers
- [ ] Keep N wave-overhang perimeters instead of full carve
- [ ] Remove Orca's original overhang moves from the G-code (vs. relying on carve)
- [ ] Min-wave-width splitting; corner reinforcement; per-region cooling
