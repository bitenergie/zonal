"""Create Standard Building Shapes: Rectangular, L, T, U, H and Courtyard floor plans.

Ports of reference/user_scripts/Create Standard Building Shapes/*.rb. Each plan is a table: named corner points plus the
spaces (suffix, corner names). The perimeter variants offset the corners inwards by the perimeter zone depth.
"""

from __future__ import annotations

from ...core.scripts_core import ArgDef, ScriptContext, UserScript, register

CATEGORY = "Create Standard Building Shapes"
EPS = 1e-4


def _f(key, label, default):
    return ArgDef(key, label, "float", default)


def _common_args():
    return [
        _f("num_floors", "Number of Floors", 1.0),
        _f("floor_to_floor_height", "Floor to Floor Height (m)", 3.8),
        _f("plenum_height", "Plenum Height (m)", 1.0),
        _f("perimeter_zone_depth", "Perimeter Zone Depth (m)", 4.57),
    ]


def _positive(a, key, label):
    return None if a[key] > EPS else f"{label} must be greater than 0."


def _between(a, key, label, upper):
    return None if EPS < a[key] < upper - EPS else f"{label} must be greater than 0 and less than {upper}m."


class FloorPlan(UserScript):
    """Base: subclasses give `args`, `check(a)`, `corners(a)`, `perimeter(a)`, `minimal` and `perimeter_spaces`."""

    category = CATEGORY
    creates_geometry = True
    args: list = []
    depth_factor = 2  # Courtyard needs 4 perimeter zones across

    def arguments(self, model):
        return self.args + _common_args()

    # to override
    def shortest_side(self, a) -> float:
        raise NotImplementedError

    def validate(self, a) -> str | None:
        raise NotImplementedError

    def corners(self, a) -> dict:
        raise NotImplementedError

    def inner(self, a, p, d) -> dict:
        raise NotImplementedError

    minimal: list = []
    perimeter_spaces: list = []

    def check(self, a) -> str | None:
        for key, label in (("num_floors", "Number of floors"), ("floor_to_floor_height", "Floor to floor height")):
            if e := _positive(a, key, label):
                return e
        if a["plenum_height"] < 0:
            return "Plenum height must be greater than or equal to 0."
        if e := self.validate(a):
            return e
        shortest = self.shortest_side(a)
        d = a["perimeter_zone_depth"]
        if d < 0 or self.depth_factor * d >= shortest - EPS:
            return (
                f"Perimeter zone depth must be greater than or equal to 0 and less than "
                f"{shortest / self.depth_factor}m."
            )
        return None

    def run(self, model, args, ctx: ScriptContext) -> bool:
        import openstudio

        osm = openstudio.model
        if err := self.check(args):
            ctx.error(err)
            return False
        d, height = args["perimeter_zone_depth"], args["floor_to_floor_height"]
        pts = self.corners(args)
        if d > 0:
            pts.update(self.inner(args, pts, d))
            layout = self.perimeter_spaces
        else:
            layout = self.minimal
        floors = int(args["num_floors"])  # Ruby: (0..num_floors-1)
        total = max(1, floors * len(layout))
        done = 0
        for floor in range(floors):
            z = height * floor
            story = osm.BuildingStory(model)
            story.setNominalFloortoFloorHeight(height)
            story.setName(f"Story {floor + 1}")
            for suffix, names in layout:
                vec = openstudio.Point3dVector()
                for n in names:
                    vec.append(openstudio.Point3d(pts[n][0], pts[n][1], z))
                space = osm.Space.fromFloorPrint(vec, height, model).get()
                x0, y0 = pts[names[0]]
                space.changeTransformation(openstudio.Transformation.translation(openstudio.Vector3d(x0, y0, z)))
                space.setBuildingStory(story)
                space.setName(f"Story {floor + 1} {suffix}")
                done += 1
                ctx.progress(done / total, "Creating Spaces")
            story.setNominalZCoordinate(z)
        ctx.info(f"Created {floors} stories with {floors * len(layout)} spaces.")
        return True


