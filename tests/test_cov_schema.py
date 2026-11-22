from zonal.core import schema


def test_indexed():
    assert schema.indexed(None) == []
    assert schema.indexed({"1": "b", "0": "a", "x": "c"})[-1] in ("a", "b", "c")
    assert schema.indexed((1, 2)) == [1, 2]
    assert schema.indexed(5) == [5]
