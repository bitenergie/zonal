"""Alter or Add Model Elements, part 2: photovoltaics, shading controls, frame & divider, space import / export.

Ports of Add_Photovoltaics, Remove_Photovoltaics, Remove_Orphan_Photovoltaics, Add_Shading_Controls,
Set_Shading_Controls, Set_WindowProperty_FrameAndDivider, Export_Selected_Space_To_New_File and
Import_Spaces_From_External_File.
"""

from __future__ import annotations

from ...core.scripts_core import ArgDef, UserScript, register
from .model_edits import CATEGORY, _opt

NEW_ELCD = "*New Electric Load Center Distribution*"


def _remove_empty_elcds(model):
    """Remove empty load center distributions and orphaned inverters -> (n_elcd, n_inverter)."""
    n_elcd = n_inv = 0
    for elcd in list(model.getElectricLoadCenterDistributions()):
        if not elcd.generators():
            inv = _opt(elcd.inverter())
            if inv is not None:
                inv.remove()
                n_inv += 1
            elcd.remove()
            n_elcd += 1
    for inv in list(model.getInverters()):
        if inv.electricLoadCenterDistribution().empty():
            inv.remove()
            n_inv += 1
    return n_elcd, n_inv


@register
class AddPhotovoltaics(UserScript):
    category = CATEGORY
    name = "Add Photovoltaics"
    description = "Add simple photovoltaic generators to the selected surfaces and shading surfaces."
    uses_selection = True
    model_dependent = True

    def arguments(self, model):
        names = sorted(e.nameString() for e in model.getElectricLoadCenterDistributions()) if model else []
        return [
            ArgDef(
                "elcd",
                "Choose Electric Load Center Distribution.",
                "choice",
                "",
                choices=(("", NEW_ELCD), *((n, n) for n in names)),
            ),
            ArgDef("fraction_area", "Fraction of Surface Area with Active Solar Cells.", "float", 1.0),
            ArgDef("efficiency", "Fixed Cell Efficiency.", "float", 0.2),
        ]

    def run(self, model, a, ctx):
        import openstudio

        osm = openstudio.model
        elcd = _opt(model.getElectricLoadCenterDistributionByName(a["elcd"])) if a["elcd"] else None
        created = elcd is None
        if created:
            elcd = osm.ElectricLoadCenterDistribution(model)
            inverter = osm.ElectricLoadCenterInverterSimple(model)
            inverter.setInverterEfficiency(0.98)
            elcd.setInverter(inverter)
        any_sel = failed = False
        for s in list(model.getPlanarSurfaces()):
            if (
                not ctx.in_selection(s)
                or s.to_SubSurface().is_initialized()
                or s.to_InteriorPartitionSurface().is_initialized()
                or s.generatorPhotovoltaics()
            ):
                continue
            any_sel = True
            panel = osm.GeneratorPhotovoltaic.simple(model)
            perf = panel.photovoltaicPerformance().to_PhotovoltaicPerformanceSimple().get()
            perf.setFractionOfSurfaceAreaWithActiveSolarCells(a["fraction_area"])
            perf.setFixedEfficiency(a["efficiency"])
            if panel.setSurface(s):
                ctx.info(f"Added GeneratorPhotovoltaic '{panel.nameString()}' for PlanarSurface '{s.nameString()}'")
                elcd.addGenerator(panel)
            else:
                failed = True
                ctx.warning(f"Could not add GeneratorPhotovoltaic for PlanarSurface '{s.nameString()}'")
                panel.remove()
        if failed and created and not elcd.generators():
            inv = _opt(elcd.inverter())
            if inv is not None:
                inv.remove()
            elcd.remove()
        if not any_sel:
            ctx.not_applicable("No allowable surfaces in the current selection. Select surfaces or shading surfaces.")
        return True


