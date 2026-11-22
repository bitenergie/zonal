"""Small drawing helpers shared by the panels (Blender-native widgets, no state)."""

from __future__ import annotations

# --- style constants (one place for the add-on's look) -------------------------------------
LABEL_FACTOR = 0.34  # share of the width of the left-aligned label column; the value gets the rest
PRIMARY_SCALE = 1.3  # main action of a panel (Run, Search)
BUTTON_SCALE = 1.0  # every other button


def split_column(layout, align: bool = True):
    """Plain column without Blender's property split (labels come from `field`)."""
    layout.use_property_split = False
    layout.use_property_decorate = False
    return layout.column(align=align)


def field_row(layout, text: str, factor: float = LABEL_FACTOR):
    """Left-aligned label + a row for the value widgets (returned). No empty space left of the label."""
    layout.use_property_split = False
    split = layout.split(factor=factor, align=True)
    split.label(text=text)
    return split.row(align=True)


def field(layout, data, prop: str, text: str | None = None, *, factor: float = LABEL_FACTOR, **kw):
    """`data.prop` with a left-aligned label column of `factor` width; the value widget expands."""
    if text is None:
        try:
            text = data.bl_rna.properties[prop].name
        except (AttributeError, KeyError):
            text = prop
    row = field_row(layout, text, factor)
    row.prop(data, prop, text="", **kw)
    return row


def draw_range(layout, data, lo: str, hi: str, text: str, factor: float = LABEL_FACTOR):
    """Two props of one range in a single aligned row under one left-aligned label."""
    row = field_row(layout, text, factor)
    row.prop(data, lo, text="")
    row.prop(data, hi, text="")
    return row


def draw_enum_row(layout, data, prop: str, text: str | None = None, *, icons_only: bool = False):
    """Expanded enum as a segmented button row."""
    row = layout.row(align=True)
    row.prop(data, prop, expand=True, icon_only=icons_only, **({} if text is None else {"text": text}))
    return row


def draw_legend(layout, entries, columns: int = 2):
    """Colour legend: `entries` = [(label, (r, g, b))]. Swatches are disabled COLOR props shown through a grid."""
    grid = layout.grid_flow(row_major=True, columns=columns, even_columns=True, align=True)
    for label, _rgb in entries:
        grid.label(text=label)
    return grid
