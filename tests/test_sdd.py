"""SDD export / import and untranslated IDF export through OpenStudio's translators."""

import pytest

pytest.importorskip("openstudio")

from gltf_factory import box_quads, write_gltf
from zonal.core.gltf_reader import read_gltf
from zonal.core.osm_builder import BuildOptions, scene_to_idf_untranslated, scene_to_sdd
from zonal.core.osm_reader import read_sdd


def _scene(tmp_path):
    extras = {"spaceName": "Room", "thermalZoneName": "Zone A"}
    g = write_gltf(tmp_path / "box.gltf", [("Box", list(box_quads(0, 0, 0, 10, 5, 3).values()), extras)])
    return read_gltf(g, y_up=False)


def test_sdd_export_writes_sdd_xml(tmp_path):
    out = tmp_path / "box.xml"
    scene_to_sdd(_scene(tmp_path), out, BuildOptions())
    assert "<SDDXML" in out.read_text(encoding="utf-8")[:500]


def test_sdd_import_reports_the_translators_limitation(tmp_path):
    # OpenStudio's SDD reverse translator cannot read "Input" SDD files (which is what the forward translator writes)
    out = tmp_path / "box.xml"
    scene_to_sdd(_scene(tmp_path), out, BuildOptions())
    with pytest.raises(RuntimeError, match="not currently supported"):
        read_sdd(out)


def test_sdd_import_bad_file(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<notsdd/>", encoding="utf-8")
    with pytest.raises(RuntimeError):
        read_sdd(bad)


def test_untranslated_idf_keeps_os_objects(tmp_path):
    out = tmp_path / "u.idf"
    scene_to_idf_untranslated(_scene(tmp_path), out, BuildOptions())
    text = out.read_text(encoding="utf-8")
    assert "OS:Surface," in text and "OS:Space," in text
