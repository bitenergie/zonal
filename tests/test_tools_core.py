from pathlib import Path

from zonal.core.tools_core import scan_osm_names, surface_type_from_normal, unique_name
from zonal.core.tools_geometry import match_surfaces, polygon_normal_unit, project_polygon


def quad(x0, y0, x1, y1, z=0.0):
    return [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]


def test_surface_type_from_normal():
    assert surface_type_from_normal(1.0) == "RoofCeiling"
    assert surface_type_from_normal(-1.0) == "Floor"
    assert surface_type_from_normal(0.0) == "Wall"


def test_unique_name():
    assert unique_name("Wall", []) == "Wall"
    assert unique_name("Wall", ["Wall"]) == "Wall 1"
    assert unique_name("Wall", ["Wall", "Wall 1"]) == "Wall 2"


def test_match_surfaces_opposite_coincident():
    a = quad(0, 0, 2, 2)
    b = list(reversed(a))
    c = quad(0, 0, 2, 2, z=5)
    pairs = match_surfaces([("a", a, "S1"), ("b", b, "S2"), ("c", c, "S3"), ("same", list(reversed(a)), "S1")])
    assert pairs == [("a", "b")]
    assert match_surfaces([("a", a, "S1"), ("b", a, "S2")]) == []  # same orientation: not a match


def test_project_inside_and_clipped():
    wall = quad(0, 0, 10, 10)
    inside = project_polygon(quad(2, 2, 4, 4), wall)
    assert inside is not None and len(inside) == 4
    clipped = project_polygon(quad(8, 8, 14, 14), wall)  # sticks out: clipped to the host
    assert sorted({round(p[0], 6) for p in clipped}) == [8.0, 10.0]
    assert project_polygon(quad(0, 0, 2, 2, z=1.0), wall) is None  # off plane
    assert project_polygon(quad(20, 20, 22, 22), wall) is None  # no overlap


def test_project_result_normal_follows_host():
    res = project_polygon(list(reversed(quad(1, 1, 2, 2))), quad(0, 0, 10, 10))
    assert polygon_normal_unit(res)[2] > 0.99


def test_scan_osm_names():
    txt = (
        "OS:SpaceType,\n  {abc},  !- Handle\n  Office,  !- Name\n  ;\n"
        "OS:Construction,\n  {d},  !- Handle\n  Wall X,  !- Name\n  {e};\n"
    )
    out = scan_osm_names(txt)
    assert out["spacetype"] == ["Office"] and out["construction"] == ["Wall X"]


def test_scan_minimal_template():
    p = Path(__file__).resolve().parent.parent / "src" / "zonal" / "templates" / "MinimalTemplate.osm"
    out = scan_osm_names(p.read_text(encoding="utf-8"))
    assert len(out["construction"]) == 26 and len(out["spacetype"]) == 32 and len(out["cset"]) == 7


def _area(p):
    from zonal.core.tools_geometry import polygon_area_3d

    return polygon_area_3d(p)


def test_split_overlap_partial():
    from zonal.core.tools_geometry import split_overlap

    a = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)]
    b = [(1, 0, 0), (1, 2, 0), (3, 2, 0), (3, 0, 0)]  # [1,3]x[0,2], facing down
    r = split_overlap(a, b)
    assert [round(_area(p), 6) for p in r["and_a"]] == [2.0]
    assert [round(_area(p), 6) for p in r["a_only"]] == [2.0]
    assert [round(_area(p), 6) for p in r["b_only"]] == [2.0]
    assert polygon_normal_unit(r["and_b"][0])[2] < 0 < polygon_normal_unit(r["and_a"][0])[2]
    assert polygon_normal_unit(r["b_only"][0])[2] < 0


def test_split_overlap_identical_nested_disjoint():
    from zonal.core.tools_geometry import split_overlap

    a = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)]
    assert split_overlap(a, a[::-1])["a_only"] == []
    inner = [(0.5, 0.5, 0), (0.5, 1.5, 0), (1.5, 1.5, 0), (1.5, 0.5, 0)]  # clockwise = faces down
    r = split_overlap(a, inner)
    assert round(sum(_area(p) for p in r["and_a"]), 6) == 1.0
    assert round(sum(_area(p) for p in r["a_only"]), 6) == 3.0 and r["b_only"] == []
    far = [(5, 0, 0), (5, 2, 0), (7, 2, 0), (7, 0, 0)]
    assert split_overlap(a, far) is None


