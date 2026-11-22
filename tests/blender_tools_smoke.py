"""Headless check of the modeling tools:
blender -b --factory-startup --python tests/blender_tools_smoke.py
"""

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import zonal

bpy.ops.wm.read_factory_settings(use_empty=True)
zonal.register()
try:
    col = bpy.data.collections.new("Office")
    bpy.context.scene.collection.children.link(col)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    for c in cube.users_collection:
        c.objects.unlink(cube)
    col.objects.link(cube)
    cube["thermalZoneName"] = "Z1"
    cube.select_set(True)

    assert bpy.ops.object.osm_separate_faces() == {"FINISHED"}
    objs = [o for o in bpy.data.objects if o.type == "MESH"]
    assert len(objs) == 6, len(objs)
    types = sorted(o["surfaceType"] for o in objs)
    assert types == ["Floor"] + ["RoofCeiling"] + ["Wall"] * 4, types
    assert all(o["thermalZoneName"] == "Z1" and col in o.users_collection for o in objs)
    assert all(len(o.data.polygons) == 1 for o in objs)

    assert bpy.ops.object.osm_lib_add("EXEC_DEFAULT", category="space", name="Space") == {"FINISHED"}
    assert "Space" in bpy.data.collections
    assert bpy.ops.view3d.osm_render_mode(mode="SURFACE_TYPE") == {"FINISHED"}
    assert all(o.color[:3] != (1.0, 1.0, 1.0) for o in bpy.data.objects if o.type == "MESH")
    for m in ("NORMAL", "CONSTRUCTION", "ZONE", "SPACE_TYPE", "STORY"):
        assert bpy.ops.view3d.osm_render_mode(mode=m) == {"FINISHED"}, m
    assert bpy.ops.view3d.osm_render_mode(mode="ZONE") == {"FINISHED"}
    zc = {tuple(o.color[:3]) for o in objs}
    assert len(zc) == 1 and zc != {(0.8, 0.8, 0.8)}, zc  # all surfaces share zone Z1
    print("blender_tools_smoke OK")
finally:
    zonal.unregister()
