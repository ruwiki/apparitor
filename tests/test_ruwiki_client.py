"""RuWiki поверх подделки клиента: кэш статусов, одинаковые имена в разном регистре, глобальные группы."""

import logging

import pytest

from apparitor import ruwiki


class FakeMW:
    def __init__(self, page=None, users=None, globals_=None):
        self.page, self.users, self.globals_ = page, users or {}, globals_ or {}
        self.calls = []

    async def raw_page(self, title):
        self.calls.append(("raw", title))
        return self.page

    async def get(self, **p):
        self.calls.append(("get", p.get("list") or p.get("meta")))
        if p.get("list") == "users":
            out = []
            for n in p["ususers"].split("|"):
                out.append(dict(self.users[n], name=n) if n in self.users else {"name": n, "missing": True})
            return {"query": {"users": out}}
        if p.get("meta") == "globaluserinfo":
            g = self.globals_.get(p["guiuser"])
            return {"query": {"globaluserinfo": dict(g, name=p["guiuser"]) if g else {"missing": True}}}
        raise AssertionError(p)


async def test_statuses_cache_even_when_page_missing(caplog):
    w = ruwiki.RuWiki(FakeMW(page=None))
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert await w.statuses() == {}
    assert sum(1 for c in w.mw.calls if c[0] == "raw") == 1, "пустой ответ тоже кэшируется"
    assert "не учитываются" in caplog.text


async def test_statuses_cached_and_parsed():
    w = ruwiki.RuWiki(FakeMW(page='{"userSet": {"K": ["Carn"]}}'))
    assert (await w.statuses())["Carn"] == {"clerk"}
    await w.statuses()
    assert len(w.mw.calls) == 1


async def test_same_name_different_case_both_answered():
    w = ruwiki.RuWiki(FakeMW(page="{}", users={"Carn": {"groups": ["editor"], "editcount": 5}}))
    r = await w.users_info(["carn", "Carn", "carn"], with_global=False)
    assert set(r) == {"carn", "Carn"} and r["carn"].name == "Carn" and r["Carn"] is r["carn"]


async def test_global_groups_gathered_and_missing_fallback():
    mw = FakeMW(
        page="{}",
        users={"Base": {"groups": ["editor"]}, "Q": {"groups": []}},
        globals_={
            "Base": {"groups": ["steward"]},
            "Q": {"groups": [], "locked": True},
            "NF (WMF)": {"groups": ["wmf-legal"]},
        },
    )
    w = ruwiki.RuWiki(mw)
    r = await w.users_info(["Base", "Q", "Nobody"], with_global=True)
    assert "steward" in r["Base"].groups and r["Q"].locked and r["Q"].blocked and r["Nobody"] is None
    assert (await w.user_info("NF (WMF)")).groups == ["wmf"], "точное имя без локальной учётки — из CentralAuth"
    assert await w.user_info("Nobody") is None


@pytest.mark.parametrize("hdr,exp", [(None, 2.0), ("5", 5.0), ("999", 30.0), ("x", 2.0)])
def test_retry_after(hdr, exp):
    from apparitor.mw import _retry_after

    assert _retry_after(hdr, 2.0) == exp


class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status, self._body, self.headers = status, body, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self._body


class FakeSession:
    closed = False

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None):
        self.calls.append(params)
        return self.responses.pop(0)


async def test_client_raw_page_and_429(monkeypatch):
    from apparitor import mw

    slept = []

    async def fake_sleep(t):
        slept.append(t)

    monkeypatch.setattr(mw.asyncio, "sleep", fake_sleep)
    page = {"query": {"pages": [{"revisions": [{"slots": {"main": {"content": "{}"}}}]}]}}
    s = FakeSession([FakeResp(429, {}, {"Retry-After": "3"}), FakeResp(200, page)])
    c = mw.Client("https://x/api.php", session=s)
    assert await c.raw_page("MediaWiki:Gadget-markadmins.json") == "{}"
    assert slept == [3.0] and s.calls[0]["titles"] == "MediaWiki:Gadget-markadmins.json"
    s = FakeSession([FakeResp(200, {"query": {"pages": [{"missing": True}]}})])
    assert await mw.Client("https://x/api.php", session=s).raw_page("Нет") is None
    s = FakeSession([FakeResp(429, {})] * 4)
    with pytest.raises(RuntimeError):
        await mw.Client("https://x/api.php", session=s).get(action="query")
    assert len(slept) == 4, "после последней попытки не спим"
