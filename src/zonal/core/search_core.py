"""Logic of the Surface Search panel (no openstudio)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import schema
from .tools_geometry import _area2d, _basis, _is_convex, _to2d, polygon_normal_unit

# SketchUp "Class" -> the surface types it contains
CLASSES = {
    "Surface": schema.BASE_SURFACE_TYPES,
    "SubSurface": schema.SUBSURFACE_TYPES,
    "ShadingSurface": tuple(schema.SHADING_TYPES),
    "InteriorPartitionSurface": (schema.PARTITION_TYPE,),
}
# Only base surfaces have a boundary condition / sun / wind exposure
CLASSES_WITH_BOUNDARY = ("Surface",)


def class_of(surface_type: str) -> str:
    for cls, types in CLASSES.items():
        if surface_type in types:
            return cls
    return ""


def azimuth_deg(normal, north_axis: float = 0.0) -> float | None:
    """Clockwise angle from north (+Y rotated by ``north_axis``) of the horizontal part of ``normal``.

    None for (nearly) horizontal faces, which have no orientation.
    """
    if math.hypot(normal[0], normal[1]) < 1e-6:
        return None
    return (math.degrees(math.atan2(normal[0], normal[1])) - north_axis) % 360.0


def tilt_deg(normal) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, normal[2]))))


def is_horizontal(normal, tol_deg: float = 1.0) -> bool:
    t = tilt_deg(normal)
    return t < tol_deg or t > 180.0 - tol_deg


def in_azimuth_range(az: float, lo: float, hi: float) -> bool:
    """Clockwise range lo..hi; lo > hi wraps through north (350..10)."""
    if lo <= hi:
        return lo <= az <= hi
    return az >= lo or az <= hi


def is_non_convex(pts3d) -> bool:
    if len(pts3d) < 4:
        return False
    n = polygon_normal_unit(pts3d)
    u, v = _basis(n)
    p2 = _to2d(pts3d, pts3d[0], u, v)
    if _area2d(p2) < 0:
        p2.reverse()
    return not _is_convex(p2, 1e-9)


@dataclass
class SearchCriteria:
    cls: str = ""  # "" = any class
    name: str = ""
    type: str = ""  # "" = any
    construction: str = ""
    boundary: str = ""
    sun: str = ""
    wind: str = ""
    az_from: float = 0.0
    az_to: float = 360.0
    exclude_horizontal: bool = False
    only_non_convex: bool = False
    min_vertices: int = 0  # 0 = off; otherwise "more than" this many
    tilt_from: float = 0.0  # tilt of the outward normal from up: roof 0, wall 90, floor 180
    tilt_to: float = 180.0
    story: str = ""  # building story name (case-insensitive, exact)
    invert: bool = False  # the faces of the chosen class that do NOT match
    invert_across_classes: bool = False  # every face (of any class) that does NOT match


def matches(c: SearchCriteria, f: dict) -> bool:
    """``f``: class, name, type, construction, boundary, sun, wind, story, normal, points (3D, world).

    Plugin semantics of the invert options: "invert" keeps the faces of the chosen class that fail the
    criteria, "invert across classes" keeps every face that fails them.
    """
    hit = _matches(c, f)
    if c.invert_across_classes:
        return not hit
    if c.invert:
        return not hit and (not c.cls or f["class"] == c.cls)
    return hit


def _matches(c: SearchCriteria, f: dict) -> bool:
    if c.cls and f["class"] != c.cls:
        return False
    if c.name and c.name.lower() not in f["name"].lower():
        return False
    if c.type and f["type"] != c.type:
        return False
    if c.construction and c.construction.lower() not in (f.get("construction") or "").lower():
        return False
    if c.boundary and f.get("boundary") != c.boundary:
        return False
    if c.sun and f.get("sun") != c.sun:
        return False
    if c.wind and f.get("wind") != c.wind:
        return False
    if c.story and c.story.lower() != (f.get("story") or "").lower():
        return False
    normal = f["normal"]
    if (c.tilt_from, c.tilt_to) != (0.0, 180.0) and not c.tilt_from <= tilt_deg(normal) <= c.tilt_to:
        return False
    if c.exclude_horizontal and is_horizontal(normal):
        return False
    if (c.az_from, c.az_to) != (0.0, 360.0):
        az = azimuth_deg(normal, f.get("north_axis", 0.0))
        if az is None or not in_azimuth_range(az, c.az_from, c.az_to):
            return False
    pts = f["points"]
    if c.min_vertices and len(pts) <= c.min_vertices:
        return False
    return not (c.only_non_convex and not is_non_convex(pts))
