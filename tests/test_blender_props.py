"""In-process tests of blender_props (osm_* proxies, library, template operators, panels)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from conftest import requires_bpy

pytestmark = requires_bpy


def _mesh(bpy, name="Obj", collection=None):
    bpy.ops.mesh.primitive_plane_add()
    ob = bpy.context.object
    ob.name = name
    if collection is not None:
        for c in list(ob.users_collection):
            c.objects.unlink(ob)
        collection.objects.link(ob)
    return ob


def test_pure_helpers(clean_scene):
    bpy = clean_scene
    from zonal.blender import props as bp

    assert bp.prop_name("x") == "osm_x"
    scene = bpy.context.scene
    assert bp.library_add(scene, "zone", "Z1") is True
    assert bp.library_add(scene, "zone", "Z1") is False
    assert bp.library_add(scene, "zone", "") is False
    assert bp.library_names(scene, "zone") == ["Z1"]
    assert bp.used_names(scene, "nonsense") == set()
    ob = _mesh(bpy)
    key = bp.LIBRARY_CATEGORIES["zone"][0]
    ob[key] = "Z2"
    assert bp.used_names(scene, "zone") == {"Z2"}
    assert {"Z1", "Z2"} <= set(bp.category_names(scene, "zone"))
    ctx = SimpleNamespace(scene=scene)
    assert "Obj" in bp._mesh_object_names(None, ctx, "")
    assert bp._collection_names(None, ctx, "") == [c.name for c in bpy.data.collections]
    assert "Z1" in bp._library_search("zone")(None, ctx, "")


def test_assign_space(clean_scene):
    bpy = clean_scene
    from zonal.blender import props as bp

    ob = _mesh(bpy)
    bp.assign_space(ob, "Room")
    assert [c.name for c in ob.users_collection] == ["Room"]
    bp.assign_space(ob, "Room")  # existing collection, already linked
    other = _mesh(bpy, "Other")
    bp.assign_space(other, "Room")
    assert len(bpy.data.collections["Room"].objects) == 2


def test_string_proxy(clean_scene):
    bpy = clean_scene
    ob = _mesh(bpy)
    ob.osm_thermalZoneName = "Zone X"
    assert ob["thermalZoneName"] == "Zone X" and ob.osm_thermalZoneName == "Zone X"
    ob.osm_thermalZoneName = ""
    assert "thermalZoneName" not in ob
    ob.osm_thermalZoneName = ""  # already absent
    ob.osm_spaceName = "Sp"
    assert ob.users_collection[0].name == "Sp"
    ob.osm_surfaceName = "P"
    assert ob["surfaceName"] == "P"
    empty = bpy.data.objects.new("E", None)
    bpy.context.scene.collection.objects.link(empty)
    empty.osm_spaceName = "NoAssign"  # non mesh: no collection move
    assert empty["spaceName"] == "NoAssign"
    ob["thermalZoneName"] = 5  # non str -> ""
    assert ob.osm_thermalZoneName == ""


def test_enum_proxy(clean_scene):
    bpy = clean_scene
    ob = _mesh(bpy)
    assert ob.osm_sunExposure == "DEFAULT"
    ob.osm_sunExposure = "NoSun"
    assert ob["sunExposure"] == "NoSun" and ob.osm_sunExposure == "NoSun"
    ob.osm_sunExposure = "DEFAULT"
    assert "sunExposure" not in ob
    ob.osm_sunExposure = "DEFAULT"
    ob.osm_surfaceType = "Wall"
    assert ob["surfaceType"] == "Wall"
    ob.osm_surfaceType = "DEFAULT"
    assert "surfaceType" not in ob
    ob["outsideBoundaryCondition"] = "bogus"
    assert ob.osm_outsideBoundaryCondition == "DEFAULT"


def test_lib_add_operator(clean_scene):
    bpy = clean_scene
    from zonal.blender import props as bp

    scene = bpy.context.scene
    ob = _mesh(bpy)
    ob.select_set(True)
    assert bpy.ops.object.osm_lib_add("EXEC_DEFAULT", category="zone", name="ZoneN") == {"FINISHED"}
    assert ob["thermalZoneName"] == "ZoneN" and "ZoneN" in bp.library_names(scene, "zone")
    assert bpy.ops.object.osm_lib_add("EXEC_DEFAULT", category="zone", name="") == {"CANCELLED"}
    assert bpy.ops.object.osm_lib_add("EXEC_DEFAULT", category=bp.SPACE, name="Sp9") == {"FINISHED"}
    assert ob.users_collection[0].name == "Sp9" and "spaceName" not in ob
    # a space with no mesh target still creates its collection
    ob.select_set(False)
    bpy.data.objects.remove(ob)
    assert bpy.ops.object.osm_lib_add("EXEC_DEFAULT", category=bp.SPACE, name="Sp10") == {"FINISHED"}
    assert bpy.data.collections.get("Sp10") is not None

    wm = MagicMock()
    ctx = SimpleNamespace(scene=scene, window_manager=wm)
    op = SimpleNamespace(category=bp.SPACE, name="")
    bp.OBJECT_OT_osm_lib_add.invoke(op, ctx, None)
    assert op.name.startswith("Space")
    op = SimpleNamespace(category="zone", name="")
    bp.OBJECT_OT_osm_lib_add.invoke(op, ctx, None)
    assert op.name
    assert wm.invoke_props_dialog.call_count == 2


def test_clear_remove_targets(clean_scene):
    bpy = clean_scene
    from zonal.blender import props as bp

    scene = bpy.context.scene
    ob = _mesh(bpy)
    ob["thermalZoneName"] = "A"
    assert bp._targets(bpy.context) == [ob]  # selected mesh
    assert bpy.ops.object.osm_lib_clear(key="thermalZoneName") == {"FINISHED"}
    assert "thermalZoneName" not in ob
    assert bpy.ops.object.osm_lib_clear(key="thermalZoneName") == {"FINISHED"}
    bp.library_add(scene, "zone", "A")
    assert bpy.ops.scene.osm_lib_remove(category="zone", name="nope") == {"FINISHED"}
    assert bp.library_names(scene, "zone") == ["A"]
    assert bpy.ops.scene.osm_lib_remove(category="zone", name="A") == {"FINISHED"}
    assert bp.library_names(scene, "zone") == []
    # active (not selected) object fallback
    ob.select_set(False)
    assert bp._targets(SimpleNamespace(selected_objects=[], object=ob)) == [ob]
    assert bp._targets(SimpleNamespace(selected_objects=[], object=None)) == []


def test_templates(clean_scene, tmp_path):
    bpy = clean_scene
    from zonal.blender import props as bp

    scene = bpy.context.scene
    assert bpy.ops.scene.osm_load_template(use_default=True) == {"FINISHED"}
    assert scene.osm_template_path == str(bp.DEFAULT_TEMPLATE)
    assert bpy.ops.scene.osm_clear_template() == {"FINISHED"}
    assert scene.osm_template_path == ""
    with pytest.raises(RuntimeError):  # ERROR report becomes an exception through bpy.ops
        bpy.ops.scene.osm_load_template(filepath=str(tmp_path / "missing.osm"))
    f = tmp_path / "t.osm"
    f.write_text("OS:SpaceType,\n  {x},\n  Tmpl Type;\n", encoding="utf-8")
    assert bpy.ops.scene.osm_load_template(filepath=str(f)) == {"FINISHED"}

    wm = MagicMock()
    ctx = SimpleNamespace(scene=scene, window_manager=wm)
    cls = bp.SCENE_OT_osm_load_template
    assert cls.invoke(SimpleNamespace(use_default=False), ctx, None) == {"RUNNING_MODAL"}
    wm.fileselect_add.assert_called_once()
    me = SimpleNamespace(use_default=True, execute=lambda c: {"FINISHED"})
    assert cls.invoke(me, ctx, None) == {"FINISHED"}


def test_panels(clean_scene):
    bpy = clean_scene
    from zonal.blender import props as bp

    scene = bpy.context.scene
    ob = _mesh(bpy)
    ctx = SimpleNamespace(scene=scene, object=ob)
    assert bp.VIEW3D_PT_osm_properties.poll(ctx)
    assert not bp.VIEW3D_PT_osm_properties.poll(SimpleNamespace(object=None))
    empty = bpy.data.objects.new("E", None)
    assert not bp.VIEW3D_PT_osm_properties.poll(SimpleNamespace(object=empty))

    ob["surfaceType"] = "FixedWindow"
    ob["outsideBoundaryCondition"] = "Surface"
    bp.VIEW3D_PT_osm_properties.draw(SimpleNamespace(layout=MagicMock()), ctx)
    ob["spaceName"] = "S"
    ob["surfaceType"] = "Wall"
    ob["outsideBoundaryCondition"] = "Outdoors"
    lay = MagicMock()
    lay.panel.return_value = (MagicMock(), MagicMock())
    bp.draw_object_fields(lay, ctx, ob)
    bp.assign_space(ob, "Col")
    del ob["spaceName"]
    bp.draw_object_fields(lay, ctx, ob)

    for i in range(35):
        bp.library_add(scene, "zone", f"Z{i}")
    lay = MagicMock()
    bp.VIEW3D_PT_osm_library.draw(SimpleNamespace(layout=lay), ctx)
    scene.osm_template_path = "C:/x/t.osm"
    bp.VIEW3D_PT_osm_library.draw(SimpleNamespace(layout=lay), ctx)
