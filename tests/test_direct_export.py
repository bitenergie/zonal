"""The .osm / gbXML export reads the Blender meshes directly; glTF is a separate, standalone feature."""

import json

import openstudio
import pytest

from conftest import requires_bpy

pytestmark = requires_bpy


def _load(path):
    r = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(path)))
    assert r.is_initialized()
    return r.get()


def _scene(bpy):
    """A 4 m cube split into 6 single-face surfaces in collection 'Office', zone 'Zone A'."""
    scene = bpy.context.scene
    col = bpy.data.collections.new("Office")
    scene.collection.children.link(col)
    bpy.ops.mesh.primitive_cube_add(size=4, location=(0, 0, 2))
    cube = bpy.context.object
    for c in list(cube.users_collection):
        c.objects.unlink(cube)
    col.objects.link(cube)
    cube["thermalZoneName"] = "Zone A"
    bpy.ops.object.select_all(action="DESELECT")
    cube.select_set(True)
    bpy.ops.object.osm_separate_faces()
    return scene


def _export(bpy, path, op="openstudio_osm", **kw):
    return getattr(bpy.ops.export_scene, op)(filepath=str(path), **kw)


def _zone_of(m):
    out = {}
    for s in m.getSurfaces():
        sp = s.space().get()
        out[s.nameString()] = sp.thermalZone().get().nameString() if sp.thermalZone().is_initialized() else ""
    return out


def test_export_osm_is_direct_and_correct(clean_scene, tmp_path, monkeypatch):
    bpy = clean_scene
    from zonal.core import gltf_reader

    _scene(bpy)
    monkeypatch.setattr(gltf_reader, "read_gltf", lambda *a, **k: pytest.fail("glTF reader used"))
    monkeypatch.setattr(bpy.ops.export_scene, "gltf", lambda **k: pytest.fail("glTF exporter used"), raising=False)
    out = tmp_path / "d.osm"
    assert _export(bpy, out) == {"FINISHED"}
    m = _load(out)
    assert len(m.getSurfaces()) == 6
    assert sorted(s.surfaceType() for s in m.getSurfaces()) == ["Floor", "RoofCeiling", "Wall", "Wall", "Wall", "Wall"]
    assert [z.nameString() for z in m.getThermalZones()] == ["Zone A"]
    assert [sp.nameString() for sp in m.getSpaces()] == ["Office"]  # collection name = default space
    floor = sum(s.grossArea() for s in m.getSurfaces() if s.surfaceType() == "Floor")
    assert floor == pytest.approx(16.0)


def test_export_parity_with_gltf_path(clean_scene, tmp_path):
    """Direct export == Blender glTF export -> read_gltf -> build, for the same scene."""
    bpy = clean_scene
    from zonal.blender import ui
    from zonal.core.osm_builder import gltf_to_osm

    scene = _scene(bpy)
    scene["northAxis"] = 15.0
    direct = tmp_path / "direct.osm"
    assert _export(bpy, direct) == {"FINISHED"}

    overrides = ui.export_targets(bpy.context, False)
    ui.export_gltf_objects(bpy.context, str(tmp_path / "s.glb"), set(overrides))
    via = tmp_path / "via.osm"
    gltf_to_osm(tmp_path / "s.glb", via, ui.build_options(bpy.context, overrides, scale=1.0))

    def sig(m):
        return sorted(
            (
                s.nameString(),
                s.surfaceType(),
                round(s.grossArea(), 6),
                s.outsideBoundaryCondition(),
                tuple(round(c, 5) for p in s.vertices() for c in (p.x(), p.y(), p.z())),
            )
            for s in m.getSurfaces()
        )

    a, b = _load(direct), _load(via)
    assert sig(a) == sig(b)
    assert a.getBuilding().northAxis() == b.getBuilding().northAxis() == 15.0


def test_export_applies_world_matrix_and_unit_scale(clean_scene, tmp_path):
    bpy = clean_scene
    scene = _scene(bpy)
    for ob in [o for o in scene.objects if o.type == "MESH"]:
        ob.location.x += 10
    out = tmp_path / "t.osm"
    assert _export(bpy, out) == {"FINISHED"}
    xs = [p.x() for s in _load(out).getSurfaces() for p in s.vertices()]
    assert min(xs) == pytest.approx(8.0) and max(xs) == pytest.approx(12.0)
    scene.unit_settings.scale_length = 0.5
    half = tmp_path / "half.osm"
    assert _export(bpy, half) == {"FINISHED"}
    xs = [p.x() for s in _load(half).getSurfaces() for p in s.vertices()]
    assert min(xs) == pytest.approx(4.0) and max(xs) == pytest.approx(6.0)
    raw = tmp_path / "raw.osm"
    assert _export(bpy, raw, apply_unit_scale=False) == {"FINISHED"}
    xs = [p.x() for s in _load(raw).getSurfaces() for p in s.vertices()]
    assert max(xs) == pytest.approx(12.0)


