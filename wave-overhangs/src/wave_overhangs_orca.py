# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=1.26", "shapely>=2.0"]
#
# [tool.orcaslicer.plugin]
# name = "Wave Overhangs"
# description = "Ripples the bottom surface of flat overhangs: thin dashed grooves cut into the overhang's skin, conforming to the supported perimeter and running outward as clean offset arcs. The part keeps its exact shape. Optional ramp mode trades a wedge of material for a printable terrace."
# author = "support-fins repo (Orca lane pattern; wave strategy after Andersons et al. and the OrcaSlicer-WaveOverhangs fork)"
# version = "0.2.0"
# ///
"""Wave Overhangs for OrcaSlicer -- ripple the overhang's underside at slice time.

WHAT IT DOES (default: skin mode)
  A flat overhang -- the underside of a ceiling, a tabletop standing on a
  stem, the roof of a tunnel -- normally prints as one flat sheet of material
  hanging in mid-air. This plugin leaves the part's shape exactly as the
  model defines it and changes only the PATTERN of that bottom surface: it
  cuts thin, dashed grooves into the overhang's first layer, arranged as
  RIPPLES -- concentric rings that conform to the supported perimeter and run
  outward, following whatever shape that perimeter has (around corners,
  around hole rims), until they run out of overhang. What prints is a rippled
  skin: solid ridges between groove dashes, every ridge joined to its
  neighbours by solid bridges, the supported band and the overhang's outer
  rim kept solid so the part's outline and walls are untouched.

  The grooves are the only material removed. Each one is a capsule thinner
  than `groove_width_mm`, one layer deep (the layer above fills over it),
  never within `edge_band_mm` of the part's surface, and the skin is checked
  to still be one connected piece after they are cut. Nothing is added
  anywhere. That is the whole edit: same part, rippled underside.

WHY GROOVES, AND WHY DASHED
  The wave-overhang forks print their rings as custom toolpaths inside one
  layer, each ring squished against the previous ring -- lateral anchoring.
  A slice-polygon plugin cannot author toolpaths: per-layer polygons are its
  only language, and polygons that touch are unioned by the slicer, so
  "adjacent rings" cannot be expressed as separate paths. Grooves are what
  CAN be expressed: they force Orca's own perimeters to trace the ripple
  arcs, and the dashes keep every ridge tied to the supported band so the
  pattern never floats. (True same-layer wave toolpaths are the lane of
  support-fins/plugins/orca-wave in this repo, which splices G-code.)

THE SEAM
  The one the Support Fins plugin uses: orca.slicing.Step.posSlice, after
  Orca has sliced the part into per-layer polygons but before perimeters,
  infill, supports and G-code. Everything downstream is then Orca's own
  logic applied to the rippled solid -- walls, infill, bridge detection,
  overhang speeds. Supports left on are generated from the same geometry as
  always: the plugin no longer removes any region they would be needed for.

  Detection is footprint-based, like the wave forks' "the wave only fills
  the unsupported portion of each layer": the overhang region of layer j is
  what its slice has that layer j-1 did not, and only regions WIDER than
  h / tan(threshold_deg) count as flat -- a 45-degree slope grows 0.2 mm per
  0.2 mm layer and is never touched; a ceiling appears all at once and
  ripples. Shrinking footprints (treads, dome tops) never grow, so top
  surfaces are never touched either.

RAMP MODE (opt in, "mode": "ramp")
  The earlier experiment, kept because it is the only support-free variant:
  grow the printed footprint one ring per LAYER so every ring rests on the
  ring below -- the flat ceiling becomes a printable terraced ramp, at the
  cost of removing the wedge under the ramp from the part (and its outer
  wall steps with it). Off by default because it changes the part's shape;
  see the README for the trade before using it.

GUARANTEES (each one is pinned by a test)
  * Nothing is added: every layer's printed polygons are a subset of that
    layer's model slices.
  * Nothing is removed except the grooves: thin (never wider than
    groove_width_mm), one layer deep, only on layers that introduce a flat
    overhang, never touching the outer rim or the supported band, and the
    skin stays connected.
  * The first layer (bed or raft) is never touched, and layers without a
    fresh flat overhang are left bit-identical.
  * Surfaces steeper than threshold_deg from horizontal are never touched.
  * In ramp mode instead: nothing added, nothing lost at all (the union of
    printed layers equals the model's), first layer and steep surfaces
    untouched.

LIMITS
  * The ripples appear in the sliced Preview, not in the Prepare 3D view.
  * Skin mode changes the surface PATTERN; it does not by itself make a
    truly flat wide overhang printable -- that is ramp mode's trade, or
    supports. What the grooves do give the skin is relief joints and a
    shorter unsupported span per ridge.
  * Vase mode: don't.
"""
import json
import math
import os
import time

