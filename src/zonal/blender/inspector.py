"""OpenStudio Inspector panel: pick an OpenStudio type, pick an object of that type, edit its fields (imports bpy).

Object-backed types (Surface, SubSurface, ...) edit the mesh objects' custom properties through the `osm_<key>`
proxies of blender_props. Store-backed types (Space, ThermalZone, BuildingStory, construction sets, ...) live in
`Scene.osm_model` and are exported as the scene extra ``osmModel`` (see inspector_schema / osm_builder).
"""

from __future__ import annotations

import time
from typing import Any

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..core import epw
from ..core import inspector_schema as S
from ..core.search_core import class_of
from ..core.tools_core import LIBRARY_CATEGORIES, LOOSE_COLLECTION, unique_name
from . import schedule_ui
from .icons import PanelIcon, ik
from .props import _field, category_names, library_add, library_names
from .surface_ops import _space_of, _surface_type, is_loose
from .widgets import field as draw_field
from .widgets import field_row, split_column

SCENE_ONLY_KEYS = ("northAxis",)  # edited as scene custom property, no RNA property on the store item


# --- data access ----------------------------------------------------------------------


def space_names(scene) -> list[str]:
    """Spaces = collections that directly hold mesh objects (plus the collections that already have store data)."""
    names = []
    for col in scene.collection.children_recursive:
        # an object linked into several collections only counts for its original (first) one
        if col.name != LOOSE_COLLECTION and any(o.type == "MESH" and o.users_collection[0] is col for o in col.objects):
            names.append(col.name)
    return sorted(names)


def store_items(scene, tname: str):
    return [i for i in scene.osm_model if i.type == tname]


def get_item(scene, tname: str, name: str):
    return next((i for i in scene.osm_model if i.type == tname and i.name == name), None)


_models_cache: dict = {"key": None, "models": []}


def template_models(scene) -> list:
    """The loaded template models (main template first, then the imported-resource files); cached per path+mtime."""
    from pathlib import Path

    from ..core import inspector_import
    from ..core.tools_core import decode_extra_templates

    paths = [getattr(scene, "osm_template_path", "")]
    paths += [p for p, _types in decode_extra_templates(getattr(scene, "osm_extra_templates", ""))]
    paths = [bpy.path.abspath(p) for p in paths if p]
    stamp = []
    for p in paths:
        try:
            stamp.append((p, Path(p).stat().st_mtime))
        except OSError:
            stamp.append((p, 0))
    if _models_cache["key"] != stamp:
        osm_paths = []
        for p in paths:
            if p.lower().endswith(".idf"):
                from ..core.osm_reader import idf_to_osm_file

                out = Path(bpy.app.tempdir) / f"{Path(p).stem}_inspector.osm"
                try:
                    idf_to_osm_file(p, out)
                except Exception:
                    continue
                p = str(out)
            osm_paths.append(p)
        _models_cache.update(key=stamp, models=inspector_import.load_models(osm_paths) if osm_paths else [])
    return _models_cache["models"]


def populate_from_template(scene, item) -> bool:
    """Fill a new store item with the values the object has in the template (so existing ones show their properties)."""
    from ..core import inspector_import

    t = S.TYPES.get(item.type)
    if t is None or t.backing != "store" or not t.category:
        return False
    values = inspector_import.read_from_models(template_models(scene), item.type, item.name)
    if values is None:
        return False
    for fd in t.fields:
        if fd.key in values and hasattr(item, fd.key):
            try:
                if fd.kind == S.COLOR:
                    rgb = S.hex_to_rgb(values[fd.key])
                    if rgb is not None:
                        setattr(item, fd.key, rgb)
                        setattr(item, f"{fd.key}_set", True)
                    continue
                setattr(item, fd.key, values[fd.key])
            except (TypeError, ValueError):
                pass
    return True


def ensure_item(scene, tname: str, name: str):
    item = get_item(scene, tname, name)
    if item is None and name:
        item = scene.osm_model.add()
        item.type, item.name = tname, name
        populate_from_template(scene, item)
    return item


def mesh_objects_of(scene, tname: str):
    cls = S.TYPES[tname].surface_class
    out = []
    for ob in scene.objects:
        if (
            ob.type == "MESH"
            and ob.data.polygons
            and not is_loose(ob)
            and class_of(_surface_type(ob, ob.data.polygons[0])) == cls
        ):
            out.append(ob)
    return sorted(out, key=lambda o: o.name)


