"""read_osm: OSM -> dicts, and the full OSM -> glTF-less roundtrip through the builder."""

import pytest

openstudio = pytest.importorskip("openstudio")

from zonal.core.osm_reader import read_osm


def make_model():
    m = openstudio.model.Model()
    z = openstudio.model.ThermalZone(m)
    z.setName("Zone A")
    sp = openstudio.model.Space.fromFloorPrint(
        openstudio.Point3dVector(
            [
                openstudio.Point3d(0, 0, 0),
                openstudio.Point3d(0, 6, 0),
                openstudio.Point3d(8, 6, 0),
                openstudio.Point3d(8, 0, 0),
            ]
        ),
        3.0,
        m,
    ).get()
    sp.setName("Room")
    sp.setThermalZone(z)
    sp.setXOrigin(10.0)
    south = next(s for s in sp.surfaces() if s.surfaceType() == "Wall" and abs(s.outwardNormal().y() + 1) < 1e-6)
    win = openstudio.model.SubSurface(
        openstudio.Point3dVector(
            [
                openstudio.Point3d(2, 0, 1),
                openstudio.Point3d(4, 0, 1),
                openstudio.Point3d(4, 0, 2),
                openstudio.Point3d(2, 0, 2),
            ]
        ),
        m,
    )
    win.setSurface(south)
    win.setName("Window X")
    group = openstudio.model.ShadingSurfaceGroup(m)
    group.setShadingSurfaceType("Site")
    sh = openstudio.model.ShadingSurface(
        openstudio.Point3dVector(
            [openstudio.Point3d(0, 20, 0), openstudio.Point3d(5, 20, 0), openstudio.Point3d(5, 20, 4)]
        ),
        m,
    )
    sh.setName("Tree")
    sh.setShadingSurfaceGroup(group)
    return m


def test_read_osm(tmp_path):
    path = tmp_path / "m.osm"
    make_model().save(openstudio.path(str(path)), True)
    data = read_osm(path)
    by = {o["name"]: o for o in data["objects"]}
    assert sorted(o["kind"] for o in data["objects"]).count("surface") == 6
    assert by["Window X"]["kind"] == "subsurface"
    assert by["Window X"]["props"]["subSurfaceName"] == "Window X"
    assert by["Window X"]["props"]["surfaceType"] == "FixedWindow"
    assert by["Window X"]["props"]["spaceName"] == "Room"
    assert by["Window X"]["props"]["thermalZoneName"] == "Zone A"
    assert by["Tree"]["kind"] == "shading" and by["Tree"]["props"]["surfaceType"] == "SiteShading"
    # the space origin (x = 10) is applied: the window spans x = 12..14
    xs = sorted(p[0] for p in by["Window X"]["polygon"])
    assert xs[0] == pytest.approx(12.0) and xs[-1] == pytest.approx(14.0)
    parent = by["Window X"]["props"]["surfaceName"]
    assert by[parent]["kind"] == "surface"
    assert by[parent]["props"]["outsideBoundaryCondition"] == "Outdoors"
    assert min(p[0] for o in data["objects"] if o["kind"] == "surface" for p in o["polygon"]) == pytest.approx(10.0)
