"""Helpers for the modeling tools (no openstudio)."""

from __future__ import annotations

# Meshes in this collection are drawing aids ("loose geometry"): never exported, projected on demand
LOOSE_COLLECTION = "OpenStudio - Loose Geometry"

# Library categories: category -> (object custom property, label)
LIBRARY_CATEGORIES = {
    "zone": ("thermalZoneName", "Thermal Zone"),
    "spacetype": ("spaceTypeName", "Space Type"),
    "story": ("buildingStoryName", "Building Story"),
    "unit": ("buildingUnitName", "Building Unit"),
    "construction": ("constructionName", "Construction"),
    "cset": ("constructionSetName", "Construction Set"),
}

# Names that only feed the Inspector's reference dropdowns (no per-object custom property, no library panel box)
INSPECTOR_CATEGORIES = (
    "schedule",
    "scheduleset",
    "defsurf",
    "defsubsurf",
    "opaquemat",
    "masslessmat",
    "airgap",
    "glazing",
    "shade",
)

_TEMPLATE_IDD = {
    "OS:SpaceType": "spacetype",
    "OS:Construction": "construction",
    "OS:Construction:AirBoundary": "construction",
    "OS:DefaultConstructionSet": "cset",
    "OS:DefaultScheduleSet": "scheduleset",
    "OS:DefaultSurfaceConstructions": "defsurf",
    "OS:DefaultSubSurfaceConstructions": "defsubsurf",
    "OS:Material": "opaquemat",
    "OS:Material:NoMass": "masslessmat",
    "OS:Material:AirGap": "airgap",
    "OS:WindowMaterial:SimpleGlazingSystem": "glazing",
    "OS:WindowMaterial:Shade": "shade",
    "OS:Schedule:Ruleset": "schedule",
    "OS:Schedule:Constant": "schedule",
    "OS:Schedule:Compact": "schedule",
    "OS:Schedule:Year": "schedule",
    "OS:Schedule:FixedInterval": "schedule",
    "OS:Schedule:VariableInterval": "schedule",
}


def surface_type_from_normal(nz: float) -> str:
    """Same tilt thresholds as osm_builder._auto_type (normal z of the outward face)."""
    if nz > 0.707:
        return "RoofCeiling"
    if nz < -0.707:
        return "Floor"
    return "Wall"


def unique_name(base: str, existing) -> str:
    """`base` if free, else `base 1`, `base 2`, ... (OpenStudio style numbering)."""
    taken = set(existing)
    if base not in taken:
        return base
    i = 1
    while f"{base} {i}" in taken:
        i += 1
    return f"{base} {i}"


def scan_osm_names(text: str) -> dict[str, list[str]]:
    """Names of template objects in an .osm file, by library category (no openstudio needed).

    OSM is IDF text: an object is `OS:SpaceType,` then one field per line (`value,  !- Field`); the
    handle is field 1 and the name field 2.
    """
    out: dict[str, list[str]] = {c: [] for c in (*LIBRARY_CATEGORIES, *INSPECTOR_CATEGORIES)}
    for block in text.split(";"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        while lines and not lines[0].startswith("OS:"):  # trailing comment of the previous object
            lines.pop(0)
        if len(lines) < 3:
            continue
        cat = _TEMPLATE_IDD.get(lines[0].strip().rstrip(","))
        if cat:
            name = lines[2].split("!-")[0].strip().rstrip(",").strip()
            if name and name not in out[cat]:
                out[cat].append(name)
    return out


# --- extra template files (the plugin's "Import Constructions / Schedules / Space Types") -------------

IMPORT_CATEGORIES = {
    "CONSTRUCTION": ("Constructions", ["Construction"]),
    "SCHEDULE": ("Schedules", ["Schedule"]),
    "SPACE_TYPE": ("Space Types", ["SpaceType"]),
    "ALL": ("Everything", ["SpaceType", "DefaultConstructionSet", "Construction", "Schedule"]),
}


def encode_extra_templates(entries) -> str:
    """[(path, [types])] -> JSON text stored in a scene property."""
    import json

    return json.dumps([{"path": str(p), "types": list(t)} for p, t in entries])


def decode_extra_templates(text) -> list[tuple[str, list[str]]]:
    """Inverse of `encode_extra_templates`; anything malformed gives an empty list."""
    import json

    try:
        data = json.loads(text) if text else []
        return [(str(d["path"]), [str(t) for t in d["types"]]) for d in data]
    except (ValueError, TypeError, KeyError):
        return []
