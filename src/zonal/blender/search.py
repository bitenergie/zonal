"""Surface Search panel: select objects whose faces match class / name / type / construction / orientation... (imports bpy)."""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..core.search_core import CLASSES, CLASSES_WITH_BOUNDARY, SearchCriteria, class_of, matches
from ..core.tools_geometry import polygon_normal_unit
from .icons import PanelIcon, ik
from .props import BOUNDARY_CONDITIONS, SUN_EXPOSURE, WIND_EXPOSURE, category_names
from .surface_ops import _space_of, _surface_type, _world_polygon, is_loose
from .widgets import PRIMARY_SCALE, draw_range, field, field_row, split_column

ANY = "ANY"
_type_items_cache: list = []


def _items(values, any_label="Any"):
    return [(ANY, any_label, "")] + [(v, v, "") for v in values]


def _type_items(self, context):
    types = CLASSES.get(self.cls) if self.cls != ANY else tuple(t for ts in CLASSES.values() for t in ts)
    _type_items_cache[:] = _items(types or ())
    return _type_items_cache


def _story_search(self, context, edit_text):
    return category_names(context.scene, "story")


def _construction_search(self, context, edit_text):
    return category_names(context.scene, "construction")


def _val(v: str) -> str:
    return "" if v == ANY else v


class OSMSearchHit(bpy.types.PropertyGroup):
    name: StringProperty()


def _hit_picked(self, context):
    """Clicking a row of the hits list selects that object."""
    if not 0 <= self.hit_index < len(self.hits):
        return
    ob = context.scene.objects.get(self.hits[self.hit_index].name)
    if ob is None or not ob.visible_get():
        return
    for o in context.view_layer.objects:
        if o.select_get():
            o.select_set(False)
    ob.select_set(True)
    context.view_layer.objects.active = ob


