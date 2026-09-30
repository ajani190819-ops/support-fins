"""End-to-end tests for the plugin's single Orca seam.

The Wave Overhangs lane shipped three no-op bugs because nothing ever drove
`execute(ctx)`. This suite exists so that cannot happen here.

Set WAVE_PLUGIN_PATH... no: set INFILL_PLUGIN_PATH to run this against the
built single-file plugin, since build.py inlines nonplanar_core.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import fake_orca  # noqa: E402
from test_nonplanar_core import cube_gcode  # noqa: E402

SRC = Path(os.environ.get(
    "INFILL_PLUGIN_PATH",
    Path(__file__).resolve().parents[1] / "src" / "unlayered_infill_orca.py"))


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    fake_orca.install()
    sys.modules.pop("unlayered_infill_orca", None)
    spec = importlib.util.spec_from_file_location("unlayered_infill_orca", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["unlayered_infill_orca"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_write_log", lambda e, enabled=True: None)
    # the plugin records whether its seam actually fired; keep that per-test
    monkeypatch.setattr(mod, "_state_path", lambda: str(tmp_path / "state.json"))
    return mod


def make_cap(plugin, config=None):
    cap = plugin.UnlayeredInfill()
    if config is not None:
        cap._config = json.dumps(config)
    return cap


def gcode_file(tmp_path, lines=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "out.gcode"
    p.write_text("".join(lines if lines is not None else cube_gcode()),
                 encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# The seam actually fires and actually rewrites the file
# --------------------------------------------------------------------------

def test_postprocess_rewrites_the_gcode(plugin, tmp_path):
    path = gcode_file(tmp_path)
    before = path.read_text(encoding="utf-8")

    cap = make_cap(plugin)
    res = cap.execute(fake_orca.Ctx(fake_orca.Step.psGCodePostProcess,
                                    gcode_path=str(path)))

    assert res.status is fake_orca.PluginResult.Success, res.message
    after = path.read_text(encoding="utf-8")
    assert after != before, "the G-code file was not modified"
    assert "infill move(s)" in res.message
    assert "wavy segment(s)" in res.message


def test_geometry_steps_are_ignored(plugin, tmp_path):
    """This plugin belongs in the post-processing field only."""
    path = gcode_file(tmp_path)
    before = path.read_text(encoding="utf-8")
    cap = make_cap(plugin)
    for step in (fake_orca.Step.posSlice, fake_orca.Step.posInfill,
                 fake_orca.Step.psSkirtBrim, fake_orca.Step.posPerimeters):
        res = cap.execute(fake_orca.Ctx(step))
        assert res.status is fake_orca.PluginResult.Success
    assert path.read_text(encoding="utf-8") == before


def test_disabled_config_leaves_the_file_alone(plugin, tmp_path):
    path = gcode_file(tmp_path)
    before = path.read_text(encoding="utf-8")
    cap = make_cap(plugin, {"enabled": False})
    res = cap.execute(fake_orca.Ctx(fake_orca.Step.psGCodePostProcess,
                                    gcode_path=str(path)))
    assert "disabled" in res.message
    assert path.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------
# Failure modes must be explained, never silent and never destructive
# --------------------------------------------------------------------------

def test_absolute_extrusion_fails_loudly_and_leaves_the_file_intact(plugin, tmp_path):
    src = [l.replace("M83 ; relative extrusion\n", "M82 ; absolute\n")
           for l in cube_gcode()]
    path = gcode_file(tmp_path, src)
    before = path.read_text(encoding="utf-8")

    cap = make_cap(plugin)
    res = cap.execute(fake_orca.Ctx(fake_orca.Step.psGCodePostProcess,
                                    gcode_path=str(path)))

    assert res.status is fake_orca.PluginResult.RecoverableError
    assert "relative E distances" in res.message
    assert path.read_text(encoding="utf-8") == before, \
        "a refused run must not touch the file"


def test_a_part_with_no_infill_says_so_and_changes_nothing(plugin, tmp_path):
    src = ["M83\n", "G1 Z0.2 F600\n", ";TYPE:Outer wall\n",
           "G1 X1.000 Y1.000 E0.10000\n", "G1 X9.000 Y1.000 E0.50000\n"]
    path = gcode_file(tmp_path, src)
    before = path.read_text(encoding="utf-8")

    res = make_cap(plugin).execute(
        fake_orca.Ctx(fake_orca.Step.psGCodePostProcess, gcode_path=str(path)))

    assert res.status is fake_orca.PluginResult.Success
    assert "nothing to do" in res.message
    assert path.read_text(encoding="utf-8") == before


def test_a_missing_file_is_reported(plugin, tmp_path):
    res = make_cap(plugin).execute(
        fake_orca.Ctx(fake_orca.Step.psGCodePostProcess,
                      gcode_path=str(tmp_path / "nope.gcode")))
    assert res.status is fake_orca.PluginResult.RecoverableError
    assert "no G-code file" in res.message


def test_a_bad_amplitude_is_explained(plugin, tmp_path):
    path = gcode_file(tmp_path)
    before = path.read_text(encoding="utf-8")
    cap = make_cap(plugin, {"amplitude": "wavy"})
    res = cap.execute(fake_orca.Ctx(fake_orca.Step.psGCodePostProcess,
                                    gcode_path=str(path)))
    assert res.status is fake_orca.PluginResult.RecoverableError
    assert "Could not understand" in res.message
    assert path.read_text(encoding="utf-8") == before


def test_an_unexpected_bug_never_breaks_the_export(plugin, tmp_path, monkeypatch):
    path = gcode_file(tmp_path)
    before = path.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(plugin.npc, "process", boom)

    res = make_cap(plugin).execute(
        fake_orca.Ctx(fake_orca.Step.psGCodePostProcess, gcode_path=str(path)))

    assert res.status is fake_orca.PluginResult.Success, \
        "a plugin bug must not fail the user's export"
    assert "left untouched" in res.message
    assert path.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------
# Config reaches the engine
# --------------------------------------------------------------------------

def test_amplitude_config_is_honoured(plugin, tmp_path):
    small = gcode_file(tmp_path / "a", cube_gcode())
    big = gcode_file(tmp_path / "b", cube_gcode())

    r1 = make_cap(plugin, {"amplitude": "-0.1"}).execute(
        fake_orca.Ctx(fake_orca.Step.psGCodePostProcess, gcode_path=str(small)))
    r2 = make_cap(plugin, {"amplitude": "-0.4"}).execute(
        fake_orca.Ctx(fake_orca.Step.psGCodePostProcess, gcode_path=str(big)))

    def wiggle(msg):
        return float(msg.split("largest Z offset")[1].split("mm")[0].strip())
    assert wiggle(r2.message) > wiggle(r1.message) * 3


def test_percent_amplitude_reads_the_layer_height(plugin, tmp_path):
    path = gcode_file(tmp_path)
    res = make_cap(plugin, {"amplitude": "-150%"}).execute(
        fake_orca.Ctx(fake_orca.Step.psGCodePostProcess, gcode_path=str(path)))
    assert res.status is fake_orca.PluginResult.Success, res.message
    assert "of layer height 0.200 mm" in res.message


def test_setup_check_reports_whether_the_seam_ever_ran(plugin, tmp_path):
    """Observed behaviour, not a guess about which preset field to use."""
    res = plugin.UnlayeredInfillCheck().execute()
    assert res.status is fake_orca.PluginResult.Success, res.message
    assert "NEVER RUN" in res.message
    assert "Slicing Pipeline Plugin" in res.message
    assert "relative E distances" in res.message

    # after a real run it reports what happened
    path = gcode_file(tmp_path)
    make_cap(plugin).execute(fake_orca.Ctx(fake_orca.Step.psGCodePostProcess,
                                           gcode_path=str(path)))
    res = plugin.UnlayeredInfillCheck().execute()
    assert "The G-code step ran" in res.message
    assert "NEVER RUN" not in res.message


def test_a_refusal_still_records_that_the_seam_fired(plugin, tmp_path):
    src = [l.replace("M83 ; relative extrusion\n", "M82 ; absolute\n")
           for l in cube_gcode()]
    path = gcode_file(tmp_path, src)
    make_cap(plugin).execute(fake_orca.Ctx(fake_orca.Step.psGCodePostProcess,
                                           gcode_path=str(path)))
    res = plugin.UnlayeredInfillCheck().execute()
    assert "refused" in res.message
    assert "relative E" in res.message


def test_an_empty_part_explains_itself(plugin, tmp_path):
    src = ["M83\n", "G1 Z0.2 F600\n", ";TYPE:Outer wall\n",
           "G1 X1.000 Y1.000 E0.10000\n", "G1 X9.000 Y1.000 E0.50000\n"]
    path = gcode_file(tmp_path, src)
    make_cap(plugin).execute(fake_orca.Ctx(fake_orca.Step.psGCodePostProcess,
                                           gcode_path=str(path)))
    res = plugin.UnlayeredInfillCheck().execute()
    assert "The G-code step ran" in res.message
    assert "no sparse infill between two" in res.message


def test_the_plugin_declares_no_dependencies():
    """The whole point of this lane: no numpy, no shapely, no restart."""
    head = SRC.read_text(encoding="utf-8").split("# ///")[1]
    assert "dependencies = []" in head, head


def test_running_the_export_step_twice_does_not_double_the_wave(plugin, tmp_path):
    """Orca invokes psGCodePostProcess separately for file export and upload.

    Without the marker the second call waves the already-waved file, doubling
    every Z displacement and splitting every segment again.
    """
    path = gcode_file(tmp_path)
    cap = make_cap(plugin)
    ctx = fake_orca.Ctx(fake_orca.Step.psGCodePostProcess, gcode_path=str(path))

    r1 = cap.execute(ctx)
    assert r1.status is fake_orca.PluginResult.Success, r1.message
    after_first = path.read_text(encoding="utf-8")

    r2 = cap.execute(ctx)
    assert r2.status is fake_orca.PluginResult.Success, r2.message
    assert "already applied" in r2.message, r2.message
    assert path.read_text(encoding="utf-8") == after_first, \
        "the second export pass modified the G-code again"


def test_setup_check_reports_the_skip(plugin, tmp_path):
    path = gcode_file(tmp_path)
    cap = make_cap(plugin)
    ctx = fake_orca.Ctx(fake_orca.Step.psGCodePostProcess, gcode_path=str(path))
    cap.execute(ctx)
    cap.execute(ctx)
    res = plugin.UnlayeredInfillCheck().execute()
    assert "The G-code step ran" in res.message


def test_the_new_shaping_options_reach_the_engine(plugin, tmp_path):
    """full_strength must actually change the output, not just be accepted."""
    a = gcode_file(tmp_path / "a")
    b = gcode_file(tmp_path / "b")
    make_cap(plugin).execute(
        fake_orca.Ctx(fake_orca.Step.psGCodePostProcess, gcode_path=str(a)))
    make_cap(plugin, {"full_strength": True}).execute(
        fake_orca.Ctx(fake_orca.Step.psGCodePostProcess, gcode_path=str(b)))
    assert a.read_text(encoding="utf-8") != b.read_text(encoding="utf-8"), \
        "full_strength was accepted but changed nothing"
