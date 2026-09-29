# new-project

Placeholder for the new project.

Nothing here yet — rename this folder to whatever the project ends up being
called and replace this file.

## Conventions in this repo

- Each project is self-contained in its own top-level folder; build and run it
  from inside that folder.
- CI lives in `/.github/workflows/` at the repo root (GitHub only reads it
  there). Add a workflow there and scope it with a `paths:` filter, e.g.
  `- 'new-project/**'`, so it doesn't fire on changes to the other project.
- Shared, repo-wide ignores go in the root `.gitignore`. Patterns that contain a
  slash are anchored to the repo root, so prefix them with `new-project/`.
