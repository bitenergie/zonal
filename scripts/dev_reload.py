"""Developer auto-reload: run the add-on straight from `src/` and reload it whenever a .py file changes.

Usage (one of):
    blender --python scripts/dev_reload.py                 # start Blender with the add-on from src/
    Text Editor > Open scripts/dev_reload.py > Run Script   # inside a running Blender

Notes
- Disable/uninstall the installed OpenStudio extension first, otherwise its classes clash with these.
- Edit a file in src/zonal and save: within ~1 s the add-on is unregistered, its modules are
  dropped from sys.modules, re-imported and registered again. A reload error is printed to the console and the
  watcher keeps running, so just fix the file and save again.
- Also adds .venv site-packages to sys.path (same CPython ABI as Blender 5.x) so `openstudio` imports;
  Blender 5.x only (4.x is not supported).
- Manual reload: F3 > "Reload OpenStudio add-on".
- Stop the watcher with `bpy.app.driver_namespace["osm_dev_reload_stop"]()`.
"""

import importlib
import sys
import traceback
from pathlib import Path

import bpy


def _find_root() -> Path:
    """Repo root: $OSM_DEV_ROOT, else walk up from this file (or the .blend, when run from a text block)."""
    import os

    cands = []
    if os.environ.get("OSM_DEV_ROOT"):
        cands.append(Path(os.environ["OSM_DEV_ROOT"]))
    cands.append(Path(__file__).resolve())
    if bpy.data.filepath:
        cands.append(Path(bpy.data.filepath).resolve())
    for c in cands:
        for p in [c, *c.parents]:
            if (p / "src" / "zonal").is_dir():
                return p
    raise RuntimeError(
        "Cannot locate the repo (src/zonal). Set the OSM_DEV_ROOT env var, "
        "or run the script from disk: blender --python scripts/dev_reload.py"
    )


ROOT = _find_root()
SRC = ROOT / "src"
PKG = "zonal"
INTERVAL = 1.0  # seconds between mtime checks

sys.path.insert(0, str(SRC))
sys.path.append(str(ROOT / ".venv" / "Lib" / "site-packages"))


def _stamp() -> dict[str, float]:
    return {str(f): f.stat().st_mtime for f in (SRC / PKG).rglob("*.py")}


def _unload() -> None:
    mod = sys.modules.get(PKG)
    if mod is not None:
        try:
            mod.unregister()
        except Exception:
            traceback.print_exc()
    for name in [n for n in sys.modules if n == PKG or n.startswith(PKG + ".")]:
        del sys.modules[name]


def reload_now() -> bool:
    """Unregister, purge and re-register the add-on from src/. Returns True on success."""
    _unload()
    importlib.invalidate_caches()
    try:
        importlib.import_module(PKG).register()
    except Exception:
        traceback.print_exc()
        print("[osm-dev] reload FAILED - fix the error and save again")
        return False
    print("[osm-dev] reloaded", PKG)
    return True


class WM_OT_osm_dev_reload(bpy.types.Operator):
    bl_idname = "wm.osm_dev_reload"
    bl_label = "Reload OpenStudio add-on"

    def execute(self, context):
        ok = reload_now()
        self.report({"INFO" if ok else "ERROR"}, "OpenStudio add-on reloaded" if ok else "Reload failed, see console")
        return {"FINISHED" if ok else "CANCELLED"}


_state = {"stamp": {}, "running": False}


def _watch():
    if not _state["running"]:
        return None
    try:
        now = _stamp()
        if now != _state["stamp"]:
            _state["stamp"] = now
            reload_now()
    except Exception:
        traceback.print_exc()
    return INTERVAL


def stop() -> None:
    _state["running"] = False


def start() -> None:
    # re-running this script: stop the previous watcher and operator
    prev = bpy.app.driver_namespace.get("osm_dev_reload_stop")
    if prev:
        prev()
    try:
        bpy.utils.unregister_class(WM_OT_osm_dev_reload)
    except RuntimeError:
        pass
    bpy.utils.register_class(WM_OT_osm_dev_reload)
    bpy.app.driver_namespace["osm_dev_reload_stop"] = stop
    reload_now()
    _state.update(stamp=_stamp(), running=True)
    bpy.app.timers.register(_watch, first_interval=INTERVAL, persistent=True)
    print(f"[osm-dev] watching {SRC / PKG}")


start()
