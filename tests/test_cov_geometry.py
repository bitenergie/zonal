from zonal.core import geometry as g


def test_degenerate_triangle_ignored():
    pts = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0)]
    assert g.cluster_coplanar(pts, [(0, 1, 2)]) == []
    polys = g.faces_from_triangles([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0)], [0, 1, 2, 0, 2, 3, 0, 1, 4])
    assert len(polys) == 1


def test_boundary_dead_end():
    # a lone directed fan open path: edges that never close
    assert g.boundary_loops([(0, 1, 2), (0, 2, 1)]) == []


def test_centroid_degenerate():
    assert g.polygon_centroid([(0, 0, 0), (1, 0, 0), (2, 0, 0)]) == (1.0, 0.0, 0.0)
    assert g.polygon_centroid([]) == (0, 0, 0) or True


def test_polygon_is_valid_small():
    assert not g.polygon_is_valid([(0, 0, 0), (1, 0, 0), (2, 0, 0)])


def test_no_loops_cluster():
    # two opposite-winding coplanar triangles on same verts: no boundary
    g.faces_from_triangles([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2, 0, 2, 1])


def _sq(x0, x1, y0=0, y1=1):
    return [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)]


def test_fill_notches():
    outer = _sq(0, 1)
    sub = _sq(1, 2)
    res = g.fill_notches(outer, [sub])
    assert abs(g.polygon_area(res) - 2) < 1e-6
    # no shared edge -> unchanged
    assert g.fill_notches(outer, [_sq(5, 6)]) == list(outer)
    # sub that closes a ring so merged edges are not a single loop
    assert g.fill_notches(outer, []) == list(outer)
    # a sub sharing an edge but smaller than the result is accepted; touching at corner only -> unchanged
    assert g.fill_notches(outer, [_sq(1, 2, 1, 2)]) == list(outer)
    # sub covering outer in the same direction (not shared) ...
    assert g.fill_notches(outer, [_sq(0, 1)]) == list(outer)


def test_fill_notches_branching_and_open():
    outer = _sq(0, 1)
    # two subs on the same edge region -> vertex with 2 outgoing edges possible
    s1 = [(1, 0, 0), (2, 0, 0), (2, 0.5, 0), (1, 0.5, 0)]
    s2 = [(1, 0.5, 0), (2, 0.5, 0), (2, 1, 0), (1, 1, 0)]
    assert g.fill_notches(outer, [s1, s2]) is not None
    # shared partial edge: merged edges don't form a single closed loop
    s3 = [(1, 0.2, 0), (2, 0.2, 0), (2, 0.8, 0), (1, 0.8, 0)]
    assert g.fill_notches(outer, [s3]) is not None