def test_export_extras_priority_and_selection(clean_scene, tmp_path):
    """defaults (collection) < mesh custom props < object custom props; selection-only exports only selected."""
    bpy = clean_scene
    scene = _scene(bpy)
    meshes = sorted((o for o in scene.objects if o.type == "MESH"), key=lambda o: o.name)
    del meshes[0]["thermalZoneName"]  # separate_faces copied the object property to every face
    meshes[0].data["thermalZoneName"] = "FromMesh"
    from zonal.blender.props import assign_space

    assign_space(meshes[0], "S0")  # zones are per space: one space (collection) per face
    assign_space(meshes[1], "S1")
    meshes[1].data["thermalZoneName"] = "FromMesh"
    meshes[1]["thermalZoneName"] = "FromObject"
    out = tmp_path / "e.osm"
    assert _export(bpy, out) == {"FINISHED"}
    zones = _zone_of(_load(out))
    assert zones[meshes[0].name] == "FromMesh"
    assert zones[meshes[1].name] == "FromObject"

    for o in scene.objects:
        o.select_set(o is meshes[2])
    sel = tmp_path / "sel.osm"
    assert _export(bpy, sel, use_selection=True) == {"FINISHED"}
    assert len(_load(sel).getSurfaces()) == 1


def test_export_store_and_unwritable(clean_scene, tmp_path):
    bpy = clean_scene
    scene = _scene(bpy)
    item = scene.osm_model.add()
    item.type, item.name = "ThermalZone", "Zone A"
    item.multiplier = 3
    out = tmp_path / "s.osm"
    assert _export(bpy, out) == {"FINISHED"}
    assert _load(out).getThermalZoneByName("Zone A").get().multiplier() == 3
    assert "osmModel" not in scene  # the scene is left untouched
    with pytest.raises(RuntimeError):
        _export(bpy, tmp_path / "no" / "dir" / "x.osm")


def test_export_gbxml_direct(clean_scene, tmp_path):
    bpy = clean_scene
    _scene(bpy)
    out = tmp_path / "g.xml"
    assert _export(bpy, out, op="openstudio_gbxml", apply_unit_scale=False) == {"FINISHED"}
    assert "<gbXML" in out.read_text(encoding="utf-8", errors="replace")


def test_scene_extras_and_idprops(clean_scene):
    bpy = clean_scene
    from zonal.blender import scene as bsn

    scene = _scene(bpy)
    scene["northAxis"] = 5.0
    scene["modelObjectMetaData"] = {"a": {"b": 1}}
    scene["_private"] = 1
    scene["arr"] = [1, 2, 3]
    scene["osmModel"] = "stale"
    ob = next(o for o in scene.objects if o.type == "MESH")
    gs = bsn.collect_scene(bpy.context, {ob.name: {}}, store_json='{"x": 1}')
    ex = gs.scene_extras
    assert ex["northAxis"] == 5.0 and ex["modelObjectMetaData"] == {"a": {"b": 1}} and ex["arr"] == [1, 2, 3]
    assert "_private" not in ex and ex["osmModel"] == '{"x": 1}'
    assert len(gs.faces) == 1
    assert bsn.plain(object()) is None
    assert bsn.plain((1, 2)) == [1, 2] and bsn.plain({"k": (1,)}) == {"k": [1]}
    json.dumps(ex)
    assert "osmModel" not in bsn.collect_scene(bpy.context, {ob.name: {}}).scene_extras  # stale value never leaks


def test_standalone_gltf_export_and_import(clean_scene, tmp_path):
    bpy = clean_scene
    scene = _scene(bpy)
    scene.osm_template_path = ""
    glb = tmp_path / "s.glb"
    assert bpy.ops.export_scene.openstudio_gltf(filepath=str(glb)) == {"FINISHED"}
    assert glb.exists()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    with pytest.raises(RuntimeError, match="No mesh"):
        bpy.ops.export_scene.openstudio_gltf(filepath=str(tmp_path / "none.glb"))
    assert bpy.ops.import_scene.openstudio_gltf(filepath=str(glb)) == {"FINISHED"}
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    assert len(meshes) == 6
    spaces = [i for i in bpy.context.scene.osm_model if i.type == "Space"]
    assert spaces and all(i.thermalZone == "Zone A" for i in spaces)  # one value per space, on the space
    assert sorted(o["surfaceType"] for o in meshes if "surfaceType" in o) == ["Floor", "RoofCeiling"] + ["Wall"] * 4