def _off(p, dx, dy):
    return (p[0] + dx, p[1] + dy)


def _shift(pts, spec, d):
    """spec: {new_name: (corner, sx, sy)} -> corners moved by (sx*d, sy*d)."""
    return {n: _off(pts[c], sx * d, sy * d) for n, (c, sx, sy) in spec.items()}


@register
class RectangularFloorPlan(FloorPlan):
    name = "Set Rectangular Floor Plan"
    description = "Rectangular building with optional perimeter / core zoning, stacked in stories."
    args = [_f("length", "Building Length (m)", 40.0), _f("width", "Building Width (m)", 20.0)]
    minimal = [("Core Space", ["sw", "nw", "ne", "se"])]
    perimeter_spaces = [
        ("West Perimeter Space", ["sw", "nw", "pnw", "psw"]),
        ("North Perimeter Space", ["nw", "ne", "pne", "pnw"]),
        ("East Perimeter Space", ["ne", "se", "pse", "pne"]),
        ("South Perimeter Space", ["se", "sw", "psw", "pse"]),
        ("Core Space", ["psw", "pnw", "pne", "pse"]),
    ]

    def validate(self, a):
        return _positive(a, "length", "Length") or _positive(a, "width", "Width")

    def shortest_side(self, a):
        return min(a["length"], a["width"])

    def corners(self, a):
        L, W = a["length"], a["width"]
        return {"nw": (0, W), "ne": (L, W), "se": (L, 0), "sw": (0, 0)}

    def inner(self, a, p, d):
        return _shift(p, {"pnw": ("nw", 1, -1), "pne": ("ne", -1, -1), "pse": ("se", -1, 1), "psw": ("sw", 1, 1)}, d)


@register
class LShapeFloorPlan(FloorPlan):
    name = "Set L-Shape Floor Plan"
    description = "L-shaped building with optional perimeter / core zoning, stacked in stories."
    args = [
        _f("length", "Building Length (m)", 40.0),
        _f("width", "Building Width (m)", 40.0),
        _f("lower_end_width", "Lower End Width (m)", 20.0),
        _f("upper_end_length", "Upper End Length (m)", 20.0),
    ]
    minimal = [("West Space", ["sw", "nw", "une", "usw"]), ("East Space", ["sw", "usw", "lne", "se"])]
    perimeter_spaces = [
        ("West Perimeter Space", ["sw", "nw", "pnw", "plsw"]),
        ("North Upper Perimeter Space", ["nw", "une", "pune", "pnw"]),
        ("East Upper Perimeter Space", ["une", "usw", "pusw", "pune"]),
        ("North Lower Perimeter Space", ["usw", "lne", "plne", "pusw"]),
        ("East Lower Perimeter Space", ["lne", "se", "pse", "plne"]),
        ("South Perimeter Space", ["se", "sw", "plsw", "pse"]),
        ("West Core Space", ["plsw", "pnw", "pune", "pusw"]),
        ("East Core Space", ["pusw", "plne", "pse", "plsw"]),
    ]

    def validate(self, a):
        return (
            _positive(a, "length", "Length")
            or _positive(a, "width", "Width")
            or _between(a, "lower_end_width", "Lower end width", a["width"])
            or _between(a, "upper_end_length", "Upper end length", a["length"])
        )

    def shortest_side(self, a):
        return min(a["lower_end_width"], a["upper_end_length"])

    def corners(self, a):
        L, W, lw, ul = a["length"], a["width"], a["lower_end_width"], a["upper_end_length"]
        return {"nw": (0, W), "une": (ul, W), "usw": (ul, lw), "lne": (L, lw), "se": (L, 0), "sw": (0, 0)}

    def inner(self, a, p, d):
        return _shift(
            p,
            {
                "pnw": ("nw", 1, -1),
                "pune": ("une", -1, -1),
                "pusw": ("usw", -1, -1),
                "plne": ("lne", -1, -1),
                "pse": ("se", -1, 1),
                "plsw": ("sw", 1, 1),
            },
            d,
        )


