"""tests/data/DefaultExample.gltf (from OpenStudio) must rebuild the geometry of DefaultExample.osm."""

from pathlib import Path

import pytest

openstudio = pytest.importorskip("openstudio")

from zonal.core.osm_builder import gltf_to_osm

FILES = Path(__file__).resolve().parent / "data"
pytestmark = pytest.mark.skipif(not (FILES / "DefaultExample.gltf").exists(), reason="example files missing")


def load(p):
    return openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(p))).get()


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    out = tmp_path_factory.mktemp("ex") / "out.osm"
    rep = gltf_to_osm(FILES / "DefaultExample.gltf", out)
    return load(FILES / "DefaultExample.osm"), load(out), rep


def test_counts(models):
    src, back, rep = models
    print(rep.summary(), rep.warnings[:10])
    assert len(back.getSurfaces()) == len(src.getSurfaces())
    assert len(back.getSubSurfaces()) == len(src.getSubSurfaces())
    assert len(back.getSpaces()) == len(src.getSpaces())
    assert len(back.getThermalZones()) == len(src.getThermalZones())
    assert len(back.getBuildingStorys()) == len(src.getBuildingStorys())
    assert len(back.getShadingSurfaces()) == len(src.getShadingSurfaces())


def test_surface_attributes(models):
    src, back, _ = models
    for s in src.getSurfaces():
        b = back.getSurfaceByName(s.nameString())
        assert b.is_initialized(), s.nameString()
        b = b.get()
        assert b.surfaceType() == s.surfaceType()
        assert b.outsideBoundaryCondition() == s.outsideBoundaryCondition()
        assert b.grossArea() == pytest.approx(s.grossArea(), rel=1e-3)
        assert b.space().get().nameString() == s.space().get().nameString()
        assert (b.adjacentSurface().get().nameString() if b.adjacentSurface().is_initialized() else None) == (
            s.adjacentSurface().get().nameString() if s.adjacentSurface().is_initialized() else None
        )
