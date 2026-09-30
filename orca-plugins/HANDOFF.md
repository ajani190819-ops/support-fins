# Handoff notes

Context for anyone (human or agent) picking this project up. The
[README](README.md) covers *using* it; this covers *working on* it — what the
pieces are, what is already known, and what will bite you.

**Status in one line:** two OrcaSlicer plugins, fully tested offline against a
fake Orca harness, **never once run inside a real OrcaSlicer**.

---

## 1. What this is

Two experimental OrcaSlicer plugins plus a one-click Windows installer.

| Plugin | Version | Orca folder | Dependencies | What it does |
| --- | --- | --- | --- | --- |
| **Wave Overhangs** | 0.0.3 | `WaveOverhangs` | `numpy>=2.0`, `shapely>=2.0` | Prints steep overhangs support-free by wave-propagating toolpaths into thin air |
| **Unlayered Infill** | 0.2.0 | `UnlayeredInfill` | none (pure stdlib) | Rewrites sparse infill onto a sine wave in Z so layers interlock instead of stacking as clean planes |

Each registers two capabilities: the worker (`Wave Overhangs`) and a
diagnostic (`Wave Overhangs - Check setup`).

A third plugin, **Support Fins**, used to live here and was removed — an
official cloud plugin supersedes it. Its source stays in the upstream project
at `ajani190819-ops/support-fins`, which this repo was split out of.

---

## 2. Layout

```
Install-Orca-Plugins.bat     the installer (CRLF line endings — keep them)
plugins.json                 catalogue; the installer reads this and nothing else
refresh-builds.py            dev/ -> shipped artifacts, and version sync
HANDOFF.md                   this file
wave-overhangs/              SHIPPED: built .py + .install_state.json + user README
unlayered-infill/            SHIPPED: same
dev/<id>/src/                real, readable source
dev/<id>/tests/              the test suites
dev/<id>/build.py            inlines the engine into one shippable file
tests/test_installer.py      catalogue / installer / builds must agree
reference/orca-wiki/         wiki PDF snapshots + what they settled
reference/nonplanar_infill_tool.py   upstream script Unlayered Infill adapts
```

**The folders at the root are generated.** Never hand-edit
`wave-overhangs/wave_overhangs_orca.py`. Edit `dev/wave-overhangs/src/` and run
`python3 refresh-builds.py`.

Why that matters concretely: the shipped `unlayered_infill_orca.py` is 316
lines, but **one of those lines is 18,668 characters** — the whole engine as an
escaped string literal. The readable version is 455 normal lines in `dev/`.

---

## 3. Verify everything

```bash
python3 -m venv .venv && .venv/bin/pip install pytest numpy shapely

.venv/bin/python -m pytest -q dev/wave-overhangs/tests      # 25 passed
.venv/bin/python -m pytest -q dev/unlayered-infill/tests    # 49 passed
.venv/bin/python tests/test_installer.py                    # catalogue agrees
.venv/bin/python refresh-builds.py --check                  # builds not stale
```

Also run each suite **against the shipped artifact**, which is what users get.
A build step that silently drops code passes the source suite and fails here:

```bash
cd dev/wave-overhangs
WAVE_PLUGIN_PATH=$PWD/../../wave-overhangs/wave_overhangs_orca.py \
  python3 -m pytest -q tests/test_orca_seams.py             # 11 passed

cd ../unlayered-infill
INFILL_PLUGIN_PATH=$PWD/../../unlayered-infill/unlayered_infill_orca.py \
  python3 -m pytest -q tests/test_orca_seam.py              # 17 passed
```

CI (`.github/workflows/plugins.yml`) runs all six.

---

## 4. Facts about OrcaSlicer's plugin system

Established from the wiki snapshots in `reference/orca-wiki/`. Several were
learned the hard way; do not re-derive them.

### There is ONE preset field, not two

**Process preset → Others → Slicing Pipeline Plugin.** That single selection
drives every pipeline step, including the G-code export step. The wiki's
invocation table says `slicingPipeline` capabilities are invoked by
`Print.cpp` **and** `PostProcessor.cpp`, and both "resolve the preset's
capability refs" — the same refs.

`post_process_plugin` appears **nowhere** in the official plugin
documentation. An earlier version of Wave Overhangs read that config key and
gated behaviour on it; on a real build the key isn't there, and the plugin
disabled itself while printing help text pointing at a setting the user cannot
find. **Do not reintroduce config-key introspection to detect wiring.**

### The export step can run TWICE for one slice

