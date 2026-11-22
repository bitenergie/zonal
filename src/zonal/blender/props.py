"""Interactive OpenStudio properties: dropdown fields, name library with +/- buttons, .osm templates (imports bpy).

The values still live in plain object custom properties (what the exporter reads); the `osm_*` properties
registered on `bpy.types.Object` are proxies that read/write those custom properties, which gives search
dropdowns and enum menus instead of free text fields.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty

from ..core import inspector_schema, schema
from ..core.tools_core import (
    LIBRARY_CATEGORIES,
    decode_extra_templates,
    encode_extra_templates,
    scan_osm_names,
    unique_name,
)
from .icons import PanelIcon, ik
from .widgets import field, field_row, split_column

DEFAULT_TEMPLATE = Path(__file__).parent.parent / "templates" / "MinimalTemplate.osm"

BOUNDARY_CONDITIONS = ("Outdoors", "Ground", "Surface", "Adiabatic", "Foundation")
SUN_EXPOSURE = ("SunExposed", "NoSun")
WIND_EXPOSURE = ("WindExposed", "NoWind")

# enum fields: key -> (label, choices)
ENUM_FIELDS = {
    "surfaceType": ("Surface Type", schema.ALL_SURFACE_TYPES),
    "outsideBoundaryCondition": ("Boundary Condition", BOUNDARY_CONDITIONS),
    "sunExposure": ("Sun Exposure", SUN_EXPOSURE),
    "windExposure": ("Wind Exposure", WIND_EXPOSURE),
}
# library-backed fields shown with a dropdown and +/-:  key -> category
LIBRARY_FIELDS = {key: cat for cat, (key, _label) in LIBRARY_CATEGORIES.items()}
LABELS = {key: label for _cat, (key, label) in LIBRARY_CATEGORIES.items()}
LABELS.update(
    {"spaceName": "Space", "outsideBoundaryConditionObjectName": "Adjacent Surface", "surfaceName": "Parent Surface"}
)
TEMPLATE_TYPE_ITEMS = [
    ("SpaceType", "Space Types", "Space types with their loads and schedules"),
    ("DefaultConstructionSet", "Construction Sets", "Default construction sets"),
    ("Construction", "Constructions", "Constructions with their materials"),
]


ZONE_PROXY_KEYS = ("idealAirLoads", *inspector_schema.ZONE_SETPOINTS)
PICKABLE_FIELDS = ("surfaceName", "outsideBoundaryConditionObjectName")  # fields naming another surface
SPACE = "space"  # pseudo library category: spaces are collections


def prop_name(key: str) -> str:
    return f"osm_{key}"


# --- library -------------------------------------------------------------------------


class OSMLibraryItem(bpy.types.PropertyGroup):
    name: StringProperty()
    category: StringProperty()


def library_names(scene, category: str) -> list[str]:
    return [i.name for i in scene.osm_library if i.category == category]


def used_names(scene, category: str) -> set[str]:
    """Names of this kind that objects refer to through their custom properties (e.g. imported stories / zones)."""
    entry = LIBRARY_CATEGORIES.get(category)
    if entry is None:
        return set()
    key = entry[0]
    names = {v for ob in scene.objects if ob.type == "MESH" and isinstance(v := ob.get(key), str) and v}
    from .inspector import SPACE_ATTR_FIELD

    field = SPACE_ATTR_FIELD.get(key)
    if field:  # values set on the spaces (Inspector store)
        names.update(v for i in getattr(scene, "osm_model", ()) if i.type == "Space" and (v := getattr(i, field, "")))
    return names


def category_names(scene, category: str) -> list[str]:
    """Every name of a kind: library / template, names used on objects and Inspector store items."""
    if category == "material":  # layer dropdown: all material kinds
        return sorted({n for c in inspector_schema.MATERIAL_CATEGORIES for n in category_names(scene, c)})
    names = set(library_names(scene, category)) | used_names(scene, category)
    tname = inspector_schema.type_for_category(category)
    if tname:
        names.update(i.name for i in getattr(scene, "osm_model", ()) if i.type == tname)
    return sorted(names)


def library_add(scene, category: str, name: str) -> bool:
    if not name or name in library_names(scene, category):
        return False
    item = scene.osm_library.add()
    item.name, item.category = name, category
    return True


def _mesh_object_names(self, context, edit_text):
    return [o.name for o in context.scene.objects if o.type == "MESH"]


def _collection_names(self, context, edit_text):
    return [c.name for c in bpy.data.collections]


def _library_search(cat: str):
    def search(self, context, edit_text):
        return category_names(context.scene, cat)

    return search


def assign_space(ob, name: str):
    """Make `ob` belong to the space `name`: its collection is created if needed and is the only one it is in."""
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    moving = [ob]
    if ob.type == "MESH" and ob.get("surfaceType") not in schema.SUBSURFACE_TYPES:  # sub surfaces follow their parent
        moving += [
            o
            for o in bpy.data.objects
            if o.type == "MESH" and o.get("surfaceName") == ob.name and o.get("surfaceType") in schema.SUBSURFACE_TYPES
        ]
    for o in moving:
        if o.name not in col.objects:
            col.objects.link(o)
        for c in list(o.users_collection):
            if c is not col:
                c.objects.unlink(o)


def _make_string_prop(key: str):
    def get(self):
        from .inspector import SPACE_ATTR_FIELD, get_space_attr  # lazy: inspector imports this module

        if key == "spaceName" and self.type == "MESH":
            from .surface_ops import _space_of

            return _space_of(self)
        if key in SPACE_ATTR_FIELD:  # a space property: one value for the whole space
            return get_space_attr(bpy.context.scene, self, key)
        v = self.get(key)
        return v if isinstance(v, str) else ""

    def set_(self, value):
        from .inspector import SPACE_ATTR_FIELD, set_space_attr

        if key == "spaceName" and self.type == "MESH":
            if value:  # the space of a surface is its collection
                assign_space(self, value)
        elif key in SPACE_ATTR_FIELD:
            set_space_attr(bpy.context.scene, self, key, value)
        elif value:
            self[key] = value
        elif key in self:
            del self[key]

    cat = LIBRARY_FIELDS.get(key)
    if cat:
        search = _library_search(cat)
    elif key == "spaceName":
        search = _collection_names
    else:
        search = _mesh_object_names
    return StringProperty(name=LABELS[key], get=get, set=set_, search=search, search_options={"SUGGESTION"})


def _make_enum_prop(key: str):
    label, choices = ENUM_FIELDS[key]
    items = [("DEFAULT", "Default" if key != "surfaceType" else "(auto from tilt)", "")] + [(c, c, "") for c in choices]
    values = [""] + list(choices)

    def get(self):
        v = self.get(key)
        return values.index(v) if v in values else 0

    def set_(self, idx):
        if idx <= 0:
            if key in self:
                del self[key]
        else:
            self[key] = values[idx]
        if key == "surfaceType":
            from .ui import sync_subsurface_displace

            sync_subsurface_displace(self)

    return EnumProperty(name=label, items=items, get=get, set=set_)


# --- operators -----------------------------------------------------------------------


def _targets(context):
    objs = [o for o in context.selected_objects if o.type == "MESH"]
    return objs or ([context.object] if context.object and context.object.type == "MESH" else [])


class OBJECT_OT_osm_lib_add(bpy.types.Operator):
    bl_idname = "object.osm_lib_add"
    bl_label = "New"
    bl_description = "Create a new named object (zone, space type, construction...) and assign it to the selection"
    bl_options = {"REGISTER", "UNDO"}

    category: StringProperty(options={"HIDDEN"})
    name: StringProperty(name="Name")

    def invoke(self, context, event):
        if self.category == SPACE:
            self.name = unique_name("Space", {c.name for c in bpy.data.collections})
        else:
            self.name = unique_name(LIBRARY_CATEGORIES[self.category][1], library_names(context.scene, self.category))
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if not self.name:
            return {"CANCELLED"}
        if self.category == SPACE:  # a space is a collection; the selection moves into it
            for ob in _targets(context):
                assign_space(ob, self.name)
            if bpy.data.collections.get(self.name) is None:
                bpy.context.scene.collection.children.link(bpy.data.collections.new(self.name))
            return {"FINISHED"}
        from .inspector import SPACE_ATTR_FIELD, set_space_attr

        library_add(context.scene, self.category, self.name)
        key = LIBRARY_CATEGORIES[self.category][0]
        for ob in _targets(context):
            if key in SPACE_ATTR_FIELD:
                set_space_attr(context.scene, ob, key, self.name)
            else:
                ob[key] = self.name
        return {"FINISHED"}


class OBJECT_OT_osm_lib_clear(bpy.types.Operator):
    bl_idname = "object.osm_lib_clear"
    bl_label = "Clear"
    bl_description = "Remove this property from the selected objects"
    bl_options = {"REGISTER", "UNDO"}

    key: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        from .inspector import SPACE_ATTR_FIELD, set_space_attr

        for ob in _targets(context):
            if self.key in SPACE_ATTR_FIELD:
                set_space_attr(context.scene, ob, self.key, "")
            elif self.key == "spaceName":
                continue  # a surface always has its collection as space
            elif self.key in ob:
                del ob[self.key]
        return {"FINISHED"}


class SCENE_OT_osm_lib_remove(bpy.types.Operator):
    bl_idname = "scene.osm_lib_remove"
    bl_label = "Remove from Library"
    bl_options = {"REGISTER", "UNDO"}

    category: StringProperty(options={"HIDDEN"})
    name: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        lib = context.scene.osm_library
        for i, item in enumerate(lib):
            if item.category == self.category and item.name == self.name:
                lib.remove(i)
                break
        return {"FINISHED"}


def template_source(path: Path) -> tuple[Path, list[str]]:
    """The .osm to read for a template file: an .idf is translated first (cached in the temp dir).

    Returns (osm path, translator warnings).
    """
    import hashlib
    import tempfile

    if path.suffix.lower() != ".idf":
        return path, []
    from ..core.osm_reader import idf_to_osm_file
    from .ui import load_openstudio

    load_openstudio()
    key = hashlib.md5(f"{path.resolve()}|{path.stat().st_mtime_ns}".encode()).hexdigest()[:10]
    out = Path(bpy.app.tempdir or tempfile.gettempdir()) / f"{path.stem}_{key}.osm"
    return out, ([] if out.exists() else idf_to_osm_file(path, out))


class SCENE_OT_osm_load_template(bpy.types.Operator):
    bl_idname = "scene.osm_load_template"
    bl_label = "Load Template (.osm)"
    bl_description = (
        "Use an .osm as template: its space types, construction sets and constructions fill the "
        "dropdowns and are copied into the exported model"
    )
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.osm;*.idf", options={"HIDDEN"})
    use_default: BoolProperty(default=False, options={"HIDDEN"})

    def invoke(self, context, event):
        if self.use_default:
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        path = DEFAULT_TEMPLATE if self.use_default else Path(bpy.path.abspath(self.filepath))
        try:
            path, _warnings = template_source(path)
            names = scan_osm_names(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, RuntimeError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        added = sum(library_add(context.scene, cat, n) for cat, ns in names.items() for n in ns)
        context.scene.osm_template_path = str(path)
        self.report({"INFO"}, f"Template {path.name}: {added} names added to the library")
        return {"FINISHED"}


class SCENE_OT_osm_clear_template(bpy.types.Operator):
    bl_idname = "scene.osm_clear_template"
    bl_label = "Clear Template"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.osm_template_path = ""
        return {"FINISHED"}


_RESOURCE_FLAGS = (  # operator property -> template type
    ("space_types", "SpaceType"),
    ("construction_sets", "DefaultConstructionSet"),
    ("constructions", "Construction"),
    ("schedules", "Schedule"),
)


class SCENE_OT_osm_import_resources(bpy.types.Operator):
    bl_idname = "scene.osm_import_resources"
    bl_label = "Open .osm Template"
    bl_description = (
        "Take the chosen kinds (space types, construction sets, constructions, schedules) of an .osm / .idf file "
        "into the exported model and fill the library dropdowns. Several files can be added"
    )
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.osm;*.idf", options={"HIDDEN"})
    space_types: BoolProperty(name="Space Types", default=True)
    construction_sets: BoolProperty(name="Construction Sets", default=True)
    constructions: BoolProperty(name="Constructions", default=True)
    schedules: BoolProperty(name="Schedules", default=True)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        col = self.layout.column(align=True)
        col.label(text="Take from file")
        for key, _t in _RESOURCE_FLAGS:
            col.prop(self, key)

    def execute(self, context):
        types = [t for key, t in _RESOURCE_FLAGS if getattr(self, key)]
        if not types:
            self.report({"WARNING"}, "Nothing selected to import")
            return {"CANCELLED"}
        try:
            path, _warnings = template_source(Path(bpy.path.abspath(self.filepath)))
            names = scan_osm_names(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, RuntimeError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        wanted = {"SpaceType": "spacetype", "DefaultConstructionSet": "cset", "Construction": "construction"}
        added = sum(
            library_add(context.scene, wanted[t], n) for t in types if t in wanted for n in names.get(wanted[t], ())
        )
        entries = decode_extra_templates(context.scene.osm_extra_templates)
        entries.append((str(path), types))
        context.scene.osm_extra_templates = encode_extra_templates(entries)
        self.report({"INFO"}, f"{Path(self.filepath).name}: {added} names added to the library")
        return {"FINISHED"}


class SCENE_OT_osm_remove_extra_template(bpy.types.Operator):
    bl_idname = "scene.osm_remove_extra_template"
    bl_label = "Remove Imported Resources"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty()

    def execute(self, context):
        entries = decode_extra_templates(context.scene.osm_extra_templates)
        if 0 <= self.index < len(entries):
            del entries[self.index]
        context.scene.osm_extra_templates = encode_extra_templates(entries)
        return {"FINISHED"}


# --- drawing -------------------------------------------------------------------------


def _field(col, ob, key):
    cat = SPACE if key == "spaceName" else LIBRARY_FIELDS.get(key)
    pn = prop_name(key)
    row = field_row(col, ob.bl_rna.properties[pn].name)
    row.prop(ob, pn, text="")
    if cat:
        row.operator("object.osm_lib_add", text="", **ik("lib_add", "ADD")).category = cat
    if key in PICKABLE_FIELDS:
        row.operator("object.osm_pick_object", text="", **ik("pick_object", "EYEDROPPER")).key = key
    if key not in ENUM_FIELDS and key != "spaceName":
        row.operator("object.osm_lib_clear", text="", **ik("lib_clear", "REMOVE")).key = key


def draw_surface_fields(layout, context, ob):
    col = split_column(layout)
    field(col, ob, prop_name("surfaceType"))
    if ob.get("surfaceType") in schema.SUBSURFACE_TYPES:
        _field(col, ob, "surfaceName")
    _field(col, ob, "constructionName")


def draw_space_fields(layout, context, ob):
    col = split_column(layout)
    from .surface_ops import space_collection

    _field(col, ob, "spaceName")
    if space_collection(ob):
        col.label(text="Values below belong to the whole space", icon="INFO")
    else:
        col.label(text="Not in a space collection (default 'Space 1')", icon="ERROR")
    for key in ("thermalZoneName", "spaceTypeName", "buildingStoryName", "buildingUnitName", "constructionSetName"):
        _field(col, ob, key)


def zone_prop_name(key: str) -> str:
    return f"osm_zone_{key}"


def _zone_item(ob, create: bool):
    """The ThermalZone store item of the zone `ob`'s space belongs to (None: no zone / not created yet)."""
    from .inspector import ensure_item, get_item, get_space_attr

    zone = get_space_attr(bpy.context.scene, ob, "thermalZoneName") if ob.type == "MESH" else ""
    if not zone:
        return None
    return (ensure_item if create else get_item)(bpy.context.scene, "ThermalZone", zone)


