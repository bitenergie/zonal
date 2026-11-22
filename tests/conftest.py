"""Shared pytest fixtures. Tests needing Blender use the ``bpy`` PyPI module (Python 3.13 only)."""

import pytest

try:
    import bpy  # noqa: F401

    HAVE_BPY = True
except ImportError:  # pragma: no cover
    HAVE_BPY = False

requires_bpy = pytest.mark.skipif(not HAVE_BPY, reason="bpy module not installed")


@pytest.fixture(scope="session")
def addon():
    """Register the add-on once per session; returns the bpy module."""
    if not HAVE_BPY:
        pytest.skip("bpy module not installed")
    import bpy

    import zonal

    zonal.register()
    yield bpy
    zonal.unregister()


@pytest.fixture
def clean_scene(addon):
    """Empty the current scene (objects, collections, meshes) before each test."""
    bpy = addon
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy
