"""Mesh -> planar polygon recovery (numpy, ``mathutils`` KDTree when available, no openstudio dependency).

glTF only carries triangles. OpenStudio needs one polygon per surface, so coplanar,
edge-connected triangles are clustered and each cluster's outer boundary is traced.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

Vec = tuple[float, float, float]


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a):
    return math.sqrt(dot(a, a))


def unit(a):
    n = norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else (0.0, 0.0, 0.0)


def polygon_normal(pts) -> Vec:
    """Newell normal (area-weighted, magnitude = 2*area)."""
    nx = ny = nz = 0.0
    for i, p in enumerate(iterable=pts):
        q = pts[(i + 1) % len(pts)]
        nx += (p[1] - q[1]) * (p[2] + q[2])
        ny += (p[2] - q[2]) * (p[0] + q[0])
        nz += (p[0] - q[0]) * (p[1] + q[1])
    return (nx, ny, nz)


def polygon_area(pts) -> float:
    return 0.5 * norm(polygon_normal(pts))


_KD_MIN_POINTS = 256  # below this the dict based weld beats building a KDTree


def weld(points, tol=1e-4):
    """Merge coincident positions. Returns (unique_points, old_index -> new_index).

    Large inputs use ``mathutils.kdtree`` (C), small ones or a missing bpy the dict grid below."""
    if len(points) >= _KD_MIN_POINTS:
        try:
            from mathutils import kdtree
        except ImportError:
            pass
        else:
            return _weld_kdtree(points, tol, kdtree)
    return _weld_grid(points, tol)


def _weld_kdtree(points, tol, kdtree):
    n = len(points)
    kd = kdtree.KDTree(n)
    for i, p in enumerate(points):
        kd.insert(p, i)
    kd.balance()
    rep = list(range(n))  # each point merges into the earliest representative within tol
    for i, p in enumerate(points):
        best = i
        for _co, j, _dist in kd.find_range(p, tol):
            if j < best and rep[j] == j:
                best = j
        rep[i] = best
    new: dict = {}
    uniq: list = []
    remap = []
    for i in range(n):
        r = rep[i]
        if r not in new:
            new[r] = len(uniq)
            uniq.append(points[r])
        remap.append(new[r])
    return uniq, remap


def _weld_grid(points, tol):
    grid: dict = {}
    uniq: list = []
    remap = []
    for p in points:
        key = tuple(round(c / tol) for c in p)
        found = None
        for dx in (0, -1, 1):
            for dy in (0, -1, 1):
                for dz in (0, -1, 1):
                    j = grid.get((key[0] + dx, key[1] + dy, key[2] + dz))
                    if j is not None and norm(sub(uniq[j], p)) <= tol:
                        found = j
                        break
                if found is not None:
                    break
            if found is not None:
                break
        if found is None:
            found = len(uniq)
            uniq.append(p)
            grid[key] = found
        remap.append(found)
    return uniq, remap


def cluster_coplanar(points, tris, ntol=1e-3, dtol=1e-3):
    """Group triangles into edge-connected, coplanar clusters. Returns list of tri lists."""
    if not len(tris):
        return []
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    tv = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    p0 = pts[tv[:, 0]]
    nrm = np.cross(pts[tv[:, 1]] - p0, pts[tv[:, 2]] - p0)
    ln = np.linalg.norm(nrm, axis=1)
    valid = ln >= 1e-12  # the rest is degenerate
    nrm /= np.where(valid, ln, 1.0)[:, None]
    off = np.einsum("ij,ij->i", nrm, p0)

    # every undirected edge of the valid triangles, sorted so triangles sharing an edge are adjacent
    vt = np.nonzero(valid)[0]
    ev = tv[vt]
    ends = np.concatenate((ev[:, (0, 1)], ev[:, (1, 2)], ev[:, (2, 0)]))
    key = ends.min(axis=1) * len(pts) + ends.max(axis=1)
    order = np.argsort(key, kind="stable")
    ks, owner = key[order], np.tile(vt, 3)[order]

    # all pairs of triangles on a shared edge (almost always exactly two), tested for coplanarity at once
    pa, pb, k = [], [], 1
    while k < len(ks):
        same = ks[:-k] == ks[k:]
        if not same.any():
            break
        pa.append(owner[:-k][same])
        pb.append(owner[k:][same])
        k += 1
    parent = list(range(len(tris)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    if pa:
        t1, t2 = np.concatenate(pa), np.concatenate(pb)
        ok = (np.einsum("ij,ij->i", nrm[t1], nrm[t2]) > 1 - ntol) & (np.abs(off[t1] - off[t2]) < dtol)
        for x, y in zip(t1[ok].tolist(), t2[ok].tolist()):
            parent[find(x)] = find(y)
    groups: dict = defaultdict(list)
    for ti in vt.tolist():
        groups[find(ti)].append(tris[ti])
    return list(groups.values())


def boundary_loops(tris) -> list[list[int]]:
    """Trace boundary loops (vertex index lists) of a triangle cluster, preserving winding."""
    directed = set()
    for a, b, c in tris:
        directed.update(((a, b), (b, c), (c, a)))
    nxt = defaultdict(list)
    for a, b in directed:
        if (b, a) not in directed:
            nxt[a].append(b)
    loops = []
    used = set()
    for start in list(nxt):
        for first in list(nxt[start]):
            if (start, first) in used:
                continue
            loop = [start]
            cur, nx = start, first
            used.add((cur, nx))
            while nx != start:
                loop.append(nx)
                cands = [n for n in nxt[nx] if (nx, n) not in used]
                if not cands:
                    loop = []
                    break
                cur, nx = nx, cands[0]
                used.add((cur, nx))
            if len(loop) >= 3:
                loops.append(loop)
    return loops


def seg_dist3(p, a, b) -> float:
    """Distance from point p to the segment a-b (3D)."""
    ab = sub(b, a)
    l2 = dot(ab, ab)
    t = 0.0 if l2 == 0 else min(1.0, max(0.0, dot(sub(p, a), ab) / l2))
    return norm(sub(p, (a[0] + t * ab[0], a[1] + t * ab[1], a[2] + t * ab[2])))


def simplify(pts, tol=1e-4):
    """Drop coincident and collinear vertices (a vertex within ``tol`` of the line through its neighbours,
    which also removes spikes). A single distance tolerance, like SketchUp's."""
    out = list(pts)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            p, q, r = out[i - 1], out[i], out[(i + 1) % len(out)]
            pr = norm(sub(r, p))
            d = norm(sub(q, p)) if pr <= tol else norm(cross(sub(q, p), sub(r, p))) / pr
            if d <= tol:
                del out[i]
                changed = True
                break
    return out


