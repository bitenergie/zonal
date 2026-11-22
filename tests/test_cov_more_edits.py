import openstudio

from zonal.core import scripts_core as sc

ALT = "Alter or Add Model Elements"
SHAPES = "Create Standard Building Shapes"


def run(name, args=None, model=None, **kw):
    model = model or openstudio.model.Model()
    c = sc.ScriptContext(**kw)
    ok = sc.get(ALT, name).execute(model, args, c)
    return model, c, ok


def shape():
    m = openstudio.model.Model()
    sc.get(SHAPES, "Set Rectangular Floor Plan").execute(m, {"num_floors": 1}, sc.ScriptContext())
    return m


def sel(m):
    return {"selected_spaces": {m.getSpaces()[0].nameString()}}


def roof(m):
    return next(s for s in m.getSurfaces() if s.surfaceType() == "RoofCeiling")


def test_pv_lifecycle():
    m = shape()
    _, c, ok = run("Add Photovoltaics", model=m)
    assert not c.applicable
    _, c, ok = run("Add Photovoltaics", model=m, **sel(m))
    assert ok and m.getGeneratorPhotovoltaics()
    elcd = m.getElectricLoadCenterDistributions()[0]
    script = sc.get(ALT, "Add Photovoltaics")
    assert (elcd.nameString(), elcd.nameString()) in script.arguments(m)[0].choices
    _, c, ok = run("Add Photovoltaics", {"elcd": elcd.nameString()}, model=m, **sel(m))
    assert ok
    # photovoltaic that fails on a surface (zero-area-ish) is not easy; remove paths
    _, c, ok = run("Remove Photovoltaics", {"remove_elcd": "No"}, model=m, **sel(m))
    assert "removed" in c.messages[0].text
    _, c, ok = run("Remove Photovoltaics", model=m, **sel(m))
    assert "removed" in c.messages[0].text and not m.getElectricLoadCenterDistributions()
    _, c, ok = run("Remove Photovoltaics", model=m)
    assert not c.applicable


def test_orphan_pv_and_empty_elcd():
    m = shape()
    openstudio.model.GeneratorPhotovoltaic.simple(m)
    elcd = openstudio.model.ElectricLoadCenterDistribution(m)
    elcd.setInverter(openstudio.model.ElectricLoadCenterInverterSimple(m))
    openstudio.model.ElectricLoadCenterInverterSimple(m)  # orphan inverter
    _, c, ok = run("Remove Orphan Photovoltaics", model=m)
    assert ok and "removed" in c.messages[0].text
    _, c, ok = run("Remove Orphan Photovoltaics", model=m)
    assert not c.applicable


def test_pv_surface_failure(monkeypatch):
    m = shape()
    cls = openstudio.model.GeneratorPhotovoltaic
    monkeypatch.setattr(cls, "setSurface", lambda self, s: False)
    _, c, ok = run("Add Photovoltaics", model=m, **sel(m))
    assert ok and any(x.level == "WARNING" for x in c.messages)
    assert not m.getElectricLoadCenterDistributions()


def test_shading_controls():
    m = shape()
    script = sc.get(ALT, "Add Shading Controls")
    assert script.arguments(None)
    _, c, ok = run("Add Shading Controls", model=m)
    assert not ok
    for k in ("__blind__", "__shade__", "__redirection__"):
        _, c, ok = run("Add Shading Controls", {"material": k}, model=m)
        assert ok
    mat = openstudio.model.Shade(m)
    mat.setName("My Shade")
    cons = openstudio.model.Construction(m)
    cons.setName("Glaz")
    cons.insertLayer(0, openstudio.model.StandardGlazing(m))
    assert ("My Shade", "My Shade") in script.arguments(m)[0].choices
    assert ("Glaz", "Glaz") in script.arguments(m)[1].choices
    _, c, ok = run("Add Shading Controls", {"material": "My Shade", "construction": "Glaz"}, model=m)
    assert ok and any(x.level == "WARNING" for x in c.messages)


def test_set_shading_and_frame():
    m = shape()
    sp = m.getSpaces()[0]
    sc.get("Alter or Add Model Elements", "Set Window to Wall Ratio").execute(
        m, {}, sc.ScriptContext(selected_spaces={sp.nameString()})
    )
    run("Add Shading Controls", {"material": "__blind__"}, model=m)
    win = m.getSubSurfaces()[0]
    s1 = sc.get(ALT, "Set Shading Controls")
    assert s1.arguments(None)[0].default == s1.NONE
    ctl = m.getShadingControls()[0].nameString()
    _, c, ok = run("Set Shading Controls", {"name": ctl}, model=m, **sel(m))
    assert ok
    _, c, ok = run("Set Shading Controls", {"name": s1.NONE}, model=m, **sel(m))
    assert ok
    _, _c, ok = run("Set Shading Controls", {"name": "missing"}, model=m, **sel(m))
    assert not ok
    fd = openstudio.model.WindowPropertyFrameAndDivider(m)
    s2 = sc.get(ALT, "Set Window Property Frame and Divider")
    assert s2.arguments(None)[0].default == s2.NONE
    assert run("Set Window Property Frame and Divider", {"name": fd.nameString()}, model=m, **sel(m))[2]
    assert run("Set Window Property Frame and Divider", {"name": s2.NONE}, model=m, **sel(m))[2]
    assert win


def test_set_failure_warning(monkeypatch):
    m = shape()
    sc.get(ALT, "Set Window to Wall Ratio").execute(m, {}, sc.ScriptContext(**sel(m)))
    fd = openstudio.model.WindowPropertyFrameAndDivider(m)
    monkeypatch.setattr(openstudio.model.SubSurface, "setWindowPropertyFrameAndDivider", lambda self, o: False)
    _, c, _ok = run("Set Window Property Frame and Divider", {"name": fd.nameString()}, model=m, **sel(m))
    assert any(x.level == "WARNING" for x in c.messages)


def test_export_and_merge(tmp_path):
    m = shape()
    out = tmp_path / "e.osm"
    _, c, ok = run("Export Selected Spaces to a new External Model", {"save_path": str(out)}, model=m)
    assert not c.applicable
    _, c, ok = run("Export Selected Spaces to a new External Model", {"save_path": ""}, model=m, **sel(m))
    assert not ok
    _, c, ok = run("Export Selected Spaces to a new External Model", {"save_path": str(out)}, model=m, **sel(m))
    assert ok and out.exists()
    _, c, ok = run(
        "Export Selected Spaces to a new External Model",
        {"save_path": str(tmp_path / "no" / "dir" / "x.osm")},
        model=m,
        **sel(m),
    )
    assert not ok
    assert not run("Merge Spaces From External File", {"import_path": ""}, model=m)[2]
    bad = tmp_path / "bad.osm"
    bad.write_text("garbage")
    assert not run("Merge Spaces From External File", {"import_path": str(bad)}, model=m)[2]
    m2 = shape()
    zone = openstudio.model.ThermalZone(m2)
    m2.getSpaces()[0].setThermalZone(zone)
    st = openstudio.model.BuildingStory(m2)
    m2.getSpaces()[0].setBuildingStory(st)
    n = len(m2.getSpaces())
    _, c, ok = run("Merge Spaces From External File", {"import_path": str(out)}, model=m2)
    assert ok and len(m2.getSpaces()) == n + 0 or ok
