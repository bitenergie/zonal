"""The Properties editor (blender_props proxies / draw_object_fields) and the Inspector must show the same values."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from conftest import requires_bpy
from zonal.core import inspector_schema as S
from zonal.core import schema

pytestmark = requires_bpy

ENUM_KEYS = ("surfaceType", "outsideBoundaryCondition", "sunExposure", "windExposure")
STRING_KEYS = (
    "spaceName",
    "thermalZoneName",
    "constructionName",
    "surfaceName",
    "spaceTypeName",
    "buildingStoryName",
    "buildingUnitName",
    "constructionSetName",
    "outsideBoundaryConditionObjectName",
)


class _Layout:
    """Fake UI layout that records every prop(data, name) call, however deeply nested."""

    def __init__(self, calls=None):
        self.calls = calls if calls is not None else []

    def prop(self, data, name, **kw):
        self.calls.append((data, name))

    def panel(self, *a, **k):
        return _Layout(self.calls), _Layout(self.calls)

    def __getattr__(self, name):
        if name in ("row", "column", "box", "split", "grid_flow"):
            return lambda *a, **k: _Layout(self.calls)
        return lambda *a, **k: MagicMock()


def _cube(bpy, collection="Office"):
    from zonal.blender import inspector as bi

    bi._type_items_state.update(items=[], scene=None, n=-1)
    scene = bpy.context.scene
    col = bpy.data.collections.new(collection)
    scene.collection.children.link(col)
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
    cube = bpy.context.object
    for c in list(cube.users_collection):
        c.objects.unlink(cube)
    col.objects.link(cube)
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    bpy.ops.object.osm_separate_faces()
    surfaces = sorted((o for o in scene.objects if o.type == "MESH"), key=lambda o: o.name)
    return scene, surfaces


def _ctx(bpy):
    return SimpleNamespace(scene=bpy.context.scene, view_layer=bpy.context.view_layer, window_manager=MagicMock())


SPACE_KEYS = (
    "spaceName",
    "thermalZoneName",
    "spaceTypeName",
    "buildingStoryName",
    "buildingUnitName",
    "constructionSetName",
)


@pytest.mark.parametrize("key", [k for k in STRING_KEYS if k not in SPACE_KEYS])
def test_string_proxy_matches_custom_property(clean_scene, key):
    _scene, surfaces = _cube(clean_scene)
    ob = surfaces[0]
    assert getattr(ob, f"osm_{key}") == (ob.get(key) or "")
    ob[key] = "Alpha"
    assert getattr(ob, f"osm_{key}") == "Alpha"
    setattr(ob, f"osm_{key}", "Beta")
    assert ob[key] == "Beta"
    setattr(ob, f"osm_{key}", "")
    assert key not in ob and getattr(ob, f"osm_{key}") == ""


@pytest.mark.parametrize("key", [k for k in SPACE_KEYS if k != "spaceName"])
def test_space_attribute_proxy_is_one_value_per_space(clean_scene, key):
    """A space attribute set on one surface is the value of the whole space (Space store), never per surface."""
    from zonal.blender import inspector as bi

    scene, surfaces = _cube(clean_scene)
    a, b = surfaces[0], surfaces[1]
    setattr(a, f"osm_{key}", "Alpha")
    assert key not in a and key not in b  # no per-surface custom property
    assert getattr(b, f"osm_{key}") == "Alpha"  # the neighbour in the same space shows the same value
    assert getattr(bi.get_item(scene, "Space", "Office"), bi.SPACE_ATTR_FIELD[key]) == "Alpha"
    setattr(b, f"osm_{key}", "")
    assert getattr(a, f"osm_{key}") == ""


def test_non_string_custom_property_shows_empty(clean_scene):
    _scene, surfaces = _cube(clean_scene)
    ob = surfaces[0]
    ob["constructionName"] = 5
    assert ob.osm_constructionName == ""


@pytest.mark.parametrize("key", ENUM_KEYS)
def test_enum_proxy_matches_custom_property(clean_scene, key):
    from zonal.blender.props import ENUM_FIELDS

    _scene, surfaces = _cube(clean_scene)
    ob = surfaces[0]
    ob.pop(key, None)
    assert getattr(ob, f"osm_{key}") == "DEFAULT"
    for choice in ENUM_FIELDS[key][1]:
        ob[key] = choice
        assert getattr(ob, f"osm_{key}") == choice, choice
        setattr(ob, f"osm_{key}", "DEFAULT")
        assert key not in ob
        setattr(ob, f"osm_{key}", choice)
        assert ob[key] == choice


def test_enum_unknown_value_displays_default_but_is_kept(clean_scene):
    """A value the dropdown cannot show (e.g. lower case from a file) displays as default yet stays stored."""
    _scene, surfaces = _cube(clean_scene)
    ob = surfaces[0]
    ob["surfaceType"] = "wall"
    assert ob.osm_surfaceType == "DEFAULT"
    assert ob["surfaceType"] == "wall"


def test_properties_editor_and_inspector_draw_same_values(clean_scene):
    from zonal.blender import inspector as bi
    from zonal.blender import props as bp

    bpy = clean_scene
    _scene, surfaces = _cube(bpy)
    wall = next(o for o in surfaces if o.get("surfaceType") == "Wall")
    wall.osm_thermalZoneName = "Z1"
    wall["constructionName"] = "Ext Wall"
    wall["outsideBoundaryCondition"] = "Surface"
    wall["outsideBoundaryConditionObjectName"] = surfaces[0].name

    editor = _Layout()
    bp.draw_object_fields(editor, _ctx(bpy), wall)
    inspector = _Layout()
    bi._draw_object_fields(inspector, _ctx(bpy), "Surface", wall)

    # zone HVAC proxies (osm_zone_*) are zone-backed, not custom props: not comparable here
    ed = {n: getattr(d, n) for d, n in editor.calls if d is wall and not n.startswith("osm_zone_")}
    ins = {n: getattr(d, n) for d, n in inspector.calls if d is wall}
    assert ins, "inspector drew nothing for the surface"
    for name, value in ins.items():
        assert name in ed, f"{name} is in the Inspector but not in the Properties editor"
        assert ed[name] == value, f"{name}: editor={ed[name]!r} inspector={value!r}"
    for name, value in {**ed, **ins}.items():
        base = name.removeprefix("osm_")
        if base in SPACE_KEYS:  # space values come from the space, the space name from the collection
            assert value == {"spaceName": "Office", "thermalZoneName": "Z1"}.get(base, ""), name
            continue
        raw = wall.get(base)
        expected = (raw if raw else "DEFAULT") if name.removeprefix("osm_") in ENUM_KEYS else (raw or "")
        assert value == expected, name


@pytest.mark.parametrize("tname", sorted(S.OBJECT_FIELDS))
def test_inspector_object_fields_all_have_proxies(clean_scene, tname):
    _scene, surfaces = _cube(clean_scene)
    for key, _label in S.OBJECT_FIELDS[tname]:
        assert hasattr(surfaces[0], f"osm_{key}"), key


def test_inspector_follows_surface_type_changed_in_editor(clean_scene):
    from zonal.blender import inspector as bi

    scene, surfaces = _cube(clean_scene)
    bi.sync_from_object(scene, surfaces[0])
    insp = scene.osm_inspector
    assert insp.type == "Surface" and insp.object == surfaces[0].name
    surfaces[0].osm_surfaceType = "FixedWindow"
    bi.sync_from_object(scene, surfaces[0])
    assert insp.type == "SubSurface" and insp.object == surfaces[0].name
    assert surfaces[0]["surfaceType"] in schema.SUBSURFACE_TYPES


def test_space_in_inspector_matches_editor(clean_scene):
    """The space of a surface is its collection, in the editor and in the Inspector."""
    from zonal.blender import inspector as bi

    _scene, surfaces = _cube(clean_scene, "Office")
    ob = surfaces[0]
    assert ob.osm_spaceName == "Office"
    assert "spaceName" not in ob
    assert bi._name_for_type(ob, "Space") == "Office"
    ob.osm_spaceName = "Lab"  # the editor also moves the object into the matching collection
    assert bi._name_for_type(ob, "Space") == "Lab"
    assert [c.name for c in ob.users_collection] == ["Lab"]
    assert ob.osm_spaceName == "Lab"


def test_space_follows_collection_move(clean_scene):
    """Moving an object to another collection changes its space everywhere (no stale property)."""
    from zonal.blender import inspector as bi

    bpy = clean_scene
    _scene, surfaces = _cube(bpy, "Office")
    ob = surfaces[0]
    ob.osm_spaceName = "Office"
    other = bpy.data.collections.new("Other")
    bpy.context.scene.collection.children.link(other)
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    other.objects.link(ob)
    assert ob.osm_spaceName == "Other"
    assert bi._name_for_type(ob, "Space") == "Other"


def test_store_items_and_object_names_stay_in_sync(clean_scene):
    from zonal.blender import inspector as bi
    from zonal.blender import props as bp

    scene, surfaces = _cube(clean_scene)
    ob = surfaces[0]
    ob.osm_thermalZoneName = "Z1"
    insp = scene.osm_inspector
    insp.type = "ThermalZone"
    bi.sync_from_object(scene, ob)
    assert insp.object == ob.osm_thermalZoneName == "Z1"
    assert bi.get_item(scene, "ThermalZone", "Z1") is not None
    bi.ensure_item(scene, "ThermalZone", "Z2")
    assert {"Z1", "Z2"} <= set(bp.category_names(scene, "zone"))
    assert {"Z1", "Z2"} <= set(bi.object_names(scene, "ThermalZone"))


def test_store_json_and_object_properties_agree(clean_scene):
    scene, surfaces = _cube(clean_scene)
    from zonal.blender import inspector as bi

    surfaces[0].osm_thermalZoneName = "Z1"
    bi.ensure_item(scene, "ThermalZone", "Z1").multiplier = 3
    data = json.loads(bi.export_json(scene))
    assert "Z1" in json.dumps(data)
    assert "thermalZoneName" not in surfaces[0]
    assert bi.get_item(scene, "Space", "Office").thermalZone == "Z1"


def test_migrate_spaces_moves_surface_values_to_the_space(clean_scene):
    bpy = clean_scene
    from zonal.blender import inspector as bi

    scene, surfaces = _cube(bpy, "Office")
    surfaces[0]["thermalZoneName"] = "Z1"
    surfaces[1]["thermalZoneName"] = "Z2"
    surfaces[1]["buildingStoryName"] = "Story 1"
    surfaces[2]["spaceName"] = "Lab"  # used to win over the collection
    assert bpy.ops.scene.osm_migrate_spaces() == {"FINISHED"}
    office = bi.get_item(scene, "Space", "Office")
    assert office.thermalZone == "Z1" and office.buildingStory == "Story 1"
    assert surfaces[2].users_collection[0].name == "Lab"
    assert not any(k in o for o in surfaces for k in ("thermalZoneName", "buildingStoryName", "spaceName"))
