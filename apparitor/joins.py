"""Вход и выход участников — то, что на сервере сообщества делал Carl-bot: роли при входе, возврат прежних ролей,
приветствие и прощание. Каждая функция включается ключом в [guild.<id>.features]: dry — бот пишет в служебный канал,
что сделал бы, и через PARITY_DELAY секунд сверяет с тем, что Carl сделал на самом деле; live — действует сам.
Текст сообщений не читается: нужны только события участника (Server Members Intent)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random
from typing import TYPE_CHECKING

import discord

from .models import TS_FMT, listed, parse_ts

if TYPE_CHECKING:
    from .bot import Apparitor

log = logging.getLogger("apparitor.joins")

# Роль с любым из этих прав бот сам не возвращает: возврат прав управления — решение человека.
DANGEROUS = discord.Permissions(
    administrator=True,
    manage_guild=True,
    manage_roles=True,
    manage_channels=True,
    manage_webhooks=True,
    manage_messages=True,
    manage_threads=True,
    ban_members=True,
    kick_members=True,
    moderate_members=True,
    mention_everyone=True,
    view_audit_log=True,
).value
FAREWELL_COLOR = 5198940  # цвет полоски у прощания Carl-bot


def sensitive(role: discord.Role) -> bool:
    return bool(role.permissions.value & DANGEROUS)


class Joins:
    PARITY_DELAY = 45  # секунд до сверки с Carl: он действует за секунды, запас на сбои
    LATE_NEW = dt.timedelta(minutes=15)  # 🆕, пришедшая так скоро после входа, — выдача Carl по таймеру, не человек

    def __init__(self, bot: Apparitor):
        self.bot = bot
        self._waiting: set[tuple[int, int]] = set()  # (сервер, участник): таймер 🆕 уже запущен в этом процессе

    # --- роли ушедшего ---------------------------------------------------------------------
    def split(self, guild: discord.Guild, role_ids: list[int], skip: list[str]):
        """(вернуть, не возвращать по правилу, выше роли бота). Удалённые, служебные и @everyone пропадают молча."""
        ok, held, above = [], [], []
        for rid in role_ids:
            role = guild.get_role(rid)
            if not role or role.is_default() or role.managed:
                continue
            if role.name in skip or sensitive(role):
                held.append(role)
            elif not role.is_assignable():
                above.append(role)
                ok.append(role)  # по правилу вернули бы; мешает только позиция роли бота
            else:
                ok.append(role)
        return ok, held, above

    # --- вход -----------------------------------------------------------------------------
    async def joined(self, m: discord.Member) -> None:
        gid, g = m.guild.id, self.bot.gcfg(m.guild.id)
        if m.bot:
            return
        if self.bot.feature(gid, "join_roles") != "off" and g.join_roles:
            if g.join_delay:  # Carl-bot выдаёт 🆕 через 240 с после входа, а не сразу; повторяем, пока не решено иное
                due = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=g.join_delay)
                await self.bot.store.join_due_set(gid, m.id, due.strftime(TS_FMT))  # рестарт не теряет выдачу
                self.bot.spawn(self.delayed_join_roles(gid, m.id, g.join_delay))
            else:
                await self.give(m, "join_roles", list(g.join_roles), [], [])
        if self.bot.feature(gid, "sticky_roles") != "off":
            ok, held, above = self.split(m.guild, await self.bot.store.sticky_get(gid, m.id), g.sticky_skip)
            if ok or held:
                await self.give(m, "sticky_roles", [r.name for r in ok], held, above)
        await self.greet(m)

    async def delayed_join_roles(self, gid: int, mid: int, delay: float) -> None:
        if (gid, mid) in self._waiting:
            return
        self._waiting.add((gid, mid))
        try:
            await asyncio.sleep(delay)
            m = await self.bot.member(gid, mid)  # за это время мог выйти
            if m:
                g = self.bot.gcfg(gid)
                names = list(g.join_roles)
                if g.roles.get("admitted") in {r.name for r in m.roles}:
                    # уже впущен (/login за эти минуты): 🆕 после ✔ открыла бы ему старые каналы новичков
                    skip = [n for n in names if n in g.remove_on_confirm]
                    names = [n for n in names if n not in skip]
                    if skip:
                        await self.bot.store.log(str(mid), "join-skip", "join_roles", listed(skip))
                        if self.bot.feature(gid, "join_roles") == "dry":
                            await self.bot.report(
                                gid, f"[холостой] вход {m.mention}: {listed(skip)} не выдал бы — уже впущен."
                            )
                if names:
                    await self.give(m, "join_roles", names, [], [], waited=max(delay, 1))
        finally:
            self._waiting.discard((gid, mid))
        # не в finally: остановка процесса посреди ожидания должна оставить срок в БД для resume
        await self.bot.store.join_due_delete(gid, mid)

    async def resume(self) -> None:
        """После рестарта: выдать 🆕 тем, чей таймер шёл в прошлом процессе (просроченным — сразу)."""
        now = dt.datetime.now(dt.UTC)
        for gid, mid, due in await self.bot.store.join_due_all():
            if gid in self.bot.cfg.guilds and (gid, mid) not in self._waiting:
                self.bot.spawn(self.delayed_join_roles(gid, mid, max(0.0, (parse_ts(due) - now).total_seconds())))

    async def updated(self, before: discord.Member, after: discord.Member) -> None:
        """Carl выдаёт 🆕 через 240 с после входа, даже если человек уже впущен ботом по /login. Такую 🆕 бот снимает
        (как при впуске, remove_on_confirm), только если она пришла в первые LATE_NEW после входа: позже — это
        решение человека, его не трогаем."""
        gid, g = after.guild.id, self.bot.gcfg(after.guild.id)
        if after.bot or self.bot.feature(gid, "join_roles") != "live":
            return
        have, had = {r.name for r in after.roles}, {r.name for r in before.roles}
        late = [r for r in after.roles if r.name in g.remove_on_confirm and r.name not in had]
        if not late or g.roles.get("admitted") not in have:
            return
        if not after.joined_at or dt.datetime.now(dt.UTC) - after.joined_at > self.LATE_NEW:
            return
        try:
            await after.remove_roles(*late, reason="Apparitor: уже впущен, роль новичка выдана после впуска")
        except discord.Forbidden:
            await self.bot.report(gid, f"{after.mention}: нет права снять {listed([r.name for r in late])}.")
            return
        await self.bot.store.log(str(after.id), "join-late-new", "remove", listed([r.name for r in late]))

    async def give(
        self, m: discord.Member, feat: str, names: list[str], held: list, above: list, waited: int = 0
    ) -> None:
        gid = m.guild.id
        note = (
            f"; не возвращаю (права управления или список исключений): {listed([r.name for r in held])}" if held else ""
        )
        if self.bot.feature(gid, feat) == "dry":
            label = f"вход {m.mention}: {'вернул бы' if feat == 'sticky_roles' else 'выдал бы'} {listed(names)}{note}"
            if above:
                label += f" (выше роли бота: {listed([r.name for r in above])})"
            check = self.bot.parity(m.id, gid, label, set(names), set(), self.PARITY_DELAY, {r.name for r in held})
            if waited:  # уже внутри фоновой задачи
                await check
            else:
                self.bot.spawn(check)
            return
        roles = [r for n in names if (r := discord.utils.get(m.guild.roles, name=n)) is not None]
        missing = [n for n in names if not any(r.name == n for r in roles)]
        blocked = {r.id for r in above}
        roles = [r for r in roles if r.id not in blocked]
        try:
            if roles:
                await m.add_roles(*roles, reason=f"Apparitor: {'возврат ролей' if feat == 'sticky_roles' else 'вход'}")
        except discord.Forbidden:
            await self.bot.report(gid, f"{m.mention}: нет права выдать {listed([r.name for r in roles])} при входе.")
            return
        if feat == "sticky_roles":
            await self.bot.store.sticky_delete(gid, m.id)
        bad = f"; нет на сервере: {listed(missing)}" if missing else ""
        up = f"; выше роли бота: {listed([r.name for r in above])}" if above else ""
        # 🆕 каждому входящему — не новость для модераторов: строка только при сбое; возврат ролей — всегда
        if feat == "sticky_roles" or note or bad or up:
            await self.bot.report(gid, f"вход {m.mention}: выдал {listed([r.name for r in roles])}{note}{bad}{up}")
        await self.bot.store.log(str(m.id), "join-roles", feat, listed([r.name for r in roles]))

    async def greet(self, m: discord.Member) -> None:
        gid, g = m.guild.id, self.bot.gcfg(m.guild.id)
        mode = self.bot.feature(gid, "welcome")
        if mode == "off" or not g.welcome_channel_id or not g.welcome_text:
            return
        if mode == "dry":
            await self.bot.report(
                gid, f"[холостой] вход {m.mention}: написал бы приветствие в <#{g.welcome_channel_id}>"
            )
            return
        ch = m.guild.get_channel(g.welcome_channel_id)
        if ch:
            await ch.send(
                g.welcome_text.replace("{mention}", m.mention), allowed_mentions=discord.AllowedMentions(users=[m])
            )

    # --- выход ----------------------------------------------------------------------------
    async def left(self, m: discord.Member) -> None:
        gid, g = m.guild.id, self.bot.gcfg(m.guild.id)
        if m.bot:
            return
        if self.bot.feature(gid, "sticky_roles") != "off":
            # запись идёт и в dry: это только база бота, а к live данные должны быть накоплены
            ids = [r.id for r in m.roles if not r.is_default() and not r.managed]
            await self.bot.store.sticky_save(gid, m.id, ids)
            await self.bot.store.sticky_purge(gid, self.cutoff(g.sticky_days))
        mode = self.bot.feature(gid, "farewell")
        if mode == "off" or not g.farewell_channel_id or not g.farewell_texts:
            return
        if mode == "dry":
            await self.bot.report(
                gid, f"[холостой] выход {m.mention} ({m.name}): написал бы прощание в <#{g.farewell_channel_id}>"
            )
            return
        ch = m.guild.get_channel(g.farewell_channel_id)
        if ch:
            await ch.send(embed=self.farewell(m, random.choice(g.farewell_texts)))

    @staticmethod
    def farewell(m: discord.Member, phrase: str) -> discord.Embed:
        e = discord.Embed(
            title=f":wave_tone2: {m.display_name}",
            description=f"{m.mention} ({m.name}),  {phrase}",
            color=FAREWELL_COLOR,
        )
        e.set_footer(text=f"Нас осталось {m.guild.member_count}")
        return e

    @staticmethod
    def cutoff(days: int) -> str:
        return (dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).strftime(TS_FMT)
