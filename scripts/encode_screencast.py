"""Turn .cache/walkthrough (frames + manifest.json from docs_screencast.py) into the website media.

    python scripts/encode_screencast.py [out_dir]     # default docs/img ; needs Pillow + ffmpeg on PATH

Writes <out_dir>/walkthrough.mp4 (1080p; no animated WebP, it looked too soft) and
<out_dir>/walkthrough/NN-*.png (the per-step stills, 1920 px wide).
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / ".cache" / "walkthrough"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "img"
W, H = 1920, 1080
BG = (29, 29, 29)


def font(size):
    for f in ("/usr/share/fonts/truetype/lato/Lato-Semibold.ttf", "C:/Windows/Fonts/segoeuib.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


FONT = font(40)


def compose(img, caption):
    img = img.convert("RGB")
    scale = W / img.width
    img = img.resize((W, round(img.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), BG)
    canvas.paste(img, (0, (H - img.height) // 2))
    if caption:
        d = ImageDraw.Draw(canvas, "RGBA")
        l, t, r, b = d.textbbox((0, 0), caption, font=FONT)
        tw, th = r - l, b - t
        x, y = (W - tw) // 2, H - 150
        d.rounded_rectangle((x - 36, y - 20, x + tw + 36, y + th + 30), radius=18, fill=(15, 15, 15, 215))
        d.text((x, y), caption, font=FONT, fill=(255, 255, 255, 255))
    return canvas


def main():
    man = json.loads((SRC / "manifest.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="zonal_enc_"))
    for i, fr in enumerate(man["frames"]):
        compose(Image.open(SRC / "frames" / fr["f"]), fr["cap"]).save(tmp / f"c{i:04d}.png")
    fps = man["fps"]
    ff = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(tmp / "c%04d.png")]
    subprocess.check_call([*ff, "-r", "30", "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
                           "-movflags", "+faststart", str(OUT / "walkthrough.mp4")])
    sd = OUT / "walkthrough"
    sd.mkdir(exist_ok=True)
    for p in sorted((SRC / "stills").glob("*.png")):
        img = Image.open(p).convert("RGB")
        img.resize((W, round(img.height * W / img.width)), Image.LANCZOS).save(sd / p.name, optimize=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print("done ->", OUT)


if __name__ == "__main__":
    main()