def polygon_centroid(pts) -> Vec:
    """Area-weighted centroid of a planar polygon (falls back to the vertex mean when degenerate)."""
    n = polygon_normal(pts)
    a2 = dot(n, n)
    if a2 <= 0:
        k = len(pts) or 1
        return tuple(sum(p[i] for p in pts) / k for i in range(3))
    o = pts[0]
    cx = cy = cz = tot = 0.0
    for i in range(1, len(pts) - 1):
        u, v = sub(pts[i], o), sub(pts[i + 1], o)
        w = dot(cross(u, v), n)  # signed twice-area weight of this triangle
        cx += w * (o[0] + pts[i][0] + pts[i + 1][0]) / 3
        cy += w * (o[1] + pts[i][1] + pts[i + 1][1]) / 3
        cz += w * (o[2] + pts[i][2] + pts[i + 1][2]) / 3
        tot += w
    return (cx / tot, cy / tot, cz / tot) if tot else o


def polygon_is_valid(pts, tol=1e-3) -> bool:
    """At least 3 distinct non-collinear points, non-zero area and all points within ``tol`` of the best plane
    (through the centroid with the Newell normal)."""
    red = simplify(pts, tol)
    if len(red) < 3:
        return False
    n = unit(polygon_normal(red))
    if n == (0.0, 0.0, 0.0):
        return False
    c = polygon_centroid(red)
    return all(abs(dot(n, sub(p, c))) <= tol for p in red)


