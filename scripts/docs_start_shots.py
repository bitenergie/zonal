"""Screenshots for docs/get-started.md and docs/interface-guide.md, taken from a *running* Blender.

    exec(open(r"C:\\Users\\marco\\source\\zonal\\scripts\\docs_start_shots.py").read())

Runs as a timer (UI stays alive) and writes raw PNGs + meta.json (region rectangles) to <repo>/.cache/start_shots.
scripts/process_start_shots.py crops / trims / annotates them into docs/img/start/*.webp.
Poll .cache/walkthrough/status.txt for DONE / ERROR. Needs Zonal installed and enabled (extension zonal).
"""

import json
import math
import traceback

ZONAL_NO_RUN = True
exec(compile(open(r"C:\Users\marco\source\zonal\scripts\docs_screencast.py", encoding="utf-8").read(), "docs_screencast.py", "exec"), globals())

OUT2 = ROOT / ".cache" / "start_shots"
OUT2.mkdir(parents=True, exist_ok=True)
for f in OUT2.glob("*.png"):
    f.unlink()
META = {}
orig_overlap = bpy.context.preferences.system.use_region_overlap
ui_region = next(r for r in area3d.regions if r.type == "UI")


def rects():
    out = []
    for a in screen.areas:
        out.append(
            {"type": a.type, "x": a.x, "y": a.y, "w": a.width, "h": a.height,
             "regions": {r.type: [r.x, r.y, r.width, r.height] for r in a.regions}}
        )
    return {"win": [win.width, win.height], "areas": out}


def snap(name, kind="window"):
    """kind: window (whole Blender window) | area (the 3D viewport area)."""
    redraw()
    yield 0.45
    path = OUT2 / f"{name}.png"
    if kind == "window":
        shot(path)
    else:
        with ov():
            bpy.ops.screen.screenshot_area(filepath=str(path))
    META[name] = {"kind": kind, **rects()}


def view(name, sidebar=False):
    space.show_region_ui = sidebar
    yield from snap(name, "area")


def panel(name, category="OpenStudio"):
    space.show_region_ui = True
    try:
        ui_region.active_panel_category = category
    except Exception:
        pass
    redraw()
    yield 0.5  # let the tab switch draw before the shot
    yield from snap(name, "area")


def cam(az, elev=60.0, dist=14.0, loc=(0.0, 0.0, 1.5)):
    r3d.view_perspective = "PERSP"
    r3d.view_location = Vector(loc)
    r3d.view_distance = dist
    r3d.view_rotation = Euler((math.radians(elev), 0, math.radians(az)), "XYZ").to_quaternion()


