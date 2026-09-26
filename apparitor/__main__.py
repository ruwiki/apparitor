"""Один процесс: Discord-клиент + HTTP (OAuth-колбэк, healthz). Процесс `web` из Procfile;
порт из $PORT (его даёт Toolforge webservice), локально 8080."""
import asyncio
import logging
import os

import discord
from aiohttp import web

from .bot import Apparitor, load_config, load_env, read_token
from .web import make_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main():
    load_env()
    cfg = load_config(os.environ.get("APPARITOR_CONFIG", "config.toml"))
    token = read_token()
    for members in (True, False):
        bot = Apparitor(cfg, members_intent=members)
        runner = web.AppRunner(make_app(bot))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "8080"))).start()
        try:
            await bot.start(token)
            return
        except discord.PrivilegedIntentsRequired:
            logging.getLogger("apparitor").error(
                "Server Members Intent не включён в Developer Portal → Bot; запускаюсь без него "
                "(нет событий входа и обхода участников)")
        finally:
            await runner.cleanup()


asyncio.run(main())
