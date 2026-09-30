# Manual install (no build tools needed)

This folder holds a ready-to-install, pre-built Support Fins Orca plugin so you
can install it by hand without running Python/Node or the `.bat`.

- **`support_fins_orca.py`** — the built plugin (the printfins.com engine is
  already bundled inside it).
- **`.install_state.json`** — Orca's sidecar so the plugin shows up enabled
  without using the UI installer.

> This is a convenience copy of `support-fins/plugins/orca/build/support_fins_orca.py`.
> Rebuild it any time with `python plugins/orca/build.py` from the `support-fins/`
> folder.

## Step 0 — remove the crashing cloud copy

In OrcaSlicer: **File > Plugins**, find **Support Fins** (the subscribed/cloud
one) and **uninstall** it. Or, with Orca fully closed, delete the file under:

```
%APPDATA%\OrcaSlicer\orca_plugins\_subscribed\...\cloud_plugin-....py
```

## Option A — install through Orca's UI (simplest)

1. **File > Plugins**.
2. Click the arrow next to **Browse plugins** and choose **Install local plugin**.
3. Select `support_fins_orca.py` from this folder.
4. Enable it, expand it, and make sure both capabilities are on.
5. **Fully quit and reopen OrcaSlicer.**

## Option B — copy the files in by hand

1. Fully quit OrcaSlicer.
2. Open your Orca config folder (in Orca: **Help > Show Configuration Folder**),
   e.g. `C:\Users\kamau\AppData\Roaming\OrcaSlicer`.
3. Inside it, go to `orca_plugins\` and create a folder named `SupportFins`.
4. Copy **both** files from here into that folder, so you end up with:

   ```
   %APPDATA%\OrcaSlicer\orca_plugins\SupportFins\support_fins_orca.py
   %APPDATA%\OrcaSlicer\orca_plugins\SupportFins\.install_state.json
   ```

5. Reopen OrcaSlicer.

## Verify

1. **File > Plugins** — confirm **Support Fins** is present and enabled.
2. Load a model, then run **Support Fins - Check setup** from the Plugins dialog.
   You should see `deps: numpy loaded at startup (audit-safe)`.
3. In your process preset (Advanced), pick **Support Fins** under
   **Others > Slicing Pipeline Plugin**, then slice.

## If the first slice still errors with a PermissionError

That means Orca had just installed numpy/mini-racer and hadn't loaded them yet.
**Fully quit and reopen OrcaSlicer once more** and it will clear — numpy loads
during Orca's audit-free startup instead of mid-slice. See the parent
[README](../README.md) and the
[plugin README](../../support-fins/plugins/orca/README.md#troubleshooting).
