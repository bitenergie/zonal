"""Blender operators, panels and preferences (imports bpy)."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..core.osm_builder import BuildOptions, BuildReport
from ..core.tools_core import decode_extra_templates
from .icons import PanelIcon, ik

ADDON_ID = __package__.rsplit(".", 1)[0]  # "zonal" or "bl_ext.<repo>.zonal"


def prefs():
    try:
        return bpy.context.preferences.addons[ADDON_ID].preferences
    except (KeyError, AttributeError):
        return None


LAST_MESSAGES: list[str] = []  # every warning of the last export / import ("Show Errors and Warnings")
REPORT_BANNER_MAX = 10


def report_messages(op, warnings) -> None:
    """Remember all warnings of an export / import and show the first few as status bar messages."""
    LAST_MESSAGES[:] = [str(w) for w in warnings]
    p = prefs()
    if p is not None and not p.show_warnings:
        return
    limit = p.warning_banner_max if p is not None else REPORT_BANNER_MAX
    for w in LAST_MESSAGES[:limit]:
        op.report({"WARNING"}, w)
    if len(LAST_MESSAGES) > limit:
        op.report({"WARNING"}, f"{len(LAST_MESSAGES) - limit} more: File > Show Errors and Warnings")


class SCENE_OT_osm_show_warnings(bpy.types.Operator):
    bl_idname = "scene.osm_show_warnings"
    bl_label = "Show Errors and Warnings"
    bl_description = (
        "List every warning of the last OpenStudio export / import (also written to the text 'OpenStudio Report')"
    )

    def invoke(self, context, event):
        text = bpy.data.texts.get("OpenStudio Report") or bpy.data.texts.new("OpenStudio Report")
        text.clear()
        text.write("\n".join(LAST_MESSAGES) if LAST_MESSAGES else "No warnings.")
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        col = self.layout.column(align=True)
        if not LAST_MESSAGES:
            col.label(text="No warnings in the last export / import.", icon="CHECKMARK")
            return
        col.label(text=f"{len(LAST_MESSAGES)} warning(s):", icon="ERROR")
        for w in LAST_MESSAGES[:20]:
            col.label(text=w if len(w) < 90 else w[:87] + "...")
        if len(LAST_MESSAGES) > 20:
            col.label(text="... see the text 'OpenStudio Report'")


def _on_unit_system(self, context):
    """Switch the current scene's display units (the stored geometry stays in metres)."""
    us = context.scene.unit_settings
    if self.unit_system == "IP":
        us.system, us.length_unit = "IMPERIAL", "FEET"
    else:
        us.system, us.length_unit = "METRIC", "METERS"


def _on_path_changed(self, context):
    global _status
    _status = None


class OSMPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    openstudio_path: StringProperty(
        name="OpenStudio Install",
        subtype="DIR_PATH",
        update=_on_path_changed,
        description="Only needed when the extension was installed without the bundled wheel: an OpenStudio install "
        "folder (contains Python/openstudio.py and bin/) whose Python version matches Blender's",
    )

    openstudio_app_path: StringProperty(
        name="OpenStudio Application",
        subtype="FILE_PATH",
        default="",
        description="OpenStudioApp executable used by 'Open in OpenStudioApplication' "
        "(e.g. C:\\openstudioapplication-<version>\\bin\\OpenStudioApp.exe)",
    )

    show_warnings: BoolProperty(
        name="Show Warnings After Export / Import",
        default=True,
        description="Report the translation warnings (first ten) in the status bar. "
        "All of them stay available through 'Show Errors and Warnings'",
    )
    new_zone_for_space: BoolProperty(
        name="New Thermal Zone for Each New Space",
        default=True,
        description="Every space gets a thermal zone named like it: default of 'Create Spaces From Diagram', "
        "and on export for spaces that have none",
    )
    auto_refresh_render: BoolProperty(
        name="Auto-Update Render Mode",
        default=True,
        description="While a render mode (colour by surface type, zone, ...) is on, re-colour the viewport "
        "automatically after every scene change. Turn off for very large scenes",
    )
    warning_banner_max: IntProperty(
        name="Warnings Shown in Status Bar",
        default=REPORT_BANNER_MAX,
        min=1,
        max=100,
        description="How many translation warnings are reported after an export / import (the rest stay in 'Show Errors and Warnings')",
    )
    snap_radius_px: FloatProperty(
        name="Snap Radius",
        default=14.0,
        min=2.0,
        max=60.0,
        subtype="PIXEL",
        description="Draw tools: pixel distance within which the cursor snaps to a vertex, midpoint or edge",
    )
    snap_max_edges: IntProperty(
        name="Snap Edge Budget",
        default=400_000,
        min=1000,
        description="Draw tools: maximum number of scene edges cached for snapping (objects beyond it are not snapped to)",
    )
    displace_offset: FloatProperty(
        name="Sub Surface Offset",
        default=0.08,
        min=0.0,
        unit="LENGTH",
        description="Viewport-only offset of sub surfaces along their normal (avoids z-fighting with the host surface). "
        "Applies to sub surfaces created or changed afterwards",
    )
    plane_fix_tol: FloatProperty(
        name="Out-of-Plane Tolerance",
        default=1e-4,
        min=0.0,
        precision=5,
        unit="LENGTH",
        description="Export: vertices further than this (metres) from the best-fit plane are projected onto it",
    )
    plane_warn_dist: FloatProperty(
        name="Out-of-Plane Warning Distance",
        default=0.0254,
        min=0.0,
        precision=4,
        unit="LENGTH",
        description="Export: projecting a vertex further than this (metres) is reported as a warning",
    )
    sub_plane_dist: FloatProperty(
        name="Sub Surface Plane Distance",
        default=0.05,
        min=0.0,
        precision=4,
        unit="LENGTH",
        description="Export: a sub surface within this distance (metres) of its base surface plane still belongs to it",
    )
    sub_parallel: FloatProperty(
        name="Sub Surface Parallel Limit",
        default=0.98,
        min=0.0,
        max=1.0,
        description="Export: minimum |cos| between sub surface and base surface normals for the relaxed parent search",
    )
    disable_user_scripts: BoolProperty(
        name="Disable OpenStudio Scripts",
        default=False,
        description="Hide the Scripts panel's Run button (the scripts change the model)",
    )
    unit_system: EnumProperty(
        name="Unit System",
        items=[("SI", "SI", "Metric display units"), ("IP", "IP", "Imperial display units (feet, inches)")],
        default="SI",
        update=_on_unit_system,
        description="Display units of the current scene. Exported models are always SI",
    )

    def draw(self, context):
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False
        self.layout.label(text="Zonal", **ik("logo"))
        self.layout.prop(self, "openstudio_path")
        self.layout.prop(self, "openstudio_app_path")
        self.layout.prop(self, "unit_system")
        self.layout.prop(self, "show_warnings")
        self.layout.prop(self, "new_zone_for_space")
        self.layout.prop(self, "auto_refresh_render")
        self.layout.prop(self, "warning_banner_max")
        self.layout.prop(self, "disable_user_scripts")
        header, body = self.layout.panel("osm_prefs_draw", default_closed=True)
        header.label(text="Draw Tools")
        if body is not None:
            for key in ("snap_radius_px", "snap_max_edges", "displace_offset"):
                body.prop(self, key)
        header, body = self.layout.panel("osm_prefs_repair", default_closed=True)
        header.label(text="Geometry Repair on Export")
        if body is not None:
            for key in ("plane_fix_tol", "plane_warn_dist", "sub_plane_dist", "sub_parallel"):
                body.prop(self, key)
        ok, msg = backend_status()
        self.layout.label(text=msg, icon="CHECKMARK" if ok else "ERROR")


# --- openstudio discovery ------------------------------------------------------------

_status: tuple[bool, str] | None = None  # cached: panels redraw constantly


def load_openstudio():
    """The `openstudio` module: bundled wheel first, else the install set in the preferences."""
    from ..core import openstudio_env

    p = prefs()
    return openstudio_env.load(bpy.path.abspath(p.openstudio_path) if p and p.openstudio_path else "")


def backend_status() -> tuple[bool, str]:
    global _status
    if _status is None:
        try:
            _status = True, f"OpenStudio {load_openstudio().openStudioLongVersion()}"
        except ImportError as e:
            _status = False, str(e)
    return _status


# --- export --------------------------------------------------------------------------


SPACE_ATTR_KEYS = (  # properties of the Space (one value per space), not of a single surface
    "spaceTypeName",
    "buildingStoryName",
    "buildingUnitName",
    "constructionSetName",
    "thermalZoneName",
)
ZONE_ATTR_KEYS = ("airLoopHVACNames",)  # properties of the Thermal Zone: one value per zone (all its spaces)
FORCED_KEY = "!space"  # overrides[name][FORCED_KEY] = {extra: value} that beat the object's own extras
SPACE_CONFLICTS: list[str] = []  # warnings of the last `default_space_overrides` call