def _make_zone_prop(fd: inspector_schema.FieldDef):
    """Object proxy of a ThermalZone field: reads / writes the zone of the object's space.

    Being an Object property, Alt+click (or Alt+Enter) applies a value to every selected object, i.e. to each
    one's own zone, exactly like the Space fields.
    """
    key = fd.key

    def get(self):
        item = _zone_item(self, False)
        return getattr(item, key) if item is not None else fd.default_value

    def set_(self, value):
        item = _zone_item(self, True)
        if item is not None:
            setattr(item, key, value)

    if fd.kind == inspector_schema.BOOL:
        return BoolProperty(name=fd.label, get=get, set=set_)

    def search(self, context, edit_text):
        return category_names(context.scene, fd.ref)

    return StringProperty(name=fd.label, get=get, set=set_, search=search, search_options={"SUGGESTION"})


ZONE_FIELDS = tuple(fd for fd in inspector_schema.TYPES["ThermalZone"].fields if fd.key in ZONE_PROXY_KEYS)


def draw_zone_fields(layout, context, ob):
    """HVAC of the thermal zone the object's space belongs to: ideal air loads + the four setpoint schedules."""
    from .inspector import get_space_attr

    col = split_column(layout)
    zone = get_space_attr(context.scene, ob, "thermalZoneName")
    if not zone:
        col.label(text="Set a Thermal Zone in the Space panel", icon="INFO")
        return
    col.label(text=f"Values below belong to zone '{zone}'", icon="INFO")
    field(col, ob, zone_prop_name("idealAirLoads"))
    col.separator()
    for key in inspector_schema.ZONE_SETPOINTS:
        row = field(col, ob, zone_prop_name(key))
        row.operator("object.osm_zone_add", text="", **ik("lib_add", "ADD")).key = key
        row.operator("object.osm_zone_clear", text="", **ik("lib_clear", "REMOVE")).key = key


