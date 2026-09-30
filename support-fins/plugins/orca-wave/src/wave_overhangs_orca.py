# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.0", "shapely>=2.0"]
#
# [tool.orcaslicer.plugin]
# name = "Wave Overhangs"
# description = "Experimental: print steep overhangs support-free by replacing the overhang region with wave-propagated toolpaths (port of the WaveOverhangs fork's algorithm as a slicing-pipeline plugin)."
# author = "Wave Overhangs plugin lane"
# version = "0.0.1"
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
try:
    import wave_core as wc
except ImportError:  # pragma: no cover - replaced by the inlined module at build
    wc = None


_DEFAULTS = {
    "enabled": True,
    "apply_to": "no-supports",   # "no-supports" | "all"
    "carve_overhang": True,      # remove the overhang area from Orca's slices
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

# Per-export stash: {(object_key, round(z,3)): [polylines_in_bed_mm]} plus meta.
_PLAN = {}


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


def _bed_offset(print_object, cfg):
    """Best-effort object-frame -> bed-absolute XY offset (mm).

    EXPERIMENTAL: Orca emits G-code in absolute bed coordinates, but layer slices
    are in the object's own frame. This returns the (dx, dy) to add. It tries the
    first instance's offset, then falls back to 0. Use the `xy_offset` config to
    override during calibration. Validate this first on any new Orca build.
    """
    manual = str(cfg.get("xy_offset") or "").strip()
    if manual:
        try:
            x, y = (float(v) for v in manual.split(","))
            return x, y
        except Exception:
            pass
    for getter in ("instances", "copies"):
        try:
            insts = list(getattr(print_object, getter)())
        except Exception:
            insts = []
        for inst in insts:
            for attr in ("shift", "offset"):
                try:
                    off = getattr(inst, attr)
                    off = off() if callable(off) else off
                    arr = np.asarray(off, dtype=np.float64).ravel()
                    if arr.size >= 2:
                        return float(arr[0]), float(arr[1])
                except Exception:
                    continue
    return 0.0, 0.0


# ---------------------------------------------------------------------------------
# Geometry step: compute + stash waves, optionally carve the overhang
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
    key = _object_key(po)
    dx, dy = _bed_offset(po, cfg)
    log["bed_offset"] = [dx, dy]
    wcfg = _wave_config(cfg, layer_height)

    layers = list(po.layers())
    prev_poly = shapely.geometry.Polygon()
    planned = 0
    carved = 0
    for layer in layers:
        try:
            z = float(layer.slice_z)
            cur = _layer_polygon_mm(layer, unit)
            if cur.is_empty:
                prev_poly = cur
                continue
            res = wc.plan_layer(cur, prev_poly, z, wcfg)
            if res.polylines:
                bed = [[(x + dx, y + dy) for (x, y) in pts] for pts in res.polylines]
                _PLAN[(key, round(z, 3))] = bed
                planned += 1
                log["layers"].append([round(z, 4), round(res.overhang_area, 3),
                                      res.n_tracks])
                if _truthy(cfg["carve_overhang"]):
                    if _carve_layer(layer, prev_poly, wcfg, unit):
                        carved += 1
            prev_poly = cur
        except Exception as e:  # never break a slice
            log.setdefault("errors", []).append(f"z={getattr(layer,'slice_z','?')}: "
                                                 f"{type(e).__name__}: {e}")
            prev_poly = shapely.geometry.Polygon()
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
                    region.slices.set(e, stype)
                    first = False
                else:
                    region.slices.append([e], stype)
        layer.make_slices()
        return True
    except Exception:
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


_Z_KEYS = (";Z:", ";HEIGHT:", ";LAYER_Z:")


def _parse_layer_z(line):
    for k in _Z_KEYS:
        if line.startswith(k):
            try:
                return float(line[len(k):].strip().split()[0])
            except Exception:
                return None
    # Fallback: a bare "G1 Zxx" / "G0 Zxx" layer move.
    if line.startswith(("G0 ", "G1 ")) and " Z" in line and " X" not in line:
        for tok in line.split():
            if tok.startswith("Z"):
                try:
                    return float(tok[1:])
                except Exception:
                    return None
    return None


def _match_plan_z(z, tol=1e-3):
    if z is None:
        return None
    best = None
    for (key, pz) in _PLAN:
        if abs(pz - z) <= max(tol, 1e-3):
            best = (key, pz)
            break
    return best


def _splice_gcode(gcode_path, cfg, log):
    with open(gcode_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    out = []
    inserted = 0
    pending = None  # a matched plan key to flush at the next layer boundary
    for line in lines:
        z = _parse_layer_z(line.strip())
        match = _match_plan_z(z) if z is not None else None
        if match is not None:
            out.append(line)
            polylines = _PLAN.get(match) or []
            block = wc.emit_layer_gcode(polylines, z=match[1],
                                        cfg=_wave_config(cfg, cfg.get("_lh", 0.2)))
            out.append("\n".join(block) + "\n")
            inserted += 1
            continue
        out.append(line)
    if inserted:
        with open(gcode_path, "w", encoding="utf-8") as f:
            f.writelines(out)
    log["spliced_layers"] = inserted
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
            log["seconds"] = round(time.time() - log["started"], 3)
            _write_log(log)
            return orca.ExecutionResult.success(f"Wave Overhangs: {msg}")

        # --- g-code seam: splice ---
        if ctx.step == orca.slicing.Step.psGCodePostProcess:
            if not _PLAN:
                return orca.ExecutionResult.success("Wave Overhangs: nothing to splice")
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
                _PLAN.clear()
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
                     "and enable carve_overhang before trusting output.")
        lines.append("next: choose Wave Overhangs under Others -> Slicing Pipeline Plugin")
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
