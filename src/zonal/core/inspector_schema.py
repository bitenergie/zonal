"""Field definitions of the OpenStudio Inspector (pure Python: no bpy, no openstudio).

Two kinds of inspector types:
* ``backing == "object"``: the "object" is a Blender mesh object; its fields are the object custom properties the
  exporter already reads (shown through the `osm_<key>` proxies of blender_props).
* ``backing == "store"``: the object lives in ``Scene.osm_model`` (one item per OpenStudio object); the items are
  serialised to the scene custom property ``osmModel`` (JSON) on export and applied by `osm_builder`.

Field kinds: string, float, int, bool, enum, ref (name of another object, see ``ref``), autocalc (float that may be
"hard sized"; stored as ``<key>`` + ``<key>_hard``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

STRING, FLOAT, INT, BOOL, ENUM, REF, AUTOCALC = "string", "float", "int", "bool", "enum", "ref", "autocalc"
RULES = "rules"  # JSON rule list of a ScheduleRuleset (see schedule_rules; "" = plain constant schedule)
COLOR = "color"  # "#RRGGBB" string ("" = not set); the Blender side edits it with a colour picker
FILE = "file"  # string holding a file path (made absolute by the Blender side on export)

# ref targets are library categories (see tools_core.LIBRARY_CATEGORIES / INSPECTOR_CATEGORIES)
ROUGHNESS = ("VeryRough", "Rough", "MediumRough", "MediumSmooth", "Smooth", "VerySmooth")
CONVECTION_INSIDE = ("Simple", "TARP", "CeilingDiffuser", "AdaptiveConvectionAlgorithm", "TrombeWall")
CONVECTION_OUTSIDE = ("SimpleCombined", "TARP", "DOE-2", "MoWiTT", "AdaptiveConvectionAlgorithm")


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    kind: str = STRING
    unit: str = ""
    ref: str = ""  # library category for REF fields
    choices: tuple = ()  # ENUM
    default: object = None  # None -> kind default ("" / 0 / False)
    setter: str = ""  # OpenStudio method; "" = not exported through the generic setter table
    readonly: bool = False  # shown, never exported

    @property
    def default_value(self):
        if self.default is not None:
            return self.default
        return {
            STRING: "",
            REF: "",
            ENUM: "",
            FILE: "",
            COLOR: "",
            RULES: "",
            FLOAT: 0.0,
            INT: 0,
            BOOL: False,
            AUTOCALC: 0.0,
        }[self.kind]


@dataclass(frozen=True)
class TypeDef:
    name: str  # OpenStudio class without the OS: prefix
    label: str
    group: str  # Geometry / HVAC / Resources
    backing: str  # "object" | "store"
    category: str = ""  # library category this type's names belong to ("" = none)
    fields: tuple = field(default_factory=tuple)
    surface_class: str = ""  # object-backed: search_core class


def _f(key, label, kind=STRING, **kw):
    return FieldDef(key, label, kind, **kw)


def _ref(key, label, ref, setter):
    return FieldDef(key, label, REF, ref=ref, setter=setter)


def hex_to_rgb(text: str):
    """ "#RRGGBB" -> (r, g, b) floats 0..1, None when malformed."""
    text = (text or "").lstrip("#")
    try:
        if len(text) != 6:
            return None
        return tuple(int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None


def rgb_to_hex(rgb) -> str:
    """(r, g, b) floats 0..1 -> "#RRGGBB" (values are clamped)."""
    return "#" + "".join(f"{round(min(1.0, max(0.0, float(c))) * 255):02X}" for c in rgb[:3])


_COLOR_FIELD = FieldDef("renderingColor", "Color", COLOR, setter="setRenderingColor")

# ThermalZone setpoint schedules: key -> (label, owner object, setter on that object). Heating / cooling go to a
# ThermostatSetpointDualSetpoint, humidifying / dehumidifying to a ZoneControlHumidistat.
ZONE_SETPOINTS = {
    "heatingSetpoint": ("Heating Setpoint Schedule", "thermostat", "setHeatingSetpointTemperatureSchedule"),
    "coolingSetpoint": ("Cooling Setpoint Schedule", "thermostat", "setCoolingSetpointTemperatureSchedule"),
    "humidifyingSetpoint": (
        "Humidifying Setpoint Schedule",
        "humidistat",
        "setHumidifyingRelativeHumiditySetpointSchedule",
    ),
    "dehumidifyingSetpoint": (
        "Dehumidifying Setpoint Schedule",
        "humidistat",
        "setDehumidifyingRelativeHumiditySetpointSchedule",
    ),
}
ZONE_SETPOINT_FIELDS = tuple(FieldDef(k, label, REF, ref="schedule") for k, (label, _o, _s) in ZONE_SETPOINTS.items())

_SPACE_FIELDS = (
    _ref("spaceType", "Space Type", "spacetype", "setSpaceType"),
    _ref("defaultConstructionSet", "Default Construction Set", "cset", "setDefaultConstructionSet"),
    _ref("defaultScheduleSet", "Default Schedule Set", "scheduleset", "setDefaultScheduleSet"),
)

_ABSORPTANCE_FIELDS = (
    _f("thermalAbsorptance", "Thermal Absorptance", FLOAT, default=0.9, setter="setThermalAbsorptance"),
    _f("solarAbsorptance", "Solar Absorptance", FLOAT, default=0.7, setter="setSolarAbsorptance"),
    _f("visibleAbsorptance", "Visible Absorptance", FLOAT, default=0.7, setter="setVisibleAbsorptance"),
)
MAX_LAYERS = 10  # layer1 (outside) .. layer10
_LAYER_FIELDS = tuple(
    _ref(f"layer{i}", "Outside Layer" if i == 1 else f"Layer {i}", "material", "") for i in range(1, MAX_LAYERS + 1)
)
# material categories (each material type has its own name list; layer dropdowns offer the union)
MATERIAL_CATEGORIES = ("opaquemat", "masslessmat", "airgap", "glazing")

TYPES: dict[str, TypeDef] = {
    t.name: t
    for t in (
        # --- geometry, object backed -------------------------------------------------------
        TypeDef("Surface", "Surface", "Geometry", "object", surface_class="Surface"),
        TypeDef("SubSurface", "SubSurface", "Geometry", "object", surface_class="SubSurface"),
        TypeDef("ShadingSurface", "ShadingSurface", "Geometry", "object", surface_class="ShadingSurface"),
        TypeDef(
            "InteriorPartitionSurface",
            "InteriorPartitionSurface",
            "Geometry",
            "object",
            surface_class="InteriorPartitionSurface",
        ),
        # --- geometry, store backed --------------------------------------------------------
        TypeDef(
            "Building",
            "Building",
            "Geometry",
            "store",
            fields=(
                _f("northAxis", "North Axis", FLOAT, unit="deg"),  # scene custom property, see blender_inspector
                *_SPACE_FIELDS,
                _f("standardsTemplate", "Standards Template", setter="setStandardsTemplate"),
            ),
        ),
        TypeDef(
            "Space",
            "Space",
            "Geometry",
            "store",
            fields=(
                *_SPACE_FIELDS,
                _f("relativeNorth", "Direction of Relative North", FLOAT, unit="deg"),
                _f("xOrigin", "X Origin", FLOAT, unit="m"),
                _f("yOrigin", "Y Origin", FLOAT, unit="m"),
                _f("zOrigin", "Z Origin", FLOAT, unit="m"),
                _ref("buildingStory", "Building Story", "story", "setBuildingStory"),
                _ref("thermalZone", "Thermal Zone", "zone", "setThermalZone"),
                _f(
                    "partOfTotalFloorArea",
                    "Part of Total Floor Area",
                    BOOL,
                    default=True,
                    setter="setPartofTotalFloorArea",
                ),
                _ref("buildingUnit", "Building Unit", "unit", "setBuildingUnit"),
                _f("volume", "Volume", AUTOCALC, unit="m³", setter="setVolume"),
                _f("ceilingHeight", "Ceiling Height", AUTOCALC, unit="m", setter="setCeilingHeight"),
                _f("floorArea", "Floor Area", AUTOCALC, unit="m²", setter="setFloorArea"),
            ),
        ),
        # --- simulation --------------------------------------------------------------------
        TypeDef(
            "WeatherFile",
            "WeatherFile",
            "Simulation",
            "store",
            fields=(
                _f("epwPath", "Weather File (.epw)", FILE),  # loads location + data; the fields below override it
                _f("city", "City", setter="setCity"),
                _f("stateProvinceRegion", "State / Province / Region", setter="setStateProvinceRegion"),
                _f("country", "Country", setter="setCountry"),
                _f("dataSource", "Data Source", setter="setDataSource"),
                _f("wmoNumber", "WMO Number", setter="setWMONumber"),
                _f("latitude", "Latitude", FLOAT, unit="deg", setter="setLatitude"),
                _f("longitude", "Longitude", FLOAT, unit="deg", setter="setLongitude"),
                _f("elevation", "Elevation", FLOAT, unit="m", setter="setElevation"),
                _f("timeZone", "Time Zone", FLOAT, unit="h", setter="setTimeZone"),
            ),
        ),
        # --- HVAC --------------------------------------------------------------------------
        TypeDef(
            "ThermalZone",
            "ThermalZone",
            "HVAC",
            "store",
            category="zone",
            fields=(
                _COLOR_FIELD,
                _f("multiplier", "Multiplier", INT, default=1, setter="setMultiplier"),
                # air loops serve a zone (zone.airLoopHVACs()); comma separated names, applied by osm_builder
                _f("airLoops", "Air Loops (comma separated)"),
                # ideal air loads + setpoint schedules: no generic setter, applied by osm_builder.apply_zone_hvac
                _f("idealAirLoads", "Ideal Air Loads", BOOL),
                *ZONE_SETPOINT_FIELDS,
                _f("zoneCeilingHeight", "Ceiling Height", AUTOCALC, unit="m", setter="setCeilingHeight"),
                _f("zoneVolume", "Volume", AUTOCALC, unit="m³", setter="setVolume"),
                _f(
                    "insideConvection",
                    "Zone Inside Convection Algorithm",
                    ENUM,
                    choices=CONVECTION_INSIDE,
                    setter="setZoneInsideConvectionAlgorithm",
                ),
                _f(
                    "outsideConvection",
                    "Zone Outside Convection Algorithm",
                    ENUM,
                    choices=CONVECTION_OUTSIDE,
                    setter="setZoneOutsideConvectionAlgorithm",
                ),
            ),
        ),
        # --- resources ---------------------------------------------------------------------
        TypeDef(
            "BuildingStory",
            "BuildingStory",
            "Resources",
            "store",
            category="story",
            fields=(
                _COLOR_FIELD,
                _ref("storyConstructionSet", "Default Construction Set", "cset", "setDefaultConstructionSet"),
                _ref("storyScheduleSet", "Default Schedule Set", "scheduleset", "setDefaultScheduleSet"),
                _f("nominalZCoordinate", "Nominal Z Coordinate", FLOAT, unit="m", setter="setNominalZCoordinate"),
                _f(
                    "nominalFloorToCeilingHeight",
                    "Nominal Floor to Ceiling Height",
                    FLOAT,
                    unit="m",
                    setter="setNominalFloortoCeilingHeight",
                ),
            ),
        ),
        TypeDef("BuildingUnit", "BuildingUnit", "Resources", "store", category="unit", fields=(_COLOR_FIELD,)),
        TypeDef(
            "SpaceType",
            "SpaceType",
            "Resources",
            "store",
            category="spacetype",
            fields=(
                _COLOR_FIELD,
                _ref("typeConstructionSet", "Default Construction Set", "cset", "setDefaultConstructionSet"),
                _ref("typeScheduleSet", "Default Schedule Set", "scheduleset", "setDefaultScheduleSet"),
                _f("typeStandardsTemplate", "Standards Template", setter="setStandardsTemplate"),
                _f("standardsBuildingType", "Standards Building Type", setter="setStandardsBuildingType"),
                _f("standardsSpaceType", "Standards Space Type", setter="setStandardsSpaceType"),
            ),
        ),
        # --- constructions, materials, schedules ---------------------------------------------
        TypeDef(
            "Construction", "Construction", "Constructions", "store", category="construction", fields=_LAYER_FIELDS
        ),
        TypeDef(
            "StandardOpaqueMaterial",
            "StandardOpaqueMaterial",
            "Constructions",
            "store",
            category="opaquemat",
            fields=(
                _f("roughness", "Roughness", ENUM, choices=ROUGHNESS, setter="setRoughness"),
                _f("thickness", "Thickness", FLOAT, unit="m", default=0.1, setter="setThickness"),
                _f("conductivity", "Conductivity", FLOAT, unit="W/m-K", default=0.1, setter="setConductivity"),
                _f("density", "Density", FLOAT, unit="kg/m³", default=0.1, setter="setDensity"),
                _f("specificHeat", "Specific Heat", FLOAT, unit="J/kg-K", default=1400.0, setter="setSpecificHeat"),
                *_ABSORPTANCE_FIELDS,
            ),
        ),
        TypeDef(
            "MasslessOpaqueMaterial",
            "MasslessOpaqueMaterial",
            "Constructions",
            "store",
            category="masslessmat",
            fields=(
                _f("roughness", "Roughness", ENUM, choices=ROUGHNESS, setter="setRoughness"),
                _f(
                    "thermalResistance",
                    "Thermal Resistance",
                    FLOAT,
                    unit="m²-K/W",
                    default=0.1,
                    setter="setThermalResistance",
                ),
                *_ABSORPTANCE_FIELDS,
            ),
        ),
        TypeDef(
            "AirGap",
            "AirGap",
            "Constructions",
            "store",
            category="airgap",
            fields=(
                _f(
                    "thermalResistance",
                    "Thermal Resistance",
                    FLOAT,
                    unit="m²-K/W",
                    default=0.1,
                    setter="setThermalResistance",
                ),
            ),
        ),
        TypeDef(
            "SimpleGlazing",
            "SimpleGlazing",
            "Constructions",
            "store",
            category="glazing",
            fields=(
                _f("uFactor", "U-Factor", FLOAT, unit="W/m²-K", default=0.1, setter="setUFactor"),
                _f(
                    "solarHeatGainCoefficient",
                    "Solar Heat Gain Coefficient",
                    FLOAT,
                    default=0.1,
                    setter="setSolarHeatGainCoefficient",
                ),
                _f(
                    "visibleTransmittance",
                    "Visible Transmittance",
                    FLOAT,
                    default=0.0,
                    setter="setVisibleTransmittance",
                ),
            ),
        ),
        # shades are applied through shading controls, not as construction layers: not in MATERIAL_CATEGORIES
        TypeDef(
            "Shade",
            "Shade",
            "Constructions",
            "store",
            category="shade",
            fields=(
                _f(
                    "shadeSolarTransmittance", "Solar Transmittance", FLOAT, default=0.4, setter="setSolarTransmittance"
                ),
                _f("shadeSolarReflectance", "Solar Reflectance", FLOAT, default=0.5, setter="setSolarReflectance"),
                _f(
                    "shadeVisibleTransmittance",
                    "Visible Transmittance",
                    FLOAT,
                    default=0.4,
                    setter="setVisibleTransmittance",
                ),
                _f(
                    "shadeVisibleReflectance", "Visible Reflectance", FLOAT, default=0.5, setter="setVisibleReflectance"
                ),
                _f(
                    "shadeEmissivity",
                    "Thermal Hemispherical Emissivity",
                    FLOAT,
                    default=0.9,
                    setter="setThermalHemisphericalEmissivity",
                ),
                _f(
                    "shadeThermalTransmittance",
                    "Thermal Transmittance",
                    FLOAT,
                    default=0.0,
                    setter="setThermalTransmittance",
                ),
                _f("shadeThickness", "Thickness", FLOAT, unit="m", default=0.005, setter="setThickness"),
                _f("shadeConductivity", "Conductivity", FLOAT, unit="W/m-K", default=0.1, setter="setConductivity"),
                _f(
                    "shadeToGlassDistance",
                    "Shade to Glass Distance",
                    FLOAT,
                    unit="m",
                    default=0.05,
                    setter="setShadetoGlassDistance",
                ),
                _f("shadeTopOpening", "Top Opening Multiplier", FLOAT, default=0.0, setter="setTopOpeningMultiplier"),
                _f(
                    "shadeBottomOpening",
                    "Bottom Opening Multiplier",
                    FLOAT,
                    default=0.0,
                    setter="setBottomOpeningMultiplier",
                ),
                _f(
                    "shadeLeftOpening",
                    "Left Side Opening Multiplier",
                    FLOAT,
                    default=0.0,
                    setter="setLeftSideOpeningMultiplier",
                ),
                _f(
                    "shadeRightOpening",
                    "Right Side Opening Multiplier",
                    FLOAT,
                    default=0.0,
                    setter="setRightSideOpeningMultiplier",
                ),
                _f(
                    "shadeAirflowPermeability",
                    "Airflow Permeability",
                    FLOAT,
                    default=0.0,
                    setter="setAirflowPermeability",
                ),
            ),
        ),
        TypeDef(
            "ScheduleConstant",
            "ScheduleConstant",
            "Schedules",
            "store",
            category="schedule",
            fields=(
                _f("scheduleValue", "Value", FLOAT, setter="setValue"),
                _f("rules", "Rules", RULES),  # no setter: the builder makes a ScheduleRuleset from it
            ),
        ),
        TypeDef(
            "DefaultConstructionSet",
            "DefaultConstructionSet",
            "Resources",
            "store",
            category="cset",
            fields=(
                _ref(
                    "exteriorSurfaces",
                    "Default Exterior Surface Constructions",
                    "defsurf",
                    "setDefaultExteriorSurfaceConstructions",
                ),
                _ref(
                    "interiorSurfaces",
                    "Default Interior Surface Constructions",
                    "defsurf",
                    "setDefaultInteriorSurfaceConstructions",
                ),
                _ref(
                    "groundContactSurfaces",
                    "Default Ground Contact Surface Constructions",
                    "defsurf",
                    "setDefaultGroundContactSurfaceConstructions",
                ),
                _ref(
                    "exteriorSubSurfaces",
                    "Default Exterior SubSurface Constructions",
                    "defsubsurf",
                    "setDefaultExteriorSubSurfaceConstructions",
                ),
                _ref(
                    "interiorSubSurfaces",
                    "Default Interior SubSurface Constructions",
                    "defsubsurf",
                    "setDefaultInteriorSubSurfaceConstructions",
                ),
                _ref(
                    "interiorPartitionConstruction",
                    "Interior Partition Construction",
                    "construction",
                    "setInteriorPartitionConstruction",
                ),
                _ref(
                    "spaceShadingConstruction",
                    "Space Shading Construction",
                    "construction",
                    "setSpaceShadingConstruction",
                ),
                _ref(
                    "buildingShadingConstruction",
                    "Building Shading Construction",
                    "construction",
                    "setBuildingShadingConstruction",
                ),
                _ref(
                    "siteShadingConstruction", "Site Shading Construction", "construction", "setSiteShadingConstruction"
                ),
                _ref(
                    "adiabaticConstruction",
                    "Adiabatic Surface Construction",
                    "construction",
                    "setAdiabaticSurfaceConstruction",
                ),
            ),
        ),
        TypeDef(
            "DefaultScheduleSet",
            "DefaultScheduleSet",
            "Resources",
            "store",
            category="scheduleset",
            fields=(
                _ref("hoursOfOperation", "Hours of Operation Schedule", "schedule", "setHoursofOperationSchedule"),
                _ref("numberOfPeople", "Number of People Schedule", "schedule", "setNumberofPeopleSchedule"),
                _ref(
                    "peopleActivityLevel",
                    "People Activity Level Schedule",
                    "schedule",
                    "setPeopleActivityLevelSchedule",
                ),
                _ref("lighting", "Lighting Schedule", "schedule", "setLightingSchedule"),
                _ref("electricEquipment", "Electric Equipment Schedule", "schedule", "setElectricEquipmentSchedule"),
                _ref("gasEquipment", "Gas Equipment Schedule", "schedule", "setGasEquipmentSchedule"),
                _ref("hotWaterEquipment", "Hot Water Equipment Schedule", "schedule", "setHotWaterEquipmentSchedule"),
                _ref("infiltration", "Infiltration Schedule", "schedule", "setInfiltrationSchedule"),
                _ref("steamEquipment", "Steam Equipment Schedule", "schedule", "setSteamEquipmentSchedule"),
                _ref("otherEquipment", "Other Equipment Schedule", "schedule", "setOtherEquipmentSchedule"),
            ),
        ),
        TypeDef(
            "DefaultSurfaceConstructions",
            "DefaultSurfaceConstructions",
            "Resources",
            "store",
            category="defsurf",
            fields=(
                _ref("floorConstruction", "Floor Construction", "construction", "setFloorConstruction"),
                _ref("wallConstruction", "Wall Construction", "construction", "setWallConstruction"),
                _ref(
                    "roofCeilingConstruction", "Roof Ceiling Construction", "construction", "setRoofCeilingConstruction"
                ),
            ),
        ),
        TypeDef(
            "DefaultSubSurfaceConstructions",
            "DefaultSubSurfaceConstructions",
            "Resources",
            "store",
            category="defsubsurf",
            fields=(
                _ref("fixedWindow", "Fixed Window Construction", "construction", "setFixedWindowConstruction"),
                _ref("operableWindow", "Operable Window Construction", "construction", "setOperableWindowConstruction"),
                _ref("door", "Door Construction", "construction", "setDoorConstruction"),
                _ref("glassDoor", "Glass Door Construction", "construction", "setGlassDoorConstruction"),
                _ref("overheadDoor", "Overhead Door Construction", "construction", "setOverheadDoorConstruction"),
                _ref("skylight", "Skylight Construction", "construction", "setSkylightConstruction"),
                _ref(
                    "tubularDome",
                    "Tubular Daylight Dome Construction",
                    "construction",
                    "setTubularDaylightDomeConstruction",
                ),
                _ref(
                    "tubularDiffuser",
                    "Tubular Daylight Diffuser Construction",
                    "construction",
                    "setTubularDaylightDiffuserConstruction",
                ),
            ),
        ),
    )
}

GROUPS = ("Geometry", "Simulation", "HVAC", "Resources", "Constructions", "Schedules")
# Singletons have exactly one object (fixed name); spaces are Blender collections
SINGLETONS = {"Building": "Building", "WeatherFile": "Weather File"}
NOT_CREATABLE = (*SINGLETONS, "Space")
BUILDING_NAME = SINGLETONS["Building"]

# Object-backed types: (custom property key, label) in display order, shown through the osm_<key> proxies
OBJECT_FIELDS = {
    "Surface": (
        ("surfaceType", "Surface Type"),
        ("constructionName", "Construction"),
        ("spaceName", "Space"),
        ("outsideBoundaryCondition", "Outside Boundary Condition"),
        ("outsideBoundaryConditionObjectName", "Outside Boundary Condition Object"),
        ("sunExposure", "Sun Exposure"),
        ("windExposure", "Wind Exposure"),
    ),
    "SubSurface": (
        ("surfaceType", "Sub Surface Type"),
        ("constructionName", "Construction"),
        ("surfaceName", "Surface Name"),
        ("outsideBoundaryConditionObjectName", "Outside Boundary Condition Object"),
    ),
    "ShadingSurface": (
        ("surfaceType", "Shading Surface Type"),
        ("constructionName", "Construction"),
        ("spaceName", "Space"),
    ),
    "InteriorPartitionSurface": (("constructionName", "Construction"), ("spaceName", "Space")),
}

# field keys must mean the same thing wherever they are used (one RNA property per key)
ALL_FIELDS: dict[str, FieldDef] = {}
for _t in TYPES.values():
    for _fd in _t.fields:
        assert ALL_FIELDS.setdefault(_fd.key, _fd) == _fd, f"conflicting field definition for {_fd.key}"


def type_for_category(category: str) -> str:
    return next((t.name for t in TYPES.values() if t.category == category), "")


def is_default(fd: FieldDef, values: dict) -> bool:
    """True when the value would not change the model (defaults are not exported)."""
    if fd.kind == AUTOCALC:
        return not values.get(f"{fd.key}_hard")
    return values.get(fd.key, fd.default_value) == fd.default_value


def export_fields(type_name: str, values: dict) -> dict:
    """The non-default fields of one object as a plain dict (``autocalc`` -> number only when hard sized)."""
    out = {}
    for fd in TYPES[type_name].fields:
        if fd.readonly or is_default(fd, values):
            continue
        out[fd.key] = values.get(fd.key, fd.default_value)
    return out


def dumps_model(objects: list[dict]) -> str:
    """``objects``: [{"type", "name", "values"}] -> JSON for the ``osmModel`` scene property."""
    out = []
    for o in objects:
        if o["type"] not in TYPES or TYPES[o["type"]].backing != "store":
            continue
        out.append({"type": o["type"], "name": o["name"], "fields": export_fields(o["type"], o["values"])})
    return json.dumps({"objects": out}, separators=(",", ":"))


def loads_model(text) -> list[dict]:
    """Inverse of `dumps_model` (tolerant: bad input -> [])."""
    try:
        data = json.loads(text) if isinstance(text, str) else (text or {})
        objs = data.get("objects", [])
    except (ValueError, AttributeError):
        return []
    return [o for o in objs if isinstance(o, dict) and o.get("type") in TYPES and o.get("name")]
