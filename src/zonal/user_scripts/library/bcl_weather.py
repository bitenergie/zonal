"""Building Component Library: port of Get_BCL_Weather_File.rb (needs network access)."""

from __future__ import annotations

from ...core.scripts_core import ArgDef, UserScript, register


@register
class GetBclWeatherFile(UserScript):
    category = "Building Component Library"
    name = "Get BCL Weather File"
    reads_scene = False
    edits_geometry = False
    description = (
        "Search the Building Component Library for a weather file by city, download it and use it as the "
        "model's weather file (the Inspector weather file of the scene is updated too)."
    )

    def arguments(self, model):
        return [
            ArgDef("city", "City Name", "string", "Denver"),
            ArgDef(
                "pick",
                "Result Number",
                "int",
                1,
                description="Which search result to download (1 = best match); all results are listed",
            ),
        ]

    def run(self, model, a, ctx):
        import openstudio

        city = a["city"].strip()
        if not city:
            ctx.error("Please enter city to search for.")
            return False
        remote = openstudio.RemoteBCL()
        responses = list(remote.searchComponentLibrary(city, "Weather File"))
        if not responses:
            ctx.error(f"No weather file BCL results for city '{city}'.")
            return False
        for i, r in enumerate(responses, 1):
            ctx.info(f"{i}. {r.name()}")
        pick = min(max(int(a["pick"]), 1), len(responses))
        remote.downloadComponent(responses[pick - 1].uid())
        component = remote.waitForComponentDownload()
        if component.empty():
            ctx.error("Cannot find local component")
            return False
        files = component.get().files("epw")
        if not files:
            ctx.error("No epw file found")
            return False
        epw_path = str(files[0])
        epw = openstudio.EpwFile(openstudio.path(epw_path))
        if not openstudio.model.WeatherFile.setWeatherFile(model, epw).is_initialized():
            ctx.error(f"Could not use '{epw_path}'")
            return False
        ctx.weather_file = epw_path
        ctx.info(f"Successfully set weather file to {epw_path}")
        return True