def best_fit_plane(pts) -> tuple[Vec, Vec]:
    """(centroid, unit Newell normal) of a polygon; the normal is (0, 0, 0) for a degenerate polygon."""
    return polygon_centroid(pts), unit(polygon_normal(pts))


def max_plane_distance(pts, centroid: Vec, normal: Vec) -> float:
    """Largest distance of any point from the plane through ``centroid`` with the unit ``normal``."""
    return max((abs(dot(normal, sub(p, centroid))) for p in pts), default=0.0)


def project_to_plane(pts, centroid: Vec, normal: Vec) -> list[Vec]:
    """Orthogonal projection of the points onto the plane through ``centroid`` with the unit ``normal``."""
    out = []
    for p in pts:
        d = dot(normal, sub(p, centroid))
        out.append((p[0] - d * normal[0], p[1] - d * normal[1], p[2] - d * normal[2]))
    return out


def faces_from_triangles(positions, indices, weld_tol=1e-4):
    """positions: [(x,y,z)], indices: flat list. Returns list of polygons (list of Vec).

    Holes are dropped: OpenStudio models openings as sub surfaces, which glTF from
    OpenStudio carries as separate nodes.
    """
    pts, remap = weld(positions, weld_tol)
    tris = []
    for i in range(0, len(indices) - 2, 3):
        a, b, c = (remap[indices[i]], remap[indices[i + 1]], remap[indices[i + 2]])
        if len({a, b, c}) == 3:
            tris.append((a, b, c))
    polys = []
    for cluster in cluster_coplanar(pts, tris):
        loops = boundary_loops(cluster)
        if not loops:
            continue
        best = max(loops, key=lambda lp: polygon_area([pts[i] for i in lp]))
        poly = simplify([pts[i] for i in best])
        if len(poly) >= 3 and polygon_area(poly) > 1e-9:
            polys.append(poly)
    return polys


def point_in_polygon(pt, poly, normal, tol: float = 1e-4, include_border: bool = True) -> bool:
    """Ray casting after projecting to the dominant plane of `normal`. A point within ``tol`` of an edge is on
    the border and returns ``include_border`` (so a window flush with its wall's edge is still inside)."""
    n = len(poly)
    if any(seg_dist3(pt, poly[i], poly[(i + 1) % n]) <= tol for i in range(n)):
        return include_border
    ax = max(range(3), key=lambda i: abs(normal[i]))
    u, v = [i for i in range(3) if i != ax]
    x, y = pt[u], pt[v]
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i][u], poly[i][v]
        x2, y2 = poly[(i + 1) % n][u], poly[(i + 1) % n][v]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def fill_notches(outer, subs, tol=1e-4):
    """Union `outer` with sub polygons that share edges with it.

    A door/window touching its parent's edge is a notch (not a hole) in the triangulated
    parent, so the traced outer loop lacks that part. Shared edges are traversed in opposite
    directions; cancelling them leaves the full outline. Returns `outer` if nothing merges.
    """
    allpts = list(outer) + [p for s in subs for p in s]
    uniq, remap = weld(allpts, tol)
    polys, k = [], 0
    for poly in [outer, *subs]:
        polys.append([remap[k + i] for i in range(len(poly))])
        k += len(poly)

    def edges(loop):
        return {(loop[i], loop[(i + 1) % len(loop)]) for i in range(len(loop))}

    merged = edges(polys[0])
    changed = False
    for loop in polys[1:]:
        e = edges(loop)
        shared = {(a, b) for (a, b) in e if (b, a) in merged}
        if not shared:
            continue
        changed = True
        merged = (merged - {(b, a) for a, b in shared}) | (e - shared)
    if not changed:
        return list(outer)
    nxt: dict = defaultdict(list)
    for a, b in merged:
        nxt[a].append(b)
    if any(len(v) != 1 for v in nxt.values()):
        return list(outer)
    start = next(iter(nxt))
    loop, cur = [start], nxt[start][0]
    while cur != start and len(loop) <= len(nxt):
        loop.append(cur)
        cur = nxt[cur][0]
    if cur != start or len(loop) != len(nxt):
        return list(outer)
    poly = simplify([uniq[i] for i in loop])
    return poly if polygon_area(poly) >= polygon_area(outer) else list(outer)
