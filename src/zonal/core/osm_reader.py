"""Read an OpenStudio model (.osm) into plain dicts that the Blender importer turns into meshes.

Needs the `openstudio` python bindings (see `openstudio_env`) but no bpy. The result is JSON-serialisable:

    {"northAxis": float, "warnings": [...],
     "objects": [{"name", "kind", "space", "polygon": [[x, y, z], ...], "props": {extras key: value}}]}

`kind` is one of surface / subsurface / shading / partition; `props` uses the same keys as the export side
(`schema.NODE_KEYS`) so an imported scene exports back to an equivalent model. Polygons are in building
coordinates (space origin / relative north applied), in metres.
"""

from __future__ import annotations


def _opt(o):
    """OpenStudio Optional* -> value or None."""
    return o.get() if o is not None and o.is_initialized() else None


def _name(o) -> str:
    o = _opt(o)
    return o.nameString() if o is not None else ""


def _poly(tr, vertices) -> list[list[float]]:
    return [[p.x(), p.y(), p.z()] for p in (tr * vertices if tr is not None else vertices)]


def _put(props: dict, key: str, value):
    if value not in ("", None):
        props[key] = value


def _construction_name(obj) -> str:
    try:
        return _name(obj.construction())
    except Exception:
        return ""


def space_props(sp) -> dict:
    props: dict = {"spaceName": sp.nameString()}
    _put(props, "thermalZoneName", _name(sp.thermalZone()))
    zone = _opt(sp.thermalZone())
    if zone is not None:  # air loops serve the zone; the Blender side stores them on the zone
        loops = [a.nameString() for a in zone.airLoopHVACs()]
        if loops:
            props["airLoopHVACNames"] = {str(i): n for i, n in enumerate(loops)}
    _put(props, "spaceTypeName", _name(sp.spaceType()))
    _put(props, "buildingStoryName", _name(sp.buildingStory()))
    _put(props, "buildingUnitName", _name(sp.buildingUnit()))
    _put(props, "constructionSetName", _name(sp.defaultConstructionSet()))
    return props


_MATERIAL_KINDS = ("Blind", "Shade", "Screen", "DaylightRedirectionDevice")


def shading_control_def(c) -> dict:
    """The OpenStudio ShadingControl as plain data (rebuilt by `osm_builder`): type, control, setpoints, material."""
    d: dict = {
        "shadingType": c.shadingType(),
        "shadingControlType": c.shadingControlType(),
        "glareControlIsActive": bool(c.glareControlIsActive()),
        "multipleSurfaceControlType": c.multipleSurfaceControlType(),
    }
    for key, getter in (("setpoint", c.setpoint), ("setpoint2", c.setpoint2)):
        value = _opt(getter()) if hasattr(getter(), "is_initialized") else getter()
        if value is not None:
            d[key] = float(value)
    slat = c.typeofSlatAngleControlforBlinds()
    if slat:
        d["slatAngleControl"] = slat
    mat = _opt(c.shadingMaterial())
    if mat is not None:
        kind = next((k for k in _MATERIAL_KINDS if getattr(mat, f"to_{k}")().is_initialized()), "")
        if kind:
            d["material"] = {"kind": kind, "name": mat.nameString()}
    cons = _opt(c.construction())
    if cons is not None:
        d["constructionName"] = cons.nameString()
    return d


def read_osm(path) -> dict:
    import openstudio

    loaded = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(path)))
    if not loaded.is_initialized():
        raise RuntimeError(f"could not load '{path}' as an OpenStudio model")
    return read_model(loaded.get())


def read_gbxml(path) -> dict:
    """gbXML -> same dict as `read_osm`, through OpenStudio's gbXML reverse translator."""
    import openstudio

    rt = openstudio.gbxml.GbXMLReverseTranslator()
    loaded = rt.loadModel(openstudio.path(str(path)))
    if not loaded.is_initialized():
        errs = "; ".join(e.logMessage() for e in rt.errors())
        raise RuntimeError(f"could not load '{path}' as gbXML" + (f": {errs}" if errs else ""))
    out = read_model(loaded.get())
    out["warnings"] = [w.logMessage() for w in rt.warnings()] + out["warnings"]
    return out


def read_idf(path) -> dict:
    """EnergyPlus .idf -> same dict as `read_osm`, through OpenStudio's EnergyPlus reverse translator."""
    import openstudio

    rt = openstudio.energyplus.ReverseTranslator()
    loaded = rt.loadModel(openstudio.path(str(path)))
    if not loaded.is_initialized():
        errs = "; ".join(e.logMessage() for e in rt.errors())
        raise RuntimeError(f"could not load '{path}' as an IDF" + (f": {errs}" if errs else ""))
    out = read_model(loaded.get())
    out["warnings"] = [w.logMessage() for w in rt.warnings()] + out["warnings"]
    return out


