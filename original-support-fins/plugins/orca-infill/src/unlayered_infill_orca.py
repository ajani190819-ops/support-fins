# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "Unlayered Infill"
# description = "Non-planar sparse infill: rides a sine wave in Z so successive layers interlock instead of stacking as clean planes, tapering to flat where it meets the solid skin."
# author = "Unlayered Infill plugin lane"
# version = "0.1.0"
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

SETUP — ONE preset field
  Process preset > Others > **Post-processing plugin** > "Unlayered Infill".
  It does *not* go in the Slicing Pipeline Plugin field; that field drives the
  geometry steps, which this plugin ignores.

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
try:
    import nonplanar_core as npc
except ImportError:  # pragma: no cover - replaced by the inlined module at build
    npc = None


_DEFAULTS = {
    "enabled": True,
    # mm, or a share of the layer height: "-150%", "-1.5x".
    # Negative dips the wave into the part, which keeps the nozzle clear.
    "amplitude": "-0.2",
    "frequency": 1.5,
    "segment_mm": 1.0,
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
            )
        except npc.NonPlanarError as e:
            # A clear, actionable stop -- surfaced as a slicing error.
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

        if not stats["moves"]:
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
        lines.append("This plugin needs exactly ONE process-preset field:")
        lines.append("  Others -> Post-processing plugin -> Unlayered Infill")
        lines.append("")
        lines.append("Do NOT put it in 'Slicing Pipeline Plugin' — that field")
        lines.append("drives the geometry steps, which this plugin ignores.")
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
