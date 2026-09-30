"""A stand-in for OrcaSlicer's embedded `orca` module, for offline tests.

Mirrors what the Wave Overhangs plugin touches, matching the real bindings
documented at
https://www.orcaslicer.com/wiki/developer_reference/plugin_development/api_reference/slicing

Two details this fake deliberately gets *right*, because the plugin got them
wrong and nothing caught it:

1. `Step` carries the full published enum, including `psGCodePostProcess`.
   At that step `ctx.print` / `ctx.object` are None and `ctx.gcode_path` is
   the working file -- so a test can drive the export seam exactly as Orca
   would.

2. `SurfaceCollection.set()` / `.append()` take a *sequence* of ExPolygons,
   like the pybind11 `ExPolygons` vector binding. Passing a bare ExPolygon
   raises, instead of silently doing something reasonable.

Slice coordinates are scaled int64 (SCALING_FACTOR = 1e-6), as in libslic3r.
"""
import enum
import types

import numpy as np
import shapely
from shapely.geometry import Polygon as SPoly, MultiPolygon

SCALE = 1e6  # scaled units per mm


class PluginResult(enum.Enum):
    Success = 0
    Skipped = 1
    RecoverableError = 2
    FatalError = 3


class ExecutionResult:
    def __init__(self, status, message="", data=""):
        self.status, self.message, self.data = status, message, data

    @staticmethod
    def success(message="", data=""):
        return ExecutionResult(PluginResult.Success, message, data)

    @staticmethod
    def skipped(message=""):
        return ExecutionResult(PluginResult.Skipped, message)

    @staticmethod
    def failure(status, message, data=""):
        if not isinstance(status, PluginResult):  # the real binding raises TypeError too
            raise TypeError(
                f"failure(): status must be PluginResult, got {type(status).__name__}")
        return ExecutionResult(status, message, data)

    def __repr__(self):
        return f"{self.status.name}: {self.message}"


class SurfaceType(enum.Enum):
    stTop = 0
    stBottom = 1
    stInternal = 4
    stInternalSolid = 5


class Polygon:
    def __init__(self, arr):
        a = np.asarray(arr, dtype=np.int64)
        if a.ndim != 2 or a.shape[1] != 2 or len(a) < 3:
            raise ValueError("polygon must be (N,2) scaled int coords with N >= 3")
        self._a = a

    def as_array(self):
        return self._a


def _ring(x):
    if isinstance(x, Polygon):
        return x.as_array()
    return Polygon(x).as_array()


class ExPolygon:
    def __init__(self, contour, holes=None):
        self._p = SPoly(_ring(contour), [_ring(h) for h in (holes or [])])
        if not self._p.is_valid:
            self._p = shapely.make_valid(self._p)
            if isinstance(self._p, MultiPolygon):
                self._p = max(self._p.geoms, key=lambda g: g.area)

    @classmethod
    def _from(cls, sp):
        e = cls.__new__(cls)
        e._p = sp
        return e

    @property
    def contour(self):
        return Polygon(np.asarray(self._p.exterior.coords[:-1], dtype=np.int64))

    @property
    def holes(self):
        return [Polygon(np.asarray(r.coords[:-1], dtype=np.int64))
                for r in self._p.interiors]

    def area(self):
        return self._p.area


class Surface:
    def __init__(self, surface_type, expolygon):
        self.surface_type, self.expolygon = surface_type, expolygon


class SurfaceCollection:
    """Matches the `ExPolygons` (vector) binding: these take a *sequence*."""

    def __init__(self):
        self.surfaces = []

    @staticmethod
    def _check(expolygons, who):
        if isinstance(expolygons, ExPolygon):
            raise TypeError(
                f"SurfaceCollection.{who}(): expected a sequence of ExPolygon, "
                f"got a bare ExPolygon (wrap it in a list)")
        seq = list(expolygons)
        for e in seq:
            if not isinstance(e, ExPolygon):
                raise TypeError(
                    f"SurfaceCollection.{who}(): expected ExPolygon, "
                    f"got {type(e).__name__}")
        return seq

    def set(self, expolygons, surface_type):
        self.surfaces = [Surface(surface_type, e)
                         for e in self._check(expolygons, "set")]

    def append(self, expolygons, surface_type):
        self.surfaces += [Surface(surface_type, e)
                          for e in self._check(expolygons, "append")]


