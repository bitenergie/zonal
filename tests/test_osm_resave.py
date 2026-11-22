"""Export an .osm from a Blender scene, load + save it with OpenStudio, and compare the two files."""

import pytest

pytest.importorskip("openstudio")
from conftest import requires_bpy

pytestmark = requires_bpy


def _object_blocks(text: str) -> dict[str, str]:
    """Map handle -> object text (comments stripped; the file's header lines are ignored)."""
    blocks = {}
    for chunk in text.replace("\r\n", "\n").split("\n\n"):
        lines = [ln.split("!-")[0].rstrip() for ln in chunk.strip().splitlines()]
        if lines and lines[0].startswith("OS:") and len(lines) > 1:
            blocks[lines[1].strip()] = "\n".join(lines)
    return blocks


def test_export_survives_openstudio_resave(clean_scene, tmp_path):
    import openstudio

    bpy = clean_scene
    bpy.ops.mesh.primitive_cube_add(size=1, location=(5, 2.5, 1.5))
    box = bpy.context.object
    box.name = "Room"
    box.scale = (10, 5, 3)
    bpy.ops.object.transform_apply(scale=True)
    box["thermalZoneName"] = "Zone 1"

    exported = tmp_path / "exported.osm"
    assert bpy.ops.export_scene.openstudio_osm(filepath=str(exported)) == {"FINISHED"}

    model = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(exported))).get()
    resaved = tmp_path / "resaved.osm"
    assert model.save(openstudio.path(str(resaved)), True)

    a = _object_blocks(exported.read_text(encoding="utf-8"))
    b = _object_blocks(resaved.read_text(encoding="utf-8"))
    assert any(v.startswith("OS:RunPeriod") for v in a.values()), "export lacks OS:RunPeriod"
    assert a.keys() == b.keys(), (sorted(a.keys() - b.keys()), sorted(b.keys() - a.keys()))
    diffs = [h for h in a if a[h] != b[h]]
    assert not diffs, "\n\n".join(f"--- exported\n{a[h]}\n--- resaved\n{b[h]}" for h in diffs[:3])


def test_weather_file_linked_for_the_gui(clean_scene, tmp_path):
    """The GUI resolves a bare Url in <osm name>/files (the CLI in ./files); else it resets the weather file."""
    from pathlib import Path

    import openstudio

    found = list(Path(openstudio.__file__).parent.rglob("*.epw")) or list(
        Path(__file__).resolve().parent.parent.rglob("*.epw")
    )
    if not found:
        pytest.skip("no sample .epw available")
    epw = tmp_path / "src" / found[0].name
    epw.parent.mkdir()
    epw.write_bytes(found[0].read_bytes())

    from zonal.blender import inspector as bi

    bpy = clean_scene
    bpy.ops.mesh.primitive_cube_add()
    bi._weather_item(bpy.context.scene).epwPath = str(epw)

    out = tmp_path / "cube.osm"
    assert bpy.ops.export_scene.openstudio_osm(filepath=str(out)) == {"FINISHED"}
    model = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(out))).get()
    assert str(model.getWeatherFile().url().get()) == epw.name  # bare file name, like the GUI saves it
    assert (tmp_path / "cube" / "files" / epw.name).is_file()
    assert (tmp_path / "files" / epw.name).is_file()
    import json

    osw = json.loads((tmp_path / "cube" / "workflow.osw").read_text(encoding="utf-8"))
    assert osw["weather_file"] == epw.name and osw["seed_file"] == "../cube.osm"
    wf = openstudio.WorkflowJSON.load(openstudio.path(str(tmp_path / "cube" / "workflow.osw"))).get()
    assert wf.weatherFile().is_initialized()
