from zonal.core import schedule_rules as R


def test_segments():
    assert R.segments(R.Rule(start=8, end=18, value=1), 0.2) == [(8, 0.2), (18, 1), (24, 0.2)]
    assert R.segments(R.Rule(start=0, end=12, value=1, outside=0.5), 0.2) == [(12, 1), (24, 0.5)]
    assert R.segments(R.Rule(start=9, end=9, value=3), 0) == [(24.0, 3)]  # empty range = whole day


def test_applications_month_runs():
    months = tuple(m in (1, 2, 3, 7, 8) for m in range(1, 13))
    apps = R.applications(R.Rule(months=months, weekdays=(True,) * 5 + (False,) * 2))
    assert [(a["start"], a["end"]) for a in apps] == [((1, 1), (3, 31)), ((7, 1), (8, 31))]
    assert apps[0]["weekdays"][5] is False and not apps[0]["dates"]
    assert R.applications(R.Rule())[0]["start"] is None  # whole year


def test_applications_specific_dates_respect_weekdays_and_month_length():
    days = tuple(d in (1, 15, 31) for d in range(1, 32))
    months = tuple(m in (2, 3) for m in range(1, 13))
    (app,) = R.applications(R.Rule(days=days, months=months))
    assert app["dates"] == [(2, 1), (2, 15), (3, 1), (3, 15), (3, 31)]  # no Feb 31
    only_sundays = (False,) * 6 + (True,)
    (app,) = R.applications(R.Rule(days=days, months=months, weekdays=only_sundays))
    assert app["dates"] == [(2, 1), (2, 15), (3, 1), (3, 15)]  # 2009: these are Sundays
    assert R.applications(R.Rule(days=(False,) * 31)) == []
    assert R.applications(R.Rule(weekdays=(False,) * 7)) == []


def test_dumps_loads_roundtrip_and_bad_input():
    rules = [R.Rule("Summer", 6, 20, 0.9, 0.1, (True,) * 5 + (False,) * 2, tuple(m in (6, 7, 8) for m in range(1, 13)))]
    assert R.loads(R.dumps(rules)) == rules
    assert R.loads("") == [] and R.loads("{oops") == [] and R.loads('[{"value":"x"}]') == []


def test_absolute_dates_are_added_to_the_pattern():
    # pattern off (no months): only the absolute dates apply; invalid ones (Feb 29 in 2009, month 13) are dropped
    only = R.Rule(months=(False,) * 12, dates=((12, 25), (1, 1), (2, 29), (13, 1), (12, 25)))
    (app,) = R.applications(only)
    assert app["dates"] == [(1, 1), (12, 25)] and app["weekdays"] == (True,) * 7
    # pattern on + dates: the month-run entry plus one entry with the dates
    both = R.Rule(months=tuple(m == 6 for m in range(1, 13)), dates=((12, 25),))
    apps = R.applications(both)
    assert [a["dates"] for a in apps] == [[], [(12, 25)]] and apps[0]["start"] == (6, 1)
    assert R.loads(R.dumps([both])) == [both]
    assert R.loads('[{"dates":[[1]]}]') == []  # malformed dates: dropped input


def test_excluded_dates_take_dates_out_of_the_pattern():
    june = tuple(m == 6 for m in range(1, 13))
    rule = R.Rule(months=june, exclude=((6, 10), (6, 11), (7, 4)), dates=((12, 25),))
    (app,) = R.applications(rule)  # exclusions force the explicit date list
    assert app["start"] is None and (6, 10) not in app["dates"] and (6, 9) in app["dates"]
    assert (12, 25) in app["dates"] and len(app["dates"]) == 30 - 2 + 1
    assert R.effective(rule, 6, 9) and not R.effective(rule, 6, 10) and R.effective(rule, 12, 25)
    assert not R.effective(rule, 7, 4) and not R.effective(rule, 2, 30)
    assert R.loads(R.dumps([rule])) == [rule]
    weekend = R.Rule(weekdays=(False,) * 5 + (True,) * 2, exclude=((1, 3),))  # Jan 3 2009 is a Saturday
    assert not R.effective(weekend, 1, 3) and R.effective(weekend, 1, 4) and not R.effective(weekend, 1, 5)
    assert R.pattern_dates(weekend)[:3] == [(1, 3), (1, 4), (1, 10)]
    assert R.applications(R.Rule(exclude=((m, d) for m in range(1, 13) for d in range(1, 32)), months=june)) == []