import orca

try:  # installed by Orca from the PEP 723 header above
    import numpy as np
except ImportError:  # pragma: no cover - surfaced to the user in execute()
    np = None

try:
    import shapely
    from shapely.geometry import LineString, Polygon as _SPoly
    from shapely.ops import unary_union, substring
except ImportError:  # pragma: no cover - surfaced to the user in execute()
    shapely = None

_DEFAULTS = {
    "enabled": True,
    "apply_to": "all",           # "all" | "no-supports": parts to ripple
    "mode": "skin",              # "skin": pattern the underside, keep the shape
                                 # "ramp": printable terraces, changes the shape
    # --- shared -----------------------------------------------------------
    "threshold_deg": 30.0,       # ripple surfaces flatter than this (from horizontal)
    "min_area_mm2": 1.0,         # ignore overhang patches smaller than this
    # --- skin mode --------------------------------------------------------
    "ring_pitch_mm": 1.2,        # centre-to-centre spacing of ripple rings
    "groove_width_mm": 0.25,     # groove width (must exceed ~0.1 mm or the
                                 # slicer's slice_closing_radius closes it)
    "dash_mm": 4.0,              # groove dash length along a ring
    "bridge_mm": 1.2,            # solid bridge between dashes (keeps the skin whole)
    "edge_band_mm": 0.8,         # solid rim kept at the overhang's outer boundary
    "solid_band_mm": 0.6,        # solid band kept at the supported perimeter
    # --- ramp mode --------------------------------------------------------
    "ramp_angle_deg": 45.0,      # steepness of the ripple ramp (from horizontal)
    "max_reach_mm": 10.0,        # how far a ripple may climb out from the perimeter
    "anchor_mm": 0.1,            # seed band width along the supported perimeter
}


# ---------------------------------------------------------------------------------
# Geometry core -- pure shapely in millimetres, no Orca types (tested without Orca)
# ---------------------------------------------------------------------------------

def _empty():
    return _SPoly()


# Offset chains explode: buffering a buffered polygon again adds join vertices
# at every vertex, so vertex counts compound layer after layer and a 100-layer
# ripple crawl grinds to a halt. Slicers clean polygons between offsets for
# exactly this reason (libslic3r's Clipper wrapper uses 0.0015 mm); we do the
# same. 2 um is 50x below the envelope tolerance the tests hold us to.
_CLEAN_TOL = 0.002

# The ring fronts the grooves follow are drawn with 64 segments per quadrant,
# so a 20 mm offset deviates from its true arc by ~1.5 um: the ripples read as
# clean arcs in the preview, not faceted ones (the complaint that shaped this
# version). Band/area work stays at shapely's coarse default -- it is only
# ever a pre-check, the capsules are clipped to the valid region anyway.
_FRONT_QUAD = 64


def _clean(g):
    if g is None or g.is_empty:
        return g
    try:
        s = g.simplify(_CLEAN_TOL, preserve_topology=False)
        return s if (not s.is_empty and s.is_valid) else g
    except Exception:
        return g


def _buf(g, r):
    """Dilate by r with round joins (coarse -- area-level work only)."""
    if g is None or g.is_empty or r <= 0.0:
        return g
    return _clean(g.buffer(r))


def _polygons(g):
    """Polygon list of any polygonal geometry (collections flattened)."""
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    out = []
    for part in getattr(g, "geoms", []):   # MultiPolygon / GeometryCollection
        out.extend(_polygons(part))
    return out


def _union(geoms):
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return _empty()
    if len(geoms) == 1:
        return geoms[0]
    return unary_union(geoms)


def _area(g):
    return 0.0 if g is None or g.is_empty else float(g.area)


def _n_components(g):
    return len(_polygons(g))


def _ring_lines(geom):
    """Closed contour LineStrings of a polygonal geometry (outer + holes)."""
    out = []
    for poly in _polygons(geom):
        try:
            out.append(LineString(poly.exterior.coords))
        except Exception:
            pass
        for r in poly.interiors:
            try:
                out.append(LineString(r.coords))
            except Exception:
                pass
    return out


