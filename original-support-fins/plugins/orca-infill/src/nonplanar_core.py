"""Non-planar infill engine: pure text in, pure text out, stdlib only.

Adapted from `nonPlanarInfill.py`, Copyright (c) 2025 Roman Tenger (TenTech),
GPL-3.0 — https://github.com/TengerTechnologies/NonPlanarInfill — by way of the
"Non-Planar Infill Tool" kept verbatim at
`my-plugins/unlayered-infill/reference/nonplanar_infill_tool.py`.

The idea is unchanged: inside sparse-infill sections, split each extrusion into
short segments and ride a sine wave in Z, `dz = amplitude * scale * sin(f * x)`,
with `scale` tapering to zero as the infill approaches the solid skin above or
below it. Successive layers then interlock instead of stacking as clean planes.

Five behaviours differ from the reference, each pinned by a test in
`tests/test_nonplanar_core.py`:

1. **Extrusion is attached to the right move.** In G-code the `E` on a line
   describes the move that *ends* at that line's coordinates. The reference
   read `(x, y)` off the current line as the segment *start* and `(x, y)` off
   the next line as the end, so every stroke was printed with its neighbour's
   extrusion. On a plain rectilinear layer that under-extrudes the long infill
   strokes by ~44% and over-extrudes the short repositioning moves.

2. **No duplicated points.** `segment_line()` returned both endpoints, so each
   emitted stroke re-stated the previous stroke's final point *with a share of
   the extrusion* — a zero-length extruding move, i.e. a blob, at every
   junction (44 of them in a ten-layer test cube).

3. **Z is restored on the way out.** The reference left the nozzle at whatever
   displaced Z the last wave segment reached. Anything printed afterwards in
   the same layer — gap fill, a top surface, the wipe — ran at that wrong
   height until the next layer change reset it.

4. **Orca's skins are recognised.** Solid layers were matched on
   `"solid infill"` only. OrcaSlicer and Bambu Studio label the outermost
   skins `;TYPE:Top surface` and `;TYPE:Bottom surface`, so the real bottom
   was never found and the taper measured from the build plate instead. That
   makes the wave far too aggressive right next to the bottom skin.

5. **The taper can't inverate.** `next_top_layer` kept a stale value once the
   nozzle rose above the topmost solid layer, which made `d_top` negative and
   flipped the wave's sign. Infill that isn't bracketed by solid above *and*
   below now simply gets no wave.

Section markers are matched on `;TYPE:` lines only, and case-insensitively:
PrusaSlicer writes `;TYPE:Internal infill`, Orca `;TYPE:Sparse infill`.
"""
import math
import re

TYPE_PREFIX = ";type:"
INFILL_MARKERS = ("internal infill", "sparse infill")
# "internal solid infill" contains "solid infill"; Orca/Bambu name the outer
# skins "top surface" / "bottom surface", which the reference missed.
SOLID_MARKERS = ("solid infill", "top surface", "bottom surface")

DEFAULT_AMPLITUDE = "-0.2"   # mm, or "%"/"x" of layer height; negative dips in
DEFAULT_FREQUENCY = 1.5
DEFAULT_SEGMENT_MM = 1.0

_WORD = re.compile(r"([A-Za-z])\s*([-+]?\d*\.?\d+)")
_Z = re.compile(r"Z([-+]?\d*\.?\d+)")


class NonPlanarError(Exception):
    """A problem worth stopping for, explained in plain English."""


def parse_words(line):
    """`G1 X1 Y2 E.5 ; comment` -> {'G':1.0,'X':1.0,'Y':2.0,'E':0.5}."""
    body = line.split(";", 1)[0]
    if not body.strip():
        return None
    words = {}
    for m in _WORD.finditer(body):
        words.setdefault(m.group(1).upper(), float(m.group(2)))
    return words or None


def section_name(line):
    """The section label if this is a `;TYPE:` line, else None."""
    low = line.lower()
    if low.startswith(TYPE_PREFIX):
        return low[len(TYPE_PREFIX):].strip()
    return None


def is_infill_section(name):
    return any(m in name for m in INFILL_MARKERS)


def is_solid_section(name):
    return any(m in name for m in SOLID_MARKERS)


def detect_extrusion_mode(lines):
    """'relative' (M83), 'absolute' (M82), or 'unknown'."""
    head = "\n".join(lines[:400])
    if "M83" in head:
        return "relative"
    if "M82" in head:
        return "absolute"
    tail = "\n".join(lines[-4000:])  # the slicer config block lives at the end
    if "relative_extrusion = 1" in tail or "use_relative_e_distances = 1" in tail:
        return "relative"
    if "relative_extrusion = 0" in tail or "use_relative_e_distances = 0" in tail:
        return "absolute"
    return "unknown"


def detect_layer_height(lines):
    """The layer height actually being printed: the most common `;HEIGHT:`,
    falling back to the slicer's `layer_height = ` config line."""
    heights = []
    config_lh = None
    for line in lines:
        if line.startswith(";HEIGHT:"):
            try:
                heights.append(float(line[len(";HEIGHT:"):].strip()))
            except ValueError:
                pass
        elif config_lh is None and "layer_height" in line:
            m = re.search(r";\s*layer_height\s*=\s*(\d*\.?\d+)", line)
            if m and "first_layer" not in line:
                config_lh = float(m.group(1))
    if heights:
        from statistics import multimode
        return max(multimode(heights))
    return config_lh


