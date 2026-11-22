"""Pure logic of the Inspector hierarchy picker: Building > Story > Space > Surface flattened for a UIList."""

from __future__ import annotations

from dataclasses import dataclass

NO_STORY = "(no story)"


@dataclass(frozen=True)
class Row:
    kind: str  # Inspector type name ("" = group row that cannot be picked)
    name: str
    label: str
    depth: int
    expandable: bool

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"


def flatten(building: str, tree: dict[str, dict[str, list[tuple[str, str]]]]) -> list[Row]:
    """`tree` = {story name ("" = none): {space name: [(surface object name, Inspector type)]}} -> ordered rows."""
    rows = [Row("Building", building, building, 0, bool(tree))]
    for story in sorted(tree, key=lambda s: (s == "", s.lower())):
        spaces = tree[story]
        rows.append(Row("BuildingStory" if story else "", story, story or NO_STORY, 1, bool(spaces)))
        for space in sorted(spaces, key=str.lower):
            surfaces = spaces[space]
            rows.append(Row("Space", space, space, 2, bool(surfaces)))
            rows.extend(Row(kind, name, name, 3, False) for name, kind in sorted(surfaces, key=lambda s: s[0].lower()))
    return rows


def visible_mask(rows: list[Row], collapsed: set[str]) -> list[bool]:
    """False for rows below a collapsed ancestor."""
    out, hide_below = [], None
    for r in rows:
        if hide_below is not None and r.depth > hide_below:
            out.append(False)
            continue
        hide_below = None
        out.append(True)
        if r.expandable and r.key in collapsed:
            hide_below = r.depth
    return out
