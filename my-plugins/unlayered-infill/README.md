# Unlayered Infill — not built yet

Placeholder. Nothing to install.

This folder, its catalogue entry and its Orca folder name are already reserved,
so when the plugin exists there is no plumbing to invent — and the installer
already knows to skip it (`"status": "planned"` in
[`../plugins.json`](../plugins.json)).

## Starting point: TenTech's non-planar infill post-processor

The intended basis is a GPL-3.0 derivative of `nonPlanarInfill.py` by Roman
Tenger / TenTech ([TengerTechnologies/NonPlanarInfill](https://github.com/TengerTechnologies/NonPlanarInfill)).

> **The script itself is not in this repo yet** — it was pasted into a chat and
> never written to disk, and that message is no longer retrievable. It needs to
> be re-supplied before work can start. Drop it at
> `reference/nonplanar_infill_tool.py` in this folder.

What it does, from notes taken at the time:

- Post-processes **already-sliced G-code**. Finds `;TYPE:` sections matching
  `internal infill` / `sparse infill` (and reads `solid infill` to find the
  top/bottom bounds of each infill column).
- Splits infill moves into ~1.0 mm segments and displaces each one vertically
  by `dz = amplitude * scale * sin(frequency * x)`, where `scale` tapers to
  zero as the move approaches the nearest solid layer above or below — so the
  wave dies out where it would collide with solid material.
- Defaults `amplitude = -0.2` mm, `frequency = 1.5`. Amplitude accepts mm, `%`,
  or `x` of the detected layer height (the most common `;HEIGHT:` value).
- **Refuses to run on absolute-E G-code** (`M82`); relative E only.
- Has a CLI, an `--inplace` mode for use as a slicer post-processing script,
  and a tkinter GUI.

### Decision to make first: post-processor, or `posSlice`?

These pull in opposite directions and the answer shapes everything else:

| | G-code post-processor (the script's approach) | Slice-geometry plugin (`Step.posSlice`) |
| --- | --- | --- |
| Works today | yes — the script is proven | no — would be written from scratch |
| Can move Z off the layer grid | **yes** — this is the whole point | **no**, slices are planar by definition |
| Who decides flow/speed/cooling | the script, by rewriting E values | Orca |
| Risk | must re-derive extrusion for every displaced segment | none, Orca owns it |

The repo's house style (argued in the Support Fins and Wave Overhangs READMEs)
is to inject geometry at `posSlice` and let Orca own flow. **Non-planar infill
is the case where that rule cannot apply**: a slice polygon is flat by
construction, so there is no way to express a wave in Z through the slicing
seam. This plugin has to be a post-processor.

That makes it architecturally the same shape as the Wave Overhangs G-code seam,
which means it needs the same wiring:

- **Others → Post-processing plugin → Unlayered Infill** (`Step.psGCodePostProcess`)
- and *not* the Slicing Pipeline Plugin field.

`Step.psGCodePostProcess` receives `ctx.gcode_path` and is the supported place
to rewrite the exported file; filesystem access to that file needs no audit
prompt. See
[`../../original-support-fins/plugins/orca-wave/tests/test_orca_seams.py`](../../original-support-fins/plugins/orca-wave/tests/test_orca_seams.py)
for a working harness that drives exactly this seam offline — it can be reused
as-is here.

## The idea

Infill that isn't tied to the print's layer grid: instead of every infill path
sitting flat at one `z`, the infill follows its own continuous path through the
part. The motivation is strength — layer-aligned infill fails along the same
planes the perimeters do, so a part is only as strong as its weakest layer
boundary.

Related work already in this repo:

- **Wave Overhangs** ([`../wave-overhangs/`](../wave-overhangs/)) is the same
  family of problem — replacing a region's normal toolpaths with ones that
  propagate rather than follow the layer grid — and it is the closest existing
  reference for how to intercept Orca at the right seam.
- The **Support Fins** Orca plugin README argues for the slicing-pipeline seam
  (`orca.slicing.Step.posSlice`) over G-code post-processing:
  [`../../original-support-fins/plugins/orca/README.md`](../../original-support-fins/plugins/orca/README.md).
  That argument holds for everything except this plugin — see the table above
  for why non-planar infill is the exception.

## When it's ready

1. Build the single-file plugin into this folder.
2. In [`../plugins.json`](../plugins.json): set `status` to `ready`, fill in
   `version`, `file`, `path`, `capabilities` and `built_from`.
3. Add a build recipe to [`../refresh-builds.py`](../refresh-builds.py) and a
   fallback line to `../../Install-Orca-Plugins.bat`.
4. `python3 my-plugins/tests/test_installer.py` will confirm the wiring.

Nobody has to re-download the installer — it reads the catalogue at run time.