def default_space_overrides(objects) -> dict[str, dict]:
    """Per mesh object: its extras overrides. The space of a surface is its (original) collection.

    Space attributes live on the Space (Inspector store, which wins in the builder). Legacy per-surface values
    (`SPACE_ATTR_KEYS`) are only a fallback: one value per space (first non-empty, objects sorted by name) is
    forced onto all its objects, and differing values are reported in `SPACE_CONFLICTS`.
    """
    from ..core.tools_core import LOOSE_COLLECTION
    from .inspector import get_item
    from .scene import custom_props

    by_zone: dict[str, list] = {}
    SPACE_CONFLICTS.clear()
    root = bpy.context.scene.collection
    out: dict[str, dict] = {}
    groups: dict[str, list] = {}
    for ob in objects:
        if ob.type != "MESH":
            continue
        users = ob.users_collection
        if any(c.name == LOOSE_COLLECTION for c in users):
            continue
        cols = [c for c in users if c != root]
        extras: dict = {}
        own = ob.get("spaceName")
        own = own if isinstance(own, str) else ""
        space = cols[0].name if cols else own
        if cols and own != cols[0].name and ob.get("surfaceType") != "SpaceShading":
            # the collection IS the space of a surface; a stale `spaceName` property must not move it
            extras[FORCED_KEY] = {"spaceName": cols[0].name}
        out[ob.name] = extras
        if space:
            groups.setdefault(space, []).append(ob)
    for space, obs in groups.items():
        obs.sort(key=lambda o: o.name)
        props = {o.name: {**custom_props(o.data), **custom_props(o)} for o in obs}
        forced = {}
        for key in SPACE_ATTR_KEYS:
            vals = [(o.name, props[o.name][key]) for o in obs if props[o.name].get(key) not in (None, "")]
            if not vals:
                continue
            forced[key] = vals[0][1]
            diff = sorted({str(v) for _n, v in vals if v != vals[0][1]})
            if diff:
                SPACE_CONFLICTS.append(
                    f"Space '{space}': {key} differs between its objects; using '{vals[0][1]}' "
                    f"(also found: {', '.join(diff)}). Run 'Move Values to Spaces' in the Inspector."
                )
        if forced:
            for o in obs:
                out[o.name].setdefault(FORCED_KEY, {}).update(forced)
        item = get_item(bpy.context.scene, "Space", space)
        zone = (getattr(item, "thermalZone", "") if item is not None else "") or forced.get("thermalZoneName", "")
        if zone:
            by_zone.setdefault(zone, []).extend((o, props[o.name]) for o in obs)
    for zone, members in by_zone.items():  # air loops serve a thermal zone: one value for all its spaces
        zitem = get_item(bpy.context.scene, "ThermalZone", zone)
        stored = [n.strip() for n in (getattr(zitem, "airLoops", "") or "").split(",") if n.strip()]
        if stored:  # the zone's own setting wins over any per-surface leftovers
            for o, _p in members:
                out[o.name].setdefault(FORCED_KEY, {})["airLoopHVACNames"] = {str(i): n for i, n in enumerate(stored)}
            continue
        for key in ZONE_ATTR_KEYS:
            vals = [p[key] for _o, p in members if p.get(key) not in (None, "")]
            if not vals:
                continue
            diff = sorted({str(v) for v in vals if v != vals[0]})
            if diff:
                SPACE_CONFLICTS.append(
                    f"Thermal zone '{zone}': {key} differs between its objects; using '{vals[0]}' "
                    f"(also found: {', '.join(diff)})."
                )
            for o, _p in members:
                out[o.name].setdefault(FORCED_KEY, {})[key] = vals[0]
    return out


DISPLACE_NAME = "OSM Displace"
DISPLACE_OFFSET = 0.08  # scene units along the normal; avoids z-fighting between sub surface and host


def sync_subsurface_displace(ob) -> None:
    """Sub surfaces get a viewport-only Displace modifier (small normal offset); other objects lose ours."""
    if ob.type != "MESH":
        return
    from ..core import schema

    mod = ob.modifiers.get(DISPLACE_NAME)
    if ob.get("surfaceType") in schema.SUBSURFACE_TYPES:
        if mod is None:
            mod = ob.modifiers.new(DISPLACE_NAME, "DISPLACE")
            mod.mid_level = 0.0
            p = prefs()
            mod.strength = p.displace_offset if p is not None else DISPLACE_OFFSET
            mod.direction = "NORMAL"
            mod.show_render = False
    elif mod is not None:
        ob.modifiers.remove(mod)


def export_gltf(context, filepath: str, use_selection: bool):
    """Blender's glTF exporter with the OpenStudio-friendly options (standalone glTF export)."""
    # the offset is a viewing aid only: switch our modifiers off while the exporter applies the others
    muted = []
    for ob in context.scene.objects:
        sync_subsurface_displace(ob)
        mod = ob.modifiers.get(DISPLACE_NAME) if ob.type == "MESH" else None
        if mod is not None and mod.show_viewport:
            mod.show_viewport = False
            muted.append(mod)
    try:
        return _export_gltf(context, filepath, use_selection)
    finally:
        for mod in muted:
            mod.show_viewport = True


def _export_gltf(context, filepath: str, use_selection: bool):
    kwargs = dict(
        filepath=filepath,
        export_format="GLB",
        export_extras=True,
        export_yup=False,
        use_selection=use_selection,
        export_apply=True,
        export_cameras=False,
        export_lights=False,
        export_materials="NONE",
        export_normals=False,
        export_texcoords=False,
        export_animations=False,
        export_skins=False,
        export_morph=False,
    )
    # tolerate option renames between Blender releases
    props = bpy.ops.export_scene.gltf.get_rna_type().properties  # ty: ignore[missing-argument]
    kwargs = {k: v for k, v in kwargs.items() if k in props}
    return bpy.ops.export_scene.gltf(**kwargs)


def export_targets(context, use_selection: bool) -> dict[str, dict]:
    """{object name: default extras} of the objects that count for an export (loose geometry is excluded)."""
    objs = list(context.selected_objects if use_selection else context.scene.objects)
    return default_space_overrides(objs)


