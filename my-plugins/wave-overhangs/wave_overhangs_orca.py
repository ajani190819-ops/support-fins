# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.0", "shapely>=2.0"]
#
# [tool.orcaslicer.plugin]
# name = "Wave Overhangs"
# description = "Experimental: print steep overhangs support-free by replacing the overhang region with wave-propagated toolpaths (port of the WaveOverhangs fork's algorithm as a slicing-pipeline plugin)."
# author = "Wave Overhangs plugin lane"
# version = "0.0.3"
# ///
"""Wave Overhangs for OrcaSlicer -- experimental slicing-pipeline plugin.

WHAT IT AIMS TO DO
  Reproduce dennisklappe/OrcaSlicer-WaveOverhangs (a C++ fork) as a Python plugin:
  detect each layer's unsupported overhang, and fill it with wave-propagated
  toolpaths (expanding fronts anchored to the supported edge) printed slowly with
  full cooling, instead of ordinary infill that would sag.

HOW IT WORKS (two seams, one plugin instance)
  1. Geometry step (Step.posSlice, per object): read each layer's sliced polygons
     and the layer below, compute the overhang region and the wave polylines
     (wave_core), and stash them keyed by layer Z. Optionally CARVE the overhang
     region out of the layer's slices so Orca does not also fill it -- the waves
     replace it rather than overlapping.
  2. G-code seam (Step.psGCodePostProcess): walk the exported G-code layer by
     layer and splice the stashed wave moves into each matching layer, with the
     wave-specific speed / fan / flow.

STATUS: EXPERIMENTAL SPIKE. The wave generator and G-code emitter are unit-tested
offline (plugins/orca-wave/tests). What still needs validation on a real Orca
build is called out inline below, chiefly:
  * OBJECT->BED coordinate mapping for the spliced moves (see _bed_offset).
  * Carving vs. keeping N wave-overhang perimeters.
Nothing here may crash a slice: every hook is wrapped and degrades to a no-op.
"""
import atexit
import json
import math
import os
import time

import orca

# --- audit-safe dependency load (see the Support Fins plugin for the rationale:
#     Orca's plugin audit blocks any path containing "conf" during a capability,
#     and numpy imports numpy/__config__.py -- so import at load, never lazily) ---
np = None
shapely = None
_DEPS_ERROR = None


def _import_deps():
    global np, shapely, _DEPS_ERROR
    try:
        import numpy
        import numpy.__config__            # noqa: F401  (warm "conf" file now)
        import numpy._core._ufunc_config    # noqa: F401
        import shapely as _sh
        import shapely.geometry             # noqa: F401
        import shapely.ops                  # noqa: F401
        np = numpy
        shapely = _sh
        _DEPS_ERROR = None
    except ImportError:
        _DEPS_ERROR = ("Wave Overhangs is finishing its first-time dependency "
                       "install (numpy, shapely). Fully quit and reopen "
                       "OrcaSlicer, then try again.")
    except Exception as e:  # pragma: no cover - defensive
        _DEPS_ERROR = f"Wave Overhangs could not load its deps: {type(e).__name__}: {e}"


_import_deps()

