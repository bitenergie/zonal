"""Surface Matching and Project Loose Geometry (imports bpy)."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from mathutils import Vector

from ..core import schema
from ..core.tools_core import LOOSE_COLLECTION, surface_type_from_normal, unique_name
from ..core.tools_geometry import (
    OverlapIndex,
    auto_subsurface_type,
    cut_polygon_by_polygon,
    match_surfaces,
    project_polygon,
    split_overlap,
)
from ..core.tools_geometry import polygon_area_3d as _area3d
from .ui import sync_subsurface_displace


def loose_collection(create: bool = False):
    col = bpy.data.collections.get(LOOSE_COLLECTION)
    if col is None and create:
        col = bpy.data.collections.new(LOOSE_COLLECTION)
        bpy.context.scene.collection.children.link(col)
    return col


def is_loose(ob) -> bool:
    # ob.users_collection scans every collection (~0.14 ms per object with 2500 objects); the loose collection is small
    col = bpy.data.collections.get(LOOSE_COLLECTION)
    return col is not None and ob.name in col.objects


def _world_polygon(ob, poly):
    mw, me = ob.matrix_world, ob.data
    return [tuple(mw @ me.vertices[i].co) for i in poly.vertices]


def _surface_type(ob, poly) -> str:
    t = ob.get("surfaceType")
    if isinstance(t, str) and t in schema.ALL_SURFACE_TYPES:
        return t
    return surface_type_from_normal((ob.matrix_world.to_3x3() @ poly.normal).normalized().z)


def space_collection(ob) -> str:
    """Name of the space a surface belongs to = its (original) collection; "" outside any space collection."""
    root = bpy.context.scene.collection
    cols = [c.name for c in ob.users_collection if c != root and c.name != LOOSE_COLLECTION]
    return cols[0] if cols else ""


def _space_of(ob) -> str:
    """The space of `ob`: its collection. Only objects outside any collection fall back to a `spaceName` property
    (space shading surfaces name their space that way)."""
    s = space_collection(ob)
    if s:
        return s
    v = ob.get("spaceName")
    return v if isinstance(v, str) else ""


def _single_face_surfaces(scene, types):
    """(object, world polygon) for exported single-face meshes of the given surface types."""
    for ob in scene.objects:
        if (
            ob.type == "MESH"
            and not is_loose(ob)
            and len(ob.data.polygons) == 1
            and _surface_type(ob, ob.data.polygons[0]) in types
        ):
            yield ob, _world_polygon(ob, ob.data.polygons[0])


def _plain(v):
    if hasattr(v, "to_dict"):
        return v.to_dict()
    if hasattr(v, "to_list"):
        return v.to_list()
    return v


def _local_coords(ob, poly):
    inv = ob.matrix_world.inverted()
    return [tuple(inv @ Vector(p)) for p in poly]


def _new_mesh(like, name, coords):
    me = bpy.data.meshes.new(name)
    me.from_pydata(coords, [], [list(range(len(coords)))])
    me.update()
    for m in like.materials:
        me.materials.append(m)
    return me


def _set_world_polygon(ob, poly):
    """Replace the (single face) geometry of `ob`; always a fresh mesh so linked duplicates stay untouched."""
    old = ob.data
    ob.data = _new_mesh(old, old.name, _local_coords(ob, poly))
    if old.users == 0:
        bpy.data.meshes.remove(old)


def _clone_surface(ob, poly, names):
    """New single-face object next to `ob` (same collections, parent, transform and custom properties)."""
    name = unique_name(ob.name, names)
    names.add(name)
    new = bpy.data.objects.new(name, _new_mesh(ob.data, name, _local_coords(ob, poly)))
    for c in ob.users_collection:
        c.objects.link(new)
    new.parent, new.matrix_parent_inverse, new.matrix_world = (
        ob.parent,
        ob.matrix_parent_inverse.copy(),
        ob.matrix_world.copy(),
    )
    for k in ob.keys():
        if not k.startswith("_"):
            new[k] = _plain(ob[k])
    return new


def _cut_crossing(context, want, tol: float) -> int:
    """Split single-face base surfaces that another surface passes completely through (SketchUp's intersect).

    Returns the number of extra pieces created. Surfaces that carry sub surfaces are left alone.
    """
    import numpy as np

    from ..core.tools_geometry import polygon_normal_unit

    items = list(_single_face_surfaces(context.scene, schema.BASE_SURFACE_TYPES))
    if len(items) < 2:
        return 0
    hosts = {str(o.get("surfaceName")) for o in context.scene.objects if o.type == "MESH" and o.get("surfaceName")}
    polys = [p for _o, p in items]
    lo = np.array([np.min(p, axis=0) for p in polys]) - tol
    hi = np.array([np.max(p, axis=0) for p in polys]) + tol
    normals = np.array([polygon_normal_unit(p) for p in polys])
    names = {o.name for o in bpy.data.objects}
    created = 0
    for i, (ob, poly) in enumerate(items):
        if ob.name in hosts:
            continue
        near = np.all(lo <= hi[i], axis=1) & np.all(hi >= lo[i], axis=1) & (np.abs(normals @ normals[i]) < 0.9999)
        near[i] = False
        cands = [j for j in np.nonzero(near)[0].tolist() if want(ob.name) or want(items[j][0].name)]
        pieces = [poly]
        for j in cands:
            pieces = [x for piece in pieces for x in cut_polygon_by_polygon(piece, polys[j], tol)]
        if len(pieces) < 2:
            continue
        _set_world_polygon(ob, pieces[0])
        if ob.get("outsideBoundaryCondition") == "Surface":  # the match no longer fits the smaller piece
            for k in ("outsideBoundaryCondition", "outsideBoundaryConditionObjectName"):
                if k in ob:
                    del ob[k]
        for piece in pieces[1:]:
            new = _clone_surface(ob, piece, names)
            for k in ("outsideBoundaryCondition", "outsideBoundaryConditionObjectName"):
                if k in new:
                    del new[k]
            created += 1
    return created


class OBJECT_OT_osm_match_surfaces(bpy.types.Operator):
    bl_idname = "object.osm_match_surfaces"
    bl_label = "Surface Matching"
    bl_description = (
        "Set boundary condition 'Surface' on facing surfaces of different spaces, or clear it again. "
        "Partly overlapping surfaces are split (2D boolean): the shared part (A and B) is matched, "
        "A not B and B not A stay as separate surfaces"
    )
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        name="Mode",
        items=[
            ("MATCH", "Match", ""),
            ("UNMATCH", "Unmatch", ""),
            ("INTERSECT", "Intersect", "Only split surfaces (where a surface passes through another / overlaps one)"),
        ],
        default="MATCH",
    )
    scope: EnumProperty(
        name="Scope",
        default="SELECTED",
        items=[("SELECTED", "Selection", "Pairs with at least one selected surface"), ("ALL", "Entire Model", "")],
    )
    tolerance: FloatProperty(name="Tolerance", default=1e-3, min=1e-6, unit="LENGTH")
    reset_constructions: BoolProperty(
        name="Reset Constructions",
        default=True,
        description="Remove hard-assigned constructions from matched surfaces so they use the interior default "
        "construction of their space again (as the SketchUp plugin does)",
    )

    def execute(self, context):
        sel = {o.name for o in context.selected_objects}
        want = (lambda n: True) if self.scope == "ALL" else (lambda n: n in sel)
        if self.mode == "UNMATCH":
            n = 0
            for ob in context.scene.objects:
                if ob.type != "MESH" or ob.get("outsideBoundaryCondition") != "Surface":
                    continue
                other = bpy.data.objects.get(ob.get("outsideBoundaryConditionObjectName", ""))
                if not (want(ob.name) or (other is not None and want(other.name))):
                    continue
                for o in (ob, other):
                    if o is not None:
                        for k in ("outsideBoundaryCondition", "outsideBoundaryConditionObjectName"):
                            if k in o:
                                del o[k]
                n += 1
            self.report({"INFO"}, f"Unmatched {n} surfaces")
            return {"FINISHED"}

        pairs = []
        intersect = self.mode == "INTERSECT"
        cut = _cut_crossing(context, want, self.tolerance) if intersect else 0
        items = [
            (ob.name, poly, _space_of(ob)) for ob, poly in _single_face_surfaces(context.scene, schema.SUBSURFACE_TYPES)
        ]
        if not intersect:
            pairs += [p for p in match_surfaces(items, self.tolerance) if want(p[0]) or want(p[1])]

        # base surfaces: partial overlaps are split into  A and B  +  A not B  +  B not A
        names = {o.name for o in bpy.data.objects}
        # `index` is a vectorised broad phase: only facing, coplanar, bbox-overlapping surfaces reach split_overlap.
        # Ids only grow (new pieces are appended), so "later in the list" == "higher id"; removed ids stay dead.
        index = OverlapIndex(self.tolerance)
        work: dict[int, tuple] = {}
        for ob, poly in _single_face_surfaces(context.scene, schema.BASE_SURFACE_TYPES):
            work[index.add(poly)] = (ob, poly, _space_of(ob))
        created = cut
        i = 0
        while i < index.size:
            if i not in work:
                i += 1
                continue
            a, pa, sa = work[i]
            hit = None
            for j in index.candidates(i):
                b, pb, sb = work[j]
                if sa == sb or not (want(a.name) or want(b.name)):
                    continue
                res = split_overlap(pa, pb, self.tolerance)
                if res is not None:
                    hit = j, res
                    break
            if hit is None:
                i += 1
                continue
            j, res = hit
            b, pb, sb = work[j]
            for k in (i, j):
                del work[k]
                index.remove(k)
            ins = []
            for ob, space, polys_and, polys_only in (
                (a, sa, res["and_a"], res["a_only"]),
                (b, sb, res["and_b"], res["b_only"]),
            ):
                keep = ob.name in sel
                objs = []
                if (
                    not polys_only
                    and len(polys_and) == 1
                    and abs(_area3d(polys_and[0]) - _area3d(_world_polygon(ob, ob.data.polygons[0])))
                    <= 1e-4 * _area3d(polys_and[0])
                ):
                    objs.append(ob)  # identical outline: leave the geometry alone
                else:
                    for k, poly in enumerate(polys_and + polys_only):
                        if k == 0:
                            _set_world_polygon(ob, poly)
                            new = ob
                        else:
                            new = _clone_surface(ob, poly, names)
                            created += 1
                            if keep:
                                sel.add(new.name)
                        if k < len(polys_and):
                            objs.append(new)
                        else:
                            if new.get("outsideBoundaryCondition") == "Surface":  # stale match of the larger surface
                                for key in ("outsideBoundaryCondition", "outsideBoundaryConditionObjectName"):
                                    if key in new:
                                        del new[key]
                            wpoly = _world_polygon(new, new.data.polygons[0])
                            work[index.add(wpoly)] = (new, wpoly, space)
                ins.append(objs)
            pairs += [(x.name, y.name) for x, y in zip(*ins)]
        if intersect:
            self.report({"INFO"}, f"Intersected: {created} extra surface piece(s) created")
            return {"FINISHED"}
        for a, b in pairs:
            for x, y in ((a, b), (b, a)):
                ob = bpy.data.objects[x]
                ob["outsideBoundaryCondition"] = "Surface"
                ob["outsideBoundaryConditionObjectName"] = y
                if self.reset_constructions and "constructionName" in ob:
                    del ob["constructionName"]
        msg = f"Matched {len(pairs)} pairs ({2 * len(pairs)} surfaces)"
        if created:
            msg += f"; split surfaces into {created} extra pieces"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class OBJECT_OT_osm_to_loose(bpy.types.Operator):
    bl_idname = "object.osm_to_loose"
    bl_label = "Move to Loose Geometry"
    bl_description = f"Move the selected objects to the '{LOOSE_COLLECTION}' collection (never exported)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        target = loose_collection(create=True)
        for ob in context.selected_objects:
            for c in list(ob.users_collection):
                c.objects.unlink(ob)
            target.objects.link(ob)
        return {"FINISHED"}


class OBJECT_OT_osm_project_loose(bpy.types.Operator):
    bl_idname = "object.osm_project_loose"
    bl_label = "Project Loose Geometry"
    bl_description = (
        "Project faces from the Loose Geometry collection onto the coplanar surfaces of the model, "
        "creating sub surfaces (windows, doors...) in every space they touch"
    )
    bl_options = {"REGISTER", "UNDO"}

    scope: EnumProperty(
        name="Scope",
        default="ALL",
        items=[("SELECTED", "Selection", "Selected loose objects"), ("ALL", "All Loose Geometry", "")],
    )
    surface_type: EnumProperty(
        name="Create",
        default="AUTO",
        items=[("AUTO", "Auto", "Door when touching the bottom edge of a wall, Skylight on a roof, else Fixed Window")]
        + [(t, t, "") for t in schema.SUBSURFACE_TYPES],
    )
    tolerance: FloatProperty(name="Plane Tolerance", default=1e-3, min=1e-6, unit="LENGTH")
    replace: BoolProperty(
        name="Replace Earlier Projections",
        default=True,
        description="Remove sub surfaces previously projected from the same loose face",
    )

    @classmethod
    def poll(cls, context):
        return loose_collection() is not None or any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context):
        # two-way: selected meshes outside the Loose Geometry collection are added to it first (both scopes)
        picked = [o for o in context.selected_objects if o.type == "MESH" and not is_loose(o)]
        if picked:
            target = loose_collection(create=True)
            for o in picked:
                target.objects.link(o)
        loose = loose_collection()
        if loose is None:
            self.report({"WARNING"}, "No Loose Geometry collection and nothing selected")
            return {"CANCELLED"}
        sources = [o for o in loose.all_objects if o.type == "MESH"]
        if self.scope == "SELECTED":
            sources = [o for o in sources if o.select_get()]
        hosts = list(_single_face_surfaces(context.scene, schema.BASE_SURFACE_TYPES))
        multi = sum(
            1 for o in context.scene.objects if o.type == "MESH" and not is_loose(o) and len(o.data.polygons) > 1
        )
        if self.replace:
            src_names = {o.name for o in sources}
            for o in [o for o in bpy.data.objects if str(o.get("osm_source", "")).rsplit(":", 1)[0] in src_names]:
                bpy.data.objects.remove(o, do_unlink=True)
        names = {o.name for o in bpy.data.objects}
        made = ignored = 0
        for src in sources:
            for poly in src.data.polygons:
                wpoly = _world_polygon(src, poly)
                hit = False
                for host, hpoly in hosts:
                    res = project_polygon(wpoly, hpoly, self.tolerance)
                    if res is None:
                        continue
                    hit = True
                    stype = self.surface_type
                    if stype == "AUTO":
                        stype = auto_subsurface_type(res, hpoly, _surface_type(host, host.data.polygons[0]), tol=self.tolerance)
                    name = unique_name(stype, names)
                    names.add(name)
                    me = bpy.data.meshes.new(name)
                    me.from_pydata([tuple(p) for p in res], [], [list(range(len(res)))])
                    me.update()
                    ob = bpy.data.objects.new(name, me)
                    for c in host.users_collection:
                        c.objects.link(ob)
                    ob["surfaceType"] = stype
                    ob["surfaceName"] = host.name
                    ob["osm_source"] = f"{src.name}:{poly.index}"
                    sync_subsurface_displace(ob)
                    made += 1
                ignored += not hit
        msg = f"Created {made} sub surfaces"
        if ignored:
            msg += f", {ignored} loose faces ignored (not on any surface)"
        if multi:
            msg += f"; {multi} multi-face meshes skipped as hosts (use Separate Faces)"
        self.report({"INFO" if made else "WARNING"}, msg)
        return {"FINISHED"} if made else {"CANCELLED"}


CLASSES = (OBJECT_OT_osm_match_surfaces, OBJECT_OT_osm_to_loose, OBJECT_OT_osm_project_loose)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
