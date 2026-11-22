"""Inspector UI of a ScheduleConstant turned into a ScheduleRuleset: a prioritised rule list plus a calendar.

Each rule = hour range + value, weekday toggles, month checkboxes and a day-of-month grid (all multi-select: click,
or drag across the toggles). List order = priority (top wins). Pure logic: `core/schedule_rules.py`.
"""

from __future__ import annotations

import calendar
import datetime

import bpy
from bpy.props import BoolProperty, BoolVectorProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from ..core import schedule_rules as R
from .widgets import draw_range


class OSMScheduleRule(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", default="Rule")
    hour_from: FloatProperty(name="From", default=0.0, min=0.0, max=24.0, precision=2, description="Start hour")
    hour_to: FloatProperty(name="To", default=24.0, min=0.0, max=24.0, precision=2, description="End hour")
    value: FloatProperty(name="Value", default=1.0, description="Schedule value between From and To")
    use_outside: BoolProperty(
        name="Outside",
        description="Value outside the hour range (off: the schedule's default value)",
    )
    outside: FloatProperty(name="Outside", default=0.0)
    weekdays: BoolVectorProperty(name="Weekdays", size=7, default=(True,) * 7)
    months: BoolVectorProperty(name="Months", size=12, default=(True,) * 12)
    days: BoolVectorProperty(name="Days of the Month", size=31, default=(True,) * 31)
    view_month: IntProperty(name="Calendar Month", min=1, max=12, default=1)


# Absolute dates of the year. The calendar shows the EFFECTIVE state of every date (weekday / month / day pattern,
# minus excluded dates, plus included dates), so pattern and calendar always agree. Editing a day stores the
# difference to the pattern as a bit mask per month (`inc_MM` / `exc_MM`). Blender arrays hold at most 32 items,
# so there is one 31-day vector per month (dates_01 .. dates_12) with get / set callbacks.
def date_prop(month: int) -> str:
    return f"dates_{month:02d}"


def _bit(r, key: str, month: int, day: int) -> bool:
    return bool(int(r.get(f"{key}_{month:02d}", 0)) >> (day - 1) & 1)


def _put(r, key: str, month: int, day: int, on: bool) -> None:
    name = f"{key}_{month:02d}"
    v = int(r.get(name, 0))
    r[name] = (v | (1 << (day - 1))) if on else (v & ~(1 << (day - 1)))


def pattern_has(r, month: int, day: int) -> bool:
    return (
        day <= calendar.monthrange(R.YEAR, month)[1]
        and bool(r.months[month - 1])
        and bool(r.days[day - 1])
        and bool(r.weekdays[datetime.date(R.YEAR, month, day).weekday()])
    )


def is_date(r, month: int, day: int) -> bool:
    """True when the rule applies on that date."""
    return _bit(r, "inc", month, day) or (pattern_has(r, month, day) and not _bit(r, "exc", month, day))


def set_date(r, month: int, day: int, on: bool) -> None:
    """Make the rule apply (or not) on that date by storing the difference to the pattern."""
    if day > calendar.monthrange(R.YEAR, month)[1]:
        return
    pat = pattern_has(r, month, day)
    _put(r, "inc", month, day, on and not pat)
    _put(r, "exc", month, day, pat and not on)


def _overrides(r, key: str) -> tuple:
    return tuple((m, d) for m in range(1, 13) for d in range(1, 32) if _bit(r, key, m, d))


def included_dates(r) -> tuple:
    return _overrides(r, "inc")


def excluded_dates(r) -> tuple:
    return _overrides(r, "exc")


def effective_count(r) -> int:
    return sum(is_date(r, m, d) for m in range(1, 13) for d in range(1, calendar.monthrange(R.YEAR, m)[1] + 1))


def clear_overrides(r) -> None:
    for m in range(1, 13):
        r[f"inc_{m:02d}"] = 0
        r[f"exc_{m:02d}"] = 0


def _make_dates_prop(month: int):
    def get(self):
        return [is_date(self, month, d) for d in range(1, 32)]

    def set_(self, values):
        for d in range(1, 32):
            if bool(values[d - 1]) != is_date(self, month, d):
                set_date(self, month, d, bool(values[d - 1]))

    return BoolVectorProperty(name=f"Dates {R.MONTH_NAMES[month - 1]}", size=31, get=get, set=set_)


for _m in range(1, 13):
    OSMScheduleRule.__annotations__[date_prop(_m)] = _make_dates_prop(_m)


def to_rule(r: OSMScheduleRule) -> R.Rule:
    return R.Rule(
        r.name,
        round(r.hour_from, 5),  # drop single-precision noise
        round(r.hour_to, 5),
        round(r.value, 5),
        round(r.outside, 5) if r.use_outside else None,
        tuple(bool(x) for x in r.weekdays),
        tuple(bool(x) for x in r.months),
        tuple(bool(x) for x in r.days),
        included_dates(r),
        excluded_dates(r),
    )


def export_rules(item) -> str:
    """The item's rule list as the schema's JSON ("" without rules: a plain constant schedule)."""
    return R.dumps([to_rule(r) for r in item.rules]) if len(item.rules) else ""


def summary(r: OSMScheduleRule) -> str:
    parts = [f"{r.hour_from:g}-{r.hour_to:g} h = {r.value:g}"]
    wd = sum(r.weekdays)
    if wd < 7:
        parts.append(f"{wd} wd")
    mo = sum(r.months)
    if mo < 12:
        parts.append(f"{mo} mo")
    dy = sum(r.days)
    if dy < 31:
        parts.append(f"{dy} d")
    inc, exc = len(included_dates(r)), len(excluded_dates(r))
    if inc or exc:
        parts.append(" ".join(x for x in (f"+{inc}" if inc else "", f"-{exc}" if exc else "") if x) + " dates")
    return ", ".join(parts)


def _item(context):
    from .inspector import get_item

    insp = context.scene.osm_inspector
    if insp.type != "ScheduleConstant" or not insp.object:
        return None
    return get_item(context.scene, insp.type, insp.object)


class OSM_UL_schedule_rules(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=f"{index + 1}")
        row.prop(item, "name", text="", emboss=False)
        row.label(text=summary(item))


class _RuleOp:
    @classmethod
    def poll(cls, context):
        return _item(context) is not None


class SCENE_OT_osm_rule_add(_RuleOp, bpy.types.Operator):
    bl_idname = "scene.osm_rule_add"
    bl_label = "Add Rule"
    bl_description = "Add a rule at the end of the list (lowest priority)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        item = _item(context)
        rule = item.rules.add()
        rule.name = f"Rule {len(item.rules)}"
        item.rule_index = len(item.rules) - 1
        return {"FINISHED"}


class SCENE_OT_osm_rule_remove(_RuleOp, bpy.types.Operator):
    bl_idname = "scene.osm_rule_remove"
    bl_label = "Remove Rule"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        item = _item(context)
        return item is not None and 0 <= item.rule_index < len(item.rules)

    def execute(self, context):
        item = _item(context)
        item.rules.remove(item.rule_index)
        item.rule_index = max(0, min(item.rule_index, len(item.rules) - 1))
        return {"FINISHED"}


class SCENE_OT_osm_rule_move(_RuleOp, bpy.types.Operator):
    bl_idname = "scene.osm_rule_move"
    bl_label = "Move Rule"
    bl_description = "Change the priority: the top rule wins"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(items=[("UP", "Up", ""), ("DOWN", "Down", "")])

    def execute(self, context):
        item = _item(context)
        i = item.rule_index
        j = i - 1 if self.direction == "UP" else i + 1
        if 0 <= i < len(item.rules) and 0 <= j < len(item.rules):
            item.rules.move(i, j)
            item.rule_index = j
        return {"FINISHED"}


class SCENE_OT_osm_rule_select(_RuleOp, bpy.types.Operator):
    bl_idname = "scene.osm_rule_select"
    bl_label = "Select"
    bl_options = {"REGISTER", "UNDO"}

    what: EnumProperty(items=[("WEEKDAYS", "Weekdays", ""), ("MONTHS", "Months", ""), ("DAYS", "Days", "")])
    mode: EnumProperty(
        items=[
            ("ALL", "All", "Select all"),
            ("NONE", "None", "Deselect all"),
            ("INVERT", "Invert", "Invert the selection"),
            ("WORKDAYS", "Mon-Fri", "Monday to Friday"),
            ("WEEKEND", "Sat-Sun", "Saturday and Sunday"),
        ]
    )

    def execute(self, context):
        item = _item(context)
        if not 0 <= item.rule_index < len(item.rules):
            return {"CANCELLED"}
        arr = getattr(item.rules[item.rule_index], self.what.lower())
        for i in range(len(arr)):
            arr[i] = {
                "ALL": True,
                "NONE": False,
                "INVERT": not arr[i],
                "WORKDAYS": i < 5,
                "WEEKEND": i >= 5,
            }[self.mode]
        return {"FINISHED"}


class SCENE_OT_osm_rule_dates(_RuleOp, bpy.types.Operator):
    """Navigation and bulk selection of the year calendar of the active rule."""

    bl_idname = "scene.osm_rule_dates"
    bl_label = "Dates"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    mode: EnumProperty(
        items=[
            ("PREV", "Previous Month", ""),
            ("NEXT", "Next Month", ""),
            ("GOTO", "First Change", "Show the month of the first date changed from the pattern"),
            ("WEEK", "Week", "Toggle the whole week"),
            ("MONTH_ON", "Select Month", "Select every day of the shown month"),
            ("MONTH_OFF", "Clear Month", "Deselect every day of the shown month"),
            ("CLEAR", "Clear All", "Deselect every date of the year (months off, no extra dates)"),
            ("RESET", "Reset Dates", "Forget the dates added / removed by hand: back to the pattern"),
        ]
    )
    ordinal: IntProperty(description="Monday of the week (date ordinal) for mode Week")

    def execute(self, context):
        item = _item(context)
        if not 0 <= item.rule_index < len(item.rules):
            return {"CANCELLED"}
        rule = item.rules[item.rule_index]
        m = rule.view_month
        if self.mode == "PREV":
            rule.view_month = 12 if m == 1 else m - 1
        elif self.mode == "NEXT":
            rule.view_month = 1 if m == 12 else m + 1
        elif self.mode == "GOTO":
            sel = included_dates(rule) + excluded_dates(rule)
            rule.view_month = min(sel)[0] if sel else datetime.date.today().month
        elif self.mode == "WEEK":
            days = [datetime.date.fromordinal(self.ordinal + i) for i in range(7)]
            on = not all(is_date(rule, d.month, d.day) for d in days)
            for d in days:
                set_date(rule, d.month, d.day, on)
        elif self.mode in ("MONTH_ON", "MONTH_OFF"):
            for d in range(1, calendar.monthrange(R.YEAR, m)[1] + 1):
                set_date(rule, m, d, self.mode == "MONTH_ON")
        elif self.mode == "CLEAR":
            for i in range(12):
                rule.months[i] = False
            clear_overrides(rule)
        elif self.mode == "RESET":
            clear_overrides(rule)
        return {"FINISHED"}


def _dates_op(layout, mode: str, **kw):
    text = kw.pop("text", "")
    op = layout.operator("scene.osm_rule_dates", text=text, **kw)
    op.mode = mode
    return op


def _plain(layout):
    """Plain button layout like the Blender calendar example: no property split / decorators (the Inspector
    panel turns property split on, which would turn every toggle into a label + button pair)."""
    layout.use_property_split = False
    layout.use_property_decorate = False
    return layout


def _draw_year_calendar(layout, rule) -> None:
    """Month view of the year (weekday aligned like a wall calendar). It shows the effective dates of the rule, so
    it follows the weekday / month / day pattern; click or drag to add / remove dates."""
    layout = _plain(layout)
    m = rule.view_month
    split = layout.split(factor=0.1, align=True)
    _dates_op(split, "GOTO", icon="RECOVER_LAST")
    header = split.split(factor=0.7)
    row = header.row()
    row.label(text=calendar.month_name[m].upper())
    row.label(text=str(R.YEAR))
    row = header.row(align=True)
    _dates_op(row, "PREV", text="<")
    _dates_op(row, "NEXT", text=">")

    first = datetime.date(R.YEAR, m, 1)
    start = first - datetime.timedelta(days=first.weekday())  # Monday of the first shown week
    grid = layout.column(align=True)  # one aligned column: rows touch each other, no gaps
    for r in range(7):
        row = grid.row(align=True)
        for c in range(8):
            col = row.column(align=True)
            if r == 0:
                col.label(text="#" if c == 0 else calendar.day_abbr[c - 1].upper())
                continue
            monday = start + datetime.timedelta(days=(r - 1) * 7)
            if c == 0:
                _dates_op(col, "WEEK", text=f"w{monday.isocalendar()[1]}", emboss=False).ordinal = monday.toordinal()
                continue
            d = monday + datetime.timedelta(days=c - 1)
            col.prop(rule, date_prop(d.month), index=d.day - 1, text=str(d.day), toggle=True, emboss=d.month == m)
    row = layout.row(align=True)
    _dates_op(row, "MONTH_ON", text="Month")
    _dates_op(row, "MONTH_OFF", text="Clear Month")
    _dates_op(row, "RESET", text="Reset")
    _dates_op(row, "CLEAR", text="Clear All")
    layout.label(text=f"Weekdays follow {R.YEAR}; w# toggles a week; Reset = back to the pattern")


def _draw_days_of_month(layout, rule) -> None:
    """Day-of-month toggles in the calendar's cell layout (7 equal columns, no spacing)."""
    grid = _plain(layout).column(align=True)
    for r in range(5):
        row = grid.row(align=True)
        for c in range(7):
            col = row.column(align=True)
            i = r * 7 + c
            if i < 31:
                col.prop(rule, "days", index=i, text=str(i + 1), toggle=True)
            else:
                col.label(text="")


def _buttons(layout, what: str, modes):
    row = layout.row(align=True)
    for mode, label in modes:
        op = row.operator("scene.osm_rule_select", text=label)
        op.what, op.mode = what, mode


_BASIC = (("ALL", "All"), ("NONE", "None"), ("INVERT", "Invert"))


def draw_rules(layout, item) -> None:
    """The rule list + the calendar of the active rule (called from the Inspector for a ScheduleConstant)."""
    layout.separator()
    layout.label(text="Rules (top = highest priority)", icon="SORTTIME")
    row = layout.row()
    row.template_list("OSM_UL_schedule_rules", "", item, "rules", item, "rule_index", rows=4)
    col = row.column(align=True)
    col.operator("scene.osm_rule_add", text="", icon="ADD")
    col.operator("scene.osm_rule_remove", text="", icon="REMOVE")
    col.separator()
    col.operator("scene.osm_rule_move", text="", icon="TRIA_UP").direction = "UP"
    col.operator("scene.osm_rule_move", text="", icon="TRIA_DOWN").direction = "DOWN"
    if not len(item.rules):
        layout.label(text="No rules: constant schedule (Value above)", icon="INFO")
        return
    if not 0 <= item.rule_index < len(item.rules):
        return
    rule = item.rules[item.rule_index]
    col = layout.column(align=True)
    col.prop(rule, "name")
    draw_range(col, rule, "hour_from", "hour_to", "Hours")
    col.prop(rule, "value")
    row = col.row(align=True)
    row.prop(rule, "use_outside")
    sub = row.row(align=True)
    sub.enabled = rule.use_outside
    sub.prop(rule, "outside", text="")
    if not rule.use_outside:
        col.label(text="Outside the hours: the schedule's default Value")

    header, body = layout.panel("osm_rule_weekdays", default_closed=False)
    header.label(text=f"Weekdays ({sum(rule.weekdays)}/7)")
    if body is not None:
        row = _plain(body).row(align=True)
        for i, name in enumerate(R.WEEKDAY_NAMES):
            row.prop(rule, "weekdays", index=i, text=name, toggle=True)
        _buttons(body, "WEEKDAYS", (("WORKDAYS", "Mon-Fri"), ("WEEKEND", "Sat-Sun"), *_BASIC))

    header, body = layout.panel("osm_rule_months", default_closed=False)
    header.label(text=f"Months ({sum(rule.months)}/12)")
    if body is not None:
        grid = _plain(body).grid_flow(row_major=True, columns=4, even_columns=True, align=True)
        for i, name in enumerate(R.MONTH_NAMES):
            grid.prop(rule, "months", index=i, text=name)
        _buttons(body, "MONTHS", _BASIC)

    header, body = layout.panel("osm_rule_days", default_closed=True)
    header.label(text=f"Days of the Month ({sum(rule.days)}/31)")
    if body is not None:
        _draw_days_of_month(body, rule)
        body.label(text="Click or drag across days to multi-select")
        _buttons(body, "DAYS", _BASIC)

    header, body = layout.panel("osm_rule_dates", default_closed=False)
    header.label(text=f"Dates of the Year ({effective_count(rule)} days)")
    if body is not None:
        _draw_year_calendar(body, rule)


CLASSES = (
    OSMScheduleRule,
    OSM_UL_schedule_rules,
    SCENE_OT_osm_rule_add,
    SCENE_OT_osm_rule_remove,
    SCENE_OT_osm_rule_move,
    SCENE_OT_osm_rule_select,
    SCENE_OT_osm_rule_dates,
)
