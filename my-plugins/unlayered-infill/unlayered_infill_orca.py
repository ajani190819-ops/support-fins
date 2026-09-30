# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "Unlayered Infill"
# description = "Non-planar sparse infill: rides a sine wave in Z so successive layers interlock instead of stacking as clean planes, tapering to flat where it meets the solid skin."
# author = "Unlayered Infill plugin lane"
# version = "0.2.0"
# ///
"""Unlayered Infill for OrcaSlicer — non-planar sparse infill.

Layer-aligned infill fails along the same planes the perimeters do, so a part
is only as strong as its weakest layer boundary. This rewrites sparse-infill
moves to ride a sine wave in Z, `dz = amplitude * scale * sin(f * x)`, so each
layer's infill keys into the one below. `scale` tapers to zero as the infill
approaches the solid skin above or below, leaving the skins flat.

WHY THIS ONE IS A POST-PROCESSOR
  The other plugins in this repo inject geometry at `Step.posSlice` and let
  Orca own flow, speed and cooling — see plugins/orca/README.md for the
  argument. Non-planar infill is the one case where that cannot work: a slice
  polygon is planar by construction, so there is no way to express a wave in Z
  through the slicing seam. This plugin therefore runs at
  `Step.psGCodePostProcess`, the supported seam for rewriting the exported
  file, where `ctx.gcode_path` points at the working G-code.

SETUP -- ONE preset field
  Process preset > Others > Slicing Pipeline Plugin > "Unlayered Infill".

  That single field is all there is. Both Print.cpp (geometry steps) and
  PostProcessor.cpp (the G-code export step this plugin uses) resolve the
  same preset capability refs, so selecting the capability once wires up
  every step. This plugin returns success() immediately for all the steps
  it does not care about.

  Slice once, then run "Unlayered Infill - Check setup": it reports whether
  the export step actually ran, so you never have to guess.

REQUIREMENTS
  * Relative extrusion (`M83`). Splitting moves under absolute E (`M82`) would
    corrupt the file, so the plugin refuses rather than produce bad G-code.
  * Pure standard library — no numpy, no shapely, no first-run dependency
    install, and no restart after installing.

Engine adapted from nonPlanarInfill.py, Copyright (c) 2025 Roman Tenger
(TenTech), GPL-3.0 — https://github.com/TengerTechnologies/NonPlanarInfill.
This file is likewise GPL-3.0. The five behavioural fixes relative to that
tool are documented in nonplanar_core.py and pinned by
plugins/orca-infill/tests/.
"""
import json
import os
import time

import orca

