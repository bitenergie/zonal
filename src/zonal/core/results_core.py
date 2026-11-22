"""Pure logic for the Results panel: read EnergyPlus SQL time series, map result keys to Blender objects.

No bpy. `openstudio` is only touched through the `SqlResults` wrapper (lazy import).
"""

from __future__ import annotations

from dataclasses import dataclass


def key_kind(variable: str) -> str:
    """Which kind of object a variable's key values refer to: "surface", "zone" or "other" (meters, site...)."""
    v = variable.strip().lower()
    if v.startswith("surface "):
        return "surface"
    if v.startswith(("zone ", "zone,")):
        return "zone"
    return "other"


def key_label(key: str) -> str:
    return key if key else "Whole building"


@dataclass(frozen=True)
class Series:
    values: list[float]
    units: str
    start: str  # ISO-ish text of the first reported time, e.g. "2006-Jan-01 01:00:00"
    interval_hours: float


class SqlResults:
    """Thin wrapper over `openstudio.SqlFile` returning plain Python types."""

    def __init__(self, path: str):
        import openstudio

        self._sql = openstudio.SqlFile(openstudio.path(path))
        if not self._sql.connectionOpen():
            raise ValueError(f"Could not open SQL file: {path}")
        self.path = path

    def env_periods(self) -> list[str]:
        return [str(e) for e in self._sql.availableEnvPeriods()]

    def frequencies(self, env: str) -> list[str]:
        return [str(f) for f in self._sql.availableReportingFrequencies(env)]

    def variables(self, env: str, freq: str) -> list[str]:
        return [str(v) for v in self._sql.availableVariableNames(env, freq)]

    def keys(self, env: str, freq: str, variable: str) -> list[str]:
        return [str(k) for k in self._sql.availableKeyValues(env, freq, variable)]

    def series(self, env: str, freq: str, variable: str, key: str) -> Series | None:
        opt = self._sql.timeSeries(env, freq, variable, key)
        if opt.empty():
            return None
        ts = opt.get()
        vec = ts.values()
        # NOTE: list(vec) segfaults in the SWIG bindings; index access is safe.
        values = [float(vec[i]) for i in range(vec.size())]
        interval = ts.intervalLength()
        hours = interval.get().totalHours() if not interval.empty() else 1.0
        return Series(values, str(ts.units()), ts.firstReportDateTime().toString(), float(hours))


@dataclass(frozen=True)
class ObjectInfo:
    """What the matcher needs to know about one Blender object (name + custom props)."""

    name: str
    space: str = ""
    zone: str = ""


def match_objects(key: str, kind: str, objects: list[ObjectInfo]) -> list[str]:
    """Names of the objects a result key applies to (case-insensitive: EnergyPlus upper-cases names).

    surface -> the object with that name; zone -> every object whose thermal zone (or, failing that, space) is the key.
    """
    k = key.strip().casefold()
    if not k:
        return []
    if kind == "surface":
        return [o.name for o in objects if o.name.strip().casefold() == k]
    if kind == "zone":
        return [o.name for o in objects if (o.zone or o.space).strip().casefold() == k]
    return []


def value_range(
    series_list: list[list[float]], lo: float | None = None, hi: float | None = None
) -> tuple[float, float]:
    """(min, max) over all series, overridden by explicit `lo` / `hi`; never degenerate (max > min)."""
    flat = [v for s in series_list for v in s]
    a = min(flat) if flat else 0.0
    b = max(flat) if flat else 1.0
    a = a if lo is None else lo
    b = b if hi is None else hi
    if b <= a:
        b = a + 1.0
    return a, b


def frame_times(n: int, start_frame: int, frames_per_step: float) -> list[float]:
    """Frame number of each of the `n` samples."""
    return [start_frame + i * frames_per_step for i in range(n)]
