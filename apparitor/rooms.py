"""Каналы по реакциям на сообщениях бота: новые каналы отслеживаемых категорий получают роль доступа и
строку с реакцией; в том же сообщении по строке «− #канал — выйти» на каждый optout-канал (readme, announcements):
реакция прячет канал (запрет на участника), его новые сообщения бот пересылает в личку. Пересылка — штатная
пересылка Discord (forward): текст сообщений боту читать не нужно."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .bot import Apparitor

log = logging.getLogger("apparitor.rooms")
POOL = [chr(0x1F1E6 + i) for i in range(26)]  # 🇦…🇿 — реакции новых каналов
PER_MESSAGE = 20  # предел реакций Discord на одно сообщение
OPTOUT_POOL = ["🚪", "🔕", "📤", "🛑"]  # строки выхода, если эмодзи канала не задан в конфиге; не заняты у Carl
HEADER = "Каналы: поставьте реакцию — войти, снимите — выйти."
BACKFILL_LIMIT = 30  # при выходе из канала прислать его целиком, если сообщений не больше (readme)


def plain(emoji: str) -> str:
    """Ключ эмодзи для сравнения: у своего — без селектора варианта U+FE0F (Discord отдаёт то с ним, то без),
    у кастомного — его id (в выгрузке Carl-bot кастомные записаны id, у события — как <:имя:id>)."""
    if m := re.fullmatch(r"<a?:\w+:(\d+)>", emoji):
        return m.group(1)
    return emoji.replace("\ufe0f", "")


class Rooms:
    def __init__(self, bot: Apparitor):
        self.bot = bot
        self._ours: set[int] | None = None  # id сообщений бота с реакциями: без запроса в БД на каждую реакцию
        self._latest: dict[tuple[int, int, str], bool] = {}  # (сообщение, участник, эмодзи) -> реакция сейчас стоит
        self._entries = asyncio.Lock()  # строки в сообщении бота дописываются по одной
        self._told: set[int] = set()  # серверы, где холостой отчёт про строки выхода уже был

    async def ours(self) -> set[int]:
        if self._ours is None:
            # сообщения Carl-bot из конфига (reaction_roles) и сообщения самого бота из БД
            ids: set[int] = {int(m) for g in self.bot.cfg.guild.values() for m in g.reaction_roles}
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
        async with self._entries:
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
                if n < PER_MESSAGE:
                    try:
                        msg = await channel.fetch_message(mid)
                    except discord.NotFound:
                        continue
                    if msg.content.startswith(HEADER):
                        await msg.edit(content=f"{msg.content}\n{emoji} {label}")
                        break
                    msg = None
            if msg is None:
                msg = await channel.send(f"{HEADER}\n{emoji} {label}", allowed_mentions=discord.AllowedMentions.none())
            await msg.add_reaction(emoji)
            await self.bot.store.add_reaction(guild.id, msg.id, emoji, action, target)
            (await self.ours()).add(msg.id)
            return emoji

    async def ensure_optout(self, guild: discord.Guild) -> None:
        """Строка «− #канал — выйти» в сообщении бота для каждого optout-канала, где её ещё нет. Холостой режим —
        только отчёт, раз за запуск."""
        g = self.bot.gcfg(guild.id)
        if not g.roles_channel_id or not g.optout_channels:
            return
        rows = await self.bot.store.reactions_of(guild.id)
        have = {t for _, _, a, t in rows if a == "optout"}
        used = {e for _, e, _, _ in rows}
        for cid in g.optout_channels:
            ch = guild.get_channel(cid)
            if cid in have or not ch:
                continue
            emoji = g.optout_emoji.get(str(cid)) or next((e for e in OPTOUT_POOL if e not in used), "")
            label = f"− #{ch.name} — выйти (сообщения придут в личку)"
            used.add(emoji)
            if self.bot.dry(guild.id):
                if cid not in self._told:
                    self._told.add(cid)
                    await self.bot.report(
                        guild.id,
                        f"[холостой] добавил бы в сообщение бота в <#{g.roles_channel_id}> строку: {emoji} {label}",
                    )
                continue
            if guild.get_channel(g.roles_channel_id) is None:
                await self.bot.report(guild.id, "Строки выхода не добавлены: бот не видит канал ролей.")
                return
            await self.add_entry(guild, label, "optout", cid, emoji)

    # --- реакции -----------------------------------------------------------------------
    async def reaction(self, p: discord.RawReactionActionEvent, added: bool) -> None:
        if not p.guild_id or (self.bot.user and p.user_id == self.bot.user.id):
            return
        if p.message_id not in await self.ours():
            return
        if (table := self.bot.gcfg(p.guild_id).reaction_roles.get(str(p.message_id))) is not None:
            await self.config_reaction(p, added, table)
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
            elif self.bot.dry(p.guild_id):
                verb = "выдал бы" if added else "снял бы"
                await self.bot.report(
                    p.guild_id, f"[холостой] реакция {p.emoji} от {member.mention}: {verb} «{role.name}»."
                )
            elif added:
                await member.add_roles(role, reason="Apparitor: вход по реакции")
            else:
                await member.remove_roles(role, reason="Apparitor: выход по реакции")
        elif action == "optout":
            await self.optout(member, added, target, p)

    async def config_reaction(self, p: discord.RawReactionActionEvent, added: bool, table: dict[str, str]) -> None:
        """Роли по реакциям на сообщениях, которые ведёт Carl-bot: таблица эмодзи → роль в конфиге."""
        gid = p.guild_id
        mode = self.bot.feature(gid, "reaction_roles")
        name = {plain(k): v for k, v in table.items()}.get(plain(str(p.emoji)))
        if mode == "off" or not name:
            return
        member = p.member or await self.bot.member(gid, p.user_id)
        if not member or member.bot:
            return
        key = (p.message_id, member.id, plain(str(p.emoji)))
        self._latest[key] = added
        had_before = name in {r.name for r in member.roles}
        need = self.bot.gcfg(gid).reaction_require.get(str(p.message_id))
        denied = added and need and not any(r.name in need for r in member.roles)  # белый список сообщения
        found = [r for r in member.guild.roles if r.name == name]
        if len(found) != 1:
            await self.bot.report(
                gid, f"Реакция {p.emoji}: ролей с именем «{name}» на сервере {len(found)}, нужна одна."
            )
            return
        if mode == "dry":
            verb = "выдал бы" if added else "снял бы"
            if denied:
                verb = f"не выдал бы (нужна одна из: {', '.join(need)}); роль"
            label = f"реакция {p.emoji} {member.mention}: {verb} «{name}»"
            want_in, want_out = (
                ({name}, set()) if added and not denied else (set(), {name})
            )  # отказ = роли быть не должно

            def why() -> str:  # расхождение бывает не ошибкой: модератору так и пишем
                parts = []
                if self._latest.get(key) != added:
                    parts.append("реакцию сняли до сверки" if added else "реакцию поставили снова до сверки")
                if denied and had_before:
                    parts.append("роль была и до реакции")
                return "; ".join(parts)

            self.bot.spawn(
                self.bot.parity(member.id, gid, label, want_in, want_out, self.bot.joins.PARITY_DELAY, why=why)
            )
        elif denied:
            return  # убрать чужую реакцию без права Manage Messages нельзя; роль просто не выдаём
        elif added:
            await member.add_roles(found[0], reason="Apparitor: вход по реакции")
        else:
            await member.remove_roles(found[0], reason="Apparitor: выход по реакции")

    async def optout(self, member: discord.Member, on: bool, cid: int, p: discord.RawReactionActionEvent) -> None:
        """Реакция на строке «− #канал — выйти»: поставили — канал прячется (запрет VIEW на участника, остальные его
        записи в канале целы) и новые сообщения идут в личку; сняли — канал возвращается."""
        gid, emoji = member.guild.id, str(p.emoji)
        c = member.guild.get_channel(cid)
        if not c:
            await self.bot.report(gid, f"Реакция {emoji} {member.mention}: канала {cid} больше нет.")
            return
        if self.bot.dry(gid):
            what = f"спрятал бы #{c.name}, пересылал бы его сообщения в личку" if on else f"вернул бы #{c.name}"
            await self.bot.report(gid, f"[холостой] {emoji} {member.mention}: {what}.")
            return
        if not on:
            await self.set_view(c, member, None)
            await self.bot.store.set_optout(gid, cid, member.id, False)
            return
        try:  # сначала проверить, что личка открыта, и только потом прятать канал
            dm = await member.create_dm()
            await dm.send(
                f"Вы вышли из #{c.name} на сервере «{member.guild.name}». Его новые сообщения будут приходить сюда. "
                f"Вернуться — снять реакцию {emoji} в канале ролей."
            )
        except discord.Forbidden:
            msg = member.guild.get_channel(p.channel_id).get_partial_message(p.message_id)
            await msg.remove_reaction(p.emoji, member)
            await self.bot.report(gid, f"{member.mention}: выход из #{c.name} не сделан — личка закрыта.")
            return
        await self.set_view(c, member, False)
        await self.bot.store.set_optout(gid, cid, member.id, True)
        history = [m async for m in c.history(limit=BACKFILL_LIMIT + 1, oldest_first=True)]
        if len(history) <= BACKFILL_LIMIT:  # короткий канал (readme) — целиком, чтобы правила остались под рукой
            for m in history:
                await m.forward(dm)

    @staticmethod
    async def set_view(channel, member: discord.Member, value: bool | None) -> None:
        """Только право просмотра в записи участника: прочие его записи в канале (запрет писать и т. п.) не трогаем."""
        ow = channel.overwrites_for(member)
        ow.view_channel = value
        if ow.is_empty():
            await channel.set_permissions(member, overwrite=None, reason="Apparitor: вернулся в канал")
        else:
            await channel.set_permissions(
                member,
                overwrite=ow,
                reason="Apparitor: " + ("вышел, сообщения в личку" if value is False else "вернулся"),
            )

    # --- пересылка -------------------------------------------------------------------
    async def message(self, m: discord.Message) -> None:
        if not m.guild or m.channel.id not in self.bot.gcfg(m.guild.id).optout_channels:
            return
        if self.bot.dry(m.guild.id):
            return
        for uid in await self.bot.store.optouts(m.guild.id, m.channel.id):
            member = await self.bot.member(m.guild.id, uid)
            if not member:
                continue
            try:
                await m.forward(await member.create_dm())
            except discord.HTTPException as e:
                log.warning("пересылка %s -> %s: %s", m.id, uid, e)