def pick_target(context, event):
    """The surface object under the mouse in whichever 3D viewport it is over (None: none / not a surface)."""
    from bpy_extras import view3d_utils

    from .surface_ops import is_loose

    mx, my = event.mouse_x, event.mouse_y
    for area in context.window.screen.areas:
        if area.type != "VIEW_3D" or not (area.x <= mx < area.x + area.width and area.y <= my < area.y + area.height):
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is None or not (
            region.x <= mx < region.x + region.width and region.y <= my < region.y + region.height
        ):
            return None
        rv3d = area.spaces.active.region_3d
        co = (mx - region.x, my - region.y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
        hit, _loc, _nrm, _idx, ob, _m = context.scene.ray_cast(context.view_layer.depsgraph, origin, direction)
        ob = ob.original if hit and ob is not None else None
        return ob if ob is not None and ob.type == "MESH" and not is_loose(ob) else None
    return None


class OBJECT_OT_osm_pick_object(bpy.types.Operator):
    bl_idname = "object.osm_pick_object"
    bl_label = "Pick Surface"
    bl_description = (
        "Eyedropper: click a surface in the 3D viewport to use it for this field (Esc / right click cancels)"
    )
    bl_options = {"REGISTER", "UNDO"}

    key: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH"

    def invoke(self, context, event):
        self.target = context.object.name
        context.window.cursor_modal_set("EYEDROPPER")
        context.workspace.status_text_set("Click a surface to pick it   |   Esc / RMB: cancel")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"}:
            self._finish(context)
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            picked = pick_target(context, event)
            if picked is None:
                return {"RUNNING_MODAL"}
            self._finish(context)
            ob = bpy.data.objects.get(self.target)
            if ob is None or picked is ob:
                self.report({"WARNING"}, "Pick a different surface")
                return {"CANCELLED"}
            setattr(ob, prop_name(self.key), picked.name)
            context.area.tag_redraw()
            return {"FINISHED"}
        return {"RUNNING_MODAL"}


class OBJECT_OT_osm_zone_add(bpy.types.Operator):
    bl_idname = "object.osm_zone_add"
    bl_label = "New Schedule"
    bl_description = "Create a new constant schedule and use it as this setpoint of the zones of the selected objects"
    bl_options = {"REGISTER", "UNDO"}

    key: StringProperty(options={"HIDDEN"})
    name: StringProperty(name="Name")

    def invoke(self, context, event):
        label = inspector_schema.ZONE_SETPOINTS[self.key][0].removesuffix(" Schedule")
        self.name = unique_name(label, category_names(context.scene, "schedule"))
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        from .inspector import ensure_item

        if not self.name or self.key not in inspector_schema.ZONE_SETPOINTS:
            return {"CANCELLED"}
        ensure_item(context.scene, "ScheduleConstant", self.name)
        for ob in _targets(context):
            setattr(ob, zone_prop_name(self.key), self.name)
        return {"FINISHED"}


class OBJECT_OT_osm_zone_clear(bpy.types.Operator):
    bl_idname = "object.osm_zone_clear"
    bl_label = "Clear"
    bl_description = "Remove this setpoint schedule from the zones of the selected objects"
    bl_options = {"REGISTER", "UNDO"}

    key: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        if self.key not in inspector_schema.ZONE_SETPOINTS:
            return {"CANCELLED"}
        for ob in _targets(context):
            if _zone_item(ob, False) is not None:
                setattr(ob, zone_prop_name(self.key), "")
        return {"FINISHED"}


def draw_boundary_fields(layout, context, ob):
    col = split_column(layout)
    field(col, ob, prop_name("outsideBoundaryCondition"))
    if ob.get("outsideBoundaryCondition") == "Surface":
        _field(col, ob, "outsideBoundaryConditionObjectName")
    field(col, ob, prop_name("sunExposure"))
    field(col, ob, prop_name("windExposure"))


def draw_object_fields(layout, context, ob):
    """Editor for the OpenStudio properties of `ob` in one flat layout (Properties editor)."""
    draw_surface_fields(layout, context, ob)
    for idname, label, drawer in (
        ("osm_obj_space", "Space", draw_space_fields),
        ("osm_obj_zone", "Thermal Zone", draw_zone_fields),
        ("osm_obj_boundary", "Boundary", draw_boundary_fields),
    ):
        header, body = layout.panel(idname, default_closed=False)
        header.label(text=label)
        if body is not None:
            drawer(body, context, ob)


class _SidebarPanel:
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"


class VIEW3D_PT_osm_properties(PanelIcon, _SidebarPanel, bpy.types.Panel):
    osm_icon = "panel_properties"
    bl_label = "Properties"
    bl_idname = "VIEW3D_PT_osm_properties"

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH"

    def draw(self, context):
        draw_surface_fields(self.layout, context, context.object)


class _PropertiesSub(_SidebarPanel):
    bl_parent_id = "VIEW3D_PT_osm_properties"

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH"


class VIEW3D_PT_osm_properties_space(_PropertiesSub, bpy.types.Panel):
    bl_label = "Space"
    bl_idname = "VIEW3D_PT_osm_properties_space"

    def draw(self, context):
        draw_space_fields(self.layout, context, context.object)


class VIEW3D_PT_osm_properties_zone(_PropertiesSub, bpy.types.Panel):
    bl_label = "Thermal Zone"
    bl_idname = "VIEW3D_PT_osm_properties_zone"

    def draw(self, context):
        draw_zone_fields(self.layout, context, context.object)


class VIEW3D_PT_osm_properties_boundary(_PropertiesSub, bpy.types.Panel):
    bl_label = "Boundary"
    bl_idname = "VIEW3D_PT_osm_properties_boundary"

    def draw(self, context):
        draw_boundary_fields(self.layout, context, context.object)


class VIEW3D_PT_osm_library(PanelIcon, _SidebarPanel, bpy.types.Panel):
    osm_icon = "panel_library"
    bl_label = "Template & Library"
    bl_idname = "VIEW3D_PT_osm_library"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        sc, lay = context.scene, self.layout
        lay.use_property_split = False
        lay.use_property_decorate = False

        lay.label(text="Template")
        row = lay.row(align=True)
        row.operator("scene.osm_load_template", text="Default", **ik("template_load", "FILE_BLEND")).use_default = True
        row.operator("scene.osm_import_resources", text="Open .osm…", **ik("template_load", "FILEBROWSER"))
        if sc.osm_template_path:
            row = lay.row(align=True)
            row.label(text=Path(sc.osm_template_path).name, icon="CHECKMARK")
            row.operator("scene.osm_clear_template", text="", **ik("template_clear", "X"))
            lay.label(text="Copy from template")
            lay.row(align=True).prop(sc, "osm_template_types", expand=True)
        for i, (path, types) in enumerate(decode_extra_templates(sc.osm_extra_templates)):
            row = lay.row(align=True)
            row.label(text=f"{Path(path).name}: {', '.join(types)}", icon="CHECKMARK")
            row.operator("scene.osm_remove_extra_template", text="", **ik("template_clear", "X")).index = i


class OSM_UL_library(bpy.types.UIList):
    """Library items of the category chosen in `scene.osm_library_category` (built-in filter + sort by name)."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "name", text="", emboss=False, icon="DOT")

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        cat = context.scene.osm_library_category
        flt = [self.bitflag_filter_item if i.category == cat else 0 for i in items]
        if self.filter_name:
            needle = self.filter_name.lower()
            flt = [f if f and needle in i.name.lower() else 0 for f, i in zip(flt, items, strict=True)]
        order = bpy.types.UI_UL_list.sort_items_by_name(items, "name") if self.use_filter_sort_alpha else []
        return flt, order


class VIEW3D_PT_osm_library_list(_SidebarPanel, bpy.types.Panel):
    bl_label = "Library"
    bl_idname = "VIEW3D_PT_osm_library_list"
    bl_parent_id = "VIEW3D_PT_osm_library"

    def draw(self, context):
        sc, lay = context.scene, self.layout
        lay.use_property_decorate = False
        cat = sc.osm_library_category
        field(lay, sc, "osm_library_category", "Kind")
        row = lay.row()
        row.template_list("OSM_UL_library", "", sc, "osm_library", sc, "osm_library_index", rows=4)
        side = row.column(align=True)
        side.operator("object.osm_lib_add", text="", **ik("lib_add", "ADD")).category = cat
        active = [i for i in sc.osm_library if i.category == cat]
        idx = sc.osm_library_index
        if 0 <= idx < len(sc.osm_library) and sc.osm_library[idx].category == cat:
            op = side.operator("scene.osm_lib_remove", text="", **ik("lib_remove", "REMOVE"))
            op.category, op.name = cat, sc.osm_library[idx].name
        extra = len(category_names(sc, cat)) - len(active)
        if extra > 0:
            lay.label(text=f"+ {extra} from objects / model (not removable here)", icon="INFO")


# --- registration --------------------------------------------------------------------

CLASSES = (
    OSMLibraryItem,
    OBJECT_OT_osm_lib_add,
    OBJECT_OT_osm_lib_clear,
    OBJECT_OT_osm_pick_object,
    OBJECT_OT_osm_zone_add,
    OBJECT_OT_osm_zone_clear,
    SCENE_OT_osm_lib_remove,
    SCENE_OT_osm_load_template,
    SCENE_OT_osm_clear_template,
    SCENE_OT_osm_import_resources,
    SCENE_OT_osm_remove_extra_template,
    VIEW3D_PT_osm_properties,
    VIEW3D_PT_osm_properties_space,
    VIEW3D_PT_osm_properties_zone,
    VIEW3D_PT_osm_properties_boundary,
    VIEW3D_PT_osm_library,
    OSM_UL_library,
    VIEW3D_PT_osm_library_list,
)

_STRING_KEYS = tuple(LIBRARY_FIELDS) + ("spaceName", "outsideBoundaryConditionObjectName", "surfaceName")


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.osm_library = CollectionProperty(type=OSMLibraryItem)
    bpy.types.Scene.osm_library_category = EnumProperty(
        name="Kind", items=[(c, label, "") for c, (_k, label) in LIBRARY_CATEGORIES.items()]
    )
    bpy.types.Scene.osm_library_index = IntProperty()
    bpy.types.Scene.osm_template_path = StringProperty(name="Template", subtype="FILE_PATH")
    bpy.types.Scene.osm_extra_templates = StringProperty(name="Imported Resources", default="")  # JSON, see tools_core
    bpy.types.Scene.osm_template_types = EnumProperty(
        name="Template Content",
        items=TEMPLATE_TYPE_ITEMS,
        options={"ENUM_FLAG"},
        default={t[0] for t in TEMPLATE_TYPE_ITEMS},
    )
    for key in _STRING_KEYS:
        setattr(bpy.types.Object, prop_name(key), _make_string_prop(key))
    for key in ENUM_FIELDS:
        setattr(bpy.types.Object, prop_name(key), _make_enum_prop(key))
    for fd in ZONE_FIELDS:
        setattr(bpy.types.Object, zone_prop_name(fd.key), _make_zone_prop(fd))


def unregister():
    for key in _STRING_KEYS + tuple(ENUM_FIELDS):
        delattr(bpy.types.Object, prop_name(key))
    for fd in ZONE_FIELDS:
        delattr(bpy.types.Object, zone_prop_name(fd.key))
    del bpy.types.Scene.osm_library_index
    del bpy.types.Scene.osm_library_category
    del bpy.types.Scene.osm_template_types
    del bpy.types.Scene.osm_template_path
    del bpy.types.Scene.osm_extra_templates
    del bpy.types.Scene.osm_library
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
