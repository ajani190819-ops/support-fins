# support-fins

Two things live here, and nothing else at the top level:

| Folder | What it is |
| --- | --- |
| **[`my-plugins/`](my-plugins/)** | Everything we build. One folder per plugin, each holding the newest ready-to-install file. This is where to look for a current version. |
| **[`original-support-fins/`](original-support-fins/)** | The original Support Fins project, untouched: the browser app at [printfins.com](https://printfins.com), its engine, plugin sources, prototype and test suite. |

---

## Install the plugins

**Double-click [`Install-Orca-Plugins.bat`](Install-Orca-Plugins.bat).** That's it.

It installs *every* plugin, fetching the newest build of each straight from this
repo. No Python, no Node, no Git, no hunting for raw GitHub URLs.

- Don't have a plugin yet? It installs it.
- Already have it? It overwrites it with the newer build.
- New plugin added later? The same file picks it up — no need to re-download it.

Keep the `.bat` wherever suits you (Downloads, Desktop, a Tools folder) and
double-click it whenever you want to be up to date. To get your copy: open
[`Install-Orca-Plugins.bat`](Install-Orca-Plugins.bat) on GitHub and use the
download button, or clone the repo.

After it runs: **fully quit and reopen OrcaSlicer**, check **File > Plugins**,
then pick the plugin in your process preset under
**Others > Slicing Pipeline Plugin**.

### Which source did it use?

Every run prints a **Source** block before it installs anything, and repeats it
in the summary at the end:

```
=== Source ===
  GitHub     ajani190819-ops/support-fins
  Branch/tag main
  Catalogue  last updated 2026-09-30
```

It always tries **`main` first** and only falls back to a work branch if `main`
doesn't have the catalogue yet — and when it does fall back it says so in plain
words. So you never have to know a branch name to be sure you got the right
files: read the banner.

<details>
<summary>Options</summary>

```bat
Install-Orca-Plugins.bat                             install / update everything
Install-Orca-Plugins.bat "C:\path\to\OrcaSlicer"     use that Orca data folder
Install-Orca-Plugins.bat --local                     use the files beside the .bat
Install-Orca-Plugins.bat --help
```

| Variable | Effect |
| --- | --- |
| `ORCA_DATA_DIR` | Default Orca data directory. |
| `PLUGIN_BRANCH` | Branch or tag to pull from. |
| `PLUGIN_ONLY` | Comma-separated ids, e.g. `support-fins,wave-overhangs`. |

</details>

---

## What's in `my-plugins/`

| Plugin | Version | State | Folder |
| --- | --- | --- | --- |
| **Support Fins** | 0.1.0 | Stable | [`my-plugins/support-fins/`](my-plugins/support-fins/) |
| **Wave Overhangs** | 0.0.1 | Experimental — check Preview before printing | [`my-plugins/wave-overhangs/`](my-plugins/wave-overhangs/) |
| **Unlayered Infill** | — | Not started | [`my-plugins/unlayered-infill/`](my-plugins/unlayered-infill/) |

Each folder holds the built plugin plus Orca's `.install_state.json` sidecar —
the exact files the installer hands out, so you can also grab one by hand.
[`my-plugins/plugins.json`](my-plugins/plugins.json) is the catalogue the
installer reads.

---

## Layout notes

A few things have to stay at the repo root:

- **[`Install-Orca-Plugins.bat`](Install-Orca-Plugins.bat)** — the thing you run
  most, so it isn't buried.
- **`.github/workflows/`** — GitHub only reads workflows from the root.
  ⚠️ `plugins.yml` still points at the pre-move paths (`web/**`, `plugins/**`),
  so the plugin CI no longer triggers. The fix is written but couldn't be pushed
  — updating a workflow file needs the `workflows` permission this session
  doesn't have. See [`my-plugins/README.md`](my-plugins/README.md#pending-ci-fix).
- **`.gitignore`**, **`.gitattributes`** — one set of rules for the whole repo.
  `.gitattributes` keeps `.bat` files CRLF so they work when double-clicked.
- **`wrangler.jsonc`** — Cloudflare looks for it at the configured root
  directory, so moving it would break the live deploy of printfins.com. Its
  `assets.directory` points at `./original-support-fins/web`.
- **`LICENSE`** (MIT) — also copied inside `original-support-fins/` so that
  folder stays a self-contained Docker build context.

### Where the plugin source lives

The *sources* stay in `original-support-fins/plugins/`, because the Support Fins
plugin bundles the printfins.com engine (`original-support-fins/web/*.js`) into
itself at build time — the plugin and the web app are one codebase. `my-plugins/`
holds the built output: what gets shipped and installed.

After changing plugin source or the web engine:

```bash
python3 my-plugins/refresh-builds.py   # rebuild, refresh my-plugins/, sync versions
```

Then commit. That's the one step between "I changed the code" and "the installer
hands out the new version". CI checks it stayed in sync.
