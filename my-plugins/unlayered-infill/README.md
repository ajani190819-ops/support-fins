# Unlayered Infill — not built yet

Placeholder. Nothing to install.

This folder, its catalogue entry and its Orca folder name are already reserved,
so when the plugin exists there is no plumbing to invent — and the installer
already knows to skip it (`"status": "planned"` in
[`../plugins.json`](../plugins.json)).

## The idea

Infill that isn't tied to the print's layer grid: instead of every infill path
sitting flat at one `z`, the infill follows its own continuous path through the
part. The motivation is strength — layer-aligned infill fails along the same
planes the perimeters do, so a part is only as strong as its weakest layer
boundary.

Related work already in this repo:

- **Wave Overhangs** ([`../wave-overhangs/`](../wave-overhangs/)) is the same
  family of problem — replacing a region's normal toolpaths with ones that
  propagate rather than follow the layer grid — and it is the closest existing
  reference for how to intercept Orca at the right seam.
- The **Support Fins** Orca plugin README explains *why* the slicing-pipeline
  seam (`orca.slicing.Step.posSlice`) is the right place for this kind of work
  rather than G-code post-processing:
  [`../../original-support-fins/plugins/orca/README.md`](../../original-support-fins/plugins/orca/README.md).

## When it's ready

1. Build the single-file plugin into this folder.
2. In [`../plugins.json`](../plugins.json): set `status` to `ready`, fill in
   `version`, `file`, `path`, `capabilities` and `built_from`.
3. Add a build recipe to [`../refresh-builds.py`](../refresh-builds.py) and a
   fallback line to `../../Install-Orca-Plugins.bat`.
4. `python3 my-plugins/tests/test_installer.py` will confirm the wiring.

Nobody has to re-download the installer — it reads the catalogue at run time.