def _flat_parts(new_area, wide_w, min_area):
    """Components of `new_area` wide enough to count as a FLAT overhang.

    An erosion test: the region must contain a disk of radius wide_w/2, where
    wide_w is the per-layer growth of a surface at threshold_deg from
    horizontal. Thin lips of steeper surfaces contain no such disk and print
    at once, like stock Orca."""
    parts = []
    for c in _polygons(new_area):
        if c.area < min_area:
            continue
        if c.buffer(-max(wide_w, 1e-3) / 2.0).is_empty:
            continue
        parts.append(c)
    return _union(parts)


def plan_ripples(slices_mm, heights_mm, cfg):
    """Turn a stack of model slices into the stack that may actually print.

    Dispatches on cfg["mode"]: "skin" (default) patterns each layer's fresh
    flat overhang with ripple grooves and changes nothing else; "ramp" grows
    the printed footprint one ring per layer (see plan_ramp_ripples).

    slices_mm:  per layer, the union of the object's slice polygons (shapely,
                millimetres, any frame -- only differences matter).
    heights_mm: per layer, the layer height (mm).
    Returns (allowed_per_layer, stats); `allowed[j]` is what layer j may print.
    """
    mode = str(cfg.get("mode", "skin") or "skin").strip().lower()
    if mode in ("ramp", "terraces"):
        return plan_ramp_ripples(slices_mm, heights_mm, cfg)
    return plan_skin_ripples(slices_mm, heights_mm, cfg)


# ---------------------------------------------------------------------------------
# Skin mode: ripple the overhang's underside, keep the part's shape
# ---------------------------------------------------------------------------------

def _groove_ring(prev, d, g, dash_len, bridge_len, phase, valid):
    """Groove dashes along the offset contour of `prev` at distance `d`.

    A dash is a contour sub-line buffered by g/2 with round caps and clipped
    to `valid` -- a capsule that follows the contour exactly, so every dash
    IS a clean arc of the perimeter's offset, whatever shape the perimeter is
    (square, L-shaped, around holes). Dashes never overlap: same-ring dashes
    are separated by `bridge_len`, and adjacent rings are a pitch apart, far
    more than the groove width -- so nothing wider than g is ever removed.
    Returns (dashes geometry, dash count)."""
    period = dash_len + bridge_len
    if period <= 0.0 or g <= 0.0:
        return _empty(), 0
    front = prev.buffer(d, quad_segs=_FRONT_QUAD) if d > 0.0 else prev
    caps = []
    for line in _ring_lines(front):
        L = line.length
        if L < 0.6:
            continue
        start = phase % period
        i = 0
        while True:
            a = start + i * period
            i += 1
            if a >= L:
                break
            b = min(a + dash_len, L)
            if b - a < 0.5:
                continue
            seg = substring(line, a, b)
            if seg.is_empty:
                continue
            cap = seg.buffer(g / 2.0, quad_segs=12)
            if cap.is_empty:
                continue
            cap = cap.intersection(valid)
            if cap.is_empty or cap.area < 0.02:
                continue
            caps.append(cap)
    return _union(caps), len(caps)


