from aiohttp.test_utils import TestClient, TestServer

from apparitor.web import make_app


async def test_routes_without_oauth(bot):
    async with TestClient(TestServer(make_app(bot))) as c:
        assert (await c.get("/")).status == 200
        r = await c.get("/healthz")
        assert r.status == 503 and "discord: not ready" in await r.text()
        assert (await c.get("/oauth/start?s=bad")).status == 400
        assert (await c.get("/oauth/callback?code=x&state=bad")).status == 400