# wave_core is inlined by build.py for the single-file install; during tests and
# development it is imported from src/ next to this file.
import sys as _sys, types as _types
# wave_core.py inlined by build.py (single-file plugin). Executed at module load
# so its shapely/numpy imports run in Orca's audit-free startup window. It is
# registered in sys.modules so its dataclasses can resolve their annotations.
_WAVE_CORE_SRC = "\"\"\"Wave-overhang toolpath core -- pure geometry, no OrcaSlicer bindings.\n\nThis is the algorithm behind dennisklappe/OrcaSlicer-WaveOverhangs (itself a port\nof stmcculloch/PrusaSlicer-WaveOverhangs), reimplemented as a slicer-independent\nPython module so it can run inside an Orca slicing-pipeline plugin AND be unit\ntested offline with shapely.\n\nThe idea (see waveoverhangs.com \"How it works\"):\n\n  * For each layer, the *overhang region* is the part of the layer that sticks out\n    past the layer below -- it has nothing underneath it.\n  * A *seed* is taken at the supported edge (the boundary between the overhang and\n    the material below).\n  * Wavefronts are grown outward from the seed into the overhang: each front is\n    the set of points a fixed distance further from the supported edge than the\n    last. Because we grow by buffering the supported region, the fronts naturally\n    diffract around corners and holes, exactly like ripples on a pond.\n  * Each front becomes an extrusion polyline. A pattern mode decides how the\n    fronts are connected into a print order.\n\nEverything here is in millimetres, in the object's own XY frame. Mapping into the\nprinter's absolute G-code coordinates is the plugin's job (see the plugin module).\n\"\"\"\nfrom __future__ import annotations\n\nimport math\nfrom dataclasses import dataclass, field\n\nfrom shapely.geometry import (\n    GeometryCollection,\n    LineString,\n    MultiLineString,\n    MultiPolygon,\n    Polygon,\n)\nfrom shapely.ops import linemerge, unary_union\n\n# ---------------------------------------------------------------------------------\n# Configuration\n# ---------------------------------------------------------------------------------\n\n\n@dataclass\nclass WaveConfig:\n    \"\"\"Mirrors the fork's tunables (waveoverhangs.com \"~20 expert tunables\").\"\"\"\n\n    # Detection\n    overhang_tol: float = 0.05        # mm the layer below is grown before subtracting\n    min_overhang_area: float = 0.5    # mm^2, ignore slivers\n\n    # Wave field\n    line_spacing: float = 0.35        # mm, centreline spacing between wave tracks\n    line_width: float = 0.40          # mm, extrusion width of a wave line\n    max_iterations: int = 400         # safety cap on wavefronts per region\n    perimeter_overlap: float = 0.10   # mm, push the field toward the kept perimeter\n\n    # Pattern: \"monotonic\" | \"zigzag\" | \"smart\"\n    pattern: str = \"smart\"\n\n    # Motion / cooling / flow (used by the G-code emitter)\n    layer_height: float = 0.20\n    flow_ratio: float = 1.0\n    filament_diameter: float = 1.75\n    print_speed: float = 2.0          # mm/s\n    travel_speed: float = 120.0       # mm/s\n    fan: float = 1.0                  # 0..1, forced during wave extrusion\n\n    def mm3_per_mm(self) -> float:\n        return self.line_width * self.layer_height * self.flow_ratio\n\n    def e_per_mm(self) -> float:\n        area = math.pi * (self.filament_diameter / 2.0) ** 2\n        return self.mm3_per_mm() / area\n\n\n# ---------------------------------------------------------------------------------\n# Geometry helpers\n# ---------------------------------------------------------------------------------\n\n\ndef _iter_lines(geom):\n    \"\"\"Yield LineStrings from any shapely geometry (skip empties/points).\"\"\"\n    if geom is None or geom.is_empty:\n        return\n    if isinstance(geom, LineString):\n        yield geom\n    elif isinstance(geom, (MultiLineString, GeometryCollection)):\n        for g in geom.geoms:\n            yield from _iter_lines(g)\n    elif hasattr(geom, \"boundary\"):\n        yield from _iter_lines(geom.boundary)\n\n\ndef overhang_region(layer: Polygon, support: Polygon, cfg: WaveConfig):\n    \"\"\"The part of `layer` that overhangs open air (not over `support`).\n\n    `layer`   : this layer's sliced area.\n    `support` : the layer-below area (what this layer can rest on). Empty for the\n                first layer -> the whole layer is \"supported\" by the bed, so no\n                overhang.\n    \"\"\"\n    if support is None or support.is_empty:\n        # First layer / nothing below: treat as fully supported by the bed.\n        return Polygon()\n    grown = support.buffer(cfg.overhang_tol) if cfg.overhang_tol else support\n    ov = layer.difference(grown)\n    if ov.is_empty:\n        return ov\n    # Drop slivers below the area threshold.\n    keep = [p for p in _polys(ov) if p.area >= cfg.min_overhang_area]\n    return unary_union(keep) if keep else Polygon()\n\n\ndef _polys(geom):\n    if geom.is_empty:\n        return []\n    if isinstance(geom, Polygon):\n        return [geom]\n    if isinstance(geom, MultiPolygon):\n        return list(geom.geoms)\n    if isinstance(geom, GeometryCollection):\n        out = []\n        for g in geom.geoms:\n            out.extend(_polys(g))\n        return out\n    return []\n\n\n# ---------------------------------------------------------------------------------\n# Wavefront propagation\n# ---------------------------------------------------------------------------------\n\n\n@dataclass\nclass WaveTrack:\n    distance: float               # mm from the supported edge (front index * spacing)\n    points: list                  # [(x, y), ...] centreline polyline\n\n\ndef wave_tracks(support: Polygon, overhang: Polygon, cfg: WaveConfig):\n    \"\"\"Grow wavefronts from the supported edge across the overhang.\n\n    Returns a list of WaveTrack ordered near->far from support. Each track is the\n    portion of an offset of the supported boundary that lies inside the overhang.\n    \"\"\"\n    tracks: list[WaveTrack] = []\n    if overhang is None or overhang.is_empty or support is None or support.is_empty:\n        return tracks\n\n    # Let the first front sit half a spacing into the overhang, then step outward.\n    # perimeter_overlap nudges the whole field back toward the kept perimeter/support\n    # so the last front hugs the supported edge on the far side.\n    base = 0.5 * cfg.line_spacing - cfg.perimeter_overlap\n    target = overhang.buffer(1e-6)\n    for i in range(cfg.max_iterations):\n        d = base + i * cfg.line_spacing\n        if d <= 0:\n            continue\n        grown = support.buffer(d)\n        front = grown.boundary.intersection(target)\n        made_any = False\n        for ln in _iter_lines(front):\n            if ln.length <= 1e-6:\n                continue\n            tracks.append(WaveTrack(distance=d, points=list(ln.coords)))\n            made_any = True\n        # Stop once the grown support fully covers the overhang (fronts run dry).\n        if grown.contains(target):\n            break\n        if not made_any and d > 1e-6 and grown.covers(target):\n            break\n    return tracks\n\n\n# ---------------------------------------------------------------------------------\n# Pattern / ordering\n# ---------------------------------------------------------------------------------\n\n\ndef _endpoints(pts):\n    return pts[0], pts[-1]\n\n\ndef _dist(a, b):\n    return math.hypot(a[0] - b[0], a[1] - b[1])\n\n\ndef order_tracks(tracks, support: Polygon, cfg: WaveConfig):\n    \"\"\"Turn wavefronts into an ordered list of printable polylines.\n\n    monotonic : print near->far, each front as its own line (lots of travels).\n    zigzag    : same order, but flip alternate fronts so the end of one is near\n                the start of the next -> connected back-and-forth motion.\n    smart     : like monotonic, but each front starts from its better-supported\n                (nearer-to-support) end so no line begins in thin air.\n    \"\"\"\n    ordered = sorted(tracks, key=lambda t: t.distance)\n    polylines = []\n    mode = (cfg.pattern or \"smart\").lower()\n\n    if mode == \"zigzag\":\n        flip = False\n        for t in ordered:\n            pts = list(reversed(t.points)) if flip else t.points\n            polylines.append(pts)\n            flip = not flip\n        return polylines\n\n    if mode == \"smart\" and support is not None and not support.is_empty:\n        for t in ordered:\n            a, b = _endpoints(t.points)\n            # Start from whichever end is closer to the supported region.\n            da = support.distance(_pt(a))\n            db = support.distance(_pt(b))\n            polylines.append(t.points if da <= db else list(reversed(t.points)))\n        return polylines\n\n    # monotonic (and fallback)\n    return [t.points for t in ordered]\n\n\ndef _pt(xy):\n    from shapely.geometry import Point\n\n    return Point(xy[0], xy[1])\n\n\n# ---------------------------------------------------------------------------------\n# G-code emission\n# ---------------------------------------------------------------------------------\n\n\ndef emit_layer_gcode(polylines, z, cfg: WaveConfig, restore_fan=None):\n    \"\"\"Emit G-code lines for one layer's wave polylines.\n\n    Coordinates are absolute bed XY; `z` is the layer height. `restore_fan` is an\n    optional 0..255 value to reset the fan to after the wave block (None = leave\n    the forced wave fan in place; the plugin usually passes the layer's fan back).\n    Relative-E is used inside the block and reset with M83/G92 so it composes with\n    Orca's own extrusion accounting.\n    \"\"\"\n    if not polylines:\n        return []\n    e_per_mm = cfg.e_per_mm()\n    print_f = int(round(cfg.print_speed * 60))\n    travel_f = int(round(cfg.travel_speed * 60))\n    out = [\"; ==== WAVE OVERHANG BEGIN ====\",\n           \"M83\",                                   # relative extrusion for our block\n           f\"M106 S{int(round(max(0.0, min(1.0, cfg.fan)) * 255))}\"]\n    for pts in polylines:\n        if len(pts) < 2:\n            continue\n        x0, y0 = pts[0]\n        out.append(f\"G0 F{travel_f} X{x0:.3f} Y{y0:.3f} Z{z:.3f}\")\n        out.append(f\"G1 F{print_f}\")\n        px, py = x0, y0\n        for (x, y) in pts[1:]:\n            seg = math.hypot(x - px, y - py)\n            if seg <= 1e-9:\n                continue\n            out.append(f\"G1 X{x:.3f} Y{y:.3f} E{seg * e_per_mm:.5f}\")\n            px, py = x, y\n    if restore_fan is not None:\n        out.append(f\"M106 S{int(restore_fan)}\")\n    out.append(\"; ==== WAVE OVERHANG END ====\")\n    return out\n\n\n# ---------------------------------------------------------------------------------\n# One-call convenience\n# ---------------------------------------------------------------------------------\n\n\n@dataclass\nclass LayerWaveResult:\n    z: float\n    polylines: list = field(default_factory=list)\n    overhang_area: float = 0.0\n    n_tracks: int = 0\n\n\ndef plan_layer(layer: Polygon, support: Polygon, z: float, cfg: WaveConfig):\n    \"\"\"Full per-layer plan: detect overhang, propagate waves, order them.\"\"\"\n    ov = overhang_region(layer, support, cfg)\n    if ov.is_empty:\n        return LayerWaveResult(z=z)\n    tracks = wave_tracks(support, ov, cfg)\n    polylines = order_tracks(tracks, support, cfg)\n    return LayerWaveResult(z=z, polylines=polylines,\n                           overhang_area=float(ov.area), n_tracks=len(tracks))\n\n\n# ---------------------------------------------------------------------------------\n# G-code layer parsing, self-calibration and splicing (pure text; unit tested)\n# ---------------------------------------------------------------------------------\n\nZ_KEYS = (\";Z:\", \";HEIGHT:\", \";LAYER_Z:\")\n\n\ndef parse_layer_z(line: str):\n    \"\"\"The layer height a G-code line announces, or None.\n\n    Handles Orca/Prusa comment markers (;Z: / ;HEIGHT: / ;LAYER_Z:) and a bare\n    layer-change move (`G1 Z.. F..` with no X/Y).\n    \"\"\"\n    s = line.strip()\n    for k in Z_KEYS:\n        if s.startswith(k):\n            try:\n                return float(s[len(k):].strip().split()[0])\n            except Exception:\n                return None\n    if s[:2] in (\"G0\", \"G1\") and \"Z\" in s and \" X\" not in (\" \" + s) and \" Y\" not in (\" \" + s):\n        for tok in s.split():\n            if tok.startswith(\"Z\"):\n                try:\n                    return float(tok[1:])\n                except Exception:\n                    return None\n    return None\n\n\ndef _extruding_xy(line: str):\n    \"\"\"(x, y) for an extruding G1 move (has X, Y and an E token), else None.\"\"\"\n    s = line.strip()\n    if not s.startswith(\"G1\"):\n        return None\n    x = y = None\n    has_e = False\n    for tok in s.split():\n        if tok.startswith(\"X\"):\n            try:\n                x = float(tok[1:])\n            except Exception:\n                return None\n        elif tok.startswith(\"Y\"):\n            try:\n                y = float(tok[1:])\n            except Exception:\n                return None\n        elif tok.startswith(\"E\"):\n            has_e = True\n    if x is not None and y is not None and has_e:\n        return (x, y)\n    return None\n\n\ndef layer_extrusion_min(lines, target_z, tol=1e-3):\n    \"\"\"Min (x, y) corner of extruding moves on the layer nearest `target_z`.\"\"\"\n    minx = miny = None\n    cur = None\n    for line in lines:\n        z = parse_layer_z(line)\n        if z is not None:\n            cur = z\n            continue\n        if cur is not None and abs(cur - target_z) <= tol:\n            xy = _extruding_xy(line)\n            if xy is not None:\n                minx = xy[0] if minx is None else min(minx, xy[0])\n                miny = xy[1] if miny is None else min(miny, xy[1])\n    return (minx, miny)\n\n\ndef _match_z(z, plans, tol=1e-3):\n    for pz in plans:\n        if abs(pz - z) <= tol:\n            return pz\n    return None\n\n\ndef splice_gcode(text, layer_plans, cfg: WaveConfig, calibration):\n    \"\"\"Insert wave moves into exported G-code. Pure text in / out.\n\n    layer_plans : {round(z,3): [polyline_in_object_frame, ...]}\n    calibration : (\"manual\", dx, dy)                     -> use this XY offset, or\n                  (\"auto\", calib_z, obj_min_x, obj_min_y) -> derive the offset by\n                    aligning Orca's own printed outline on layer `calib_z` to the\n                    object-frame outline min corner (a pure translation).\n\n    Wave moves for a layer are inserted just before the NEXT layer marker, i.e.\n    after Orca has printed that layer's own perimeters/infill.\n    Returns (new_text, inserted_layer_count, (dx, dy)).\n    \"\"\"\n    lines = text.splitlines(keepends=True)\n\n    if calibration and calibration[0] == \"manual\":\n        dx, dy = float(calibration[1]), float(calibration[2])\n    elif calibration and calibration[0] == \"auto\":\n        _, cz, omx, omy = calibration\n        gmin = layer_extrusion_min(lines, cz)\n        if gmin[0] is None or omx is None:\n            dx, dy = 0.0, 0.0\n        else:\n            dx, dy = gmin[0] - omx, gmin[1] - omy\n    else:\n        dx, dy = 0.0, 0.0\n\n    out = []\n    inserted = 0\n    pending = None  # (z, polylines) waiting to be flushed at the next layer marker\n\n    def flush():\n        nonlocal inserted\n        if pending is None:\n            return\n        z, polys = pending\n        shifted = [[(x + dx, y + dy) for (x, y) in pts] for pts in polys]\n        for ln in emit_layer_gcode(shifted, z, cfg):\n            out.append(ln + \"\\n\")\n        inserted += 1\n\n    for line in lines:\n        z = parse_layer_z(line)\n        if z is not None:\n            flush()\n            pending = None\n            key = _match_z(z, layer_plans)\n            if key is not None:\n                pending = (z, layer_plans[key])\n        out.append(line)\n    flush()\n\n    return \"\".join(out), inserted, (dx, dy)\n\n"
wc = _types.ModuleType("wave_core")
_sys.modules["wave_core"] = wc
try:
    exec(compile(_WAVE_CORE_SRC, "wave_core (inlined)", "exec"), wc.__dict__)
