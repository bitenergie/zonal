"""Serve dist/ as a *dynamic* Blender extensions repository.

    uv run python scripts/serve_repo.py                      # http://127.0.0.1:8000/api/v1/extensions/
    uv run python scripts/serve_repo.py --token SECRET --host 0.0.0.0 --port 8080

In Blender: Preferences > Get Extensions > Repositories > + > Add Remote Repository, URL
http://127.0.0.1:8000/api/v1/extensions/  (add the access token if --token is used).

The listing is generated from the zips in dist/ with `blender --command extension server-generate`, then
filtered per request by the `blender_version` and `platform` query parameters and reduced to the newest version
of every extension, as described in
https://docs.blender.org/manual/en/latest/advanced/extensions/creating_repository/dynamic_repository.html
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
BLENDER = os.environ.get("BLENDER") or r"C:\Program Files\Blender Foundation\Blender 5.3\blender.exe"
API = "/api/v1/extensions/"
DOWNLOAD = "/download/"


def vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v.split("-")[0].split("+")[0])[:3]) or (0,)


def compatible(item: dict, blender_version: str | None, platform: str | None) -> bool:
    if blender_version:
        bv = vtuple(blender_version)
        if "blender_version_min" in item and bv < vtuple(item["blender_version_min"]):
            return False
        if "blender_version_max" in item and bv >= vtuple(item["blender_version_max"]):
            return False
    plats = item.get("platforms") or []
    return not (platform and plats and platform not in plats)


def build_listing(index: dict, base_url: str, blender_version: str | None, platform: str | None) -> dict:
    newest: dict[str, dict] = {}
    for item in index.get("data", []):
        if not compatible(item, blender_version, platform):
            continue
        cur = newest.get(item["id"])
        if cur is None or vtuple(item["version"]) > vtuple(cur["version"]):
            newest[item["id"]] = item
    data = []
    for item in newest.values():
        item = dict(item)
        name = Path(unquote(item["archive_url"])).name
        item["archive_url"] = f"{base_url}{DOWNLOAD}{quote(name)}"
        data.append(item)
    return {**index, "data": data}


def generate_index(blender: str, repo_dir: Path) -> None:
    cmd = [blender, "--factory-startup", "--command", "extension", "server-generate", "--repo-dir", str(repo_dir)]
    subprocess.run(cmd, check=True)


def make_handler(repo_dir: Path, token: str | None):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not token:
                return True
            got = self.headers.get("Authorization", "")
            return hmac.compare_digest(got, f"Bearer {token}")

        def do_GET(self):
            url = urlparse(self.path)
            if not self._authorized():
                self._send(401, b'{"error": "unauthorized"}')
                return
            if url.path.rstrip("/") == API.rstrip("/"):
                q = parse_qs(url.query)
                index = json.loads((repo_dir / "index.json").read_text(encoding="utf-8"))
                base = f"http://{self.headers.get('Host', 'localhost')}"
                listing = build_listing(index, base, q.get("blender_version", [None])[0], q.get("platform", [None])[0])
                self._send(200, json.dumps(listing, indent=2).encode())
            elif url.path.startswith(DOWNLOAD):
                f = repo_dir / Path(unquote(url.path[len(DOWNLOAD) :])).name  # .name: no path traversal
                if f.suffix == ".zip" and f.is_file():
                    self._send(200, f.read_bytes(), "application/zip")
                else:
                    self._send(404, b'{"error": "not found"}')
            else:
                self._send(404, b'{"error": "not found"}')

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--blender", default=BLENDER)
    ap.add_argument("--dir", type=Path, default=ROOT / "dist", help="folder with the extension zips")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--token", help="require 'Authorization: Bearer <token>'")
    ap.add_argument("--no-generate", action="store_true", help="reuse the existing index.json")
    a = ap.parse_args()
    if not a.no_generate:
        generate_index(a.blender, a.dir)
    if not (a.dir / "index.json").is_file():
        print("no index.json in", a.dir, file=sys.stderr)
        return 1
    print(f"serving http://{a.host}:{a.port}{API}  ({'token required' if a.token else 'no authentication'})")
    ThreadingHTTPServer((a.host, a.port), make_handler(a.dir, a.token)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
