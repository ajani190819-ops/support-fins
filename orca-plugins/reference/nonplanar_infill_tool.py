#!/usr/bin/env python3
#
# Non-Planar Infill Tool
# ----------------------
# Double-click this file, pick your sliced G-code, and it produces a
# *_nonplanar.gcode next to it with wavy (non-planar) interlocking infill.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version. See <https://www.gnu.org/licenses/>.
#
# Engine adapted from nonPlanarInfill.py
# Copyright (c) 2025 Roman Tenger (TenTech) - GPL-3.0
# https://github.com/TengerTechnologies/NonPlanarInfill
#
# Changes (2026):
#   - Works with OrcaSlicer / Bambu Studio G-code, not just PrusaSlicer
#     (";TYPE:sparse infill" / ";TYPE:internal solid infill" markers added).
#   - NEVER overwrites your file: writes a new <name>_nonplanar.gcode.
#   - Verifies relative-extrusion mode up front and refuses to produce
#     broken output if the G-code uses absolute E.
#   - Reports exactly what it did (sections found, moves modulated,
#     Z-wiggle range) so you are never left wondering "did it work?".
#   - Friendly window mode when double-clicked; terminal mode when given
#     a filename.
#   - "--inplace" mode for seamless slicer post-processing: OrcaSlicer can
#     call this automatically on every export, no separate steps at all.
#   - Amplitude may be given in mm or as a percentage/multiple of the layer
#     height actually used ("200%", "-1.5x"), read from the G-code itself.

import argparse
import math
import os
import re
import sys

# Section-name markers, compared case-insensitively: PrusaSlicer writes
# ";TYPE:Internal infill" / ";TYPE:Solid infill"; OrcaSlicer capitalizes
# (";TYPE:Sparse infill", ;TYPE:Internal solid infill"); Bambu Studio uses
# lowercase. Match on the word content, not the case.
INFILL_MARKERS = ("internal infill", "sparse infill")
SOLID_MARKERS = ("solid infill",)  # covers Prusa "Solid infill" and Orca/Bambu "internal solid infill"
TYPE_PREFIX = ";type:"

DEFAULT_AMPLITUDE = -0.2   # mm; negative dips the wave into the part (nozzle-safe)
DEFAULT_FREQUENCY = 1.5    # sine frequency along X
SEGMENT_LENGTH = 1.0       # mm; infill moves are split into segments this long


class ModulationError(Exception):
    """A problem worth stopping for, explained in plain English."""


def detect_extrusion_mode(lines):
    """Figure out if the file extrudes relatively (M83) or absolutely (M82)."""
    head = "\n".join(lines[:400])
    if "M83" in head:
        return "relative"
    if "M82" in head:
        return "absolute"
    tail = "\n".join(lines[-4000:])  # slicer config block lives at the end
    if "relative_extrusion = 1" in tail or "use_relative_e_distances = 1" in tail:
        return "relative"
    if "relative_extrusion = 0" in tail or "use_relative_e_distances = 0" in tail:
        return "absolute"
    return "unknown"


