#!/usr/bin/env python3
"""Build the single-file Wave Overhangs OrcaSlicer plugin.

    python3 plugins/orca-wave/build.py   # -> plugins/orca-wave/build/wave_overhangs_orca.py

Inlines src/wave_core.py into src/wave_overhangs_orca.py so the result is ONE .py
file to drop into OrcaSlicer. Orca installs numpy + shapely itself from the PEP 723
header on first load.

Unlike the Support Fins build there is no JS bundle here -- the whole algorithm is
pure Python (wave_core), just embedded as an inlined module.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "src"
OUT = HERE / "build"

IMPORT_BLOCK = '''try:
    import wave_core as wc
except ImportError:  # pragma: no cover - replaced by the inlined module at build
    wc = None'''

INLINE_TEMPLATE = '''import sys as _sys, types as _types
# wave_core.py inlined by build.py (single-file plugin). Executed at module load
# so its shapely/numpy imports run in Orca's audit-free startup window. It is
# registered in sys.modules so its dataclasses can resolve their annotations.
_WAVE_CORE_SRC = {src}
wc = _types.ModuleType("wave_core")
_sys.modules["wave_core"] = wc
try:
    exec(compile(_WAVE_CORE_SRC, "wave_core (inlined)", "exec"), wc.__dict__)
except Exception:  # pragma: no cover - surfaced via the setup check / execute()
    _sys.modules.pop("wave_core", None)
    wc = None'''


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    core = (SRC / "wave_core.py").read_text(encoding="utf-8")
    plugin = (SRC / "wave_overhangs_orca.py").read_text(encoding="utf-8")
    if plugin.count(IMPORT_BLOCK) != 1:
        sys.exit("could not find the wave_core import block to replace")
    inlined = INLINE_TEMPLATE.format(src=json.dumps(core))
    out = plugin.replace(IMPORT_BLOCK, inlined)
    target = OUT / "wave_overhangs_orca.py"
    target.write_text(out, encoding="utf-8")
    print(f"built {target.relative_to(HERE.parent.parent)} "
          f"({target.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
