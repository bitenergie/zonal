"""Produce the screenshots used by docs/interface-guide.md.

Run it through uv (starts a *GUI* Blender, because panels only exist in a window; it quits when done):

    uv run python scripts/docs_screenshots.py                 # Blender 5.x found under Program Files
    uv run python scripts/docs_screenshots.py --blender "C:\\Path\\to\\blender.exe"

    uv run python scripts/docs_screenshots.py --hero-only     # only the animated hero (img/hero.webp)
    uv run python scripts/docs_screenshots.py --no-hero       # only the still images

or directly:  blender --factory-startup --python scripts/docs_screenshots.py -- <out_dir> [--hero-only | --no-hero]

The hero animation (stories lift apart and settle again while the camera turns, colours switch from surface type to
building story) is rendered frame by frame from the viewport and encoded with ffmpeg (must be on PATH) to an animated WebP.

Needs a Blender whose Python matches the dev .venv (5.x = CPython 3.13), same as the other blender_* smoke tests.

The demo building is made with the add-on's own Scripts panel (L-shape floor plan, window-to-wall ratio, overhangs,
thermal zones) plus a neighbouring site-shading block, so the screenshots show a multi-storey model with windows and shading.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "img"


def _find_blender() -> str:
    if os.environ.get("BLENDER"):
        return os.environ["BLENDER"]
    base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation"
    cands = sorted(base.glob("Blender 5.*/blender.exe"), key=lambda p: float(p.parent.name.split()[-1]))
    if not cands:
        raise SystemExit("No Blender 5.x found; pass --blender or set BLENDER")
    return str(cands[-1])


def launcher() -> int:
    args = sys.argv[1:]
    exe = args[args.index("--blender") + 1] if "--blender" in args else _find_blender()
    out = Path(args[args.index("--out") + 1]) if "--out" in args else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    # opening a .blend skips the splash screen, which would otherwise cover the viewport
    blank = Path(tempfile.mkdtemp()) / "blank.blend"
    subprocess.check_call(
        [
            exe,
            "-b",
            "--factory-startup",
            "--python-expr",
            f"import bpy;bpy.ops.wm.save_as_mainfile(filepath={str(blank)!r})",
        ]
    )
    # fullscreen keeps window managers such as PowerToys FancyZones from snapping the window to a zone
    size = ["--window-geometry", "0", "0", "1500", "950"] if "--windowed" in args else ["--window-fullscreen"]
    flags = [f for f in ("--hero-only", "--no-hero") if f in args]
    cmd = [
        exe,
        "--factory-startup",
        *size,
        str(blank),
        "--python",
        str(Path(__file__).resolve()),
        "--",
        str(out),
        *flags,
    ]
    print("running:", " ".join(cmd))
    return subprocess.call(cmd)


# --------------------------------------------------------------------------------------
# Everything below runs inside Blender
# --------------------------------------------------------------------------------------

try:
    import bpy  # noqa: F401

    # the dev .venv also has the bpy wheel: only the `blender ... -- <out>` run (not the uv launcher) is 'inside Blender'
    # LIVE: exec'd inside a running Blender (ZONAL_LIVE=True, ZONAL_ARGS=[out_dir, flags]); uses the installed extension,
    # does not register it again, does not toggle fullscreen and does not quit Blender (restores the sidebar panels).
    LIVE = bool(globals().get("ZONAL_LIVE"))
    IN_BLENDER = "--" in sys.argv or LIVE
except ImportError:
    IN_BLENDER = False


def blender_main():
    import bpy
    import numpy as np
    from mathutils import Euler

    if LIVE:
        import importlib

        pkg = "bl_ext.user_default.zonal"
        zonal = importlib.import_module(pkg)
        blender_inspector, blender_props, blender_results, blender_scripts, blender_search, blender_surface_ops, blender_tools = (
            importlib.import_module(f"{pkg}.blender.{m}")
            for m in ("inspector", "props", "results", "scripts", "search", "surface_ops", "tools")
        )
        user_args = list(globals()["ZONAL_ARGS"])
        # panel helpers (ORIGINAL list, clones with fresh collapsed state): see docs_screencast.py
        globals()["ZONAL_NO_RUN"] = True
        exec(compile((ROOT / "scripts" / "docs_screencast.py").read_text(encoding="utf-8"), "docs_screencast.py", "exec"), globals())
    else:
        sys.path.insert(0, str(ROOT / "src"))
        sys.path.append(str(ROOT / ".venv" / "Lib" / "site-packages"))  # openstudio bindings (same CPython ABI)
        user_args = sys.argv[sys.argv.index("--") + 1 :]
    out = Path(user_args[0])
    hero_only, no_hero = "--hero-only" in user_args, "--no-hero" in user_args
    out.mkdir(parents=True, exist_ok=True)

    if not LIVE:
        import zonal
        from zonal.blender import inspector as blender_inspector
        from zonal.blender import props as blender_props
        from zonal.blender import results as blender_results
        from zonal.blender import scripts as blender_scripts
        from zonal.blender import search as blender_search
        from zonal.blender import surface_ops as blender_surface_ops
        from zonal.blender import tools as blender_tools

    if not LIVE:
        zonal.register()
    scene = bpy.context.scene
    win = bpy.context.window_manager.windows[0]

    # ---- scene -----------------------------------------------------------------
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)

    def run_script(category, name, **values):
        """Run a Scripts-panel script exactly like the Run button (scene in, scene out)."""
        st = scene.osm_scripts
        st.category = category
        st.script = name
        for a in st.args:
            if a.key in values:
                a.set_value(values[a.key])
        st.source, st.output = "SCENE", "SCENE"
        res = bpy.ops.scene.osm_run_script("EXEC_DEFAULT")
        print("script", name, res, st.status)

    def select_objs(objs, active=None):
        for o in scene.objects:
            o.select_set(False)
        for o in objs:
            o.select_set(True)
        if objs:
            bpy.context.view_layer.objects.active = active or objs[0]

    space_of = blender_surface_ops._space_of  # a surface's space = its collection

    def meshes(stype):
        return [o for o in scene.objects if o.type == "MESH" and o.get("surfaceType") == stype]

    bpy.ops.scene.osm_load_template("EXEC_DEFAULT", use_default=True)

    # 1. four storeys from the L-shape floor plan script (perimeter + core zones)
    run_script(
        "Create Standard Building Shapes",
        "Set L-Shape Floor Plan",
        length=36.0,
        width=30.0,
        lower_end_width=14.0,
        upper_end_length=16.0,
        num_floors=4,
        floor_to_floor_height=3.6,
        plenum_height=0.0,
        perimeter_zone_depth=4.5,
    )
    select_objs([])
    bpy.ops.object.osm_match_surfaces(mode="MATCH", scope="ALL")
    # 2. windows: window-to-wall ratio on every outer wall
    select_objs(meshes("Wall"))
    run_script("Alter or Add Model Elements", "Set Window to Wall Ratio", wwr=0.35, offset=0.9)
    # 3. overhangs on all windows (space shading), then thermal zones
    select_objs(meshes("FixedWindow"))
    run_script("Alter or Add Model Elements", "Add Overhangs by Projection Factor", projection_factor=0.5)
    select_objs([])
    run_script("Alter or Add Model Elements", "Add New Thermal Zone For Spaces With No Thermal Zone")
    # 4. site shading: a neighbouring block east of the building
    bpy.ops.mesh.primitive_cube_add(size=1, location=(52.0, 12.0, 7.0))
    block = bpy.context.object
    block.name = "Neighbour"
    block.scale = (10.0, 18.0, 14.0)
    bpy.ops.object.transform_apply(scale=True)
    bpy.ops.object.osm_separate_faces()
    select_objs([o for o in scene.objects if o.name.startswith("Neighbour")])
    bpy.ops.object.osm_set_shading(group="SiteShading")
    scene["northAxis"] = 0.0
    wall = next(o for o in meshes("Wall") if space_of(o).startswith("Story 1 South"))
    window = next(o for o in meshes("FixedWindow") if space_of(o).startswith("Story 2"))
    print(
        "model:",
        {k: len(meshes(k)) for k in ("Wall", "Floor", "RoofCeiling", "FixedWindow", "SiteShading", "SpaceShading")},
    )

    # ---- viewport / ui prep ----------------------------------------------------
    try:
        bpy.context.preferences.system.use_region_overlap = False  # opaque sidebar
    except Exception:
        pass
    area3d = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    space = area3d.spaces.active
    r3d = space.region_3d
    space.shading.type = "SOLID"
    space.overlay.show_floor = True
    r3d.view_perspective = "PERSP"
    r3d.view_rotation = Euler((math.radians(65), 0, math.radians(35)), "XYZ").to_quaternion()

    def select(*obs, active=None):
        select_objs(list(obs), active)

    def override(area, region_type="WINDOW"):
        region = next(r for r in area.regions if r.type == region_type)
        return bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region)

    with override(area3d):
        bpy.ops.view3d.view_all()
    r3d.view_distance *= 0.8

    def redraw():
        for a in win.screen.areas:
            a.tag_redraw()

    # ---- image helpers ---------------------------------------------------------
    def grab(name, crop=None, trim=False):
        """Screenshot the 3D area to out/<name>.png. crop=(x0, x1) pixel columns; trim removes empty space below."""
        path = out / f"{name}.png"
        # always render into a fresh temp file and move it over the target: never depends on the old file
        # (an existing image is not overwritten by screenshot_area on some setups, and may be locked by a viewer)
        tmp = Path(tempfile.mkdtemp(prefix="osm_shot_")) / f"{name}.png"
        with override(area3d):
            bpy.ops.screen.screenshot_area(filepath=str(tmp))
        if crop or trim:
            img = bpy.data.images.load(str(tmp))
            w, h = img.size
            px = np.empty(w * h * 4, np.float32)
            img.pixels.foreach_get(px)
            px = px.reshape(h, w, 4)
            x0, x1 = crop or (0, w)
            px = px[:, x0:x1]
            if trim:  # rows are bottom-up: keep from the lowest non-background row (panel bottom) to the top
                body = px[:, 6 : px.shape[1] - 48]
                bg = np.median(body.reshape(-1, 4), axis=0)
                pc = (np.abs(body - bg) > 0.015).any(axis=2).any(axis=1)
                ys = np.nonzero(pc)[0][::-1]  # top of the image first
                y0 = 0
                if len(ys):
                    y0 = int(ys[0])
                    for y in ys[1:]:  # walk down while content continues (gaps up to 60 px)
                        if y0 - y > 60:
                            break
                        y0 = int(y)
                    y0 = max(y0 - 14, 0)
                px = px[y0:]
            nh, nw = px.shape[:2]
            res = bpy.data.images.new(name, nw, nh, alpha=False)
            res.pixels.foreach_set(np.ascontiguousarray(px).ravel())
            res.filepath_raw = str(tmp)
            res.file_format = "PNG"
            res.save()
            bpy.data.images.remove(res)
            bpy.data.images.remove(img)
        try:
            os.replace(tmp, path)
        except OSError as e:  # e.g. the old image is open in another program
            print(f"!! could not overwrite {path}: {e}; new image kept at {tmp}")
            return
        print("saved", path)

    ui = next(r for r in area3d.regions if r.type == "UI")
    PANELS = {
        "modeling": blender_tools.VIEW3D_PT_osm_modeling,
        "rendering": blender_tools.VIEW3D_PT_osm_render,
        "properties": blender_props.VIEW3D_PT_osm_properties,
        "library": blender_props.VIEW3D_PT_osm_library,
        "search": blender_search.VIEW3D_PT_osm_search,
        "inspector": blender_inspector.VIEW3D_PT_osm_inspector,
        "scripts": blender_scripts.VIEW3D_PT_osm_scripts,
        "results": blender_results.VIEW3D_PT_osm_results,
    }
    ORIG_OPTS = {cls: set(getattr(cls, "bl_options", set())) for cls in PANELS.values()}
    orig_scale, orig_overlap = bpy.context.preferences.view.ui_scale, bpy.context.preferences.system.use_region_overlap
    for cls in PANELS.values():
        cls.bl_options = set()  # start expanded

    def restore_live():
        _unreg_demo()
        for cls in PANELS.values():
            cls.bl_options = ORIG_OPTS[cls]
        restore_panels()
        bpy.context.preferences.view.ui_scale = orig_scale
        bpy.context.preferences.system.use_region_overlap = orig_overlap
        clean_view(False)
        space.show_region_ui = True

    def clean_view(on: bool):
        """Panel shots: uniform empty viewport behind the (overlapping) sidebar, no headers."""
        space.show_object_viewport_mesh = not on
        space.overlay.show_overlays = not on
        space.show_gizmo = not on
        space.show_region_header = not on
        space.show_region_tool_header = not on
        space.show_region_toolbar = not on
        space.shading.background_type = "VIEWPORT" if on else "THEME"
        space.shading.background_color = (0.16, 0.16, 0.16)

    def family(cls):
        out = [cls]
        for c in ORIGINAL:
            if getattr(c, "bl_parent_id", None) == cls.bl_idname:
                out += family(c)
        return out

    def show_only_live(key):
        clean_view(True)
        _unreg_demo()
        for c in reversed(ORIGINAL):
            try:
                bpy.utils.unregister_class(c)
            except RuntimeError:
                pass
        for i, c in enumerate(family(PANELS[key])):
            attrs = {"bl_idname": c.bl_idname + "_demo"}
            if i == 0:
                attrs["bl_options"] = set()  # top-level panel expanded, sub-panels as in the add-on
            if getattr(c, "bl_parent_id", ""):
                attrs["bl_parent_id"] = c.bl_parent_id + "_demo"
            d = type(c.__name__ + "_demo", (c,), attrs)
            bpy.utils.register_class(d)
            DEMO.append(d)
        space.show_region_ui = True
        try:
            ui.active_panel_category = "OpenStudio"
        except Exception:
            pass
        redraw()

    def show_only(key):
        if LIVE:
            return show_only_live(key)
        clean_view(True)
        for cls in PANELS.values():
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass
        bpy.utils.register_class(PANELS[key])
        space.show_region_ui = True
        try:
            ui.active_panel_category = "OpenStudio"
        except Exception:
            pass
        redraw()

    def panel_shot(key, fname=None):
        show_only(key)
        yield 0.6
        w = area3d.width
        grab(fname or f"panel-{key}", crop=(ui.x - area3d.x, w), trim=True)
        clean_view(False)

    def viewport_shot(name):
        clean_view(False)
        space.show_region_ui = False
        redraw()
        yield 0.6
        grab(name)

    def render_mode(mode):
        """Reset the viewport colours first so every shot starts from fresh colours, then apply the mode."""
        with override(area3d):
            bpy.ops.view3d.osm_render_mode(mode="OFF")
            if mode != "OFF":
                bpy.ops.view3d.osm_render_mode(mode=mode)
        space.shading.color_type = "MATERIAL" if mode == "OFF" else "OBJECT"

    # ---- the shot list ---------------------------------------------------------
    def ensure_fullscreen():
        """Window managers (FancyZones) snap the new window into a zone after it opened: undo that."""
        if os.name != "nt" or LIVE:
            return False
        import ctypes

        sw, sh = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
        if win.width >= sw - 8 and win.height >= sh - 8:
            return False
        with bpy.context.temp_override(window=win, screen=win.screen, area=area3d):
            bpy.ops.wm.window_fullscreen_toggle()
        return True

    def stills():
        render_mode("SURFACE_TYPE")  # active (after a reset) for every shot except the boundary-condition one
        select(wall)
        yield from panel_shot("modeling")
        yield from panel_shot("properties")
        yield from panel_shot("rendering")
        yield from panel_shot("library")

        s = scene.osm_search
        s.type = "Floor"
        with override(area3d):
            bpy.ops.object.osm_search_model()
        yield from panel_shot("search", "panel-search")
        render_mode("SURFACE_TYPE")
        yield from viewport_shot("view-search-result")
        s.type = "ANY"

        try:
            insp = scene.osm_inspector
            insp.type = "Surface"
            insp.object = window.name
        except Exception as e:
            print("inspector setup failed:", e)
        yield from panel_shot("inspector", "panel-inspector-surface")
        try:
            insp.type = "ThermalZone"
            names = blender_inspector.object_names(scene, "ThermalZone")
            if names:
                insp.object = names[0]
        except Exception as e:
            print("inspector zone setup failed:", e)
        yield from panel_shot("inspector", "panel-inspector-zone")

        st = scene.osm_scripts
        st.category = "Alter or Add Model Elements"
        st.script = "Set Window to Wall Ratio"
        yield from panel_shot("scripts")
        yield from panel_shot("results")

        select()
        render_mode("SURFACE_TYPE")
        yield from viewport_shot("view-model")
        render_mode("SURFACE_TYPE")
        yield from viewport_shot("view-render-surface-type")
        render_mode("BOUNDARY")
        yield from viewport_shot("view-render-boundary")
        render_mode("SURFACE_TYPE")

        with override(area3d):
            bpy.ops.wm.tool_set_by_id(name="openstudio.draw_rectangle")
        rect = bpy.context.workspace.tools.from_space_view3d_mode("OBJECT").operator_properties(
            "view3d.osm_draw_rectangle"
        )
        rect.fixed_width, rect.fixed_height = 1.0, 2.0
        select(wall)
        yield from viewport_shot("view-draw-rectangle-tool")
        with override(area3d):
            bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
        render_mode("OFF")  # leave the scene reset

    # ---- hero animation --------------------------------------------------------------
    HERO_FRAMES = 168  # 7 s at 24 fps: one slow turn

    def story_of(ob):
        import re

        m = re.match(r"Story (\d+)", space_of(ob) or str(ob.get("spaceName", "")))
        return int(m.group(1)) if m else 0

    def hero_frames():
        """Stylised loop: the storeys lift apart and settle again while the camera makes one turn;
        colours switch from surface type to building story while the model is apart."""
        import shutil

        frames_dir = Path(tempfile.mkdtemp(prefix="osm_hero_"))
        movers = [(o, story_of(o), o.location.z) for o in scene.objects if o.type == "MESH" and story_of(o) > 0]
        gap = 4.5  # metres between storeys when fully apart
        clean_view(True)
        space.show_object_viewport_mesh = True  # clean_view hides meshes for the panel shots
        space.show_region_ui = False
        space.overlay.show_overlays = False
        # chroma key: a pure green background that ffmpeg turns into transparency (Standard view transform keeps it exact)
        space.shading.background_color = (0.0, 1.0, 0.0)
        view = scene.view_settings
        old_transform, old_look = view.view_transform, view.look
        view.view_transform, view.look = "Standard", "None"
        select()
        render_mode("SURFACE_TYPE")
        r3d.view_perspective = "PERSP"
        with override(area3d):
            bpy.ops.view3d.view_all()
        r3d.view_distance *= 0.85
        base_dist = r3d.view_distance
        base_loc = r3d.view_location.copy()
        mode = "SURFACE_TYPE"
        yield 0.5
        for i in range(HERO_FRAMES):
            t = i / HERO_FRAMES  # last frame != first: the loop closes by itself
            apart = math.sin(math.pi * t) ** 2  # 0 at both ends
            apart = apart * apart * (3 - 2 * apart)  # smoothstep: dwell at both ends
            for ob, story, z0 in movers:
                ob.location.z = z0 + (story - 1) * gap * apart
            r3d.view_location = (base_loc.x, base_loc.y, base_loc.z + 1.5 * (len({m[1] for m in movers}) - 1) * apart)
            r3d.view_distance = base_dist * (1.0 + 0.25 * apart)
            r3d.view_rotation = Euler(
                (math.radians(62 - 6 * apart), 0, math.radians(35 + 360 * t)), "XYZ"
            ).to_quaternion()
            want = "STORY" if 0.22 < t < 0.78 else "SURFACE_TYPE"
            if want != mode:
                render_mode(want)
                mode = want
            redraw()
            yield 0.12
            tmp = frames_dir / f"f{i:03d}.png"
            with override(area3d):
                bpy.ops.screen.screenshot_area(filepath=str(tmp))
        for ob, _story, z0 in movers:
            ob.location.z = z0
        render_mode("OFF")
        clean_view(False)
        view.view_transform, view.look = old_transform, old_look
        ffmpeg = shutil.which("ffmpeg")
        target = out / "hero.webp"
        if ffmpeg is None:
            print(f"!! ffmpeg not found on PATH: frames are in {frames_dir}")
            return
        # centre 3:2 crop, 960 px wide, 24 fps, endless loop
        vf = (
            "crop=min(iw\\,ih*3/2):min(ih\\,iw*2/3),scale=900:-2:flags=lanczos,"
            "colorkey=0x00FF00:0.22:0.08,format=yuva420p"
        )
        cmd = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            "24",
            "-i",
            str(frames_dir / "f%03d.png"),
            "-vf",
            vf,
            "-loop",
            "0",
            "-quality",
            "75",
            "-compression_level",
            "6",
            str(tmp_out := out / "hero.tmp.webp"),
        ]
        subprocess.check_call(cmd)
        os.replace(tmp_out, target)
        shutil.rmtree(frames_dir, ignore_errors=True)
        print("saved", target)

    def script():
        yield 3.0  # FancyZones & co. move the window shortly after it appears
        for _ in range(4):
            if not ensure_fullscreen():
                break
            yield 2.0
        yield 1.0
        r3d.view_location = r3d.view_location  # touch: the area size changed, keep the framing below valid
        with override(area3d):
            bpy.ops.view3d.view_all()
        r3d.view_distance *= 0.8
        yield 1.0
        if not hero_only:
            yield from stills()
        if not no_hero:
            yield from hero_frames()
        print("docs screenshots OK ->", out)
        if LIVE:
            restore_live()
            (ROOT / ".cache" / "walkthrough" / "status.txt").write_text("SHOTS DONE")
        else:
            bpy.ops.wm.quit_blender()

    gen = script()

    def step():
        try:
            return next(gen)
        except StopIteration:
            return None
        except Exception:
            import traceback

            traceback.print_exc()
            if LIVE:
                (ROOT / ".cache" / "walkthrough" / "status.txt").write_text("ERROR\n" + traceback.format_exc())
                restore_live()
            else:
                bpy.ops.wm.quit_blender()
            return None

    bpy.app.timers.register(step, first_interval=1.0)


if IN_BLENDER:
    blender_main()
elif __name__ == "__main__":
    raise SystemExit(launcher())