def object_names(scene, tname: str) -> list[str]:
    t = S.TYPES.get(tname)
    if t is None:
        return []
    if t.backing == "object":
        return [o.name for o in mesh_objects_of(scene, tname)]
    if tname in S.SINGLETONS:
        return [S.SINGLETONS[tname]]
    if tname == "Space":
        return space_names(scene)
    return category_names(scene, t.category) if t.category else sorted(i.name for i in store_items(scene, tname))


def ref_names(scene, category: str) -> list[str]:
    """Names offered by a reference field (same set the object dropdown lists)."""
    return category_names(scene, category)


def export_json(scene) -> str:
    """The store as JSON for the ``osmModel`` scene extra ("" when there is nothing to export)."""
    spaces = set(space_names(scene))
    objs = []
    for item in scene.osm_model:
        t = S.TYPES.get(item.type)
        if t is None or (item.type == "Space" and item.name not in spaces):
            continue
        values = {}
        for fd in t.fields:
            if fd.key in SCENE_ONLY_KEYS:
                continue
            v = getattr(item, fd.key)
            if fd.kind == S.RULES:
                v = schedule_ui.export_rules(item)
            elif fd.kind == S.COLOR:
                v = S.rgb_to_hex(v) if getattr(item, f"{fd.key}_set") else ""
            elif fd.kind == S.FILE and v:
                v = bpy.path.abspath(v)  # "//relative" paths are relative to the .blend
            elif fd.kind in (S.FLOAT, S.AUTOCALC):
                v = round(v, 5)  # drop single-precision noise (39.83 -> 39.830002)
            values[fd.key] = "" if (fd.kind == S.ENUM and v == "DEFAULT") else v
            if fd.kind == S.AUTOCALC:
                values[f"{fd.key}_hard"] = getattr(item, f"{fd.key}_hard")
        objs.append({"type": item.type, "name": item.name, "values": values})
    return S.dumps_model(objs) if objs else ""


# --- RNA ------------------------------------------------------------------------------


def _ref_search(category: str):
    def search(self, context, edit_text):
        return ref_names(context.scene, category)

    return search


def epw_data(item) -> dict | None:
    """Location values stored in the item's .epw file (None: no path or unreadable)."""
    return epw.read_location(bpy.path.abspath(item.epwPath)) if item.epwPath else None


def _differs(item, key: str, file_value) -> bool:
    v = getattr(item, key)
    if isinstance(file_value, float):  # Blender floats are single precision
        return abs(v - file_value) > 1e-5 * max(1.0, abs(file_value))
    return v != file_value


def apply_epw(item, only: str = "") -> bool:
    """Copy the .epw's location values into the item's fields (one field when ``only`` is given)."""
    data = epw_data(item)
    if data is None:
        return False
    for key, value in data.items():
        if not only or key == only:
            setattr(item, key, value)
    return True


def _on_epw(self, context):
    apply_epw(self)


def _make_prop(fd: S.FieldDef) -> dict:
    """Annotations (key -> property) for one field; AUTOCALC adds a ``<key>_hard`` flag."""
    label = f"{fd.label} ({fd.unit})" if fd.unit else fd.label
    if fd.kind == S.REF:
        return {fd.key: StringProperty(name=label, search=_ref_search(fd.ref), search_options={"SUGGESTION"})}
    if fd.kind == S.FILE:
        return {fd.key: StringProperty(name=label, update=_on_epw)}
    if fd.kind == S.RULES:
        return {}  # the `rules` collection is declared by schedule_ui
    if fd.kind == S.COLOR:
        return {
            fd.key: FloatVectorProperty(name=label, subtype="COLOR", size=3, min=0.0, max=1.0, default=(0.8, 0.8, 0.8)),
            f"{fd.key}_set": BoolProperty(
                name="Custom", default=False, description="Use this colour instead of the OpenStudio default"
            ),
        }
    if fd.kind == S.FLOAT:
        return {fd.key: FloatProperty(name=label, default=float(fd.default_value))}
    if fd.kind == S.INT:
        return {fd.key: IntProperty(name=label, default=int(fd.default_value))}
    if fd.kind == S.BOOL:
        return {fd.key: BoolProperty(name=label, default=bool(fd.default_value))}
    if fd.kind == S.ENUM:
        items = [("DEFAULT", "(default)", "")] + [(c, c, "") for c in fd.choices]
        return {fd.key: EnumProperty(name=label, items=items, default="DEFAULT")}
    if fd.kind == S.AUTOCALC:
        return {
            fd.key: FloatProperty(name=label, default=0.0, min=0.0),
            f"{fd.key}_hard": BoolProperty(
                name="Hard Sized", default=False, description="Use the value instead of letting OpenStudio calculate it"
            ),
        }
    return {fd.key: StringProperty(name=label)}


