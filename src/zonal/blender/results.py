"""Results panel: read an EnergyPlus .sql, keyframe a variable onto surfaces / zones (imports bpy).

Per matched object the custom prop `osm_result` is keyframed (a shared heatmap material reads it through an Attribute
node); optional text labels are mesh objects with a Geometry Nodes group (Value to String -> String to Curves -> Fill)
whose Value input is keyframed, so they animate and render without any handler.
"""

from __future__ import annotations

import contextlib

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from mathutils import Quaternion, Vector

from ..core.results_core import ObjectInfo, SqlResults, frame_times, key_kind, key_label, match_objects, value_range
from .icons import PanelIcon, ik
from .surface_ops import is_loose

PROP = "osm_result"  # keyframed float custom prop on result objects
LABEL_PROP = "osm_result_label"  # marks label objects the add-on created
ORIG_MATS = "osm_result_orig_mats"
GROUP_NAME = "OSM_ValueLabel"
MOD_NAME = "OSM Value Label"
HEATMAP_MAT = "OSM Result Heatmap"
LABEL_MAT = "OSM Result Label"

_cache: dict = {"path": "", "sql": None}
_env_items: list = []
_freq_items: list = []


# --- SQL access ---------------------------------------------------------------------


def _sql(s) -> SqlResults:
    path = bpy.path.abspath(s.sql_path)
    if _cache["sql"] is None or _cache["path"] != path:
        from . import ui as blender_ui

        blender_ui.load_openstudio()
        _cache["sql"], _cache["path"] = SqlResults(path), path
    return _cache["sql"]


NONE = "NONE"  # placeholder item: an empty identifier would be a separator, not a selectable item


def _enum_items(store: list, values: list[str]):
    store[:] = [(v, v, "") for v in values] or [(NONE, "(none)", "")]
    return store


def _envs(self, context):
    sql = _cache["sql"]
    return _enum_items(_env_items, sql.env_periods() if sql else [])


def _freqs(self, context):
    sql = _cache["sql"]
    return _enum_items(_freq_items, sql.frequencies(self.env) if sql and self.env != NONE else [])


def _refill_variables(self, context=None):
    """Env / frequency changed: rebuild the variable list."""
    self.variables.clear()
    sql = _cache["sql"]
    if not sql or NONE in (self.env, self.freq):
        return
    for name in sql.variables(self.env, self.freq):
        self.variables.add().name = name
    self.active = min(self.active, max(len(self.variables) - 1, 0))


def _env_changed(self, context):
    _refill_variables(self, context)


class OSMResultVar(bpy.types.PropertyGroup):
    """`name` = EnergyPlus variable name."""


HEATMAP_PRESETS = {
    "BLUE_YELLOW_RED": ((0.0, (0.05, 0.2, 1.0)), (0.5, (1.0, 0.9, 0.2)), (1.0, (1.0, 0.1, 0.05))),
    "VIRIDIS": ((0.0, (0.267, 0.005, 0.329)), (0.5, (0.128, 0.567, 0.551)), (1.0, (0.993, 0.906, 0.144))),
    "TURBO": (
        (0.0, (0.19, 0.07, 0.23)),
        (0.25, (0.27, 0.64, 0.96)),
        (0.5, (0.64, 0.98, 0.26)),
        (0.75, (0.98, 0.58, 0.14)),
        (1.0, (0.48, 0.02, 0.01)),
    ),
    "GREYSCALE": ((0.0, (0.05, 0.05, 0.05)), (1.0, (0.95, 0.95, 0.95))),
}


def apply_heatmap_preset(ramp, key: str) -> None:
    """Rewrite the colour ramp's stops from a preset."""
    stops = HEATMAP_PRESETS[key]
    els = ramp.color_ramp.elements
    while len(els) > 1:
        els.remove(els[-1])
    els[0].position, els[0].color = stops[0][0], (*stops[0][1], 1.0)
    for pos, rgb in stops[1:]:
        els.new(pos).color = (*rgb, 1.0)


def _preset_changed(self, context):
    mat = bpy.data.materials.get(HEATMAP_MAT)
    node = mat.node_tree.nodes.get("Ramp") if mat and mat.node_tree else None
    if node is not None:
        apply_heatmap_preset(node, self.preset)


