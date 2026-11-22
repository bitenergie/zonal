"""OSM -> (OpenStudio's own glTF translator) -> our reader/builder -> OSM."""

import pytest

openstudio = pytest.importorskip("openstudio")
if not hasattr(openstudio, "gltf"):
    pytest.skip("openstudio.gltf unavailable", allow_module_level=True)

from zonal.core.osm_builder import BuildOptions, gltf_to_osm


def make_model():
    m = openstudio.model.Model()
    z = openstudio.model.ThermalZone(m)
    z.setName("Zone A")
    story = openstudio.model.BuildingStory(m)
    story.setName("Story 1")
    st = openstudio.model.SpaceType(m)
    st.setName("Office")
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
    sp.setBuildingStory(story)
    sp.setSpaceType(st)
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
    return m


def test_openstudio_gltf_roundtrip(tmp_path):
    src = make_model()
    gltf = tmp_path / "os.gltf"
    assert openstudio.gltf.GltfForwardTranslator().modelToGLTF(src, openstudio.path(str(gltf)))
    out = tmp_path / "back.osm"
    rep = gltf_to_osm(gltf, out, BuildOptions())
    back = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(out))).get()
    assert not rep.warnings, rep.warnings
    assert len(back.getSurfaces()) == 6 and len(back.getSubSurfaces()) == 1
    assert sorted(s.surfaceType() for s in back.getSurfaces()) == sorted(s.surfaceType() for s in src.getSurfaces())
    for s in src.getSurfaces():
        b = back.getSurfaceByName(s.nameString()).get()
        assert b.grossArea() == pytest.approx(s.grossArea(), rel=1e-4)
        assert b.outsideBoundaryCondition() == s.outsideBoundaryCondition()
        assert b.outwardNormal().z() == pytest.approx(s.outwardNormal().z(), abs=1e-6)  # winding preserved
    sp = back.getSpaceByName("Room").get()
    assert sp.thermalZone().get().nameString() == "Zone A"
    assert sp.spaceType().get().nameString() == "Office"
    assert sp.buildingStory().get().nameString() == "Story 1"
    assert back.getSubSurfaceByName("Window X").get().subSurfaceType() == "FixedWindow"