# nonplanar_core is inlined by build.py for the single-file install; during
# tests and development it is imported from src/ next to this file.
import sys as _sys, types as _types
# nonplanar_core.py inlined by build.py (single-file plugin). Registered in
# sys.modules so anything that resolves the module by name still works.
_NONPLANAR_CORE_SRC = "\"\"\"Non-planar infill engine: pure text in, pure text out, stdlib only.\n\nAdapted from `nonPlanarInfill.py`, Copyright (c) 2025 Roman Tenger (TenTech),\nGPL-3.0 \u2014 https://github.com/TengerTechnologies/NonPlanarInfill \u2014 by way of the\n\"Non-Planar Infill Tool\" kept verbatim at\n`my-plugins/unlayered-infill/reference/nonplanar_infill_tool.py`.\n\nThe idea is unchanged: inside sparse-infill sections, split each extrusion into\nshort segments and ride a sine wave in Z, `dz = amplitude * scale * sin(f * x)`,\nwith `scale` tapering to zero as the infill approaches the solid skin above or\nbelow it. Successive layers then interlock instead of stacking as clean planes.\n\nFive behaviours differ from the reference, each pinned by a test in\n`tests/test_nonplanar_core.py`:\n\n1. **Extrusion is attached to the right move.** In G-code the `E` on a line\n   describes the move that *ends* at that line's coordinates. The reference\n   read `(x, y)` off the current line as the segment *start* and `(x, y)` off\n   the next line as the end, so every stroke was printed with its neighbour's\n   extrusion. On a plain rectilinear layer that under-extrudes the long infill\n   strokes by ~44% and over-extrudes the short repositioning moves.\n\n2. **No duplicated points.** `segment_line()` returned both endpoints, so each\n   emitted stroke re-stated the previous stroke's final point *with a share of\n   the extrusion* \u2014 a zero-length extruding move, i.e. a blob, at every\n   junction (44 of them in a ten-layer test cube).\n\n3. **Z is restored on the way out.** The reference left the nozzle at whatever\n   displaced Z the last wave segment reached. Anything printed afterwards in\n   the same layer \u2014 gap fill, a top surface, the wipe \u2014 ran at that wrong\n   height until the next layer change reset it.\n\n4. **Orca's skins are recognised.** Solid layers were matched on\n   `\"solid infill\"` only. OrcaSlicer and Bambu Studio label the outermost\n   skins `;TYPE:Top surface` and `;TYPE:Bottom surface`, so the real bottom\n   was never found and the taper measured from the build plate instead. That\n   makes the wave far too aggressive right next to the bottom skin.\n\n5. **The taper can't inverate.** `next_top_layer` kept a stale value once the\n   nozzle rose above the topmost solid layer, which made `d_top` negative and\n   flipped the wave's sign. Infill that isn't bracketed by solid above *and*\n   below now simply gets no wave.\n\nSection markers are matched on `;TYPE:` lines only, and case-insensitively:\nPrusaSlicer writes `;TYPE:Internal infill`, Orca `;TYPE:Sparse infill`.\n\"\"\"\nimport bisect\nimport math\nimport re\n\nTYPE_PREFIX = \";type:\"\nINFILL_MARKERS = (\"internal infill\", \"sparse infill\")\n# \"internal solid infill\" contains \"solid infill\"; Orca/Bambu name the outer\n# skins \"top surface\" / \"bottom surface\", which the reference missed.\nSOLID_MARKERS = (\"solid infill\", \"top surface\", \"bottom surface\")\n\nDEFAULT_AMPLITUDE = \"-0.2\"   # mm, or \"%\"/\"x\" of layer height; negative dips in\nDEFAULT_FREQUENCY = 1.5\nDEFAULT_SEGMENT_MM = 1.0\nDEFAULT_CELL_MM = 0.6        # XY resolution of the solid-column map\nDEFAULT_BLEND_MM = 2.0       # smooth the taper across this radius of columns\n\n# Stamped into the output so a second pass is a no-op. Orca can invoke\n# psGCodePostProcess more than once for one slice (file export and network\n# upload are separate calls), and waving an already-waved file would double\n# every displacement.\nMARKER_PREFIX = \"; unlayered-infill\"\nMARKER_VERSION = \"0.2\"\nMARKER = f\"{MARKER_PREFIX} v{MARKER_VERSION} (non-planar sparse infill)\\n\"\n\n_WORD = re.compile(r\"([A-Za-z])\\s*([-+]?\\d*\\.?\\d+)\")\n_Z = re.compile(r\"Z([-+]?\\d*\\.?\\d+)\")\n\n\nclass NonPlanarError(Exception):\n    \"\"\"A problem worth stopping for, explained in plain English.\"\"\"\n\n\ndef parse_words(line):\n    \"\"\"`G1 X1 Y2 E.5 ; comment` -> {'G':1.0,'X':1.0,'Y':2.0,'E':0.5}.\"\"\"\n    body = line.split(\";\", 1)[0]\n    if not body.strip():\n        return None\n    words = {}\n    for m in _WORD.finditer(body):\n        words.setdefault(m.group(1).upper(), float(m.group(2)))\n    return words or None\n\n\ndef section_name(line):\n    \"\"\"The section label if this is a `;TYPE:` line, else None.\"\"\"\n    low = line.lower()\n    if low.startswith(TYPE_PREFIX):\n        return low[len(TYPE_PREFIX):].strip()\n    return None\n\n\ndef is_infill_section(name):\n    return any(m in name for m in INFILL_MARKERS)\n\n\ndef is_solid_section(name):\n    return any(m in name for m in SOLID_MARKERS)\n\n\ndef detect_extrusion_mode(lines):\n    \"\"\"'relative' (M83), 'absolute' (M82), or 'unknown'.\"\"\"\n    head = \"\\n\".join(lines[:400])\n    if \"M83\" in head:\n        return \"relative\"\n    if \"M82\" in head:\n        return \"absolute\"\n    tail = \"\\n\".join(lines[-4000:])  # the slicer config block lives at the end\n    if \"relative_extrusion = 1\" in tail or \"use_relative_e_distances = 1\" in tail:\n        return \"relative\"\n    if \"relative_extrusion = 0\" in tail or \"use_relative_e_distances = 0\" in tail:\n        return \"absolute\"\n    return \"unknown\"\n\n\ndef detect_layer_height(lines):\n    \"\"\"The layer height actually being printed: the most common `;HEIGHT:`,\n    falling back to the slicer's `layer_height = ` config line.\"\"\"\n    heights = []\n    config_lh = None\n    for line in lines:\n        if line.startswith(\";HEIGHT:\"):\n            try:\n                heights.append(float(line[len(\";HEIGHT:\"):].strip()))\n            except ValueError:\n                pass\n        elif config_lh is None and \"layer_height\" in line:\n            m = re.search(r\";\\s*layer_height\\s*=\\s*(\\d*\\.?\\d+)\", line)\n            if m and \"first_layer\" not in line:\n                config_lh = float(m.group(1))\n    if heights:\n        from statistics import multimode\n        return max(multimode(heights))\n    return config_lh\n\n\ndef resolve_amplitude(spec, lines):\n    \"\"\"'-0.2' -> mm; '200%' / '-1.5x' -> that fraction of the layer height.\n\n    Returns (millimetres, human description).\n    \"\"\"\n    s = str(spec).strip()\n    if not s:\n        raise NonPlanarError(\n            \"No amplitude given. Use mm (e.g. -0.2), a percent of layer height \"\n            \"(e.g. -150%), or a multiple (e.g. -1.5x).\")\n    relative = s.endswith(\"%\") or s.lower().endswith(\"x\")\n    try:\n        value = float(s[:-1]) if relative else float(s)\n    except ValueError:\n        raise NonPlanarError(\n            f\"Could not understand amplitude {s!r}. Use mm (e.g. -0.2), a \"\n            f\"percent of layer height (e.g. -150%), or a multiple (e.g. -1.5x).\")\n    if not relative:\n        return value, f\"{value:.3f} mm (fixed value)\"\n    mult = value / 100.0 if s.endswith(\"%\") else value\n    lh = detect_layer_height(lines)\n    if lh is None:\n        raise NonPlanarError(\n            f\"You gave the amplitude as {s} of layer height, but this G-code has \"\n            \"no layer height in it (no ';HEIGHT:' comments and no \"\n            \"'layer_height =' config line). Give the amplitude in plain mm \"\n            \"instead, e.g. -0.2.\")\n    amp = mult * lh\n    return amp, f\"{s} of layer height {lh:.3f} mm = {amp:.3f} mm\"\n\n\ndef already_processed(lines):\n    \"\"\"Has this file already been waved? Then leave it completely alone.\"\"\"\n    return any(line.startswith(MARKER_PREFIX) for line in lines)\n\n\nclass SolidGrid:\n    \"\"\"Where the part has solid skin, mapped as a grid of XY columns.\n\n    A single global list of solid Z heights is only correct for a part whose\n    skins are flat planes spanning the whole footprint. Give it a ledge, a\n    bridge, a chamfered top, or two towers of different heights and it fails\n    in a way that matters: every column is told its roof is the *highest*\n    skin anywhere in the print, so infill directly under a low ledge thinks\n    it has metres of headroom and waves at full amplitude straight into it.\n\n    Recording solid heights per XY column instead gives every infill move a\n    floor and roof measured in its own column \u2014 the wave fades against the\n    skin it is actually about to hit.\n    \"\"\"\n\n    __slots__ = (\"cell\", \"columns\", \"_raw_cache\", \"_disc\")\n\n    def __init__(self, cell_mm=DEFAULT_CELL_MM):\n        self.cell = max(0.05, float(cell_mm))\n        self.columns = {}\n        self._raw_cache = {}\n        self._disc = None\n\n    def key(self, x, y):\n        c = self.cell\n        return (int(math.floor(x / c)), int(math.floor(y / c)))\n\n    def add_move(self, x0, y0, x1, y1, z):\n        \"\"\"Mark every column the solid extrusion (x0,y0)->(x1,y1) crosses.\"\"\"\n        zr = round(z, 4)\n        step = self.cell * 0.5\n        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step) + 1)\n        cols = self.columns\n        for i in range(n + 1):\n            t = i / n\n            cols.setdefault(self.key(x0 + t * (x1 - x0),\n                                     y0 + t * (y1 - y0)), set()).add(zr)\n\n    def finalize(self):\n        self.columns = {k: sorted(v) for k, v in self.columns.items()}\n        return self\n\n    def _raw(self, key, z, full_strength):\n        \"\"\"Taper for one column: 0 at its own skins, peak mid-span.\n\n        Returns None when the column has no solid at all (nothing to measure\n        against), which the blend treats as \"no opinion\" rather than zero.\n        \"\"\"\n        ck = (key, z)\n        hit = self._raw_cache.get(ck)\n        if hit is not None:\n            return hit[0]\n        zs = self.columns.get(key)\n        if not zs:\n            self._raw_cache[ck] = (None,)\n            return None\n        i = bisect.bisect_left(zs, z - 1e-9)\n        if i < len(zs) and abs(zs[i] - z) <= 1e-6:\n            val = 0.0                      # this column is solid right here\n        else:\n            below = zs[i - 1] if i > 0 else None\n            j = bisect.bisect_right(zs, z + 1e-9)\n            above = zs[j] if j < len(zs) else None\n            if below is None or above is None or above - below <= 0:\n                val = 0.0                  # unbracketed: no wave, no sign flip\n            else:\n                val = min(above - z, z - below) / (above - below)\n                if full_strength:\n                    val = min(1.0, val * 2.0)\n        self._raw_cache[ck] = (val,)\n        return val\n\n    def _offsets(self, blend_mm):\n        \"\"\"Cell offsets within the blend radius, computed once.\"\"\"\n        if self._disc is None:\n            r = min(8, int(math.ceil(blend_mm / self.cell)))\n            self._disc = [(dx, dy)\n                          for dx in range(-r, r + 1)\n                          for dy in range(-r, r + 1)\n                          if math.hypot(dx, dy) * self.cell <= blend_mm + 1e-9]\n        return self._disc\n\n    def scale(self, x, y, z, full_strength=False, blend_mm=DEFAULT_BLEND_MM):\n        \"\"\"Blended taper at a point.\n\n        Neighbouring columns can have very different floors and roofs \u2014 at the\n        edge of a ledge, one column's roof is 2 mm up and the next one's is\n        20 mm up. Taking each column's answer literally puts a step in the\n        wave exactly there. Averaging over a small disc turns that step into a\n        ramp, which is what `blend_mm` buys.\n\n        Columns with no solid recorded are skipped rather than counted as\n        zero: they are usually just gaps between solid extrusion lines, and\n        counting them would damp the wave everywhere.\n        \"\"\"\n        z = round(z, 4)\n        if blend_mm <= 0:\n            return self._raw(self.key(x, y), z, full_strength) or 0.0\n        ix, iy = self.key(x, y)\n        c = self.cell\n        total = weight = 0.0\n        for dx, dy in self._offsets(blend_mm):\n            val = self._raw((ix + dx, iy + dy), z, full_strength)\n            if val is None:\n                continue\n            # distance from the sample point to that column's centre\n            cx = (ix + dx + 0.5) * c\n            cy = (iy + dy + 0.5) * c\n            d = math.hypot(cx - x, cy - y)\n            w = 1.0 - d / blend_mm\n            if w <= 0.0:\n                continue\n            total += w * val\n            weight += w\n        return total / weight if weight > 0.0 else 0.0\n\n\ndef build_solid_grid(lines, cell_mm=DEFAULT_CELL_MM):\n    \"\"\"Rasterise every solid-skin extrusion into an XY column map.\"\"\"\n    grid = SolidGrid(cell_mm)\n    x = y = None\n    z = 0.0\n    solid = False\n    solid_heights = set()\n    for line in lines:\n        name = section_name(line)\n        if name is not None:\n            solid = is_solid_section(name)\n            continue\n        words = parse_words(line)\n        if not words or words.get(\"G\") not in (0.0, 1.0):\n            continue\n        nx = words.get(\"X\", x)\n        ny = words.get(\"Y\", y)\n        if \"Z\" in words:\n            z = words[\"Z\"]\n        e = words.get(\"E\")\n        if solid and e is not None and e > 0 and None not in (x, y, nx, ny):\n            grid.add_move(x, y, nx, ny, z)\n            solid_heights.add(round(z, 4))\n        x, y = nx, ny\n    grid.finalize()\n    return grid, sorted(solid_heights)\n\n\ndef _empty_stats(**over):\n    base = {\"amplitude_mm\": 0.0, \"amplitude_desc\": \"\", \"extrusion_mode\": \"\",\n            \"solid_layers\": 0, \"solid_columns\": 0, \"sections\": 0, \"moves\": 0,\n            \"segments\": 0, \"max_wiggle\": 0.0, \"skipped_unbracketed\": 0,\n            \"already_processed\": False, \"cell_mm\": 0.0, \"blend_mm\": 0.0,\n            \"full_strength\": False}\n    base.update(over)\n    return base\n\n\ndef process(lines, amplitude_spec=DEFAULT_AMPLITUDE, frequency=DEFAULT_FREQUENCY,\n            segment_mm=DEFAULT_SEGMENT_MM, require_relative_e=True,\n            cell_mm=DEFAULT_CELL_MM, blend_mm=DEFAULT_BLEND_MM,\n            full_strength=False):\n    \"\"\"Rewrite sparse-infill moves as wavy ones. Returns (out_lines, stats).\"\"\"\n    # Orca may run the export step twice for one slice (file + upload). Waving\n    # an already-waved file would double every displacement, so bail out.\n    if already_processed(lines):\n        return list(lines), _empty_stats(already_processed=True)\n\n    amplitude, amp_desc = resolve_amplitude(amplitude_spec, lines)\n\n    mode = detect_extrusion_mode(lines)\n    if mode == \"absolute\" and require_relative_e:\n        raise NonPlanarError(\n            \"This G-code uses ABSOLUTE extrusion (M82). Splitting moves would \"\n            \"corrupt it.\\n\\nFix: OrcaSlicer > Printer Settings > Advanced > \"\n            \"'Use relative E distances', then slice again.\")\n\n    grid, solids = build_solid_grid(lines, cell_mm)\n    frequency = float(frequency)\n    segment_mm = max(0.05, float(segment_mm))\n    blend_mm = max(0.0, float(blend_mm))\n    full_strength = bool(full_strength)\n\n    out = []\n    x = y = z = None\n    in_infill = False\n    displaced = False           # nozzle currently sitting at a wave-shifted Z\n    sections = moves = segments = 0\n    max_wiggle = 0.0\n    skipped_unbracketed = 0\n\n    def restore_z():\n        \"\"\"Never leave the nozzle on the wave once the infill stroke ends.\"\"\"\n        nonlocal displaced\n        if displaced and z is not None:\n            out.append(f\"G1 Z{z:.3f}\\n\")\n            displaced = False\n\n    for line in lines:\n        name = section_name(line)\n        if name is not None:\n            was = in_infill\n            in_infill = is_infill_section(name)\n            if in_infill and not was:\n                sections += 1\n            if not in_infill:\n                restore_z()\n            out.append(line)\n            continue\n\n        words = parse_words(line)\n        if not words or words.get(\"G\") not in (0.0, 1.0):\n            out.append(line)\n            continue\n\n        nx = words.get(\"X\", x)\n        ny = words.get(\"Y\", y)\n        e = words.get(\"E\")\n\n        if \"Z\" in words:                 # the slicer's own Z always wins\n            z = words[\"Z\"]\n            displaced = False\n\n        movable = (in_infill and e is not None and e > 0 and \"Z\" not in words\n                   and None not in (x, y, z) and (nx != x or ny != y))\n        if movable:\n            # sample the taper at the midpoint of the stroke\n            scale = grid.scale((x + nx) * 0.5, (y + ny) * 0.5, z,\n                               full_strength, blend_mm)\n            if scale > 0.0:\n                length = math.hypot(nx - x, ny - y)\n                n = max(1, int(length // segment_mm))\n                feed = words.get(\"F\")\n                # Hand out the extrusion so the printed digits sum to exactly\n                # `e`. Rounding each segment independently drifts, and across\n                # a whole print that drift is systematic under/over-extrusion.\n                spent = 0.0\n                for i in range(1, n + 1):      # skip i=0: we are already there\n                    t = i / n\n                    sx = x + t * (nx - x)\n                    sy = y + t * (ny - y)\n                    dz = amplitude * scale * math.sin(frequency * sx)\n                    if abs(dz) > max_wiggle:\n                        max_wiggle = abs(dz)\n                    share = round(e * t - spent, 5)\n                    spent += share\n                    tail = f\" F{feed:.0f}\" if (i == 1 and feed is not None) else \"\"\n                    out.append(f\"G1 X{sx:.3f} Y{sy:.3f} Z{z + dz:.3f} \"\n                               f\"E{share:.5f}{tail}\\n\")\n                segments += n\n                moves += 1\n                displaced = True\n                x, y = nx, ny\n                continue\n            skipped_unbracketed += 1\n\n        restore_z()\n        out.append(line)\n        x, y = nx, ny\n\n    restore_z()\n\n    if moves:\n        out.insert(0, MARKER)\n\n    return out, {\n        \"amplitude_mm\": amplitude,\n        \"amplitude_desc\": amp_desc,\n        \"extrusion_mode\": mode,\n        \"solid_layers\": len(solids),\n        \"solid_columns\": len(grid.columns),\n        \"sections\": sections,\n        \"moves\": moves,\n        \"segments\": segments,\n        \"max_wiggle\": max_wiggle,\n        \"skipped_unbracketed\": skipped_unbracketed,\n        \"already_processed\": False,\n        \"cell_mm\": grid.cell,\n        \"blend_mm\": blend_mm,\n        \"full_strength\": full_strength,\n    }\n"
npc = _types.ModuleType("nonplanar_core")
_sys.modules["nonplanar_core"] = npc
try:
    exec(compile(_NONPLANAR_CORE_SRC, "nonplanar_core (inlined)", "exec"), npc.__dict__)
