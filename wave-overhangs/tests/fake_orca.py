"""A stand-in for OrcaSlicer's embedded `orca` module, for offline tests.

Mirrors only what the Wave Overhangs plugin touches, with the same shapes and
units as the real bindings (scaled int64 slice coords, the PluginResult enum),
and the same API surface the Support Fins plugin proved against a real Orca
nightly. Geometry is shapely-backed, standing in for Clipper.

FakePrintObject slices a trimesh part (an independent slicer) in a
PrusaSlicer-style frame -- centred on the XY footprint, scaled 1e6 per mm,
object bottom at z = 0. `from_layers` builds a print object straight from
per-layer shapely polygons for exact-control tests; the plugin is frame-
agnostic (it only ever diffs consecutive layers), so the frame is not load-
bearing here, only realistic.
"""
import enum
import sys
import types

import numpy as np
import shapely
from shapely.geometry import Polygon as SPoly
from shapely.ops import unary_union

SCALE = 1e6  # scaled units per mm, as in libslic3r (SCALING_FACTOR = 1e-6)


class PluginResult(enum.Enum):
    Success = 0
    Skipped = 1
    RecoverableError = 2
    FatalError = 3


class ExecutionResult:
    def __init__(self, status, message=""):
        self.status, self.message = status, message

    @staticmethod
    def success(message="", data=""):
        return ExecutionResult(PluginResult.Success, message)

    @staticmethod
    def skipped(message=""):
        return ExecutionResult(PluginResult.Skipped, message)

    @staticmethod
    def failure(status, message, data=""):
        if not isinstance(status, PluginResult):  # the real binding raises TypeError too
            raise TypeError(f"failure(): status must be PluginResult, got {type(status).__name__}")
        return ExecutionResult(status, message)

    def __repr__(self):
        return f"{self.status.name}: {self.message}"


class SurfaceType(enum.Enum):
    stTop = 0
    stBottom = 1
    stInternal = 4
    stInternalSolid = 5


class FakePolygon:
    def __init__(self, arr):
        self._a = np.asarray(arr, dtype=np.int64)

    def as_array(self):
        return self._a


def _ring(x):
    if isinstance(x, FakePolygon):
        return x.as_array()
    a = np.asarray(x)
    if a.dtype != np.int64 or a.ndim != 2 or a.shape[1] != 2 or len(a) < 3:
        raise ValueError("polygon coordinates must be an (N,2) int64 ndarray (scaled coords)")
    return a


class ExPolygon:
    def __init__(self, contour, holes=None):
        self._p = SPoly(_ring(contour), [_ring(h) for h in (holes or [])])
        if not self._p.is_valid:
            self._p = shapely.make_valid(self._p)
            if isinstance(self._p, shapely.geometry.MultiPolygon):
                self._p = max(self._p.geoms, key=lambda g: g.area)

    @property
    def contour(self):
        return FakePolygon(np.asarray(self._p.exterior.coords[:-1], dtype=np.int64))

    @property
    def holes(self):
        return [FakePolygon(np.asarray(r.coords[:-1], dtype=np.int64)) for r in self._p.interiors]

    def area(self):
        return self._p.area


class Surface:
    def __init__(self, surface_type, expolygon):
        self.surface_type, self.expolygon = surface_type, expolygon


class SurfaceCollection:
    def __init__(self):
        self.surfaces = []

    def set(self, expolygons, surface_type):
        self.surfaces = [Surface(surface_type, e) for e in expolygons]

    def append(self, expolygons, surface_type):
        self.surfaces += [Surface(surface_type, e) for e in expolygons]


class LayerRegion:
    def __init__(self):
        self.slices = SurfaceCollection()


class Layer:
    def __init__(self, slice_z, print_z, height):
        self.slice_z, self.print_z, self.height = slice_z, print_z, height
        self._regions = [LayerRegion()]
        self.lslices_geom = None

    def regions(self):
        return self._regions

    def make_slices(self):
        polys = [s.expolygon._p for r in self._regions for s in r.slices.surfaces]
        self.lslices_geom = unary_union(polys) if polys else SPoly()

    def islands(self):
        """Union of everything printed on this layer, in SCALED coords
        (islands().area / SCALE**2 is mm^2)."""
        if self.lslices_geom is None:
            self.make_slices()
        return self.lslices_geom


class _Mesh:
    def __init__(self, V, T):
        self._V, self._T = V.astype(np.float32), T.astype(np.int32)

    def vertices(self):
        return self._V

    def triangles(self):
        return self._T


