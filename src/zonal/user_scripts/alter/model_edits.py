"""Alter or Add Model Elements: ports of reference/user_scripts/Alter or Add Model Elements/*.rb (model API only).

Scripts that only make sense inside SketchUp or that need external files (PV, shading controls, frame & divider,
import / export of spaces) are not ported yet.
"""

from __future__ import annotations

from ...core.scripts_core import ArgDef, UserScript, register

CATEGORY = "Alter or Add Model Elements"


def _opt(o):
    """OpenStudio Optional -> value or None."""
    return o.get() if o.is_initialized() else None


def _tf_points(tf, pts):
    import openstudio

    vec = openstudio.Point3dVector()
    for p in pts:
        vec.append(tf * p)
    return vec


@register
class AddThermalZonesForUntaggedSpaces(UserScript):
    category = CATEGORY
    name = "Add New Thermal Zone For Spaces With No Thermal Zone"
    description = "Create one new thermal zone for every space that has none."

    def run(self, model, args, ctx):
        import openstudio

        spaces = list(model.getSpaces())
        n = 0
        for i, sp in enumerate(spaces):
            if sp.thermalZone().empty():
                tz = openstudio.model.ThermalZone(model)
                sp.setThermalZone(tz)
                ctx.info(f"Created {tz.nameString()} and assigned {sp.nameString()} to it.")
                n += 1
            ctx.progress((i + 1) / len(spaces))
        if not n:
            ctx.not_applicable("Every space already has a thermal zone.")
        return True


@register
class AddOverhangs(UserScript):
    category = CATEGORY
    name = "Add Overhangs by Projection Factor"
    description = "Add an overhang to the selected windows (sub surfaces, surfaces or spaces)."
    uses_selection = True

    def arguments(self, model):
        return [
            ArgDef("projection_factor", "Projection Factor (Fraction)", "float", 0.5),
            ArgDef("offset", "Offset (Fraction)", "float", 0.0),
            ArgDef("remove_existing_shading", "Remove Existing Space Shading Groups", "bool", False),
        ]

    def run(self, model, a, ctx):
        any_sel, cleaned = False, set()
        for s in list(model.getSubSurfaces()):
            if not ctx.in_selection(s):
                continue
            any_sel = True
            if s.subSurfaceType() not in ("FixedWindow", "OperableWindow"):
                continue
            if a["remove_existing_shading"]:
                surf = _opt(s.surface())
                space = _opt(surf.space()) if surf is not None else None
                if space is not None and space.nameString() not in cleaned:
                    for g in list(space.shadingSurfaceGroups()):
                        ctx.info(f"Removing {g.briefDescription()}.")
                        g.remove()
                    cleaned.add(space.nameString())
            oh = _opt(s.addOverhangByProjectionFactor(a["projection_factor"], a["offset"]))
            if oh is None:
                ctx.warning(f"Unable to add overhang to {s.briefDescription()}.")
                continue
            oh.setName(f"{s.nameString()} - Overhang")
            ctx.info(f"Added overhang {oh.briefDescription()} to {s.briefDescription()}.")
        if not any_sel:
            ctx.not_applicable("No sub surfaces in the current selection. Select sub surfaces, surfaces or spaces.")
        return True


@register
class AssignBuildingStories(UserScript):
    category = CATEGORY
    name = "Assign Building Stories"
    description = "Give every space without a story the story matching its lowest z coordinate."

    def run(self, model, a, ctx):
        import openstudio

        spaces = list(model.getSpaces())
        mins = []
        for sp in spaces:
            zs = [v.z() for s in sp.surfaces() for v in s.vertices()]
            if zs:
                mins.append((min(zs) + sp.zOrigin(), sp))
        mins.sort(key=lambda t: t[0])

        def story_for(z):
            for st in model.getBuildingStorys():
                sz = _opt(st.nominalZCoordinate())
                if sz is not None and abs(sz - z) < 1e-6:
                    return st
            st = openstudio.model.BuildingStory(model)
            st.setNominalZCoordinate(z)
            return st

        n = 0
        for z, sp in mins:
            if sp.buildingStory().empty():
                st = story_for(z)
                sp.setBuildingStory(st)
                ctx.info(f"Set story of space {sp.nameString()} to {st.nameString()}.")
                n += 1
        if not n:
            ctx.not_applicable("Every space already has a building story.")
        return True


