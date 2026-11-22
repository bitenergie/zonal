"""Run inside Blender after installing the extension zip: exports a cube using the BUNDLED openstudio wheel."""

import sys

import bpy

mod = next(m for m in bpy.context.preferences.addons.keys() if m.endswith("zonal"))
print("ADDON", mod)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.preferences.addon_enable(module=mod) if mod not in bpy.context.preferences.addons else None
bpy.ops.mesh.primitive_cube_add(size=4, location=(0, 0, 2))
out = sys.argv[sys.argv.index("--") + 1]
print("RESULT", bpy.ops.export_scene.openstudio_osm(filepath=out))
import openstudio

print("BINDINGS", openstudio.openStudioLongVersion(), openstudio.__file__)