class OSMResultsSettings(bpy.types.PropertyGroup):
    preset: EnumProperty(
        name="Preset",
        items=[
            ("BLUE_YELLOW_RED", "Blue-Yellow-Red", "Cold to hot"),
            ("VIRIDIS", "Viridis", "Perceptually uniform"),
            ("TURBO", "Turbo", "Rainbow with better contrast"),
            ("GREYSCALE", "Greyscale", ""),
        ],
        update=_preset_changed,
        description="Replace the colour ramp stops (edit them below afterwards)",
    )
    sql_path: StringProperty(name="SQL File", description="EnergyPlus eplusout.sql")
    env: EnumProperty(name="Period", items=_envs, update=_env_changed, description="Environment / run period")
    freq: EnumProperty(name="Frequency", items=_freqs, update=_env_changed, description="Reporting frequency")
    variables: CollectionProperty(type=OSMResultVar)
    active: IntProperty(default=0)
    key_filter: StringProperty(name="Key Filter", description="Only keys containing this text (case-insensitive)")
    only_selected: BoolProperty(
        name="Selected Objects Only", description="Restrict surface / zone matching to the selection"
    )
    heatmap: BoolProperty(
        name="Heatmap Material",
        default=True,
        description="Assign a shared material that colours the objects by the keyframed value "
        "(original materials are restored by Clear)",
    )
    labels: BoolProperty(name="Value Labels", default=True, description="Add animated text labels")
    decimals: IntProperty(name="Decimals", default=1, min=0, max=6)
    text_size: FloatProperty(name="Text Size", default=0.5, min=0.01, subtype="DISTANCE")
    label_upright: BoolProperty(
        name="Upright Zone Labels",
        default=True,
        description="Zone / meter labels: stand the text up (readable from -Y) instead of flat. "
        "Surface labels always lie on their face",
    )
    label_offset: FloatProperty(
        name="Offset",
        default=0.02,
        min=0.0,
        subtype="DISTANCE",
        description="Distance of surface labels along the face normal (avoids z-fighting)",
    )
    manual_range: BoolProperty(
        name="Manual Range", description="Fix the colour range instead of using the data min / max"
    )
    range_min: FloatProperty(name="Min")
    range_max: FloatProperty(name="Max", default=100.0)
    start_frame: IntProperty(name="Start Frame", default=1)
    frames_per_step: FloatProperty(
        name="Frames per Step",
        default=1.0,
        min=0.01,
        description="Frames between two samples (1 = one hour per frame for hourly data)",
    )
    stride: IntProperty(
        name="Every Nth Sample",
        default=1,
        min=1,
        description="Keyframe every Nth sample (e.g. 24 for daily snapshots of hourly data)",
    )
    interp: EnumProperty(
        name="Interpolation", default="CONSTANT", items=[("CONSTANT", "Constant", ""), ("LINEAR", "Linear", "")]
    )
    set_range: BoolProperty(
        name="Set Frame Range", default=True, description="Set the scene frame range to the animation"
    )
    status: StringProperty(default="", options={"HIDDEN"})


# --- animation helpers ------------------------------------------------------------------


def _fcurves(idb):
    """All fcurves of `idb`'s action (slotted / layered action API)."""
    ad = idb.animation_data
    act = ad.action if ad else None
    if act is None:
        return
    slot = ad.action_slot
    for layer in act.layers:
        for strip in layer.strips:
            cb = strip.channelbag(slot)
            if cb:
                yield from cb.fcurves


def _find_fcurve(idb, path: str):
    return next((fc for fc in _fcurves(idb) if fc.data_path == path), None)


def _remove_fcurve(idb, path: str):
    fc = _find_fcurve(idb, path)
    if fc is None:
        return
    ad, act = idb.animation_data, idb.animation_data.action
    coll = next(
        cb.fcurves for layer in act.layers for strip in layer.strips if (cb := strip.channelbag(ad.action_slot))
    )
    coll.remove(fc)


