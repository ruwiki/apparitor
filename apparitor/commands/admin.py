"""/sync и /audit — для держателей Manage Roles."""

from __future__ import annotations

import io

import discord
from discord import app_commands

from .. import audit, match, rules, ruwiki
from ..bot import Apparitor, log


def register(bot: Apparitor) -> None:
    tree = bot.tree

    @tree.command(name="sync", description="Пересчитать роли всем привязанным (только Manage Roles)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def sync(inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        # связки общие для всех серверов; пересчитываем только тех, кто на ЭТОМ сервере
        here = [
            (did, name, m) for did, name in await bot.store.all_links() if (m := await bot.member(inter.guild_id, did))
        ]
        infos = await bot.wiki.users_info([n for _, n, _ in here])
        lines = []
        for _, name, m in here:
            text = await bot.evaluate(m, infos.get(name), name)
            if "выдать —, снять —" not in text:
                lines.append(f"{name}: {text}")
        await inter.followup.send(
            "\n".join(lines)[:1900] or f"Изменений нет (привязанных на сервере: {len(here)}).", ephemeral=True
        )

    @tree.command(name="link", description="Привязать участника к учётке рувики вручную (только Manage Roles)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.describe(member="Участник Discord", wiki_name="Имя в рувики (или глобальная учётка)")
    async def link(inter: discord.Interaction, member: discord.Member, wiki_name: str):
        """Связка, подтверждённая человеком с правом Manage Roles: для тех, у кого ник никогда не совпадёт
        (Grapefruit = Ле Лой). Кто привязал — в evidence и журнале."""
        await inter.response.defer(ephemeral=True)
        info = await bot.wiki.user_info(wiki_name)
        if not info:
            await inter.followup.send(f"В рувики и CentralAuth нет участника «{wiki_name}».", ephemeral=True)
            return
        await bot.store.link(member.id, info.name, "manual", f"{inter.user.name} ({inter.user.id})")
        await bot.store.log(str(inter.user.id), "link-manual", info.name, f"discord {member.id} ({member.name})")
        await bot.report(inter.guild_id, f"/link от {inter.user.mention}: {member.mention} ↔ **{info.name}**")
        text = await bot.evaluate(member, info, info.name)
        await inter.followup.send(f"{member.mention} ↔ **{info.name}** ({info.labels_text}). {text}", ephemeral=True)

    @tree.command(name="audit", description="Сопоставить всех участников сервера с рувики (только Manage Roles)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def audit_cmd(inter: discord.Interaction):
        """Полный отчёт по ЭТОМУ серверу, файлом. Ролей не трогает. Сопоставление по нику — подсказка,
        не связка: на нём ничего не делается."""
        await inter.response.defer(ephemeral=True)
        guild = inter.guild
        members = [m for m in guild.members if not m.bot]
        if len(members) < max(1, (guild.member_count or 1) // 2):  # кэш пуст или неполон — тянем список
            members = [m async for m in guild.fetch_members(limit=None) if not m.bot]
        linked = dict(await bot.store.all_links())
        gcfg = bot.gcfg(guild.id)
        managed = rules.managed_roles(gcfg)
        global_role_names = {v for k, v in gcfg.roles.items() if k in ruwiki.GLOBAL_KEEP and v}
        cands = {
            m.id: [linked[m.id]] if m.id in linked else match.candidates(m.nick, m.global_name, m.name) for m in members
        }
        # глобальные группы (стюард и т.п.) запрашиваем только там, где они могут повлиять на роли
        need_global = {n for m in members if {r.name for r in m.roles} & global_role_names for n in cands[m.id]}
        all_names = {n for v in cands.values() for n in v}
        infos = await bot.wiki.users_info([n for n in all_names if n not in need_global], with_global=False)
        infos |= await bot.wiki.users_info(list(need_global), with_global=True)
        rows, new_cands = [], []
        for m in members:
            have = {r.name for r in m.roles}
            info = match.pick(cands[m.id], infos)
            row = audit.Row(
                who=m.display_name + (f" (логин {m.name})" if m.name != m.display_name else ""),
                mine=", ".join(sorted(have & managed)) or "—",
                info=info,
                linked=linked.get(m.id),
            )
            if info:
                row.decision = rules.decide(bot.cfg.admit, gcfg, info, have)
                if not row.linked:
                    new_cands.append((m.id, info.name, "nick"))
            rows.append(row)
        if new_cands:
            await bot.store.set_candidates(guild.id, new_cands)
        head, body = audit.build(guild.name, rows)
        file = lambda: discord.File(io.BytesIO(body.encode()), filename=f"audit-{guild.id}.txt")  # noqa: E731
        if await bot.report(guild.id, head, file=file()):
            await inter.followup.send(head + "\nПолный отчёт файлом — в служебном канале.", ephemeral=True)
        else:  # канала нет или нет права — файл тому, кто спросил
            await inter.followup.send(
                head + "\nВ служебный канал написать не удалось, файл здесь.", ephemeral=True, file=file()
            )
        await bot.store.log(str(inter.user.id), "audit", str(guild.id), head)
        log.info("audit %s: %s", guild.id, head)
