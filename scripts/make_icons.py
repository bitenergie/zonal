"""Copy the exported UI icons from design/icons into the add-on and build the toolbar .dat files.

    uv run --with pillow python scripts/make_icons.py

Output (src/zonal/icons/):
    dark/<name>.png, light/<name>.png   64px icons, loaded as Blender preview icons (blender_icons.py)
    dark/<name>.dat, light/<name>.dat   toolbar icons (Blender's tool system only accepts its triangle format)
"""

import shutil
import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "design" / "icons"
DST = ROOT / "src" / "zonal" / "icons"
TOOLS = ("tool_draw_rectangle",)
GRID = 32  # pixel grid of the .dat icon; Blender's icon space is 0..255 (8 units per cell)


def write_dat(png: Path, out: Path) -> None:
    """Blender 'VCO' icon: 8 byte header, then per triangle 3x(x, y) u8 coords, then 3x RGBA u8 colours."""
    img = Image.open(png).convert("RGBA").resize((GRID, GRID), Image.LANCZOS)
    cell = 256 // GRID
    coords, colors = bytearray(), bytearray()
    for row in range(GRID):
        y1 = 255 - row * cell  # image rows run top-down, Blender's icon space bottom-up
        y0 = max(y1 - cell, 0)
        y1 = min(y1, 255)
        for col in range(GRID):
            r, g, b, a = img.getpixel((col, row))
            if a < 8:
                continue
            x0, x1 = col * cell, min(col * cell + cell, 255)
            for tri in (((x0, y0), (x1, y0), (x1, y1)), ((x0, y0), (x1, y1), (x0, y1))):
                for x, y in tri:
                    coords += bytes((x, y))
                    colors += bytes((r, g, b, a))
    out.write_bytes(b"VCO\x00" + struct.pack("<4B", 0xFF, 0xFF, 0, 0) + bytes(coords) + bytes(colors))


def main() -> int:
    for theme in ("dark", "light"):
        src = SRC / f"for_{theme}_ui"
        dst = DST / theme
        dst.mkdir(parents=True, exist_ok=True)
        for png in sorted((src / "png_64").glob("*.png")):
            shutil.copy2(png, dst / png.name)
        for name in TOOLS:
            write_dat(src / "png_128" / f"{name}.png", dst / f"{name}.dat")
    print("icons written to", DST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
