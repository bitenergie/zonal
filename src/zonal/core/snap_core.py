"""Screen-space snapping over cached world geometry (vectorised numpy).

``SnapGeometry`` holds vertices and edges in world space, projects them once per view (memoised on the
perspective matrix) and finds the nearest vertex / edge midpoint / point on an edge to the mouse with
vectorised numpy, instead of one Python call per candidate. (A KDTree / BVHTree indexes world space, but the
query here is in pixels and the projection changes with every view move, so the tree would be rebuilt as often
as it is queried; one ``argmin`` over the memoised projection is cheaper.)
"""

from __future__ import annotations

import math

import numpy as np

# Pixels added to a candidate's distance when ranking kinds: a vertex beats a midpoint beats an edge
# when they are about equally close to the cursor.
BIAS_PX = {"vertex": 0.0, "midpoint": 4.0, "edge": 10.0}
_EMPTY3 = np.zeros((0, 3))


def project(persp, width: float, height: float, pts):
    """World points (N,3) -> screen pixels (N,2) and a mask of points in front of the viewer."""
    n = len(pts)
    if n == 0:
        return np.zeros((0, 2)), np.zeros(0, dtype=bool)
    h = np.ones((n, 4))
    h[:, :3] = pts
    clip = h @ persp.T
    w = clip[:, 3]
    ok = w > 1e-6
    w = np.where(ok, w, 1.0)
    return np.stack((width * 0.5 * (1.0 + clip[:, 0] / w), height * 0.5 * (1.0 + clip[:, 1] / w)), axis=1), ok


def _nearest_point(scr, ok, mouse, max_d2):
    if len(scr) == 0:
        return None
    d = scr - mouse
    d2 = np.einsum("ij,ij->i", d, d)
    d2[~ok] = np.inf
    i = int(np.argmin(d2))
    return (i, float(d2[i])) if d2[i] < max_d2 else None


def _nearest_segment(a, b, ok, mouse, max_d2):
    """Index and squared distance of the screen segment a-b closest to the mouse."""
    if len(a) == 0:
        return None
    ab = b - a
    l2 = np.einsum("ij,ij->i", ab, ab)
    deg = l2 < 1e-9
    t = np.clip(np.einsum("ij,ij->i", mouse - a, ab) / np.where(deg, 1.0, l2), 0.0, 1.0)
    t[deg] = 0.0
    diff = (a + ab * t[:, None]) - mouse
    d2 = np.einsum("ij,ij->i", diff, diff)
    d2[~ok] = np.inf
    i = int(np.argmin(d2))
    return (i, float(d2[i])) if d2[i] < max_d2 else None


def closest_on_segment_to_ray(e1, e2, ray_o, ray_d):
    """Point of segment e1-e2 closest to the (infinite) ray; endpoints clamp the result."""
    d = e2 - e1
    w0 = e1 - ray_o
    a, b, c = float(d @ d), float(d @ ray_d), float(ray_d @ ray_d)
    dd, e = float(d @ w0), float(ray_d @ w0)
    den = a * c - b * b
    s = 0.0 if (a < 1e-12 or abs(den) < 1e-12) else (b * e - c * dd) / den
    return e1 + d * max(0.0, min(1.0, s))


class SnapGeometry:
    """World-space vertices + edges with a per-view screen-space cache."""

    def __init__(self, verts=None, e1=None, e2=None):
        self.verts = np.asarray(verts, dtype=np.float64).reshape(-1, 3) if verts is not None else _EMPTY3
        self.e1 = np.asarray(e1, dtype=np.float64).reshape(-1, 3) if e1 is not None else _EMPTY3
        self.e2 = np.asarray(e2, dtype=np.float64).reshape(-1, 3) if e2 is not None else _EMPTY3
        self.mids = (self.e1 + self.e2) * 0.5
        self._key = None
        self._scr = None

    def __len__(self):
        return len(self.verts) + len(self.e1)

    def _screen(self, persp, width, height):
        key = (persp.tobytes(), width, height)
        if key != self._key:
            self._key = key
            sv, vok = project(persp, width, height, self.verts)
            sm, mok = project(persp, width, height, self.mids)
            s1, ok1 = project(persp, width, height, self.e1)
            s2, ok2 = project(persp, width, height, self.e2)
            self._scr = (sv, vok, sm, mok, s1, s2, ok1 & ok2)
        return self._scr

    def nearest(
        self,
        persp,
        width,
        height,
        mouse,
        ray_o,
        ray_d,
        max_px,
        kinds=("vertex", "midpoint", "edge"),
        bias_scale: float = 1.0,
    ):
        """Best snap within ``max_px`` of ``mouse`` (x, y): ``(score, kind, world_xyz)`` or None.

        ``score`` is the pixel distance plus the kind's bias, lower is better. ``ray_o`` / ``ray_d``
        (the view ray through the mouse) place the edge snap on the edge itself.
        """
        if not len(self):
            return None
        sv, vok, sm, mok, s1, s2, eok = self._screen(persp, width, height)
        mouse = np.asarray(mouse, dtype=np.float64)
        max_d2 = max_px * max_px
        found = []
        if "vertex" in kinds:
            hit = _nearest_point(sv, vok, mouse, max_d2)
            if hit:
                found.append((math.sqrt(hit[1]) + BIAS_PX["vertex"] * bias_scale, "vertex", self.verts[hit[0]]))
        if "midpoint" in kinds:
            hit = _nearest_point(sm, mok, mouse, max_d2)
            if hit:
                found.append((math.sqrt(hit[1]) + BIAS_PX["midpoint"] * bias_scale, "midpoint", self.mids[hit[0]]))
        if "edge" in kinds:
            hit = _nearest_segment(s1, s2, eok, mouse, max_d2)
            if hit:
                i = hit[0]
                world = closest_on_segment_to_ray(
                    self.e1[i], self.e2[i], np.asarray(ray_o, float), np.asarray(ray_d, float)
                )
                found.append((math.sqrt(hit[1]) + BIAS_PX["edge"] * bias_scale, "edge", world))
        if not found:
            return None
        score, kind, world = min(found, key=lambda c: c[0])
        return score, kind, (float(world[0]), float(world[1]), float(world[2]))
