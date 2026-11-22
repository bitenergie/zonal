"""Headless check of the Surface Search and OpenStudio Inspector panels:

blender -b --factory-startup --python tests/blender_inspector_smoke.py -- <out.osm>

Dev only: borrows the uv venv's site-packages for `openstudio` (Blender 5.x = same CPython ABI).
"""

import sys
from pathlib import Path

import bpy

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
sys.path.append(str(root / ".venv" / "Lib" / "site-packages"))
out = Path(sys.argv[sys.argv.index("--") + 1])

import zonal

bpy.ops.wm.read_factory_settings(use_empty=True)
zonal.register()
try:
    scene = bpy.context.scene
    col = bpy.data.collections.new("Office")
    scene.collection.children.link(col)
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
    cube = bpy.context.object
    cube.name = "Room"
    for c in list(cube.users_collection):
        c.objects.unlink(cube)
    col.objects.link(cube)
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    bpy.ops.object.osm_separate_faces()
    surfaces = [o for o in scene.objects if o.type == "MESH"]
    assert len(surfaces) == 6

    # --- Surface Search --------------------------------------------------------------
    s = scene.osm_search
    s.type = "Floor"
    assert bpy.ops.object.osm_search_model() == {"FINISHED"}
    sel = [o for o in scene.objects if o.select_get()]
    assert len(sel) == 1 and sel[0]["surfaceType"] == "Floor" and s.count == 1, (len(sel), s.count)

    s.type, s.exclude_horizontal = "ANY", True
    bpy.ops.object.osm_search_model()
    assert s.count == 4  # the four walls
    s.az_from, s.az_to = 135.0, 225.0  # south (normal -Y)
    bpy.ops.object.osm_search_model()
    assert s.count == 1, s.count
    s.az_from, s.az_to, s.exclude_horizontal = 0.0, 360.0, False

    scene["northAxis"] = 90.0  # south wall now faces azimuth 180 - 90 = 90
    s.az_from, s.az_to = 45.0, 135.0
    bpy.ops.object.osm_search_model()
    assert s.count == 1
    s.az_from, s.az_to = 0.0, 360.0
    del scene["northAxis"]

    s.name = "nomatch"
    bpy.ops.object.osm_search_model()
    assert s.count == 0 and not [o for o in scene.objects if o.select_get()]
    s.name = ""
    s.cls = "SubSurface"
    bpy.ops.object.osm_search_model()
    assert s.count == 0
    s.cls = "ANY"
    s.use_min_vertices, s.min_vertices = True, 4
    bpy.ops.object.osm_search_model()
    assert s.count == 0  # quads: not MORE than 4
    s.use_min_vertices = False

    # --- Inspector: object-backed type ---------------------------------------------------
    insp = scene.osm_inspector
    insp.type = "Surface"
    from zonal.blender.inspector import _type_items, _type_items_state

    _type_items_state["items"] = []  # drop the cached labels so the counts are recomputed
    labels = {i[0]: i[1] for i in _type_items(None, bpy.context) if i}
    assert labels["Surface"] == "OS:Surface (6)" and labels["ThermalZone"] == "OS:ThermalZone (0)", labels
    assert insp.object == surfaces[0].name or insp.object in {o.name for o in surfaces}
    insp.object = surfaces[1].name
    assert bpy.context.view_layer.objects.active == surfaces[1]
    insp.name = "Renamed Surface"
    assert insp.object == "Renamed Surface" and bpy.data.objects.get("Renamed Surface") is not None

    # --- Inspector: store-backed types -----------------------------------------------------
    insp.type = "ThermalZone"
    assert bpy.ops.scene.osm_model_add("EXEC_DEFAULT", name="Zone A") == {"FINISHED"}
    item = next(i for i in scene.osm_model if i.type == "ThermalZone")
    item.multiplier = 4
    item.insideConvection = "TARP"
    assert insp.object == "Zone A" and "Zone A" in [i.name for i in scene.osm_library]

    insp.type = "BuildingStory"
    bpy.ops.scene.osm_model_add("EXEC_DEFAULT", name="Story A")
    story = next(i for i in scene.osm_model if i.type == "BuildingStory")
    story.nominalZCoordinate = 2.5

    insp.type = "Space"
    assert insp.object == "Office"
    space = next(i for i in scene.osm_model if i.type == "Space" and i.name == "Office")
    space.thermalZone, space.buildingStory = "Zone A", "Story A"
    space.xOrigin = 5.0
    space.volume_hard, space.volume = True, 120.0
    insp.type = "Building"
    assert insp.object == "Building"
    b = next(i for i in scene.osm_model if i.type == "Building")
    b.standardsTemplate = "90.1-2019"

    insp.type = "WeatherFile"
    assert insp.object == "Weather File"
    wf = next(i for i in scene.osm_model if i.type == "WeatherFile")
    from zonal.blender.inspector import _differs, epw_data

    epw_file = out.with_name("smoke.epw")
    epw_file.write_text("LOCATION,Denver Intl Ap,CO,USA,TMY3,725650,39.83,-104.65,-7.0,1650.0\n", encoding="utf-8")
    wf.epwPath = str(epw_file)  # loading fills the fields from the file
    assert wf.city == "Denver Intl Ap" and abs(wf.latitude - 39.83) < 1e-4 and wf.wmoNumber == "725650"
    assert not any(_differs(wf, k, v) for k, v in epw_data(wf).items())
    wf.city = "Zurich"  # manual edit -> differs from the file -> reset button
    assert _differs(wf, "city", epw_data(wf)["city"])
    assert bpy.ops.scene.osm_weather_reload(key="city") == {"FINISHED"} and wf.city == "Denver Intl Ap"
    wf.city, wf.latitude = "Zurich", 47.4
    assert bpy.ops.scene.osm_weather_reload(key="") == {"FINISHED"} and abs(wf.latitude - 39.83) < 1e-4
    wf.city, wf.latitude = "Zurich", 47.4  # kept as manual overrides for the export below
    assert bpy.ops.scene.osm_weather_browse.get_rna_type().properties["filter_glob"].default == "*.epw"

    insp.type = "DefaultScheduleSet"
    assert bpy.ops.scene.osm_model_add("EXEC_DEFAULT", name="My Schedules") == {"FINISHED"}
    assert bpy.ops.scene.osm_load_template(use_default=True) == {"FINISHED"}
    from zonal.blender.inspector import ref_names

    assert "Medium Office Lighting Schedule" in ref_names(scene, "schedule")
    ss = next(i for i in scene.osm_model if i.type == "DefaultScheduleSet")
    ss.lighting = "Medium Office Lighting Schedule"

    # names that only exist on surfaces / in the library are listed too, and get an item when picked
    from zonal.blender.inspector import object_names

    surfaces[2]["buildingStoryName"] = "Building Story"  # custom property only
    from zonal.blender.props import library_add

    library_add(scene, "story", "Building Story 1")  # library only
    assert {"Story A", "Building Story", "Building Story 1"} <= set(object_names(scene, "BuildingStory"))
    insp.type = "BuildingStory"
    insp.object = "Building Story"
    assert any(i.type == "BuildingStory" and i.name == "Building Story" for i in scene.osm_model)
    insp.name = "Story Renamed"  # renames the surface property and the library too
    assert surfaces[2]["buildingStoryName"] == "Story Renamed" and insp.object == "Story Renamed"
    assert "Building Story" not in object_names(scene, "BuildingStory")

    # remove / recreate
    insp.type = "BuildingStory"
    bpy.ops.scene.osm_model_add("EXEC_DEFAULT", name="Tmp")
    assert bpy.ops.scene.osm_model_remove() == {"FINISHED"}
    assert "Tmp" not in [i.name for i in scene.osm_model]

    import json

    from zonal.blender.inspector import export_json

    payload = json.loads(export_json(scene))
    kinds = {(o["type"], o["name"]) for o in payload["objects"]}
    assert {("ThermalZone", "Zone A"), ("Space", "Office"), ("Building", "Building")} <= kinds, kinds

    # --- export and verify in OpenStudio ---------------------------------------------------
    assert bpy.ops.export_scene.openstudio_osm(filepath=str(out)) == {"FINISHED"}
    assert "osmModel" not in scene
    import openstudio

    m = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(out))).get()
    z = m.getThermalZoneByName("Zone A").get()
    assert z.multiplier() == 4 and z.zoneInsideConvectionAlgorithm().get() == "TARP"
    sp = m.getSpaceByName("Office").get()
    assert sp.thermalZone().get().nameString() == "Zone A" and sp.buildingStory().get().nameString() == "Story A"
    assert sp.xOrigin() == 5.0 and sp.volume() == 120.0
    assert m.getBuildingStoryByName("Story A").get().nominalZCoordinate().get() == 2.5
    assert m.getBuilding().standardsTemplate().get() == "90.1-2019"
    assert len(m.getObjectsByType("OS:WeatherFile")) == 1 and m.getWeatherFile().city() == "Zurich"
    ssm = m.getDefaultScheduleSetByName("My Schedules").get()
    assert ssm.lightingSchedule().get().nameString() == "Medium Office Lighting Schedule"
    # geometry did not move although the space origin changed
    floor = next(x for x in m.getSurfaces() if x.surfaceType() == "Floor")
    xs = sorted({round((sp.transformation() * v).x(), 3) for v in floor.vertices()})
    assert xs == [-1.0, 1.0], xs
    print("blender_inspector_smoke OK")
finally:
    zonal.unregister()
