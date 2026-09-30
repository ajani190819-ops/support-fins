#!/usr/bin/env python3
"""Rebuild every plugin from source and refresh the ready-to-install copies here.

    python3 my-plugins/refresh-builds.py          # rebuild + copy + sync versions
    python3 my-plugins/refresh-builds.py --check  # verify only, no writes (CI)

Why this exists
---------------
`my-plugins/<id>/` holds the ready-to-install build of each plugin -- that is what
`Install-Orca-Plugins.bat` downloads, so it IS the released version. The source it
is built from lives in the original project (`original-support-fins/plugins/...`),
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

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
MANIFEST = HERE / "plugins.json"

# id -> (build script, file the build produces)
BUILDS = {
    "support-fins": ("plugins/orca/build.py", "plugins/orca/build/support_fins_orca.py"),
    "wave-overhangs": ("plugins/orca-wave/build.py", "plugins/orca-wave/build/wave_overhangs_orca.py"),
}

VERSION_RE = re.compile(r'^#\s*version\s*=\s*"([^"]+)"', re.MULTILINE)


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def project_dir(entry: dict) -> pathlib.Path:
    """Absolute path of the source project a plugin is built from."""
    return REPO / entry["built_from"]


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
        if pid not in BUILDS:
            fail(f"{pid} is marked ready but refresh-builds.py has no build recipe for it")

        script, artifact = BUILDS[pid]
        src_root = project_dir(entry)
        if not (src_root.parent.parent / script).exists() and not (REPO / entry["built_from"]).exists():
            fail(f"{pid}: source project missing at {entry['built_from']}")

        # Build scripts expect to run from their project root (they resolve web/ and
        # plugins/shared/ relative to it).
        proj = src_root
        while proj != REPO and not (proj / "plugins").is_dir():
            proj = proj.parent
        subprocess.run([sys.executable, script], cwd=proj, check=True)

        built = proj / artifact
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

        print(f"{pid:<16} v{version}  {built.stat().st_size / 1024:.0f} KB  -> my-plugins/{pid}/{entry['file']}")

    if not args.check:
        text = json.dumps(manifest, indent=2) + "\n"
        if MANIFEST.read_text(encoding="utf-8") != text:
            MANIFEST.write_text(text, encoding="utf-8")

    if args.check:
        if stale:
            print("\nStale — rebuild and commit:", file=sys.stderr)
            for s in stale:
                print(f"  {s}", file=sys.stderr)
            print("\n  python3 my-plugins/refresh-builds.py", file=sys.stderr)
            return 1
        print("\nmy-plugins/ is up to date with the sources.")
        return 0

    print("\nRefreshed." if changed else "\nAlready up to date.")
    for c in changed:
        print(f"  updated {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
