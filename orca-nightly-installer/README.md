# orca-nightly-installer

One-step Windows bootstrapper that installs **and updates everything** needed to
add [Support Fins](../support-fins/plugins/orca/) to a **nightly** build of
OrcaSlicer.

Unlike the root [`install-orca-support-fins.bat`](../install-orca-support-fins.bat)
(which assumes you already have the repo, Python and Node), this script is a
self-contained bootstrapper: run it on a fresh machine and it takes care of the
prerequisites, the source, the build, and the install.

## Use it

Double-click **`install-support-fins-nightly.bat`**, or run it from a terminal:

```bat
install-support-fins-nightly.bat
```

Optionally point it at a specific Orca data directory (for example a portable
`data_dir` next to your nightly `OrcaSlicer.exe`, or a differently-named config
folder):

```bat
install-support-fins-nightly.bat "C:\Users\you\AppData\Roaming\OrcaSlicerNightly"
```

Restart OrcaSlicer (or reopen **File > Plugins**) afterwards.

## What it does

1. **Prerequisites** — checks for Git, Python 3 and Node.js and installs any that
   are missing via `winget`. (esbuild is fetched on demand through `npx` during
   the build; Orca installs `numpy` / `mini-racer` itself on first load.)
2. **Source** — if you run it from inside a checkout it `git pull`s the repo it
   lives in; otherwise it clones/updates a cached copy under
   `%LOCALAPPDATA%\SupportFinsOrca\support-fins`.
3. **Build** — runs `support-fins/plugins/orca/build.py` to produce the
   single-file plugin `build/support_fins_orca.py`.
4. **Nightly data dir** — scans `%APPDATA%\OrcaSlicer*` and prefers folders whose
   name looks like a nightly/dev/alpha/beta build. It uses the single match
   automatically, prompts when there are several, and lets you type a full path.
5. **Install/update** — copies the plugin into
   `<data_dir>\orca_plugins\SupportFins\` and writes Orca's `.install_state.json`
   with both capabilities (**Support Fins** and **Support Fins - Check setup**)
   enabled.

Re-run it any time to update to the latest source and rebuild.

## In OrcaSlicer after installing

1. Restart Orca, or reopen **File > Plugins**.
2. Confirm **Support Fins** is enabled.
3. With a model on the plate, run **Support Fins - Check setup** from the Plugins
   dialog to verify the engine and mesh access.
4. In your process preset (Advanced), pick **Support Fins** under
   **Others > Slicing Pipeline Plugin**, then slice.

## After installing: restart Orca (important)

Orca installs the plugin's Python dependencies (numpy, mini-racer) **on first
load**, and they only become usable after a full restart. So after running the
installer, **fully quit and reopen OrcaSlicer** before slicing.

If you slice too early you may see:

```
PermissionError: Plugin attempted an audited operation without permission
```

with a traceback ending in `numpy/__init__.py`. This is a known OrcaSlicer
sandbox limitation ([#15944](https://github.com/OrcaSlicer/OrcaSlicer/issues/15944)):
its audit refuses any file path containing `conf`/`cert`/`secret`, and numpy's
`__config__.py` trips it when numpy is imported during slicing instead of at
startup. Fully quitting and reopening Orca fixes it — numpy then loads during the
audit-free startup window. Run **Support Fins - Check setup** afterwards; it
should print `deps: numpy loaded at startup (audit-safe)`.

## Environment overrides

| Variable | Effect |
| --- | --- |
| `ORCA_DATA_DIR` | Default Orca data directory (same as the first argument). |
| `SUPPORT_FINS_REPO` | Git URL to clone when no local checkout is found. Default: `https://github.com/ajani190819-ops/support-fins.git` |
| `SKIP_PREREQS=1` | Do not try to install Git/Python/Node via winget. |
| `SKIP_UPDATE=1` | Do not git pull/clone; just build what is already on disk. |

## Requirements & notes

- Windows with `winget` (App Installer) available for automatic prerequisite
  installs. Without it, the script tells you which tools to install manually.
- If a prerequisite is installed during the run, its `PATH` may not be visible in
  the same window. The script adds common install locations for the current
  session; if a step still can't find a freshly-installed tool, close the window
  and run the installer again.
- Requires a recent Orca **nightly/current** build with the Python plugin system
  (**File > Plugins** and the **Slicing Pipeline Plugin** picker). Older builds
  (2.4-style) are not supported by this lane.

See the plugin's own docs for how it works and how to test it:
[`../support-fins/plugins/orca/README.md`](../support-fins/plugins/orca/README.md).
