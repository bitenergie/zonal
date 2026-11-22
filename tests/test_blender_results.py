"""In-process tests of the Results panel (blender_results) with the `bpy` module."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from mathutils import Vector

SAMPLE = os.environ.get("OSM_SAMPLE_SQL", "")
needs_sql = pytest.mark.skipif(not os.path.exists(SAMPLE), reason="sample eplusout.sql not available (OSM_SAMPLE_SQL)")


@pytest.fixture
def br(clean_scene):
    from zonal.blender import results as blender_results

    blender_results._cache.update(path="", sql=None)
    return blender_results


def _plane(bpy, name, loc=(0, 0, 0), size=1.0, zone="Thermal Zone", rot=None):
    bpy.ops.mesh.primitive_plane_add(size=size, location=loc)
    ob = bpy.context.object
    ob.name = name
    if zone:
        ob["thermalZoneName"] = zone
    if rot:
        ob.rotation_euler = rot
    bpy.context.view_layer.update()
    return ob


def _fake_ctx(bpy, **kw):
    return SimpleNamespace(
        scene=bpy.context.scene,
        selected_objects=list(bpy.context.selected_objects),
        window_manager=kw.get("wm", MagicMock()),
    )


# --- pure-ish helpers ---------------------------------------------------------------------


def test_enum_helpers(br):
    assert br._enum_items([], []) == [(br.NONE, "(none)", "")]
    assert br._enum_items([], ["a"]) == [("a", "a", "")]
    br._cache["sql"] = None
    assert br._envs(None, None)[0][0] == br.NONE
    fake = MagicMock()
    fake.env_periods.return_value = ["E1"]
    fake.frequencies.return_value = ["Hourly"]
    br._cache["sql"] = fake
    assert br._envs(None, None)[0][0] == "E1"
    assert br._freqs(SimpleNamespace(env="E1"), None)[0][0] == "Hourly"
    assert br._freqs(SimpleNamespace(env=br.NONE), None)[0][0] == br.NONE
    br._cache["sql"] = None


def test_refill_variables(br, clean_scene):
    s = clean_scene.context.scene.osm_results
    br._cache["sql"] = None
    br._refill_variables(s)
    assert len(s.variables) == 0
    fake = MagicMock()
    fake.variables.return_value = ["V1", "V2"]
    br._cache["sql"] = fake
    s.active = 5
    br._refill_variables(s)  # env / freq are NONE or invalid placeholders
    s.variables.add().name = "x"
    # force valid env / freq via a patched settings object
    st = SimpleNamespace(env="E", freq="F", variables=s.variables, active=5)
    br._refill_variables(st)
    assert [v.name for v in s.variables] == ["V1", "V2"] and st.active == 1
    br._env_changed(st, None)
    br._cache["sql"] = None


def test_materials_and_group(br, clean_scene):
    m = br.heatmap_material(0, 10)
    assert m.name == br.HEATMAP_MAT
    assert br.heatmap_material(2, 5) is m
    assert m.node_tree.nodes["Range"].inputs["From Max"].default_value == 5
    assert br.label_material() is br.label_material()
    ng = br.label_group()
    assert br.label_group() is ng
    ids = br._socket_ids(ng)
    assert {"Value", "Decimals", "Size", "Suffix"} <= set(ids)


def test_write_keys_and_fcurves(br, clean_scene):
    bpy = clean_scene
    ob = _plane(bpy, "A")
    ob["osm_result"] = 1.0
    assert list(br._fcurves(ob)) == []
    br.write_keys(ob, '["osm_result"]', [1, 2, 3], [1.0, 2.0, 3.0], "LINEAR")
    fc = br._find_fcurve(ob, '["osm_result"]')
    assert len(fc.keyframe_points) == 3
    br.write_keys(ob, '["osm_result"]', [1, 2], [5.0, 6.0], "CONSTANT")  # replaces
    assert len(br._find_fcurve(ob, '["osm_result"]').keyframe_points) == 2
    br._remove_fcurve(ob, '["osm_result"]')
    assert br._find_fcurve(ob, '["osm_result"]') is None
    br._remove_fcurve(ob, '["nope"]')  # no-op


def test_scene_queries(br, clean_scene):
    bpy = clean_scene
    a = _plane(bpy, "A")
    col = bpy.data.collections.new("Space X")
    bpy.context.scene.collection.children.link(col)
    b = _plane(bpy, "B")
    for c in b.users_collection:
        c.objects.unlink(b)
    col.objects.link(b)
    assert br._space_of(a) == ""
    assert br._space_of(b) == "Space X"
    a["spaceName"] = "Explicit"
    assert br._space_of(a) == "Explicit"
    lab = _plane(bpy, "L")
    lab[br.LABEL_PROP] = True
    ctx = SimpleNamespace(scene=bpy.context.scene, selected_objects=[b])
    assert {o.name for o in br._candidates(ctx, False)} == {"A", "B"}
    assert [o.name for o in br._candidates(ctx, True)] == ["B"]
    assert br._center([]) == Vector()
    assert br._center([a]).length < 1e-6


def test_face_pose_floor_wall_and_empty(br, clean_scene):
    bpy = clean_scene
    floor = _plane(bpy, "Floor")
    loc, rot = br._face_pose(floor, 0.1)
    assert abs(loc.z - 0.1) < 1e-6
    flipped = _plane(bpy, "Ceil", rot=(3.14159265, 0, 0))
    _, rot2 = br._face_pose(flipped, 0.1)
    assert rot2 != rot
    wall = _plane(bpy, "Wall", rot=(1.5707963, 0, 0))
    loc, rot = br._face_pose(wall, 0.05)
    assert abs((rot @ Vector((0, 1, 0))).z) > 0.99
    empty = bpy.data.objects.new("Empty", bpy.data.meshes.new("E"))
    bpy.context.scene.collection.objects.link(empty)
    loc, rot = br._face_pose(empty, 1.0)
    assert tuple(rot) == (1.0, 0.0, 0.0, 0.0)


def _wall_with_window(bpy, window_loc, window_size, offset=0.0):
    wall = _plane(bpy, "Wall", size=4.0, rot=(1.5707963, 0, 0))
    wall["surfaceType"] = "Wall"
    win = _plane(bpy, "Win", loc=window_loc, size=window_size, rot=(1.5707963, 0, 0))
    win["surfaceType"] = "FixedWindow"
    win["surfaceName"] = "Wall"
    return wall, win


@pytest.mark.parametrize(
    "win_loc,win_size",
    [
        ((0, 0, 1.5), 0.5),  # near the top: free strip below
        ((0, 0, -1.5), 0.5),  # near the bottom: free strip above
        ((1.6, 0, 0.0), 0.5),  # beside it (no vertical strip once block spans the full height)
        ((0, 0, 0), 4.0),  # covering everything: falls back to start
        ((-1.6, 0, 0), 0.5),
    ],
)
def test_face_pose_avoids_subsurfaces(br, clean_scene, win_loc, win_size):
    bpy = clean_scene
    wall, win = _wall_with_window(bpy, win_loc, win_size)
    subs = br._sub_points([wall, win])
    assert [o.name for o, _ in subs] == ["Win"]
    loc, _ = br._face_pose(wall, 0.0, subs, 0.1)
    assert isinstance(loc, Vector)
    # a sub-surface is never moved
    loc2, _ = br._face_pose(win, 0.0, subs, 0.1)
    assert (loc2 - win.matrix_world @ win.data.polygons[0].center).length < 1e-6


def test_free_spot_sideways_and_nonrect(br, clean_scene):
    bpy = clean_scene
    wall = _plane(bpy, "Wall", size=4.0, rot=(1.5707963, 0, 0))
    poly = wall.data.polygons[0]
    n = (wall.matrix_world.to_3x3() @ poly.normal).normalized()
    start = wall.matrix_world @ poly.center
    # a tall thin strip occupying the full height, offset to one side -> sideways branch
    for x, expect_right in ((1.0, False), (-1.0, True)):
        win = _plane(bpy, f"W{x}", loc=(x, 0, 0), size=1.0, rot=(1.5707963, 0, 0))
        win.scale = (1.0, 4.0, 1.0)
        win["surfaceType"] = "Door"
        bpy.context.view_layer.update()
        subs = br._sub_points([win])
        res = br._free_spot(wall, poly, n, start, subs, 0.0)
        assert (res - start).length > 0.1
        bpy.data.objects.remove(win)
    # more than 4 vertices -> start
    me = bpy.data.meshes.new("poly5")
    import math

    me.from_pydata([(math.cos(i), 0, math.sin(i)) for i in range(5)], [], [(0, 1, 2, 3, 4)])
    big = bpy.data.objects.new("Pent", me)
    bpy.context.scene.collection.objects.link(big)
    bpy.context.view_layer.update()
    assert br._free_spot(big, me.polygons[0], Vector((0, -1, 0)), Vector(), [(wall, [])]) == Vector()
    # floor normal (up vector switches)
    floor = _plane(bpy, "Fl", size=4.0)
    fp = floor.data.polygons[0]
    win = _plane(bpy, "FW", loc=(0, 0, 0), size=1.0)
    win["surfaceType"] = "Skylight"
    win["surfaceName"] = "Fl"
    subs = br._sub_points([win])
    assert isinstance(br._free_spot(floor, fp, Vector((0, 0, 1)), floor.matrix_world @ fp.center, subs), Vector)


def test_add_label_both_rotations(br, clean_scene):
    bpy = clean_scene
    s = bpy.context.scene.osm_results
    ctx = _fake_ctx(bpy)
    ob1 = br._add_label(ctx, s, "n1", Vector((1, 2, 3)), [1, 2], [1.0, 2.0], "C")
    assert br.LABEL_PROP in ob1 and ob1.rotation_euler[0] > 1.0
    s.label_upright = False
    ob2 = br._add_label(ctx, s, "n2", Vector(), [1, 2], [1.0, 2.0], "", rotation=ob1.rotation_euler.to_quaternion())
    assert ob2.rotation_mode == "QUATERNION"
    ob3 = br._add_label(ctx, s, "n3", Vector(), [1, 2], [1.0, 2.0], "")
    assert tuple(ob3.rotation_euler) == (0.0, 0.0, 0.0)


def test_clear(br, clean_scene):
    bpy = clean_scene
    a = _plane(bpy, "A")
    orig = bpy.data.materials.new("orig")
    a.data.materials.append(orig)
    a[br.PROP] = 1.0
    a[br.ORIG_MATS] = ["orig", "gone"]
    a.data.materials.append(br.heatmap_material(0, 1))
    b = _plane(bpy, "B")  # object with PROP, no ORIG_MATS
    b[br.PROP] = 2.0
    br._add_label(_fake_ctx(bpy), bpy.context.scene.osm_results, "l", Vector(), [1, 2], [1.0, 2.0], "")
    assert br._clear(None) == 3
    assert br.PROP not in a and [m.name for m in a.data.materials] == ["orig"]
    assert br._clear(None) == 0
    assert bpy.ops.scene.osm_results_clear() == {"FINISHED"}


# --- operators without SQL -------------------------------------------------------------------


def test_load_failure_and_apply_without_vars(br, clean_scene, tmp_path):
    bpy = clean_scene
    s = bpy.context.scene.osm_results
    assert bpy.ops.scene.osm_results_apply() == {"CANCELLED"}
    s.sql_path = str(tmp_path / "missing.sql")
    with pytest.raises(RuntimeError):  # ERROR reports raise through bpy.ops
        bpy.ops.scene.osm_results_load()
    assert s.status


def test_browse_operator(br, clean_scene):
    bpy = clean_scene
    op = br.SCENE_OT_osm_results_browse
    wm = MagicMock()
    fake = SimpleNamespace(filepath="")
    ctx = SimpleNamespace(scene=bpy.context.scene, window_manager=wm)
    assert op.invoke(fake, ctx, None) == {"RUNNING_MODAL"}  # empty path
    bpy.context.scene.osm_results.sql_path = "//x.sql"
    assert op.invoke(fake, ctx, None) == {"RUNNING_MODAL"}
    assert fake.filepath.endswith("x.sql")
    wm.fileselect_add.assert_called()
    with pytest.raises(RuntimeError):
        op.execute(SimpleNamespace(filepath="/nonexistent/none.sql"), bpy.context)


def test_apply_with_mocked_sql(br, clean_scene):
    """Drives apply() through every branch using a fake SqlResults."""
    bpy = clean_scene
    s = bpy.context.scene.osm_results
    _plane(bpy, "Cube", loc=(0, 0, 0))
    _plane(bpy, "Cube 1", loc=(2, 0, 0), rot=(1.5707963, 0, 0))
    win = _plane(bpy, "Win", loc=(2, 0, 0), size=0.2, rot=(1.5707963, 0, 0))
    win["surfaceType"] = "FixedWindow"
    win["surfaceName"] = "Cube 1"
    from zonal.core.results_core import Series

    ser = Series([1.0, 2.0, 3.0, 4.0], "C", "", 1.0)
    fake = MagicMock()
    fake.keys.side_effect = lambda e, f, v: {
        "Surface Inside Face Temperature": ["CUBE 1", "NOPE", "EMPTY"],
        "Zone Air Temperature": ["THERMAL ZONE"],
        "Electricity:Facility": [""],
    }[v]
    fake.series.side_effect = lambda e, f, v, k: (
        Series([], "C", "", 1.0) if k == "EMPTY" else (None if k == "NOPE" else ser)
    )
    br._cache.update(sql=fake, path=bpy.path.abspath(s.sql_path))
    s.env, s.freq = br.NONE, br.NONE  # (update callbacks clear the list, so fill it afterwards)
    for n in ("Surface Inside Face Temperature", "Zone Air Temperature", "Electricity:Facility"):
        s.variables.add().name = n

    # surface variable, labels + stride + manual range
    s.active = 0
    s.stride = 2
    s.manual_range, s.range_min, s.range_max = True, 0.0, 10.0
    assert bpy.ops.scene.osm_results_apply() == {"FINISHED"}, s.status
    assert "Surface Inside Face Temperature" in s.status
    assert bpy.data.objects["Cube 1"].data.materials[0].name == br.HEATMAP_MAT
    # zone variable, no heatmap, no set_range
    s.active, s.heatmap, s.set_range, s.manual_range, s.stride = 1, False, False, False, 1
    assert bpy.ops.scene.osm_results_apply() == {"FINISHED"}
    # meter, labels off, only selected
    s.active, s.labels, s.only_selected = 2, False, False
    assert bpy.ops.scene.osm_results_apply() == {"FINISHED"}
    s.labels = True
    assert bpy.ops.scene.osm_results_apply() == {"FINISHED"}  # kind other -> one label at building centre
    # key filter removing everything / no match
    s.key_filter = "zzz"
    assert bpy.ops.scene.osm_results_apply() == {"CANCELLED"}
    # sql error
    fake.keys.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        bpy.ops.scene.osm_results_apply()
    # surface var with no matching objects
    fake.keys.side_effect = lambda e, f, v: ["UNMATCHED"]
    s.key_filter, s.active = "", 0
    assert bpy.ops.scene.osm_results_apply() == {"CANCELLED"}
    br._cache.update(path="", sql=None)


# --- operators with the real SQL ---------------------------------------------------------------


@needs_sql
def test_real_sql_load_and_apply(br, clean_scene):
    pytest.importorskip("openstudio")
    bpy = clean_scene
    for i, name in enumerate(("Cube", "Cube 1")):
        _plane(bpy, name, loc=(i * 2, 0, 0))
    s = bpy.context.scene.osm_results
    s.sql_path = SAMPLE
    assert bpy.ops.scene.osm_results_load() == {"FINISHED"}, s.status
    names = [v.name for v in s.variables]
    assert "Zone Air Temperature" in names
    s.active = names.index("Zone Air Temperature")
    s.stride = 100
    assert bpy.ops.scene.osm_results_apply() == {"FINISHED"}, s.status
    # same path: cached sql reused
    assert br._sql(s) is br._cache["sql"]
    s.env = s.env  # triggers update callbacks
    assert bpy.ops.scene.osm_results_clear() == {"FINISHED"}
    # browse execute path loads the file
    assert br.SCENE_OT_osm_results_browse.execute(SimpleNamespace(filepath=SAMPLE), bpy.context) == {"FINISHED"}


# --- UI --------------------------------------------------------------------------------------


def test_panel_and_list_draw(br, clean_scene):
    bpy = clean_scene
    s = bpy.context.scene.osm_results
    ctx = SimpleNamespace(scene=bpy.context.scene)
    panel = br.VIEW3D_PT_osm_results
    fake = SimpleNamespace(layout=MagicMock())
    panel.draw(fake, ctx)  # no variables
    s.status = "hello"
    s.variables.add().name = "Zone Air Temperature"
    s.manual_range = True
    fake = SimpleNamespace(layout=MagicMock())
    panel.draw(fake, ctx)
    s.manual_range = False
    s.heatmap = False
    panel.draw(SimpleNamespace(layout=MagicMock()), ctx)
    for name in ("Surface Inside Face Temperature", "Electricity:Facility"):
        item = SimpleNamespace(name=name)
        layout = MagicMock()
        br.OSM_UL_results_vars.draw_item(None, ctx, layout, None, item, 0, None, None, 0)
        assert layout.row.return_value.label.call_count == 2
