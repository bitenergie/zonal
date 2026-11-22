import pytest

pytest.importorskip("openstudio")

from zonal.core import results_core as rc


def test_missing_sql(tmp_path):
    with pytest.raises((ValueError, RuntimeError, OSError)):
        rc.SqlResults(str(tmp_path / "nope.sql"))