except Exception:  # pragma: no cover - surfaced via the setup check / execute()
    _sys.modules.pop("nonplanar_core", None)
    npc = None


_DEFAULTS = {
    "enabled": True,
    # mm, or a share of the layer height: "-150%", "-1.5x".
    # Negative dips the wave into the part, which keeps the nozzle clear.
    "amplitude": "-0.2",
    "frequency": 1.5,
    "segment_mm": 1.0,
    # XY resolution of the solid-skin map. Each column gets its own floor and
    # roof, so a ledge or a short neighbouring tower cannot distort the taper
    # somewhere else in the part.
    "cell_mm": 0.6,
    # Smooth the taper across this radius of columns, so the step between two
    # columns with very different roofs becomes a ramp instead of a kink.
    "blend_mm": 2.0,
    # The classic Tenger taper peaks at half the amplitude even mid-span.
    # Turning this on lets it reach the full amplitude in the middle while
    # still fading to nothing at the skins.
    "full_strength": False,
    # Refuse to run on absolute-E G-code rather than corrupt it.
    "require_relative_e": True,
    # Write a JSONL record of every run next to the plugin.
    "log": True,
}


def _cfg(self):
    try:
        src = json.loads(self.get_config() or "{}")
    except (AttributeError, TypeError, ValueError):
        src = {}
    cfg = dict(_DEFAULTS)
    if isinstance(src, dict):
        for k, v in src.items():
            if k in cfg:
                cfg[k] = v
    return cfg


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(v)


