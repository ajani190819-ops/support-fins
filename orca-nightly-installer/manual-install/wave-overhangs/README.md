# Wave Overhangs — manual install (EXPERIMENTAL)

Pre-built, ready-to-install Wave Overhangs plugin. No Python/Node needed to
install — Orca installs the plugin's deps (numpy, shapely) itself on first load.

- **`wave_overhangs_orca.py`** — the built single-file plugin.
- **`.install_state.json`** — Orca's sidecar so it shows up enabled.

> **Experimental spike.** The wave generator is tested, but the Orca-facing parts
> (object→bed coordinate mapping, G-code splicing) are not yet validated on a real
> slice. Check the G-code **Preview** before printing, and start with a small test
> part. See the plugin README for what still needs calibration:
> `../../../support-fins/plugins/orca-wave/README.md`.

## Install (same as the Support Fins one)

**Option A — through Orca (simplest):**
1. **File > Plugins**.
2. Arrow next to **Browse plugins** → **Install local plugin**.
3. Select `wave_overhangs_orca.py` from this folder.
4. Enable it (both capabilities).
5. **Fully quit and reopen OrcaSlicer.**

**Option B — copy by hand:**
1. Fully quit OrcaSlicer.
2. Open your config folder (Orca: **Help > Show Configuration Folder**),
   e.g. `C:\Users\kamau\AppData\Roaming\OrcaSlicer`.
3. Go into `orca_plugins\` and make a folder `WaveOverhangs`.
4. Copy **both** files here into it:
   ```
   %APPDATA%\OrcaSlicer\orca_plugins\WaveOverhangs\wave_overhangs_orca.py
   %APPDATA%\OrcaSlicer\orca_plugins\WaveOverhangs\.install_state.json
   ```
5. Reopen OrcaSlicer.

## Use it

1. Run **Wave Overhangs - Check setup** from the Plugins dialog — expect
   `deps: numpy + shapely loaded at startup (audit-safe)`.
2. In your process preset (Advanced): **Others > Slicing Pipeline Plugin** →
   **Wave Overhangs**.
3. Slice a part with a steep overhang and inspect the **Preview**.
