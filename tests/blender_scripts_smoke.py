"""Headless check of the Scripts panel (direct scene -> model path):

blender -b --factory-startup --python tests/blender_scripts_smoke.py -- C:\\path\\out.osm

Dev only: borrows the uv venv's site-packages for `openstudio` (Blender 5.x = same CPython ABI).
"""

import sys
from pathlib import Path

import bpy

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
sys.path.append(str(root / ".venv" / "Lib" / "site-packages"))
out_osm = sys.argv[sys.argv.index("--") + 1]

import zonal
from zonal.core import scripts_core as sc

bpy.ops.wm.read_factory_settings(use_empty=True)
zonal.register()
try:
    scene = bpy.context.scene
    s = scene.osm_scripts
    cats = sc.categories()  # the enum items are a dynamic callback
    print("categories:", cats)
    assert "Create Standard Building Shapes" in cats

    # --- dropdowns + variables
    s.category = "Create Standard Building Shapes"
    assert s.script in sc.scripts(s.category), s.script
    s.script = "Set L-Shape Floor Plan"
    keys = [a.key for a in s.args]
    print("L-shape args:", keys)
    assert keys[:2] == ["length", "width"] and "perimeter_zone_depth" in keys
    arg = {a.key: a for a in s.args}
    assert abs(arg["length"].float_value - 40.0) < 1e-6
    arg["perimeter_zone_depth"].float_value = 0.0
    arg["num_floors"].float_value = 2.0

    # --- shape generator, result into the scene (new collection)
    s.output = "SCENE"
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}, s.status
    meshes = [o for o in scene.objects if o.type == "MESH"]
    print("objects after L-shape:", len(meshes), s.status)
    assert any(str(o.get("spaceName", "")).startswith("Story 1 West Space") for o in meshes)
    assert any(o.get("surfaceType") == "Floor" for o in meshes)

    # --- alter script on the scene model: window to wall ratio on the selected wall
    s.category = "Alter or Add Model Elements"
    s.script = "Set Window to Wall Ratio"
    walls = [o for o in meshes if o.get("surfaceType") == "Wall" and o.get("outsideBoundaryCondition") == "Outdoors"]
    assert walls, "no exterior walls"
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    walls[0].select_set(True)
    bpy.context.view_layer.objects.active = walls[0]
    before = len([o for o in scene.objects if o.get("surfaceType") in ("FixedWindow", "OperableWindow")])
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}, s.status
    after = [o for o in scene.objects if o.type == "MESH" and o.get("surfaceType") in ("FixedWindow", "OperableWindow")]
    print("windows:", before, "->", len(after), s.status)
    assert before == 0 and len(after) == 1

    # --- same script, result as a file instead of touching the scene
    n_objects = len(scene.objects)
    s.output = "FILE"
    s.output_path = out_osm
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}, s.status
    assert Path(out_osm).is_file() and len(scene.objects) == n_objects

    # --- model dependent choice + report
    s.script = "Make Selected Surfaces Adiabatic and Assign a Construction"
    assert bpy.ops.scene.osm_scripts_refresh() == {"FINISHED"}
    s.category = "Reports"
    s.script = "Space Type Report"
    report = Path(out_osm).with_suffix(".csv")
    {a.key: a for a in s.args}["save_path"].path_value = str(report)
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}, s.status
    assert report.is_file() and "Space" in report.read_text(encoding="utf-8")
    # --- visualization: view layers per story, hide by space
    s.source = "SCENE"
    s.category = "Visualization"
    s.script = "Make View Layer For Each Building Story Floorplan"
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}, s.status
    layers = [v.name for v in scene.view_layers]
    print("view layers:", layers)
    assert "OS - All Stories" in layers and "OS - Story 1" in layers
    s.script = "Hide Spaces Included In Building Area"
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}, s.status
    meshes = [o for o in scene.objects if o.type == "MESH"]
    assert meshes and all(o.hide_get() for o in meshes)

    # --- wizard: result becomes the scene template, building defaults land in the Inspector
    s.category = "On Demand Template Generators"
    s.output = "SCENE"
    assert bpy.ops.scene.osm_run_script() == {"FINISHED"}, s.status
    assert scene.osm_template_path and Path(scene.osm_template_path).is_file(), scene.osm_template_path
    print("template:", scene.osm_template_path, "|", s.status)
    n_objects = len(scene.objects)
    assert n_objects == len(scene.objects)  # geometry untouched

    # the regular export now carries the wizard's space types, construction set and building defaults
    for o in scene.objects:
        o.hide_set(False)
    final = str(Path(out_osm).with_name("final.osm"))
    assert bpy.ops.export_scene.openstudio_osm(filepath=final) == {"FINISHED"}
    import openstudio

    m = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(final)).get()
    print("exported space types:", len(m.getSpaceTypes()), "construction sets:", len(m.getDefaultConstructionSets()))
    assert len(m.getSpaceTypes()) > 3 and len(m.getDefaultConstructionSets()) == 1
    b = m.getBuilding()
    print("building defaults:", b.spaceType().is_initialized(), b.defaultConstructionSet().is_initialized())
    assert b.spaceType().is_initialized() and b.defaultConstructionSet().is_initialized()
    print("OK")
finally:
    zonal.unregister()