class OSM_UL_search_hits(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        ob = context.scene.objects.get(item.name)
        layout.label(text=item.name, icon="MESH_DATA" if ob is None or ob.visible_get() else "HIDE_ON")


class OSMSearchSettings(bpy.types.PropertyGroup):
    hits: CollectionProperty(type=OSMSearchHit)
    hit_index: IntProperty(default=-1, update=_hit_picked, options={"HIDDEN", "SKIP_SAVE"})
    cls: EnumProperty(name="Class", items=_items(CLASSES), description="Kind of OpenStudio object to look for")
    name: StringProperty(name="Name", description="Case-insensitive part of the object name")
    type: EnumProperty(name="Type", items=_type_items)
    construction: StringProperty(name="Construction", search=_construction_search, search_options={"SUGGESTION"})
    boundary: EnumProperty(
        name="Boundary",
        items=_items(BOUNDARY_CONDITIONS),
        description="Outside boundary condition (unset = Ground for floors, else Outdoors)",
    )
    sun: EnumProperty(name="Sun Exposure", items=_items(SUN_EXPOSURE))
    wind: EnumProperty(name="Wind Exposure", items=_items(WIND_EXPOSURE))
    az_from: FloatProperty(
        name="From", default=0.0, min=0.0, max=360.0, description="Orientation range start, clockwise, north = 0"
    )
    az_to: FloatProperty(name="To", default=360.0, min=0.0, max=360.0, description="Orientation range end")
    tilt_from: FloatProperty(
        name="Tilt From", default=0.0, min=0.0, max=180.0, description="Tilt range start: roof 0, wall 90, floor 180"
    )
    tilt_to: FloatProperty(name="To", default=180.0, min=0.0, max=180.0, description="Tilt range end")
    story: StringProperty(
        name="Building Story",
        description="Only surfaces of spaces on this building story",
        search=_story_search,
        search_options={"SUGGESTION"},
    )
    invert: BoolProperty(name="Invert Search", description="Select the objects of the class that do NOT match")
    invert_across_classes: BoolProperty(
        name="Invert Across Classes", description="Select every object that does NOT match, of any class"
    )
    hide_others: BoolProperty(
        name="Hide Everything Else", description="Hide the objects that were not found (see Unhide All)"
    )
    hidden: StringProperty(default="", options={"HIDDEN"})
    exclude_horizontal: BoolProperty(name="Exclude Horizontal Surfaces")
    only_non_convex: BoolProperty(name="Only Non-Convex Surfaces")
    use_min_vertices: BoolProperty(name="Only Surfaces With More Than")
    min_vertices: IntProperty(name="Vertices", default=4, min=3)
    count: IntProperty(default=-1, options={"HIDDEN"})
    report: StringProperty(default="", options={"HIDDEN"})


def _default_boundary(stype: str, ob) -> tuple[str, str, str]:
    """boundary / sun / wind as the exporter would end up with them when the custom props are unset."""
    bc = ob.get("outsideBoundaryCondition")
    if not isinstance(bc, str) or not bc:
        bc = "Ground" if stype == "Floor" else "Outdoors"
    sun = ob.get("sunExposure")
    if not isinstance(sun, str) or not sun:
        sun = "SunExposed" if bc == "Outdoors" else "NoSun"
    wind = ob.get("windExposure")
    if not isinstance(wind, str) or not wind:
        wind = "WindExposed" if bc == "Outdoors" else "NoWind"
    return bc, sun, wind


def _facts(ob, poly, north: float) -> dict:
    stype = _surface_type(ob, poly)
    pts = _world_polygon(ob, poly)
    bc, sun, wind = _default_boundary(stype, ob) if class_of(stype) in CLASSES_WITH_BOUNDARY else ("", "", "")
    n = ob.get("constructionName")
    return {
        "class": class_of(stype),
        "name": ob.name,
        "type": stype,
        "construction": n if isinstance(n, str) else "",
        "boundary": bc,
        "sun": sun,
        "wind": wind,
        "normal": polygon_normal_unit(pts),
        "points": pts,
        "north_axis": north,
    }


def criteria_from(s: OSMSearchSettings) -> SearchCriteria:
    return SearchCriteria(
        cls=_val(s.cls),
        name=s.name.strip(),
        type=_val(s.type),
        construction=s.construction.strip(),
        boundary=_val(s.boundary),
        sun=_val(s.sun),
        wind=_val(s.wind),
        az_from=s.az_from,
        az_to=s.az_to,
        tilt_from=s.tilt_from,
        tilt_to=s.tilt_to,
        story=s.story.strip(),
        invert=s.invert,
        invert_across_classes=s.invert_across_classes,
        exclude_horizontal=s.exclude_horizontal,
        only_non_convex=s.only_non_convex,
        min_vertices=s.min_vertices if s.use_min_vertices else 0,
    )


def _story_of(scene, ob) -> str:
    """Building story of the object's space: the Inspector store wins over the per-object custom property."""
    from .inspector import get_item

    item = get_item(scene, "Space", _space_of(ob)) if hasattr(scene, "osm_model") else None
    story = getattr(item, "buildingStory", "") if item is not None else ""
    if story:
        return story
    v = ob.get("buildingStoryName")
    return v if isinstance(v, str) else ""


def find_objects(scene, crit: SearchCriteria, candidates) -> list:
    north = float(scene.get("northAxis") or 0.0)
    out = []
    for ob in candidates:
        if ob.type != "MESH" or is_loose(ob):
            continue
        story = _story_of(scene, ob) if crit.story else ""
        for p in ob.data.polygons:
            if len(p.vertices) >= 3:
                f = _facts(ob, p, north)
                f["story"] = story
                if matches(crit, f):
                    out.append(ob)
                    break
    return out


class _SearchBase:
    bl_options = {"REGISTER", "UNDO"}

    def candidates(self, context):
        raise NotImplementedError

    def execute(self, context):
        s = context.scene.osm_search
        found = find_objects(context.scene, criteria_from(s), self.candidates(context))
        for ob in context.view_layer.objects:
            if ob.select_get():
                ob.select_set(False)
        visible = [o for o in found if o.visible_get()]
        for ob in visible:
            ob.select_set(True)
        if visible:
            context.view_layer.objects.active = visible[0]
        s.count = len(found)
        s.report = "\n".join(o.name for o in found)
        s.hits.clear()
        for o in found:
            s.hits.add().name = o.name
        s.hit_index = -1
        if s.hide_others:
            keep = {o.name for o in found}
            hide = [o for o in context.scene.objects if o.type == "MESH" and o.name not in keep]
            hide = [o for o in hide if not is_loose(o) and not o.hide_get()]
            for o in hide:
                o.hide_set(True)
            s.hidden = "\n".join(sorted({*s.hidden.splitlines(), *(o.name for o in hide)}))
        hidden = len(found) - len(visible)
        self.report(
            {"INFO"}, f"{len(visible)} object(s) selected" + (f" ({hidden} hidden not selected)" if hidden else "")
        )
        return {"FINISHED"}


class OBJECT_OT_osm_search_model(_SearchBase, bpy.types.Operator):
    bl_idname = "object.osm_search_model"
    bl_label = "Search Entire Model"
    bl_description = "Select every object with a face matching the search"

    def candidates(self, context):
        return list(context.scene.objects)


class OBJECT_OT_osm_search_selection(_SearchBase, bpy.types.Operator):
    bl_idname = "object.osm_search_selection"
    bl_label = "Search Selection"
    bl_description = "Narrow the current selection to objects with a face matching the search"

    def candidates(self, context):
        return list(context.selected_objects)


class OBJECT_OT_osm_search_unhide(bpy.types.Operator):
    bl_idname = "object.osm_search_unhide"
    bl_label = "Unhide All"
    bl_description = "Show the objects hidden by a search with 'Hide Everything Else'"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.osm_search
        n = 0
        for name in s.hidden.splitlines():
            ob = context.scene.objects.get(name)
            if ob is not None and ob.hide_get():
                ob.hide_set(False)
                n += 1
        s.hidden = ""
        self.report({"INFO"}, f"{n} object(s) shown")
        return {"FINISHED"}


class OBJECT_OT_osm_search_view_layer(bpy.types.Operator):
    bl_idname = "object.osm_search_view_layer"
    bl_label = "View Layer From Result"
    bl_description = "Create a view layer that shows only the objects found by the last search"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="Search Result")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        scene = context.scene
        names = set(scene.osm_search.report.splitlines())
        if not names:
            self.report({"WARNING"}, "Run a search first")
            return {"CANCELLED"}
        vl = scene.view_layers.get(self.name) or scene.view_layers.new(self.name)
        for ob in scene.objects:
            if ob.type == "MESH":
                try:
                    ob.hide_set(ob.name not in names, view_layer=vl)
                except RuntimeError:  # not part of that view layer
                    pass
        self.report({"INFO"}, f"View layer '{vl.name}': {len(names)} object(s) visible")
        return {"FINISHED"}


