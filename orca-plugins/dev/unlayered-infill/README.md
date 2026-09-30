# Unlayered Infill — non-planar sparse infill for OrcaSlicer

An OrcaSlicer plugin that makes sparse infill ride a sine wave in Z, so
successive layers interlock instead of stacking as clean planes. Layer-aligned
infill fails along the same planes the perimeters do; a part is only as strong
as its weakest layer boundary.

> **Status: experimental.** The engine is covered by 20 tests and the Orca seam
> by 12 more, including against the built single-file artifact. Nothing has been
> validated on a real printer. Check the G-code Preview and start small.

## Why this one is a post-processor

The other two lanes in this repo (`plugins/orca`, `plugins/orca-wave`) inject
geometry at `Step.posSlice` and let Orca own flow, speed and cooling. That is
deliberate, and plugins/orca/README.md argues for it.

Non-planar infill is the exception: **a slice polygon is planar by
construction**. There is no way to express a wave in Z through the slicing seam,
no matter how the polygons are shaped. So this plugin runs at
`Step.psGCodePostProcess` — the supported seam for rewriting the exported file,
where `ctx.gcode_path` points at the working G-code and filesystem access to it
needs no audit prompt.

Selecting it is simple: **Others → Slicing Pipeline Plugin → `Unlayered
Infill`**. There is only that one picker. `Print.cpp` (geometry steps) and
`PostProcessor.cpp` (the G-code export step) both resolve the *same* preset
capability refs, so a single selection wires up every step; this plugin
returns `success()` immediately for the ones it ignores.

Rather than assume the export step ran, the plugin
**records whether its seam actually fired** (in `unlayered_infill_state.json`
next to itself) and `Unlayered Infill - Check setup` reports it. One slice
answers the question definitively.

## Layout

```
plugins/orca-infill/
├── src/
│   ├── nonplanar_core.py        the engine: pure text in, pure text out, stdlib only
│   └── unlayered_infill_orca.py the plugin: one G-code seam + a setup check
├── build.py                     inlines the core -> build/unlayered_infill_orca.py
└── tests/
    ├── fake_orca.py             stdlib-only stand-in for the embedded orca module
    ├── test_nonplanar_core.py   20 tests on the engine
    └── test_orca_seam.py        12 tests driving execute(ctx) end to end
```

## Build

```bash
cd support-fins
python3 plugins/orca-infill/build.py   # -> plugins/orca-infill/build/unlayered_infill_orca.py
```

One file, ~21 KB, **no dependencies** — the PEP 723 header declares
`dependencies = []`, so there is no first-run install and no restart after
installing. Install it via **File > Plugins > Install local plugin**.

## How it works

```
dz = amplitude × scale(z) × sin(frequency × x)
```

* Only moves inside a `;TYPE:` section naming *sparse* or *internal* infill are
  touched. Walls, skins, gap fill, supports and travel are passed through
  untouched.
* Each qualifying move is split into `segment_mm` pieces and re-emitted with the
  displaced Z, sharing out the original move's extrusion.
* `scale(z)` is `min(above − z, z − below) / (above − below)` where `above` and
  `below` are the nearest solid layers. It is 0 at a skin and 0.5 midway
  between. Infill that is not bracketed by solid both above *and* below gets no
  wave at all.

Solid layers are found from `;TYPE:` sections matching `solid infill`,
`top surface` or `bottom surface` — which covers PrusaSlicer's naming and
Orca/Bambu's.

## Configuration

```json
{
  "enabled": true,
  "amplitude": "-0.2",
  "frequency": 1.5,
  "segment_mm": 1.0,
  "require_relative_e": true,
  "log": true
}
```

`amplitude` accepts mm (`-0.2`), a percent of the detected layer height
(`-150%`), or a multiple (`-1.5x`). Negative dips the wave into the part.

Relative extrusion (`M83`) is required — splitting moves under absolute E
(`M82`) would corrupt the file, so the plugin refuses instead. Diagnostics go to
`unlayered_infill_log.jsonl` next to the plugin.

## Tests

```bash
cd support-fins
python3 -m pytest -q plugins/orca-infill/tests/
```

Stdlib only; no numpy, no shapely. Run the seam suite against the *built*
single-file plugin too, since `build.py` inlines the core:

