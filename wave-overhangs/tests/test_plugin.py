"""Offline tests for the Wave Overhangs Orca plugin.

    python -m pytest -q tests/

Two layers of testing, like the Support Fins plugin's suite:

* the geometry core (`plan_ripples`) is tested directly on synthetic polygon
  stacks -- exact control over every layer, no Orca anywhere;
* the plugin file is run end to end against `fake_orca`, a stand-in for
  Orca's embedded module that slices real STLs with trimesh.
"""
import importlib.util
import json
import math
import pathlib
import sys
import types

import numpy as np
import pytest
import trimesh
from shapely.geometry import box as sbox
from shapely.geometry import Polygon as SPoly
from shapely.ops import unary_union

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fake_orca  # noqa: E402

ORCA = fake_orca.install()

SRC = HERE.parent / "src" / "wave_overhangs_orca.py"
MODELS = HERE / "models"


def load_plugin():
    spec = importlib.util.spec_from_file_location("wave_overhangs_orca", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


WO = load_plugin()

TOL_ENV = 0.1  # mm of envelope slack for offset-vs-offset comparisons (buffer
               # discretisation: shapely's 8-segment arcs sit ~0.008*r inside
               # the true offset, and composed buffers re-discretise)


def cfg(**over):
    c = dict(WO._DEFAULTS)
    c.update(over)
    return c


def sq(x0, y0, x1, y1):
    return sbox(x0, y0, x1, y1)


def stack(polys, h=0.2):
    """A layer stack + heights: `polys[j]` is layer j's slice (shapely, mm)."""
    return list(polys), [h] * len(polys)


def envelope(geom):
    return geom


def assert_within(inner, outer, tol, msg=""):
    """inner <= outer grown by tol (envelope test that survives buffer noise)."""
    assert inner.difference(outer.buffer(tol)).area < 1e-9, msg


def assert_covers(outer, inner, tol, msg=""):
    """outer >= inner shrunk by tol."""
    assert outer.difference(inner.buffer(tol)).area < 1e-9, msg


# --------------------------------------------------------------------------
# geometry core: the tabletop (flat ceiling standing on a stem)
# --------------------------------------------------------------------------

def tabletop_stack(top_w=16.0, stem_w=8.0, stem_h=20.0, top_h=8.0, h=0.2):
    stem = sq(-stem_w / 2, -stem_w / 2, stem_w / 2, stem_w / 2)
    top = sq(-top_w / 2, -top_w / 2, top_w / 2, top_w / 2)
    n_stem, n_top = round(stem_h / h), round(top_h / h)
    return stack([stem] * n_stem + [top] * n_top, h), stem, top


def test_seed_band_and_ring_per_layer_conform_to_the_perimeter():
    """The heart of the feature: at the ceiling layer only a seed band prints,
    and each following layer's footprint is the next offset contour of the
    supported perimeter -- ripples going outward, following its shape."""
    (U, heights), stem, top = tabletop_stack()   # 16mm top over 8mm stem,
    allowed, stats = WO.plan_ripples(U, heights, cfg())  # 4mm overhang, 8mm headroom
    step = 0.2  # ramp 45 deg, layer 0.2 -> one 0.2mm ring per layer

    first = 100  # first layer of the tabletop
    assert WO._area(allowed[first - 1].difference(stem)) < 1e-9  # untouched below
    # seed ring: layer `first` prints exactly the step-wide offset of the stem
    assert_within(allowed[first], stem.buffer(step), TOL_ENV, "seed ring too wide")
    assert_covers(allowed[first], stem.buffer(step), TOL_ENV, "seed ring too thin")
    # ring k prints on layer first+k-1 and not before: the footprint there is
    # exactly the k*step offset of the stem perimeter (square, rounded corners)
    for k in (2, 5, 10, 15, 19):
        want = stem.buffer(k * step)
        got = allowed[first + k - 1]
        assert_within(got, want, TOL_ENV, f"ring {k} runs ahead of the perimeter")
        assert_covers(got, want, TOL_ENV, f"ring {k} lags the perimeter")
    # the overhang is fully re-covered once the wave has crossed it (4mm / 0.2)
    assert WO._area(allowed[first + 21].difference(top)) < 1e-9


def test_no_material_is_added_or_lost_tabletop():
    (U, heights), stem, top = tabletop_stack()
    allowed, stats = WO.plan_ripples(U, heights, cfg())
    before, after = WO._union(U), WO._union(allowed)
    assert WO._area(after.difference(before)) < 1e-9
    assert WO._area(before.difference(after)) < 1e-9
    # printed geometry is always inside that layer's model slice
    for a, u in zip(allowed, U):
        assert WO._area(a.difference(u)) < 1e-9


def test_first_layer_prints_fully():
    (U, heights), stem, top = tabletop_stack()
    allowed, _ = WO.plan_ripples(U, heights, cfg())
    assert allowed[0].equals(U[0])


def test_thin_roof_stops_the_wave_and_leaves_the_rest_to_orca():
    """A ceiling with only 4mm of solid above can only be rippled 4mm out at a
    45-degree ramp; whatever the wave cannot cover in time prints on the
    ceiling's own layer, like stock Orca."""
    (U, heights), stem, top = tabletop_stack(top_w=40.0, stem_w=8.0,
                                             stem_h=20.0, top_h=4.0)
    allowed, stats = WO.plan_ripples(U, heights, cfg())
    first = 100
    deadline_zone = stem.buffer(4.0 + TOL_ENV)         # 4mm headroom / 0.2 ramp
    # the stock part (beyond what the ramp can reach) prints at the ceiling layer
    stock = top.difference(deadline_zone)
    assert WO._area(allowed[first].intersection(stock)) > WO._area(stock) - 0.05
    # ...and everything the wave did defer stayed inside the reachable zone
    deferred = WO._union([u.difference(a) for a, u in zip(allowed, U)])
    assert WO._area(deferred.difference(deadline_zone)) < 1e-6
    # no material lost
    assert WO._area(WO._union(U).difference(WO._union(allowed))) < 1e-9


def test_max_reach_caps_the_wave():
    (U, heights), stem, top = tabletop_stack(top_w=28.0, stem_w=4.0,
                                             stem_h=20.0, top_h=8.0)
    allowed, stats = WO.plan_ripples(U, heights, cfg(max_reach_mm=3.0))
    zone = stem.buffer(3.0 + TOL_ENV)
    deferred = WO._union([u.difference(a) for a, u in zip(allowed, U)])
    assert WO._area(deferred.difference(zone)) < 1e-6
    stock = top.difference(zone)
    assert WO._area(allowed[100].intersection(stock)) > WO._area(stock) - 0.05


def test_two_ceilings_both_ripple():
    stem = sq(-4, -4, 4, 4)
    mid = sq(-12, -12, 12, 12)
    thin = sq(-6, -6, 6, 6)
    wide = sq(-14, -14, 14, 14)
    U = [stem] * 50 + [mid] * 30 + [thin] * 30 + [wide] * 30
    heights = [0.2] * len(U)
    allowed, stats = WO.plan_ripples(U, heights, cfg())
    assert stats["ripple_layers"] >= 2
    before, after = WO._union(U), WO._union(allowed)
    assert WO._area(before.difference(after)) < 1e-9
    assert WO._area(after.difference(before)) < 1e-9


# --------------------------------------------------------------------------
# geometry core: what must NOT ripple
# --------------------------------------------------------------------------

def test_steep_slope_is_untouched():
    """A 45-degree slope grows 0.2mm per 0.2mm layer -- under the 30-degree
    threshold's width, so every layer prints as sliced."""
    growth = 0.2  # mm per layer at 45 degrees
    w = 10.0
    U = [sq(-w / 2, -w / 2, w / 2 + j * growth, w / 2) for j in range(60)]
    allowed, stats = WO.plan_ripples(U, [0.2] * len(U), cfg())
    for a, u in zip(allowed, U):
        assert a is u or a.equals(u), "a 45-degree slope was modified"
    assert stats["rippled_mm2"] == 0.0


def test_shallow_slope_ripples_only_below_the_threshold():
    slope_deg = 20.0
    growth = 0.2 / math.tan(math.radians(slope_deg))   # 0.55 mm/layer
    U = [sq(-5, -5, 5 + j * growth, 5) for j in range(60)]
    allowed, _ = WO.plan_ripples(U, [0.2] * len(U), cfg())                 # threshold 30
    assert any(not a.equals(u) for a, u in zip(allowed, U)), "20-degree slope did not ripple"
    allowed, stats = WO.plan_ripples(U, [0.2] * len(U), cfg(threshold_deg=10.0))
    for a, u in zip(allowed, U):
        assert a is u or a.equals(u), "a 20-degree slope was modified below the threshold"
    assert stats["rippled_mm2"] == 0.0


def test_narrow_strip_prints_at_once_like_stock():
    """A 0.3mm lip (under the threshold width) is Orca's problem, not ours."""
    U = [sq(0, 0, 10, 10)] * 20 + [sq(0, 0, 10, 10.3)] * 20
    allowed, stats = WO.plan_ripples(U, [0.2] * len(U), cfg())
    for a, u in zip(allowed, U):
        assert a is u or a.equals(u)
    assert stats["rippled_mm2"] == 0.0


def test_shrinking_footprint_is_never_touched():
    """Domes, stair treads, top surfaces: the slice only shrinks, so nothing
    is ever deferred there."""
    import math as _m
    U = []
    for j in range(50):
        r = 10.0 - 0.15 * j
        U.append(SPoly([(r * _m.cos(_m.radians(t)), r * _m.sin(_m.radians(t)))
                        for t in range(0, 360, 6)]))
    allowed, stats = WO.plan_ripples(U, [0.2] * len(U), cfg())
    for a, u in zip(allowed, U):
        assert a is u or a.equals(u)
    assert stats["rippled_mm2"] == 0.0


def test_tiny_overhang_patches_are_ignored():
    U = [sq(0, 0, 10, 10)] * 20 + \
        [sq(0, 0, 10, 10).union(sq(10.05, 4.9, 10.75, 5.1))] * 20   # 0.2 mm^2 lip
    allowed, stats = WO.plan_ripples(U, [0.2] * len(U), cfg())     # min_area 1 mm^2
    for a, u in zip(allowed, U):
        assert a is u or a.equals(u)
    assert stats["rippled_mm2"] == 0.0


def test_hole_in_the_ceiling_ripples_from_the_hole_too():
    """The wave seeds off every supported perimeter, including hole rims: rings
    grow inward from the rim on the same one-per-layer clock as the outer wave."""
    from shapely.geometry import Point
    stem = SPoly([(-4, -4), (4, -4), (4, 4), (-4, 4)],
                 [[(-1.5, -1.5), (1.5, -1.5), (1.5, 1.5), (-1.5, 1.5)]])
    top = sq(-10, -10, 10, 10)   # corners 8.5 mm from the stem: inside the reach
    U = [stem] * 50 + [top] * 60
    allowed, stats = WO.plan_ripples(U, [0.2] * len(U), cfg())
    assert stats["rippled_mm2"] > 0.0
    assert stats["unreached_mm2"] < 1e-6, "nothing should be beyond the reach here"
    # after k rings (layer 50+k-1) material within k*0.2 of any supported
    # perimeter has printed: outside the stem, and INTO the hole
    got = allowed[50 + 5 - 1]
    assert got.contains(Point(0, 4.9))          # 0.9 out from the stem edge: ring 5
    assert not got.contains(Point(0, 5.3))      # 1.3 out: ring 7, not yet
    assert got.contains(Point(0, 1.2))          # 0.3 into the hole: ring 2, done
    assert not got.contains(Point(0, 0.3))      # 1.2 into the hole: still open
    # the hole is still open at ring 5 (it closes after ~8 rings) and the
    # printed area is one connected island with that hole
    parts = WO._polygons(got)
    assert len(parts) == 1 and len(parts[0].interiors) == 1
    # fully closed soon after: the hole is gone by ring 9
    assert len(WO._polygons(allowed[50 + 9 - 1])[0].interiors) == 0
    before, after = WO._union(U), WO._union(allowed)
    assert WO._area(before.difference(after)) < 1e-9
    assert WO._area(after.difference(before)) < 1e-9


# --------------------------------------------------------------------------
# geometry core: no material lost, on everything we can throw at it
# --------------------------------------------------------------------------

SYNTHETIC = {
    "tabletop": tabletop_stack()[0],
    "wide_thin_roof": tabletop_stack(top_w=40.0, stem_w=8.0, stem_h=20.0, top_h=4.0)[0],
    "l_ceiling": stack([sq(-6, -6, 6, 6)] * 50 +
                       [sq(-6, -6, 20, 20)] * 40),           # L-shaped perimeter
    "mushroom": stack([sq(-2, -2, 2, 2)] +
                      [sq(-2 - 0.5 * j, -2 - 0.5 * j, 2 + 0.5 * j, 2 + 0.5 * j)
                       for j in range(40)] +
                      [sq(-22, -22, 22, 22)] * 10),          # widening every layer
    "floating_island": stack([sq(-10, -10, 10, 10)] * 30 +
                             [sq(-2, -2, 2, 2)] * 10 +        # pinch: layers vanish
                             [sq(-9, -9, 9, 9)] * 20),        # then reappear
}


@pytest.mark.parametrize("name", sorted(SYNTHETIC))
def test_no_material_lost_synthetic(name):
    U, heights = SYNTHETIC[name]
    allowed, stats = WO.plan_ripples(U, heights, cfg())
    before, after = WO._union(U), WO._union(allowed)
    assert WO._area(before.difference(after)) < 1e-9, "material lost"
    assert WO._area(after.difference(before)) < 1e-9, "material added"
    for j, (a, u) in enumerate(zip(allowed, U)):
        assert WO._area(a.difference(u)) < 1e-9, f"layer {j} prints outside the model"
    assert allowed[0].equals(U[0])


@pytest.mark.parametrize("name", sorted(p.stem for p in MODELS.glob("*.stl")))
def test_no_material_lost_on_stress_models(name):
    """The stress models from the Support Fins prototype, in poses that put
    their flat features in the air: whatever happens, nothing is lost."""
    m = trimesh.load(MODELS / f"{name}.stl")
    for deg in (0, 90, -90):
        tr = trimesh.transformations.rotation_matrix(math.radians(deg), [1, 0, 0])
        po = fake_orca.FakePrintObject(m, tr)
        U = [WO._layer_union(L, 1 / fake_orca.SCALE) for L in po.layers()]
        if all(u.is_empty for u in U):
            continue
        allowed, stats = WO.plan_ripples(U, WO._slice_heights(po.layers()), cfg())
        before, after = WO._union(U), WO._union(allowed)
        assert WO._area(before.difference(after)) < 1e-6, f"{name}@{deg}: material lost"
        assert WO._area(after.difference(before)) < 1e-6, f"{name}@{deg}: material added"
        for j, (a, u) in enumerate(zip(allowed, U)):
            assert WO._area(a.difference(u)) < 1e-9, f"{name}@{deg}: layer {j} outside the model"


# --------------------------------------------------------------------------
# end to end through the fake Orca
# --------------------------------------------------------------------------

def rot_x(deg):
    return trimesh.transformations.rotation_matrix(math.radians(deg), [1, 0, 0])


def islands_mm(layer):
    return layer.islands().area / fake_orca.SCALE ** 2


def layer_polys(po):
    """Per-layer printed geometry, as shapely (mm)."""
    return [L.islands().buffer(0) for L in po.layers()]


def union_mm(po):
    """The part as it actually prints: the union of every layer's islands.

    The SUM of layer areas is deliberately NOT conserved by ripples -- a
    ripple is material printed one layer later than the model asked for, so
    the terraced wedge under the ramp is removed from the part. The union
    (the set of points that print at all) is what must not lose anything."""
    from shapely.ops import unary_union as _uu
    g = _uu([L.islands() for L in po.layers() if not L.islands().is_empty])
    return g.area / fake_orca.SCALE ** 2


# (model, rotation) poses with a genuine flat overhang: the wave must engage.
# tshape stood on its stem puts the crossbar's underside 16 mm in the air;
# ushape rotated puts the U's base over the legs' gap; portal rotated turns
# its window into a tunnel with a roof; lowledge is a plate on a small foot.
RIPPLING = [
    ("tshape", -90),
    ("ushape", -90),
    ("portal", 90),
    ("lowledge", 0),
]

# Poses with nothing flatter than the 30-degree threshold: must print untouched.
UNTOUCHED = [
    ("cube", 0),
    ("wedge", 0),      # 45-degree slope
    ("cone", 0),       # 67-degree walls from horizontal
    ("staircase", 0),  # treads shrink going up: top surfaces, never overhangs
    ("arch", 90),      # the arch curve never gets flatter than ~53 degrees
]


@pytest.mark.parametrize("name,deg", RIPPLING)
def test_flat_overhang_models_get_rippled(name, deg):
    part = trimesh.load(MODELS / f"{name}.stl")
    po = fake_orca.FakePrintObject(part, rot_x(deg))
    plain = fake_orca.FakePrintObject(part, rot_x(deg))
    res = WO.WaveOverhangsSlicing().execute(fake_orca.Ctx(po))
    assert res.status is fake_orca.PluginResult.Success, res
    assert "rippled" in res.message
    changed = [islands_mm(L) != islands_mm(P) for L, P in zip(po.layers(), plain.layers())]
    assert any(changed), "no layer changed"
    assert not changed[0], "the first layer was touched"
    # the part prints whole: nothing lost, nothing invented
    assert abs(union_mm(po) - union_mm(plain)) < 0.05, "the printed part changed footprint"
    for L, P in zip(po.layers(), plain.layers()):
        assert L.islands().difference(P.islands()).area < 1e-6 * fake_orca.SCALE ** 2, \
            "a layer prints outside the model's slice"
    j = changed.index(True)
    assert islands_mm(po.layers()[j]) < islands_mm(plain.layers()[j]), \
        "the overhang layer should print less than the model asked for"
    if j + 3 < len(changed):
        assert islands_mm(po.layers()[j + 3]) > islands_mm(po.layers()[j]), \
            "the footprint must grow again above the overhang layer"


@pytest.mark.parametrize("name,deg", UNTOUCHED)
def test_steep_models_print_exactly_as_sliced(name, deg):
    part = trimesh.load(MODELS / f"{name}.stl")
    po = fake_orca.FakePrintObject(part, rot_x(deg))
    plain = fake_orca.FakePrintObject(part, rot_x(deg))
    res = WO.WaveOverhangsSlicing().execute(fake_orca.Ctx(po))
    assert res.status is fake_orca.PluginResult.Success, res
    assert "no flat overhangs" in res.message
    assert [islands_mm(L) for L in po.layers()] == [islands_mm(P) for L, P in zip(po.layers(), plain.layers())]



def test_part_with_orca_supports_on_can_be_skipped():
    part = trimesh.load(MODELS / "tshape.stl")
    po = fake_orca.FakePrintObject(part, rot_x(-90), config={"enable_support": "1"})
    before = [islands_mm(L) for L in po.layers()]
    cap = WO.WaveOverhangsSlicing()
    cap._config = json.dumps({"apply_to": "no-supports"})
    res = cap.execute(fake_orca.Ctx(po))
    assert "skipped" in res.message
    assert [islands_mm(L) for L in po.layers()] == before


def test_other_steps_and_disabled_config_do_nothing():
    part = trimesh.load(MODELS / "tshape.stl")
    po = fake_orca.FakePrintObject(part, rot_x(-90))
    before = [islands_mm(L) for L in po.layers()]
    cap = WO.WaveOverhangsSlicing()
    cap.execute(fake_orca.Ctx(po, step=fake_orca.Step.posPerimeters))
    cap._config = json.dumps({"enabled": False})
    assert "disabled" in cap.execute(fake_orca.Ctx(po)).message
    assert [islands_mm(L) for L in po.layers()] == before


def test_plugin_config_threshold_is_honoured():
    """A 1mm-wide overhang ripples at the default 30-degree threshold but is
    left to Orca when the threshold drops to 5 degrees."""
    def part():
        return fake_orca.FakePrintObject(polys_per_layer=[sq(0, 0, 10, 10)] * 20 +
                                                        [sq(0, 0, 10, 11)] * 20)
    po = part()
    res = WO.WaveOverhangsSlicing().execute(fake_orca.Ctx(po))
    assert "rippled" in res.message
    po2 = part()
    cap = WO.WaveOverhangsSlicing()
    cap._config = json.dumps({"threshold_deg": 5})
    res2 = cap.execute(fake_orca.Ctx(po2))
    assert "no flat overhangs" in res2.message
    assert [islands_mm(L) for L in po2.layers()] == [islands_mm(L) for L in part().layers()]


def test_errors_are_reported_not_raised(monkeypatch):
    part = trimesh.load(MODELS / "tshape.stl")
    po = fake_orca.FakePrintObject(part, rot_x(-90))
    monkeypatch.setattr(WO, "plan_ripples",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    res = WO.WaveOverhangsSlicing().execute(fake_orca.Ctx(po))
    assert res.status is fake_orca.PluginResult.RecoverableError and "boom" in res.message


def test_broken_plan_fails_conservation_before_touching_layers(monkeypatch):
    part = trimesh.load(MODELS / "tshape.stl")
    po = fake_orca.FakePrintObject(part, rot_x(-90))
    before = [islands_mm(L) for L in po.layers()]
    real = WO.plan_ripples

    def lying_planner(U, heights, cfg):
        allowed, stats = real(U, heights, cfg)
        allowed[5] = allowed[5].union(sq(-40, -40, 40, 40))   # invent material
        return allowed, stats

    monkeypatch.setattr(WO, "plan_ripples", lying_planner)
    res = WO.WaveOverhangsSlicing().execute(fake_orca.Ctx(po))
    assert res.status is fake_orca.PluginResult.RecoverableError
    assert "conservation" in res.message
    assert [islands_mm(L) for L in po.layers()] == before


def test_plugin_registers_its_capabilities():
    ORCA.registered.clear()
    WO.WaveOverhangsPlugin().register_capabilities()
    assert ORCA.registered == [WO.WaveOverhangsSlicing, WO.WaveOverhangsCheck]


def test_setup_check_script_self_tests_the_core(monkeypatch):
    part = trimesh.creation.box((10, 20, 30))
    obj = fake_orca.FakeModelObject([
        fake_orca.FakeVolume(np.asarray(part.vertices), np.asarray(part.faces))])
    monkeypatch.setattr(ORCA.host, "model",
                        lambda: types.SimpleNamespace(objects=lambda: [obj]),
                        raising=False)
    res = WO.WaveOverhangsCheck().execute()
    assert res.status is fake_orca.PluginResult.Success, res
    assert "geometry core: ok" in res.message
    assert "1 object(s)" in res.message
