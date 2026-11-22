import openstudio

from zonal.core import scripts_core as sc
from zonal.user_scripts.reports import more_reports as mr
from zonal.user_scripts.visualization import viewport as vp

V = "Visualization"
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


def test_manifold_helpers():
    m = shape()
    sp = m.getSpaces()[0]
    assert vp.is_manifold(sp)
    assert not vp.is_manifold(openstudio.model.Space(m))
    wall = next(s for s in sp.surfaces() if s.surfaceType() == "Wall")
    wall.remove()
    assert not vp.is_manifold(sp)


def test_find_by_space_type_and_zone():
    m = shape()
    st = openstudio.model.SpaceType(m)
    st.setName("Lab")
    m.getSpaces()[0].setSpaceType(st)
    script = sc.get(V, "Find Spaces By Space Type")
    assert script.arguments(m)[0].default == "Lab"
    _, c, ok = run(V, script.name, {"space_type_name": "Lab"}, model=m)
    assert ok and len(c.show_spaces) == 1
    assert not run(V, script.name, {"space_type_name": ""}, model=openstudio.model.Model())[2]
    zs = sc.get(V, "Find Spaces By Thermal Zone")
    assert zs.arguments(None)[0].choices == ()
    assert not run(V, zs.name, {"thermal_zone_name": ""}, model=m)[2]
    z = openstudio.model.ThermalZone(m)
    m.getSpaces()[0].setThermalZone(z)
    assert run(V, zs.name, {"thermal_zone_name": z.nameString()}, model=m)[2]
    m.getSpaces()[1].setPartofTotalFloorArea(False)
    _, c, ok = run(V, "Hide Spaces Included In Building Area", model=m)
    assert len(c.show_spaces) == 1


def test_story_layers_edge_cases():
    _, c, ok = run(V, "Make View Layer For Each Building Story Floorplan", model=openstudio.model.Model())
    assert not c.applicable
    m = shape()
    for x in m.getSpaces():
        x.resetBuildingStory()
    st = openstudio.model.BuildingStory(m)  # without nominal z
    m.getSpaces()[0].setBuildingStory(st)
    _, c, _ok = run(V, "Make View Layer For Each Building Story Floorplan", model=m)
    assert "OS - <no story>" in c.view_layers


def test_rpx(tmp_path):
    m = shape()
    z = openstudio.model.ThermalZone(m)
    sp = m.getSpaces()[0]
    sp.setThermalZone(z)
    wall = next(s for s in sp.surfaces() if s.surfaceType() == "Wall")
    sub = openstudio.model.SubSurface(pv((1, 0, 1), (2, 0, 1), (2, 0, 2)), m)
    sub.setSurface(wall)
    sub.setSubSurfaceType("Door")
    out = tmp_path / "z.rpx"
    script = sc.get("Reports", "Export RPX File")
    assert script.arguments(m)[0].default == z.nameString()
    assert script.arguments(None)[0].default == ""
    _, _c, ok = run("Reports", script.name, {"thermal_zone": z.nameString(), "save_path": str(out)}, model=m)
    assert ok and out.read_text().startswith("ROOM")
    assert not run("Reports", script.name, {"thermal_zone": "", "save_path": str(out)}, model=m)[2]
    assert not run("Reports", script.name, {"thermal_zone": "zz", "save_path": str(out)}, model=m)[2]
    assert not run("Reports", script.name, {"thermal_zone": z.nameString(), "save_path": ""}, model=m)[2]


def test_diagnostic_branches():
    m = openstudio.model.Model()
    z = openstudio.model.ThermalZone(m)
    sp = openstudio.model.Space(m)
    sp.setThermalZone(z)
    s1 = openstudio.model.Surface(pv((0, 0, 0), (1, 0, 0), (1, 1, 0)), m)
    s1.setSpace(sp)
    s1.setName("tiny")
    s2 = openstudio.model.Surface(pv((0, 0, 0), (1, 0, 0), (1, 1, 0)), m)
    s2.setSpace(sp)
    s2.setName("same")
    big = [(i, (i % 2) * 0.5 - 100, 0) for i in range(30)]
    big = [(float(i), float(i * i), 0.0) for i in range(30)]
    s3 = openstudio.model.Surface(pv(*big), m)
    s3.setSpace(sp)
    s3.setName("manyverts")
    s4 = openstudio.model.Surface(pv((10, 10, 0), (12, 10, 0), (12, 12, 0), (10, 12, 0), (10, 10, 0)), m)
    s4.setSpace(sp)
    openstudio.model.SubSurface(pv((0, 0, 0), (0.1, 0, 0), (0.1, 0.1, 0)), m)
    _, c, ok = run("Reports", "OSM Diagnostic Script", {"remove_errors": False}, model=m)
    assert ok
    text = " ".join(x.text for x in c.messages)
    assert "area less" in text and "similar geometry" in text and "more than 25" in text
    _, c, ok = run("Reports", "OSM Diagnostic Script", {"remove_errors": True, "remove_warnings": True}, model=m)
    assert ok
    _, c, ok = run("Reports", "OSM Diagnostic Script", model=openstudio.model.Model())
    assert any("No problems" in x.text for x in c.messages)
    assert mr._short(["a"] * 6).endswith("(+2)")


def test_space_type_report_needs_path():
    assert not run("Reports", "Space Type Report", {"save_path": ""}, model=shape())[2]