File export and network upload are separate `psGCodePostProcess` calls. Any
G-code transform must be idempotent. Unlayered Infill stamps
`; unlayered-infill v0.2` and returns the input untouched if it's already
there.

### The audit hook is OFF at module load and ON during capability calls

Import every third-party dependency at **module load time**, never lazily
inside a capability. During a capability call, any file open is audited, and
paths containing `conf`, `cert` or `secret` are **blocked outright with no
prompt**. `import numpy` reads `numpy/__config__.py` and
`numpy/_core/_ufunc_config.py` — both contain "conf" — so a lazy numpy import
inside a capability dies with `PermissionError` (OrcaSlicer issue #15944).

Writes **inside `data_dir()` need no prompt**, and plugins live at
`data_dir()/orca_plugins/<plugin>/`. So the state and log files these plugins
write next to themselves are fine.

### Other

- At `psGCodePostProcess`, `ctx.print` and `ctx.object` are `None`. You get
  `gcode_path`, `host`, `output_name`.
- `ctx.config_value(key)` returns `None` if the key is absent.
- Never call `orca.host.ui.*` from a slicing capability — wrong thread.
- A capability name may not contain `;` (it's the preset reference separator).
- Steps: `posSlice, posPerimeters, posPrepareInfill, posInfill, posIroning,
  posContouring, posSupportMaterial, posSimplifyPath, psWipeTower, psSkirtBrim,
  psGCodePostProcess`.
- Requires OrcaSlicer **newer than 2.4.2, or a nightly**. Plugins declare
  `requires-python >=3.12`.

---

## 5. Design decisions worth keeping

### "Check setup" measures, it doesn't infer

Both plugins record which pipeline steps actually fired, in a small JSON file
next to themselves (`wave_overhangs_state.json`, `unlayered_infill_state.json`,
both gitignored). `Check setup` reports the recorded facts:

```
--- what the last slice actually did ---
planning step (posSlice): ran, 12 layer(s) with waves
G-code step (psGCodePostProcess): NEVER RUN
```

This replaced a version that guessed from config keys and guessed wrong. It
works on any build, including UI neither of us has seen. Keep this property.

### Wave Overhangs will not carve until the splice is proven

It has two seams: `posSlice` plans and may carve the overhang out of the
slices; `psGCodePostProcess` splices the wave moves into the G-code. Carving
without the splice leaves a **hole in the part**. So carving stays off until
the splice has been *observed* running at least once, then enables itself.
First slice after a fresh install never carves. That is intended.

### Failure modes are contained

Test-enforced: geometry steps no-op; refusals and internal errors never touch
the G-code file; an unexpected exception returns `Success` so a plugin bug
cannot fail someone's export.

### Unlayered Infill refuses absolute E

`M82` G-code plus move-splitting equals corruption. It stops with an
actionable message instead. Users must enable *Use relative E distances*.

---

## 6. Configuration

**Wave Overhangs** (`enabled`, `apply_to`, `carve_overhang`, `overhang_tol`,
`min_overhang_area`, `line_spacing`, `line_width`, `perimeter_overlap`,
`pattern`, `flow_ratio`, `print_speed`, `travel_speed`, `fan`,
`max_iterations`, `xy_offset`).

**Unlayered Infill:**

| Key | Default | Notes |
| --- | --- | --- |
| `amplitude` | `-0.2` | mm, or `-150%` / `-1.5x` of layer height. Negative dips **into** the part, keeping the nozzle clear. |
| `frequency` | `1.5` | ripples per mm along X |
| `segment_mm` | `1.0` | move subdivision length |
| `cell_mm` | `0.6` | XY resolution of the solid-skin column map |
| `blend_mm` | `2.0` | smooths the taper across neighbouring columns |
| `full_strength` | `false` | classic taper peaks at 0.5; this reaches 1.0 mid-span |
| `require_relative_e` | `true` | refuse M82 rather than corrupt it |

### How the infill taper works

Solid extrusions are rasterised into XY columns `cell_mm` across. Each column
records the Z heights that are solid **in that column**, so each infill move is
bracketed by its own local floor and roof:
`scale = min(d_above, d_below) / span`, peaking at 0.5 mid-span (1.0 with
`full_strength`), then `dz = amplitude * scale * sin(frequency * x)`.

A single global list of solid heights — the obvious implementation, and what
the upstream script does — is wrong on any part whose skins are not flat planes
across the whole footprint. Two towers of different heights: the short one's
roof enters the global list, so the tall tower gets pinched to zero taper at
that height, planting a flat unwoven plane straight through it — precisely the
weakness the tool exists to remove. Measured: 0.2857 with per-column bracketing
versus 0.0 with global. `test_a_tall_tower_ignores_a_short_neighbours_roof`
guards it.

Per-column raw tapers are memoised on `(column, z)`, so blending costs about
23% on a 100k-segment file instead of multiplying the work by the disc size.

---

## 7. The installer

`Install-Orca-Plugins.bat`, double-clickable, no dependencies beyond Windows.

- `REPO=ajani190819-ops/orca-plugins`, `MANIFEST_PATH=plugins.json`,
  `REF_1=main`.
- Downloads `plugins.json`, builds a plan (`id|name|version|orca_dir|file|repo_path`),
  then downloads each plugin and writes Orca's `.install_state.json` sidecar so
  a fresh copy shows up already enabled.
- Plan is built by inline PowerShell; if that fails it falls back to a
  **hardcoded list** inside the `.bat`.
- Downloads try `curl.exe`, then `Invoke-WebRequest` (TLS 1.2), then
  `bitsadmin`.
- Env vars: `ORCA_DATA_DIR`, `PLUGIN_BRANCH`, `PLUGIN_ONLY`. Args: `--local`,
  `--help`.

**The repo must be public.** Downloads are unauthenticated `raw.githubusercontent.com`
requests; a private repo 404s on every file.

---

## 8. Gotchas

- **Bumping a version takes two edits in lockstep:** the PEP 723 `# version`
  header *and* the hardcoded fallback line in `Install-Orca-Plugins.bat`.
  `tests/test_installer.py` fails if you forget the second. `plugins.json` is
  generated from the header, so don't hand-edit it.
- **The `.bat` is CRLF, all 534 lines.** Python's `Path.read_text()` /
  `write_text()` silently converts CRLF to LF and will corrupt it. Use
  `io.open(..., newline='')` and assert the CRLF count afterwards.
- **Don't add `*_state.json` to `.gitignore` without `!.install_state.json`.**
  That pattern matches Orca's sidecar and silently drops it from the repo,
  breaking file-copy installs. Already fixed; don't undo it.
- **Testing `dev/<id>/src/*_orca.py` directly needs `src/` on `sys.path`** —
  the engine module is only inlined at build time. Without it, capabilities
  return a misleading `RecoverableError`.
- **A PEP 723 header is fenced by `# ///` on both sides**, so
  `split("# ///")[1]` is the body.
- **Don't install `py_mini_racer`** if you ever work on Support Fins upstream —
  modern releases dropped `init_mini_racer`. Pin `mini-racer==0.14.1`.

---

## 9. Known gaps — read before trusting output

1. **Nothing has run in a real OrcaSlicer.** Every test is against a fake
   harness written alongside the plugins. They prove internal consistency, not
   that the model of Orca's API is right. The first real slice is the real
   test. If it fails, `data_dir()/log/python_*.log` has the traceback.
2. **Wave Overhangs is `sin(f·x)` only — invariant along Y.** It makes ridges,
   not a lattice. Interlocking is therefore directional.
3. **Defaults are untuned on hardware.** `amplitude=-0.2`, `frequency=1.5` are
   guesses.
4. **`cell_mm=0.6` versus ~0.42 mm solid line spacing is unverified.** If solid
   coverage turns out patchy, columns will be missing and the wave will damp
   where it shouldn't. This is the first number to check against a real slice.
5. **`full_strength` can displace by the entire gap** to the nearest skin in a
   thin part. Off by default for that reason.
6. **Wave Overhangs' object→bed XY mapping (`_bed_offset`) is unvalidated.**
   Its `Check setup` says so.

---

## 10. Suggested first moves

1. Install via the `.bat`, restart Orca, confirm both appear in **Plugins**
   with the right versions and both capabilities each.
2. Start with **Unlayered Infill** — zero dependencies, so a failure is the
   plugin system rather than a `uv` dependency download.
3. Enable *Use relative E distances*, select the plugin under **Others →
   Slicing Pipeline Plugin**, slice a small cube, then run **Check setup**. It
   will say whether the export step fired.
4. Inspect the G-code Preview before printing anything.
5. Feed real measurements back into gaps 3 and 4 above.

## 11. Licensing

Unlayered Infill adapts Roman Tenger's
[NonPlanarInfill](https://github.com/TengerTechnologies/NonPlanarInfill),
**GPL-3.0**. The original is kept verbatim at
`reference/nonplanar_infill_tool.py` and
`dev/unlayered-infill/README.md` documents every behavioural change, including
six bugs found in it. Keep the attribution and honour GPL-3.0 when
distributing.