except Exception:  # pragma: no cover - surfaced via the setup check / execute()
    _sys.modules.pop("wave_core", None)
    wc = None


_DEFAULTS = {
    "enabled": True,
    "apply_to": "no-supports",   # "no-supports" | "all"
    # Remove the overhang from Orca's slices so the waves replace it rather
    # than overlap it. Only ever applied once the G-code splice has actually
    # been observed running (see _splice_confirmed) -- carving without it
    # would leave a hole.
    "carve_overhang": True,
    "overhang_tol": 0.05,
    "min_overhang_area": 0.5,
    "line_spacing": 0.35,
    "line_width": 0.40,
    "perimeter_overlap": 0.10,
    "pattern": "smart",          # "monotonic" | "zigzag" | "smart"
    "flow_ratio": 1.0,
    "print_speed": 2.0,
    "travel_speed": 120.0,
    "fan": 1.0,
    "max_iterations": 400,
    # Optional manual bed-offset override "x,y" (mm) for calibration; "" = auto.
    "xy_offset": "",
}

# Per-export stash (object frame; bed offset is derived at splice time):
#   _PLAN  : {round(z,3): [polyline_in_object_frame, ...]}
#   _CALIB : (calib_z, obj_min_x, obj_min_y) from a fully-supported layer whose
#            G-code outline matches its slice outline, used to self-calibrate the
#            object->bed XY offset.
_PLAN = {}
_CALIB = None
# Objects planned so far in the current slice run. posSlice fires once per
# object, but nothing tells us when a *new* run starts -- seeing an object we
# already planned means Orca re-sliced, so the old stash is stale and must go.
# Without this, a slice whose export seam never fires leaves _PLAN populated
# and the next export splices yesterday's waves.
_PLANNED_OBJECTS = set()
# Reasons _carve_layer bailed, surfaced in the result message instead of being
# swallowed.
_CARVE_ERRORS = []
# Objects planned while the G-code seam was known NOT to be wired up.
_NOT_WIRED = []