class OBJECT_OT_osm_search_report(bpy.types.Operator):
    bl_idname = "object.osm_search_report"
    bl_label = "Last Report"
    bl_description = "List the objects found by the last search"

    def execute(self, context):
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        s = context.scene.osm_search
        names = s.report.splitlines()
        col = self.layout.column(align=True)
        col.label(text=f"{len(names)} object(s) found" if s.count >= 0 else "No search run yet")
        for n in names[:40]:
            col.label(text=n)
        if len(names) > 40:
            col.label(text=f"… {len(names) - 40} more")


class VIEW3D_PT_osm_search(PanelIcon, bpy.types.Panel):
    osm_icon = "panel_search"
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"
    bl_label = "Surface Search"
    bl_idname = "VIEW3D_PT_osm_search"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.scene.osm_search
        col = split_column(self.layout)
        for prop in ("cls", "type", "construction", "story"):
            field(col, s, prop)


class _SearchSub:
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"
    bl_parent_id = "VIEW3D_PT_osm_search"
    bl_options = {"DEFAULT_CLOSED"}

    def _begin(self, context):
        self.layout.use_property_split = False
        self.layout.use_property_decorate = False
        return context.scene.osm_search


class VIEW3D_PT_osm_search_boundary(_SearchSub, bpy.types.Panel):
    bl_label = "Boundary"
    bl_idname = "VIEW3D_PT_osm_search_boundary"

    def draw(self, context):
        s = self._begin(context)
        col = split_column(self.layout)
        col.active = s.cls in (ANY, *CLASSES_WITH_BOUNDARY)  # only base surfaces have these
        field(col, s, "boundary")
        field(col, s, "sun")
        field(col, s, "wind")