@register
class RemovePhotovoltaics(UserScript):
    category = CATEGORY
    name = "Remove Photovoltaics"
    description = (
        "Remove the photovoltaics of the selected surfaces, optionally with empty distributions and inverters."
    )
    uses_selection = True

    def arguments(self, model):
        return [
            ArgDef(
                "remove_elcd",
                "Remove Empty Electric Load Center Distribution And Orphaned Inverters?",
                "choice",
                "Yes",
                choices=(("Yes", "Yes"), ("No", "No")),
            )
        ]

    def run(self, model, a, ctx):
        n_pv = 0
        for s in model.getPlanarSurfaces():
            if ctx.in_selection(s):
                for pv in list(s.generatorPhotovoltaics()):
                    pv.remove()
                    n_pv += 1
        n_elcd = n_inv = 0
        if a["remove_elcd"] == "Yes":
            n_elcd, n_inv = _remove_empty_elcds(model)
        if n_pv + n_elcd + n_inv == 0:
            ctx.not_applicable("No objects removed.")
        else:
            ctx.info(
                f"{n_pv} GeneratorPhotovoltaic, {n_elcd} ElectricLoadCenterDistribution, {n_inv} Inverter "
                f"objects removed."
            )
        return True


@register
class RemoveOrphanPhotovoltaics(UserScript):
    category = CATEGORY
    name = "Remove Orphan Photovoltaics"
    description = "Remove photovoltaics without a surface or distribution, plus empty distributions and inverters."

    def run(self, model, a, ctx):
        n_pv = 0
        for pv in list(model.getGeneratorPhotovoltaics()):
            if pv.electricLoadCenterDistribution().empty() or pv.surface().empty():
                pv.remove()
                n_pv += 1
        n_elcd, n_inv = _remove_empty_elcds(model)
        if n_pv + n_elcd + n_inv == 0:
            ctx.not_applicable("No objects removed.")
        else:
            ctx.info(
                f"{n_pv} GeneratorPhotovoltaic, {n_elcd} ElectricLoadCenterDistribution, {n_inv} Inverter "
                f"objects removed."
            )
        return True


NEW_MATERIALS = {
    "__blind__": ("*New Blind*", "Blind"),
    "__redirection__": ("*New DaylightRedirectionDevice*", "DaylightRedirectionDevice"),
    "__shade__": ("*New Shade*", "Shade"),
}


@register
class AddShadingControls(UserScript):
    category = CATEGORY
    name = "Add Shading Controls"
    description = "Create shading controls from a shading material and / or a switchable glazing construction."
    model_dependent = True

    def arguments(self, model):
        mats = sorted(m.nameString() for m in model.getShadingMaterials()) if model else []
        cons = []
        if model:
            for c in model.getConstructions():
                if any(layer.to_FenestrationMaterial().is_initialized() for layer in c.layers()):
                    cons.append(c.nameString())
        return [
            ArgDef(
                "material",
                "Shading material.",
                "choice",
                "",
                description="Material deployed when shaded",
                choices=(("", "*None*"), *((k, v[0]) for k, v in NEW_MATERIALS.items()), *((n, n) for n in mats)),
            ),
            ArgDef(
                "construction",
                "Switchable construction.",
                "choice",
                "",
                description="Construction representing the shaded state of a switchable glazing",
                choices=(("", "*None*"), *((n, n) for n in sorted(cons))),
            ),
        ]

    def run(self, model, a, ctx):
        import openstudio

        osm = openstudio.model
        construction = _opt(model.getConstructionByName(a["construction"])) if a["construction"] else None
        mat_id = a["material"]
        material = None
        if mat_id in NEW_MATERIALS:
            material = getattr(osm, NEW_MATERIALS[mat_id][1])(model)
        elif mat_id:
            material = _opt(model.getShadingMaterialByName(mat_id))
        if construction is None and material is None:
            ctx.error("Need to choose either a shading material or a shaded construction.")
            return False
        if construction is not None and material is not None:
            ctx.warning("Selected both shading material and a shaded construction, creating shading control for both.")
        for item in (construction, material):
            if item is not None:
                ctx.info(f"Created shading control {osm.ShadingControl(item).nameString()}.")
        return True


def _set_on_sub_surfaces(model, ctx, a, none_label, getter, setter, resetter, label):
    name = a["name"]
    obj = None
    if name != none_label:
        obj = next((c for c in getter() if c.nameString() == name), None)
        if obj is None:
            ctx.error(f"Could not find {label} '{name}'.")
            return False
    for s in model.getSubSurfaces():
        if not ctx.in_selection(s):
            continue
        if obj is None:
            getattr(s, resetter)()
        elif not getattr(s, setter)(obj):
            ctx.warning(f"Could not set {label} '{name}' for SubSurface '{s.nameString()}'.")
    return True


