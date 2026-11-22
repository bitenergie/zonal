"""Download the openstudio wheels that blender_manifest.toml lists into src/zonal/wheels."""

import json
import sys
import urllib.request
from pathlib import Path

VERSION = "3.11.0"
TAGS = (
    "cp313-cp313-win_amd64",  # Blender 5.x on Windows (4.x is not supported)
    "py3-none-manylinux1_x86_64",
    "py3-none-manylinux2014_aarch64",
    "py3-none-macosx_10_9_x86_64",
    "py3-none-macosx_11_0_arm64",
)
DEST = Path(__file__).resolve().parent.parent / "src" / "zonal" / "wheels"


def main() -> int:
    DEST.mkdir(exist_ok=True)
    with urllib.request.urlopen(f"https://pypi.org/pypi/openstudio/{VERSION}/json") as r:
        files = json.load(r)["urls"]
    for tag in TAGS:
        f = next((f for f in files if f["filename"].endswith(f"{tag}.whl")), None)
        if f is None:
            print(f"no wheel for {tag}", file=sys.stderr)
            return 1
        target = DEST / f["filename"]
        if target.exists():
            print("have", target.name)
            continue
        print("downloading", target.name)
        urllib.request.urlretrieve(f["url"], target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
