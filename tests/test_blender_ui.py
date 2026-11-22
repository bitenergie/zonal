"""In-process Blender tests for blender_ui, blender_icons and the package __init__."""

import pytest

pytest.importorskip("bpy")


def _room(bpy, name="Room", col_name="Office", location=(5, 2.5, 1.5), size=(10, 5, 3)):
    col = bpy.data.collections.get(col_name) or bpy.data.collections.new(col_name)
    if col.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(col)
    bpy.ops.mesh.primitive_cube_add(size=1)
    box = bpy.context.object
    box.name = name
    box.scale = size
    box.location = location
    bpy.ops.object.transform_apply(scale=True)
    for c in list(box.users_collection):
        c.objects.unlink(box)
    col.objects.link(box)
    return box


def _window(bpy):
    bpy.ops.mesh.primitive_plane_add(size=1)
    win = bpy.context.object
    win.name = "Window 1"
    win.scale = (2, 1, 1)
    win.rotation_euler = (1.5707963, 0, 0)
    win.location = (5, 0, 1.5)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    win["surfaceType"] = "FixedWindow"
    win["spaceName"] = "Office"
    return win


def test_init_exports():
    import zonal

    assert zonal.__version__
    assert zonal.bl_info["name"]


def test_prefs_and_status(clean_scene, monkeypatch):
    from zonal.blender import ui

    class FakePrefs:
        openstudio_path = ""

    fp = FakePrefs()
    monkeypatch.setattr(ui, "prefs", lambda: fp)
    p = fp
    ui._status = "stale"
    ui._on_path_changed(None, None)
    assert ui._status is None
    ok, msg = ui.backend_status()
    assert ok and "OpenStudio" in msg
    assert ui.backend_status() is ui._status
    # missing add-on
    monkeypatch.undo()
    monkeypatch.setattr(ui, "ADDON_ID", "nope")
    assert ui.prefs() is None
    monkeypatch.undo()
    monkeypatch.setattr(ui, "prefs", lambda: fp)

    # failure path
    monkeypatch.setattr(ui, "_status", None)
    monkeypatch.setattr(ui, "load_openstudio", lambda: (_ for _ in ()).throw(ImportError("boom")))
    assert ui.backend_status() == (False, "boom")
    ui._status = None
    monkeypatch.undo()
    monkeypatch.setattr(ui, "prefs", lambda: fp)

    # load_openstudio with a path set
    from zonal.core import openstudio_env

    seen = []
    monkeypatch.setattr(openstudio_env, "load", lambda path: seen.append(path) or "mod")
    p.openstudio_path = "//x"
    assert ui.load_openstudio() == "mod" and seen[-1].endswith("x")
    p.openstudio_path = ""
    monkeypatch.undo()
    ui._status = None


def test_prefs_draw(clean_scene):
    from zonal.blender import ui

    class L:
        def __init__(self):
            self.calls = []

        def label(self, **kw):
            self.calls.append(("label", kw))

        def prop(self, *a, **kw):
            self.calls.append(("prop", a))

        def panel(self, *a, **kw):
            return self, self

    class Fake:
        layout = L()

    ui.OSMPreferences.draw(Fake, None)
    assert len(Fake.layout.calls) == 19
    ui._status = (False, "bad")
    Fake.layout = L()
    ui.OSMPreferences.draw(Fake, None)
    assert Fake.layout.calls[-1][1]["icon"] == "ERROR"
    ui._status = None