@register
class TShapeFloorPlan(FloorPlan):
    name = "Set T-Shape Floor Plan"
    description = "T-shaped building with optional perimeter / core zoning, stacked in stories."
    args = [
        _f("length", "Building Length (m)", 40.0),
        _f("width", "Building Width (m)", 40.0),
        _f("upper_end_width", "Upper End Width (m)", 20.0),
        _f("lower_end_length", "Lower End Length (m)", 20.0),
        _f("left_end_offset", "Left End Offset (m)", 10.0),
    ]
    minimal = [
        ("North Space", ["usw", "unw", "une", "use", "lnw", "lne"]),
        ("South Space", ["lsw", "lne", "lnw", "lse"]),
    ]
    perimeter_spaces = [
        ("West Lower Perimeter Space", ["lsw", "lne", "plne", "plsw"]),
        ("South Upper Left Perimeter Space", ["lne", "usw", "pusw", "plne"]),
        ("West Upper Perimeter Space", ["usw", "unw", "punw", "pusw"]),
        ("North Perimeter Space", ["unw", "une", "pune", "punw"]),
        ("East Upper Perimeter Space", ["une", "use", "puse", "pune"]),
        ("South Upper Right Perimeter Space", ["use", "lnw", "plnw", "puse"]),
        ("East Lower Perimeter Space", ["lnw", "lse", "plse", "plnw"]),
        ("South Lower Perimeter Space", ["lse", "lsw", "plsw", "plse"]),
        ("North Core Space", ["pusw", "punw", "pune", "puse", "plnw", "plne"]),
        ("South Core Space", ["plsw", "plne", "plnw", "plse"]),
    ]

    def validate(self, a):
        return (
            _positive(a, "length", "Length")
            or _positive(a, "width", "Width")
            or _between(a, "upper_end_width", "Upper end width", a["width"])
            or _between(a, "lower_end_length", "Lower end length", a["length"])
            or _between(a, "left_end_offset", "Left end offset", a["length"] - a["lower_end_length"])
        )

    def shortest_side(self, a):
        return min(a["length"], a["width"], a["upper_end_width"], a["lower_end_length"])

    def corners(self, a):
        L, W, uw, ll, off = a["length"], a["width"], a["upper_end_width"], a["lower_end_length"], a["left_end_offset"]
        return {
            "lne": (off, W - uw),
            "usw": (0, W - uw),
            "unw": (0, W),
            "une": (L, W),
            "use": (L, W - uw),
            "lnw": (off + ll, W - uw),
            "lse": (off + ll, 0),
            "lsw": (off, 0),
        }

    def inner(self, a, p, d):
        return _shift(
            p,
            {
                "plne": ("lne", 1, 1),
                "pusw": ("usw", 1, 1),
                "punw": ("unw", 1, -1),
                "pune": ("une", -1, -1),
                "puse": ("use", -1, 1),
                "plnw": ("lnw", -1, 1),
                "plse": ("lse", -1, 1),
                "plsw": ("lsw", 1, 1),
            },
            d,
        )