def start_shots():
    bpy.context.preferences.system.use_region_overlap = False  # opaque sidebar
    bpy.context.preferences.view.ui_scale = 1.25
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)
    try:
        bpy.ops.scene.osm_clear_template()
    except Exception:
        pass
    space.shading.type = "SOLID"
    space.shading.color_type = "MATERIAL"
    space.overlay.show_overlays = True
    space.overlay.show_floor = True
    space.show_region_header = space.show_region_tool_header = space.show_region_toolbar = True
    props_area.spaces.active.context = "SCENE"
    try:
        import ctypes

        ctypes.windll.user32.SetCursorPos(1200, 700)
    except Exception:
        pass
    yield 1.0

    # --- the factory scene: Collection / Cube / Light / Camera ------------------------------------
    base = bpy.data.collections.new("Collection")
    scene.collection.children.link(base)
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection.children["Collection"]
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
    bpy.context.object.name = "Cube"
    bpy.ops.object.light_add(type="POINT", location=(4.08, 1.0, 5.9))
    bpy.context.object.name = "Light"
    bpy.ops.object.camera_add(location=(7.36, -6.93, 4.96), rotation=(math.radians(63.6), 0, math.radians(46.7)))
    bpy.context.object.name = "Camera"
    cam(46.7, 63.6, 17.0, (0, 0, 0))
    select_objs([])
    restore_panels()
    yield 0.5
    yield from snap("00-overview", "window")
    yield from panel("01-panels")

    # --- 2 template -------------------------------------------------------------------------------
    for ob in [o for o in scene.objects]:
        bpy.data.objects.remove(ob)
    yield from set_panels(["library", "library_list"])
    bpy.ops.scene.osm_load_template("EXEC_DEFAULT", use_default=True)
    try:
        items = scene.bl_rna.properties["osm_library_category"].enum_items
        scene.osm_library_category = next(i.identifier for i in items if "Construction Set" in i.name)
    except Exception as e:
        log(f"library kind {e}")
    yield from panel("02-template-panel")

    # --- 3 the space = a collection ---------------------------------------------------------------
    bpy.data.collections.remove(bpy.data.collections["Collection"])
    make_collection("Office")
    cam(-35, 60, 14)
    yield from snap("03-outliner", "window")

    # --- 4 a box: Add > Mesh > Cube, then the numbers in Item ------------------------------------
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 1.5))
    box = bpy.context.object
    box.name = "Office"
    box.dimensions = (5, 4, 3)
    select_objs([box])
    yield from set_panels([])
    yield from panel("04a-item-panel", "Item")
    yield from view("04a-box")
    # draw tool in the toolbar
    with ov():
        bpy.ops.wm.tool_set_by_id(name="openstudio.draw_rectangle")
    try:
        rect = bpy.context.workspace.tools.from_space_view3d_mode("OBJECT").operator_properties("view3d.osm_draw_rectangle")
        rect.fixed_width, rect.fixed_height = 0.0, 0.0
        rect.clone_to_loose, rect.auto_boundary = False, False
    except Exception as e:
        log(f"rect props {e}")
    yield from view("04b-draw-tool")
    try:
        rect.clone_to_loose, rect.auto_boundary = True, True  # back to the defaults
    except Exception:
        pass
    with ov():
        bpy.ops.wm.tool_set_by_id(name="builtin.select_box")

    # --- 5 separate faces -------------------------------------------------------------------------
    yield from set_panels(["modeling", "matching", "loose"])
    select_objs([box])
    yield from panel("05a-modeling-panel")
    with ov():
        bpy.ops.object.osm_separate_faces()
    render_mode(mode_id("SURFACE_TYPE", "TYPE"))
    select_objs([])
    yield from view("05b-separated")

    # --- 6 window from loose geometry -------------------------------------------------------------
    from mathutils import Matrix

    loose = bpy.data.collections.get("OpenStudio - Loose Geometry") or bpy.data.collections.new("OpenStudio - Loose Geometry")
    if loose.name not in scene.collection.children:
        scene.collection.children.link(loose)
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 0))
    wob = bpy.context.object
    wob.name = "Window rectangle"
    wob.data.transform(Matrix.Translation((1.0, -2.0, 1.5)) @ Matrix.Rotation(math.radians(90), 4, "X") @ Matrix.Diagonal((1.8, 1.2, 1.0, 1.0)))
    for c in list(wob.users_collection):
        c.objects.unlink(wob)
    loose.objects.link(wob)
    wob.show_wire = True
    select_objs([wob])
    yield from view("06a-loose-rectangle")
    yield from panel("06a-subsurface-panel")
    with ov():
        bpy.ops.object.osm_project_loose("EXEC_DEFAULT", scope="SELECTED")
    loose.hide_viewport = True
    select_objs([])
    yield from view("06b-window")

    # --- 7 properties -----------------------------------------------------------------------------
    def cy(o):
        return sum((o.matrix_world @ v.co).y for v in o.data.vertices) / len(o.data.vertices)

    south = min((o for o in surfaces("Office") if o.get("surfaceType") == "Wall"), key=cy)
    try:
        bpy.ops.object.osm_zone_add(key="thermalZoneName", name="Office Zone")
    except Exception as e:
        log(f"zone {e}")
    for o in surfaces("Office"):
        try:
            o.osm_thermalZoneName = "Office Zone"
        except Exception:
            pass
    set_outdoor_bc(surfaces("Office"))
    select_objs([south])
    yield from set_panels(["properties", "p_space", "p_zone", "p_boundary"])
    yield from panel("07-properties-panel")
    yield from view("07-wall-selected")

    # --- 8 colour modes ---------------------------------------------------------------------------
    select_objs([])
    yield from set_panels(["render", "render_colors"])
    for tag, ids in (("surface-type", ("SURFACE_TYPE", "TYPE")), ("boundary", ("BOUNDARY",)), ("zone", ("ZONE",))):
        m = mode_id(*ids)
        if m:
            render_mode(m)
        yield from view(f"08-mode-{tag}")
        if tag == "surface-type":
            yield from panel("08-rendering-panel")

    # --- 9 second space + match -------------------------------------------------------------------
    render_mode(mode_id("SURFACE_TYPE", "TYPE"))
    make_collection("Corridor")
    cor = add_box("Corridor", (5, 2, 3), (0, 3))
    select_objs([cor])
    with ov():
        bpy.ops.object.osm_separate_faces()
    set_outdoor_bc(surfaces("Corridor"))
    cam(-35, 60, 17, (0, 1.5, 1.5))
    yield from set_panels(["modeling", "matching", "loose"])
    select_objs([])
    yield from view("09a-two-spaces")
    with ov():
        bpy.ops.object.osm_match_surfaces(mode="MATCH", scope="ALL")
    render_mode(mode_id("BOUNDARY"))
    faces = [(o, o.location.copy()) for o in surfaces("Corridor")]
    for o, home in faces:
        o.location = home + Vector((0, 1.8, 0))
    cam(-60, 55, 17, (0, 2.0, 1.5))
    yield from view("09b-matched")
    for o, home in faces:
        o.location = home
    yield from panel("09-matching-panel")
    yield from set_panels(["render", "render_colors"])
    yield from panel("09-legend-panel")

    # --- 10 export (Scene properties) -------------------------------------------------------------
    cam(-35, 60, 17, (0, 1.5, 1.5))
    render_mode(mode_id("SURFACE_TYPE", "TYPE"))
    try:
        bpy.utils.unregister_class(m_ui.SCENE_PT_openstudio)
    except RuntimeError:
        pass
    d = demo_clone(m_ui.SCENE_PT_openstudio)
    bpy.utils.register_class(d)
    DEMO.append(d)
    yield from set_panels([])
    yield from snap("10-export", "window")

    # --- done -------------------------------------------------------------------------------------
    restore_panels()
    bpy.context.preferences.view.ui_scale = orig_ui_scale
    bpy.context.preferences.system.use_region_overlap = orig_overlap
    space.show_region_ui = True
    (OUT2 / "meta.json").write_text(json.dumps(META, indent=1))
    log("START_SHOTS DONE")


def start_step(gen=start_shots()):
    try:
        return next(gen)
    except StopIteration:
        return None
    except Exception:
        log("ERROR\n" + traceback.format_exc())
        try:
            restore_panels()
            bpy.context.preferences.view.ui_scale = orig_ui_scale
            bpy.context.preferences.system.use_region_overlap = orig_overlap
        except Exception:
            pass
        return None


log("start_shots started")
bpy.app.timers.register(start_step, first_interval=0.5)
