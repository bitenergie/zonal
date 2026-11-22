"""Headless check: Surface Matching splits partial overlaps; setting spaceName moves the object's collection.

blender -b --factory-startup --python tests/blender_match_split_smoke.py   (Blender 5.x)
"""

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import zonal

zonal.register()


def quad(name, pts, space):
    me = bpy.data.meshes.new(name)
    me.from_pydata(pts, [], [list(range(len(pts)))])
    ob = bpy.data.objects.new(name, me)
    col = bpy.data.collections.get(space) or bpy.data.collections.new(space)
    if col.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(col)
    col.objects.link(ob)
    ob["spaceName"] = space
    return ob


a = quad("Ceiling A", [(0, 0, 3), (2, 0, 3), (2, 2, 3), (0, 2, 3)], "A")  # faces up
b = quad("Floor B", [(1, 0, 3), (1, 2, 3), (3, 2, 3), (3, 0, 3)], "B")  # faces down
for o in (a, b):
    o["surfaceType"] = "RoofCeiling" if o is a else "Floor"
    o.select_set(True)
bpy.ops.object.osm_match_surfaces(mode="MATCH", scope="SELECTED")

bpy.data.objects.remove(bpy.data.objects["Cube"])
objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
print("objects:", sorted(o.name for o in objs))
area = {o.name: o.data.polygons[0].area for o in objs}
matched = {
    o.name: o.get("outsideBoundaryConditionObjectName") for o in objs if o.get("outsideBoundaryCondition") == "Surface"
}
print("areas:", area, "matched:", matched)
assert len(objs) == 4 and abs(sum(area.values()) - 8.0) < 1e-6  # shared 2+2, A not B 2, B not A 2
assert len(matched) == 2 and all(matched[matched[k]] == k for k in matched)
assert any(abs(area[k] - 2.0) < 1e-6 for k in matched)

# changing the space moves the object into that collection (created when missing)
a.osm_spaceName = "Kitchen"
assert [c.name for c in a.users_collection] == ["Kitchen"], [c.name for c in a.users_collection]
print("OK")