def resolve_amplitude(spec, lines):
    """'-0.2' -> mm; '200%' / '-1.5x' -> that fraction of the layer height.

    Returns (millimetres, human description).
    """
    s = str(spec).strip()
    if not s:
        raise NonPlanarError(
            "No amplitude given. Use mm (e.g. -0.2), a percent of layer height "
            "(e.g. -150%), or a multiple (e.g. -1.5x).")
    relative = s.endswith("%") or s.lower().endswith("x")
    try:
        value = float(s[:-1]) if relative else float(s)
    except ValueError:
        raise NonPlanarError(
            f"Could not understand amplitude {s!r}. Use mm (e.g. -0.2), a "
            f"percent of layer height (e.g. -150%), or a multiple (e.g. -1.5x).")
    if not relative:
        return value, f"{value:.3f} mm (fixed value)"
    mult = value / 100.0 if s.endswith("%") else value
    lh = detect_layer_height(lines)
    if lh is None:
        raise NonPlanarError(
            f"You gave the amplitude as {s} of layer height, but this G-code has "
            "no layer height in it (no ';HEIGHT:' comments and no "
            "'layer_height =' config line). Give the amplitude in plain mm "
            "instead, e.g. -0.2.")
    amp = mult * lh
    return amp, f"{s} of layer height {lh:.3f} mm = {amp:.3f} mm"


def find_solid_layers(lines):
    """Z heights that carry a solid skin — the anchors the wave tapers into."""
    heights = set()
    z = 0.0
    for line in lines:
        words = parse_words(line)
        if words and words.get("G") in (0.0, 1.0) and "Z" in words:
            z = words["Z"]
        name = section_name(line)
        if name and is_solid_section(name):
            heights.add(round(z, 4))
    return sorted(heights)


def taper_scale(z, solids):
    """0 at a solid skin, rising to 0.5 midway between the skins around it.

    Infill with no solid both above *and* below is not bracketed, so it gets no
    wave at all — that is the case the reference turned into a sign flip.
    """
    below = above = None
    for s in solids:
        if s < z - 1e-9:
            below = s if below is None else max(below, s)
        elif s > z + 1e-9:
            above = s if above is None else min(above, s)
    if below is None or above is None:
        return 0.0
    span = above - below
    if span <= 0:
        return 0.0
    return min(above - z, z - below) / span


def process(lines, amplitude_spec=DEFAULT_AMPLITUDE, frequency=DEFAULT_FREQUENCY,
            segment_mm=DEFAULT_SEGMENT_MM, require_relative_e=True):
    """Rewrite sparse-infill moves as wavy ones. Returns (out_lines, stats)."""
    amplitude, amp_desc = resolve_amplitude(amplitude_spec, lines)

    mode = detect_extrusion_mode(lines)
    if mode == "absolute" and require_relative_e:
        raise NonPlanarError(
            "This G-code uses ABSOLUTE extrusion (M82). Splitting moves would "
            "corrupt it.\n\nFix: OrcaSlicer > Printer Settings > Advanced > "
            "'Use relative E distances', then slice again.")

    solids = find_solid_layers(lines)
    frequency = float(frequency)
    segment_mm = max(0.05, float(segment_mm))

    out = []
    x = y = z = None
    in_infill = False
    displaced = False           # nozzle currently sitting at a wave-shifted Z
    sections = moves = segments = 0
    max_wiggle = 0.0
    skipped_unbracketed = 0

    def restore_z():
        """Never leave the nozzle on the wave once the infill stroke ends."""
        nonlocal displaced
        if displaced and z is not None:
            out.append(f"G1 Z{z:.3f}\n")
            displaced = False

    for line in lines:
        name = section_name(line)
        if name is not None:
            was = in_infill
            in_infill = is_infill_section(name)
            if in_infill and not was:
                sections += 1
            if not in_infill:
                restore_z()
            out.append(line)
            continue

        words = parse_words(line)
        if not words or words.get("G") not in (0.0, 1.0):
            out.append(line)
            continue

        nx = words.get("X", x)
        ny = words.get("Y", y)
        e = words.get("E")

        if "Z" in words:                 # the slicer's own Z always wins
            z = words["Z"]
            displaced = False

        movable = (in_infill and e is not None and e > 0 and "Z" not in words
                   and None not in (x, y, z) and (nx != x or ny != y))
        if movable:
            scale = taper_scale(z, solids)
            if scale > 0.0:
                length = math.hypot(nx - x, ny - y)
                n = max(1, int(length // segment_mm))
                feed = words.get("F")
                # Hand out the extrusion so the printed digits sum to exactly
                # `e`. Rounding each segment independently drifts, and across
                # a whole print that drift is systematic under/over-extrusion.
                spent = 0.0
                for i in range(1, n + 1):      # skip i=0: we are already there
                    t = i / n
                    sx = x + t * (nx - x)
                    sy = y + t * (ny - y)
                    dz = amplitude * scale * math.sin(frequency * sx)
                    if abs(dz) > max_wiggle:
                        max_wiggle = abs(dz)
                    share = round(e * t - spent, 5)
                    spent += share
                    tail = f" F{feed:.0f}" if (i == 1 and feed is not None) else ""
                    out.append(f"G1 X{sx:.3f} Y{sy:.3f} Z{z + dz:.3f} "
                               f"E{share:.5f}{tail}\n")
                segments += n
                moves += 1
                displaced = True
                x, y = nx, ny
                continue
            skipped_unbracketed += 1

        restore_z()
        out.append(line)
        x, y = nx, ny

    restore_z()

    return out, {
        "amplitude_mm": amplitude,
        "amplitude_desc": amp_desc,
        "extrusion_mode": mode,
        "solid_layers": len(solids),
        "sections": sections,
        "moves": moves,
        "segments": segments,
        "max_wiggle": max_wiggle,
        "skipped_unbracketed": skipped_unbracketed,
    }
