"""Wave-overhang toolpath core -- pure geometry, no OrcaSlicer bindings.

This is the algorithm behind dennisklappe/OrcaSlicer-WaveOverhangs (itself a port
of stmcculloch/PrusaSlicer-WaveOverhangs), reimplemented as a slicer-independent
Python module so it can run inside an Orca slicing-pipeline plugin AND be unit
tested offline with shapely.

The idea (see waveoverhangs.com "How it works"):

  * For each layer, the *overhang region* is the part of the layer that sticks out
    past the layer below -- it has nothing underneath it.
  * A *seed* is taken at the supported edge (the boundary between the overhang and
    the material below).
  * Wavefronts are grown outward from the seed into the overhang: each front is
    the set of points a fixed distance further from the supported edge than the
    last. Because we grow by buffering the supported region, the fronts naturally
    diffract around corners and holes, exactly like ripples on a pond.
  * Each front becomes an extrusion polyline. A pattern mode decides how the
    fronts are connected into a print order.

Everything here is in millimetres, in the object's own XY frame. Mapping into the
printer's absolute G-code coordinates is the plugin's job (see the plugin module).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Polygon,
)
from shapely.ops import linemerge, unary_union

# ---------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------


@dataclass
class WaveConfig:
    """Mirrors the fork's tunables (waveoverhangs.com "~20 expert tunables")."""

    # Detection
    overhang_tol: float = 0.05        # mm the layer below is grown before subtracting
    min_overhang_area: float = 0.5    # mm^2, ignore slivers

    # Wave field
    line_spacing: float = 0.35        # mm, centreline spacing between wave tracks
    line_width: float = 0.40          # mm, extrusion width of a wave line
    max_iterations: int = 400         # safety cap on wavefronts per region
    perimeter_overlap: float = 0.10   # mm, push the field toward the kept perimeter

    # Pattern: "monotonic" | "zigzag" | "smart"
    pattern: str = "smart"

    # Motion / cooling / flow (used by the G-code emitter)
    layer_height: float = 0.20
    flow_ratio: float = 1.0
    filament_diameter: float = 1.75
    print_speed: float = 2.0          # mm/s
    travel_speed: float = 120.0       # mm/s
    fan: float = 1.0                  # 0..1, forced during wave extrusion

    def mm3_per_mm(self) -> float:
        return self.line_width * self.layer_height * self.flow_ratio

    def e_per_mm(self) -> float:
        area = math.pi * (self.filament_diameter / 2.0) ** 2
        return self.mm3_per_mm() / area


# ---------------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------------


def _iter_lines(geom):
    """Yield LineStrings from any shapely geometry (skip empties/points)."""
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, LineString):
        yield geom
    elif isinstance(geom, (MultiLineString, GeometryCollection)):
        for g in geom.geoms:
            yield from _iter_lines(g)
    elif hasattr(geom, "boundary"):
        yield from _iter_lines(geom.boundary)


def overhang_region(layer: Polygon, support: Polygon, cfg: WaveConfig):
    """The part of `layer` that overhangs open air (not over `support`).

    `layer`   : this layer's sliced area.
    `support` : the layer-below area (what this layer can rest on). Empty for the
                first layer -> the whole layer is "supported" by the bed, so no
                overhang.
    """
    if support is None or support.is_empty:
        # First layer / nothing below: treat as fully supported by the bed.
        return Polygon()
    grown = support.buffer(cfg.overhang_tol) if cfg.overhang_tol else support
    ov = layer.difference(grown)
    if ov.is_empty:
        return ov
    # Drop slivers below the area threshold.
    keep = [p for p in _polys(ov) if p.area >= cfg.min_overhang_area]
    return unary_union(keep) if keep else Polygon()


def _polys(geom):
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if isinstance(geom, GeometryCollection):
        out = []
        for g in geom.geoms:
            out.extend(_polys(g))
        return out
    return []


# ---------------------------------------------------------------------------------
# Wavefront propagation
# ---------------------------------------------------------------------------------


@dataclass
class WaveTrack:
    distance: float               # mm from the supported edge (front index * spacing)
    points: list                  # [(x, y), ...] centreline polyline


