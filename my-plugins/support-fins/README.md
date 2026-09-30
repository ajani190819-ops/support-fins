# Support Fins — OrcaSlicer plugin

**v0.1.0 · stable · ready to install**

Adds [printfins.com](https://printfins.com) breakaway support fins under
overhangs at slice time. Objects that already have Orca's own support enabled
are left alone.

| | |
| --- | --- |
| File | `support_fins_orca.py` (~111 KB, engine bundled in) |
| Installs to | `%APPDATA%\OrcaSlicer\orca_plugins\SupportFins\` |
| Capabilities | `Support Fins`, `Support Fins - Check setup` |
| Needs | Nothing — Orca installs numpy + mini-racer itself on first load |
| Source | [`../../original-support-fins/plugins/orca/`](../../original-support-fins/plugins/orca/) |

## Install

Double-click **[`../../Install-Orca-Plugins.bat`](../../Install-Orca-Plugins.bat)**.
It handles this plugin and every other one, and re-running it updates them.

<details>
<summary>By hand instead</summary>

**Through Orca's UI (most reliable — some builds only register plugins added this way):**

1. **File > Plugins**.
2. Arrow next to **Browse plugins** → **Install local plugin**.
3. Pick `support_fins_orca.py` from this folder.
4. Enable it, with both capabilities on.
5. **Fully quit and reopen OrcaSlicer.**

**By copying files:**

1. Fully quit OrcaSlicer.
2. Open your config folder (**Help > Show Configuration Folder**).
3. Create `orca_plugins\SupportFins\` inside it.
4. Copy **both** files from this folder in:
   ```
   %APPDATA%\OrcaSlicer\orca_plugins\SupportFins\support_fins_orca.py
   %APPDATA%\OrcaSlicer\orca_plugins\SupportFins\.install_state.json
   ```
5. Reopen OrcaSlicer.

</details>

## Use it

1. **File > Plugins** — confirm **Support Fins** is present and enabled.
2. Load a model, run **Support Fins - Check setup** from the Plugins dialog.
   Expect `deps: numpy loaded at startup (audit-safe)`.
3. Process preset (Advanced): **Others > Slicing Pipeline Plugin** →
   **Support Fins**.
4. Slice. The fins appear in **Preview**, not Prepare — they are injected after
   mesh slicing, so they follow the part: rotate it, re-slice, new fins.

## Known issues

**A first slice that fails with `PermissionError`** means Orca had just
installed numpy/mini-racer and hadn't loaded them yet. Fully quit and reopen
OrcaSlicer once more and it clears.

**A crashing cloud/subscribed copy.** If you ever installed Support Fins from
Orca's plugin cloud, uninstall it in **File > Plugins** — or, with Orca closed,
delete it under `%APPDATA%\OrcaSlicer\orca_plugins\_subscribed\`. The installer
deliberately never touches that folder, and warns you if it finds one.

More detail: the
[plugin README](../../original-support-fins/plugins/orca/README.md#troubleshooting).

## Rebuilding this file

It is generated — don't hand-edit it.

```bash
python3 my-plugins/refresh-builds.py
```
