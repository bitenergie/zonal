"""gbXML export (glTF -> model -> gbXML) and import (gbXML -> dicts) through OpenStudio's translators."""

import pytest

pytest.importorskip("openstudio")

from gltf_factory import box_quads, write_gltf
from zonal.core.osm_builder import gltf_to_gbxml
from zonal.core.osm_reader import read_gbxml


def _box_gltf(tmp_path):
    extras = {"spaceName": "Room", "thermalZoneName": "Zone A"}
    return write_gltf(tmp_path / "box.gltf", [("Box", list(box_quads(0, 0, 0, 10, 5, 3).values()), extras)])


def test_export_then_import(tmp_path):
    out = tmp_path / "box.xml"
    gltf_to_gbxml(_box_gltf(tmp_path), out)
    text = out.read_text(encoding="utf-8")
    assert "<gbXML" in text and "<Campus" in text

    data = read_gbxml(out)
    surfaces = [o for o in data["objects"] if o["kind"] == "surface"]
    assert len(surfaces) == 6
    assert {o["props"]["surfaceType"] for o in surfaces} == {"Floor", "Wall", "RoofCeiling"}
    assert all(o["space"] for o in surfaces)
    zs = [p[2] for o in surfaces for p in o["polygon"]]
    assert min(zs) == pytest.approx(0.0) and max(zs) == pytest.approx(3.0)


def test_import_bad_file(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<notgbxml/>", encoding="utf-8")
    with pytest.raises(RuntimeError):
        read_gbxml(bad)
