#!/usr/bin/env python3
"""Render what the Wave Overhangs plugin would do to a part, as an SVG.

    python3 demo/ripple_svg.py                       # built-in tabletop demo (skin mode)
    python3 demo/ripple_svg.py --mode ramp           # the terrace variant
    python3 demo/ripple_svg.py tests/models/tshape.stl --pose -90
    python3 demo/ripple_svg.py part.stl --ring-pitch 2 --out out/part.svg

Two views, side by side:

  * PLAN -- one cell per sampled layer: the model's slice (dashed) and what
    the plugin lets that layer print (filled). In skin mode the filled skin
    shows the ripple groove dashes running outward from the supported
    perimeter; in ramp mode the footprint grows one ring per layer.

  * PROFILE -- a cross-section along y = 0 of the whole part: the model's
    outline (red) against the printed solid (blue). Skin mode keeps the
    outline exactly; ramp mode shows the terraced wedge it removes.

Runs the plugin's own geometry core (src/wave_overhangs_orca.py, imported
without Orca), so the picture is the real plan, not a sketch of it.
"""
import argparse
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tests"))          # fake_orca lives with the tests

import fake_orca  # noqa: E402  (installs the fake `orca` module)
_ORCA = fake_orca.install()

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("wave_overhangs_orca",
                                              ROOT / "src" / "wave_overhangs_orca.py")
WO = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(WO)

from shapely.geometry import box as sbox  # noqa: E402
from shapely.geometry import Polygon as SPoly  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

MM = 3.0          # svg units per mm
STROKE = 0.35


# ----------------------------------------------------------------- demo parts

def tabletop(top_w=32.0, stem_w=8.0, stem_h=20.0, top_h=10.0):
    """A square tabletop standing on a square stem: one flat ceiling."""
    stem = sbox(-stem_w / 2, -stem_w / 2, stem_w / 2, stem_w / 2)
    top = sbox(-top_w / 2, -top_w / 2, top_w / 2, top_w / 2)
    return [stem] * round(stem_h / 0.2) + [top] * round(top_h / 0.2)


def ring_shelf():
    """A ceiling with a hole in the middle: ripples grow from the outside
    perimeter and the hole rim at the same time."""
    stem = SPoly([(-5, -5), (5, -5), (5, 5), (-5, 5)],
                 [[(-2, -2), (2, -2), (2, 2), (-2, 2)]])
    top = sbox(-16, -16, 16, 16)
    return [stem] * 80 + [top] * 80


DEMOS = {"tabletop": tabletop, "ring-shelf": ring_shelf}


def stack_from_stl(path, pose_deg, layer_h):
    import trimesh
    import numpy as np
    part = trimesh.load(path)
    tr = np.eye(4) if pose_deg == 0 else trimesh.transformations.rotation_matrix(
        math.radians(pose_deg), [1, 0, 0])
    po = fake_orca.FakePrintObject(part, tr, layer_height=layer_h)
    return [WO._layer_union(L, 1 / fake_orca.SCALE) for L in po.layers()]


# ----------------------------------------------------------------- svg drawing

class Svg:
    def __init__(self):
        self.parts = []

    def line(self, x1, y1, x2, y2, stroke="#888", w=STROKE, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" '
                          f'y2="{y2:.2f}" stroke="{stroke}" stroke-width="{w}"{d}/>')

    def text(self, x, y, s, size=9, fill="#333", anchor="start", mono=True):
        fam = "monospace" if mono else "sans-serif"
        self.parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" '
                          f'font-size="{size}" fill="{fill}" text-anchor="{anchor}">{s}</text>')

    def polygon(self, pts, fill="none", stroke="#888", w=STROKE, dash=None, opacity=1.0):
        p = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(f'<polygon points="{p}" fill="{fill}" fill-opacity="{opacity}" '
                          f'stroke="{stroke}" stroke-width="{w}"{d}/>')

    def rect(self, x, y, w, h, fill, stroke="none", opacity=1.0):
        self.parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
                          f'fill="{fill}" stroke="{stroke}" fill-opacity="{opacity}"/>')

    def group(self, transform):
        self.parts.append(f'<g transform="{transform}">')
        return _GroupCloser(self)

    def save(self, path, w, h):
        body = "\n".join(self.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
            f'viewBox="0 0 {w:.0f} {h:.0f}">\n{body}\n</svg>\n', encoding="utf-8")
        print(f"wrote {path}")


