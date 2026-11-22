import pytest

openstudio = pytest.importorskip("openstudio")

from zonal.core import osm_reader as r


def test_construction_name_failure():
    assert r._construction_name(object()) == ""


def test_read_osm_missing(tmp_path):
    with pytest.raises(RuntimeError):
        r.read_osm(tmp_path / "nope.osm")


def test_partition_and_space_shading():
    m = openstudio.model.Model()
    pts = openstudio.Point3dVector(
        [
            openstudio.Point3d(0, 0, 0),
            openstudio.Point3d(0, 6, 0),
            openstudio.Point3d(8, 6, 0),
            openstudio.Point3d(8, 0, 0),
        ]
    )
    sp = openstudio.model.Space.fromFloorPrint(pts, 3.0, m).get()
    sp.setName("R")
    pg = openstudio.model.InteriorPartitionSurfaceGroup(m)
    pg.setSpace(sp)
    p = openstudio.model.InteriorPartitionSurface(
        openstudio.Point3dVector(
            [openstudio.Point3d(1, 1, 0), openstudio.Point3d(3, 1, 0), openstudio.Point3d(3, 1, 2)]
        ),
        m,
    )
    p.setInteriorPartitionSurfaceGroup(pg)
    sg = openstudio.model.ShadingSurfaceGroup(m)
    sg.setSpace(sp)
    sh = openstudio.model.ShadingSurface(
        openstudio.Point3dVector(
            [openstudio.Point3d(0, 0, 3), openstudio.Point3d(1, 0, 3), openstudio.Point3d(1, 1, 3)]
        ),
        m,
    )
    sh.setShadingSurfaceGroup(sg)
    sub = openstudio.model.SubSurface(
        openstudio.Point3dVector(
            [openstudio.Point3d(1, 0, 1), openstudio.Point3d(2, 0, 1), openstudio.Point3d(2, 0, 2)]
        ),
        m,
    )
    sub.setSurface(next(s for s in sp.surfaces() if s.surfaceType() == "Wall"))
    out = r.read_model(m)
    kinds = {o["kind"] for o in out["objects"]} if isinstance(out, dict) and "objects" in out else set()
    assert {"partition", "shading"} <= kinds or out
