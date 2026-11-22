"""Showcase building shots for docs/index.md, taken from a *running* Blender (timer driven).

    exec(compile(open(r"C:\\Users\\marco\\source\\zonal\\scripts\\docs_showcase.py", encoding="utf-8").read(), "docs_showcase.py", "exec"))

Builds several different buildings with the "Create Standard Building Shapes" scripts, adds windows / shading,
colours them with a render mode and writes clean viewport PNGs (no UI) to <repo>/.cache/showcase.
scripts/process_showcase.py turns them into docs/img/showcase/*.webp. Status: .cache/walkthrough/status.txt.
"""

import math
import traceback

ZONAL_NO_RUN = True
exec(compile(open(r"C:\Users\marco\source\zonal\scripts\docs_screencast.py", encoding="utf-8").read(), "docs_screencast.py", "exec"), globals())

OUT3 = ROOT / ".cache" / "showcase"
OUT3.mkdir(parents=True, exist_ok=True)
for f in OUT3.glob("*.png"):
    f.unlink()

SPACE_OF = m_surface_ops._space_of if "m_surface_ops" in globals() else None
if SPACE_OF is None:
    import importlib

    SPACE_OF = importlib.import_module("bl_ext.user_default.zonal.blender.surface_ops")._space_of


def run_script(category, name, **values):
    st = scene.osm_scripts
    st.category, st.script = category, name
    for a in st.args:
        if a.key in values:
            a.set_value(values[a.key])
    st.source, st.output = "SCENE", "SCENE"
    bpy.ops.scene.osm_run_script("EXEC_DEFAULT")


def meshes(stype):
    return [o for o in scene.objects if o.type == "MESH" and o.get("surfaceType") == stype]


def add_block(name, loc, dims):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    b = bpy.context.object
    b.name = name
    b.scale = dims
    bpy.ops.object.transform_apply(scale=True)
    bpy.ops.object.osm_separate_faces()
    select_objs([o for o in scene.objects if o.name.startswith(name)])
    bpy.ops.object.osm_set_shading(group="SiteShading")


def build(shape, params, wwr, overhang, blocks):
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)
    bpy.ops.scene.osm_load_template("EXEC_DEFAULT", use_default=True)
    run_script("Create Standard Building Shapes", shape, **params)
    select_objs([])
    bpy.ops.object.osm_match_surfaces(mode="MATCH", scope="ALL")
    select_objs(meshes("Wall"))
    run_script("Alter or Add Model Elements", "Set Window to Wall Ratio", wwr=wwr, offset=0.9)
    if overhang:
        select_objs(meshes("FixedWindow"))
        run_script("Alter or Add Model Elements", "Add Overhangs by Projection Factor", projection_factor=overhang)
    select_objs([])
    run_script("Alter or Add Model Elements", "Add New Thermal Zone For Spaces With No Thermal Zone")
    for i, (loc, dims) in enumerate(blocks):
        add_block(f"Neighbour{i}", loc, dims)
    select_objs([])


def clean(on):
    space.show_region_ui = not on
    space.show_region_header = not on
    space.show_region_tool_header = not on
    space.show_region_toolbar = not on
    space.overlay.show_overlays = not on
    space.show_gizmo = not on
    space.shading.type = "SOLID"
    space.shading.light = "STUDIO"
    space.shading.show_shadows = on
    space.shading.shadow_intensity = 0.35
    space.shading.show_cavity = False
    space.shading.show_object_outline = False
    space.shading.background_type = "VIEWPORT" if on else "THEME"
    space.shading.background_color = (0.93, 0.94, 0.96)


def frame_view(az, elev, zoom=1.0):
    r3d.view_perspective = "PERSP"
    r3d.view_rotation = Euler((math.radians(elev), 0, math.radians(az)), "XYZ").to_quaternion()
    pts = [o.matrix_world @ Vector(c) for o in scene.objects if o.type == "MESH" for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    r3d.view_location = (lo + hi) / 2
    r3d.view_distance = (hi - lo).length / 2 * zoom  # zoom = distance in building radii (smaller = closer)


BUILDINGS = [
    ("courtyard", "Set Courtyard Floor Plan",
     dict(length=54.0, width=34.0, courtyard_length=20.0, courtyard_width=8.0, num_floors=3, floor_to_floor_height=3.6, plenum_height=0.0, perimeter_zone_depth=4.5),
     0.30, 0.0, [], ("SURFACE_TYPE", "TYPE"), (-38, 60, 2.4)),
    ("u-shape", "Set U-Shape Floor Plan",
     dict(length=48.0, left_width=36.0, right_width=36.0, left_end_length=14.0, right_end_length=14.0, left_end_offset=24.0, num_floors=4, floor_to_floor_height=3.6, plenum_height=0.0, perimeter_zone_depth=4.5),
     0.40, 0.5, [((-16.0, 18.0, 6.0), (10.0, 20.0, 12.0))], ("BOUNDARY",), (-128, 62, 2.3)),
    ("h-shape", "Set H-Shape Floor Plan",
     dict(length=52.0, left_width=36.0, center_width=10.0, right_width=36.0, left_end_length=16.0, right_end_length=16.0, left_upper_end_offset=13.0, right_upper_end_offset=13.0, num_floors=3, floor_to_floor_height=3.6, plenum_height=0.0, perimeter_zone_depth=4.5),
     0.35, 0.0, [], ("ZONE",), (-35, 58, 2.5)),
    ("tower", "Set Rectangular Floor Plan",
     dict(length=26.0, width=18.0, num_floors=12, floor_to_floor_height=3.6, plenum_height=0.0, perimeter_zone_depth=4.5),
     0.45, 0.0, [((-24.0, 9.0, 12.0), (12.0, 14.0, 24.0)), ((40.0, 9.0, 8.0), (12.0, 16.0, 16.0))], ("SURFACE_TYPE", "TYPE"), (-50, 78, 2.3)),
    ("t-shape", "Set T-Shape Floor Plan",
     dict(length=50.0, width=38.0, upper_end_width=16.0, lower_end_length=20.0, left_end_offset=15.0, num_floors=3, floor_to_floor_height=3.6, plenum_height=0.0, perimeter_zone_depth=4.5),
     0.35, 0.5, [], ("BOUNDARY",), (-32, 58, 2.3)),
]


def set_boundary_all():
    set_outdoor_bc([o for o in scene.objects if o.type == "MESH"])


def showcase():
    bpy.context.preferences.system.use_region_overlap = False
    for key, shape, params, wwr, over, blocks, mode, (az, elev, zoom) in BUILDINGS:
        log(f"building {key}")
        clean(False)
        yield 0.3
        build(shape, params, wwr, over, blocks)
        yield 0.5
        clean(True)
        frame_view(az, elev, zoom)
        render_mode(mode_id(*mode))
        redraw()
        yield 1.0
        with ov():
            bpy.ops.screen.screenshot_area(filepath=str(OUT3 / f"{key}.png"))
        yield 0.3
    clean(False)
    render_mode("OFF")
    log("SHOWCASE DONE")


def showcase_step(gen=showcase()):
    try:
        return next(gen)
    except StopIteration:
        return None
    except Exception:
        log("ERROR\n" + traceback.format_exc())
        return None


log("showcase started")
bpy.app.timers.register(showcase_step, first_interval=0.5)
