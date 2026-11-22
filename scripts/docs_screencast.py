"""Record the "Get started" walkthrough from a *running* Blender (driven over the Blender MCP / Python console).

    exec(open(r"C:\\Users\\marco\\source\\zonal\\scripts\\docs_screencast.py").read())

It runs as a timer (the Blender UI stays alive), saves one full-window PNG per video frame to
<repo>/.cache/walkthrough/frames, one still per step to .../stills and writes manifest.json (frame, caption).
scripts/encode_screencast.py (ffmpeg) turns that into the MP4 / WebP. Poll .cache/walkthrough/status.txt.
"""

import json
import math
import traceback
from pathlib import Path

import bpy
from mathutils import Euler, Vector

NO_RUN = bool(globals().get("ZONAL_NO_RUN"))  # True: only define the helpers (used by docs_start_shots.py)
ROOT = Path(r"C:\Users\marco\source\zonal")
OUT = ROOT / ".cache" / "walkthrough"
FRAMES, STILLS = OUT / "frames", OUT / "stills"
for d in (FRAMES, STILLS):
    d.mkdir(parents=True, exist_ok=True)
    if not NO_RUN:
        for f in d.glob("*.png"):
            f.unlink()
FPS = 10

win = bpy.context.window_manager.windows[0]
scene = bpy.context.scene
screen = win.screen
area3d = next(a for a in screen.areas if a.type == "VIEW_3D")
props_area = next(a for a in screen.areas if a.type == "PROPERTIES")
space = area3d.spaces.active
r3d = space.region_3d

from bl_ext.user_default.zonal.blender import modeling as m_modeling
from bl_ext.user_default.zonal.blender import props as m_props
from bl_ext.user_default.zonal.blender import tools as m_tools
from bl_ext.user_default.zonal.blender import ui as m_ui

P = {
    "library": m_props.VIEW3D_PT_osm_library,
    "library_list": m_props.VIEW3D_PT_osm_library_list,
    "modeling": m_tools.VIEW3D_PT_osm_modeling,
    "matching": m_tools.VIEW3D_PT_osm_modeling_matching,
    "loose": m_tools.VIEW3D_PT_osm_modeling_loose,
    "spaces": m_modeling.VIEW3D_PT_osm_modeling_spaces,
    "shading": m_modeling.VIEW3D_PT_osm_modeling_shading,
    "render": m_tools.VIEW3D_PT_osm_render,
    "render_colors": m_tools.VIEW3D_PT_osm_render_colors,
    "properties": m_props.VIEW3D_PT_osm_properties,
    "p_space": m_props.VIEW3D_PT_osm_properties_space,
    "p_zone": m_props.VIEW3D_PT_osm_properties_zone,
    "p_boundary": m_props.VIEW3D_PT_osm_properties_boundary,
}
# every Zonal sidebar panel that is registered now (so we can hide the rest and restore at the end)
from bl_ext.user_default.zonal.blender import inspector as m_insp
from bl_ext.user_default.zonal.blender import results as m_res
from bl_ext.user_default.zonal.blender import scripts as m_scr
from bl_ext.user_default.zonal.blender import search as m_search

