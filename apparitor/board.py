"""Доска лучших сообщений (замена Starboard): ⭐ у сообщения достигли порога — бот пересылает его в канал доски штатным
forward и пишет над ним строку «🌟 **N** | ссылка»; счётчик в строке обновляется. Текст сообщений бот не читает:
считает реакции, а пересылка идёт средствами Discord (проверено 23.11.2026 на сообщении бота в #moderbot).
Источник открыт, если его видит роль board_open_role («all») — категория открытости не гарантирует; каналы из
board_sources берутся всегда. Закрытое содержимое на открытую доску не попадает."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .bot import Apparitor

log = logging.getLogger("apparitor.board")
TIERS = ((20, "💫"), (10, "🌟"), (0, "⭐"))  # как на доске Starboard в #memes (по данным 23.11.2026)


def tier(count: int) -> str:
    return next(e for lo, e in TIERS if count >= lo)


class Board:
    def __init__(self, bot: Apparitor):
        self.bot = bot
        self._told: dict[int, int] = {}  # сообщение -> число звёзд в последнем отчёте холостого режима
        self._locks: dict[
            int, list
        ] = {}  # сообщение -> [замок, сколько задач ждут]: две звезды подряд — одна пересылка
        self._no_role: set[int] = set()  # серверы, где об отсутствии роли «all» уже сказано

    async def source_ok(self, channel) -> bool:
        gid = channel.guild.id
        g = self.bot.gcfg(gid)
        if channel.id == g.board_channel_id:
            return False
        if channel.id in g.board_sources:
            return True
        if g.board_categories and getattr(channel, "category_id", None) not in g.board_categories:
            return False
        if isinstance(channel, discord.Thread) and channel.is_private():
            return False
        role = discord.utils.get(channel.guild.roles, name=g.board_open_role)
        if role is None:  # без роли открытость не проверить: закрыто, и об этом знают модераторы
            if gid not in self._no_role:
                self._no_role.add(gid)
                await self.bot.report(
                    gid, f"Доска: нет роли «{g.board_open_role}», открытость каналов не проверить — не работаю."
                )
            return False
        return channel.permissions_for(role).view_channel

    async def stars(self, msg: discord.Message, exact: bool) -> int:
        """exact=False: ниже порога точное число не нужно (звезда автора вычитает одну) — users() не перебираем."""
        g = self.bot.gcfg(msg.guild.id)
        r = discord.utils.get(msg.reactions, emoji=g.board_emoji)
        if not r:
            return 0
        n = r.count
        if not exact and n < g.board_threshold:
            return n
        if not g.board_self_star:  # свою звезду не считаем: узнаём, поставил ли её автор
            async for u in r.users():
                if u.id == msg.author.id:
                    n -= 1
                    break
        return n

    async def reaction(self, p: discord.RawReactionActionEvent) -> None:
        gid = p.guild_id
        g = self.bot.gcfg(gid) if gid else None
        mode = self.bot.feature(gid, "starboard") if gid else "off"
        if mode == "off" or not g.board_channel_id or str(p.emoji) != g.board_emoji:
            return
        guild = self.bot.get_guild(gid)
        channel = guild and guild.get_channel_or_thread(p.channel_id)
        if not channel or not await self.source_ok(channel):
            return
        lock = self._locks.setdefault(p.message_id, [asyncio.Lock(), 0])
        lock[1] += 1
        try:
            async with lock[0]:
                await self.update(p.message_id, channel, guild, g, mode)
        finally:
            lock[1] -= 1
            if not lock[1]:  # ждущих нет — замок не копим
                self._locks.pop(p.message_id, None)

    async def update(self, mid: int, channel, guild, g, mode: str) -> None:
        try:
            msg = await channel.fetch_message(mid)
        except discord.HTTPException as e:
            log.warning("доска: сообщение %s: %s", mid, e)
            return
        if msg.author.bot:
            return
        rec = await self.bot.store.board_get(msg.id)
        count = await self.stars(msg, exact=rec is not None)
        if count < g.board_threshold and not rec:
            return
        if mode == "dry":
            if self._told.get(msg.id) != count:
                self._told[msg.id] = count
                act = "обновил бы счётчик" if rec else f"переслал бы в <#{g.board_channel_id}>"
                await self.bot.report(guild.id, f"[холостой] доска: {tier(count)} {count} у {msg.jump_url}: {act}.")
            return
        await self.publish(msg, count, rec, guild.get_channel(g.board_channel_id))

    async def publish(self, msg: discord.Message, count: int, rec, board) -> None:
        gid = msg.guild.id
        line = f"{tier(count)} **{count}** | {msg.jump_url}"
        try:
            if rec is None:
                head = await board.send(line, allowed_mentions=discord.AllowedMentions.none())
                fwd = await msg.forward(board)
                await self.bot.store.board_set(gid, msg.id, head.id, fwd.id, count)
            elif rec[2] != count:
                await board.get_partial_message(rec[0]).edit(content=line)
                await self.bot.store.board_set(gid, msg.id, rec[0], rec[1], count)
        except discord.HTTPException as e:
            await self.bot.report(gid, f"Доска: не вышло переслать {msg.jump_url}: {e}")
        else:
            await self.bot.store.log("board", "post" if rec is None else "count", str(msg.id), str(count))
