"""EnergyPlus .idf export (glTF -> model -> idf) and import (idf -> dicts) through OpenStudio's translators."""

import pytest

pytest.importorskip("openstudio")

from gltf_factory import box_quads, write_gltf
from zonal.core.gltf_reader import read_gltf
from zonal.core.osm_builder import BuildOptions, scene_to_idf
from zonal.core.osm_reader import read_idf


def _box_gltf(tmp_path):
    extras = {"spaceName": "Room", "thermalZoneName": "Zone A"}
    return write_gltf(tmp_path / "box.gltf", [("Box", list(box_quads(0, 0, 0, 10, 5, 3).values()), extras)])


def test_export_then_import(tmp_path):
    out = tmp_path / "box.idf"
    opts = BuildOptions()
    scene_to_idf(read_gltf(_box_gltf(tmp_path), y_up=opts.y_up, scale=opts.scale, overrides=opts.overrides), out, opts)
    assert "BuildingSurface:Detailed" in out.read_text(encoding="utf-8", errors="replace")

    data = read_idf(out)
    surfaces = [o for o in data["objects"] if o["kind"] == "surface"]
    assert len(surfaces) == 6
    zs = [p[2] for o in surfaces for p in o["polygon"]]
    assert min(zs) == pytest.approx(0.0) and max(zs) == pytest.approx(3.0)


def test_import_bad_file(tmp_path):
    bad = tmp_path / "bad.idf"
    bad.write_text("this is not an idf", encoding="utf-8")
    with pytest.raises(RuntimeError):
        read_idf(bad)
