"""Scripts panel: run the ported OpenStudio user scripts from a Blender sidebar panel (imports bpy).

Category dropdown -> script dropdown (only when the category has several) -> the script's variables -> Run.
The script runs on an OpenStudio model built from the scene (or loaded from an .osm file); the result either replaces
the OpenStudio objects in the scene or is saved as a new .osm. Logic lives in the bpy-free `scripts_core`.
"""

from __future__ import annotations

import json
import tempfile
import textwrap
from pathlib import Path
from types import SimpleNamespace

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..core import scripts_core as sc
from .icons import PanelIcon, ik
from .widgets import BUTTON_SCALE, field, field_row, split_column

_items_cache: dict = {}  # Blender keeps only a pointer to enum item lists: hold them here


# --- enum item callbacks ---------------------------------------------------------------------


def _category_items(self, context):
    items = [(c, c, "") for c in sc.categories()]
    _items_cache["cat"] = items
    return items


def _script_items(self, context):
    items = [(n, n, (sc.get(self.category, n) or sc.UserScript()).description) for n in sc.scripts(self.category)]
    _items_cache[f"script:{self.category}"] = items
    return items


def _choice_items(self, context):
    try:
        raw = json.loads(self.choices_json or "[]")
    except ValueError:
        raw = []
    items = [(i, label, "") for i, label in raw] or [(sc.NONE_ID, "<none>", "")]
    _items_cache[f"choice:{self.key}:{self.choices_json}"] = items
    return items


# --- property groups -------------------------------------------------------------------------


class OSMScriptArg(bpy.types.PropertyGroup):
    key: StringProperty()
    label: StringProperty()
    type: StringProperty()
    float_value: FloatProperty(precision=3)
    int_value: IntProperty()
    bool_value: BoolProperty()
    string_value: StringProperty()
    path_value: StringProperty(subtype="FILE_PATH")
    enum_value: EnumProperty(items=_choice_items)
    choices_json: StringProperty()
    ext: StringProperty()
    read: BoolProperty()

    def value(self):
        t = self.type
        if t == "float":
            return self.float_value
        if t == "int":
            return self.int_value
        if t == "bool":
            return self.bool_value
        if t == "string":
            return self.string_value
        if t == "path":
            return bpy.path.abspath(self.path_value) if self.path_value else ""
        return "" if self.enum_value == sc.NONE_ID else self.enum_value

    def set_value(self, v):
        t = self.type
        if t == "float":
            self.float_value = float(v)
        elif t == "int":
            self.int_value = int(v)
        elif t == "bool":
            self.bool_value = bool(v)
        elif t == "string":
            self.string_value = str(v)
        elif t == "path":
            v = str(v)
            if v.startswith("//") and not bpy.data.filepath:  # blend relative paths need a saved file
                v = v[2:]
            self.path_value = v
        else:
            ids = [i for i, _ in json.loads(self.choices_json or "[]")]
            v = sc.NONE_ID if v == "" else v
            if v in ids:
                self.enum_value = v


def current_script(s):
    return sc.get(s.category, s.script)


def rebuild_args(s, model=None, keep=False):
    """Re-create the variable list for the selected script (`keep`: carry over the values already entered)."""
    script = current_script(s)
    old = {a.key: a.value() for a in s.args} if keep else {}
    s.args.clear()
    if script is None:
        return
    for d in script.arguments(model):
        a = s.args.add()
        a.key, a.label, a.type, a.ext, a.read = d.key, d.label, d.type, d.ext, d.read
        a.name = d.key
        if d.type == "choice":
            a.choices_json = json.dumps([[i or sc.NONE_ID, label] for i, label in d.choices])
        a.set_value(old.get(d.key, d.default))


def _on_category(self, context):
    names = sc.scripts(self.category)
    if names and self.script not in names:
        self.script = names[0]
    rebuild_args(self)
    self.status = ""


def _on_script(self, context):
    rebuild_args(self)
    self.status = ""


