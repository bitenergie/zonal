import pytest

from zonal.core.diagram_core import extrude_plan, is_horizontal, plan_spaces
from zonal.core.geometry import polygon_area, polygon_normal, unit

SQ = [(0, 0, 0), (10, 0, 0), (10, 5, 0), (0, 5, 0)]


def test_is_horizontal():
    assert is_horizontal(SQ) and is_horizontal(SQ[::-1])
    assert not is_horizontal([(0, 0, 0), (1, 0, 0), (1, 0, 1)])


@pytest.mark.parametrize("plan", [SQ, SQ[::-1]])
def test_extrusion_is_closed_and_outward(plan):
    (floor,) = extrude_plan(plan, 3.0)
    kinds = [t for _l, t, _p in floor]
    assert kinds == ["Floor", "RoofCeiling", "Wall", "Wall", "Wall", "Wall"]
    for _l, t, poly in floor:
        n = unit(polygon_normal(poly))
        if t == "Floor":
            assert n[2] == pytest.approx(-1) and polygon_area(poly) == pytest.approx(50)
        elif t == "RoofCeiling":
            assert n[2] == pytest.approx(1) and {p[2] for p in poly} == {3.0}
        else:  # walls point away from the plan centre (5, 2.5)
            c = [sum(p[i] for p in poly) / 4 for i in range(3)]
            assert abs(n[2]) < 1e-9 and n[0] * (c[0] - 5) + n[1] * (c[1] - 2.5) > 0


def test_floors_stack():
    floors = extrude_plan(SQ, 3.0, 3)
    assert len(floors) == 3
    zs = [sorted({p[2] for _l, t, poly in fl for p in poly}) for fl in floors]
    assert zs == [[0, 3], [3, 6], [6, 9]]


def test_collinear_points_do_not_make_extra_walls():
    plan = [(0, 0, 0), (5, 0, 0), (10, 0, 0), (10, 5, 0), (0, 5, 0)]
    (floor,) = extrude_plan(plan, 3.0)
    assert sum(t == "Wall" for _l, t, _p in floor) == 4


@pytest.mark.parametrize(
    "plan, h, n",
    [([(0, 0, 0), (1, 0, 0), (1, 0, 1)], 3, 1), (SQ, 0, 1), (SQ, 3, 0), ([(0, 0, 0), (1, 0, 0)], 3, 1)],
)
def test_invalid_input(plan, h, n):
    with pytest.raises(ValueError):
        extrude_plan(plan, h, n)


def test_plan_spaces_sort_and_names():
    a = [(10, 0, 0), (12, 0, 0), (12, 2, 0), (10, 2, 0)]
    b = [(0, 5, 0), (2, 5, 0), (2, 7, 0), (0, 7, 0)]
    c = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)]
    spaces = plan_spaces([b, a, c], 3.0, 2)
    assert [s["name"] for s in spaces] == [
        "Story 1 Space 1",
        "Story 2 Space 1",
        "Story 1 Space 2",
        "Story 2 Space 2",
        "Story 1 Space 3",
        "Story 2 Space 3",
    ]
    # sorted by (min y, min x): c (0,0) < a (y 0, x 10) < b (y 5)
    assert min(p[0] for _l, _t, p in spaces[0]["surfaces"][:1] for p in p) == 0
    assert spaces[0]["story"] == "Story 1" and spaces[1]["story"] == "Story 2"