@register
class UShapeFloorPlan(FloorPlan):
    name = "Set U-Shape Floor Plan"
    description = "U-shaped building with optional perimeter / core zoning, stacked in stories."
    args = [
        _f("length", "Building Length (m)", 40.0),
        _f("left_width", "Left Width (m)", 40.0),
        _f("right_width", "Right Width (m)", 40.0),
        _f("left_end_length", "Left End Length (m)", 15.0),
        _f("right_end_length", "Right End Length (m)", 15.0),
        _f("left_end_offset", "Left End Offset (m)", 25.0),
    ]
    minimal = [
        ("West Space", ["lsw", "lnw", "lne", "usw"]),
        ("South Space", ["lsw", "usw", "use", "lse"]),
        ("East Space", ["use", "rnw", "rne", "lse"]),
    ]
    perimeter_spaces = [
        ("West Left Perimeter Space", ["lsw", "lnw", "plnw", "plsw"]),
        ("North Left Perimeter Space", ["lnw", "lne", "plne", "plnw"]),
        ("East Left Perimeter Space", ["lne", "usw", "pusw", "plne"]),
        ("North Lower Perimeter Space", ["usw", "use", "puse", "pusw"]),
        ("West Right Perimeter Space", ["use", "rnw", "prnw", "puse"]),
        ("North Right Perimeter Space", ["rnw", "rne", "prne", "prnw"]),
        ("East Right Perimeter Space", ["rne", "lse", "plse", "prne"]),
        ("South Lower Perimeter Space", ["lse", "lsw", "plsw", "plse"]),
        ("West Core Space", ["plsw", "plnw", "plne", "pusw"]),
        ("South Core Space", ["pusw", "puse", "plse", "plsw"]),
        ("East Core Space", ["puse", "prnw", "prne", "plse"]),
    ]

    def validate(self, a):
        L, lw, off = a["length"], a["left_width"], a["left_end_offset"]
        if (
            e := _positive(a, "length", "Length")
            or _positive(a, "left_width", "Left width")
            or _between(a, "left_end_length", "Left end length", L)
        ):
            return e
        if not EPS < a["right_end_length"] < L - a["left_end_length"] - EPS:
            return f"Right end length must be greater than 0 and less than {L - a['left_end_length']}m."
        if not EPS < off < lw - EPS:
            return f"Left end offset must be greater than 0 and less than {lw}m."
        if a["right_width"] <= lw - off - EPS:
            return f"Right width must be greater than {lw - off}m."
        return None

    def shortest_side(self, a):
        return min(
            a["length"] / 2,
            a["left_width"],
            a["right_width"],
            a["left_end_length"],
            a["right_end_length"],
            a["left_width"] - a["left_end_offset"],
        )

    def corners(self, a):
        L, lw, rw = a["length"], a["left_width"], a["right_width"]
        le, re, off = a["left_end_length"], a["right_end_length"], a["left_end_offset"]
        return {
            "lnw": (0, lw),
            "lne": (le, lw),
            "usw": (le, lw - off),
            "use": (L - re, lw - off),
            "rnw": (L - re, rw),
            "rne": (L, rw),
            "lse": (L, 0),
            "lsw": (0, 0),
        }

    def inner(self, a, p, d):
        return _shift(
            p,
            {
                "plnw": ("lnw", 1, -1),
                "plne": ("lne", -1, -1),
                "pusw": ("usw", -1, -1),
                "puse": ("use", 1, -1),
                "prnw": ("rnw", 1, -1),
                "prne": ("rne", -1, -1),
                "plse": ("lse", -1, 1),
                "plsw": ("lsw", 1, 1),
            },
            d,
        )