OTHER = [
    m_search.VIEW3D_PT_osm_search,
    m_search.VIEW3D_PT_osm_search_boundary,
    m_search.VIEW3D_PT_osm_search_orientation,
    m_search.VIEW3D_PT_osm_search_geometry,
    m_search.VIEW3D_PT_osm_search_options,
    m_search.VIEW3D_PT_osm_search_run,
    m_insp.VIEW3D_PT_osm_inspector,
    m_insp.VIEW3D_PT_osm_inspector_props,
    m_scr.VIEW3D_PT_osm_scripts,
    m_scr.VIEW3D_PT_osm_scripts_variables,
    m_scr.VIEW3D_PT_osm_scripts_run,
    m_scr.VIEW3D_PT_osm_scripts_io,
    m_scr.VIEW3D_PT_osm_scripts_help,
    m_res.VIEW3D_PT_osm_results,
    m_res.VIEW3D_PT_osm_results_heatmap,
    m_res.VIEW3D_PT_osm_results_labels,
    m_res.VIEW3D_PT_osm_results_animation,
]
# original sidebar order (parents before their children)
ORIGINAL = [
    P["library"], P["library_list"], P["modeling"], P["matching"], P["loose"], P["spaces"], P["shading"],
    P["render"], P["render_colors"], m_search.VIEW3D_PT_osm_search, m_search.VIEW3D_PT_osm_search_boundary,
    m_search.VIEW3D_PT_osm_search_orientation, m_search.VIEW3D_PT_osm_search_geometry,
    m_search.VIEW3D_PT_osm_search_options, m_search.VIEW3D_PT_osm_search_run, m_insp.VIEW3D_PT_osm_inspector,
    m_insp.VIEW3D_PT_osm_inspector_props, m_scr.VIEW3D_PT_osm_scripts, m_scr.VIEW3D_PT_osm_scripts_variables,
    m_scr.VIEW3D_PT_osm_scripts_run, m_scr.VIEW3D_PT_osm_scripts_io, m_scr.VIEW3D_PT_osm_scripts_help,
    m_res.VIEW3D_PT_osm_results, m_res.VIEW3D_PT_osm_results_heatmap, m_res.VIEW3D_PT_osm_results_labels,
    m_res.VIEW3D_PT_osm_results_animation, P["properties"], P["p_space"], P["p_zone"], P["p_boundary"],
]
ORIG_OPTS = {c: set(getattr(c, "bl_options", set())) for c in ORIGINAL + [m_ui.SCENE_PT_openstudio]}
orig_ui_scale = bpy.context.preferences.view.ui_scale

S = {"T": 0.0, "i": 0, "az": -40.0, "manifest": [], "target": (Vector((0, 0, 0)), 14.0, 62.0), "orbit": 3.0}


def log(msg):
    (OUT / "status.txt").write_text(msg)


def redraw():
    for a in screen.areas:
        a.tag_redraw()


def ov(area=None, region_type="WINDOW"):
    area = area or area3d
    region = next(r for r in area.regions if r.type == region_type)
    return bpy.context.temp_override(window=win, screen=screen, area=area, region=region)


DEMO = []  # registered clones (own bl_idname => fresh, expanded panel state)


def demo_clone(cls):
    attrs = {"bl_idname": getattr(cls, "bl_idname", cls.__name__) + "_demo", "bl_options": set()}
    if getattr(cls, "bl_parent_id", ""):
        attrs["bl_parent_id"] = cls.bl_parent_id + "_demo"
    return type(cls.__name__ + "_demo", (cls,), attrs)


def _unreg_demo():
    while DEMO:
        try:
            bpy.utils.unregister_class(DEMO.pop())
        except RuntimeError:
            pass


def set_panels(keys, extra=()):
    """Show only these Zonal sidebar panels, expanded. Blender remembers the collapsed state per panel id,
    so each panel is shown through a clone with a new id. Use `yield from set_panels(...)`."""
    _unreg_demo()
    for c in reversed(ORIGINAL):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
    for c in [P[k] for k in keys] + list(extra):
        d = demo_clone(c)
        bpy.utils.register_class(d)
        DEMO.append(d)
    redraw()
    yield 0.4


def restore_panels():
    _unreg_demo()
    for c in reversed(ORIGINAL):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
    for c in ORIGINAL:
        try:
            bpy.utils.register_class(c)
        except ValueError:
            pass
    try:
        bpy.utils.register_class(m_ui.SCENE_PT_openstudio)
    except ValueError:
        pass


def shot(path):
    with bpy.context.temp_override(window=win, screen=screen):
        bpy.ops.screen.screenshot(filepath=str(path))


def orbit():
    loc, dist, elev = S["target"]
    S["az"] += S["orbit"] / FPS
    r3d.view_location = r3d.view_location.lerp(loc, 0.12)
    r3d.view_distance += (dist - r3d.view_distance) * 0.12
    r3d.view_rotation = Euler((math.radians(elev), 0, math.radians(S["az"])), "XYZ").to_quaternion()