_WIRING_HELP = (
    "Carving is off because the G-code step has not been seen running yet. "
    "Waves were planned and will be spliced in if the export step fires; the "
    "overhang also still prints normally, so a part is never left hollow. "
    "Slice once, then run 'Wave Overhangs - Check setup' to see whether the "
    "G-code step ran. Once it has, carving turns itself on."
)


def _reset_stash():
    global _CALIB
    _PLAN.clear()
    _PLANNED_OBJECTS.clear()
    del _CARVE_ERRORS[:]
    _CALIB = None


def _state_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "wave_overhangs_state.json")


def _load_state():
    """What happened on previous slices. Empty dict if we have no history."""
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def _splice_confirmed():
    """Has Step.psGCodePostProcess ever actually run for this install?

    This is measured, not guessed. Which preset field drives the export seam
    differs between OrcaSlicer builds -- some expose one plugin picker, some
    two -- so rather than read a setting whose name we cannot rely on, we
    record what really fires and behave accordingly.

    Until the splice is observed at least once, carving is refused: removing
    the overhang from the slices is only safe if something puts the waves
    back, and an unconfirmed seam might not.
    """
    return bool(_load_state().get("splice_ever"))


def _cfg(self):
    try:
        src = json.loads(self.get_config() or "{}")
    except (AttributeError, TypeError, ValueError):
        src = {}
    cfg = dict(_DEFAULTS)
    for k, v in src.items():
        if k in cfg:
            cfg[k] = v
    return cfg


