"""Modeling tools: rectangle draw tool, separate faces, render modes and the sidebar panels (imports bpy)."""

from __future__ import annotations

import bpy
import gpu
import numpy as np
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, StringProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector, geometry

from ..core import tools_geometry
from ..core.snap_core import SnapGeometry
from ..core.tools_core import surface_type_from_normal, unique_name
from .icons import PanelIcon, ik, tool_icon
from .ui import DISPLACE_NAME, sync_subsurface_displace
from .widgets import field_row

SNAP_PX = 14.0
SNAP_MAX_EDGES = 400_000  # total edges cached for snapping; objects beyond the budget are not snapped to
SNAP_TYPES = {"MESH", "CURVE", "SURFACE", "FONT"}


def _pref(name: str, default):
    """Add-on preference ``name`` (the module constant ``default`` when preferences are unavailable)."""
    from .ui import prefs

    p = prefs()
    return getattr(p, name, default) if p is not None else default


SNAP_COLORS = {
    "first": (1.0, 1.0, 1.0, 1.0),
    "vertex": (1.0, 0.85, 0.1, 1.0),
    "midpoint": (0.2, 0.9, 1.0, 1.0),
    "edge": (0.4, 1.0, 0.3, 1.0),
    "grid": (0.7, 0.7, 0.7, 1.0),
}


# --- separate faces ------------------------------------------------------------------


def _eligible(ob, max_faces: int | None = None) -> bool:
    if ob.type != "MESH" or ob.children or any(m.name != DISPLACE_NAME for m in ob.modifiers) or ob.data.shape_keys:
        return False
    n = len(ob.data.polygons)
    return n >= 2 and (max_faces is None or n <= max_faces)


def _plain(v):
    if hasattr(v, "to_dict"):
        return v.to_dict()
    if hasattr(v, "to_list"):
        return v.to_list()
    return v


def separate_faces(context, objects, set_surface_type: bool = True, max_faces: int | None = None) -> int:
    """Replace every multi-face mesh object by one object per face. Returns the number of objects created."""
    created = []
    todo = [o for o in objects if _eligible(o, max_faces)]
    if todo:  # deselect before deleting, otherwise the selection holds dead references
        for o in list(context.view_layer.objects.selected):
            o.select_set(False)
    for ob in todo:
        me, mw = ob.data, ob.matrix_world.copy()
        rot = mw.to_3x3()
        faces = []
        for poly in me.polygons:
            coords = [me.vertices[i].co.copy() for i in poly.vertices]
            nz = (rot @ poly.normal).normalized().z
            faces.append((coords, nz))
        props = {k: _plain(ob[k]) for k in ob.keys() if not k.startswith("_")}
        cols, parent, pinv, base = list(ob.users_collection), ob.parent, ob.matrix_parent_inverse.copy(), ob.name
        bpy.data.objects.remove(ob, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)
        names = {o.name for o in bpy.data.objects}
        for coords, nz in faces:
            name = unique_name(base, names)
            names.add(name)
            new_me = bpy.data.meshes.new(name)
            new_me.from_pydata([tuple(c) for c in coords], [], [list(range(len(coords)))])
            new_me.update()
            new = bpy.data.objects.new(name, new_me)
            for c in cols:
                c.objects.link(new)
            new.parent, new.matrix_parent_inverse, new.matrix_world = parent, pinv, mw
            for k, v in props.items():
                new[k] = v
            if set_surface_type and "surfaceType" not in new:
                new["surfaceType"] = surface_type_from_normal(nz)
            created.append(new)
    if created:
        for o in created:
            try:
                o.select_set(True)
            except RuntimeError:  # collection excluded from the view layer
                pass
        try:
            context.view_layer.objects.active = created[-1]
        except (RuntimeError, ReferenceError):
            pass
    return len(created)


class OBJECT_OT_osm_separate_faces(bpy.types.Operator):
    bl_idname = "object.osm_separate_faces"
    bl_label = "Separate Faces"
    bl_description = (
        "Split every face of the selected meshes into its own object (one OpenStudio surface each); "
        "custom properties are copied and a missing surfaceType is set from the face tilt"
    )
    bl_options = {"REGISTER", "UNDO"}

    set_surface_type: BoolProperty(name="Set Surface Type", default=True)

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context):
        if context.mode.startswith("EDIT"):
            bpy.ops.object.mode_set(mode="OBJECT")
        n = separate_faces(context, list(context.selected_objects), self.set_surface_type)
        if not n:
            self.report({"INFO"}, "Nothing to separate (need meshes with 2+ faces, no modifiers or children)")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Created {n} face objects")
        return {"FINISHED"}


# --- draw tool helpers ---------------------------------------------------------------

DRAW_OP = "view3d.osm_draw_rectangle"


def _active_draw_props(context):
    """Operator properties of the active draw tool (its settings live on the tool, not on the scene), or None."""
    try:
        tool = context.workspace.tools.from_space_view3d_mode("OBJECT")
        if tool is not None and tool.idname in DRAW_TOOL_IDS:
            return tool.operator_properties(DRAW_OP)
    except Exception:
        pass
    return None


@persistent
def _geo_dirty_handler(scene, depsgraph=None):
    """Geometry / transform / collection changes invalidate the snap cache."""
    global _scene_geo_dirty
    if depsgraph is not None and not _scene_geo_dirty:
        for u in depsgraph.updates:
            if u.is_updated_geometry or u.is_updated_transform or isinstance(u.id, bpy.types.Collection):
                _scene_geo_dirty = True
                break


# --- draw surface tool ---------------------------------------------------------------


def _uniform_shader():
    for name in ("UNIFORM_COLOR", "3D_UNIFORM_COLOR"):
        try:
            return gpu.shader.from_builtin(name)
        except Exception:
            continue
    return None


