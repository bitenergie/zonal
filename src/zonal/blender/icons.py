"""Custom UI icons (imports bpy).

PNGs in icons/<dark|light>/ (made by scripts/make_icons.py) are loaded as preview icons; the set follows the
theme's text colour. The two toolbar tools need Blender's triangle .dat format and take an absolute path.
"""

from __future__ import annotations

from pathlib import Path

import bpy
import bpy.utils.previews

ICON_DIR = Path(__file__).parent.parent / "icons"
_previews: dict[str, bpy.utils.previews.ImagePreviewCollection] = {}  # "dark" / "light"


def theme_variant() -> str:
    """'dark' (light glyphs for dark UIs) or 'light', by luminance of the theme's regular widget text."""
    try:
        r, g, b = bpy.context.preferences.themes[0].user_interface.wcol_regular.text[:3]
    except (AttributeError, IndexError, ValueError):
        return "dark"
    return "dark" if (0.2126 * r + 0.7152 * g + 0.0722 * b) > 0.5 else "light"


def _collection(variant: str):
    pcoll = _previews.get(variant)
    if pcoll is None:
        pcoll = _previews[variant] = bpy.utils.previews.new()
        for png in sorted((ICON_DIR / variant).glob("*.png")):
            pcoll.load(png.stem, str(png), "IMAGE")
    return pcoll


def icon_id(name: str) -> int:
    """Preview icon id for the current theme, 0 when the icon is missing."""
    try:
        item = _collection(theme_variant()).get(name)
    except Exception:
        return 0
    return item.icon_id if item else 0


def ik(name: str, fallback: str = "NONE") -> dict:
    """`**ik("file_export", "EXPORT")` -> icon_value=<custom icon> (or the built-in `fallback` icon)."""
    i = icon_id(name)
    return {"icon_value": i} if i else {"icon": fallback}


def tool_icon(name: str) -> str:
    """bl_icon for a WorkSpaceTool: absolute path of icons/<variant>/<name>.dat without the extension."""
    base = ICON_DIR / theme_variant() / name
    return str(base) if base.with_suffix(".dat").exists() else "ops.mesh.primitive_plane_add_gizmo"


class PanelIcon:
    """Panel mixin: shows the custom icon `osm_icon` in the panel header."""

    osm_icon = ""

    def draw_header(self, context):
        self.layout.label(**ik(self.osm_icon))


def unregister() -> None:
    for pcoll in _previews.values():
        bpy.utils.previews.remove(pcoll)
    _previews.clear()
