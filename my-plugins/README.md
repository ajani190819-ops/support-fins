# my-plugins

Everything we build. One folder per plugin, each holding the **newest
ready-to-install file** — no build folders, no nesting, no hunting.

| Plugin | Version | State | Orca folder | Folder |
| --- | --- | --- | --- | --- |
| Support Fins | **0.1.0** | Stable | `SupportFins` | [`support-fins/`](support-fins/) |
| Wave Overhangs | **0.0.1** | Experimental | `WaveOverhangs` | [`wave-overhangs/`](wave-overhangs/) |
| Unlayered Infill | 0.1.0 | Experimental | `UnlayeredInfill` | [`unlayered-infill/`](unlayered-infill/) |

## Installing

Double-click **[`../Install-Orca-Plugins.bat`](../Install-Orca-Plugins.bat)**. It
installs or updates all of them and needs no build tools. See the
[root README](../README.md#install-the-plugins).

To install one by hand instead, copy that plugin's two files into
`%APPDATA%\OrcaSlicer\orca_plugins\<Orca folder>\`. Each plugin's README has the
steps.

## What each folder contains

```
my-plugins/<id>/
├── <plugin>_orca.py       the built single-file plugin -- this is the release
├── .install_state.json    Orca's sidecar, so a plain file copy shows up enabled
└── README.md              what it does, how to install it by hand, known limits
```

Nothing else. Sources and tests stay with the project they are built from, in
`../original-support-fins/plugins/`.

## Files here

| File | Purpose |
| --- | --- |
| [`plugins.json`](plugins.json) | The catalogue. The installer downloads this and learns everything else from it. |
| [`refresh-builds.py`](refresh-builds.py) | Rebuilds every plugin from source and refreshes the copies here. |
| [`tests/test_installer.py`](tests/test_installer.py) | Checks the catalogue, the `.bat` and the shipped files still agree. |

## Releasing a new version

1. Edit the source in `../original-support-fins/plugins/<lane>/`.
2. Bump `version` in the plugin's PEP 723 header — that header is the single
   source of truth; `plugins.json` and the sidecars are derived from it.
3. ```bash
   python3 my-plugins/refresh-builds.py
   python3 my-plugins/tests/test_installer.py
   ```
4. Commit and push. Anyone who double-clicks the installer now gets it.

## Adding a new plugin

1. `mkdir my-plugins/<id>` and drop the built `.py` in.
2. Add an entry to [`plugins.json`](plugins.json) with `"status": "ready"`.
3. Add a build recipe to `BUILDS` in [`refresh-builds.py`](refresh-builds.py).
4. Add its line to the fallback list in `../Install-Orca-Plugins.bat`
   (`:fallback_plan`) — the test will tell you the exact line if you forget.
5. Run `python3 my-plugins/tests/test_installer.py`.

The installer needs no other change: it reads the catalogue at run time, so a
`.bat` already sitting on a laptop picks the new plugin up on its next run.

## Pending CI fix

`.github/workflows/plugins.yml` is **stale**: it still filters on `web/**` and
`plugins/**`, which stopped existing when the project moved into a subfolder, so
the plugin CI no longer triggers on anything. The corrected workflow is sitting
in [`ci/plugins.yml`](ci/plugins.yml) — it couldn't be written to `.github/`
directly because updating a workflow file needs a GitHub `workflows` permission
this session doesn't have.

To apply it:

```bash
cp my-plugins/ci/plugins.yml .github/workflows/plugins.yml
rm -r my-plugins/ci
git commit -am "Fix the plugins workflow after the reorganisation"
```

What it changes:

- paths and `working-directory` follow `original-support-fins/`
- also builds and tests the Wave Overhangs lane (14 tests), which was never wired up
- adds an `installer` job running `tests/test_installer.py`, plus an advisory
  `refresh-builds.py --check` for stale shipped builds