def _draw_preview(op):
    pts = list(op.pts)
    if op.hover is not None:
        pts.append(op.hover)
    shader = _uniform_shader()
    if shader is None or not pts:
        return
    shader.bind()
    try:
        gpu.state.line_width_set(2.0)
        gpu.state.point_size_set(8.0)
    except AttributeError:
        pass
    coords = [tuple(p) for p in pts]
    if len(pts) >= 3:
        coords.append(coords[0])
    shader.uniform_float("color", (1.0, 0.55, 0.1, 1.0))
    batch_for_shader(shader, "LINE_STRIP", {"pos": coords}).draw(shader)
    shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
    batch_for_shader(shader, "POINTS", {"pos": [tuple(p) for p in op.pts]}).draw(shader)


_drawing = False  # a draw operator is running: it shows its own marker, the hover preview stays hidden
_hovering = False
DRAW_TOOL_IDS = ("openstudio.draw_rectangle",)


def _draw_snap_marker(op):
    """Small square at the active snap point (screen space), coloured by snap kind."""
    if op.snap is None or (_drawing and isinstance(op, VIEW3D_OT_osm_draw_hover)):
        return
    pos, kind = op.snap
    sp = view3d_utils.location_3d_to_region_2d(op.region, op.region_data, pos)
    shader = _uniform_shader()
    if sp is None or shader is None:
        return
    r = 6.0
    x, y = sp.x, sp.y
    shader.bind()
    try:
        gpu.state.line_width_set(2.0)
    except AttributeError:
        pass
    shader.uniform_float("color", SNAP_COLORS.get(kind, (1.0, 1.0, 1.0, 1.0)))
    batch_for_shader(
        shader, "LINE_LOOP", {"pos": [(x - r, y - r), (x + r, y - r), (x + r, y + r), (x - r, y + r)]}
    ).draw(shader)


_scene_geo = None
_scene_geo_dirty = True


def _build_scene_geometry(context) -> SnapGeometry:
    """Vertices and edges of every visible object in world space (evaluated, so modifiers and instances count)."""
    verts, e1s, e2s, total = [], [], [], 0
    max_edges = _pref("snap_max_edges", SNAP_MAX_EDGES)
    for inst in context.evaluated_depsgraph_get().object_instances:
        ob = inst.object
        if not inst.show_self or ob.type not in SNAP_TYPES:
            continue
        try:
            me = ob.to_mesh()
        except RuntimeError:
            continue
        try:
            nv, ne = len(me.vertices), len(me.edges)
            if nv == 0 or total + ne > max_edges:
                continue
            co = np.empty(nv * 3, dtype=np.float32)
            me.vertices.foreach_get("co", co)
            mw = np.array(inst.matrix_world, dtype=np.float64)
            wco = co.reshape(-1, 3).astype(np.float64) @ mw[:3, :3].T + mw[:3, 3]
            ed = np.empty(ne * 2, dtype=np.int32)
            me.edges.foreach_get("vertices", ed)
            ed = ed.reshape(-1, 2)
        finally:
            ob.to_mesh_clear()
        total += ne
        verts.append(wco)
        if ne:
            e1s.append(wco[ed[:, 0]])
            e2s.append(wco[ed[:, 1]])
    cat = lambda parts: np.concatenate(parts) if parts else None
    return SnapGeometry(cat(verts), cat(e1s), cat(e2s))


def _scene_geometry(context) -> SnapGeometry:
    global _scene_geo, _scene_geo_dirty
    if _scene_geo is None or _scene_geo_dirty:
        _scene_geo, _scene_geo_dirty = _build_scene_geometry(context), False
    return _scene_geo


def _occluded(context, p: Vector) -> bool:
    """True when a surface lies between the viewer and p (so p is not a point the user can see)."""
    rv3d = context.region_data
    inv = rv3d.view_matrix.inverted()
    if rv3d.is_perspective:
        origin = inv.translation
        d = p - origin
        dist = d.length
        if dist < 1e-9:
            return False
        direction = d / dist
    else:
        direction = inv.to_3x3() @ Vector((0.0, 0.0, -1.0))
        dist = 1e4
        origin = p - direction * dist
    tol = 1e-3 * dist + 1e-4
    return bool(context.scene.ray_cast(context.view_layer.depsgraph, origin, direction, distance=dist - tol)[0])


def _pick_ray(self, context, event):
    co = (event.mouse_region_x, event.mouse_region_y)
    return (
        view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, co),
        view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, co),
        co,
    )