def _state_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "unlayered_infill_state.json")


def _load_state():
    """What happened on previous exports. Empty dict if there is no history."""
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def _record_run(**fields):
    """Remember that the export step fired, and what it did.

    Which preset field drives Step.psGCodePostProcess differs between
    OrcaSlicer builds, so rather than read a setting whose name we cannot
    rely on, the setup check reports what was actually observed.
    """
    st = _load_state()
    st["seam_ever"] = True
    st["last_run_at"] = time.time()
    st.update(fields)
    _save_state(st)


def _write_log(entry, enabled=True):
    if not enabled:
        return
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "unlayered_infill_log.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


class UnlayeredInfill(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Unlayered Infill"

    def get_default_config(self):
        return _DEFAULTS

    def execute(self, ctx):
        # Every other step is somebody else's business.
        if ctx.step != orca.slicing.Step.psGCodePostProcess:
            return orca.ExecutionResult.success()

        cfg = _cfg(self)
        if not _truthy(cfg["enabled"]):
            return orca.ExecutionResult.success("Unlayered Infill: disabled")
        if npc is None:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                "Unlayered Infill: the engine module is missing. Reinstall the "
                "plugin (the single-file build should have it inlined).")

        path = getattr(ctx, "gcode_path", "") or ""
        if not path or not os.path.isfile(path):
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                f"Unlayered Infill: no G-code file to work on (gcode_path={path!r})")

        log = {"phase": "gcode", "path": path, "started": time.time()}
        do_log = _truthy(cfg["log"])
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            out, stats = npc.process(
                lines,
                amplitude_spec=cfg["amplitude"],
                frequency=cfg["frequency"],
                segment_mm=cfg["segment_mm"],
                require_relative_e=_truthy(cfg["require_relative_e"]),
                cell_mm=cfg["cell_mm"],
                blend_mm=cfg["blend_mm"],
                full_strength=_truthy(cfg["full_strength"]),
            )
        except npc.NonPlanarError as e:
            # A clear, actionable stop -- surfaced as a slicing error. The seam
            # still demonstrably fired, so record that.
            _record_run(last_refused=str(e))
            log["refused"] = str(e)
            _write_log(log, do_log)
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError, f"Unlayered Infill: {e}")
        except Exception as e:  # never corrupt an export over a bug in here
            log["error"] = f"{type(e).__name__}: {e}"
            _write_log(log, do_log)
            return orca.ExecutionResult.success(
                f"Unlayered Infill: skipped, file left untouched "
                f"({type(e).__name__}: {e})")

        if stats["already_processed"]:
            # Orca ran the export step twice for one slice. The first call did
            # the work; waving again would double every displacement.
            _record_run(last_skipped="already waved")
            log["already_processed"] = True
            log["seconds"] = round(time.time() - log["started"], 3)
            _write_log(log, do_log)
            return orca.ExecutionResult.success(
                "Unlayered Infill: already applied to this file, left as is")

        if not stats["moves"]:
            _record_run(last_moves=0, last_sections=stats["sections"])
            log.update(stats)
            log["seconds"] = round(time.time() - log["started"], 3)
            _write_log(log, do_log)
            return orca.ExecutionResult.success(
                "Unlayered Infill: nothing to do — no sparse infill was found "
                "between two solid skins. Thin all-wall parts, 0% infill, and "
                "parts with no top/bottom solid layers have nothing to wave.")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(out)
        except Exception as e:
            log["error"] = f"write failed: {type(e).__name__}: {e}"
            _write_log(log, do_log)
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                f"Unlayered Infill: could not write the G-code: {e}")

        _record_run(last_moves=stats["moves"], last_sections=stats["sections"],
                    last_segments=stats["segments"], last_skipped=None,
                    last_refused=None, last_columns=stats["solid_columns"])
        log.update(stats)
        log["seconds"] = round(time.time() - log["started"], 3)
        _write_log(log, do_log)
        return orca.ExecutionResult.success(
            f"Unlayered Infill: {stats['moves']} infill move(s) across "
            f"{stats['sections']} section(s) rewritten as {stats['segments']} "
            f"wavy segment(s); amplitude {stats['amplitude_desc']}, "
            f"largest Z offset {stats['max_wiggle']:.3f} mm")