def _wave_config(cfg, layer_height):
    return wc.WaveConfig(
        overhang_tol=float(cfg["overhang_tol"]),
        min_overhang_area=float(cfg["min_overhang_area"]),
        line_spacing=float(cfg["line_spacing"]),
        line_width=float(cfg["line_width"]),
        perimeter_overlap=float(cfg["perimeter_overlap"]),
        pattern=str(cfg["pattern"]),
        layer_height=float(layer_height),
        flow_ratio=float(cfg["flow_ratio"]),
        print_speed=float(cfg["print_speed"]),
        travel_speed=float(cfg["travel_speed"]),
        fan=float(cfg["fan"]),
        max_iterations=int(cfg["max_iterations"]),
    )


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------------
# Reading Orca layer geometry (mm, object frame)
# ---------------------------------------------------------------------------------


def _expoly_to_shapely(e, unit):
    contour = np.asarray(e.contour.as_array(), dtype=np.float64) * unit
    holes = [np.asarray(h.as_array(), dtype=np.float64) * unit for h in e.holes]
    return shapely.geometry.Polygon(contour, holes)


def _layer_polygon_mm(layer, unit):
    """Union of a layer's sliced ExPolygons, in mm, object frame."""
    polys = []
    for region in layer.regions():
        for s in region.slices.surfaces:
            try:
                polys.append(_expoly_to_shapely(s.expolygon, unit))
            except Exception:
                pass
    if not polys:
        return shapely.geometry.Polygon()
    return shapely.ops.unary_union(polys)