def plan_skin_ripples(slices_mm, heights_mm, cfg):
    """Pattern each layer's fresh flat overhang with ripple grooves.

    Per layer j:
      R      = cur - prev (grown by 0.05 mm): the overhang region, the part of
               this slice with nothing below it
      flat   = the components of R wide enough to be a FLAT overhang
      valid  = flat, kept `edge_band` inside the part's boundary: grooves
               never approach the outer rim or hole rims
      rings  = offset contours of `prev` (the supported footprint) at
               d0, d0+pitch, d0+2*pitch, ... while any valid material remains
               beyond them -- the ripples, conforming to the supported
               perimeter and running outward until the overhang ends
      grooves= dashed capsules along those contours, clipped to valid
      allowed= cur - grooves

    Invariants (they are the point of the shape of this loop):
      allowed[j] <= slices_mm[j]                          -- never add material
      cur - allowed consists of capsules no wider than groove_width
      cur - allowed never touches the outer rim (edge_band) or the supported
      band (solid_band), and never disconnects the skin
    """
    n = len(slices_mm)
    U = [s if s is not None and not s.is_empty else _empty() for s in slices_mm]
    thr = math.radians(max(1.0, min(89.0, float(cfg["threshold_deg"]))))
    min_area = max(0.0, float(cfg["min_area_mm2"]))
    pitch = max(0.4, float(cfg["ring_pitch_mm"]))
    g = min(max(0.12, float(cfg["groove_width_mm"])), 0.6 * pitch)
    dash_len = max(0.5, float(cfg["dash_mm"]))
    bridge_len = max(0.3, float(cfg["bridge_mm"]))
    edge_band = max(0.1, float(cfg["edge_band_mm"]))
    solid_band = max(0.3, float(cfg["solid_band_mm"]))
    max_rings = 64

    stats = {
        "layers": n, "mode": "skin",
        "patterned_layers": 0,     # layers that got groove dashes
        "rings": 0,                # groove rings cut
        "dashes": 0,               # groove dashes cut
        "grooved_mm2": 0.0,        # total area removed by grooves
        "overhang_mm2": 0.0,       # total flat-overhang area patterned
        "connectivity_fallbacks": 0,  # layers left unpatterned to keep the skin whole
        "touched_layers": 0,
    }

    allowed = []
    prev = _empty()
    for j in range(n):
        cur = U[j]
        # The bed layer, empty layers and layers with nothing below them
        # (a floating island has no supported perimeter to ripple from) pass
        # through untouched. So does anything with no fresh footprint.
        if j == 0 or cur.is_empty or prev.is_empty or cur.equals(prev):
            allowed.append(cur)
            prev = cur
            continue

        h = max(1e-6, float(heights_mm[j]))
        wide_w = h / math.tan(thr)
        R = cur.difference(_buf(prev, 0.05))
        flat = _flat_parts(R, wide_w, min_area)
        valid = flat.intersection(cur.buffer(-edge_band)) if edge_band > 0.0 else flat

        grooves = _empty()
        if not valid.is_empty and valid.area >= min_area:
            d0 = solid_band + g / 2.0
            pieces = []
            for k in range(max_rings):
                d = d0 + k * pitch
                beyond = valid.difference(_buf(prev, max(0.0, d - g / 2.0 - 0.05)))
                if beyond.is_empty:
                    break           # the overhang is fully rippled out to here
                band = valid.intersection(_buf(prev, d + g / 2.0)) \
                             .difference(_buf(prev, max(0.0, d - g / 2.0)))
                if band.is_empty or band.area < 0.02:
                    continue        # this ring finds no material (e.g. a gap in
                                    # the overhang); later rings may again
                phase = (k % 2) * (dash_len + bridge_len) / 2.0   # stagger rings
                dashes, count = _groove_ring(prev, d, g, dash_len, bridge_len,
                                             phase, valid)
                if not dashes.is_empty:
                    pieces.append(dashes)
                    stats["rings"] += 1
                    stats["dashes"] += count
            grooves = _union(pieces)

        allow = cur
        if not grooves.is_empty:
            trial = cur.difference(grooves)
            if _n_components(trial) != _n_components(cur):
                # A dash would have severed a neck or swallowed an island:
                # never trade the skin's integrity for the pattern.
                stats["connectivity_fallbacks"] += 1
            else:
                allow = trial
                stats["patterned_layers"] += 1
                stats["grooved_mm2"] += _area(cur) - _area(trial)
                stats["overhang_mm2"] += _area(flat)
                if _area(cur) - _area(trial) > 1e-9:
                    stats["touched_layers"] += 1

        allowed.append(allow)
        prev = allow
    return allowed, stats


# ---------------------------------------------------------------------------------
# Ramp mode (opt in): one ring per layer, a printable ramp at the cost of a wedge
# ---------------------------------------------------------------------------------

