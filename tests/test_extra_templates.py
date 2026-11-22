"""Extra template files: import only constructions / schedules / space types from other .osm files."""

from pathlib import Path

import pytest

pytest.importorskip("openstudio")
import openstudio

from zonal.core.gltf_reader import Face, GltfScene
from zonal.core.osm_builder import BuildOptions, build_model

TEMPLATE = str(
    Path(__file__).resolve().parent.parent / "src" / "zonal" / "templates" / "MinimalTemplate.osm"
)
WALL = [(0.0, 0, 0), (10, 0, 0), (10, 0, 3), (0, 0, 3)]


def scene():
    return GltfScene([Face("W", WALL, {"spaceName": "S", "surfaceType": "Wall"}, "W")], {}, "t")


def test_only_constructions_from_extra_file():
    m, _ = build_model(scene(), BuildOptions(extra_templates=[(TEMPLATE, ["Construction"])]))
    assert len(m.getConstructions()) > 0 and len(m.getMaterials()) > 0
    assert len(m.getSpaceTypes()) == 0 and len(m.getDefaultConstructionSets()) == 0


def test_schedules_are_kept_only_when_requested():
    src = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(TEMPLATE)).get()
    assert len(src.getScheduleRulesets()) > 0
    m, _ = build_model(scene(), BuildOptions(extra_templates=[(TEMPLATE, ["Construction"])]))
    assert len(m.getScheduleRulesets()) == 0  # unreferenced ones are pruned
    m, _ = build_model(scene(), BuildOptions(extra_templates=[(TEMPLATE, ["Schedule"])]))
    assert len(m.getScheduleRulesets()) == len(src.getScheduleRulesets())


def test_first_file_wins_on_name_clash_and_main_template_combines():
    m, _ = build_model(
        scene(),
        BuildOptions(
            template_osm=TEMPLATE, template_types=["SpaceType"], extra_templates=[(TEMPLATE, ["Construction"])]
        ),
    )
    assert len(m.getSpaceTypes()) > 0 and len(m.getConstructions()) > 0
    n = len(m.getConstructions())
    m2, _ = build_model(
        scene(),
        BuildOptions(extra_templates=[(TEMPLATE, ["Construction"]), (TEMPLATE, ["Construction"])]),
    )
    assert len(m2.getConstructions()) == n  # the second copy is skipped, nothing duplicated


def test_missing_extra_file_warns():
    _m, rep = build_model(scene(), BuildOptions(extra_templates=[("nope.osm", ["Construction"])]))
    assert any("could not be loaded" in w for w in rep.warnings)
