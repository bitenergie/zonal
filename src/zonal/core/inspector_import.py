"""Read the Inspector field values of existing OpenStudio objects (template .osm models) so the panel can show them.

Without this, picking a template construction / schedule / material in the Inspector created an empty store item:
every field blank (and a construction without layers). The getters are derived from the setter names of
`inspector_schema` (``setThermalResistance`` -> ``thermalResistance``, case-insensitive). openstudio is imported by the
caller (the model is passed in), so this module stays importable without it.
"""

from __future__ import annotations

from . import inspector_schema as S

_GETTER_CACHE: dict[tuple[type, str], str | None] = {}


def _getter(obj, setter: str):
    """The bound getter matching ``setX`` (case-insensitive; None when the class has none)."""
    key = (type(obj), setter)
    if key not in _GETTER_CACHE:
        want = setter[3:].lower()
        _GETTER_CACHE[key] = next((n for n in dir(obj) if n.lower() == want and not n.startswith("_")), None)
    name = _GETTER_CACHE[key]
    return getattr(obj, name) if name else None


def _unwrap(value):
    """OpenStudio optionals -> value / None; model objects -> their name."""
    if hasattr(value, "is_initialized"):
        if not value.is_initialized():
            return None
        value = value.get()
    if hasattr(value, "nameString"):
        return value.nameString()
    return value


def _find(model, tname: str, name: str):
    getter = getattr(model, f"get{tname}ByName", None)
    if getter is None:
        return None
    found = getter(name)
    return found.get() if found.is_initialized() else None


def read_fields(model, tname: str, name: str) -> dict | None:
    """Field values of the object ``name`` of Inspector type ``tname`` in ``model`` (None: no such object).

    Only the fields a user can edit through the type's definition are returned; unreadable ones are skipped.
    Constructions also return ``layer1..N`` (outside first).
    """
    tdef = S.TYPES.get(tname)
    if tdef is None or tdef.backing != "store" or not name:
        return None
    obj = _find(model, tname, name)
    if obj is None:
        return None
    out: dict = {}
    for fd in tdef.fields:
        if not fd.setter or fd.kind == S.AUTOCALC:
            continue
        get = _getter(obj, fd.setter)
        if get is None:
            continue
        try:
            if fd.kind == S.COLOR:
                raw = get()
                if hasattr(raw, "is_initialized"):
                    raw = raw.get() if raw.is_initialized() else None
                if raw is None:
                    continue
                out[fd.key] = S.rgb_to_hex(
                    (raw.renderingRedValue() / 255, raw.renderingGreenValue() / 255, raw.renderingBlueValue() / 255)
                )
                continue
            value = _unwrap(get())
        except Exception:
            continue
        if value is None:
            continue
        if fd.kind == S.FLOAT:
            value = float(value)
        elif fd.kind == S.INT:
            value = int(value)
        elif fd.kind == S.BOOL:
            value = bool(value)
        elif fd.kind == S.ENUM and value not in fd.choices:
            continue
        else:
            value = value if isinstance(value, (int, float)) else str(value)
        out[fd.key] = value
    if tname == "Construction":
        for i, layer in enumerate(list(obj.layers())[: S.MAX_LAYERS], 1):
            out[f"layer{i}"] = layer.nameString()
    return out


def schedule_kind(model, name: str) -> str:
    """OpenStudio class of the named schedule ("" when absent), e.g. ``ScheduleRuleset`` (not editable as a constant)."""
    found = model.getScheduleByName(name)
    return found.get().iddObject().name().replace("OS:", "").replace(":", "") if found.is_initialized() else ""


def load_models(paths) -> list:
    """Load the template .osm files that exist and are readable (others are skipped)."""
    import openstudio

    models = []
    for p in paths:
        if not p:
            continue
        try:
            m = openstudio.osversion.VersionTranslator().loadModel(openstudio.path(str(p)))
        except Exception:
            continue
        if m.is_initialized():
            models.append(m.get())
    return models


def read_from_models(models, tname: str, name: str) -> dict | None:
    """First model that has the object wins (same precedence as the template merge)."""
    for m in models:
        values = read_fields(m, tname, name)
        if values is not None:
            return values
    return None
