#!/usr/bin/env python3
"""Guard the contract between plugins.json and Install-Orca-Plugins.bat.

    python3 tests/test_installer.py

The .bat is Windows-only, so this cannot run it. What it CAN do is check the
things that actually break in practice, none of which need Windows:

  * the catalogue is valid and every "ready" plugin really ships the file it
    promises, at the path the installer will build a URL from
  * no field contains a character that would corrupt the pipe-delimited plan
    the .bat parses with `for /f ... delims=|`
  * the .bat's built-in fallback list still matches the catalogue
  * a faithful replay of the installer's install loop does the right thing:
    first run installs, second run overwrites, a copy parked under a different
    folder name gets updated too, and a `_subscribed` cloud copy is left alone
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = REPO / "plugins.json"
BAT = REPO / "Install-Orca-Plugins.bat"

failures: list[str] = []


def check(cond: bool, msg: str) -> bool:
    if not cond:
        failures.append(msg)
    return cond


# --------------------------------------------------------------------------
# 1. catalogue
# --------------------------------------------------------------------------
manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
plugins = manifest["plugins"]
ready = [p for p in plugins if p["status"] == "ready"]

check(bool(ready), "no plugins are marked ready -- the installer would install nothing")
check(len({p["id"] for p in plugins}) == len(plugins), "duplicate plugin ids in plugins.json")

for p in plugins:
    pid = p["id"]
    for key in ("id", "name", "version", "status", "file", "path", "orca_dir", "capabilities"):
        check(key in p, f"{pid}: missing key {key!r}")

    if p["status"] != "ready":
        check(p["file"] is None and p["path"] is None,
              f"{pid}: status is {p['status']!r} but it still declares a file")
        continue

    # Pipe and caret would break the plan file / batch escaping.
    for key in ("id", "name", "version", "orca_dir", "file", "path"):
        val = str(p[key])
        check("|" not in val, f"{pid}: {key} contains '|', which breaks the installer's plan format")
        check("^" not in val and "%" not in val and "!" not in val,
              f"{pid}: {key} contains a character cmd.exe would mangle")

    check(p["path"] == f"{pid}/{p['file']}",
          f"{pid}: path {p['path']!r} should be {pid}/{p['file']}")
    check("/" in p["path"] and "\\" not in p["path"],
          f"{pid}: path must use forward slashes -- it becomes a URL")

    shipped = REPO / p["path"]
    check(shipped.exists(), f"{pid}: {p['path']} does not exist, so the download would 404")
    if shipped.exists():
        check(shipped.stat().st_size > 2000,
              f"{pid}: {p['path']} is smaller than the installer's 2000-byte sanity floor")
        head = shipped.read_text(encoding="utf-8")[:4000]
        m = re.search(r'^#\s*version\s*=\s*"([^"]+)"', head, re.MULTILINE)
        check(m is not None, f"{pid}: no PEP 723 version header in the built plugin")
        if m:
            check(m.group(1) == p["version"],
                  f"{pid}: plugins.json says v{p['version']} but the file says v{m.group(1)}")

    check(len(p["capabilities"]) >= 1, f"{pid}: no capabilities, Orca would show nothing to enable")

    sidecar = REPO / pid / ".install_state.json"
    check(sidecar.exists(), f"{pid}: missing .install_state.json")
    if sidecar.exists():
        state = json.loads(sidecar.read_text(encoding="utf-8"))
        check(state["installed_version"] == p["version"],
              f"{pid}: sidecar version {state['installed_version']} != catalogue {p['version']}")
        check([k for c in state["capabilities"] for k in c] == p["capabilities"],
              f"{pid}: sidecar capabilities do not match the catalogue")


# --------------------------------------------------------------------------
# 2. the .bat agrees with the catalogue
# --------------------------------------------------------------------------
bat = BAT.read_text(encoding="utf-8", errors="replace")

check(f'set "REPO={manifest["repo"]}"' in bat,
      f"the .bat does not point at {manifest['repo']}")
check('set "MANIFEST_PATH=plugins.json"' in bat,
      "the .bat does not fetch plugins.json")

# The fallback list is only used when PowerShell cannot parse the catalogue, so
# it silently rots unless something checks it.
for p in ready:
    line = "^|".join([p["id"], p["name"], p["version"], p["orca_dir"], p["file"], p["path"]])
    check(line in bat, f"{p['id']}: the .bat fallback list is stale, expected a line with:\n      {line}")

fallback_ids = re.findall(r'echo ([a-z-]+)\^\|', bat)
check(sorted(set(fallback_ids)) == sorted(p["id"] for p in ready),
      f"fallback list {sorted(set(fallback_ids))} != ready plugins {sorted(p['id'] for p in ready)}")


# --------------------------------------------------------------------------
# 3. replay the installer's install loop
# --------------------------------------------------------------------------
def report_and_exit() -> None:
    print(f"FAILED ({len(failures)})")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)


# The replay copies real files around, so stop here rather than crashing on a
# plugin the checks above already flagged as missing.
if any(p["status"] == "ready" and not (REPO / p["path"]).exists()
       for p in plugins if p.get("path")):
    report_and_exit()


def build_plan(man: dict, only: list[str] | None = None) -> list[dict]:
    """Mirror of the PowerShell step in :build_plan."""
    out = []
    for p in man["plugins"]:
        if p["status"] != "ready":
            continue
        if only and p["id"] not in only:
            continue
        out.append(p)
    return out


def install_one(p: dict, plugin_root: pathlib.Path) -> str:
    """Mirror of :install_one -- returns 'NEW' or 'UPDATED'."""
    src = REPO / p["path"]
    dest_dir = plugin_root / p["orca_dir"]
    was_there = (dest_dir / p["file"]).exists()
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest_dir / p["file"])
    (dest_dir / ".install_state.json").write_text(
        json.dumps(
            {
                "capabilities": [{c: True} for c in p["capabilities"]],
                "enabled": True,
                "installed_from": "local",
                "installed_version": p["version"],
                "plugin_name": p["name"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # :update_siblings -- refresh other copies, but never a cloud/subscribed one
    for other in plugin_root.rglob(p["file"]):
        if other.parent == dest_dir:
            continue
        if "_subscribed" in str(other.parent):
            continue
        shutil.copyfile(src, other)
    return "UPDATED" if was_there else "NEW"


with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp) / "OrcaSlicer" / "orca_plugins"

    plan = build_plan(manifest)
    check([p["id"] for p in plan] == [p["id"] for p in ready], "plan does not match the ready plugins")

    # -- first run: nothing installed yet
    results = {p["id"]: install_one(p, root) for p in plan}
    check(all(v == "NEW" for v in results.values()),
          f"first run should report NEW for everything, got {results}")
    for p in plan:
        installed = root / p["orca_dir"] / p["file"]
        check(installed.exists(), f"{p['id']}: not installed")
        check(installed.read_bytes() == (REPO / p["path"]).read_bytes(),
              f"{p['id']}: installed file differs from the shipped one")
        check((root / p["orca_dir"] / ".install_state.json").exists(),
              f"{p['id']}: sidecar not written")

    # -- an older copy under a different folder name, plus a cloud copy
    p0 = plan[0]
    stray = root / "SupportFinsOld"
    stray.mkdir()
    (stray / p0["file"]).write_text("stale", encoding="utf-8")
    cloud = root / "_subscribed" / "abc123"
    cloud.mkdir(parents=True)
    (cloud / p0["file"]).write_text("cloud copy", encoding="utf-8")

    # -- second run: everything already there
    results = {p["id"]: install_one(p, root) for p in plan}
    check(all(v == "UPDATED" for v in results.values()),
          f"second run should report UPDATED for everything, got {results}")
    check((stray / p0["file"]).read_bytes() == (REPO / p0["path"]).read_bytes(),
          "a copy under a different folder name should have been updated too")
    check((cloud / p0["file"]).read_text(encoding="utf-8") == "cloud copy",
          "the _subscribed cloud copy must be left alone")

    # -- PLUGIN_ONLY narrows the plan
    only = build_plan(manifest, only=[ready[0]["id"]])
    check(len(only) == 1 and only[0]["id"] == ready[0]["id"], "PLUGIN_ONLY filtering is broken")


# --------------------------------------------------------------------------
if failures:
    report_and_exit()

print(f"ok -- catalogue, .bat fallback list and install replay all agree "
      f"({len(ready)} ready plugin(s), {len(plugins)} listed)")
