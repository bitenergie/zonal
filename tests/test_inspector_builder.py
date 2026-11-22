import json
from pathlib import Path

import pytest

pytest.importorskip("openstudio")
import openstudio

from gltf_factory import box_quads, write_gltf
from zonal.core import inspector_schema as S
from zonal.core.osm_builder import BuildOptions, gltf_to_osm

TEMPLATE = str(
    Path(__file__).resolve().parent.parent / "src" / "zonal" / "templates" / "MinimalTemplate.osm"
)


def load(path):
    m = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(path)))
    assert m.is_initialized()
    return m.get()


def build(tmp_path, store_objects, opts=None, extras=None):
    q = box_quads(0, 0, 0, 10, 5, 3)
    objs = [
        (f"S {k}", [p], {"spaceName": "Office", "surfaceType": t, **(extras or {})})
        for (k, p), t in zip(q.items(), ["Floor", "RoofCeiling", "Wall", "Wall", "Wall", "Wall"])
    ]
    scene = {"osmModel": S.dumps_model(store_objects)}
    g = write_gltf(tmp_path / "m.gltf", objs, scene)
    rep = gltf_to_osm(g, tmp_path / "m.osm", opts or BuildOptions(y_up=False))
    return rep, load(tmp_path / "m.osm")


def test_store_resources_and_space_links(tmp_path):
    store = [
        {"type": "ThermalZone", "name": "Zone X", "values": {"multiplier": 3, "insideConvection": "TARP"}},
        {"type": "BuildingStory", "name": "Story A", "values": {"nominalZCoordinate": 2.5}},
        {"type": "SpaceType", "name": "Lab", "values": {"standardsBuildingType": "Office"}},
        {
            "type": "Space",
            "name": "Office",
            "values": {
                "thermalZone": "Zone X",
                "buildingStory": "Story A",
                "spaceType": "Lab",
                "partOfTotalFloorArea": False,
                "volume": 99.0,
                "volume_hard": True,
            },
        },
    ]
    rep, m = build(tmp_path, store)
    assert not rep.warnings, rep.warnings
    z = m.getThermalZoneByName("Zone X").get()
    assert z.multiplier() == 3 and z.zoneInsideConvectionAlgorithm().get() == "TARP"
    assert m.getBuildingStoryByName("Story A").get().nominalZCoordinate().get() == pytest.approx(2.5)
    assert m.getSpaceTypeByName("Lab").get().standardsBuildingType().get() == "Office"
    sp = m.getSpaceByName("Office").get()
    assert sp.thermalZone().get().nameString() == "Zone X"
    assert sp.buildingStory().get().nameString() == "Story A"
    assert sp.spaceType().get().nameString() == "Lab"
    assert sp.partofTotalFloorArea() is False
    assert sp.volume() == pytest.approx(99.0)


def test_store_wins_over_surface_extras(tmp_path):
    store = [{"type": "Space", "name": "Office", "values": {"thermalZone": "From Store"}}]
    _rep, m = build(tmp_path, store, extras={"thermalZoneName": "From Extras"})
    assert m.getSpaceByName("Office").get().thermalZone().get().nameString() == "From Store"


def test_space_origin_keeps_world_geometry(tmp_path):
    store = [
        {
            "type": "Space",
            "name": "Office",
            "values": {"xOrigin": 5.0, "yOrigin": -2.0, "zOrigin": 1.0, "relativeNorth": 30.0},
        }
    ]
    rep, m = build(tmp_path, store)
    assert not rep.warnings, rep.warnings
    sp = m.getSpaceByName("Office").get()
    assert sp.xOrigin() == pytest.approx(5.0) and sp.directionofRelativeNorth() == pytest.approx(30.0)
    t = sp.transformation()
    floor = next(s for s in m.getSurfaces() if s.surfaceType() == "Floor")
    world = [t * v for v in floor.vertices()]
    xs = sorted({round(p.x(), 3) for p in world})
    ys = sorted({round(p.y(), 3) for p in world})
    assert xs == [0.0, 10.0] and ys == [0.0, 5.0]
    assert all(abs(p.z()) < 1e-6 for p in world)


def test_default_set_references_use_template(tmp_path):
    store = [
        {
            "type": "DefaultConstructionSet",
            "name": "My CS",
            "values": {
                "exteriorSurfaces": "Exterior Surface Constructions",
                "interiorPartitionConstruction": "Interior Partition",
            },
        },
        {
            "type": "DefaultScheduleSet",
            "name": "My Schedules",
            "values": {
                "lighting": "Office Bldg Light",
                "infiltration": "Office Infil Quarter On",
            },
        },
        {
            "type": "Building",
            "name": "Building",
            "values": {
                "defaultConstructionSet": "My CS",
                "defaultScheduleSet": "My Schedules",
                "standardsTemplate": "90.1-2019",
            },
        },
    ]
    rep, m = build(tmp_path, store, BuildOptions(y_up=False, template_osm=TEMPLATE))
    assert not rep.warnings, rep.warnings
    cs = m.getDefaultConstructionSetByName("My CS").get()
    assert cs.defaultExteriorSurfaceConstructions().get().nameString() == "Exterior Surface Constructions"
    ss = m.getDefaultScheduleSetByName("My Schedules").get()
    assert ss.lightingSchedule().get().nameString() == "Office Bldg Light"
    b = m.getBuilding()
    assert b.defaultConstructionSet().get().nameString() == "My CS"
    assert b.standardsTemplate().get() == "90.1-2019"


