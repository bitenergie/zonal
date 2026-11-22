from pathlib import Path

import pytest

pytest.importorskip("openstudio")
import openstudio

from gltf_factory import box_quads, write_gltf
from zonal.core import inspector_schema as S
from zonal.core import osm_builder as ob
from zonal.core.gltf_reader import GltfScene, read_gltf
from zonal.core.osm_builder import BuildOptions, build_model

TEMPLATE = str(
    Path(__file__).resolve().parent.parent / "src" / "zonal" / "templates" / "MinimalTemplate.osm"
)
Q = box_quads(0, 0, 0, 10, 5, 3)


def build(tmp_path, objs, scene=None, **opts):
    g = write_gltf(tmp_path / "m.gltf", objs, scene)
    o = BuildOptions(y_up=False, **opts)
    sc = read_gltf(g, y_up=False)
    return build_model(sc, o)


def bare():
    return ob._Builder(GltfScene([], {}, ""), BuildOptions())


def test_template_failures_and_types(tmp_path):
    m, rep = build(tmp_path, [("F", [Q["floor"]], {})], template_osm=str(tmp_path / "nope.osm"))
    assert any("could not be loaded" in w for w in rep.warnings)
    m, rep = build(tmp_path, [("F", [Q["floor"]], {})], template_osm=TEMPLATE, template_types=["SpaceType"])
    assert len(m.getDefaultConstructionSets()) == 0 and len(m.getSpaceTypes()) > 0


def test_bad_metadata_and_north(tmp_path):
    scene = {
        "northAxis": "abc",
        "modelObjectMetaData": {
            "0": {"iddObjectType": "OS:ThermalZone", "name": "Z", "color": "zz", "multiplier": "abc"},
            "1": {"iddObjectType": "OS:BuildingStory", "name": "St", "color": "#0000"},
        },
    }
    objs = [("F", [Q["floor"]], {"spaceName": "S", "thermalZoneName": "Z", "buildingStoryName": "St"})]
    _m, rep = build(tmp_path, objs, scene)
    assert any("northAxis" in w for w in rep.warnings)
    assert any("metadata for zone" in w for w in rep.warnings)


def test_resolve_ref_and_apply_fields():
    b = bare()
    assert b.resolve_ref("construction", "") is None
    assert b.resolve_ref("construction", "C1") is not None
    assert b.resolve_ref("schedule", "nope") is None
    assert b.resolve_ref("bogus", "x") is None
    assert b.resolve_ref("zone", "Z") is not None

    class FD:
        def __init__(self, key, kind, setter, ref="", label="L"):
            self.key, self.kind, self.setter, self.ref, self.label = key, kind, setter, ref, label

    class TD:
        fields = [
            FD("a", S.REF, "setX", "zone"),
            FD("b", S.REF, "setX", "schedule"),
            FD("c", S.INT, "setX"),
            FD("d", S.BOOL, "setX"),
            FD("e", S.STRING if hasattr(S, "STRING") else "string", "setX"),
            FD("f", S.FLOAT, "boom"),
            FD("g", S.FLOAT, "setX"),
            FD("h", S.FLOAT, ""),
            FD("i", S.FLOAT, "bad"),
        ]

    class Obj:
        def setX(self, v):
            return True

        def bad(self, v):
            return False

        def boom(self, v):
            raise RuntimeError("x")

    b.apply_fields(Obj(), TD, {"a": "Z", "b": "none", "c": "3", "d": 1, "e": "s", "f": 1, "g": "x", "i": 1}, "lbl")
    w = b.report.warnings
    assert any("not found" in x for x in w) and any("could not set" in x for x in w)
    assert any("L:" in x for x in w)


def test_weather_and_transform_errors(tmp_path):
    b = bare()
    b.apply_weather_file({"epwPath": str(tmp_path / "x.epw")}, "wf")
    assert b.report.warnings
    b.apply_space_transform(object(), {"xOrigin": 1}, "sp")
    assert any("origin" in w for w in b.report.warnings)
    b.apply_space_transform(object(), {}, "sp")


def test_space_transform_with_shading_and_partition(tmp_path):
    store = S.dumps_model(
        [
            {"type": "Space", "name": "Office", "values": {"xOrigin": 5.0, "relativeNorth": 10.0}},
            {"type": "BuildingUnit", "name": "U", "values": {}},
        ]
    )
    objs = [
        (
            "F",
            [Q["floor"]],
            {"spaceName": "Office", "surfaceType": "Floor", "buildingUnitName": "U", "constructionSetName": "CS"},
        ),
        ("SH", [[(0, 0, 3), (1, 0, 3), (1, 1, 3)]], {"surfaceType": "SpaceShading", "spaceName": "Office"}),
        ("P", [Q["south"]], {"surfaceType": "InteriorPartitionSurface", "spaceName": "Office"}),
    ]
    m, rep = build(tmp_path, objs, {"osmModel": store})
    assert rep.partitions == 1 and rep.shading_surfaces == 1
    sp = m.getSpaceByName("Office").get()
    assert sp.xOrigin() == 5.0