class OSMModelObject(bpy.types.PropertyGroup):
    """One OpenStudio object of the Inspector store; the field properties are added below from inspector_schema."""

    type: StringProperty()
    rules: CollectionProperty(type=schedule_ui.OSMScheduleRule)  # ScheduleConstant: prioritised rule list
    rule_index: IntProperty(default=0)


for _fd in S.ALL_FIELDS.values():
    if _fd.key not in SCENE_ONLY_KEYS:
        OSMModelObject.__annotations__.update(_make_prop(_fd))


def _type_counts(scene) -> dict[str, int]:
    """Number of objects per Inspector type, with ONE pass over the scene objects (not one per type)."""
    surf = {}  # surface class -> count
    used = {cat: set() for cat in LIBRARY_CATEGORIES}
    keys = {cat: entry[0] for cat, entry in LIBRARY_CATEGORIES.items()}
    for ob in scene.objects:
        if ob.type != "MESH":
            continue
        for cat, key in keys.items():
            v = ob.get(key)
            if isinstance(v, str) and v:
                used[cat].add(v)
        if ob.data.polygons and not is_loose(ob):
            cls = class_of(_surface_type(ob, ob.data.polygons[0]))
            surf[cls] = surf.get(cls, 0) + 1
    counts = {}
    for t in S.TYPES.values():
        if t.backing == "object":
            counts[t.name] = surf.get(t.surface_class, 0)
        elif t.name in S.SINGLETONS:
            counts[t.name] = 1
        elif t.name == "Space":
            counts[t.name] = len(space_names(scene))
        elif t.category:
            names = set(library_names(scene, t.category)) | used.get(t.category, set())
            tn = S.type_for_category(t.category)
            if tn:
                names.update(i.name for i in store_items(scene, tn))
            counts[t.name] = len(names)
        else:
            counts[t.name] = len(store_items(scene, t.name))
    return counts


_TYPE_ITEMS_TTL = (
    3.0  # seconds; the counts in the labels may lag a little, the dropdown must not cost a scan per redraw
)
_type_items_state: dict[str, Any] = {"time": 0.0, "scene": None, "n": -1, "items": []}


def _type_items(self, context):
    sc = context.scene
    st = _type_items_state
    now = time.monotonic()
    if st["items"] and st["scene"] == sc.name and st["n"] == len(sc.objects) and now - st["time"] < _TYPE_ITEMS_TTL:
        return st["items"]
    counts = _type_counts(sc)
    items = []
    for group in S.GROUPS:
        if items:
            items.append(None)
        for t in S.TYPES.values():
            if t.group == group:
                items.append((t.name, f"OS:{t.label} ({counts.get(t.name, 0)})", ""))
    st.update(
        time=now, scene=sc.name, n=len(sc.objects), items=items
    )  # keeps the strings alive (Blender only borrows them)
    return items


def _object_search(self, context, edit_text):
    return object_names(context.scene, self.type)


def _select_object(context, name: str):
    ob = bpy.data.objects.get(name)
    if ob is None or ob.name not in context.view_layer.objects:
        return
    for o in context.view_layer.objects:
        if o.select_get():
            o.select_set(False)
    if ob.visible_get():
        ob.select_set(True)
    context.view_layer.objects.active = ob


_syncing = False  # True while the selection sync writes type / object (must not re-select in Blender)
_last_active = ""


def _name_for_type(ob, tname: str) -> str:
    """The name of the `tname` object that surface `ob` belongs to ("" when it has none)."""
    t = S.TYPES.get(tname)
    if t is None or t.backing == "object":
        return ob.name if t is not None else ""
    if tname == "Space":
        return _space_of(ob)
    entry = LIBRARY_CATEGORIES.get(t.category)
    if entry and entry[0] in SPACE_ATTR_FIELD:
        return get_space_attr(bpy.context.scene, ob, entry[0])
    v = ob.get(entry[0]) if entry else None
    return v if isinstance(v, str) else ""