class _GroupCloser:
    def __init__(self, svg):
        self.svg = svg

    def __enter__(self):
        return self.svg

    def __exit__(self, *a):
        self.svg.parts.append("</g>")


def rings_of(geom):
    out = []
    for poly in WO._polygons(geom):
        out.append(list(poly.exterior.coords))
        out.extend(list(r.coords) for r in poly.interiors)
    return out


def draw_plan(svg, U, allowed, cols, z_step, cell, pad):
    """One cell per sampled layer: model slice dashed, printed filled."""
    n = len(U)
    idx = sorted(set(round(i * (n - 1) / max(1, cols - 1)) for i in range(cols)))
    allg = unary_union([u for u in U if not u.is_empty])
    x0, y0, x1, y1 = allg.bounds
    s = min((cell - 2 * pad) / max(x1 - x0, 1e-6), (cell - 2 * pad) / max(y1 - y0, 1e-6))
    for c, j in enumerate(idx):
        col, row = c % cols, c // cols
        g = svg.group(f"translate({col * cell} {row * cell})")
        svg.rect(0, 0, cell, cell, fill="#fafafa", stroke="#ddd")
        inner = svg.group(
            f"translate({pad + (cell - 2 * pad - (x1 - x0) * s) / 2} "
            f"{pad + (cell - 2 * pad - (y1 - y0) * s) / 2}) scale({s:.4f} {-s:.4f}) "
            f"translate({-x0:.4f} {-y1:.4f})")
        for ring in rings_of(U[j]):
            svg.polygon(ring, fill="none", stroke="#c44", w=2 / s, dash=f"{6 / s:.1f},{4 / s:.1f}")
        for ring in rings_of(allowed[j]):
            svg.polygon(ring, fill="#4a7", stroke="#274", w=2 / s, opacity=0.45)
        inner.__exit__()
        svg.text(4, 12, f"layer {j}  z={z_step(j):.1f}", size=8)
        g.__exit__()
    return math.ceil(len(idx) / cols) * cell


