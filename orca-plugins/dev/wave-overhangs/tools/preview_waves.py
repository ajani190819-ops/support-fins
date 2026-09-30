#!/usr/bin/env python3
"""See the wave overhangs OUTSIDE OrcaSlicer.

Orca's on-screen Preview can't show the plugin's wave toolpaths (they're spliced
into the exported G-code after the preview is built). This renders them to an SVG
you can open in any browser, so you can actually look at the arcs.

Two modes:

  # 1) Demo: render the wave algorithm on a sample overhang (with a hole, to show
  #    how the fronts diffract around it).
  python3 plugins/orca-wave/tools/preview_waves.py --demo -o waves.svg

  # 2) Real: read an EXPORTED .gcode and draw the "; WAVE OVERHANG" blocks the
  #    plugin injected, one colour per layer.
  python3 plugins/orca-wave/tools/preview_waves.py --gcode myprint.gcode -o waves.svg

Demo mode needs shapely (same dep the plugin uses). G-code mode is pure stdlib.
"""
import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

_PALETTE = ["#e6194B", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4",
            "#f032e6", "#bfef45", "#fabed4", "#469990", "#dcbeff", "#9A6324"]


def _svg(polyline_groups, filled=None, title="wave overhangs", pad=4.0):
    """polyline_groups: list of (label, [ [(x,y),...], ... ]). filled: list of
    (fill, [(x,y),...]) background polygons. Returns an SVG string (y flipped)."""
    xs, ys = [], []
    for _, polys in polyline_groups:
        for pts in polys:
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
    for _, pts in (filled or []):
        xs += [p[0] for p in pts]
        ys += [p[1] for p in pts]
    if not xs:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='200' height='40'>" \
               "<text x='6' y='24'>no wave paths found</text></svg>"
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w = (maxx - minx) + 2 * pad
    h = (maxy - miny) + 2 * pad
    scale = max(1.0, 900.0 / max(w, h))

    def X(x):
        return (x - minx + pad) * scale

    def Y(y):
        return (maxy - y + pad) * scale     # flip so +Y is up

    out = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{w*scale:.0f}' "
           f"height='{h*scale:.0f}' viewBox='0 0 {w*scale:.0f} {h*scale:.0f}'>",
           f"<rect width='100%' height='100%' fill='#111'/>",
           f"<text x='8' y='20' fill='#ccc' font-family='sans-serif' "
           f"font-size='14'>{title}</text>"]
    for fill, pts in (filled or []):
        d = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts)
        out.append(f"<polygon points='{d}' fill='{fill}' fill-opacity='0.25' "
                   f"stroke='none'/>")
    for i, (label, polys) in enumerate(polyline_groups):
        col = _PALETTE[i % len(_PALETTE)]
        for pts in polys:
            if len(pts) < 2:
                continue
            d = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts)
            out.append(f"<polyline points='{d}' fill='none' stroke='{col}' "
                       f"stroke-width='1.6' stroke-linecap='round' "
                       f"stroke-linejoin='round'/>")
    out.append("</svg>")
    return "\n".join(out)


def demo(outfile):
    import wave_core as wc
    from shapely.geometry import Polygon

    outer = Polygon([(0, 0), (40, 0), (40, 30), (0, 30)])
    support = Polygon([(0, 0), (8, 0), (8, 30), (0, 30)])     # supported strip, left
    hole = Polygon([(20, 12), (26, 12), (26, 18), (20, 18)])
    layer = outer.difference(hole)
    cfg = wc.WaveConfig(overhang_tol=0.0, line_spacing=0.8, min_overhang_area=0.0,
                        pattern="smart")
    ov = wc.overhang_region(layer, support, cfg)
    tracks = wc.wave_tracks(support, ov, cfg)
    polys = wc.order_tracks(tracks, support, cfg)

    filled = [("#4363d8", list(support.exterior.coords)),
              ("#888", list(ov.exterior.coords) if ov.geom_type == "Polygon"
               else list(ov.geoms[0].exterior.coords))]
    svg = _svg([("waves", polys)], filled=filled,
               title=f"demo: {len(tracks)} wave fronts (blue=support, grey=overhang)")
    pathlib.Path(outfile).write_text(svg, encoding="utf-8")
    print(f"wrote {outfile}  ({len(tracks)} fronts across the overhang)")


def from_gcode(path, outfile):
    layers = {}          # z -> list of polylines
    cur_z = None
    in_block = False
    cur_poly = []
    x = y = None
    for raw in pathlib.Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        s = raw.strip()
        if s.startswith((";Z:", ";HEIGHT:", ";LAYER_Z:")):
            try:
                cur_z = float(s.split(":", 1)[1].strip().split()[0])
            except Exception:
                pass
            continue
        if "WAVE OVERHANG BEGIN" in s:
            in_block = True
            cur_poly = []
            continue
        if "WAVE OVERHANG END" in s:
            if len(cur_poly) >= 2:
                layers.setdefault(cur_z, []).append(cur_poly)
            in_block = False
            cur_poly = []
            continue
        if not in_block:
            continue
        if s.startswith("G0"):    # travel = start of a new wave line
            if len(cur_poly) >= 2:
                layers.setdefault(cur_z, []).append(cur_poly)
            cur_poly = []
            nx, ny = _xy(s)
            if nx is not None:
                x, y = nx, ny
                cur_poly = [(x, y)]
        elif s.startswith("G1"):
            nx, ny = _xy(s)
            if nx is not None:
                x, y = nx, ny
                cur_poly.append((x, y))

    if not layers:
        print("No '; WAVE OVERHANG' blocks found in that G-code. Did the plugin run "
              "and splice? Check wave_overhangs_log.jsonl (spliced_layers).")
        return
    groups = [(f"z={z}", polys) for z, polys in sorted(layers.items(),
              key=lambda kv: (kv[0] is None, kv[0]))]
    total = sum(len(p) for _, p in groups)
    svg = _svg(groups, title=f"{path}: {total} wave lines across {len(groups)} layer(s)")
    pathlib.Path(outfile).write_text(svg, encoding="utf-8")
    print(f"wrote {outfile}  ({total} wave lines, {len(groups)} layer(s))")


def _xy(line):
    x = y = None
    for tok in line.split():
        if tok.startswith("X"):
            try:
                x = float(tok[1:])
            except Exception:
                pass
        elif tok.startswith("Y"):
            try:
                y = float(tok[1:])
            except Exception:
                pass
    return x, y


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--demo", action="store_true", help="render a sample overhang")
    g.add_argument("--gcode", metavar="FILE", help="read wave blocks from a .gcode")
    ap.add_argument("-o", "--out", default="waves.svg", help="output SVG (default waves.svg)")
    args = ap.parse_args()
    if args.demo:
        demo(args.out)
    else:
        from_gcode(args.gcode, args.out)


if __name__ == "__main__":
    main()
