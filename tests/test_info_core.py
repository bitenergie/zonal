from zonal.core.info_core import describe


def test_describe_full_wall():
    props = {
        "spaceName": "Office",
        "thermalZoneName": "Zone 1",
        "constructionName": "Brick",
        "outsideBoundaryCondition": "Surface",
        "outsideBoundaryConditionObjectName": "Wall 7",
        "sunExposure": "NoSun",
        "windExposure": 3,  # wrong type is ignored
    }
    lines = describe("Wall 1", "Wall", "Surface", props, 12.5, (0.0, -1.0, 0.0), 0.0)
    assert lines[0] == "Surface: Wall 1" and lines[1] == "Type: Wall"
    assert "Space: Office" in lines and "Thermal Zone: Zone 1" in lines and "Construction: Brick" in lines
    assert "Boundary: Surface (Wall 7)" in lines and "Sun: NoSun" in lines
    assert not any(line.startswith("Wind") for line in lines)
    assert "Area: 12.50 m²" in lines
    assert lines[-1] == "Tilt: 90.0°, Azimuth: 180.0°"


def test_horizontal_has_no_azimuth_and_space_fallback():
    lines = describe("F", "Floor", "Surface", {}, 4.0, (0.0, 0.0, -1.0), 0.0, space_fallback="Hall")
    assert "Space: Hall" in lines and lines[-1] == "Tilt: 180.0°"