@register
class CleanupOrigins(UserScript):
    category = CATEGORY
    name = "Cleanup Origins"
    description = (
        "Move the origin of the selected spaces (and their shading / partition groups) to their bounding box minimum."
    )
    uses_selection = True

    @staticmethod
    def _cleanup(group):
        import openstudio

        bb = group.transformation() * group.boundingBox()
        if bb.minX().empty():
            return
        t = openstudio.Transformation.translation(
            openstudio.Vector3d(bb.minX().get(), bb.minY().get(), bb.minZ().get())
        )
        group.changeTransformation(t)

    def run(self, model, a, ctx):
        n = 0
        for sp in model.getSpaces():
            if not ctx.in_selection(sp):
                continue
            self._cleanup(sp)
            for g in list(sp.shadingSurfaceGroups()) + list(sp.interiorPartitionSurfaceGroups()):
                self._cleanup(g)
            n += 1
        for g in list(model.getShadingSurfaceGroups()) + list(model.getInteriorPartitionSurfaceGroups()):
            if g.space().is_initialized():
                continue
            children = g.shadingSurfaces() if hasattr(g, "shadingSurfaces") else g.interiorPartitionSurfaces()
            if any(ctx.in_selection(s) for s in children):
                self._cleanup(g)
                n += 1
        if not n:
            ctx.not_applicable("Nothing selected.")
        return True


@register
class MakeSurfacesAdiabatic(UserScript):
    category = CATEGORY
    name = "Make Selected Surfaces Adiabatic and Assign a Construction"
    description = "Set the selected surfaces' boundary condition to Adiabatic and optionally assign a construction."
    uses_selection = True
    model_dependent = True

    def arguments(self, model):
        names = sorted(c.nameString() for c in model.getConstructions()) if model is not None else []
        return [
            ArgDef(
                "construction_name",
                "Pick a Construction For Selected Surfaces (Optional)",
                "choice",
                "",
                choices=(("", "<none>"), *((n, n) for n in names)),
            )
        ]

    def run(self, model, a, ctx):
        sel = [s for s in model.getSurfaces() if ctx.in_selection(s)]
        if not sel:
            ctx.not_applicable("No surfaces in the current selection.")
            return True
        for s in sel:
            s.setOutsideBoundaryCondition("Adiabatic")
        name = a["construction_name"]
        if name:
            c = _opt(model.getConstructionByName(name))
            if c is None:
                ctx.error(f"Unable to locate construction {name} in model.")
                return False
            ctx.info(f"Setting selected surfaces' construction to {c.briefDescription()}.")
            for s in sel:
                s.setConstruction(c)
        return True


@register
class RemoveHardAssignedConstructions(UserScript):
    category = CATEGORY
    name = "Remove Hard Assigned Constructions"
    description = "Reset the construction of every surface and sub surface so default construction sets apply."
    confirm_message = "This will remove hard assigned constructions."

    def run(self, model, a, ctx):
        n = 0
        for p in model.getPlanarSurfaces():
            p.resetConstruction()
            n += 1
        ctx.info(f"Reset the construction of {n} surfaces.")
        return True


