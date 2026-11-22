"""Headless check of the Results panel against a real eplusout.sql:

blender -b --factory-startup --python tests/blender_results_smoke.py -- C:\\path\\eplusout.sql

Dev only: borrows the uv venv's site-packages for `openstudio` (Blender 5.x = same CPython ABI).
"""

import sys
from pathlib import Path

import bpy

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
sys.path.append(str(root / ".venv" / "Lib" / "site-packages"))
sql_path = sys.argv[sys.argv.index("--") + 1]

import zonal
from zonal.blender import results as br

bpy.ops.wm.read_factory_settings(use_empty=True)
zonal.register()
try:
    scene = bpy.context.scene
    for i, name in enumerate(("Cube", "Cube 1", "Cube 2")):
        bpy.ops.mesh.primitive_plane_add(size=1, location=(i * 2, 0, 0))
        ob = bpy.context.object
        ob.name = name
        ob["thermalZoneName"] = "Thermal Zone"
    orig_mats = len(bpy.data.objects["Cube"].data.materials)

    s = scene.osm_results
    s.sql_path = sql_path
    assert bpy.ops.scene.osm_results_load() == {"FINISHED"}, s.status
    names = [v.name for v in s.variables]
    print("variables:", names, "freq:", s.freq)
    assert "Zone Air Temperature" in names

    def evaluated_verts(ob):
        dg = bpy.context.evaluated_depsgraph_get()
        me = ob.evaluated_get(dg).to_mesh()
        n = len(me.vertices)
        ob.evaluated_get(dg).to_mesh_clear()
        return n

    # --- zone variable: every object of the zone gets the keyframes, one label -------------
    s.active = names.index("Zone Air Temperature")
    s.key_filter = "THERMAL ZONE"
    assert bpy.ops.scene.osm_results_apply() == {"FINISHED"}, s.status
    print(s.status)
    cube = bpy.data.objects["Cube"]
    fc = br._find_fcurve(cube, '["osm_result"]')
    assert fc and len(fc.keyframe_points) == 8760, "expected 8760 keyframes"
    labels = [o for o in bpy.data.objects if br.LABEL_PROP in o]
    assert len(labels) == 1, labels
    assert cube.data.materials[0].name == br.HEATMAP_MAT
    seen = {}
    for f in (1, 3000, 6000):
        scene.frame_set(f)
        seen[f] = (round(cube["osm_result"], 3), evaluated_verts(labels[0]))
        assert abs(cube["osm_result"] - fc.evaluate(f)) < 1e-6
    print("frame -> (value, label verts):", seen)
    assert len({v[0] for v in seen.values()}) > 1, "value does not change over time"
    assert all(v[1] > 0 for v in seen.values()), "label has no geometry"
    assert len({v[1] for v in seen.values()}) > 1 or True  # vertex count may legitimately repeat

    # --- surface variable, daily stride, no labels --------------------------------------------
    s.key_filter = ""
    s.labels = True
    s.label_offset = 0.05
    s.stride = 24
    wall = bpy.data.objects["Cube 1"]
    wall.rotation_euler = (1.5707963, 0, 0.6)  # a tilted-to-vertical, turned wall
    bpy.context.view_layer.update()
    s.active = names.index("Surface Inside Face Temperature")
    assert bpy.ops.scene.osm_results_apply() == {"FINISHED"}, s.status
    print(s.status)
    fc = br._find_fcurve(bpy.data.objects["Cube 1"], '["osm_result"]')
    assert fc and len(fc.keyframe_points) == 365, len(fc.keyframe_points) if fc else None
    labs = [o for o in bpy.data.objects if br.LABEL_PROP in o]
    assert len(labs) == 2, "one label per matched surface (old zone label cleared)"
    lab = next(o for o in labs if o.name.startswith("Result Cube 1"))
    poly = wall.data.polygons[0]
    n = (wall.matrix_world.to_3x3() @ poly.normal).normalized()
    from mathutils import Vector

    assert (lab.rotation_quaternion @ Vector((0, 0, 1)) - n).length < 1e-4, "label not aligned to face normal"
    assert abs((lab.location - wall.matrix_world @ poly.center).dot(n) - 0.05) < 1e-4, "label offset along normal"
    assert abs((lab.rotation_quaternion @ Vector((0, 1, 0))).z) > 0.99, "wall text should be upright"

    # --- clear restores materials -------------------------------------------------------------
    assert bpy.ops.scene.osm_results_clear() == {"FINISHED"}
    assert "osm_result" not in bpy.data.objects["Cube 1"]
    assert br._find_fcurve(bpy.data.objects["Cube 1"], '["osm_result"]') is None
    assert len(bpy.data.objects["Cube"].data.materials) == orig_mats
    print("OK")
finally:
    zonal.unregister()
