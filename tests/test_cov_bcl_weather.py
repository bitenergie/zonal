import openstudio

from zonal.core import scripts_core as sc

CAT, NAME = "Building Component Library", "Get BCL Weather File"


class Resp:
    def __init__(self, n):
        self.n = n

    def name(self):
        return f"R{self.n}"

    def uid(self):
        return f"uid{self.n}"


class Comp:
    def __init__(self, files):
        self._f = files

    def empty(self):
        return False

    def get(self):
        return self

    def files(self, ext):
        return self._f


class Empty:
    def empty(self):
        return True


def fake_remote(responses, component):
    class R:
        downloaded = []

        def searchComponentLibrary(self, city, kind):
            return responses

        def downloadComponent(self, uid):
            R.downloaded.append(uid)

        def waitForComponentDownload(self):
            return component

    return R


def go(args=None):
    m = openstudio.model.Model()
    c = sc.ScriptContext()
    ok = sc.get(CAT, NAME).execute(m, args, c)
    return m, c, ok


def test_no_results(monkeypatch):
    monkeypatch.setattr(openstudio, "RemoteBCL", fake_remote([], None))
    _m, c, ok = go()
    assert not ok and "No weather file" in c.messages[-1].text


def test_empty_component_and_no_epw(monkeypatch):
    monkeypatch.setattr(openstudio, "RemoteBCL", fake_remote([Resp(1)], Empty()))
    assert not go()[2]
    monkeypatch.setattr(openstudio, "RemoteBCL", fake_remote([Resp(1)], Comp([])))
    _m, c, ok = go()
    assert not ok and "No epw" in c.messages[-1].text


def test_success_and_bad_weather(monkeypatch, tmp_path):
    epw = tmp_path / "w.epw"
    epw.write_text("LOCATION,Denver,CO,USA,x,1,39.7,-104.9,-7.0,1650.0\nDESIGN CONDITIONS,0\n")
    R = fake_remote([Resp(1), Resp(2)], Comp([str(epw)]))
    monkeypatch.setattr(openstudio, "RemoteBCL", R)
    ok_flag = {"v": True}

    class Opt:
        def is_initialized(self):
            return ok_flag["v"]

    monkeypatch.setattr(openstudio, "EpwFile", lambda p: object())
    monkeypatch.setattr(openstudio.model.WeatherFile, "setWeatherFile", staticmethod(lambda m, e: Opt()))
    m, c, ok = go({"pick": 99})
    assert ok and R.downloaded == ["uid2"]
    ok_flag["v"] = False
    _m, _c, ok = go({"pick": 0})
    assert not ok and R.downloaded[-1] == "uid1"