def _pick_point(self, context, event):
    """World point under the cursor on the drawing plane, plus whether it snapped to the first point."""
    origin, direction, co = _pick_ray(self, context, event)
    hit, loc, nrm, _idx, _hit_ob, _m = context.scene.ray_cast(context.view_layer.depsgraph, origin, direction)
    if self.plane is None:
        if hit:
            n = Vector(nrm)
            if n.dot(direction) > 0:
                n.negate()
            self.plane = (Vector(loc), n.normalized())
        else:
            n = Vector((0.0, 0.0, 1.0))
            if n.dot(direction) > 0:
                n.negate()
            self.plane = (context.scene.cursor.location.copy(), n)
    p = geometry.intersect_line_plane(origin, origin + direction, self.plane[0], self.plane[1])
    if p is None:
        return None, False
    # snapping: the first point closes the loop; otherwise the best of the drawn polyline and every visible
    # object (cached, vectorised), ranked by pixel distance + a per-kind bias (vertex < midpoint < edge)
    self.snap = None
    region, rv3d = context.region, context.region_data
    ui = context.preferences.system.ui_scale
    max_px = _pref("snap_radius_px", SNAP_PX) * ui
    mouse = np.array(co, dtype=np.float64)
    best, kind = None, ""
    if len(self.pts) >= 3:
        sp = view3d_utils.location_3d_to_region_2d(region, rv3d, self.pts[0])
        if sp is not None and (sp - Vector(co)).length < max_px:
            best, kind = self.pts[0], "first"
    if best is None:
        persp = np.array(rv3d.perspective_matrix, dtype=np.float64)
        o_np, d_np = np.array(origin), np.array(direction.normalized())
        args = (persp, region.width, region.height, mouse, o_np, d_np, max_px)
        found = []
        if self.ALLOW_AXIS and len(self.pts) >= 2:
            drawn = np.array([tuple(q) for q in self.pts], dtype=np.float64)
            found.append(SnapGeometry(drawn[1:], drawn[:-1], drawn[1:]).nearest(*args, bias_scale=ui))
        scene_hit = _scene_geometry(context).nearest(*args, bias_scale=ui)
        if scene_hit is not None and not _occluded(context, Vector(scene_hit[2])):
            found.append(scene_hit)
        found = [f for f in found if f is not None]
        if found:
            _score, kind, xyz = min(found, key=lambda f: f[0])
            best = Vector(xyz)
    pl_p, pl_n = self.plane
    snapped = best is not None
    q = p
    if snapped:
        # project the snapped point onto the drawing plane
        q = best - pl_n * (best - pl_p).dot(pl_n)
        if kind == "first":
            self.snap = (q, kind)
            return q, True
    # axis lock: slide along the chosen world axis (projected into the drawing plane) from the last point
    ref = self._ref()
    direction = None
    if self.axis and ref is not None and self.ALLOW_AXIS:
        d = tools_geometry.axis_in_plane(self.axis, tuple(pl_n))
        if d is not None:
            direction = d
            q = Vector(tools_geometry.constrain_to_direction(tuple(q), tuple(ref), d))
    # Ctrl: snap to the grid increment (distance along the locked axis, else world grid)
    if event.ctrl and not snapped:
        step = getattr(self, "snap_increment", 0.1)
        q = Vector(
            tools_geometry.snap_to_grid(
                tuple(q),
                step,
                tuple(pl_p),
                tuple(pl_n),
                ref=tuple(ref) if direction is not None else None,
                direction=direction,
            )
        )
        kind = "grid"
    if snapped or kind == "grid":
        self.snap = (q, kind)
    return q, False


def _hover_watch():
    """Timer: start the snap preview as soon as a draw tool becomes active (tool keymaps don't see plain mouse moves)."""
    try:
        if not _hovering:
            for win in bpy.context.window_manager.windows:
                try:
                    tool = win.workspace.tools.from_space_view3d_mode("OBJECT")
                except Exception:
                    continue
                if tool is None or tool.idname not in DRAW_TOOL_IDS:
                    continue
                for area in win.screen.areas:
                    region = next((r for r in area.regions if r.type == "WINDOW"), None)
                    if area.type == "VIEW_3D" and region is not None:
                        with bpy.context.temp_override(window=win, area=area, region=region):
                            bpy.ops.view3d.osm_draw_hover("INVOKE_DEFAULT")
                        return 0.3
    except Exception:
        pass
    return 0.3


class _HoverState:
    """Stand-in for the draw operator's state so its _pick can run before the first click."""

    ALLOW_AXIS = False
    axis = None
    snap_increment = 0.1

    def __init__(self):
        self.pts, self.plane, self.snap = [], None, None

    def _ref(self):
        return None

    def _ray(self, context, event):
        return _pick_ray(self, context, event)


