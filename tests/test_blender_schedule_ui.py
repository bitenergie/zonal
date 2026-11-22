"""In-process tests of blender/schedule_ui (rule list operators, calendar <-> pattern sync, export, drawing)."""

import datetime
import json
from unittest.mock import MagicMock

from conftest import requires_bpy

pytestmark = requires_bpy


def _schedule(bpy):
    from zonal.blender import inspector as bi

    scene = bpy.context.scene
    item = bi.ensure_item(scene, "ScheduleConstant", "Occ")
    scene.osm_inspector.type, scene.osm_inspector.object = "ScheduleConstant", "Occ"
    bpy.ops.scene.osm_rule_add()
    return scene, item, item.rules[0]


def _cal(rule, month):
    """What the calendar shows for a month: the effective flags of the month's days."""
    return list(getattr(rule, f"dates_{month:02d}"))


def test_calendar_follows_the_pattern(clean_scene):
    from zonal.blender import schedule_ui as su

    bpy = clean_scene
    _scene, item, rule = _schedule(bpy)
    assert all(_cal(rule, 6)[:30])  # a new rule applies all year
    rule.months[5] = False  # untick June in the pattern -> the calendar changes at once
    assert not any(_cal(rule, 6))
    assert all(_cal(rule, 7)[:31])
    bpy.ops.scene.osm_rule_select(what="WEEKDAYS", mode="WEEKEND")  # only Sat / Sun
    jan = _cal(rule, 1)
    assert [d + 1 for d, on in enumerate(jan) if on] == [3, 4, 10, 11, 17, 18, 24, 25, 31]  # January 2009
    rule.days[2] = False  # day 3 off in the pattern
    assert not _cal(rule, 1)[2] and _cal(rule, 1)[3]
    assert su.effective_count(rule) == sum(
        1
        for m in range(1, 13)
        for d in range(1, 29 if m == 2 else 31 if m in (4, 6, 9, 11) else 32)
        if rule.months[m - 1] and rule.days[d - 1] and datetime.date(2009, m, d).weekday() >= 5
    )


def test_calendar_edits_become_overrides(clean_scene):
    from zonal.blender import schedule_ui as su

    bpy = clean_scene
    _scene, item, rule = _schedule(bpy)
    arr = rule.dates_12
    arr[24] = False  # Dec 25 off although the pattern has it -> exclusion
    assert su.excluded_dates(rule) == ((12, 25),) and not su.included_dates(rule)
    assert not _cal(rule, 12)[24]
    rule.months[11] = False  # December off in the pattern: the exclusion changes nothing visible
    assert not any(_cal(rule, 12))
    rule.dates_12[0] = True  # Dec 1 on although the pattern is off -> inclusion
    assert su.included_dates(rule) == ((12, 1),) and _cal(rule, 12)[0]
    rule.months[11] = True  # pattern back on: Dec 25 is still excluded, Dec 1 is on in both ways
    assert not _cal(rule, 12)[24] and _cal(rule, 12)[0]
    bpy.ops.scene.osm_rule_dates(mode="RESET")
    assert all(_cal(rule, 12)[:31]) and not su.included_dates(rule) and not su.excluded_dates(rule)


def test_navigation_and_bulk_operators(clean_scene):
    from zonal.blender import schedule_ui as su

    bpy = clean_scene
    _scene, item, rule = _schedule(bpy)
    bpy.ops.scene.osm_rule_dates(mode="PREV")
    assert rule.view_month == 12
    bpy.ops.scene.osm_rule_dates(mode="NEXT")
    bpy.ops.scene.osm_rule_dates(mode="NEXT")
    assert rule.view_month == 2
    bpy.ops.scene.osm_rule_dates(mode="MONTH_OFF")
    assert not any(_cal(rule, 2)) and len(su.excluded_dates(rule)) == 28
    bpy.ops.scene.osm_rule_dates(mode="MONTH_ON")
    assert all(_cal(rule, 2)[:28]) and not su.excluded_dates(rule)

    monday = datetime.date(2009, 2, 2).toordinal()
    bpy.ops.scene.osm_rule_dates(mode="WEEK", ordinal=monday)  # all on -> toggles the week off
    assert [d + 1 for d, on in enumerate(_cal(rule, 2)) if on and d < 28] == [1] + list(range(9, 29))
    bpy.ops.scene.osm_rule_dates(mode="WEEK", ordinal=monday)
    assert all(_cal(rule, 2)[:28])

    su.set_date(rule, 12, 25, False)
    rule.view_month = 5
    bpy.ops.scene.osm_rule_dates(mode="GOTO")
    assert rule.view_month == 12
    bpy.ops.scene.osm_rule_dates(mode="CLEAR")
    assert not any(rule.months) and su.effective_count(rule) == 0


def test_overrides_are_exported_and_summarised(clean_scene):
    from zonal.blender import inspector as bi
    from zonal.blender import schedule_ui as su

    bpy = clean_scene
    scene, item, rule = _schedule(bpy)
    for i in range(12):
        rule.months[i] = i == 5  # June only
    su.set_date(rule, 12, 25, True)  # outside the pattern -> included
    su.set_date(rule, 6, 10, False)  # inside the pattern -> excluded
    data = json.loads(json.loads(bi.export_json(scene))["objects"][0]["fields"]["rules"])[0]
    assert data["dates"] == [[12, 25]] and data["exclude"] == [[6, 10]]
    assert "+1 -1 dates" in su.summary(rule)


def test_draw_rules_with_calendar(clean_scene):
    from zonal.blender import schedule_ui as su

    bpy = clean_scene
    _scene, item, rule = _schedule(bpy)
    lay = MagicMock()
    lay.panel.return_value = (MagicMock(), MagicMock())
    su.draw_rules(lay, item)
    for month in (1, 2, 12):
        rule.view_month = month
        su.draw_rules(lay, item)
    item.rules.clear()
    su.draw_rules(lay, item)  # no rules: hint only
