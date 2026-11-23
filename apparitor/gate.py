"""Обход непроверенных участников: связка из WikiAuthBot → правило впуска сервера → роли.
Вхолостую — сводка в служебный канал: кого бот впустил бы и кому отказал бы, без записи ролей."""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
from typing import TYPE_CHECKING

import discord

from .mw import UA
from .wikiauthbot import WikiAuthBot

if TYPE_CHECKING:
    from .bot import Apparitor

log = logging.getLogger("apparitor.gate")
TICK = 300  # секунд между обходами
FRESH = dt.timedelta(days=2)  # недавно зашедших проверяем каждый обход, остальных — раз в STALE
STALE = dt.timedelta(hours=6)
INLINE = 5  # больше строк — одной сводкой с файлом


class Gate:
    def __init__(self, bot: Apparitor):
        self.bot = bot
        self.wab = WikiAuthBot(UA)
        self.last: dict[int, dt.datetime] = {}  # discord_id -> когда спрашивали WikiAuthBot
        self.told: dict[int, str] = {}  # discord_id -> последний отчёт: одинаковый не повторяем

    def watched(self) -> list[int]:
        return [g for g in self.bot.cfg.guilds if self.bot.gcfg(g).gate_watch and WikiAuthBot.key(g)]

    async def run(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            for gid in self.watched():
                try:
                    await self.sweep(gid)
                except Exception:
                    log.exception("обход сервера %s", gid)
            await asyncio.sleep(TICK)

    def due(self, m: discord.Member, now: dt.datetime) -> bool:
        last = self.last.get(m.id)
        if last is None:
            return True
        fresh = m.joined_at and now - m.joined_at < FRESH
        return now - last >= (dt.timedelta(seconds=TICK - 5) if fresh else STALE)

    async def sweep(self, gid: int) -> None:
        guild = self.bot.get_guild(gid)
        admitted = self.bot.gcfg(gid).roles.get("admitted")
        if not guild or not admitted:
            return
        now = dt.datetime.now(dt.UTC)
        lines = []
        for m in [m for m in guild.members if not m.bot and not any(r.name == admitted for r in m.roles)]:
            if not self.due(m, now):
                continue
            self.last[m.id] = now
            try:
                line = await self.check(m)
            except Exception as e:  # сбой WikiAuthBot/вики по одному участнику не валит обход
                log.warning("проверка %s: %s", m.id, e)
                continue
            if line and self.told.get(m.id) != line:
                self.told[m.id] = line
                lines.append(line)
        await self.tell(gid, lines)

    async def check(self, m: discord.Member) -> str | None:
        """Строка отчёта или None, если связки в WikiAuthBot нет."""
        gid = m.guild.id
        central = await self.wab.whois(gid, m.id)
        if central is None:
            return None
        name = await self.bot.wiki.name_by_central_id(central)
        if not name:
            return None
        if not await self.bot.store.wiki_of(m.id):
            await self.bot.store.link(m.id, name, "wikiauthbot", f"central {central}")
        info = await self.bot.wiki.user_info(name)
        verdict = await self.bot.evaluate(m, info, name, quiet=True)
        who = f"{m.mention} ({m.name}) ↔ **{name}**" if self.bot.gcfg(gid).report_names else m.mention
        return f"{who}: {verdict.text}"

    async def tell(self, gid: int, lines: list[str]) -> None:
        if not lines:
            return
        tag = "[холостой] " if self.bot.dry(gid) else ""
        if len(lines) <= INLINE:
            for line in lines:
                await self.bot.report(gid, f"{tag}WikiAuthBot: {line}")
            return
        head = f"{tag}Обход непроверенных по WikiAuthBot: новых итогов {len(lines)}, список файлом."
        body = "\n".join(lines).encode()
        await self.bot.report(gid, head, file=discord.File(io.BytesIO(body), filename=f"gate-{gid}.txt"))

    async def close(self) -> None:
        await self.wab.close()