class FakeVolume:
    def __init__(self, V, T, matrix=None, part=True):
        self._mesh, self._m, self._part = _Mesh(V, T), (np.eye(4) if matrix is None else matrix), part

    def mesh(self):
        return self._mesh

    def matrix(self):
        return np.array(self._m, dtype=np.float64)

    def is_model_part(self):
        return self._part


class FakeModelObject:
    def __init__(self, volumes, oid=1):
        self._v, self._id = volumes, oid

    def volumes(self):
        return self._v

    def id(self):
        return self._id


class FakePrintObject:
    """Print object whose layers come either from slicing a trimesh part through
    `trafo` (like Orca) or directly from per-layer shapely polygons (mm)."""

    def __init__(self, trimesh_part=None, trafo=None, layer_height=0.2, config=None,
                 polys_per_layer=None):
        import trimesh
        self._cfg = {"enable_support": "0", "layer_height": str(layer_height)}
        self._cfg.update(config or {})
        self._mo = None
        self._layers = []
        h = float(layer_height)
        if polys_per_layer is not None:
            self._mo = FakeModelObject([FakeVolume(np.zeros((0, 3)), np.zeros((0, 3), dtype=int))])
            for j, polys in enumerate(polys_per_layer):
                z0, z1 = j * h, (j + 1) * h
                L = Layer(slice_z=(z0 + z1) / 2, print_z=z1, height=h)
                for p in ([polys] if isinstance(polys, SPoly) else polys):
                    L.regions()[0].slices.surfaces.append(
                        Surface(SurfaceType.stInternal, ExPolygon(*self._scaled(p))))
                L.make_slices()
                self._layers.append(L)
            return
        tr = np.eye(4) if trafo is None else np.asarray(trafo, dtype=np.float64)
        posed = trimesh_part.copy()
        posed.apply_transform(tr)
        lo, hi = posed.bounds
        posed.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
        self._mo = FakeModelObject([FakeVolume(np.asarray(posed.vertices),
                                               np.asarray(posed.faces))])
        z = 0.0
        while z + h <= posed.bounds[1][2] + 1e-9:
            L = Layer(slice_z=z + h / 2, print_z=z + h, height=h)
            sec = posed.section(plane_origin=[0, 0, z + h / 2], plane_normal=[0, 0, 1])
            if sec is not None:
                planar, to3d = sec.to_2D()
                for poly in planar.polygons_full:
                    ext = self._to_xy(np.asarray(poly.exterior.coords), to3d)
                    holes = [self._to_xy(np.asarray(r.coords), to3d) for r in poly.interiors]
                    L.regions()[0].slices.surfaces.append(Surface(
                        SurfaceType.stInternal,
                        ExPolygon(self._sc(ext), [self._sc(hh) for hh in holes])))
            L.make_slices()
            self._layers.append(L)
            z += h

    @staticmethod
    def _to_xy(pts2d, to3d):
        P = np.c_[pts2d[:, :2], np.zeros(len(pts2d)), np.ones(len(pts2d))] @ to3d.T
        return P[:-1, :2]

    @staticmethod
    def _sc(xy):
        return np.rint(xy * SCALE).astype(np.int64)

    def _scaled(self, poly):
        ext = self._sc(np.asarray(poly.exterior.coords)[:-1])
        holes = [self._sc(np.asarray(r.coords)[:-1]) for r in poly.interiors]
        return ext, holes

    def model_object(self):
        return self._mo

    def trafo(self):
        return np.eye(4)

    def bounding_box(self):
        raise NotImplementedError

    def layers(self):
        return self._layers

    def config_value(self, key):
        return self._cfg.get(key)


class _CapBase:
    _config = "{}"

    def get_config(self):
        return self._config


class Step(enum.Enum):
    posSlice = 0
    posPerimeters = 1


class Ctx:
    def __init__(self, obj, step=Step.posSlice):
        self.object, self.step = obj, step

    def config_value(self, key):
        return self.object.config_value(key) if self.object else None

    def cancelled(self):
        return False


def install():
    """Register this fake as `orca` in sys.modules and return it."""
    m = types.ModuleType("orca")
    m.PluginResult = PluginResult
    m.ExecutionResult = ExecutionResult
    m.host = types.SimpleNamespace(ExPolygon=ExPolygon, SurfaceType=SurfaceType)
    m.slicing = types.SimpleNamespace(SlicingPipelineCapabilityBase=_CapBase, Step=Step,
                                      unscale=lambda v: v / SCALE)
    m.script = types.SimpleNamespace(ScriptPluginCapabilityBase=_CapBase)
    m.base = object
    m.plugin = lambda cls: cls
    m.registered = []
    m.register_capability = m.registered.append
    sys.modules["orca"] = m
    return m
