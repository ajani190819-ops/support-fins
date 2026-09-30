#!/usr/bin/env python3
"""Build the single-file Unlayered Infill OrcaSlicer plugin.

    python3 plugins/orca-infill/build.py   # -> plugins/orca-infill/build/unlayered_infill_orca.py

Inlines src/nonplanar_core.py into src/unlayered_infill_orca.py so the result is
ONE .py file to drop into OrcaSlicer.

Unlike the other two lanes this plugin has no third-party dependencies at all —
the engine is pure standard library — so there is no first-run dependency
install and no restart after installing.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "src"
OUT = HERE / "build"

IMPORT_BLOCK = '''try:
    import nonplanar_core as npc
except ImportError:  # pragma: no cover - replaced by the inlined module at build
    npc = None'''

INLINE_TEMPLATE = '''import sys as _sys, types as _types
# nonplanar_core.py inlined by build.py (single-file plugin). Registered in
# sys.modules so anything that resolves the module by name still works.
_NONPLANAR_CORE_SRC = {src}
npc = _types.ModuleType("nonplanar_core")
_sys.modules["nonplanar_core"] = npc
try:
    exec(compile(_NONPLANAR_CORE_SRC, "nonplanar_core (inlined)", "exec"), npc.__dict__)
except Exception:  # pragma: no cover - surfaced via the setup check / execute()
    _sys.modules.pop("nonplanar_core", None)
    npc = None'''


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    core = (SRC / "nonplanar_core.py").read_text(encoding="utf-8")
    plugin = (SRC / "unlayered_infill_orca.py").read_text(encoding="utf-8")
    if plugin.count(IMPORT_BLOCK) != 1:
        sys.exit("could not find the nonplanar_core import block to replace")
    inlined = INLINE_TEMPLATE.format(src=json.dumps(core))
    out = plugin.replace(IMPORT_BLOCK, inlined)
    target = OUT / "unlayered_infill_orca.py"
    target.write_text(out, encoding="utf-8")
    print(f"built {target.relative_to(HERE.parent.parent)} "
          f"({target.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