@register
class HShapeFloorPlan(FloorPlan):
    name = "Set H-Shape Floor Plan"
    description = "H-shaped building with optional perimeter / core zoning, stacked in stories."
    args = [
        _f("length", "Building Length (m)", 40.0),
        _f("left_width", "Left Width (m)", 40.0),
        _f("center_width", "Center Width (m)", 10.0),
        _f("right_width", "Right Width (m)", 40.0),
        _f("left_end_length", "Left End Length (m)", 15.0),
        _f("right_end_length", "Right End Length (m)", 15.0),
        _f("left_upper_end_offset", "Left Upper End Offset (m)", 15.0),
        _f("right_upper_end_offset", "Right Upper End Offset (m)", 15.0),
    ]
    minimal = [
        ("West Space", ["lsw", "lnw", "lne", "cnw", "csw", "lse"]),
        ("Center Space", ["csw", "cnw", "cne", "cse"]),
        ("East Space", ["rsw", "cse", "cne", "rnw", "rne", "rse"]),
    ]
    perimeter_spaces = [
        ("West Left Perimeter Space", ["lsw", "lnw", "plnw", "plsw"]),
        ("North Left Perimeter Space", ["lnw", "lne", "plne", "plnw"]),
        ("East Upper Left Perimeter Space", ["lne", "cnw", "pcnw", "plne"]),
        ("North Center Perimeter Space", ["cnw", "cne", "pcne", "pcnw"]),
        ("West Upper Right Perimeter Space", ["cne", "rnw", "prnw", "pcne"]),
        ("North Right Perimeter Space", ["rnw", "rne", "prne", "prnw"]),
        ("East Right Perimeter Space", ["rne", "rse", "prse", "prne"]),
        ("South Right Perimeter Space", ["rse", "rsw", "prsw", "prse"]),
        ("West Lower Right Perimeter Space", ["rsw", "cse", "pcse", "prsw"]),
        ("South Center Perimeter Space", ["cse", "csw", "pcsw", "pcse"]),
        ("East Lower Left Perimeter Space", ["csw", "lse", "plse", "pcsw"]),
        ("South Left Perimeter Space", ["lse", "lsw", "plsw", "plse"]),
        ("West Core Space", ["plsw", "plnw", "plne", "pcnw", "pcsw", "plse"]),
        ("Center Core Space", ["pcsw", "pcnw", "pcne", "pcse"]),
        ("East Core Space", ["prsw", "pcse", "pcne", "prnw", "prne", "prse"]),
    ]

    def validate(self, a):
        L, lw, cw, rw = a["length"], a["left_width"], a["center_width"], a["right_width"]
        if (
            e := _positive(a, "length", "Length")
            or _positive(a, "left_width", "Left width")
            or _positive(a, "right_width", "Right width")
        ):
            return e
        m = min(lw, rw)
        if not EPS < cw < m - EPS:
            return f"Center width must be greater than 0 and less than {m}m."
        if e := _between(a, "left_end_length", "Left end length", L):
            return e
        if not EPS < a["right_end_length"] < L - a["left_end_length"] - EPS:
            return f"Right end length must be greater than 0 and less than {L - a['left_end_length']}m."
        return _between(a, "left_upper_end_offset", "Left upper end offset", lw - cw) or _between(
            a, "right_upper_end_offset", "Right upper end offset", rw - cw
        )

    def shortest_side(self, a):
        return min(
            a["length"] / 2,
            a["left_width"],
            a["center_width"],
            a["right_width"],
            a["left_end_length"],
            a["right_end_length"],
        )

    def corners(self, a):
        L, lw, cw, rw = a["length"], a["left_width"], a["center_width"], a["right_width"]
        le, re = a["left_end_length"], a["right_end_length"]
        lo, ro = a["left_upper_end_offset"], a["right_upper_end_offset"]
        left_origin = (rw - ro) - (lw - lo) if (rw - ro) > (lw - lo) else 0
        lny = lw + left_origin
        cny = lny - lo
        csy = cny - cw
        rny = cny + ro
        return {
            "lnw": (0, lny),
            "lne": (le, lny),
            "lse": (le, left_origin),
            "lsw": (0, left_origin),
            "cnw": (le, cny),
            "cne": (L - re, cny),
            "cse": (L - re, csy),
            "csw": (le, csy),
            "rnw": (L - re, rny),
            "rne": (L, rny),
            "rse": (L, rny - rw),
            "rsw": (L - re, rny - rw),
        }

    def inner(self, a, p, d):
        return _shift(
            p,
            {
                "plnw": ("lnw", 1, -1),
                "plne": ("lne", -1, -1),
                "plse": ("lse", -1, 1),
                "plsw": ("lsw", 1, 1),
                "pcnw": ("cnw", -1, -1),
                "pcne": ("cne", 1, -1),
                "pcse": ("cse", 1, 1),
                "pcsw": ("csw", -1, 1),
                "prnw": ("rnw", 1, -1),
                "prne": ("rne", -1, -1),
                "prse": ("rse", -1, 1),
                "prsw": ("rsw", 1, 1),
            },
            d,
        )


