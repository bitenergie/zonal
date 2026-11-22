from pathlib import Path

import pytest

pytest.importorskip("openstudio")
import openstudio

from gltf_factory import box_quads, write_gltf
from zonal.core.geometry import faces_from_triangles
from zonal.core.osm_builder import BuildOptions, gltf_to_osm


def load(osm_path):
    m = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(osm_path)))
    assert m.is_initialized()
    return m.get()


def test_plain_box_one_mesh_is_classified_per_face(tmp_path):
    g = write_gltf(tmp_path / "box.gltf", [("Box", list(box_quads(0, 0, 0, 10, 5, 3).values()), {})])
    rep = gltf_to_osm(g, tmp_path / "box.osm")
    m = load(tmp_path / "box.osm")
    assert rep.surfaces == 6 and not rep.warnings
    types = sorted(s.surfaceType() for s in m.getSurfaces())
    assert types == ["Floor", "RoofCeiling", "Wall", "Wall", "Wall", "Wall"]
    floor = next(s for s in m.getSurfaces() if s.surfaceType() == "Floor")
    assert floor.outsideBoundaryCondition() == "Ground"
    assert floor.grossArea() == pytest.approx(50.0)
    assert len(m.getSpaces()) == 1


def test_extras_roundtrip_full_schema(tmp_path):
    q = box_quads(0, 0, 0, 10, 5, 3)
    common = {
        "spaceName": "Office",
        "thermalZoneName": "Zone A",
        "spaceTypeName": "Open Office",
        "buildingStoryName": "Story 1",
        "constructionSetName": "CS",
        "airLoopHVACNames": {"0": "Loop 1"},
    }
    objs = [
        (
            "S South",
            [q["south"]],
            {
                **common,
                "surfaceType": "Wall",
                "outsideBoundaryCondition": "Outdoors",
                "sunExposure": "SunExposed",
                "windExposure": "WindExposed",
                "constructionName": "Ext Wall",
            },
        ),
        ("S Floor", [q["floor"]], {**common, "surfaceType": "Floor", "outsideBoundaryCondition": "Adiabatic"}),
        # a window lying on the south wall, parent given by name
        (
            "Win 1",
            [[(2, 0, 1), (4, 0, 1), (4, 0, 2), (2, 0, 2)]],
            {**common, "surfaceType": "FixedWindow", "surfaceName": "S South", "subSurfaceName": "Win 1"},
        ),
        # a door found by containment (no surfaceName)
        ("Door 1", [[(6, 0, 0.1), (7, 0, 0.1), (7, 0, 2.2), (6, 0, 2.2)]], {**common, "surfaceType": "Door"}),
        ("Overhang", [[(0, 0, 3), (10, 0, 3), (10, -1, 3), (0, -1, 3)]], {"surfaceType": "BuildingShading"}),
        ("Tree", [[(20, 0, 0), (21, 0, 0), (21, 0, 3), (20, 0, 3)]], {"surfaceType": "SiteShading"}),
    ]
    scene = {
        "northAxis": 15.0,
        "generator": "OpenStudio",
        "buildingStoryNames": {"0": "Story 1"},
        "modelObjectMetaData": {
            "0": {
                "iddObjectType": "OS:BuildingStory",
                "name": "Story 1",
                "handle": "x",
                "color": "#FF0000",
                "multiplier": 0,
                "nominal_z_coordinate": 0.0,
                "nominal_floorCeiling_Height": 3.0,
                "open_to_below": False,
            },
            "1": {
                "iddObjectType": "OS:ThermalZone",
                "name": "Zone A",
                "handle": "y",
                "color": "#00FF00",
                "multiplier": 4,
                "nominal_z_coordinate": 0,
                "nominal_floorCeiling_Height": 0,
                "open_to_below": False,
            },
        },
    }
    g = write_gltf(tmp_path / "m.gltf", objs, scene)
    rep = gltf_to_osm(g, tmp_path / "m.osm", BuildOptions(y_up=False))
    m = load(tmp_path / "m.osm")
    assert rep.sub_surfaces == 2 and rep.shading_surfaces == 2
    assert not [w for w in rep.warnings if "no construction" not in w], rep.warnings
    assert m.getBuilding().northAxis() == pytest.approx(15.0)
    south = m.getSurfaceByName("S South").get()
    assert south.outsideBoundaryCondition() == "Outdoors" and south.windExposure() == "WindExposed"
    assert south.construction().get().nameString() == "Ext Wall"
    assert sorted(ss.nameString() for ss in south.subSurfaces()) == ["Door 1", "Win 1"]
    assert m.getSubSurfaceByName("Win 1").get().subSurfaceType() == "FixedWindow"
    assert m.getSubSurfaceByName("Door 1").get().subSurfaceType() == "Door"
    sp = m.getSpaceByName("Office").get()
    assert sp.thermalZone().get().nameString() == "Zone A"
    assert sp.thermalZone().get().multiplier() == 4
    assert sp.spaceType().get().nameString() == "Open Office"
    story = sp.buildingStory().get()
    assert story.nominalFloortoCeilingHeight().get() == pytest.approx(3.0)
    assert story.renderingColor().get().renderingRedValue() == 255
    assert m.getAirLoopHVACByName("Loop 1").get().thermalZones()[0].nameString() == "Zone A"
    kinds = {s.shadingSurfaceGroup().get().shadingSurfaceType() for s in m.getShadingSurfaces()}
    assert kinds == {"Building", "Site"}


