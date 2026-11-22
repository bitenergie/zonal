"""Headless check of properties/library/template, surface matching and loose-geometry projection:

blender -b --factory-startup --python tests/blender_features_smoke.py -- <out.osm>

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

    # two adjacent rooms, one collection (= space) each, faces separated
    for name, x in (("RoomA", 1.0), ("RoomB", 3.0)):
        col = bpy.data.collections.new(name)
        scene.collection.children.link(col)
        bpy.ops.mesh.primitive_cube_add(size=2, location=(x, 0, 1))
        cube = bpy.context.object
        cube.name = name
        for c in list(cube.users_collection):
            c.objects.unlink(cube)
        col.objects.link(cube)
        bpy.ops.object.select_all(action="DESELECT")
        cube.select_set(True)
        bpy.ops.object.osm_separate_faces()

    # --- properties proxies + library + template
    ob = next(o for o in scene.objects if o.get("surfaceType") == "Wall")
    ob.osm_thermalZoneName = "Zone 1"
    assert ob["thermalZoneName"] == "Zone 1"
    ob.osm_thermalZoneName = ""
    assert "thermalZoneName" not in ob
    ob.osm_outsideBoundaryCondition = "Ground"
    assert ob["outsideBoundaryCondition"] == "Ground" and ob.osm_outsideBoundaryCondition == "Ground"
    ob.osm_outsideBoundaryCondition = "DEFAULT"
    assert "outsideBoundaryCondition" not in ob
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    assert bpy.ops.object.osm_lib_add("EXEC_DEFAULT", category="zone", name="Zone 9") == {"FINISHED"}
    assert ob["thermalZoneName"] == "Zone 9" and [i.name for i in scene.osm_library] == ["Zone 9"]
    assert bpy.ops.scene.osm_load_template("EXEC_DEFAULT", use_default=True) == {"FINISHED"}
    cats = {i.category for i in scene.osm_library}
    assert {"construction", "spacetype", "cset", "zone"} <= cats, cats
    assert scene.osm_template_path.endswith("MinimalTemplate.osm")
    cname = next(i.name for i in scene.osm_library if i.category == "construction")
    ob.osm_constructionName = cname
    ob.osm_thermalZoneName = ""

    # --- surface matching
    bpy.ops.object.select_all(action="DESELECT")
    assert bpy.ops.object.osm_match_surfaces(mode="MATCH", scope="ALL") == {"FINISHED"}
    matched = [o for o in scene.objects if o.get("outsideBoundaryCondition") == "Surface"]
    assert len(matched) == 2, [o.name for o in matched]
    a, b = matched
    assert a["outsideBoundaryConditionObjectName"] == b.name and b["outsideBoundaryConditionObjectName"] == a.name
    assert bpy.ops.object.osm_match_surfaces(mode="UNMATCH", scope="ALL") == {"FINISHED"}
    assert not [o for o in scene.objects if "outsideBoundaryCondition" in o]
    bpy.ops.object.osm_match_surfaces(mode="MATCH", scope="ALL")

    # --- loose geometry -> window on RoomA's south wall (y = -1)
    me = bpy.data.meshes.new("aid")
    me.from_pydata([(0.5, -1, 0.5), (1.5, -1, 0.5), (1.5, -1, 1.5), (0.5, -1, 1.5)], [], [[0, 1, 2, 3]])
    aid = bpy.data.objects.new("aid", me)
    scene.collection.objects.link(aid)
    bpy.ops.object.select_all(action="DESELECT")
    aid.select_set(True)
    bpy.ops.object.osm_to_loose()
    assert [c.name for c in aid.users_collection] == ["OpenStudio - Loose Geometry"]
    assert bpy.ops.object.osm_project_loose(scope="ALL", surface_type="FixedWindow") == {"FINISHED"}
    wins = [o for o in scene.objects if o.get("surfaceType") == "FixedWindow"]
    assert len(wins) == 1 and wins[0]["surfaceName"] in bpy.data.objects
    assert [c.name for c in wins[0].users_collection] == ["RoomA"]
    bpy.ops.object.osm_project_loose(scope="ALL", surface_type="FixedWindow")  # re-run replaces, no duplicates
    assert len([o for o in scene.objects if o.get("surfaceType") == "FixedWindow"]) == 1

    # --- export with template (python bindings) and verify with openstudio
    assert bpy.ops.export_scene.openstudio_osm(filepath=str(out)) == {"FINISHED"}
    import openstudio

    m = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(out))).get()
    assert len(m.getSpaces()) == 2
    assert len(m.getSurfaces()) == 12, len(m.getSurfaces())  # loose geometry not exported
    assert len(m.getSubSurfaces()) == 1
    assert len([s for s in m.getSurfaces() if s.adjacentSurface().is_initialized()]) == 2
    assert len(m.getConstructions()) >= 11  # template constructions, no placeholders only
    assert m.getConstructionByName(cname).is_initialized()
    assert len(m.getSpaceTypes()) == 1
    print("blender_features_smoke OK")
finally:
    zonal.unregister()