def read_sdd(path) -> dict:
    """SDD (California Title 24 Simulation Data Dictionary) -> same dict as `read_osm`, via OpenStudio's SDD translator."""
    import openstudio

    rt = openstudio.sdd.SddReverseTranslator()
    loaded = rt.loadModel(openstudio.path(str(path)))
    if not loaded.is_initialized():
        errs = "; ".join(e.logMessage() for e in rt.errors())
        raise RuntimeError(f"could not load '{path}' as SDD" + (f": {errs}" if errs else ""))
    out = read_model(loaded.get())
    out["warnings"] = [w.logMessage() for w in rt.warnings()] + out["warnings"]
    return out


def idf_to_osm_file(idf_path, osm_path) -> list[str]:
    """Translate an EnergyPlus .idf to an .osm file (e.g. to use its constructions / schedules as a template).

    Returns the translator warnings; raises RuntimeError when the file cannot be read or written.
    """
    import openstudio

    rt = openstudio.energyplus.ReverseTranslator()
    loaded = rt.loadModel(openstudio.path(str(idf_path)))
    if not loaded.is_initialized():
        errs = "; ".join(e.logMessage() for e in rt.errors())
        raise RuntimeError(f"could not load '{idf_path}' as an IDF" + (f": {errs}" if errs else ""))
    if not loaded.get().save(openstudio.path(str(osm_path)), True):
        raise RuntimeError(f"could not write '{osm_path}'")
    return [w.logMessage() for w in rt.warnings()]


def read_model(model) -> dict:
    out: dict = {"northAxis": model.getBuilding().northAxis(), "warnings": [], "objects": [], "shadingControls": {}}
    objects = out["objects"]

    for sp in model.getSpaces():
        tr = sp.transformation()
        base = space_props(sp)
        for s in sp.surfaces():
            props = dict(base)
            props["surfaceType"] = s.surfaceType()
            _put(props, "constructionName", _construction_name(s))
            props["outsideBoundaryCondition"] = s.outsideBoundaryCondition()
            _put(props, "outsideBoundaryConditionObjectName", _name(s.adjacentSurface()))
            props["sunExposure"] = s.sunExposure()
            props["windExposure"] = s.windExposure()
            objects.append(
                {
                    "name": s.nameString(),
                    "kind": "surface",
                    "space": sp.nameString(),
                    "polygon": _poly(tr, s.vertices()),
                    "props": props,
                }
            )
            for ss in s.subSurfaces():
                sprops = dict(base)
                sprops["surfaceType"] = ss.subSurfaceType()
                sprops["surfaceName"] = s.nameString()
                sprops["subSurfaceName"] = ss.nameString()
                _put(sprops, "constructionName", _construction_name(ss))
                _put(sprops, "outsideBoundaryConditionObjectName", _name(ss.adjacentSubSurface()))
                controls = list(ss.shadingControls())
                if controls:
                    sprops["shadingControlName"] = controls[0].nameString()
                    out["shadingControls"].setdefault(controls[0].nameString(), shading_control_def(controls[0]))
                    if len(controls) > 1:
                        out["warnings"].append(
                            f"{ss.nameString()}: only the first of {len(controls)} shading controls is kept"
                        )
                objects.append(
                    {
                        "name": ss.nameString(),
                        "kind": "subsurface",
                        "space": sp.nameString(),
                        "polygon": _poly(tr, ss.vertices()),
                        "props": sprops,
                    }
                )
        for g in sp.interiorPartitionSurfaceGroups():
            gtr = g.buildingTransformation()
            for p in g.interiorPartitionSurfaces():
                pprops = dict(base)
                pprops["surfaceType"] = "InteriorPartitionSurface"
                _put(pprops, "constructionName", _construction_name(p))
                objects.append(
                    {
                        "name": p.nameString(),
                        "kind": "partition",
                        "space": sp.nameString(),
                        "polygon": _poly(gtr, p.vertices()),
                        "props": pprops,
                    }
                )

    for c in model.getShadingControls():  # including controls not assigned to any sub surface
        out["shadingControls"].setdefault(c.nameString(), shading_control_def(c))

    for g in model.getShadingSurfaceGroups():
        gtype = g.shadingSurfaceType()  # Site / Building / Space
        space = _opt(g.space())
        # building coordinates: a space group includes its space's placement, site/building groups only their own
        gtr = g.buildingTransformation() if space is not None else g.transformation()
        for sh in g.shadingSurfaces():
            props = {"surfaceType": f"{gtype}Shading", "shadingName": sh.nameString()}
            if space is not None:
                props["spaceName"] = space.nameString()
            _put(props, "constructionName", _construction_name(sh))
            objects.append(
                {
                    "name": sh.nameString(),
                    "kind": "shading",
                    "space": space.nameString() if space else "",
                    "polygon": _poly(gtr, sh.vertices()),
                    "props": props,
                }
            )
    return out
