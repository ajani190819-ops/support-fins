"""Offline tests for the wave-overhang core (no OrcaSlicer needed).

    python3 -m pytest -q plugins/orca-wave/tests/
"""
import math
import pathlib
import sys

import pytest
from shapely.geometry import Polygon

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

import wave_core as wc  # noqa: E402


def _rect(x0, y0, x1, y1):
    return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


# ---- overhang detection -----------------------------------------------------


def test_first_layer_has_no_overhang():
    layer = _rect(0, 0, 20, 20)
    ov = wc.overhang_region(layer, Polygon(), wc.WaveConfig())
    assert ov.is_empty


def test_overhang_is_layer_minus_support():
    # A layer 20 wide sitting on a 10-wide column below: 10mm overhangs.
    layer = _rect(0, 0, 20, 10)
    support = _rect(0, 0, 10, 10)
    ov = wc.overhang_region(layer, support, wc.WaveConfig(overhang_tol=0.0))
    assert ov.area == pytest.approx(100.0, rel=1e-3)   # 10 x 10
    # It is the right-hand strip.
    minx, miny, maxx, maxy = ov.bounds
    assert minx == pytest.approx(10.0, abs=1e-6)
    assert maxx == pytest.approx(20.0, abs=1e-6)


def test_slivers_below_threshold_are_dropped():
    layer = _rect(0, 0, 10.2, 10)
    support = _rect(0, 0, 10, 10)          # only a 0.2mm strip overhangs -> 2mm^2
    ov = wc.overhang_region(layer, support, wc.WaveConfig(min_overhang_area=5.0))
    assert ov.is_empty


# ---- wavefront propagation --------------------------------------------------


def test_waves_fill_the_overhang_and_stay_inside_it():
    layer = _rect(0, 0, 20, 10)
    support = _rect(0, 0, 10, 10)
    cfg = wc.WaveConfig(overhang_tol=0.0, line_spacing=0.5, min_overhang_area=0.0)
    ov = wc.overhang_region(layer, support, cfg)
    tracks = wc.wave_tracks(support, ov, cfg)

    assert len(tracks) > 5, "a 10mm span at 0.5mm spacing should make many fronts"

    # Every wave point must lie within the overhang region (a small tolerance for
    # the offset/rounding on the boundary).
    padded = ov.buffer(1e-6)
    for t in tracks:
        for (x, y) in t.points:
            assert padded.distance(wc._pt((x, y))) < 1e-3

    # Fronts should march outward: distances strictly increase.
    dists = [t.distance for t in tracks]
    assert dists == sorted(dists)
    assert max(dists) <= 10.0 + cfg.line_spacing


def test_waves_reach_the_far_edge():
    layer = _rect(0, 0, 20, 10)
    support = _rect(0, 0, 10, 10)
    cfg = wc.WaveConfig(overhang_tol=0.0, line_spacing=0.5, min_overhang_area=0.0)
    ov = wc.overhang_region(layer, support, cfg)
    tracks = wc.wave_tracks(support, ov, cfg)
    farthest = max(max(x for (x, y) in t.points) for t in tracks)
    # The last front should get close to the far edge at x=20.
    assert farthest > 19.0


def test_waves_diffract_around_a_hole():
    # Overhang with a hole in it -> fronts must route around it, and no wave point
    # may land inside the hole.
    outer = _rect(0, 0, 20, 20)
    support = _rect(0, 0, 4, 20)            # supported strip on the left
    hole = _rect(10, 8, 14, 12)
    layer = outer.difference(hole)
    cfg = wc.WaveConfig(overhang_tol=0.0, line_spacing=0.6, min_overhang_area=0.0)
    ov = wc.overhang_region(layer, support, cfg)
    tracks = wc.wave_tracks(support, ov, cfg)
    assert tracks
    hole_pad = hole.buffer(-1e-6)
    for t in tracks:
        for (x, y) in t.points:
            assert not hole_pad.contains(wc._pt((x, y)))


# ---- ordering / patterns ----------------------------------------------------


def test_smart_starts_each_track_near_support():
    layer = _rect(0, 0, 20, 10)
    support = _rect(0, 0, 10, 10)
    cfg = wc.WaveConfig(overhang_tol=0.0, line_spacing=0.7, pattern="smart",
                        min_overhang_area=0.0)
    ov = wc.overhang_region(layer, support, cfg)
    tracks = wc.wave_tracks(support, ov, cfg)
    polylines = wc.order_tracks(tracks, support, cfg)
    assert len(polylines) == len(tracks)
    for pts in polylines:
        start_d = support.distance(wc._pt(pts[0]))
        end_d = support.distance(wc._pt(pts[-1]))
        assert start_d <= end_d + 1e-9


def test_zigzag_connects_consecutive_fronts():
    layer = _rect(0, 0, 20, 10)
    support = _rect(0, 0, 10, 10)
    cfg = wc.WaveConfig(overhang_tol=0.0, line_spacing=0.7, pattern="zigzag",
                        min_overhang_area=0.0)
    ov = wc.overhang_region(layer, support, cfg)
    tracks = wc.wave_tracks(support, ov, cfg)
    polylines = wc.order_tracks(tracks, support, cfg)
    # Alternate fronts are flipped, so the gap between the end of one polyline and
    # the start of the next should be small on average vs. the raw (unflipped) case.
    def travel_sum(polys):
        return sum(wc._dist(polys[i][-1], polys[i + 1][0])
                   for i in range(len(polys) - 1))
    raw = [t.points for t in sorted(tracks, key=lambda t: t.distance)]
    assert travel_sum(polylines) <= travel_sum(raw) + 1e-9


