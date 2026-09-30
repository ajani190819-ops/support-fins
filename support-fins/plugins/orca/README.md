# Support Fins — OrcaSlicer plugin lane

This is the current OrcaSlicer integration target: **the Python plugin system in
recent Orca nightlies / current builds**, not the old 2.4-style post-processing-script
path.

The plugin is intentionally a **slicing-pipeline plugin**.  It runs at
`orca.slicing.Step.posSlice`, after Orca has sliced the user's part into layer polygons but
before Orca generates walls, infill, support material, wave-overhang paths, or G-code.  That
means Support Fins become ordinary slice geometry and Orca's own downstream planners handle
them.

## What it does

`src/support_fins_orca.py` registers two capabilities from one plugin file:

1. **Support Fins** — slicing-pipeline capability.  For every print object whose Orca
   **Enable support** setting is off, it:
   - reads the object volumes and transforms from Orca's live slicing graph,
   - runs the same `web/*.js` Support Fins engine used by printfins.com, bundled into the
     plugin with esbuild and executed via `mini-racer`,
   - cross-sections the generated fin/pad/tine meshes at each layer `slice_z`,
   - converts those loops to Orca `ExPolygon`s,
   - merges them into the layer's first region and calls `layer.make_slices()` so the rest
     of Orca treats them as part geometry.
2. **Support Fins - Check setup** — script capability.  Run this from the Plugins dialog to
   verify a new install before slicing.  It checks the dependency install, the bundled engine,
   and Orca host mesh access, then reports visible object/volume counts.

The fins appear in **Preview**, not Prepare, because they are injected after mesh slicing.
Rotate the part, re-slice, and the fins follow the rotated part.

## Why this path, especially with wave overhangs / unlayered infill

A raw G-code post-processor would have to reverse-engineer layers, flow, extrusion modes,
travel ordering, wave-overhang paths, and non-planar/unlayered infill before safely inserting
new material.  By injecting polygons at `posSlice`, we stay upstream of those choices:
Orca still owns perimeters, infill, path ordering, speeds, extrusion amounts, previews, and
export.

That is the right seam for an Orca fork that adds wave overhangs or unlayered infill: the
fork sees the fins as just more sliced geometry.

## Windows one-step install/update

From the repository root, double-click or run:

```bat
install-orca-support-fins.bat
```

There is also a wrapper here:

```bat
support-fins\plugins\orca\install-update-windows.bat
```

The batch file builds the plugin, finds your Orca data directory under `%APPDATA%`, copies the
built plugin into `orca_plugins\SupportFins\`, and writes Orca's `.install_state.json` sidecar
with both capabilities enabled.  If you have more than one Orca data directory, it prompts you
to choose one.  You can also pass the data directory explicitly:

```bat
install-orca-support-fins.bat "C:\Users\you\AppData\Roaming\OrcaSlicer"
```

Restart Orca, or reopen **File > Plugins**, after running it.

## Manual build

From the **inner** project folder:

```bash
cd support-fins
python3 plugins/orca/build.py
```

Output:

```text
plugins/orca/build/support_fins_orca.py
```

The generated file is the installable plugin.  Install **that built file**, not
`src/support_fins_orca.py`; the source file contains an `ENGINE_JS` placeholder.  The build
output is intentionally not committed (`plugins/orca/build/` is gitignored) because it
contains the minified bundled JS engine.

`build.py` uses `plugins/shared/bundle.py`, which calls `esbuild` either from `$ESBUILD`, an
installed `esbuild`, or `npx --yes esbuild@0.28`.

## Manual install in current Orca

Use a recent Orca build that has **File > Plugins** / the Python plugin system.

1. Build the plugin as above.
2. In Orca: **File > Plugins**.
3. Click the arrow next to **Browse plugins** and choose **Install local plugin**.
4. Select `plugins/orca/build/support_fins_orca.py`.
5. Enable the plugin, then expand it and make sure both capabilities are enabled.
6. Select **Support Fins - Check setup** and run it.  With a model on the plate, it should
   report that the engine loaded and mesh faces are visible.
7. In the process preset, switch to Advanced and choose the **Support Fins** capability under
   **Others > Slicing Pipeline Plugin**.
8. Slice.  Objects with Orca's own **Enable support** turned on are skipped unless the plugin
   config sets `apply_to` to `"all"`.

If your build has no Plugins window or no Slicing Pipeline Plugin picker, it is too old for
this lane.  Use a current nightly/current release with the Python plugin system.

## Configuration

The default capability config is:

```json
{
  "enabled": true,
  "apply_to": "no-supports",
  "coverage": 0.5,
  "tines": true,
  "tine_density": 0.0,
  "bed_pad": true
}
```

- `apply_to: "no-supports"` means the normal per-object Orca **Enable support** toggle picks
  between Orca supports and Support Fins.
- `apply_to: "all"` ignores that toggle and tries to add fins to every model part.
- `coverage`, `tines`, `tine_density`, and `bed_pad` mirror the website/plugin engine options.

## Diagnostics

The slicing capability appends one JSON line per object to:

```text
support_fins_log.jsonl
```

next to the installed plugin file.  This is best-effort and never fails the slice.  It records
layer counts, frame calibration, engine stats, elephant-foot compensation, and errors.

The setup-check script capability is the first thing to run when testing a new Orca nightly.
It avoids discovering install/API/dependency problems only after a full slice.

## Tests

Offline checks from the inner `support-fins/` folder:

```bash
python3 plugins/orca/build.py
deno test --allow-read tests/ plugins/shared/tests/
python3 -m pytest -q plugins/orca/tests/
```

The pytest suite uses `plugins/orca/tests/fake_orca.py`, a small stand-in for the real Orca
bindings.  It verifies that injected layer polygons match an independent trimesh/shapely slice
of part + fins, that support-enabled parts are skipped, that mirrored parts keep outward normals,
and that errors return `RecoverableError` instead of crashing the slice.

## Legacy probe

`support_fins_probe.py` is the older read-only mesh/overhang probe.  It is kept for reference,
but the main path now is the built slicing-pipeline plugin described above.
