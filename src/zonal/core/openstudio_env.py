"""Locate the OpenStudio python bindings (pure Python, no bpy, so it is unit-testable).

Two ways to get `import openstudio` working inside Blender:

1. the wheel bundled in the extension zip (Blender installs it, nothing to do), or
2. an OpenStudio install whose bindings match Blender's CPython: the user sets its folder in the add-on
   preferences. An install looks like `<root>/Python/openstudio.py` (+ `_openstudio*.pyd`) with the DLLs in
   `<root>/bin`; the root, its `bin` or its `Python` folder are all accepted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_dll_dirs: list = []  # keep os.add_dll_directory handles alive


def resolve_install(path: str | os.PathLike) -> tuple[Path, Path | None] | None:
    """Return (folder holding openstudio.py, DLL folder or None) for a user supplied path, else None."""
    p = Path(path)
    for root in (p, p.parent):
        for py in (root / "Python", root):
            if (py / "openstudio.py").is_file():
                install = py.parent if py.name == "Python" else root  # the folder that holds bin/
                dll = install / "bin"
                return py, dll if dll.is_dir() else None
    return None


def activate(path: str) -> bool:
    """Make the bindings of the install at `path` importable. False if `path` is not an OpenStudio install."""
    found = resolve_install(path) if path else None
    if found is None:
        return False
    py, dll = found
    if str(py) not in sys.path:
        sys.path.append(str(py))
    if dll is not None and hasattr(os, "add_dll_directory"):
        _dll_dirs.append(os.add_dll_directory(str(dll)))
    return True


def load(path: str = ""):
    """Import and return the `openstudio` module, using the install at `path` if the plain import fails.

    Raises ImportError with a message that says what to do.
    """
    try:
        import openstudio

        return openstudio
    except ImportError as plain:
        err = plain
    if path:
        if not activate(path):
            raise ImportError(f"'{path}' is not an OpenStudio install (no Python/openstudio.py found)") from err
        try:
            import openstudio

            return openstudio
        except ImportError as e:  # typically "DLL load failed": the install's CPython differs from Blender's
            raise ImportError(
                f"OpenStudio at '{path}' does not load in Python {sys.version_info.major}.{sys.version_info.minor}: {e}"
            ) from e
    raise ImportError(
        "OpenStudio python bindings not found: use the extension zip that bundles the wheel, "
        "or set the OpenStudio install folder in the add-on preferences"
    ) from err