def draw_profile(svg, U, allowed, heights, y, w, pad):
    """Cross-section along y ~ 0: model in red, printed solid in blue."""
    strip = sbox(-1e6, y - 0.05, 1e6, y + 0.05)
    z = 0.0
    xs = []
    rects_model, rects_print = [], []
    for u, a, h in zip(U, allowed, heights):
        for poly in WO._polygons(u.intersection(strip)):
            b = poly.bounds
            rects_model.append((b[0], z, b[2] - b[0], h))
            xs += [b[0], b[2]]
        for poly in WO._polygons(a.intersection(strip)):
            b = poly.bounds
            rects_print.append((b[0], z, b[2] - b[0], h))
            xs += [b[0], b[2]]
        z += h
    if not xs:
        return 0
    z1 = z
    x0, x1 = min(xs), max(xs)
    s = min((w - 2 * pad) / max(x1 - x0, 1e-6), 220 / max(z1, 1e-6))
    g = svg.group(f"translate({pad} {pad})")
    svg.text(0, -4, "profile at y = 0  (red: model, blue: printed)", size=10)
    inner = svg.group(f"scale({s:.4f} {-s:.4f}) translate({-x0:.4f} {-z1:.4f})")
    for x, z, w_, h_ in rects_model:
        svg.rect(x, z, max(w_, 0.02), h_, fill="#e99", opacity=0.9)
    for x, z, w_, h_ in rects_print:
        svg.rect(x + 0.01, z + 0.005, max(w_ - 0.02, 0.02), max(h_ - 0.01, 0.02),
                 fill="#4a7", opacity=0.95)
    inner.__exit__()
    g.__exit__()
    return pad * 2 + z1 * s


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stl", nargs="?", help="STL to slice; omit for the built-in demo")
    ap.add_argument("--demo", default="tabletop", choices=sorted(DEMOS))
    ap.add_argument("--pose", type=float, default=-90,
                    help="rotation about X before slicing, degrees (STL only)")
    ap.add_argument("--layer", type=float, default=0.2)
    ap.add_argument("--mode", default="skin", choices=["skin", "ramp"])
    ap.add_argument("--threshold", type=float, default=30.0)
    ap.add_argument("--reach", type=float, default=10.0, help="ramp mode: max ripple reach, mm")
    ap.add_argument("--ramp", type=float, default=45.0, help="ramp mode: ramp angle, degrees")
    ap.add_argument("--ring-pitch", type=float, default=None, help="skin mode: ring spacing, mm")
    ap.add_argument("--groove", type=float, default=None, help="skin mode: groove width, mm")
    ap.add_argument("--cols", type=int, default=8)
    ap.add_argument("--out", default=None, help="output .svg (default out/<name>.svg)")
    args = ap.parse_args()

    cfg = dict(WO._DEFAULTS)
    cfg.update({"mode": args.mode, "threshold_deg": args.threshold,
                "ramp_angle_deg": args.ramp, "max_reach_mm": args.reach})
    if args.ring_pitch is not None:
        cfg["ring_pitch_mm"] = args.ring_pitch
    if args.groove is not None:
        cfg["groove_width_mm"] = args.groove

    if args.stl:
        U = stack_from_stl(args.stl, args.pose, args.layer)
        name = pathlib.Path(args.stl).stem
    else:
        U = DEMOS[args.demo]()
        name = args.demo
    heights = [args.layer] * len(U)
    allowed, stats = WO.plan_ripples(U, heights, cfg)

    out = pathlib.Path(args.out) if args.out else (ROOT / "out" / f"{name}.svg")
    cell, pad, page_pad = 170, 10, 16
    svg = Svg()
    svg.text(page_pad, 18, f"Wave Overhangs ({args.mode} mode) -- {name}  "
             f"(threshold {args.threshold}\u00b0, layer {args.layer} mm"
             + (f", ring pitch {cfg['ring_pitch_mm']} mm" if args.mode == "skin"
                else f", ramp {args.ramp}\u00b0, reach {args.reach} mm") + ")",
             size=12, mono=False)
    if args.mode == "skin":
        svg.text(page_pad, 32, f"rippled {stats['grooved_mm2']:.0f} mm\u00b2 of underside "
                 f"({stats['rings']} ring(s), {stats['dashes']} dash(es)) -- part shape unchanged",
                 size=10, mono=False)
    else:
        svg.text(page_pad, 32, f"rippled {stats['rippled_mm2']:.0f} mm\u00b2 over "
                 f"{stats['ripple_layers']} layer(s), {stats['max_ring']} ring(s) deep; "
                 f"{stats['unreached_mm2']:.0f} mm\u00b2 left for Orca", size=10, mono=False)
    top = 44
    g = svg.group(f"translate({page_pad} {top})")
    plan_h = draw_plan(svg, U, allowed, args.cols,
                       lambda j: (j + 1) * args.layer, cell, pad)
    g.__exit__()
    prof_top = top + plan_h + 30
    g = svg.group(f"translate({page_pad} {prof_top})")
    draw_profile(svg, U, allowed, heights, y=0.0,
                 w=args.cols * cell, pad=pad)
    g.__exit__()
    svg.save(out, args.cols * cell + 2 * page_pad, prof_top + 300)


if __name__ == "__main__":
    main()
