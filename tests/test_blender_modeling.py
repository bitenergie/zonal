"""In-process Blender tests for blender/modeling.py (diagram, space attributes, shading / partition groups)."""

import pytest

from conftest import requires_bpy
from test_blender_surface_ops import quad, select

pytestmark = requires_bpy

PLAN = [(0, 0, 0), (10, 0, 0), (10, 5, 0), (0, 5, 0)]


def test_spaces_from_diagram(clean_scene):
    bpy = clean_scene
    plan = quad(bpy, "plan", PLAN)
    select(bpy, plan)
    assert bpy.ops.object.osm_spaces_from_diagram(floor_height=3.0, num_floors=2) == {"FINISHED"}
    assert "plan" not in bpy.data.objects  # consumed
    cols = sorted(c.name for c in bpy.data.collections)
    assert cols == ["Story 1 Space 1", "Story 2 Space 1"]
    c2 = bpy.data.collections["Story 2 Space 1"]
    assert len(c2.objects) == 6
    types = sorted(o["surfaceType"] for o in c2.objects)
    assert types == ["Floor", "RoofCeiling", "Wall", "Wall", "Wall", "Wall"]
    assert not any("buildingStoryName" in o or "thermalZoneName" in o for o in c2.objects)  # on the space only
    item = bpy.context.scene.osm_model[0] if len(bpy.context.scene.osm_model) == 1 else None
    from zonal.blender.inspector import get_item

    item = get_item(bpy.context.scene, "Space", "Story 2 Space 1")
    assert item.buildingStory == "Story 2" and item.thermalZone == "Story 2 Space 1"
    floor = next(o for o in c2.objects if o["surfaceType"] == "Floor")
    assert round(floor.data.vertices[0].co.z, 6) == 3.0
    assert floor.data.polygons[0].normal.z < -0.99


def test_diagram_rejects_vertical_faces_and_empty_selection(clean_scene):
    bpy = clean_scene
    wall = quad(bpy, "wall", [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)])
    select(bpy, wall)
    with pytest.raises(RuntimeError, match="horizontal"):  # operator errors surface as RuntimeError from Python
        bpy.ops.object.osm_spaces_from_diagram()
    assert "wall" in bpy.data.objects
    done = quad(bpy, "done", PLAN, stype="Floor")  # already an OpenStudio surface: not a plan
    select(bpy, done)
    assert bpy.ops.object.osm_spaces_from_diagram() == {"CANCELLED"}


def test_set_space_attributes(clean_scene):
    bpy = clean_scene
    a = quad(bpy, "a", PLAN, space="Office", stype="Floor")
    b = quad(bpy, "b", PLAN, space="Office", stype="RoofCeiling")
    c = quad(bpy, "c", PLAN, space="Hall", stype="Floor")
    select(bpy, a)
    assert bpy.ops.object.osm_set_space_attributes(space_type="Office Type", building_story="Story 3") == {"FINISHED"}
    from zonal.blender.inspector import get_item

    scene = bpy.context.scene
    office = get_item(scene, "Space", "Office")
    assert office.spaceType == "Office Type" and office.buildingStory == "Story 3"
    assert b.osm_spaceTypeName == "Office Type"  # whole space
    assert get_item(scene, "Space", "Hall") is None and c.osm_spaceTypeName == ""  # other spaces untouched
    assert bpy.ops.object.osm_set_space_attributes(zone_per_space=True) == {"FINISHED"}
    assert office.thermalZone == "Office"
    assert bpy.ops.object.osm_set_space_attributes() == {"CANCELLED"}  # nothing to set


def test_set_shading_and_partition(clean_scene):
    bpy = clean_scene
    a = quad(bpy, "a", PLAN, space="Office", stype="Wall")
    select(bpy, a)
    assert bpy.ops.object.osm_set_shading(group="SpaceShading") == {"FINISHED"}
    assert a["surfaceType"] == "SpaceShading" and a["spaceName"] == "Office"
    bpy.ops.object.osm_set_shading(group="SiteShading")
    assert a["surfaceType"] == "SiteShading" and "spaceName" not in a
    assert bpy.ops.object.osm_set_partition(space="Hall") == {"FINISHED"}
    assert a["surfaceType"] == "InteriorPartitionSurface" and a.users_collection[0].name == "Hall"


