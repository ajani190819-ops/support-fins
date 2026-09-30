# OrcaSlicer plugins

Two experimental [OrcaSlicer](https://orcaslicer.com) plugins, plus a
double-clickable installer that fetches the latest build of each from this
repo and drops it into your OrcaSlicer data folder.

Requires **OrcaSlicer newer than 2.4.2, or a nightly** — the Python plugin
system does not exist in older releases.

| Plugin | Version | Status | Needs |
| --- | --- | --- | --- |
| [**Wave Overhangs**](wave-overhangs/) | 0.0.3 | Experimental | numpy, shapely |
| [**Unlayered Infill**](unlayered-infill/) | 0.2.0 | Experimental | nothing (pure stdlib) |

## Install

Download **[`Install-Orca-Plugins.bat`](Install-Orca-Plugins.bat)** and
double-click it. It finds your OrcaSlicer data folder, downloads the current
build of each plugin, and installs or updates it. Close OrcaSlicer first.

```
Install-Orca-Plugins.bat              install / update everything
Install-Orca-Plugins.bat --local      install from a local checkout instead
```

| Environment variable | Effect |
| --- | --- |
| `ORCA_DATA_DIR` | Use a specific data folder instead of auto-detecting |
| `PLUGIN_BRANCH` | Pull from a branch or tag other than the default |
| `PLUGIN_ONLY` | Comma-separated ids, e.g. `wave-overhangs,unlayered-infill` |

Then, in OrcaSlicer: **Process preset → Others → Slicing Pipeline Plugin →**
pick the capability. That one field drives every step, including the G-code
export step — see [`reference/orca-wiki/`](reference/orca-wiki/) for why.

Each plugin also ships a **"… - Check setup"** capability. Run it after a
slice: it reports which pipeline steps actually fired, so "nothing happened"
turns into a specific answer instead of a guess.

## Working on this

[`HANDOFF.md`](HANDOFF.md) is the orientation doc: what OrcaSlicer's
plugin system actually does, the design decisions worth keeping, the
gotchas that will bite you, and the known gaps. Read it before changing
anything.

## Layout

```
Install-Orca-Plugins.bat     the installer
plugins.json                 catalogue the installer reads
refresh-builds.py            rebuild dev/ -> the folders below
wave-overhangs/              ready-to-install build + Orca sidecar + docs
unlayered-infill/            ready-to-install build + Orca sidecar + docs
dev/<id>/                    source, tests and build.py for each plugin
tests/test_installer.py      checks catalogue, installer and builds agree
reference/                   OrcaSlicer wiki snapshots, upstream source material
```

The folders at the root are what actually gets installed; `dev/` is where they
are built from. `python3 refresh-builds.py` rebuilds and re-syncs both, and
`python3 refresh-builds.py --check` fails if they have drifted.

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install pytest numpy shapely

.venv/bin/python -m pytest dev/wave-overhangs/tests   -q    # 25
.venv/bin/python -m pytest dev/unlayered-infill/tests -q    # 49
.venv/bin/python tests/test_installer.py
.venv/bin/python refresh-builds.py --check
```

Bumping a version means editing the PEP 723 `# version` header **and** the
hardcoded fallback line in `Install-Orca-Plugins.bat`; `tests/test_installer.py`
fails if you forget the second.

## Credit

**Unlayered Infill** adapts Roman Tenger's
[NonPlanarInfill](https://github.com/TengerTechnologies/NonPlanarInfill)
(GPL-3.0). The original is kept verbatim at
[`reference/nonplanar_infill_tool.py`](reference/nonplanar_infill_tool.py);
[`dev/unlayered-infill/README.md`](dev/unlayered-infill/README.md) documents
every behavioural change and the bugs found along the way.

These plugins began life in a fork of
[support-fins](https://github.com/ajani190819-ops/support-fins) and were split
out so that project stays untouched. Support Fins itself is not here — use the
official cloud plugin.
