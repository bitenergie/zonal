"""In-process Blender tests for blender_surface_ops (matching, loose geometry)."""

from conftest import requires_bpy

pytestmark = requires_bpy


def quad(bpy, name, pts, space=None, stype=None, loose=False, extra=None):
    from zonal.blender import surface_ops as so

    me = bpy.data.meshes.new(name)
    me.from_pydata(pts, [], [list(range(len(pts)))])
    me.update()
    ob = bpy.data.objects.new(name, me)
    if loose:
        so.loose_collection(create=True).objects.link(ob)
    elif space:
        col = bpy.data.collections.get(space) or bpy.data.collections.new(space)
        if col.name not in bpy.context.scene.collection.children:
            bpy.context.scene.collection.children.link(col)
        col.objects.link(ob)
        ob["spaceName"] = space
    else:
        bpy.context.scene.collection.objects.link(ob)
    if stype:
        ob["surfaceType"] = stype
    for k, v in (extra or {}).items():
        ob[k] = v
    return ob


def select(bpy, *obs):
    for o in bpy.context.view_layer.objects.selected:
        o.select_set(False)
    for o in obs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = obs[-1] if obs else None


def test_loose_collection_and_is_loose(clean_scene):
    bpy = clean_scene
    from zonal.blender import surface_ops as so

    assert so.loose_collection() is None
    ob = bpy.data.objects.new("x", bpy.data.meshes.new("x"))
    bpy.context.scene.collection.objects.link(ob)
    assert not so.is_loose(ob)
    col = so.loose_collection(create=True)
    assert so.loose_collection() is col
    col.objects.link(ob)
    assert so.is_loose(ob)


def test_surface_type_and_space_of(clean_scene):
    bpy = clean_scene
    from zonal.blender import surface_ops as so

    a = quad(bpy, "a", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], stype="Wall")
    assert so._surface_type(a, a.data.polygons[0]) == "Wall"
    b = quad(bpy, "b", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    assert so._surface_type(b, b.data.polygons[0]) in ("Floor", "RoofCeiling")
    assert so._space_of(b) == "Scene Collection" or so._space_of(b) == ""
    c = quad(bpy, "c", [(0, 0, 0), (1, 0, 0), (1, 1, 0)], space="Room")
    assert so._space_of(c) == "Room"
    c["spaceName"] = ""
    assert so._space_of(c) == "Room"
    d = quad(bpy, "d", [(0, 0, 0), (1, 0, 0), (1, 1, 0)], loose=True)
    assert so._space_of(d) == ""
    assert so._plain(5) == 5


def test_plain_conversions(clean_scene):
    from zonal.blender import surface_ops as so

    class D:
        def to_dict(self):
            return {"a": 1}

    class L:
        def to_list(self):
            return [1]

    assert so._plain(D()) == {"a": 1}
    assert so._plain(L()) == [1]


def test_set_world_polygon_and_clone(clean_scene):
    bpy = clean_scene
    from zonal.blender import surface_ops as so

    mat = bpy.data.materials.new("m")
    ob = quad(
        bpy, "S", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], space="R", stype="Wall", extra={"_hidden": 1, "k": 2}
    )
    ob.data.materials.append(mat)
    names = {o.name for o in bpy.data.objects}
    new = so._clone_surface(ob, [(0, 0, 0), (2, 0, 0), (2, 2, 0)], names)
    assert new.name != ob.name and new["k"] == 2 and "_hidden" not in new
    assert new.data.materials[0] == mat
    old = ob.data
    so._set_world_polygon(ob, [(0, 0, 0), (3, 0, 0), (3, 3, 0)])
    assert ob.data is not old and len(ob.data.vertices) == 3
    # shared mesh keeps old data alive (users > 0)
    twin = bpy.data.objects.new("twin", ob.data)
    bpy.context.scene.collection.objects.link(twin)
    shared = ob.data
    so._set_world_polygon(ob, [(0, 0, 0), (1, 0, 0), (1, 5, 0)])
    assert twin.data is shared


