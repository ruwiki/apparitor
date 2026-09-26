from tests.conftest import GUILD, info

RUWIKI = 1044474820089368666
CLERKS = 1071563548678959134


def test_admissible_thresholds(bot):
    assert bot.admissible(info())[0]
    assert not bot.admissible(info(blocked=True))[0]
    bot.cfg["admit"]["min_edits"] = 100
    assert not bot.admissible(info(editcount=99))[0]
    bot.cfg["admit"]["min_age_days"] = 0
    assert bot.admissible(info(editcount=100, registration="2999-01-01T00:00:00Z"))[0], (
        "0 = не проверять даже при сдвиге часов"
    )


def test_wanted_roles_per_guild(bot):
    i = info(groups=["sysop", "checkuser", "suppress"], sysop=True, apat=True)
    assert bot.wanted_roles(GUILD, i) == ["✔", "Администратор"]
    assert bot.wanted_roles(RUWIKI, i) == ["аутентифицирован(а)", "Администратор", "ЧЮ", "Ревизор"]
    assert bot.wanted_roles(CLERKS, i) == ["ЧЮ"]
    assert bot.wanted_roles(999, i) == []


def test_managed_roles_include_remove_on_confirm(bot):
    assert "🆕 new user" in bot.managed_roles(GUILD)
    assert "Арбитр" in bot.managed_roles(CLERKS)


def test_commands_registered(bot):
    assert sorted(c.name for c in bot.tree.get_commands()) == ["audit", "auth", "confirm", "status", "sync", "verify"]
