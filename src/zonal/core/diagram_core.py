"""Create Spaces From Diagram: extrude horizontal floor-plan polygons into spaces (no bpy, no openstudio).

The SketchUp plugin's `SpaceDiagramInterface`: every selected horizontal face becomes one space per floor
(`floor_height` high, `num_floors` floors), made of a floor, a roof / ceiling and one wall per edge.
"""

from __future__ import annotations

from .geometry import Vec, polygon_normal, simplify, unit

HORIZONTAL_TOL = 1e-3  # |nz| of a face normal must exceed 1 - this to count as horizontal


def is_horizontal(poly) -> bool:
    return abs(unit(polygon_normal(poly))[2]) > 1.0 - HORIZONTAL_TOL


def sort_key(poly) -> tuple[float, float]:
    """Plugin order: by the lowest y, then the lowest x of the polygon's vertices (stable, readable names)."""
    return (min(p[1] for p in poly), min(p[0] for p in poly))


def extrude_plan(poly, floor_height: float, num_floors: int = 1):
    """One horizontal polygon -> list of floors, each ``[(label, surface_type, polygon), ...]`` (outward facing).

    The plan may be wound either way; floors face down, roofs up and walls away from the plan. Raises ValueError
    for a non-horizontal or degenerate polygon, a non-positive height or fewer than one floor.
    """
    if floor_height <= 0 or num_floors < 1:
        raise ValueError("floor height must be positive and at least one floor is needed")
    pts = simplify([tuple(map(float, p)) for p in poly])
    if len(pts) < 3 or not is_horizontal(pts):
        raise ValueError("the floor plan must be a horizontal polygon")
    z0 = sum(p[2] for p in pts) / len(pts)
    ccw = [(p[0], p[1], z0) for p in pts]
    if polygon_normal(ccw)[2] < 0:
        ccw.reverse()  # counter-clockwise seen from above: normal +z
    floors = []
    for f in range(num_floors):
        zb, zt = z0 + f * floor_height, z0 + (f + 1) * floor_height
        bottom = [(x, y, zb) for x, y, _ in ccw]
        top = [(x, y, zt) for x, y, _ in ccw]
        surfaces: list[tuple[str, str, list[Vec]]] = [("Floor", "Floor", bottom[::-1]), ("Roof", "RoofCeiling", top)]
        n = len(ccw)
        for i in range(n):
            j = (i + 1) % n
            surfaces.append((f"Wall {i + 1}", "Wall", [bottom[i], bottom[j], top[j], top[i]]))
        floors.append(surfaces)
    return floors


def plan_spaces(polys, floor_height: float, num_floors: int = 1):
    """All plans -> ``[{"name", "story", "surfaces"}]`` named ``Story <f> Space <k>`` (k in plugin sort order)."""
    spaces = []
    for k, poly in enumerate(sorted(polys, key=sort_key), start=1):
        for f, surfaces in enumerate(extrude_plan(poly, floor_height, num_floors), start=1):
            spaces.append({"name": f"Story {f} Space {k}", "story": f"Story {f}", "surfaces": surfaces})
    return spaces
