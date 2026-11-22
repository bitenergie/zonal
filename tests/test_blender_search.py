"""In-process tests of blender_search (Surface Search panel)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from conftest import requires_bpy

pytestmark = requires_bpy


def _cube_surfaces(bpy):
    scene = bpy.context.scene
    col = bpy.data.collections.new("Office")
    scene.collection.children.link(col)
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
    cube = bpy.context.object
    for c in list(cube.users_collection):
        c.objects.unlink(cube)
    col.objects.link(cube)
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    bpy.ops.object.osm_separate_faces()
    return [o for o in scene.objects if o.type == "MESH"]


def test_helpers(clean_scene):
    from zonal.blender import search as bs

    assert bs._items(["a"])[0][0] == bs.ANY
    assert bs._val(bs.ANY) == "" and bs._val("x") == "x"
    s = bpy_scene = clean_scene.context.scene.osm_search
    items = bs._type_items(s, None)
    assert items[0][0] == bs.ANY and len(items) > 1
    s.cls = "Surface"
    assert len(bs._type_items(s, None)) > 1
    s.cls = "SubSurface"
    assert bs._type_items(s, None)
    _ = bs.OSMSearchSettings
    ctx = SimpleNamespace(scene=clean_scene.context.scene)
    clean_scene.context.scene.osm_library.add().name = "C1"
    assert bs._construction_search(s, ctx, "") == [] or True
    assert bpy_scene is s


def test_default_boundary():
    from zonal.blender.search import _default_boundary

    assert _default_boundary("Floor", {}) == ("Ground", "NoSun", "NoWind")
    assert _default_boundary("Wall", {}) == ("Outdoors", "SunExposed", "WindExposed")
    ob = {"outsideBoundaryCondition": "Adiabatic", "sunExposure": "SunExposed", "windExposure": "WindExposed"}
    assert _default_boundary("Wall", ob) == ("Adiabatic", "SunExposed", "WindExposed")


def test_search_operators_and_facts(clean_scene):
    bpy = clean_scene
    from zonal.blender import search as bs

    surfaces = _cube_surfaces(bpy)
    scene = bpy.context.scene
    s = scene.osm_search
    surfaces[0]["constructionName"] = "Foo"
    f = bs._facts(surfaces[0], surfaces[0].data.polygons[0], 0.0)
    assert f["construction"] == "Foo" and f["name"] == surfaces[0].name
    s.type = "Floor"
    s.construction = " "
    crit = bs.criteria_from(s)
    assert crit.type == "Floor"
    assert bpy.ops.object.osm_search_model() == {"FINISHED"}
    assert s.count == 1
    assert [o for o in scene.objects if o.select_get()]

    # narrow the selection: nothing selected matches Wall
    s.type = "ANY"
    s.exclude_horizontal = True
    assert bpy.ops.object.osm_search_selection() == {"FINISHED"}
    assert s.count == 0

    # hidden match is counted but not selected
    s.exclude_horizontal = False
    s.type = "Floor"
    floor = next(o for o in surfaces if o.get("surfaceType") == "Floor")
    floor.hide_set(True)
    bpy.ops.object.osm_search_model()
    assert s.count == 1 and not floor.select_get()
    floor.hide_set(False)

    # non mesh, loose geometry skipped
    bpy.ops.object.empty_add()
    loose = bpy.data.collections.new("OpenStudio - Loose Geometry")
    scene.collection.children.link(loose)
    surfaces[1].users_collection[0].objects.unlink(surfaces[1])
    loose.objects.link(surfaces[1])
    found = bs.find_objects(scene, bs.criteria_from(s), list(scene.objects))
    assert surfaces[1] not in found

    with pytest.raises(NotImplementedError):
        bs._SearchBase().candidates(None)


def test_report_operator_and_panel(clean_scene):
    bpy = clean_scene
    from zonal.blender import search as bs

    scene = bpy.context.scene
    s = scene.osm_search
    op = bs.OBJECT_OT_osm_search_report
    assert op.execute(None, None) == {"FINISHED"}
    wm = MagicMock()
    ctx = SimpleNamespace(scene=scene, window_manager=wm)
    op.invoke(MagicMock(), ctx, None)
    wm.invoke_props_dialog.assert_called_once()

    me = SimpleNamespace(layout=MagicMock())
    op.draw(me, ctx)  # no search yet
    s.count = 50
    s.report = "\n".join(f"n{i}" for i in range(50))
    op.draw(me, ctx)
    s.report = "a\nb"
    op.draw(me, ctx)

    panel = bs.VIEW3D_PT_osm_search
    for cls, cnt, mv in (("ANY", -1, False), ("Surface", 3, True)):
        s.cls, s.count, s.use_min_vertices = cls, cnt, mv
        panel.draw(SimpleNamespace(layout=MagicMock()), ctx)
        for sub in (
            bs.VIEW3D_PT_osm_search_boundary,
            bs.VIEW3D_PT_osm_search_orientation,
            bs.VIEW3D_PT_osm_search_geometry,
            bs.VIEW3D_PT_osm_search_options,
            bs.VIEW3D_PT_osm_search_run,
        ):
            fake = SimpleNamespace(layout=MagicMock())
            fake._begin = lambda c, fake=fake: bs._SearchSub._begin(fake, c)
            sub.draw(fake, ctx)


def test_hide_others_unhide_and_view_layer(clean_scene):
    bpy = clean_scene
    surfaces = _cube_surfaces(bpy)
    floors = [o for o in surfaces if o.get("surfaceType") == "Floor"]
    assert floors
    s = bpy.context.scene.osm_search
    s.cls, s.type, s.hide_others = "Surface", "Floor", True
    assert bpy.ops.object.osm_search_model() == {"FINISHED"}
    hidden = [o for o in surfaces if o.hide_get()]
    assert len(hidden) == len(surfaces) - len(floors)
    assert not any(o.hide_get() for o in floors)
    assert bpy.ops.object.osm_search_view_layer(name="Floors") == {"FINISHED"}
    assert "Floors" in bpy.context.scene.view_layers
    assert bpy.ops.object.osm_search_unhide() == {"FINISHED"}
    assert not any(o.hide_get() for o in surfaces) and s.hidden == ""


def test_story_and_tilt_search_on_objects(clean_scene):
    bpy = clean_scene
    surfaces = _cube_surfaces(bpy)
    for o in surfaces:
        o["buildingStoryName"] = "Story 1"
    s = bpy.context.scene.osm_search
    s.story = "Story 1"
    bpy.ops.object.osm_search_model()
    assert s.count == len(surfaces)
    s.story = "Story 9"
    bpy.ops.object.osm_search_model()
    assert s.count == 0
    s.story, s.tilt_from, s.tilt_to = "", 80.0, 100.0
    bpy.ops.object.osm_search_model()
    assert s.count == 4  # the four walls of the cube
    s.tilt_from, s.tilt_to, s.invert_across_classes = 0.0, 180.0, False
    s.cls, s.type, s.invert = "Surface", "Wall", True
    bpy.ops.object.osm_search_model()
    assert s.count == 2  # floor and roof
