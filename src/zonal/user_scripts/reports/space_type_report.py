"""Reports: port of reference/user_scripts/Reports/SpaceType_Report.rb (writes a CSV, leaves the model alone)."""

from __future__ import annotations

from ...core.scripts_core import ArgDef, UserScript, register

FT2_PER_M2 = 10.7639
M2_PER_FT2 = 1.0 / FT2_PER_M2
NO_TYPE, NO_ZONE, NO_STORY = "<no space type>", "<no thermal zone>", "<no building story>"


def _name_or(opt, fallback):
    return opt.get().nameString() if opt.is_initialized() else fallback


def _r(x):
    return round(x, 2)


def build_rows(model) -> list[str]:
    """CSV lines of the report (pure function of the model)."""
    out = [
        "Building, Space Type, Floor Area (ft^2), Lighting Power Density (W/ft^2), "
        + "Electric Equipment Density (W/ft^2), People Density (people/1000*ft^2)"
    ]
    b = model.getBuilding()
    out.append(
        ", ".join(
            str(v)
            for v in (
                b.nameString(),
                _name_or(b.spaceType(), NO_TYPE),
                _r(FT2_PER_M2 * b.floorArea()),
                _r(M2_PER_FT2 * b.lightingPowerPerFloorArea()),
                _r(M2_PER_FT2 * b.electricEquipmentPowerPerFloorArea()),
                _r(1000 * M2_PER_FT2 * b.peoplePerFloorArea()),
            )
        )
    )
    out.append("")

    spaces = sorted(model.getSpaces(), key=lambda s: s.nameString())

    def group(title, groups):
        out.append(f"{title}, Floor Area (ft^2), Number of Spaces")
        for name, sps in groups:
            if sps:
                out.append(f"{name}, {_r(FT2_PER_M2 * sum(s.floorArea() for s in sps))}, {len(sps)}")
        out.append("")

    stories = sorted(model.getBuildingStorys(), key=lambda s: s.nameString())
    group(
        "Building Story",
        [(s.nameString(), list(s.spaces())) for s in stories]
        + [(NO_STORY, [s for s in spaces if s.buildingStory().empty()])],
    )
    types = sorted(model.getSpaceTypes(), key=lambda s: s.nameString())
    group(
        "Space Type",
        [(t.nameString(), list(t.spaces())) for t in types] + [(NO_TYPE, [s for s in spaces if s.spaceType().empty()])],
    )

    out.append(
        "Space, Space Type, Thermal Zone, Building Story, Floor Area (ft^2), Lighting Power Density (W/ft^2), "
        "Electric Equipment Density (W/ft^2), People Density (people/1000*ft^2)"
    )
    ordered = [s for t in types for s in sorted(t.spaces(), key=lambda x: x.nameString())]
    ordered += [s for s in spaces if s.spaceType().empty()]
    for s in ordered:
        out.append(
            ", ".join(
                str(v)
                for v in (
                    s.nameString(),
                    _name_or(s.spaceType(), NO_TYPE),
                    _name_or(s.thermalZone(), NO_ZONE),
                    _name_or(s.buildingStory(), NO_STORY),
                    _r(FT2_PER_M2 * s.floorArea()),
                    _r(M2_PER_FT2 * s.lightingPowerPerFloorArea()),
                    _r(M2_PER_FT2 * s.electricEquipmentPowerPerFloorArea()),
                    _r(1000 * M2_PER_FT2 * s.peoplePerFloorArea()),
                )
            )
        )
    out.append("")
    return out


@register
class SpaceTypeReport(UserScript):
    category = "Reports"
    name = "Space Type Report"
    description = "Write a CSV with floor areas and load densities per building, story, space type and space."
    modifies_model = False

    def arguments(self, model):
        return [ArgDef("save_path", "Save Space Type Report As", "path", "//SpaceTypeReport.csv", ext="csv")]

    def run(self, model, a, ctx):
        path = a["save_path"]
        if not path:
            ctx.error("Choose a file to save the report to.")
            return False
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(build_rows(model)) + "\n")
        ctx.info(f"Wrote {path}")
        return True
