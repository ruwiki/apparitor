from aiohttp.test_utils import TestClient, TestServer

from apparitor.web import make_app


async def test_routes_without_oauth(bot):
    async with TestClient(TestServer(make_app(bot))) as c:
        assert (await c.get("/")).status == 200
        r = await c.get("/healthz")
        assert r.status == 503 and "discord: not ready" in await r.text()
        assert (await c.get("/oauth/start?s=bad")).status == 400
        assert (await c.get("/oauth/callback?code=x&state=bad")).status == 400


async def test_index_lists_mode_of_every_feature(bot):
    """Страница бота — не общий dry_run, а режим каждой функции: сейчас часть живых при холостом сервере."""
    async with TestClient(TestServer(make_app(bot))) as c:
        text = await (await c.get("/")).text()
    assert "впуск по /login и /confirm — боевой" in text and "reaction_roles — боевой" in text
    assert "starboard — холостой" in text and "/sync, /link, обход, реакции на сообщениях бота — холостой" in text
