import pytest

from zonal.core.geometry import polygon_normal, unit
from zonal.core.volume_core import order_cells, split_into_cells


def box(x0, y0, z0, x1, y1, z1, flip=False):
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]
    return v, [x[::-1] for x in f] if flip else f


def join(*meshes):
    pts, faces = [], []
    for v, f in meshes:
        base = len(pts)
        pts += v
        faces += [[i + base for i in x] for x in f]
    return pts, faces


def center(poly):
    return tuple(sum(p[i] for p in poly) / len(poly) for i in range(3))


@pytest.mark.parametrize("flip", [False, True])
def test_single_box_any_winding(flip):
    cells = split_into_cells(*box(0, 0, 0, 4, 3, 2, flip))
    assert len(cells) == 1 and not cells[0]["open"] and len(cells[0]["polys"]) == 6
    c = (2, 1.5, 1)
    for poly in cells[0]["polys"]:  # all outward
        n = unit(polygon_normal(poly))
        m = center(poly)
        assert sum(n[i] * (m[i] - c[i]) for i in range(3)) > 0


def test_two_separate_boxes():
    assert len(split_into_cells(*join(box(0, 0, 0, 1, 1, 1), box(5, 0, 0, 6, 1, 1)))) == 2


def test_touching_boxes_with_duplicate_walls():
    cells = split_into_cells(*join(box(0, 0, 0, 1, 1, 1), box(1, 0, 0, 2, 1, 1)))
    assert len(cells) == 2 and all(len(c["polys"]) == 6 for c in cells)


def test_shared_wall_modelled_once():
    # one box 2x1x1 split by a single internal wall at x=1: the outer faces are split too
    v = [(x, y, z) for z in (0, 1) for y in (0, 1) for x in (0, 1, 2)]
    i = {p: k for k, p in enumerate(v)}
    quad = lambda *ps: [i[p] for p in ps]
    faces = []
    for x0, x1 in ((0, 1), (1, 2)):
        faces += [
            quad((x0, 0, 0), (x0, 1, 0), (x1, 1, 0), (x1, 0, 0)),
            quad((x0, 0, 1), (x1, 0, 1), (x1, 1, 1), (x0, 1, 1)),
            quad((x0, 0, 0), (x1, 0, 0), (x1, 0, 1), (x0, 0, 1)),
            quad((x0, 1, 0), (x0, 1, 1), (x1, 1, 1), (x1, 1, 0)),
        ]
    faces += [quad((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)), quad((2, 0, 0), (2, 1, 0), (2, 1, 1), (2, 0, 1))]
    faces.append(quad((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)))  # shared wall, once
    cells = split_into_cells(v, faces)
    assert len(cells) == 2 and all(len(c["polys"]) == 6 and not c["open"] for c in cells)


def test_t_junction_neighbour():
    # the big room's wall is one quad, the neighbour's wall is two quads: vertices on edges are inserted
    big = box(0, 0, 0, 1, 2, 1)
    a, b = box(1, 0, 0, 2, 1, 1), box(1, 1, 0, 2, 2, 1)
    cells = split_into_cells(*join(big, a, b))
    assert len(cells) == 3 and not any(c["open"] for c in cells)


def test_open_box_is_flagged():
    v, f = box(0, 0, 0, 1, 1, 1)
    cells = split_into_cells(v, f[:5])
    assert len(cells) == 1 and cells[0]["open"] and len(cells[0]["polys"]) == 5
    assert split_into_cells(v, f[:5], keep_open=False) == []


def test_coplanar_merge():
    # floor of a box made of two quads becomes one polygon (box with a subdivided bottom)
    v, f = box(0, 0, 0, 2, 1, 1)
    v += [(1, 0, 0), (1, 1, 0)]
    f = [x for k, x in enumerate(f) if k != 0] + [[0, 3, 9, 1 + 7 + 0][:0] or [0, 3, 9, 8], [8, 9, 2, 1]]
    cells = split_into_cells(v, f)
    assert len(cells) == 1 and len(cells[0]["polys"]) == 6
    assert len(split_into_cells(v, f, merge_coplanar=False)[0]["polys"]) == 7


def test_order_cells_stories():
    cells = split_into_cells(*join(box(0, 0, 3, 1, 1, 6), box(0, 0, 0, 1, 1, 3), box(2, 0, 0, 3, 1, 3)))
    ordered = order_cells(cells)
    assert [s for _c, s in ordered] == [0, 0, 1]
