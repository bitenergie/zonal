"""Geometry repairs in the builder (the SketchUp plugin's check_model_object rules)."""

import pytest

pytest.importorskip("openstudio")

from zonal.core.gltf_reader import Face, GltfScene
from zonal.core.osm_builder import BuildOptions, build_model

WALL = [(0, 0, 0), (10, 0, 0), (10, 0, 3), (0, 0, 3)]  # south wall, normal -y (outward)
WIN = [(2, 0, 1), (4, 0, 1), (4, 0, 2), (2, 0, 2)]
FLOOR = [(0, 0, 0), (0, 5, 0), (10, 5, 0), (10, 0, 0)]  # normal -z
ROOF = [(0, 0, 3), (10, 0, 3), (10, 5, 3), (0, 5, 3)]  # normal +z


def _face(name, poly, **extras):
    extras.setdefault("spaceName", "S")
    return Face(name, [tuple(map(float, p)) for p in poly], extras, name)


def run(*faces):
    return build_model(GltfScene(list(faces), {}, "t"), BuildOptions())


def nz(surface):
    n = surface.outwardNormal()
    return n.z()


def test_upside_down_floor_and_roof_are_flipped():
    m, rep = run(
        _face("F", FLOOR[::-1], surfaceType="Floor"),
        _face("R", ROOF[::-1], surfaceType="RoofCeiling"),
    )
    assert nz(m.getSurfaceByName("F").get()) < 0
    assert nz(m.getSurfaceByName("R").get()) > 0
    assert sum("upside-down" in w for w in rep.warnings) == 2


def test_correct_floor_is_untouched():
    m, rep = run(_face("F", FLOOR, surfaceType="Floor"))
    assert nz(m.getSurfaceByName("F").get()) < 0
    assert not any("upside-down" in w for w in rep.warnings)


def test_degenerate_surface_is_skipped():
    m, rep = run(
        _face("Line", [(0, 0, 0), (1, 0, 0), (2, 0, 0)], surfaceType="Wall"),
        _face("Wall", WALL, surfaceType="Wall"),
    )
    assert len(m.getSurfaces()) == 1
    assert any("Line" in w for w in rep.warnings)


def test_out_of_plane_vertex_is_projected():
    bent = [(0, 0, 0), (10, 0, 0), (10, 0.2, 3), (0, 0, 3)]
    m, rep = run(_face("W", bent, surfaceType="Wall"))
    assert any("out of plane" in w for w in rep.warnings)
    verts = list(m.getSurfaceByName("W").get().vertices())
    assert len(verts) == 4
    assert abs(m.getSurfaceByName("W").get().outwardNormal().y()) > 0.9  # still a wall facing -y / +y


def test_inside_out_window_is_flipped():
    m, rep = run(_face("W", WALL, surfaceType="Wall"), _face("Win", WIN[::-1], surfaceType="FixedWindow"))
    assert len(m.getSubSurfaces()) == 1
    ss = m.getSubSurfaces()[0]
    assert ss.outwardNormal().y() < 0  # same direction as the wall
    assert any("inside-out" in w for w in rep.warnings)


def test_slightly_off_plane_window_is_projected():
    off = [(x, 0.02, z) for x, _y, z in WIN]
    m, rep = run(_face("W", WALL, surfaceType="Wall"), _face("Win", off, surfaceType="FixedWindow"))
    assert len(m.getSubSurfaces()) == 1
    assert all(abs(v.y()) < 1e-6 for v in m.getSubSurfaces()[0].vertices())


def test_window_far_from_wall_is_still_skipped():
    far = [(x, 1.0, z) for x, _y, z in WIN]
    m, rep = run(_face("W", WALL, surfaceType="Wall"), _face("Win", far, surfaceType="FixedWindow"))
    assert len(m.getSubSurfaces()) == 0
    assert any("no parent surface" in w for w in rep.warnings)


def test_window_equal_to_wall_is_skipped():
    m, rep = run(_face("W", WALL, surfaceType="Wall"), _face("Win", WALL, surfaceType="FixedWindow"))
    assert len(m.getSubSurfaces()) == 0
    assert any("same size" in w for w in rep.warnings)


def test_matched_pair_stays_consistent():
    a = _face("A", WALL, surfaceType="Wall", outsideBoundaryCondition="Surface", outsideBoundaryConditionObjectName="B")
    b = _face(
        "B",
        WALL[::-1],
        surfaceType="Wall",
        spaceName="T",
        outsideBoundaryCondition="Surface",
        outsideBoundaryConditionObjectName="A",
    )
    m, rep = run(a, b)
    sa, sb = m.getSurfaceByName("A").get(), m.getSurfaceByName("B").get()
    assert sa.adjacentSurface().get().handle() == sb.handle()
    assert sb.adjacentSurface().get().handle() == sa.handle()
    assert not any("point back" in w for w in rep.warnings)