def plan_ramp_ripples(slices_mm, heights_mm, cfg):
    """Grow the printed footprint one ring per layer so each ring rests on the
    ring below (the support-free variant; changes the part's shape).

    State machine, one pass bottom-up:
      reached   what the previous layer actually printed (the wavefront's base)
      pending   overhang area already deferred, waiting for its ripple

    Per layer:
      grown      = cur within `step` of `reached` -- this layer's ring, the seed
                  band, and everything continuous with the layer below
      new_area   = cur beyond `anchor` of `reached` -- the fresh footprint
      wide       = components of new_area wide enough to count as FLAT overhang;
                  thin lips of steep slopes print at once
      cand       = wide beyond this layer's ring: the ripple candidates
      defer_now  = the part of cand the wave will reach while the solid is still
                  there -- ring k prints at layer j+k-1, so a point may wait only
                  if it is inside the model's slices at that layer. This is what
                  bounds the ramp by the thickness of the solid above the
                  overhang, and by max_reach_mm.
      forced     = what cannot or should not wait: pending whose next layer is
                  its last, candidates the wave cannot reach in time, and
                  (safety valve) pending stranded beyond the reach.

    Invariants:
      allowed[j] <= slices_mm[j]                     -- never add material
      pending <= next layer's slices                 -- nothing waits past its solid
      allowed[j] U pending >= slices_mm[j]           -- nothing is ever lost
    """
    n = len(slices_mm)
    U = [s if s is not None and not s.is_empty else _empty() for s in slices_mm]
    ramp = math.radians(max(5.0, min(85.0, float(cfg["ramp_angle_deg"]))))
    thr = math.radians(max(1.0, min(89.0, float(cfg["threshold_deg"]))))
    reach = max(0.0, float(cfg["max_reach_mm"]))
    anchor = max(0.0, float(cfg["anchor_mm"]))
    min_area = max(0.0, float(cfg["min_area_mm2"]))

    # above[k]: everywhere the model still has material at some layer >= k. The
    # deadline test reads this -- a deferred point must still be inside the model
    # on the layer its ripple arrives.
    above = [None] * n
    acc = _empty()
    for k in range(n - 1, -1, -1):
        acc = _union([acc, U[k]])
        above[k] = acc

    stats = {
        "layers": n, "mode": "ramp",
        "ripple_layers": 0,      # layers that deferred overhang area
        "rippled_mm2": 0.0,      # total area that waited for its ring
        "unreached_mm2": 0.0,    # overhang the wave could not cover (printed stock)
        "flushed_mm2": 0.0,      # pending material flushed back at its last chance
        "touched_layers": 0,     # layers whose print set differs from the model
        "max_ring": 0,
    }

    allowed = []
    reached = _empty()   # what the previous layer printed
    pending = _empty()   # deferred area still waiting for its ripple

    for j in range(n):
        cur = U[j]
        nxt = U[j + 1] if j + 1 < n else _empty()

        if j == 0:
            # Bed (or raft) layer: fully supported, always printed as sliced.
            allowed.append(cur)
            reached = cur
            pending = _empty()
            continue

        if pending.is_empty and cur.difference(reached).is_empty:
            # Nothing new and nothing waiting: a vertical wall section (the
            # common case in tall parts) -- print exactly what Orca sliced.
            allowed.append(cur)
            reached = cur
            continue

        h = max(1e-6, float(heights_mm[j]))
        step = max(0.05, h / math.tan(ramp))          # ring growth per layer, mm
        wide_w = h / math.tan(thr)                    # growth that counts as flat, mm

        grown = cur.intersection(_buf(reached, step))
        anchored = cur.intersection(_buf(reached, anchor)) if anchor > 0 else cur.intersection(reached)
        new_area = cur.difference(_buf(reached, anchor)) if anchor > 0 else cur.difference(reached)

        wide = _flat_parts(new_area, wide_w, min_area)
        narrow = new_area.difference(wide)

        cand = wide.difference(grown).difference(anchored)

        # Which candidates may wait? Ring k of the wave prints on layer j+k-1;
        # a point in ring k is deferred only if the model still contains it then
        # (and it survives into the next layer, and it is within the reach).
        defer_now = _empty()
        if not cand.is_empty and j + 1 < n and reach > 0.0:
            # ring k reaches k*step out; the reach caps its outer radius
            kmax = min(n - j, max(1, int(reach / step + 1e-9)))
            ok = _empty()
            inner = _buf(reached, step)
            for k in range(2, kmax + 1):
                outer = _buf(reached, k * step)
                ring = outer.difference(inner)
                if not ring.is_empty:
                    ok = _union([ok, ring.intersection(above[j + k - 1])])
                    if k > stats["max_ring"]:
                        stats["max_ring"] = k
                inner = outer
            defer_now = ok.intersection(cand).intersection(nxt)

        forced = _union([
            pending.difference(nxt),                            # last chance: print now
            cand.difference(defer_now),                         # the wave can't get there
            pending.difference(_buf(reached, reach + step)),    # stranded (safety valve)
        ])

        allow = _union([grown, anchored, narrow, forced])
        # stats: only deferral of area that was not already waiting counts (the
        # same pending annulus is re-offered every layer until its ring arrives)
        new_defer = defer_now.difference(pending)
        if not new_defer.is_empty:
            stats["ripple_layers"] += 1
            stats["rippled_mm2"] += _area(new_defer)
        stats["unreached_mm2"] += _area(cand.difference(defer_now))
        stats["flushed_mm2"] += _area(pending.difference(nxt)) if j + 1 < n else 0.0
        # pending carries only unprinted area, and only area the next layer still
        # contains (forced already took everything else out of the old pending).
        # Both sides of the state machine are cleaned: they are what next layer's
        # offsets grow from, so keeping them lean is what keeps the chain fast.
        pending = _clean(_union([pending, defer_now]).difference(allow))

        if _area(cur.difference(allow)) > 1e-9:
            stats["touched_layers"] += 1

        allowed.append(allow)
        reached = _clean(allow)

    return allowed, stats


