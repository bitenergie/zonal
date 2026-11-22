from zonal.core import search_core as s

SQ = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]


def f(**kw):
    d = {
        "class": "Surface",
        "name": "Wall A",
        "type": "Wall",
        "construction": "Brick",
        "normal": (0, -1, 0),
        "points": SQ,
        "boundary": "Outdoors",
        "sun": "SunExposed",
        "wind": "WindExposed",
    }
    d.update(kw)
    return d


def test_class_of_unknown():
    assert s.class_of("Nope") == ""
    assert s.azimuth_deg((0, 0, 1)) is None


def test_is_non_convex_triangle():
    assert not s.is_non_convex(SQ[:3])


def test_matches_all_filters():
    assert s.matches(s.SearchCriteria(), f())
    assert not s.matches(s.SearchCriteria(cls="Other"), f())
    assert not s.matches(s.SearchCriteria(name="zzz"), f())
    assert not s.matches(s.SearchCriteria(type="Floor"), f())
    assert not s.matches(s.SearchCriteria(construction="glass"), f())
    assert not s.matches(s.SearchCriteria(boundary="Ground"), f())
    assert not s.matches(s.SearchCriteria(sun="NoSun"), f())
    assert not s.matches(s.SearchCriteria(wind="NoWind"), f())
    assert s.matches(s.SearchCriteria(boundary="Outdoors", sun="SunExposed", wind="WindExposed"), f())
    assert not s.matches(s.SearchCriteria(exclude_horizontal=True), f(normal=(0, 0, 1)))
    assert not s.matches(s.SearchCriteria(az_from=10, az_to=20), f(normal=(0, 0, 1)))
    assert not s.matches(s.SearchCriteria(az_from=10, az_to=20), f())
    assert not s.matches(s.SearchCriteria(min_vertices=4), f())
    assert s.matches(s.SearchCriteria(min_vertices=3), f())
    assert not s.matches(s.SearchCriteria(only_non_convex=True), f())


def test_non_convex_clockwise():
    pts = [(0, 0, 0), (0, 2, 0), (1, 2, 0), (1, 1, 0), (2, 1, 0), (2, 0, 0)]
    assert s.is_non_convex(pts)


def test_tilt_range():
    roof = f(normal=(0, 0, 1))
    wall = f(normal=(0, -1, 0))
    floor = f(normal=(0, 0, -1))
    c = s.SearchCriteria(tilt_from=80, tilt_to=100)
    assert s.matches(c, wall) and not s.matches(c, roof) and not s.matches(c, floor)
    c = s.SearchCriteria(tilt_from=170, tilt_to=180)
    assert s.matches(c, floor) and not s.matches(c, wall)
    assert s.matches(s.SearchCriteria(), roof)  # default range 0..180 matches everything


def test_story_filter_is_case_insensitive_and_exact():
    c = s.SearchCriteria(story="story 2")
    assert s.matches(c, f(story="Story 2"))
    assert not s.matches(c, f(story="Story 20"))
    assert not s.matches(c, f())  # no story at all


def test_invert_within_class_and_across_classes():
    wall, sub = f(name="W"), f(name="S", **{"class": "SubSurface", "type": "FixedWindow"})
    c = s.SearchCriteria(cls="Surface", type="Wall", invert=True)
    assert not s.matches(c, wall)  # a wall matches the search, so it is excluded
    assert s.matches(c, f(type="Floor"))  # a non-wall surface is kept
    assert not s.matches(c, sub)  # other classes stay out of a within-class inversion
    c = s.SearchCriteria(cls="Surface", type="Wall", invert_across_classes=True)
    assert s.matches(c, sub) and s.matches(c, f(type="Floor")) and not s.matches(c, wall)
    c = s.SearchCriteria(type="Wall", invert=True)  # no class chosen: inversion covers every class
    assert s.matches(c, sub)
