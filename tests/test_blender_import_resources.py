"""tools_core helpers + osm_reader IDF->OSM for the 'Import Resources' feature (no Blender needed)."""

import pytest

from zonal.core.tools_core import (
    IMPORT_CATEGORIES,
    decode_extra_templates,
    encode_extra_templates,
)


def test_extra_template_roundtrip_and_garbage():
    entries = [("a.osm", ["Construction"]), ("b.osm", ["Schedule", "SpaceType"])]
    assert decode_extra_templates(encode_extra_templates(entries)) == entries
    for bad in ("", "nope", "[1]", '[{"path": 1}]'):
        assert decode_extra_templates(bad) == []


def test_import_categories_are_template_types():
    from zonal.core.osm_builder import OPTIONAL_TEMPLATE_TYPES, TEMPLATE_TYPES

    known = set(TEMPLATE_TYPES) | set(OPTIONAL_TEMPLATE_TYPES)
    assert all(set(t) <= known for _l, t in IMPORT_CATEGORIES.values())


def test_idf_to_osm_file(tmp_path):
    openstudio = pytest.importorskip("openstudio")
    from zonal.core.osm_reader import idf_to_osm_file

    idf = tmp_path / "m.idf"
    idf.write_text("Material,\n  Brick,\n  Rough,\n  0.1,\n  0.8,\n  1900,\n  800;\nConstruction,\n  Wall,\n  Brick;\n")
    out = tmp_path / "m.osm"
    idf_to_osm_file(idf, out)
    m = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(out))).get()
    assert any(c.nameString() == "Wall" for c in m.getConstructions())
    with pytest.raises(RuntimeError):
        idf_to_osm_file(tmp_path / "missing.idf", tmp_path / "x.osm")