# ---------------------------------------------------------------------------------
# Orca glue -- reading and writing the live slicing graph
# ---------------------------------------------------------------------------------
# Only the API surface the Support Fins plugin already proved against a real Orca
# nightly is used: layer.regions() -> region.slices (read, set, append),
# layer.make_slices(), layer.slice_z, orca.host.ExPolygon(contour, holes) built
# from int64 rings, and orca.slicing.unscale(1) for the scaled-unit size. The
# mesh is never read -- the wave works purely on slice polygons, so there is no
# frame calibration to get wrong.


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


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _to_shapely(expoly, unit):
    """Orca ExPolygon (scaled int64) -> shapely Polygon (mm)."""
    c = np.asarray(expoly.contour.as_array(), dtype=np.float64) * unit
    if len(c) < 3:
        return _empty()
    holes = []
    for h in expoly.holes:
        a = np.asarray(h.as_array(), dtype=np.float64) * unit
        if len(a) >= 3:
            holes.append(a)
    p = _SPoly(c, holes)
    if not p.is_valid:
        p = p.buffer(0)
    return p


def _to_expolys(geom, unit):
    """Shapely polygonal geometry (mm) -> list of Orca ExPolygons (scaled int64)."""
    out = []
    for poly in _polygons(geom):
        if poly.is_empty or poly.area <= 0.0:
            continue
        ext = np.rint(np.asarray(poly.exterior.coords)[:-1] / unit).astype(np.int64)
        if len(ext) < 3:
            continue
        holes = []
        for r in poly.interiors:
            a = np.rint(np.asarray(r.coords)[:-1] / unit).astype(np.int64)
            if len(a) >= 3:
                holes.append(a)
        out.append(orca.host.ExPolygon(ext, holes))
    return out


def _layer_union(layer, unit):
    """Union of every region's slices on this layer, as one shapely geometry (mm)."""
    polys = []
    for region in layer.regions():
        for s in region.slices.surfaces:
            polys.append(_to_shapely(s.expolygon, unit))
    return _union(polys)


def _clip_layer(layer, allowed, unit):
    """Clip every region's slices to `allowed`, keeping surface types, then let
    Orca re-derive the layer's islands. Returns the number of regions touched."""
    touched = 0
    for region in layer.regions():
        by_type = {}
        for s in region.slices.surfaces:
            kept = _to_shapely(s.expolygon, unit).intersection(allowed)
            if kept.is_empty:
                continue
            for e in _to_expolys(kept, unit):
                by_type.setdefault(s.surface_type, []).append(e)
        items = list(by_type.items())
        if not items:
            # The whole region was deferred; empty the collection explicitly.
            region.slices.set([], orca.host.SurfaceType.stInternal)
            touched += 1
            continue
        region.slices.set(items[0][1], items[0][0])
        for t, es in items[1:]:
            region.slices.append(es, t)
        touched += 1
    layer.make_slices()
    return touched


def _slice_heights(layers):
    """Per-layer heights from slice_z mid-planes (works for variable layers too)."""
    zs = [float(L.slice_z) for L in layers]
    heights = [max(1e-3, 2.0 * zs[0])]      # first layer: mid-plane to bottom
    for j in range(1, len(zs)):
        heights.append(max(1e-3, zs[j] - zs[j - 1]))
    return heights


