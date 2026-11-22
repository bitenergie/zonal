import pytest

from zonal.core import inspector_schema as S


def test_keys_are_unique_across_types():
    # module import already asserts this; also check every ref points at a known category
    from zonal.core.tools_core import INSPECTOR_CATEGORIES, LIBRARY_CATEGORIES

    cats = (
        set(LIBRARY_CATEGORIES) | set(INSPECTOR_CATEGORIES) | {"material"}
    )  # "material" = union of the material kinds
    for fd in S.ALL_FIELDS.values():
        if fd.kind == S.REF:
            assert fd.ref in cats, fd.key


def test_category_types_roundtrip():
    for t in S.TYPES.values():
        if t.category:
            assert S.type_for_category(t.category) == t.name


def test_screenshot_fields_present():
    keys = {t: [f.label for f in S.TYPES[t].fields] for t in S.TYPES}
    assert "Thermal Zone" in keys["Space"] and "Part of Total Floor Area" in keys["Space"]
    assert "Multiplier" in keys["ThermalZone"]
    assert len(S.TYPES["DefaultScheduleSet"].fields) == 10
    assert len(S.TYPES["DefaultSubSurfaceConstructions"].fields) == 8
    assert len(S.TYPES["DefaultConstructionSet"].fields) == 10


def test_defaults_are_not_exported():
    assert S.export_fields("ThermalZone", {"multiplier": 1}) == {}
    assert S.export_fields("ThermalZone", {"multiplier": 4}) == {"multiplier": 4}
    assert S.export_fields("Space", {"partOfTotalFloorArea": True}) == {}
    assert S.export_fields("Space", {"partOfTotalFloorArea": False}) == {"partOfTotalFloorArea": False}
    # autocalc: only exported when hard sized
    assert S.export_fields("Space", {"volume": 12.0}) == {}
    assert S.export_fields("Space", {"volume": 12.0, "volume_hard": True}) == {"volume": 12.0}


def test_dump_load():
    text = S.dumps_model(
        [
            {"type": "ThermalZone", "name": "Z", "values": {"multiplier": 3}},
            {"type": "Surface", "name": "ignored (object backed)", "values": {}},
        ]
    )
    objs = S.loads_model(text)
    assert objs == [{"type": "ThermalZone", "name": "Z", "fields": {"multiplier": 3}}]
    assert S.loads_model("not json") == [] and S.loads_model(None) == []


def test_setters_exist_in_openstudio():
    pytest.importorskip("openstudio")
    from openstudio import model as m

    cls_of = {
        "Building": m.Building,
        "WeatherFile": m.WeatherFile,
        "Space": m.Space,
        "ThermalZone": m.ThermalZone,
        "BuildingStory": m.BuildingStory,
        "BuildingUnit": m.BuildingUnit,
        "SpaceType": m.SpaceType,
        "DefaultConstructionSet": m.DefaultConstructionSet,
        "DefaultScheduleSet": m.DefaultScheduleSet,
        "DefaultSurfaceConstructions": m.DefaultSurfaceConstructions,
        "DefaultSubSurfaceConstructions": m.DefaultSubSurfaceConstructions,
        "Construction": m.Construction,
        "StandardOpaqueMaterial": m.StandardOpaqueMaterial,
        "MasslessOpaqueMaterial": m.MasslessOpaqueMaterial,
        "AirGap": m.AirGap,
        "SimpleGlazing": m.SimpleGlazing,
        "Shade": m.Shade,
        "ScheduleConstant": m.ScheduleConstant,
    }
    for t in S.TYPES.values():
        for fd in t.fields:
            if fd.setter:
                assert hasattr(cls_of[t.name], fd.setter), f"{t.name}.{fd.setter}"


def test_convection_choices_are_valid():
    pytest.importorskip("openstudio")
    from openstudio import model as m

    assert set(S.CONVECTION_INSIDE) <= set(m.ThermalZone.validZoneInsideConvectionAlgorithmValues())
    assert set(S.CONVECTION_OUTSIDE) <= set(m.ThermalZone.validZoneOutsideConvectionAlgorithmValues())


def test_color_hex_roundtrip():
    from zonal.core import inspector_schema as S

    assert S.rgb_to_hex((1, 0.5, 0)) == "#FF8000"
    assert S.hex_to_rgb("#FF8000") == (1.0, 128 / 255, 0.0)
    assert S.hex_to_rgb("zz") is None and S.hex_to_rgb("#12345") is None
    assert S.rgb_to_hex(S.hex_to_rgb("#0A1B2C")) == "#0A1B2C"


def test_color_field_only_exported_when_set():
    from zonal.core import inspector_schema as S

    assert "renderingColor" not in S.export_fields("BuildingStory", {"renderingColor": ""})
    assert S.export_fields("BuildingStory", {"renderingColor": "#FF0000"})["renderingColor"] == "#FF0000"
