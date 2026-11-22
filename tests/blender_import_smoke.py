"""Headless import/export roundtrip: blender -b --factory-startup --python tests/blender_import_smoke.py -- <in.osm> <out.osm>

Imports <in.osm>, exports the scene again and checks that surface / sub surface counts survive.
Blender 5.x (uses the .venv openstudio).
"""

import sys
from pathlib import Path

import bpy

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
sys.path.append(str(root / ".venv" / "Lib" / "site-packages"))
args = sys.argv[sys.argv.index("--") + 1 :]
src, out = args[0], args[1]

import zonal

bpy.ops.wm.read_factory_settings(use_empty=True)
zonal.register()

res = bpy.ops.import_scene.openstudio_osm(filepath=src)
assert res == {"FINISHED"}, res
n_obj = len([o for o in bpy.data.objects if o.type == "MESH"])
print("IMPORTED OBJECTS", n_obj, "COLLECTIONS", [c.name for c in bpy.data.collections])
assert n_obj > 0

res = bpy.ops.export_scene.openstudio_osm(filepath=out)
assert res == {"FINISHED"}, res

import openstudio

vt = openstudio.osversion.VersionTranslator()
a = vt.loadModel(openstudio.path(src)).get()
b = vt.loadModel(openstudio.path(out)).get()
print(
    "SURFACES",
    len(a.getSurfaces()),
    "->",
    len(b.getSurfaces()),
    "SUBS",
    len(a.getSubSurfaces()),
    "->",
    len(b.getSubSurfaces()),
    "SPACES",
    len(a.getSpaces()),
    "->",
    len(b.getSpaces()),
)
assert len(a.getSurfaces()) == len(b.getSurfaces())
assert len(a.getSubSurfaces()) == len(b.getSubSurfaces())
assert len(a.getSpaces()) == len(b.getSpaces())
print("IMPORT SMOKE OK")