def inject_ripples(print_object, cfg, unit, log=None):
    """Ripple one PrintObject's flat overhangs. Returns a short result string."""
    log = {} if log is None else log
    layers = list(print_object.layers())
    if len(layers) < 2:
        return "nothing to ripple (single layer)"
    U = [_layer_union(L, unit) for L in layers]
    if all(u.is_empty for u in U):
        return "no slices found"
    heights = _slice_heights(layers)
    allowed, stats = plan_ripples(U, heights, cfg)
    mode = stats.get("mode", "skin")
    log.update({
        "layers": len(layers),
        "layer_height": round(float(heights[1]) if len(heights) > 1 else heights[0], 4),
        "mode": mode,
        "patterned_layers": stats.get("patterned_layers", 0),
        "rings": stats.get("rings", 0),
        "dashes": stats.get("dashes", 0),
        "grooved_mm2": round(stats.get("grooved_mm2", 0.0), 3),
        "ripple_layers": stats.get("ripple_layers", 0),
        "max_ring": stats.get("max_ring", 0),
    })
    # Conservation check on the model's own terms. Skin mode's only legitimate
    # removal is the groove area it just accounted for, per layer (the UNION of
    # printed layers often loses nothing at all: the layer above a groove prints
    # the model's full footprint, so the grooves only texture the underside).
    # Ramp mode must not lose or add anything anywhere. Anything else refuses
    # to touch the layers.
    before = _union(U)
    after = _union(allowed)
    union_lost = _area(before.difference(after))
    added = _area(after.difference(before))
    removed = sum(_area(u.difference(a)) for a, u in zip(allowed, U))
    log["conservation"] = {"removed_mm2": round(removed, 6),
                           "union_lost_mm2": round(union_lost, 6),
                           "added_mm2": round(added, 6)}
    grooved = float(stats.get("grooved_mm2", 0.0))
    if mode == "skin":
        if added > 0.05 or union_lost > grooved + 0.05 or abs(removed - grooved) > 0.05:
            raise RuntimeError(f"ripple plan fails conservation: added {added:.3f} mm^2, "
                               f"removed {removed:.3f} mm^2 vs {grooved:.3f} mm^2 of grooves, "
                               f"union lost {union_lost:.3f} mm^2")
    else:
        if union_lost > 0.05 or added > 0.05:
            raise RuntimeError(f"ripple plan fails conservation: lost {union_lost:.3f} mm^2, added {added:.3f} mm^2")

    touched = 0
    for L, a, u in zip(layers, allowed, U):
        if _area(u.difference(a)) <= 1e-9:
            continue           # layer unchanged: leave Orca's polygons bit-exact
        touched += _clip_layer(L, a, unit)
    log["touched_layers"] = touched
    if mode == "skin":
        if stats["patterned_layers"] <= 0:
            return "no flat overhangs to ripple"
        return (f"rippled the underside of {stats['patterned_layers']} layer(s): "
                f"{stats['rings']} ring(s), {stats['dashes']} dash(es), "
                f"{stats['grooved_mm2']:.0f} mm^2 of grooves -- part shape unchanged "
                f"({touched} layer(s) edited)")
    if stats["rippled_mm2"] <= 0.0:
        return "no flat overhangs to ripple"
    return (f"rippled {stats['rippled_mm2']:.0f} mm^2 of flat overhang across "
            f"{stats['ripple_layers']} layer(s), {stats['max_ring']} ring(s) deep; "
            f"{stats['unreached_mm2']:.0f} mm^2 left for Orca "
            f"({touched} layer(s) edited)")