def test_one_sided_boundary_is_reset():
    # the partner exists but is also matched to somebody else: the pair is repaired / reset, never left dangling
    a = _face("A", WALL, surfaceType="Wall", outsideBoundaryCondition="Surface", outsideBoundaryConditionObjectName="B")
    b = _face("B", WALL[::-1], surfaceType="Wall", spaceName="T")
    m, _rep = run(a, b)
    sa = m.getSurfaceByName("A").get()
    if sa.outsideBoundaryCondition() == "Surface":
        assert sa.adjacentSurface().is_initialized()


# --- plugin parity: zones, colours, site, sub surface adjacency -------------------------------------
def test_new_zone_for_space_and_zone_colours():
    scene = GltfScene([_face("W", WALL, surfaceType="Wall")], {}, "t")
    m, _ = build_model(scene, BuildOptions())
    assert not m.getSpaces()[0].thermalZone().is_initialized()
    m, _ = build_model(scene, BuildOptions(new_zone_for_space=True))
    tz = m.getSpaces()[0].thermalZone()
    assert tz.is_initialized() and tz.get().nameString().startswith("S")
    assert tz.get().renderingColor().is_initialized()


def test_zone_without_metadata_gets_a_rendering_colour():
    m, _ = build_model(GltfScene([_face("W", WALL, thermalZoneName="Z1")], {}, "t"), BuildOptions())
    assert m.getThermalZoneByName("Z1").get().renderingColor().is_initialized()


def test_site_follows_weather_fields():
    import json

    fields = {"city": "Bern", "latitude": 46.9, "longitude": 7.4, "timeZone": 1.0, "elevation": 540.0}
    store = json.dumps({"objects": [{"type": "WeatherFile", "name": "w", "fields": fields}]})
    m, _ = build_model(GltfScene([_face("W", WALL)], {"osmModel": store}, "t"), BuildOptions())
    site = m.getSite()
    assert (site.nameString(), site.latitude(), site.longitude(), site.timeZone(), site.elevation()) == (
        "Bern",
        46.9,
        7.4,
        1.0,
        540.0,
    )


def test_reciprocal_sub_surface_adjacency_is_kept():
    wall = [(0, 0, 0), (10, 0, 0), (10, 0, 3), (0, 0, 3)]
    win = [(2, 0, 1), (4, 0, 1), (4, 0, 2), (2, 0, 2)]
    m, rep = run(
        _face(
            "wall1",
            wall,
            surfaceType="Wall",
            spaceName="A",
            outsideBoundaryCondition="Surface",
            outsideBoundaryConditionObjectName="wall2",
        ),
        _face(
            "wall2",
            wall[::-1],
            surfaceType="Wall",
            spaceName="B",
            outsideBoundaryCondition="Surface",
            outsideBoundaryConditionObjectName="wall1",
        ),
        _face("win1", win, surfaceType="FixedWindow", spaceName="A", outsideBoundaryConditionObjectName="win2"),
        _face("win2", win[::-1], surfaceType="FixedWindow", spaceName="B", outsideBoundaryConditionObjectName="win1"),
    )
    w1, w2 = (m.getSubSurfaceByName(n).get() for n in ("win1", "win2"))
    assert w1.adjacentSubSurface().get().handle() == w2.handle()
    assert w2.adjacentSubSurface().get().handle() == w1.handle()
    assert not any("point back" in w for w in rep.warnings)


def test_window_dividing_its_wall_is_reported_and_skipped():
    low = [(0, 0, 0), (10, 0, 0), (10, 0, 1), (0, 0, 1)]
    high = [(0, 0, 2), (10, 0, 2), (10, 0, 3), (0, 0, 3)]
    strip = [(0, 0, 1), (10, 0, 1), (10, 0, 2), (0, 0, 2)]  # edge to edge: splits the wall in two
    m, rep = run(
        _face("low", low, surfaceType="Wall"),
        _face("high", high, surfaceType="Wall"),
        _face("strip", strip, surfaceType="FixedWindow"),
    )
    assert len(m.getSubSurfaces()) == 0
    assert any("divides its base surface" in w and "low" in w and "high" in w for w in rep.warnings)


def test_window_outside_its_named_wall_is_not_contained():
    beside = [(11, 0, 1), (13, 0, 1), (13, 0, 2), (11, 0, 2)]
    m, rep = run(_face("W", WALL, surfaceType="Wall"), _face("Win", beside, surfaceType="FixedWindow", surfaceName="W"))
    assert len(m.getSubSurfaces()) == 0
    assert any("not contained" in w for w in rep.warnings)


def test_window_touching_one_wall_edge_is_still_attached():
    door = [(2, 0, 0), (3, 0, 0), (3, 0, 2), (2, 0, 2)]
    m, rep = run(_face("W", WALL, surfaceType="Wall"), _face("Door", door, surfaceType="Door", surfaceName="W"))
    assert len(m.getSubSurfaces()) == 1
