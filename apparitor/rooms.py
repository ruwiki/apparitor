"""Каналы по реакциям на сообщениях бота: новые каналы отслеживаемых категорий получают роль доступа и
строку с реакцией; реакция 📭 — выйти из optout-каналов (readme, announcements), их новые сообщения бот
пересылает в личку. Пересылка — штатная пересылка Discord (forward): текст сообщений боту читать не нужно."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .bot import Apparitor

log = logging.getLogger("apparitor.rooms")
POOL = [chr(0x1F1E6 + i) for i in range(26)]  # 🇦…🇿 — реакции новых каналов
PER_MESSAGE = 20  # предел реакций Discord на одно сообщение
OPTOUT = "📭"
HEADER = "Новые каналы: поставьте реакцию — войти, снимите — выйти."
BACKFILL_LIMIT = 30  # при выходе из канала прислать его целиком, если сообщений не больше (readme)


class Rooms:
    def __init__(self, bot: Apparitor):
        self.bot = bot
        self._ours: set[int] | None = None  # id сообщений бота с реакциями: без запроса в БД на каждую реакцию

    async def ours(self) -> set[int]:
        if self._ours is None:
            ids: set[int] = set()
            for gid in self.bot.cfg.guilds:
                ids |= {m for m, *_ in await self.bot.store.reactions_of(gid)}
            self._ours = ids
        return self._ours

    # --- новые каналы ----------------------------------------------------------------
    async def channel_created(self, ch: discord.abc.GuildChannel) -> None:
        gid = ch.guild.id
        g = self.bot.gcfg(gid)
        if not g.track_categories or not g.report_channel_id or isinstance(ch, discord.CategoryChannel):
            return
        warn = (
            " Канал виден всем, кто зашёл без проверки."
            if ch.permissions_for(ch.guild.default_role).view_channel
            else ""
        )
        prefix = g.track_categories.get(str(ch.category_id))
        if prefix is None:
            await self.bot.report(
                gid, f"Новый канал {ch.mention} вне отслеживаемых категорий — в список не добавлен.{warn}"
            )
            return
        role_name = f"{prefix}{ch.name}"
        if self.bot.dry(gid) or not g.roles_channel_id:
            await self.bot.report(
                gid,
                f"[холостой] Новый канал {ch.mention}: создал бы роль «{role_name}» с доступом к нему "
                f"и реакцию в <#{g.roles_channel_id}>.{warn}",
            )
            return
        role = discord.utils.get(ch.guild.roles, name=role_name) or await ch.guild.create_role(
            name=role_name, reason=f"Apparitor: доступ к новому каналу #{ch.name}"
        )
        await ch.set_permissions(role, view_channel=True, reason="Apparitor: вход по реакции")
        emoji = await self.add_entry(ch.guild, f"{ch.mention}", "role", role.id)
        await self.bot.report(gid, f"Новый канал {ch.mention}: роль «{role_name}», реакция {emoji}.{warn}")

    async def add_entry(self, guild: discord.Guild, label: str, action: str, target: int, emoji: str = "") -> str:
        """Строка с реакцией в сообщении бота: дописывает в неполное, иначе новое сообщение."""
        g = self.bot.gcfg(guild.id)
        channel = guild.get_channel(g.roles_channel_id)
        rows = await self.bot.store.reactions_of(guild.id)
        used = {e for _, e, _, _ in rows}
        if not emoji:
            emoji = next((e for e in POOL if e not in used), "")
            if not emoji:
                raise RuntimeError("все реакции 🇦–🇿 заняты")
        counts: dict[int, int] = {}
        for m, *_ in rows:
            counts[m] = counts.get(m, 0) + 1
        msg = None
        for mid, n in counts.items():
            if n < PER_MESSAGE and action == "role":
                try:
                    msg = await channel.fetch_message(mid)
                except discord.NotFound:
                    continue
                if msg.content.startswith(HEADER):
                    await msg.edit(content=f"{msg.content}\n{emoji} {label}")
                    break
                msg = None
        if msg is None:
            text = f"{HEADER}\n{emoji} {label}" if action == "role" else label
            msg = await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        await msg.add_reaction(emoji)
        await self.bot.store.add_reaction(guild.id, msg.id, emoji, action, target)
        (await self.ours()).add(msg.id)
        return emoji

    async def post_optout(self, guild: discord.Guild) -> str:
        """Сообщение с 📭 — один раз, по команде модератора."""
        g = self.bot.gcfg(guild.id)
        names = ", ".join(f"<#{c}>" for c in g.optout_channels)
        text = (
            f"{OPTOUT} — выйти из {names}: каналы пропадут из списка, а их новые сообщения бот будет "
            f"пересылать вам в личные сообщения. Снимите реакцию — каналы вернутся."
        )
        if self.bot.dry(guild.id):
            return f"[холостой] опубликовал бы в <#{g.roles_channel_id}>:\n{text}"
        await self.add_entry(guild, text, "optout", 0, emoji=OPTOUT)
        return "Опубликовано."

    # --- реакции -----------------------------------------------------------------------
    async def reaction(self, p: discord.RawReactionActionEvent, added: bool) -> None:
        if not p.guild_id or (self.bot.user and p.user_id == self.bot.user.id):
            return
        if p.message_id not in await self.ours():
            return
        act = await self.bot.store.reaction(p.message_id, str(p.emoji))
        if not act:
            return
        member = p.member or await self.bot.member(p.guild_id, p.user_id)
        if not member or member.bot:
            return
        action, target = act
        if action == "role":
            role = member.guild.get_role(target)
            if not role:
                await self.bot.report(p.guild_id, f"Реакция {p.emoji}: роли {target} больше нет.")
            elif added:
                await member.add_roles(role, reason="Apparitor: вход по реакции")
            else:
                await member.remove_roles(role, reason="Apparitor: выход по реакции")
        elif action == "optout":
            await self.optout(member, added, p)

    async def optout(self, member: discord.Member, on: bool, p: discord.RawReactionActionEvent | None = None) -> None:
        g = self.bot.gcfg(member.guild.id)
        chans = [c for c in (member.guild.get_channel(i) for i in g.optout_channels) if c]
        if not on:
            for c in chans:
                await c.set_permissions(member, overwrite=None, reason="Apparitor: вернулся в канал")
            await self.bot.store.set_optout(member.guild.id, member.id, False)
            return
        names = ", ".join(f"#{c.name}" for c in chans)
        try:  # сначала проверить, что личка открыта, и только потом прятать каналы
            dm = await member.create_dm()
            await dm.send(
                f"Вы вышли из {names} на сервере «{member.guild.name}». Их новые сообщения будут приходить сюда. "
                f"Вернуться — снять реакцию {OPTOUT}."
            )
        except discord.Forbidden:
            if p:
                msg = member.guild.get_channel(p.channel_id).get_partial_message(p.message_id)
                await msg.remove_reaction(p.emoji, member)
            await self.bot.report(member.guild.id, f"{member.mention}: выход из {names} не сделан — личка закрыта.")
            return
        for c in chans:
            await c.set_permissions(member, view_channel=False, reason="Apparitor: вышел, сообщения в личку")
        await self.bot.store.set_optout(member.guild.id, member.id, True)
        for c in chans:  # короткий канал (readme) — целиком, чтобы правила остались под рукой
            history = [m async for m in c.history(limit=BACKFILL_LIMIT + 1, oldest_first=True)]
            if len(history) <= BACKFILL_LIMIT:
                for m in history:
                    await m.forward(dm)

    # --- пересылка -------------------------------------------------------------------
    async def message(self, m: discord.Message) -> None:
        if not m.guild or m.channel.id not in self.bot.gcfg(m.guild.id).optout_channels:
            return
        for uid in await self.bot.store.optouts(m.guild.id):
            member = await self.bot.member(m.guild.id, uid)
            if not member:
                continue
            try:
                await m.forward(await member.create_dm())
            except discord.HTTPException as e:
                log.warning("пересылка %s -> %s: %s", m.id, uid, e)