@register
class RemoveLoadsAssignedToSpaces(UserScript):
    category = CATEGORY
    name = "Remove Loads Directly Assigned to Spaces"
    description = "Remove internal mass, people, lights, equipment, infiltration and outdoor air assigned to spaces."
    confirm_message = "This will remove all loads directly assigned to a space."
    _LISTS = (
        "internalMass",
        "people",
        "lights",
        "luminaires",
        "electricEquipment",
        "gasEquipment",
        "hotWaterEquipment",
        "steamEquipment",
        "otherEquipment",
        "spaceInfiltrationDesignFlowRates",
        "spaceInfiltrationEffectiveLeakageAreas",
    )

    def run(self, model, a, ctx):
        n = 0
        for sp in model.getSpaces():
            for getter in self._LISTS:
                for obj in list(getattr(sp, getter)()):
                    obj.remove()
                    n += 1
            sp.resetDesignSpecificationOutdoorAir()
        ctx.info(f"Removed {n} load objects.")
        return True


@register
class RemoveOrphanSubSurfaces(UserScript):
    category = CATEGORY
    name = "Remove Orphan SubSurfaces"
    description = "Remove sub surfaces that have no parent surface."
    confirm_message = "This will remove sub surfaces with no parent."

    def run(self, model, a, ctx):
        orphans = [s for s in model.getSubSurfaces() if s.surface().empty()]
        for s in orphans:
            s.remove()
        ctx.info(f"Removing {len(orphans)} sub-surfaces." if orphans else "No orphan sub-surfaces to remove.")
        return True


@register
class RemoveUnusedThermalZones(UserScript):
    category = CATEGORY
    name = "Remove Unused ThermalZones"
    description = "Remove thermal zones without spaces or equipment."
    confirm_message = "This will remove thermal zones without spaces or equipment."

    def run(self, model, a, ctx):
        unused = [z for z in model.getThermalZones() if not z.spaces() and not z.equipment() and z.isRemovable()]
        for z in unused:
            z.remove()
        ctx.info(f"Removing {len(unused)} thermal zones." if unused else "No unused thermal zones to remove.")
        return True


@register
class RenameThermalZones(UserScript):
    category = CATEGORY
    name = "Rename Thermal Zones Based On Space Names"
    description = "Name every thermal zone after its first space (alphabetically), plus ' - Plus' for several."

    def run(self, model, a, ctx):
        for z in model.getThermalZones():
            names = sorted(s.nameString() for s in z.spaces())
            if not names:
                ctx.info(f"{z.nameString()} did not have any spaces, and will not be renamed.")
                continue
            z.setName("Thermal Zone: " + names[0] + (" - Plus" if len(names) > 1 else ""))
        return True


@register
class SetPartitionHeight(UserScript):
    category = CATEGORY
    name = "Set Interior Partition Height Above Floor"
    description = "Move the upper edge of every interior partition surface to the given height."

    def arguments(self, model):
        return [ArgDef("height", "Height (m)", "float", 1.7)]

    def run(self, model, a, ctx):
        import openstudio

        for p in model.getInteriorPartitionSurfaces():
            vec = openstudio.Point3dVector()
            for v in p.vertices():
                z = a["height"] if abs(v.z()) > 0.01 else v.z()
                vec.append(openstudio.Point3d(v.x(), v.y(), z))
            p.setVertices(vec)
        return True