_UPDATE_TIP = (
    "Replace the scene's OpenStudio objects with the changed model (custom property data that is not "
    "part of an OpenStudio model, e.g. Inspector entries, is not carried over)"
)


class OSMScriptsSettings(bpy.types.PropertyGroup):
    category: EnumProperty(name="Folder", description="Script folder", items=_category_items, update=_on_category)
    script: EnumProperty(name="Script", description="Script to run", items=_script_items, update=_on_script)
    args: CollectionProperty(type=OSMScriptArg)
    source: EnumProperty(
        name="Input",
        description="Model the script runs on",
        items=[
            ("SCENE", "Scene", "Build the model from the OpenStudio objects of this scene"),
            ("FILE", "OSM File", "Load an existing .osm file"),
        ],
        default="SCENE",
    )
    input_path: StringProperty(name="Input .osm", subtype="FILE_PATH")
    output: EnumProperty(
        name="Result",
        description="What happens with the changed model",
        items=[
            ("SCENE", "Update Scene", _UPDATE_TIP),
            ("FILE", "Save as .osm", "Write the changed model to a new .osm file and leave the scene untouched"),
        ],
        default="SCENE",
    )
    output_path: StringProperty(name="Output .osm", subtype="FILE_PATH", default="//script_result.osm")
    status: StringProperty()


# --- model plumbing --------------------------------------------------------------------------


class Source(SimpleNamespace):
    """The model a script runs on: `model`, `replace_objs` (scene objects it was built from), `names` / `spaces`
    (selection as model names) and `space_of` (scene object name -> space name)."""


def _scene_model(context):
    """`Source` for the OpenStudio objects of the scene (built directly from the meshes, no glTF)."""
    from ..core.osm_builder import build_model
    from .ui import build_options, default_space_overrides, load_openstudio, scene_faces

    load_openstudio()
    scene = context.scene
    overrides = default_space_overrides(list(scene.objects))
    if not overrides:
        raise RuntimeError("No mesh objects in the scene")
    selected = {o.name for o in context.selected_objects if o.name in overrides}
    from .surface_ops import _space_of

    space_of = {n: _space_of(bpy.data.objects[n]) for n in overrides}
    scale = scene.unit_settings.scale_length
    opts = build_options(context, overrides, scale=scale)
    model, _report = build_model(scene_faces(context, overrides, scale), opts)
    names = [s.nameString() for s in model.getPlanarSurfaces()]
    sel_names = sc.expand_selection(names, selected)
    sel_spaces = sc.fully_selected_spaces(space_of, selected)
    return Source(model=model, replace_objs=list(overrides), names=sel_names, spaces=sel_spaces, space_of=space_of)


def _load_model(path: str):
    import openstudio

    loaded = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(path))
    if not loaded.is_initialized():
        raise RuntimeError(f"could not load '{path}' as an OpenStudio model")
    return loaded.get()


def _input_model(context, script):
    s = context.scene.osm_scripts
    from .ui import load_openstudio

    load_openstudio()
    if s.source == "FILE":
        if script.viewport:
            raise RuntimeError("This script acts on the objects of the scene: set Input to Scene")
        path = bpy.path.abspath(s.input_path)
        if not path or not Path(path).is_file():
            raise RuntimeError("Choose an existing .osm file as input")
        return Source(model=_load_model(path), replace_objs=[], names=set(), spaces=set(), space_of={})
    if script.creates_geometry or not script.reads_scene:  # these add to the scene / do not need it
        import openstudio

        return Source(model=openstudio.model.Model(), replace_objs=[], names=set(), spaces=set(), space_of={})
    return _scene_model(context)


def _apply_visibility(context, ctx, space_of):
    """Show / hide the scene objects of spaces, and build the requested view layers."""
    scene, objs = context.scene, bpy.data.objects

    def visit(vl, state):
        for name, space in space_of.items():
            ob = objs.get(name)
            shown = state(space)
            if ob is not None and ob.name in vl.objects and shown is not None:
                ob.hide_set(not shown, view_layer=vl)

    visit(context.view_layer, lambda sp: True if sp in ctx.show_spaces else (False if sp in ctx.hide_spaces else None))
    for lname, visible in ctx.view_layers.items():
        vl = scene.view_layers.get(lname) or scene.view_layers.new(lname)
        visit(vl, lambda sp, visible=visible: sp in visible)