# ---------------------------------------------------------------------------------
# Geometry step: compute + stash waves, optionally carve the overhang
#
# Object->bed XY mapping: Orca emits G-code in absolute bed coordinates, but layer
# slices are in the object's own frame. Rather than guess an instance transform, we
# SELF-CALIBRATE at splice time: a fully-supported layer prints the same outline in
# both frames, so aligning its min corner recovers the (pure-translation) offset.
# The `xy_offset` config overrides it. See wave_core.splice_gcode / _CALIB.
# ---------------------------------------------------------------------------------


def _object_key(po):
    for attr in ("id", "model_object"):
        try:
            v = getattr(po, attr)()
            return str(getattr(v, "id", lambda: v)() if callable(getattr(v, "id", None)) else v)
        except Exception:
            continue
    return str(id(po))


def _plan_object(po, cfg, layer_height, unit, log):
    global _CALIB
    wcfg = _wave_config(cfg, layer_height)

    layers = list(po.layers())
    prev_poly = shapely.geometry.Polygon()
    planned = 0
    carved = 0
    calib_area = -1.0
    manual = str(cfg.get("xy_offset") or "").strip()
    for layer in layers:
        try:
            z = float(layer.slice_z)
            cur = _layer_polygon_mm(layer, unit)
            if cur.is_empty:
                prev_poly = cur
                continue
            res = wc.plan_layer(cur, prev_poly, z, wcfg)
            if res.polylines:
                # Store in the OBJECT frame; the bed offset is derived at splice.
                _PLAN[round(z, 3)] = res.polylines
                planned += 1
                log["layers"].append([round(z, 4), round(res.overhang_area, 3),
                                      res.n_tracks])
                if _truthy(cfg["carve_overhang"]):
                    if _carve_layer(layer, prev_poly, wcfg, unit):
                        carved += 1
            else:
                # A fully-supported layer: its G-code outline == its slice outline,
                # so it makes a good self-calibration reference. Keep the biggest.
                # (The parentheses matter: `and` binds tighter than `or`, so the
                # unbracketed form meant "no calibration yet" alone could win.)
                if not manual and (_CALIB is None or cur.area > calib_area):
                    minx, miny, _, _ = cur.bounds
                    _CALIB = (round(z, 3), float(minx), float(miny))
                    calib_area = cur.area
            prev_poly = cur
        except Exception as e:  # never break a slice
            log.setdefault("errors", []).append(f"z={getattr(layer,'slice_z','?')}: "
                                                 f"{type(e).__name__}: {e}")
            prev_poly = shapely.geometry.Polygon()
    log["calibration"] = _CALIB
    log["planned_layers"] = planned
    log["carved_layers"] = carved
    return f"{planned} layer(s) with waves, {carved} carved"