def test_empty_weather_file_is_exported(tmp_path):
    for opts in (BuildOptions(y_up=False), BuildOptions(y_up=False, template_osm=TEMPLATE)):
        d = tmp_path / ("t" if opts.template_osm else "n")
        d.mkdir()
        _rep, m = build(d, [], opts)
        wf = m.getObjectsByType("OS:WeatherFile")
        assert len(wf) == 1  # exactly one, also when the template brings its own
        if not opts.template_osm:
            assert not m.getWeatherFile().url().is_initialized()  # empty


def test_weather_file_fields_override(tmp_path):
    store = [
        {
            "type": "WeatherFile",
            "name": "Weather File",
            "values": {"city": "Zurich", "latitude": 47.4, "longitude": 8.5, "timeZone": 1.0, "elevation": 432.0},
        }
    ]
    rep, m = build(tmp_path, store)
    assert not rep.warnings, rep.warnings
    assert len(m.getObjectsByType("OS:WeatherFile")) == 1
    wf = m.getWeatherFile()
    assert wf.city() == "Zurich" and wf.latitude() == pytest.approx(47.4) and wf.timeZone() == pytest.approx(1.0)


EPW = (
    next((p for p in Path("C:/Program Files/NREL").glob("*/weather/*.epw") if p.stem), None)
    if Path("C:/Program Files/NREL").exists()
    else None
)


@pytest.mark.skipif(EPW is None, reason="no .epw file available")
def test_weather_file_from_epw_then_override(tmp_path):
    store = [{"type": "WeatherFile", "name": "Weather File", "values": {"epwPath": str(EPW), "city": "Override"}}]
    rep, m = build(tmp_path, store)
    assert not rep.warnings, rep.warnings
    wf = m.getWeatherFile()
    assert wf.url().is_initialized() and wf.city() == "Override"
    assert wf.latitude() != 0.0  # taken from the epw header


def test_weather_file_missing_epw_warns(tmp_path):
    store = [{"type": "WeatherFile", "name": "Weather File", "values": {"epwPath": str(tmp_path / "nope.epw")}}]
    rep, m = build(tmp_path, store)
    assert any("nope.epw" in w for w in rep.warnings)
    assert len(m.getObjectsByType("OS:WeatherFile")) == 1


def test_missing_reference_warns(tmp_path):
    store = [{"type": "DefaultScheduleSet", "name": "SS", "values": {"lighting": "Nope"}}]
    rep, _m = build(tmp_path, store)
    assert any("Nope" in w for w in rep.warnings)


def test_unknown_store_json_is_ignored(tmp_path):
    q = box_quads(0, 0, 0, 1, 1, 1)
    g = write_gltf(tmp_path / "m.gltf", [("S", [q["floor"]], {})], {"osmModel": "garbage"})
    rep = gltf_to_osm(g, tmp_path / "m.osm", BuildOptions(y_up=False))
    assert rep.surfaces == 1 and json.loads("{}") == {}


def test_store_rendering_color(tmp_path):
    store = [
        {"type": "BuildingStory", "name": "Story A", "values": {"renderingColor": "#FF8000"}},
        {"type": "ThermalZone", "name": "Zone X", "values": {"renderingColor": "#0A1B2C"}},
        {"type": "Space", "name": "Office", "values": {"thermalZone": "Zone X", "buildingStory": "Story A"}},
    ]
    rep, m = build(tmp_path, store)
    story = m.getBuildingStoryByName("Story A").get().renderingColor().get()
    assert (story.renderingRedValue(), story.renderingGreenValue(), story.renderingBlueValue()) == (255, 128, 0)
    zone = m.getThermalZoneByName("Zone X").get().renderingColor().get()
    assert zone.renderingBlueValue() == 0x2C


