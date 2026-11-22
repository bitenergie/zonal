"""Text of the Info Tool tooltip (no bpy, no openstudio): what the SketchUp plugin's InfoTool shows on hover."""

from __future__ import annotations

from .search_core import azimuth_deg, tilt_deg

# (custom property, label) in display order; empty values are skipped
_PROPS = (
    ("spaceName", "Space"),
    ("thermalZoneName", "Thermal Zone"),
    ("spaceTypeName", "Space Type"),
    ("buildingStoryName", "Building Story"),
    ("buildingUnitName", "Building Unit"),
    ("constructionName", "Construction"),
    ("constructionSetName", "Construction Set"),
    ("surfaceName", "Parent Surface"),
    ("shadingControlName", "Shading Control"),
)


def _text(v) -> str:
    return v if isinstance(v, str) else ""


def describe(
    name: str,
    surface_type: str,
    cls: str,
    props: dict,
    area: float,
    normal,
    north_axis: float = 0.0,
    space_fallback: str = "",
) -> list[str]:
    """Tooltip lines for one surface: identity, assigned names, boundary conditions, area and orientation."""
    lines = [f"{cls or 'Surface'}: {name}", f"Type: {surface_type}"]
    values = {k: _text(props.get(k)) for k, _label in _PROPS}
    if not values["spaceName"] and space_fallback:
        values["spaceName"] = space_fallback  # the collection the object lives in
    for key, label in _PROPS:
        if values[key]:
            lines.append(f"{label}: {values[key]}")
    bc = _text(props.get("outsideBoundaryCondition"))
    if bc:
        other = _text(props.get("outsideBoundaryConditionObjectName"))
        lines.append(f"Boundary: {bc}" + (f" ({other})" if other else ""))
    for key, label in (("sunExposure", "Sun"), ("windExposure", "Wind")):
        if _text(props.get(key)):
            lines.append(f"{label}: {props[key]}")
    lines.append(f"Area: {area:.2f} m²")
    az = azimuth_deg(normal, north_axis)
    lines.append(f"Tilt: {tilt_deg(normal):.1f}°" + (f", Azimuth: {az:.1f}°" if az is not None else ""))
    return lines
