"""Build a *static* Blender extensions repository (index.json + index.html + zip) in ./repo.

    uv run python scripts/make_repo.py
    uv run python scripts/make_repo.py --base-url https://github.com/OWNER/REPO/releases/download/v0.1.0

Without --base-url the archive_url stays relative (host index.json and the zip side by side).
With --base-url the zip is expected at <base-url>/<zip name>; use this when the zip (50-80 MB per platform because of the
bundled OpenStudio wheels) is too large for the web host, e.g. GitHub Pages -> attach the zip to a GitHub Release.
Add to Blender: Preferences > Get Extensions > Repositories > Add Remote Repository > <host>/index.json
https://docs.blender.org/manual/en/latest/advanced/extensions/creating_repository/static_repository.html
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "zonal"
DEFAULT_BLENDER = os.environ.get("BLENDER", "blender")


def blender(exe: str, *args: str) -> None:
    subprocess.run([exe, "--factory-startup", "--command", "extension", *args], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--blender", default=DEFAULT_BLENDER, help="Blender >= 5.0 executable (or $BLENDER)")
    ap.add_argument("--out", type=Path, default=ROOT / "repo")
    ap.add_argument("--base-url", help="absolute folder URL where the zip is hosted")
    ap.add_argument("--skip-fetch", action="store_true", help="wheels are already in src/zonal/wheels")
    a = ap.parse_args()

    if not a.skip_fetch:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_wheels.py")], check=True)
    blender(a.blender, "validate", str(SRC))
    if a.out.exists():
        shutil.rmtree(a.out)
    a.out.mkdir(parents=True)
    blender(a.blender, "build", "--split-platforms", "--source-dir", str(SRC), "--output-dir", str(a.out))
    blender(a.blender, "server-generate", "--repo-dir", str(a.out), "--html")

    if a.base_url:
        index = a.out / "index.json"
        data = json.loads(index.read_text(encoding="utf-8"))
        base = a.base_url.rstrip("/")
        for item in data["data"]:
            item["archive_url"] = f"{base}/{Path(item['archive_url']).name}"
        index.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("repository ready in", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
