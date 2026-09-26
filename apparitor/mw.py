"""Клиент API MediaWiki: один на вики, без знания о группах и статусах. Только чтение, без аккаунта;
запись (арбвики: userrights, createaccount) добавится сюда же — с токенами и логином."""

from __future__ import annotations

import asyncio

import aiohttp

UA = "Apparitor/0.1 (https://ru.wikipedia.org/wiki/User:Carn; access bot for ruwiki ArbCom Discord)"


ATTEMPTS = 4


class Client:
    def __init__(self, api_url: str, user_agent: str = UA, session: aiohttp.ClientSession | None = None):
        """Сессия создаётся лениво при первом запросе (внутри цикла событий) либо передаётся готовая."""
        self.api_url = api_url
        self.headers = {"User-Agent": user_agent}
        self._session = session

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def get(self, **params) -> dict:
        """GET с форматом json/formatversion=2. 429 — пауза по Retry-After (иначе 1, 2, 4 с), четыре попытки."""
        params.setdefault("format", "json")
        params.setdefault("formatversion", "2")
        for attempt in range(ATTEMPTS):
            async with self.session.get(self.api_url, params=params, headers=self.headers) as r:
                if r.status != 429:
                    r.raise_for_status()
                    return await r.json()
                if attempt < ATTEMPTS - 1:
                    await asyncio.sleep(_retry_after(r.headers.get("Retry-After"), 2**attempt))
        raise RuntimeError(f"{self.api_url}: 429 после {ATTEMPTS} попыток")

    async def raw_page(self, title: str) -> str | None:
        """Текст последней версии страницы (для JSON-страниц гаджетов); None — страницы нет."""
        d = await self.get(action="query", prop="revisions", rvprop="content", rvslots="main", titles=title)
        page = d["query"]["pages"][0]
        if "missing" in page:
            return None
        return page["revisions"][0]["slots"]["main"]["content"]


def _retry_after(header: str | None, default: float) -> float:
    try:
        return min(float(header), 30.0) if header else default
    except ValueError:
        return default