def test_split_overlap_concave():
    from zonal.core.tools_geometry import split_overlap

    ell = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0), (1, 2, 0), (0, 2, 0)]  # area 3
    sq = [(0, 0, 0), (0, 2, 0), (2, 2, 0), (2, 0, 0)]  # facing down, area 4
    r = split_overlap(ell, sq)
    assert round(sum(_area(p) for p in r["and_a"]), 6) == 3.0
    assert r["a_only"] == [] and round(sum(_area(p) for p in r["b_only"]), 6) == 1.0


def test_split_overlap_vertex_on_edge():
    from zonal.core.tools_geometry import split_overlap

    a = [(0, 0, 3), (4, 0, 3), (4, 4, 3), (0, 4, 3)]
    # apex of B on A's bottom edge, B otherwise inside A (facing down)
    r = split_overlap(a, [(2, 0, 3), (1, 2, 3), (3, 2, 3)])
    assert round(sum(_area(p) for p in r["and_a"]), 6) == 2.0
    assert round(sum(_area(p) for p in r["a_only"]), 6) == 14.0 and r["b_only"] == []
    for piece in r["a_only"]:
        assert len(set(piece)) == len(piece)  # no self-touching outline
    # B's vertices on A's edges, B sticking out on one side
    r = split_overlap(a, [(2, 0, 3), (5, 2, 3), (2, 4, 3), (0, 2, 3)][::-1])
    assert r is not None
    assert round(sum(_area(p) for p in r["and_a"]) + sum(_area(p) for p in r["a_only"]), 6) == 16.0
    # B inside A sharing part of an edge: stays a single piece
    r = split_overlap(a, [(1, 0, 3), (1, 2, 3), (3, 2, 3), (3, 0, 3)])
    assert len(r["a_only"]) == 1 and len(r["and_a"]) == 1


def test_point_in_polygon_border_and_validity():
    from zonal.core.geometry import point_in_polygon, polygon_is_valid, simplify

    sq = [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)]
    assert point_in_polygon((2, 0, 0), sq, (0, 0, 1))  # on an edge
    assert not point_in_polygon((2, 0, 0), sq, (0, 0, 1), include_border=False)
    assert point_in_polygon((2, 2, 0), sq, (0, 0, 1)) and not point_in_polygon((5, 2, 0), sq, (0, 0, 1))
    assert polygon_is_valid(sq)
    assert not polygon_is_valid([(0, 0, 0), (1, 0, 0), (2, 0, 0)])  # collinear
    assert not polygon_is_valid([(0, 0, 0), (4, 0, 0), (4, 4, 1), (0, 4, 0)])  # non-planar
    assert simplify([(0, 0, 0), (2, 0, 0), (4, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)]) == sq


def test_match_surfaces_extra_collinear_vertex():
    a = ("a", [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)], "s1")
    b = ("b", [(0, 0, 0), (0, 0, 3), (2, 0, 3), (4, 0, 3), (4, 0, 0)], "s2")
    assert match_surfaces([a, b]) == [("a", "b")]


def test_axis_constraint_and_grid_snap():
    from zonal.core.tools_geometry import axis_in_plane, constrain_to_direction, snap_to_grid

    up = (0.0, 0.0, 1.0)
    assert axis_in_plane("Z", up) is None  # vertical axis is perpendicular to a horizontal plane
    assert axis_in_plane("X", up) == (1.0, 0.0, 0.0)
    wall = (0.0, -1.0, 0.0)  # plane y = const: Z stays usable, Y does not
    assert axis_in_plane("Y", wall) is None
    assert axis_in_plane("Z", wall) == (0.0, 0.0, 1.0)
    q = constrain_to_direction((3.0, 2.0, 0.0), (1.0, 1.0, 0.0), (1.0, 0.0, 0.0))
    assert q == (3.0, 1.0, 0.0)
    s = snap_to_grid((1.23, 2.51, 0.02), 0.1, (0, 0, 0), up)
    assert [round(c, 6) for c in s] == [1.2, 2.5, 0.0]
    t = snap_to_grid((3.04, 1.0, 0.0), 0.5, (0, 0, 0), up, ref=(1.0, 1.0, 0.0), direction=(1.0, 0.0, 0.0))
    assert [round(c, 6) for c in t] == [3.0, 1.0, 0.0]