def _save_template(context, model):
    """The model's space types / construction sets / constructions become the scene template."""
    import openstudio

    base = Path(bpy.path.abspath("//")) if bpy.data.filepath else Path(tempfile.gettempdir())
    stem = Path(bpy.data.filepath).stem if bpy.data.filepath else "osm"
    path = base / f"{stem}_template.osm"
    if not model.save(openstudio.path(str(path)), True):
        raise RuntimeError(f"could not write '{path}'")
    context.scene.osm_template_path = str(path)
    return path


def _inspector_hand_offs(context, ctx):
    from ..core import inspector_schema as S
    from .inspector import _weather_item, ensure_item

    if ctx.weather_file:
        _weather_item(context.scene).epwPath = ctx.weather_file
    if ctx.building_defaults:
        item = ensure_item(context.scene, "Building", S.BUILDING_NAME)
        for key, value in ctx.building_defaults.items():
            setattr(item, key, value)


class SCENE_OT_osm_scripts_refresh(bpy.types.Operator):
    bl_idname = "scene.osm_scripts_refresh"
    bl_label = "Refresh Variables"
    bl_description = "Rebuild the variable list; model dependent choices are read from the current input model"

    def execute(self, context):
        s = context.scene.osm_scripts
        script = current_script(s)
        model = None
        if script is not None and script.model_dependent:
            try:
                model = _input_model(context, script).model
            except Exception as e:
                self.report({"WARNING"}, f"Could not read the model: {e}")
        rebuild_args(s, model, keep=True)
        return {"FINISHED"}


def _redraw_all(context):
    """Refresh the viewport and sidebar panels (Inspector, selection, results) after a script run."""
    try:
        context.view_layer.update()
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                area.tag_redraw()
    except Exception:
        pass