def test_schedule_rules_become_ruleset(tmp_path):
    from zonal.core import schedule_rules as R

    rules = [
        R.Rule("Weekend off", 0, 24, 0.0, weekdays=(False,) * 5 + (True,) * 2),
        R.Rule("Summer office", 8, 18, 1.0, 0.1, months=tuple(m in (6, 7, 8) for m in range(1, 13))),
        R.Rule(
            "Xmas", 0, 24, 0.0, days=tuple(d == 25 for d in range(1, 32)), months=tuple(m == 12 for m in range(1, 13))
        ),
    ]
    store = [
        {"type": "ScheduleConstant", "name": "Occ", "values": {"rules": R.dumps(rules), "scheduleValue": 0.3}},
        {"type": "ScheduleConstant", "name": "Plain", "values": {"scheduleValue": 1.0}},
    ]
    rep, m = build(tmp_path, store)
    rs = m.getScheduleRulesetByName("Occ").get()
    assert [r.nameString() for r in rs.scheduleRules()] == ["Weekend off", "Summer office", "Xmas"]  # priority order
    assert [r.ruleIndex() for r in rs.scheduleRules()] == [0, 1, 2]
    assert list(rs.defaultDaySchedule().values()) == [0.3]
    summer = rs.scheduleRules()[1]
    assert list(summer.daySchedule().values()) == [0.1, 1.0, 0.1]
    assert str(summer.startDate().get()).endswith("Jun-01") and str(summer.endDate().get()).endswith("Aug-31")
    assert [str(d) for d in rs.scheduleRules()[2].specificDates()] == ["2009-Dec-25"]
    assert not m.getScheduleConstantByName("Occ").is_initialized()
    assert m.getScheduleConstantByName("Plain").get().value() == 1.0
    assert not [w for w in rep.warnings if "Occ" in w]


def test_schedule_rule_absolute_dates(tmp_path):
    from zonal.core import schedule_rules as R

    rule = R.Rule("Holidays", 0, 24, 0.0, months=(False,) * 12, dates=((12, 25), (1, 1)))
    store = [{"type": "ScheduleConstant", "name": "Occ", "values": {"rules": R.dumps([rule]), "scheduleValue": 1.0}}]
    rep, m = build(tmp_path, store)
    (sr,) = m.getScheduleRulesetByName("Occ").get().scheduleRules()
    assert sorted(str(d) for d in sr.specificDates()) == ["2009-Dec-25", "2009-Jan-01"]
    assert not [w for w in rep.warnings if "Occ" in w]


def test_schedule_rule_excluded_dates(tmp_path):
    from zonal.core import schedule_rules as R

    june = tuple(m == 6 for m in range(1, 13))
    rule = R.Rule("June minus two", 0, 24, 0.0, months=june, exclude=((6, 10), (6, 11)))
    store = [{"type": "ScheduleConstant", "name": "Occ", "values": {"rules": R.dumps([rule]), "scheduleValue": 1.0}}]
    rep, m = build(tmp_path, store)
    (sr,) = m.getScheduleRulesetByName("Occ").get().scheduleRules()
    dates = [str(d) for d in sr.specificDates()]
    assert len(dates) == 28 and "2009-Jun-10" not in dates and "2009-Jun-09" in dates
    assert not [w for w in rep.warnings if "Occ" in w]


def test_zone_ideal_air_loads_and_setpoint_schedules(tmp_path):
    store = [
        {"type": "ScheduleConstant", "name": "Heat SP", "values": {"scheduleValue": 21.0}},
        {"type": "ScheduleConstant", "name": "Cool SP", "values": {"scheduleValue": 24.0}},
        {"type": "ScheduleConstant", "name": "Hum SP", "values": {"scheduleValue": 30.0}},
        {"type": "ScheduleConstant", "name": "Dehum SP", "values": {"scheduleValue": 60.0}},
        {
            "type": "ThermalZone",
            "name": "Zone X",
            "values": {
                "idealAirLoads": True,
                "heatingSetpoint": "Heat SP",
                "coolingSetpoint": "Cool SP",
                "humidifyingSetpoint": "Hum SP",
                "dehumidifyingSetpoint": "Dehum SP",
            },
        },
        {"type": "Space", "name": "Office", "values": {"thermalZone": "Zone X"}},
    ]
    rep, m = build(tmp_path, store)
    assert not rep.warnings, rep.warnings
    z = m.getThermalZoneByName("Zone X").get()
    assert z.useIdealAirLoads()
    t = z.thermostatSetpointDualSetpoint().get()
    assert t.heatingSetpointTemperatureSchedule().get().nameString() == "Heat SP"
    assert t.coolingSetpointTemperatureSchedule().get().nameString() == "Cool SP"
    h = z.zoneControlHumidistat().get()
    assert h.humidifyingRelativeHumiditySetpointSchedule().get().nameString() == "Hum SP"
    assert h.dehumidifyingRelativeHumiditySetpointSchedule().get().nameString() == "Dehum SP"


def test_zone_missing_schedule_warns(tmp_path):
    store = [
        {"type": "ThermalZone", "name": "Zone X", "values": {"heatingSetpoint": "Nope"}},
        {"type": "Space", "name": "Office", "values": {"thermalZone": "Zone X"}},
    ]
    rep, m = build(tmp_path, store)
    assert any("Heating Setpoint Schedule 'Nope' not found" in w for w in rep.warnings), rep.warnings
    assert not m.getThermalZoneByName("Zone X").get().thermostatSetpointDualSetpoint().is_initialized()
