# Wave Overhangs — OrcaSlicer plugin

**v0.0.2 · EXPERIMENTAL · ready to install**

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

> **You must set TWO things, not one.** Setting only the first is the usual
> reason the plugin appears to do nothing at all.

1. Run **Wave Overhangs - Check setup** from the Plugins dialog. Expect
   `deps: numpy + shapely loaded at startup (audit-safe)`.
2. Process preset (Advanced) → **Others**, and set **both**:
   - **Slicing Pipeline Plugin** → **Wave Overhangs**
     — plans the waves and carves the overhang out of the slices.
   - **Post-processing plugin** → **Wave Overhangs**
     — writes the wave moves into the exported G-code.

   Both point at the same capability name, `Wave Overhangs`. They are separate
   preset fields because they run at different times: the first during slicing,
   the second at export.
3. Slice a part with a steep overhang and **inspect the Preview**.

If only the pipeline field is set, the plugin now detects it, **refuses to
carve** (so the overhang still prints as normal solid material rather than
coming out hollow), and says so in the slicing result message.

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