# --- space attributes: one value per space (Space store item), never per surface -------------

SPACE_ATTR_FIELD = {  # custom-property key (schema) -> field of the Space store item
    "spaceTypeName": "spaceType",
    "thermalZoneName": "thermalZone",
    "buildingStoryName": "buildingStory",
    "buildingUnitName": "buildingUnit",
    "constructionSetName": "defaultConstructionSet",
}


def get_space_attr(scene, ob, key: str) -> str:
    """The value of a space attribute for the space `ob` is in (a legacy per-object value is only a fallback)."""
    item = get_item(scene, "Space", _space_of(ob))
    v = getattr(item, SPACE_ATTR_FIELD[key], "") if item is not None else ""
    if v:
        return v
    legacy = ob.get(key)
    return legacy if isinstance(legacy, str) else ""


def set_space_attr(scene, ob, key: str, value: str) -> None:
    """Set a space attribute on the SPACE of `ob` (all its surfaces). Objects outside any space keep it themselves."""
    space = _space_of(ob)
    if not space:
        if value:
            ob[key] = value
        elif key in ob:
            del ob[key]
        return
    item = ensure_item(scene, "Space", space)
    setattr(item, SPACE_ATTR_FIELD[key], value)
    if key in ob:
        del ob[key]  # drop the legacy per-surface value so it cannot disagree


def sync_from_object(scene, ob) -> None:
    """Point the Inspector at the active surface: its own type for surfaces, its zone / story / space for store types."""
    global _syncing
    if ob is None or ob.type != "MESH" or not ob.data.polygons or is_loose(ob):
        return
    insp = scene.osm_inspector
    t = S.TYPES.get(insp.type)
    if t is None:
        return
    if t.backing == "object":
        cls = class_of(_surface_type(ob, ob.data.polygons[0]))
        tname = next((n for n, d in S.TYPES.items() if d.backing == "object" and d.surface_class == cls), insp.type)
        name = ob.name
    else:
        tname, name = insp.type, _name_for_type(ob, insp.type)
        if not name:
            return
    if (insp.type, insp.object) == (tname, name):
        return
    _syncing = True
    try:
        if insp.type != tname:
            insp.type = tname
        insp.object = name
    finally:
        _syncing = False


@persistent
def _on_depsgraph(scene, depsgraph=None):
    global _last_active
    try:
        ob = bpy.context.view_layer.objects.active
        if ob is None or ob.name == _last_active:
            return
        _last_active = ob.name
        if hasattr(scene, "osm_inspector") and ob.select_get():
            sync_from_object(scene, ob)
        if hasattr(scene, "osm_inspector") and scene.osm_inspector.tree_n != len(scene.objects):
            build_tree(scene)
    except Exception:
        pass


def _on_object(self, context):
    t = S.TYPES.get(self.type)
    if _syncing or t is None or not self.object:
        return
    if t.backing == "store":
        ensure_item(context.scene, self.type, self.object)
    else:
        _select_object(context, self.object)


def _on_type(self, context):
    if _syncing:
        return
    names = object_names(context.scene, self.type)
    self.object = names[0] if names else ""
    _on_object(self, context)


def _get_name(self):
    return self.object


def _set_name(self, value):
    """Rename the current object (mesh object, space collection or store item)."""
    sc = bpy.context.scene
    old, t = self.object, S.TYPES.get(self.type)
    if t is None or not old or not value or value == old:
        return
    if t.backing == "object":
        ob = bpy.data.objects.get(old)
        if ob is not None:
            ob.name = value
            self.object = ob.name
        return
    if self.type in S.SINGLETONS:
        return
    existing = set(object_names(sc, self.type)) - {old}
    new = unique_name(value, existing)
    if self.type == "Space":
        col = bpy.data.collections.get(old)
        if col is not None:
            col.name = new
            new = col.name
        item = get_item(sc, "Space", old)
    else:
        item = get_item(sc, self.type, old)
        entry = LIBRARY_CATEGORIES.get(t.category)
        if entry:  # the old name lives on surfaces and in the library: rename it there too
            for ob in sc.objects:
                if ob.type == "MESH" and ob.get(entry[0]) == old:
                    ob[entry[0]] = new
            for lib in sc.osm_library:
                if lib.category == t.category and lib.name == old:
                    lib.name = new
    if item is not None:
        item.name = new
    self.object = new