def test_match_full_overlap_and_unmatch(clean_scene):
    bpy = clean_scene
    a = quad(bpy, "Ceil A", [(0, 0, 3), (2, 0, 3), (2, 2, 3), (0, 2, 3)], "A", "RoofCeiling")
    b = quad(bpy, "Floor B", [(0, 0, 3), (0, 2, 3), (2, 2, 3), (2, 0, 3)], "B", "Floor")
    w1 = quad(bpy, "Win A", [(0.5, 0.5, 3), (1, 0.5, 3), (1, 1, 3), (0.5, 1, 3)], "A", "FixedWindow")
    w2 = quad(bpy, "Win B", [(0.5, 0.5, 3), (0.5, 1, 3), (1, 1, 3), (1, 0.5, 3)], "B", "FixedWindow")
    select(bpy, a, b, w1, w2)
    assert bpy.ops.object.osm_match_surfaces(mode="MATCH", scope="SELECTED") == {"FINISHED"}
    assert a["outsideBoundaryCondition"] == "Surface" and a["outsideBoundaryConditionObjectName"] == "Floor B"
    assert w1["outsideBoundaryConditionObjectName"] == "Win B"
    assert len([o for o in bpy.data.objects]) == 4  # identical outlines: no split
    # unmatch with scope selection of only one side clears both
    select(bpy, a)
    bpy.ops.object.osm_match_surfaces(mode="UNMATCH", scope="SELECTED")
    assert "outsideBoundaryCondition" not in a and "outsideBoundaryCondition" not in b
    assert w1.get("outsideBoundaryCondition") == "Surface"
    # unmatch an object whose partner is missing; ALL scope
    w1["outsideBoundaryConditionObjectName"] = "nope"
    bpy.ops.object.osm_match_surfaces(mode="UNMATCH", scope="ALL")
    assert "outsideBoundaryCondition" not in w1 and "outsideBoundaryCondition" not in w2
    # unmatch with nothing selected: object not wanted
    w1["outsideBoundaryCondition"] = "Surface"
    select(bpy)
    bpy.ops.object.osm_match_surfaces(mode="UNMATCH", scope="SELECTED")
    assert w1["outsideBoundaryCondition"] == "Surface"


def test_match_partial_split_with_stale_and_clone_selection(clean_scene):
    bpy = clean_scene
    a = quad(
        bpy,
        "Ceil A",
        [(0, 0, 3), (2, 0, 3), (2, 2, 3), (0, 2, 3)],
        "A",
        "RoofCeiling",
        extra={"outsideBoundaryCondition": "Surface", "outsideBoundaryConditionObjectName": "x"},
    )
    b = quad(
        bpy,
        "Floor B",
        [(1, 0, 3), (1, 2, 3), (3, 2, 3), (3, 0, 3)],
        "B",
        "Floor",
        extra={"outsideBoundaryCondition": "Surface"},
    )
    c = quad(bpy, "Floor C", [(10, 0, 3), (10, 2, 3), (12, 2, 3), (12, 0, 3)], "A", "Floor")
    select(bpy, a, b, c)
    bpy.ops.object.osm_match_surfaces(mode="MATCH", scope="SELECTED")
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert len(objs) == 5
    assert abs(sum(o.data.polygons[0].area for o in objs) - 8 - 4) < 1e-6  # incl. 4 for C
    stale = [o for o in objs if o.get("outsideBoundaryCondition") == "Surface"]
    assert len(stale) == 2
    # scope ALL with same-space pair skipped and non-wanted pair skipped
    select(bpy, c)
    assert bpy.ops.object.osm_match_surfaces(mode="MATCH", scope="SELECTED") == {"FINISHED"}


def test_match_unselected_clone_and_same_space(clean_scene):
    bpy = clean_scene
    a = quad(bpy, "Ceil A", [(0, 0, 3), (2, 0, 3), (2, 2, 3), (0, 2, 3)], "A", "RoofCeiling")
    quad(bpy, "Floor B", [(1, 0, 3), (1, 2, 3), (3, 2, 3), (3, 0, 3)], "B", "Floor")
    d = quad(bpy, "Floor A2", [(1, 0, 3), (1, 2, 3), (3, 2, 3), (3, 0, 3)], "A", "Floor")
    select(bpy)
    bpy.ops.object.osm_match_surfaces(mode="MATCH", scope="ALL")
    assert a.get("outsideBoundaryCondition") == "Surface"
    del d


def test_to_loose_and_project(clean_scene):
    bpy = clean_scene
    from zonal.blender import surface_ops as so

    quad(bpy, "Wall", [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)], "R", "Wall")
    multi = bpy.data.objects.new("multi", bpy.data.meshes.new("multi"))
    multi.data.from_pydata(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 1, 0), (2, 0, 0)], [], [[0, 1, 2, 3], [1, 5, 4, 2]]
    )
    bpy.context.scene.collection.objects.link(multi)
    win = quad(bpy, "WinAid", [(1, 0, 1), (2, 0, 1), (2, 0, 2), (1, 0, 2)])
    off = quad(bpy, "Off", [(1, 5, 1), (2, 5, 1), (2, 5, 2), (1, 5, 2)])
    select(bpy, win, off)
    assert bpy.ops.object.osm_to_loose() == {"FINISHED"}
    assert so.is_loose(win) and so.is_loose(off)
    assert [c.name for c in win.users_collection] == [so.LOOSE_COLLECTION]
    # project selected: only win selected
    select(bpy, win)
    assert bpy.ops.object.osm_project_loose(scope="SELECTED", surface_type="Door") == {"FINISHED"}
    subs = [o for o in bpy.data.objects if o.get("osm_source")]
    assert len(subs) == 1 and subs[0]["surfaceType"] == "Door" and subs[0]["surfaceName"] == "Wall"
    assert subs[0].users_collection[0].name == "R"  # same space as its host
    assert subs[0].modifiers  # displace
    # re-run all: replaces earlier projection; 'off' ignored, multi skipped
    select(bpy)
    res = bpy.ops.object.osm_project_loose(scope="ALL", replace=True)
    assert res == {"FINISHED"}
    assert len([o for o in bpy.data.objects if o.get("osm_source")]) == 1
    # no replace -> duplicates
    bpy.ops.object.osm_project_loose(scope="ALL", replace=False)
    assert len([o for o in bpy.data.objects if o.get("osm_source")]) == 2