def segment_line(x1, y1, x2, y2, segment_length):
    """Split a straight move into points spaced ~segment_length apart."""
    segments = []
    total_length = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    num_segments = max(1, int(total_length // segment_length))
    for i in range(num_segments + 1):
        t = i / num_segments
        segments.append((x1 + t * (x2 - x1), y1 + t * (y2 - y1)))
    return segments


def detect_layer_height(lines):
    """Find the nominal layer height. Prefers the most common ;HEIGHT: value
    from layer-change comments (this is what is actually being printed, even
    with adaptive layer heights); falls back to the slicer's config block."""
    heights = []
    config_lh = None
    for line in lines:
        if line.startswith(";HEIGHT:"):
            try:
                heights.append(float(line[len(";HEIGHT:"):].strip()))
            except ValueError:
                pass
        else:
            m = re.search(r";\s*layer_height\s*=\s*(\d*\.?\d+)", line)
            if m and "first_layer" not in line:
                config_lh = float(m.group(1))
    if heights:
        from statistics import multimode
        return max(multimode(heights))
    return config_lh


def resolve_amplitude(spec, lines):
    """Turn the user's amplitude text into (mm_value, human_description).
    Accepts plain mm ("-0.2"), percent of layer height ("200%", "-200%"),
    or a multiple ("2x", "-1.5x")."""
    s = str(spec).strip()
    try:
        if s.endswith("%") or s.lower().endswith("x"):
            mult = float(s[:-1]) / 100.0 if s.endswith("%") else float(s[:-1])
            lh = detect_layer_height(lines)
            if lh is None:
                raise ModulationError(
                    f"You gave amplitude as {s} of layer height, but I couldn't "
                    "find any layer height in this G-code (no ';HEIGHT:' "
                    "comments). Re-slice with OrcaSlicer defaults, or give the "
                    "amplitude in plain mm, e.g. -0.2."
                )
            amp = mult * lh
            return amp, f"{s} of layer height {lh:.3f} mm = {amp:.3f} mm"
        return float(s), f"{float(s):.3f} mm (fixed value)"
    except ValueError:
        raise ModulationError(
            f"Could not understand amplitude '{s}'. Use mm (e.g. -0.2), a "
            f"percent of layer height (e.g. -150%), or a multiple (e.g. -1.5x)."
        )


def process_gcode(input_file, output_file, amplitude_spec, frequency, log=print):
    MOVE = r"X([-+]?\d*\.?\d+)\s*Y([-+]?\d*\.?\d+)\s*E([-+]?\d*\.?\d+)"

    log(f"Reading: {input_file}")
    with open(input_file, "r", errors="replace") as f:
        lines = f.readlines()
    log(f"  -> {len(lines)} lines")

    amplitude, amp_desc = resolve_amplitude(amplitude_spec, lines)
    log(f"Amplitude: {amp_desc}")

    mode = detect_extrusion_mode(lines)
    log(f"Extrusion mode: {mode}")
    if mode == "absolute":
        raise ModulationError(
            "This G-code uses ABSOLUTE extrusion (M82), which non-planar "
            "segmentation would corrupt.\n\nFix: in OrcaSlicer go to Printer "
            "Settings -> General -> Advanced and enable 'Use relative E "
            "distances', then re-slice and export again."
        )
    if mode == "unknown":
        log("  (could not confirm extrusion mode - assuming relative; if the "
            "print extrudes strangely, enable 'Use relative E distances' in "
            "your slicer and re-slice)")

    # Pass 1: where are the solid (top/bottom) layers?
    solid_infill_heights = []
    current_z = 0.0
    for line in lines:
        if line.startswith("G1") and "Z" in line:
            m = re.search(r"Z([-+]?\d*\.?\d+)", line)
            if m:
                current_z = float(m.group(1))
        if any(marker in line.lower() for marker in SOLID_MARKERS):
            solid_infill_heights.append(current_z)

    last_bottom_layer = 0.0
    next_top_layer = float("inf")

    # Pass 2: rewrite infill moves into Z-modulated segments.
    modified_lines = []
    in_infill = False
    current_z = 0.0
    processed = set()
    sections = 0
    moves_modulated = 0
    max_wiggle = 0.0

    for line_num, line in enumerate(lines):
        if line.startswith("G1") and "Z" in line:
            m = re.search(r"Z([-+]?\d*\.?\d+)", line)
            if m:
                current_z = float(m.group(1))
                lower = [z for z in solid_infill_heights if z < current_z]
                upper = [z for z in solid_infill_heights if z > current_z]
                if lower:
                    last_bottom_layer = max(lower)
                if upper:
                    next_top_layer = min(upper)

        if any(marker in line.lower() for marker in INFILL_MARKERS):
            in_infill = True
            sections += 1
        elif line.lower().startswith(TYPE_PREFIX):
            in_infill = False

        if in_infill and line_num not in processed and "E" in line:
            processed.add(line_num)
            m = re.search(MOVE, line)
            if m and line_num + 1 < len(lines):
                x1, y1, e = float(m.group(1)), float(m.group(2)), float(m.group(3))
                m2 = re.search(MOVE, lines[line_num + 1])
                if m2:
                    x2, y2 = float(m2.group(1)), float(m2.group(2))
                    segments = segment_line(x1, y1, x2, y2, SEGMENT_LENGTH)
                    n = len(segments)
                    e_per = e / n if n else e
                    total_span = next_top_layer - last_bottom_layer
                    for sx, sy in segments:
                        d_top = next_top_layer - current_z
                        d_bot = current_z - last_bottom_layer
                        scale = min(d_top, d_bot) / total_span if total_span else 0.0
                        dz = amplitude * scale * math.sin(frequency * sx)
                        max_wiggle = max(max_wiggle, abs(dz))
                        modified_lines.append(
                            f"G1 X{sx:.3f} Y{sy:.3f} Z{current_z + dz:.3f} E{e_per:.5f}\n"
                        )
                    moves_modulated += 1
                    continue

        modified_lines.append(line)

    with open(output_file, "w") as f:
        f.writelines(modified_lines)

    log(f"Infill sections found: {sections}")
    log(f"Infill moves modulated: {moves_modulated}")
    if moves_modulated:
        log(f"Biggest Z wiggle: {max_wiggle:.3f} mm")
    log(f"Saved: {output_file}")

    return {
        "sections": sections,
        "moves": moves_modulated,
        "max_wiggle": max_wiggle,
        "output": output_file,
    }


def default_output_path(input_file):
    folder = os.path.dirname(os.path.abspath(input_file))
    base = os.path.basename(input_file)
    name, _ = os.path.splitext(base)
    return os.path.join(folder, name + "_nonplanar.gcode")


# ---------------------------------------------------------------------------
# Mode 1: run from a terminal with a filename (optional; for power users)
# ---------------------------------------------------------------------------
def run_cli():
    parser = argparse.ArgumentParser(
        description="Add non-planar (sine-wave, interlocking) infill to sliced G-code."
    )
    parser.add_argument("input_file", nargs="?", help="G-code file to process")
    parser.add_argument("-amplitude", "--amplitude", type=str, default=str(DEFAULT_AMPLITUDE),
                        help="Wave depth: plain mm (e.g. -0.2) or a fraction of the "
                             "layer height, e.g. 200%% or 2x (use -amplitude=-200%% "
                             "for a negative/dip-down percentage).")
    parser.add_argument("-frequency", "--frequency", type=float, default=DEFAULT_FREQUENCY)
    parser.add_argument("-i", "--inplace", action="store_true",
                        help="Modify the file IN PLACE (required for slicer "
                             "post-processing: Orca hands us its temp file and "
                             "exports whatever we leave behind).")
    args = parser.parse_args()

    if not args.input_file:
        return False  # no file -> show the window instead

    if not os.path.isfile(args.input_file):
        print(f"ERROR: no such file: {args.input_file}")
        sys.exit(1)

    out = args.input_file if args.inplace else default_output_path(args.input_file)
    if args.inplace:
        print("In-place mode (slicer post-processing): rewriting the file itself.")
    try:
        stats = process_gcode(args.input_file, out, args.amplitude, args.frequency)
    except ModulationError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if stats["moves"] == 0:
        print("NOTE: 0 infill moves were changed. Is this sliced G-code with "
              "non-zero infill? (Thin all-wall parts have no infill.)")
    return True


# ---------------------------------------------------------------------------
# Mode 2: double-click -> friendly window
# ---------------------------------------------------------------------------
def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title("Non-Planar Infill Tool")
    root.geometry("560x420")

    state = {"file": ""}

    file_lbl = tk.Label(root, text="No G-code file chosen yet", wraplength=520,
                        anchor="w", justify="left")
    file_lbl.pack(padx=12, pady=(12, 4), fill="x")

    def browse():
        path = filedialog.askopenfilename(
            title="Choose the G-code you exported from your slicer",
            filetypes=[("G-code", "*.gcode *.gco *.g *.txt"), ("All files", "*.*")],
        )
        if path:
            state["file"] = path
            file_lbl.config(text=path)

    tk.Button(root, text="Choose G-code file...", command=browse).pack(pady=(0, 8))

    row = tk.Frame(root)
    row.pack()
    tk.Label(row, text="Amplitude (mm like -0.2, or % of layer height like -150%): ").pack(side="left")
    amp_var = tk.StringVar(value=str(DEFAULT_AMPLITUDE))
    tk.Entry(row, textvariable=amp_var, width=7).pack(side="left")
    tk.Label(row, text="   Frequency (e.g. 1.5): ").pack(side="left")
    freq_var = tk.StringVar(value=str(DEFAULT_FREQUENCY))
    tk.Entry(row, textvariable=freq_var, width=7).pack(side="left")

    status = tk.Text(root, height=12, wrap="word", state="disabled")
    status.pack(padx=12, pady=10, fill="both", expand=True)

    def say(msg):
        status.config(state="normal")
        status.insert("end", str(msg) + "\n")
        status.see("end")
        status.config(state="disabled")
        root.update_idletasks()

    def run():
        status.config(state="normal")
        status.delete("1.0", "end")
        status.config(state="disabled")
        if not state["file"]:
            messagebox.showinfo("Pick a file first",
                                "Use the button above to choose your sliced G-code.")
            return
        amp_spec = amp_var.get().strip()
        if not amp_spec:
            messagebox.showerror("Amplitude needed",
                                 "Enter an amplitude: mm like -0.2, or % of layer "
                                 "height like -150%, or a multiple like -1.5x.")
            return
        try:
            freq = float(freq_var.get())
        except ValueError:
            messagebox.showerror("Numbers needed",
                                 "Frequency must be a number, like 1.5.")
            return
        out = default_output_path(state["file"])
        try:
            stats = process_gcode(state["file"], out, amp_spec, freq, log=say)
        except ModulationError as e:
            messagebox.showerror("Cannot process this file", str(e))
            return
        except Exception as e:  # never die silently on a beginner's machine
            messagebox.showerror("Something went wrong", f"{type(e).__name__}: {e}")
            return
        if stats["moves"] == 0:
            say("")
            say("Finished, but 0 infill moves were changed. Make sure the file "
                "is fresh sliced G-code and the model has non-zero infill "
                "(remember: thin, all-wall parts have no infill to modulate).")
        else:
            say("")
            say(f"DONE - wavy G-code saved next to your file as:")
            say(f"  {stats['output']}")
            say("Print THAT file (not the original).")

    tk.Button(root, text="MAKE IT WAVY", command=run,
              font=("Segoe UI", 11, "bold")).pack(pady=(0, 12))
    root.mainloop()


if __name__ == "__main__":
    if not run_cli():
        try:
            run_gui()
        except ImportError:
            print("This Python has no tkinter, so no window mode. Run like:")
            print("  python nonplanar_infill_tool.py yourfile.gcode")
            sys.exit(1)
