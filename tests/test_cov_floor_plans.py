import itertools

import pytest

from zonal.core import scripts_core as sc

SHAPES = "Create Standard Building Shapes"


@pytest.mark.parametrize("name", sc.scripts(SHAPES))
def test_validation_branches(name):
    script = sc.get(SHAPES, name)
    base = script.defaults(None)
    keys = [a.key for a in script.arguments(None) if a.type in ("float", "int")]
    errors = set()
    for key, val in itertools.product(keys, (0, -1, 1000, 1e-9, 0.5, 5, 20)):
        e = script.check({**base, key: val})
        if e:
            errors.add(e)
    for k1, k2 in itertools.combinations(keys, 2):
        for v1, v2 in itertools.product((0.5, 5, 20, 1000), repeat=2):
            e = script.check({**base, k1: v1, k2: v2})
            if e:
                errors.add(e)
    assert errors
    assert script.check(base) is None
