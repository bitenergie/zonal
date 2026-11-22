# Setup

## Development environment

```
uv sync                       # Python 3.13 venv with the openstudio wheel
uv run pytest -q              # unit tests (no Blender needed)
uv run ruff check .
```

## Build and install the extension

```
uv run python scripts/build.py            # builds dist/zonal-<ver>.zip
uv run python scripts/build.py --install  # ...and installs into Blender
```

## Working on these docs

```
uv sync --group docs
uv run zensical serve    # live preview
uv run zensical build    # static site in ./site
```

!!! note "Screenshots are generated"
    Run `uv run python scripts/docs_screenshots.py` to regenerate every image of the
    [interface guide](../interface-guide.md).

### Regenerating the screenshots and the hero animation

`scripts/docs_screenshots.py` starts a real (GUI) Blender, builds the demo model, shows one panel at a time and saves cropped PNGs to `docs/img/`.

```
uv run python scripts/docs_screenshots.py                       # newest Blender 5.x in Program Files
uv run python scripts/docs_screenshots.py --blender "C:\Path\blender.exe" --out documentation\img
```

A Blender 5.x is needed because the development `.venv` provides the `openstudio` bindings for CPython 3.13.
To add a screenshot, add the panel class to `PANELS` and a `yield from panel_shot(...)` line to `script()`.

`--hero-only` renders just the animated hero of the home page (`img/hero.webp`: the storeys of the demo building lift apart while the camera turns); `--no-hero` skips it. It needs `ffmpeg` on the `PATH`.