def write_keys(idb, path: str, frames: list[float], values: list[float], interp: str) -> None:
    """Replace the animation of `path` by one keyframe per sample (fast bulk write)."""
    _remove_fcurve(idb, path)
    idb.keyframe_insert(path, frame=frames[0])  # creates action / slot / fcurve in whatever API this Blender has
    fc = _find_fcurve(idb, path)
    kps = fc.keyframe_points
    kps.clear()
    kps.add(len(frames))
    flat = [0.0] * (2 * len(frames))
    flat[0::2], flat[1::2] = frames, values
    kps.foreach_set("co", flat)
    for kp in kps:
        kp.interpolation = interp
    fc.update()


# --- materials / node groups --------------------------------------------------------------


def heatmap_material(lo: float, hi: float):
    mat = bpy.data.materials.get(HEATMAP_MAT)
    if mat is None:
        mat = bpy.data.materials.new(HEATMAP_MAT)
        with contextlib.suppress(Exception):  # always on in newer Blender
            mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        attr = nt.nodes.new("ShaderNodeAttribute")
        attr.name = "Value"
        attr.attribute_type = "OBJECT"
        attr.attribute_name = PROP
        rng = nt.nodes.new("ShaderNodeMapRange")
        rng.name = "Range"
        rng.clamp = True
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.name = "Ramp"
        els = ramp.color_ramp.elements
        els[0].position, els[0].color = 0.0, (0.05, 0.2, 1.0, 1.0)
        els[1].position, els[1].color = 1.0, (1.0, 0.1, 0.05, 1.0)
        mid = els.new(0.5)
        mid.color = (1.0, 0.9, 0.2, 1.0)
        emi = nt.nodes.new("ShaderNodeEmission")
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(attr.outputs["Fac"], rng.inputs["Value"])
        nt.links.new(rng.outputs["Result"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], emi.inputs["Color"])
        nt.links.new(emi.outputs[0], out.inputs["Surface"])
        for i, n in enumerate((attr, rng, ramp, emi, out)):
            n.location = (i * 240, 0)
        mat.diffuse_color = (0.8, 0.3, 0.2, 1.0)
    rng = mat.node_tree.nodes["Range"]
    rng.inputs["From Min"].default_value, rng.inputs["From Max"].default_value = lo, hi
    return mat


def label_material():
    mat = bpy.data.materials.get(LABEL_MAT)
    if mat is None:
        mat = bpy.data.materials.new(LABEL_MAT)
        with contextlib.suppress(Exception):
            mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        emi = nt.nodes.new("ShaderNodeEmission")
        emi.inputs["Color"].default_value = (1, 1, 1, 1)
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(emi.outputs[0], out.inputs["Surface"])
        mat.diffuse_color = (1, 1, 1, 1)
    return mat


def label_group():
    """Geometry Nodes group: Value (+ Suffix) -> text outline -> filled mesh."""
    ng = bpy.data.node_groups.get(GROUP_NAME)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    itf = ng.interface
    itf.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    itf.new_socket("Value", in_out="INPUT", socket_type="NodeSocketFloat")
    dec = itf.new_socket("Decimals", in_out="INPUT", socket_type="NodeSocketInt")
    dec.default_value = 1
    size = itf.new_socket("Size", in_out="INPUT", socket_type="NodeSocketFloat")
    size.default_value = 0.5
    itf.new_socket("Suffix", in_out="INPUT", socket_type="NodeSocketString")
    n = ng.nodes
    gin, gout = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    v2s = n.new("FunctionNodeValueToString")
    join = n.new("GeometryNodeStringJoin")
    s2c = n.new("GeometryNodeStringToCurves")
    for sock, wanted in (("Align X", "Center"), ("Align Y", "Middle")):  # menu sockets (identifier case varies)
        for cand in (wanted, wanted.upper()):
            try:
                s2c.inputs[sock].default_value = cand
                break
            except (TypeError, ValueError):
                continue
    real = n.new("GeometryNodeRealizeInstances")
    fill = n.new("GeometryNodeFillCurve")
    setmat = n.new("GeometryNodeSetMaterial")  # GN output drops the object's slots, so the group assigns the material
    setmat.inputs["Material"].default_value = label_material()
    lk = ng.links.new
    lk(gin.outputs["Value"], v2s.inputs["Value"])
    lk(gin.outputs["Decimals"], v2s.inputs["Decimals"])
    lk(gin.outputs["Suffix"], join.inputs[1])  # multi-input: the first link is joined LAST, so link the suffix first
    lk(v2s.outputs[0], join.inputs[1])
    lk(join.outputs[0], s2c.inputs["String"])
    lk(gin.outputs["Size"], s2c.inputs["Size"])
    lk(s2c.outputs[0], real.inputs[0])
    lk(real.outputs[0], fill.inputs[0])
    lk(fill.outputs[0], setmat.inputs[0])
    lk(setmat.outputs[0], gout.inputs[0])
    for i, node in enumerate((gin, v2s, join, s2c, real, fill, setmat, gout)):
        node.location = (i * 220, 0)
    return ng


