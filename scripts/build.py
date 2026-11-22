"""Build (and optionally install) the Blender extension zip.

uv run python scripts/build.py            # -> dist/zonal-<ver>-<platform>.zip
uv run python scripts/build.py --install  # also install into Blender's user_default repo
"""

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLENDER = os.environ.get("BLENDER") or r"C:\Program Files\Blender Foundation\Blender 5.3\blender.exe"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blender", default=BLENDER)
    ap.add_argument("--install", action="store_true")
    a = ap.parse_args()
    subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_wheels.py")], check=True)
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    base = [a.blender, "--factory-startup", "--command", "extension"]
    subprocess.run(
        [
            *base,
            "build",
            "--split-platforms",
            "--source-dir",
            str(ROOT / "src" / "zonal"),
            "--output-dir",
            str(dist),
        ],
        check=True,
    )
    if a.install:
        plat = {"win32": "windows_x64", "linux": "linux_x64", "darwin": "macos_arm64"}[sys.platform]
        if sys.platform == "darwin" and platform.machine() == "x86_64":
            plat = "macos_x64"
        zips = sorted(dist.glob(f"zonal-*-{plat}.zip"), key=lambda p: p.stat().st_mtime)
        subprocess.run([*base, "install-file", "-r", "user_default", "--enable", str(zips[-1])], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
