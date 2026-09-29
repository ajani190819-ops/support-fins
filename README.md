# support-fins (repo)

This repository holds two separate projects, side by side.

| Folder | What it is |
| --- | --- |
| [`support-fins/`](support-fins/) | The original Support Fins project — the browser app at [printfins.com](https://printfins.com), the slicer plugins, the prototype and the test suite. Self-contained: build, run and test it from inside that folder. See its [README](support-fins/README.md). |
| [`new-project/`](new-project/) | New work. Currently a placeholder — see its [README](new-project/README.md). |

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
