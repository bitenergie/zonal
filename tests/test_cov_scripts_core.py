import pytest

from zonal.core import scripts_core as sc


def test_argdef_defaults_and_errors():
    with pytest.raises(ValueError):
        sc.ArgDef("k", "L", type="bogus")
    assert sc.ArgDef("k", "L", type="choice", choices=(("a", "A"),)).default == "a"
    assert sc.ArgDef("k", "L", type="choice").default == ""
    assert sc.ArgDef("k", "L", type="int").default == 0


def test_context_messages():
    seen = []
    c = sc.ScriptContext(selected_names=["a"], progress=lambda f, t: seen.append((f, t)))
    c.progress(2.0, "x")
    assert seen == [(1.0, "x")]
    assert c.has_selection()
    c.warning("w")
    c.not_applicable("n")
    assert not c.applicable and not c.has_errors
    c.error("e")
    assert c.has_errors
    sc.ScriptContext().progress(0.5)


def test_register_requires_names_and_execute_catches():
    class Bad(sc.UserScript):
        pass

    with pytest.raises(ValueError):
        sc.register(Bad)

    class Boom(sc.UserScript):
        category = "ZZ cov"
        name = "boom"

        def run(self, model, args, ctx):
            raise RuntimeError("x")

    class Fine(Boom):
        name = "fine"

        def arguments(self, model):
            return [sc.ArgDef("a", "A", default=3.0)]

        def run(self, model, args, ctx):
            return args["a"] == 3.0

    sc.register(Boom)
    sc.register(Fine)
    c = sc.ScriptContext()
    assert not sc.get("ZZ cov", "boom").execute(None, None, c)
    assert c.has_errors
    assert sc.get("ZZ cov", "fine").execute(None, None, sc.ScriptContext())
    assert sc.get("ZZ cov", "fine").defaults() == {"a": 3.0}
    sc.REGISTRY.pop("ZZ cov")
