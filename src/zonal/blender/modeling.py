"""More modeling tools: spaces from a diagram, space attributes, shading / partition groups (imports bpy)."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from ..core import schema
from ..core.diagram_core import is_horizontal, plan_spaces
from ..core.geometry import polygon_normal, unit
from ..core.tools_core import surface_type_from_normal, unique_name
from ..core.volume_core import order_cells, split_into_cells
from .icons import ik
from .props import library_names
from .surface_ops import _space_of, _world_polygon, is_loose
from .ui import sync_subsurface_displace


def _plan_faces(context):
    """(object, world polygon) for every face of the selected, not yet OpenStudio, mesh objects."""
    out = []
    for ob in context.selected_objects:
        if ob.type == "MESH" and not is_loose(ob) and "surfaceType" not in ob:
            out += [(ob, _world_polygon(ob, p)) for p in ob.data.polygons]
    return out


class OBJECT_OT_osm_spaces_from_diagram(bpy.types.Operator):
    bl_idname = "object.osm_spaces_from_diagram"
    bl_label = "Create Spaces From Diagram"
    bl_description = (
        "Extrude the selected horizontal faces (a 2D floor plan) into spaces: one collection per space and floor "
        "with a floor, a roof and one wall per edge (the SketchUp plugin's 'Create Spaces From Diagram')"
    )
    bl_options = {"REGISTER", "UNDO"}

    floor_height: FloatProperty(name="Floor Height", default=3.0, min=0.01, unit="LENGTH")
    num_floors: IntProperty(name="Number of Floors", default=1, min=1, max=200)
    zone_per_space: BoolProperty(
        name="Thermal Zone per Space", default=True, description="Give every new space a thermal zone of its name"
    )
    remove_plan: BoolProperty(
        name="Remove Plan Faces", default=True, description="Delete the selected plan objects after extruding them"
    )

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def invoke(self, context, event):
        from .ui import prefs

        if prefs() is not None:
            self.zone_per_space = prefs().new_zone_for_space
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        faces = _plan_faces(context)
        if not faces:
            self.report({"WARNING"}, "Select the floor plan faces (loose geometry, not yet OpenStudio surfaces)")
            return {"CANCELLED"}
        polys = [p for _o, p in faces]
        if not all(is_horizontal(p) for p in polys):
            self.report({"ERROR"}, "Not all selected faces are horizontal: limit the selection to the floor plan")
            return {"CANCELLED"}
        try:
            spaces = plan_spaces(polys, self.floor_height, self.num_floors)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        from .inspector import ensure_item

        taken_cols = {c.name for c in bpy.data.collections}
        names = {o.name for o in bpy.data.objects}
        made = 0
        for sp in spaces:
            cname = unique_name(sp["name"], taken_cols)
            taken_cols.add(cname)
            col = bpy.data.collections.new(cname)
            context.scene.collection.children.link(col)
            for label, stype, poly in sp["surfaces"]:
                name = unique_name(f"{cname} {label}", names)
                names.add(name)
                me = bpy.data.meshes.new(name)
                me.from_pydata([tuple(p) for p in poly], [], [list(range(len(poly)))])
                me.update()
                ob = bpy.data.objects.new(name, me)
                col.objects.link(ob)
                ob["surfaceType"] = stype
                sync_subsurface_displace(ob)
                made += 1
            item = ensure_item(context.scene, "Space", cname)  # space attributes live on the space only
            item.buildingStory = sp["story"]
            if self.zone_per_space:
                item.thermalZone = cname
        if self.remove_plan:
            for ob in {o for o, _p in faces}:
                me = ob.data
                bpy.data.objects.remove(ob, do_unlink=True)
                if me.users == 0:
                    bpy.data.meshes.remove(me)
        self.report({"INFO"}, f"Created {len(spaces)} space(s) with {made} surfaces")
        return {"FINISHED"}


def _source_meshes(context):
    """Selected mesh objects that are not loose geometry and not yet OpenStudio surfaces."""
    return [o for o in context.selected_objects if o.type == "MESH" and not is_loose(o) and "surfaceType" not in o]


def _world_mesh(context, objs, apply_modifiers=True):
    """(points, faces) of the meshes in world space, n-gons kept; with the modifiers applied when asked."""
    dg = context.evaluated_depsgraph_get()
    pts, faces = [], []
    for ob in objs:
        eo = ob.evaluated_get(dg) if apply_modifiers else ob
        me = eo.to_mesh() if apply_modifiers else ob.data
        try:
            mw, base = ob.matrix_world, len(pts)
            pts += [tuple(mw @ v.co) for v in me.vertices]
            faces += [[base + i for i in p.vertices] for p in me.polygons]
        finally:
            if apply_modifiers:
                eo.to_mesh_clear()
    return pts, faces


def _self_intersect(context, pts, faces, tol):
    """Cut the faces of a polygon soup where they pass through each other (Blender's exact self intersect)."""
    me = bpy.data.meshes.new("osm_intersect")
    me.from_pydata(pts, [], faces)
    ob = bpy.data.objects.new("osm_intersect", me)
    context.scene.collection.objects.link(ob)
    prev_active = context.view_layer.objects.active
    try:
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.intersect(mode="SELECT", separate_mode="CUT", threshold=tol, solver="EXACT")
        bpy.ops.object.mode_set(mode="OBJECT")
        return [tuple(v.co) for v in me.vertices], [list(p.vertices) for p in me.polygons]
    finally:
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.meshes.remove(me)
        if prev_active is not None:
            context.view_layer.objects.active = prev_active


class OBJECT_OT_osm_spaces_from_mesh(bpy.types.Operator):
    bl_idname = "object.osm_spaces_from_mesh"
    bl_label = "Create Spaces From Mesh"
    bl_description = (
        "Turn a 3D mesh made of several volumes (closed shells, rooms that share walls, partly overlapping walls) "
        "into spaces: one collection per enclosed volume with one surface object per face, then split overlapping "
        "walls and match the shared ones. The 3D counterpart of 'Create Spaces From Diagram'"
    )
    bl_options = {"REGISTER", "UNDO"}

    tolerance: FloatProperty(name="Tolerance", default=1e-3, min=1e-6, unit="LENGTH")
    apply_modifiers: BoolProperty(
        name="Apply Modifiers",
        default=True,
        description="Use the mesh as the modifiers shape it (mirror, array, solidify...) instead of the base mesh",
    )
    self_intersect: BoolProperty(
        name="Intersect Faces First",
        default=False,
        description="Cut faces where other faces pass through them (Blender's exact self intersect), e.g. a "
        "partition that ends in the middle of a wall or rooms that overlap. Overlapping volumes become extra spaces",
    )
    merge_coplanar: BoolProperty(
        name="Merge Coplanar Faces",
        default=True,
        description="Join touching coplanar faces of one space into a single surface",
    )
    keep_open: BoolProperty(
        name="Keep Open Volumes",
        default=True,
        description="Also create a space for a volume that is not watertight (the faces are used as modelled)",
    )
    match: BoolProperty(
        name="Intersect and Match Surfaces",
        default=True,
        description="Split walls that are only partly shared and set the 'Surface' boundary condition between spaces",
    )
    assign_stories: BoolProperty(
        name="Assign Building Stories",
        default=True,
        description="Group spaces by their lowest level into Story 1, Story 2, ...",
    )
    zone_per_space: BoolProperty(
        name="Thermal Zone per Space", default=True, description="Give every new space a thermal zone of its name"
    )
    remove_source: BoolProperty(
        name="Remove Source Mesh",
        default=True,
        description="Delete the selected mesh objects after creating the spaces",
    )

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def invoke(self, context, event):
        from .ui import prefs

        if prefs() is not None:
            self.zone_per_space = prefs().new_zone_for_space
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if context.mode.startswith("EDIT"):
            bpy.ops.object.mode_set(mode="OBJECT")
        sources = _source_meshes(context)
        if not sources:
            self.report(
                {"WARNING"}, "Select the mesh with the volumes (not loose geometry, not yet OpenStudio surfaces)"
            )
            return {"CANCELLED"}
        pts, faces = _world_mesh(context, sources, self.apply_modifiers)
        if self.self_intersect:
            try:
                pts, faces = _self_intersect(context, pts, faces, self.tolerance)
            except RuntimeError as e:
                self.report({"WARNING"}, f"Self intersect failed, continuing without it: {e}")
        cells = split_into_cells(
            pts, faces, min(self.tolerance, 1e-3), merge_coplanar=self.merge_coplanar, keep_open=self.keep_open
        )
        if not cells:
            self.report({"ERROR"}, "No enclosed volume found in the selected mesh")
            return {"CANCELLED"}
        from .inspector import ensure_item

        taken_cols = {c.name for c in bpy.data.collections}
        names = {o.name for o in bpy.data.objects}
        per_story: dict[int, int] = {}
        created, open_names, n_spaces = [], [], 0
        for cell, story in order_cells(cells):
            per_story[story] = k = per_story.get(story, 0) + 1
            cname = unique_name(f"Story {story + 1} Space {k}", taken_cols)
            taken_cols.add(cname)
            col = bpy.data.collections.new(cname)
            context.scene.collection.children.link(col)
            counts: dict[str, int] = {}
            for poly in cell["polys"]:
                stype = surface_type_from_normal(unit(polygon_normal(poly))[2])
                label = {"Floor": "Floor", "RoofCeiling": "Roof", "Wall": "Wall"}[stype]
                counts[label] = counts.get(label, 0) + 1
                name = unique_name(f"{cname} {label} {counts[label]}", names)
                names.add(name)
                me = bpy.data.meshes.new(name)
                me.from_pydata([tuple(p) for p in poly], [], [list(range(len(poly)))])
                me.update()
                ob = bpy.data.objects.new(name, me)
                col.objects.link(ob)
                ob["surfaceType"] = stype
                sync_subsurface_displace(ob)
                created.append(ob)
            item = ensure_item(context.scene, "Space", cname)  # space attributes live on the space only
            if self.assign_stories:
                item.buildingStory = f"Story {story + 1}"
            if self.zone_per_space:
                item.thermalZone = cname
            n_spaces += 1
            if cell["open"]:
                open_names.append(cname)
        if self.remove_source:
            for ob in sources:
                me = ob.data
                bpy.data.objects.remove(ob, do_unlink=True)
                if me.users == 0:
                    bpy.data.meshes.remove(me)
        msg = f"Created {n_spaces} space(s) with {len(created)} surfaces"
        if self.match:
            for o in list(context.selected_objects):
                o.select_set(False)
            for ob in created:
                try:
                    ob.select_set(True)
                except RuntimeError:  # collection excluded from the view layer
                    pass
            for mode in ("INTERSECT", "MATCH"):
                try:
                    bpy.ops.object.osm_match_surfaces(mode=mode, scope="SELECTED", tolerance=self.tolerance)
                except RuntimeError as e:
                    self.report({"WARNING"}, f"Surface matching ({mode.lower()}) failed: {e}")
            matched = sum(1 for o in bpy.data.objects if o.get("outsideBoundaryCondition") == "Surface")
            msg += f"; {matched} surfaces matched"
        if open_names:
            self.report({"WARNING"}, f"Not watertight (faces used as modelled): {', '.join(open_names[:5])}")
        self.report({"INFO"}, msg)
        return {"FINISHED"}


def _library_search(category: str):
    def search(self, context, edit_text):
        return library_names(context.scene, category)

    return search


# operator property -> (library category, object custom property, Inspector Space field)
_ATTRS = {
    "space_type": ("spacetype", "spaceTypeName", "spaceType"),
    "thermal_zone": ("zone", "thermalZoneName", "thermalZone"),
    "building_story": ("story", "buildingStoryName", "buildingStory"),
    "building_unit": ("unit", "buildingUnitName", "buildingUnit"),
    "construction_set": ("cset", "constructionSetName", "defaultConstructionSet"),
}


class OBJECT_OT_osm_set_space_attributes(bpy.types.Operator):
    bl_idname = "object.osm_set_space_attributes"
    bl_label = "Set Attributes for Selected Spaces"
    bl_description = (
        "Assign a space type, thermal zone, building story, building unit or construction set to every space "
        "that has a selected surface. Empty fields are left unchanged"
    )
    bl_options = {"REGISTER", "UNDO"}

    space_type: StringProperty(name="Space Type", search=_library_search("spacetype"), search_options={"SUGGESTION"})
    thermal_zone: StringProperty(name="Thermal Zone", search=_library_search("zone"), search_options={"SUGGESTION"})
    building_story: StringProperty(
        name="Building Story", search=_library_search("story"), search_options={"SUGGESTION"}
    )
    building_unit: StringProperty(name="Building Unit", search=_library_search("unit"), search_options={"SUGGESTION"})
    construction_set: StringProperty(
        name="Construction Set", search=_library_search("cset"), search_options={"SUGGESTION"}
    )
    zone_per_space: BoolProperty(
        name="New Thermal Zone per Space", default=False, description="Name each space's zone like the space"
    )

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        from .inspector import ensure_item

        scene = context.scene
        spaces = {_space_of(o) for o in context.selected_objects if o.type == "MESH" and not is_loose(o)} - {""}
        if not spaces:
            self.report({"WARNING"}, "No selected surface belongs to a space")
            return {"CANCELLED"}
        values = {k: getattr(self, k) for k in _ATTRS if getattr(self, k)}
        if not values and not self.zone_per_space:
            self.report({"WARNING"}, "Nothing to set: fill at least one field")
            return {"CANCELLED"}
        for sp in spaces:  # the space is the only place these values live
            item = ensure_item(scene, "Space", sp)
            for key, value in values.items():
                field = _ATTRS[key][2]
                if hasattr(item, field):
                    setattr(item, field, value)
            if self.zone_per_space and hasattr(item, "thermalZone"):
                item.thermalZone = sp
        self.report({"INFO"}, f"Updated {len(spaces)} space(s)")
        return {"FINISHED"}


class OBJECT_OT_osm_set_shading(bpy.types.Operator):
    bl_idname = "object.osm_set_shading"
    bl_label = "Set as Shading Surface"
    bl_description = (
        "Turn the selected objects into shading surfaces of a Site, Building or Space shading group "
        "(the SketchUp plugin's 'New Shading Surface Group')"
    )
    bl_options = {"REGISTER", "UNDO"}

    group: EnumProperty(
        name="Group",
        items=[
            ("SiteShading", "Site", "Shading that belongs to the site (trees, neighbouring buildings)"),
            ("BuildingShading", "Building", "Shading that belongs to the building (overhangs, fins)"),
            ("SpaceShading", "Space", "Shading that belongs to one space (moves with it)"),
        ],
        default="BuildingShading",
    )
    space: StringProperty(name="Space", description="Space of a Space shading group (empty: each object's own space)")

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context):
        n = 0
        for ob in context.selected_objects:
            if ob.type != "MESH" or is_loose(ob):
                continue
            ob["surfaceType"] = self.group
            if self.group == "SpaceShading":
                sp = self.space or _space_of(ob)
                if sp:
                    ob["spaceName"] = sp
            elif "spaceName" in ob:
                del ob["spaceName"]
            sync_subsurface_displace(ob)
            n += 1
        self.report({"INFO"}, f"{n} object(s) set to {self.group}")
        return {"FINISHED"} if n else {"CANCELLED"}