class SCENE_OT_osm_run_script(bpy.types.Operator):
    bl_idname = "scene.osm_run_script"
    bl_label = "Run Script"
    bl_description = "Run the selected OpenStudio script on the model"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        script = current_script(context.scene.osm_scripts)
        if script is not None and script.confirm_message:
            msg = script.confirm_message + " Continue?"
            try:
                return context.window_manager.invoke_confirm(self, event, title=script.name, message=msg)
            except TypeError:
                try:
                    return context.window_manager.invoke_confirm(self, event)
                except Exception:
                    return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def execute(self, context):
        from .ui import prefs

        p = prefs()
        if p is not None and p.disable_user_scripts:
            self.report({"ERROR"}, "OpenStudio scripts are disabled in the add-on preferences")
            return {"CANCELLED"}
        try:
            return self._run(context)
        finally:
            _redraw_all(context)

    def _run(self, context):
        from ..core.osm_reader import read_model
        from .ui import build_scene

        s = context.scene.osm_scripts
        script = current_script(s)
        if script is None:
            self.report({"ERROR"}, "No script selected")
            return {"CANCELLED"}
        wm = context.window_manager
        try:
            src = _input_model(context, script)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        model = src.model
        args = {a.key: a.value() for a in s.args}
        wm.progress_begin(0, 100)
        ctx = sc.ScriptContext(src.names, src.spaces, progress=lambda f, _t: wm.progress_update(int(f * 100)))
        try:
            ok = script.execute(model, args, ctx)
        finally:
            wm.progress_end()
        for m in ctx.messages[:15]:
            self.report({m.level}, m.text)
        if len(ctx.messages) > 15:
            self.report({"INFO"}, f"... {len(ctx.messages) - 15} more messages")
        if not ok:
            s.status = "Failed"
            return {"CANCELLED"}
        if script.viewport:
            _apply_visibility(context, ctx, src.space_of)
            s.status = f"{script.name}: done"
            return {"FINISHED"}
        if not script.modifies_model:
            s.status = f"{script.name}: done"
            return {"FINISHED"}
        if s.output == "FILE":
            import openstudio

            out = bpy.path.abspath(s.output_path)
            if not out or not model.save(openstudio.path(out), True):
                self.report({"ERROR"}, f"Could not write '{out}'")
                return {"CANCELLED"}
            s.status = f"{script.name}: wrote {Path(out).name}"
            self.report({"INFO"}, s.status)
            return {"FINISHED"}
        # update the scene
        notes = []
        try:
            if script.produces_template:
                notes.append(f"template {_save_template(context, model).name}")
            _inspector_hand_offs(context, ctx)
            if ctx.weather_file:
                notes.append("weather file")
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if not script.edits_geometry:
            s.status = f"{script.name}: " + (", ".join(notes) or "done")
            self.report({"INFO"}, s.status)
            return {"FINISHED"}
        # drop the objects the model was built from, then import the changed model
        data = read_model(model)
        scale = 1.0 / (context.scene.unit_settings.scale_length or 1.0)
        for w in data.get("warnings", [])[:5]:
            self.report({"WARNING"}, w)
        cols = {c for n in src.replace_objs if (ob := bpy.data.objects.get(n)) for c in ob.users_collection}
        for n in src.replace_objs:
            ob = bpy.data.objects.get(n)
            if ob is not None:
                bpy.data.objects.remove(ob, do_unlink=True)
        for c in cols:  # space collections emptied by the replacement
            if not c.objects and not c.children and c != context.scene.collection:
                bpy.data.collections.remove(c)
        counts = build_scene(context, data, scale, script.name)
        s.status = f"{script.name}: " + (", ".join(f"{n} {k}" for k, n in counts.items()) or "no geometry")
        self.report({"INFO"}, s.status)
        return {"FINISHED"}


# --- UI --------------------------------------------------------------------------------------


def _draw_arg(layout, a):
    if a.type == "bool":
        layout.prop(a, "bool_value", text=a.label)
        return
    prop = {"float": "float_value", "int": "int_value", "string": "string_value", "path": "path_value"}.get(
        a.type, "enum_value"
    )
    field(layout, a, prop, a.label)


class VIEW3D_PT_osm_scripts(PanelIcon, bpy.types.Panel):
    osm_icon = "panel_scripts"
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"
    bl_label = "Scripts"
    bl_idname = "VIEW3D_PT_osm_scripts"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(**ik("panel_scripts", "SCRIPT"))

    def draw(self, context):
        s, lay = context.scene.osm_scripts, self.layout
        lay.use_property_split = False
        lay.use_property_decorate = False
        script = current_script(s)
        from .ui import prefs

        if prefs() is not None and prefs().disable_user_scripts:
            lay.label(text="Scripts are disabled in the add-on preferences", icon="LOCKED")
            return

        col = split_column(lay)
        field(col, s, "category", "Category")
        if len(sc.scripts(s.category)) > 1:
            field(col, s, "script", "Script")
        if script is None:
            lay.label(text="No scripts available", icon="ERROR")


class _ScriptsSub:
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"
    bl_parent_id = "VIEW3D_PT_osm_scripts"

    @classmethod
    def _script(cls, context):
        from .ui import prefs

        if prefs() is not None and prefs().disable_user_scripts:
            return None
        return current_script(context.scene.osm_scripts)

    def _begin(self, context):
        self.layout.use_property_split = False
        self.layout.use_property_decorate = False
        return context.scene.osm_scripts, current_script(context.scene.osm_scripts)


