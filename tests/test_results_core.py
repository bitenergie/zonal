import os

import pytest

from zonal.core import results_core as rc

SAMPLE = os.environ.get("OSM_SAMPLE_SQL", "")


def test_key_kind():
    assert rc.key_kind("Surface Inside Face Temperature") == "surface"
    assert rc.key_kind("Zone Air Temperature") == "zone"
    assert rc.key_kind("Electricity:Facility") == "other"
    assert rc.key_label("") == "Whole building"


def test_match_objects_case_insensitive():
    objs = [
        rc.ObjectInfo("Cube", space="Space 1", zone="Thermal Zone"),
        rc.ObjectInfo("Cube 1", space="Space 1", zone="Thermal Zone"),
        rc.ObjectInfo("Other", space="Space 2", zone=""),
    ]
    assert rc.match_objects("CUBE", "surface", objs) == ["Cube"]
    assert rc.match_objects("THERMAL ZONE", "zone", objs) == ["Cube", "Cube 1"]
    assert rc.match_objects("SPACE 2", "zone", objs) == ["Other"]  # falls back to the space
    assert rc.match_objects("", "surface", objs) == []
    assert rc.match_objects("CUBE", "other", objs) == []


def test_value_range():
    assert rc.value_range([[1, 5], [3, 9]]) == (1, 9)
    assert rc.value_range([[2, 2]]) == (2, 3)  # never degenerate
    assert rc.value_range([[1, 5]], lo=0, hi=10) == (0, 10)
    assert rc.value_range([]) == (0.0, 1.0)


def test_frame_times():
    assert rc.frame_times(3, 1, 0.5) == [1, 1.5, 2.0]


@pytest.mark.skipif(not os.path.exists(SAMPLE), reason="sample eplusout.sql not available (set OSM_SAMPLE_SQL)")
def test_sample_sql():
    pytest.importorskip("openstudio")
    r = rc.SqlResults(SAMPLE)
    env = r.env_periods()[0]
    assert "Hourly" in r.frequencies(env)
    names = r.variables(env, "Hourly")
    assert "Zone Air Temperature" in names
    key = r.keys(env, "Hourly", "Zone Air Temperature")[0]
    s = r.series(env, "Hourly", "Zone Air Temperature", key)
    assert s is not None and len(s.values) == 8760 and s.units == "C" and s.interval_hours == 1.0
    assert r.series(env, "Hourly", "Zone Air Temperature", "NO SUCH KEY") is None