def wave_tracks(support: Polygon, overhang: Polygon, cfg: WaveConfig):
    """Grow wavefronts from the supported edge across the overhang.

    Returns a list of WaveTrack ordered near->far from support. Each track is the
    portion of an offset of the supported boundary that lies inside the overhang.
    """
    tracks: list[WaveTrack] = []
    if overhang is None or overhang.is_empty or support is None or support.is_empty:
        return tracks

    # Let the first front sit half a spacing into the overhang, then step outward.
    # perimeter_overlap nudges the whole field back toward the kept perimeter/support
    # so the last front hugs the supported edge on the far side.
    base = 0.5 * cfg.line_spacing - cfg.perimeter_overlap
    target = overhang.buffer(1e-6)
    for i in range(cfg.max_iterations):
        d = base + i * cfg.line_spacing
        if d <= 0:
            continue
        grown = support.buffer(d)
        front = grown.boundary.intersection(target)
        made_any = False
        for ln in _iter_lines(front):
            if ln.length <= 1e-6:
                continue
            tracks.append(WaveTrack(distance=d, points=list(ln.coords)))
            made_any = True
        # Stop once the grown support fully covers the overhang (fronts run dry).
        if grown.contains(target):
            break
        if not made_any and d > 1e-6 and grown.covers(target):
            break
    return tracks


# ---------------------------------------------------------------------------------
# Pattern / ordering
# ---------------------------------------------------------------------------------


def _endpoints(pts):
    return pts[0], pts[-1]


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def order_tracks(tracks, support: Polygon, cfg: WaveConfig):
    """Turn wavefronts into an ordered list of printable polylines.

    monotonic : print near->far, each front as its own line (lots of travels).
    zigzag    : same order, but flip alternate fronts so the end of one is near
                the start of the next -> connected back-and-forth motion.
    smart     : like monotonic, but each front starts from its better-supported
                (nearer-to-support) end so no line begins in thin air.
    """
    ordered = sorted(tracks, key=lambda t: t.distance)
    polylines = []
    mode = (cfg.pattern or "smart").lower()

    if mode == "zigzag":
        flip = False
        for t in ordered:
            pts = list(reversed(t.points)) if flip else t.points
            polylines.append(pts)
            flip = not flip
        return polylines

    if mode == "smart" and support is not None and not support.is_empty:
        for t in ordered:
            a, b = _endpoints(t.points)
            # Start from whichever end is closer to the supported region.
            da = support.distance(_pt(a))
            db = support.distance(_pt(b))
            polylines.append(t.points if da <= db else list(reversed(t.points)))
        return polylines

    # monotonic (and fallback)
    return [t.points for t in ordered]


def _pt(xy):
    from shapely.geometry import Point

    return Point(xy[0], xy[1])


# ---------------------------------------------------------------------------------
# G-code emission
# ---------------------------------------------------------------------------------


def emit_layer_gcode(polylines, z, cfg: WaveConfig, restore_fan=None):
    """Emit G-code lines for one layer's wave polylines.

    Coordinates are absolute bed XY; `z` is the layer height. `restore_fan` is an
    optional 0..255 value to reset the fan to after the wave block (None = leave
    the forced wave fan in place; the plugin usually passes the layer's fan back).
    Relative-E is used inside the block and reset with M83/G92 so it composes with
    Orca's own extrusion accounting.
    """
    if not polylines:
        return []
    e_per_mm = cfg.e_per_mm()
    print_f = int(round(cfg.print_speed * 60))
    travel_f = int(round(cfg.travel_speed * 60))
    out = ["; ==== WAVE OVERHANG BEGIN ====",
           "M83",                                   # relative extrusion for our block
           f"M106 S{int(round(max(0.0, min(1.0, cfg.fan)) * 255))}"]
    for pts in polylines:
        if len(pts) < 2:
            continue
        x0, y0 = pts[0]
        out.append(f"G0 F{travel_f} X{x0:.3f} Y{y0:.3f} Z{z:.3f}")
        out.append(f"G1 F{print_f}")
        px, py = x0, y0
        for (x, y) in pts[1:]:
            seg = math.hypot(x - px, y - py)
            if seg <= 1e-9:
                continue
            out.append(f"G1 X{x:.3f} Y{y:.3f} E{seg * e_per_mm:.5f}")
            px, py = x, y
    if restore_fan is not None:
        out.append(f"M106 S{int(restore_fan)}")
    out.append("; ==== WAVE OVERHANG END ====")
    return out


# ---------------------------------------------------------------------------------
# One-call convenience
# ---------------------------------------------------------------------------------


@dataclass
class LayerWaveResult:
    z: float
    polylines: list = field(default_factory=list)
    overhang_area: float = 0.0
    n_tracks: int = 0