# --- hierarchy picker (flattened tree in a UIList; Blender has no Python tree view API) -----------


class OSMTreeRow(bpy.types.PropertyGroup):
    """One row of the hierarchy: `name` = object name, `kind` = Inspector type ("" = group header)."""

    kind: StringProperty()
    label: StringProperty()
    depth: IntProperty()
    expandable: BoolProperty()
    expanded: BoolProperty(default=True)


def _surface_kinds() -> dict[str, str]:
    return {d.surface_class: n for n, d in S.TYPES.items() if d.backing == "object"}


def build_tree(scene) -> None:
    """Rebuild `scene.osm_inspector.tree` from the scene (keeps the collapsed state of rows that still exist)."""
    from ..core import inspector_tree as T
    from .search import _story_of

    insp = scene.osm_inspector
    collapsed = {r.kind + ":" + r.name for r in insp.tree if not r.expanded}
    kinds = _surface_kinds()
    tree: dict[str, dict[str, list[tuple[str, str]]]] = {}
    entries: list[tuple[str, str, str, str]] = []
    space_story: dict[str, str] = {}  # one story per space: first non-empty among its objects
    for ob in scene.objects:
        if ob.type != "MESH" or not ob.data.polygons or is_loose(ob):
            continue
        space = _name_for_type(ob, "Space")
        if not space:
            continue
        kind = kinds.get(class_of(_surface_type(ob, ob.data.polygons[0])), "")
        story = _story_of(scene, ob)
        if story and not space_story.get(space):
            space_story[space] = story
        space_story.setdefault(space, "")
        entries.append((space, ob.name, kind, story))
    for space, name, kind, _story in entries:
        tree.setdefault(space_story[space], {}).setdefault(space, []).append((name, kind))
    insp.tree.clear()
    for r in T.flatten(S.SINGLETONS.get("Building", "Building"), tree):
        item = insp.tree.add()
        item.kind, item.name, item.label, item.depth, item.expandable = r.kind, r.name, r.label, r.depth, r.expandable
        item.expanded = r.key not in collapsed
    insp.tree_n = len(scene.objects)
    insp.tree_index = -1


def _tree_picked(self, context):
    """Clicking a row points the Inspector at that object."""
    if not 0 <= self.tree_index < len(self.tree):
        return
    row = self.tree[self.tree_index]
    if not row.kind or row.kind not in S.TYPES:
        return
    if self.type != row.kind:
        self.type = row.kind
    self.object = row.name