def test_project_loose_cancel_paths(clean_scene):
    bpy = clean_scene
    from zonal.blender import surface_ops as so

    # nothing: poll false
    assert not bpy.ops.object.osm_project_loose.poll()
    # picked mesh, no hosts -> made 0 -> CANCELLED (warning)
    m = quad(bpy, "Aid", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    select(bpy, m)
    assert bpy.ops.object.osm_project_loose() == {"CANCELLED"}
    assert so.is_loose(m)
    # execute directly with no loose collection and nothing selected
    bpy.data.objects.remove(m)
    bpy.data.collections.remove(so.loose_collection())
    op = so.OBJECT_OT_osm_project_loose
    from unittest.mock import MagicMock

    inst = MagicMock()
    inst.scope, inst.surface_type, inst.tolerance, inst.replace = "ALL", "FixedWindow", 1e-3, True
    ctx = MagicMock()
    ctx.selected_objects = []
    assert op.execute(inst, ctx) == {"CANCELLED"}
    inst.report.assert_called()


def test_to_loose_poll(clean_scene):
    bpy = clean_scene
    assert not bpy.ops.object.osm_to_loose.poll()


def test_register_unregister_cycle(clean_scene):
    from zonal.blender import surface_ops as so

    so.unregister()
    so.register()


def test_intersect_cuts_a_wall_where_a_slab_passes_through(clean_scene):
    bpy = clean_scene
    wall = quad(bpy, "wall", [(0, 0, 0), (10, 0, 0), (10, 0, 3), (0, 0, 3)], space="A", stype="Wall")
    slab = quad(bpy, "slab", [(-1, -1, 1.5), (-1, 5, 1.5), (11, 5, 1.5), (11, -1, 1.5)], space="B", stype="Floor")
    select(bpy, wall, slab)
    assert bpy.ops.object.osm_match_surfaces(mode="INTERSECT", scope="ALL") == {"FINISHED"}
    walls = [o for o in bpy.data.objects if o.name.startswith("wall")]
    assert len(walls) == 2
    areas = sorted(round(o.data.polygons[0].area, 6) for o in walls)
    assert areas == [15.0, 15.0]
    assert all(o["surfaceType"] == "Wall" and o["spaceName"] == "A" for o in walls)
    # no boundary conditions are touched by an intersect
    assert not any("outsideBoundaryCondition" in o for o in bpy.data.objects)


def test_intersect_skips_surfaces_with_sub_surfaces(clean_scene):
    bpy = clean_scene
    wall = quad(bpy, "wall", [(0, 0, 0), (10, 0, 0), (10, 0, 3), (0, 0, 3)], space="A", stype="Wall")
    quad(
        bpy,
        "win",
        [(1, 0, 0.5), (2, 0, 0.5), (2, 0, 1), (1, 0, 1)],
        space="A",
        stype="FixedWindow",
        extra={"surfaceName": "wall"},
    )
    quad(bpy, "slab", [(-1, -1, 1.5), (-1, 5, 1.5), (11, 5, 1.5), (11, -1, 1.5)], space="B", stype="Floor")
    select(bpy, wall)
    assert bpy.ops.object.osm_match_surfaces(mode="INTERSECT", scope="ALL") == {"FINISHED"}
    assert len([o for o in bpy.data.objects if o.name.startswith("wall")]) == 1


def test_assign_space_moves_sub_surfaces(clean_scene):
    bpy = clean_scene
    from zonal.blender.props import assign_space

    wall = quad(bpy, "Wall", [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)], "R", "Wall")
    win = quad(
        bpy, "Win", [(1, 0, 1), (2, 0, 1), (2, 0, 2), (1, 0, 2)], "R", "FixedWindow", extra={"surfaceName": "Wall"}
    )
    assign_space(wall, "Other")
    assert [c.name for c in wall.users_collection] == ["Other"]
    assert [c.name for c in win.users_collection] == ["Other"]
    assign_space(win, "Third")  # a sub surface alone moves only itself
    assert [c.name for c in wall.users_collection] == ["Other"]
