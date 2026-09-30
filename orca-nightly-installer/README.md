# orca-nightly-installer

One `.bat` you keep anywhere (Downloads, a Tools folder, the Desktop) and
double-click to **install or update** the OrcaSlicer plugins — **Support Fins**
and/or **Wave Overhangs**.

**No Python, Node, Git or build tools required.** The installer downloads the
ready-built plugins and copies them into your OrcaSlicer data folder. Run it again
any time to update to the latest build.

## Use it

Double-click **`install-support-fins-nightly.bat`**, or run it from a terminal:

```bat
install-support-fins-nightly.bat
```

It will:

1. Ask which plugin(s) you want — Support Fins, Wave Overhangs, or both.
2. Find your OrcaSlicer data folder under `%APPDATA%` (preferring a nightly
   folder), or ask if there is more than one. You can also pass it explicitly:
   ```bat
   install-support-fins-nightly.bat "C:\Users\you\AppData\Roaming\OrcaSlicer"
   ```
3. Download the ready-built plugin(s) and install each into
   `<data_dir>\orca_plugins\<Name>\`, with Orca's `.install_state.json` so they
   show up enabled.

Then, in OrcaSlicer:

1. **Fully quit and reopen OrcaSlicer** (dependencies install on first load).
2. **File > Plugins** — confirm the plugin(s) are enabled.
3. Run the **"… - Check setup"** capability from the Plugins dialog.
4. In your process preset (Advanced): **Others > Slicing Pipeline Plugin** —
   choose **Support Fins** and/or **Wave Overhangs**.

## Updating

Just double-click the same `.bat` again. Each run re-downloads the latest build
and overwrites the installed copy. Keep the file in your Downloads/Tools folder
and re-run it whenever you want updates or to add the other plugin.

## If a plugin doesn't appear (do this — it's the reliable path)

Many OrcaSlicer builds only register a plugin when it's added through the UI, so a
plain file-copy into `orca_plugins\` may never show up. To make that easy, every
run also drops the downloaded files into **`%USERPROFILE%\Downloads\OrcaPlugins`**
and opens that folder for you.

In OrcaSlicer: **File > Plugins > (arrow next to "Browse plugins") > Install local
plugin**, then pick from that folder:

```
%USERPROFILE%\Downloads\OrcaPlugins\support_fins_orca.py
%USERPROFILE%\Downloads\OrcaPlugins\wave_overhangs_orca.py
```

Enable the plugin, then fully quit and reopen Orca.

## Environment overrides

| Variable | Effect |
| --- | --- |
| `ORCA_DATA_DIR` | Default Orca data directory (same as the first argument). |
| `PLUGIN_BRANCH` | Git branch/ref to download the built plugins from. Default: `arena/01a0f0b3-support-fins`. Change to `main` once merged. |
| `PLUGIN_PICK` | `1`=Support Fins, `2`=Wave Overhangs, `3`=Both — skips the menu. |

## Notes

- Windows with PowerShell (built in) is used for the download — no extra tools.
- **Support Fins** is stable and tested. **Wave Overhangs** is an experimental
  spike (see its [README](../support-fins/plugins/orca-wave/README.md)) — check
  the exported G-code before printing.
- The `manual-install/` subfolder holds the same pre-built plugins if you prefer
  to install them by hand; the `.bat` uses those automatically when run from
  inside a repo checkout.
- Prefer to build from source instead? Use the root
  [`install-orca-support-fins.bat`](../install-orca-support-fins.bat), which
  builds Support Fins locally (needs Python + Node).
