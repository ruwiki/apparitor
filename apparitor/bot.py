"""Discord-часть: slash-команды. Без Message Content Intent — текст сообщений боту не нужен."""
from __future__ import annotations
import logging
import os
import secrets
import tomllib
import datetime as dt

import aiohttp
import discord
from discord import app_commands

from . import wiki
from .store import Store, parse_ts
from .web import new_state

log = logging.getLogger("apparitor")
CODE_TTL = dt.timedelta(minutes=30)


def load_config(path: str = "config.toml") -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    if not cfg.get("guilds"):
        raise SystemExit("guilds в конфиге обязателен: список id серверов, на которых работает бот")
    cfg["guild"] = {int(k): v for k, v in cfg.get("guild", {}).items()}
    return cfg


class Apparitor(discord.Client):
    def __init__(self, cfg: dict, members_intent: bool = True):
        intents = discord.Intents.default()  # message_content не включаем никогда
        intents.members = members_intent      # для входа участников и обхода списка; нужен тумблер в портале
        super().__init__(intents=intents)
        self.cfg = cfg
        self.store = Store(cfg.get("db", {}))
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = self.on_command_error
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        register(self)
        for gid in self.cfg["guilds"]:
            await self.sync_guild(gid)

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
        if guild.id in self.cfg["guilds"]:
            ok = await self.sync_guild(guild.id)
            log.info("добавлен на %s (%s): команды %s", guild.name, guild.id, "готовы" if ok else "не синхронизированы")
        else:
            log.warning("добавлен на сервер вне конфига: %s (%s) — команд там нет", guild.name, guild.id)

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()

    async def on_command_error(self, inter: discord.Interaction, error: Exception):
        log.exception("команда %s: %s", inter.command and inter.command.name, error)
        text = ("Нужно право Manage Roles." if isinstance(error, app_commands.MissingPermissions)
                else "Ошибка при выполнении; запись есть в логе бота.")
        try:
            if inter.response.is_done():
                await inter.followup.send(text, ephemeral=True)
            else:
                await inter.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    # --- служебное ---------------------------------------------------------
    def gcfg(self, guild_id: int) -> dict:
        """Настройки сервера: report_channel_id, roles, remove_on_confirm. Нет секции — пустые."""
        return self.cfg["guild"].get(guild_id, {})

    async def member(self, guild_id: int, discord_id: int) -> discord.Member | None:
        g = self.get_guild(guild_id)
        if not g:
            return None
        m = g.get_member(discord_id)
        if m:
            return m
        try:
            return await g.fetch_member(discord_id)
        except discord.NotFound:
            return None

    async def report(self, guild_id: int, text: str) -> None:
        """Строка в служебный канал сервера. В холостом режиме — единственный выход."""
        cid = self.gcfg(guild_id).get("report_channel_id") or 0
        ch = self.get_channel(cid) if cid else None
        if not ch:
            log.warning("сервер %s: служебный канал %s не найден; отчёт: %s", guild_id, cid, text)
            return
        try:
            await ch.send(text[:1900])
        except discord.Forbidden:
            log.warning("сервер %s: нет права писать в #%s; отчёт: %s", guild_id, ch.name, text)

    # --- правила ------------------------------------------------------------
    def admissible(self, info: dict) -> tuple[bool, str]:
        a = self.cfg["admit"]
        if a.get("reject_blocked") and info["blocked"]:
            return False, "аккаунт заблокирован"
        if a.get("min_edits") and info["editcount"] < a["min_edits"]:
            return False, f"правок {info['editcount']} < {a['min_edits']}"
        if a.get("min_age_days") and info["registration"]:
            reg = parse_ts(info["registration"])
            age = (dt.datetime.now(dt.timezone.utc) - reg).days
            if age < a["min_age_days"]:
                return False, f"аккаунту {age} дн. < {a['min_age_days']}"
        return True, "ок"

    def managed_roles(self, guild_id: int) -> set[str]:
        gc = self.gcfg(guild_id)
        return {v for v in gc.get("roles", {}).values() if v} | set(gc.get("remove_on_confirm", []))

    def wanted_roles(self, guild_id: int, info: dict) -> list[str]:
        """Имена ролей: admitted всем прошедшим, apat по условию, остальные ключи = группы.
        Пустая строка в конфиге = не использовать."""
        r = self.gcfg(guild_id).get("roles", {})
        out = [r["admitted"]] if r.get("admitted") else []
        if info["apat"] and r.get("apat"):
            out.append(r["apat"])
        out += [v for k, v in r.items() if k not in ("admitted", "apat") and v and k in info["groups"]]
        return out

    async def evaluate(self, member: discord.Member, info: dict | None, wiki_name: str) -> str:
        """Единая точка: критерии впуска → роли (или снятие всех управляемых при отказе).
        Вызывается из /confirm, OAuth-колбэка и /sync. Возвращает текст для человека."""
        gid = member.guild.id
        if not info:
            await self.report(gid, f"{member.mention}: в рувики нет такого участника" + (f" ({wiki_name})" if self.gcfg(gid).get("report_names") else ""))
            return f"В рувики нет участника {wiki_name}."
        ok, why = self.admissible(info)
        want = set(self.wanted_roles(gid, info)) if ok else set()
        added, removed, missing = await self.apply_roles(member, info, want, why if not ok else "")
        if not ok:
            await self.store.log(str(member.id), "reject", info["name"], why)
            return f"Критерии впуска не пройдены: {why}."
        if self.cfg.get("dry_run", True):
            return "Холостой режим: роли не менялись, отчёт в служебном канале."
        return f"Роли: выдать {added or '—'}, снять {removed or '—'}" + (f", на сервере нет {missing}" if missing else "")

    async def apply_roles(self, member: discord.Member, info: dict, want: set[str], reason: str
                          ) -> tuple[list[str], list[str], list[str]]:
        """Приводит управляемые роли участника к `want`. Возвращает (added, removed, missing)."""
        have = {r.name for r in member.roles}
        add_names = sorted(want - have)
        add = [discord.utils.get(member.guild.roles, name=n) for n in add_names]
        missing = [n for n, r in zip(add_names, add) if r is None]
        add = [r for r in add if r is not None]
        rem = [r for r in member.roles if r.name in (self.managed_roles(member.guild.id) - want)]
        dry = self.cfg.get("dry_run", True)
        if add or rem or reason or dry:   # вхолостую отчитываемся всегда, иначе тест не виден
            gc = self.gcfg(member.guild.id)
            if gc.get("report_names", False):   # только в закрытый канал: связка ник ↔ вики-аккаунт
                who = f"{member.mention} ↔ **{info['name']}** ({', '.join(info['labels']) or 'без флагов'})"
            else:                                # публичный канал: без имени в вики и без флагов
                who = f"{member.mention}: вики-аккаунт подтверждён"
            await self.report(member.guild.id, f"{'[холостой] ' if dry else ''}{who}"
                              f"{'; отказ: ' + reason if reason else ''}: "
                              f"выдать {[r.name for r in add] or '—'}, снять {[r.name for r in rem] or '—'}"
                              + (f", нет ролей {missing}" if missing else ""))
        if not dry:
            if add:
                await member.add_roles(*add, reason=f"Apparitor: {info['name']} {info['labels']}")
            if rem:
                await member.remove_roles(*rem, reason=f"Apparitor: {reason or 'sync'}")
        await self.store.log(str(member.id), "roles-dry" if dry else "roles", info["name"],
                       f"+{[r.name for r in add]} -{[r.name for r in rem]}")
        return [r.name for r in add], [r.name for r in rem], missing

    async def after_link(self, discord_id: int, guild_id: int, wiki_name: str) -> str:
        """Из OAuth-колбэка: участник по id → evaluate."""
        m = await self.member(guild_id, discord_id)
        if not m:
            return "Вас нет на сервере Discord, роли выдать некому."
        info = await wiki.user_info(self.http_session, wiki_name)
        return await self.evaluate(m, info, wiki_name)


