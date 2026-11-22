"""Build an OpenStudio model (.osm) from a glTF carrying OpenStudio `extras`.

This is the inverse of `openstudio::gltf::GltfForwardTranslator`. It needs the
`openstudio` python bindings (bundled wheel or an OpenStudio install, see `openstudio_env`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC

import numpy as np

from . import inspector_schema, schedule_rules, schema
from .geometry import (
    best_fit_plane,
    dot,
    fill_notches,
    max_plane_distance,
    point_in_polygon,
    polygon_area,
    polygon_normal,
    project_to_plane,
    simplify,
    sub,
    unit,
)
from .gltf_reader import Face, GltfScene, read_gltf


@dataclass
class BuildOptions:
    y_up: bool | None = None  # None: auto-detect from glTF generator
    scale: float = 1.0  # glTF units -> metres
    default_space_name: str = "Space 1"
    create_constructions: bool = True  # placeholder (layer-less) constructions by name
    match_surfaces: bool = False  # run Space.matchSurfaces on coincident surfaces
    new_zone_for_space: bool = False  # the plugin's "New Zone for Space": spaces left without a thermal zone get one
    overrides: dict = field(default_factory=dict)  # node name -> default extras
    template_osm: str = ""  # .osm whose templates (space types, constructions...) seed the model
    template_types: list[str] = field(default_factory=lambda: list(TEMPLATE_TYPES))
    # more resource files (.osm), each with its own categories: [(path, ["Construction", "Schedule"]), ...]
    extra_templates: list[tuple[str, list[str]]] = field(default_factory=list)
    # geometry repair tolerances (add-on preferences); defaults are the module constants below
    plane_fix_tol: float = 1e-4
    plane_warn_dist: float = 0.0254
    sub_plane_dist: float = 0.05
    sub_parallel: float = 0.98


# template categories that can be taken over from `template_osm` (OpenStudio class names)
TEMPLATE_TYPES = ("SpaceType", "DefaultConstructionSet", "Construction")
# "Schedule" additionally keeps schedules nothing refers to (otherwise unreferenced template schedules are pruned);
# it is only ever requested explicitly (the SketchUp plugin's "Import Schedules")
OPTIONAL_TEMPLATE_TYPES = ("Schedule",)
_CLASS_GETTERS = {
    "SpaceType": ("getSpaceTypes",),
    "DefaultConstructionSet": ("getDefaultConstructionSets",),
    "Construction": ("getConstructions", "getConstructionAirBoundarys"),
    "Schedule": ("getScheduleRulesets", "getScheduleConstants", "getScheduleCompacts"),
}


@dataclass
class BuildReport:
    surfaces: int = 0
    sub_surfaces: int = 0
    shading_surfaces: int = 0
    partitions: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.surfaces} surfaces, {self.sub_surfaces} sub surfaces, "
            f"{self.shading_surfaces} shading surfaces, {self.partitions} partitions, "
            f"{len(self.warnings)} warnings"
        )


def _s(extras: dict, key: str) -> str:
    v = extras.get(key)
    return v if isinstance(v, str) else ""


def _idd(name: str):
    import openstudio

    return openstudio.IddObjectType(name)


def _auto_type(poly) -> str:
    n = unit(polygon_normal(poly))
    if n[2] > 0.707:
        return "RoofCeiling"
    if n[2] < -0.707:
        return "Floor"
    return "Wall"


class _Builder:
    def __init__(self, scene: GltfScene, opts: BuildOptions):
        import openstudio
        from openstudio import model as osm

        self.os, self.osm = openstudio, osm
        self.scene, self.opts = scene, opts
        self.report = BuildReport()
        self.model = self.load_template() or osm.Model()
        self.cache: dict[tuple[str, str], object] = {}
        self.surfaces: dict[str, object] = {}  # by name
        self.surface_polys: dict[str, tuple] = {}  # name -> (poly, normal, space name)
        self._surface_cache = None  # numpy view of surface_polys, see _surface_index
        self.sub_surfaces: dict[str, object] = {}
        self.sub_polys: dict[str, list] = {}  # parent surface name -> sub surface polygons
        self.shading_groups: dict[tuple, object] = {}
        self.pending_shading: list[tuple[object, str]] = []
        self.partition_groups: dict[str, object] = {}
        self.pending_adjacent: list[tuple[str, str, str]] = []  # (kind, name, other)
        self.metadata = {}  # (iddObjectType, name) -> metadata dict

    # -- helpers -----------------------------------------------------------------
    def warn(self, msg: str):
        self.report.warnings.append(msg)

    @property
    def has_template(self) -> bool:
        return bool(self.opts.template_osm or self.opts.extra_templates)

    @property
    def keep_schedules(self) -> bool:
        """True when a template asked for all its schedules (they are then never pruned)."""
        return "Schedule" in self.opts.template_types or any("Schedule" in t for _p, t in self.opts.extra_templates)

    def load_template(self):
        """The main template (stripped to the wanted resources) plus the resources of the extra templates."""
        m = self.load_main_template()
        if not self.opts.extra_templates:
            return m
        if m is None:
            m = self.osm.Model()
        for path, types in self.opts.extra_templates:
            self.merge_template(m, path, types)
        return m

    def merge_template(self, dst, path, types):
        """Copy the chosen resource categories of the .osm `path` into `dst` (clone keeps materials, schedules...).

        Objects whose name already exists in `dst` are skipped, so the first file wins (and nothing is duplicated).
        """
        src = self.os.osversion.VersionTranslator().loadModel(self.os.path(str(path)))
        if not src.is_initialized():
            self.warn(f"template '{path}' could not be loaded")
            return
        src, taken = src.get(), {}
        for cat in types:
            for getter_name in _CLASS_GETTERS.get(cat, ()):
                getter = getattr(src, getter_name, None)
                if getter is None:
                    continue
                for o in getter():
                    key = (o.iddObject().type().valueName(), o.nameString())
                    if key not in taken and not self._exists(dst, key):
                        o.clone(dst)
                        taken[key] = True

    @staticmethod
    def _exists(model, key) -> bool:
        return any(o.nameString() == key[1] for o in model.getObjectsByType(_idd(key[0])))

    def load_main_template(self):
        """Clone the template model and strip everything but the wanted template resources.

        Cloning the whole model (instead of single objects) keeps schedule/material references intact.
        """
        path = self.opts.template_osm
        if not path:
            return None
        src = self.os.osversion.VersionTranslator().loadModel(self.os.path(str(path)))
        if not src.is_initialized():
            self.warn(f"template '{path}' could not be loaded")
            return None
        m = src.get().clone(True).to_Model()
        keep = set(self.opts.template_types)
        drop = [
            "Space",
            "ThermalZone",
            "BuildingStory",
            "BuildingUnit",
            "ShadingSurfaceGroup",
            "AirLoopHVAC",
            "PlantLoop",
        ]
        drop += [t for t in TEMPLATE_TYPES if t not in keep]
        if "DefaultConstructionSet" not in keep:
            drop.append("DefaultScheduleSet")
        for cls in drop:
            getter = getattr(m, f"get{cls}s", None) or getattr(m, f"get{cls}es", None)
            for o in list(getter()) if getter else []:
                o.remove()
        return m

    def prune_schedules(self):
        """Drop template schedules nothing refers to (after the Inspector store had its chance to reference them)."""
        if not self.has_template or self.keep_schedules:
            return
        for getter in (self.model.getScheduleRulesets, self.model.getScheduleConstants):
            for o in list(getter()):
                if o.directUseCount() == 0:
                    o.remove()

    def pts(self, poly):
        return self.os.Point3dVector([self.os.Point3d(*p) for p in poly])

    def color(self, text: str):
        try:
            text = text.lstrip("#")
            r, g, b = (int(text[i : i + 2], 16) for i in (0, 2, 4))
        except (ValueError, IndexError):
            return None
        c = self.osm.RenderingColor(self.model)
        c.setRenderingRedValue(r)
        c.setRenderingGreenValue(g)
        c.setRenderingBlueValue(b)
        return c

    def named(self, kind: str, name: str, factory):
        """Get-or-create a named ModelObject."""
        if not name:
            return None
        key = (kind, name)
        if key not in self.cache:
            obj = self.lookup(kind, name)
            if obj is None:
                obj = factory()
                obj.setName(name)
            self.cache[key] = obj
            md = self.metadata.get(key)
            if md:
                self.apply_metadata(kind, obj, md)
        return self.cache[key]

    _CLASS_OF = {
        "spacetype": "SpaceType",
        "cset": "DefaultConstructionSet",
        "construction": "Construction",
        "airwall": "ConstructionAirBoundary",
        "scheduleset": "DefaultScheduleSet",
        "defsurf": "DefaultSurfaceConstructions",
        "defsubsurf": "DefaultSubSurfaceConstructions",
        "opaquemat": "StandardOpaqueMaterial",
        "masslessmat": "MasslessOpaqueMaterial",
        "airgap": "AirGap",
        "glazing": "SimpleGlazing",
        "shade": "Shade",
    }

    def lookup(self, kind: str, name: str):
        """Existing template object of this kind/name (only resource kinds can come from a template)."""
        cls = self._CLASS_OF.get(kind)
        if not self.has_template:
            return None
        if kind == "schedule":  # a template schedule may be a ruleset, compact...
            found = self.model.getScheduleByName(name)
            return found.get() if found.is_initialized() else None
        if not cls:
            return None
        found = getattr(self.model, f"get{cls}ByName")(name)
        return found.get() if found.is_initialized() else None

    def apply_metadata(self, kind, obj, md):
        col = self.color(md.get("color", "")) if md.get("color") else None
        try:
            if kind == "story":
                if col:
                    obj.setRenderingColor(col)
                if md.get("nominal_z_coordinate") is not None:
                    obj.setNominalZCoordinate(float(md["nominal_z_coordinate"]))
                h = float(md.get("nominal_floorCeiling_Height") or 0)
                if h > 0:
                    obj.setNominalFloortoCeilingHeight(h)
            elif kind in ("zone", "spacetype", "unit"):
                if col:
                    obj.setRenderingColor(col)
                if kind == "zone" and int(md.get("multiplier") or 0) > 0:
                    obj.setMultiplier(int(md["multiplier"]))
        except Exception as e:
            self.warn(f"metadata for {kind} '{md.get('name')}': {e}")

    # -- get-or-create per object kind ---------------------------------------------
    def story(self, n):
        return self.named("story", n, lambda: self.osm.BuildingStory(self.model))

    def zone(self, n):
        return self.named("zone", n, lambda: self.osm.ThermalZone(self.model))

    def spacetype(self, n):
        return self.named("spacetype", n, lambda: self.osm.SpaceType(self.model))

    def unit(self, n):
        return self.named("unit", n, lambda: self.osm.BuildingUnit(self.model))

    def cset(self, n):
        return self.named("cset", n, lambda: self.osm.DefaultConstructionSet(self.model))

    def airloop(self, n):
        return self.named("airloop", n, lambda: self.osm.AirLoopHVAC(self.model))

    def scheduleset(self, n):
        return self.named("scheduleset", n, lambda: self.osm.DefaultScheduleSet(self.model))

    def schedule(self, n):
        return self.named("schedule", n, lambda: self.osm.ScheduleConstant(self.model))

    def opaquemat(self, n):
        return self.named("opaquemat", n, lambda: self.osm.StandardOpaqueMaterial(self.model))

    def masslessmat(self, n):
        return self.named("masslessmat", n, lambda: self.osm.MasslessOpaqueMaterial(self.model))

    def airgap(self, n):
        return self.named("airgap", n, lambda: self.osm.AirGap(self.model))

    def glazing(self, n):
        return self.named("glazing", n, lambda: self.osm.SimpleGlazing(self.model))

    def shade(self, n):
        return self.named("shade", n, lambda: self.osm.Shade(self.model))

    def defsurf(self, n):
        return self.named("defsurf", n, lambda: self.osm.DefaultSurfaceConstructions(self.model))

    def defsubsurf(self, n):
        return self.named("defsubsurf", n, lambda: self.osm.DefaultSubSurfaceConstructions(self.model))

    # -- Inspector store (scene extras "osmModel", see inspector_schema) ---------------------
    def resolve_ref(self, cat: str, name: str):
        """The model object a reference field points at (created when it is a plain named resource)."""
        if not name:
            return None
        if cat == "construction":
            return self.construction(name, "")
        if cat in ("schedule", "material"):  # may be template objects or ones the store just created
            found = getattr(self.model, "getScheduleByName" if cat == "schedule" else "getMaterialByName")(name)
            return found.get() if found.is_initialized() else None
        make = getattr(self, cat, None)  # zone / spacetype / story / unit / cset / scheduleset / defsurf / defsubsurf
        return make(name) if make else None

    def apply_fields(self, obj, tdef, fields: dict, label: str):
        for fd in tdef.fields:
            if not fd.setter or fd.key not in fields:
                continue
            value = fields[fd.key]
            try:
                if fd.kind == inspector_schema.REF:
                    value = self.resolve_ref(fd.ref, str(value))
                    if value is None:
                        self.warn(f"{label}: {fd.label} '{fields[fd.key]}' not found")
                        continue
                elif fd.kind == inspector_schema.COLOR:
                    value = self.color(str(value))
                    if value is None:
                        self.warn(f"{label}: {fd.label} '{fields[fd.key]}' is not a #RRGGBB colour")
                        continue
                elif fd.kind in (inspector_schema.FLOAT, inspector_schema.AUTOCALC):
                    value = float(value)
                elif fd.kind == inspector_schema.INT:
                    value = int(value)
                elif fd.kind == inspector_schema.BOOL:
                    value = bool(value)
                else:
                    value = str(value)
                if getattr(obj, fd.setter)(value) is False:
                    self.warn(f"{label}: could not set {fd.label} to {fields[fd.key]!r}")
            except Exception as e:
                self.warn(f"{label}: {fd.label}: {e}")

    def apply_store_resources(self):
        """Zones, stories, space types, construction sets...: before the geometry so surfaces find them."""
        order = (
            "schedule",
            "opaquemat",
            "masslessmat",
            "airgap",
            "glazing",
            "shade",
            "construction",
            "defsurf",
            "defsubsurf",
            "scheduleset",
            "cset",
            "spacetype",
            "story",
            "unit",
            "zone",
        )
        for cat in order:
            tname = inspector_schema.type_for_category(cat)
            for o in self.store:
                if o["type"] == tname:
                    fields = o.get("fields") or {}
                    rules = schedule_rules.loads(fields.get("rules")) if cat == "schedule" else []
                    if rules:
                        default = float(fields.get("scheduleValue") or 0.0)
                        self.named("schedule", o["name"], lambda r=rules, d=default: self.rules_schedule(d, r))
                        continue
                    if cat == "construction":
                        obj = self.named("construction", o["name"], lambda: self.osm.Construction(self.model))
                    else:
                        obj = getattr(self, cat)(o["name"])
                    if obj is not None:
                        fields, label = o.get("fields") or {}, f"{tname} '{o['name']}'"
                        self.apply_fields(obj, inspector_schema.TYPES[tname], fields, label)
                        if cat == "construction":
                            self.apply_layers(obj, fields, label)
                        if cat == "zone":
                            loops = [n.strip() for n in str(fields.get("airLoops") or "").split(",") if n.strip()]
                            self.attach_air_loops(obj, o["name"], loops)
                            self.apply_zone_hvac(obj, fields, label)

    def apply_zone_hvac(self, tz, fields: dict, label: str):
        """Ideal air loads and the four setpoint schedules of a thermal zone.

        Heating / cooling go to a dual setpoint thermostat, humidifying / dehumidifying to a humidistat; the owner
        object is only created when at least one of its schedules is set.
        """
        owners: dict = {}
        for key, (fd_label, owner, setter) in inspector_schema.ZONE_SETPOINTS.items():
            name = str(fields.get(key) or "")
            if not name:
                continue
            sched = self.resolve_ref("schedule", name)
            if sched is None:
                self.warn(f"{label}: {fd_label} '{name}' not found")
                continue
            if owner not in owners:
                if owner == "thermostat":
                    obj = self.osm.ThermostatSetpointDualSetpoint(self.model)
                    obj.setName(f"{tz.nameString()} Thermostat")
                    tz.setThermostatSetpointDualSetpoint(obj)
                else:
                    obj = self.osm.ZoneControlHumidistat(self.model)
                    obj.setName(f"{tz.nameString()} Humidistat")
                    tz.setZoneControlHumidistat(obj)
                owners[owner] = obj
            try:
                if getattr(owners[owner], setter)(sched) is False:
                    self.warn(f"{label}: {fd_label} '{name}' was rejected")
            except Exception as e:
                self.warn(f"{label}: {fd_label}: {e}")
        if fields.get("idealAirLoads"):
            try:
                if not tz.setUseIdealAirLoads(True):
                    self.warn(f"{label}: could not enable ideal air loads")
            except Exception as e:
                self.warn(f"{label}: ideal air loads: {e}")

    def _time(self, hours: float):
        h = int(hours)
        m = round((hours - h) * 60)
        if m == 60:
            h, m = h + 1, 0
        return self.os.Time(0, h, m)

    def rules_schedule(self, default: float, rules):
        """A ScheduleRuleset: constant default day + one ScheduleRule per rule calendar entry (list order = priority)."""
        rs = self.osm.ScheduleRuleset(self.model)
        rs.defaultDaySchedule().addValue(self._time(24), default)
        apply = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        for rule in reversed(rules):  # a new ScheduleRule gets index 0 (highest priority): create the lowest first
            apps = schedule_rules.applications(rule)
            for i, app in enumerate(apps):
                sr = self.osm.ScheduleRule(rs)
                sr.setName(rule.name if len(apps) == 1 else f"{rule.name} {i + 1}")
                for end, value in schedule_rules.segments(rule, default):
                    sr.daySchedule().addValue(self._time(end), value)
                for day, on in zip(apply, app["weekdays"], strict=True):
                    getattr(sr, f"setApply{day}")(bool(on))
                if app["dates"]:
                    for m, d in app["dates"]:
                        sr.addSpecificDate(self.os.Date(self.os.MonthOfYear(m), d))
                elif app["start"]:
                    sr.setStartDate(self.os.Date(self.os.MonthOfYear(app["start"][0]), app["start"][1]))
                    sr.setEndDate(self.os.Date(self.os.MonthOfYear(app["end"][0]), app["end"][1]))
        return rs

    def apply_layers(self, construction, fields: dict, label: str):
        """``layer1`` (outside) .. ``layerN``: the materials in order (empty slots are skipped)."""
        layers = []
        for i in range(1, inspector_schema.MAX_LAYERS + 1):
            name = str(fields.get(f"layer{i}") or "")
            if not name:
                continue
            m = self.resolve_ref("material", name)
            if m is None:
                self.warn(f"{label}: layer {i} material '{name}' not found")
            else:
                layers.append(m)
        if layers and not construction.setLayers(self.osm.MaterialVector(layers)):
            self.warn(f"{label}: could not set the layers")

    def apply_store_spaces(self):
        """Building and Spaces: after the geometry, the store wins over the per-surface extras."""
        for o in self.store:
            f, label = o.get("fields") or {}, f"{o['type']} '{o['name']}'"
            if o["type"] == "Building":
                b = self.model.getBuilding()
                self.apply_fields(b, inspector_schema.TYPES["Building"], f, label)
            elif o["type"] == "WeatherFile":
                self.apply_weather_file(f, label)
            elif o["type"] == "Space":
                sp = self.named("space", o["name"], lambda: self.osm.Space(self.model))
                self.apply_fields(sp, inspector_schema.TYPES["Space"], f, label)
                self.apply_space_transform(sp, f, label)

    def ensure_simulation_objects(self):
        """Create the unique simulation objects a model saved by the OpenStudio app has (and nothing more).

        The GUI's weather/run-period/simulation tabs expect them; ``get*`` creates a missing one with
        defaults and returns the existing one (e.g. from a template) otherwise.
        """
        for getter in (
            "getSite",
            "getYearDescription",
            "getClimateZones",
            "getFacility",
            "getSimulationControl",
            "getSizingParameters",
            "getTimestep",
            "getShadowCalculation",
            "getHeatBalanceAlgorithm",
            "getLifeCycleCostParameters",
        ):
            try:
                getattr(self.model, getter)()
            except Exception as e:
                self.warn(f"{getter}: {e}")

    def apply_weather_file(self, f: dict, label: str):
        """The .epw (if any) sets location and data; explicit fields then override single values."""
        epw = f.get("epwPath")
        if epw:
            try:
                if not self.osm.WeatherFile.setWeatherFile(
                    self.model, self.os.EpwFile(self.os.path(str(epw)))
                ).is_initialized():
                    self.warn(f"{label}: could not use '{epw}'")
            except Exception as e:
                self.warn(f"{label}: {epw}: {e}")
        wf = self.model.getWeatherFile()
        self.apply_fields(wf, inspector_schema.TYPES["WeatherFile"], f, label)
        self.sync_site(wf, label)

    def sync_site(self, wf, label: str):
        """The Site follows the weather file location (the plugin writes city / lat / lon / time zone / elevation).

        OpenStudio's WeatherFile setters leave the Site object untouched, so it would stay at 0 / 0 otherwise.
        """
        site = self.model.getSite()
        try:
            if wf.city():
                site.setName(wf.city())
            for getter, setter in (
                ("latitude", "setLatitude"),
                ("longitude", "setLongitude"),
                ("timeZone", "setTimeZone"),
                ("elevation", "setElevation"),
            ):
                if not getattr(site, setter)(float(getattr(wf, getter)())):
                    self.warn(f"{label}: could not set the Site {getter}")
        except Exception as e:
            self.warn(f"{label}: Site: {e}")

    def apply_space_transform(self, sp, f: dict, label: str):
        """Set the space origin / relative north without moving the geometry (vertices are space-relative)."""
        keys = ("relativeNorth", "xOrigin", "yOrigin", "zOrigin")
        if not any(k in f for k in keys):
            return
        try:
            members = [(s, self.os.Transformation()) for s in sp.surfaces()]
            members += [(ss, self.os.Transformation()) for s in sp.surfaces() for ss in s.subSurfaces()]
            for g in sp.shadingSurfaceGroups():
                members += [(sh, g.transformation()) for sh in g.shadingSurfaces()]
            for g in sp.interiorPartitionSurfaceGroups():
                members += [(p, g.transformation()) for p in g.interiorPartitionSurfaces()]
            # The builder never sets an origin before this point, so the old transformation is the identity.
            # (Don't call sp.transformation() before the setters: OpenStudio caches the first value it returns.)
            world = [(o, tg, tg * o.vertices()) for o, tg in members]
            sp.setDirectionofRelativeNorth(float(f.get("relativeNorth", 0.0)))
            sp.setXOrigin(float(f.get("xOrigin", 0.0)))
            sp.setYOrigin(float(f.get("yOrigin", 0.0)))
            sp.setZOrigin(float(f.get("zOrigin", 0.0)))
            new = sp.transformation()
            for o, tg, verts in world:
                o.setVertices((new * tg).inverse() * verts)
        except Exception as e:
            self.warn(f"{label}: origin / relative north: {e}")

    def construction(self, name, material):
        if not name or not (
            self.opts.create_constructions or self.has_template or ("construction", name) in self.cache
        ):
            return None
        if material == "AirWall" or ("airwall", name) in self.cache or self.lookup("airwall", name) is not None:
            # A template's Construction:AirBoundary is not found by getConstructionByName; without this check a
            # layerless Construction with the same name is created (E+: "Missing required property 'outside_layer'").
            return self.named("airwall", name, lambda: self.osm.ConstructionAirBoundary(self.model))
        return self.named("construction", name, lambda: self.osm.Construction(self.model))

    def space(self, name: str, extras: dict):
        existing = ("space", name) in self.cache
        sp = self.named("space", name, lambda: self.osm.Space(self.model))
        if existing:
            return sp
        if (st := self.spacetype(_s(extras, "spaceTypeName"))) is not None:
            sp.setSpaceType(st)
        if (bs := self.story(_s(extras, "buildingStoryName"))) is not None:
            sp.setBuildingStory(bs)
        if (bu := self.unit(_s(extras, "buildingUnitName"))) is not None:
            sp.setBuildingUnit(bu)
        if (cs := self.cset(_s(extras, "constructionSetName"))) is not None:
            sp.setDefaultConstructionSet(cs)
        tzn = _s(extras, "thermalZoneName")
        if tzn:
            tz = self.zone(tzn)
            sp.setThermalZone(tz)
            self.attach_air_loops(tz, tzn, schema.indexed(extras.get("airLoopHVACNames")))
        return sp

    def finalize_zones(self):
        """The plugin's Space / ThermalZone initialisation, once the store has had its say.

        With ``new_zone_for_space`` every space without a thermal zone gets one named like it; every thermal
        zone without a rendering colour gets one (OpenStudio picks a default), as in ThermalZone.check_model_object.
        """
        if self.opts.new_zone_for_space:
            for sp in self.model.getSpaces():
                if not sp.thermalZone().is_initialized():
                    tz = self.osm.ThermalZone(self.model)
                    tz.setName(sp.nameString())  # OpenStudio makes a clashing name unique
                    sp.setThermalZone(tz)
        for tz in self.model.getThermalZones():
            if not tz.renderingColor().is_initialized():
                tz.setRenderingColor(self.osm.RenderingColor(self.model))

    def attach_air_loops(self, tz, tzn: str, names):
        """Air loops serve a thermal zone: add a branch for the zone to each named loop (once per loop + zone)."""
        for ln in names:
            loop = self.airloop(ln)
            key = ("airloop-zone", f"{ln}|{tzn}")
            if loop is not None and key not in self.cache:
                self.cache[key] = True
                loop.addBranchForZone(tz)

    # -- main --------------------------------------------------------------------
    def run(self) -> BuildReport:
        sc = self.scene.scene_extras
        iddmap = {
            "OS:BuildingStory": "story",
            "OS:ThermalZone": "zone",
            "OS:SpaceType": "spacetype",
            "OS:BuildingUnit": "unit",
            "OS:DefaultConstructionSet": "cset",
            "OS:AirLoopHVAC": "airloop",
            "OS:Space": "space",
        }
        for md in schema.indexed(sc.get("modelObjectMetaData")):
            kind = iddmap.get(md.get("iddObjectType"))
            if kind and md.get("name"):
                self.metadata[(kind, md["name"])] = md
                # create eagerly so objects without geometry survive
                {
                    "story": self.story,
                    "zone": self.zone,
                    "spacetype": self.spacetype,
                    "unit": self.unit,
                    "cset": self.cset,
                    "airloop": self.airloop,
                }.get(kind, lambda n: None)(md["name"])
        for name in schema.indexed(sc.get("buildingStoryNames")):
            self.story(name)
        self.store = inspector_schema.loads_model(sc.get("osmModel"))
        self.apply_store_resources()
        self.model.getWeatherFile()  # singleton: creates an empty OS:WeatherFile unless the template has one
        self.model.getRunPeriod()  # singleton: OpenStudio's weather/simulation setup fails without an OS:RunPeriod
        self.ensure_simulation_objects()
        building = self.model.getBuilding()
        try:
            building.setNorthAxis(float(sc.get("northAxis") or 0.0))
        except Exception as e:
            self.warn(f"northAxis: {e}")

        typed = []
        for f in self.scene.faces:
            t = _s(f.extras, "surfaceType")
            if t not in schema.ALL_SURFACE_TYPES:
                t = _auto_type(f.polygon)
            elif t in schema.BASE_SURFACE_TYPES and f.node_face_count > 1:
                t = _auto_type(f.polygon)  # one mesh, many faces: classify each face by tilt
            typed.append((f, t))

        for f, t in typed:
            if t in schema.BASE_SURFACE_TYPES:
                self.add_surface(f, t)
        for f, t in typed:
            if t in schema.SUBSURFACE_TYPES:
                self.add_sub_surface(f, t)
        self.fill_parent_notches()
        self.apply_shading_controls()
        for f, t in typed:
            if t in schema.SHADING_TYPES:
                self.add_shading(f, t)
            elif t == schema.PARTITION_TYPE:
                self.add_partition(f)
        self.resolve_adjacency()
        self.apply_store_spaces()
        self.finalize_zones()
        if self.opts.match_surfaces:
            spaces = self.model.getSpaces()
            try:
                self.osm.matchSurfaces(self.osm.SpaceVector(spaces)) if hasattr(self.osm, "matchSurfaces") else [
                    a.matchSurfaces(b) for i, a in enumerate(spaces) for b in spaces[i + 1 :]
                ]
            except Exception as e:
                self.warn(f"matchSurfaces: {e}")
        self.fill_missing_constructions()
        self.prune_schedules()
        return self.report

    # -- geometry repair (the checks of the SketchUp plugin's check_model_object) -----------------
    def sanitize(self, poly, name: str):
        """Drop duplicate / collinear vertices and project out-of-plane vertices onto the best-fit plane.

        Returns None (with a warning) when no valid polygon is left (fewer than 3 vertices / no area).
        """
        pts = simplify([tuple(p) for p in poly])
        if len(pts) < 3:
            self.warn(f"{name}: fewer than 3 distinct vertices, skipped")
            return None
        centroid, n = best_fit_plane(pts)
        if n == (0.0, 0.0, 0.0):
            self.warn(f"{name}: degenerate polygon (no area), skipped")
            return None
        worst = max_plane_distance(pts, centroid, n)
        if worst > self.opts.plane_fix_tol:
            if worst > self.opts.plane_warn_dist:
                self.warn(f"{name}: vertices were {worst:.3f} m out of plane, projected onto the best-fit plane")
            pts = project_to_plane(pts, centroid, n)
        return pts

    def orient(self, poly, surface_type: str, name: str):
        """Floors must face down and roofs / ceilings up: reverse an upside-down polygon (with a warning)."""
        nz = unit(polygon_normal(poly))[2]
        if (surface_type == "Floor" and nz > 1e-6) or (surface_type == "RoofCeiling" and nz < -1e-6):
            self.warn(f"{name}: {surface_type} surface was upside-down, fixed")
            return poly[::-1]
        return poly

    def add_surface(self, f: Face, t: str):
        ex = f.extras
        poly = self.sanitize(f.polygon, f.name)
        if poly is None:
            return
        poly = self.orient(poly, t, f.name)
        spname = _s(ex, "spaceName") or self.opts.default_space_name
        space = self.space(spname, ex)
        s = self.osm.Surface(self.pts(poly), self.model)
        s.setName(f.name)
        s.setSpace(space)
        s.setSurfaceType(t)
        self.apply_construction(s, ex)
        bc = _s(ex, "outsideBoundaryCondition")
        other = _s(ex, "outsideBoundaryConditionObjectName")
        if bc == "Surface":
            if other:
                self.pending_adjacent.append(("surface", f.name, other))
            else:
                s.assignDefaultBoundaryCondition()
        elif bc:
            if not s.setOutsideBoundaryCondition(bc):
                self.warn(f"{f.name}: invalid boundary condition '{bc}'")
                s.assignDefaultBoundaryCondition()
        else:
            s.assignDefaultBoundaryCondition()
        if _s(ex, "sunExposure"):
            s.setSunExposure(_s(ex, "sunExposure"))
        if _s(ex, "windExposure"):
            s.setWindExposure(_s(ex, "windExposure"))
        self.surfaces[f.name] = s
        self.surface_polys[f.name] = (poly, unit(polygon_normal(poly)), spname)
        self.report.surfaces += 1

    def apply_construction(self, obj, ex):
        c = self.construction(
            _s(ex, "constructionName"), _s(ex, "constructionMaterialName") or ("AirWall" if ex.get("airWall") else "")
        )
        if c is not None and not obj.setConstruction(c):
            self.warn(f"{obj.nameString()}: could not set construction '{_s(ex, 'constructionName')}'")

    def conform_to_parent(self, poly, parent: str, name: str):
        """Make a sub surface polygon agree with its base surface: face the same way, lie in its plane.

        Only acts on a polygon that is nearly parallel to / close to the parent plane (like the plugin's
        "inside-out" and "not in the same plane" fixes); anything else is returned unchanged.
        """
        ppoly, pn, _sp = self.surface_polys[parent]
        n = unit(polygon_normal(poly))
        c = dot(n, pn)
        if abs(c) < self.opts.sub_parallel:
            return poly
        dist = max(abs(dot(pn, sub(p, ppoly[0]))) for p in poly)
        if dist > self.opts.sub_plane_dist:
            return poly
        if c < 0:
            self.warn(f"sub surface '{name}': inside-out (normal opposes its base surface), fixed")
            poly = poly[::-1]
        if dist > self.opts.plane_fix_tol:
            if dist > self.opts.plane_warn_dist:
                self.warn(f"sub surface '{name}': not in the plane of '{parent}' ({dist:.3f} m), projected onto it")
            poly = project_to_plane(poly, ppoly[0], pn)
        return poly

    def locate_parent(self, poly, ex, name: str = ""):
        """(base surface name | None, sub surface polygon fixed to lie in / face like that surface).

        Order: the ``surfaceName`` extra, a strict geometric match (same plane and facing, inside the base
        polygon), then a relaxed one (flipped and / or slightly off the plane, inside after projection).
        """
        pname = _s(ex, "surfaceName")
        if pname in self.surfaces:
            return pname, self.conform_to_parent(poly, pname, name)
        names, normals, offsets = self._surface_index()
        if not names:
            return None, poly
        n = np.array(unit(polygon_normal(poly)))
        pts = np.array(poly).reshape(-1, 3)
        # vectorised normal + plane prefilter; the exact point-in-polygon test only runs on the survivors
        cos = normals @ n
        dist = np.abs(pts @ normals.T - offsets).max(axis=0)  # worst vertex distance to each base plane
        strict = (cos > 0.999) & (np.abs(offsets - normals @ pts[0]) < 1e-3)
        for k in np.nonzero(strict)[0].tolist():
            bpoly, pn, _sp = self.surface_polys[names[k]]
            if all(point_in_polygon(p, bpoly, pn, 1e-3) for p in poly):
                return names[k], poly
        relaxed = (np.abs(cos) >= self.opts.sub_parallel) & (dist <= self.opts.sub_plane_dist) & ~strict
        best = None
        for k in np.nonzero(relaxed)[0].tolist():
            bpoly, pn, _sp = self.surface_polys[names[k]]
            proj = project_to_plane(poly, bpoly[0], pn)
            if all(point_in_polygon(p, bpoly, pn, 1e-3) for p in proj) and (best is None or dist[k] < best[0]):
                best = (float(dist[k]), names[k])
        if best is None:
            return None, poly
        return best[1], self.conform_to_parent(poly, best[1], name)

    def contained(self, poly, parent: str, tol: float = 1e-3) -> bool:
        """All sub surface vertices lie in (or on the border of) the base surface (SubSurface.rb "not contained")."""
        bpoly, pn, _sp = self.surface_polys[parent]
        return all(point_in_polygon(p, bpoly, pn, tol) for p in poly)

    def dividing(self, poly, tol: float = 1e-3) -> list[str]:
        """Base surfaces a sub surface cuts through (SubSurface.rb "divides its base surface"), [] if it does not.

        A strip window running from edge to edge splits its wall into two meshes (two base surfaces): it touches
        (an edge of) each of them but is contained by none. At least two coplanar base surfaces must hold two or
        more of its vertices on or inside their outline.
        """
        names, normals, offsets = self._surface_index()
        if len(names) < 2:
            return []
        n = np.array(unit(polygon_normal(poly)))
        coplanar = (np.abs(normals @ n) > 0.999) & (np.abs(offsets - normals @ np.array(poly[0])) < tol * 10)
        hit = []
        for k in np.nonzero(coplanar)[0].tolist():
            bpoly, pn, _sp = self.surface_polys[names[k]]
            if sum(point_in_polygon(p, bpoly, pn, tol) for p in poly) >= 2:
                hit.append(names[k])
        return hit if len(hit) >= 2 else []

    def _surface_index(self):
        """(names, unit normals, plane offsets) of all base surfaces in insertion order, rebuilt when it grew."""
        if self._surface_cache is None or len(self._surface_cache[0]) != len(self.surface_polys):
            names = list(self.surface_polys)
            normals = np.array([self.surface_polys[k][1] for k in names]).reshape(-1, 3)
            firsts = np.array([self.surface_polys[k][0][0] for k in names]).reshape(-1, 3)
            self._surface_cache = (names, normals, np.einsum("ij,ij->i", normals, firsts))
        return self._surface_cache

    def add_sub_surface(self, f: Face, t: str):
        ex = f.extras
        poly = self.sanitize(f.polygon, f.name)
        if poly is None:
            return
        parent, poly = self.locate_parent(poly, ex, f.name)
        if parent is not None and not self.contained(poly, parent):
            # an explicit surfaceName is trusted for the plane, but the plugin still demands containment
            cut = self.dividing(poly)
            self.warn(
                f"sub surface '{f.name}': divides its base surface ({', '.join(cut)}), skipped"
                if cut
                else f"sub surface '{f.name}': not contained by its base surface '{parent}', skipped"
            )
            return
        if parent is None:
            cut = self.dividing(poly)
            self.warn(
                f"sub surface '{f.name}': divides its base surface ({', '.join(cut)}), skipped"
                if cut
                else f"sub surface '{f.name}': no parent surface found, skipped"
            )
            return
        if polygon_area(poly) >= 0.999 * polygon_area(self.surface_polys[parent][0]):
            self.warn(f"sub surface '{f.name}': same size as its base surface '{parent}', skipped")
            return
        ss = self.osm.SubSurface(self.pts(poly), self.model)
        ss.setName(_s(ex, "subSurfaceName") or f.name)
        if not ss.setSurface(self.surfaces[parent]):
            self.warn(f"sub surface '{f.name}': could not attach to '{parent}'")
        ss.setSubSurfaceType(t)
        self.apply_construction(ss, ex)
        other = _s(ex, "outsideBoundaryConditionObjectName")
        if other:
            self.pending_adjacent.append(("sub", ss.nameString(), other))
        self.sub_surfaces[ss.nameString()] = ss
        if control := _s(ex, "shadingControlName"):
            self.pending_shading.append((ss, control))
        self.sub_polys.setdefault(parent, []).append(poly)
        self.report.sub_surfaces += 1

    def shading_control(self, name: str, defs: dict):
        """Get-or-create the ShadingControl `name` from its definition (scene extra `shadingControls`)."""
        key = ("shadingcontrol", name)
        if key in self.cache:
            return self.cache[key]
        d = defs.get(name)
        if not isinstance(d, dict):
            self.warn(f"shading control '{name}' has no definition, skipped")
            return None
        mat = d.get("material") or {}
        target = None
        if mat.get("kind") in ("Blind", "Shade", "Screen", "DaylightRedirectionDevice"):
            found = self.model.getShadingMaterialByName(mat.get("name", ""))
            target = found.get() if found.is_initialized() else getattr(self.osm, mat["kind"])(self.model)
            if not found.is_initialized() and mat.get("name"):
                target.setName(mat["name"])
        elif d.get("constructionName"):
            found = self.model.getConstructionByName(d["constructionName"])
            target = found.get() if found.is_initialized() else self.osm.Construction(self.model)
            if not found.is_initialized():
                target.setName(d["constructionName"])
        if target is None:
            self.warn(f"shading control '{name}' has neither material nor construction, skipped")
            return None
        ctl = self.osm.ShadingControl(target)
        ctl.setName(name)
        for setter, value in (
            ("setShadingType", d.get("shadingType")),
            ("setShadingControlType", d.get("shadingControlType")),
            ("setMultipleSurfaceControlType", d.get("multipleSurfaceControlType")),
            ("setTypeofSlatAngleControlforBlinds", d.get("slatAngleControl")),
            ("setSetpoint", d.get("setpoint")),
            ("setSetpoint2", d.get("setpoint2")),
            ("setGlareControlIsActive", d.get("glareControlIsActive")),
        ):
            if (
                value is not None
                and not getattr(ctl, setter)(value)
                and setter not in ("setSetpoint2", "setTypeofSlatAngleControlforBlinds")
            ):  # only apply to some types
                self.warn(f"shading control '{name}': {setter[3:]} '{value}' rejected")
        self.cache[key] = ctl
        return ctl

    def apply_shading_controls(self):
        defs = self.scene.scene_extras.get("shadingControls") or {}
        setter = "setShadingControl" if hasattr(self.osm.SubSurface, "setShadingControl") else "addShadingControl"
        for name in defs:  # also controls no sub surface refers to (e.g. made by the Add Shading Controls script)
            self.shading_control(name, defs)
        for ss, name in self.pending_shading:
            ctl = self.shading_control(name, defs)
            if ctl is not None and not getattr(ss, setter)(ctl):
                self.warn(f"{ss.nameString()}: could not set shading control '{name}'")

    def fill_parent_notches(self):
        for parent, polys in self.sub_polys.items():
            poly, _n, _sp = self.surface_polys[parent]
            full = fill_notches(poly, polys)
            if len(full) != len(poly) or any(a != b for a, b in zip(full, poly)):
                if self.surfaces[parent].setVertices(self.pts(full)):
                    self.surface_polys[parent] = (full, *self.surface_polys[parent][1:])
                else:
                    self.warn(f"{parent}: could not restore notch from sub surfaces")

    def add_shading(self, f: Face, t: str):
        ex = f.extras
        gtype = schema.SHADING_TYPES[t]
        spname = _s(ex, "spaceName") if gtype == "Space" else ""
        key = (gtype, spname)
        if key not in self.shading_groups:
            g = self.osm.ShadingSurfaceGroup(self.model)
            g.setShadingSurfaceType(gtype)
            g.setName(f"{gtype} Shading Group" + (f" {spname}" if spname else ""))
            if gtype == "Space":
                g.setSpace(self.space(spname or self.opts.default_space_name, ex))
            self.shading_groups[key] = g
        poly = self.sanitize(f.polygon, f.name)
        if poly is None:
            return
        sh = self.osm.ShadingSurface(self.pts(poly), self.model)
        sh.setName(_s(ex, "shadingName") or f.name)
        sh.setShadingSurfaceGroup(self.shading_groups[key])
        self.apply_construction(sh, ex)
        self.report.shading_surfaces += 1

    def add_partition(self, f: Face):
        ex = f.extras
        spname = _s(ex, "spaceName") or self.opts.default_space_name
        if spname not in self.partition_groups:
            g = self.osm.InteriorPartitionSurfaceGroup(self.model)
            g.setName(f"Partition Group {spname}")
            g.setSpace(self.space(spname, ex))
            self.partition_groups[spname] = g
        poly = self.sanitize(f.polygon, f.name)
        if poly is None:
            return
        p = self.osm.InteriorPartitionSurface(self.pts(poly), self.model)
        p.setName(f.name)
        p.setInteriorPartitionSurfaceGroup(self.partition_groups[spname])
        self.apply_construction(p, ex)
        self.report.partitions += 1

    _SURF_GETTERS = {"Wall": "wallConstruction", "Floor": "floorConstruction", "RoofCeiling": "roofCeilingConstruction"}
    _SUB_GETTERS = {
        "FixedWindow": "fixedWindowConstruction",
        "OperableWindow": "operableWindowConstruction",
        "Door": "doorConstruction",
        "GlassDoor": "glassDoorConstruction",
        "OverheadDoor": "overheadDoorConstruction",
        "Skylight": "skylightConstruction",
        "TubularDaylightDome": "tubularDaylightDomeConstruction",
        "TubularDaylightDiffuser": "tubularDaylightDiffuserConstruction",
    }

    def fill_missing_constructions(self):
        """EnergyPlus needs a construction on every (sub) surface ("Missing required property 'construction_name'").

        Surfaces without an explicit construction normally inherit one from the default construction set, but a set often
        has gaps (exterior floors, ground walls, adiabatic surfaces, doors...) and gbXML files may omit constructions.
        Those silently get the closest construction of the model's default construction sets (the building's set first);
        with sets present, what still cannot be resolved is reported as a warning.
        """
        sets = list(self.model.getDefaultConstructionSets())
        bset = self.model.getBuilding().defaultConstructionSet()
        if bset.is_initialized():
            sets.sort(key=lambda cs: cs.handle() != bset.get().handle())  # building's set first
        pools: dict[str, list] = {}

        def add(key, opt):
            if opt.is_initialized():
                pools.setdefault(key, []).append(opt.get())

        for cs in sets:
            for grp in (
                cs.defaultExteriorSurfaceConstructions(),
                cs.defaultInteriorSurfaceConstructions(),
                cs.defaultGroundContactSurfaceConstructions(),
            ):
                if grp.is_initialized():
                    for key, getter in self._SURF_GETTERS.items():
                        add(key, getattr(grp.get(), getter)())
            add("Adiabatic", cs.adiabaticSurfaceConstruction())
            for grp in (cs.defaultExteriorSubSurfaceConstructions(), cs.defaultInteriorSubSurfaceConstructions()):
                if grp.is_initialized():
                    for key, getter in self._SUB_GETTERS.items():
                        add(key, getattr(grp.get(), getter)())

        def first(*keys):
            return next((pools[k][0] for k in keys if pools.get(k)), None)

        missing = []
        for s in self.model.getSurfaces():
            if s.construction().is_initialized():
                continue
            c = (
                first("Adiabatic", s.surfaceType())
                if s.outsideBoundaryCondition() == "Adiabatic"
                else first(s.surfaceType())
            )
            if c is None or not s.setConstruction(c):
                missing.append(s.nameString())
        for ss in self.model.getSubSurfaces():
            if ss.construction().is_initialized():
                continue
            t = ss.subSurfaceType()
            window = "Window" in t or t == "Skylight" or t.startswith("Tubular")
            c = first(t, "FixedWindow") if window else first(t, "Door", "GlassDoor")
            if c is None or not ss.setConstruction(c):
                missing.append(ss.nameString())
        if missing and sets:  # without any default set the layer-less / name-only mode is intended
            self.warn(
                f"{len(missing)} surface(s) have no construction and no default construction set provides one "
                f"(EnergyPlus will fail): {', '.join(missing[:5])}{', ...' if len(missing) > 5 else ''}. "
                f"Load a template or set a construction."
            )

    def resolve_adjacency(self):
        for kind, name, other in self.pending_adjacent:
            a = (self.surfaces if kind == "surface" else self.sub_surfaces).get(name)
            b = (self.surfaces if kind == "surface" else self.sub_surfaces).get(other)
            if a is None or b is None:
                self.warn(f"{name}: adjacent object '{other}' not found")
                if kind == "surface" and a is not None:
                    a.assignDefaultBoundaryCondition()
                continue
            ok = a.setAdjacentSurface(b) if kind == "surface" else a.setAdjacentSubSurface(b)
            if not ok:
                self.warn(f"{name}: could not set adjacency to '{other}'")
        self.check_adjacency()

    def check_adjacency(self):
        """Every 'Surface' boundary needs a partner that points back (the plugin's check_model_object).

        A surface may be its own partner (story multipliers). Broken pairs, e.g. when a surface was matched
        to two others, are repaired where possible and otherwise reset to the default boundary condition.
        """
        for name, s in self.surfaces.items():
            adj = s.adjacentSurface()
            if not adj.is_initialized():
                if s.outsideBoundaryCondition() == "Surface":
                    self.warn(f"{name}: boundary 'Surface' without an adjacent surface, reset to default")
                    s.assignDefaultBoundaryCondition()
                    s.assignDefaultSunExposure()
                    s.assignDefaultWindExposure()
                continue
            other = adj.get()
            back = other.adjacentSurface()
            if not back.is_initialized() or back.get().handle() != s.handle():
                self.warn(f"{name}: adjacent surface '{other.nameString()}' does not point back, fixed")
                if not s.setAdjacentSurface(other):
                    s.resetAdjacentSurface()
                    s.assignDefaultBoundaryCondition()
            elif s.outsideBoundaryCondition() != "Surface":
                self.warn(f"{name}: has an adjacent surface but boundary '{s.outsideBoundaryCondition()}', fixed")
                s.setOutsideBoundaryCondition("Surface")
        self.check_sub_adjacency()

    def check_sub_adjacency(self):
        """Every sub surface that names an adjacent sub surface needs one that points back (SubSurface.rb)."""
        for name, ss in self.sub_surfaces.items():
            adj = ss.adjacentSubSurface()
            if not adj.is_initialized():
                continue
            other = adj.get()
            back = other.adjacentSubSurface()
            if not back.is_initialized() or back.get().handle() != ss.handle():
                self.warn(f"{name}: adjacent sub surface '{other.nameString()}' does not point back, fixed")
                if not ss.setAdjacentSubSurface(other):
                    ss.resetAdjacentSubSurface()


def build_model(scene: GltfScene, opts: BuildOptions | None = None):
    """GltfScene -> (openstudio.model.Model, BuildReport)."""
    b = _Builder(scene, opts or BuildOptions())
    report = b.run()
    return b.model, report


def scene_to_osm(scene: GltfScene, osm_path, opts: BuildOptions | None = None) -> BuildReport:
    """Build the model from already collected faces (e.g. straight from Blender meshes) and save the .osm."""
    model, report = build_model(scene, opts or BuildOptions())
    import openstudio

    _localize_weather_file(model, osm_path, report)
    if not model.save(openstudio.path(str(osm_path)), True):
        raise RuntimeError(f"could not write {osm_path}")
    return report


def _localize_weather_file(model, osm_path, report: BuildReport):
    """Copy the .epw to ``<osm dir>/files`` and reference it by file name, as the OpenStudio app does.

    The app resolves a weather file through the model's file paths (``<osm name>/files``, ...), so an absolute
    path to some other folder shows up as "not set" in its GUI.
    """
    import shutil
    from pathlib import Path

    osm_path = Path(osm_path)
    osw = osm_path.resolve().parent / osm_path.stem / "workflow.osw"
    try:
        wf = model.getWeatherFile()
        url = wf.url()
        src = Path(url.get()) if url.is_initialized() else None
        if src is None or not src.is_file():
            # the osw's weather_file replaces the model's weather file when the app opens it, so a stale one
            # (from an earlier export) must not survive
            _write_workflow(osw, osm_path.name, None)
            return
        # the OpenStudio app resolves it in <osm name>/files (its project folder), the CLI/measures in ./files
        base = osm_path.resolve().parent
        for files in (base / "files", base / osm_path.stem / "files"):
            files.mkdir(parents=True, exist_ok=True)
            dst = files / src.name
            if not (dst.exists() and dst.samefile(src)):
                shutil.copy2(src, dst)
        _write_workflow(osw, osm_path.name, src.name)
        o = wf.iddObject()
        idx = next(i for i in range(o.numFields()) if o.getField(i).get().name() == "Url")
        if not wf.setString(idx, src.name):
            raise RuntimeError("could not set the Url field")
    except Exception as e:
        report.warnings.append(f"weather file: could not copy next to the .osm ({e})")


def _write_workflow(osw_path, osm_name: str, epw_name: str | None):
    """``<osm name>/workflow.osw`` as the OpenStudio app writes it: it links the seed model and the weather file.

    Per the OSW spec a ``weather_file`` in the osw replaces the weather file of the seed model before
    measures run, so the osw must always agree with the model: ``epw_name`` is set, or (None) any stale
    ``weather_file`` is removed. An osw already written by the app is only updated; with no weather file
    and no existing osw nothing is written.
    """
    import json
    from datetime import datetime

    now = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    try:
        data = json.loads(osw_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if epw_name is None:
            return
        data = {"created_at": now, "steps": []}
    data.update(seed_file=f"../{osm_name}", updated_at=now)
    if epw_name:
        data["weather_file"] = epw_name
    else:
        data.pop("weather_file", None)
    osw_path.parent.mkdir(parents=True, exist_ok=True)
    osw_path.write_text(json.dumps(data, indent=3), encoding="utf-8")


def scene_to_gbxml(scene: GltfScene, gbxml_path, opts: BuildOptions | None = None) -> BuildReport:
    """Build the model like `scene_to_osm`, then write it with OpenStudio's gbXML forward translator."""
    model, report = build_model(scene, opts or BuildOptions())
    import openstudio

    ft = openstudio.gbxml.GbXMLForwardTranslator()
    if not ft.modelToGbXML(model, openstudio.path(str(gbxml_path))):
        errs = "; ".join(e.logMessage() for e in ft.errors())
        raise RuntimeError(f"could not write {gbxml_path}" + (f": {errs}" if errs else ""))
    report.warnings.extend(w.logMessage() for w in ft.warnings())
    return report


def scene_to_idf(scene: GltfScene, idf_path, opts: BuildOptions | None = None) -> BuildReport:
    """Build the model like `scene_to_osm`, then write an EnergyPlus .idf with OpenStudio's forward translator."""
    model, report = build_model(scene, opts or BuildOptions())
    import openstudio

    ft = openstudio.energyplus.ForwardTranslator()
    workspace = ft.translateModel(model)
    if not workspace.save(openstudio.path(str(idf_path)), True):
        errs = "; ".join(e.logMessage() for e in ft.errors())
        raise RuntimeError(f"could not write {idf_path}" + (f": {errs}" if errs else ""))
    report.warnings.extend(w.logMessage() for w in ft.warnings())
    return report


def scene_to_idf_untranslated(scene: GltfScene, idf_path, opts: BuildOptions | None = None) -> BuildReport:
    """The model's OS: objects as an IDF text file, without the EnergyPlus translation (Export Untranslated Idf)."""
    model, report = build_model(scene, opts or BuildOptions())
    import openstudio

    if not model.toIdfFile().save(openstudio.path(str(idf_path)), True):
        raise RuntimeError(f"could not write {idf_path}")
    return report


def scene_to_sdd(scene: GltfScene, sdd_path, opts: BuildOptions | None = None) -> BuildReport:
    """Build the model like `scene_to_osm`, then write a California SDD (.xml) with OpenStudio's SDD translator."""
    model, report = build_model(scene, opts or BuildOptions())
    import openstudio

    # OpenStudio 3.11's SDD translator crashes (access violation) on an empty OS:ClimateZones, which the builder adds
    for cz in list(model.getObjectsByType(openstudio.IddObjectType("OS:ClimateZones"))):
        cz.remove()
    ft = openstudio.sdd.SddForwardTranslator()
    if not ft.modelToSDD(model, openstudio.path(str(sdd_path))):
        errs = "; ".join(e.logMessage() for e in ft.errors())
        raise RuntimeError(f"could not write {sdd_path}" + (f": {errs}" if errs else ""))
    report.warnings.extend(w.logMessage() for w in ft.warnings())
    return report


def gltf_to_osm(gltf_path, osm_path, opts: BuildOptions | None = None) -> BuildReport:
    """Standalone glTF -> .osm conversion."""
    opts = opts or BuildOptions()
    scene = read_gltf(gltf_path, y_up=opts.y_up, scale=opts.scale, overrides=opts.overrides)
    return scene_to_osm(scene, osm_path, opts)


def gltf_to_gbxml(gltf_path, gbxml_path, opts: BuildOptions | None = None) -> BuildReport:
    """Standalone glTF -> gbXML conversion."""
    opts = opts or BuildOptions()
    scene = read_gltf(gltf_path, y_up=opts.y_up, scale=opts.scale, overrides=opts.overrides)
    return scene_to_gbxml(scene, gbxml_path, opts)
