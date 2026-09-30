"""A stand-in for OrcaSlicer's embedded `orca` module, for offline tests.

Deliberately stdlib-only. The Unlayered Infill plugin has no third-party
dependencies, and its test lane shouldn't invent any: the geometry-heavy
version of this fake lives at plugins/orca-wave/tests/fake_orca.py and pulls
in numpy + shapely, which nothing here needs.

Modelled on the published API:
https://www.orcaslicer.com/wiki/developer_reference/plugin_development/api_reference/slicing

The detail that matters for this plugin: at `Step.psGCodePostProcess` the
graph fields (`print`, `object`) are None and `gcode_path` points at the
working file. The fake enforces that split so a test cannot accidentally pass
by reading something the real host would not provide.
"""
import enum
import sys
import types


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
        if not isinstance(status, PluginResult):  # the real binding raises too
            raise TypeError(
                f"failure(): status must be PluginResult, got {type(status).__name__}")
        return ExecutionResult(status, message, data)

    def __repr__(self):
        return f"{self.status.name}: {self.message}"


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
    """SlicingPipelineContext."""

    def __init__(self, step, gcode_path="", config=None, output_name="out.gcode"):
        self.step = step
        self.orca_version = "2.5.0-fake"
        if step is Step.psGCodePostProcess:
            self.object, self.print = None, None
            self.gcode_path = gcode_path
        else:
            self.object, self.print = object(), object()
            self.gcode_path = ""
        self.host = ""
        self.output_name = output_name
        self._config = dict(config or {})

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


def install():
    """Register this fake as `orca` in sys.modules and return it."""
    m = types.ModuleType("orca")
    m.PluginResult = PluginResult
    m.ExecutionResult = ExecutionResult
    m.PythonPluginBase = _CapBase
    m.slicing = types.SimpleNamespace(
        SlicingPipelineCapabilityBase=_CapBase, Step=Step,
        SlicingPipelineContext=Ctx)
    m.script = types.SimpleNamespace(ScriptPluginCapabilityBase=_CapBase)
    m.host = types.SimpleNamespace(
        model=lambda: types.SimpleNamespace(objects=lambda: []))
    m.base = object
    m.plugin = lambda cls: cls
    m.register_capability = lambda cls: None
    sys.modules["orca"] = m
    return m
