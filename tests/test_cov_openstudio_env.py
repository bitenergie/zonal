import sys

import pytest

from zonal.core import openstudio_env as env


def test_load_ok_and_mismatch(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "openstudio", object())
    assert env.load() is sys.modules["openstudio"]
    monkeypatch.setitem(sys.modules, "openstudio", None)
    root = tmp_path / "os"
    (root / "Python").mkdir(parents=True)
    (root / "Python" / "openstudio.py").write_text("raise ImportError('DLL load failed')")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(env, "_dll_dirs", [])
    with pytest.raises(ImportError, match="does not load"):
        env.load(str(root))