@register
class SetShadingControls(UserScript):
    category = CATEGORY
    name = "Set Shading Controls"
    description = "Assign a shading control to the selected sub surfaces (or remove it)."
    uses_selection = True
    model_dependent = True
    NONE = "<No Shading Control>"

    def arguments(self, model):
        names = [c.nameString() for c in model.getShadingControls()] if model else []
        choices = [(n, n) for n in names] + [(self.NONE, self.NONE)]
        return [ArgDef("name", "Set Shading Controls", "choice", choices[0][0], choices=tuple(choices))]

    def run(self, model, a, ctx):
        import openstudio

        # newer OpenStudio versions renamed set -> add
        setter = (
            "setShadingControl" if hasattr(openstudio.model.SubSurface, "setShadingControl") else "addShadingControl"
        )
        return _set_on_sub_surfaces(
            model, ctx, a, self.NONE, model.getShadingControls, setter, "resetShadingControl", "ShadingControl"
        )


@register
class SetFrameAndDivider(UserScript):
    category = CATEGORY
    name = "Set Window Property Frame and Divider"
    description = "Assign a window frame and divider to the selected sub surfaces (or remove it)."
    uses_selection = True
    model_dependent = True
    NONE = "<None>"

    def arguments(self, model):
        names = [c.nameString() for c in model.getWindowPropertyFrameAndDividers()] if model else []
        choices = [(n, n) for n in names] + [(self.NONE, self.NONE)]
        return [ArgDef("name", "Window Property Frame and Divider", "choice", choices[0][0], choices=tuple(choices))]

    def run(self, model, a, ctx):
        return _set_on_sub_surfaces(
            model,
            ctx,
            a,
            self.NONE,
            model.getWindowPropertyFrameAndDividers,
            "setWindowPropertyFrameAndDivider",
            "resetWindowPropertyFrameAndDivider",
            "WindowPropertyFrameAndDivider",
        )


@register
class ExportSelectedSpaces(UserScript):
    category = CATEGORY
    name = "Export Selected Spaces to a new External Model"
    description = "Copy the selected spaces into a new .osm file."
    uses_selection = True
    modifies_model = False

    def arguments(self, model):
        return [ArgDef("save_path", "Save Export Spaces As", "path", "//ExportedSpaces.osm", ext="osm")]

    def run(self, model, a, ctx):
        import openstudio

        spaces = [s for s in model.getSpaces() if ctx.in_selection(s)]
        if not spaces:
            ctx.not_applicable("No spaces selected.")
            return True
        if not a["save_path"]:
            ctx.error("Choose a file to save the spaces to.")
            return False
        out = openstudio.model.Model()
        for sp in spaces:
            ctx.info(f"Adding {sp.briefDescription()} to {a['save_path']}.")
            sp.clone(out)
        if not out.save(openstudio.path(a["save_path"]), True):
            ctx.error(f"Could not write {a['save_path']}")
            return False
        ctx.info(f"File named '{a['save_path']}' created with {len(spaces)} spaces.")
        return True


@register
class MergeSpacesFromFile(UserScript):
    category = CATEGORY
    name = "Merge Spaces From External File"
    description = "Copy the spaces of another .osm into the model; spaces with the same name are replaced."

    def arguments(self, model):
        return [ArgDef("import_path", "Select OSM File to Import Spaces From", "path", "", ext="osm", read=True)]

    def run(self, model, a, ctx):
        import openstudio

        path = a["import_path"]
        if not path:
            ctx.error("Non-empty path argument is required.")
            return False
        loaded = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(path))
        if loaded.empty():
            ctx.error(f"Could not load '{path}' as an OpenStudio model.")
            return False
        for sp in loaded.get().getSpaces():
            existing = next((e for e in model.getSpaces() if e.nameString() == sp.nameString()), None)
            story = zone = None
            if existing is not None:
                story, zone = _opt(existing.buildingStory()), _opt(existing.thermalZone())
                ctx.info(f"Replacing Space '{existing.nameString()}'")
                existing.remove()
            cloned = sp.clone(model).to_Space().get()
            if story is not None:
                cloned.setBuildingStory(story)
            if zone is not None:
                cloned.setThermalZone(zone)
        ctx.info(f"Spaces from file named '{path}' have been imported")
        return True