def _set_input(mod, ident: str, value) -> None:
    getattr(mod.properties.inputs, ident).value = value


def _input_path(mod, ident: str) -> str:
    return f'modifiers["{mod.name}"].properties.inputs.{ident}.value'


def _socket_ids(ng) -> dict[str, str]:
    return {it.name: it.identifier for it in ng.interface.items_tree if it.item_type == "SOCKET"}


# --- scene queries -------------------------------------------------------------------------


def _space_of(ob) -> str:
    from .surface_ops import _space_of as space_of

    return space_of(ob)


def _candidates(context, only_selected: bool):
    src = context.selected_objects if only_selected else context.scene.objects
    return [o for o in src if o.type == "MESH" and not is_loose(o) and LABEL_PROP not in o]


def _center(obs) -> Vector:
    pts = [o.matrix_world @ Vector(c) for o in obs for c in o.bound_box]
    return sum(pts, Vector()) / len(pts) if pts else Vector()


SUB_TYPES = {
    "FixedWindow",
    "OperableWindow",
    "Door",
    "GlassDoor",
    "OverheadDoor",
    "Skylight",
    "TubularDaylightDome",
    "TubularDaylightDiffuser",
}


def _sub_points(cands):
    """[(object, world bound-box corners)] for the sub-surface objects (computed once per apply)."""
    return [
        (o, [o.matrix_world @ Vector(c) for c in o.bound_box])
        for o in cands
        if str(o.get("surfaceType", "")) in SUB_TYPES
    ]


def _free_spot(ob, poly, n: Vector, start: Vector, subs, margin: float = 0.0) -> Vector:
    """Label point on the largest free strip of `poly` beside the sub-surfaces lying on it, kept `margin` away; `start` if none overlap."""
    mw, me = ob.matrix_world, ob.data
    verts = [mw @ me.vertices[v].co for v in poly.vertices]
    if len(verts) > 4:  # strip centre may fall outside a non-rectangular face
        return start
    up = Vector((0.0, 0.0, 1.0)) if abs(n.z) < 0.999 else Vector((1.0, 0.0, 0.0))
    u = (up - n * up.dot(n)).normalized()
    w = n.cross(u)
    rect = [
        (min(p.dot(u) for p in pts), max(p.dot(u) for p in pts), min(p.dot(w) for p in pts), max(p.dot(w) for p in pts))
        for pts in (
            verts,
            *(
                c
                for o, c in subs
                if o is not ob
                and o.get("surfaceName", ob.name) == ob.name
                and all(abs(n.dot(p - start)) < 0.1 for p in c)
            ),
        )
    ]
    if len(rect) < 2:
        return start
    fx0, fx1, fy0, fy1 = rect[0]
    bx0, bx1 = min(r[0] for r in rect[1:]), max(r[1] for r in rect[1:])
    by0, by1 = min(r[2] for r in rect[1:]), max(r[3] for r in rect[1:])
    # u = "up" along the face (world Z on walls), w = sideways. Prefer straight above / below the block, then beside it
    uc, wc = (fx0 + fx1) / 2, (fy0 + fy1) / 2
    plane = n * n.dot(start)
    for pad in (margin, 0.0):  # margin first, then touching
        top, bottom = fx1 - (bx1 + pad), (bx0 - pad) - fx0
        if max(top, bottom) > 0:
            # text centre `margin` beyond the block edge, but no further than the middle of the free strip
            ux = min(bx1 + margin, (bx1 + pad + fx1) / 2) if top >= bottom else max(bx0 - margin, (fx0 + bx0 - pad) / 2)
            return u * ux + w * wc + plane
        left, right = (by0 - pad) - fy0, fy1 - (by1 + pad)
        if max(left, right) > 0:
            wx = (by1 + pad + fy1) / 2 if right >= left else (fy0 + by0 - pad) / 2
            return u * uc + w * wx + plane
    return start


