"""Split one 3D mesh into its enclosed volumes (spaces) (no bpy, no openstudio).

Input is a polygon soup: several closed shells, rooms that share walls (a shared face may be modelled once, with
three or more faces meeting at an edge) or a mix. The faces cut space into cells; every bounded cell becomes one
space made of outward facing polygons, so a wall shared by two rooms shows up once per room (matched afterwards by
Surface Matching). The face normals of the input are not trusted: sides are found geometrically.

Algorithm: every face has two sides. Around each edge the faces are sorted by angle; the two sides that touch the
same wedge between neighbouring faces belong to the same cell (union-find). A component with positive signed volume
is a bounded cell, a negative one is the unbounded outside; a component holding both sides of one face has a hole
(open edge) and is reported as ``open`` instead.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from .geometry import Vec, boundary_loops, cluster_coplanar, polygon_area, polygon_normal, simplify, unit, weld
from .tools_geometry import OverlapIndex, polygon_normal_unit, split_overlap


def _index_polygons(polygons, tol):
    """Weld the corners of point polygons and return ``(points, index polygons)``; degenerate ones are dropped."""
    flat = [p for poly in polygons for p in poly]
    pts, remap = weld([tuple(map(float, p)) for p in flat], tol)
    polys, k = [], 0
    for poly in polygons:
        idx = [remap[k + i] for i in range(len(poly))]
        k += len(poly)
        idx = [v for n, v in enumerate(idx) if v != idx[n - 1]]
        if len(idx) >= 3 and len(set(idx)) == len(idx) and polygon_area([pts[i] for i in idx]) > tol * tol:
            polys.append(idx)
    return pts, polys


class _CoplanarIndex(OverlapIndex):
    """OverlapIndex that also offers polygons facing the same way (normals are not trusted here)."""

    def candidates(self, i, after=None):
        lo = i + 1 if after is None else after + 1
        s = slice(lo, self.size)
        n, tol = self._n[s], 3.0 * self.tol
        m = self._alive[s] & (np.abs(n @ self._n[i]) >= 0.999)
        m &= np.abs((self._p0[s] - self._p0[i]) @ self._n[i]) <= tol
        m &= np.all(self._lo[s] <= self._hi[i] + tol, axis=1) & np.all(self._hi[s] >= self._lo[i] - tol, axis=1)
        return np.nonzero(m)[0] + lo


def resolve_overlaps(polygons, tol: float = 1e-4):
    """Split coplanar polygons that overlap partly so shared parts become one polygon each.

    Two rooms whose walls only partly face each other (or the same wall modelled twice) end up with one shared
    piece plus the remainders; identical polygons collapse into one.
    """
    index = _CoplanarIndex(tol)
    work: dict[int, list] = {}
    for poly in polygons:
        if len(poly) >= 3:
            work[index.add(poly)] = [tuple(p) for p in poly]
    i = 0
    while i < index.size:
        if i not in work:
            i += 1
            continue
        pa = work[i]
        na = polygon_normal_unit(pa)
        hit = None
        for j in index.candidates(i).tolist():
            pb = work[j]
            nb = polygon_normal_unit(pb)
            res = split_overlap(pa, pb if sum(x * y for x, y in zip(na, nb)) < 0 else pb[::-1], tol)
            if res is not None:
                hit = j, res
                break
        if hit is None:
            i += 1
            continue
        j, res = hit
        for k in (i, j):
            del work[k]
            index.remove(k)
        for poly in res["and_a"][:1] + res["and_a"][1:] + res["a_only"] + res["b_only"]:
            work[index.add(poly)] = poly
    return [work[k] for k in sorted(work)]


def _split_t_junctions(pts, polys, tol):
    P = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
    edges = {(min(a, b), max(a, b)) for poly in polys for a, b in zip(poly, poly[1:] + poly[:1])}
    inserts: dict[tuple[int, int], list[int]] = {}
    for a, b in edges:
        ab = P[b] - P[a]
        l2 = float(ab @ ab)
        if l2 <= tol * tol:
            continue
        lo, hi = np.minimum(P[a], P[b]) - tol, np.maximum(P[a], P[b]) + tol
        cand = np.nonzero(np.all((P >= lo) & (P <= hi), axis=1))[0]
        cand = cand[(cand != a) & (cand != b)]
        if not len(cand):
            continue
        t = (P[cand] - P[a]) @ ab / l2
        d = np.linalg.norm(P[cand] - (P[a] + t[:, None] * ab), axis=1)
        ok = (d <= tol) & (t > 0) & (t < 1)
        if ok.any():
            order = np.argsort(t[ok])
            inserts[(a, b)] = cand[ok][order].tolist()  # ascending from a to b
    if not inserts:
        return polys
    out = []
    for poly in polys:
        new = []
        for a, b in zip(poly, poly[1:] + poly[:1]):
            new.append(a)
            mid = inserts.get((a, b) if a < b else (b, a))
            if mid:
                new += mid if a < b else mid[::-1]
        out.append(new)
    return out


def _signed_volume(polys_pts) -> float:
    if not polys_pts:
        return 0.0
    o = np.mean([p for poly in polys_pts for p in poly], axis=0)
    v = 0.0
    for poly in polys_pts:
        q = np.asarray(poly, dtype=np.float64) - o
        for i in range(1, len(q) - 1):
            v += float(q[0] @ np.cross(q[i], q[i + 1])) / 6.0
    return v


def _merge_coplanar(polys, ntol=1e-3, dtol=1e-3):
    """Join edge-connected coplanar polygons of one cell (same winding) into single polygons."""
    if len(polys) < 2:
        return polys
    pts: list[Vec] = []
    tris = []
    for poly in polys:
        base = len(pts)
        pts += list(poly)
        tris += [(base, base + i, base + i + 1) for i in range(1, len(poly) - 1)]
    uniq, remap = weld(pts, 1e-6)
    tris = [(remap[a], remap[b], remap[c]) for a, b, c in tris]
    tris = [t for t in tris if len(set(t)) == 3]
    out = []
    for cluster in cluster_coplanar(uniq, tris, ntol, dtol):
        loops = boundary_loops(cluster)
        if not loops:
            continue
        best = max(loops, key=lambda lp: polygon_area([uniq[i] for i in lp]))
        out.append([uniq[i] for i in best])
    return out


def split_into_cells(points, faces, tol: float = 1e-4, merge_coplanar: bool = True, keep_open: bool = True):
    """Mesh -> ``[{"polys": [polygon, ...], "open": bool}]``, one entry per enclosed volume.

    ``points``: [(x, y, z)], ``faces``: vertex index lists. The polygons of a closed cell face outwards. Open
    components (the mesh has a hole there) are returned with ``open=True`` and their polygons as modelled, flipped as
    a whole when that makes the signed volume positive; they are left out when ``keep_open`` is false.
    """
    pts0 = [tuple(map(float, p)) for p in points]
    pts, polys = _index_polygons([[pts0[i] for i in f] for f in faces], tol)
    if not polys:
        return []
    pts, polys = _index_polygons(resolve_overlaps([[pts[i] for i in poly] for poly in polys], tol), tol)
    if not polys:
        return []
    polys = _split_t_junctions(pts, polys, tol)
    P = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
    nrm = [unit(polygon_normal([pts[i] for i in poly])) for poly in polys]

    parent = list(range(2 * len(polys)))  # node 2*f = front side (normal side), 2*f+1 = back side

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    edges: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for fi, poly in enumerate(polys):
        for a, b in zip(poly, poly[1:] + poly[:1]):
            edges[(a, b) if a < b else (b, a)].append((fi, 1 if a < b else -1))

    for (a, b), users in edges.items():
        e = P[b] - P[a]
        e /= np.linalg.norm(e)
        ins = []
        for fi, s in users:
            w = s * np.cross(np.asarray(nrm[fi]), e)  # in-plane, pointing into the face
            ins.append(w)
        u = ins[0]
        v = np.cross(e, u)
        ang = [math.atan2(float(w @ v), float(w @ u)) for w in ins]
        order = sorted(range(len(users)), key=lambda k: ang[k])
        for k in range(len(order)):
            i, j = order[k], order[(k + 1) % len(order)]
            fi, fj = users[i][0], users[j][0]
            # the wedge after face i (counter-clockwise about the edge) and the one before face j
            di = np.cross(e, ins[i])
            dj = -np.cross(e, ins[j])
            ni = 2 * fi + (0 if float(di @ np.asarray(nrm[fi])) > 0 else 1)
            nj = 2 * fj + (0 if float(dj @ np.asarray(nrm[fj])) > 0 else 1)
            union(ni, nj)

    comps: dict[int, list[int]] = defaultdict(list)
    for node in range(2 * len(polys)):
        comps[find(node)].append(node)

    cells = []
    for nodes in comps.values():
        faces_in = {n // 2 for n in nodes}
        is_open = len(faces_in) < len(nodes)  # a face has both sides here
        oriented = []
        for n in nodes:
            poly = [pts[i] for i in polys[n // 2]]
            oriented.append(poly[::-1] if n % 2 == 0 else poly)  # cell on the front side -> outward = -normal
        if is_open:
            if not keep_open:
                continue
            oriented = [[pts[i] for i in polys[f]] for f in sorted(faces_in)]
            if _signed_volume(oriented) < 0:
                oriented = [p[::-1] for p in oriented]
        elif _signed_volume(oriented) <= tol**3:
            continue  # the unbounded outside (or a sliver)
        if merge_coplanar:
            oriented = _merge_coplanar(oriented)
        oriented = [s for s in (simplify(p, tol) for p in oriented) if len(s) >= 3 and polygon_area(s) > tol * tol]
        if len(oriented) >= 4:
            cells.append({"polys": oriented, "open": is_open})
    return cells


def order_cells(cells, tol: float = 1e-2):
    """Reading order and stories: ``[(cell, story_index)]``, cells sorted by story (lowest first), then y, then x.

    A story is a group of cells whose lowest points are within ``tol`` of each other.
    """
    info = []
    for c in cells:
        allp = [p for poly in c["polys"] for p in poly]
        info.append((c, min(p[2] for p in allp), min(p[1] for p in allp), min(p[0] for p in allp)))
    stories: list[float] = []
    for z in sorted(i[1] for i in info):
        if not stories or z - stories[-1] > tol:
            stories.append(z)

    def story_of(z):
        return min(range(len(stories)), key=lambda k: abs(stories[k] - z))

    out = [(c, story_of(z)) for c, z, _y, _x in info]
    pos = {id(c): (y, x) for c, _z, y, x in info}
    out.sort(key=lambda t: (t[1], *pos[id(t[0])]))
    return out
