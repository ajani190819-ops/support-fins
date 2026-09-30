#!/usr/bin/env python3
"""Rebuild every plugin from source and refresh the ready-to-install copies here.

    python3 refresh-builds.py          # rebuild + copy + sync versions
    python3 refresh-builds.py --check  # verify only, no writes (CI)

Why this exists
---------------
`<id>/` at the repo root holds the ready-to-install build of each plugin -- that is what
`Install-Orca-Plugins.bat` downloads, so it IS the released version. The source it
is built from lives under `dev/<id>/`,
because the Support Fins plugin bundles the printfins.com engine (`web/*.js`) at
build time.

Run this after touching plugin source or `web/`, then commit. That is the one step
between "I changed the code" and "the installer hands out the new version".

It also keeps `plugins.json` and each `.install_state.json` honest: the version is
read from the plugin's PEP 723 header, so bumping it in the source is enough.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys

HERE = REPO = pathlib.Path(__file__).resolve().parent
MANIFEST = HERE / "plugins.json"

# Every plugin follows the same shape, so there is no per-plugin table to keep
# in sync: dev/<id>/build.py runs in dev/<id>/ and writes dev/<id>/build/<file>.
DEV = HERE / "dev"

VERSION_RE = re.compile(r'^#\s*version\s*=\s*"([^"]+)"', re.MULTILINE)


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def read_version(built: pathlib.Path) -> str | None:
    """Version from the built plugin's PEP 723 `[tool.orcaslicer.plugin]` header."""
    head = built.read_text(encoding="utf-8")[:4000]
    m = VERSION_RE.search(head)
    return m.group(1) if m else None


def install_state(entry: dict, version: str) -> str:
    """Orca's sidecar, so a plain file copy shows up already enabled."""
    return json.dumps(
        {
            "capabilities": [{c: True} for c in entry["capabilities"]],
            "enabled": True,
            "installed_from": "local",
            "installed_version": version,
            "plugin_name": entry["name"],
        },
        indent=2,
    ) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="fail if anything here is stale instead of rewriting it")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    stale: list[str] = []
    changed: list[str] = []

    for entry in manifest["plugins"]:
        pid = entry["id"]
        if entry["status"] != "ready":
            print(f"{pid:<16} skipped ({entry['status']})")
            continue
        proj = REPO / entry["built_from"]
        if not (proj / "build.py").exists():
            fail(f"{pid}: no build.py under {entry['built_from']}")

        subprocess.run([sys.executable, "build.py"], cwd=proj, check=True)

        built = proj / "build" / entry["file"]
        if not built.exists():
            fail(f"{pid}: build did not produce {built}")

        version = read_version(built) or entry.get("version")
        if not version:
            fail(f"{pid}: could not read a version from {built}")

        dest_dir = HERE / pid
        dest_dir.mkdir(parents=True, exist_ok=True)
        targets = {
            dest_dir / entry["file"]: built.read_text(encoding="utf-8"),
            dest_dir / ".install_state.json": install_state(entry, version),
        }

        for path, text in targets.items():
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current == text:
                continue
            rel = path.relative_to(REPO)
            if args.check:
                stale.append(str(rel))
            else:
                path.write_text(text, encoding="utf-8")
                changed.append(str(rel))

        if entry.get("version") != version:
            if args.check:
                stale.append(f"plugins.json: {pid} version {entry.get('version')} != {version}")
            else:
                entry["version"] = version
                changed.append(f"plugins.json ({pid} -> {version})")

        print(f"{pid:<16} v{version}  {built.stat().st_size / 1024:.0f} KB  -> {pid}/{entry['file']}")

    if not args.check:
        text = json.dumps(manifest, indent=2) + "\n"
        if MANIFEST.read_text(encoding="utf-8") != text:
            MANIFEST.write_text(text, encoding="utf-8")

    if args.check:
        if stale:
            print("\nStale — rebuild and commit:", file=sys.stderr)
            for s in stale:
                print(f"  {s}", file=sys.stderr)
            print("\n  python3 refresh-builds.py", file=sys.stderr)
            return 1
        print("\nThe ready-to-install builds are up to date with dev/.")
        return 0

    print("\nRefreshed." if changed else "\nAlready up to date.")
    for c in changed:
        print(f"  updated {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
