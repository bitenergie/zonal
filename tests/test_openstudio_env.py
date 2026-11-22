import sys

import pytest

from zonal.core import openstudio_env as env


def _install(root):
    (root / "Python").mkdir(parents=True)
    (root / "Python" / "openstudio.py").write_text("")
    (root / "bin").mkdir()
    return root


@pytest.mark.parametrize("sub", ["", "bin", "Python"])
def test_resolve_accepts_root_bin_or_python(tmp_path, sub):
    root = _install(tmp_path / "os")
    py, dll = env.resolve_install(root / sub if sub else root)
    assert py == root / "Python" and dll == root / "bin"


def test_resolve_rejects_other_dirs(tmp_path):
    assert env.resolve_install(tmp_path) is None
    assert not env.activate("")


def test_activate_adds_python_dir(tmp_path):
    root = _install(tmp_path / "os")
    try:
        assert env.activate(str(root))
        assert str(root / "Python") in sys.path
    finally:
        sys.path[:] = [p for p in sys.path if p != str(root / "Python")]


def test_load_bad_path_message(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "openstudio", None)  # force ImportError
    with pytest.raises(ImportError, match="not an OpenStudio install"):
        env.load(str(tmp_path))
    with pytest.raises(ImportError, match="bundles the wheel"):
        env.load("")
