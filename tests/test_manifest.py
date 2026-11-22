"""The extension manifest must stay valid and in sync with the package version."""

import tomllib
from pathlib import Path

import zonal

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = tomllib.loads((ROOT / "src" / "zonal" / "blender_manifest.toml").read_text("utf-8"))


def test_required_fields():
    for key in (
        "schema_version",
        "id",
        "version",
        "name",
        "tagline",
        "maintainer",
        "type",
        "blender_version_min",
        "license",
    ):
        assert key in MANIFEST
    assert MANIFEST["type"] == "add-on"
    assert len(MANIFEST["tagline"]) <= 64 and not MANIFEST["tagline"].endswith(".")
    assert all(t.startswith("SPDX:") for t in MANIFEST["license"])


def test_id_matches_package():
    assert MANIFEST["id"] == "zonal"
    assert (ROOT / "src" / MANIFEST["id"]).is_dir()


def test_versions_in_sync():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    assert MANIFEST["version"] == pyproject["project"]["version"] == zonal.__version__


def test_wheels_listed_exist_in_fetch_script():
    fetch = (ROOT / "scripts" / "fetch_wheels.py").read_text("utf-8")
    for wheel in MANIFEST["wheels"]:
        assert Path(wheel).name.split("-", 2)[2].removesuffix(".whl") in fetch


def test_permission_reasons_short():
    assert all(len(v) <= 64 for v in MANIFEST.get("permissions", {}).values())
