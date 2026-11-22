"""Text editor > Templates > OpenStudio: starter scripts that edit the OpenStudio custom properties.

Each template is plain bpy code (it runs in Blender's Text editor with Alt+P) using the
OpenStudio schema keys (`spaceName`, `thermalZoneName`, `surfaceType`, ...) stored as object
custom properties. Pick one, adjust the variables at the top and run it.
"""

from __future__ import annotations

import bpy

_HEAD = """import bpy

# Objects the script works on: the selection (set ONLY_SELECTED = False for the whole scene).
ONLY_SELECTED = True
objects = [o for o in (bpy.context.selected_objects if ONLY_SELECTED else bpy.context.scene.objects)
           if o.type == "MESH"]
"""

TEMPLATES: dict[str, tuple[str, str, str]] = {
    # id: (menu label, file name, source)
    "rename_by_space": (
        "Rename Objects by Space",
        "osm_rename_by_space.py",
        _HEAD
        + """
# Name = "<space> <n>" where space is the collection the object lives in.
for space in sorted({o.osm_spaceName or "Space" for o in objects}):
    group = [o for o in objects if (o.osm_spaceName or "Space") == space]
    for i, o in enumerate(group, 1):
        o.name = f"{space} {i}"
print(f"Renamed {len(objects)} objects")
""",
    ),
    "rename_prefix": (
        "Rename Objects (Prefix / Suffix / Replace)",
        "osm_rename_replace.py",
        _HEAD
        + """
PREFIX = ""
SUFFIX = ""
FIND, REPLACE = "", ""   # simple text replace, ignored when FIND is empty

for o in objects:
    name = o.name.replace(FIND, REPLACE) if FIND else o.name
    o.name = f"{PREFIX}{name}{SUFFIX}"
print(f"Renamed {len(objects)} objects")
""",
    ),
    "set_space_name": (
        "Set Space Name on Selection",
        "osm_set_space_name.py",
        _HEAD
        + """
SPACE_NAME = "Office 1"
ALSO_ZONE = True      # also set thermalZoneName to the same name

# The space of a surface is its collection: osm_spaceName moves the object into that collection.
# Zone / story / space type ... belong to the space (all its surfaces), not to one surface.
for o in objects:
    o.osm_spaceName = SPACE_NAME
    if ALSO_ZONE:
        o.osm_thermalZoneName = SPACE_NAME
print(f"Moved {len(objects)} objects to space {SPACE_NAME}")
""",
    ),
    "space_from_collection": (
        "Space / Zone Name from Collection",
        "osm_space_from_collection.py",
        _HEAD
        + """
# The collection IS the space. Give every space a thermal zone named like it.
SET_ZONE = True

for o in objects:
    name = o.osm_spaceName
    if name and SET_ZONE:
        o.osm_thermalZoneName = name
print(f"Updated {len(objects)} objects")
""",
    ),
    "set_property": (
        "Set a Property on Each Face / Object",
        "osm_set_property.py",
        _HEAD
        + """
# Any schema key from the OpenStudio extras, e.g.
#   surfaceType: Wall | RoofCeiling | Floor     outsideBoundaryCondition: Outdoors | Ground | Adiabatic | Surface
#   constructionName, spaceTypeName, buildingStoryName, sunExposure, windExposure
KEY = "outsideBoundaryCondition"
VALUE = "Adiabatic"

for o in objects:
    o[KEY] = VALUE
print(f"Set {KEY} = {VALUE!r} on {len(objects)} objects")
""",
    ),
    "clear_property": (
        "Clear a Property",
        "osm_clear_property.py",
        _HEAD
        + """
KEY = "constructionName"

n = 0
for o in objects:
    if KEY in o.keys():
        del o[KEY]
        n += 1
print(f"Removed {KEY} from {n} objects")
""",
    ),
    "surface_type_by_tilt": (
        "Surface Type from Tilt",
        "osm_surface_type_by_tilt.py",
        _HEAD
        + """
from math import degrees
from mathutils import Vector

# Classify each object by the average face normal: down = Floor, up = RoofCeiling, otherwise Wall.
for o in objects:
    n = Vector()
    for p in o.data.polygons:
        n += o.matrix_world.to_3x3() @ p.normal * p.area
    if n.length == 0:
        continue
    tilt = degrees(n.normalized().angle(Vector((0, 0, 1))))
    o["surfaceType"] = "Floor" if tilt > 150 else "RoofCeiling" if tilt < 30 else "Wall"
    print(f"{o.name}: tilt {tilt:.0f} -> {o['surfaceType']}")
""",
    ),
    "list_surfaces": (
        "List Surfaces (Name, Area, Properties)",
        "osm_list_surfaces.py",
        _HEAD
        + """
KEYS = ("spaceName", "thermalZoneName", "surfaceType", "outsideBoundaryCondition", "constructionName")

total = 0.0
for o in sorted(objects, key=lambda x: x.name):
    area = sum(p.area for p in o.data.polygons) * o.matrix_world.to_scale()[0] * o.matrix_world.to_scale()[1]
    total += area
    props = ", ".join(f"{k}={o[k]}" for k in KEYS if k in o.keys())
    print(f"{o.name:30s} {area:9.2f} m2  {props}")
print(f"{len(objects)} objects, total area {total:.2f} m2")
""",
    ),
    "osm_model": (
        "OpenStudio Model: Edit an .osm",
        "osm_edit_model.py",
        """import openstudio  # needs the OpenStudio bindings (the add-on bundles them)

IN_PATH = r"C:\\path\\in.osm"
OUT_PATH = r"C:\\path\\out.osm"

translator = openstudio.osversion.VersionTranslator()
model = translator.loadModel(openstudio.path(IN_PATH)).get()

# Rename every space and its thermal zone: "Space 1", "Space 2", ...
for i, space in enumerate(sorted(model.getSpaces(), key=lambda s: s.nameString()), 1):
    space.setName(f"Space {i}")
    zone = space.thermalZone()
    if zone.is_initialized():
        zone.get().setName(f"Zone {i}")

# Change a value on every surface: here all exterior walls get the Adiabatic boundary condition.
# for surface in model.getSurfaces():
#     if surface.surfaceType() == "Wall" and surface.outsideBoundaryCondition() == "Outdoors":
#         surface.setOutsideBoundaryCondition("Adiabatic")

model.save(openstudio.path(OUT_PATH), True)
print(f"{len(model.getSpaces())} spaces, {len(model.getSurfaces())} surfaces")
""",
    ),
}


