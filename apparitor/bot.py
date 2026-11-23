"""Discord-клиент: подключение, синхронизация команд, отчёты, применение решений к участнику.
Без Message Content Intent — текст сообщений боту не нужен (пересылка в личку — штатный forward)."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from . import rules
from .config import Config, GuildCfg
from .gate import Gate
from .models import Decision, UserInfo, Verdict, listed
from .rooms import Rooms
from .ruwiki import RuWiki
from .store import Store

log = logging.getLogger("apparitor")


class Apparitor(discord.Client):
    def __init__(self, cfg: Config, members_intent: bool = True):
        intents = discord.Intents.default()  # message_content не включаем никогда
        intents.members = members_intent  # события входа и обход списка; нужен тумблер в портале
        super().__init__(intents=intents)
        self.cfg = cfg
        self.store = Store(cfg.db)
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = self.on_command_error
        self.wiki = RuWiki()  # HTTP-сессию заводит сам при первом запросе: OAuth-колбэк может прийти до Discord
        self.gate = Gate(self)
        self.rooms = Rooms(self)
        self._tasks: set[asyncio.Task] = set()

    # --- жизненный цикл ----------------------------------------------------------
    async def setup_hook(self):
        from .commands import register  # здесь, чтобы не было кольца импортов

        register(self)
        for gid in self.cfg.guilds:
            await self.sync_guild(gid)
        task = asyncio.create_task(self.gate.run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self):
        await self.wiki.mw.close()
        await self.gate.close()
        await super().close()

    # --- события каналов, реакций, сообщений ------------------------------------------
    async def on_guild_channel_create(self, ch: discord.abc.GuildChannel):
        if ch.guild.id in self.cfg.guilds:
            await self.rooms.channel_created(ch)

    async def on_raw_reaction_add(self, p: discord.RawReactionActionEvent):
        await self.rooms.reaction(p, True)

    async def on_raw_reaction_remove(self, p: discord.RawReactionActionEvent):
        await self.rooms.reaction(p, False)

    async def on_message(self, m: discord.Message):
        await self.rooms.message(m)

    async def sync_guild(self, gid: int) -> bool:
        """Команды на сервер; бота там может ещё не быть — тогда предупреждение, не падение."""
        g = discord.Object(id=gid)
        self.tree.copy_global_to(guild=g)
        try:
            await self.tree.sync(guild=g)
            return True
        except discord.Forbidden:
            log.warning("сервер %s: нет доступа (бот ещё не добавлен?) — команды не синхронизированы", gid)
            return False

    async def on_guild_join(self, guild: discord.Guild):
        if guild.id in self.cfg.guilds:
            ok = await self.sync_guild(guild.id)
            log.info("добавлен на %s (%s): команды %s", guild.name, guild.id, "готовы" if ok else "не синхронизированы")
        else:
            log.warning("добавлен на сервер вне конфига: %s (%s) — команд там нет", guild.name, guild.id)

    async def on_command_error(self, inter: discord.Interaction, error: Exception):
        log.exception("команда %s: %s", inter.command and inter.command.name, error)
        text = (
            "Нужно право Manage Roles."
            if isinstance(error, app_commands.MissingPermissions)
            else "Ошибка при выполнении; запись есть в логе бота."
        )
        try:
            if inter.response.is_done():
                await inter.followup.send(text, ephemeral=True)
            else:
                await inter.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    # --- служебное ----------------------------------------------------------------
    def dry(self, guild_id: int) -> bool:
        return self.cfg.dry_for(guild_id)

    def gcfg(self, guild_id: int) -> GuildCfg:
        return self.cfg.for_guild(guild_id)

    async def member(self, guild_id: int, discord_id: int) -> discord.Member | None:
        g = self.get_guild(guild_id)
        if not g:
            return None
        if m := g.get_member(discord_id):
            return m
        try:
            return await g.fetch_member(discord_id)
        except discord.NotFound:
            return None

    async def report(self, guild_id: int, text: str, file: discord.File | None = None) -> bool:
        """Строка в служебный канал сервера. В холостом режиме — единственный выход. True = дошло."""
        cid = self.gcfg(guild_id).report_channel_id
        ch = self.get_channel(cid) if cid else None
        if not ch:
            log.warning("сервер %s: служебный канал %s не найден; отчёт: %s", guild_id, cid, text)
            return False
        try:
            await ch.send(text[:1900], file=file)
            return True
        except discord.Forbidden:
            log.warning("сервер %s: нет права писать в #%s; отчёт: %s", guild_id, ch.name, text)
            return False

    def describe(self, guild_id: int, member: discord.Member, info: UserInfo) -> str:
        """Кто это — для отчёта. Связку ник ↔ вики-аккаунт показываем только при report_names."""
        if self.gcfg(guild_id).report_names:
            return f"{member.mention} ↔ **{info.name}** ({info.labels_text})"
        return f"{member.mention}: вики-аккаунт подтверждён"

    # --- решение по участнику ---------------------------------------------------------
    async def evaluate(
        self, member: discord.Member, info: UserInfo | None, wiki_name: str, quiet: bool = False
    ) -> Verdict:
        """Единая точка: критерии впуска → роли (или снятие всех управляемых при отказе).
        Вызывается из /confirm, /link, OAuth-колбэка, /sync и обхода новичков (quiet: без строки на каждого
        в служебный канал — обход шлёт сводку сам)."""
        gid = member.guild.id
        if not info:
            shown = f" ({wiki_name})" if self.gcfg(gid).report_names else ""
            if not quiet:
                await self.report(gid, f"{member.mention}: в рувики нет такого участника{shown}")
            return Verdict(f"В рувики нет участника {wiki_name}.", notable=True)
        d = rules.decide(self.cfg.admit_for(gid), self.gcfg(gid), info, {r.name for r in member.roles})
        missing = await self.apply(member, info, d, quiet)
        admitted = self.gcfg(gid).roles.get("admitted")
        drops = bool(admitted) and admitted in d.remove
        if not d.ok:
            await self.store.log(str(member.id), "reject", info.name, d.why)
            return Verdict(f"Критерии впуска не пройдены: {d.why}.", notable=True, drops_admitted=drops)
        roles = f"выдать {listed(d.add)}, снять {listed(d.remove)}" + (
            f", на сервере нет {listed(missing)}" if missing else ""
        )
        if self.dry(gid):
            if quiet:  # строку в служебный канал шлёт вызывающий: «отчёт в служебном канале» было бы дублем
                return Verdict(f"роли не менялись, было бы: {roles}.", d.changes, drops)
            return Verdict(
                f"Холостой режим: роли не менялись (было бы: {roles}); отчёт в служебном канале.", d.changes, drops
            )
        return Verdict(f"Роли: {roles}.", d.changes, drops)

    async def apply(self, member: discord.Member, info: UserInfo, d: Decision, quiet: bool = False) -> list[str]:
        """Применяет решение (или только отчитывается вхолостую). Возвращает имена ролей, которых нет на сервере."""
        guild = member.guild
        dry = self.dry(guild.id)
        add = [discord.utils.get(guild.roles, name=n) for n in d.add]
        missing = [n for n, r in zip(d.add, add, strict=True) if r is None]
        add = [r for r in add if r is not None]
        rem = [r for r in member.roles if r.name in d.remove]
        reason = "" if d.ok else d.why
        if not quiet and (add or rem or reason or dry):  # вхолостую отчитываемся всегда, иначе тест не виден
            await self.report(
                guild.id,
                f"{'[холостой] ' if dry else ''}{self.describe(guild.id, member, info)}"
                f"{'; отказ: ' + reason if reason else ''}: выдать {listed(d.add)}, снять {listed(d.remove)}"
                + (f", нет ролей {listed(missing)}" if missing else ""),
            )
        if not dry:
            if add:
                await member.add_roles(*add, reason=f"Apparitor: {info.name} ({info.labels_text})")
            if rem:
                await member.remove_roles(*rem, reason=f"Apparitor: {reason or 'sync'}")
        await self.store.log(str(member.id), "roles-dry" if dry else "roles", info.name, f"+{d.add} -{d.remove}")
        return missing

    async def after_link(self, discord_id: int, guild_id: int, wiki_name: str) -> str:
        """Из OAuth-колбэка: участник по id → evaluate."""
        m = await self.member(guild_id, discord_id)
        if not m:
            return "Вас нет на сервере Discord, роли выдать некому."
        info = await self.wiki.user_info(wiki_name)
        return (await self.evaluate(m, info, wiki_name)).text
