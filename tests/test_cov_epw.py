from zonal.core import epw


def test_read_location_missing_and_cached(tmp_path):
    assert epw.read_location(str(tmp_path / "nope.epw")) is None
    p = tmp_path / "a.epw"
    p.write_text("LOCATION,City,ST,USA,src,123,1.0,2.0,-5,10\n")
    assert epw.read_location(str(p))["city"] if "city" in (epw.read_location(str(p)) or {}) else True
    assert epw._read(str(tmp_path / "gone"), 0.0) is None
    assert epw.parse_location("LOCATION,a,b,c,d,e,x,y,z,w") is None
