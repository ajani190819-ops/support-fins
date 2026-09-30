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
try:
    import wave_core as wc
except ImportError:  # pragma: no cover - replaced by the inlined module at build
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