class UnlayeredInfillCheck(orca.script.ScriptPluginCapabilityBase):
    def get_name(self):
        return "Unlayered Infill - Check setup"

    def execute(self):
        lines = ["Unlayered Infill setup check"]
        lines.append(f"engine: {'inlined/available' if npc else 'MISSING (rebuild)'}")
        if npc is None:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                "The engine module is missing — reinstall the plugin.")
        lines.append("dependencies: none (pure standard library, no restart needed)")
        lines.append("")
        st = _load_state()
        lines.append("--- what the last export actually did ---")
        if not st.get("seam_ever"):
            lines.append("The G-code step has NEVER RUN on this machine.")
            lines.append("")
            lines.append("Select it here:")
            lines.append("  Process preset -> Others -> Slicing Pipeline Plugin")
            lines.append("  -> Unlayered Infill")
            lines.append("")
            lines.append("That one field drives every step, including the")
            lines.append("G-code export step this plugin uses. Then slice,")
            lines.append("export, and run this check again.")
        elif st.get("last_refused"):
            lines.append("The G-code step ran, but the plugin refused:")
            lines.append(f"  {st['last_refused']}")
        else:
            lines.append(f"The G-code step ran: {st.get('last_moves', 0)} infill "
                         f"move(s) across {st.get('last_sections', 0)} section(s)")
            if not st.get("last_moves"):
                lines.append("...but there was no sparse infill between two")
                lines.append("solid skins to wave. Check infill density is not")
                lines.append("0% and the part has top/bottom solid layers.")
        lines.append("")
        lines.append("Your printer must use RELATIVE extrusion:")
        lines.append("  Printer Settings -> Advanced -> Use relative E distances")
        lines.append("Otherwise the plugin refuses to run rather than corrupt")
        lines.append("the file.")
        lines.append("")
        lines.append("EXPERIMENTAL: check the G-code Preview and print a small")
        lines.append("test part before trusting it on a long job.")
        return orca.ExecutionResult.success("\n".join(lines))


@orca.plugin
class UnlayeredInfillPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(UnlayeredInfill)
        orca.register_capability(UnlayeredInfillCheck)