def _carve_layer(layer, prev_poly, wcfg, unit):
    """Remove the overhang area from this layer's first region so Orca skips it.

    Mirrors the additive approach in the Support Fins plugin, but subtractive.
    Best-effort; returns True if it modified the layer.
    """
    regions = layer.regions()
    if not regions:
        return False
    cur = _layer_polygon_mm(layer, unit)
    ov = wc.overhang_region(cur, prev_poly, wcfg)
    if ov.is_empty:
        return False
    region = regions[0]
    inv = 1.0 / unit
    new_surfaces = []
    for s in region.slices.surfaces:
        try:
            poly = _expoly_to_shapely(s.expolygon, unit)
            kept = poly.difference(ov)
            for p in _shapely_polys(kept):
                new_surfaces.append((s.surface_type, p))
        except Exception:
            return False
    if not new_surfaces:
        return False
    try:
        first = True
        for stype, p in new_surfaces:
            expolys = _shapely_to_expolys(p, inv)
            for e in expolys:
                if first:
                    # set()/append() take a *sequence* of ExPolygons (the
                    # binding is an ExPolygons vector). Passing a bare
                    # ExPolygon raises, and this used to be swallowed by the
                    # except below -- so carving silently never happened.
                    region.slices.set([e], stype)
                    first = False
                else:
                    region.slices.append([e], stype)
        layer.make_slices()
        return True
    except Exception as e:
        _CARVE_ERRORS.append(f"{type(e).__name__}: {e}")
        return False


def _shapely_polys(geom):
    if geom.is_empty:
        return []
    t = geom.geom_type
    if t == "Polygon":
        return [geom]
    if t in ("MultiPolygon", "GeometryCollection"):
        out = []
        for g in geom.geoms:
            out.extend(_shapely_polys(g))
        return out
    return []


def _shapely_to_expolys(poly, inv):
    def ring(coords):
        arr = (np.asarray(coords, dtype=np.float64) * inv).round().astype(np.int64)
        return orca.host.Polygon(arr.tolist())
    contour = ring(poly.exterior.coords)
    holes = [ring(r.coords) for r in poly.interiors]
    return [orca.host.ExPolygon(contour, holes)]


# ---------------------------------------------------------------------------------
# G-code seam: splice stashed waves into the exported file
# ---------------------------------------------------------------------------------


