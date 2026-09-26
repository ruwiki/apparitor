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

    @tree.command(
        name="audit", description="Сопоставить всех участников сервера с рувики по нику (только Manage Roles)"
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def audit(inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        guild = inter.guild
        members = [m for m in guild.members if not m.bot]
        if len(members) < max(1, (guild.member_count or 1) // 2):  # кэш пуст или неполон — тянем список
            members = [m async for m in guild.fetch_members(limit=None) if not m.bot]
        linked = dict(await bot.store.all_links())
        # кандидат = привязанное имя, иначе ник на сервере, иначе отображаемое имя, иначе логин
        cand = {m.id: (linked.get(m.id) or m.nick or m.global_name or m.name) for m in members}
        infos = await wiki.users_info(bot.http_session, list(set(cand.values())), with_global=False)
        gcfg, managed = bot.gcfg(guild.id), rules.managed_roles(bot.gcfg(guild.id))
        rows, mism, n_linked, n_match, n_none, new_cands = [], [], 0, 0, 0, []
        for m in members:
            name, info = cand[m.id], infos.get(cand[m.id])
            src = "oauth" if m.id in linked else ("nick" if info else "—")
            if m.id in linked:
                n_linked += 1
            elif info:
                n_match += 1
                new_cands.append((m.id, info["name"], "nick"))
            else:
                n_none += 1
            have = {r.name for r in m.roles}
            d = rules.decide(bot.cfg["admit"], gcfg, info, have) if info else None
            flag = "" if not d else ("=" if not d["add"] and not d["remove"] else "≠")
            if flag == "≠":
                mism.append(
                    f"{m.display_name} ↔ {info['name']}: есть {sorted(have & managed) or '—'}, надо {d['want'] or '—'}"
                )
            labels = ", ".join(info["labels"]) if info else "—"
            rows.append(
                f"{m.display_name}\t{name}\t{src}\t{labels}\t{'; '.join(sorted(have & managed)) or '—'}\t{flag}"
            )
        if new_cands:
            await bot.store.set_candidates(guild.id, new_cands)
        head = (
            f"Аудит {guild.name}: участников {len(members)}; привязано через OAuth {n_linked}, "
            f"совпадение по нику {n_match}, не сопоставлено {n_none}; расхождений ролей {len(mism)}."
        )
        body = "участник\tкандидат в вики\tисточник\tфлаги\tуправляемые роли\tсовпадение\n" + "\n".join(rows)
        f = discord.File(io.BytesIO(body.encode()), filename=f"audit-{guild.id}.tsv")
        sent = await bot.report(guild.id, head + ("\nРасхождения:\n" + "\n".join(mism[:25]) if mism else ""), file=f)
        tail = (
            "\nПолная таблица — в служебном канале."
            if sent
            else "\nВ служебный канал написать не удалось (нет права?), таблица не отправлена."
        )
        await inter.followup.send(head + tail, ephemeral=True)
        await bot.store.log(str(inter.user.id), "audit", str(guild.id), head)
        log.info("audit %s: %s", guild.id, head)
