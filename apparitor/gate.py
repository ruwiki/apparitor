"""Обход непроверенных участников: связка из WikiAuthBot → правило впуска сервера → роли.
Вхолостую — сводка в служебный канал: кого бот впустил бы и кому отказал бы, без записи ролей."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
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
IMPORT_EVERY = dt.timedelta(hours=24)  # повторный импорт трогает только тех, у кого связки в БД ещё нет
INLINE = 5  # больше строк — одной сводкой с файлом


class Gate:
    def __init__(self, bot: Apparitor):
        self.bot = bot
        self.wab = WikiAuthBot(UA)
        self.pace = 0.7  # секунд между запросами к WikiAuthBot при импорте (как в аудите 23.11)
        self.imported: dict[int, dt.datetime] = {}  # server id -> когда запускали импорт связок
        self.last: dict[int, dt.datetime] = {}  # discord_id -> когда спрашивали WikiAuthBot
        self.told: dict[int, str] | None = None  # discord_id -> отпечаток последнего отчёта; живёт и в БД, чтобы
        # после рестарта не слать всё заново

    def watched(self) -> list[int]:
        return [g for g in self.bot.cfg.guilds if self.bot.gcfg(g).gate_watch and WikiAuthBot.key(g)]

    async def run(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            for gid in self.bot.cfg.guilds:
                self.maybe_import(gid)
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

    @staticmethod
    def digest(line: str) -> str:
        return hashlib.blake2b(line.encode(), digest_size=16).hexdigest()

    async def sweep(self, gid: int) -> None:
        if self.told is None:
            self.told = await self.bot.store.gate_told_all()
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
            if line and self.told.get(m.id) != (d := self.digest(line)):
                self.told[m.id] = d
                await self.bot.store.gate_told_set(m.id, d)
                lines.append(line)
        await self.tell(gid, lines)

    def maybe_import(self, gid: int) -> None:
        """Раз в сутки в фоне; на запуске сразу. Импорт — до ухода WikiAuthBot: после ухода связок уже не спросить."""
        now = dt.datetime.now(dt.UTC)
        if not self.bot.gcfg(gid).import_links or not WikiAuthBot.key(gid):
            return
        if (last := self.imported.get(gid)) and now - last < IMPORT_EVERY:
            return
        self.imported[gid] = now
        self.bot.spawn(self.run_import(gid))

    async def run_import(self, gid: int) -> None:
        try:
            head, body = await self.import_links(gid)
        except Exception:
            log.exception("импорт связок %s", gid)
            self.imported.pop(gid, None)  # повторим на следующем обходе
            return
        file = discord.File(io.BytesIO(body.encode()), filename=f"import-{gid}.txt") if body else None
        await self.bot.report(gid, head, file=file)

    async def import_links(self, gid: int) -> tuple[str, str]:
        """Связки всех участников сервера из WikiAuthBot в таблицу links (method wikiauthbot): чтобы после ухода
        WikiAuthBot с сервера подтверждение никто не потерял. Уже связанных не трогаем и не перепроверяем.
        Возвращает (итог строкой, список «логин<TAB>имя<TAB>номер» для файла, если report_names)."""
        guild = self.bot.get_guild(gid)
        linked = {d for d, _ in await self.bot.store.all_links()}
        todo = [m for m in guild.members if not m.bot and m.id not in linked]
        n = {"imported": 0, "no_link": 0, "no_name": 0, "errors": 0, "raced": 0}
        lines = []
        for m in todo:
            try:
                central = await self.wab.whois(gid, m.id)
                name = await self.bot.wiki.name_by_central_id(central) if central is not None else None
            except Exception as e:  # сбой по одному участнику не обрывает импорт
                log.warning("импорт %s: %s", m.id, e)
                n["errors"] += 1
                continue
            finally:
                await asyncio.sleep(self.pace)
            if central is None:
                n["no_link"] += 1
            elif not name:
                n["no_name"] += 1
            elif await self.bot.store.wiki_of(m.id):
                n["raced"] += (
                    1  # пока спрашивали WikiAuthBot, человек сам вошёл через /login или его привязали: та связка старше
                )
            else:
                await self.bot.store.link(m.id, name, "wikiauthbot", f"central {central}; импорт")
                n["imported"] += 1
                lines.append(f"{m.name}\t{name}\t{central}")
        head = (
            f"Импорт связок из WikiAuthBot: без связки в боте было {len(todo)}; импортировано {n['imported']}; "
            f"в WikiAuthBot связки нет {n['no_link']}; имя по номеру не найдено {n['no_name']}; ошибок {n['errors']}; "
            f"связка появилась за время импорта (не тронута) {n['raced']}."
        )
        await self.bot.store.log("gate", "import-links", str(gid), head)
        return head, "\n".join(lines) if self.bot.gcfg(gid).report_names else ""

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