def _splice_gcode(gcode_path, cfg, log):
    with open(gcode_path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    manual = str(cfg.get("xy_offset") or "").strip()
    if manual:
        try:
            mx, my = (float(v) for v in manual.split(","))
            calibration = ("manual", mx, my)
        except Exception:
            calibration = ("auto",) + (_CALIB or (None, None, None))
    elif _CALIB is not None:
        calibration = ("auto",) + _CALIB
    else:
        calibration = ("manual", 0.0, 0.0)

    wcfg = _wave_config(cfg, cfg.get("_lh", 0.2))
    new_text, inserted, offset = wc.splice_gcode(text, _PLAN, wcfg, calibration)
    if inserted:
        with open(gcode_path, "w", encoding="utf-8") as f:
            f.write(new_text)
    log["spliced_layers"] = inserted
    log["applied_offset"] = list(offset)
    return inserted


# ---------------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------------


class WaveOverhangsSlicing(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Wave Overhangs"

    def get_default_config(self):
        return _DEFAULTS

    def execute(self, ctx):
        cfg = _cfg(self)
        if not cfg["enabled"]:
            return orca.ExecutionResult.success("Wave Overhangs: disabled")
        if np is None or shapely is None or wc is None:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                _DEPS_ERROR or "Wave Overhangs needs numpy + shapely.")

        # --- geometry step: plan + carve ---
        if ctx.step == orca.slicing.Step.posSlice and ctx.object is not None:
            po = ctx.object
            if cfg["apply_to"] != "all" and _truthy(po.config_value("enable_support")):
                return orca.ExecutionResult.success(
                    "Wave Overhangs: skipped (Orca supports on for this part)")

            key = _object_key(po)
            if key in _PLANNED_OBJECTS:   # Orca re-sliced: drop the stale stash
                _reset_stash()
            _PLANNED_OBJECTS.add(key)

            # Waves only reach the printer through the G-code seam. Until we
            # have actually seen that seam run, carving is unsafe: it would
            # remove the overhang with nothing to put back.
            wired = _splice_confirmed()
            if not wired and _truthy(cfg["carve_overhang"]):
                cfg["carve_overhang"] = False
                _NOT_WIRED.append(key)
            try:
                lh = float(po.config_value("layer_height")
                           or ctx.config_value("layer_height") or 0.2)
            except (TypeError, ValueError):
                lh = 0.2
            cfg["_lh"] = lh
            log = {"object": _object_key(po), "layer_height": lh,
                   "started": time.time(), "layers": []}
            try:
                msg = _plan_object(po, cfg, lh, orca.slicing.unscale(1), log)
            except Exception as e:
                log["error"] = f"{type(e).__name__}: {e}"
                _write_log(log)
                return orca.ExecutionResult.failure(
                    orca.PluginResult.RecoverableError,
                    f"Wave Overhangs: {type(e).__name__}: {e}")
            st = _load_state()
            st["last_plan_at"] = time.time()
            st["last_plan_layers"] = log.get("planned_layers", 0)
            st["plans_since_splice"] = int(st.get("plans_since_splice", 0)) + 1
            _save_state(st)

            log["seconds"] = round(time.time() - log["started"], 3)
            log["splice_confirmed"] = wired
            if _CARVE_ERRORS:
                log["carve_errors"] = list(_CARVE_ERRORS)
            _write_log(log)
            if key in _NOT_WIRED:
                return orca.ExecutionResult.success(
                    f"Wave Overhangs: {msg}. {_WIRING_HELP}")
            if _CARVE_ERRORS:
                return orca.ExecutionResult.success(
                    f"Wave Overhangs: {msg} (carve failed: {_CARVE_ERRORS[0]})")
            return orca.ExecutionResult.success(f"Wave Overhangs: {msg}")

        # --- g-code seam: splice ---
        if ctx.step == orca.slicing.Step.psGCodePostProcess:
            if not _PLAN:
                return orca.ExecutionResult.success(
                    "Wave Overhangs: nothing to splice. If you expected waves, "
                    "check that Wave Overhangs is also selected under Others -> "
                    "Slicing Pipeline Plugin, which is what plans them.")
            log = {"phase": "gcode", "started": time.time()}
            try:
                n = _splice_gcode(ctx.gcode_path, cfg, log)
            except Exception as e:
                log["error"] = f"{type(e).__name__}: {e}"
                _write_log(log)
                # Don't fail the export over post-processing.
                return orca.ExecutionResult.success(
                    f"Wave Overhangs: splice skipped ({type(e).__name__})")
            finally:
                _reset_stash()
            # The seam ran. Record it: this is what lets carving switch on.
            st = _load_state()
            st["splice_ever"] = True
            st["last_splice_at"] = time.time()
            st["last_splice_layers"] = n
            st["plans_since_splice"] = 0
            _save_state(st)

            log["seconds"] = round(time.time() - log["started"], 3)
            _write_log(log)
            return orca.ExecutionResult.success(
                f"Wave Overhangs: spliced {n} layer(s)")

        return orca.ExecutionResult.success()


class WaveOverhangsCheck(orca.script.ScriptPluginCapabilityBase):
    def get_name(self):
        return "Wave Overhangs - Check setup"

    def execute(self):
        lines = ["Wave Overhangs setup check"]
        if np is None or shapely is None:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                _DEPS_ERROR or "Wave Overhangs needs numpy + shapely.")
        lines.append("deps: numpy + shapely loaded at startup (audit-safe)")
        lines.append(f"wave_core: {'inlined/available' if wc else 'MISSING (rebuild)'}")
        try:
            model = orca.host.model()
            objects = list(model.objects())
            lines.append(f"model: {len(objects)} object(s) on the plate")
        except Exception as e:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                f"orca.host.model() failed: {type(e).__name__}: {e}")
        lines.append("EXPERIMENTAL: validate object->bed XY mapping (_bed_offset) "
                     "before trusting output.")

        # Report what actually happened, rather than guessing from settings.
        st = _load_state()
        lines.append("")
        lines.append("--- what the last slice actually did ---")
        if not st.get("last_plan_at"):
            lines.append("No slice recorded yet. Select Wave Overhangs in your")
            lines.append("process preset under Others (whichever plugin picker")
            lines.append("your build shows), slice something with a steep")
            lines.append("overhang, then run this check again.")
        else:
            lines.append(f"planning step (posSlice): ran, "
                         f"{st.get('last_plan_layers', 0)} layer(s) with waves")
            if st.get("splice_ever"):
                lines.append(f"G-code step (psGCodePostProcess): ran, "
                             f"{st.get('last_splice_layers', 0)} layer(s) spliced")
                lines.append("")
                lines.append("Both steps work. Carving is enabled from now on.")
            else:
                lines.append("G-code step (psGCodePostProcess): NEVER RUN")
                lines.append("")
                lines.append(f"Waves have been planned {st.get('plans_since_splice', 0)} "
                             f"time(s) and never written out.")
                lines.append("That is why nothing changes in the G-code.")
                lines.append("")
                lines.append("Check that 'Wave Overhangs' is still selected in")
                lines.append("Process preset -> Others -> Slicing Pipeline Plugin.")
                lines.append("That one field drives both steps. Then slice and")
                lines.append("re-run this check.")
                lines.append("")
                lines.append("Carving stays off until the export step is seen,")
                lines.append("so your overhangs still print normally meanwhile.")
        return orca.ExecutionResult.success("\n".join(lines))


def _write_log(entry):
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "wave_overhangs_log.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


@orca.plugin
class WaveOverhangsPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(WaveOverhangsSlicing)
        orca.register_capability(WaveOverhangsCheck)