def build_options(
    context, overrides: dict, *, scale: float, create_constructions: bool = True, match_surfaces: bool = False
) -> BuildOptions:
    sc = context.scene
    template = bpy.path.abspath(sc.osm_template_path) if getattr(sc, "osm_template_path", "") else ""
    ttypes = sorted(getattr(sc, "osm_template_types", ())) if template else []
    p = prefs()
    tolerances = (
        {k: getattr(p, k) for k in ("plane_fix_tol", "plane_warn_dist", "sub_plane_dist", "sub_parallel")}
        if p is not None
        else {}
    )
    return BuildOptions(
        **tolerances,
        new_zone_for_space=bool(p is not None and p.new_zone_for_space),
        y_up=False,
        scale=scale,
        create_constructions=create_constructions,
        match_surfaces=match_surfaces,
        overrides=overrides,
        template_osm=template,
        template_types=ttypes,
        extra_templates=[
            (bpy.path.abspath(path), types)
            for path, types in decode_extra_templates(getattr(sc, "osm_extra_templates", ""))
        ],
    )


def scene_faces(context, overrides: dict, scale: float):
    """The scene as faces for `osm_builder`, read directly from the Blender meshes (no glTF file)."""
    from .inspector import export_json  # lazy: blender_inspector imports blender_props
    from .scene import collect_scene

    return collect_scene(context, overrides, scale=scale, store_json=export_json(context.scene))


def run_build(context, osm_path: str, overrides: dict, opts: BuildOptions, fmt: str = "osm") -> BuildReport:
    """Write the scene as .osm (or gbXML) with the OpenStudio API; the faces come straight from the meshes."""
    load_openstudio()
    from ..core.osm_builder import (
        scene_to_gbxml,
        scene_to_idf,
        scene_to_idf_untranslated,
        scene_to_osm,
        scene_to_sdd,
    )

    faces = scene_faces(context, overrides, opts.scale)
    writer = {
        "gbxml": scene_to_gbxml,
        "idf": scene_to_idf,
        "idf_untranslated": scene_to_idf_untranslated,
        "sdd": scene_to_sdd,
    }.get(fmt, scene_to_osm)
    return writer(faces, osm_path, opts)


class _ExportOptions:
    """Options and pipeline shared by the .osm and gbXML exporters (`fmt` selects the writer)."""

    fmt = "osm"

    use_selection: BoolProperty(name="Selection Only", default=False)
    apply_unit_scale: BoolProperty(
        name="Apply Unit Scale",
        default=True,
        description="Multiply coordinates by the scene's Unit Scale so that 1 Blender unit = Unit Scale metres",
    )
    create_constructions: BoolProperty(
        name="Placeholder Constructions",
        default=True,
        description="Create empty constructions for every constructionName found",
    )
    match_surfaces: BoolProperty(
        name="Match Surfaces", default=False, description="Link coincident surfaces of different spaces"
    )

    def writer_format(self) -> str:
        return self.fmt

    def execute(self, context):
        out = Path(self.filepath)
        try:
            overrides = export_targets(context, self.use_selection)
            if not overrides:
                self.report({"ERROR"}, "No mesh objects to export")
                return {"CANCELLED"}
            scale = context.scene.unit_settings.scale_length if self.apply_unit_scale else 1.0
            opts = build_options(
                context,
                overrides,
                scale=scale,
                create_constructions=self.create_constructions,
                match_surfaces=self.match_surfaces,
            )
            report = run_build(context, str(out), overrides, opts, self.writer_format())
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        report_messages(self, [*SPACE_CONFLICTS, *report.warnings])
        self.report({"INFO"}, f"Wrote {out.name}: {report.summary()}")
        return {"FINISHED"}


