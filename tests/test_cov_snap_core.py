import numpy as np

from zonal.core import snap_core as sn


def test_project_empty_and_behind():
    s, ok = sn.project(np.eye(4), 100, 100, np.zeros((0, 3)))
    assert s.shape == (0, 2)
    p = np.eye(4)
    p[3] = [0, 0, 0, -1]  # w negative
    s, ok = sn.project(p, 100, 100, np.array([[0.0, 0, 0]]))
    assert not ok[0]


def test_nearest_helpers_empty_and_far():
    z = np.zeros((0, 2))
    assert sn._nearest_point(z, np.zeros(0, bool), np.zeros(2), 1.0) is None
    assert sn._nearest_segment(z, z, np.zeros(0, bool), np.zeros(2), 1.0) is None
    a = np.array([[0.0, 0.0]])
    assert sn._nearest_point(a, np.array([True]), np.array([50.0, 50.0]), 1.0) is None
    assert sn._nearest_segment(a, a, np.array([True]), np.array([50.0, 50.0]), 1.0) is None
    assert sn._nearest_segment(a, a, np.array([True]), np.array([0.5, 0.0]), 1.0)[0] == 0


def test_closest_on_segment_degenerate_and_parallel():
    o, d = np.array([0.0, 0, 10]), np.array([0.0, 0, -1])
    e = np.array([1.0, 1, 0])
    assert (sn.closest_on_segment_to_ray(e, e, o, d) == e).all()
    r = sn.closest_on_segment_to_ray(np.array([0.0, 0, 0]), np.array([0.0, 0, 5]), o, d)
    assert r.shape == (3,)


def test_geometry_empty():
    g = sn.SnapGeometry()
    assert len(g) == 0
    assert g.nearest(np.eye(4), 100, 100, (50, 50), np.zeros(3), np.array([0.0, 0, -1]), 10) is None