class OBJECT_OT_osm_set_partition(bpy.types.Operator):
    bl_idname = "object.osm_set_partition"
    bl_label = "Set as Interior Partition"
    bl_description = (
        "Turn the selected objects into interior partition surfaces of their space "
        "(the SketchUp plugin's 'New Interior Partition Surface Group')"
    )
    bl_options = {"REGISTER", "UNDO"}

    space: StringProperty(name="Space", description="Space of the partitions (empty: each object's own space)")

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context):
        n = 0
        for ob in context.selected_objects:
            if ob.type != "MESH" or is_loose(ob):
                continue
            ob["surfaceType"] = schema.PARTITION_TYPE
            if self.space:
                from .props import assign_space

                assign_space(ob, self.space)
            sync_subsurface_displace(ob)
            n += 1
        self.report({"INFO"}, f"{n} object(s) set to {schema.PARTITION_TYPE}")
        return {"FINISHED"} if n else {"CANCELLED"}


def hover_lines(context, ob, poly_index: int) -> list[str]:
    """Info Tool text for face ``poly_index`` of ``ob`` (empty for objects that are not OpenStudio surfaces)."""
    from ..core.info_core import describe
    from ..core.search_core import class_of
    from ..core.tools_geometry import polygon_area_3d, polygon_normal_unit
    from .surface_ops import _surface_type

    if ob is None or ob.type != "MESH" or is_loose(ob) or not 0 <= poly_index < len(ob.data.polygons):
        return []
    poly = ob.data.polygons[poly_index]
    stype = _surface_type(ob, poly)
    pts = _world_polygon(ob, poly)
    scale = context.scene.unit_settings.scale_length or 1.0
    props = {k: ob[k] for k in ob.keys() if not k.startswith("_")}
    return describe(
        ob.name,
        stype,
        class_of(stype),
        props,
        polygon_area_3d(pts) * scale * scale,
        polygon_normal_unit(pts),
        float(context.scene.get("northAxis") or 0.0),
        _space_of(ob),
    )