def _face_pose(ob, offset: float, subs=(), margin: float = 0.0):
    """(location, rotation quaternion) of a label lying on `ob`'s largest face, lifted along its normal.

    The label moves off the sub-surfaces in `subs` (see `_sub_points`) that share the parent's centre."""
    me, mw = ob.data, ob.matrix_world
    poly = max(me.polygons, key=lambda p: p.area, default=None)
    if poly is None:
        return _center([ob]), Quaternion()
    n = (mw.to_3x3() @ poly.normal).normalized()
    center = mw @ poly.center
    if subs and str(ob.get("surfaceType", "")) not in SUB_TYPES:
        center = _free_spot(ob, poly, n, center, subs, margin)
    if abs(n.z) > 0.999:  # floor / roof: text reads along +X, from above (ceiling-side faces flipped)
        rot = Quaternion() if n.z > 0 else Quaternion((1.0, 0.0, 0.0), 3.14159265)
    else:  # wall: text upright (local Y toward world Z)
        rot = n.to_track_quat("Z", "Y")
    return center + n * offset, rot


def _add_label(context, s, name: str, where: Vector, frames, values, units: str, rotation=None):
    ng = label_group()
    ob = bpy.data.objects.new(f"Result {name}", bpy.data.meshes.new(f"Result {name}"))
    ob.data.vertices.add(1)  # one vertex so the modifier has geometry to replace
    context.scene.collection.objects.link(ob)
    ob[LABEL_PROP] = True
    ob.location = where
    if rotation is not None:
        ob.rotation_mode = "QUATERNION"
        ob.rotation_quaternion = rotation
    else:
        ob.rotation_euler = (1.5707963, 0.0, 0.0) if s.label_upright else (0.0, 0.0, 0.0)
    mod = ob.modifiers.new(MOD_NAME, "NODES")
    mod.node_group = ng
    ids = _socket_ids(ng)
    _set_input(mod, ids["Decimals"], s.decimals)
    _set_input(mod, ids["Size"], s.text_size)
    _set_input(mod, ids["Suffix"], f" {units}" if units else "")
    _set_input(mod, ids["Value"], float(values[0]))
    write_keys(ob, _input_path(mod, ids["Value"]), frames, values, s.interp)
    return ob


# --- operators -------------------------------------------------------------------------------


class SCENE_OT_osm_results_browse(bpy.types.Operator):
    bl_idname = "scene.osm_results_browse"
    bl_label = "Choose SQL File"
    bl_description = "Pick an EnergyPlus eplusout.sql and load it"

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.sql", options={"HIDDEN"})

    def invoke(self, context, event):
        current = bpy.path.abspath(context.scene.osm_results.sql_path)
        if current:
            self.filepath = current
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        context.scene.osm_results.sql_path = self.filepath
        return bpy.ops.scene.osm_results_load()


class SCENE_OT_osm_results_load(bpy.types.Operator):
    bl_idname = "scene.osm_results_load"
    bl_label = "Load Results"
    bl_description = "Open the SQL file and list its periods, frequencies and variables"

    def execute(self, context):
        s = context.scene.osm_results
        _cache["sql"] = None
        try:
            sql = _sql(s)
            envs = sql.env_periods()
        except Exception as e:
            _cache["sql"] = None
            s.status = str(e)
            self.report({"ERROR"}, s.status)
            return {"CANCELLED"}
        s.env = envs[0] if envs else NONE
        freqs = sql.frequencies(s.env) if envs else []
        s.freq = "Hourly" if "Hourly" in freqs else (freqs[0] if freqs else NONE)
        _refill_variables(s)
        s.status = f"{len(s.variables)} variable(s)"
        return {"FINISHED"}


def _clear(context):
    n = 0
    for ob in list(bpy.data.objects):
        if LABEL_PROP in ob:
            data = ob.data
            bpy.data.objects.remove(ob)
            if data and data.users == 0:
                bpy.data.meshes.remove(data)
            n += 1
        elif PROP in ob:
            _remove_fcurve(ob, f'["{PROP}"]')
            names = ob.get(ORIG_MATS)
            if names is not None and ob.type == "MESH":
                ob.data.materials.clear()
                for m in names:
                    if m in bpy.data.materials:
                        ob.data.materials.append(bpy.data.materials[m])
            for k in (PROP, ORIG_MATS, "osm_result_var", "osm_result_units"):
                if k in ob:
                    del ob[k]
            n += 1
    return n


