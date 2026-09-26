"""/auth, /verify, /confirm, /status — привязка Discord ↔ вики-аккаунт и справка о флагах."""

from __future__ import annotations

import datetime as dt
import os
import secrets

import discord
from discord import app_commands

from ..bot import Apparitor
from ..models import UserInfo
from ..web import new_state

CODE_TTL = dt.timedelta(minutes=30)


def fmt(info: UserInfo) -> str:
    st = "заблокирован" if info.blocked else ("частичная блокировка" if info.blocked_partial else "не заблокирован")
    return (
        f"**{info.name}** — {', '.join(info.labels) or 'без флагов'}; "
        f"правок {info.editcount}, с {(info.registration or '?')[:10]}, {st}; "
        f"АПАТ: {'да' if info.apat else 'нет'}"
    )


def register(bot: Apparitor) -> None:
    tree = bot.tree

    def names_ok(gid: int) -> bool:
        return bot.gcfg(gid).report_names

    @tree.command(name="auth", description="Подтвердить вики-аккаунт входом через Мету (OAuth)")
    @app_commands.guild_only()
    async def auth(inter: discord.Interaction):
        base = bot.cfg.base_url
        if not base or not os.environ.get("OAUTH_CLIENT_ID"):
            await inter.response.send_message("OAuth ещё не настроен, используйте `/verify <имя>`.", ephemeral=True)
            return
        state = new_state()
        await bot.store.set_pending(inter.user.id, inter.guild_id, "", state)
        await inter.response.send_message(
            f"Войдите своей учёткой Викимедиа по ссылке (30 минут): <{base}/oauth/start?s={state}>", ephemeral=True
        )
        await bot.report(inter.guild_id, f"/auth от {inter.user.mention}")

    @tree.command(name="verify", description="Привязать вики-аккаунт рувики без OAuth: код в описании правки")
    @app_commands.guild_only()
    @app_commands.describe(wiki_name="Имя участника в рувики")
    async def verify(inter: discord.Interaction, wiki_name: str):
        await inter.response.defer(ephemeral=True)
        info = await bot.wiki.user_info(wiki_name)
        shown = f"«{wiki_name}» — {'есть' if info else 'нет такого'}" if names_ok(inter.guild_id) else "запрошен код"
        await bot.report(inter.guild_id, f"/verify от {inter.user.mention}: {shown}")
        if not info:
            await inter.followup.send(f"В рувики нет участника «{wiki_name}».", ephemeral=True)
            return
        code = "apparitor-" + secrets.token_hex(3)
        await bot.store.set_pending(inter.user.id, inter.guild_id, info.name, code)
        await inter.followup.send(
            f"Сделайте любую правку в рувики от имени **{info.name}** (например, в своей песочнице) "
            f"с описанием правки `{code}`, затем выполните `/confirm`. Код действует 30 минут.",
            ephemeral=True,
        )

    @tree.command(name="confirm", description="Проверить правку с кодом и получить роли")
    @app_commands.guild_only()
    async def confirm(inter: discord.Interaction):
        p = await bot.store.get_pending(inter.user.id)
        if not p or not p["wiki_name"]:
            await inter.response.send_message("Сначала `/verify <имя>`.", ephemeral=True)
            return
        if dt.datetime.now(dt.UTC) - p["issued_at"] > CODE_TTL:
            await inter.response.send_message("Код устарел, повторите `/verify`.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        revid = await bot.wiki.find_code_in_contribs(p["wiki_name"], p["code"], p["issued_at"])
        who = f"{p['wiki_name']}, " if names_ok(inter.guild_id) else ""
        await bot.report(
            inter.guild_id, f"/confirm от {inter.user.mention}: {who}правка с кодом: {revid or 'не найдена'}"
        )
        if not revid:
            await inter.followup.send(
                f"Правки с описанием `{p['code']}` от {p['wiki_name']} не вижу. "
                "Подождите минуту после сохранения и повторите.",
                ephemeral=True,
            )
            return
        await bot.store.link(inter.user.id, p["wiki_name"], "edit-summary", revid)
        info = await bot.wiki.user_info(p["wiki_name"])
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
        info = await bot.wiki.user_info(name)
        await bot.report(
            inter.guild_id, f"/status от {inter.user.mention}" + (f": «{name}»" if names_ok(inter.guild_id) else "")
        )
        await inter.followup.send(fmt(info) if info else f"Нет участника «{name}».", ephemeral=True)
