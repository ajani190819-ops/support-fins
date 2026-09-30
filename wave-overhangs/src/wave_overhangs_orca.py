# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=1.26", "shapely>=2.0"]
#
# [tool.orcaslicer.plugin]
# name = "Wave Overhangs"
# description = "Ripples flat overhangs into terraces that conform to the support perimeter and grow outward layer by layer, so they print without support material. Orca's own supports, if on, only appear where the ripples could not reach."
# author = "support-fins repo (Orca lane pattern; wave strategy after Andersons et al. and the OrcaSlicer-WaveOverhangs fork)"
# version = "0.1.0"
# ///
"""Wave Overhangs for OrcaSlicer -- ripples carved at slice time.

WHAT IT DOES
  A flat overhang -- the underside of a ceiling, a tabletop standing on a
  stem, the roof of a tunnel -- normally prints as one layer of material
  hanging in mid-air, and sags. This plugin replaces it with RIPPLES: on the
  overhang's first layer only a narrow seed band along the supported
  perimeter is kept, and every following layer may grow the printed
  footprint one `step` further outward, following that perimeter's shape
  exactly. Each new ring rests on the ring the layer below just printed, so
  nothing is printed over air: the flat ceiling becomes a terraced ramp that
  climbs at a printable angle. The ripples conform to the support perimeter
  and propagate outward like waves on a pond -- around corners, around
  holes, whatever the perimeter does.

  This is the same idea as the wave-overhang strategy of Andersons et al.
  ("Wave-inspired path-planning for support-free horizontal overhangs in
  FDM") and the OrcaSlicer-WaveOverhangs forks, expressed at the only seam
  a Python plugin can write to.

THE SEAM
  The one the Support Fins plugin uses: orca.slicing.Step.posSlice, after
  Orca has sliced the part into per-layer polygons but before perimeters,
  infill, supports and G-code. Unlike fins, which ADD polygons, this plugin
  only ever SHRINKS them -- a ripple is material printed one layer later
  than the model asked for. Everything downstream is then Orca's own logic
  applied to the rippled solid: walls, infill, bridge detection, overhang
  speeds. Supports left on are generated for the geometry that remains, so
  they appear only where the ripples could not reach -- wave-aware support
  integration without touching support code.

  Detection is footprint-based, like the wave forks' "the wave only fills
  the unsupported portion of each layer": the overhang region of layer j is
  what its slice has that layer j-1 did not. A region counts as FLAT when
  it is wider than h / tan(threshold_deg) -- a 45-degree slope grows
  0.2 mm per 0.2 mm layer and is never touched; a ceiling appears all at
  once and ripples. Shrinking footprints (treads, dome tops) never grow, so
  top surfaces are never touched either.

WHY ONE RING PER LAYER (and what that costs)
  The forks print their rings as custom paths inside a single layer, each
  squished against the previous ring at the same Z. A Python plugin cannot
  write toolpaths, only slice polygons, so this is the planar form: ring k
  of the wave prints on layer k. Two consequences, both bounded and both
  stated up front:
    * the overhang's underside becomes a terraced ramp instead of a flat
      ceiling. The ramp climbs layer_height per step of reach; it is
      material REMOVED from the model, never more than the solid above the
      overhang allows (a ripple stops at the model's top surface: deferring
      past the last layer that still contains the point is simply not
      allowed) and never more than max_reach_mm;
    * where the ramp cannot arrive in time -- thin roofs, overhangs wider
      than the reach -- the leftover prints on the overhang's own layer,
      exactly as stock Orca would have sliced it (bridging, or on Orca
      supports if they are enabled).

  The ripples show on the outside of the part too: near the overhang the
  outer wall steps out with the terraces instead of rising vertically. On
  visible ceilings that is the point; on hidden ones nobody will see it.

GUARANTEES (each one is pinned by a test)
  * Nothing is added: every layer's printed polygons are a subset of that
    layer's model slices.
  * Nothing is lost: the union of the printed layers equals the union of
    the model's slices. Every deferred point either prints when its ripple
    reaches it, or is flushed back in at its last opportunity (the final
    layer of the solid there).
  * The first layer (bed or raft) is never touched.
  * Surfaces steeper than threshold_deg from horizontal are never touched.

LIMITS
  * The ripples appear in the sliced Preview, not in the Prepare 3D view.
  * The rings print at your configured wall / overhang speeds. Orca's
    overhang-speed logic sees them (each ring is a genuine small overhang),
    so configure that -- and your cooling -- before pushing ramp_angle_deg
    past ~55.
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
    from shapely.geometry import Polygon as _SPoly
    from shapely.ops import unary_union
except ImportError:  # pragma: no cover - surfaced to the user in execute()
    shapely = None

_DEFAULTS = {
    "enabled": True,
    "apply_to": "all",           # "all" | "no-supports": parts to ripple
    "threshold_deg": 30.0,       # ripple surfaces flatter than this (from horizontal)
    "ramp_angle_deg": 45.0,      # steepness of the ripple ramp (from horizontal)
    "max_reach_mm": 10.0,        # how far a ripple may climb out from the perimeter
    "min_area_mm2": 1.0,         # ignore overhang patches smaller than this
    "anchor_mm": 0.1,            # seed band width: fresh area this close to the
                                 # previous layer prints at once (also the floor on
                                 # ring width when ramp_angle is very steep)
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


def _clean(g):
    if g is None or g.is_empty:
        return g
    try:
        s = g.simplify(_CLEAN_TOL, preserve_topology=False)
        return s if (not s.is_empty and s.is_valid) else g
    except Exception:
        return g


def _buf(g, r):
    """Dilate by r with round joins.

    Round joins make this a true distance field: ring k of the wave is the
    exact k*step offset contour of the perimeter it grows from, corners
    rounded, concavities followed -- "conforming to the support perimeter"
    is a property of the maths, not an approximation we tune."""
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


def plan_ripples(slices_mm, heights_mm, cfg):
    """Turn a stack of model slices into the stack that may actually print.

    slices_mm:  per layer, the union of the object's slice polygons (shapely,
                millimetres, any frame -- only differences matter).
    heights_mm: per layer, the layer height (mm).
    Returns (allowed_per_layer, stats): `allowed[j]` is the shapely geometry
    layer j may print -- a subset of slices_mm[j], and never empty where the
    model has material.

    State machine, one pass bottom-up:
      reached   what the previous layer actually printed (the wavefront's base)
      pending   overhang area already deferred, waiting for its ripple

    Per layer:
      grown      = cur within `step` of `reached` -- this layer's ring, the seed
                  band, and everything continuous with the layer below
      new_area   = cur beyond `anchor` of `reached` -- the fresh footprint
      wide       = components of new_area wide enough to count as FLAT overhang
                  (an erosion test: the region must contain a disk of radius
                  h / 2 tan(threshold)); thin lips of steep slopes print at once
      cand       = wide beyond this layer's ring: the ripple candidates
      defer_now  = the part of cand the wave will reach while the solid is still
                  there -- ring k prints at layer j+k-1, so a point may wait only
                  if it is inside the model's slices at that layer. This is what
                  bounds the ramp by the thickness of the solid above the
                  overhang, and by max_reach_mm.
      forced     = what cannot or should not wait: pending whose next layer is
                  its last, candidates the wave cannot reach in time, and
                  (safety valve) pending stranded beyond the reach.

    Invariants (they are the point of the shape of this loop):
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
        "layers": n,
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

        # Split the fresh footprint into flat overhang (ripple candidates) and
        # thin lips of steep surfaces (print at once, like stock Orca).
        wide_parts = []
        for c in _polygons(new_area):
            if c.area < min_area:
                continue
            if c.buffer(-max(wide_w, 1e-3) / 2.0).is_empty:
                continue          # no disk of radius wide_w/2 fits: a steep lip
            wide_parts.append(c)
        wide = _union(wide_parts)
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
    log.update({
        "layers": len(layers),
        "layer_height": round(float(heights[1]) if len(heights) > 1 else heights[0], 4),
        "ripple_layers": stats["ripple_layers"],
        "rippled_mm2": round(stats["rippled_mm2"], 3),
        "unreached_mm2": round(stats["unreached_mm2"], 3),
        "flushed_mm2": round(stats["flushed_mm2"], 3),
        "max_ring": stats["max_ring"],
    })
    # Conservation check on the model's own terms: nothing added, nothing lost.
    # (Rounding to Orca's 1e-6 mm grid can shave fractions of a mm^2; a mm^2 is
    # already far below a nozzle's business.)
    before = _union(U)
    after = _union(allowed)
    lost = _area(before.difference(after))
    added = _area(after.difference(before))
    log["conservation"] = {"lost_mm2": round(lost, 6), "added_mm2": round(added, 6)}
    if lost > 0.05 or added > 0.05:   # pragma: no cover - guards a broken edit
        raise RuntimeError(f"ripple plan fails conservation: lost {lost:.3f} mm^2, added {added:.3f} mm^2")

    touched = 0
    for L, a, u in zip(layers, allowed, U):
        if _area(u.difference(a)) <= 1e-9:
            continue           # layer unchanged: leave Orca's polygons bit-exact
        touched += _clip_layer(L, a, unit)
    log["touched_layers"] = touched
    if stats["rippled_mm2"] <= 0.0:
        return "no flat overhangs needed ripples"
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
    do, without needing a slice: ripples found, rings, conservation. The
    first failure a user would otherwise see is mid-slice; this catches it
    first, the same job the Support Fins setup check does for fins."""

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
        ceiling must defer, and nothing may be lost or added."""
        from shapely.geometry import box
        cfg = dict(_DEFAULTS)
        cfg["max_reach_mm"] = 1.0     # small so the test is quick and sharp
        stem = box(-5, -5, 5, 5)
        top = box(-15, -15, 15, 15)
        U = [stem, stem, top, top]
        allowed, stats = plan_ripples(U, [0.2] * 4, cfg)
        before, after = _union(U), _union(allowed)
        lost = _area(before.difference(after))
        added = _area(after.difference(before))
        if lost > 1e-6 or added > 1e-6:
            return False, (f"conservation broken (lost {lost:.6f} mm^2, "
                           f"added {added:.6f} mm^2)")
        if stats["rippled_mm2"] <= 0.0:
            return False, "the synthetic ceiling was not deferred"
        # layer 2 (the ceiling) must print less than the full top; layer 3 grows
        if _area(allowed[2]) >= _area(top) - 1e-6 or _area(allowed[3]) <= _area(allowed[2]) + 1e-6:
            return False, "the ripple does not grow outward layer by layer"
        return True, (f"ok -- {stats['rippled_mm2']:.1f} mm^2 deferred, "
                      f"{stats['max_ring']} ring(s), conservation exact")


@orca.plugin
class WaveOverhangsPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(WaveOverhangsSlicing)
        orca.register_capability(WaveOverhangsCheck)
