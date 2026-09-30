# Unlayered Infill — OrcaSlicer plugin

**v0.1.0 · EXPERIMENTAL · ready to install**

> **Check the G-code Preview before printing, and start with a small test part.**
> The engine is covered by 20 tests and the Orca seam by 12 more, but nothing
> here has been validated on a real printer yet.

Non-planar sparse infill. Instead of every infill path sitting flat at one Z,
infill moves ride a sine wave, `dz = amplitude × scale × sin(frequency × x)`,
so each layer's infill keys into the one below instead of stacking as clean
planes. `scale` tapers to zero where the infill approaches the solid skin above
or below, so the skins stay flat and the part looks normal from outside.

The motivation is strength: layer-aligned infill fails along the same planes the
perimeters do, so a part is only as strong as its weakest layer boundary.

| | |
| --- | --- |
| File | `unlayered_infill_orca.py` (~21 KB) |
| Installs to | `%APPDATA%\OrcaSlicer\orca_plugins\UnlayeredInfill\` |
| Capabilities | `Unlayered Infill`, `Unlayered Infill - Check setup` |
| Needs | **Nothing.** Pure standard library — no numpy, no shapely, no first-run install, no restart |
| Source | [`../../original-support-fins/plugins/orca-infill/`](../../original-support-fins/plugins/orca-infill/) |
| Licence | GPL-3.0 (see [Credit](#credit)) |

## Install

Double-click **[`../../Install-Orca-Plugins.bat`](../../Install-Orca-Plugins.bat)**.
It handles this plugin and every other one, and re-running it updates them.

<details>
<summary>By hand instead</summary>

1. **File > Plugins**.
2. Arrow next to **Browse plugins** → **Install local plugin**.
3. Pick `unlayered_infill_orca.py` from this folder.
4. Enable it, with both capabilities on.

Unlike the other two plugins this one has no dependencies, so it works
immediately — no restart needed.

</details>

## Use it

1. Run **Unlayered Infill - Check setup** from the Plugins dialog.
2. Printer Settings → Advanced → enable **Use relative E distances**.
   The plugin refuses to run on absolute-E G-code rather than corrupt it.
3. Process preset (Advanced) → **Others** → **Slicing Pipeline Plugin** →
   **Unlayered Infill**. That one field drives every step, including the
   G-code export step this plugin uses.
4. Slice, then **run Check setup again** — it reports whether the export step
   actually ran:

   ```
   --- what the last export actually did ---
   The G-code step ran: 418 infill move(s) across 37 section(s)
   ```

   If it says `NEVER RUN`, the export step isn't being reached and nothing in
   your G-code changed.
5. **Inspect the Preview** before printing.

### Why this plugin is a post-processor when the others aren't

Support Fins and Wave Overhangs inject geometry at `Step.posSlice` and let Orca
own flow, speed and cooling. That is the house rule, and
[the Support Fins README](../../original-support-fins/plugins/orca/README.md)
argues for it at length.

Non-planar infill is the one case where the rule cannot apply: **a slice polygon
is planar by construction**, so there is no way to express a wave in Z through
the slicing seam. This plugin therefore runs at `Step.psGCodePostProcess`, the
supported seam for rewriting the exported file.

## Settings

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

| Key | Meaning |
| --- | --- |
| `amplitude` | Wave depth. Plain mm (`-0.2`), a percent of the layer height (`-150%`), or a multiple (`-1.5x`). **Negative dips the wave into the part**, which keeps the nozzle clear of what it already printed. |
| `frequency` | Sine frequency along X. Higher = tighter ripples. |
| `segment_mm` | How finely infill moves are chopped before displacing them. Smaller = smoother wave, bigger file. |
| `require_relative_e` | Refuse to run on absolute-E (`M82`) G-code. Leave this on. |
| `log` | Append a JSONL record of each run next to the plugin. |

The wave reaches its full amplitude halfway between two solid skins and fades to
nothing at each of them.

## Credit

The engine is adapted from **`nonPlanarInfill.py`**, Copyright © 2025
**Roman Tenger (TenTech)**, GPL-3.0 —
<https://github.com/TengerTechnologies/NonPlanarInfill>. This plugin is likewise
GPL-3.0.

The tool it came to us through is kept verbatim at
[`reference/nonplanar_infill_tool.py`](reference/nonplanar_infill_tool.py) — it
still works standalone (double-click it, or
`python nonplanar_infill_tool.py yourfile.gcode`) and is useful for
side-by-side comparison.

### What changed on the way into the plugin

Five defects were found by testing the reference against realistic OrcaSlicer
output, and each is pinned by a test:

| # | Defect | Effect on a ten-layer test cube |
| --- | --- | --- |
| 1 | The `E` on a G-code line describes the move that *ends* there, but the engine used it for the move that *starts* there | long infill strokes under-extruded ~44%, short repositioning moves over-extruded |
| 2 | Segment lists included both endpoints, so each stroke restated the previous one's last point *with extrusion* | 55 zero-length extruding moves — a blob at every junction |
| 3 | Z was never restored when an infill section ended | 10 gap-fill extrusions ran at Z 0.82 instead of 0.80 |
| 4 | Solid layers were matched on `"solid infill"` only, but Orca names its outer skins `Top surface` / `Bottom surface` | the bottom skin was never found, so the taper measured from the build plate and the wave was ~2.5× too aggressive next to it |
| 5 | The "next solid above" kept a stale value above the topmost skin, making the taper go negative | the wave inverted near the top of the part |

Plus: extrusion is now handed out so the printed digits sum to exactly the
original value, rather than each segment rounding independently and drifting.

## Rebuilding this file

It is generated — don't hand-edit it.

```bash
python3 my-plugins/refresh-builds.py
```