class VIEW3D_PT_osm_search_orientation(_SearchSub, bpy.types.Panel):
    bl_label = "Orientation"
    bl_idname = "VIEW3D_PT_osm_search_orientation"

    def draw(self, context):
        s = self._begin(context)
        col = split_column(self.layout)
        draw_range(col, s, "az_from", "az_to", "Azimuth")
        field_row(col, "").label(text="Clockwise, north = 0°")
        draw_range(col, s, "tilt_from", "tilt_to", "Tilt")
        col.prop(s, "exclude_horizontal")


class VIEW3D_PT_osm_search_geometry(_SearchSub, bpy.types.Panel):
    bl_label = "Geometry"
    bl_idname = "VIEW3D_PT_osm_search_geometry"

    def draw(self, context):
        s = self._begin(context)
        col = split_column(self.layout)
        col.prop(s, "only_non_convex")
        row = field_row(col, "More Than")
        row.prop(s, "use_min_vertices", text="")
        sub = row.row(align=True)
        sub.active = s.use_min_vertices
        sub.prop(s, "min_vertices", text="")


class VIEW3D_PT_osm_search_options(_SearchSub, bpy.types.Panel):
    bl_label = "Options"
    bl_idname = "VIEW3D_PT_osm_search_options"

    def draw(self, context):
        s = self._begin(context)
        col = split_column(self.layout)
        col.prop(s, "invert")
        col.prop(s, "invert_across_classes")
        col.prop(s, "hide_others")


class VIEW3D_PT_osm_search_run(_SearchSub, bpy.types.Panel):
    """Headerless last section: search buttons in one row, view helpers, result list."""

    bl_label = "Search"
    bl_idname = "VIEW3D_PT_osm_search_run"
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        s, lay = self._begin(context), self.layout
        row = lay.row(align=True)
        row.scale_y = PRIMARY_SCALE
        row.operator("object.osm_search_model", text="Model", **ik("search_model", "VIEWZOOM"))
        row.operator("object.osm_search_selection", text="Selection", **ik("search_selection", "RESTRICT_SELECT_OFF"))
        row = lay.row(align=True)
        row.operator("object.osm_search_unhide", **ik("search_unhide", "HIDE_OFF"))
        row.operator("object.osm_search_view_layer", text="View Layer", **ik("search_view_layer", "RENDERLAYERS"))
        if s.count >= 0:
            row = lay.row(align=True)
            row.label(text=f"{s.count} found")
            row.operator("object.osm_search_report", text="Report", **ik("search_report", "TEXT"))
            if len(s.hits):
                lay.template_list("OSM_UL_search_hits", "", s, "hits", s, "hit_index", rows=min(6, len(s.hits) + 1))


CLASSES_TO_REGISTER = (
    OSMSearchHit,
    OSMSearchSettings,
    OSM_UL_search_hits,
    OBJECT_OT_osm_search_model,
    OBJECT_OT_osm_search_selection,
    OBJECT_OT_osm_search_unhide,
    OBJECT_OT_osm_search_view_layer,
    OBJECT_OT_osm_search_report,
    VIEW3D_PT_osm_search,
    VIEW3D_PT_osm_search_boundary,
    VIEW3D_PT_osm_search_orientation,
    VIEW3D_PT_osm_search_geometry,
    VIEW3D_PT_osm_search_options,
    VIEW3D_PT_osm_search_run,
)


def register():
    for c in CLASSES_TO_REGISTER:
        bpy.utils.register_class(c)
    bpy.types.Scene.osm_search = PointerProperty(type=OSMSearchSettings)


def unregister():
    del bpy.types.Scene.osm_search
    for c in reversed(CLASSES_TO_REGISTER):
        bpy.utils.unregister_class(c)
