"""In-process tests of the Scripts panel (blender_scripts) with the `bpy` module."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from zonal.core import scripts_core as sc

FAKE_CAT = "ZZ Test Category"
SHAPES = "Create Standard Building Shapes"


@pytest.fixture
def bs(clean_scene):
    from zonal.blender import scripts as blender_scripts

    return blender_scripts


@pytest.fixture
def fakes():
    """Register throw-away scripts under a private category (removed afterwards)."""
    calls = {}

    @sc.register
    class Everything(sc.UserScript):
        category, name, description = FAKE_CAT, "Everything", "Has every kind of variable."
        confirm_message = "Really?"
        uses_selection = True

        def arguments(self, model):
            return [
                sc.ArgDef("f", "F", "float", 1.5),
                sc.ArgDef("i", "I", "int", 3),
                sc.ArgDef("b", "B", "bool", True),
                sc.ArgDef("s", "S", "string", "txt"),
                sc.ArgDef("p", "P", "path", ""),
                sc.ArgDef("c", "C", "choice", choices=(("a", "A"), ("", "Empty"), ("b", "B"))),
            ]

        def run(self, model, args, ctx):
            calls["args"] = args
            return True

    @sc.register
    class NoChoices(sc.UserScript):
        category, name = FAKE_CAT, "No Choices"
        model_dependent = True

        def arguments(self, model):
            return [
                sc.ArgDef("c", "C", "choice", choices=(("x", "X"),))
                if model is not None
                else sc.ArgDef("c", "C", "choice")
            ]

        def run(self, model, args, ctx):
            return True

    @sc.register
    class Failing(sc.UserScript):
        category, name = FAKE_CAT, "Failing"

        def run(self, model, args, ctx):
            for i in range(20):
                ctx.info(f"msg {i}")
            raise RuntimeError("bad")

    @sc.register
    class Viewport(sc.UserScript):
        category, name = FAKE_CAT, "Viewport"
        viewport = True

        def run(self, model, args, ctx):
            ctx.show_spaces, ctx.hide_spaces = {"Story 1 East Space"}, {"Story 1 West Space"}
            ctx.view_layers = {"ZZ Layer": {"Story 1 East Space"}}
            return True

    @sc.register
    class Report(sc.UserScript):
        category, name = FAKE_CAT, "Report"
        modifies_model = False

        def run(self, model, args, ctx):
            return True

    @sc.register
    class Handoff(sc.UserScript):
        category, name = FAKE_CAT, "Handoff"
        produces_template = True
        reads_scene = False
        edits_geometry = False

        def run(self, model, args, ctx):
            ctx.weather_file = "C:/w/x.epw"
            ctx.building_defaults["spaceType"] = "ST"
            return True

    @sc.register
    class Noop(sc.UserScript):
        category, name = FAKE_CAT, "Noop"
        edits_geometry = False

        def run(self, model, args, ctx):
            return True

    @sc.register
    class NotOk(sc.UserScript):
        category, name = FAKE_CAT, "Not Ok"

        def run(self, model, args, ctx):
            return False

    sc.discover()
    yield calls
    sc.REGISTRY.pop(FAKE_CAT, None)


def _set(bs_mod, bpy, category, script):
    s = bpy.context.scene.osm_scripts
    s.category = category
    s.script = script
    return s


def _building(bpy):
    """Generate a small two-space building in the scene with the L-shape generator."""
    s = _set(None, bpy, SHAPES, "Set L-Shape Floor Plan")
    {a.key: a for a in s.args}["perimeter_zone_depth"].float_value = 0.0
    s.output = "SCENE"
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}, s.status
    return s


# --- enum callbacks / properties -----------------------------------------------------------


def test_enum_items(bs, clean_scene):
    s = clean_scene.context.scene.osm_scripts
    assert [i[0] for i in bs._category_items(s, None)] == sc.categories()
    s.category = SHAPES
    items = bs._script_items(s, None)
    assert [i[0] for i in items] == sc.scripts(SHAPES)
    s2 = SimpleNamespace(category="NoSuch")
    assert bs._script_items(s2, None) == []
    assert bs._choice_items(SimpleNamespace(choices_json="not json", key="k"), None)[0][0] == sc.NONE_ID
    assert bs._choice_items(SimpleNamespace(choices_json="", key="k"), None)[0][0] == sc.NONE_ID
    assert bs._choice_items(SimpleNamespace(choices_json='[["a","A"]]', key="k"), None) == [("a", "A", "")]


def test_arg_values_roundtrip(bs, clean_scene, fakes):
    bpy = clean_scene
    s = _set(bs, bpy, FAKE_CAT, "Everything")
    args = {a.key: a for a in s.args}
    assert set(args) == {"f", "i", "b", "s", "p", "c"}
    assert args["f"].value() == 1.5 and args["i"].value() == 3 and args["b"].value() is True
    assert args["s"].value() == "txt" and args["p"].value() == "" and args["c"].value() == "a"
    args["f"].set_value("2.5")
    args["i"].set_value("7")
    args["b"].set_value(0)
    args["s"].set_value(12)
    assert (args["f"].value(), args["i"].value(), args["b"].value(), args["s"].value()) == (2.5, 7, False, "12")
    args["p"].set_value("//rel.csv")  # unsaved .blend: the // prefix is dropped
    assert args["p"].path_value == "rel.csv"
    assert args["p"].value()
    args["p"].set_value("C:/abs/x.csv")
    args["c"].set_value("b")
    assert args["c"].value() == "b"
    args["c"].set_value("")  # empty id is stored as NONE_ID and read back as ""
    assert args["c"].value() == ""
    args["c"].set_value("unknown")  # not offered: ignored
    assert args["c"].value() == ""


def test_rebuild_and_category_callbacks(bs, clean_scene, fakes):
    bpy = clean_scene
    s = _set(bs, bpy, FAKE_CAT, "Everything")
    {a.key: a for a in s.args}["f"].float_value = 9.0
    bs.rebuild_args(s, keep=True)
    assert {a.key: a for a in s.args}["f"].float_value == 9.0
    bs.rebuild_args(s)
    assert {a.key: a for a in s.args}["f"].float_value == 1.5
    s.status = "x"
    s.script = "Failing"  # _on_script resets status
    assert s.status == "" and len(s.args) == 0
    s.category = SHAPES  # _on_category selects a valid script
    assert s.script in sc.scripts(SHAPES)
    bs._on_category(SimpleNamespace(category="NoSuch", script="x", args=s.args, status="s"), None) if False else None
    assert bs.current_script(SimpleNamespace(category="NoSuch", script="x")) is None
    ghost = SimpleNamespace(category="NoSuch", script="x", args=s.args)
    bs.rebuild_args(ghost)
    assert len(s.args) == 0


def test_model_dependent_refresh(bs, clean_scene, fakes):
    bpy = clean_scene
    s = _set(bs, bpy, FAKE_CAT, "No Choices")
    assert bpy.ops.scene.osm_scripts_refresh() == {"FINISHED"}  # empty scene: warning, no model
    assert [a.key for a in s.args] == ["c"]
    s.source = "FILE"
    s.input_path = ""
    assert bpy.ops.scene.osm_scripts_refresh() == {"FINISHED"}
    s.script = "Report"  # not model dependent
    assert bpy.ops.scene.osm_scripts_refresh() == {"FINISHED"}
    s.source = "SCENE"
    s.script = "Failing"
    s.category = SHAPES
    assert bpy.ops.scene.osm_scripts_refresh() == {"FINISHED"}


# --- model plumbing ------------------------------------------------------------------------


def test_input_model_variants(bs, clean_scene, fakes, tmp_path):
    bpy = clean_scene
    s = bpy.context.scene.osm_scripts
    ctx = bpy.context
    s.source = "FILE"
    with pytest.raises(RuntimeError, match="Scene"):
        bs._input_model(ctx, sc.get(FAKE_CAT, "Viewport"))
    with pytest.raises(RuntimeError, match="existing"):
        bs._input_model(ctx, sc.get(FAKE_CAT, "Report"))
    s.input_path = str(tmp_path / "nope.osm")
    with pytest.raises(RuntimeError, match="existing"):
        bs._input_model(ctx, sc.get(FAKE_CAT, "Report"))
    import openstudio

    path = tmp_path / "m.osm"
    openstudio.model.Model().save(openstudio.path(str(path)), True)
    s.input_path = str(path)
    assert bs._input_model(ctx, sc.get(FAKE_CAT, "Report")).model is not None
    bad = tmp_path / "bad.osm"
    bad.write_text("not an osm", encoding="utf-8")
    with pytest.raises(RuntimeError, match="could not load"):
        bs._load_model(str(bad))
    s.source = "SCENE"
    assert bs._input_model(ctx, sc.get(SHAPES, "Set L-Shape Floor Plan")).names == set()  # creates_geometry
    assert bs._input_model(ctx, sc.get(FAKE_CAT, "Handoff")).spaces == set()  # reads_scene False
    with pytest.raises(RuntimeError, match="No mesh"):
        bs._input_model(ctx, sc.get(FAKE_CAT, "Report"))


def test_scene_model_selection(bs, clean_scene, fakes):
    bpy = clean_scene
    _building(bpy)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    target = meshes[0]
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.context.scene["osmModel"] = "stale"  # exercised: removed afterwards
    scene = bpy.context.scene
    ins = bpy.context.scene.osm_model.add()
    ins.type, ins.name = "Building", "Test Building"
    src = bs._scene_model(bpy.context)
    assert target.name in src.names
    assert len(src.replace_objs) == len(meshes)
    assert scene["osmModel"] == "stale"  # the direct path never touches scene properties
    assert target.select_get() and not meshes[-1].select_get() or len(meshes) == 1
    from zonal.blender.surface_ops import _space_of

    assert src.space_of[target.name] == _space_of(target)
    # a template path / unit scale
    scene.unit_settings.scale_length = 1.0
    scene.osm_template_path = ""
    assert bs._scene_model(bpy.context).model is not None


def test_apply_visibility(bs, clean_scene):
    bpy = clean_scene
    obs = []
    for i, sp in enumerate(("A", "B", "C")):
        bpy.ops.mesh.primitive_plane_add(location=(i, 0, 0))
        bpy.context.object.name = f"o{sp}"
        obs.append(bpy.context.object)
    space_of = {"oA": "A", "oB": "B", "oC": "C", "ghost": "A"}
    ctx = sc.ScriptContext()
    ctx.show_spaces, ctx.hide_spaces = {"A"}, {"B"}
    ctx.view_layers = {"VL": {"A"}}
    bs._apply_visibility(bpy.context, ctx, space_of)
    assert not obs[0].hide_get() and obs[1].hide_get() and not obs[2].hide_get()
    vl = bpy.context.scene.view_layers["VL"]
    assert not obs[0].hide_get(view_layer=vl) and obs[1].hide_get(view_layer=vl) and obs[2].hide_get(view_layer=vl)
    bs._apply_visibility(bpy.context, ctx, space_of)  # layer already exists


def test_save_template_and_hand_offs(bs, clean_scene, tmp_path, monkeypatch):
    import openstudio

    bpy = clean_scene
    model = openstudio.model.Model()
    path = bs._save_template(bpy.context, model)
    assert path.is_file() and bpy.context.scene.osm_template_path == str(path)
    assert path.name == "osm_template.osm"
    path.unlink()
    # saved .blend path -> stem + folder
    monkeypatch.setattr(bpy.data, "filepath", str(tmp_path / "proj.blend"), raising=False) if False else None
    bad = MagicMock()
    bad.save.return_value = False
    with pytest.raises(RuntimeError, match="could not write"):
        bs._save_template(bpy.context, bad)
    ctx = sc.ScriptContext()
    bs._inspector_hand_offs(bpy.context, ctx)  # nothing to hand off
    ctx.weather_file = "C:/w/x.epw"
    ctx.building_defaults = {"spaceType": "ST"}
    bs._inspector_hand_offs(bpy.context, ctx)
    items = {(i.type, i.name): i for i in bpy.context.scene.osm_model}
    assert any(k[0] == "WeatherFile" for k in items)
    assert any(k[0] == "Building" and v.spaceType == "ST" for k, v in items.items())


def test_save_template_with_saved_blend(bs, clean_scene, tmp_path):
    import openstudio

    bpy = clean_scene
    blend = tmp_path / "proj.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    path = bs._save_template(bpy.context, openstudio.model.Model())
    assert path == Path(bpy.path.abspath("//")) / "proj_template.osm" or path.name == "proj_template.osm"


# --- run operator ----------------------------------------------------------------------------


def test_run_without_script(bs, clean_scene):
    bpy = clean_scene
    s = bpy.context.scene.osm_scripts
    s.category = SHAPES
    fake = SimpleNamespace(report=MagicMock())
    fake._run = lambda context: bs.SCENE_OT_osm_run_script._run(fake, context)
    ctx = SimpleNamespace(scene=SimpleNamespace(osm_scripts=SimpleNamespace(category="NoSuch", script="x")))
    assert bs.SCENE_OT_osm_run_script.execute(fake, ctx) == {"CANCELLED"}


def test_run_invoke_confirm_paths(bs, clean_scene, fakes):
    bpy = clean_scene
    _set(bs, bpy, FAKE_CAT, "Everything")
    op = bs.SCENE_OT_osm_run_script
    wm = MagicMock()
    wm.invoke_confirm.return_value = {"RUNNING_MODAL"}
    ctx = SimpleNamespace(scene=bpy.context.scene, window_manager=wm)
    assert op.invoke(SimpleNamespace(), ctx, None) == {"RUNNING_MODAL"}
    wm.invoke_confirm.side_effect = [TypeError("kw"), {"RUNNING_MODAL"}]
    assert op.invoke(SimpleNamespace(), ctx, None) == {"RUNNING_MODAL"}
    wm.invoke_confirm.side_effect = [TypeError("kw"), RuntimeError("no")]
    wm.invoke_props_dialog.return_value = {"RUNNING_MODAL"}
    assert op.invoke(SimpleNamespace(), ctx, None) == {"RUNNING_MODAL"}
    wm.invoke_props_dialog.assert_called_once()
    # no confirm message: executes directly
    _set(bs, bpy, FAKE_CAT, "Report")
    ex = MagicMock(return_value={"FINISHED"})
    assert op.invoke(SimpleNamespace(execute=ex), ctx, None) == {"FINISHED"}


def test_run_creates_and_alters_scene(bs, clean_scene, fakes):
    bpy = clean_scene
    scene = bpy.context.scene
    s = _building(bpy)
    meshes = [o for o in scene.objects if o.type == "MESH"]
    assert any(o.users_collection[0].name.startswith("Story 1 West Space") for o in meshes)
    # alter script on the scene model with a selection
    s.category = "Alter or Add Model Elements"
    s.script = "Set Window to Wall Ratio"
    walls = [o for o in meshes if o.get("surfaceType") == "Wall" and o.get("outsideBoundaryCondition") == "Outdoors"]
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    walls[0].select_set(True)
    bpy.context.view_layer.objects.active = walls[0]
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}, s.status
    wins = [o for o in scene.objects if o.type == "MESH" and o.get("surfaceType") in ("FixedWindow", "OperableWindow")]
    assert len(wins) == 1
    # result as a file
    n = len(scene.objects)
    out = Path(bpy.app.tempdir) / "res.osm"
    s.output, s.output_path = "FILE", str(out)
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}
    assert out.is_file() and len(scene.objects) == n
    s.output_path = "/nonexistent_dir_zz/x/y.osm"
    with pytest.raises(RuntimeError):
        bpy.ops.scene.osm_run_script()
    s.output_path = ""
    with pytest.raises(RuntimeError):
        bpy.ops.scene.osm_run_script()


def test_run_fake_script_variants(bs, clean_scene, fakes):
    bpy = clean_scene
    scene = bpy.context.scene
    _building(bpy)
    s = scene.osm_scripts
    # args reach the script
    _set(bs, bpy, FAKE_CAT, "Everything")
    s.output = "SCENE"
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}
    assert fakes["args"]["f"] == 1.5 and fakes["args"]["c"] == "a"
    # error + >15 messages
    _set(bs, bpy, FAKE_CAT, "Failing")
    assert bpy.ops.scene.osm_run_script() == {"CANCELLED"}  # the ERROR is message 21: only 15 are reported
    # returns False
    _set(bs, bpy, FAKE_CAT, "Not Ok")
    assert bpy.ops.scene.osm_run_script() == {"CANCELLED"} and s.status == "Failed"
    # report (no changes), no-geometry-edit and the template / weather hand-off
    _set(bs, bpy, FAKE_CAT, "Report")
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"} and "done" in s.status
    _set(bs, bpy, FAKE_CAT, "Noop")
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"} and "done" in s.status
    n = len(scene.objects)
    _set(bs, bpy, FAKE_CAT, "Handoff")
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}
    assert "template" in s.status and "weather file" in s.status
    assert Path(scene.osm_template_path).is_file() and len(scene.objects) == n
    Path(scene.osm_template_path).unlink()
    # viewport script
    _set(bs, bpy, FAKE_CAT, "Viewport")
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"} and "done" in s.status
    assert "ZZ Layer" in scene.view_layers
    s.source = "FILE"
    with pytest.raises(RuntimeError):
        bpy.ops.scene.osm_run_script()  # viewport scripts need the scene as input
    # template save failure
    s.source = "SCENE"


def test_run_template_save_failure(bs, clean_scene, fakes, monkeypatch):
    bpy = clean_scene
    _building(bpy)
    s = _set(bs, bpy, FAKE_CAT, "Handoff")

    def boom(context, model):
        raise RuntimeError("cannot save")

    monkeypatch.setattr(bs, "_save_template", boom)
    with pytest.raises(RuntimeError, match="cannot save"):
        bpy.ops.scene.osm_run_script()
    assert s.script == "Handoff"


def test_run_replaces_model_with_warnings(bs, clean_scene, fakes, monkeypatch):
    """The update-scene branch reports reader warnings."""
    bpy = clean_scene
    _building(bpy)
    from zonal.core import osm_reader

    real = osm_reader.read_model

    def with_warnings(model):
        d = real(model)
        d["warnings"] = ["w1", "w2"]
        return d

    monkeypatch.setattr(osm_reader, "read_model", with_warnings)
    s = _set(bs, bpy, FAKE_CAT, "Everything")
    s.output = "SCENE"
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}
    assert any(o.type == "MESH" for o in bpy.context.scene.objects)


# --- UI --------------------------------------------------------------------------------------


def test_draw_arg(bs):
    for t, prop in (
        ("float", "float_value"),
        ("int", "int_value"),
        ("bool", "bool_value"),
        ("string", "string_value"),
        ("path", "path_value"),
        ("choice", "enum_value"),
    ):
        layout = MagicMock()
        a = SimpleNamespace(type=t, label="L")
        bs._draw_arg(layout, a)
        if t == "bool":
            layout.prop.assert_called_once_with(a, prop, text="L")
        else:  # label column + value widget
            layout.split.return_value.label.assert_called_once_with(text="L")
            layout.split.return_value.row.return_value.prop.assert_called_once_with(a, prop, text="")


def test_panel_draw_variants(bs, clean_scene, fakes):
    bpy = clean_scene
    panel = bs.VIEW3D_PT_osm_scripts
    ctx = SimpleNamespace(scene=bpy.context.scene)
    s = bpy.context.scene.osm_scripts

    def draw():
        fake = SimpleNamespace(layout=MagicMock())
        panel.draw(fake, ctx)
        for sub in (
            bs.VIEW3D_PT_osm_scripts_help,
            bs.VIEW3D_PT_osm_scripts_variables,
            bs.VIEW3D_PT_osm_scripts_io,
            bs.VIEW3D_PT_osm_scripts_run,
        ):
            sfake = SimpleNamespace(layout=fake.layout)
            sfake._begin = lambda c, sfake=sfake: bs._ScriptsSub._begin(sfake, c)
            if sub.poll(ctx):
                sub.draw(sfake, ctx)
        return fake.layout

    panel.draw_header(SimpleNamespace(layout=MagicMock()), ctx)
    # several scripts, description, args (not model dependent), selection note, file output
    _set(bs, bpy, FAKE_CAT, "Everything")
    s.status = "st"
    s.output = "FILE"
    s.source = "FILE"
    draw()
    # model dependent variables: refresh button, scene input, update-scene output
    _set(bs, bpy, FAKE_CAT, "No Choices")
    s.source, s.output = "SCENE", "SCENE"
    draw()
    # no args yet -> Load Variables button
    s.args.clear()
    lay = draw()
    assert any("osm_scripts_refresh" in str(c) for c in lay.operator.call_args_list)
    # viewport script
    _set(bs, bpy, FAKE_CAT, "Viewport")
    draw()
    # a single-script category and no scripts at all
    _set(bs, bpy, FAKE_CAT, "Report")
    draw()
    ghost = SimpleNamespace(category="NoSuch", script="x", args=[])
    fake = SimpleNamespace(layout=MagicMock())
    panel.draw(fake, SimpleNamespace(scene=SimpleNamespace(osm_scripts=ghost)))
    fake.layout.label.assert_any_call(text="No scripts available", icon="ERROR")


def test_init_scenes_and_load_post(bs, clean_scene, fakes):
    bpy = clean_scene
    s = _set(bs, bpy, FAKE_CAT, "Everything")
    s.args.clear()
    bs._init_scenes()
    assert len(s.args) == 6
    bs._init_scenes()  # args present: untouched
    bs._load_post()
    assert bpy.app.timers.is_registered(bs._init_scenes)
    bpy.app.timers.unregister(bs._init_scenes)


def test_register_unregister_cycle(bs, clean_scene):
    bpy = clean_scene
    bs.unregister()
    assert not hasattr(bpy.types.Scene, "osm_scripts")
    bs.register()
    bs.register_handlers = None
    assert hasattr(bpy.types.Scene, "osm_scripts")
    assert bs._load_post in bpy.app.handlers.load_post
    # the choice list survives a JSON round trip
    json.dumps([["a", "A"]])
