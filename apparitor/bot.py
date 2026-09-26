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
from .store import Store
from .web import new_state

log = logging.getLogger("apparitor")
CODE_TTL = dt.timedelta(minutes=30)


def load_config(path: str = "config.toml") -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    if not cfg.get("guild_id"):
        raise SystemExit("guild_id в конфиге обязателен: бот работает на одном сервере")
    return cfg


class Apparitor(discord.Client):
    def __init__(self, cfg: dict):
        super().__init__(intents=discord.Intents.default())  # members/message_content не включаем
        self.cfg = cfg
        self.store = Store(cfg["db"])
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = self.on_command_error
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        register(self)
        g = discord.Object(id=self.cfg["guild_id"])
        self.tree.copy_global_to(guild=g)
        await self.tree.sync(guild=g)

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
    @property
    def guild(self) -> discord.Guild | None:
        return self.get_guild(self.cfg["guild_id"])

    async def member(self, discord_id: int) -> discord.Member | None:
        g = self.guild
        if not g:
            return None
        m = g.get_member(discord_id)
        if m:
            return m
        try:
            return await g.fetch_member(discord_id)
        except discord.NotFound:
            return None

    async def report(self, text: str) -> None:
        """Строка в служебный канал (#moderbot). В холостом режиме — единственный выход."""
        cid = self.cfg.get("report_channel_id") or 0
        ch = self.get_channel(cid) if cid else None
        if ch:
            await ch.send(text[:1900])

    # --- правила ------------------------------------------------------------
    def admissible(self, info: dict) -> tuple[bool, str]:
        a = self.cfg["admit"]
        if a.get("reject_blocked") and info["blocked"]:
            return False, "аккаунт заблокирован"
        if a.get("min_edits") and info["editcount"] < a["min_edits"]:
            return False, f"правок {info['editcount']} < {a['min_edits']}"
        if a.get("min_age_days") and info["registration"]:
            reg = dt.datetime.strptime(info["registration"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            age = (dt.datetime.now(dt.timezone.utc) - reg).days
            if age < a["min_age_days"]:
                return False, f"аккаунту {age} дн. < {a['min_age_days']}"
        return True, "ок"

    def managed_roles(self) -> set[str]:
        return {v for v in self.cfg["roles"].values() if v} | set(self.cfg.get("remove_on_confirm", []))

    def wanted_roles(self, info: dict) -> list[str]:
        """Имена ролей: admitted всем прошедшим, apat по условию, остальные ключи = группы.
        Пустая строка в конфиге = не использовать."""
        r = self.cfg["roles"]
        out = [r["admitted"]] if r.get("admitted") else []
        if info["apat"] and r.get("apat"):
            out.append(r["apat"])
        out += [v for k, v in r.items() if k not in ("admitted", "apat") and v and k in info["groups"]]
        return out

    async def evaluate(self, member: discord.Member, info: dict | None, wiki_name: str) -> str:
        """Единая точка: критерии впуска → роли (или снятие всех управляемых при отказе).
        Вызывается из /confirm, OAuth-колбэка и /sync. Возвращает текст для человека."""
        if not info:
            await self.report(f"{member.mention} ↔ {wiki_name}: в рувики нет такого участника")
            return f"В рувики нет участника {wiki_name}."
        ok, why = self.admissible(info)
        want = set(self.wanted_roles(info)) if ok else set()
        added, removed, missing = await self.apply_roles(member, info, want, why if not ok else "")
        if not ok:
            self.store.log(str(member.id), "reject", info["name"], why)
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
        rem = [r for r in member.roles if r.name in (self.managed_roles() - want)]
        dry = self.cfg.get("dry_run", True)
        if add or rem or reason:
            await self.report(f"{'[холостой] ' if dry else ''}{member.mention} ↔ **{info['name']}** "
                              f"({', '.join(info['labels']) or 'без флагов'}"
                              f"{'; отказ: ' + reason if reason else ''}): "
                              f"выдать {[r.name for r in add] or '—'}, снять {[r.name for r in rem] or '—'}"
                              + (f", нет ролей {missing}" if missing else ""))
        if not dry:
            if add:
                await member.add_roles(*add, reason=f"Apparitor: {info['name']} {info['labels']}")
            if rem:
                await member.remove_roles(*rem, reason=f"Apparitor: {reason or 'sync'}")
        self.store.log(str(member.id), "roles-dry" if dry else "roles", info["name"],
                       f"+{[r.name for r in add]} -{[r.name for r in rem]}")
        return [r.name for r in add], [r.name for r in rem], missing

    async def after_link(self, discord_id: int, wiki_name: str) -> str:
        """Из OAuth-колбэка: участник по id → evaluate."""
        m = await self.member(discord_id)
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
        bot.store.set_pending(inter.user.id, "", state)
        await inter.response.send_message(
            f"Войдите своей учёткой Викимедиа по ссылке (30 минут): {base}/oauth/start?s={state}", ephemeral=True)
        await bot.report(f"/auth от {inter.user.mention}")

    @tree.command(name="verify", description="Привязать вики-аккаунт рувики без OAuth: код в описании правки")
    @app_commands.guild_only()
    @app_commands.describe(wiki_name="Имя участника в рувики")
    async def verify(inter: discord.Interaction, wiki_name: str):
        await inter.response.defer(ephemeral=True)
        info = await wiki.user_info(bot.http_session, wiki_name)
        await bot.report(f"/verify от {inter.user.mention}: «{wiki_name}» — {'есть' if info else 'нет такого'}")
        if not info:
            await inter.followup.send(f"В рувики нет участника «{wiki_name}».", ephemeral=True)
            return
        code = "apparitor-" + secrets.token_hex(3)
        bot.store.set_pending(inter.user.id, info["name"], code)
        await inter.followup.send(
            f"Сделайте любую правку в рувики от имени **{info['name']}** (например, в своей песочнице) "
            f"с описанием правки `{code}`, затем выполните `/confirm`. Код действует 30 минут.", ephemeral=True)

    @tree.command(name="confirm", description="Проверить правку с кодом и получить роли")
    @app_commands.guild_only()
    async def confirm(inter: discord.Interaction):
        p = bot.store.get_pending(inter.user.id)
        if not p or not p["wiki_name"]:
            await inter.response.send_message("Сначала `/verify <имя>`.", ephemeral=True)
            return
        if dt.datetime.now(dt.timezone.utc) - p["issued_at"] > CODE_TTL:
            await inter.response.send_message("Код устарел, повторите `/verify`.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        revid = await wiki.find_code_in_contribs(bot.http_session, p["wiki_name"], p["code"], p["issued_at"])
        await bot.report(f"/confirm от {inter.user.mention}: {p['wiki_name']}, правка с кодом: {revid or 'не найдена'}")
        if not revid:
            await inter.followup.send(f"Правки с описанием `{p['code']}` от {p['wiki_name']} не вижу. "
                                      f"Подождите минуту после сохранения и повторите.", ephemeral=True)
            return
        bot.store.link(inter.user.id, p["wiki_name"], "edit-summary", revid)
        info = await wiki.user_info(bot.http_session, p["wiki_name"])
        text = await bot.evaluate(inter.user, info, p["wiki_name"])
        await inter.followup.send(f"Подтверждено (revid {revid}). {fmt(info) if info else ''}\n{text}", ephemeral=True)

    @tree.command(name="status", description="Флаги участника рувики (АПАТ, админ и т.д.)")
    @app_commands.guild_only()
    @app_commands.describe(wiki_name="Имя в рувики; пусто = свой привязанный аккаунт")
    async def status(inter: discord.Interaction, wiki_name: str | None = None):
        name = wiki_name or bot.store.wiki_of(inter.user.id)
        if not name:
            await inter.response.send_message("Аккаунт не привязан: `/auth` или `/verify <имя>`.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        info = await wiki.user_info(bot.http_session, name)
        await bot.report(f"/status от {inter.user.mention}: «{name}»")
        await inter.followup.send(fmt(info) if info else f"Нет участника «{name}».", ephemeral=True)

    @tree.command(name="sync", description="Пересчитать роли всем привязанным (только Manage Roles)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def sync(inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        links = bot.store.all_links()
        infos = await wiki.users_info(bot.http_session, [n for _, n in links])
        lines = []
        for did, name in links:
            m = await bot.member(did)
            if not m:
                lines.append(f"{name}: ушёл с сервера")
                continue
            text = await bot.evaluate(m, infos.get(name), name)
            if "Роли: выдать —, снять —" not in text:
                lines.append(f"{name}: {text}")
        await inter.followup.send("\n".join(lines)[:1900] or "Изменений нет.", ephemeral=True)


def read_token() -> str:
    """DISCORD_TOKEN из окружения (Toolforge envvars) или локального .env; .env также подгружает OAUTH_*."""
    if os.path.exists(".env"):
        for line in open(".env"):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN не задан (.env или окружение)")
    return token