def test_sync_displace_and_overrides(clean_scene):
    bpy = clean_scene
    from zonal.blender import ui
    from zonal.core.tools_core import LOOSE_COLLECTION

    box = _room(bpy)
    win = _window(bpy)
    bpy.ops.object.empty_add()
    empty = bpy.context.object
    ui.sync_subsurface_displace(empty)  # non-mesh early return
    assert not empty.modifiers

    ui.sync_subsurface_displace(win)
    assert ui.DISPLACE_NAME in win.modifiers
    ui.sync_subsurface_displace(win)  # already there
    assert len(win.modifiers) == 1
    win["surfaceType"] = "Wall"
    ui.sync_subsurface_displace(win)
    assert not win.modifiers
    win["surfaceType"] = "FixedWindow"

    loose = bpy.data.collections.new(LOOSE_COLLECTION)
    bpy.context.scene.collection.children.link(loose)
    bpy.ops.mesh.primitive_plane_add()
    lo = bpy.context.object
    lo.name = "Loose"
    for c in list(lo.users_collection):
        c.objects.unlink(lo)
    loose.objects.link(lo)
    box2 = _room(bpy, "Other", "Spaced")
    box2["spaceName"] = "Custom"
    root = bpy.data.objects.new("rootmesh", bpy.data.meshes.new("rootmesh"))
    bpy.context.scene.collection.objects.link(root)

    out = ui.default_space_overrides(bpy.context.scene.objects)
    assert out[box.name] == {ui.FORCED_KEY: {"spaceName": "Office"}}  # the collection is the space
    assert out["Other"] == {ui.FORCED_KEY: {"spaceName": "Spaced"}}  # a stale spaceName property is ignored
    assert out["rootmesh"] == {}
    assert "Loose" not in out and empty.name not in out


def test_space_attributes_are_one_value_per_space(clean_scene):
    bpy = clean_scene
    from zonal.blender import ui

    a = _room(bpy, "A", "Office")
    b = _room(bpy, "B", "Office")
    a["thermalZoneName"] = "Z1"
    b["thermalZoneName"] = "Z2"
    b["buildingStoryName"] = "Story 1"
    out = ui.default_space_overrides(bpy.context.scene.objects)
    forced = {n: {k: v for k, v in out[n][ui.FORCED_KEY].items() if k != "spaceName"} for n in ("A", "B")}
    assert forced["A"] == forced["B"] == {"thermalZoneName": "Z1", "buildingStoryName": "Story 1"}
    assert len(ui.SPACE_CONFLICTS) == 1 and "thermalZoneName" in ui.SPACE_CONFLICTS[0]


def test_air_loops_are_one_value_per_thermal_zone(clean_scene):
    bpy = clean_scene
    from zonal.blender import ui

    a = _room(bpy, "A", "Office")
    b = _room(bpy, "B", "Lab")  # another space, same thermal zone
    a["thermalZoneName"] = b["thermalZoneName"] = "Z"
    b["airLoopHVACNames"] = {"0": "Loop 1"}
    out = ui.default_space_overrides(bpy.context.scene.objects)
    assert out["A"][ui.FORCED_KEY]["airLoopHVACNames"] == {"0": "Loop 1"} == out["B"][ui.FORCED_KEY]["airLoopHVACNames"]


def test_export_osm_gbxml_and_import(clean_scene, tmp_path):
    bpy = clean_scene
    from zonal.blender import ui

    box = _room(bpy)
    box["thermalZoneName"] = "Zone 1"
    win = _window(bpy)
    bpy.context.scene["northAxis"] = 20.0
    bpy.context.scene.osm_template_path = ""

    # modifier muting path: displace is visible during export_gltf and restored afterwards
    ui.sync_subsurface_displace(win)
    out = tmp_path / "m.osm"
    assert bpy.ops.export_scene.openstudio_osm(filepath=str(out), match_surfaces=True) == {"FINISHED"}
    assert out.exists() and not out.with_suffix(".glb").exists()  # direct export: no intermediate glTF
    assert win.modifiers[ui.DISPLACE_NAME].show_viewport
    assert "osmModel" not in bpy.context.scene

    xml = tmp_path / "m.xml"
    assert bpy.ops.export_scene.openstudio_gbxml(filepath=str(xml), apply_unit_scale=False) == {"FINISHED"}
    assert xml.exists()

    # selection-only export
    for o in bpy.context.view_layer.objects:
        o.select_set(o is box)
    out2 = tmp_path / "sel.osm"
    assert bpy.ops.export_scene.openstudio_osm(filepath=str(out2), use_selection=True) == {"FINISHED"}

    # import both formats
    bpy.ops.wm.read_factory_settings(use_empty=True)
    assert bpy.ops.import_scene.openstudio_osm(filepath=str(out)) == {"FINISHED"}
    assert any(o.get("surfaceType") for o in bpy.data.objects)
    assert "m" in bpy.data.collections
    bpy.ops.wm.read_factory_settings(use_empty=True)
    assert bpy.ops.import_scene.openstudio_gbxml(filepath=str(xml), apply_unit_scale=False) == {"FINISHED"}
    assert len(bpy.data.objects) > 0