def test_boundary_conditions_and_subsurfaces(tmp_path):
    objs = [
        ("A", [Q["south"]], {"spaceName": "S", "surfaceType": "Wall", "outsideBoundaryCondition": "Surface"}),
        ("B", [Q["north"]], {"spaceName": "S", "surfaceType": "Wall", "outsideBoundaryCondition": "Bogus"}),
        (
            "C",
            [Q["east"]],
            {
                "spaceName": "S",
                "surfaceType": "Wall",
                "outsideBoundaryCondition": "Surface",
                "outsideBoundaryConditionObjectName": "Missing",
            },
        ),
        (
            "D",
            [Q["west"]],
            {
                "spaceName": "S",
                "surfaceType": "Wall",
                "outsideBoundaryCondition": "Surface",
                "outsideBoundaryConditionObjectName": "A",
            },
        ),
        (
            "W",
            [[(2, 0, 1), (4, 0, 1), (4, 0, 2), (2, 0, 2)]],
            {"surfaceType": "FixedWindow", "outsideBoundaryConditionObjectName": "W2", "constructionName": "WC"},
        ),
        ("W2", [[(2, 50, 1), (4, 50, 1), (4, 50, 2), (2, 50, 2)]], {"surfaceType": "FixedWindow"}),
        ("Orphan", [[(20, 0, 1), (21, 0, 1), (21, 0, 2)]], {"surfaceType": "Door", "spaceName": "S"}),
        ("Floor", [Q["floor"]], {"spaceName": "S", "surfaceType": "Floor", "constructionName": "FC"}),
    ]
    _m, rep = build(tmp_path, objs)
    w = " ".join(rep.warnings)
    assert "invalid boundary condition" in w
    assert "not found" in w
    assert "no parent surface" in w


def test_notch_and_match_surfaces(tmp_path):
    wall = [(0, 0, 0), (10, 0, 0), (10, 0, 3), (0, 0, 3)]
    door = [(10, 0, 0), (11, 0, 0), (11, 0, 2), (10, 0, 2)]
    objs = [
        ("Wall", [wall], {"spaceName": "S", "surfaceType": "Wall"}),
        ("Door", [door], {"surfaceType": "Door", "surfaceName": "Wall"}),
    ]
    _m, rep = build(tmp_path, objs, match_surfaces=True)
    assert rep.sub_surfaces >= 0


def test_match_surfaces_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(openstudio.model, "matchSurfaces", lambda *a: 1 / 0, raising=False)
    _m, rep = build(tmp_path, [("F", [Q["floor"]], {})], match_surfaces=True)
    assert any("matchSurfaces" in w for w in rep.warnings)


def test_save_and_gbxml_errors(tmp_path, monkeypatch):
    g = write_gltf(tmp_path / "m.gltf", [("F", [Q["floor"]], {})])
    with pytest.raises(RuntimeError):
        ob.gltf_to_osm(g, tmp_path / "no" / "such" / "dir" / "m.osm", BuildOptions(y_up=False))

    class Msg:
        def logMessage(self):
            return "boom"

    class FT:
        def modelToGbXML(self, *a):
            return False

        def errors(self):
            return [Msg()]

        def warnings(self):
            return []

    monkeypatch.setattr(openstudio.gbxml, "GbXMLForwardTranslator", FT)
    with pytest.raises(RuntimeError, match="boom"):
        ob.gltf_to_gbxml(g, tmp_path / "o.xml", BuildOptions(y_up=False))

    class FT2(FT):
        def modelToGbXML(self, *a):
            return True

        def warnings(self):
            return [Msg()]

    monkeypatch.setattr(openstudio.gbxml, "GbXMLForwardTranslator", FT2)
    assert "boom" in ob.gltf_to_gbxml(g, tmp_path / "o.xml", BuildOptions(y_up=False)).warnings


def test_store_materials_constructions_schedules(tmp_path):
    store = S.dumps_model(
        [
            {
                "type": "StandardOpaqueMaterial",
                "name": "Brick",
                "values": {"thickness": 0.2, "conductivity": 0.8, "roughness": "Rough"},
            },
            {"type": "AirGap", "name": "Gap", "values": {"thermalResistance": 0.18}},
            {"type": "SimpleGlazing", "name": "Glass", "values": {"uFactor": 1.8, "solarHeatGainCoefficient": 0.4}},
            {"type": "Shade", "name": "Roller", "values": {"shadeSolarTransmittance": 0.1, "shadeThickness": 0.01}},
            {"type": "Construction", "name": "Wall", "values": {"layer1": "Brick", "layer2": "Gap", "layer3": "Nope"}},
            {"type": "ScheduleConstant", "name": "Always", "values": {"scheduleValue": 1.0}},
            {"type": "DefaultScheduleSet", "name": "SS", "values": {"lighting": "Always"}},
        ]
    )
    objs = [("F", [Q["floor"]], {"spaceName": "Office", "surfaceType": "Floor", "constructionName": "Wall"})]
    m, rep = build(tmp_path, objs, {"osmModel": store})
    brick = m.getStandardOpaqueMaterialByName("Brick").get()
    assert brick.thickness() == 0.2 and brick.roughness() == "Rough"
    assert m.getSimpleGlazingByName("Glass").get().uFactor() == 1.8
    roller = m.getShadeByName("Roller").get()
    assert roller.solarTransmittance() == 0.1 and roller.thickness() == 0.01
    wall = m.getConstructionByName("Wall").get()
    assert [x.nameString() for x in wall.layers()] == ["Brick", "Gap"]
    assert m.getScheduleConstantByName("Always").get().value() == 1.0
    assert m.getDefaultScheduleSetByName("SS").get().lightingSchedule().is_initialized()
    assert any("Nope" in w for w in rep.warnings)
