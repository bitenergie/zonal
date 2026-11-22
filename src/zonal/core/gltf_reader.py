"""Minimal glTF 2.0 reader: nodes -> world-space planar polygons + OpenStudio extras.

No openstudio or bpy dependency (numpy comes in through `geometry`), so it also runs outside Blender.
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import Vec, faces_from_triangles

_COMPONENT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

IDENTITY = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)


@dataclass
class Face:
    name: str
    polygon: list[Vec]
    extras: dict = field(default_factory=dict)
    node_name: str = ""
    index_in_node: int = 0
    node_face_count: int = 1


@dataclass
class GltfScene:
    faces: list[Face]
    scene_extras: dict
    generator: str = ""


# --- matrices (column-major, as glTF) -------------------------------------------------


def _mul(a, b):
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return tuple(out)


def _trs(t, q, s):
    x, y, z, w = q
    sx, sy, sz = s
    return (
        (1 - 2 * (y * y + z * z)) * sx,
        (2 * (x * y + z * w)) * sx,
        (2 * (x * z - y * w)) * sx,
        0.0,
        (2 * (x * y - z * w)) * sy,
        (1 - 2 * (x * x + z * z)) * sy,
        (2 * (y * z + x * w)) * sy,
        0.0,
        (2 * (x * z + y * w)) * sz,
        (2 * (y * z - x * w)) * sz,
        (1 - 2 * (x * x + y * y)) * sz,
        0.0,
        t[0],
        t[1],
        t[2],
        1.0,
    )


def _local_matrix(node: dict):
    if "matrix" in node:
        return tuple(float(v) for v in node["matrix"])
    return _trs(node.get("translation", (0, 0, 0)), node.get("rotation", (0, 0, 0, 1)), node.get("scale", (1, 1, 1)))


def _apply(m, p):
    return (
        m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
        m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
        m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14],
    )


# --- container + accessors ------------------------------------------------------------


def _load_container(path: Path) -> tuple[dict, bytes | None]:
    data = path.read_bytes()
    if data[:4] == b"glTF":
        _, _, length = struct.unpack_from("<4sII", data, 0)
        off, js, binchunk = 12, None, None
        while off < length:
            clen, ctype = struct.unpack_from("<II", data, off)
            chunk = data[off + 8 : off + 8 + clen]
            if ctype == 0x4E4F534A:
                js = json.loads(chunk.decode("utf-8"))
            elif ctype == 0x004E4942:
                binchunk = chunk
            off += 8 + clen
        return js or {}, binchunk
    return json.loads(data.decode("utf-8")), None


def _buffers(gltf: dict, base: Path, glb_bin: bytes | None) -> list[bytes]:
    out = []
    for i, b in enumerate(gltf.get("buffers", [])):
        uri = b.get("uri")
        if uri is None:
            out.append(glb_bin or b"")
        elif uri.startswith("data:"):
            out.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            from urllib.parse import unquote

            out.append((base / unquote(uri)).read_bytes())
    return out


def _read_accessor(gltf: dict, buffers: list[bytes], index: int) -> list:
    acc = gltf["accessors"][index]
    fmt, size = _COMPONENT[acc["componentType"]]
    n = _NCOMP[acc["type"]]
    count = acc["count"]
    if "bufferView" not in acc:
        return [tuple([0] * n) if n > 1 else 0 for _ in range(count)]
    bv = gltf["bufferViews"][acc["bufferView"]]
    buf = buffers[bv["buffer"]]
    start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = bv.get("byteStride") or size * n
    out = []
    unpack = struct.Struct("<" + fmt * n).unpack_from
    for i in range(count):
        v = unpack(buf, start + i * stride)
        out.append(v if n > 1 else v[0])
    return out


def _primitive_triangles(gltf, buffers, prim) -> tuple[list[Vec], list[int]]:
    pos = _read_accessor(gltf, buffers, prim["attributes"]["POSITION"])
    mode = prim.get("mode", 4)
    idx = _read_accessor(gltf, buffers, prim["indices"]) if "indices" in prim else list(range(len(pos)))
    if mode == 4:
        tri = idx
    elif mode == 5:  # strip
        tri = []
        for i in range(len(idx) - 2):
            tri += [idx[i], idx[i + 1], idx[i + 2]] if i % 2 == 0 else [idx[i + 1], idx[i], idx[i + 2]]
    elif mode == 6:  # fan
        tri = []
        for i in range(1, len(idx) - 1):
            tri += [idx[0], idx[i], idx[i + 1]]
    else:
        return [], []
    return [tuple(p) for p in pos], tri


# --- public API -----------------------------------------------------------------------


def read_gltf(
    path, *, y_up: bool | None = None, scale: float = 1.0, overrides: dict[str, dict] | None = None
) -> GltfScene:
    """Read a .gltf/.glb and return world-space polygons in OpenStudio's Z-up frame.

    y_up: True if the file is Y-up (glTF convention, e.g. files from OpenStudio itself).
          None = auto (Y-up when the generator is OpenStudio, else Z-up as written by
          Blender with export_yup=False).
    scale: multiplier applied to all coordinates (to metres).
    overrides: {node name: default extras} merged *under* the node's own extras.
    """
    path = Path(path)
    gltf, glb_bin = _load_container(path)
    buffers = _buffers(gltf, path.parent, glb_bin)
    generator = gltf.get("asset", {}).get("generator", "")
    if y_up is None:
        y_up = generator.startswith("OpenStudio")
    overrides = overrides or {}

    nodes = gltf.get("nodes", [])
    scenes = gltf.get("scenes", [])
    scene = scenes[gltf.get("scene", 0)] if scenes else {"nodes": list(range(len(nodes)))}

    faces: list[Face] = []

    def visit(i: int, parent_m):
        node = nodes[i]
        m = _mul(parent_m, _local_matrix(node))
        if "mesh" in node:
            mesh = gltf["meshes"][node["mesh"]]
            extras = {}
            extras.update(overrides.get(node.get("name", ""), {}))
            extras.update(mesh.get("extras") or {})
            extras.update(node.get("extras") or {})
            positions: list[Vec] = []
            indices: list[int] = []
            for prim in mesh.get("primitives", []):
                p, t = _primitive_triangles(gltf, buffers, prim)
                base = len(positions)
                positions += [_apply(m, q) for q in p]
                indices += [base + k for k in t]
            if y_up:
                positions = [(x, -z, y) for x, y, z in positions]
            if scale != 1.0:
                positions = [(x * scale, y * scale, z * scale) for x, y, z in positions]
            polys = faces_from_triangles(positions, indices)
            nname = node.get("name") or mesh.get("name") or f"node{i}"
            for k, poly in enumerate(polys):
                fname = nname if len(polys) == 1 else f"{nname} {k + 1}"
                faces.append(Face(fname, poly, dict(extras), nname, k, len(polys)))
        for c in node.get("children", []):
            visit(c, m)

    for root in scene.get("nodes", []):
        visit(root, IDENTITY)
    return GltfScene(faces, scene.get("extras") or {}, generator)