def test_hover_lines(clean_scene):
    bpy = clean_scene
    from zonal.blender.modeling import hover_lines

    wall = quad(
        bpy,
        "wall",
        [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)],
        space="Office",
        stype="Wall",
        extra={"constructionName": "Brick"},
    )
    lines = hover_lines(bpy.context, wall, 0)
    assert lines[0] == "Surface: wall" and "Space: Office" in lines and "Construction: Brick" in lines
    assert "Area: 12.00 m²" in lines
    assert hover_lines(bpy.context, wall, 5) == [] and hover_lines(bpy.context, None, 0) == []
    loose = quad(bpy, "aid", PLAN, loose=True)
    assert hover_lines(bpy.context, loose, 0) == []


def _mesh_object(bpy, name, verts, faces):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _boxes(*specs):
    verts, faces = [], []
    for x0, y0, z0, x1, y1, z1 in specs:
        b = len(verts)
        verts += [
            (x0, y0, z0),
            (x1, y0, z0),
            (x1, y1, z0),
            (x0, y1, z0),
            (x0, y0, z1),
            (x1, y0, z1),
            (x1, y1, z1),
            (x0, y1, z1),
        ]
        faces += [
            [i + b for i in f]
            for f in ([0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7])
        ]
    return verts, faces


def test_spaces_from_mesh_matches_shared_walls(clean_scene):
    bpy = clean_scene
    # a 2 x 2 room block (one wall only half shared) + a room on top
    ob = _mesh_object(
        bpy, "massing", *_boxes((0, 0, 0, 4, 4, 3), (4, 0, 0, 8, 2, 3), (4, 2, 0, 8, 4, 3), (0, 0, 3, 4, 4, 6))
    )
    select(bpy, ob)
    assert bpy.ops.object.osm_spaces_from_mesh(zone_per_space=True) == {"FINISHED"}
    assert "massing" not in bpy.data.objects
    cols = sorted(c.name for c in bpy.data.collections)
    assert cols == ["Story 1 Space 1", "Story 1 Space 2", "Story 1 Space 3", "Story 2 Space 1"]
    from zonal.blender.inspector import get_item

    scene = bpy.context.scene
    assert get_item(scene, "Space", "Story 2 Space 1").buildingStory == "Story 2"
    assert get_item(scene, "Space", "Story 1 Space 2").thermalZone == "Story 1 Space 2"
    matched = [o for o in bpy.data.objects if o.get("outsideBoundaryCondition") == "Surface"]
    # big room wall split in two halves (2) + 2 partners, partition between the small rooms (2), ceiling/floor (2)
    assert len(matched) == 8
    for o in matched:
        other = bpy.data.objects[o["outsideBoundaryConditionObjectName"]]
        assert other["outsideBoundaryConditionObjectName"] == o.name
        assert o.users_collection[0] is not other.users_collection[0]


def test_spaces_from_mesh_rejects_nonvolumes(clean_scene):
    bpy = clean_scene
    ob = _mesh_object(bpy, "sheet", PLAN, [[0, 1, 2, 3]])
    select(bpy, ob)
    with pytest.raises(RuntimeError, match="No enclosed volume"):
        bpy.ops.object.osm_spaces_from_mesh(keep_open=False)
    assert "sheet" in bpy.data.objects  # untouched


def test_spaces_from_mesh_self_intersect_option(clean_scene):
    bpy = clean_scene
    ob = _mesh_object(bpy, "overlap", *_boxes((0, 0, 0, 4, 4, 3), (2, 1, 0, 6, 3, 3)))
    select(bpy, ob)
    assert bpy.ops.object.osm_spaces_from_mesh(self_intersect=True, match=False) == {"FINISHED"}
    assert len(bpy.data.collections) >= 3  # A only, B only and the overlap


def test_spaces_from_mesh_modifiers_option(clean_scene):
    bpy = clean_scene
    ob = _mesh_object(bpy, "half", *_boxes((0, 0, 0, 2, 2, 3)))
    mod = ob.modifiers.new("arr", "ARRAY")
    mod.count, mod.relative_offset_displace = 3, (2.0, 0, 0)
    select(bpy, ob)
    assert bpy.ops.object.osm_spaces_from_mesh(apply_modifiers=False, match=False, remove_source=False) == {"FINISHED"}
    assert len(bpy.data.collections) == 1
    for c in list(bpy.data.collections):
        for o in list(c.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(c)
    assert bpy.ops.object.osm_spaces_from_mesh(apply_modifiers=True, match=False) == {"FINISHED"}
    assert len(bpy.data.collections) == 3