def test_export_with_store_and_template(clean_scene, tmp_path):
    bpy = clean_scene
    _room(bpy)
    from zonal.blender import ui

    ui_mod = ui
    import zonal.blender.inspector as insp

    orig = insp.export_json
    insp.export_json = lambda scene: '{"Building": {}}'
    try:
        tpl = __import__("pathlib").Path(ui_mod.__file__).parent / "templates" / "MinimalTemplate.osm"
        bpy.context.scene.osm_template_path = str(tpl)
        out = tmp_path / "t.osm"
        res = bpy.ops.export_scene.openstudio_osm(filepath=str(out))
        assert res == {"FINISHED"}
    finally:
        insp.export_json = orig
        bpy.context.scene.osm_template_path = ""


def test_export_errors(clean_scene, tmp_path):
    bpy = clean_scene
    with pytest.raises(RuntimeError, match="No mesh objects"):
        bpy.ops.export_scene.openstudio_osm(filepath=str(tmp_path / "a.osm"))
    _room(bpy)
    # build failure: unwritable destination dir
    with pytest.raises(RuntimeError):
        bpy.ops.export_scene.openstudio_osm(filepath=str(tmp_path / "no" / "dir" / "a.osm"))


def test_export_gltf_tolerates_unknown_props(clean_scene, monkeypatch, tmp_path):
    bpy = clean_scene
    from zonal.blender import ui

    _room(bpy)
    assert ui.export_gltf(bpy.context, str(tmp_path / "x.glb"), False) == {"FINISHED"}


def test_run_build_run_read_paths(monkeypatch):
    from zonal.blender import ui
    from zonal.core import osm_builder, osm_reader

    monkeypatch.setattr(ui, "load_openstudio", lambda: None)
    monkeypatch.setattr(ui, "scene_faces", lambda *a: "faces")
    monkeypatch.setattr(osm_builder, "scene_to_gbxml", lambda *a: "gb")
    monkeypatch.setattr(osm_builder, "scene_to_osm", lambda *a: "osm")
    opts = osm_builder.BuildOptions()
    assert ui.run_build(None, "b", {}, opts, "gbxml") == "gb"
    assert ui.run_build(None, "b", {}, opts) == "osm"
    monkeypatch.setattr(osm_reader, "read_gbxml", lambda p: "gb")
    monkeypatch.setattr(osm_reader, "read_osm", lambda p: "osm")
    assert ui.run_read("a", "gbxml") == "gb"
    assert ui.run_read("a") == "osm"


def test_import_errors_and_warnings(clean_scene, monkeypatch, tmp_path):
    bpy = clean_scene
    from zonal.blender import ui

    with pytest.raises(RuntimeError):
        bpy.ops.import_scene.openstudio_osm(filepath=str(tmp_path / "missing.osm"))
    data = {
        "objects": [
            {"name": "tiny", "polygon": [(0, 0, 0), (1, 0, 0)], "space": "S", "kind": "wall", "props": {}},
            {
                "name": "sh",
                "polygon": [(0, 0, 0), (1, 0, 0), (1, 1, 0)],
                "space": None,
                "kind": "shading",
                "props": {"a": 1},
            },
            {
                "name": "un",
                "polygon": [(0, 0, 0), (1, 0, 0), (1, 1, 0)],
                "space": None,
                "kind": "wall",
                "props": {"surfaceType": "FixedWindow"},
            },
            {"name": "un2", "polygon": [(0, 0, 0), (1, 0, 0), (1, 1, 0)], "space": None, "kind": "wall", "props": {}},
        ],
        "northAxis": None,
        "warnings": ["w1"],
    }
    monkeypatch.setattr(ui, "run_read", lambda p, fmt: data)
    assert bpy.ops.import_scene.openstudio_osm(filepath=str(tmp_path / "x.osm")) == {"FINISHED"}
    assert bpy.data.collections.get("Shading") and bpy.data.collections.get("Unassigned")
    assert bpy.context.scene["northAxis"] == 0.0
    # nothing imported
    monkeypatch.setattr(ui, "run_read", lambda p, fmt: {"objects": []})
    assert bpy.ops.import_scene.openstudio_osm(filepath=str(tmp_path / "x.osm")) == {"FINISHED"}