def _draw_info(op):
    import blf

    if not op.lines:
        return
    x, y = op.mouse
    blf.size(0, 14.0)
    blf.enable(0, blf.SHADOW)
    blf.shadow(0, 3, 0.0, 0.0, 0.0, 1.0)
    blf.color(0, 1.0, 1.0, 1.0, 1.0)
    for i, line in enumerate(op.lines):
        blf.position(0, x + 18, y - 20 - 18 * i, 0)
        blf.draw(0, line)
    blf.disable(0, blf.SHADOW)


class VIEW3D_OT_osm_info_tool(bpy.types.Operator):
    bl_idname = "view3d.osm_info_tool"
    bl_label = "Info Tool"
    bl_description = (
        "Hover a surface to see its OpenStudio type, space, zone, construction, boundary, area and orientation. "
        "Click selects it, Esc / right click leaves the tool"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D" and context.region_data is not None

    def invoke(self, context, event):
        self.lines, self.mouse, self.hit = [], (event.mouse_region_x, event.mouse_region_y), None
        self._handle = bpy.types.SpaceView3D.draw_handler_add(_draw_info, (self,), "WINDOW", "POST_PIXEL")
        context.workspace.status_text_set("Info Tool: hover a surface | LMB select | Esc / RMB finish")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _end(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
        context.workspace.status_text_set(None)
        if context.area:
            context.area.tag_redraw()

    def _pick(self, context, event):
        from bpy_extras import view3d_utils

        region, rv3d = context.region, context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        ok, _loc, _nor, index, ob, _mat = context.scene.ray_cast(context.view_layer.depsgraph, origin, direction)
        return (ob, index) if ok else (None, -1)

    def modal(self, context, event):
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM"} or (
            event.type.startswith(("NDOF", "NUMPAD")) and event.type != "NUMPAD_ENTER"
        ):
            return {"PASS_THROUGH"}
        if event.type == "MOUSEMOVE":
            self.mouse = (event.mouse_region_x, event.mouse_region_y)
            self.hit = self._pick(context, event)
            self.lines = hover_lines(context, *self.hit)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            ob = self.hit[0] if self.hit else None
            if ob is not None:
                for o in context.view_layer.objects.selected:
                    o.select_set(False)
                ob.select_set(True)
                context.view_layer.objects.active = ob
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._end(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


class VIEW3D_PT_osm_modeling_spaces(bpy.types.Panel):
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"
    bl_label = "Spaces & Groups"
    bl_idname = "VIEW3D_PT_osm_modeling_spaces"
    bl_parent_id = "VIEW3D_PT_osm_modeling"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator("object.osm_spaces_from_diagram", **ik("model_diagram", "MOD_BUILD"))
        col.operator("object.osm_spaces_from_mesh", **ik("model_mesh_spaces", "MESH_CUBE"))
        col.operator("object.osm_set_space_attributes", **ik("model_attributes", "PROPERTIES"))
        col.operator("scene.osm_zone_per_space", icon="GROUP")


class VIEW3D_PT_osm_modeling_shading(bpy.types.Panel):
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"
    bl_label = "Shading"
    bl_idname = "VIEW3D_PT_osm_modeling_shading"
    bl_parent_id = "VIEW3D_PT_osm_modeling"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        lay = self.layout
        row = lay.row(align=True)
        for group, label in (("SiteShading", "Site"), ("BuildingShading", "Building"), ("SpaceShading", "Space")):
            row.operator("object.osm_set_shading", text=label).group = group
        lay.operator("object.osm_set_partition", **ik("model_partition", "MOD_WIREFRAME"))


class VIEW3D_MT_PIE_osm_tools(bpy.types.Menu):
    bl_label = "OpenStudio"

    def draw(self, context):
        pie = self.layout.menu_pie()
        op = pie.operator("wm.tool_set_by_id", text="Draw Rectangle", **ik("tool_draw_rectangle", "MESH_PLANE"))
        op.name = "openstudio.draw_rectangle"
        pie.operator("object.osm_separate_faces", **ik("model_separate_faces", "MOD_EXPLODE"))
        op = pie.operator("object.osm_match_surfaces", text="Match Selection", **ik("model_match_surfaces"))
        op.mode, op.scope = "MATCH", "SELECTED"
        pie.operator("view3d.osm_info_tool", **ik("model_info", "INFO"))
        pie.operator("object.osm_set_space_attributes", **ik("model_attributes", "PROPERTIES"))
        op = pie.operator("view3d.osm_render_mode", text="Color by Surface Type")
        op.mode = "SURFACE_TYPE"
        op = pie.operator("view3d.osm_render_mode", text="Color Off", icon="LOOP_BACK")
        op.mode = "OFF"


_keymaps: list = []


def _register_keymap():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:  # background mode
        return
    km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
    kmi = km.keymap_items.new("wm.call_menu_pie", "O", "PRESS", ctrl=True, shift=True)
    kmi.properties.name = "VIEW3D_MT_PIE_osm_tools"
    _keymaps.append((km, kmi))


CLASSES = (
    VIEW3D_MT_PIE_osm_tools,
    OBJECT_OT_osm_spaces_from_diagram,
    OBJECT_OT_osm_spaces_from_mesh,
    OBJECT_OT_osm_set_space_attributes,
    OBJECT_OT_osm_set_shading,
    OBJECT_OT_osm_set_partition,
    VIEW3D_OT_osm_info_tool,
    VIEW3D_PT_osm_modeling_spaces,
    VIEW3D_PT_osm_modeling_shading,
)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    _register_keymap()


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