def plan_layer(layer: Polygon, support: Polygon, z: float, cfg: WaveConfig):
    """Full per-layer plan: detect overhang, propagate waves, order them."""
    ov = overhang_region(layer, support, cfg)
    if ov.is_empty:
        return LayerWaveResult(z=z)
    tracks = wave_tracks(support, ov, cfg)
    polylines = order_tracks(tracks, support, cfg)
    return LayerWaveResult(z=z, polylines=polylines,
                           overhang_area=float(ov.area), n_tracks=len(tracks))


# ---------------------------------------------------------------------------------
# G-code layer parsing, self-calibration and splicing (pure text; unit tested)
# ---------------------------------------------------------------------------------

Z_KEYS = (";Z:", ";HEIGHT:", ";LAYER_Z:")


def parse_layer_z(line: str):
    """The layer height a G-code line announces, or None.

    Handles Orca/Prusa comment markers (;Z: / ;HEIGHT: / ;LAYER_Z:) and a bare
    layer-change move (`G1 Z.. F..` with no X/Y).
    """
    s = line.strip()
    for k in Z_KEYS:
        if s.startswith(k):
            try:
                return float(s[len(k):].strip().split()[0])
            except Exception:
                return None
    if s[:2] in ("G0", "G1") and "Z" in s and " X" not in (" " + s) and " Y" not in (" " + s):
        for tok in s.split():
            if tok.startswith("Z"):
                try:
                    return float(tok[1:])
                except Exception:
                    return None
    return None


def _extruding_xy(line: str):
    """(x, y) for an extruding G1 move (has X, Y and an E token), else None."""
    s = line.strip()
    if not s.startswith("G1"):
        return None
    x = y = None
    has_e = False
    for tok in s.split():
        if tok.startswith("X"):
            try:
                x = float(tok[1:])
            except Exception:
                return None
        elif tok.startswith("Y"):
            try:
                y = float(tok[1:])
            except Exception:
                return None
        elif tok.startswith("E"):
            has_e = True
    if x is not None and y is not None and has_e:
        return (x, y)
    return None


def layer_extrusion_min(lines, target_z, tol=1e-3):
    """Min (x, y) corner of extruding moves on the layer nearest `target_z`."""
    minx = miny = None
    cur = None
    for line in lines:
        z = parse_layer_z(line)
        if z is not None:
            cur = z
            continue
        if cur is not None and abs(cur - target_z) <= tol:
            xy = _extruding_xy(line)
            if xy is not None:
                minx = xy[0] if minx is None else min(minx, xy[0])
                miny = xy[1] if miny is None else min(miny, xy[1])
    return (minx, miny)


def _match_z(z, plans, tol=1e-3):
    for pz in plans:
        if abs(pz - z) <= tol:
            return pz
    return None


def splice_gcode(text, layer_plans, cfg: WaveConfig, calibration):
    """Insert wave moves into exported G-code. Pure text in / out.

    layer_plans : {round(z,3): [polyline_in_object_frame, ...]}
    calibration : ("manual", dx, dy)                     -> use this XY offset, or
                  ("auto", calib_z, obj_min_x, obj_min_y) -> derive the offset by
                    aligning Orca's own printed outline on layer `calib_z` to the
                    object-frame outline min corner (a pure translation).

    Wave moves for a layer are inserted just before the NEXT layer marker, i.e.
    after Orca has printed that layer's own perimeters/infill.
    Returns (new_text, inserted_layer_count, (dx, dy)).
    """
    lines = text.splitlines(keepends=True)

    if calibration and calibration[0] == "manual":
        dx, dy = float(calibration[1]), float(calibration[2])
    elif calibration and calibration[0] == "auto":
        _, cz, omx, omy = calibration
        gmin = layer_extrusion_min(lines, cz)
        if gmin[0] is None or omx is None:
            dx, dy = 0.0, 0.0
        else:
            dx, dy = gmin[0] - omx, gmin[1] - omy
    else:
        dx, dy = 0.0, 0.0

    out = []
    inserted = 0
    pending = None  # (z, polylines) waiting to be flushed at the next layer marker

    def flush():
        nonlocal inserted
        if pending is None:
            return
        z, polys = pending
        shifted = [[(x + dx, y + dy) for (x, y) in pts] for pts in polys]
        for ln in emit_layer_gcode(shifted, z, cfg):
            out.append(ln + "\n")
        inserted += 1

    for line in lines:
        z = parse_layer_z(line)
        if z is not None:
            flush()
            pending = None
            key = _match_z(z, layer_plans)
            if key is not None:
                pending = (z, layer_plans[key])
        out.append(line)
    flush()

    return "".join(out), inserted, (dx, dy)