class SCENE_OT_osm_results_clear(bpy.types.Operator):
    bl_idname = "scene.osm_results_clear"
    bl_label = "Clear"
    bl_description = "Remove result keyframes and labels, restore the original materials"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        self.report({"INFO"}, f"Cleared {_clear(context)} object(s)")
        return {"FINISHED"}


class SCENE_OT_osm_results_apply(bpy.types.Operator):
    bl_idname = "scene.osm_results_apply"
    bl_label = "Apply Variable"
    bl_description = "Keyframe the selected variable onto the matching surfaces / zones"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.osm_results
        if not s.variables:
            self.report({"WARNING"}, "Load an SQL file first")
            return {"CANCELLED"}
        var = s.variables[s.active].name
        kind = key_kind(var)
        try:
            sql = _sql(s)
            keys = [k for k in sql.keys(s.env, s.freq, var) if s.key_filter.strip().casefold() in k.casefold()]
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _clear(context)
        cands = _candidates(context, s.only_selected)
        subs_all = _sub_points(cands) if s.labels else []
        from .inspector import get_space_attr

        infos = [ObjectInfo(o.name, _space_of(o), get_space_attr(context.scene, o, "thermalZoneName")) for o in cands]
        by_name = {o.name: o for o in cands}

        data = []  # (key, series, [objects])
        for key in keys:
            ser = sql.series(s.env, s.freq, var, key)
            if ser is None or not ser.values:
                continue
            objs = [by_name[n] for n in match_objects(key, kind, infos)]
            if objs or kind == "other":
                data.append((key, ser, objs))
        if not data:
            self.report({"WARNING"}, f"No {kind} objects match the keys of '{var}'")
            return {"CANCELLED"}

        stride = s.stride

        def sample(vals):
            return vals[::stride]

        lo, hi = value_range(
            [sample(d[1].values) for d in data],
            s.range_min if s.manual_range else None,
            s.range_max if s.manual_range else None,
        )
        units = data[0][1].units
        mat = heatmap_material(lo, hi) if s.heatmap else None
        n_obj = n_lab = 0
        for key, ser, objs in data:
            vals = sample(ser.values)
            frames = frame_times(len(vals), s.start_frame, s.frames_per_step)
            for ob in objs:
                ob[PROP] = float(vals[0])
                ob["osm_result_var"], ob["osm_result_units"] = var, units
                write_keys(ob, f'["{PROP}"]', frames, vals, s.interp)
                if mat is not None and ob.type == "MESH":
                    ob[ORIG_MATS] = [m.name for m in ob.data.materials if m]
                    ob.data.materials.clear()
                    ob.data.materials.append(mat)
                n_obj += 1
            if s.labels:
                if kind == "surface":
                    subs = subs_all
                    for ob in objs:
                        where, rot = _face_pose(ob, s.label_offset, subs, s.text_size)
                        _add_label(context, s, f"{ob.name} {var}", where, frames, vals, units, rot)
                        n_lab += 1
                else:  # one label per zone / meter
                    where = _center(objs or cands)
                    _add_label(context, s, f"{key_label(key)} {var}", where, frames, vals, units)
                    n_lab += 1
        if s.set_range:
            last = s.start_frame + (len(sample(data[0][1].values)) - 1) * s.frames_per_step
            context.scene.frame_start, context.scene.frame_end = s.start_frame, max(round(last), s.start_frame + 1)
            context.scene.frame_set(s.start_frame)
        s.status = f"{var}: {n_obj} object(s), {n_lab} label(s), range {lo:.1f}..{hi:.1f} {units}"
        self.report({"INFO"}, s.status)
        return {"FINISHED"}


# --- UI --------------------------------------------------------------------------------------