@register
class CourtyardFloorPlan(FloorPlan):
    name = "Set Courtyard Floor Plan"
    description = "Building around a central courtyard, with optional perimeter / core zoning, stacked in stories."
    depth_factor = 4
    args = [
        _f("length", "Building Length (m)", 50.0),
        _f("width", "Building Width (m)", 30.0),
        _f("courtyard_length", "Courtyard Length (m)", 15.0),
        _f("courtyard_width", "Courtyard Width (m)", 5.0),
    ]
    minimal = [
        ("West Space", ["sw", "nw", "cnw", "csw"]),
        ("North Space", ["nw", "ne", "cne", "cnw"]),
        ("East Space", ["ne", "se", "cse", "cne"]),
        ("South Space", ["se", "sw", "csw", "cse"]),
    ]
    perimeter_spaces = [
        ("West Outer Perimeter Space", ["sw", "nw", "onw", "osw"]),
        ("North Outer Perimeter Space", ["nw", "ne", "one", "onw"]),
        ("East Outer Perimeter Space", ["ne", "se", "ose", "one"]),
        ("South Outer Perimeter Space", ["se", "sw", "osw", "ose"]),
        ("West Core Space", ["osw", "onw", "inw", "isw"]),
        ("North Core Space", ["onw", "one", "ine", "inw"]),
        ("East Core Space", ["one", "ose", "ise", "ine"]),
        ("South Core Space", ["ose", "osw", "isw", "ise"]),
        ("West Inner Perimeter Space", ["isw", "inw", "cnw", "csw"]),
        ("North Inner Perimeter Space", ["inw", "ine", "cne", "cnw"]),
        ("East Inner Perimeter Space", ["ine", "ise", "cse", "cne"]),
        ("South Inner Perimeter Space", ["ise", "isw", "csw", "cse"]),
    ]

    def validate(self, a):
        return (
            _positive(a, "length", "Length")
            or _positive(a, "width", "Width")
            or _positive(a, "courtyard_length", "Courtyard length")
            or _positive(a, "courtyard_width", "Courtyard width")
        )

    def shortest_side(self, a):
        return min(a["length"], a["width"])

    def check(self, a):
        if e := super().check(a):
            return e
        d4 = 4 * a["perimeter_zone_depth"]
        if a["courtyard_length"] >= a["length"] - d4 - EPS:
            return f"Courtyard length must be less than {a['length'] - d4}m."
        if a["courtyard_width"] >= a["width"] - d4 - EPS:
            return f"Courtyard width must be less than {a['width'] - d4}m."
        return None

    def corners(self, a):
        L, W, cl, cw = a["length"], a["width"], a["courtyard_length"], a["courtyard_width"]
        x0, y0 = (L - cl) / 2, (W - cw) / 2
        return {
            "nw": (0, W),
            "ne": (L, W),
            "se": (L, 0),
            "sw": (0, 0),
            "cnw": (x0, y0 + cw),
            "cne": (x0 + cl, y0 + cw),
            "cse": (x0 + cl, y0),
            "csw": (x0, y0),
        }

    def inner(self, a, p, d):
        return _shift(
            p,
            {
                "onw": ("nw", 1, -1),
                "one": ("ne", -1, -1),
                "ose": ("se", -1, 1),
                "osw": ("sw", 1, 1),
                "inw": ("cnw", -1, 1),
                "ine": ("cne", 1, 1),
                "ise": ("cse", 1, -1),
                "isw": ("csw", -1, -1),
            },
            d,
        )
