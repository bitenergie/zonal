"""OpenStudio for Blender: export the scene to an OpenStudio model (.osm).

Pipeline: Blender glTF exporter (custom properties -> glTF `extras`, following the
OpenStudio glTF schema) -> `gltf_reader` -> `osm_builder` (openstudio python bindings).

Everything except `blender_ui` is importable without bpy.
"""

bl_info = {
    "name": "Zonal",
    "author": "Marco Conte",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "File > Export > OpenStudio Model (.osm)",
    "description": "Export Blender geometry to an OpenStudio .osm via the OpenStudio glTF schema",
    "category": "Import-Export",
}

__version__ = "0.1.0-beta.1"


def register():
    from .blender import ui as blender_ui

    blender_ui.register()


def unregister():
    from .blender import ui as blender_ui

    blender_ui.unregister()
