"""Read the Blender scene straight into the faces `osm_builder` consumes (imports bpy).

This is the direct path for .osm / gbXML export: evaluated meshes -> world-space polygons + custom properties,
no intermediate glTF file. (The glTF exporter / importer remain available as standalone operators.)
"""

from __future__ import annotations

import contextlib

import numpy as np

from ..core.geometry import faces_from_triangles
from ..core.gltf_reader import Face, GltfScene

_SKIP_PREFIXES = ("_", "cycles")


def plain(value):
    """Blender ID property value -> plain JSON-compatible Python (None when it cannot be represented)."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, "to_dict"):
        return {k: plain(v) for k, v in value.to_dict().items()}
    if hasattr(value, "to_list"):
        return [plain(v) for v in value.to_list()]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    try:
        return [plain(v) for v in value]
    except TypeError:
        return None


def custom_props(id_block) -> dict:
    """The user custom properties of an object / mesh / scene (what the glTF exporter wrote as `extras`)."""
    out = {}
    for key in id_block.keys():
        if key.startswith(_SKIP_PREFIXES):
            continue
        value = plain(id_block[key])
        if value is not None:
            out[key] = value
    return out


@contextlib.contextmanager
def displace_muted(scene):
    """The sub surface Displace modifier is a viewing aid only: switch it off while meshes are evaluated."""
    from .ui import DISPLACE_NAME, sync_subsurface_displace

    muted = []
    for ob in scene.objects:
        sync_subsurface_displace(ob)
        mod = ob.modifiers.get(DISPLACE_NAME) if ob.type == "MESH" else None
        if mod is not None and mod.show_viewport:
            mod.show_viewport = False
            muted.append(mod)
    try:
        yield
    finally:
        for mod in muted:
            mod.show_viewport = True


def object_faces(ob, depsgraph, *, scale: float = 1.0, overrides: dict | None = None) -> list[Face]:
    """World-space polygons of one mesh object (modifiers applied), with its merged extras.

    Merge order, lowest to highest priority: operator defaults < mesh custom properties < object custom properties.
    """
    ev = ob.evaluated_get(depsgraph)
    mesh = ev.to_mesh()
    try:
        mesh.calc_loop_triangles()
        # bulk copies (foreach_get) + one matrix product instead of a Python call per vertex / triangle
        co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        m = np.array(ev.matrix_world, dtype=np.float64)
        world = (co.reshape(-1, 3).astype(np.float64) @ m[:3, :3].T + m[:3, 3]) * scale
        positions = list(map(tuple, world.tolist()))
        tri = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", tri)
        indices = tri.tolist()
    finally:
        ev.to_mesh_clear()
    extras: dict = {}
    ov = (overrides or {}).get(ob.name, {})
    forced = ov.get("!space", {})  # ui.FORCED_KEY: the space's single value beats per-object extras
    extras.update({k: v for k, v in ov.items() if k != "!space"})
    if ob.data is not None:
        extras.update(custom_props(ob.data))
    extras.update(custom_props(ob))
    extras.update(forced)
    polys = faces_from_triangles(positions, indices)
    faces = []
    for k, poly in enumerate(polys):
        name = ob.name if len(polys) == 1 else f"{ob.name} {k + 1}"
        faces.append(Face(name, poly, dict(extras), ob.name, k, len(polys)))
    return faces


def collect_scene(context, overrides: dict[str, dict], *, scale: float = 1.0, store_json: str = "") -> GltfScene:
    """Faces of the objects named in `overrides` (see `blender_ui.default_space_overrides`) plus scene extras.

    `store_json` is the Inspector store (`blender_inspector.export_json`), passed as the `osmModel` scene extra.
    """
    scene = context.scene
    faces: list[Face] = []
    with displace_muted(scene):
        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        for ob in scene.objects:
            if ob.type == "MESH" and ob.name in overrides:
                faces.extend(object_faces(ob, depsgraph, scale=scale, overrides=overrides))
    extras = custom_props(scene)
    extras.pop("osmModel", None)  # only the live Inspector store counts, never a stale scene property
    if store_json:
        extras["osmModel"] = store_json
    return GltfScene(faces, extras, "Blender")
