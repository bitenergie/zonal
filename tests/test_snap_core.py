import numpy as np
import pytest

from zonal.core.snap_core import SnapGeometry, closest_on_segment_to_ray, project

W = H = 200.0
EYE = np.eye(4)  # x, y in [-1, 1] map to pixels 0..200, w = 1
RAY_O, RAY_D = np.array([0.0, 0.0, 10.0]), np.array([0.0, 0.0, -1.0])


def px(x, y):
    return (W * 0.5 * (1 + x), H * 0.5 * (1 + y))


def square():
    v = np.array([(-0.5, -0.5, 0), (0.5, -0.5, 0), (0.5, 0.5, 0), (-0.5, 0.5, 0)], float)
    return SnapGeometry(v, v, np.roll(v, -1, axis=0))


def test_project_and_behind_camera():
    persp = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 1, 0]], float)  # w = z
    scr, ok = project(persp, W, H, np.array([(0.0, 0.0, 1.0), (0.0, 0.0, -1.0)]))
    assert ok.tolist() == [True, False]
    assert scr[0] == pytest.approx(px(0, 0))


def test_vertex_snap_and_radius():
    g = square()
    hit = g.nearest(EYE, W, H, px(-0.5, -0.5) + np.array([3.0, 2.0]), RAY_O, RAY_D, 14.0)
    assert hit[1] == "vertex" and hit[2] == pytest.approx((-0.5, -0.5, 0.0))
    assert g.nearest(EYE, W, H, px(-0.5, -0.5) + np.array([40.0, 40.0]), RAY_O, RAY_D, 14.0) is None


def test_midpoint_loses_to_much_closer_vertex_but_wins_when_alone():
    g = square()
    assert g.nearest(EYE, W, H, px(0.0, -0.5), RAY_O, RAY_D, 14.0)[1] == "midpoint"
    near_vertex = np.array(px(0.5, -0.5)) + np.array([-2.0, 0.0])
    assert g.nearest(EYE, W, H, near_vertex, RAY_O, RAY_D, 14.0)[1] == "vertex"


def test_edge_snap_lies_on_the_edge():
    g = square()
    mouse = np.array(px(0.25, -0.5)) + np.array([0.0, 4.0])  # 4 px off the bottom edge, away from verts / mid
    hit = g.nearest(EYE, W, H, mouse, np.array([0.25, -0.46, 10.0]), RAY_D, 14.0)
    assert hit[1] == "edge"
    assert hit[2][1] == pytest.approx(-0.5) and hit[2][0] == pytest.approx(0.25)


def test_kinds_filter_and_empty():
    g = square()
    assert g.nearest(EYE, W, H, px(-0.5, -0.5), RAY_O, RAY_D, 14.0, kinds=("midpoint",)) is None
    assert SnapGeometry().nearest(EYE, W, H, (0, 0), RAY_O, RAY_D, 14.0) is None


def test_cache_follows_view_change():
    g = square()
    assert g.nearest(EYE, W, H, px(-0.5, -0.5), RAY_O, RAY_D, 5.0)[1] == "vertex"
    shifted = np.eye(4)
    shifted[0, 3] = 1.0  # view moved: the old pixel no longer hits the vertex
    assert g.nearest(shifted, W, H, px(-0.5, -0.5), RAY_O, RAY_D, 5.0) is None


def test_closest_on_segment_clamps():
    p = closest_on_segment_to_ray(
        np.array([0.0, 0, 0]), np.array([1.0, 0, 0]), np.array([5.0, 1, 5]), np.array([0.0, 0, -1])
    )
    assert p == pytest.approx([1.0, 0, 0])
