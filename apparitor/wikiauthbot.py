"""WikiAuthBot-ng как источник связок Discord -> учётка Викимедиа: GET /whois/{discord_id}.
Ключ привязан к серверу Discord (WIKIAUTHBOT_KEY_<id сервера>, выдаёт его автор); ответ — номер
глобальной учётки (CentralAuth) или «not found». API: github.com/fee1-dead/wikiauthbot-ng/blob/main/API.md"""

from __future__ import annotations

import logging
import os

import aiohttp

log = logging.getLogger("apparitor.wikiauthbot")
URL = "https://wikiauthbot-ng.toolforge.org/whois/"


class WikiAuthBot:
    def __init__(self, user_agent: str):
        self.ua = user_agent
        self._session: aiohttp.ClientSession | None = None

    @staticmethod
    def key(guild_id: int) -> str | None:
        return os.environ.get(f"WIKIAUTHBOT_KEY_{guild_id}")

    async def whois(self, guild_id: int, discord_id: int) -> int | None:
        """Номер глобальной учётки или None (не аутентифицирован). Сбой сервиса — исключение: «нет связки»
        и «не знаю» различаются, иначе упавший WikiAuthBot выглядел бы как пустая очередь."""
        key = self.key(guild_id)
        if not key:
            raise RuntimeError(f"нет WIKIAUTHBOT_KEY_{guild_id}")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": self.ua}, timeout=aiohttp.ClientTimeout(total=20)
            )
        async with self._session.get(f"{URL}{discord_id}", headers={"authorization": key}) as r:
            body = (await r.text()).strip()
            if r.status != 200:
                raise RuntimeError(f"WikiAuthBot {r.status} для {discord_id}")
        return int(body) if body.isdigit() else None

    async def close(self) -> None:
        if self._session:
            await self._session.close()
