"""Constants of the OpenStudio glTF `extras` schema (see reference/openstudio_gltf/schemas)."""

# Node extras keys (all strings unless noted)
NODE_KEYS = (
    "handle",
    "name",
    "surfaceType",
    "surfaceName",
    "surfaceHandle",
    "subSurfaceName",
    "subSurfaceHandle",
    "shadingName",
    "shadingHandle",
    "constructionName",
    "constructionHandle",
    "constructionMaterialName",
    "constructionSetName",
    "constructionSetHandle",
    "constructionSetMaterialName",
    "spaceName",
    "spaceHandle",
    "thermalZoneName",
    "thermalZoneHandle",
    "thermalZoneMaterialName",
    "spaceTypeName",
    "spaceTypeHandle",
    "spaceTypeMaterialName",
    "buildingStoryName",
    "buildingStoryHandle",
    "buildingStoryMaterialName",
    "buildingUnitName",
    "buildingUnitHandle",
    "buildingUnitMaterialName",
    "outsideBoundaryCondition",
    "outsideBoundaryConditionObjectName",
    "outsideBoundaryConditionObjectHandle",
    "sunExposure",
    "windExposure",
    "boundaryMaterialName",
    "surfaceTypeMaterialName",
    "airWall",  # bool
    "coincidentWithOutsideObject",  # bool
    "illuminanceSetpoint",  # number
    "airLoopHVACNames",
    "airLoopHVACHandles",
    "airLoopHVACMaterialNames",  # {"0": str, ...}
)

BASE_SURFACE_TYPES = ("Wall", "Floor", "RoofCeiling")
SUBSURFACE_TYPES = (
    "FixedWindow",
    "OperableWindow",
    "Door",
    "GlassDoor",
    "OverheadDoor",
    "Skylight",
    "TubularDaylightDome",
    "TubularDaylightDiffuser",
)
SHADING_TYPES = {"SiteShading": "Site", "BuildingShading": "Building", "SpaceShading": "Space"}
PARTITION_TYPE = "InteriorPartitionSurface"

ALL_SURFACE_TYPES = BASE_SURFACE_TYPES + SUBSURFACE_TYPES + tuple(SHADING_TYPES) + (PARTITION_TYPE,)


def indexed(obj) -> list:
    """glTF extras store arrays as {"0": a, "1": b}; accept real lists as well."""
    if obj is None:
        return []
    if isinstance(obj, dict):
        return [obj[k] for k in sorted(obj, key=lambda k: int(k) if str(k).isdigit() else 0)]
    if isinstance(obj, (list, tuple)):
        return list(obj)
    return [obj]
