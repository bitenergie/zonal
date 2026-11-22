import openstudio
import pytest

from zonal.core import scripts_core as sc

ALT = "Alter or Add Model Elements"
SHAPES = "Create Standard Building Shapes"


def run(cat, name, args=None, model=None, **kw):
    model = model or openstudio.model.Model()
    c = sc.ScriptContext(**kw)
    ok = sc.get(cat, name).execute(model, args, c)
    return model, c, ok


def shape():
    return run(SHAPES, "Set Rectangular Floor Plan", {"num_floors": 2})[0]


def pv(*pts):
    v = openstudio.Point3dVector()
    for p in pts:
        v.append(openstudio.Point3d(*p))
    return v


def test_zones_and_rename_edge_cases():
    m = shape()
    run(ALT, "Add New Thermal Zone For Spaces With No Thermal Zone", model=m)
    _, c, ok = run(ALT, "Add New Thermal Zone For Spaces With No Thermal Zone", model=m)
    assert ok and not c.applicable
    openstudio.model.ThermalZone(m)  # zone without spaces
    _, c, ok = run(ALT, "Rename Thermal Zones Based On Space Names", model=m)
    assert any("did not have any spaces" in x.text for x in c.messages)
    _, c, ok = run(ALT, "Remove Unused ThermalZones", model=m)
    assert any("Removing" in x.text for x in c.messages)
    _, c, ok = run(ALT, "Remove Unused ThermalZones", model=m)
    assert any("No unused" in x.text for x in c.messages)


def test_overhangs_branches():
    m = shape()
    sp = m.getSpaces()[0]
    sel = {"selected_spaces": {sp.nameString()}}
    _, c, ok = run(ALT, "Add Overhangs by Projection Factor", model=m)
    assert not c.applicable
    run(ALT, "Set Window to Wall Ratio", model=m, **sel)
    sub = m.getSubSurfaces()[0]
    sub.setSubSurfaceType("Door")
    _, c, ok = run(ALT, "Add Overhangs by Projection Factor", model=m, **sel)
    assert ok
    sub.setSubSurfaceType("FixedWindow")
    g = openstudio.model.ShadingSurfaceGroup(m)
    g.setSpace(sp)
    _, c, ok = run(ALT, "Add Overhangs by Projection Factor", {"remove_existing_shading": True}, model=m, **sel)
    assert any("Removing" in x.text for x in c.messages)
    assert m.getShadingSurfaces()
    run(ALT, "Add Overhangs by Projection Factor", {"projection_factor": -5.0}, model=m, **sel)


def test_stories():
    m = shape()
    for sp in m.getSpaces():
        sp.resetBuildingStory()
    st = openstudio.model.BuildingStory(m)
    st.setNominalZCoordinate(0.0)
    run(ALT, "Assign Building Stories", model=m)
    assert all(not sp.buildingStory().empty() for sp in m.getSpaces())
    _, c, ok = run(ALT, "Assign Building Stories", model=m)
    assert not c.applicable
    m2 = openstudio.model.Model()
    openstudio.model.Space(m2)
    _, c, _ok = run(ALT, "Assign Building Stories", model=m2)
    assert not c.applicable


def test_cleanup_origins_groups():
    m = shape()
    sp = m.getSpaces()[0]
    g = openstudio.model.ShadingSurfaceGroup(m)
    g.setSpace(sp)
    sh = openstudio.model.ShadingSurface(pv((0, 0, 3), (1, 0, 3), (1, 1, 3)), m)
    sh.setShadingSurfaceGroup(g)
    pg = openstudio.model.InteriorPartitionSurfaceGroup(m)
    pg.setSpace(sp)
    sg = openstudio.model.ShadingSurfaceGroup(m)
    ssh = openstudio.model.ShadingSurface(pv((20, 20, 3), (21, 20, 3), (21, 21, 3)), m)
    ssh.setShadingSurfaceGroup(sg)
    sg2 = openstudio.model.InteriorPartitionSurfaceGroup(m)
    ip = openstudio.model.InteriorPartitionSurface(pv((20, 20, 0), (21, 20, 0), (21, 20, 1)), m)
    ip.setInteriorPartitionSurfaceGroup(sg2)
    _, c, ok = run(
        ALT,
        "Cleanup Origins",
        model=m,
        selected_spaces={sp.nameString()},
        selected_names={ssh.nameString(), ip.nameString()},
    )
    assert ok, [x.text for x in c.messages]
    assert c.applicable
    _, c, ok = run(ALT, "Cleanup Origins", model=shape())
    assert not c.applicable
    empty = openstudio.model.ShadingSurfaceGroup(m)
    sc.get(ALT, "Cleanup Origins")._cleanup(empty)


