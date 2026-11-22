"""Ported user scripts (scripts_core + user_scripts/*): argument tables and behaviour on real OpenStudio models."""

import pytest

openstudio = pytest.importorskip("openstudio")

from zonal.core import scripts_core as sc

SHAPES = "Create Standard Building Shapes"
SHAPE_COUNTS = {  # (perimeter spaces, minimal spaces) per floor
    "Set Rectangular Floor Plan": (5, 1),
    "Set L-Shape Floor Plan": (8, 2),
    "Set T-Shape Floor Plan": (10, 2),
    "Set U-Shape Floor Plan": (11, 3),
    "Set H-Shape Floor Plan": (15, 3),
    "Set Courtyard Floor Plan": (12, 4),
}


def ctx(**kw):
    return sc.ScriptContext(**kw)


def run(category, name, args=None, model=None, **ctx_kw):
    model = model or openstudio.model.Model()
    c = ctx(**ctx_kw)
    ok = sc.get(category, name).execute(model, args, c)
    return model, c, ok


def test_registry_has_all_categories():
    cats = sc.categories()
    assert {SHAPES, "Alter or Add Model Elements", "Reports"} <= set(cats)
    assert len(sc.scripts(SHAPES)) == 6


def test_every_script_has_valid_args():
    model = openstudio.model.Model()
    for cat in sc.categories():
        for name in sc.scripts(cat):
            script = sc.get(cat, name)
            keys = [a.key for a in script.arguments(model)]
            assert len(keys) == len(set(keys)), name
            assert set(script.defaults(model)) == set(keys)
            for a in script.arguments(model):
                if a.type == "choice" and a.choices:  # model dependent lists are empty for an empty model
                    assert a.default in [c[0] for c in a.choices], (name, a.key)


@pytest.mark.parametrize("name", sorted(SHAPE_COUNTS))
def test_shape_defaults(name):
    n_perim, n_min = SHAPE_COUNTS[name]
    model, c, ok = run(SHAPES, name, {"num_floors": 2})
    assert ok, c.messages
    assert len(model.getSpaces()) == 2 * n_perim
    assert len(model.getBuildingStorys()) == 2
    model, c, ok = run(SHAPES, name, {"perimeter_zone_depth": 0, "num_floors": 3})
    assert ok and len(model.getSpaces()) == 3 * n_min
    for sp in model.getSpaces():
        assert sp.floorArea() > 0


def test_shape_floor_areas():
    model, _, ok = run(SHAPES, "Set Rectangular Floor Plan", {"length": 30, "width": 10, "num_floors": 1})
    assert ok and sum(s.floorArea() for s in model.getSpaces()) == pytest.approx(300)
    model, _, ok = run(SHAPES, "Set L-Shape Floor Plan", {})  # 40x40 minus the 20x20 corner
    assert ok and sum(s.floorArea() for s in model.getSpaces()) == pytest.approx(1200)
    model, _, ok = run(SHAPES, "Set Courtyard Floor Plan", {})  # 50x30 minus 15x5
    assert ok and sum(s.floorArea() for s in model.getSpaces()) == pytest.approx(1500 - 75)


def test_shape_validation_errors():
    _, c, ok = run(SHAPES, "Set L-Shape Floor Plan", {"lower_end_width": 50})
    assert not ok and "Lower end width" in c.messages[0].text
    _, c, ok = run(SHAPES, "Set Rectangular Floor Plan", {"perimeter_zone_depth": 50})
    assert not ok and "Perimeter zone depth" in c.messages[0].text


def shape_model():
    return run(SHAPES, "Set Rectangular Floor Plan", {"num_floors": 1})[0]


