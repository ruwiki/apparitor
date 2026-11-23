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
    v = await bot.evaluate(member("✔"), info(), "X")
    assert not v.notable and "выдать —, снять —" in v.text
    v = await bot.evaluate(member("✔"), info(blocked=True), "X")
    assert v.notable and "не пройдены" in v.text
    v = await bot.evaluate(member(), None, "Нет такого")
    assert v.notable and "нет участника" in v.text
