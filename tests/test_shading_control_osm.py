"""Shading controls in saved / exported .osm files (script edits, OpenStudio round trip, Blender import -> export)."""

import openstudio
import pytest

from conftest import requires_bpy
from zonal.core import scripts_core as sc

ALT = "Alter or Add Model Elements"
SHAPES = "Create Standard Building Shapes"


def _model_with_shading_control():
    m = openstudio.model.Model()
    sc.get(SHAPES, "Set Rectangular Floor Plan").execute(m, {"num_floors": 1}, sc.ScriptContext())
    for sp in m.getSpaces():  # EnergyPlus only translates spaces that belong to a thermal zone
        tz = openstudio.model.ThermalZone(m)
        sp.setThermalZone(tz)
    space = m.getSpaces()[0].nameString()
    ctx = sc.ScriptContext(selected_spaces={space})
    sc.get(ALT, "Set Window to Wall Ratio").execute(m, {}, ctx)
    assert sc.get(ALT, "Add Shading Controls").execute(m, {"material": "__blind__"}, sc.ScriptContext())
    name = m.getShadingControls()[0].nameString()
    assert sc.get(ALT, "Set Shading Controls").execute(m, {"name": name}, ctx)
    return m, name


def _load(path):
    r = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(path)))
    assert r.is_initialized(), "saved .osm could not be loaded"
    return r.get()


def _assigned(model):
    return [
        s
        for s in model.getSubSurfaces()
        if s.shadingControl().is_initialized() or (hasattr(s, "shadingControls") and s.shadingControls())
    ]


def test_shading_control_survives_save_and_reload(tmp_path):
    m, name = _model_with_shading_control()
    assert _assigned(m)
    out = tmp_path / "shade.osm"
    assert m.save(openstudio.path(str(out)), True)
    m2 = _load(out)
    assert [c.nameString() for c in m2.getShadingControls()] == [name]
    assert len(_assigned(m2)) == len(_assigned(m))
    assert all(c.shadingMaterial().get().to_Blind().is_initialized() for c in m2.getShadingControls())


def test_model_with_shading_control_is_valid_and_translates(tmp_path):
    m, _name = _model_with_shading_control()
    assert m.validityReport(openstudio.StrictnessLevel("Final")) is not None
    idf = openstudio.energyplus.ForwardTranslator().translateModel(m)
    assert idf.getObjectsByType(openstudio.IddObjectType("WindowShadingControl")), (
        "no WindowShadingControl in the translated IDF"
    )


def test_shading_control_without_assignment_is_harmless(tmp_path):
    m = openstudio.model.Model()
    sc.get(SHAPES, "Set Rectangular Floor Plan").execute(m, {"num_floors": 1}, sc.ScriptContext())
    sc.get(ALT, "Add Shading Controls").execute(m, {"material": "__shade__"}, sc.ScriptContext())
    out = tmp_path / "orphan.osm"
    assert m.save(openstudio.path(str(out)), True)
    assert len(_load(out).getShadingControls()) == 1


@requires_bpy
def test_blender_import_export_keeps_shading_control(clean_scene, tmp_path):
    """.osm with a shading control -> Blender import -> Blender export: the control and its windows must survive."""
    bpy = clean_scene
    m, _name = _model_with_shading_control()
    src = tmp_path / "in.osm"
    assert m.save(openstudio.path(str(src)), True)
    n_windows = len(m.getSubSurfaces())

    bpy.context.scene.osm_template_path = ""
    assert bpy.ops.import_scene.openstudio_osm(filepath=str(src)) == {"FINISHED"}
    out = tmp_path / "out.osm"
    assert bpy.ops.export_scene.openstudio_osm(filepath=str(out)) == {"FINISHED"}

    m2 = _load(out)
    assert len(m2.getSubSurfaces()) == n_windows
    assert len(m2.getShadingControls()) >= 1, "exported .osm lost its ShadingControl objects"
    assert _assigned(m2), "exported windows are no longer linked to a ShadingControl"


@requires_bpy
@pytest.mark.parametrize("fmt", ["osm"])
def test_blender_export_is_loadable_and_translates(clean_scene, tmp_path, fmt):
    """Whatever the export produces must load back and forward translate (catches dangling shading references)."""
    bpy = clean_scene
    m, _name = _model_with_shading_control()
    src = tmp_path / "in.osm"
    m.save(openstudio.path(str(src)), True)
    bpy.context.scene.osm_template_path = ""
    bpy.ops.import_scene.openstudio_osm(filepath=str(src))
    out = tmp_path / "out.osm"
    bpy.ops.export_scene.openstudio_osm(filepath=str(out))
    m2 = _load(out)
    for sub in m2.getSubSurfaces():
        ctl = sub.shadingControl()
        if ctl.is_initialized():
            assert ctl.get().model() == m2
    assert openstudio.energyplus.ForwardTranslator().translateModel(m2) is not None


def _roundtrip(bpy, m, tmp_path):
    src = tmp_path / "in.osm"
    assert m.save(openstudio.path(str(src)), True)
    bpy.context.scene.osm_template_path = ""
    assert bpy.ops.import_scene.openstudio_osm(filepath=str(src)) == {"FINISHED"}
    out = tmp_path / "out.osm"
    assert bpy.ops.export_scene.openstudio_osm(filepath=str(out)) == {"FINISHED"}
    return _load(out)