class OSM_UL_inspector_tree(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        for _ in range(item.depth):
            row.label(text="", icon="BLANK1")
        if item.expandable:
            row.prop(item, "expanded", text="", icon="TRIA_DOWN" if item.expanded else "TRIA_RIGHT", emboss=False)
        else:
            row.label(text="", icon="BLANK1")
        row.label(text=item.label)

    def filter_items(self, context, data, propname):
        from ..core import inspector_tree as T

        rows = getattr(data, propname)
        proxies = [T.Row(r.kind, r.name, r.label, r.depth, r.expandable) for r in rows]
        collapsed = {p.key for p, r in zip(proxies, rows, strict=True) if not r.expanded}
        mask = T.visible_mask(proxies, collapsed)
        return [self.bitflag_filter_item if m else 0 for m in mask], []


class SCENE_OT_osm_tree_refresh(bpy.types.Operator):
    bl_idname = "scene.osm_tree_refresh"
    bl_label = "Refresh Hierarchy"
    bl_description = "Rebuild the Building / Story / Space / Surface list from the scene"

    def execute(self, context):
        build_tree(context.scene)
        return {"FINISHED"}


class SCENE_OT_osm_migrate_spaces(bpy.types.Operator):
    bl_idname = "scene.osm_migrate_spaces"
    bl_label = "Move Values to Spaces"
    bl_description = (
        "Older scenes keep space type / zone / story / unit / construction set (and spaceName) on every surface. "
        "Move them to the Space (one value per space) and remove them from the surfaces"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .props import assign_space
        from .surface_ops import space_collection

        scene = context.scene
        relocated = 0
        by_space: dict[str, list] = {}
        for ob in scene.objects:
            if ob.type != "MESH" or is_loose(ob):
                continue
            own = ob.get("spaceName")
            if isinstance(own, str) and own and ob.get("surfaceType") != "SpaceShading":
                if own != space_collection(ob):  # the property used to win over the collection: keep that space
                    assign_space(ob, own)
                    relocated += 1
                del ob["spaceName"]
            if sp := space_collection(ob):
                by_space.setdefault(sp, []).append(ob)
        moved, conflicts = 0, []
        for sp, obs in by_space.items():
            obs.sort(key=lambda o: o.name)
            for key, field in SPACE_ATTR_FIELD.items():
                vals = [v for o in obs for src in (o, o.data) if isinstance(v := src.get(key), str) and v]
                if vals:
                    if len(set(vals)) > 1:
                        conflicts.append(f"{sp}: {key} {sorted(set(vals))} -> '{vals[0]}'")
                    item = ensure_item(scene, "Space", sp)
                    if not getattr(item, field):
                        setattr(item, field, vals[0])
                        moved += 1
                for o in obs:
                    for src in (o, o.data):
                        if key in src:
                            del src[key]
            # air loops serve the thermal zone: move them to the ThermalZone item
            from ..core import schema
            from .scene import plain

            loops = [
                n for o in obs for src in (o, o.data) for n in schema.indexed(plain(src.get("airLoopHVACNames"))) if n
            ]
            zone = get_space_attr(scene, obs[0], "thermalZoneName")
            if loops and zone:
                zitem = ensure_item(scene, "ThermalZone", zone)
                if not zitem.airLoops:
                    zitem.airLoops = ", ".join(dict.fromkeys(loops))
                    moved += 1
                for o in obs:
                    for src in (o, o.data):
                        if "airLoopHVACNames" in src:
                            del src["airLoopHVACNames"]
        if hasattr(scene, "osm_inspector"):
            build_tree(scene)
        for c in conflicts:
            self.report({"WARNING"}, c)
        self.report(
            {"INFO"},
            f"{moved} space value(s) moved, {relocated} surface(s) moved to their space, {len(conflicts)} conflict(s)",
        )
        return {"FINISHED"}


class OSMInspectorSettings(bpy.types.PropertyGroup):
    tree: CollectionProperty(type=OSMTreeRow)
    tree_index: IntProperty(default=-1, update=_tree_picked, options={"HIDDEN", "SKIP_SAVE"})
    tree_n: IntProperty(default=-1, options={"HIDDEN"})  # object count at the last rebuild
    picker: EnumProperty(
        name="Selector",
        items=[
            ("HIERARCHY", "Hierarchy", "Pick the object in the model tree", "OUTLINER", 0),
            ("TYPE", "Type", "Pick a type, then one of its objects", "PRESET", 1),
        ],
        default="HIERARCHY",
    )
    type: EnumProperty(name="Type", items=_type_items, update=_on_type)
    object: StringProperty(name="Object", search=_object_search, search_options={"SUGGESTION"}, update=_on_object)
    name: StringProperty(name="Name", get=_get_name, set=_set_name)


# --- operators ------------------------------------------------------------------------


class SCENE_OT_osm_model_add(bpy.types.Operator):
    bl_idname = "scene.osm_model_add"
    bl_label = "New OpenStudio Object"
    bl_description = "Create a new object of the selected type"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name")

    @classmethod
    def poll(cls, context):
        t = S.TYPES.get(context.scene.osm_inspector.type)
        return t is not None and t.backing == "store" and t.name not in S.NOT_CREATABLE

    def invoke(self, context, event):
        tname = context.scene.osm_inspector.type
        label = LIBRARY_CATEGORIES.get(S.TYPES[tname].category, (None, tname))[1]
        self.name = unique_name(label, ref_names(context.scene, S.TYPES[tname].category))
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        insp = context.scene.osm_inspector
        tname = insp.type
        if not self.name or get_item(context.scene, tname, self.name) is not None:
            return {"CANCELLED"}
        ensure_item(context.scene, tname, self.name)
        cat = S.TYPES[tname].category
        if cat in LIBRARY_CATEGORIES:  # keeps the per-object dropdowns (Properties panel) in sync
            library_add(context.scene, cat, self.name)
        insp.object = self.name
        return {"FINISHED"}


def _weather_item(scene):
    return ensure_item(scene, "WeatherFile", S.SINGLETONS["WeatherFile"])


class SCENE_OT_osm_weather_browse(bpy.types.Operator):
    bl_idname = "scene.osm_weather_browse"
    bl_label = "Choose Weather File"
    bl_description = "Pick an .epw file; its location data fills the fields below"

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.epw", options={"HIDDEN"})

    def invoke(self, context, event):
        current = bpy.path.abspath(_weather_item(context.scene).epwPath)
        if current:
            self.filepath = current
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        item = _weather_item(context.scene)
        item.epwPath = self.filepath  # the property's update callback reads the file
        if epw_data(item) is None:
            self.report({"WARNING"}, "Could not read a LOCATION line from that file")
        return {"FINISHED"}


class SCENE_OT_osm_weather_reload(bpy.types.Operator):
    bl_idname = "scene.osm_weather_reload"
    bl_label = "Reset to .epw Value"
    bl_description = "Read the value from the .epw file again (all fields when no field is given)"
    bl_options = {"REGISTER", "UNDO"}

    key: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        if not apply_epw(_weather_item(context.scene), self.key):
            self.report({"WARNING"}, "No readable .epw file")
            return {"CANCELLED"}
        return {"FINISHED"}


class SCENE_OT_osm_model_remove(bpy.types.Operator):
    bl_idname = "scene.osm_model_remove"
    bl_label = "Remove OpenStudio Object"
    bl_description = "Remove the selected Inspector object (a Space or Building only loses its Inspector values)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        insp = context.scene.osm_inspector
        t = S.TYPES.get(insp.type)
        return t is not None and t.backing == "store" and get_item(context.scene, insp.type, insp.object) is not None

    def execute(self, context):
        insp = context.scene.osm_inspector
        for i, item in enumerate(context.scene.osm_model):
            if item.type == insp.type and item.name == insp.object:
                context.scene.osm_model.remove(i)
                break
        if insp.type in S.NOT_CREATABLE:
            ensure_item(context.scene, insp.type, insp.object)  # back to defaults
        else:
            names = object_names(context.scene, insp.type)
            insp.object = names[0] if names else ""
        return {"FINISHED"}


# --- panel ----------------------------------------------------------------------------


def _draw_weather_fields(layout, item):
    """File picker (.epw filter) and location fields, each with a reset button once it differs from the file."""
    col = layout.column(align=True)
    row = col.row(align=True)
    row.prop(item, "epwPath", text="")
    row.operator("scene.osm_weather_browse", text="", **ik("weather_browse", "FILEBROWSER"))
    data = epw_data(item)
    if item.epwPath and data is None:
        col.label(text="Not a readable .epw file", icon="ERROR")
    changed = [k for k in epw.LOCATION_KEYS if data and _differs(item, k, data[k])]
    if data:
        row = col.row(align=True)
        row.enabled = bool(changed)
        row.operator(
            "scene.osm_weather_reload",
            text="Reset All to .epw" if changed else "Matches .epw",
            **ik("weather_reload", "LOOP_BACK"),
        ).key = ""
    col.separator()
    for fd in S.TYPES["WeatherFile"].fields:
        if fd.key == "epwPath":
            continue
        row = draw_field(col, item, fd.key)
        if fd.key in changed:
            row.operator("scene.osm_weather_reload", text="", **ik("weather_reload", "LOOP_BACK")).key = fd.key


def _draw_store_fields(layout, scene, tdef: S.TypeDef, item):
    if tdef.name == "WeatherFile":
        _draw_weather_fields(layout, item)
        return
    col = split_column(layout)
    for fd in tdef.fields:
        if fd.key == "northAxis":
            if "northAxis" in scene:
                draw_field(col, scene, '["northAxis"]', "North Axis (deg)")
            else:
                col.operator("scene.osm_init_props", text="+ North Axis")
        elif fd.kind == S.RULES:
            schedule_ui.draw_rules(layout, item)
        elif fd.kind == S.COLOR:
            row = field_row(col, fd.label)
            row.prop(item, f"{fd.key}_set", text="")
            sub = row.row(align=True)
            sub.enabled = getattr(item, f"{fd.key}_set")
            sub.prop(item, fd.key, text="")
        elif fd.kind == S.AUTOCALC:
            row = field_row(col, f"{fd.label} ({fd.unit})" if fd.unit else fd.label)
            row.prop(item, f"{fd.key}_hard", text="")
            sub = row.row(align=True)
            sub.enabled = getattr(item, f"{fd.key}_hard")
            sub.prop(item, fd.key, text="")
        else:
            draw_field(col, item, fd.key)


def _draw_object_fields(layout, context, tname: str, ob):
    col = split_column(layout)
    for key, _label in S.OBJECT_FIELDS[tname]:
        if (
            key == "outsideBoundaryConditionObjectName"
            and tname == "Surface"
            and ob.get("outsideBoundaryCondition") != "Surface"
        ):
            continue
        _field(col, ob, key)
    if tname == "Surface":
        col.separator()
        col.label(text="Unset values use OpenStudio's defaults", icon="INFO")


class VIEW3D_PT_osm_inspector(PanelIcon, bpy.types.Panel):
    osm_icon = "panel_inspector"
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"
    bl_label = "OpenStudio Inspector"
    bl_idname = "VIEW3D_PT_osm_inspector"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        sc, lay = context.scene, self.layout
        insp = sc.osm_inspector
        lay.use_property_split = False
        lay.use_property_decorate = False
        lay.row(align=True).prop(insp, "picker", expand=True)
        if insp.picker == "HIERARCHY":
            row = lay.row()
            row.template_list("OSM_UL_inspector_tree", "", insp, "tree", insp, "tree_index", rows=6)
            col = row.column(align=True)
            col.operator("scene.osm_tree_refresh", text="", icon="FILE_REFRESH")
            col.operator("scene.osm_migrate_spaces", text="", icon="IMPORT")
            return
        tdef = S.TYPES.get(insp.type)
        col = lay.column(align=True)
        col.prop(insp, "type", text="")
        row = col.row(align=True)
        row.prop(insp, "object", text="", icon="DOWNARROW_HLT")
        if tdef and tdef.backing == "store" and tdef.name not in S.NOT_CREATABLE:
            row.operator("scene.osm_model_add", text="", **ik("inspector_add", "ADD"))
            row.operator("scene.osm_model_remove", text="", **ik("inspector_remove", "REMOVE"))
        if tdef is None or not insp.object:
            has = tdef is not None and object_names(sc, tdef.name)
            lay.label(text="Pick an object above" if has else "No objects of this type")


class _InspectorSub:
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"
    bl_parent_id = "VIEW3D_PT_osm_inspector"


class VIEW3D_PT_osm_inspector_props(_InspectorSub, bpy.types.Panel):
    """Fields of the chosen object."""

    bl_label = "Properties"
    bl_idname = "VIEW3D_PT_osm_inspector_props"

    @classmethod
    def poll(cls, context):
        insp = context.scene.osm_inspector
        return S.TYPES.get(insp.type) is not None and bool(insp.object)

    def draw(self, context):
        sc, lay = context.scene, self.layout
        insp = sc.osm_inspector
        tdef = S.TYPES[insp.type]
        lay.use_property_decorate = False
        lay.label(text=f"OS:{tdef.label}")
        if tdef.name not in S.SINGLETONS:
            draw_field(split_column(lay), insp, "name")
        if tdef.backing == "object":
            ob = bpy.data.objects.get(insp.object)
            if ob is None:
                lay.label(text="Object not found", icon="ERROR")
            else:
                _draw_object_fields(lay, context, tdef.name, ob)
            return
        item = get_item(sc, tdef.name, insp.object)
        if item is None:
            lay.operator("scene.osm_model_add", text="Create", **ik("inspector_add", "ADD"))
            return
        _draw_store_fields(lay, sc, tdef, item)
        if tdef.name == "Space":
            lay.label(text="Inspector values override the surfaces' own zone / story / type", icon="INFO")


CLASSES = (
    *schedule_ui.CLASSES,
    OSMModelObject,
    OSMTreeRow,
    OSMInspectorSettings,
    OSM_UL_inspector_tree,
    SCENE_OT_osm_tree_refresh,
    SCENE_OT_osm_migrate_spaces,
    SCENE_OT_osm_model_add,
    SCENE_OT_osm_model_remove,
    SCENE_OT_osm_weather_browse,
    SCENE_OT_osm_weather_reload,
    VIEW3D_PT_osm_inspector,
    VIEW3D_PT_osm_inspector_props,
)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.osm_model = CollectionProperty(type=OSMModelObject)
    bpy.types.Scene.osm_inspector = PointerProperty(type=OSMInspectorSettings)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    del bpy.types.Scene.osm_inspector
    del bpy.types.Scene.osm_model
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
