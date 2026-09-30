# support-fins (repo)

This repository holds several projects, side by side.

| Folder | What it is |
| --- | --- |
| [`support-fins/`](support-fins/) | The original Support Fins project — the browser app at [printfins.com](https://printfins.com), the slicer plugins, the prototype and the test suite. Self-contained: build, run and test it from inside that folder. See its [README](support-fins/README.md). |
| [`wave-overhangs/`](wave-overhangs/) | The Wave Overhangs OrcaSlicer plugin — rebuilds flat overhangs as ripples that conform to the support perimeter and grow outward layer by layer, at slice time. See its [README](wave-overhangs/README.md). |
| [`orca-nightly-installer/`](orca-nightly-installer/) | A double-click `.bat` that installs or updates the built OrcaSlicer plugins into your Orca data folder. See its [README](orca-nightly-installer/README.md). |

## Layout notes

Everything the old project needs moved with it (`web/`, `plugins/`, `prototype/`,
`tests/`, `docs/`, `assets/`, `dev-server.py`, `Dockerfile`, `docker-compose.yml`,
`nginx.conf`, `.dockerignore`). Three things deliberately stayed at the repo root:

- **`.github/workflows/`** — GitHub only reads workflows from the root. Both
  workflows were updated to run inside `support-fins/`.
- **`.gitignore`** — one file for the whole repo. Its project-specific rules are
  prefixed with `support-fins/`.
- **`wrangler.jsonc`** — Cloudflare's git integration looks for the Wrangler
  config at the configured root directory (the repo root by default), so moving
  it would break the live deploy. It now points at `./support-fins/web`.

`LICENSE` (MIT) sits at the root and is also copied into `support-fins/` so that
folder stays a self-contained Docker build context.

`wave-overhangs/` was `new-project/` (the placeholder this README used to
describe). It keeps the same conventions: self-contained, own tests, own CI
workflow scoped with a `wave-overhangs/**` path filter.