def fmt(info: dict) -> str:
    st = "заблокирован" if info["blocked"] else ("частичная блокировка" if info["blocked_partial"] else "не заблокирован")
    return (f"**{info['name']}** — {', '.join(info['labels']) or 'без флагов'}; "
            f"правок {info['editcount']}, с {(info['registration'] or '?')[:10]}, {st}; "
            f"АПАТ: {'да' if info['apat'] else 'нет'}")


def register(bot: Apparitor):
    tree = bot.tree

    @tree.command(name="auth", description="Подтвердить вики-аккаунт входом через Мету (OAuth)")
    @app_commands.guild_only()
    async def auth(inter: discord.Interaction):
        base = bot.cfg.get("base_url", "")
        if not base or not os.environ.get("OAUTH_CLIENT_ID"):
            await inter.response.send_message("OAuth ещё не настроен, используйте `/verify <имя>`.", ephemeral=True)
            return
        state = new_state()
        await bot.store.set_pending(inter.user.id, inter.guild_id, "", state)
        await inter.response.send_message(
            f"Войдите своей учёткой Викимедиа по ссылке (30 минут): <{base}/oauth/start?s={state}>", ephemeral=True)
        await bot.report(inter.guild_id, f"/auth от {inter.user.mention}")

    @tree.command(name="verify", description="Привязать вики-аккаунт рувики без OAuth: код в описании правки")
    @app_commands.guild_only()
    @app_commands.describe(wiki_name="Имя участника в рувики")
    async def verify(inter: discord.Interaction, wiki_name: str):
        await inter.response.defer(ephemeral=True)
        info = await wiki.user_info(bot.http_session, wiki_name)
        await bot.report(inter.guild_id, f"/verify от {inter.user.mention}: " + (f"«{wiki_name}» — {'есть' if info else 'нет такого'}" if bot.gcfg(inter.guild_id).get("report_names") else "запрошен код"))
        if not info:
            await inter.followup.send(f"В рувики нет участника «{wiki_name}».", ephemeral=True)
            return
        code = "apparitor-" + secrets.token_hex(3)
        await bot.store.set_pending(inter.user.id, inter.guild_id, info["name"], code)
        await inter.followup.send(
            f"Сделайте любую правку в рувики от имени **{info['name']}** (например, в своей песочнице) "
            f"с описанием правки `{code}`, затем выполните `/confirm`. Код действует 30 минут.", ephemeral=True)

    @tree.command(name="confirm", description="Проверить правку с кодом и получить роли")
    @app_commands.guild_only()
    async def confirm(inter: discord.Interaction):
        p = await bot.store.get_pending(inter.user.id)
        if not p or not p["wiki_name"]:
            await inter.response.send_message("Сначала `/verify <имя>`.", ephemeral=True)
            return
        if dt.datetime.now(dt.timezone.utc) - p["issued_at"] > CODE_TTL:
            await inter.response.send_message("Код устарел, повторите `/verify`.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        revid = await wiki.find_code_in_contribs(bot.http_session, p["wiki_name"], p["code"], p["issued_at"])
        await bot.report(inter.guild_id, f"/confirm от {inter.user.mention}: " + (f"{p['wiki_name']}, " if bot.gcfg(inter.guild_id).get("report_names") else "") + f"правка с кодом: {revid or 'не найдена'}")
        if not revid:
            await inter.followup.send(f"Правки с описанием `{p['code']}` от {p['wiki_name']} не вижу. "
                                      f"Подождите минуту после сохранения и повторите.", ephemeral=True)
            return
        await bot.store.link(inter.user.id, p["wiki_name"], "edit-summary", revid)
        info = await wiki.user_info(bot.http_session, p["wiki_name"])
        text = await bot.evaluate(inter.user, info, p["wiki_name"])
        await inter.followup.send(f"Подтверждено (revid {revid}). {fmt(info) if info else ''}\n{text}", ephemeral=True)

    @tree.command(name="status", description="Флаги участника рувики (АПАТ, админ и т.д.)")
    @app_commands.guild_only()
    @app_commands.describe(wiki_name="Имя в рувики; пусто = свой привязанный аккаунт")
    async def status(inter: discord.Interaction, wiki_name: str | None = None):
        name = wiki_name or await bot.store.wiki_of(inter.user.id)
        if not name:
            await inter.response.send_message("Аккаунт не привязан: `/auth` или `/verify <имя>`.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        info = await wiki.user_info(bot.http_session, name)
        await bot.report(inter.guild_id, f"/status от {inter.user.mention}" + (f": «{name}»" if bot.gcfg(inter.guild_id).get("report_names") else ""))
        await inter.followup.send(fmt(info) if info else f"Нет участника «{name}».", ephemeral=True)

    @tree.command(name="sync", description="Пересчитать роли всем привязанным (только Manage Roles)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def sync(inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        links = await bot.store.all_links()
        infos = await wiki.users_info(bot.http_session, [n for _, n in links])
        lines = []
        for did, name in links:
            m = await bot.member(inter.guild_id, did)
            if not m:
                lines.append(f"{name}: ушёл с сервера")
                continue
            text = await bot.evaluate(m, infos.get(name), name)
            if "Роли: выдать —, снять —" not in text:
                lines.append(f"{name}: {text}")
        await inter.followup.send("\n".join(lines)[:1900] or "Изменений нет.", ephemeral=True)


def load_env() -> None:
    """Локальный .env (DISCORD_TOKEN, OAUTH_*, TOOL_TOOLSDB_*) в окружение; на Toolforge всё уже в envvars."""
    if os.path.exists(".env"):
        for line in open(".env"):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)


def read_token() -> str:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN не задан (.env или окружение)")
    return token