# ---- g-code emission --------------------------------------------------------


def test_emit_gcode_extrudes_and_sets_fan():
    cfg = wc.WaveConfig(line_width=0.4, layer_height=0.2, flow_ratio=1.0,
                        print_speed=2.0, fan=1.0)
    polylines = [[(0.0, 0.0), (10.0, 0.0)]]     # a 10mm straight wave line
    lines = wc.emit_layer_gcode(polylines, z=0.2, cfg=cfg, restore_fan=76)
    text = "\n".join(lines)
    assert "WAVE OVERHANG BEGIN" in text and "WAVE OVERHANG END" in text
    assert "M106 S255" in text                  # 100% fan forced
    assert "M106 S76" in text                   # restored afterwards
    assert "F120" in text                       # 2 mm/s -> 120 mm/min print feed

    # Extruded amount matches width*height*length / filament area.
    e_vals = [float(tok[1:]) for line in lines if line.startswith("G1 ")
              for tok in line.split() if tok.startswith("E")]
    assert e_vals, "expected at least one extruding move"
    expected_e = 10.0 * cfg.e_per_mm()
    assert e_vals[-1] == pytest.approx(expected_e, rel=1e-3)


def test_plan_layer_end_to_end():
    layer = _rect(0, 0, 20, 10)
    support = _rect(0, 0, 10, 10)
    cfg = wc.WaveConfig(overhang_tol=0.0, line_spacing=0.5, min_overhang_area=0.0)
    res = wc.plan_layer(layer, support, z=0.4, cfg=cfg)
    assert res.z == 0.4
    assert res.overhang_area == pytest.approx(100.0, rel=1e-3)
    assert res.n_tracks > 5
    assert res.polylines


# ---- g-code parsing / calibration / splicing --------------------------------


def test_parse_layer_z_variants():
    assert wc.parse_layer_z(";Z:0.4") == pytest.approx(0.4)
    assert wc.parse_layer_z(";HEIGHT:0.2") == pytest.approx(0.2)
    assert wc.parse_layer_z("G1 Z1.60 F9000") == pytest.approx(1.6)
    assert wc.parse_layer_z("G1 X10 Y10 E1.2") is None   # not a layer move
    assert wc.parse_layer_z("; just a comment") is None


def _fake_gcode(offset):
    """A tiny Orca-style export: a supported base layer (an outline square at the
    bed offset) then a higher layer. The base layer is the calibration reference.
    """
    ox, oy = offset
    L = []
    L.append(";LAYER_CHANGE")
    L.append(";Z:0.2")
    # base outline: object-frame square (0,0)-(30,30) printed at the bed offset
    for (x, y) in [(0, 0), (30, 0), (30, 30), (0, 30), (0, 0)]:
        L.append(f"G1 X{x + ox:.3f} Y{y + oy:.3f} E0.5")
    L.append(";LAYER_CHANGE")
    L.append(";Z:1.2")
    L.append("G1 X%.3f Y%.3f E0.5" % (ox, oy))
    L.append(";LAYER_CHANGE")
    L.append(";Z:2.2")
    return "\n".join(L) + "\n"


def test_layer_extrusion_min_finds_the_outline_corner():
    text = _fake_gcode(offset=(100.0, 50.0))
    lines = text.splitlines(keepends=True)
    mn = wc.layer_extrusion_min(lines, 0.2)
    assert mn[0] == pytest.approx(100.0, abs=1e-6)
    assert mn[1] == pytest.approx(50.0, abs=1e-6)


def test_splice_auto_calibrates_and_places_waves_at_bed_coords():
    # Object-frame wave polyline at (0,0)->(10,0) on layer z=1.2.
    # The object sits at bed offset (100,50); calibration must recover that, so the
    # spliced move must land at (100,50)->(110,50).
    offset = (100.0, 50.0)
    text = _fake_gcode(offset)
    plans = {1.2: [[(0.0, 0.0), (10.0, 0.0)]]}
    cfg = wc.WaveConfig(line_width=0.4, layer_height=0.2, print_speed=2.0, fan=1.0)
    calib = ("auto", 0.2, 0.0, 0.0)   # calibration layer z, object-frame min corner

    new_text, inserted, applied = wc.splice_gcode(text, plans, cfg, calib)
    assert inserted == 1
    assert applied == pytest.approx((100.0, 50.0))
    assert "WAVE OVERHANG BEGIN" in new_text

    # The extruding wave move must be at the calibrated bed coordinates.
    wave_moves = [ln for ln in new_text.splitlines()
                  if ln.startswith("G1 ") and " E" in ln and "X110.000" in ln]
    assert wave_moves, "expected a wave extrusion ending at X110 (100+10)"
    assert "Y50.000" in wave_moves[0]

    # And the wave block is inserted after the z=1.2 layer's own content, i.e.
    # before the z=2.2 marker.
    idx_wave = new_text.index("WAVE OVERHANG BEGIN")
    idx_next = new_text.index(";Z:2.2")
    assert idx_wave < idx_next


def test_splice_manual_offset():
    text = _fake_gcode(offset=(0.0, 0.0))
    plans = {1.2: [[(1.0, 1.0), (2.0, 1.0)]]}
    cfg = wc.WaveConfig()
    new_text, inserted, applied = wc.splice_gcode(
        text, plans, cfg, ("manual", 7.0, 3.0))
    assert inserted == 1
    assert applied == (7.0, 3.0)
    assert "X8.000" in new_text and "Y4.000" in new_text   # (1,1)+(7,3)=(8,4)
