"""Клиент API MediaWiki: один на вики, без знания о группах и статусах. Только чтение, без аккаунта;
запись (арбвики: userrights, createaccount) добавится сюда же — с токенами и логином."""

from __future__ import annotations

import asyncio

import aiohttp

UA = "Apparitor/0.1 (https://ru.wikipedia.org/wiki/User:Carn; access bot for ruwiki ArbCom Discord)"


class Client:
    def __init__(self, session: aiohttp.ClientSession, api_url: str, user_agent: str = UA):
        self.session = session
        self.api_url = api_url
        self.headers = {"User-Agent": user_agent}

    async def get(self, **params) -> dict:
        """GET с форматом json/formatversion=2; 429 — экспоненциальная пауза, четыре попытки."""
        params.setdefault("format", "json")
        params.setdefault("formatversion", "2")
        for attempt in range(4):
            async with self.session.get(self.api_url, params=params, headers=self.headers) as r:
                if r.status == 429:
                    await asyncio.sleep(2**attempt)
                    continue
                r.raise_for_status()
                return await r.json()
        raise RuntimeError(f"{self.api_url}: 429 после 4 попыток")

    async def raw_page(self, title: str) -> str | None:
        """Текст последней версии страницы (для JSON-страниц гаджетов); None — страницы нет."""
        d = await self.get(action="query", prop="revisions", rvprop="content", rvslots="main", titles=title)
        page = d["query"]["pages"][0]
        if "missing" in page:
            return None
        return page["revisions"][0]["slots"]["main"]["content"]