```bash
INFILL_PLUGIN_PATH=$PWD/plugins/orca-infill/build/unlayered_infill_orca.py \
  python3 -m pytest -q plugins/orca-infill/tests/test_orca_seam.py
```

### Provenance, and what changed

Adapted from **`nonPlanarInfill.py`**, Copyright © 2025 **Roman Tenger
(TenTech)**, GPL-3.0 — <https://github.com/TengerTechnologies/NonPlanarInfill>.
This lane is likewise GPL-3.0. The intermediate tool it reached us through is
kept verbatim at `reference/nonplanar_infill_tool.py`.

Testing that tool against realistic Orca output turned up five defects. Each is
now a test in `test_nonplanar_core.py`; measured on a ten-layer 20 mm cube:

| # | Defect | Reference | Fixed |
| --- | --- | --- | --- |
| 1 | `E` applied to the move *starting* at a line rather than the one *ending* there | long strokes ~44% under-extruded | exact |
| 2 | Duplicated segment endpoints, each carrying extrusion | 55 zero-length extruding moves | 0 |
| 3 | Z never restored after an infill section | 10 gap-fill extrusions at the wrong height | 0 |
| 4 | Orca's `Top surface` / `Bottom surface` not counted as solid | 2 skins found of 5; taper measured from the build plate | 5 of 5 |
| 5 | Stale "next solid above" → negative taper → inverted wave | present above the top skin | impossible by construction |

Plus exact extrusion accounting: shares are handed out so the printed digits sum
to the original value instead of each segment rounding independently.

## Roadmap

- [x] Engine with the reference's defects fixed, tested offline
- [x] Single-file plugin at the G-code seam, tested through a fake `orca`
- [x] Same suite run against the built artifact
- [ ] Validate on a real printer; tune default amplitude/frequency
- [ ] Wave along Y as well as X (currently `sin(f·x)` only, so the wave is
      invariant along Y)
- [ ] Respect per-object settings rather than one global config

## 0.2.0 — per-column skins, blending, full strength, idempotency

0.1.0 reproduced the reference's *global* model of where solid material is: a
single sorted list of Z heights carrying a skin anywhere in the print. Four
changes, each pinned by tests in `tests/test_nonplanar_core.py`:

### Solid material is mapped per XY column

`build_solid_grid()` rasterises every solid extrusion into a grid of columns
`cell_mm` (0.6 mm) across, recording the Z heights that are solid *in that
column*. `SolidGrid.scale()` brackets an infill move between its own column's
nearest skin below and above.

The global list is wrong on any part whose skins are not one flat plane. Two
towers of different heights share a build plate: the short one's top skin at
z=1.0 enters the global list, so the tall tower is pinched to zero taper at
z=1.0 as well — planting a flat, unwoven plane straight through it. That is
the exact layer-boundary weakness the tool exists to remove, manufactured by
the tool itself. `test_a_tall_tower_ignores_a_short_neighbours_roof` measures
it.

### `blend_mm` smooths column seams

Adjacent columns at the edge of a ledge can have roofs millimetres apart, so
taking each column's answer literally puts a step in the wave right there.
`scale()` averages over a disc of radius `blend_mm` (2.0 mm), distance
weighted. Columns with no solid recorded are *skipped* rather than counted as
zero — they are usually just the gaps between solid extrusion lines, and
counting them would damp the wave everywhere.

Raw per-column tapers are memoised on `(column, z)`, so the blend costs about
23% on a 100k-segment file rather than multiplying the work by the disc size.

### `full_strength`

The classic taper is `min(d_top, d_bot) / span`, which peaks at **0.5**
mid-span — the wave never exceeds half the requested amplitude. With
`full_strength` it is doubled and clamped to 1.0, reaching the full amplitude
mid-span while still fading to zero at the skins. Off by default: at 1.0 the
displacement can equal the whole gap to the nearest skin in a thin part.

### A marker makes a second pass a no-op

Output carries `; unlayered-infill v0.2` and `already_processed()` makes
`process()` return the input untouched. OrcaSlicer can invoke
`psGCodePostProcess` **twice for one slice** — once for the file, once for a
network upload — and the wiki is explicit that these are separate calls.
Without the marker the second call waves the waved file: every displacement
doubles and every segment is split again.

The marker is only written when moves were actually modulated, so a file the
plugin declined to touch is not stamped in a way that would make a later real
run skip it.
