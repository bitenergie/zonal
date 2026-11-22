from zonal.core import tools_geometry as t


def quad(x0, y0, x1, y1, z=0.0):
    return [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]


def test_axis_helpers():
    assert t.axis_in_plane("Z", (0, 0, 1)) is None
    assert t.axis_in_plane("X", (0, 0, 1)) == (1.0, 0.0, 0.0)
    assert t.nearest_axis_direction((5, 1, 0), (0, 0, 0), (0, 0, 1)) == (1.0, 0.0, 0.0)
    assert t.constrain_to_direction((3, 4, 0), (0, 0, 0), (1, 0, 0)) == (3, 0, 0)


def test_snap():
    assert t.snap_scalar(1.4, 0) == 1.4
    assert t.snap_to_grid((1.4, 2.6, 0.2), 0, (0, 0, 0), (0, 0, 1)) == (1.4, 2.6, 0.2)
    assert t.snap_to_grid((1.4, 2.6, 0.2), 1, (0, 0, 0), (0, 0, 1)) == (1.0, 3.0, 0.0)
    r = t.snap_to_grid((1.4, 0, 0), 1, (0, 0, 0), (0, 0, 1), ref=(0, 0, 0), direction=(1, 0, 0))
    assert r == (1.0, 0.0, 0.0)


def test_project_polygon_branches():
    host = quad(0, 0, 4, 4)
    assert t.project_polygon([(0, 0, 0), (1, 0, 0)], host) is None
    assert t.project_polygon(quad(0, 0, 1, 1, 1.0), host) is None  # off plane
    wall = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]
    assert t.project_polygon(wall, host) is None  # not parallel
    assert t.project_polygon(quad(10, 10, 11, 11), host) is None  # no overlap
    # concave host
    L = [(0, 0, 0), (4, 0, 0), (4, 2, 0), (2, 2, 0), (2, 4, 0), (0, 4, 0)]
    assert t.project_polygon(quad(0.2, 0.2, 1, 1), L) is not None
    assert t.project_polygon(quad(3, 3, 3.5, 3.5), L) is None


def test_split_overlap_branches():
    a = quad(0, 0, 2, 2)
    b = quad(1, 0, 3, 2)[::-1]
    assert t.split_overlap(a[:2], b) is None
    assert t.split_overlap(a, quad(0, 0, 2, 2)) is None  # same orientation
    assert t.split_overlap(a, quad(0, 0, 2, 2, 1.0)[::-1]) is None  # off plane
    assert t.split_overlap(a, quad(5, 5, 6, 6)[::-1]) is None
    r = t.split_overlap(a, b)
    assert r and r["and_a"] and r["a_only"] and r["b_only"]
    # nested (hole) case
    r = t.split_overlap(quad(0, 0, 6, 6), quad(2, 2, 3, 3)[::-1])
    assert r and r["and_a"]
    # inner touching edge from inside
    r = t.split_overlap(quad(0, 0, 6, 6), quad(0, 0, 3, 3)[::-1])
    assert r
    # clockwise A -> area <= 0 guard is via normal; just ensure degenerate polygons are None
    assert t.split_overlap([(0, 0, 0), (1, 0, 0), (2, 0, 0)], b) is None


def test_cut_polygon_by_polygon():
    import pytest

    from zonal.core.tools_geometry import cut_polygon_by_polygon, polygon_area_3d

    wall = [(0, 0, 0), (10, 0, 0), (10, 0, 3), (0, 0, 3)]
    slab = [(-1, -1, 1.5), (11, -1, 1.5), (11, 5, 1.5), (-1, 5, 1.5)]
    pieces = cut_polygon_by_polygon(wall, slab)
    assert sorted(round(polygon_area_3d(x), 6) for x in pieces) == [15.0, 15.0]
    assert sorted(max(p[2] for p in x) for x in pieces) == [1.5, 3.0]
    # the slab only covers part of the cut: nothing is split
    short = [(0, -1, 1.5), (5, -1, 1.5), (5, 5, 1.5), (0, 5, 1.5)]
    assert cut_polygon_by_polygon(wall, short) == [[tuple(map(float, p)) for p in wall]]
    # parallel / touching polygons
    assert len(cut_polygon_by_polygon(wall, [(0, 1, 0), (10, 1, 0), (10, 1, 3), (0, 1, 3)])) == 1
    edge = [(-1, -1, 3), (11, -1, 3), (11, 5, 3), (-1, 5, 3)]  # in the plane of the wall's top edge
    assert len(cut_polygon_by_polygon(wall, edge)) == 1
    # a concave (L-shaped) wall cut by a slab through its tall part ends in two pieces
    ell = [(0, 0, 0), (10, 0, 0), (10, 0, 1), (4, 0, 1), (4, 0, 3), (0, 0, 3)]
    slab2 = [(-1, -1, 2), (11, -1, 2), (11, 5, 2), (-1, 5, 2)]
    parts = cut_polygon_by_polygon(ell, slab2)
    assert len(parts) == 2 and sum(polygon_area_3d(x) for x in parts) == pytest.approx(polygon_area_3d(ell))