class WaveOverhangsSlicing(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Wave Overhangs"

    def get_default_config(self):
        return dict(_DEFAULTS)

    def execute(self, ctx):
        if ctx.step != orca.slicing.Step.posSlice or ctx.object is None:
            return orca.ExecutionResult.success()
        cfg = _cfg(self)
        if not cfg["enabled"]:
            return orca.ExecutionResult.success("Wave Overhangs: disabled in plugin config")
        if np is None or shapely is None:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                "Wave Overhangs needs numpy and shapely (install failed? try "
                "disabling/enabling the plugin or reinstalling it)")
        po = ctx.object
        if cfg["apply_to"] != "all" and _truthy(po.config_value("enable_support")):
            return orca.ExecutionResult.success(
                "Wave Overhangs: skipped (Orca supports are on for this part)")
        if ctx.cancelled():
            return orca.ExecutionResult.success("Wave Overhangs: cancelled")
        log = {"object_id": _safe(lambda: po.id()), "started": time.time()}
        try:
            msg = inject_ripples(po, cfg, orca.slicing.unscale(1), log)
        except Exception as e:  # never break a slice over ripples; report and carry on
            log["error"] = f"{type(e).__name__}: {e}"
            _write_log(log)
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                f"Wave Overhangs: {type(e).__name__}: {e}")
        log["result"] = msg
        log["seconds"] = round(time.time() - log["started"], 3)
        _write_log(log)
        return orca.ExecutionResult.success(f"Wave Overhangs: {msg}")


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


def _write_log(entry):
    """One JSON line per sliced object, next to the plugin file
    (wave_overhangs_log.jsonl). Best effort -- never fails the slice."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "wave_overhangs_log.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------------
# Script capability: a Plugins-dialog smoke test + dry-run report
# ---------------------------------------------------------------------------------

class WaveOverhangsCheck(orca.script.ScriptPluginCapabilityBase):
    """Run from Plugins -> Wave Overhangs -> Run.

    Checks the dependency install, runs the geometry core on a synthetic
    flat overhang (a table top standing on a stem) and reports what it would
    do, without needing a slice. The first failure a user would otherwise
    see is mid-slice; this catches it first, the same job the Support Fins
    setup check does for fins."""

    def get_name(self):
        return "Wave Overhangs - Check setup"

    def execute(self):
        lines = ["Wave Overhangs setup check"]
        if np is None or shapely is None:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                "Wave Overhangs needs numpy and shapely. Orca should install them "
                "from the plugin metadata; try disabling/enabling the plugin or "
                "reinstalling it.")
        lines.append("deps: numpy + shapely import fine")
        try:
            ok, detail = self._self_test()
            lines.append(f"geometry core: {detail}")
            if not ok:
                return orca.ExecutionResult.failure(
                    orca.PluginResult.RecoverableError,
                    "Wave Overhangs self-test failed: " + detail)
        except Exception as e:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                f"Wave Overhangs self-test raised: {type(e).__name__}: {e}")
        try:
            model = orca.host.model()
            objects = list(model.objects())
            lines.append(f"model: {len(objects)} object(s) on the plate")
        except Exception as e:
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError,
                f"orca.host.model() failed: {type(e).__name__}: {e}")
        lines.append("next: choose the Wave Overhangs capability in a process preset "
                     "under Others -> Slicing Pipeline Plugin, then slice")
        return orca.ExecutionResult.success("\n".join(lines))

    @staticmethod
    def _self_test():
        """A tabletop on a stem, sliced into 4 synthetic layers of 0.2 mm: the
        ceiling's underside must get grooves -- thin, rim-safe, connected --
        and nothing may be added anywhere."""
        from shapely.geometry import box
        cfg = dict(_DEFAULTS)
        stem = box(-5, -5, 5, 5)
        top = box(-15, -15, 15, 15)
        U = [stem, stem, top, top]
        allowed, stats = plan_ripples(U, [0.2] * 4, cfg)
        g = float(cfg["groove_width_mm"])
        before, after = _union(U), _union(allowed)
        added = _area(after.difference(before))
        if added > 1e-6:
            return False, f"conservation broken (added {added:.6f} mm^2)"
        if stats["patterned_layers"] != 1 or stats["rings"] < 1:
            return False, "the synthetic ceiling was not rippled"
        removed = U[2].difference(allowed[2])
        if removed.is_empty:
            return False, "no grooves on the ceiling layer"
        for c in _polygons(removed):
            if not c.buffer(-(g / 2.0 + 0.02)).is_empty:
                return False, "a groove is wider than the configured width (a chunk)"
        if _n_components(allowed[2]) != _n_components(U[2]):
            return False, "the grooves disconnected the skin"
        return True, (f"ok -- {stats['rings']} ring(s), {stats['dashes']} dash(es), "
                      f"{stats['grooved_mm2']:.1f} mm^2 of grooves, shape unchanged")


@orca.plugin
class WaveOverhangsPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(WaveOverhangsSlicing)
        orca.register_capability(WaveOverhangsCheck)