def frame(caption):
    orbit()
    redraw()
    yield 0.14
    path = FRAMES / f"f{S['i']:04d}.png"
    shot(path)
    S["manifest"].append({"f": path.name, "t": round(S["T"], 3), "cap": caption})
    S["i"] += 1
    S["T"] += 1.0 / FPS


def run(seconds, caption, fn=None):
    n = max(2, int(seconds * FPS))
    for i in range(n):
        if fn:
            fn(i / (n - 1))
        yield from frame(caption)


def still(name):
    yield 0.2
    shot(STILLS / f"{name}.png")


def ease(t):
    return t * t * (3 - 2 * t)


def select_objs(objs, active=None):
    for o in scene.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = active or objs[0]


def make_collection(name, parent=None):
    col = bpy.data.collections.new(name)
    (parent or scene.collection).children.link(col)
    lc = next(lc for lc in bpy.context.view_layer.layer_collection.children if lc.collection is col)
    bpy.context.view_layer.active_layer_collection = lc
    return col


def add_box(name, dims, base_xy):
    """Box whose bottom face is at Z=0 (mesh offset, object origin at world zero)."""
    from mathutils import Matrix

    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
    ob = bpy.context.object
    ob.name = name
    ob.data.transform(Matrix.Diagonal((*dims, 1.0)))
    ob.data.transform(Matrix.Translation((base_xy[0], base_xy[1], dims[2] / 2)))
    return ob


def surfaces(prefix=None):
    return [
        o for o in scene.objects
        if o.type == "MESH" and o.get("surfaceType") and (prefix is None or o.name.startswith(prefix))
    ]


def set_outdoor_bc(objs):
    for o in objs:
        t = o.get("surfaceType")
        try:
            o.osm_outsideBoundaryCondition = "Ground" if t == "Floor" else "Outdoors"
        except Exception as e:
            log(f"bc {e}")
            return


def render_mode(mode):
    with ov():
        bpy.ops.view3d.osm_render_mode(mode="OFF")
        if mode != "OFF":
            bpy.ops.view3d.osm_render_mode(mode=mode)
    space.shading.color_type = "MATERIAL" if mode == "OFF" else "OBJECT"


def mode_id(*needles):
    items = [i.identifier for i in bpy.ops.view3d.osm_render_mode.get_rna_type().properties["mode"].enum_items]
    for n in needles:
        for i in items:
            if n in i:
                return i
    return None


# ------------------------------------------------------------------------------------------------
def reset():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)
    for k in ("osm_library",):
        pass
    try:
        bpy.ops.scene.osm_clear_template()
    except Exception:
        pass
    bpy.context.preferences.view.ui_scale = 1.35
    space.shading.type = "SOLID"
    space.shading.color_type = "MATERIAL"
    space.overlay.show_floor = True
    space.overlay.show_overlays = True
    space.show_region_ui = True
    space.show_region_header = True
    space.show_region_tool_header = True
    space.show_region_toolbar = True
    props_area.spaces.active.context = "SCENE"
    bpy.context.view_layer.update()
    try:  # park the OS mouse over empty viewport so no tooltip covers the UI
        import ctypes

        ctypes.windll.user32.SetCursorPos(260, 900)
    except Exception:
        pass


