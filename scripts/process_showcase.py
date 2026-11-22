"""Crop (the 3D-area screenshot has corner widgets), resize and save .cache/showcase/*.png as high-quality WebP.

    python scripts/process_showcase.py   ->  docs/img/showcase/<name>.webp
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC, DST = ROOT / ".cache" / "showcase", ROOT / "docs" / "img" / "showcase"
DST.mkdir(parents=True, exist_ok=True)
for p in sorted(SRC.glob("*.png")):
    im = Image.open(p).convert("RGB")
    w, h = im.size
    im = im.crop((14, 14, w - 14, h - 14))
    if im.width > 1800:
        im = im.resize((1800, round(im.height * 1800 / im.width)), Image.LANCZOS)
    im.save(DST / f"{p.stem}.webp", quality=92, method=6)
    print(p.stem, im.size, (DST / f"{p.stem}.webp").stat().st_size // 1024, "KB")