@register
class SetWindowToWallRatio(UserScript):
    category = CATEGORY
    name = "Set Window to Wall Ratio"
    description = "Add a window band to the selected exterior walls."
    uses_selection = True

    def arguments(self, model):
        return [
            ArgDef("wwr", "Window to Wall Ratio (Fraction)", "float", 0.4),
            ArgDef("offset", "Offset (meters)", "float", 0.76),
            ArgDef(
                "application_type",
                "Application Type",
                "choice",
                "Above Floor",
                choices=(("Above Floor", "Above Floor"), ("Below Ceiling", "Below Ceiling")),
            ),
        ]

    def run(self, model, a, ctx):
        wwr, offset = a["wwr"], a["offset"]
        if wwr <= 0 or wwr >= 1:
            ctx.error("Window to Wall Ratio must be greater than 0 and less than 1.")
            return False
        if offset <= 0:
            ctx.error("Offset value must be greater than 0.")
            return False
        above_floor = a["application_type"] == "Above Floor"
        any_sel = False
        for s in list(model.getSurfaces()):
            if not ctx.in_selection(s):
                continue
            any_sel = True
            if s.outsideBoundaryCondition() != "Outdoors" or s.surfaceType() != "Wall":
                continue
            win = _opt(s.setWindowToWallRatio(wwr, offset, above_floor))
            if win is None:
                ctx.warning(f"Unable to set window-to-wall ratio of {s.nameString()} to {wwr}.")
                continue
            actual = win.grossArea() / s.grossArea()
            ctx.info(f"Set window-to-wall ratio of {s.nameString()} to {actual:.3f}.")
        if not any_sel:
            ctx.not_applicable("No surfaces in the current selection. Select surfaces or spaces.")
        return True


@register
class ChangeShadingType(UserScript):
    category = CATEGORY
    name = "Change Shading Type"
    description = "Set the selected top level shading groups to Site or Building shading."
    uses_selection = True

    def arguments(self, model):
        return [
            ArgDef(
                "shading_type", "Shading Type", "choice", "Site", choices=(("Site", "Site"), ("Building", "Building"))
            )
        ]

    def run(self, model, a, ctx):
        any_sel = False
        for g in model.getShadingSurfaceGroups():
            if g.shadingSurfaceType() == "Space" or not any(ctx.in_selection(s) for s in g.shadingSurfaces()):
                continue
            any_sel = True
            if not g.setShadingSurfaceType(a["shading_type"]):
                ctx.warning(f"Unable to set shading type of {g.nameString()} to {a['shading_type']}.")
        if not any_sel:
            ctx.not_applicable("No shading surfaces in the current selection.")
        return True


@register
class MoveSurfacesToNewSpace(UserScript):
    category = CATEGORY
    name = "Move Selected Surfaces to New Space"
    description = "Create a space and move the selected surfaces (with their sub surfaces) into it."
    uses_selection = True

    def run(self, model, a, ctx):
        import openstudio

        sel = [s for s in model.getSurfaces() if ctx.in_selection(s) and not s.space().empty()]
        if not sel:
            ctx.not_applicable("No surfaces in the current selection.")
            return True
        new = openstudio.model.Space(model)
        new.setTransformation(sel[0].space().get().transformation())
        for s in sel:
            old = s.space().get()
            tf = new.transformation().inverse() * old.transformation()
            s.setSpace(new)
            s.setVertices(_tf_points(tf, s.vertices()))
            for sub in s.subSurfaces():
                sub.setVertices(_tf_points(tf, sub.vertices()))
        ctx.info(f"Moved {len(sel)} surfaces to {new.nameString()}.")
        return True


@register
class IntersectSpaceGeometry(UserScript):
    category = CATEGORY
    name = "Intersect Space Geometry"
    description = "Intersect and match the surfaces of all spaces whose bounding boxes overlap."

    def run(self, model, a, ctx):
        spaces = list(model.getSpaces())
        start = len(model.getSurfaces())
        ctx.info(f"The building started with {start} base surfaces.")
        if not start:
            ctx.not_applicable("The building does not have any base surfaces to intersect.")
            return True
        boxes = [sp.transformation() * sp.boundingBox() for sp in spaces]
        for i in range(len(spaces)):
            for j in range(i + 1, len(spaces)):
                if boxes[i].intersects(boxes[j]):
                    spaces[i].intersectSurfaces(spaces[j])
                    spaces[i].matchSurfaces(spaces[j])
            ctx.progress((i + 1) / len(spaces))
        end = len(model.getSurfaces())
        ctx.info(f"The building finished with {end} base surfaces.")
        if end == start:
            ctx.not_applicable("No surface intersections were found.")
        return True
