"""Headless gbXML roundtrip: blender -b --factory-startup --python tests/blender_gbxml_smoke.py -- <in.xml> <out.xml>

Imports <in.xml> (gbXML), exports the scene again as gbXML and checks that the surface count survives.
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

res = bpy.ops.import_scene.openstudio_gbxml(filepath=src)
assert res == {"FINISHED"}, res
n_obj = len([o for o in bpy.data.objects if o.type == "MESH"])
print("IMPORTED OBJECTS", n_obj, "COLLECTIONS", [c.name for c in bpy.data.collections])
assert n_obj > 0

res = bpy.ops.export_scene.openstudio_gbxml(filepath=out)
assert res == {"FINISHED"}, res

import openstudio

rt = openstudio.gbxml.GbXMLReverseTranslator()
a = rt.loadModel(openstudio.path(src)).get()
b = rt.loadModel(openstudio.path(out)).get()
print(
    "SURFACES", len(a.getSurfaces()), "->", len(b.getSurfaces()), "SPACES", len(a.getSpaces()), "->", len(b.getSpaces())
)
assert len(a.getSurfaces()) == len(b.getSurfaces())
assert len(a.getSpaces()) == len(b.getSpaces())