class VIEW3D_OT_osm_draw_hover(bpy.types.Operator):
    """Shows the snap marker while a draw tool is active, before the first click (never swallows events)."""

    bl_idname = "view3d.osm_draw_hover"
    bl_label = "Draw Tool Snap Preview"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D" and context.region_data is not None

    def invoke(self, context, event):
        global _hovering
        if _hovering:
            return {"PASS_THROUGH"}
        _hovering = True
        self.region, self.region_data = context.region, context.region_data
        self.state, self.snap = _HoverState(), None
        self._handle = bpy.types.SpaceView3D.draw_handler_add(_draw_snap_marker, (self,), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL", "PASS_THROUGH"}

    def _stop(self, context):
        global _hovering
        _hovering = False
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
        if context.area:
            context.area.tag_redraw()

    def modal(self, context, event):
        try:
            tool = context.workspace.tools.from_space_view3d_mode(context.mode)
            active = tool is not None and tool.idname in DRAW_TOOL_IDS
        except Exception:
            active = True
        if not active or context.area is None or context.region is None:
            self._stop(context)
            return {"CANCELLED", "PASS_THROUGH"}
        if event.type in {"MOUSEMOVE", "LEFT_CTRL", "RIGHT_CTRL"} and not _drawing:
            inside = 0 <= event.mouse_region_x <= self.region.width and 0 <= event.mouse_region_y <= self.region.height
            self.state.plane, self.state.snap = None, None
            props = _active_draw_props(context)
            if props is not None:
                self.state.snap_increment = props.snap_increment
            if inside:
                _pick_point(self.state, context, event)
            self.snap = self.state.snap
            context.area.tag_redraw()
        return {"PASS_THROUGH"}


class _DrawBase:
    """Shared state / lifecycle of the draw operators (a mixin: Blender mishandles subclassed operators)."""

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D" and context.region_data is not None

    # picking ------------------------------------------------------------------------
    def _ray(self, context, event):
        return _pick_ray(self, context, event)

    def _pick(self, context, event):
        """World point under the cursor on the drawing plane, plus whether it snapped to the first point."""
        return _pick_point(self, context, event)

    ALLOW_AXIS = True
    axis = None
    snap = None  # (world point, kind) of the active snap, drawn as a small square

    def _ref(self):
        """Point the axis lock / distance snapping is measured from."""
        return self.pts[-1] if self.pts else None

    def _status(self, context):
        extra = f" | Axis: {self.axis}" if self.axis else ""
        context.workspace.status_text_set(self.HELP + extra)

    # lifecycle ----------------------------------------------------------------------
    def invoke(self, context, event):
        self.pts, self.plane, self.hover, self.at_first, self.axis = [], None, None, False, None
        self.hover, self.at_first = self._pick(context, event)
        if self.hover is None:
            self.report({"WARNING"}, "View is parallel to the drawing plane")
            return {"CANCELLED"}
        self.pts.append(self.hover.copy())
        self._add_handlers(context)
        context.workspace.status_text_set(self.HELP)
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _add_handlers(self, context):
        global _drawing
        _drawing = True
        self.region, self.region_data = context.region, context.region_data
        self._handle = bpy.types.SpaceView3D.draw_handler_add(_draw_preview, (self,), "WINDOW", "POST_VIEW")
        self._handle_px = bpy.types.SpaceView3D.draw_handler_add(_draw_snap_marker, (self,), "WINDOW", "POST_PIXEL")

    def _end(self, context):
        global _drawing
        _drawing = False
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
        bpy.types.SpaceView3D.draw_handler_remove(self._handle_px, "WINDOW")
        context.workspace.status_text_set(None)
        if context.area:
            context.area.tag_redraw()

    def modal(self, context, event):
        if (
            event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM"}
            or event.type.startswith(("NDOF", "NUMPAD"))
            and event.type != "NUMPAD_ENTER"
        ):
            return {"PASS_THROUGH"}
        if event.type in {"X", "Y", "Z"} and event.value == "PRESS":
            self.axis = None if self.axis == event.type else event.type  # same key again releases the lock
            self._status(context)
            event_type = "MOUSEMOVE"
        else:
            event_type = event.type
        if event_type in {"MOUSEMOVE", "LEFT_CTRL", "RIGHT_CTRL"}:
            p, self.at_first = self._pick(context, event)
            if p is not None:
                self.hover = p
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type == "ESC" and event.value == "PRESS":
            self._end(context)
            return {"CANCELLED"}
        if event.type == "BACK_SPACE" and event.value == "PRESS":
            if len(self.pts) > 1:
                self.pts.pop()
            else:
                self._end(context)
                return {"CANCELLED"}
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            p, at_first = self._pick(context, event)
            if p is None:
                return {"RUNNING_MODAL"}
            if at_first:
                return self._finish(context)
            if not self.pts or (p - self.pts[-1]).length > 1e-6:
                self.pts.append(p.copy())
            return {"RUNNING_MODAL"}
        if event.type in {"RET", "NUMPAD_ENTER", "RIGHTMOUSE"} and event.value == "PRESS":
            return self._finish(context)
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        self._end(context)
        assert self.plane is not None
        return self._create(context, self.pts, self.plane[1])

    def _create(self, context, pts, plane_normal):
        """Create one surface object from world points; wound counter-clockwise around plane_normal."""
        pts = list(pts)
        if len(pts) < 3:
            return {"CANCELLED"}
        newell = Vector((0.0, 0.0, 0.0))
        for a, b in zip(pts, pts[1:] + pts[:1]):
            newell += a.cross(b)
        if newell.length < 1e-9:
            self.report({"WARNING"}, "Degenerate polygon")
            return {"CANCELLED"}
        if newell.dot(plane_normal) < 0:  # wind counter-clockwise around the plane normal (facing the viewer)
            pts.reverse()
            newell.negate()
        stype = surface_type_from_normal(newell.normalized().z)
        name = unique_name(stype, {o.name for o in bpy.data.objects})
        me = bpy.data.meshes.new(name)
        me.from_pydata([tuple(p) for p in pts], [], [list(range(len(pts)))])
        me.update()
        ob = bpy.data.objects.new(name, me)
        auto_boundary = bool(getattr(self, "auto_boundary", False))
        if auto_boundary or getattr(self, "clone_to_loose", False):  # drawing aid: no surface type, never exported
            from .surface_ops import loose_collection

            loose_collection(create=True).objects.link(ob)
        else:
            context.collection.objects.link(ob)
            ob["surfaceType"] = stype
            sync_subsurface_displace(ob)
        for o in context.view_layer.objects.selected:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        if auto_boundary:
            res = bpy.ops.object.osm_project_loose(scope="SELECTED", surface_type="AUTO")
            if "FINISHED" not in res:
                self.report({"WARNING"}, "Auto Boundary: the rectangle does not lie on any surface")
        return {"FINISHED"}


class VIEW3D_OT_osm_draw_rectangle(_DrawBase, bpy.types.Operator):
    bl_idname = "view3d.osm_draw_rectangle"
    bl_label = "Draw Rectangle Surface"
    bl_description = (
        "Click a corner, then drag/click the opposite corner to draw a rectangular OpenStudio surface. "
        "Width / Height > 0 (tool settings) fix that dimension; both fixed places it with one click"
    )
    bl_options = {"REGISTER", "UNDO"}

    HELP = "Draw Rectangle: move + LMB place opposite corner | Ctrl: snap to grid | RMB / Esc: cancel"
    ALLOW_AXIS = False  # a rectangle is already aligned to the plane's axes

    def _ref(self):
        return Vector(self.origin)

    fixed_width: FloatProperty(
        name="Fixed Width",
        default=0.0,
        min=0.0,
        subtype="DISTANCE",
        unit="LENGTH",
        description="Fixed width (0 = set by dragging)",
    )
    fixed_height: FloatProperty(
        name="Fixed Height",
        default=0.0,
        min=0.0,
        subtype="DISTANCE",
        unit="LENGTH",
        description="Fixed height (0 = set by dragging)",
    )
    snap_increment: FloatProperty(
        name="Snap Increment",
        default=0.1,
        min=0.001,
        subtype="DISTANCE",
        unit="LENGTH",
        description="Grid step used while holding Ctrl",
    )
    clone_to_loose: BoolProperty(
        name="Clone to Loose Geometry",
        default=True,
        description="Move the drawn rectangle to the Loose Geometry collection (a drawing aid without surface type, never exported)",
    )
    auto_boundary: BoolProperty(
        name="Auto Boundary",
        default=True,
        description=(
            "Project the drawn rectangle onto the surfaces it lies on (Project Selected): the sub surface type is "
            "chosen automatically, Door when it touches the bottom edge of a wall, Skylight on a roof"
        ),
    )
    width: FloatProperty(name="Width", subtype="DISTANCE", unit="LENGTH", default=1.0)
    height: FloatProperty(name="Height", subtype="DISTANCE", unit="LENGTH", default=1.0)
    origin: FloatVectorProperty(size=3, options={"HIDDEN", "SKIP_SAVE"})
    axis_u: FloatVectorProperty(size=3, options={"HIDDEN", "SKIP_SAVE"})
    axis_v: FloatVectorProperty(size=3, options={"HIDDEN", "SKIP_SAVE"})

    def _corners(self, w, h):
        o, u, v = Vector(self.origin), Vector(self.axis_u), Vector(self.axis_v)
        return [o, o + u * w, o + u * w + v * h, o + v * h]

    def _size_from(self, p):
        """Width/height for the opposite corner p: fixed scene values where > 0, otherwise dragged."""
        d = p - Vector(self.origin)
        w, h = d.dot(Vector(self.axis_u)), d.dot(Vector(self.axis_v))
        if self.fixed_w > 0:
            w = self.fixed_w * (-1.0 if w < 0 else 1.0)
        if self.fixed_h > 0:
            h = self.fixed_h * (-1.0 if h < 0 else 1.0)
        return w, h

    def invoke(self, context, event):
        self.pts, self.plane, self.hover, self.at_first = [], None, None, False
        p, _ = self._pick(context, event)
        if p is None:
            self.report({"WARNING"}, "View is parallel to the drawing plane")
            return {"CANCELLED"}
        assert self.plane is not None
        n = self.plane[1]
        u = Vector((1.0, 0.0, 0.0)) if abs(n.z) > 0.9999 else Vector((0.0, 0.0, 1.0)).cross(n).normalized()
        v = n.cross(u).normalized()  # u x v == n
        self.origin, self.axis_u, self.axis_v = p, u, v
        self.fixed_w, self.fixed_h = max(self.fixed_width, 0.0), max(self.fixed_height, 0.0)
        if self.fixed_w > 0 and self.fixed_h > 0:
            self.width, self.height = self.fixed_w, self.fixed_h
            return self.execute(context)
        self.pts = self._corners(0.0, 0.0)
        self._add_handlers(context)
        context.workspace.status_text_set(self.HELP)
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM"}:
            return {"PASS_THROUGH"}
        if event.type == "MOUSEMOVE" or (event.type == "LEFTMOUSE" and event.value == "PRESS"):
            p, _ = self._pick(context, event)
            if p is None:
                return {"RUNNING_MODAL"}
            w, h = self._size_from(p)
            if event.type == "MOUSEMOVE":
                self.pts = self._corners(w, h)
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            self._end(context)
            if abs(w) < 1e-6 or abs(h) < 1e-6:
                return {"CANCELLED"}
            self.width, self.height = w, h
            return self.execute(context)
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._end(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def draw(self, context):  # redo panel: only the size is redoable; the tool settings live in the top bar
        self.layout.prop(self, "width")
        self.layout.prop(self, "height")

    def execute(self, context):  # also the redo path: width / height editable in the operator panel
        if not any(self.axis_u) or abs(self.width) < 1e-6 or abs(self.height) < 1e-6:
            return {"CANCELLED"}
        n = Vector(self.axis_u).cross(Vector(self.axis_v))
        return self._create(context, self._corners(self.width, self.height), n)


class OSM_WT_draw_rectangle(bpy.types.WorkSpaceTool):
    bl_space_type = "VIEW_3D"
    bl_context_mode = "OBJECT"
    bl_idname = "openstudio.draw_rectangle"
    bl_label = "Draw Rectangle"
    bl_description = "Draw a rectangular OpenStudio surface with optional fixed width and height"
    bl_icon = "ops.mesh.primitive_plane_add_gizmo"
    bl_widget = None
    bl_keymap = (
        ("view3d.osm_draw_rectangle", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
        ("view3d.osm_draw_hover", {"type": "MOUSEMOVE", "value": "ANY"}, None),
    )

    def draw_settings(context, layout, tool):
        props = tool.operator_properties("view3d.osm_draw_rectangle")
        for key in ("fixed_width", "fixed_height", "snap_increment", "clone_to_loose", "auto_boundary"):
            layout.prop(props, key)


# --- render modes --------------------------------------------------------------------

SURFACE_COLORS = {
    "Wall": (0.95, 0.85, 0.45),
    "Floor": (0.60, 0.35, 0.25),
    "RoofCeiling": (0.75, 0.25, 0.25),
    "FixedWindow": (0.55, 0.80, 0.95),
    "OperableWindow": (0.55, 0.80, 0.95),
    "Skylight": (0.55, 0.80, 0.95),
    "Door": (0.75, 0.60, 0.35),
    "GlassDoor": (0.55, 0.80, 0.95),
    "OverheadDoor": (0.75, 0.60, 0.35),
    "TubularDaylightDome": (0.55, 0.80, 0.95),
    "TubularDaylightDiffuser": (0.55, 0.80, 0.95),
    "SiteShading": (0.55, 0.40, 0.70),
    "BuildingShading": (0.55, 0.40, 0.70),
    "SpaceShading": (0.55, 0.40, 0.70),
    "InteriorPartitionSurface": (0.65, 0.65, 0.65),
}
BOUNDARY_COLORS = {
    "Outdoors": (0.45, 0.70, 0.95),
    "Ground": (0.55, 0.40, 0.25),
    "Surface": (0.45, 0.80, 0.45),
    "Adiabatic": (0.70, 0.70, 0.70),
    "Foundation": (0.50, 0.30, 0.20),
}
GREY = (0.8, 0.8, 0.8)


def _surface_type_of(ob) -> str:
    t = ob.get("surfaceType")
    if isinstance(t, str) and t in SURFACE_COLORS:
        return t
    if ob.data.polygons:
        return surface_type_from_normal((ob.matrix_world.to_3x3() @ ob.data.polygons[0].normal).normalized().z)
    return ""


def name_color(name: str) -> tuple[float, float, float]:
    """Stable, well-separated colour for a name (golden-ratio hue from a CRC of the name)."""
    import colorsys
    import zlib

    if not name:
        return GREY
    h = (zlib.crc32(name.encode("utf-8")) * 0.6180339887) % 1.0
    return colorsys.hsv_to_rgb(h, 0.55, 0.95)


def _normal_color(ob):
    if not ob.data.polygons:
        return GREY
    n = (ob.matrix_world.to_3x3() @ ob.data.polygons[0].normal).normalized()
    return (n.x * 0.5 + 0.5, n.y * 0.5 + 0.5, n.z * 0.5 + 0.5)


def _store_space(scene, ob):
    from .inspector import get_item
    from .results import _space_of

    sp = _space_of(ob)
    return get_item(scene, "Space", sp) if sp else None


def _name_for_mode(scene, ob, mode: str) -> str:
    """Construction / zone / space type / story name of an object. The Inspector store (Space item) wins over
    per-surface custom props, like in the export."""
    if mode == "CONSTRUCTION":
        v = ob.get("constructionName")
        return v if isinstance(v, str) else ""
    store_key, prop = {
        "ZONE": ("thermalZone", "thermalZoneName"),
        "SPACE_TYPE": ("spaceType", "spaceTypeName"),
        "STORY": ("buildingStory", "buildingStoryName"),
    }[mode]
    item = _store_space(scene, ob)
    v = getattr(item, store_key, "") if item is not None else ""
    if not v:
        v = ob.get(prop)
    return v if isinstance(v, str) else ""


NAME_MODES = ("CONSTRUCTION", "ZONE", "SPACE_TYPE", "STORY")


_legend_busy = False  # True while the legend collection is rebuilt (its colour update must not write overrides)


def _legend_changed(self, context):
    """A swatch of the legend was edited: remember it as an override of that name and re-colour (no legend rebuild)."""
    if _legend_busy:
        return
    sc = context.scene
    for o in sc.osm_color_overrides:
        if o.mode == self.mode and o.name == self.name:
            o.color = self.color
            break
    else:
        o = sc.osm_color_overrides.add()
        o.mode, o.name, o.color = self.mode, self.name, self.color
    apply_render_mode(sc, self.mode, rebuild_legend=False)


class OSMLegendItem(bpy.types.PropertyGroup):
    name: StringProperty()
    mode: StringProperty()
    color: FloatVectorProperty(
        subtype="COLOR", size=3, min=0.0, max=1.0, default=(0.8, 0.8, 0.8), update=_legend_changed
    )


class OSMColorOverride(bpy.types.PropertyGroup):
    """A user-edited colour of one name in one render mode (no update callback)."""

    name: StringProperty()
    mode: StringProperty()
    color: FloatVectorProperty(subtype="COLOR", size=3, min=0.0, max=1.0, default=(0.8, 0.8, 0.8))


def apply_render_mode(scene, mode: str, rebuild_legend: bool = True) -> None:
    global _legend_busy
    overrides = {o.name: tuple(o.color) for o in getattr(scene, "osm_color_overrides", ()) if o.mode == mode}
    legend: dict[str, tuple] = {}
    for ob in scene.objects:
        if ob.type != "MESH":
            continue
        label = ""
        if mode == "SURFACE_TYPE":
            label = _surface_type_of(ob)
            rgb = SURFACE_COLORS.get(label, GREY)
        elif mode == "BOUNDARY":
            v = ob.get("outsideBoundaryCondition")
            label = v if isinstance(v, str) and v in BOUNDARY_COLORS else ""
            rgb = BOUNDARY_COLORS.get(label, GREY)
        elif mode == "NORMAL":
            rgb = _normal_color(ob)
        elif mode in NAME_MODES:
            label = _name_for_mode(scene, ob, mode)
            rgb = name_color(label)
        else:
            rgb = (1.0, 1.0, 1.0)
        if label:
            rgb = overrides.get(label, rgb)
            legend[label] = rgb
        new = (*rgb, 1.0)
        if any(abs(a - b) > 1e-6 for a, b in zip(ob.color, new)):  # unchanged writes would re-trigger the depsgraph
            ob.color = new
    if rebuild_legend and hasattr(scene, "osm_legend"):
        cur = {i.name: tuple(i.color) for i in scene.osm_legend}
        if (
            cur.keys() == legend.keys()
            and all(all(abs(a - b) < 1e-6 for a, b in zip(cur[k], legend[k])) for k in cur)
            and all(i.mode == mode for i in scene.osm_legend)
        ):
            return
        _legend_busy = True
        try:
            scene.osm_legend.clear()
            for label in sorted(legend):
                item = scene.osm_legend.add()
                item.name, item.mode, item.color = label, mode, legend[label]
        finally:
            _legend_busy = False


# Live colouring: while a render mode is on, edits of surfaceType / outsideBoundaryCondition made through the
# osm_<key> proxies (UI or Python) re-colour the scene via the message bus. Subscriptions die on file load.
_live_mode = "OFF"
_msgbus_owner = object()


def _on_osm_prop_changed():
    if _live_mode != "OFF":
        apply_render_mode(bpy.context.scene, _live_mode)


_refresh_pending = False


def _refresh_live():
    global _refresh_pending
    _refresh_pending = False
    if _live_mode != "OFF":
        try:
            apply_render_mode(bpy.context.scene, _live_mode)
        except Exception:
            pass


def _live_depsgraph_handler(scene, depsgraph=None):
    """Keep an active render mode up to date after any scene change (new/renamed/edited objects, properties).

    Debounced through a timer; apply_render_mode only writes colours that changed, so it does not loop."""
    global _refresh_pending
    if _live_mode == "OFF" or _refresh_pending:
        return
    from .ui import prefs

    p = prefs()
    if p is not None and not p.auto_refresh_render:
        return
    _refresh_pending = True
    bpy.app.timers.register(_refresh_live, first_interval=0.2)


def _subscribe_msgbus():
    for key in (
        "osm_surfaceType",
        "osm_outsideBoundaryCondition",
        "osm_constructionName",
        "osm_thermalZoneName",
        "osm_spaceTypeName",
        "osm_buildingStoryName",
    ):
        if key not in bpy.types.Object.bl_rna.properties:
            continue
        bpy.msgbus.subscribe_rna(key=(bpy.types.Object, key), owner=_msgbus_owner, args=(), notify=_on_osm_prop_changed)


@persistent
def _load_post(_dummy=None):
    global _live_mode, _scene_geo, _scene_geo_dirty
    _scene_geo, _scene_geo_dirty = None, True
    _live_mode = "OFF"  # file-load cleared the subscriptions; modes are not saved with the file
    _subscribe_msgbus()


class VIEW3D_OT_osm_render_mode(bpy.types.Operator):
    bl_idname = "view3d.osm_render_mode"
    bl_label = "Render Mode"
    bl_description = "Colour objects in the viewport by OpenStudio data (writes Object > Viewport Display > Color)"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        items=[
            ("SURFACE_TYPE", "By Surface Type", ""),
            ("BOUNDARY", "By Boundary Condition", ""),
            ("NORMAL", "By Normal", ""),
            ("CONSTRUCTION", "By Construction", ""),
            ("ZONE", "By Thermal Zone", ""),
            ("SPACE_TYPE", "By Space Type", ""),
            ("STORY", "By Building Story", ""),
            ("OFF", "Off", ""),
        ]
    )

    @classmethod
    def description(cls, context, properties):
        return {
            "SURFACE_TYPE": "Colour by surface type",
            "BOUNDARY": "Colour by boundary condition",
            "NORMAL": "Colour by surface normal direction (XYZ = RGB)",
            "CONSTRUCTION": "Colour by construction",
            "ZONE": "Colour by thermal zone",
            "SPACE_TYPE": "Colour by space type",
            "STORY": "Colour by building story",
            "OFF": "Reset viewport colours",
        }.get(properties.mode, cls.bl_description)

    def execute(self, context):
        global _live_mode
        apply_render_mode(context.scene, self.mode)
        _live_mode = self.mode
        sd = context.space_data
        if sd is not None and sd.type == "VIEW_3D":
            sd.shading.color_type = "MATERIAL" if self.mode == "OFF" else "OBJECT"
        return {"FINISHED"}


# --- sidebar -------------------------------------------------------------------------


class SCENE_OT_osm_zone_per_space(bpy.types.Operator):
    bl_idname = "scene.osm_zone_per_space"
    bl_label = "Zone per Space"
    bl_description = "Give every space its own thermal zone (named like the space)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .inspector import get_item
        from .results import _space_of
        from .surface_ops import is_loose

        scene = context.scene
        spaces = set()
        for ob in scene.objects:
            if ob.type != "MESH" or is_loose(ob):
                continue
            sp = _space_of(ob)
            if sp:
                ob["thermalZoneName"] = sp
                spaces.add(sp)
        for sp in spaces:  # the Inspector store wins over per-surface extras
            item = get_item(scene, "Space", sp)
            if item is not None and hasattr(item, "thermalZone"):
                item.thermalZone = sp
        self.report({"INFO"}, f"{len(spaces)} space(s): one thermal zone each")
        return {"FINISHED"}


class _OSMPanel:
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"


class VIEW3D_PT_osm_modeling(PanelIcon, _OSMPanel, bpy.types.Panel):
    osm_icon = "panel_modeling"
    bl_label = "Modeling"
    bl_idname = "VIEW3D_PT_osm_modeling"

    def draw(self, context):
        self.layout.operator("view3d.osm_info_tool", **ik("model_info", "INFO"))


class VIEW3D_PT_osm_modeling_matching(_OSMPanel, bpy.types.Panel):
    bl_label = "Surface"
    bl_idname = "VIEW3D_PT_osm_modeling_matching"
    bl_parent_id = "VIEW3D_PT_osm_modeling"

    def draw(self, context):
        lay, sc = self.layout, context.scene
        field_row(lay, "Scope").prop(sc, "osm_match_scope", expand=True)
        row = lay.row(align=True)
        for mode, label in (("MATCH", "Match"), ("UNMATCH", "Unmatch"), ("INTERSECT", "Intersect")):
            op = row.operator("object.osm_match_surfaces", text=label)
            op.mode, op.scope = mode, sc.osm_match_scope
        lay.operator("object.osm_separate_faces", **ik("model_separate_faces", "MOD_EXPLODE"))


class VIEW3D_PT_osm_modeling_loose(_OSMPanel, bpy.types.Panel):
    bl_label = "Subsurface"
    bl_idname = "VIEW3D_PT_osm_modeling_loose"
    bl_parent_id = "VIEW3D_PT_osm_modeling"

    def draw(self, context):
        lay = self.layout
        lay.operator("object.osm_to_loose", **ik("model_to_loose", "MESH_PLANE"))
        row = lay.row(align=True)
        op = row.operator(
            "object.osm_project_loose", text="Project Selected", **ik("model_project_loose", "MOD_SHRINKWRAP")
        )
        op.scope = "SELECTED"
        op = row.operator("object.osm_project_loose", text="Project All", **ik("model_project_loose", "MOD_SHRINKWRAP"))
        op.scope = "ALL"


RENDER_MODES = (
    ("SURFACE_TYPE", "Surface Type", "MATERIAL"),
    ("BOUNDARY", "Boundary Condition", "SELECT_INTERSECT"),
    ("NORMAL", "Normal", "ORIENTATION_NORMAL"),
    ("CONSTRUCTION", "Construction", "MOD_BUILD"),
    ("ZONE", "Thermal Zone", "GROUP"),
    ("SPACE_TYPE", "Space Type", "OUTLINER_COLLECTION"),
    ("STORY", "Building Story", "SORTSIZE"),
    ("OFF", "Off", "LOOP_BACK"),
)


def draw_render_buttons(layout):
    """Render mode buttons (grid)."""
    grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
    for mode, label, icon in RENDER_MODES:
        grid.operator(
            "view3d.osm_render_mode",
            text=label,
            depress=(mode == _live_mode),
            **ik(f"model_render_{mode.lower()}", icon),
        ).mode = mode


def has_legend(context) -> bool:
    return _live_mode != "OFF" and bool(len(getattr(context.scene, "osm_legend", ())))


def draw_render_legend(layout, context):
    """Editable swatches of the active mode's legend + reset."""
    layout.use_property_split = True
    layout.use_property_decorate = False
    col = layout.column(align=True)
    for item in context.scene.osm_legend:
        if item.mode == _live_mode:
            col.prop(item, "color", text=item.name)
    if any(o.mode == _live_mode for o in context.scene.osm_color_overrides):
        layout.operator("view3d.osm_reset_legend_colors", icon="LOOP_BACK")


def draw_render_modes(layout, context):
    """Buttons + legend of the active mode in one layout (viewport shading popover)."""
    draw_render_buttons(layout)
    if has_legend(context):
        layout.separator(type="LINE")
        draw_render_legend(layout, context)


class VIEW3D_OT_osm_reset_legend_colors(bpy.types.Operator):
    bl_idname = "view3d.osm_reset_legend_colors"
    bl_label = "Reset Colors"
    bl_description = "Forget the edited colors of the active render mode"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        ov = context.scene.osm_color_overrides
        for i in reversed(range(len(ov))):
            if ov[i].mode == _live_mode:
                ov.remove(i)
        apply_render_mode(context.scene, _live_mode)
        return {"FINISHED"}


class VIEW3D_PT_osm_render(PanelIcon, _OSMPanel, bpy.types.Panel):
    osm_icon = "panel_render"
    bl_label = "Rendering"
    bl_idname = "VIEW3D_PT_osm_render"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_render_buttons(self.layout)


class VIEW3D_PT_osm_render_colors(_OSMPanel, bpy.types.Panel):
    bl_label = "Colors"
    bl_idname = "VIEW3D_PT_osm_render_colors"
    bl_parent_id = "VIEW3D_PT_osm_render"

    @classmethod
    def poll(cls, context):
        return has_legend(context)

    def draw(self, context):
        draw_render_legend(self.layout, context)


# --- viewport shading popover -------------------------------------------------------


class VIEW3D_PT_osm_shading(bpy.types.Panel):
    """Own section in the header's shading popover (next to Lighting / Color / Options)."""

    bl_space_type, bl_region_type = "VIEW_3D", "HEADER"
    bl_parent_id = "VIEW3D_PT_shading"
    bl_label = "OpenStudio"
    bl_idname = "VIEW3D_PT_osm_shading"

    def draw_header(self, context):
        self.layout.label(**ik("panel_render"))

    def draw(self, context):
        draw_render_modes(self.layout, context)


# --- registration --------------------------------------------------------------------

CLASSES = (
    OBJECT_OT_osm_separate_faces,
    VIEW3D_OT_osm_draw_hover,
    VIEW3D_OT_osm_draw_rectangle,
    OSMLegendItem,
    OSMColorOverride,
    VIEW3D_OT_osm_render_mode,
    VIEW3D_OT_osm_reset_legend_colors,
    SCENE_OT_osm_zone_per_space,
    VIEW3D_PT_osm_modeling,
    VIEW3D_PT_osm_modeling_matching,
    VIEW3D_PT_osm_modeling_loose,
    VIEW3D_PT_osm_render,
    VIEW3D_PT_osm_render_colors,
    VIEW3D_PT_osm_shading,
)


def register():
    from . import props as blender_props
    from . import surface_ops as blender_surface_ops

    OSM_WT_draw_rectangle.bl_icon = tool_icon("tool_draw_rectangle")
    blender_props.register()
    blender_surface_ops.register()
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.osm_match_scope = EnumProperty(
        name="Scope",
        items=[("SELECTED", "Selection", "Pairs with at least one selected surface"), ("ALL", "Entire Model", "")],
        default="SELECTED",
    )
    bpy.types.Scene.osm_legend = CollectionProperty(type=OSMLegendItem)
    bpy.types.Scene.osm_color_overrides = CollectionProperty(type=OSMColorOverride)
    from . import inspector as blender_inspector
    from . import modeling as blender_modeling
    from . import results as blender_results
    from . import scripts as blender_scripts
    from . import search as blender_search

    blender_modeling.register()
    blender_search.register()
    blender_inspector.register()
    blender_results.register()
    blender_scripts.register()
    from . import text_templates as blender_text_templates

    blender_text_templates.register()
    try:
        bpy.utils.register_tool(OSM_WT_draw_rectangle, after={"builtin.primitive_cube_add"}, separator=True)
    except Exception:
        bpy.utils.register_tool(OSM_WT_draw_rectangle)
    if _geo_dirty_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_geo_dirty_handler)
    if _live_depsgraph_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_live_depsgraph_handler)
    if not bpy.app.timers.is_registered(_hover_watch):
        bpy.app.timers.register(_hover_watch, first_interval=1.0, persistent=True)
    _subscribe_msgbus()
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)


def unregister():
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if bpy.app.timers.is_registered(_hover_watch):
        bpy.app.timers.unregister(_hover_watch)
    if _geo_dirty_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_geo_dirty_handler)
    if _live_depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_live_depsgraph_handler)
    bpy.utils.unregister_tool(OSM_WT_draw_rectangle)
    from . import inspector as blender_inspector
    from . import results as blender_results
    from . import scripts as blender_scripts
    from . import search as blender_search
    from . import text_templates as blender_text_templates

    blender_text_templates.unregister()
    blender_scripts.unregister()
    blender_results.unregister()
    blender_inspector.unregister()
    blender_search.unregister()
    from . import modeling as blender_modeling

    blender_modeling.unregister()
    del bpy.types.Scene.osm_color_overrides
    del bpy.types.Scene.osm_match_scope
    del bpy.types.Scene.osm_legend
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    from . import props as blender_props
    from . import surface_ops as blender_surface_ops

    blender_surface_ops.unregister()
    blender_props.unregister()
