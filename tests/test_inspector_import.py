"""Inspector fields of existing (template) constructions, materials and schedules must be readable and round-trip."""

from pathlib import Path

import pytest

pytest.importorskip("openstudio")
import openstudio

from zonal.core import inspector_import as II
from zonal.core import inspector_schema as S

TEMPLATE = Path(__file__).resolve().parent.parent / "src" / "zonal" / "templates" / "MinimalTemplate.osm"


@pytest.fixture(scope="module")
def models():
    ms = II.load_models([str(TEMPLATE), "", str(TEMPLATE.with_name("missing.osm"))])
    assert len(ms) == 1  # empty / missing paths are skipped
    return ms


def _model():
    return openstudio.model.Model()


def test_construction_layers_and_material_values():
    m = _model()
    mat = openstudio.model.StandardOpaqueMaterial(m)
    mat.setName("Brick")
    mat.setThickness(0.2)
    mat.setConductivity(0.8)
    mat.setRoughness("Rough")
    gyp = openstudio.model.MasslessOpaqueMaterial(m)
    gyp.setName("Gyp")
    gyp.setThermalResistance(0.3)
    c = openstudio.model.Construction(m)
    c.setName("Wall")
    assert c.setLayers(openstudio.model.MaterialVector([mat, gyp]))

    assert II.read_fields(m, "Construction", "Wall") == {"layer1": "Brick", "layer2": "Gyp"}
    brick = II.read_fields(m, "StandardOpaqueMaterial", "Brick")
    assert brick["thickness"] == pytest.approx(0.2) and brick["conductivity"] == pytest.approx(0.8)
    assert brick["roughness"] == "Rough" and brick["solarAbsorptance"] == pytest.approx(0.7)
    assert II.read_fields(m, "MasslessOpaqueMaterial", "Gyp")["thermalResistance"] == pytest.approx(0.3)


def test_schedule_constant_and_kind():
    m = _model()
    s = openstudio.model.ScheduleConstant(m)
    s.setName("Always 0.4")
    s.setValue(0.4)
    assert II.read_fields(m, "ScheduleConstant", "Always 0.4") == {"scheduleValue": pytest.approx(0.4)}
    assert II.schedule_kind(m, "Always 0.4") == "ScheduleConstant"
    assert II.schedule_kind(m, "nope") == ""


def test_missing_and_invalid_inputs():
    m = _model()
    assert II.read_fields(m, "Construction", "nope") is None
    assert II.read_fields(m, "Surface", "x") is None  # object backed
    assert II.read_fields(m, "Bogus", "x") is None
    assert II.read_fields(m, "Construction", "") is None


def test_sets_read_references_by_name():
    m = _model()
    ss = openstudio.model.DefaultScheduleSet(m)
    ss.setName("SS")
    sch = openstudio.model.ScheduleConstant(m)
    sch.setName("Lights")
    assert ss.setLightingSchedule(sch)
    cs = openstudio.model.DefaultConstructionSet(m)
    cs.setName("CS")
    assert II.read_fields(m, "DefaultScheduleSet", "SS") == {"lighting": "Lights"}
    assert II.read_fields(m, "DefaultConstructionSet", "CS") == {}


def test_template_objects_are_all_readable(models):
    """Every construction / material / schedule-set of the bundled template yields values, and they re-apply cleanly."""
    m = models[0]
    for c in m.getConstructions():
        v = II.read_fields(m, "Construction", c.nameString())
        assert v and v["layer1"] == c.layers()[0].nameString()
    for tname, getter in (
        ("StandardOpaqueMaterial", m.getStandardOpaqueMaterials),
        ("MasslessOpaqueMaterial", m.getMasslessOpaqueMaterials),
        ("SimpleGlazing", m.getSimpleGlazings),
        ("DefaultScheduleSet", m.getDefaultScheduleSets),
        ("DefaultConstructionSet", m.getDefaultConstructionSets),
    ):
        for o in getter():
            v = II.read_fields(m, tname, o.nameString())
            assert v is not None
            for fd in S.TYPES[tname].fields:
                if fd.setter and fd.kind == S.FLOAT:
                    assert fd.key in v, f"{tname}.{fd.key} not read for {o.nameString()}"


def test_read_from_models_first_wins(models):
    name = models[0].getConstructions()[0].nameString()
    assert II.read_from_models(models, "Construction", name)["layer1"]
    assert II.read_from_models(models, "Construction", "nope") is None
    assert II.read_from_models([], "Construction", name) is None
