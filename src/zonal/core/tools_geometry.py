"""Geometry for surface matching and loose-geometry projection (numpy, ``mathutils`` CDT; no openstudio)."""

from __future__ import annotations

import itertools
import math

import numpy as np

from .geometry import point_in_polygon, polygon_centroid, polygon_is_valid, simplify


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _len(a):
    return _dot(a, a) ** 0.5


def _unit(a):
    n = _len(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else (0.0, 0.0, 0.0)


def _newell(pts):
    n = [0.0, 0.0, 0.0]
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return tuple(n)


def polygon_normal_unit(pts):
    return _unit(_newell(pts))


def polygon_area_3d(pts) -> float:
    return 0.5 * _len(_newell(pts))


# --- draw tool snapping / axis constraints ---------------------------------------------

WORLD_AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


def axis_in_plane(axis, normal, eps: float = 1e-3):
    """World axis projected onto the drawing plane as a unit vector, or None if (almost) perpendicular to it."""
    d = WORLD_AXES[axis]
    k = _dot(d, normal)
    v = (d[0] - normal[0] * k, d[1] - normal[1] * k, d[2] - normal[2] * k)
    return _unit(v) if _len(v) > eps else None


def constrain_to_direction(p, ref, direction):
    """Closest point to p on the line through ref along the unit vector direction."""
    t = _dot(_sub(p, ref), direction)
    return (ref[0] + direction[0] * t, ref[1] + direction[1] * t, ref[2] + direction[2] * t)


def nearest_axis_direction(p, ref, normal):
    """In-plane world-axis direction best aligned with the move ref -> p (None if no axis lies in the plane)."""
    d = _sub(p, ref)
    best, best_s = None, -1.0
    for a in WORLD_AXES:
        u = axis_in_plane(a, normal)
        if u is not None and abs(_dot(d, u)) > best_s:
            best, best_s = u, abs(_dot(d, u))
    return best


def snap_scalar(x: float, step: float) -> float:
    return round(x / step) * step if step > 0 else x


def snap_to_grid(p, step: float, plane_point, normal, ref=None, direction=None):
    """Snap p to a grid of `step`, keeping it on the drawing plane.

    With ref/direction (axis lock) the distance from ref along direction is snapped; otherwise the world
    coordinates are rounded and the result is projected back onto the plane (exact for axis-aligned planes).
    """
    if step <= 0:
        return tuple(p)
    if ref is not None and direction is not None:
        t = snap_scalar(_dot(_sub(p, ref), direction), step)
        return (ref[0] + direction[0] * t, ref[1] + direction[1] * t, ref[2] + direction[2] * t)
    q = (snap_scalar(p[0], step), snap_scalar(p[1], step), snap_scalar(p[2], step))
    k = _dot(_sub(q, plane_point), normal)
    return (q[0] - normal[0] * k, q[1] - normal[1] * k, q[2] - normal[2] * k)


# --- surface matching ------------------------------------------------------------------


class OverlapIndex:
    """Vectorised broad phase for "which polygons could overlap this one with the opposite orientation".

    Stores per polygon its unit normal, a point on its plane and its bounding box in numpy arrays, so one
    query is a few array operations over all polygons instead of a Python loop with polygon maths per pair.
    Ids are handed out in insertion order; polygons can be removed again. ``candidates`` is a conservative
    superset: facing normals (``dot <= -0.999``), plane distance and bounding boxes within a few ``tol``.
    """

    def __init__(self, tol: float = 1e-3, capacity: int = 64):
        self.tol = tol
        self.size = 0
        self._n = np.zeros((capacity, 3))
        self._p0 = np.zeros((capacity, 3))
        self._lo = np.zeros((capacity, 3))
        self._hi = np.zeros((capacity, 3))
        self._alive = np.zeros(capacity, dtype=bool)

    def add(self, poly) -> int:
        if self.size == len(self._n):
            grow = len(self._n)
            for name in ("_n", "_p0", "_lo", "_hi"):
                arr = getattr(self, name)
                setattr(self, name, np.concatenate((arr, np.zeros((grow, 3)))))
            self._alive = np.concatenate((self._alive, np.zeros(grow, dtype=bool)))
        i = self.size
        pts = np.asarray(poly, dtype=np.float64)
        self._n[i] = polygon_normal_unit(poly)
        self._p0[i] = pts[0]
        self._lo[i] = pts.min(axis=0)
        self._hi[i] = pts.max(axis=0)
        self._alive[i] = True
        self.size += 1
        return i

    def remove(self, i: int) -> None:
        self._alive[i] = False

    def candidates(self, i: int, after: int | None = None):
        """Ids of live polygons (greater than ``after``, default ``i``) that may face and overlap polygon ``i``."""
        lo = i + 1 if after is None else after + 1
        s = slice(lo, self.size)
        n, tol = self._n[s], 3.0 * self.tol
        m = self._alive[s] & (n @ self._n[i] <= -0.999)
        m &= np.abs((self._p0[s] - self._p0[i]) @ self._n[i]) <= tol
        m &= np.all(self._lo[s] <= self._hi[i] + tol, axis=1) & np.all(self._hi[s] >= self._lo[i] - tol, axis=1)
        return np.nonzero(m)[0] + lo


def match_surfaces(items, tol: float = 1e-3, area_rtol: float = 0.01):
    """Pair coincident surfaces of different spaces.

    items: iterable of (key, polygon, space). Two surfaces match when they lie in the same plane with
    opposite normals, have the same area and the same vertex set (within ``tol``). Each surface is used once.
    Returns a list of (key_a, key_b).
    """
    # reduce collinear/coincident points first so an extra vertex on an edge does not prevent a match
    prep = []
    index = OverlapIndex(tol)
    for k, p, s in items:
        if len(p) >= 3 and polygon_is_valid(p, tol):
            p = simplify(p, tol)
            prep.append((k, p, s, polygon_normal_unit(p), polygon_area_3d(p), polygon_centroid(p)))
            index.add(p)
    pairs, used = [], set()
    for i, (ka, pa, sa, na, aa, ca) in enumerate(prep):
        if ka in used:
            continue
        for j in index.candidates(i):
            kb, pb, sb, nb, ab, cb = prep[j]
            if kb in used or sa == sb or _dot(na, nb) > -0.999:
                continue
            if abs(_dot(na, _sub(cb, ca))) > tol or abs(aa - ab) > area_rtol * max(aa, ab):
                continue
            if all(any(_len(_sub(p, q)) <= tol for q in pa) for p in pb) and all(
                any(_len(_sub(p, q)) <= tol for q in pb) for p in pa
            ):
                pairs.append((ka, kb))
                used.update((ka, kb))
                break
    return pairs


# --- projecting loose geometry ---------------------------------------------------------


def _basis(n):
    a = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = _unit(_cross(n, a))
    return u, _cross(n, u)  # u x v == n


def _to2d(poly, o, u, v):
    return [(_dot(_sub(p, o), u), _dot(_sub(p, o), v)) for p in poly]


def _area2d(p):
    return 0.5 * sum(a[0] * b[1] - a[1] * b[0] for a, b in zip(p, p[1:] + p[:1]))


def _is_convex(p, eps=1e-9) -> bool:
    n = len(p)
    for i in range(n):
        a, b, c = p[i], p[(i + 1) % n], p[(i + 2) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) < -eps:
            return False
    return True


def _clip_convex(subject, clip, eps=1e-9):
    """Sutherland-Hodgman; both polygons 2D counter-clockwise, `clip` convex."""
    out = subject
    for i, a in enumerate(clip):
        b = clip[(i + 1) % len(clip)]
        if not out:
            break

        def side(p, a=a, b=b):
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

        inp, out = out, []
        for j, p in enumerate(inp):
            q = inp[(j + 1) % len(inp)]
            sp, sq = side(p), side(q)
            if sp >= -eps:
                out.append(p)
            if (sp >= -eps) != (sq >= -eps):
                t = sp / (sp - sq)
                out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def project_polygon(loose, host, tol: float = 1e-3, min_area: float = 1e-6):
    """Project a loose polygon onto a host surface polygon.

    Returns the part of ``loose`` lying on ``host`` (3D, wound like the host so its normal points the same
    way), or None when it is not in the host's plane or does not overlap. A concave host is only handled
    when the loose polygon lies completely inside it.
    """
    if len(loose) < 3 or not polygon_is_valid(host, tol) or not polygon_is_valid(loose, tol):
        return None
    nh = polygon_normal_unit(host)
    if abs(_dot(polygon_normal_unit(loose), nh)) < 0.999:
        return None
    o = polygon_centroid(host)
    if any(abs(_dot(nh, _sub(p, o))) > tol for p in loose):
        return None
    u, v = _basis(nh)
    h2, l2 = _to2d(host, o, u, v), _to2d(loose, o, u, v)
    if _area2d(l2) < 0:  # host is counter-clockwise about its own normal; wind the loose polygon alike
        l2.reverse()
    if _is_convex(h2):
        res = _clip_convex(l2, h2)
    else:
        h3 = [(q[0], q[1], 0.0) for q in h2]
        res = l2 if all(point_in_polygon((p[0], p[1], 0.0), h3, (0, 0, 1), tol) for p in l2) else []
    if len(res) < 3 or abs(_area2d(res)) < min_area:
        return None
    return [(o[0] + a * u[0] + b * v[0], o[1] + a * u[1] + b * v[1], o[2] + a * u[2] + b * v[2]) for a, b in res]


def auto_subsurface_type(projected, host, host_type: str, default: str = "FixedWindow", tol: float = 1e-3) -> str:
    """Sub surface type for ``projected`` (the part of a loose polygon on ``host``):
    Skylight on a roof, Door when it touches the bottom edge of a wall, else ``default``."""
    if host_type == "RoofCeiling":
        return "Skylight"
    if host_type == "Wall" and min(p[2] for p in projected) <= min(p[2] for p in host) + tol:
        return "Door"
    return default


# --- 2D boolean split (and / A not B / B not A, like Clipper2) -------------------------------


def _seg_dist(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else min(1.0, max(0.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _seg_cross(a, b, c, d):
    rx, ry, sx, sy = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t, u = (qx * sy - qy * sx) / den, (qx * ry - qy * rx) / den
    if 0 <= t <= 1 and 0 <= u <= 1:
        return (a[0] + t * rx, a[1] + t * ry)
    return None


def _node(segs, tol):
    """Split segments at all mutual intersections / touching points; returns (vertices, edge set)."""
    splits = [[] for _ in segs]
    for i, (a, b) in enumerate(segs):
        for j in range(i + 1, len(segs)):
            c, d = segs[j]
            splits[i] += [p for p in (c, d) if _seg_dist(p, a, b) <= tol]
            splits[j] += [p for p in (a, b) if _seg_dist(p, c, d) <= tol]
            x = _seg_cross(a, b, c, d)
            if x:
                splits[i].append(x)
                splits[j].append(x)
    verts: list = []

    def vid(p):
        for k, q in enumerate(verts):
            if math.hypot(p[0] - q[0], p[1] - q[1]) <= tol:
                return k
        verts.append(p)
        return len(verts) - 1

    edges = set()
    for (a, b), sp in zip(segs, splits):
        dx, dy = b[0] - a[0], b[1] - a[1]
        ids = []
        for p in sorted([a, b] + sp, key=lambda p: (p[0] - a[0]) * dx + (p[1] - a[1]) * dy):
            k = vid(p)
            if not ids or ids[-1] != k:
                ids.append(k)
        edges.update((min(u, v), max(u, v)) for u, v in itertools.pairwise(ids))
    return verts, edges


def _bounded_faces(verts, edges):
    """Counter-clockwise face cycles (as point lists) of a planar straight-line graph."""
    out: dict = {}
    for u, v in edges:
        out.setdefault(u, []).append(v)
        out.setdefault(v, []).append(u)
    for u, lst in out.items():
        lst.sort(key=lambda v, u=u: math.atan2(verts[v][1] - verts[u][1], verts[v][0] - verts[u][0]))
    seen, faces = set(), []
    for u, nbrs in out.items():
        for v in nbrs:
            if (u, v) in seen:
                continue
            cyc, a, b = [], u, v
            while (a, b) not in seen:
                seen.add((a, b))
                cyc.append(a)
                lst = out[b]
                a, b = b, lst[lst.index(a) - 1]
            pts = [verts[k] for k in cyc]
            if _area2d(pts) > 0:
                faces.append(pts)
    return faces


def _pip2d(p, poly) -> bool:
    inside = False
    for a, b in zip(poly, poly[1:] + poly[:1]):
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < a[0] + (p[1] - a[1]) / (b[1] - a[1]) * (b[0] - a[0]):
            inside = not inside
    return inside


def _interior_point(poly):
    ys = sorted({p[1] for p in poly})
    _gap, y = max(((ys[i + 1] - ys[i], (ys[i] + ys[i + 1]) / 2) for i in range(len(ys) - 1)), default=(0, ys[0]))
    xs = sorted(
        a[0] + (y - a[1]) / (b[1] - a[1]) * (b[0] - a[0])
        for a, b in zip(poly, poly[1:] + poly[:1])
        if (a[1] > y) != (b[1] > y)
    )
    pairs = [(xs[i + 1] - xs[i], (xs[i] + xs[i + 1]) / 2) for i in range(0, len(xs) - 1, 2)]
    return (max(pairs)[1], y)


def _drop_collinear(poly, tol):
    return [(p[0], p[1]) for p in simplify([(x, y, 0.0) for x, y in poly], tol)]


def _slits(inner, outer):
    """Horizontal cuts from the left/right-most vertex of `inner` to the boundary of `outer`, so a polygon
    lying completely inside another does not leave the overlay disconnected."""
    out = []
    for p, sign in ((min(inner), -1), (max(inner), 1)):
        hits = []
        for c, d in zip(outer, outer[1:] + outer[:1]):
            if (c[1] - p[1]) * (d[1] - p[1]) <= 0 and c[1] != d[1]:
                x = c[0] + (p[1] - c[1]) / (d[1] - c[1]) * (d[0] - c[0])
                if (x - p[0]) * sign > 0:
                    hits.append(x)
        if hits:
            out.append((p, ((min if sign > 0 else max)(hits), p[1])))
    return out


def _on_boundary(p, poly, tol) -> bool:
    return min(_seg_dist(p, c, d) for c, d in zip(poly, poly[1:] + poly[:1])) <= tol


def _contained(inner, outer, tol) -> bool:
    """Every vertex and edge midpoint of `inner` is inside `outer` or on its boundary."""
    pts = list(inner) + [((p[0] + q[0]) / 2, (p[1] + q[1]) / 2) for p, q in zip(inner, inner[1:] + inner[:1])]
    return all(_pip2d(p, outer) or _on_boundary(p, outer, tol) for p in pts)


def _pinched(face, tol) -> bool:
    """True when the outline touches itself (a vertex visited twice), e.g. a vertex of the inner polygon on an
    edge of the outer one."""
    seen = []
    for p in face:
        if any(math.hypot(p[0] - q[0], p[1] - q[1]) <= tol for q in seen):
            return True
        seen.append(p)
    return False


_FALLBACK = object()


def _boolean_split_cdt(a, b, tol, min_area):
    """Overlay via Blender's constrained Delaunay triangulation (C++): it nodes the outlines and reports which
    input polygons contain each output region (``[0, 1]`` = A and B, ``[0]`` = A not B, ``[1]`` = B not A).
    Returns ``_FALLBACK`` when a region has a hole or pinches itself, which the slit based fallback handles."""
    from mathutils import Vector
    from mathutils.geometry import delaunay_2d_cdt

    na = len(a)
    verts, _e, faces, _ov, _oe, owners = delaunay_2d_cdt(
        [Vector(p) for p in (*a, *b)], [], [list(range(na)), list(range(na, na + len(b)))], 4, tol
    )
    res: tuple = ([], [], [])
    regions = []
    for face, own in zip(faces, owners):
        pts = [(verts[i][0], verts[i][1]) for i in face]
        if _area2d(pts) < 0:
            pts.reverse()
        if not own or _area2d(pts) < min_area:
            continue
        pts = _drop_collinear(pts, tol)
        if len(pts) < 3 or _pinched(pts, tol):
            return _FALLBACK
        regions.append(pts)
        res[0 if len(own) == 2 else 1 if own[0] == 0 else 2].append(pts)
    # a region lying inside another one leaves a hole that this output cannot express
    inner = [_interior_point(r) for r in regions]
    if any(i != j and _pip2d(inner[j], regions[i]) for i in range(len(regions)) for j in range(len(regions))):
        return _FALLBACK
    return res if res[0] else None


def boolean_split_2d(a, b, tol: float = 1e-3, min_area: float = 1e-6):
    """Overlay two simple counter-clockwise polygons: returns (and, a_not_b, b_not_a), each a list of
    counter-clockwise polygons, or None when they do not overlap. Vertices lying on the other polygon's edges
    are handled. Holes cannot be represented, so a polygon enclosing the other (or touching it from the inside
    at isolated points) is returned as several pieces."""
    try:
        res = _boolean_split_cdt(a, b, tol, min_area)
    except ImportError:  # no bpy: use the pure Python overlay
        res = _FALLBACK
    return _boolean_split_py(a, b, tol, min_area) if res is _FALLBACK else res


def _boolean_split_py(a, b, tol: float = 1e-3, min_area: float = 1e-6):
    """Pure Python overlay (O(s^2) noding), used for nested / pinched polygons and without bpy."""
    base = list(zip(a, a[1:] + a[:1])) + list(zip(b, b[1:] + b[:1]))
    nested = [(i, o) for i, o in ((b, a), (a, b)) if _contained(i, o, tol)]
    for with_slits in (False, True):
        segs = list(base)
        if with_slits:
            for inner, outer in nested:
                segs += [s for s in _slits(inner, outer) if not _on_boundary(s[0], outer, tol)]
        verts, edges = _node(segs, tol)
        res: tuple = ([], [], [])
        pinched = False
        for face in _bounded_faces(verts, edges):
            if _area2d(face) < min_area:
                continue
            q = _interior_point(face)
            in_a, in_b = _pip2d(q, a), _pip2d(q, b)
            if in_a or in_b:
                res[0 if in_a and in_b else 1 if in_a else 2].append(_drop_collinear(face, tol))
                pinched = pinched or _pinched(face, tol)
        # a contained polygon strictly inside the other leaves a hole: that always needs the cuts
        strict = any(all(_pip2d(p, o) and not _on_boundary(p, o, tol) for p in i) for i, o in nested)
        if with_slits or not (pinched or strict):
            return res if res[0] else None
    return None


def split_overlap(pa, pb, tol: float = 1e-3, min_area: float = 1e-6):
    """Boolean split of two coplanar, oppositely oriented 3D polygons.

    Returns None when they do not overlap, else a dict with ``and_a``/``and_b`` (the shared region, wound like
    A resp. B, same order so ``and_a[k]`` faces ``and_b[k]``), ``a_only`` and ``b_only`` (the remainders).
    """
    if len(pa) < 3 or len(pb) < 3:
        return None
    # cheap rejects first (orientation, plane); the validity checks simplify both polygons and are far costlier
    na, nb = polygon_normal_unit(pa), polygon_normal_unit(pb)
    if _dot(na, nb) > -0.999:
        return None
    if not polygon_is_valid(pa, tol) or not polygon_is_valid(pb, tol):
        return None
    o = polygon_centroid(pa)
    if any(abs(_dot(na, _sub(p, o))) > tol for p in pb):
        return None
    u, v = _basis(na)
    a2, b2 = _to2d(pa, o, u, v), _to2d(pb, o, u, v)
    b2.reverse()  # B's normal is opposite: make it counter-clockwise in A's frame
    if _area2d(a2) <= 0 or _area2d(b2) <= 0:
        return None
    res = boolean_split_2d(a2, b2, tol, min_area)
    if res is None:
        return None

    def to3d(poly, flip):
        pts = [(o[0] + x * u[0] + y * v[0], o[1] + x * u[1] + y * v[1], o[2] + x * u[2] + y * v[2]) for x, y in poly]
        return pts[::-1] if flip else pts

    both, a_only, b_only = res
    return {
        "and_a": [to3d(p, False) for p in both],
        "and_b": [to3d(p, True) for p in both],
        "a_only": [to3d(p, False) for p in a_only],
        "b_only": [to3d(p, True) for p in b_only],
    }


# --- intersect: cut a polygon where another polygon passes completely through it -------------------------


def _plane_of(poly):
    n = polygon_normal_unit(poly)
    return n, _dot(n, poly[0])


def cut_polygon_by_polygon(p, q, tol: float = 1e-3, min_area: float = 1e-6):
    """Split planar polygon ``p`` where the plane of polygon ``q`` cuts it AND ``q`` covers the whole cut.

    This is SketchUp's "intersect": an edge only divides a face when it runs from boundary to boundary, so a
    chord of ``p`` is only used where ``q`` spans all of it (a slab passing through a wall). Parallel polygons,
    partial overlaps and touching polygons leave ``p`` alone. Returns the list of pieces (``[p]`` if no cut).
    """
    p = [tuple(map(float, x)) for x in p]
    np_, _dp = _plane_of(p)
    nq, dq = _plane_of(q)
    if _len(_cross(np_, nq)) < 1e-6:
        return [p]
    side = [_dot(nq, x) - dq > tol for x in p]
    if all(side) or not any(side):
        return [p]
    d = _unit(_cross(np_, nq))
    # signed distance to q's plane for every vertex; crossings are inserted into a copy of the outline
    verts, crossings = [], []  # crossings: (t along the cut line, index in verts)
    n = len(p)
    for i in range(n):
        a, b = p[i], p[(i + 1) % n]
        verts.append(a)
        if side[i] != side[(i + 1) % n]:
            sa, sb = _dot(nq, a) - dq, _dot(nq, b) - dq
            k = sa / (sa - sb)
            x = (a[0] + k * (b[0] - a[0]), a[1] + k * (b[1] - a[1]), a[2] + k * (b[2] - a[2]))
            verts.append(x)
            crossings.append((_dot(x, d), len(verts) - 1))
    crossings.sort()
    for c in range(0, len(crossings) - 1, 2):  # consecutive crossing pairs bound the inside of the polygon
        (t1, i1), (t2, i2) = crossings[c], crossings[c + 1]
        x1, x2 = verts[i1], verts[i2]
        if t2 - t1 <= tol:
            continue
        samples = [tuple(x1[j] + f * (x2[j] - x1[j]) for j in range(3)) for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
        if not all(point_in_polygon(s, q, nq, tol) for s in samples):
            continue
        lo, hi = sorted((i1, i2))
        a_part = verts[lo : hi + 1]
        b_part = verts[hi:] + verts[: lo + 1]
        pieces = [simplify(a_part), simplify(b_part)]
        if any(len(x) < 3 or polygon_area_3d(x) <= min_area for x in pieces):
            continue
        out = []
        for piece in pieces:  # further chords of a concave polygon are found again on the pieces
            out += cut_polygon_by_polygon(piece, q, tol, min_area)
        return out
    return [p]
