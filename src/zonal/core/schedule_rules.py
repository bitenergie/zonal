"""Pure model of a ScheduleRuleset built from a prioritised rule list (no bpy / openstudio).

Each `Rule` has an hour range with a value (`outside` elsewhere, default = the schedule's default value) and a
calendar: weekdays x months x days of the month. Rule order = priority (first = highest). `applications` turns the
calendar into what OpenStudio's ScheduleRule can express: a date range + weekday flags, or a list of specific dates.
"""

from __future__ import annotations

import calendar
import datetime
import json
from dataclasses import dataclass

YEAR = 2009  # OpenStudio's default assumed year (decides which dates fall on which weekday)
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass
class Rule:
    name: str = "Rule"
    start: float = 0.0  # hour 0..24
    end: float = 24.0
    value: float = 1.0
    outside: float | None = None  # None -> the schedule's default value
    weekdays: tuple = (True,) * 7  # Mon..Sun
    months: tuple = (True,) * 12
    days: tuple = (True,) * 31
    dates: tuple = ()  # absolute dates ((month, day), ...) added to the pattern above
    exclude: tuple = ()  # absolute dates taken out of the pattern


def segments(rule: Rule, default: float) -> list[tuple[float, float]]:
    """The rule's day as [(end hour, value)] covering 0..24 (an empty / inverted range means the whole day)."""
    s, e = min(24.0, max(0.0, rule.start)), min(24.0, max(0.0, rule.end))
    if e <= s:
        return [(24.0, rule.value)]
    out = default if rule.outside is None else rule.outside
    segs = []
    if s > 0:
        segs.append((s, out))
    segs.append((e, rule.value))
    if e < 24:
        segs.append((24.0, out))
    return segs


def _runs(flags) -> list[tuple[int, int]]:
    """Consecutive true runs of a 1-based month flag list -> [(first, last)]."""
    runs, first = [], None
    for i, on in enumerate(list(flags) + [False], 1):
        if on and first is None:
            first = i
        elif not on and first is not None:
            runs.append((first, i - 1))
            first = None
    return runs


def applications(rule: Rule, year: int = YEAR) -> list[dict]:
    """[{weekdays, start (m, d) | None, end (m, d) | None, dates [(m, d)]}]; empty when the rule never applies.

    Effective dates = (weekday / month / day-of-month pattern - `exclude`) + `dates` (valid ones only). Without
    exclusions the pattern keeps its compact form (date range + weekday flags) and `dates` is one extra entry."""
    extra = _valid(rule.dates, year)
    if rule.exclude:
        dates = sorted((set(pattern_dates(rule, year)) - set(rule.exclude)) | set(extra))
        return [{"weekdays": (True,) * 7, "start": None, "end": None, "dates": dates}] if dates else []
    apps = _pattern_applications(rule, year)
    if extra:
        apps.append({"weekdays": (True,) * 7, "start": None, "end": None, "dates": extra})
    return apps


def _valid(dates, year: int) -> list[tuple[int, int]]:
    return sorted({(m, d) for m, d in dates if 1 <= m <= 12 and 1 <= d <= calendar.monthrange(year, m)[1]})


def pattern_dates(rule: Rule, year: int = YEAR) -> list[tuple[int, int]]:
    """Every (month, day) matching the weekday / month / day-of-month pattern."""
    return [
        (m, d)
        for m in range(1, 13)
        if rule.months[m - 1]
        for d in range(1, calendar.monthrange(year, m)[1] + 1)
        if rule.days[d - 1] and rule.weekdays[datetime.date(year, m, d).weekday()]
    ]


def effective(rule: Rule, month: int, day: int, year: int = YEAR) -> bool:
    """True when the rule applies on that date (what the calendar shows)."""
    if (month, day) in rule.dates:
        return True
    if (month, day) in rule.exclude or day > calendar.monthrange(year, month)[1]:
        return False
    return rule.months[month - 1] and rule.days[day - 1] and rule.weekdays[datetime.date(year, month, day).weekday()]


def _pattern_applications(rule: Rule, year: int) -> list[dict]:
    if not (any(rule.weekdays) and any(rule.months) and any(rule.days)):
        return []
    if all(rule.days):  # month runs with a date range and weekday flags
        out = []
        for m1, m2 in _runs(rule.months):
            whole = m1 == 1 and m2 == 12
            out.append(
                {
                    "weekdays": tuple(rule.weekdays),
                    "start": None if whole else (m1, 1),
                    "end": None if whole else (m2, calendar.monthrange(year, m2)[1]),
                    "dates": [],
                }
            )
        return out
    dates = pattern_dates(rule, year)
    return [{"weekdays": (True,) * 7, "start": None, "end": None, "dates": dates}] if dates else []


def _flags(values, n: int) -> tuple:
    v = [bool(x) for x in (values or [])][:n]
    return tuple(v + [True] * (n - len(v)))


def dumps(rules: list[Rule]) -> str:
    return json.dumps(
        [
            {
                "name": r.name,
                "from": r.start,
                "to": r.end,
                "value": r.value,
                "outside": r.outside,
                "weekdays": list(r.weekdays),
                "months": list(r.months),
                "days": list(r.days),
                "dates": [list(x) for x in r.dates],
                "exclude": [list(x) for x in r.exclude],
            }
            for r in rules
        ],
        separators=(",", ":"),
    )


def loads(text) -> list[Rule]:
    """Inverse of `dumps` (tolerant: bad input -> [])."""
    try:
        data = json.loads(text) if isinstance(text, str) and text else []
        return [
            Rule(
                name=str(d.get("name") or "Rule"),
                start=float(d.get("from", 0.0)),
                end=float(d.get("to", 24.0)),
                value=float(d.get("value", 1.0)),
                outside=None if d.get("outside") is None else float(d["outside"]),
                weekdays=_flags(d.get("weekdays"), 7),
                months=_flags(d.get("months"), 12),
                days=_flags(d.get("days"), 31),
                dates=tuple((int(m), int(day)) for m, day in d.get("dates", [])),
                exclude=tuple((int(m), int(day)) for m, day in d.get("exclude", [])),
            )
            for d in data
        ]
    except (ValueError, TypeError, AttributeError):
        return []