def test_new_zones_and_rename_and_remove_unused():
    model = shape_model()
    cat = "Alter or Add Model Elements"
    _, _c, ok = run(cat, "Add New Thermal Zone For Spaces With No Thermal Zone", model=model)
    assert ok and len(model.getThermalZones()) == 5
    run(cat, "Rename Thermal Zones Based On Space Names", model=model)
    assert all(z.nameString().startswith("Thermal Zone: Story 1") for z in model.getThermalZones())
    for sp in list(model.getSpaces())[:2]:
        sp.resetThermalZone()
    run(cat, "Remove Unused ThermalZones", model=model)
    assert len(model.getThermalZones()) == 3


def test_assign_stories_and_wwr_with_selection():
    cat = "Alter or Add Model Elements"
    model = shape_model()
    for sp in model.getSpaces():
        sp.resetBuildingStory()
    run(cat, "Assign Building Stories", model=model)
    assert all(not sp.buildingStory().empty() for sp in model.getSpaces())

    walls = [s for s in model.getSurfaces() if s.surfaceType() == "Wall"]
    target = walls[0]
    _, c, ok = run(cat, "Set Window to Wall Ratio", {"wwr": 0.3}, model=model, selected_names={target.nameString()})
    assert ok
    assert len(model.getSubSurfaces()) == 1
    assert model.getSubSurfaces()[0].grossArea() / target.grossArea() == pytest.approx(0.3, rel=1e-2)
    # nothing selected -> not applicable, no windows added
    model2 = shape_model()
    _, c, ok = run(cat, "Set Window to Wall Ratio", model=model2)
    assert ok and not c.applicable and not model2.getSubSurfaces()


def test_overhangs_and_adiabatic_and_remove_hard_constructions():
    cat = "Alter or Add Model Elements"
    model = shape_model()
    space = model.getSpaces()[0]
    run(cat, "Set Window to Wall Ratio", model=model, selected_spaces={space.nameString()})
    assert model.getSubSurfaces()
    _, _c, ok = run(cat, "Add Overhangs by Projection Factor", model=model, selected_spaces={space.nameString()})
    assert ok and model.getShadingSurfaces()
    _, _c, ok = run(
        cat,
        "Make Selected Surfaces Adiabatic and Assign a Construction",
        model=model,
        selected_spaces={space.nameString()},
    )
    assert ok and any(s.outsideBoundaryCondition() == "Adiabatic" for s in model.getSurfaces())
    _, _c, ok = run(cat, "Remove Hard Assigned Constructions", model=model)
    assert ok


def test_cleanup_move_and_intersect():
    cat = "Alter or Add Model Elements"
    model = shape_model()
    sp = model.getSpaces()[0]
    _, c, ok = run(cat, "Cleanup Origins", model=model, selected_spaces={sp.nameString()})
    assert ok, c.messages
    surf = next(s for s in model.getSurfaces() if s.surfaceType() == "Wall")
    n = len(model.getSpaces())
    _, c, ok = run(cat, "Move Selected Surfaces to New Space", model=model, selected_names={surf.nameString()})
    assert ok, c.messages
    assert len(model.getSpaces()) == n + 1
    _, c, ok = run(cat, "Intersect Space Geometry", model=model)
    assert ok, c.messages


def test_remove_loads_orphans_and_partition_height():
    cat = "Alter or Add Model Elements"
    model = shape_model()
    assert run(cat, "Remove Loads Directly Assigned to Spaces", model=model)[2]
    assert run(cat, "Remove Orphan SubSurfaces", model=model)[2]
    assert run(cat, "Set Interior Partition Height Above Floor", {"height": 2.0}, model=model)[2]


def test_space_type_report(tmp_path):
    model = shape_model()
    out = tmp_path / "r.csv"
    _, c, ok = run("Reports", "Space Type Report", {"save_path": str(out)}, model=model)
    assert ok, c.messages
    text = out.read_text(encoding="utf-8")
    assert "Story 1 Core Space" in text and "<no space type>" in text
    assert text.startswith("Building, Space Type")


