"""Slash-команды. Логика решений — в bot.evaluate и rules; здесь только разбор ввода и ответы."""

from __future__ import annotations

import datetime as dt
import io
import os
import secrets

import discord
from discord import app_commands

from . import rules, wiki
from .bot import Apparitor, log
from .web import new_state

CODE_TTL = dt.timedelta(minutes=30)


def fmt(info: dict) -> str:
    st = (
        "заблокирован"
        if info["blocked"]
        else ("частичная блокировка" if info["blocked_partial"] else "не заблокирован")
    )
    return (
        f"**{info['name']}** — {', '.join(info['labels']) or 'без флагов'}; "
        f"правок {info['editcount']}, с {(info['registration'] or '?')[:10]}, {st}; "
        f"АПАТ: {'да' if info['apat'] else 'нет'}"
    )


def register(bot: Apparitor):
    tree = bot.tree

    def names_ok(gid: int) -> bool:
        return bool(bot.gcfg(gid).get("report_names"))

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
            f"Войдите своей учёткой Викимедиа по ссылке (30 минут): <{base}/oauth/start?s={state}>", ephemeral=True
        )
        await bot.report(inter.guild_id, f"/auth от {inter.user.mention}")

    @tree.command(name="verify", description="Привязать вики-аккаунт рувики без OAuth: код в описании правки")
    @app_commands.guild_only()
    @app_commands.describe(wiki_name="Имя участника в рувики")
    async def verify(inter: discord.Interaction, wiki_name: str):
        await inter.response.defer(ephemeral=True)
        info = await wiki.user_info(bot.http_session, wiki_name)
        shown = f"«{wiki_name}» — {'есть' if info else 'нет такого'}" if names_ok(inter.guild_id) else "запрошен код"
        await bot.report(inter.guild_id, f"/verify от {inter.user.mention}: {shown}")
        if not info:
            await inter.followup.send(f"В рувики нет участника «{wiki_name}».", ephemeral=True)
            return
        code = "apparitor-" + secrets.token_hex(3)
        await bot.store.set_pending(inter.user.id, inter.guild_id, info["name"], code)
        await inter.followup.send(
            f"Сделайте любую правку в рувики от имени **{info['name']}** (например, в своей песочнице) "
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
        revid = await wiki.find_code_in_contribs(bot.http_session, p["wiki_name"], p["code"], p["issued_at"])
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
        await bot.report(
            inter.guild_id, f"/status от {inter.user.mention}" + (f": «{name}»" if names_ok(inter.guild_id) else "")
        )
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
            if "выдать —, снять —" not in text:
                lines.append(f"{name}: {text}")
        await inter.followup.send("\n".join(lines)[:1900] or "Изменений нет.", ephemeral=True)

    @tree.command(name="audit", description="Сопоставить всех участников сервера с рувики (только Manage Roles)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def audit(inter: discord.Interaction):
        """Полный отчёт по ЭТОМУ серверу, файлом: расхождения по подтверждённым связкам, расхождения по нику
        (не подтверждено — может быть чужой участник с таким же ником), не сопоставленные. Ролей не трогает."""
        await inter.response.defer(ephemeral=True)
        guild = inter.guild
        members = [m for m in guild.members if not m.bot]
        if len(members) < max(1, (guild.member_count or 1) // 2):  # кэш пуст или неполон — тянем список
            members = [m async for m in guild.fetch_members(limit=None) if not m.bot]
        linked = dict(await bot.store.all_links())
        gcfg = bot.gcfg(guild.id)
        managed = rules.managed_roles(gcfg)
        roles_map = gcfg.get("roles", {})
        global_role_names = {roles_map[k] for k in roles_map if k in wiki.GLOBAL_KEEP and roles_map[k]}
        # кандидат = подтверждённое имя, иначе ник на сервере, иначе отображаемое имя, иначе логин
        cand = {m.id: (linked.get(m.id) or m.nick or m.global_name or m.name) for m in members}
        # глобальные группы (стюард и т.п.) запрашиваем только там, где они могут повлиять на роли
        need_global = {cand[m.id] for m in members if {r.name for r in m.roles} & global_role_names}
        infos = await wiki.users_info(
            bot.http_session, [n for n in set(cand.values()) if n not in need_global], with_global=False
        )
        infos |= await wiki.users_info(bot.http_session, list(need_global), with_global=True)
        verified, by_nick, unmatched, new_cands, n_linked, n_nick = [], [], [], [], 0, 0
        for m in members:
            name, info = cand[m.id], infos.get(cand[m.id])
            have = {r.name for r in m.roles}
            mine = ", ".join(sorted(have & managed)) or "—"
            who = m.display_name + (f" (логин {m.name})" if m.name != m.display_name else "")
            if not info:
                unmatched.append(f"{who}; роли бота: {mine}")
                continue
            if m.id in linked:
                n_linked += 1
            else:
                n_nick += 1
                new_cands.append((m.id, info["name"], "nick"))
            d = rules.decide(bot.cfg["admit"], gcfg, info, have)
            if not d["add"] and not d["remove"]:
                continue
            why = f"; отказ: {d['why']}" if not d["ok"] else ""
            line = (
                f"{who} ↔ {info['name']} ({', '.join(info['labels']) or 'без флагов'}{why}): "
                f"роли бота сейчас {mine}; бот выдал бы {', '.join(d['add']) or '—'}, снял бы {', '.join(d['remove']) or '—'}"
            )
            (verified if m.id in linked else by_nick).append(line)
        if new_cands:
            await bot.store.set_candidates(guild.id, new_cands)
        head = (
            f"Аудит сервера {guild.name}: участников {len(members)}; связка подтверждена (OAuth/правка) {n_linked}, "
            f"совпадение только по нику {n_nick}, не сопоставлено {len(unmatched)}. "
            f"Расхождений: по подтверждённым {len(verified)}, по нику {len(by_nick)}. Роли не менялись."
        )
        body = "\n".join(
            [
                head,
                "",
                f"== Расхождения по подтверждённым связкам ({len(verified)}) — человек тот; ошибка возможна в карте ролей ==",
                *(verified or ["нет"]),
                "",
                f"== Расхождения по нику ({len(by_nick)}) — НЕ подтверждено: ник мог совпасть с чужим участником рувики ==",
                *(by_nick or ["нет"]),
                "",
                f"== Не сопоставлены ({len(unmatched)}) — ник не совпал ни с одним участником рувики ==",
                *(unmatched or ["нет"]),
            ]
        )
        f = discord.File(io.BytesIO(body.encode()), filename=f"audit-{guild.id}.txt")
        sent = await bot.report(guild.id, head, file=f)
        tail = (
            "\nПолный отчёт файлом — в служебном канале."
            if sent
            else "\nВ служебный канал написать не удалось (нет права?), файл не отправлен."
        )
        await inter.followup.send(head + tail, ephemeral=True)
        await bot.store.log(str(inter.user.id), "audit", str(guild.id), head)
        log.info("audit %s: %s", guild.id, head)