def test_set_prop_init_props_and_menus(clean_scene):
    bpy = clean_scene
    from zonal.blender import ui

    box = _room(bpy)
    for o in bpy.context.view_layer.objects:
        o.select_set(o is box)
    assert bpy.ops.object.osm_set_prop(key="spaceName", value="X") == {"FINISHED"}
    assert box.users_collection[0].name == "X"
    # no selection: falls back to the active object
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.ops.object.osm_set_prop(key="k", value="v")
    assert box["k"] == "v"
    # active empty is skipped
    bpy.ops.object.empty_add()
    bpy.ops.object.osm_set_prop(key="k2", value="v")
    assert "k2" not in bpy.context.object

    assert "northAxis" not in bpy.context.scene
    assert bpy.ops.scene.osm_init_props() == {"FINISHED"}
    assert bpy.context.scene["northAxis"] == 0.0

    class L:
        def __init__(self):
            self.ops = []

        def operator(self, idname, **kw):
            self.ops.append(idname)

    class Self:
        layout = L()

    for f in (ui.menu_func, ui.menu_func_import, ui.menu_func_gbxml, ui.menu_func_import_gbxml):
        f(Self, None)
    assert len(Self.layout.ops) == 4


def test_panels(clean_scene):
    bpy = clean_scene
    from zonal.blender import ui

    class Ctx:
        object = None

    assert not ui.OBJECT_PT_openstudio.poll(Ctx)
    Ctx.object = _room(bpy)
    assert ui.OBJECT_PT_openstudio.poll(Ctx)

    drawn = []
    from zonal.blender import props

    orig = props.draw_object_fields
    props.draw_object_fields = lambda *a: drawn.append(a)
    try:

        class P:
            layout = None

        ui.OBJECT_PT_openstudio.draw(P, Ctx)
    finally:
        props.draw_object_fields = orig
    assert drawn

    class Sub:
        def __init__(self):
            self.log = []

        def column(self):
            return self

        def row(self, **kw):
            return self

        def prop(self, *a, **kw):
            self.log.append("prop")

        def operator(self, idname, **kw):
            self.log.append(idname)

        def label(self, **kw):
            self.log.append("label")

        def separator(self, **kw):
            pass

        def menu(self, *a, **kw):
            pass

        def panel(self, *a, **kw):
            return self, self

    class SC:
        scene = bpy.context.scene

    for with_north in (False, True):
        if with_north:
            SC.scene["northAxis"] = 1.0
        else:
            SC.scene.pop("northAxis", None)
        p = type("P", (), {"layout": Sub()})
        ui.SCENE_PT_openstudio.draw(p, SC)
        assert ("prop" in p.layout.log) == with_north
        assert ("scene.osm_init_props" in p.layout.log) == (not with_north)