def walkthrough():
    reset()
    S["target"] = (Vector((2.5, 0.5, 1.2)), 16.0, 62.0)
    r3d.view_perspective = "PERSP"
    r3d.view_location = Vector((2.5, 0.5, 1.2))
    r3d.view_distance = 16.0
    yield 1.5

    # 0 intro: the stock sidebar, panels collapsed
    restore_panels()
    yield from run(2.5, "Zonal  ·  Building energy modeling for Blender")
    yield from still("00-intro")

    # 1 template
    yield from set_panels(["library", "library_list"])
    yield from run(1.5, "1 · Open the OpenStudio tab and load the default template")
    with ov():
        bpy.ops.scene.osm_load_template("EXEC_DEFAULT", use_default=True)
    yield from run(2.5, "1 · Open the OpenStudio tab and load the default template")
    yield from still("01-template")

    # 2 space = collection
    yield from set_panels(["modeling", "matching", "loose"])
    office = make_collection("Office")
    yield from run(2.0, "2 · A space is a collection: create “Office” and make it active")
    yield from still("02-space-collection")

    # 3 draw the volume (grows from the floor)
    box = add_box("Office", (5, 4, 3), (0, 0))
    
    def grow(t):
        k = max(0.02, ease(t))
        box.scale = (k, k, k)

    yield from run(1.6, "3 · Model the volume with Blender's own tools (a 5 × 4 × 3 m box)", grow)
    box.scale = (1, 1, 1)
    select_objs([box])
    yield from still("03-box")

    # 4 separate faces -> one surface per face, typed from its normal
    select_objs([box])
    with ov():
        bpy.ops.object.osm_separate_faces()
    render_mode(mode_id("SURFACE_TYPE", "TYPE"))
    faces = surfaces("Office")
    homes = [(o, o.location.copy()) for o in faces]
    normals = []
    for o in faces:
        me = o.data
        n = (o.matrix_world.to_3x3() @ me.polygons[0].normal).normalized() if me.polygons else Vector((0, 0, 1))
        normals.append(n)

    def explode(t):
        k = math.sin(math.pi * t) ** 2 * 1.4
        for (o, home), n in zip(homes, normals):
            o.location = home + n * k

    yield from run(2.8, "4 · Separate Faces: every face becomes a surface (Wall, Floor, RoofCeiling)", explode)
    for o, home in homes:
        o.location = home
    yield from still("04-separate-faces")

    # 5 window from loose geometry
    loose = bpy.data.collections.get("OpenStudio - Loose Geometry") or bpy.data.collections.new(
        "OpenStudio - Loose Geometry"
    )
    if loose.name not in scene.collection.children:
        scene.collection.children.link(loose)
    from mathutils import Matrix

    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 0))
    win_ob = bpy.context.object
    win_ob.name = "Window rectangle"
    # mesh in world position on the south wall plane (y = -2), 1.8 x 1.2 m; the object is slid in along y
    win_ob.data.transform(Matrix.Translation((1.0, -2.0, 1.5)) @ Matrix.Rotation(math.radians(90), 4, "X") @ Matrix.Diagonal((1.8, 1.2, 1.0, 1.0)))
    for c in list(win_ob.users_collection):
        c.objects.unlink(win_ob)
    loose.objects.link(win_ob)
    win_ob.show_wire = True
    select_objs([win_ob])

    def slide(t):
        win_ob.location.y = -0.8 * (1 - ease(t))

    win_ob.location.y = -0.8
    yield from run(2.4, "5 · Draw a loose rectangle on the wall", slide)
    win_ob.location.y = 0.0
    bpy.context.view_layer.update()
    with ov():
        r = bpy.ops.object.osm_project_loose("EXEC_DEFAULT", scope="SELECTED")
    log(f"project {r}")
    loose.hide_viewport = True
    yield from run(2.4, "5 · Project Selected: it becomes a window (sub surface) on the wall behind")
    yield from still("05-window")

    # 6 properties: thermal zone + space
    def cen_y(o):
        return sum((o.matrix_world @ v.co).y for v in o.data.vertices) / len(o.data.vertices)

    south = min((o for o in surfaces("Office") if o.get("surfaceType") == "Wall"), key=cen_y)
    select_objs([south])
    yield from set_panels(["properties", "p_space", "p_zone", "p_boundary"])
    try:
        bpy.ops.object.osm_zone_add(key="thermalZoneName", name="Office Zone")
    except Exception as e:
        log(f"zone_add: {e}")
    for o in surfaces("Office"):
        try:
            o.osm_thermalZoneName = "Office Zone"
        except Exception:
            pass
    set_outdoor_bc(surfaces("Office"))
    yield from run(3.2, "6 · Pick the thermal zone and construction set from the library")
    yield from still("06-properties")

    # 7 colour modes
    yield from set_panels(["render", "render_colors"])
    select_objs([])
    for label, ids in (("Surface Type", ("SURFACE_TYPE", "TYPE")), ("Boundary Condition", ("BOUNDARY",)), ("Thermal Zone", ("ZONE",))):
        m = mode_id(*ids)
        if m:
            render_mode(m)
        yield from run(1.8, f"7 · Check the model with colour modes: {label}")
        yield from still(f"07-mode-{label.lower().replace(' ', '-')}")

    # 8 second space + matching
    render_mode(mode_id("SURFACE_TYPE", "TYPE"))
    yield from set_panels(["modeling", "matching", "loose", "render"])
    S["target"] = (Vector((2.5, 1.5, 1.2)), 19.0, 60.0)
    corridor_col = make_collection("Corridor")
    cor = add_box("Corridor", (5, 2, 3), (0, 3))
    select_objs([cor])
    with ov():
        bpy.ops.object.osm_separate_faces()
    set_outdoor_bc(surfaces("Corridor"))
    render_mode(mode_id("SURFACE_TYPE", "TYPE"))
    yield from run(2.4, "8 · Add a second space, “Corridor”, next to the office")
    with ov():
        bpy.ops.object.osm_match_surfaces(mode="MATCH", scope="ALL")
    render_mode(mode_id("BOUNDARY"))
    cor_faces = [(o, o.location.copy()) for o in surfaces("Corridor")]

    def slide_apart(t):  # pull the corridor away so the matched (Surface) wall is visible from both sides
        k = math.sin(math.pi * t) ** 2 * 2.6
        for o, home in cor_faces:
            o.location = home + Vector((0, k, 0))

    yield from run(4.0, "8 · Match All: the shared wall gets the boundary condition “Surface” in both spaces", slide_apart)
    for o, home in cor_faces:
        o.location = home
    yield from still("08-matching")

    # 9 export
    render_mode(mode_id("SURFACE_TYPE", "TYPE"))
    yield from set_panels(["render"])
    try:
        bpy.utils.unregister_class(m_ui.SCENE_PT_openstudio)
    except RuntimeError:
        pass
    d = demo_clone(m_ui.SCENE_PT_openstudio)
    bpy.utils.register_class(d)
    DEMO.append(d)
    redraw()
    yield 0.4
    yield from run(1.8, "9 · Export an OpenStudio model (.osm) from the Scene properties")
    osm_path = OUT / "office.osm"
    try:
        with ov():
            r = bpy.ops.export_scene.openstudio_osm("EXEC_DEFAULT", filepath=str(osm_path))
        log(f"export {r} {osm_path.exists()}")
    except Exception as e:
        log(f"export failed {e}")
    yield from run(2.6, "9 · Ready to run in OpenStudio / EnergyPlus")
    yield from still("09-export")

    # outro: the finished model, slow turn
    yield from set_panels(["render", "render_colors"])
    S["orbit"] = 8.0
    S["target"] = (Vector((2.5, 1.5, 1.2)), 17.0, 55.0)
    yield from run(3.0, "Zonal  ·  by Bitenergie")
    yield from still("10-final")


if not NO_RUN:
    def step_fn(gen=walkthrough()):
        try:
            return next(gen)
        except StopIteration:
            restore_panels()
            bpy.context.preferences.view.ui_scale = orig_ui_scale
            (OUT / "manifest.json").write_text(json.dumps({"fps": FPS, "frames": S["manifest"]}, indent=1))
            log(f"DONE {S['i']} frames")
            return None
        except Exception:
            log("ERROR\n" + traceback.format_exc())
            try:
                restore_panels()
                bpy.context.preferences.view.ui_scale = orig_ui_scale
            except Exception:
                pass
            return None


    log("started")
    bpy.app.timers.register(step_fn, first_interval=0.5)
