"""HTTP-часть: OAuth 2.0 «только идентификация» через Мету + страница статуса.
Живёт в одном процессе с Discord-клиентом (общий event loop и Store)."""

from __future__ import annotations

import datetime as dt
import os
import secrets

from aiohttp import ClientSession, web

META = "https://meta.wikimedia.org/w/rest.php/oauth2"
BOT = web.AppKey("bot", object)


def make_app(bot) -> web.Application:
    app = web.Application()
    app[BOT] = bot
    app.add_routes(
        [
            web.get("/", index),
            web.get("/healthz", healthz),
            web.get("/oauth/start", start),
            web.get("/oauth/callback", callback),
        ]
    )
    return app


async def index(req: web.Request) -> web.Response:
    bot = req.app[BOT]
    mode = "холостой" if bot.cfg.dry_run else "боевой"
    return web.Response(text=f"Apparitor: бот доступов АК рувики. Режим: {mode}. Код: {bot.cfg.repo_url}\n")


async def healthz(req: web.Request) -> web.Response:
    """Для проверки после деплоя: Discord-сессия жива и БД отвечает."""
    bot = req.app[BOT]
    discord_ok = bot.is_ready() and not bot.is_closed()
    db_ok = await bot.store.health()
    ok = discord_ok and db_ok
    return web.Response(
        status=200 if ok else 503,
        text="ok" if ok else f"discord: {'ok' if discord_ok else 'not ready'}; db: {'ok' if db_ok else 'fail'}",
    )


async def start(req: web.Request) -> web.Response:
    """Ссылка из /login: state уже выдан боту, переадресуем на Мету."""
    bot = req.app[BOT]
    state = req.query.get("s", "")
    if not state or not await bot.store.pending_by_state(state):
        return web.Response(status=400, text="Неизвестная или устаревшая ссылка. Повторите /login в Discord.")
    url = f"{META}/authorize?response_type=code&client_id={os.environ['OAUTH_CLIENT_ID']}&state={state}"
    raise web.HTTPFound(url)


async def callback(req: web.Request) -> web.Response:
    bot = req.app[BOT]
    code, state = req.query.get("code"), req.query.get("state", "")
    p = await bot.store.pending_by_state(state)
    if not code or not p:
        return web.Response(status=400, text="Нет кода или state. Повторите /login в Discord.")
    if dt.datetime.now(dt.UTC) - p["issued_at"] > dt.timedelta(minutes=30):
        return web.Response(status=400, text="Ссылка устарела (30 минут). Повторите /login.")
    async with ClientSession() as s:
        async with s.post(
            f"{META}/access_token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": os.environ["OAUTH_CLIENT_ID"],
                "client_secret": os.environ["OAUTH_CLIENT_SECRET"],
            },
        ) as r:
            tok = await r.json(content_type=None) if r.content_type.endswith("json") else {}
            if r.status != 200 or "access_token" not in tok:
                return web.Response(
                    status=502, text=f"Мета не выдала токен (HTTP {r.status}): {tok.get('message', '')}"
                )
        async with s.get(f"{META}/resource/profile", headers={"Authorization": f"Bearer {tok['access_token']}"}) as r:
            if r.status != 200:
                return web.Response(status=502, text=f"Мета не отдала профиль (HTTP {r.status}).")
            prof = await r.json(content_type=None)
    if not prof.get("username"):
        return web.Response(status=502, text="В профиле нет имени участника.")
    # токен дальше не нужен и не хранится; из профиля берём только имя и глобальный id
    username, sub = prof["username"], str(prof.get("sub", ""))
    await bot.store.link(p["discord_id"], username, "oauth", sub)
    await bot.store.log(str(p["discord_id"]), "oauth-ok", username, sub)
    text = await bot.after_link(p["discord_id"], p["guild_id"], username)
    return web.Response(
        text=f"Готово: Discord-аккаунт привязан к участнику {username}.\n{text}\n"
        "Можно закрыть вкладку и вернуться в Discord."
    )


def new_state() -> str:
    return secrets.token_urlsafe(24)
