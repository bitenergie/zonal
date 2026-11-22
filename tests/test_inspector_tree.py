from zonal.core import inspector_tree as T


def _rows():
    return T.flatten(
        "Building",
        {
            "S2": {"B": [("b wall", "Surface")]},
            "S1": {"A": [("a floor", "Surface"), ("a door", "SubSurface")]},
            "": {"C": []},
        },
    )


def test_flatten_order_and_depth():
    rows = _rows()
    assert [(r.depth, r.label) for r in rows] == [
        (0, "Building"),
        (1, "S1"),
        (2, "A"),
        (3, "a door"),
        (3, "a floor"),
        (1, "S2"),
        (2, "B"),
        (3, "b wall"),
        (1, T.NO_STORY),
        (2, "C"),
    ]
    assert rows[-2].kind == "" and not rows[-1].expandable and rows[2].expandable


def test_visible_mask_collapses_subtrees():
    rows = _rows()
    assert all(T.visible_mask(rows, set()))
    mask = T.visible_mask(rows, {"BuildingStory:S1"})
    assert [r.label for r, m in zip(rows, mask, strict=True) if m] == [
        "Building",
        "S1",
        "S2",
        "B",
        "b wall",
        T.NO_STORY,
        "C",
    ]
    assert T.visible_mask(rows, {"Building:Building"}) == [True] + [False] * (len(rows) - 1)
