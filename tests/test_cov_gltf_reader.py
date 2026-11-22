import base64
import json
import struct

import pytest

from zonal.core import gltf_reader as r


def _doc(mode, idx=None, uri="data"):
    pos = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    blob = b"".join(struct.pack("<3f", *p) for p in pos)
    acc = [{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"}]
    prim = {"attributes": {"POSITION": 0}, "mode": mode}
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(blob)}]
    if idx is not None:
        ib = struct.pack(f"<{len(idx)}H", *idx)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(ib)})
        acc.append({"bufferView": 1, "componentType": 5123, "count": len(idx), "type": "SCALAR"})
        prim["indices"] = 1
        blob += ib
    buf = {"byteLength": len(blob)}
    if uri == "data":
        buf["uri"] = "data:application/octet-stream;base64," + base64.b64encode(blob).decode()
    elif uri:
        buf["uri"] = uri
    return (
        {
            "asset": {"version": "2.0"},
            "nodes": [{"name": "n", "mesh": 0, "scale": [2, 2, 2]}],
            "meshes": [{"primitives": [prim]}],
            "accessors": acc,
            "bufferViews": views,
            "buffers": [buf],
        },
        blob,
    )


@pytest.mark.parametrize("mode,idx", [(5, [0, 1, 3, 2]), (6, [0, 1, 2, 3]), (4, [0, 1, 2, 0, 2, 3]), (4, None)])
def test_modes(tmp_path, mode, idx):
    doc, _ = _doc(mode, idx)
    p = tmp_path / "a.gltf"
    p.write_text(json.dumps(doc))
    sc = r.read_gltf(p, y_up=True, scale=2.0)
    assert len(sc.faces) <= 1


def test_unsupported_mode(tmp_path):
    doc, _ = _doc(1, [0, 1])
    p = tmp_path / "a.gltf"
    p.write_text(json.dumps(doc))
    assert r.read_gltf(p).faces == []


def test_external_buffer_and_no_view(tmp_path):
    doc, blob = _doc(4, [0, 1, 2, 0, 2, 3], uri="my%20buf.bin")
    (tmp_path / "my buf.bin").write_bytes(blob)
    p = tmp_path / "a.gltf"
    p.write_text(json.dumps(doc))
    assert len(r.read_gltf(p).faces) == 1
    # accessor without bufferView -> zeros
    doc["accessors"][0].pop("bufferView")
    assert r._read_accessor(doc, [blob], 0) == [(0, 0, 0)] * 4
    doc["accessors"][1].pop("bufferView")
    assert r._read_accessor(doc, [blob], 1) == [0] * 6


def test_glb(tmp_path):
    doc, blob = _doc(4, [0, 1, 2, 0, 2, 3], uri=None)
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\0" * (-len(blob) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    data = struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body
    p = tmp_path / "a.glb"
    p.write_bytes(data)
    assert len(r.read_gltf(p).faces) == 1


def test_no_scenes_children_and_names(tmp_path):
    doc, _ = _doc(4, [0, 1, 2, 0, 2, 3])
    doc["nodes"] = [{"children": [1]}, {"mesh": 0}]
    p = tmp_path / "a.gltf"
    p.write_text(json.dumps(doc))
    sc = r.read_gltf(p)
    assert sc.faces[0].name == "node1"