def test_adjacent_surfaces(tmp_path):
    a, b = box_quads(0, 0, 0, 5, 5, 3), box_quads(5, 0, 0, 10, 5, 3)
    objs = [
        (
            "A east",
            [a["east"]],
            {
                "spaceName": "A",
                "surfaceType": "Wall",
                "outsideBoundaryCondition": "Surface",
                "outsideBoundaryConditionObjectName": "B west",
            },
        ),
        (
            "B west",
            [b["west"]],
            {
                "spaceName": "B",
                "surfaceType": "Wall",
                "outsideBoundaryCondition": "Surface",
                "outsideBoundaryConditionObjectName": "A east",
            },
        ),
    ]
    g = write_gltf(tmp_path / "adj.gltf", objs)
    rep = gltf_to_osm(g, tmp_path / "adj.osm")
    m = load(tmp_path / "adj.osm")
    assert not rep.warnings, rep.warnings
    assert m.getSurfaceByName("A east").get().adjacentSurface().get().nameString() == "B west"


def test_polygon_recovery_from_triangles():
    # L-shaped polygon fan-triangulated + welded duplicate verts
    quad = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (1, 2, 0), (0, 2, 0)]
    pos, idx = [], []
    for i in range(1, 5):
        b = len(pos)
        pos += [quad[0], quad[i], quad[i + 1]]
        idx += [b, b + 1, b + 2]
    polys = faces_from_triangles(pos, idx)
    assert len(polys) == 1 and len(polys[0]) == 6


def test_template_seeds_resources(tmp_path):
    """Constructions / space types from a template .osm are reused instead of created as placeholders."""

    from gltf_factory import box_quads, write_gltf
    from zonal.core.osm_builder import BuildOptions, gltf_to_osm

    tpl = Path(__file__).resolve().parent.parent / "src" / "zonal" / "templates" / "MinimalTemplate.osm"
    src = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(tpl))).get()
    cname = src.getConstructions()[0].nameString()
    stname = src.getSpaceTypes()[0].nameString()
    gl = tmp_path / "a.gltf"
    write_gltf(
        gl,
        [
            (
                "Wall A",
                [box_quads(0, 0, 0, 1, 1, 1)["south"]],
                {"surfaceType": "Wall", "constructionName": cname, "spaceName": "S", "spaceTypeName": stname},
            )
        ],
    )
    out = tmp_path / "a.osm"
    gltf_to_osm(gl, out, BuildOptions(template_osm=str(tpl)))
    m = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(out))).get()
    assert len(m.getConstructions()) == len(src.getConstructions())  # reused, no "Name 1" duplicate
    assert len(m.getSpaceTypes()) == len(src.getSpaceTypes())  # all template space types are kept
    assert len(m.getSpaces()) == 1 and len(m.getThermalZones()) == 0 and len(m.getBuildingStorys()) == 0
    assert m.getSurfaces()[0].construction().get().nameString() == cname


def test_missing_constructions_are_filled_from_default_set(tmp_path):
    """Adiabatic surfaces have no slot in the template's construction set: they must still get a construction."""
    from gltf_factory import box_quads, write_gltf
    from zonal.core.gltf_reader import read_gltf
    from zonal.core.osm_builder import BuildOptions, build_model

    quads = list(box_quads(0, 0, 0, 10, 5, 3).values())
    g = write_gltf(
        tmp_path / "b.gltf",
        [("Box", quads, {"spaceName": "Room", "thermalZoneName": "Z", "outsideBoundaryCondition": "Adiabatic"})],
    )
    opts = BuildOptions(template_osm="src/zonal/templates/MinimalTemplate.osm")
    model, report = build_model(read_gltf(g), opts)
    assert all(s.construction().is_initialized() for s in model.getSurfaces())
    assert not any("no construction and no default" in w for w in report.warnings)
