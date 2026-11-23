from types import SimpleNamespace

from tests.conftest import GUILD, info


def member(*roles: str, high: tuple[str, ...] = ()) -> SimpleNamespace:
    """Участник Discord, насколько он нужен evaluate: id, упоминание, роли, сервер. high — роли выше роли бота."""
    guild = SimpleNamespace(
        id=GUILD,
        roles=[
            SimpleNamespace(name=r, is_assignable=lambda r=r: r not in high)
            for r in ("✔", "Администратор", "🆕 new user")
        ],
    )
    return SimpleNamespace(id=1, mention="<@1>", roles=[r for r in guild.roles if r.name in roles], guild=guild)


async def test_evaluate_verdict(bot):
    """Холостой режим: /sync показывает только тех, у кого роли изменились бы, отказ или нет учётки."""
    v = await bot.evaluate(member("🆕 new user"), info(groups=["sysop"]), "X")
    assert v.notable and "выдать Администратор, ✔, снять 🆕 new user)" in v.text
    v = await bot.evaluate(member("✔"), info(editcount=500), "X")
    assert not v.notable and "выдать —, снять —" in v.text
    v = await bot.evaluate(member("✔"), info(blocked=True), "X")
    assert v.notable and "не пройдены" in v.text
    v = await bot.evaluate(member(), None, "Нет такого")
    assert v.notable and "нет участника" in v.text


async def test_rejected_newcomer_keeps_new_user_role(bot):
    """Отказ по правилу сервера (мало правок) — 🆕 new user не снимается: он ещё новичок."""
    v = await bot.evaluate(member("🆕 new user"), info(editcount=5), "X")
    assert v.notable and "правок 5 < 101" in v.text


def test_dry_run_per_guild(cfg):
    cfg.guild[GUILD].dry_run = False
    assert cfg.dry_for(GUILD) is False and cfg.dry_for(1) is True


async def test_apply_skips_roles_above_bot_but_gives_the_rest(bot, monkeypatch):
    """Роль выше роли бота (Администратор) не должна срывать выдачу ✔ и снятие 🆕: один вызов с такой ролью даёт 403."""
    out, calls = [], []

    async def report(gid, text, file=None):
        out.append(text)
        return True

    async def add_roles(*r, reason=""):
        assert all(x.is_assignable() for x in r), "роль выше роли бота в вызове"
        calls.append(("add", [x.name for x in r]))

    async def remove_roles(*r, reason=""):
        calls.append(("remove", [x.name for x in r]))

    monkeypatch.setattr(bot, "report", report)
    bot.cfg.guild[GUILD].dry_run = False
    m = member("🆕 new user", high=("Администратор",))
    m.add_roles, m.remove_roles = add_roles, remove_roles
    await bot.evaluate(m, info(groups=["sysop"]), "X")
    assert ("add", ["✔"]) in calls and ("remove", ["🆕 new user"]) in calls
    assert "выше роли бота, не трону: Администратор" in out[0]
