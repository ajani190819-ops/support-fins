# Wave Overhangs — OrcaSlicer plugin

**v0.0.3 · EXPERIMENTAL · ready to install**

> **Check the G-code Preview before printing, and start with a small test part.**
> The wave generator itself is tested, but the Orca-facing parts — object→bed
> coordinate mapping and G-code splicing — have not been validated on a real
> slice yet.

Prints steep overhangs support-free by replacing the overhang region with
wave-propagated toolpaths: expanding fronts anchored to the supported edge,
printed slowly with full cooling, instead of ordinary infill that would sag.
A Python port of the
[dennisklappe/OrcaSlicer-WaveOverhangs](https://github.com/dennisklappe/OrcaSlicer-WaveOverhangs)
C++ fork.

| | |
| --- | --- |
| File | `wave_overhangs_orca.py` (~37 KB) |
| Installs to | `%APPDATA%\OrcaSlicer\orca_plugins\WaveOverhangs\` |
| Capabilities | `Wave Overhangs`, `Wave Overhangs - Check setup` |
| Needs | Nothing — Orca installs numpy + shapely itself on first load |
| Source | [`../../original-support-fins/plugins/orca-wave/`](../../original-support-fins/plugins/orca-wave/) |

## Install

Double-click **[`../../Install-Orca-Plugins.bat`](../../Install-Orca-Plugins.bat)**.
It handles this plugin and every other one, and re-running it updates them.

<details>
<summary>By hand instead</summary>

**Through Orca's UI (most reliable):**

1. **File > Plugins**.
2. Arrow next to **Browse plugins** → **Install local plugin**.
3. Pick `wave_overhangs_orca.py` from this folder.
4. Enable it, with both capabilities on.
5. **Fully quit and reopen OrcaSlicer.**

**By copying files:**

1. Fully quit OrcaSlicer.
2. Open your config folder (**Help > Show Configuration Folder**).
3. Create `orca_plugins\WaveOverhangs\` inside it.
4. Copy **both** files from this folder in:
   ```
   %APPDATA%\OrcaSlicer\orca_plugins\WaveOverhangs\wave_overhangs_orca.py
   %APPDATA%\OrcaSlicer\orca_plugins\WaveOverhangs\.install_state.json
   ```
5. Reopen OrcaSlicer.

</details>

## Use it

1. Run **Wave Overhangs - Check setup** from the Plugins dialog. Expect
   `deps: numpy + shapely loaded at startup (audit-safe)`.
2. Process preset (Advanced) → **Others** → **Slicing Pipeline Plugin** →
   **Wave Overhangs**. That one field is all you need — it drives both of the
   plugin's steps.
3. Slice a part with a steep overhang, then **run Check setup again**.

### Check setup tells you what actually happened

The plugin has two seams: one during slicing that plans the waves, and one at
export that writes them into the G-code. Both are driven by that single preset
field. Rather than assume they ran, the plugin **records which steps really
fired** and reports it:

```
--- what the last slice actually did ---
planning step (posSlice): ran, 12 layer(s) with waves
G-code step (psGCodePostProcess): NEVER RUN
```

That is the "it does nothing" case, and it tells you the export seam isn't
being reached. If it says both ran, you're set.

Until the export seam has been seen working at least once, the plugin
**refuses to carve** the overhang out of the slices, so your part still prints
normally rather than coming out hollow. Carving switches itself on
automatically once the splice is confirmed.

## What still needs calibration

See the
[plugin README](../../original-support-fins/plugins/orca-wave/README.md) for the
open list. Short version: the wave geometry is covered by tests
(`plugins/orca-wave/tests/`), the Orca integration is not.

## Rebuilding this file

It is generated — don't hand-edit it.

```bash
python3 my-plugins/refresh-builds.py
```
