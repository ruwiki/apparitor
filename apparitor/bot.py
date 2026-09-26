"""Discord-часть: slash-команды. Без Message Content Intent — текст сообщений боту не нужен."""
from __future__ import annotations
import os
import secrets
import tomllib
import datetime as dt

import aiohttp
import discord
from discord import app_commands

from . import wiki
from .store import Store


def load_config(path: str = "config.toml") -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


class Apparitor(discord.Client):
    def __init__(self, cfg: dict):
        super().__init__(intents=discord.Intents.default())  # members/message_content не включаем
        self.cfg = cfg
        self.store = Store(cfg["db"])
        self.tree = app_commands.CommandTree(self)
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        register(self)
        gid = self.cfg.get("guild_id") or 0
        if gid:
            g = discord.Object(id=gid)
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        else:
            await self.tree.sync()

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()

    # --- роли -------------------------------------------------------------
    def role_by_name(self, guild: discord.Guild, name: str) -> discord.Role | None:
        return discord.utils.get(guild.roles, name=name)

    def wanted_roles(self, info: dict) -> list[str]:
        """Имена ролей по условиям; пустая строка в конфиге = условие не используется."""
        r = self.cfg["roles"]
        cond = {"admitted": True, "sysop": info["sysop"], "apat": info["apat"],
                "arbcom": "arbcom" in info["groups"], "checkuser": "checkuser" in info["groups"],
                "bureaucrat": "bureaucrat" in info["groups"]}
        return [r[k] for k, ok in cond.items() if ok and r.get(k)]

    def admissible(self, info: dict) -> tuple[bool, str]:
        a = self.cfg["admit"]
        if a.get("reject_blocked") and info["blocked"]:
            return False, "аккаунт заблокирован"
        if info["editcount"] < a["min_edits"]:
            return False, f"правок {info['editcount']} < {a['min_edits']}"
        if info["registration"]:
            reg = dt.datetime.strptime(info["registration"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            age = (dt.datetime.now(dt.timezone.utc) - reg).days
            if age < a["min_age_days"]:
                return False, f"аккаунту {age} дн. < {a['min_age_days']}"
        return True, "ок"

    async def apply_roles(self, member: discord.Member, info: dict) -> tuple[list[str], list[str]]:
        """Выдаёт нужные, снимает ненужные из управляемого набора. Возвращает (added, removed, missing)."""
        managed = {v for v in self.cfg["roles"].values() if v}
        want = set(self.wanted_roles(info))
        have = {r.name for r in member.roles}
        add = [self.role_by_name(member.guild, n) for n in want - have]
        rem = [r for r in member.roles if r.name in (managed - want)]
        missing = [n for n, r in zip(want - have, add) if r is None]
        add = [r for r in add if r is not None]
        if add:
            await member.add_roles(*add, reason=f"Apparitor: {info['name']} {info['labels']}")
        if rem:
            await member.remove_roles(*rem, reason="Apparitor: sync")
        self.store.log(str(member.id), "roles", info["name"], f"+{[r.name for r in add]} -{[r.name for r in rem]}")
        return [r.name for r in add], [r.name for r in rem], missing


def fmt(info: dict) -> str:
    st = "заблокирован" if info["blocked"] else "не заблокирован"
    return (f"**{info['name']}** — {', '.join(info['labels']) or 'без флагов'}; "
            f"правок {info['editcount']}, с {(info['registration'] or '?')[:10]}, {st}; "
            f"АПАТ: {'да' if info['apat'] else 'нет'}")


def register(bot: Apparitor):
    tree = bot.tree

    @tree.command(name="verify", description="Привязать вики-аккаунт рувики: получить код для правки")
    @app_commands.describe(wiki_name="Имя участника в рувики")
    async def verify(inter: discord.Interaction, wiki_name: str):
        info = await wiki.user_info(bot.http_session, wiki_name)
        if not info:
            await inter.response.send_message(f"В рувики нет участника «{wiki_name}».", ephemeral=True)
            return
        code = "apparitor-" + secrets.token_hex(3)
        bot.store.set_pending(inter.user.id, info["name"], code)
        await inter.response.send_message(
            f"Сделайте любую правку в рувики от имени **{info['name']}** (например, в своей песочнице) "
            f"с описанием правки `{code}`, затем выполните `/confirm`. Код действует 30 минут.",
            ephemeral=True)

    @tree.command(name="confirm", description="Проверить правку с кодом и получить роли")
    async def confirm(inter: discord.Interaction):
        p = bot.store.get_pending(inter.user.id)
        if not p:
            await inter.response.send_message("Сначала `/verify <имя>`.", ephemeral=True)
            return
        if dt.datetime.now(dt.timezone.utc) - p["issued_at"] > dt.timedelta(minutes=30):
            await inter.response.send_message("Код устарел, повторите `/verify`.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        revid = await wiki.find_code_in_contribs(bot.http_session, p["wiki_name"], p["code"], p["issued_at"])
        if not revid:
            await inter.followup.send(f"Правки с описанием `{p['code']}` от {p['wiki_name']} не вижу. "
                                      f"Подождите минуту после сохранения и повторите.", ephemeral=True)
            return
        bot.store.link(inter.user.id, p["wiki_name"], "edit-summary", revid)
        info = await wiki.user_info(bot.http_session, p["wiki_name"])
        ok, why = bot.admissible(info)
        if not ok:
            bot.store.log(str(inter.user.id), "reject", info["name"], why)
            await inter.followup.send(f"Аккаунт подтверждён (revid {revid}), но критерии впуска не пройдены: {why}.",
                                      ephemeral=True)
            return
        added, removed, missing = await bot.apply_roles(inter.user, info)
        msg = f"Подтверждено (revid {revid}). {fmt(info)}\nВыданы роли: {added or '—'}"
        if missing:
            msg += f"\n⚠️ На сервере нет ролей: {missing}"
        await inter.followup.send(msg, ephemeral=True)

    @tree.command(name="status", description="Флаги участника рувики (АПАТ, админ и т.д.)")
    @app_commands.describe(wiki_name="Имя в рувики; пусто = свой привязанный аккаунт")
    async def status(inter: discord.Interaction, wiki_name: str | None = None):
        name = wiki_name or bot.store.wiki_of(inter.user.id)
        if not name:
            await inter.response.send_message("Аккаунт не привязан: `/verify <имя>`.", ephemeral=True)
            return
        info = await wiki.user_info(bot.http_session, name)
        await inter.response.send_message(fmt(info) if info else f"Нет участника «{name}».", ephemeral=True)

    @tree.command(name="sync", description="Пересчитать роли всем привязанным (только Manage Roles)")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def sync(inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        lines = []
        for did, name in bot.store.all_links():
            m = inter.guild.get_member(did) or await inter.guild.fetch_member(did)
            info = await wiki.user_info(bot.http_session, name)
            if not m or not info:
                lines.append(f"{name}: не найден")
                continue
            added, removed, _ = await bot.apply_roles(m, info)
            if added or removed:
                lines.append(f"{name}: +{added} -{removed}")
        await inter.followup.send("\n".join(lines) or "Изменений нет.", ephemeral=True)


def main():
    cfg = load_config()
    token = os.environ.get("DISCORD_TOKEN")
    if not token and os.path.exists(".env"):
        for line in open(".env"):
            if line.startswith("DISCORD_TOKEN="):
                token = line.split("=", 1)[1].strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN не задан (.env или окружение)")
    Apparitor(cfg).run(token, log_handler=None)