class TEXT_OT_osm_insert_template(bpy.types.Operator):
    bl_idname = "text.osm_insert_template"
    bl_label = "Insert OpenStudio Template"
    bl_description = "Create a new text block from this OpenStudio starter script"
    bl_options = {"REGISTER", "UNDO"}

    template: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        item = TEMPLATES.get(self.template)
        if item is None:
            return {"CANCELLED"}
        _label, filename, source = item
        text = bpy.data.texts.new(filename)
        text.write(source)
        text.cursor_set(0)
        space = context.space_data
        if space is not None and space.type == "TEXT_EDITOR":
            space.text = text
        return {"FINISHED"}


class TEXT_MT_osm_templates(bpy.types.Menu):
    bl_idname = "TEXT_MT_osm_templates"
    bl_label = "OpenStudio"

    def draw(self, context):
        layout = self.layout
        for key, (label, _f, _s) in TEMPLATES.items():
            layout.operator(TEXT_OT_osm_insert_template.bl_idname, text=label).template = key


def _draw_menu(self, context):
    self.layout.menu(TEXT_MT_osm_templates.bl_idname, icon="MOD_BUILD")


_CLASSES = (TEXT_OT_osm_insert_template, TEXT_MT_osm_templates)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TEXT_MT_templates.append(_draw_menu)


def unregister():
    bpy.types.TEXT_MT_templates.remove(_draw_menu)
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
