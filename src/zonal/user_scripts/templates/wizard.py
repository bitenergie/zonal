"""On Demand Template Generators: port of SpaceTypeAndConstructionSetWizard.rb with its two generators.

Builds space types (loads, ventilation, schedules from `Master_Schedules.osm`) and default construction sets
(constructions + materials) from the DOE reference-building data in `OpenStudio_Standards.json`. The result lives in the
model; for the Blender scene it becomes the scene template (space types, construction sets, constructions), so the
export keeps them.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ...core.scripts_core import ArgDef, UserScript, register

RESOURCES = Path(__file__).parent / "resources"
STANDARDS_JSON = RESOURCES / "OpenStudio_Standards.json"
MASTER_SCHEDULES = RESOURCES / "Master_Schedules.osm"

DEFAULT_SPACE_TYPE = {
    "FullServiceRestaurant": "Dining",
    "Hospital": "PatRoom",
    "LargeHotel": "GuestRoom",
    "MidriseApartment": "Apartment",
    "Office": "OpenOffice",
    "Outpatient": "Exam",
    "PrimarySchool": "Classroom",
    "QuickServiceRestaurant": "Dining",
    "Retail": "Retail",
    "SecondarySchool": "Classroom",
    "SmallHotel": "GuestRoom",
    "StripMall": "WholeBuilding",
    "SuperMarket": "Sales/Produce",
    "Warehouse": "Bulk",
}
SHORT_BUILDING = {
    "FullServiceRestaurant": "FullSrvRest",
    "LargeHotel": "LrgHotel",
    "LargeOffice": "LrgOffice",
    "MediumOffice": "MedOffice",
    "Mid-riseApartment": "MidApt",
    "PrimarySchool": "PriSchl",
    "QuickServiceRestaurant": "QckSrvRest",
    "SecondarySchool": "SecSchl",
    "SmallHotel": "SmHotel",
    "SmallOffice": "SmOffice",
    "StripMall": "StMall",
    "SuperMarket": "SpMarket",
}


@lru_cache(maxsize=1)
def standards() -> dict:
    return json.loads(STANDARDS_JSON.read_text(encoding="utf-8"))


def make_name(template, clim, building_type, spc_type, climate_first: bool) -> str:
    clim = clim.replace("ClimateZone ", "CZ")
    if clim == "CZ1-8":
        clim = ""
    building_type = SHORT_BUILDING.get(building_type, building_type)
    parts = [template]
    if climate_first:  # construction sets: template - climate - building - type
        parts += [p for p in (clim, building_type, spc_type) if p]
    else:  # space types: template - building - type - climate
        parts += [p for p in (building_type, spc_type, clim) if p]
    return " - ".join(parts)


def climate_zone_in_set(climate_zone, climate_zone_set) -> bool:
    data = standards()["climate_zone_sets"].get(climate_zone_set)
    return bool(data and climate_zone in data.get("climate_zones", ()))


def find_climate_zone_set(table: dict, template, clim, building_type, spc_type):
    """The climate zone set of `table[template]` that holds this building / space type and contains `clim`."""
    result = None
    for czs, by_building in table.get(template, {}).items():
        if spc_type in by_building.get(building_type, {}):
            zones = standards()["climate_zone_sets"].get(czs, {}).get("climate_zones", ())
            if clim in zones and result is None:
                result = czs
    return result


def _conv(os, value, src, dst) -> float:
    return os.convert(float(value), src, dst).get()


def _nonzero(v) -> bool:
    return v is not None and v != 0


# --- space types ----------------------------------------------------------------------------


class SpaceTypeGenerator:
    def __init__(self, os, model):
        self.os, self.model = os, model
        self._library = None

    @property
    def library(self):
        if self._library is None:
            loaded = self.os.osversion.VersionTranslator().loadModel(self.os.path(str(MASTER_SCHEDULES)))
            if loaded.empty():
                raise RuntimeError("could not load the master schedule library")
            self._library = loaded.get()
        return self._library

    def schedule(self, name):
        existing = self.model.getScheduleRulesetByName(name)
        if existing.is_initialized():
            return existing.get()
        src = self.library.getScheduleRulesetByName(name)
        if src.empty():
            raise RuntimeError(f"schedule called '{name}' not found in master schedule library")
        return src.get().clone(self.model).to_ScheduleRuleset().get()

    def generate(self, template, clim, building_type, spc_type):
        osm, model = self.os.model, self.model
        d = standards()["space_types"][template][clim][building_type][spc_type]
        name = make_name(template, clim, building_type, spc_type, climate_first=False)
        st = osm.SpaceType(model)
        st.setName(name)
        st.setStandardsBuildingType(building_type)
        st.setStandardsSpaceType(spc_type)
        r, g, b = (int(x) for x in d["rgb"].split("_"))
        color = osm.RenderingColor(model)
        color.setRenderingRedValue(r)
        color.setRenderingGreenValue(g)
        color.setRenderingBlueValue(b)
        st.setRenderingColor(color)
        sset = osm.DefaultScheduleSet(model)
        sset.setName(f"{name} Schedule Set")
        st.setDefaultScheduleSet(sset)

        def sched(key, setter):
            if d.get(key) is not None:
                getattr(sset, setter)(self.schedule(d[key]))

        per_area, per_person = d.get("lighting_w_per_area"), d.get("lighting_w_per_person")
        if _nonzero(per_area) or _nonzero(per_person):
            ld = osm.LightsDefinition(model)
            ld.setName(f"{name} Lights Definition")
            if _nonzero(per_area):
                ld.setWattsperSpaceFloorArea(_conv(self.os, per_area, "W/ft^2", "W/m^2"))
            if _nonzero(per_person):
                ld.setWattsperPerson(float(per_person))
            lights = osm.Lights(ld)
            lights.setName(f"{name} Lights")
            lights.setSpaceType(st)
            sched("lighting_sch", "setLightingSchedule")

        v_area, v_person, v_ach = (
            d.get("ventilation_per_area"),
            d.get("ventilation_per_person"),
            d.get("ventilation_ach"),
        )
        if _nonzero(v_area) or _nonzero(v_person) or _nonzero(v_ach):
            oa = osm.DesignSpecificationOutdoorAir(model)
            oa.setName(f"{name} Ventilation")
            st.setDesignSpecificationOutdoorAir(oa)
            oa.setOutdoorAirMethod("Sum")
            if _nonzero(v_area):
                oa.setOutdoorAirFlowperFloorArea(_conv(self.os, v_area, "ft^3/min*ft^2", "m^3/s*m^2"))
            if _nonzero(v_person):
                oa.setOutdoorAirFlowperPerson(_conv(self.os, v_person, "ft^3/min*person", "m^3/s*person"))
            if _nonzero(v_ach):
                oa.setOutdoorAirFlowAirChangesperHour(float(v_ach))

        occ = d.get("occupancy_per_area")
        if _nonzero(occ):
            pd = osm.PeopleDefinition(model)
            pd.setName(f"{name} People Definition")
            pd.setPeopleperSpaceFloorArea(_conv(self.os, occ / 1000, "people/ft^2", "people/m^2"))
            people = osm.People(pd)
            people.setName(f"{name} People")
            people.setSpaceType(st)
            sched("occupancy_sch", "setNumberofPeopleSchedule")
            sched("occupancy_activity_sch", "setPeopleActivityLevelSchedule")

        inf = d.get("infiltration_per_area_ext")
        if _nonzero(inf):
            fl = osm.SpaceInfiltrationDesignFlowRate(model)
            fl.setName(f"{name} Infiltration")
            fl.setSpaceType(st)
            fl.setFlowperExteriorSurfaceArea(_conv(self.os, inf, "ft^3/min*ft^2", "m^3/s*m^2"))
            sched("infiltration_sch", "setInfiltrationSchedule")

        for key, definition, cls, label, unit, sch_key, sch_setter in (
            (
                "elec_equip_per_area",
                osm.ElectricEquipmentDefinition,
                osm.ElectricEquipment,
                "Electric Equipment",
                "W/ft^2",
                "elec_equip_sch",
                "setElectricEquipmentSchedule",
            ),
            (
                "gas_equip_per_area",
                osm.GasEquipmentDefinition,
                osm.GasEquipment,
                "Gas Equipment",
                "Btu/hr*ft^2",
                "gas_equip_sch",
                "setGasEquipmentSchedule",
            ),
        ):
            v = d.get(key)
            if _nonzero(v):
                df = definition(model)
                df.setName(f"{name} {label} Definition")
                df.setWattsperSpaceFloorArea(_conv(self.os, v, unit, "W/m^2"))
                eq = cls(df)
                eq.setName(f"{name} {label}")
                eq.setSpaceType(st)
                sched(sch_key, sch_setter)

        thermostat = osm.ThermostatSetpointDualSetpoint(model)
        thermostat.setName(f"{name} Thermostat")
        if d.get("heating_setpoint_sch") is not None:
            thermostat.setHeatingSetpointTemperatureSchedule(self.schedule(d["heating_setpoint_sch"]))
        if d.get("cooling_setpoint_sch") is not None:
            thermostat.setCoolingSetpointTemperatureSchedule(self.schedule(d["cooling_setpoint_sch"]))
        return st


# --- construction sets ----------------------------------------------------------------------


class ConstructionSetGenerator:
    def __init__(self, os, model):
        self.os, self.model = os, model

    def material(self, name):
        osm, os = self.os.model, self.os
        for m in self.model.getMaterials():
            if m.nameString() == name:
                return m
        data = standards()["materials"].get(name)
        if not data:
            raise RuntimeError(f"Cannot find material named '{name}'")
        kind, model = data["material_type"], self.model

        def f(k):  # the data file misspells "reflectance" in some back side keys
            return float(data[k] if k in data else data[k.replace("reflectance", "relectance")])

        if kind == "StandardOpaqueMaterial":
            m = osm.StandardOpaqueMaterial(model)
            m.setName(name)
            m.setRoughness(str(data["roughness"]))
            m.setThickness(_conv(os, f("thickness"), "in", "m"))
            m.setConductivity(_conv(os, f("conductivity"), "Btu*in/hr*ft^2*R", "W/m*K"))
            m.setDensity(_conv(os, f("density"), "lb/ft^3", "kg/m^3"))
            m.setSpecificHeat(_conv(os, f("specific_heat"), "Btu/lb*R", "J/kg*K"))
        elif kind == "MasslessOpaqueMaterial":
            m = osm.MasslessOpaqueMaterial(model)  # the Ruby original called setters this class does not have
            m.setName(name)
            m.setThermalResistance(_conv(os, f("resistance"), "hr*ft^2*R/Btu", "m^2*K/W"))
        elif kind == "AirGap":
            m = osm.AirGap(model)
            m.setName(name)
            m.setThermalResistance(_conv(os, f("resistance"), "hr*ft^2*R/Btu*in", "m*K/W"))
            return m
        elif kind == "Gas":
            m = osm.Gas(model)
            m.setName(name)
            m.setThickness(_conv(os, f("thickness"), "in", "m"))
            m.setGasType(str(data["gas_type"]))
            return m
        elif kind == "SimpleGlazing":
            m = osm.SimpleGlazing(model)
            m.setName(name)
            m.setUFactor(_conv(os, f("u_factor"), "Btu/hr*ft^2*R", "W/m^2*K"))
            m.setSolarHeatGainCoefficient(f("solar_heat_gain_coefficient"))
            m.setVisibleTransmittance(f("visible_transmittance"))
            return m
        elif kind == "StandardGlazing":
            m = osm.StandardGlazing(model)
            m.setName(name)
            m.setOpticalDataType(str(data["optical_data_type"]))
            m.setThickness(_conv(os, f("thickness"), "in", "m"))
            m.setSolarTransmittanceatNormalIncidence(f("solar_transmittance_at_normal_incidence"))
            m.setFrontSideSolarReflectanceatNormalIncidence(f("front_side_solar_reflectance_at_normal_incidence"))
            m.setBackSideSolarReflectanceatNormalIncidence(f("back_side_solar_reflectance_at_normal_incidence"))
            m.setVisibleTransmittanceatNormalIncidence(f("visible_transmittance_at_normal_incidence"))
            m.setFrontSideVisibleReflectanceatNormalIncidence(f("front_side_visible_reflectance_at_normal_incidence"))
            m.setBackSideVisibleReflectanceatNormalIncidence(f("back_side_visible_reflectance_at_normal_incidence"))
            m.setInfraredTransmittanceatNormalIncidence(f("infrared_transmittance_at_normal_incidence"))
            m.setFrontSideInfraredHemisphericalEmissivity(f("front_side_infrared_hemispherical_emissivity"))
            m.setBackSideInfraredHemisphericalEmissivity(f("back_side_infrared_hemispherical_emissivity"))
            m.setConductivity(_conv(os, f("conductivity"), "Btu*in/hr*ft^2*R", "W/m*K"))
            m.setDirtCorrectionFactorforSolarandVisibleTransmittance(
                f("dirt_correction_factor_for_solar_and_visible_transmittance")
            )
            m.setSolarDiffusing("true" in str(data.get("solar_diffusing", "")).lower())
            return m
        else:
            raise RuntimeError(f"Unknown material type {kind}")
        m.setThermalAbsorptance(f("thermal_absorptance"))
        m.setSolarAbsorptance(f("solar_absorptance"))
        m.setVisibleAbsorptance(f("visible_absorptance"))
        return m

    def construction(self, name):
        for c in self.model.getConstructions():
            if c.nameString() == name:
                return c
        data = standards()["constructions"].get(name)
        if not data:
            raise RuntimeError(f"Cannot find construction named '{name}'")
        c = self.os.model.Construction(self.model)
        c.setName(name)
        info = c.standardsInformation()
        info.setIntendedSurfaceType(data.get("intended_surface_type") or "")
        info.setStandardsConstructionType(data.get("standards_construction_type") or "")
        layers = self.os.model.MaterialVector()
        for mat in data["materials"]:
            layers.append(self.material(mat))
        c.setLayers(layers)
        return c

    def generate(self, template, clim, building_type, spc_type):
        osm, model = self.os.model, self.model
        data = standards()["construction_sets"][template][clim][building_type][spc_type]
        cs = osm.DefaultConstructionSet(model)
        cs.setName(make_name(template, clim, building_type, spc_type, climate_first=True))

        def fill(group, mapping):
            for key, setter in mapping.items():
                if data.get(key):
                    getattr(group, setter)(self.construction(data[key]))

        surf = {"floor": "setFloorConstruction", "wall": "setWallConstruction", "ceiling": "setRoofCeilingConstruction"}
        for prefix, setter in (
            ("exterior", "setDefaultExteriorSurfaceConstructions"),
            ("interior", "setDefaultInteriorSurfaceConstructions"),
            ("ground_contact", "setDefaultGroundContactSurfaceConstructions"),
        ):
            group = osm.DefaultSurfaceConstructions(model)
            getattr(cs, setter)(group)
            roof = "roof" if prefix == "exterior" else "ceiling"
            fill(
                group,
                {f"{prefix}_floor": surf["floor"], f"{prefix}_wall": surf["wall"], f"{prefix}_{roof}": surf["ceiling"]},
            )
        ext = osm.DefaultSubSurfaceConstructions(model)
        cs.setDefaultExteriorSubSurfaceConstructions(ext)
        fill(
            ext,
            {
                "exterior_fixed_window": "setFixedWindowConstruction",
                "exterior_operable_window": "setOperableWindowConstruction",
                "exterior_door": "setDoorConstruction",
                "exterior_glass_door": "setGlassDoorConstruction",
                "exterior_overhead_door": "setOverheadDoorConstruction",
                "exterior_skylight": "setSkylightConstruction",  # the Ruby original set the overhead door here
                "tubular_daylight_dome": "setTubularDaylightDomeConstruction",
                "tubular_daylight_diffuser": "setTubularDaylightDiffuserConstruction",
            },
        )
        inner = osm.DefaultSubSurfaceConstructions(model)
        cs.setDefaultInteriorSubSurfaceConstructions(inner)
        fill(
            inner,
            {
                "interior_fixed_window": "setFixedWindowConstruction",
                "interior_operable_window": "setOperableWindowConstruction",
                "interior_door": "setDoorConstruction",
            },
        )
        fill(
            cs,
            {
                "interior_partition": "setInteriorPartitionConstruction",
                "space_shading": "setSpaceShadingConstruction",
                "building_shading": "setBuildingShadingConstruction",
                "site_shading": "setSiteShadingConstruction",
            },
        )
        return cs


@register
class SpaceTypeAndConstructionSetWizard(UserScript):
    category = "On Demand Template Generators"
    name = "Space Type and Construction Set Wizard"
    description = (
        "Create DOE reference building space types and / or a default construction set for a template, "
        "building type and climate zone. In the scene they become the template (space types, construction "
        "sets, constructions) used when exporting."
    )
    produces_template = True
    reads_scene = False
    edits_geometry = False

    def arguments(self, model):
        s = standards()
        building_types, templates = set(), []
        for table in ("space_types", "construction_sets"):
            for template, sets in s[table].items():
                if template not in templates:
                    templates.append(template)
                for by_building in sets.values():
                    building_types.update(by_building)
        zones = [z for z in s["climate_zones"] if "CEC" not in z]
        choice = lambda items: tuple((i, i) for i in items)
        return [
            ArgDef("buildingType", "Building Type", "choice", "Office", choices=choice(sorted(building_types))),
            ArgDef("template", "Template", "choice", "189.1-2009", choices=choice(templates)),
            ArgDef("climateZone", "ASHRAE Climate Zone", "choice", "ASHRAE 169-2006-5B", choices=choice(zones)),
            ArgDef("createSpaceTypes", "Create Space Types?", "bool", True),
            ArgDef("createConstructionSet", "Create Construction Set?", "bool", True),
            ArgDef("setBuildingDefaults", "Set Building Defaults Using New Objects?", "bool", True),
        ]

    def run(self, model, a, ctx):
        import openstudio

        s = standards()
        template, building_type, climate = a["template"], a["buildingType"], a["climateZone"]
        ctx.info(
            f"The building started with {len(model.getSpaceTypes())} space types and "
            f"{len(model.getDefaultConstructionSets())} construction sets."
        )
        building = model.getBuilding()
        made_space_type = made_set = None
        if a["createSpaceTypes"]:
            gen = SpaceTypeGenerator(openstudio, model)
            for czs in sorted(s["space_types"].get(template, {})):
                if not climate_zone_in_set(climate, czs):
                    continue
                spc = s["space_types"][template][czs].get(building_type, {})
                if spc:
                    ctx.info(f"Creating Space Types for: {template}, {czs}, {building_type}.")
                for i, spc_type in enumerate(sorted(spc)):
                    st = gen.generate(template, czs, building_type, spc_type)
                    ctx.info(f"Creating '{st.nameString()}'.")
                    ctx.progress((i + 1) / len(spc), "Creating space types")
                    if DEFAULT_SPACE_TYPE.get(building_type) == spc_type and a["setBuildingDefaults"]:
                        ctx.info("Setting Default Space Type for the Building.")
                        building.setSpaceType(st)
                        made_space_type = st
        if a["createConstructionSet"]:
            czs = find_climate_zone_set(s["construction_sets"], template, climate, building_type, "")
            gen = ConstructionSetGenerator(openstudio, model)
            by_building = s["construction_sets"].get(template, {}).get(czs, {}) if czs else {}
            for spc_type in sorted(by_building.get(building_type, {})):
                cs = gen.generate(template, czs, building_type, spc_type)
                ctx.info(f"Creating Construction Set: {cs.nameString()}")
                if a["setBuildingDefaults"]:
                    ctx.info("Setting Default Construction Set for the Building.")
                    building.setDefaultConstructionSet(cs)
                    made_set = cs
            if not by_building.get(building_type):
                ctx.warning(f"No construction set for {template}, {climate}, {building_type}.")
        ctx.info(
            f"The model has {len(model.getSpaceTypes())} space types and "
            f"{len(model.getDefaultConstructionSets())} construction sets."
        )
        if made_space_type is not None:
            ctx.building_defaults["spaceType"] = made_space_type.nameString()
        if made_set is not None:
            ctx.building_defaults["defaultConstructionSet"] = made_set.nameString()
        return True
