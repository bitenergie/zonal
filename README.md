# Zonal

*Zonal by Bitenergie. Building energy modeling for Blender.*

Blender extension that exports the scene to an OpenStudio `.osm` model. The Blender meshes and their custom
properties (the OpenStudio glTF `extras` schema) are built directly, with the OpenStudio API, into spaces, surfaces,
sub-surfaces, shading and zones. glTF import / export is available separately (File > Import / Export > OpenStudio glTF).

Requires Blender 5.0+ on Windows x64, Linux x64/arm64 or macOS x64/arm64. The OpenStudio 3.11 Python bindings are bundled as wheels (one zip per platform).

## Install

Add the repository in Blender: *Preferences > Get Extensions > Repositories > + > Add Remote Repository*, URL

```

https://bitenergie.github.io/zonal/index.json
```

then search for **Zonal** and install. Or drag the zip from the
[Releases](https://github.com/bitenergie/zonal/releases/latest) page into Blender.

## Develop

```
uv sync
uv run pytest -q
uv run ruff check .
uv run python scripts/build.py --install     # build zip and install into Blender
```

## Publish (static extensions repository)

```
uv run python scripts/make_repo.py --base-url https://github.com/bitenergie/zonal/releases/download/vX.Y.Z
```

writes `repo/` (one zip per platform, `index.json`, `index.html`). Pushing a `vX.Y.Z` tag runs
[.github/workflows/release.yml](.github/workflows/release.yml): tests, build, zip attached to the GitHub Release,
listing published on GitHub Pages. Bump the version in `pyproject.toml`, `blender_manifest.toml` and `__init__.py`
together (`tests/test_manifest.py` checks they match). See [docs/](docs/) for details.

License: GPL-3.0-or-later. Bundled third-party software (OpenStudio, BSD-3-Clause variant) is listed in [THIRD_PARTY_NOTICES.md](src/zonal/THIRD_PARTY_NOTICES.md).

## Disclaimer

> Zonal is an independent project. It is not affiliated with or endorsed by NREL, the Alliance for Sustainable Energy or the OpenStudio project. OpenStudio is a trademark of its owners. Blender is a trademark of the Blender Foundation.
