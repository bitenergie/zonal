"""Reports, part 2: Export RPX File and OSM Diagnostic Script (ports of the Ruby originals)."""

from __future__ import annotations

from ...core.scripts_core import ArgDef, UserScript, register
from ..alter.model_edits import _opt

CATEGORY = "Reports"


def _xyz(vertices) -> str:
    return ", ".join(f"{c}" for v in vertices for c in (v.x(), v.y(), v.z()))


def rpx_text(model, zone) -> str:
    """RPX (Radiance Preview / Radiance room) text of one thermal zone."""
    site = model.getSite()
    out = [
        f"ROOM, {zone.nameString()}, SI",
        f"SITE, {site.latitude()}, {site.longitude()}, {site.timeZone()}, {site.elevation()}",
    ]
    for space in zone.spaces():
        for surface in space.surfaces():
            v = surface.vertices()
            out.append(f"S, {surface.nameString()}, 0.9, {len(v)}, {_xyz(v)}")
            for sub in surface.subSurfaces():
                window = sub.subSurfaceType() in ("FixedWindow", "OperableWindow", "GlassDoor", "Skylight")
                v = sub.vertices()
                out.append(
                    f"{'W' if window else 'P'}, {sub.nameString()}, {0.84 if window else 0.9}, {len(v)}, {_xyz(v)}"
                )
    return "\n".join(out) + "\n"


@register
class ExportRpx(UserScript):
    category = CATEGORY
    name = "Export RPX File"
    description = "Write the geometry of one thermal zone as an RPX file."
    modifies_model = False
    model_dependent = True

    def arguments(self, model):
        zones = sorted(z.nameString() for z in model.getThermalZones()) if model else []
        return [
            ArgDef(
                "thermal_zone",
                "Thermal Zone to Export",
                "choice",
                zones[0] if zones else "",
                choices=tuple((z, z) for z in zones),
            ),
            ArgDef("save_path", "Output Path", "path", "//zone.rpx", ext="rpx"),
        ]

    def run(self, model, a, ctx):
        zone = _opt(model.getThermalZoneByName(a["thermal_zone"])) if a["thermal_zone"] else None
        if zone is None:
            ctx.error(
                "No thermal zone was chosen."
                if not a["thermal_zone"]
                else f"The thermal zone '{a['thermal_zone']}' was not found in the model."
            )
            return False
        if not a["save_path"]:
            ctx.error("Choose an output file.")
            return False
        with open(a["save_path"], "w", encoding="utf-8", newline="") as f:
            f.write(rpx_text(model, zone))
        ctx.info(f"Wrote {a['save_path']}")
        return True


def _short(names, n=4) -> str:
    return ", ".join(names[:n]) + (f", ... (+{len(names) - n})" if len(names) > n else "")


@register
class OsmDiagnostic(UserScript):
    category = CATEGORY
    name = "OSM Diagnostic Script"
    description = (
        "Look for invalid objects, degenerate or tiny surfaces, duplicate geometry and duplicate vertices; "
        "optionally remove the offenders from the model."
    )

    def arguments(self, model):
        return [
            ArgDef("remove_errors", "Remove Objects with Errors?", "bool", True),
            ArgDef("remove_warnings", "Remove Objects with Warnings?", "bool", False),
        ]

    def run(self, model, a, ctx):
        import openstudio

        rm_err, rm_warn = a["remove_errors"], a["remove_warnings"]
        ctx.info(
            f"Model has {model.numObjects()} objects: {len(model.getThermalZones())} thermal zones, "
            f"{len(model.getSpaces())} spaces, {len(model.getSurfaces())} base surfaces, "
            f"{len(model.getSubSurfaces())} sub surfaces, {len(model.getShadingSurfaces())} shading surfaces, "
            f"{len(model.getInteriorPartitionSurfaces())} interior partition surfaces."
        )

        def report(level, title, items, remove):
            if not items:
                return
            names = [i.nameString() for i in items]
            (ctx.warning if level == "warning" else ctx.info)(
                f"{title}: {_short(names)}" + (" - removed" if remove else "")
            )
            if remove:
                for o in items:
                    if model.getModelObject(o.handle()).is_initialized():  # an earlier removal may have taken it
                        o.remove()

        # objects failing the draft validity test
        draft = openstudio.StrictnessLevel("Draft")
        try:
            invalid = [o for o in model.modelObjects() if not o.isValid(draft)]
        except Exception:
            invalid = []
        report("warning", "Fail the draft validity test", invalid, rm_err)

        report(
            "warning",
            "Surfaces with less than three vertices",
            [s for s in model.getSurfaces() if len(s.vertices()) < 3],
            rm_err,
        )
        report(
            "warning",
            "Sub surfaces with less than three vertices",
            [s for s in model.getSubSurfaces() if len(s.vertices()) < 3],
            rm_err,
        )
        report(
            "info",
            "Surfaces with more than 25 vertices",
            [s for s in model.getSurfaces() if len(s.vertices()) > 25],
            False,
        )
        report(
            "warning",
            "Surfaces with area less than 0.1 m^2",
            [s for s in model.getSurfaces() if s.grossArea() < 0.1],
            rm_warn,
        )
        report(
            "warning",
            "Sub surfaces with area less than 0.1 m^2",
            [s for s in model.getSubSurfaces() if s.grossArea() < 0.1],
            rm_warn,
        )

        # same geometry twice in one surface group (sub surfaces first, so they are removed before base surfaces)
        similar = {}
        for group in model.getPlanarSurfaceGroups():
            planar = [c.to_PlanarSurface().get() for c in group.children() if c.to_PlanarSurface().is_initialized()]
            subs = [
                sub
                for p in planar
                for sub in (p.to_Surface().get().subSurfaces() if p.to_Surface().is_initialized() else [])
            ]
            allp = subs + planar
            for i, p1 in enumerate(allp):
                if any(p1.equalVertices(p2) or p1.reverseEqualVertices(p2) for p2 in allp[i + 1 :]):
                    similar[p1.handle().__str__()] = p1
        report("warning", "Surfaces with similar geometry in the same group", list(similar.values()), rm_err)

        dup = []
        for s in list(model.getSurfaces()) + list(model.getSubSurfaces()):
            seen = set()
            for v in s.vertices():
                key = (v.x(), v.y(), v.z())
                if key in seen:
                    dup.append(s)
                    break
                seen.add(key)
        report("warning", "Surfaces with duplicate vertices", dup, rm_err)
        if not ctx.messages[1:]:
            ctx.info("No problems found.")
        return True
