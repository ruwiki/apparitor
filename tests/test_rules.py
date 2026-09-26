from apparitor import rules
from apparitor.config import guild_cfg
from tests.conftest import GUILD, info

RUWIKI = 1044474820089368666
CLERKS = 1071563548678959134


def test_admissible_thresholds():
    assert rules.admissible({}, info())[0]
    assert not rules.admissible({}, info(blocked=True))[0]
    assert not rules.admissible({"min_edits": 100}, info(editcount=99))[0]
    assert rules.admissible({"min_age_days": 0}, info(registration="2999-01-01T00:00:00Z"))[0], "0 = не проверять"
    assert not rules.admissible({"min_age_days": 30}, info(registration="2999-01-01T00:00:00Z"))[0]


def test_wanted_roles_per_guild(cfg):
    i = info(groups=["sysop", "checkuser", "suppress"], sysop=True, apat=True)
    assert rules.wanted_roles(guild_cfg(cfg, GUILD), i) == ["✔", "Администратор"]
    assert rules.wanted_roles(guild_cfg(cfg, RUWIKI), i) == ["аутентифицирован(а)", "Администратор", "ЧЮ", "Ревизор"]
    assert rules.wanted_roles(guild_cfg(cfg, CLERKS), i) == ["ЧЮ"]
    assert rules.wanted_roles(guild_cfg(cfg, 999), i) == []
    i = info(groups=["closer", "closer-plus", "vandalfighter", "clerk", "techdeleter", "vrts"])
    assert rules.wanted_roles(guild_cfg(cfg, RUWIKI), i) == [
        "аутентифицирован(а)",
        "ПИ+",
        "Вандалоборец",
        "Клерк",
        "ТУ",
    ], "статусы из JSON гаджета; ПИ+ заменяет ПИ; vrts = '' не выдаётся"
    assert rules.wanted_roles(guild_cfg(cfg, CLERKS), i) == ["Клерк"]
    g = dict(guild_cfg(cfg, RUWIKI), roles=dict(guild_cfg(cfg, RUWIKI)["roles"], **{"closer-plus": ""}))
    assert "ПИ" in rules.wanted_roles(g, i), "ПИ+ не сопоставлен роли — ПИ остаётся"


def test_managed_and_diff(cfg):
    g = guild_cfg(cfg, GUILD)
    assert "🆕 new user" in rules.managed_roles(g)
    add, rem = rules.role_diff({"🆕 new user", "Модератор чата"}, {"✔"}, rules.managed_roles(g))
    assert add == ["✔"] and rem == ["🆕 new user"], "чужие роли (Модератор чата) не трогаем"


def test_decide_reject_strips_managed(cfg):
    d = rules.decide({}, guild_cfg(cfg, GUILD), info(blocked=True), {"✔", "Администратор", "Модератор чата"})
    assert not d["ok"] and d["add"] == [] and d["remove"] == ["Администратор", "✔"]


def test_commands_registered(bot):
    assert sorted(c.name for c in bot.tree.get_commands()) == ["audit", "auth", "confirm", "status", "sync", "verify"]
