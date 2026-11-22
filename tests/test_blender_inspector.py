"""In-process tests of blender_inspector (Inspector store, sync, operators, panel)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from conftest import requires_bpy

pytestmark = requires_bpy

EPW = "LOCATION,Denver Intl Ap,CO,USA,TMY3,725650,39.83,-104.65,-7.0,1650.0\n"


def _setup(bpy):
    """A cube split into six surfaces inside the collection 'Office'."""
    from zonal.blender import inspector as bi

    bi._type_items_state.update(items=[], scene=None, n=-1)
    scene = bpy.context.scene
    col = bpy.data.collections.new("Office")
    scene.collection.children.link(col)
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
    cube = bpy.context.object
    for c in list(cube.users_collection):
        c.objects.unlink(cube)
    col.objects.link(cube)
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    bpy.ops.object.osm_separate_faces()
    surfaces = sorted((o for o in scene.objects if o.type == "MESH"), key=lambda o: o.name)
    return scene, surfaces


def _ctx(bpy, **kw):
    return SimpleNamespace(scene=bpy.context.scene, view_layer=bpy.context.view_layer, window_manager=MagicMock(), **kw)


def test_data_access(clean_scene):
    bpy = clean_scene
    from zonal.blender import inspector as bi

    scene, _surfaces = _setup(bpy)
    loose = bpy.data.collections.new("OpenStudio - Loose Geometry")
    scene.collection.children.link(loose)
    assert bi.space_names(scene) == ["Office"]

    assert bi.ensure_item(scene, "ThermalZone", "") is None
    bi.ensure_item(scene, "ThermalZone", "Z")
    assert bi.ensure_item(scene, "ThermalZone", "Z").name == "Z" and len(scene.osm_model) == 1
    assert [i.name for i in bi.store_items(scene, "ThermalZone")] == ["Z"]
    assert bi.get_item(scene, "ThermalZone", "nope") is None

    assert len(bi.mesh_objects_of(scene, "Surface")) == 6
    empty_mesh = bpy.data.objects.new("nomesh", bpy.data.meshes.new("m"))
    scene.collection.objects.link(empty_mesh)
    assert empty_mesh not in bi.mesh_objects_of(scene, "Surface")

    assert bi.object_names(scene, "Bogus") == []
    assert len(bi.object_names(scene, "Surface")) == 6
    assert bi.object_names(scene, "Building") == ["Building"]
    assert bi.object_names(scene, "Space") == ["Office"]
    assert bi.object_names(scene, "ThermalZone") == ["Z"]
    assert bi.ref_names(scene, "zone") == ["Z"]


def test_type_counts_and_items(clean_scene):
    bpy = clean_scene
    from zonal.blender import inspector as bi
    from zonal.blender.props import library_add

    scene, surfaces = _setup(bpy)
    surfaces[0]["thermalZoneName"] = "ZoneS"
    surfaces[0]["buildingStoryName"] = 5  # ignored
    library_add(scene, "zone", "ZoneL")
    bi.ensure_item(scene, "ThermalZone", "ZoneI")
    bi.ensure_item(scene, "Space", "Office")
    empty = bpy.data.objects.new("e", None)
    scene.collection.objects.link(empty)
    nomesh = bpy.data.objects.new("nm", bpy.data.meshes.new("m2"))
    scene.collection.objects.link(nomesh)
    counts = bi._type_counts(scene)
    assert counts["Surface"] == 6 and counts["ThermalZone"] == 3 and counts["Space"] == 1
    assert counts["Building"] == 1

    ctx = SimpleNamespace(scene=scene)
    items = bi._type_items(None, ctx)
    assert None in items and any(i and i[1].startswith("OS:Surface") for i in items)
    assert bi._type_items(None, ctx) is items  # cached
    bi._type_items_state["time"] = 0.0
    assert bi._type_items(None, ctx) is not None  # expired


def test_export_json(clean_scene, tmp_path):
    bpy = clean_scene
    from zonal.blender import inspector as bi

    scene, _ = _setup(bpy)
    assert bi.export_json(scene) == ""
    z = bi.ensure_item(scene, "ThermalZone", "Z")
    z.multiplier = 3
    bi.ensure_item(scene, "Space", "Ghost")  # not an existing space: skipped
    sp = bi.ensure_item(scene, "Space", "Office")
    sp.volume_hard, sp.volume = True, 12.5
    bi.ensure_item(scene, "Building", "Building")
    wf = bi.ensure_item(scene, "WeatherFile", "Weather File")
    wf.epwPath = str(tmp_path / "x.epw")  # unreadable: update hook finds nothing
    bad = scene.osm_model.add()
    bad.type, bad.name = "Unknown", "u"
    payload = json.loads(bi.export_json(scene))
    names = {(o["type"], o["name"]) for o in payload["objects"]}
    assert ("Space", "Ghost") not in names and ("Unknown", "u") not in names
    assert ("ThermalZone", "Z") in names and ("Space", "Office") in names
    office = next(o for o in payload["objects"] if o["name"] == "Office")
    assert office.get("fields")


def test_epw(clean_scene, tmp_path):
    bpy = clean_scene
    from zonal.blender import inspector as bi

    scene, _ = _setup(bpy)
    wf = bi.ensure_item(scene, "WeatherFile", "Weather File")
    assert bi.epw_data(wf) is None
    assert bi.apply_epw(wf) is False
    f = tmp_path / "w.epw"
    f.write_text(EPW, encoding="utf-8")
    wf.epwPath = str(f)  # fills fields through _on_epw
    assert wf.city == "Denver Intl Ap"
    assert not bi._differs(wf, "city", "Denver Intl Ap")
    assert bi._differs(wf, "city", "Other")
    assert not bi._differs(wf, "latitude", 39.83)
    assert bi._differs(wf, "latitude", 10.0)
    wf.city, wf.latitude = "Z", 1.0
    assert bi.apply_epw(wf, only="city") and wf.city == "Denver Intl Ap" and wf.latitude == 1.0
    assert bi.apply_epw(wf) and abs(wf.latitude - 39.83) < 1e-4


def test_sync_and_handlers(clean_scene):
    bpy = clean_scene
    from zonal.blender import inspector as bi

    scene, surfaces = _setup(bpy)
    insp = scene.osm_inspector
    ctx = _ctx(bpy)

    # guards
    bi.sync_from_object(scene, None)
    nomesh = bpy.data.objects.new("nm", bpy.data.meshes.new("m2"))
    scene.collection.objects.link(nomesh)
    bi.sync_from_object(scene, nomesh)
    insp["type"] = 9999  # unknown enum index -> TYPES.get returns None
    bi.sync_from_object(scene, surfaces[0])
    insp.type = "Surface"

    # object backed
    bi.sync_from_object(scene, surfaces[2])
    assert insp.object == surfaces[2].name
    bi.sync_from_object(scene, surfaces[2])  # unchanged: early return
    insp.type = "ThermalZone"
    # store backed without a name: returns, with a name: switches object
    bi.sync_from_object(scene, surfaces[0])
    surfaces[0]["thermalZoneName"] = "ZoneQ"
    bi.sync_from_object(scene, surfaces[0])
    assert insp.object == "ZoneQ"
    insp.type = "Space"
    bi.sync_from_object(scene, surfaces[0])
    assert insp.object == "Office"
    surfaces[0]["spaceName"] = "Explicit"  # a stale property never moves a surface out of its collection
    assert bi._name_for_type(surfaces[0], "Space") == "Office"
    assert bi._name_for_type(surfaces[0], "Nope") == ""
    assert bi._name_for_type(surfaces[0], "Surface") == surfaces[0].name
    assert bi._name_for_type(surfaces[0], "ThermalZone") == "ZoneQ"
    assert bi._name_for_type(surfaces[0], "Building") == ""

    # depsgraph handler
    bi._last_active = ""
    for o in scene.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = None
    bi._on_depsgraph(scene)  # no active
    insp.type = "Surface"
    for o in scene.objects:
        o.select_set(False)
    surfaces[3].select_set(True)
    bpy.context.view_layer.objects.active = surfaces[3]
    bi._on_depsgraph(scene)
    assert bi._last_active == surfaces[3].name
    assert insp.object == surfaces[3].name
    bi._on_depsgraph(scene)  # same active
    bi._last_active = ""
    bi._on_depsgraph(None)  # AttributeError swallowed

    # _select_object
    bi._select_object(ctx, "missing")
    surfaces[1].hide_set(True)
    bi._select_object(ctx, surfaces[1].name)
    assert bpy.context.view_layer.objects.active == surfaces[1] and not surfaces[1].select_get()
    surfaces[1].hide_set(False)
    bi._select_object(ctx, surfaces[1].name)
    assert surfaces[1].select_get()

    # update callbacks
    bi._syncing = True
    bi._on_type(insp, ctx)
    bi._syncing = False
    insp.type = "Surface"
    insp.type = "BuildingUnit"  # no names -> object ""
    assert insp.object == ""
    bi._on_object(insp, ctx)
    insp.object = "NewUnit"  # store: creates item
    assert bi.get_item(scene, "BuildingUnit", "NewUnit") is not None
    insp["type"] = 9999
    bi._on_object(insp, ctx)


def test_rename(clean_scene):
    bpy = clean_scene
    from zonal.blender import inspector as bi
    from zonal.blender.props import library_add

    scene, surfaces = _setup(bpy)
    insp = scene.osm_inspector
    _ctx(bpy)
    assert insp.name == insp.object

    # unknown / empty / same
    insp["type"] = 9999
    bi._set_name(insp, "x")
    insp.type = "Surface"
    insp.object = ""
    bi._set_name(insp, "x")
    insp.object = surfaces[0].name
    bi._set_name(insp, surfaces[0].name)
    bi._set_name(insp, "")
    insp.name = "Renamed"
    assert insp.object == "Renamed"
    insp["object"] = "ghost"
    bi._set_name(insp, "other")  # object missing: nothing happens
    assert bpy.data.objects.get("other") is None

    # singleton: ignored
    insp.type = "Building"
    insp.name = "Foo"
    assert insp.object == "Building"

    # Space: collection renamed + item
    insp.type = "Space"
    insp.object = "Office"
    bi.ensure_item(scene, "Space", "Office")
    insp.name = "Lab"
    assert insp.object == "Lab" and bpy.data.collections.get("Lab") is not None
    assert bi.get_item(scene, "Space", "Lab") is not None
    insp["object"] = "NoSuchCollection"
    bi._set_name(insp, "Whatever")  # collection missing, item missing
    assert insp.object == "Whatever"

    # category-backed: surfaces + library renamed
    insp.type = "ThermalZone"
    surfaces[1]["thermalZoneName"] = "Zold"
    library_add(scene, "zone", "Zold")
    insp.object = "Zold"
    insp.name = "Znew"
    assert surfaces[1]["thermalZoneName"] == "Znew" and insp.object == "Znew"
    assert "Znew" in [i.name for i in scene.osm_library]


def test_operators(clean_scene, tmp_path):
    bpy = clean_scene
    from zonal.blender import inspector as bi

    scene, _ = _setup(bpy)
    insp = scene.osm_inspector
    insp.type = "ThermalZone"
    assert bpy.ops.scene.osm_model_add("EXEC_DEFAULT", name="ZA") == {"FINISHED"}
    assert bpy.ops.scene.osm_model_add("EXEC_DEFAULT", name="ZA") == {"CANCELLED"}
    assert bpy.ops.scene.osm_model_add("EXEC_DEFAULT", name="") == {"CANCELLED"}
    assert bpy.ops.scene.osm_model_remove() == {"FINISHED"}
    # remove with no other names -> object ""
    bi.ensure_item(scene, "ThermalZone", "ZB")
    insp.object = "ZB"
    bpy.ops.scene.osm_model_remove()
    # poll false for object-backed type
    insp.type = "Surface"
    assert not bi.SCENE_OT_osm_model_add.poll(_ctx(bpy))
    assert not bi.SCENE_OT_osm_model_remove.poll(_ctx(bpy))
    insp["type"] = 9999
    assert not bi.SCENE_OT_osm_model_remove.poll(_ctx(bpy))

    # remove NOT_CREATABLE recreates defaults
    bi._type_items_state["items"] = []
    insp.type = "Building"
    assert bpy.ops.scene.osm_model_remove() == {"FINISHED"}
    assert bi.get_item(scene, "Building", "Building") is not None

    # invoke of add
    insp.type = "ThermalZone"
    wm = MagicMock()
    me = SimpleNamespace(name="")
    bi.SCENE_OT_osm_model_add.invoke(me, SimpleNamespace(scene=scene, window_manager=wm), None)
    assert me.name and wm.invoke_props_dialog.called
    insp.type = "Space"
    bi.SCENE_OT_osm_model_add.invoke(me, SimpleNamespace(scene=scene, window_manager=wm), None)

    # weather operators
    f = tmp_path / "w.epw"
    f.write_text(EPW, encoding="utf-8")
    bi._type_items_state["items"] = []
    me = SimpleNamespace(filepath="")
    ctx = SimpleNamespace(scene=scene, window_manager=wm)
    assert bi.SCENE_OT_osm_weather_browse.invoke(me, ctx, None) == {"RUNNING_MODAL"}
    assert me.filepath == ""
    wf = bi._weather_item(scene)
    wf.epwPath = str(f)
    me = SimpleNamespace(filepath="")
    bi.SCENE_OT_osm_weather_browse.invoke(me, ctx, None)
    assert me.filepath == str(f)
    reports = []
    me = SimpleNamespace(filepath=str(f), report=lambda *a: reports.append(a))
    assert bi.SCENE_OT_osm_weather_browse.execute(me, ctx) == {"FINISHED"} and not reports
    me.filepath = str(tmp_path / "none.epw")
    bi.SCENE_OT_osm_weather_browse.execute(me, ctx)
    assert reports and reports[0][0] == {"WARNING"}

    wf.epwPath = str(f)
    wf.city = "Zurich"
    assert bpy.ops.scene.osm_weather_reload(key="city") == {"FINISHED"}
    wf.epwPath = ""
    me = SimpleNamespace(key="", report=lambda *a: reports.append(a))
    assert bi.SCENE_OT_osm_weather_reload.execute(me, ctx) == {"CANCELLED"}


def _fake_layout():
    lay = MagicMock()
    lay.panel.return_value = (MagicMock(), MagicMock())
    return lay


def test_panel_draw(clean_scene, tmp_path):
    bpy = clean_scene
    from zonal.blender import inspector as bi
    from zonal.core import inspector_schema as S

    scene, surfaces = _setup(bpy)
    insp = scene.osm_inspector
    ctx = _ctx(bpy)
    panels = (bi.VIEW3D_PT_osm_inspector, bi.VIEW3D_PT_osm_inspector_props)

    def draw():
        for panel in panels:
            if not hasattr(panel, "poll") or panel.poll(ctx):
                for pick in ("HIERARCHY", "TYPE"):
                    insp.picker = pick
                    panel.draw(SimpleNamespace(layout=_fake_layout()), ctx)

    def settype(t):
        bi._type_items_state["items"] = []
        insp.type = t

    # no type selected yet / unknown type
    insp["type"] = 9999
    draw()
    settype("ThermalZone")  # no objects
    draw()
    bi.ensure_item(scene, "ThermalZone", "Z1")
    bi.ensure_item(scene, "ThermalZone", "Z2")
    insp["object"] = ""
    draw()  # objects exist but none picked
    # picked but no item -> Create
    insp["object"] = "Z3"
    draw()
    insp["object"] = "Z1"
    draw()

    # object backed
    settype("Surface")
    insp.object = surfaces[0].name
    draw()
    surfaces[0]["outsideBoundaryCondition"] = "Surface"
    draw()
    insp["object"] = "missing"
    draw()
    settype("SubSurface")
    insp["object"] = "x"
    draw()
    for t, d in S.OBJECT_FIELDS.items():
        assert d
        settype(t)
        insp["object"] = surfaces[0].name
        draw()

    # all store types, with and without items
    for tname, t in S.TYPES.items():
        if t.backing != "store":
            continue
        settype(tname)
        names = bi.object_names(scene, tname)
        insp["object"] = names[0] if names else "Item"
        bi.ensure_item(scene, tname, insp.object)
        draw()
    # Space + northAxis present / absent
    settype("Building")
    draw()
    scene["northAxis"] = 10.0
    draw()
    # weather: no path, unreadable path, readable path (matches + differs)
    settype("WeatherFile")
    item = bi._weather_item(scene)
    draw()
    item.epwPath = str(tmp_path / "none.epw")
    draw()
    f = tmp_path / "w.epw"
    f.write_text(EPW, encoding="utf-8")
    item.epwPath = str(f)
    draw()
    item.city = "Zurich"
    draw()
    settype("Space")
    draw()


def test_search_callbacks_and_exception_path(clean_scene):
    bpy = clean_scene
    from zonal.blender import inspector as bi

    scene, surfaces = _setup(bpy)
    insp = scene.osm_inspector
    ctx = SimpleNamespace(scene=scene)
    assert bi._ref_search("zone")(insp, ctx, "") == []
    insp.type = "Surface"
    assert len(bi._object_search(insp, ctx, "")) == 6

    # active object of another surface class: the type follows it
    surfaces[1]["surfaceType"] = "FixedWindow"
    bi._syncing = False
    bi.sync_from_object(scene, surfaces[1])
    assert insp.type == "SubSurface"

    # a failing sync inside the handler is swallowed
    bi._last_active = ""
    for o in scene.objects:
        o.select_set(False)
    surfaces[2].select_set(True)
    bpy.context.view_layer.objects.active = surfaces[2]
    bi._on_depsgraph(SimpleNamespace(osm_inspector=None))


def test_template_objects_show_their_properties(clean_scene):
    """A construction / material / schedule set picked from the template must not be an empty item."""
    from pathlib import Path

    import pytest

    pytest.importorskip("openstudio")
    import openstudio

    from zonal.blender import inspector as bi

    bpy = clean_scene
    scene, _ = _setup(bpy)
    template = (
        Path(__file__).resolve().parent.parent / "src" / "zonal" / "templates" / "MinimalTemplate.osm"
    )
    scene.osm_template_path = str(template)
    m = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(template))).get()

    con = m.getConstructions()[0]
    item = bi.ensure_item(scene, "Construction", con.nameString())
    assert item.layer1 == con.layers()[0].nameString()
    if len(con.layers()) > 1:
        assert item.layer2 == con.layers()[1].nameString()
    payload = json.loads(bi.export_json(scene))
    assert payload["objects"][0]["fields"]["layer1"] == con.layers()[0].nameString()

    mat = m.getStandardOpaqueMaterials()[0]
    mi = bi.ensure_item(scene, "StandardOpaqueMaterial", mat.nameString())
    assert abs(mi.thickness - mat.thickness()) < 1e-4 and abs(mi.conductivity - mat.conductivity()) < 1e-4

    ss = m.getDefaultScheduleSets()
    if ss:
        si = bi.ensure_item(scene, "DefaultScheduleSet", ss[0].nameString())
        lighting = ss[0].lightingSchedule()
        assert si.lighting == (lighting.get().nameString() if lighting.is_initialized() else "")

    # an object that is not in the template stays blank; no template -> blank
    assert bi.ensure_item(scene, "Construction", "Brand New").layer1 == ""
    scene.osm_template_path = ""
    assert bi.ensure_item(scene, "Construction", con.nameString() + " x").layer1 == ""


def test_zone_hvac_proxies_apply_per_object_zone(scene_with_two_zones=None):
    import bpy

    from zonal.blender import inspector as bi
    from zonal.blender import props

    sc = bpy.context.scene
    obs = []
    for i, z in enumerate(("ZA", "ZB")):
        ob = bpy.data.objects.new(f"o{i}", bpy.data.meshes.new(f"m{i}"))
        col = bpy.data.collections.new(f"Sp{i}")
        sc.collection.children.link(col)
        col.objects.link(ob)
        bi.ensure_item(sc, "Space", col.name).thermalZone = z
        obs.append(ob)
    for ob in obs:
        setattr(ob, props.zone_prop_name("heatingSetpoint"), "Sched")
        setattr(ob, props.zone_prop_name("idealAirLoads"), True)
    for z in ("ZA", "ZB"):
        item = bi.get_item(sc, "ThermalZone", z)
        assert item.heatingSetpoint == "Sched" and item.idealAirLoads
    assert getattr(obs[0], props.zone_prop_name("heatingSetpoint")) == "Sched"


def test_zone_add_and_clear_operators_apply_to_selection():
    import bpy

    from zonal.blender import inspector as bi
    from zonal.blender import props

    sc = bpy.context.scene
    obs = []
    for i, z in enumerate(("ZC", "ZD")):
        ob = bpy.data.objects.new(f"p{i}", bpy.data.meshes.new(f"pm{i}"))
        col = bpy.data.collections.new(f"SpP{i}")
        sc.collection.children.link(col)
        col.objects.link(ob)
        bi.ensure_item(sc, "Space", col.name).thermalZone = z
        obs.append(ob)
    bpy.ops.object.select_all(action="DESELECT")
    for ob in obs:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = obs[0]
    bpy.ops.object.osm_zone_add(key="coolingSetpoint", name="Cool 24")
    assert bi.get_item(sc, "ScheduleConstant", "Cool 24") is not None
    for z in ("ZC", "ZD"):
        assert bi.get_item(sc, "ThermalZone", z).coolingSetpoint == "Cool 24"
    bpy.ops.object.osm_zone_clear(key="coolingSetpoint")
    for z in ("ZC", "ZD"):
        assert bi.get_item(sc, "ThermalZone", z).coolingSetpoint == ""
    assert props.zone_prop_name("coolingSetpoint") == "osm_zone_coolingSetpoint"


def test_pick_object_operator_registered_and_drawn_for_surface_fields():
    import bpy

    from zonal.blender import props

    assert hasattr(bpy.ops.object, "osm_pick_object")
    assert set(props.PICKABLE_FIELDS) == {"surfaceName", "outsideBoundaryConditionObjectName"}