def test_icons(clean_scene, monkeypatch):
    from zonal.blender import icons as ic

    assert ic.theme_variant() in ("dark", "light")

    class T:
        class user_interface:
            class wcol_regular:
                text = (1.0, 1.0, 1.0, 1.0)

    class Prefs:
        themes = [T]

    class Ctx:
        preferences = Prefs

    monkeypatch.setattr(ic.bpy, "context", Ctx)
    assert ic.theme_variant() == "dark"
    T.user_interface.wcol_regular.text = (0.0, 0.0, 0.0, 1.0)
    assert ic.theme_variant() == "light"
    T.user_interface.wcol_regular.text = ()
    assert ic.theme_variant() == "dark"
    Prefs.themes = []
    assert ic.theme_variant() == "dark"
    monkeypatch.undo()

    # real collections / icons
    names = [p.stem for p in (ic.ICON_DIR / ic.theme_variant()).glob("*.png")]
    assert names
    ic._previews.clear()
    coll = ic._collection(ic.theme_variant())
    assert names[0] in coll
    real = ic.icon_id(names[0])
    monkeypatch.setattr(ic, "icon_id", lambda n: 7)
    assert ic.ik(names[0]) == {"icon_value": 7}
    monkeypatch.undo()
    assert real >= 0
    assert ic.icon_id("does_not_exist") == 0
    assert ic.ik("does_not_exist", "EXPORT") == {"icon": "EXPORT"}
    assert ic.ik("does_not_exist") == {"icon": "NONE"}

    # tool_icon: existing .dat vs fallback
    dats = list((ic.ICON_DIR / ic.theme_variant()).glob("*.dat"))
    if dats:
        assert ic.tool_icon(dats[0].stem).endswith(dats[0].stem)
    assert ic.tool_icon("nonexistent") == "ops.mesh.primitive_plane_add_gizmo"

    # exception inside _collection
    monkeypatch.setattr(ic, "_collection", lambda v: (_ for _ in ()).throw(RuntimeError("x")))
    assert ic.icon_id("anything") == 0
    monkeypatch.undo()

    # PanelIcon
    class Lay:
        kw = None

        def label(self, **kw):
            Lay.kw = kw

    class PI(ic.PanelIcon):
        osm_icon = names[0]
        layout = Lay()

    PI().draw_header(None)
    assert "icon_value" in Lay.kw or "icon" in Lay.kw

    # unregister clears (re-created lazily afterwards)
    ic.unregister()
    assert not ic._previews
    assert ic.icon_id(names[0]) >= 0


def test_export_reports_warnings(clean_scene, monkeypatch, tmp_path):
    bpy = clean_scene
    from zonal.blender import ui

    _room(bpy)

    class Rep:
        warnings = ["w1", "w2"]

        def summary(self):
            return "ok"

    monkeypatch.setattr(ui, "run_build", lambda *a: Rep())
    assert bpy.ops.export_scene.openstudio_osm(filepath=str(tmp_path / "w.osm")) == {"FINISHED"}


def test_warning_report_and_preferences(clean_scene, monkeypatch):
    bpy = clean_scene
    from zonal.blender import ui

    class Op:
        def __init__(self):
            self.out = []

        def report(self, kind, msg):
            self.out.append((kind, msg))

    op = Op()
    ui.report_messages(op, [f"w{i}" for i in range(13)])
    assert ui.LAST_MESSAGES[-1] == "w12" and len(ui.LAST_MESSAGES) == 13
    assert len(op.out) == 11 and "3 more" in op.out[-1][1]  # ten warnings + the "more" line
    from types import SimpleNamespace

    prefs = SimpleNamespace(show_warnings=False, unit_system="SI", disable_user_scripts=False)
    monkeypatch.setattr(ui, "prefs", lambda: prefs)
    op = Op()
    ui.report_messages(op, ["x"])
    assert op.out == [] and ui.LAST_MESSAGES == ["x"]  # silent, but still available for the dialog
    prefs.show_warnings = True
    prefs.unit_system = "IP"
    ui._on_unit_system(prefs, bpy.context)
    assert bpy.context.scene.unit_settings.system == "IMPERIAL"
    prefs.unit_system = "SI"
    ui._on_unit_system(prefs, bpy.context)
    assert bpy.context.scene.unit_settings.system == "METRIC"
    assert bpy.ops.scene.osm_show_warnings.poll()
    prefs.disable_user_scripts = True
    with pytest.raises(RuntimeError, match="disabled"):
        bpy.ops.scene.osm_run_script()
