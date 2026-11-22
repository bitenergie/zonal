"""Visualization: ports of reference/user_scripts/Visualization/*.rb.

The SketchUp originals toggled entity visibility. Here a script only decides which OpenStudio spaces to show or
hide (`ctx.show_spaces`, `ctx.hide_spaces`) or which view layers to make (`ctx.view_layers`); the Blender panel applies
that to the scene objects of those spaces. They need the scene as input (not an .osm file) and never change the model.
"""

from __future__ import annotations

from ...core.scripts_core import ArgDef, UserScript, register
from ..alter.model_edits import _opt

CATEGORY = "Visualization"


class _Viz(UserScript):
    category = CATEGORY
    viewport = True
    modifies_model = False


def is_manifold(space, tol: float = 1e-4) -> bool:
    """Closed solid: every edge of the space's surfaces is shared by exactly two surfaces."""
    surfaces = list(space.surfaces())
    if len(surfaces) < 4:
        return False

    def key(v):
        return (round(v.x() / tol), round(v.y() / tol), round(v.z() / tol))

    edges: dict = {}
    for s in surfaces:
        pts = [key(v) for v in s.vertices()]
        for p, q in zip(pts, pts[1:] + pts[:1], strict=True):
            if p != q:
                e = (p, q) if p < q else (q, p)
                edges[e] = edges.get(e, 0) + 1
    return bool(edges) and all(n == 2 for n in edges.values())


@register
class FindSpacesBySpaceType(_Viz):
    name = "Find Spaces By Space Type"
    description = "Show only the spaces of one space type."
    model_dependent = True

    def arguments(self, model):
        names = sorted(t.nameString() for t in model.getSpaceTypes()) if model else []
        return [
            ArgDef(
                "space_type_name",
                "Space Type",
                "choice",
                names[0] if names else "",
                choices=tuple((n, n) for n in names),
            )
        ]

    def run(self, model, a, ctx):
        if not a["space_type_name"]:
            ctx.error("The model has no space types.")
            return False
        for sp in model.getSpaces():
            t = _opt(sp.spaceType())
            shown = t is not None and t.nameString() == a["space_type_name"]
            (ctx.show_spaces if shown else ctx.hide_spaces).add(sp.nameString())
        ctx.info(f"{len(ctx.show_spaces)} spaces shown, {len(ctx.hide_spaces)} hidden.")
        return True


@register
class FindSpacesByThermalZone(_Viz):
    name = "Find Spaces By Thermal Zone"
    description = "Show only the spaces of one thermal zone."
    model_dependent = True

    def arguments(self, model):
        names = sorted(z.nameString() for z in model.getThermalZones()) if model else []
        return [
            ArgDef(
                "thermal_zone_name",
                "Thermal Zone",
                "choice",
                names[0] if names else "",
                choices=tuple((n, n) for n in names),
            )
        ]

    def run(self, model, a, ctx):
        if not a["thermal_zone_name"]:
            ctx.error("The model has no thermal zones.")
            return False
        for sp in model.getSpaces():
            z = _opt(sp.thermalZone())
            if z is None:
                continue  # the original leaves spaces without a zone as they are
            (ctx.show_spaces if z.nameString() == a["thermal_zone_name"] else ctx.hide_spaces).add(sp.nameString())
        ctx.info(f"{len(ctx.show_spaces)} spaces shown, {len(ctx.hide_spaces)} hidden.")
        return True


@register
class HideSpacesInBuildingArea(_Viz):
    name = "Hide Spaces Included In Building Area"
    description = "Hide the spaces that are part of the total floor area, show the others."

    def run(self, model, a, ctx):
        for sp in model.getSpaces():
            (ctx.hide_spaces if sp.partofTotalFloorArea() else ctx.show_spaces).add(sp.nameString())
        ctx.info(f"{len(ctx.hide_spaces)} spaces hidden, {len(ctx.show_spaces)} shown.")
        return True


@register
class HideManifoldSolidSpaces(_Viz):
    name = "Hide Manifold Solid Spaces"
    description = "Hide the spaces that are closed solids, leaving the ones with gaps or open geometry visible."

    def run(self, model, a, ctx):
        spaces = list(model.getSpaces())
        for i, sp in enumerate(spaces):
            (ctx.hide_spaces if is_manifold(sp) else ctx.show_spaces).add(sp.nameString())
            ctx.progress((i + 1) / len(spaces))
        ctx.info(f"{len(ctx.hide_spaces)} manifold spaces hidden, {len(ctx.show_spaces)} shown.")
        return True


@register
class StoryViewLayers(_Viz):
    name = "Make View Layer For Each Building Story Floorplan"
    description = (
        "Create a view layer 'OS - <story>' per building story (plus 'OS - All Stories' and "
        "'OS - <no story>') that shows only that story's spaces."
    )

    def run(self, model, a, ctx):
        spaces = list(model.getSpaces())
        if not spaces:
            ctx.not_applicable("The model has no spaces.")
            return True
        ctx.show_spaces.update(sp.nameString() for sp in spaces)
        ctx.view_layers["OS - All Stories"] = {sp.nameString() for sp in spaces}

        def z(story):
            v = _opt(story.nominalZCoordinate())
            return (v is None, v or 0.0)

        for story in sorted(model.getBuildingStorys(), key=z):
            ctx.view_layers[f"OS - {story.nameString()}"] = {sp.nameString() for sp in story.spaces()}
        loose = {sp.nameString() for sp in spaces if sp.buildingStory().empty()}
        if loose:
            ctx.view_layers["OS - <no story>"] = loose
        ctx.info(f"Made {len(ctx.view_layers)} view layers.")
        return True
