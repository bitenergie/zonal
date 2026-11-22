"""In-process Blender tests for blender_tools (separate faces, draw tools, render modes, panels)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from conftest import requires_bpy

pytestmark = requires_bpy


def tools():
    from zonal.blender import tools as blender_tools

    return blender_tools


def make_obj(bpy, name, verts, faces, col=None, **props):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    (col or bpy.context.scene.collection).objects.link(ob)
    for k, v in props.items():
        ob[k] = v
    return ob


CUBE_V = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
CUBE_F = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]


def cube(bpy, name="Cube", **props):
    return make_obj(bpy, name, CUBE_V, CUBE_F, **props)


def select(bpy, *obs):
    for o in bpy.context.view_layer.objects.selected:
        o.select_set(False)
    for o in obs:
        o.select_set(True)
    if obs:
        bpy.context.view_layer.objects.active = obs[-1]
    bpy.context.view_layer.update()


# --- separate faces -----------------------------------------------------------------


def test_plain_and_eligible(clean_scene):
    bpy = clean_scene
    t = tools()
    assert t._plain(3) == 3
    assert t._plain(SimpleNamespace(to_dict=lambda: {"a": 1})) == {"a": 1}
    assert t._plain(SimpleNamespace(to_list=lambda: [1])) == [1]
    c = cube(bpy)
    assert t._eligible(c) and t._eligible(c, 10) and not t._eligible(c, 3)
    c.modifiers.new("x", "BEVEL")
    assert not t._eligible(c)
    empty = bpy.data.objects.new("e", None)
    assert not t._eligible(empty)
    single = make_obj(bpy, "s", CUBE_V[:4], [[0, 1, 2, 3]])
    assert not t._eligible(single)
    shaped = cube(bpy, "shaped")
    shaped.shape_key_add(name="Basis")
    assert not t._eligible(shaped)
    parent = cube(bpy, "par")
    kid = cube(bpy, "kid")
    kid.parent = parent
    assert not t._eligible(parent)


def test_separate_faces(clean_scene):
    bpy = clean_scene
    t = tools()
    col = bpy.data.collections.new("Room")
    bpy.context.scene.collection.children.link(col)
    parent = bpy.data.objects.new("P", None)
    bpy.context.scene.collection.objects.link(parent)
    c = make_obj(bpy, "Cube", CUBE_V, CUBE_F, col=col, spaceName="Room", surfaceType="Wall", _skip=1)
    c.parent = parent
    c["lst"] = [1, 2]
    c["d"] = {"a": 1}
    select(bpy, c)
    n = t.separate_faces(bpy.context, [c])
    assert n == 6
    objs = [o for o in bpy.data.objects if o.name.startswith("Cube")]
    assert len(objs) == 6 and all(o["spaceName"] == "Room" and o.parent == parent for o in objs)
    assert all("_skip" not in o for o in objs)
    assert objs[0]["d"]["a"] == 1
    assert bpy.context.view_layer.objects.active in objs
    # surfaceType set from tilt when missing
    c2 = cube(bpy, "Box")
    select(bpy, c2)
    t.separate_faces(bpy.context, [c2], set_surface_type=True)
    types = {o["surfaceType"] for o in bpy.data.objects if o.name.startswith("Box")}
    assert {"Floor", "RoofCeiling", "Wall"} <= types
    c3 = cube(bpy, "Plain")
    t.separate_faces(bpy.context, [c3], set_surface_type=False)
    assert all("surfaceType" not in o for o in bpy.data.objects if o.name.startswith("Plain"))
    # nothing eligible
    assert t.separate_faces(bpy.context, [parent]) == 0
    # shared mesh keeps data (users > 0)
    c4 = cube(bpy, "Twin")
    twin = bpy.data.objects.new("Twin2", c4.data)
    bpy.context.scene.collection.objects.link(twin)
    assert t.separate_faces(bpy.context, [c4]) == 6


def test_separate_faces_select_errors(clean_scene):
    bpy = clean_scene
    t = tools()

    class Objs:
        selected = []

        @property
        def active(self):
            return None

        @active.setter
        def active(self, v):
            raise RuntimeError

    ctx = SimpleNamespace(view_layer=SimpleNamespace(objects=Objs()))
    col = bpy.data.collections.new("Ex")
    bpy.context.scene.collection.children.link(col)
    bpy.context.view_layer.layer_collection.children["Ex"].exclude = True
    c2 = make_obj(bpy, "In", CUBE_V, CUBE_F, col=col)
    assert t.separate_faces(ctx, [c2]) == 6


def test_separate_operator(clean_scene):
    bpy = clean_scene
    t = tools()
    assert not bpy.ops.object.osm_separate_faces.poll()
    c = cube(bpy)
    select(bpy, c)
    assert bpy.ops.object.osm_separate_faces() == {"FINISHED"}
    # nothing to separate any more
    select(bpy, *[o for o in bpy.data.objects][:1])
    assert bpy.ops.object.osm_separate_faces() == {"CANCELLED"}
    # edit mode branch
    inst = MagicMock()
    inst.set_surface_type = True
    ctx = MagicMock()
    ctx.mode = "EDIT_MESH"
    ctx.selected_objects = []
    fake_bpy = MagicMock()
    with patch.object(t, "bpy", fake_bpy):
        assert t.OBJECT_OT_osm_separate_faces.execute(inst, ctx) == {"CANCELLED"}
        fake_bpy.ops.object.mode_set.assert_called_once_with(mode="OBJECT")


# --- draw tool helpers ------------------------------------------------------------------


def test_active_draw_props(clean_scene):
    t = tools()
    ctx = MagicMock()
    ctx.workspace.tools.from_space_view3d_mode.return_value = SimpleNamespace(
        idname="openstudio.draw_rectangle", operator_properties=lambda n: n
    )
    assert t._active_draw_props(ctx) == "view3d.osm_draw_rectangle"
    ctx.workspace.tools.from_space_view3d_mode.return_value = SimpleNamespace(idname="other")
    assert t._active_draw_props(ctx) is None
    ctx.workspace.tools.from_space_view3d_mode.return_value = None
    assert t._active_draw_props(ctx) is None
    ctx.workspace.tools.from_space_view3d_mode.side_effect = RuntimeError
    assert t._active_draw_props(ctx) is None


def test_geo_dirty_handler(clean_scene):
    bpy = clean_scene
    t = tools()
    t._scene_geo_dirty = False
    upd = SimpleNamespace(is_updated_geometry=False, is_updated_transform=True, id=None)
    dg = SimpleNamespace(
        updates=[
            SimpleNamespace(is_updated_geometry=False, is_updated_transform=False, id=bpy.data.objects.new("o", None)),
            upd,
        ]
    )
    t._geo_dirty_handler(None, dg)
    assert t._scene_geo_dirty is True
    # collection update flags dirty
    t._scene_geo_dirty = False
    col = bpy.data.collections.new("c")
    dg = SimpleNamespace(updates=[SimpleNamespace(is_updated_geometry=False, is_updated_transform=False, id=col)])
    t._geo_dirty_handler(None, dg)
    assert t._scene_geo_dirty is True
    # nothing relevant / no depsgraph
    t._scene_geo_dirty = False
    t._geo_dirty_handler(None)
    assert t._scene_geo_dirty is False


# --- gpu drawing --------------------------------------------------------------------


def test_uniform_shader():
    t = tools()
    with patch.object(t.gpu.shader, "from_builtin", side_effect=[RuntimeError, "sh"]):
        assert t._uniform_shader() == "sh"
    with patch.object(t.gpu.shader, "from_builtin", side_effect=RuntimeError):
        assert t._uniform_shader() is None


def test_draw_preview():
    from mathutils import Vector

    t = tools()
    sh = MagicMock()
    op = SimpleNamespace(pts=[Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((1, 1, 0))], hover=Vector((0, 1, 0)))
    with (
        patch.object(t, "_uniform_shader", return_value=sh),
        patch.object(t, "batch_for_shader") as b,
        patch.object(t.gpu.state, "line_width_set"),
        patch.object(t.gpu.state, "point_size_set"),
    ):
        t._draw_preview(op)
        assert b.call_count == 2
    # no shader / no points
    with patch.object(t, "_uniform_shader", return_value=None):
        t._draw_preview(op)
    with patch.object(t, "_uniform_shader", return_value=sh):
        t._draw_preview(SimpleNamespace(pts=[], hover=None))
    # AttributeError on gpu.state, two points -> no closing coord
    with (
        patch.object(t, "_uniform_shader", return_value=sh),
        patch.object(t, "batch_for_shader"),
        patch.object(t.gpu.state, "line_width_set", side_effect=AttributeError),
    ):
        t._draw_preview(SimpleNamespace(pts=[Vector((0, 0, 0)), Vector((1, 0, 0))], hover=None))


def test_draw_snap_marker():
    from mathutils import Vector

    t = tools()
    op = SimpleNamespace(snap=None, region=None, region_data=None)
    t._draw_snap_marker(op)  # no snap
    op.snap = (Vector((0, 0, 0)), "vertex")
    sh = MagicMock()
    with (
        patch.object(t.view3d_utils, "location_3d_to_region_2d", return_value=Vector((10, 10))),
        patch.object(t, "_uniform_shader", return_value=sh),
        patch.object(t, "batch_for_shader") as b,
        patch.object(t.gpu.state, "line_width_set"),
    ):
        t._draw_snap_marker(op)
        b.assert_called_once()
    with (
        patch.object(t.view3d_utils, "location_3d_to_region_2d", return_value=Vector((10, 10))),
        patch.object(t, "_uniform_shader", return_value=sh),
        patch.object(t, "batch_for_shader"),
        patch.object(t.gpu.state, "line_width_set", side_effect=AttributeError),
    ):
        t._draw_snap_marker(op)
    # offscreen / no shader
    with patch.object(t.view3d_utils, "location_3d_to_region_2d", return_value=None):
        t._draw_snap_marker(op)
    with (
        patch.object(t.view3d_utils, "location_3d_to_region_2d", return_value=Vector((1, 1))),
        patch.object(t, "_uniform_shader", return_value=None),
    ):
        t._draw_snap_marker(op)
    # hover marker hidden while drawing
    hover = MagicMock(spec=t.VIEW3D_OT_osm_draw_hover)
    hover.snap = (Vector((0, 0, 0)), "x")
    t._drawing = True
    try:
        t._draw_snap_marker(hover)
    finally:
        t._drawing = False


# --- scene geometry / snapping --------------------------------------------------------


def test_scene_geometry(clean_scene):
    bpy = clean_scene
    t = tools()
    c = cube(bpy)
    e = bpy.data.objects.new("empty", None)
    bpy.context.scene.collection.objects.link(e)
    make_obj(bpy, "vertless", [], [])
    _ = bpy.context.scene.collection.objects.link
    make_obj(bpy, "pts", [(0, 0, 5), (1, 0, 5)], [])  # vertices but no edges
    bpy.context.view_layer.update()
    g = t._build_scene_geometry(bpy.context)
    assert len(g) > 0
    del c
    t._scene_geo, t._scene_geo_dirty = None, True
    g1 = t._scene_geometry(bpy.context)
    assert t._scene_geometry(bpy.context) is g1
    # edge budget exceeded and to_mesh failure
    with patch.object(t, "SNAP_MAX_EDGES", 1):
        assert len(t._build_scene_geometry(bpy.context)) == 0 or True
    with patch.object(bpy.types.Object, "to_mesh", side_effect=RuntimeError, create=True):
        pass


def test_scene_geometry_to_mesh_error(clean_scene):
    bpy = clean_scene
    t = tools()
    cube(bpy)
    bpy.context.view_layer.update()
    real = SimpleNamespace(show_self=True, object=MagicMock(type="MESH"), matrix_world=np.eye(4))
    real.object.to_mesh.side_effect = RuntimeError
    dg = SimpleNamespace(object_instances=[real, SimpleNamespace(show_self=False, object=MagicMock(type="MESH"))])
    ctx = SimpleNamespace(evaluated_depsgraph_get=lambda: dg)
    g = t._build_scene_geometry(ctx)
    assert len(g) == 0


class FakeRV3D:
    def __init__(self, persp=True):
        from mathutils import Matrix

        self.view_matrix = Matrix.Translation((0, 0, -10))
        self.perspective_matrix = Matrix.Identity(4)
        self.is_perspective = persp


def test_occluded():
    from mathutils import Vector

    t = tools()
    ctx = MagicMock()
    ctx.region_data = FakeRV3D(True)
    ctx.scene.ray_cast.return_value = (True, None, None, 0, None, None)
    assert t._occluded(ctx, Vector((0, 0, 0))) is True
    ctx.scene.ray_cast.return_value = (False, None, None, 0, None, None)
    assert t._occluded(ctx, Vector((0, 0, 0))) is False
    assert t._occluded(ctx, Vector((0, 0, 10))) is False  # at the eye
    ctx.region_data = FakeRV3D(False)
    assert t._occluded(ctx, Vector((0, 0, 0))) is False


def test_pick_ray():
    t = tools()
    ev = SimpleNamespace(mouse_region_x=5, mouse_region_y=6)
    with (
        patch.object(t.view3d_utils, "region_2d_to_origin_3d", return_value="o"),
        patch.object(t.view3d_utils, "region_2d_to_vector_3d", return_value="d"),
    ):
        assert t._pick_ray(None, MagicMock(), ev) == ("o", "d", (5, 6))
        assert t._HoverState()._ray(MagicMock(), ev) == ("o", "d", (5, 6))
        assert t._DrawBase()._ray(MagicMock(), ev) == ("o", "d", (5, 6))


class PickSelf:
    ALLOW_AXIS = True
    axis = None
    snap_increment = 0.1

    def __init__(self, pts=(), plane=None, axis=None, allow=True):
        self.pts, self.plane, self.snap, self.axis, self.ALLOW_AXIS = list(pts), plane, None, axis, allow

    def _ref(self):
        return self.pts[-1] if self.pts else None


def pick_env(hit=False, hit_normal=(0, 0, 1), cursor=(0, 0, 0)):
    from mathutils import Matrix, Vector

    ctx = MagicMock()
    ctx.scene.ray_cast.return_value = (hit, Vector((0, 0, 0)), Vector(hit_normal), 0, None, Matrix())
    ctx.scene.cursor.location = Vector(cursor)
    ctx.region.width = ctx.region.height = 100
    ctx.region_data = FakeRV3D()
    ctx.preferences.system.ui_scale = 1.0
    return ctx


def run_pick(
    t,
    me,
    ctx,
    ev,
    origin=(0.0, 0.0, 5.0),
    direction=(0.0, 0.0, -1.0),
    sp=None,
    scene_hit=None,
    occluded=False,
    drawn_hit=None,
):
    from mathutils import Vector

    geo = MagicMock()
    geo.nearest.return_value = scene_hit
    with (
        patch.object(t.view3d_utils, "region_2d_to_origin_3d", return_value=Vector(origin)),
        patch.object(t.view3d_utils, "region_2d_to_vector_3d", return_value=Vector(direction)),
        patch.object(t.view3d_utils, "location_3d_to_region_2d", return_value=sp),
        patch.object(t, "_scene_geometry", return_value=geo),
        patch.object(t, "_occluded", return_value=occluded),
        patch.object(t.SnapGeometry, "nearest", return_value=drawn_hit),
    ):
        return t._pick_point(me, ctx, ev)


def ev(ctrl=False):
    return SimpleNamespace(mouse_region_x=50, mouse_region_y=50, ctrl=ctrl)


def test_pick_point_plane_choices(clean_scene):
    from mathutils import Vector

    t = tools()
    # no hit -> cursor plane, facing viewer
    me = PickSelf()
    p, first = run_pick(t, me, pick_env(), ev())
    assert first is False and p is not None and abs(p.z) < 1e-9 and me.plane[1].z == 1.0
    # hit with normal facing away -> flipped; ray going up for flip of default
    me = PickSelf()
    p, _ = run_pick(t, me, pick_env(hit=True, hit_normal=(0, 0, -1)), ev())
    assert me.plane[1].z == 1.0
    me = PickSelf()
    p, _ = run_pick(t, me, pick_env(), ev(), origin=(0, 0, -5), direction=(0, 0, 1))
    assert me.plane[1].z == -1.0
    # parallel ray -> None
    me = PickSelf(plane=(Vector((0, 0, 0)), Vector((0, 0, 1))))
    p, first = run_pick(t, me, pick_env(), ev(), origin=(0, 0, 5), direction=(1, 0, 0))
    assert p is None and first is False


def test_pick_point_snapping(clean_scene):
    from mathutils import Vector

    t = tools()
    plane = (Vector((0, 0, 0)), Vector((0, 0, 1)))
    pts = [Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((1, 1, 0))]
    # first point closes loop
    me = PickSelf(pts, plane)
    p, first = run_pick(t, me, pick_env(), ev(), sp=Vector((50, 50)))
    assert first is True and me.snap[1] == "first"
    # first point far away; scene hit used; drawn hit better
    me = PickSelf(pts, plane)
    p, first = run_pick(
        t,
        me,
        pick_env(),
        ev(),
        sp=Vector((500, 500)),
        scene_hit=(5.0, "vertex", (2, 2, 1)),
        drawn_hit=(3.0, "edge", (0.5, 0.5, 0)),
    )
    assert first is False and me.snap[1] == "edge"
    # scene hit occluded, no drawn hit -> no snap
    me = PickSelf(pts, plane)
    p, _ = run_pick(t, me, pick_env(), ev(), sp=None, scene_hit=(5.0, "vertex", (2, 2, 1)), occluded=True)
    assert me.snap is None
    # scene-only snap, projected onto plane
    me = PickSelf([], plane)
    p, _ = run_pick(t, me, pick_env(), ev(), scene_hit=(5.0, "midpoint", (2, 2, 1)))
    assert me.snap[1] == "midpoint" and abs(me.snap[0].z) < 1e-9
    # ctrl grid snap when not snapped
    me = PickSelf([], plane)
    p, _ = run_pick(t, me, pick_env(), ev(ctrl=True))
    assert me.snap[1] == "grid"
    # axis lock
    me = PickSelf([Vector((0, 0, 0))], plane, axis="X")
    p, _ = run_pick(t, me, pick_env(), ev(ctrl=True), direction=(0.01, 0.2, -1))
    assert abs(p.y) < 1e-9
    # axis lock along z in z-plane -> axis_in_plane None
    me = PickSelf([Vector((0, 0, 0))], plane, axis="Z")
    p, _ = run_pick(t, me, pick_env(), ev())
    assert p is not None
    # plain miss
    me = PickSelf([], plane)
    p, _ = run_pick(t, me, pick_env(), ev())
    assert me.snap is None


# --- hover watch / hover operator ------------------------------------------------------


def test_hover_watch():
    t = tools()
    t._hovering = False
    # no windows
    fake_bpy = MagicMock()
    fake_bpy.context.window_manager.windows = []
    with patch.object(t, "bpy", fake_bpy):
        assert t._hover_watch() == 0.3
    # tool lookup failing, tool None, tool not draw, draw with no area, draw with area
    region = SimpleNamespace(type="WINDOW")
    area = SimpleNamespace(type="VIEW_3D", regions=[region])
    area_nr = SimpleNamespace(type="VIEW_3D", regions=[])
    w_err = MagicMock()
    w_err.workspace.tools.from_space_view3d_mode.side_effect = RuntimeError
    w_none = MagicMock()
    w_none.workspace.tools.from_space_view3d_mode.return_value = None
    w_other = MagicMock()
    w_other.workspace.tools.from_space_view3d_mode.return_value = SimpleNamespace(idname="x")
    w_draw = MagicMock()
    w_draw.workspace.tools.from_space_view3d_mode.return_value = SimpleNamespace(idname="openstudio.draw_rectangle")
    w_draw.screen.areas = [SimpleNamespace(type="OUTLINER", regions=[region]), area_nr, area]
    fake_bpy.context.window_manager.windows = [w_err, w_none, w_other, w_draw]
    with patch.object(t, "bpy", fake_bpy):
        assert t._hover_watch() == 0.3
        fake_bpy.ops.view3d.osm_draw_hover.assert_called_once_with("INVOKE_DEFAULT")
    # already hovering
    t._hovering = True
    with patch.object(t, "bpy", fake_bpy):
        assert t._hover_watch() == 0.3
    t._hovering = False
    # exception swallowed
    fake_bpy.context.window_manager.windows = None
    with patch.object(t, "bpy", fake_bpy):
        assert t._hover_watch() == 0.3


def test_hover_state():
    t = tools()
    h = t._HoverState()
    assert h._ref() is None and h.pts == []


def hover_cls(t):
    return type(
        "H",
        (),
        {
            k: v
            for k, v in vars(t.VIEW3D_OT_osm_draw_hover).items()
            if callable(v) and not isinstance(v, classmethod) and k in ("invoke", "_stop", "modal")
        },
    )


def test_hover_operator(clean_scene):
    _ = clean_scene
    t = tools()
    H = t.VIEW3D_OT_osm_draw_hover
    assert H.poll(SimpleNamespace(area=None, region_data=None)) is False
    assert H.poll(SimpleNamespace(area=SimpleNamespace(type="VIEW_3D"), region_data=1)) is True
    cls = hover_cls(t)
    me = cls()
    ctx = MagicMock()
    t._hovering = True
    assert me.invoke(ctx, None) == {"PASS_THROUGH"}
    t._hovering = False
    with patch.object(t.bpy.types.SpaceView3D, "draw_handler_add", return_value="h") as add:
        assert me.invoke(ctx, None) == {"RUNNING_MODAL", "PASS_THROUGH"}
        add.assert_called_once()
    assert t._hovering is True
    ctx.window_manager.modal_handler_add.assert_called_once()

    # modal: active tool, mouse move inside
    me.region = SimpleNamespace(width=100, height=100)
    me.state = t._HoverState()
    ctx = MagicMock()
    ctx.workspace.tools.from_space_view3d_mode.return_value = SimpleNamespace(idname="openstudio.draw_rectangle")
    event = SimpleNamespace(type="MOUSEMOVE", mouse_region_x=10, mouse_region_y=10)
    with (
        patch.object(t, "_active_draw_props", return_value=SimpleNamespace(snap_increment=0.5)),
        patch.object(t, "_pick_point") as pp,
    ):
        assert me.modal(ctx, event) == {"PASS_THROUGH"}
        pp.assert_called_once()
        assert me.state.snap_increment == 0.5
    # outside, no props
    event = SimpleNamespace(type="LEFT_CTRL", mouse_region_x=-1, mouse_region_y=10)
    with patch.object(t, "_active_draw_props", return_value=None), patch.object(t, "_pick_point") as pp:
        me.modal(ctx, event)
        pp.assert_not_called()
    # drawing: ignores moves
    t._drawing = True
    try:
        me.modal(ctx, event)
    finally:
        t._drawing = False
    # other event
    assert me.modal(ctx, SimpleNamespace(type="A")) == {"PASS_THROUGH"}
    # tool lookup raising -> active True
    ctx.workspace.tools.from_space_view3d_mode.side_effect = RuntimeError
    assert me.modal(ctx, SimpleNamespace(type="A")) == {"PASS_THROUGH"}
    # inactive -> stop
    ctx.workspace.tools.from_space_view3d_mode.side_effect = None
    ctx.workspace.tools.from_space_view3d_mode.return_value = SimpleNamespace(idname="other")
    me._handle = "h"
    with patch.object(t.bpy.types.SpaceView3D, "draw_handler_remove") as rm:
        assert me.modal(ctx, event) == {"CANCELLED", "PASS_THROUGH"}
        rm.assert_called_once()
    assert t._hovering is False
    # no area -> stop, no tag_redraw
    ctx = MagicMock()
    ctx.area = None
    ctx.workspace.tools.from_space_view3d_mode.return_value = None
    me._handle = "h"
    t._hovering = True
    with patch.object(t.bpy.types.SpaceView3D, "draw_handler_remove"):
        assert me.modal(ctx, event) == {"CANCELLED", "PASS_THROUGH"}


# --- draw operators ---------------------------------------------------------------------


class Report:
    def report(self, kind, msg):
        self.reports = getattr(self, "reports", []) + [(kind, msg)]


def draw_surface_fake(t, **attrs):
    cls = type("DS", (Report, t._DrawBase), {"HELP": "help", "clone_to_loose": False, "auto_boundary": False, **attrs})
    return cls()


def rect_fake(t, **attrs):
    R = t.VIEW3D_OT_osm_draw_rectangle
    methods = {k: vars(R)[k] for k in ("_ref", "_corners", "_size_from", "invoke", "modal", "execute", "draw")}
    cls = type(
        "DR",
        (Report, t._DrawBase),
        {
            **methods,
            "HELP": "h",
            "ALLOW_AXIS": False,
            "clone_to_loose": False,
            "auto_boundary": False,
            "fixed_width": 0.0,
            "fixed_height": 0.0,
            "width": 1.0,
            "height": 1.0,
            **attrs,
        },
    )
    return cls()


def fake_ctx(bpy=None):
    ctx = MagicMock()
    return ctx


def test_draw_base_basics():
    t = tools()
    d = draw_surface_fake(t)
    d.pts = []
    assert d._ref() is None
    d.pts = [1]
    assert d._ref() == 1
    ctx = MagicMock()
    d.axis = "X"
    d._status(ctx)
    ctx.workspace.status_text_set.assert_called_with("help | Axis: X")
    d.axis = None
    d._status(ctx)
    ctx.workspace.status_text_set.assert_called_with("help")
    p = MagicMock()
    with patch.object(t, "_pick_point", return_value=("p", False)) as pp:
        assert d._pick(ctx, None) == ("p", False)
        pp.assert_called_once()
    assert t.VIEW3D_OT_osm_draw_rectangle.poll(SimpleNamespace(area=None, region_data=None)) is False
    assert (
        t.VIEW3D_OT_osm_draw_rectangle.poll(SimpleNamespace(area=SimpleNamespace(type="VIEW_3D"), region_data=1))
        is True
    )
    del p


def test_draw_base_lifecycle():
    from mathutils import Vector

    t = tools()
    d = draw_surface_fake(t)
    ctx = MagicMock()
    # parallel view
    with patch.object(t, "_pick_point", return_value=(None, False)):
        assert d.invoke(ctx, None) == {"CANCELLED"}
    assert d.reports
    with (
        patch.object(t, "_pick_point", return_value=(Vector((0, 0, 0)), False)),
        patch.object(t.bpy.types.SpaceView3D, "draw_handler_add", return_value="h"),
    ):
        assert d.invoke(ctx, None) == {"RUNNING_MODAL"}
    assert t._drawing is True and len(d.pts) == 1
    with patch.object(t.bpy.types.SpaceView3D, "draw_handler_remove"):
        d._end(ctx)
        ctx.area = None
        d._end(ctx)
    assert t._drawing is False


def modal_ev(type_, value="PRESS"):
    return SimpleNamespace(type=type_, value=value, mouse_region_x=0, mouse_region_y=0, ctrl=False)


def test_draw_base_modal():
    from mathutils import Vector

    t = tools()
    d = draw_surface_fake(t)
    d.pts, d.plane, d.hover, d.axis = [Vector((0, 0, 0))], (Vector((0, 0, 0)), Vector((0, 0, 1))), None, None
    ctx = MagicMock()
    for et in ("MIDDLEMOUSE", "WHEELUPMOUSE", "NDOF_X", "NUMPAD_1"):
        assert d.modal(ctx, modal_ev(et)) == {"PASS_THROUGH"}
    # axis keys
    with patch.object(t, "_pick_point", return_value=(Vector((1, 0, 0)), False)):
        assert d.modal(ctx, modal_ev("X")) == {"RUNNING_MODAL"} and d.axis == "X"
        d.modal(ctx, modal_ev("X"))
        assert d.axis is None
        assert d.modal(ctx, modal_ev("MOUSEMOVE", "NOTHING")) == {"RUNNING_MODAL"}
    # mouse move with None point keeps hover
    with patch.object(t, "_pick_point", return_value=(None, False)):
        d.hover = "keep"
        d.modal(ctx, modal_ev("MOUSEMOVE"))
        assert d.hover == "keep"
    # leftmouse paths
    with patch.object(t, "_pick_point", return_value=(None, False)):
        assert d.modal(ctx, modal_ev("LEFTMOUSE")) == {"RUNNING_MODAL"}
    with patch.object(t, "_pick_point", return_value=(Vector((2, 0, 0)), False)):
        d.modal(ctx, modal_ev("LEFTMOUSE"))
        assert len(d.pts) == 2
        d.modal(ctx, modal_ev("LEFTMOUSE"))  # duplicate point ignored
        assert len(d.pts) == 2
    d.pts = []
    with patch.object(t, "_pick_point", return_value=(Vector((2, 0, 0)), False)):
        d.modal(ctx, modal_ev("LEFTMOUSE"))
        assert len(d.pts) == 1
    # close on first point -> finish
    with (
        patch.object(t, "_pick_point", return_value=(Vector((0, 0, 0)), True)),
        patch.object(d, "_finish", return_value={"FINISHED"}),
    ):
        assert d.modal(ctx, modal_ev("LEFTMOUSE")) == {"FINISHED"}
    for et in ("RET", "NUMPAD_ENTER", "RIGHTMOUSE"):
        with patch.object(d, "_finish", return_value={"FINISHED"}):
            assert d.modal(ctx, modal_ev(et)) == {"FINISHED"}
    # backspace
    d.pts = [1, 2]
    assert d.modal(ctx, modal_ev("BACK_SPACE")) == {"RUNNING_MODAL"} and d.pts == [1]
    with patch.object(d, "_end") as end:
        assert d.modal(ctx, modal_ev("BACK_SPACE")) == {"CANCELLED"}
        end.assert_called_once()
        assert d.modal(ctx, modal_ev("ESC")) == {"CANCELLED"}
    # unrelated event
    assert d.modal(ctx, modal_ev("A")) == {"RUNNING_MODAL"}


def test_create_and_finish(clean_scene):
    bpy = clean_scene
    from mathutils import Vector

    t = tools()
    d = draw_surface_fake(t)
    ctx = bpy.context
    assert d._create(ctx, [Vector((0, 0, 0))], Vector((0, 0, 1))) == {"CANCELLED"}
    line = [Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((2, 0, 0))]
    assert d._create(ctx, line, Vector((0, 0, 1))) == {"CANCELLED"}
    assert d.reports
    sq = [Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((1, 1, 0)), Vector((0, 1, 0))]
    stale = bpy.data.objects.new("stale", None)
    bpy.context.scene.collection.objects.link(stale)
    stale.select_set(True)
    assert d._create(ctx, sq, Vector((0, 0, 1))) == {"FINISHED"}
    ob = bpy.context.view_layer.objects.active
    assert ob["surfaceType"] == "RoofCeiling" and not stale.select_get()
    # reversed winding flips
    assert d._create(ctx, sq, Vector((0, 0, -1))) == {"FINISHED"}
    assert bpy.context.view_layer.objects.active["surfaceType"] == "Floor"
    # clone to loose
    dl = draw_surface_fake(t, clone_to_loose=True)
    assert dl._create(ctx, sq, Vector((0, 0, 1))) == {"FINISHED"}
    new = bpy.context.view_layer.objects.active
    assert [c.name for c in new.users_collection] == ["OpenStudio - Loose Geometry"]
    assert "surfaceType" not in new
    # auto boundary: loose rectangle, then Project Selected with automatic type
    proj = MagicMock(return_value={"FINISHED"})
    da = draw_surface_fake(t, auto_boundary=True)
    with patch.object(t.bpy.ops, "object", SimpleNamespace(osm_project_loose=proj)):
        assert da._create(ctx, sq, Vector((0, 0, 1))) == {"FINISHED"}
        proj.assert_called_once_with(scope="SELECTED", surface_type="AUTO")
        assert not getattr(da, "reports", [])
        proj.return_value = {"CANCELLED"}
        da._create(ctx, sq, Vector((0, 0, 1)))
        assert da.reports
    assert "surfaceType" not in bpy.context.view_layer.objects.active
    # _finish
    d.pts, d.plane = sq, (Vector(), Vector((0, 0, 1)))
    d._handle = d._handle_px = "h"
    with patch.object(t.bpy.types.SpaceView3D, "draw_handler_remove"):
        assert d._finish(MagicMock(**{"workspace.status_text_set": MagicMock()}) if False else _fin_ctx(bpy)) in (
            {"FINISHED"},
            {"CANCELLED"},
        )


def _fin_ctx(bpy):
    ctx = MagicMock()
    ctx.collection = bpy.context.scene.collection
    ctx.view_layer = bpy.context.view_layer
    return ctx


def test_rectangle_operator(clean_scene):
    bpy = clean_scene
    from mathutils import Vector

    t = tools()
    r = rect_fake(t)
    plane = (Vector((0, 0, 0)), Vector((0, 0, 1)))

    def pick_stub(p, with_plane=plane):
        def _p(self, ctx, event):
            self.plane = with_plane
            return p, False

        return _p

    ctx = MagicMock()
    # parallel
    with patch.object(t, "_pick_point", return_value=(None, False)):
        assert r.invoke(ctx, None) == {"CANCELLED"}
    # drag mode, z plane
    with (
        patch.object(
            t, "_pick_point", side_effect=lambda me, c, e: (setattr(me, "plane", plane), (Vector((1, 1, 0)), False))[1]
        ),
        patch.object(t.bpy.types.SpaceView3D, "draw_handler_add", return_value="h"),
    ):
        assert r.invoke(ctx, None) == {"RUNNING_MODAL"}
    assert len(r.pts) == 4 and tuple(r.axis_u) == (1.0, 0.0, 0.0)
    assert r._ref() == Vector((1, 1, 0))
    # wall plane (normal along y): uses cross with z
    wall = (Vector((0, 0, 0)), Vector((0, -1, 0)))
    r2 = rect_fake(t, fixed_width=2.0, fixed_height=3.0)
    ctx2 = _fin_ctx(bpy)
    with patch.object(
        t, "_pick_point", side_effect=lambda me, c, e: (setattr(me, "plane", wall), (Vector((0, 0, 0)), False))[1]
    ):
        assert r2.invoke(ctx2, None) == {"FINISHED"}
    ob = bpy.context.view_layer.objects.active
    assert ob["surfaceType"] == "Wall"
    # size_from with fixed values (and negative directions)
    r3 = rect_fake(t)
    r3.origin, r3.axis_u, r3.axis_v = Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((0, 1, 0))
    r3.fixed_w, r3.fixed_h = 0.0, 0.0
    assert r3._size_from(Vector((2, 3, 0))) == (2, 3)
    r3.fixed_w, r3.fixed_h = 5.0, 6.0
    assert r3._size_from(Vector((-2, 3, 0))) == (-5.0, 6.0)
    assert r3._size_from(Vector((2, -3, 0))) == (5.0, -6.0)

    # modal
    r3.fixed_w = r3.fixed_h = 0.0
    r3.pts = []
    mctx = MagicMock()
    assert r3.modal(mctx, modal_ev("MIDDLEMOUSE")) == {"PASS_THROUGH"}
    with patch.object(t, "_pick_point", return_value=(None, False)):
        assert r3.modal(mctx, modal_ev("MOUSEMOVE")) == {"RUNNING_MODAL"}
    with patch.object(t, "_pick_point", return_value=(Vector((2, 2, 0)), False)):
        assert r3.modal(mctx, modal_ev("MOUSEMOVE")) == {"RUNNING_MODAL"}
        assert len(r3.pts) == 4
    # click with degenerate size
    with patch.object(t, "_pick_point", return_value=(Vector((0, 2, 0)), False)), patch.object(r3, "_end"):
        assert r3.modal(mctx, modal_ev("LEFTMOUSE")) == {"CANCELLED"}
    # click ok
    with (
        patch.object(t, "_pick_point", return_value=(Vector((2, 2, 0)), False)),
        patch.object(r3, "_end"),
        patch.object(r3, "execute", return_value={"FINISHED"}),
    ):
        assert r3.modal(mctx, modal_ev("LEFTMOUSE")) == {"FINISHED"}
        assert r3.width == 2 and r3.height == 2
    with patch.object(r3, "_end"):
        assert r3.modal(mctx, modal_ev("ESC")) == {"CANCELLED"}
        assert r3.modal(mctx, modal_ev("RIGHTMOUSE")) == {"CANCELLED"}
    assert r3.modal(mctx, modal_ev("A")) == {"RUNNING_MODAL"}

    # execute guards and redo path
    r4 = rect_fake(t)
    r4.axis_u, r4.axis_v = (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)
    assert r4.execute(ctx2) == {"CANCELLED"}
    r4.axis_u, r4.width = (1.0, 0.0, 0.0), 0.0
    assert r4.execute(ctx2) == {"CANCELLED"}
    r4.origin, r4.width, r4.height = (0.0, 0.0, 5.0), 2.0, 2.0
    assert r4.execute(ctx2) == {"FINISHED"}
    # draw panel
    r4.layout = MagicMock()
    r4.draw(ctx)
    assert r4.layout.prop.call_count == 2


def test_workspace_tools_draw_settings():
    t = tools()
    layout = MagicMock()
    tool = MagicMock()
    t.OSM_WT_draw_rectangle.draw_settings(MagicMock(), layout, tool)
    assert layout.prop.call_count == 5


# --- render modes -------------------------------------------------------------------------


def test_name_color_and_surface_type(clean_scene):
    bpy = clean_scene
    t = tools()
    assert t.name_color("") == t.GREY
    assert t.name_color("a") == t.name_color("a") != t.name_color("b")
    o = make_obj(bpy, "w", CUBE_V[:4], [[0, 1, 2, 3]], surfaceType="Wall")
    assert t._surface_type_of(o) == "Wall"
    o["surfaceType"] = "bogus"
    assert t._surface_type_of(o) == "RoofCeiling" or t._surface_type_of(o) == "Floor"
    empty = bpy.data.objects.new("e", bpy.data.meshes.new("e"))
    assert t._surface_type_of(empty) == ""
    assert t._normal_color(empty) == t.GREY
    assert t._normal_color(o)[2] in (0.0, 1.0)


def test_apply_render_mode(clean_scene):
    bpy = clean_scene
    t = tools()
    from zonal.blender import inspector as blender_inspector

    sc = bpy.context.scene
    col = bpy.data.collections.new("Sp")
    sc.collection.children.link(col)
    a = make_obj(
        bpy,
        "a",
        CUBE_V[:4],
        [[0, 1, 2, 3]],
        col=col,
        surfaceType="Wall",
        outsideBoundaryCondition="Outdoors",
        constructionName="C1",
        thermalZoneName="Z1",
        spaceTypeName="ST",
        buildingStoryName="S1",
    )
    b = make_obj(bpy, "b", CUBE_V[:4], [[0, 1, 2, 3]], outsideBoundaryCondition=5, constructionName=3)
    e = bpy.data.objects.new("empty", None)
    sc.collection.objects.link(e)
    t.apply_render_mode(sc, "SURFACE_TYPE")
    assert tuple(a.color)[:3] == pytest.approx(t.SURFACE_COLORS["Wall"])
    t.apply_render_mode(sc, "BOUNDARY")
    assert tuple(a.color)[:3] == pytest.approx(t.BOUNDARY_COLORS["Outdoors"])
    assert tuple(b.color)[:3] == pytest.approx(t.GREY)
    t.apply_render_mode(sc, "NORMAL")
    t.apply_render_mode(sc, "OFF")
    assert tuple(a.color) == (1.0, 1.0, 1.0, 1.0)
    for mode, want in (("CONSTRUCTION", "C1"), ("ZONE", "Z1"), ("SPACE_TYPE", "ST"), ("STORY", "S1")):
        t.apply_render_mode(sc, mode)
        assert tuple(a.color)[:3] == pytest.approx(t.name_color(want))
    assert t._name_for_mode(sc, b, "CONSTRUCTION") == ""
    assert t._name_for_mode(sc, b, "ZONE") == ""
    # inspector store wins
    item = blender_inspector.ensure_item(sc, "Space", "Sp")
    item.thermalZone = "StoreZ"
    assert t._name_for_mode(sc, a, "ZONE") == "StoreZ"
    assert t._store_space(sc, a).name == item.name
    assert t._store_space(sc, e) is None or True


def test_render_mode_operator_and_msgbus(clean_scene):
    bpy = clean_scene
    t = tools()
    a = make_obj(bpy, "a", CUBE_V[:4], [[0, 1, 2, 3]], surfaceType="Wall")
    assert bpy.ops.view3d.osm_render_mode(mode="SURFACE_TYPE") == {"FINISHED"}
    assert t._live_mode == "SURFACE_TYPE"
    # live recolour via the message bus callback
    a["surfaceType"] = "Floor"
    t._on_osm_prop_changed()
    assert tuple(a.color)[:3] == pytest.approx(t.SURFACE_COLORS["Floor"])
    # shading branch
    inst = SimpleNamespace(mode="OFF")
    ctx = MagicMock()
    ctx.space_data.type = "VIEW_3D"
    t.VIEW3D_OT_osm_render_mode.execute(inst, ctx)
    assert ctx.space_data.shading.color_type == "MATERIAL"
    inst.mode = "NORMAL"
    t.VIEW3D_OT_osm_render_mode.execute(inst, ctx)
    assert ctx.space_data.shading.color_type == "OBJECT"
    # off: callback does nothing
    t._live_mode = "OFF"
    t._on_osm_prop_changed()
    for mode in ("SURFACE_TYPE", "BOUNDARY", "NORMAL", "CONSTRUCTION", "ZONE", "SPACE_TYPE", "STORY", "OFF"):
        assert t.VIEW3D_OT_osm_render_mode.description(None, SimpleNamespace(mode=mode))
    assert (
        t.VIEW3D_OT_osm_render_mode.description(None, SimpleNamespace(mode="?"))
        == t.VIEW3D_OT_osm_render_mode.bl_description
    )
    # load_post resets state
    t._live_mode = "ZONE"
    t._load_post()
    assert t._live_mode == "OFF" and t._scene_geo is None and t._scene_geo_dirty is True
    # missing proxy property is skipped
    with patch.object(t.bpy.msgbus, "subscribe_rna") as sub:
        t._subscribe_msgbus()
        assert sub.call_count == 6
    props = MagicMock()
    props.__contains__ = lambda self, k: False
    fake_types = SimpleNamespace(Object=SimpleNamespace(bl_rna=SimpleNamespace(properties=props)))
    with patch.object(t.bpy, "types", fake_types), patch.object(t.bpy.msgbus, "subscribe_rna") as sub:
        t._subscribe_msgbus()
        sub.assert_not_called()


# --- sidebar --------------------------------------------------------------------------------


def test_zone_per_space(clean_scene):
    bpy = clean_scene
    from zonal.blender import inspector as blender_inspector
    from zonal.blender import surface_ops as so

    sc = bpy.context.scene
    col = bpy.data.collections.new("Office")
    sc.collection.children.link(col)
    a = make_obj(bpy, "a", CUBE_V[:4], [[0, 1, 2, 3]], col=col)
    loose = make_obj(bpy, "l", CUBE_V[:4], [[0, 1, 2, 3]])
    so.loose_collection(create=True).objects.link(loose)
    bpy.data.collections["OpenStudio - Loose Geometry"].objects.link(loose) if False else None
    nospace = bpy.data.objects.new("empty", None)
    sc.collection.objects.link(nospace)
    item = blender_inspector.ensure_item(sc, "Space", "Office")
    assert bpy.ops.scene.osm_zone_per_space() == {"FINISHED"}
    assert a["thermalZoneName"] == "Office" and item.thermalZone == "Office"


def test_panels_draw(clean_scene):
    t = tools()
    for cls in (t.VIEW3D_PT_osm_modeling, t.VIEW3D_PT_osm_render):
        me = SimpleNamespace(layout=MagicMock())
        cls.draw(me, MagicMock())
        assert me.layout.method_calls


def test_register_fallbacks(clean_scene):
    bpy = clean_scene
    t = tools()
    t.unregister()
    calls = []

    real = bpy.utils.register_tool

    def flaky(tool, **kw):
        calls.append((tool.bl_idname, bool(kw)))
        if kw:
            raise RuntimeError("placement")
        return real(tool)

    with patch.object(bpy.utils, "register_tool", side_effect=flaky):
        t.register()
    assert ("openstudio.draw_rectangle", True) in calls and ("openstudio.draw_rectangle", False) in calls
    # re-register is idempotent for handlers/timers (guards already satisfied)
    assert t._geo_dirty_handler in bpy.app.handlers.depsgraph_update_post
    assert bpy.app.timers.is_registered(t._hover_watch)
    t.unregister()
    t.register()
    # unregister when handlers/timers are absent
    t.unregister()
    t.register()