def test_selection_helpers():
    assert sc.expand_selection(["Wall 1", "Wall 2", "Wallet 1", "Floor"], {"Wall"}) == {"Wall", "Wall 1", "Wall 2"}
    space_of = {"a": "S1", "b": "S1", "c": "S2"}
    assert sc.fully_selected_spaces(space_of, {"a", "b"}) == {"S1"}
    assert sc.fully_selected_spaces(space_of, {"a", "c"}) == {"S2"}


# --- part 2: remaining ports ---------------------------------------------------------------

ALT = "Alter or Add Model Elements"


def zoned_model():
    model = shape_model()
    run(ALT, "Add New Thermal Zone For Spaces With No Thermal Zone", model=model)
    return model


def sel_space(model, i=0):
    return {model.getSpaces()[i].nameString()}


def test_all_scripts_registered():
    assert len(sc.categories()) == 6
    assert sum(len(sc.scripts(c)) for c in sc.categories()) == 39  # every original user script


def test_visualization_scripts():
    V = "Visualization"
    model = zoned_model()
    spaces = {s.nameString() for s in model.getSpaces()}
    zone = model.getThermalZones()[0]
    _, c, ok = run(V, "Find Spaces By Thermal Zone", {"thermal_zone_name": zone.nameString()}, model=model)
    assert ok and c.show_spaces == {zone.spaces()[0].nameString()} and c.show_spaces | c.hide_spaces == spaces
    _, c, ok = run(V, "Find Spaces By Space Type", {"space_type_name": ""}, model=model)
    assert not ok  # no space types
    _, c, ok = run(V, "Hide Spaces Included In Building Area", model=model)
    assert ok and c.hide_spaces == spaces  # all spaces count towards the building area
    _, c, ok = run(V, "Hide Manifold Solid Spaces", model=model)
    assert ok and c.hide_spaces == spaces  # box spaces made by fromFloorPrint are closed solids
    _, c, ok = run(V, "Make View Layer For Each Building Story Floorplan", model=model)
    assert ok and set(c.view_layers) == {"OS - All Stories", "OS - Story 1"}
    assert c.view_layers["OS - Story 1"] == spaces


def test_manifold_detects_open_space():
    from zonal.user_scripts.visualization.viewport import is_manifold

    model = shape_model()
    sp = model.getSpaces()[0]
    assert is_manifold(sp)
    next(s for s in sp.surfaces() if s.surfaceType() == "Floor").remove()
    assert not is_manifold(sp)


def test_photovoltaics_roundtrip():
    model = shape_model()
    roof = next(s for s in model.getSurfaces() if s.surfaceType() == "RoofCeiling")
    sel = {roof.nameString()}
    _, c, ok = run(ALT, "Add Photovoltaics", {"efficiency": 0.15}, model=model, selected_names=sel)
    assert ok, c.messages
    assert len(model.getGeneratorPhotovoltaics()) == 1 and len(model.getElectricLoadCenterDistributions()) == 1
    _, c, ok = run(ALT, "Remove Photovoltaics", model=model, selected_names=sel)
    assert ok and not model.getGeneratorPhotovoltaics() and not model.getElectricLoadCenterDistributions()
    run(ALT, "Add Photovoltaics", model=model, selected_names=sel)
    for pv in model.getGeneratorPhotovoltaics():
        pv.resetSurface()
    _, c, ok = run(ALT, "Remove Orphan Photovoltaics", model=model)
    assert ok and not model.getGeneratorPhotovoltaics() and not model.getInverters()


