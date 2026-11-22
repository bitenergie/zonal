"""Read the location of an EnergyPlus weather file (.epw) from its first line (pure Python, no openstudio).

``LOCATION,city,state/province,country,data source,WMO,latitude,longitude,time zone,elevation``
The keys are the field keys of the Inspector's WeatherFile type (inspector_schema).
"""

from __future__ import annotations

import os
from functools import lru_cache

_TEXT = ("city", "stateProvinceRegion", "country", "dataSource", "wmoNumber")
_NUMBERS = ("latitude", "longitude", "timeZone", "elevation")
LOCATION_KEYS = _TEXT + _NUMBERS


def parse_location(line: str) -> dict | None:
    """The WeatherFile fields stored in an epw ``LOCATION`` line, or None when it is not one."""
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) < 10 or parts[0].upper() != "LOCATION":
        return None
    out: dict = dict(zip(_TEXT, parts[1:6]))
    try:
        out.update(zip(_NUMBERS, (float(p) for p in parts[6:10])))
    except ValueError:
        return None
    return out


@lru_cache(maxsize=16)
def _read(path: str, mtime: float) -> dict | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse_location(f.readline())
    except OSError:
        return None


def read_location(path: str) -> dict | None:
    """`parse_location` of the file's first line (cached per modification time); None if unreadable."""
    try:
        return _read(path, os.stat(path).st_mtime)
    except OSError:
        return None
