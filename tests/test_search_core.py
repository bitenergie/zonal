from zonal.core.search_core import (
    SearchCriteria,
    azimuth_deg,
    class_of,
    in_azimuth_range,
    is_horizontal,
    is_non_convex,
    matches,
)

SQUARE = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
L_SHAPE = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (1, 2, 0), (0, 2, 0)]


def face(**kw):
    f = {
        "class": "Surface",
        "name": "Surface 1",
        "type": "Wall",
        "construction": "",
        "normal": (0, -1, 0),
        "points": SQUARE,
    }
    f.update(kw)
    return f


def test_azimuth_clockwise_from_north():
    assert azimuth_deg((0, 1, 0)) == 0
    assert round(azimuth_deg((1, 0, 0))) == 90
    assert round(azimuth_deg((0, -1, 0))) == 180
    assert round(azimuth_deg((-1, 0, 0))) == 270
    assert azimuth_deg((0, 0, 1)) is None


def test_azimuth_north_axis_offset():
    assert round(azimuth_deg((1, 0, 0), north_axis=90)) == 0
    assert round(azimuth_deg((0, 1, 0), north_axis=10)) == 350


def test_azimuth_range_wraps_through_north():
    assert in_azimuth_range(355, 350, 10) and in_azimuth_range(5, 350, 10)
    assert not in_azimuth_range(180, 350, 10)
    assert in_azimuth_range(90, 45, 135) and not in_azimuth_range(200, 45, 135)


def test_horizontal():
    assert is_horizontal((0, 0, -1)) and not is_horizontal((0, -1, 0))


def test_non_convex():
    assert not is_non_convex(SQUARE)
    assert is_non_convex(L_SHAPE)
    assert is_non_convex(list(reversed(L_SHAPE)))  # winding independent
    assert not is_non_convex(SQUARE[:3])


def test_class_of():
    assert class_of("Wall") == "Surface" and class_of("Door") == "SubSurface"
    assert class_of("SiteShading") == "ShadingSurface" and class_of("InteriorPartitionSurface")


def test_matches_name_substring_and_type():
    assert matches(SearchCriteria(name="surface"), face())
    assert not matches(SearchCriteria(name="roof"), face())
    assert matches(SearchCriteria(type="Wall"), face())
    assert not matches(SearchCriteria(type="Floor"), face())
    assert not matches(SearchCriteria(cls="SubSurface"), face())


def test_matches_orientation_and_horizontal():
    south = face(normal=(0, -1, 0))
    assert matches(SearchCriteria(az_from=135, az_to=225), south)
    assert not matches(SearchCriteria(az_from=0, az_to=90), south)
    floor = face(normal=(0, 0, -1))
    assert not matches(SearchCriteria(az_from=0, az_to=90), floor)  # no orientation
    assert not matches(SearchCriteria(exclude_horizontal=True), floor)
    assert matches(SearchCriteria(), floor)


def test_matches_geometry_filters():
    assert not matches(SearchCriteria(only_non_convex=True), face())
    assert matches(SearchCriteria(only_non_convex=True), face(points=L_SHAPE))
    assert matches(SearchCriteria(min_vertices=4), face(points=L_SHAPE))  # 6 > 4
    assert not matches(SearchCriteria(min_vertices=4), face())  # exactly 4


def test_matches_boundary_exposure():
    f = face(boundary="Outdoors", sun="SunExposed", wind="WindExposed")
    assert matches(SearchCriteria(boundary="Outdoors", sun="SunExposed"), f)
    assert not matches(SearchCriteria(boundary="Ground"), f)