class VIEW3D_PT_osm_scripts_variables(_ScriptsSub, bpy.types.Panel):
    bl_label = "Variables"
    bl_idname = "VIEW3D_PT_osm_scripts_variables"
    bl_options = {"HEADER_LAYOUT_EXPAND"}

    @classmethod
    def poll(cls, context):
        s = context.scene.osm_scripts
        script = cls._script(context)
        return script is not None and bool(s.args or script.arguments(None))

    def draw_header(self, context):
        s, script = self._begin(context)
        if script is not None and s.args and script.model_dependent:
            row = self.layout.row(align=True)
            row.alignment = "RIGHT"
            row.scale_x = 1.5
            row.operator("scene.osm_scripts_refresh", text="", icon="FILE_REFRESH")

    def draw(self, context):
        s, _script = self._begin(context)
        if not s.args:
            self.layout.operator("scene.osm_scripts_refresh", text="Load Variables", icon="FILE_REFRESH")
            return
        col = split_column(self.layout)
        for a in s.args:
            _draw_arg(col, a)


class VIEW3D_PT_osm_scripts_run(_ScriptsSub, bpy.types.Panel):
    """Headerless last section: status + Run."""

    bl_label = "Run"
    bl_idname = "VIEW3D_PT_osm_scripts_run"
    bl_options = {"HIDE_HEADER"}

    @classmethod
    def poll(cls, context):
        return cls._script(context) is not None

    def draw(self, context):
        s, _script = self._begin(context)
        if s.status:
            self.layout.label(text=s.status)
        row = self.layout.row()
        row.scale_y = BUTTON_SCALE
        row.operator("scene.osm_run_script", text="Run", **ik("script_run", "PLAY"))


class VIEW3D_PT_osm_scripts_io(_ScriptsSub, bpy.types.Panel):
    bl_label = "Input / Output"
    bl_idname = "VIEW3D_PT_osm_scripts_io"

    @classmethod
    def poll(cls, context):
        script = cls._script(context)
        return script is not None and not script.viewport

    def draw(self, context):
        s, script = self._begin(context)
        col = split_column(self.layout)
        field(col, s, "source", "Input")
        if s.source == "FILE":
            field_row(col, "").prop(s, "input_path", text="")
        if script.modifies_model:
            field(col, s, "output", "Output")
            if s.output == "FILE":
                field_row(col, "").prop(s, "output_path", text="")


class VIEW3D_PT_osm_scripts_help(_ScriptsSub, bpy.types.Panel):
    bl_label = "Help"
    bl_idname = "VIEW3D_PT_osm_scripts_help"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        script = cls._script(context)
        return script is not None and bool(script.description or script.uses_selection or script.viewport)

    def draw(self, context):
        _s, script = self._begin(context)
        lay = self.layout
        if script.description:
            col = lay.column(align=True)
            width = getattr(getattr(context, "region", None), "width", 0) or 320
            for line in textwrap.wrap(script.description, max(24, int(width / 7.5))):
                col.label(text=line)
        if script.uses_selection:
            lay.label(text="Acts on the selected surfaces / spaces", icon="RESTRICT_SELECT_OFF")
        if script.viewport:
            lay.label(text="Shows / hides the scene's objects", icon="HIDE_OFF")


# --- registration ----------------------------------------------------------------------------

CLASSES = (
    OSMScriptArg,
    OSMScriptsSettings,
    SCENE_OT_osm_scripts_refresh,
    SCENE_OT_osm_run_script,
    VIEW3D_PT_osm_scripts,
    VIEW3D_PT_osm_scripts_variables,
    VIEW3D_PT_osm_scripts_run,
    VIEW3D_PT_osm_scripts_io,
    VIEW3D_PT_osm_scripts_help,
)


def _init_scenes():
    for scene in bpy.data.scenes:
        s = scene.osm_scripts
        if s.script and not s.args:
            rebuild_args(s)


@persistent
def _load_post(_dummy=None):
    bpy.app.timers.register(_init_scenes, first_interval=0.1)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.osm_scripts = PointerProperty(type=OSMScriptsSettings)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)
    bpy.app.timers.register(_init_scenes, first_interval=0.5)


def unregister():
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if bpy.app.timers.is_registered(_init_scenes):
        bpy.app.timers.unregister(_init_scenes)
    del bpy.types.Scene.osm_scripts
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
