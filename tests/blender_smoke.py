"""Headless end-to-end test: blender -b --python tests/blender_smoke.py -- <out.osm>

Dev only: borrows the uv venv's site-packages for `openstudio` (same CPython ABI as Blender 4.5).
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

col = bpy.data.collections.new("Office")
bpy.context.scene.collection.children.link(col)
bpy.ops.mesh.primitive_cube_add(size=1)
box = bpy.context.object
box.name = "Room"
box.scale = (10, 5, 3)
box.location = (5, 2.5, 1.5)
bpy.ops.object.transform_apply(scale=True)
for c in box.users_collection:
    c.objects.unlink(box)
col.objects.link(box)
box["thermalZoneName"] = "Zone 1"

bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 0))
win = bpy.context.object
win.name = "Window 1"
win.scale = (2, 1, 1)
win.rotation_euler = (1.5707963, 0, 0)
win.location = (5, -0.0, 1.5)
bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
win["surfaceType"] = "FixedWindow"
win["spaceName"] = "Office"
bpy.context.scene["northAxis"] = 20.0

res = bpy.ops.export_scene.openstudio_osm(filepath=str(out))
print("RESULT", res)
assert res == {"FINISHED"}

import openstudio

m = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(out))).get()
print(
    "SURFACES",
    sorted(s.surfaceType() for s in m.getSurfaces()),
    "SUBS",
    len(m.getSubSurfaces()),
    "SPACES",
    [s.nameString() for s in m.getSpaces()],
    "NORTH",
    m.getBuilding().northAxis(),
)
assert len(m.getSurfaces()) == 6 and len(m.getSubSurfaces()) == 1
assert m.getBuilding().northAxis() == 20.0
print("SMOKE OK")
