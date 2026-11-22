"""User scripts: pure-Python framework (no bpy).

Python ports of the OpenStudio SketchUp-plugin "UserScripts" (reference/user_scripts/). A script declares its variables
as `ArgDef`s (the Ruby `arguments(model)`) and mutates an `openstudio.model.Model` in `run` (the Ruby `run`). The
Blender panel (blender_scripts.py) draws the arguments and runs the script; this module stays importable in pytest.

Replacements for the SketchUp runner calls:
    runner.registerInfo/Warning/Error -> ctx.info / ctx.warning / ctx.error
    runner.registerAsNotApplicable    -> ctx.not_applicable
    runner.updateProgress             -> ctx.progress
    runner.inSelection(obj)           -> ctx.in_selection(obj)  (selected Blender objects / their spaces)
    runner.yesNoPrompt                -> UserScript.confirm_message (Blender confirmation dialog before the run)
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass

ARG_TYPES = ("float", "int", "bool", "string", "choice", "path")


@dataclass
class ArgDef:
    """One script variable. `choice` takes `choices` as (id, label) pairs; a model-dependent list is built by the
    script's `arguments(model)` (e.g. thermal zone names)."""

    key: str
    label: str
    type: str = "float"
    default: object = None
    choices: tuple = ()
    description: str = ""
    ext: str = ""  # path: file extension without the dot ("csv")
    read: bool = False  # path: the file must exist (open dialog) instead of being written

    def __post_init__(self):
        if self.type not in ARG_TYPES:
            raise ValueError(f"{self.key}: unknown argument type {self.type!r}")
        if self.default is None:
            self.default = {"float": 0.0, "int": 0, "bool": False, "string": "", "path": ""}.get(self.type)
            if self.type == "choice":
                self.default = self.choices[0][0] if self.choices else ""


@dataclass
class Message:
    level: str  # INFO / WARNING / ERROR
    text: str


class ScriptContext:
    """What a script may ask of its host (the sketchup runner's job)."""

    def __init__(self, selected_names=(), selected_spaces=(), progress: Callable[[float, str], None] | None = None):
        self.selected_names = set(selected_names)
        self.selected_spaces = set(selected_spaces)
        self.messages: list[Message] = []
        self.applicable = True
        self._progress = progress
        # viewport results (Visualization scripts): space names to show / hide, and view layers to make
        self.show_spaces: set[str] = set()
        self.hide_spaces: set[str] = set()
        self.view_layers: dict[str, set[str]] = {}  # layer name -> spaces visible in it (all others hidden)
        # hand-offs to the host
        self.weather_file = ""  # .epw path the scene's Inspector weather file should use
        self.building_defaults: dict[str, str] = {}  # Building fields to set in the Inspector (name of the default)

    # --- runner replacements
    def info(self, text: str):
        self.messages.append(Message("INFO", text))

    def warning(self, text: str):
        self.messages.append(Message("WARNING", text))

    def error(self, text: str):
        self.messages.append(Message("ERROR", text))

    def not_applicable(self, text: str):
        self.applicable = False
        self.info(text)

    def progress(self, fraction: float, text: str = ""):
        if self._progress:
            self._progress(max(0.0, min(1.0, fraction)), text)

    @property
    def has_errors(self) -> bool:
        return any(m.level == "ERROR" for m in self.messages)

    def has_selection(self) -> bool:
        return bool(self.selected_names or self.selected_spaces)

    def in_selection(self, obj) -> bool:
        """True when the model object belongs to the Blender selection (itself, its parent surface or its space)."""
        name = obj.nameString()
        if name in self.selected_names or name in self.selected_spaces:
            return True
        for getter in ("surface", "space"):  # sub surface -> surface -> space
            f = getattr(obj, getter, None)
            if f is None:
                continue
            parent = f()
            if hasattr(parent, "is_initialized"):
                parent = parent.get() if parent.is_initialized() else None
            if parent is not None and self.in_selection(parent):
                return True
        return False


class UserScript:
    """Base class. Subclass, set the class attributes and `@register`."""

    category = ""
    name = ""
    description = ""
    confirm_message = ""  # non-empty: the host asks Yes/No before running (SketchUp's yesNoPrompt)
    uses_selection = False  # the script only touches the selected objects
    creates_geometry = False  # adds spaces without reading the model (the scene can stay as it is)
    modifies_model = True  # False for reports: nothing to write back to the scene / .osm
    reads_scene = True  # False: runs on an empty model (the scene geometry is irrelevant)
    edits_geometry = True  # False: the result is not written back as scene geometry (templates, weather, ...)
    produces_template = False  # the model's space types / construction sets become the scene template
    viewport = False  # Visualization: result is object visibility (ctx.show/hide_spaces, ctx.view_layers)
    model_dependent = False  # arguments() lists model objects (the panel offers a refresh button)

    def arguments(self, model) -> list[ArgDef]:
        return []

    def run(self, model, args: dict, ctx: ScriptContext) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def defaults(self, model=None) -> dict:
        return {a.key: a.default for a in self.arguments(model)}

    def execute(self, model, args: dict | None, ctx: ScriptContext) -> bool:
        """Run with `args` completed by the defaults; False (and an ERROR message) on failure."""
        full = self.defaults(model)
        full.update(args or {})
        try:
            ok = bool(self.run(model, full, ctx))
        except Exception as e:
            ctx.error(f"{self.name}: {e}")
            return False
        return ok and not ctx.has_errors


# --- registry ---------------------------------------------------------------------------

REGISTRY: dict[str, dict[str, UserScript]] = {}
_discovered = False


def register(cls):
    """Class decorator: instantiate and file the script under its category."""
    inst = cls()
    if not inst.category or not inst.name:
        raise ValueError(f"{cls.__name__}: category and name are required")
    REGISTRY.setdefault(inst.category, {})[inst.name] = inst
    return cls


def discover() -> dict[str, dict[str, UserScript]]:
    """Import every module of the `user_scripts` package once (they register themselves)."""
    global _discovered
    if not _discovered:
        pkg = importlib.import_module(f"{__package__.rsplit('.', 1)[0]}.user_scripts")
        for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            importlib.import_module(m.name)
        _discovered = True
    return REGISTRY


def categories() -> list[str]:
    return sorted(discover())


def scripts(category: str) -> list[str]:
    return sorted(discover().get(category, {}))


def get(category: str, name: str) -> UserScript | None:
    return discover().get(category, {}).get(name)


# --- selection helpers (Blender objects -> model names) ---------------------------------------

NONE_ID = "__none__"  # Blender enum items cannot have an empty identifier


def expand_selection(model_names, selected) -> set[str]:
    """Selected object names plus the surfaces split off them (`name 1`, `name 2`, ... from multi-face meshes)."""
    import re

    selected = set(selected)
    out = set(selected)
    for n in model_names:
        m = re.match(r"^(.*) \d+$", n)
        if m and m.group(1) in selected:
            out.add(n)
    return out


def fully_selected_spaces(space_of: dict[str, str], selected) -> set[str]:
    """Spaces all of whose objects (`space_of`: object name -> space name) are selected (e.g. a whole collection)."""
    selected = set(selected)
    members: dict[str, list[bool]] = {}
    for obj, space in space_of.items():
        members.setdefault(space, []).append(obj in selected)
    return {sp for sp, flags in members.items() if sp and all(flags)}