def test_adiabatic():
    m = shape()
    sp = m.getSpaces()[0]
    name = "Make Selected Surfaces Adiabatic and Assign a Construction"
    _, c, ok = run(ALT, name, model=m)
    assert not c.applicable
    cons = openstudio.model.Construction(m)
    cons.setName("Wall C")
    script = sc.get(ALT, name)
    assert ("Wall C", "Wall C") in script.arguments(m)[0].choices
    _, c, ok = run(ALT, name, {"construction_name": "Wall C"}, model=m, selected_spaces={sp.nameString()})
    assert ok
    _, c, ok = run(ALT, name, {"construction_name": "Nope"}, model=m, selected_spaces={sp.nameString()})
    assert not ok
    assert script.arguments(None)[0].choices == (("", "<none>"),)


def test_orphan_subsurfaces_and_removals():
    m = shape()
    openstudio.model.SubSurface(pv((0, 0, 0), (1, 0, 0), (1, 0, 1)), m)
    _, c, ok = run(ALT, "Remove Orphan SubSurfaces", model=m)
    assert any("Removing 1" in x.text for x in c.messages)
    _, c, ok = run(ALT, "Remove Orphan SubSurfaces", model=m)
    assert any("No orphan" in x.text for x in c.messages)
    sp = m.getSpaces()[0]
    openstudio.model.People(openstudio.model.PeopleDefinition(m)).setSpace(sp)
    _, c, _ok = run(ALT, "Remove Loads Directly Assigned to Spaces", model=m)
    assert "Removed 1" in c.messages[0].text


def test_partition_height():
    m = shape()
    sp = m.getSpaces()[0]
    pg = openstudio.model.InteriorPartitionSurfaceGroup(m)
    pg.setSpace(sp)
    ip = openstudio.model.InteriorPartitionSurface(pv((1, 1, 0), (3, 1, 0), (3, 1, 3), (1, 1, 3)), m)
    ip.setInteriorPartitionSurfaceGroup(pg)
    assert run(ALT, "Set Interior Partition Height Above Floor", {"height": 1.0}, model=m)[2]
    assert max(v.z() for v in ip.vertices()) == pytest.approx(1.0)


def test_wwr_errors_and_below_ceiling():
    m = shape()
    sp = m.getSpaces()[0]
    sel = {"selected_spaces": {sp.nameString()}}
    assert not run(ALT, "Set Window to Wall Ratio", {"wwr": 1.5}, model=m, **sel)[2]
    assert not run(ALT, "Set Window to Wall Ratio", {"offset": 0.0}, model=m, **sel)[2]
    _, c, ok = run(ALT, "Set Window to Wall Ratio", {"application_type": "Below Ceiling"}, model=m, **sel)
    assert ok and m.getSubSurfaces()
    _, c, ok = run(ALT, "Set Window to Wall Ratio", {"offset": 100.0}, model=m, **sel)
    assert any(x.level == "WARNING" for x in c.messages)


def test_change_shading_type():
    m = shape()
    g = openstudio.model.ShadingSurfaceGroup(m)
    sh = openstudio.model.ShadingSurface(pv((20, 20, 3), (21, 20, 3), (21, 21, 3)), m)
    sh.setShadingSurfaceGroup(g)
    gs = openstudio.model.ShadingSurfaceGroup(m)
    gs.setSpace(m.getSpaces()[0])
    sh2 = openstudio.model.ShadingSurface(pv((0, 0, 3), (1, 0, 3), (1, 1, 3)), m)
    sh2.setShadingSurfaceGroup(gs)
    _, c, ok = run(ALT, "Change Shading Type", model=m)
    assert not c.applicable
    _, c, ok = run(
        ALT,
        "Change Shading Type",
        {"shading_type": "Building"},
        model=m,
        selected_names={sh.nameString(), sh2.nameString()},
    )
    assert ok and g.shadingSurfaceType() == "Building"


def test_move_surfaces_and_intersect_empty():
    m = shape()
    _, c, ok = run(ALT, "Move Selected Surfaces to New Space", model=m)
    assert not c.applicable
    wall = next(s for s in m.getSurfaces() if s.surfaceType() == "Wall")
    run(ALT, "Move Selected Surfaces to New Space", model=m, selected_names={wall.nameString()})
    _, c, _ok = run(ALT, "Intersect Space Geometry", model=openstudio.model.Model())
    assert not c.applicable
    run(ALT, "Intersect Space Geometry", model=shape())
