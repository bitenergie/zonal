"""Turn the raw screenshots of docs_start_shots.py (.cache/start_shots) into docs/img/start/*.webp.

    python scripts/process_start_shots.py          # needs Pillow + numpy
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / ".cache" / "start_shots"
OUT = ROOT / "docs" / "img" / "start"
OUT.mkdir(parents=True, exist_ok=True)
META = json.loads((SRC / "meta.json").read_text())
ACCENT = (255, 140, 0)


def font(size):
    for f in ("/usr/share/fonts/truetype/lato/Lato-Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def load(name):
    return Image.open(SRC / f"{name}.png").convert("RGB")


def area_of(meta, typ):
    return next(a for a in meta["areas"] if a["type"] == typ)


def top_left(meta, rect, img_h, origin_y=None):
    """Blender rect (x, y bottom-left, w, h) -> PIL box. Window shots: origin = window; area shots: origin = the 3D area."""
    x, y, w, h = rect
    return x, img_h - (y + h), x + w, img_h - y


def save(img, name, width=None):
    if width and img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    img.save(OUT / f"{name}.webp", quality=88, method=6)
    print("saved", name, img.size)


def trim_bottom(img, pad=14, gap=60):
    px = np.asarray(img.convert("RGB")).astype(np.int16)
    bg = np.median(px[:, 6:-48].reshape(-1, 3), axis=0)
    diff = (np.abs(px[:, 6:-48] - bg) > 6).any(axis=2).any(axis=1)
    ys = np.nonzero(diff)[0]
    if not len(ys):
        return img
    end = int(ys[0])
    for y in ys[1:]:
        if y - end > gap:
            break
        end = int(y)
    return img.crop((0, 0, img.width, min(img.height, end + pad)))


def panel(name, out=None):
    """Sidebar of a 3D-area shot: crop the UI region, trim the empty bottom."""
    m = META[name]
    a = area_of(m, "VIEW_3D")
    ui = a["regions"]["UI"]
    img = load(name)
    x0 = ui[0] - a["x"]
    top = (a["y"] + a["h"]) - (ui[1] + ui[3])
    img = img.crop((x0, top, x0 + ui[2], top + ui[3]))
    save(trim_bottom(img), out or name)


def view(name, out=None, width=1400):
    save(load(name), out or name, width)


def overview():
    m = META["00-overview"]
    img = load("00-overview")
    H = img.height
    d = ImageDraw.Draw(img, "RGBA")
    v3 = area_of(m, "VIEW_3D")
    outl = area_of(m, "OUTLINER")
    prop = area_of(m, "PROPERTIES")
    tl = area_of(m, "DOPESHEET_EDITOR")
    items = [
        (1, v3["regions"]["TOOLS"]),
        (2, [v3["regions"]["WINDOW"][0] + 60, v3["regions"]["WINDOW"][1] + 80, v3["regions"]["WINDOW"][2] - 130 - v3["regions"]["UI"][2], v3["regions"]["WINDOW"][3] - 160]),
        (3, v3["regions"]["UI"]),
        (4, [outl["x"], outl["y"], outl["w"], outl["h"]]),
        (5, [prop["x"], prop["y"], prop["w"], prop["h"]]),
        (6, [tl["x"], tl["y"], tl["w"], tl["h"]]),
        (7, v3["regions"]["HEADER"]),
    ]
    f = font(34)
    for n, rect in items:
        x0, y0, x1, y1 = top_left(m, rect, H)
        if n == 2:
            d.rectangle((x0, y0, x1, y1), outline=ACCENT + (255,), width=5)
            bx, by = x0 + 14, y0 + 14
        else:
            d.rectangle((x0, y0, x1, y1), outline=ACCENT + (255,), width=4, fill=ACCENT + (28,))
            bx, by = x0 + 6, y0 + 6
        d.ellipse((bx, by, bx + 52, by + 52), fill=ACCENT + (255,))
        tw = d.textlength(str(n), font=f)
        d.text((bx + 26 - tw / 2, by + 5), str(n), font=f, fill=(20, 20, 20, 255))
    save(img, "00-overview", 1800)


def crop_area(name, typ, out, trim=True, scale=1.0, region="full"):
    m = META[name]
    img = load(name)
    a = area_of(m, typ)
    box = top_left(m, [a["x"], a["y"], a["w"], a["h"]], img.height)
    c = img.crop(box)
    if trim:
        c = trim_bottom(c)
    if scale != 1.0:
        c = c.resize((round(c.width * scale), round(c.height * scale)), Image.LANCZOS)
    save(c, out)


overview()
panel("01-panels")
panel("02-template-panel")
crop_area("03-outliner", "OUTLINER", "03-outliner", trim=False, scale=2.0)
panel("04a-item-panel")
view("04a-box")
img = load("04b-draw-tool")
save(img.crop((0, 0, 1180, 640)), "04b-draw-tool")
panel("05a-modeling-panel")
view("05b-separated")
view("06a-loose-rectangle")
panel("06a-subsurface-panel")
view("06b-window")
panel("07-properties-panel")
view("07-wall-selected")
for t in ("surface-type", "boundary", "zone"):
    view(f"08-mode-{t}")
panel("08-rendering-panel")
view("09a-two-spaces")
view("09b-matched")
panel("09-matching-panel")
if "09-legend-panel" in META:
    panel("09-legend-panel")
m = META["10-export"]
img = load("10-export")
a = area_of(m, "PROPERTIES")
box = top_left(m, a["regions"]["WINDOW"], img.height)
save(trim_bottom(img.crop(box), pad=10), "10-export")
