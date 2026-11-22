import openstudio
import pytest

from zonal.core import scripts_core as sc
from zonal.user_scripts.templates import wizard as w

CAT = "On Demand Template Generators"
NAME = "Space Type and Construction Set Wizard"


def test_make_name_cz18():
    assert w.make_name("T", "CZ1-8", "Office", "Open", True) == "T - Office - Open"
    assert w.make_name("T", "ClimateZone 1-8", "Office", "Open", False) == "T - Office - Open"


def test_all_materials_and_errors():
    m = openstudio.model.Model()
    gen = w.ConstructionSetGenerator(openstudio, m)
    kinds = set()
    for name, data in w.standards()["materials"].items():
        kinds.add(data["material_type"])
        gen.material(name)
        assert gen.material(name)  # second call returns the existing one
    assert kinds
    with pytest.raises(RuntimeError, match="Cannot find material"):
        gen.material("nope")
    with pytest.raises(RuntimeError, match="Cannot find construction"):
        gen.construction("nope")
    s = w.standards()
    first = next(iter(s["materials"]))
    s["materials"]["__bad"] = {"material_type": "Weird"}
    try:
        with pytest.raises(RuntimeError, match="Unknown material type"):
            gen.material("__bad")
    finally:
        del s["materials"]["__bad"]
    assert first


def test_space_type_variants(monkeypatch):
    s = w.standards()
    m = openstudio.model.Model()
    gen = w.SpaceTypeGenerator(openstudio, m)
    picks = {}
    for tpl, czs in s["space_types"].items():
        for cz, bts in czs.items():
            for bt, types in bts.items():
                for st, d in types.items():
                    if w._nonzero(d.get("lighting_w_per_person")) and "pp" not in picks:
                        picks["pp"] = (tpl, cz, bt, st)
                    if w._nonzero(d.get("ventilation_ach")) and "ach" not in picks:
                        picks["ach"] = (tpl, cz, bt, st)
                    if w._nonzero(d.get("ventilation_per_person")) and "vpp" not in picks:
                        picks["vpp"] = (tpl, cz, bt, st)
                    if w._nonzero(d.get("gas_equip_per_area")) and "gas" not in picks:
                        picks["gas"] = (tpl, cz, bt, st)
    for key in picks.values():
        gen.generate(*key)
    # library errors
    gen2 = w.SpaceTypeGenerator(openstudio, openstudio.model.Model())
    with pytest.raises(RuntimeError, match="not found in master"):
        gen2.schedule("no such schedule")
    gen3 = w.SpaceTypeGenerator(openstudio, openstudio.model.Model())
    monkeypatch.setattr(w, "MASTER_SCHEDULES", "/does/not/exist.osm")
    with pytest.raises(RuntimeError, match="could not load"):
        _ = gen3.library


def test_wizard_options():
    script = sc.get(CAT, NAME)
    args = script.arguments(None)
    assert args
    m = openstudio.model.Model()
    c = sc.ScriptContext()
    assert script.execute(m, {"createConstructionSet": False, "setBuildingDefaults": False}, c)
    # a building type without construction sets -> warning
    w.standards()
    c = sc.ScriptContext()
    script.execute(openstudio.model.Model(), {"createSpaceTypes": False, "buildingType": "NoSuchType"}, c)
    assert any(x.level == "WARNING" for x in c.messages)