def test_standalone_gltf_selection_and_errors(clean_scene, tmp_path):
    bpy = clean_scene
    scene = _scene(bpy)
    first = next(o for o in scene.objects if o.type == "MESH")
    for o in scene.objects:
        o.select_set(o is first)
    glb = tmp_path / "one.glb"
    assert bpy.ops.export_scene.openstudio_gltf(filepath=str(glb), use_selection=True) == {"FINISHED"}
    bad = tmp_path / "bad.glb"
    bad.write_bytes(b"nope")
    with pytest.raises(RuntimeError):
        bpy.ops.import_scene.openstudio_gltf(filepath=str(bad))


def test_gltf_items_kinds():
    from zonal.blender.ui import gltf_items
    from zonal.core.gltf_reader import Face, GltfScene

    tri = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    sc = GltfScene(
        [
            Face("a", tri, {"surfaceType": "FixedWindow", "spaceName": "S"}),
            Face("b", tri, {"surfaceType": "SiteShading"}),
            Face("c", tri, {"surfaceType": "InteriorPartitionSurface"}),
            Face("d", tri, {}),
        ],
        {"northAxis": 3},
        "x",
    )
    d = gltf_items(sc)
    assert [o["kind"] for o in d["objects"]] == ["subsurface", "shading", "partition", "surface"]
    assert d["objects"][0]["space"] == "S" and d["northAxis"] == 3.0


def _window(bpy):
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, -2.0, 2))
    win = bpy.context.object
    win.rotation_euler = (1.5707963, 0, 0)
    bpy.ops.object.transform_apply(rotation=True)
    win["surfaceType"] = "FixedWindow"
    win["spaceName"] = "Office"
    return win


def test_displace_viewing_aid_is_ignored_and_restored(clean_scene, tmp_path):
    """The sub surface Displace modifier moves the viewport mesh only: exports must use the real coordinates."""
    bpy = clean_scene
    from zonal.blender import ui

    _scene(bpy)
    win = _window(bpy)
    ui.sync_subsurface_displace(win)
    mod = win.modifiers[ui.DISPLACE_NAME]
    assert mod.show_viewport
    out = tmp_path / "w.osm"
    assert _export(bpy, out) == {"FINISHED"}
    assert mod.show_viewport  # restored
    sub = _load(out).getSubSurfaces()[0]
    assert {round(p.y(), 4) for p in sub.vertices()} == {-2.0}  # no 0.01 normal offset applied
    glb = tmp_path / "w.glb"
    assert bpy.ops.export_scene.openstudio_gltf(filepath=str(glb)) == {"FINISHED"}
    assert mod.show_viewport


def test_standalone_gltf_carries_inspector_store(clean_scene, tmp_path):
    bpy = clean_scene
    from zonal.core.gltf_reader import read_gltf

    scene = _scene(bpy)
    item = scene.osm_model.add()
    item.type, item.name = "ThermalZone", "Zone A"
    glb = tmp_path / "store.glb"
    assert bpy.ops.export_scene.openstudio_gltf(filepath=str(glb)) == {"FINISHED"}
    assert "osmModel" not in scene
    assert "Zone A" in read_gltf(glb).scene_extras["osmModel"]


def test_gltf_export_operator_reports_errors(clean_scene, tmp_path, monkeypatch):
    bpy = clean_scene
    from zonal.blender import ui

    _scene(bpy)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ui, "export_gltf_objects", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        bpy.ops.export_scene.openstudio_gltf(filepath=str(tmp_path / "x.glb"))


def test_gltf_menu_entries():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from zonal.blender import ui

    layout = MagicMock()
    for f in (ui.menu_func_gltf, ui.menu_func_import_gltf):
        f(SimpleNamespace(layout=layout), None)
    ids = [c.args[0] for c in layout.operator.call_args_list]
    assert ids == ["export_scene.openstudio_gltf", "import_scene.openstudio_gltf"]


def test_air_loops_are_set_on_the_zone(clean_scene, tmp_path):
    """Air loops belong to the thermal zone: the zone item's list wins over stale per-surface values."""
    bpy = clean_scene
    scene = _scene(bpy)
    meshes = [o for o in scene.objects if o.type == "MESH"]
    meshes[0]["airLoopHVACNames"] = {"0": "Stale Loop"}
    item = scene.osm_model.add()
    item.type, item.name = "ThermalZone", "Zone A"
    item.airLoops = "Loop 1"
    out = tmp_path / "a.osm"
    assert _export(bpy, out) == {"FINISHED"}
    m = _load(out)
    loops = sorted(a.nameString() for a in m.getAirLoopHVACs())
    assert loops == ["Loop 1"]  # the stale per-surface loop is ignored
    zone = m.getThermalZoneByName("Zone A").get()
    assert [a.nameString() for a in zone.airLoopHVACs()] == ["Loop 1"]