@requires_bpy
def test_roundtrip_keeps_control_fields_and_translates(clean_scene, tmp_path):
    bpy = clean_scene
    m, name = _model_with_shading_control()
    ctl = m.getShadingControls()[0]
    assert ctl.setShadingControlType("OnIfHighSolarOnWindow") and ctl.setSetpoint(250.0)
    assert ctl.setGlareControlIsActive(True)
    m2 = _roundtrip(bpy, m, tmp_path)
    c2 = m2.getShadingControls()[0]
    assert c2.nameString() == name and len(m2.getShadingControls()) == 1
    assert (c2.shadingType(), c2.shadingControlType()) == (ctl.shadingType(), "OnIfHighSolarOnWindow")
    assert c2.setpoint().get() == 250.0 and c2.glareControlIsActive()
    assert c2.shadingMaterial().get().to_Blind().is_initialized()
    assert len(c2.subSurfaces()) == len(ctl.subSurfaces()) == 4
    assert _assigned(m2) and all(s.shadingControl().get() == c2 for s in _assigned(m2))
    # the exported model works in EnergyPlus: one WindowShadingControl per zone, no dangling reference
    idf = openstudio.energyplus.ForwardTranslator().translateModel(m2)
    assert idf.getObjectsByType(openstudio.IddObjectType("WindowShadingControl"))


@requires_bpy
def test_roundtrip_construction_based_control(clean_scene, tmp_path):
    """Switchable glazing: the control points to a construction instead of a shading material."""
    bpy = clean_scene
    m, _name = _model_with_shading_control()
    m.getShadingControls()[0].remove()
    cons = openstudio.model.Construction(m)
    cons.setName("Switchable")
    cons.insertLayer(0, openstudio.model.StandardGlazing(m))
    ctl = openstudio.model.ShadingControl(cons)
    ctl.setName("Switch Ctl")
    for ss in m.getSubSurfaces():
        ss.setShadingControl(ctl)
    m2 = _roundtrip(bpy, m, tmp_path)
    c2 = m2.getShadingControls()[0]
    assert c2.nameString() == "Switch Ctl" and c2.shadingType() == "SwitchableGlazing"
    assert c2.construction().get().nameString() == "Switchable"
    assert len(c2.subSurfaces()) == 4


@requires_bpy
def test_unreferenced_or_undefined_controls(clean_scene, tmp_path):
    bpy = clean_scene
    m, _name = _model_with_shading_control()
    src = tmp_path / "in.osm"
    m.save(openstudio.path(str(src)), True)
    bpy.context.scene.osm_template_path = ""
    bpy.ops.import_scene.openstudio_osm(filepath=str(src))
    assert "shadingControls" in bpy.context.scene
    # a control with no window pointing to it is still created (Add Shading Controls makes such ones)
    bpy.context.scene["shadingControls"] = {
        "Unused": {"shadingType": "InteriorBlind", "material": {"kind": "Blind", "name": "B"}}
    }
    out = tmp_path / "o.osm"
    assert bpy.ops.export_scene.openstudio_osm(filepath=str(out)) == {"FINISHED"}
    m2 = _load(out)
    assert len(m2.getShadingControls()) == 1 and len(m2.getSubSurfaces()) == 4


def test_builder_control_edge_cases(tmp_path):
    """Builder level: bad definitions are reported, not fatal."""
    from zonal.core.gltf_reader import Face, GltfScene
    from zonal.core.osm_builder import BuildOptions, build_model

    wall = [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)]
    win = [(1, 0, 1), (2, 0, 1), (2, 0, 2), (1, 0, 2)]

    def build(defs, **ex):
        faces = [
            Face("W", wall, {"surfaceType": "Wall", "spaceName": "S"}),
            Face("Win", win, {"surfaceType": "FixedWindow", "spaceName": "S", **ex}),
        ]
        return build_model(GltfScene(faces, {"shadingControls": defs}, "t"), BuildOptions(y_up=False))

    model, rep = build({}, shadingControlName="Missing")
    assert not model.getShadingControls() and any("no definition" in w for w in rep.warnings)
    model, rep = build({"C": {"shadingType": "InteriorBlind"}}, shadingControlName="C")
    assert not model.getShadingControls() and any("neither material nor construction" in w for w in rep.warnings)
    model, rep = build(
        {"C": {"material": {"kind": "Shade", "name": "Sh"}, "shadingType": "NotAType", "setpoint": 100.0}},
        shadingControlName="C",
    )
    assert len(model.getShadingControls()) == 1 and any("rejected" in w for w in rep.warnings)
    assert model.getShadingControls()[0].shadingMaterial().get().nameString() == "Sh"
    # an existing material of that name is reused
    model, rep = build({"C": {"material": {"kind": "Shade", "name": "Sh"}}}, shadingControlName="C")
    assert len(model.getShadingMaterials()) == 1


def test_reader_multiple_controls_warning():
    from zonal.core import osm_reader

    m, _ = _model_with_shading_control()
    ss = m.getSubSurfaces()[0]
    second = openstudio.model.ShadingControl(openstudio.model.Shade(m))
    ss.addShadingControl(second)
    data = osm_reader.read_model(m)
    assert any("only the first" in w for w in data["warnings"])
    assert data["shadingControls"]


@requires_bpy
def test_script_edit_keeps_shading_controls_through_scene(clean_scene, tmp_path):
    """Scripts re-import the edited model into the scene; assigned controls must still export."""
    bpy = clean_scene
    from zonal.blender import scripts as bs

    m, _name = _model_with_shading_control()
    src = tmp_path / "in.osm"
    m.save(openstudio.path(str(src)), True)
    bpy.context.scene.osm_template_path = ""
    bpy.ops.import_scene.openstudio_osm(filepath=str(src))
    source = bs._scene_model(bpy.context)  # scene -> model (direct), as scripts do
    assert len(source.model.getShadingControls()) == 1
    assert len(source.model.getShadingControls()[0].subSurfaces()) == 4