def test_shading_controls_and_frame_divider():
    model = shape_model()
    wall = next(s for s in model.getSurfaces() if s.surfaceType() == "Wall")
    run(ALT, "Set Window to Wall Ratio", model=model, selected_names={wall.nameString()})
    win = model.getSubSurfaces()[0]
    _, c, ok = run(ALT, "Add Shading Controls", {"material": "__shade__"}, model=model)
    assert ok, c.messages
    assert len(model.getShadingControls()) == 1
    name = model.getShadingControls()[0].nameString()
    _, c, ok = run(ALT, "Set Shading Controls", {"name": name}, model=model, selected_names={win.nameString()})
    assert ok, c.messages
    assert win.shadingControl().is_initialized() or win.shadingControls()
    _, c, ok = run(
        ALT, "Set Shading Controls", {"name": "<No Shading Control>"}, model=model, selected_names={win.nameString()}
    )
    assert ok
    _, c, ok = run(ALT, "Add Shading Controls", model=model)
    assert not ok  # nothing chosen
    fd = openstudio.model.WindowPropertyFrameAndDivider(model)
    _, c, ok = run(
        ALT,
        "Set Window Property Frame and Divider",
        {"name": fd.nameString()},
        model=model,
        selected_names={win.nameString()},
    )
    assert ok and win.windowPropertyFrameAndDivider().is_initialized()


def test_export_and_merge_spaces(tmp_path):
    model = shape_model()
    out = tmp_path / "ex.osm"
    sp = model.getSpaces()[0]
    _, c, ok = run(
        ALT,
        "Export Selected Spaces to a new External Model",
        {"save_path": str(out)},
        model=model,
        selected_spaces={sp.nameString()},
    )
    assert ok and out.is_file()
    target = shape_model()
    n = len(target.getSpaces())
    _, c, ok = run(ALT, "Merge Spaces From External File", {"import_path": str(out)}, model=target)
    assert ok, c.messages
    assert len(target.getSpaces()) == n  # same name: replaced
    assert run(ALT, "Merge Spaces From External File", {"import_path": ""}, model=target)[1].has_errors


def test_rpx_and_diagnostic(tmp_path):
    model = zoned_model()
    zone = model.getThermalZones()[0].nameString()
    out = tmp_path / "z.rpx"
    _, c, ok = run("Reports", "Export RPX File", {"thermal_zone": zone, "save_path": str(out)}, model=model)
    assert ok, c.messages
    text = out.read_text(encoding="utf-8")
    assert text.startswith(f"ROOM, {zone}, SI") and "\nS, " in text
    _, c, ok = run("Reports", "Export RPX File", {"thermal_zone": "", "save_path": str(out)}, model=model)
    assert not ok
    # diagnostic: a sliver surface is removed, only with the matching option
    surf = model.getSurfaces()[0]
    vec = openstudio.Point3dVector()
    for x, y in ((0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)):
        vec.append(openstudio.Point3d(x, y, 0))
    surf.setVertices(vec)
    n = len(model.getSurfaces())
    _, c, ok = run("Reports", "OSM Diagnostic Script", {"remove_warnings": False}, model=model)
    assert ok and len(model.getSurfaces()) == n and any("area less" in m.text for m in c.messages)
    _, c, ok = run("Reports", "OSM Diagnostic Script", {"remove_warnings": True}, model=model)
    assert ok and len(model.getSurfaces()) == n - 1


def test_wizard_creates_space_types_and_construction_set():
    model = openstudio.model.Model()
    _, c, ok = run("On Demand Template Generators", "Space Type and Construction Set Wizard", model=model)
    assert ok, c.messages
    assert len(model.getSpaceTypes()) > 3
    assert len(model.getDefaultConstructionSets()) == 1
    assert model.getConstructions() and model.getBuilding().spaceType().is_initialized()
    assert c.building_defaults["spaceType"].startswith("189.1-2009 - Office")
    assert model.getBuilding().defaultConstructionSet().is_initialized()
    st = next(
        s
        for s in model.getSpaceTypes()
        if s.nameString().endswith("OpenOffice - CZ4-8") or "OpenOffice" in s.nameString()
    )
    assert st.lights() and st.defaultScheduleSet().is_initialized()


def test_bcl_requires_city():
    _, c, ok = run("Building Component Library", "Get BCL Weather File", {"city": " "})
    assert not ok and "city" in c.messages[0].text