class EXPORT_SCENE_OT_openstudio_osm(_ExportOptions, bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.openstudio_osm"
    bl_label = "Export OpenStudio Model (.osm)"
    bl_description = (
        "Export mesh objects as OpenStudio surfaces (direct, with the OpenStudio API), "
        "using the OpenStudio custom properties"
    )
    filename_ext = ".osm"
    filter_glob: StringProperty(default="*.osm", options={"HIDDEN"})


class EXPORT_SCENE_OT_openstudio_gbxml(_ExportOptions, bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.openstudio_gbxml"
    bl_label = "Export gbXML (.xml)"
    bl_description = (
        "Export mesh objects as a gbXML file: the OpenStudio model is built first, "
        "then written with OpenStudio's gbXML translator"
    )
    filename_ext = ".xml"
    filter_glob: StringProperty(default="*.xml;*.gbxml", options={"HIDDEN"})
    fmt = "gbxml"


class EXPORT_SCENE_OT_openstudio_idf(_ExportOptions, bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.openstudio_idf"
    bl_label = "Export EnergyPlus IDF (.idf)"
    bl_description = (
        "Export mesh objects as an EnergyPlus .idf: the OpenStudio model is built first, "
        "then written with OpenStudio's EnergyPlus forward translator"
    )
    filename_ext = ".idf"
    filter_glob: StringProperty(default="*.idf", options={"HIDDEN"})
    fmt = "idf"
    untranslated: BoolProperty(
        name="Untranslated",
        default=False,
        description="Write the OpenStudio (OS:) objects as IDF text without the EnergyPlus translation",
    )

    def writer_format(self) -> str:
        return "idf_untranslated" if self.untranslated else "idf"


class EXPORT_SCENE_OT_openstudio_sdd(_ExportOptions, bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.openstudio_sdd"
    bl_label = "Export SDD (.xml)"
    bl_description = (
        "Export mesh objects as a California Simulation Data Dictionary file: the OpenStudio model is built "
        "first, then written with OpenStudio's SDD translator"
    )
    filename_ext = ".xml"
    filter_glob: StringProperty(default="*.xml;*.sdd", options={"HIDDEN"})
    fmt = "sdd"


class SCENE_OT_osm_open_in_app(bpy.types.Operator):
    bl_idname = "scene.osm_open_in_app"
    bl_label = "Open in OpenStudioApplication"
    bl_description = (
        "Export the scene as an .osm (next to the .blend file, or in the temp folder when it is "
        "unsaved) and open it in the OpenStudio Application"
    )

    def execute(self, context):
        import subprocess
        import tempfile

        p = prefs()
        exe = Path(bpy.path.abspath(p.openstudio_app_path)) if p and p.openstudio_app_path else None
        if exe is None or not exe.is_file():
            self.report({"ERROR"}, "Set the OpenStudioApplication executable in the add-on preferences")
            return {"CANCELLED"}
        try:
            overrides = export_targets(context, False)
            if not overrides:
                self.report({"ERROR"}, "No mesh objects to export")
                return {"CANCELLED"}
            blend = Path(bpy.data.filepath) if bpy.data.filepath else None
            folder = blend.parent if blend else Path(tempfile.gettempdir()) / "zonal"
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / f"{blend.stem if blend else 'untitled'}.osm"
            opts = build_options(context, overrides, scale=context.scene.unit_settings.scale_length)
            report = run_build(context, str(out), overrides, opts)
            subprocess.Popen([str(exe), str(out)], cwd=str(exe.parent))
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        report_messages(self, report.warnings)
        self.report({"INFO"}, f"Opened {out} in OpenStudioApplication")
        return {"FINISHED"}


def export_gltf_objects(context, filepath: str, names) -> None:
    """Standalone glTF export of the named objects; the Inspector store travels as the scene extra `osmModel`."""
    from .inspector import export_json  # lazy: blender_inspector imports blender_props

    keep = [o for o in context.view_layer.objects if o.select_get()]
    active = context.view_layer.objects.active
    for o in context.view_layer.objects:
        o.select_set(o.name in names)
    store = export_json(context.scene)
    if store:
        context.scene["osmModel"] = store
    # the glTF has no collections: write the space (and its attributes) on every surface for the file only
    from .inspector import SPACE_ATTR_FIELD, get_item, get_space_attr
    from .surface_ops import space_collection

    temp: list[tuple] = []
    for n in names:
        ob = bpy.data.objects.get(n)
        if ob is None:
            continue
        vals = dict(names[n].get(FORCED_KEY, {})) if isinstance(names, dict) else {}
        space = space_collection(ob)
        if space and ob.get("surfaceType") != "SpaceShading":
            vals["spaceName"] = space
        for key in SPACE_ATTR_FIELD:
            if v := get_space_attr(context.scene, ob, key):
                vals[key] = v
        zitem = get_item(context.scene, "ThermalZone", vals.get("thermalZoneName", ""))
        loops = [n.strip() for n in (getattr(zitem, "airLoops", "") or "").split(",") if n.strip()]
        if loops:  # air loops belong to the thermal zone
            vals["airLoopHVACNames"] = {str(i): n for i, n in enumerate(loops)}
        for k, v in vals.items():
            temp.append((ob, k, ob.get(k)))
            ob[k] = v
    try:
        export_gltf(context, filepath, True)
    finally:
        for ob, k, old in reversed(temp):
            if old is None:
                if k in ob:
                    del ob[k]
            else:
                ob[k] = old
        if "osmModel" in context.scene:
            del context.scene["osmModel"]
        for o in context.view_layer.objects:
            o.select_set(o in keep)
        context.view_layer.objects.active = active


class EXPORT_SCENE_OT_openstudio_gltf(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.openstudio_gltf"
    bl_label = "Export OpenStudio glTF (.glb)"
    bl_description = (
        "Standalone glTF export (Blender's glTF exporter) with the OpenStudio custom properties as "
        "extras. The .osm / gbXML exporters do not use it"
    )
    filename_ext = ".glb"
    filter_glob: StringProperty(default="*.glb", options={"HIDDEN"})
    use_selection: BoolProperty(name="Selection Only", default=False)

    def execute(self, context):
        try:
            names = export_targets(context, self.use_selection)
            if not names:
                self.report({"ERROR"}, "No mesh objects to export")
                return {"CANCELLED"}
            export_gltf_objects(context, self.filepath, names)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Wrote {Path(self.filepath).name}")
        return {"FINISHED"}


# --- import --------------------------------------------------------------------------


def run_read(osm_path: str, fmt: str = "osm") -> dict:
    load_openstudio()
    from ..core.osm_reader import read_gbxml, read_idf, read_osm, read_sdd

    return {"gbxml": read_gbxml, "idf": read_idf, "sdd": read_sdd}.get(fmt, read_osm)(osm_path)


def build_scene(context, data: dict, scale: float, collection_name: str) -> dict[str, int]:
    """Create one mesh object (a single polygon face) per model surface, one collection per space."""
    root = bpy.data.collections.new(collection_name)
    context.scene.collection.children.link(root)
    cols: dict[str, bpy.types.Collection] = {}
    counts: dict[str, int] = {}
    space_values: dict[str, dict[str, str]] = {}
    zone_loops: dict[str, str] = {}
    from .inspector import SPACE_ATTR_FIELD as SPACE_FIELD_OF_KEY

    for item in data["objects"]:
        poly = item["polygon"]
        if len(poly) < 3:
            continue
        label = item["space"] or {"shading": "Shading"}.get(item["kind"], "Unassigned")
        if label not in cols:
            cols[label] = bpy.data.collections.new(label)
            root.children.link(cols[label])
        mesh = bpy.data.meshes.new(item["name"])
        mesh.from_pydata([tuple(c * scale for c in p) for p in poly], [], [tuple(range(len(poly)))])
        mesh.update()
        ob = bpy.data.objects.new(item["name"], mesh)
        cols[label].objects.link(ob)
        zone = item["props"].get("thermalZoneName")
        for k, v in item["props"].items():
            if k == "airLoopHVACNames" and zone:  # air loops serve the zone: stored once on the zone
                from ..core import schema

                names = schema.indexed(v)
                if names:
                    zone_loops.setdefault(zone, ", ".join(names))
            elif k in SPACE_FIELD_OF_KEY and item["space"]:  # space values live on the space, once
                space_values.setdefault(item["space"], {}).setdefault(SPACE_FIELD_OF_KEY[k], v)
            elif k == "spaceName" and item["kind"] != "shading":  # the collection is the space
                continue
            else:
                ob[k] = v
        sync_subsurface_displace(ob)
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    if zone_loops:
        from .inspector import ensure_item

        for zone, loops in zone_loops.items():
            ensure_item(context.scene, "ThermalZone", zone).airLoops = loops
    if space_values:
        from .inspector import ensure_item

        for space, fields in space_values.items():
            store = ensure_item(context.scene, "Space", space)
            for field, v in fields.items():
                setattr(store, field, v)
    context.scene["northAxis"] = float(data.get("northAxis") or 0.0)
    if data.get("shadingControls"):  # definitions travel as a scene property; sub surfaces refer to them by name
        context.scene["shadingControls"] = data["shadingControls"]
    elif "shadingControls" in context.scene:
        del context.scene["shadingControls"]
    return counts


class _ImportOptions:
    fmt = "osm"

    apply_unit_scale: BoolProperty(
        name="Apply Unit Scale",
        default=True,
        description="Divide coordinates (metres) by the scene's Unit Scale, the inverse of the export option",
    )

    def execute(self, context):
        src = Path(self.filepath)
        try:
            data = run_read(str(src), self.fmt)
            scale = 1.0 / (context.scene.unit_settings.scale_length or 1.0) if self.apply_unit_scale else 1.0
            counts = build_scene(context, data, scale, src.stem)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        report_messages(self, data.get("warnings", []))
        self.report(
            {"INFO"}, f"Imported {src.name}: " + (", ".join(f"{n} {k}" for k, n in counts.items()) or "nothing")
        )
        return {"FINISHED"}


class IMPORT_SCENE_OT_openstudio_osm(_ImportOptions, bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.openstudio_osm"
    bl_label = "Import OpenStudio Model (.osm)"
    bl_description = (
        "Import the surfaces, sub surfaces and shading of an OpenStudio model as mesh objects "
        "(one collection per space) carrying the OpenStudio custom properties"
    )
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".osm"
    filter_glob: StringProperty(default="*.osm", options={"HIDDEN"})


class IMPORT_SCENE_OT_openstudio_gbxml(_ImportOptions, bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.openstudio_gbxml"
    bl_label = "Import gbXML (.xml)"
    bl_description = (
        "Import a gbXML file (through OpenStudio's gbXML translator) as mesh objects, "
        "one collection per space, carrying the OpenStudio custom properties"
    )
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".xml"
    filter_glob: StringProperty(default="*.xml;*.gbxml", options={"HIDDEN"})
    fmt = "gbxml"


class IMPORT_SCENE_OT_openstudio_idf(_ImportOptions, bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.openstudio_idf"
    bl_label = "Import EnergyPlus IDF (.idf)"
    bl_description = (
        "Import an EnergyPlus .idf (through OpenStudio's reverse translator) as mesh objects, "
        "one collection per space, carrying the OpenStudio custom properties"
    )
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".idf"
    filter_glob: StringProperty(default="*.idf", options={"HIDDEN"})
    fmt = "idf"


class IMPORT_SCENE_OT_openstudio_sdd(_ImportOptions, bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.openstudio_sdd"
    bl_label = "Import SDD (.xml)"
    bl_description = (
        "Import a California Simulation Data Dictionary file (through OpenStudio's SDD translator) as mesh objects, "
        "one collection per space, carrying the OpenStudio custom properties"
    )
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".xml"
    filter_glob: StringProperty(default="*.xml;*.sdd", options={"HIDDEN"})
    fmt = "sdd"


def gltf_items(scene) -> dict:
    """GltfScene (faces + extras) -> the dict `build_scene` takes (same shape as `osm_reader.read_model`)."""
    from ..core import schema

    objects = []
    for f in scene.faces:
        stype = f.extras.get("surfaceType", "")
        kind = (
            "subsurface"
            if stype in schema.SUBSURFACE_TYPES
            else "shading"
            if stype in schema.SHADING_TYPES
            else "partition"
            if stype == schema.PARTITION_TYPE
            else "surface"
        )
        objects.append(
            {
                "name": f.name,
                "kind": kind,
                "space": str(f.extras.get("spaceName") or ""),
                "polygon": f.polygon,
                "props": dict(f.extras),
            }
        )
    return {"northAxis": float(scene.scene_extras.get("northAxis") or 0.0), "warnings": [], "objects": objects}


class IMPORT_SCENE_OT_openstudio_gltf(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.openstudio_gltf"
    bl_label = "Import OpenStudio glTF (.glb/.gltf)"
    bl_description = (
        "Standalone glTF import: planar faces of the glTF (e.g. from OpenStudio's glTF translator) "
        "become mesh objects carrying the OpenStudio `extras` as custom properties"
    )
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".glb"
    filter_glob: StringProperty(default="*.glb;*.gltf", options={"HIDDEN"})
    apply_unit_scale: BoolProperty(
        name="Apply Unit Scale",
        default=True,
        description="Divide coordinates (metres) by the scene's Unit Scale, the inverse of the export option",
    )

    def execute(self, context):
        from ..core.gltf_reader import read_gltf

        src = Path(self.filepath)
        try:
            data = gltf_items(read_gltf(src))
            scale = 1.0 / (context.scene.unit_settings.scale_length or 1.0) if self.apply_unit_scale else 1.0
            counts = build_scene(context, data, scale, src.stem)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report(
            {"INFO"}, f"Imported {src.name}: " + (", ".join(f"{n} {k}" for k, n in counts.items()) or "nothing")
        )
        return {"FINISHED"}


# --- properties panel ----------------------------------------------------------------

OBJECT_FIELDS = (
    ("surfaceType", "Surface Type"),
    ("spaceName", "Space"),
    ("thermalZoneName", "Thermal Zone"),
    ("spaceTypeName", "Space Type"),
    ("buildingStoryName", "Building Story"),
    ("buildingUnitName", "Building Unit"),
    ("constructionName", "Construction"),
    ("constructionSetName", "Construction Set"),
    ("outsideBoundaryCondition", "Boundary Condition"),
    ("outsideBoundaryConditionObjectName", "Adjacent Object"),
    ("sunExposure", "Sun Exposure"),
    ("windExposure", "Wind Exposure"),
    ("surfaceName", "Parent Surface (sub surfaces)"),
)


class OBJECT_OT_osm_set_prop(bpy.types.Operator):
    bl_idname = "object.osm_set_prop"
    bl_label = "Set OpenStudio Property"
    bl_options = {"REGISTER", "UNDO"}
    key: StringProperty()
    value: StringProperty()

    def execute(self, context):
        from .inspector import SPACE_ATTR_FIELD, set_space_attr
        from .props import assign_space

        for ob in context.selected_objects or [context.object]:
            if not ob or ob.type != "MESH":
                continue
            if self.key == "spaceName" and self.value:  # the space of a surface is its collection
                assign_space(ob, self.value)
            elif self.key in SPACE_ATTR_FIELD:  # one value for the whole space
                set_space_attr(context.scene, ob, self.key, self.value)
            else:
                ob[self.key] = self.value
        return {"FINISHED"}


class OBJECT_PT_openstudio(PanelIcon, bpy.types.Panel):
    osm_icon = "panel_openstudio"
    bl_label = "OpenStudio"
    bl_space_type, bl_region_type, bl_context = "PROPERTIES", "WINDOW", "object"

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH"

    def draw(self, context):
        from .props import draw_object_fields

        draw_object_fields(self.layout, context, context.object)


class SCENE_PT_openstudio(PanelIcon, bpy.types.Panel):
    osm_icon = "panel_openstudio"
    bl_label = "OpenStudio"
    bl_space_type, bl_region_type, bl_context = "PROPERTIES", "WINDOW", "scene"

    def draw(self, context):
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False
        col = self.layout.column()
        ok, msg = backend_status()
        if not ok:  # the version line is gone; still surface a missing backend
            col.label(text=msg, icon="ERROR")
            col.separator()
        main = col.row(align=True)
        main.scale_y = 1.3
        main.operator("export_scene.openstudio_osm", text="Export .osm", **ik("file_export", "EXPORT"))
        main.menu("SCENE_MT_osm_export", text="", icon="DOWNARROW_HLT")
        row = col.row(align=True)
        row.operator("import_scene.openstudio_osm", text="Import .osm", **ik("file_import", "IMPORT"))
        row.menu("SCENE_MT_osm_import", text="", icon="DOWNARROW_HLT")
        header, body = self.layout.panel("osm_scene_tools", default_closed=True)
        header.label(text="Tools")
        if body is not None:
            body.operator("scene.osm_open_in_app", icon="WINDOW")
            body.operator("scene.osm_show_warnings", icon="ERROR")


_FORMATS = (
    ("osm", "OpenStudio Model (.osm)"),
    ("gbxml", "gbXML (.xml)"),
    ("idf", "EnergyPlus (.idf)"),
    ("sdd", "SDD (.xml)"),
    ("gltf", "OpenStudio glTF"),
)


class SCENE_MT_osm_import(bpy.types.Menu):
    bl_label = "Import"

    def draw(self, context):
        for key, label in _FORMATS:
            self.layout.operator(f"import_scene.openstudio_{key}", text=label, icon="IMPORT")


class SCENE_MT_osm_export(bpy.types.Menu):
    bl_label = "Export"

    def draw(self, context):
        for key, label in _FORMATS:
            self.layout.operator(f"export_scene.openstudio_{key}", text=label, icon="EXPORT")


class SCENE_OT_osm_init_props(bpy.types.Operator):
    bl_idname = "scene.osm_init_props"
    bl_label = "Add OpenStudio scene properties"

    def execute(self, context):
        context.scene["northAxis"] = 0.0
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(EXPORT_SCENE_OT_openstudio_osm.bl_idname, text="OpenStudio Model (.osm)")


def menu_func_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_openstudio_osm.bl_idname, text="OpenStudio Model (.osm)")


def menu_func_gltf(self, context):
    self.layout.operator(EXPORT_SCENE_OT_openstudio_gltf.bl_idname, text="OpenStudio glTF (.glb)")


def menu_func_import_gltf(self, context):
    self.layout.operator(IMPORT_SCENE_OT_openstudio_gltf.bl_idname, text="OpenStudio glTF (.glb/.gltf)")


def menu_func_gbxml(self, context):
    self.layout.operator(EXPORT_SCENE_OT_openstudio_gbxml.bl_idname, text="gbXML (.xml) via OpenStudio")


def menu_func_import_gbxml(self, context):
    self.layout.operator(IMPORT_SCENE_OT_openstudio_gbxml.bl_idname, text="gbXML (.xml) via OpenStudio")


def menu_func_sdd(self, context):
    self.layout.operator(EXPORT_SCENE_OT_openstudio_sdd.bl_idname, text="SDD (.xml) via OpenStudio")


def menu_func_import_sdd(self, context):
    self.layout.operator(IMPORT_SCENE_OT_openstudio_sdd.bl_idname, text="SDD (.xml) via OpenStudio")


def menu_func_idf(self, context):
    self.layout.operator(EXPORT_SCENE_OT_openstudio_idf.bl_idname, text="EnergyPlus IDF (.idf) via OpenStudio")


def menu_func_import_idf(self, context):
    self.layout.operator(IMPORT_SCENE_OT_openstudio_idf.bl_idname, text="EnergyPlus IDF (.idf) via OpenStudio")


CLASSES = (
    OSMPreferences,
    SCENE_MT_osm_import,
    SCENE_MT_osm_export,
    EXPORT_SCENE_OT_openstudio_osm,
    IMPORT_SCENE_OT_openstudio_osm,
    EXPORT_SCENE_OT_openstudio_gbxml,
    IMPORT_SCENE_OT_openstudio_gbxml,
    EXPORT_SCENE_OT_openstudio_idf,
    IMPORT_SCENE_OT_openstudio_idf,
    EXPORT_SCENE_OT_openstudio_sdd,
    IMPORT_SCENE_OT_openstudio_sdd,
    SCENE_OT_osm_open_in_app,
    SCENE_OT_osm_show_warnings,
    EXPORT_SCENE_OT_openstudio_gltf,
    IMPORT_SCENE_OT_openstudio_gltf,
    OBJECT_OT_osm_set_prop,
    OBJECT_PT_openstudio,
    SCENE_OT_osm_init_props,
    SCENE_PT_openstudio,
)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_export.append(menu_func)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_gbxml)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_idf)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_idf)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_gltf)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_gltf)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_gbxml)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_sdd)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_sdd)
    from . import tools as blender_tools  # lazy: blender_tools imports this module

    blender_tools.register()


def unregister():
    from . import tools as blender_tools

    blender_tools.unregister()
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_sdd)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_sdd)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_gltf)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_gltf)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_idf)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_idf)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_gbxml)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_gbxml)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    from . import icons as blender_icons

    blender_icons.unregister()
