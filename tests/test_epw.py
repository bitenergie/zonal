import os

from zonal.core import epw
from zonal.core import inspector_schema as S

LINE = "LOCATION,Denver Intl Ap,CO,USA,TMY3,725650,39.83,-104.65,-7.0,1650.0\n"


def test_parse_location():
    d = epw.parse_location(LINE)
    assert d == {
        "city": "Denver Intl Ap",
        "stateProvinceRegion": "CO",
        "country": "USA",
        "dataSource": "TMY3",
        "wmoNumber": "725650",
        "latitude": 39.83,
        "longitude": -104.65,
        "timeZone": -7.0,
        "elevation": 1650.0,
    }


def test_parse_rejects_other_lines():
    assert epw.parse_location("DESIGN CONDITIONS,1,x") is None
    assert epw.parse_location("LOCATION,a,b,c") is None
    assert epw.parse_location("LOCATION,a,b,c,d,e,north,0,0,0") is None


def test_keys_match_inspector_fields():
    assert set(epw.LOCATION_KEYS) == {f.key for f in S.TYPES["WeatherFile"].fields} - {"epwPath"}


def test_read_location_file(tmp_path):
    p = tmp_path / "x.epw"
    p.write_text(LINE + "DESIGN CONDITIONS,1\n", encoding="utf-8")
    assert epw.read_location(str(p))["city"] == "Denver Intl Ap"
    p.write_text(LINE.replace("Denver Intl Ap", "Zurich"), encoding="utf-8")
    os.utime(p, (1, 2_000_000_000))  # make sure the modification time differs
    assert epw.read_location(str(p))["city"] == "Zurich"  # cache follows the file
    assert epw.read_location(str(tmp_path / "missing.epw")) is None
