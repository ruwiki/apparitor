from types import SimpleNamespace

from tests.conftest import GUILD, info


def member(*roles: str) -> SimpleNamespace:
    """Участник Discord, насколько он нужен evaluate: id, упоминание, роли, сервер."""
    guild = SimpleNamespace(id=GUILD, roles=[SimpleNamespace(name=r) for r in ("✔", "Администратор", "🆕 new user")])
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