class OSM_UL_results_vars(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.label(text=item.name)
        kind = key_kind(item.name)
        row.label(text="" if kind == "other" else kind.capitalize())


class _ResultsSub:
    """Sub-panel of Results: only shown once a result file is loaded."""

    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"
    bl_parent_id = "VIEW3D_PT_osm_results"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.osm_results.variables)

    def _begin(self, context):
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False
        return context.scene.osm_results


class VIEW3D_PT_osm_results(PanelIcon, bpy.types.Panel):
    osm_icon = "panel_results"
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "OpenStudio"
    bl_label = "Results"
    bl_idname = "VIEW3D_PT_osm_results"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s, lay = context.scene.osm_results, self.layout
        lay.use_property_split = True
        lay.use_property_decorate = False
        row = lay.row(align=True)
        row.prop(s, "sql_path", text="")
        row.operator("scene.osm_results_browse", text="", icon="FILEBROWSER")
        row.operator("scene.osm_results_load", text="", icon="FILE_REFRESH")
        if s.status:
            lay.label(text=s.status)
        if not s.variables:
            return
        col = lay.column(align=True)
        col.prop(s, "env")
        col.prop(s, "freq")
        lay.template_list("OSM_UL_results_vars", "", s, "variables", s, "active", rows=6)
        col = lay.column(align=True)
        col.prop(s, "key_filter")
        col.prop(s, "only_selected")
        row = lay.row(align=True)
        row.scale_y = 1.3
        row.operator("scene.osm_results_apply", **ik("results_apply", "PLAY"))
        row.operator("scene.osm_results_clear", **ik("results_clear", "X"))


class VIEW3D_PT_osm_results_heatmap(_ResultsSub, bpy.types.Panel):
    bl_label = "Heatmap"
    bl_idname = "VIEW3D_PT_osm_results_heatmap"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.prop(context.scene.osm_results, "heatmap", text="")

    def draw(self, context):
        s = self._begin(context)
        col = self.layout.column(align=True)
        col.active = s.heatmap
        col.prop(s, "manual_range")
        if s.manual_range:
            col.prop(s, "range_min")
            col.prop(s, "range_max")
        mat = bpy.data.materials.get(HEATMAP_MAT)
        node = mat.node_tree.nodes.get("Ramp") if mat and mat.node_tree else None
        box = self.layout.column()
        box.active = s.heatmap
        if node is not None:
            box.prop(s, "preset")
            box.template_color_ramp(node, "color_ramp", expand=False)
        else:
            box.label(text="Apply results to edit the color ramp", icon="INFO")


class VIEW3D_PT_osm_results_labels(_ResultsSub, bpy.types.Panel):
    bl_label = "Labels"
    bl_idname = "VIEW3D_PT_osm_results_labels"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.prop(context.scene.osm_results, "labels", text="")

    def draw(self, context):
        s = self._begin(context)
        col = self.layout.column(align=True)
        col.active = s.labels
        col.prop(s, "decimals")
        col.prop(s, "text_size")
        col.prop(s, "label_upright")
        col.prop(s, "label_offset")


class VIEW3D_PT_osm_results_animation(_ResultsSub, bpy.types.Panel):
    bl_label = "Animation"
    bl_idname = "VIEW3D_PT_osm_results_animation"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = self._begin(context)
        col = self.layout.column(align=True)
        col.prop(s, "start_frame")
        col.prop(s, "frames_per_step")
        col.prop(s, "stride")
        col.prop(s, "interp")
        col.prop(s, "set_range")


CLASSES_TO_REGISTER = (
    OSMResultVar,
    OSMResultsSettings,
    SCENE_OT_osm_results_browse,
    SCENE_OT_osm_results_load,
    SCENE_OT_osm_results_apply,
    SCENE_OT_osm_results_clear,
    OSM_UL_results_vars,
    VIEW3D_PT_osm_results,
    VIEW3D_PT_osm_results_heatmap,
    VIEW3D_PT_osm_results_labels,
    VIEW3D_PT_osm_results_animation,
)


def register():
    for c in CLASSES_TO_REGISTER:
        bpy.utils.register_class(c)
    bpy.types.Scene.osm_results = PointerProperty(type=OSMResultsSettings)


def unregister():
    del bpy.types.Scene.osm_results
    for c in reversed(CLASSES_TO_REGISTER):
        bpy.utils.unregister_class(c)
    _cache.update(path="", sql=None)
