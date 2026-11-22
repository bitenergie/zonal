"""Tiny glTF writer for tests (Z-up, embedded buffers)."""

import base64
import json
import struct


def box_quads(x0, y0, z0, x1, y1, z1):
    """Six outward-facing (CCW from outside) quads of an axis-aligned box."""
    v = lambda x, y, z: (x, y, z)
    return {
        "floor": [v(x0, y0, z0), v(x0, y1, z0), v(x1, y1, z0), v(x1, y0, z0)],
        "roof": [v(x0, y0, z1), v(x1, y0, z1), v(x1, y1, z1), v(x0, y1, z1)],
        "south": [v(x0, y0, z0), v(x1, y0, z0), v(x1, y0, z1), v(x0, y0, z1)],
        "east": [v(x1, y0, z0), v(x1, y1, z0), v(x1, y1, z1), v(x1, y0, z1)],
        "north": [v(x1, y1, z0), v(x0, y1, z0), v(x0, y1, z1), v(x1, y1, z1)],
        "west": [v(x0, y1, z0), v(x0, y0, z0), v(x0, y0, z1), v(x0, y1, z1)],
    }


def write_gltf(path, objects, scene_extras=None, generator="Blender"):
    """objects: list of (name, [polygon, ...], extras). Polygons are triangulated as fans."""
    blob = bytearray()
    views, accessors, meshes, nodes = [], [], [], []
    for name, polys, extras in objects:
        pos, idx = [], []
        for poly in polys:
            base = len(pos)
            pos += poly
            for i in range(1, len(poly) - 1):
                idx += [base, base + i, base + i + 1]
        pb = b"".join(struct.pack("<3f", *p) for p in pos)
        ib = struct.pack(f"<{len(idx)}I", *idx)
        for data, target in ((pb, 34962), (ib, 34963)):
            views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target})
            blob += data
        accessors.append({"bufferView": len(views) - 2, "componentType": 5126, "count": len(pos), "type": "VEC3"})
        accessors.append({"bufferView": len(views) - 1, "componentType": 5125, "count": len(idx), "type": "SCALAR"})
        meshes.append(
            {
                "name": name,
                "primitives": [{"attributes": {"POSITION": len(accessors) - 2}, "indices": len(accessors) - 1}],
            }
        )
        node = {"name": name, "mesh": len(meshes) - 1}
        if extras is not None:
            node["extras"] = extras
        nodes.append(node)
    doc = {
        "asset": {"version": "2.0", "generator": generator},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes))), **({"extras": scene_extras} if scene_extras else {})}],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [
            {
                "byteLength": len(blob),
                "uri": "data:application/octet-stream;base64," + base64.b64encode(bytes(blob)).decode(),
            }
        ],
    }
    path.write_text(json.dumps(doc))
    return path