class LayerRegion:
    def __init__(self):
        self.slices = SurfaceCollection()


class Layer:
    def __init__(self, slice_z, polygons):
        self.slice_z = slice_z
        self._regions = [LayerRegion()]
        self._regions[0].slices.set(
            [ExPolygon(np.asarray(p.exterior.coords[:-1]) * SCALE)
             for p in polygons], SurfaceType.stInternal)
        self.make_slices_calls = 0

    def regions(self):
        return self._regions

    def make_slices(self):
        self.make_slices_calls += 1

    def polygon_mm(self):
        """Test helper: the layer's current footprint in mm."""
        polys = []
        for r in self._regions:
            for s in r.slices.surfaces:
                polys.append(SPoly(np.asarray(s.expolygon.contour.as_array()) / SCALE,
                                   [np.asarray(h.as_array()) / SCALE
                                    for h in s.expolygon.holes]))
        return shapely.ops.unary_union(polys) if polys else SPoly()


class FakePrintObject:
    def __init__(self, layers, config=None):
        self._layers = layers
        self._config = {"layer_height": 0.2, "enable_support": False}
        self._config.update(config or {})

    def id(self):
        return 1

    def layers(self):
        return self._layers

    def config_value(self, key):
        return self._config.get(key)


class Step(enum.Enum):
    """The published enum, in pipeline order."""
    posSlice = 0
    posPerimeters = 1
    posPrepareInfill = 2
    posInfill = 3
    posIroning = 4
    posContouring = 5
    posSupportMaterial = 6
    posSimplifyPath = 7
    psSkirtBrim = 8
    psWipeTower = 9
    psGCodePostProcess = 10


class Ctx:
    """SlicingPipelineContext.

    At psGCodePostProcess the graph fields are None and gcode_path is set;
    at geometry steps it is the other way round. The fake enforces that.
    """

    def __init__(self, step, obj=None, gcode_path="", config=None,
                 output_name="out.gcode"):
        self.step = step
        self.orca_version = "2.5.0-fake"
        if step is Step.psGCodePostProcess:
            self.object, self.print = None, None
            self.gcode_path = gcode_path
        else:
            self.object, self.print = obj, object()
            self.gcode_path = ""
        self.host = ""
        self.output_name = output_name
        self._config = {"layer_height": 0.2}
        self._config.update(config or {})

    def config_value(self, key):
        return self._config.get(key)

    def cancelled(self):
        return False


class _CapBase:
    _config = "{}"

    def get_config(self):
        return self._config

    def save_config(self, cfg):
        self._config = cfg


def _model():
    return types.SimpleNamespace(objects=lambda: [])


def install():
    """Register this fake as `orca` in sys.modules and return it."""
    m = types.ModuleType("orca")
    m.PluginResult = PluginResult
    m.ExecutionResult = ExecutionResult
    m.PythonPluginBase = _CapBase
    m.host = types.SimpleNamespace(
        ExPolygon=ExPolygon, Polygon=Polygon, SurfaceType=SurfaceType,
        model=_model)
    m.slicing = types.SimpleNamespace(
        SlicingPipelineCapabilityBase=_CapBase, Step=Step,
        SlicingPipelineContext=Ctx, unscale=lambda v: v / SCALE)
    m.script = types.SimpleNamespace(ScriptPluginCapabilityBase=_CapBase)
    m.base = object
    m.plugin = lambda cls: cls
    m.register_capability = lambda cls: None

    import sys
    sys.modules["orca"] = m
    return m
