"""Доска лучших сообщений (замена Starboard): ⭐ у сообщения достигли порога — бот пересылает его в канал доски штатным
forward и пишет над ним строку «🌟 **N** | ссылка»; счётчик в строке обновляется. Текст сообщений бот не читает:
считает реакции, а пересылка идёт средствами Discord (проверено 23.11.2026 на сообщении бота в #moderbot).
Источники — только каналы из конфига: закрытое содержимое на открытую доску не попадает."""

from __future__ import annotations

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

    def source_ok(self, channel) -> bool:
        g = self.bot.gcfg(channel.guild.id)
        if channel.id == g.board_channel_id:
            return False
        return channel.id in g.board_sources or getattr(channel, "category_id", None) in g.board_categories

    async def stars(self, msg: discord.Message) -> int:
        g = self.bot.gcfg(msg.guild.id)
        r = discord.utils.get(msg.reactions, emoji=g.board_emoji)
        if not r:
            return 0
        n = r.count
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
        if not channel or not self.source_ok(channel):
            return
        try:
            msg = await channel.fetch_message(p.message_id)
        except discord.HTTPException as e:
            log.warning("доска: сообщение %s: %s", p.message_id, e)
            return
        if msg.author.bot:
            return
        count = await self.stars(msg)
        rec = await self.bot.store.board_get(msg.id)
        if count < g.board_threshold and not rec:
            return
        if mode == "dry":
            if self._told.get(msg.id) != count:
                self._told[msg.id] = count
                act = "обновил бы счётчик" if rec else f"переслал бы в <#{g.board_channel_id}>"
                await self.bot.report(gid, f"[холостой] доска: {tier(count)} {count} у {msg.jump_url}: {act}.")
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
