from apparitor import rules
from apparitor.config import Admit, GuildCfg
from tests.conftest import GUILD, info

RUWIKI = 1044474820089368666
CLERKS = 1071563548678959134


def test_admissible_thresholds():
    assert rules.admissible(Admit(), info())[0]
    assert not rules.admissible(Admit(), info(blocked=True))[0]
    assert rules.admissible(Admit(reject_blocked=False), info(blocked=True))[0]
    assert not rules.admissible(Admit(min_edits=100), info(editcount=99))[0]
    assert rules.admissible(Admit(min_age_days=0), info(registration="2999-01-01T00:00:00Z"))[0], "0 = не проверять"
    assert not rules.admissible(Admit(min_age_days=30), info(registration="2999-01-01T00:00:00Z"))[0]
    assert "lock" in rules.admissible(Admit(), info(blocked=True, locked=True))[1]


def test_wanted_roles_per_guild(cfg):
    i = info(groups=["sysop", "checkuser", "suppress"])
    assert i.sysop and i.apat
    assert rules.wanted_roles(cfg.for_guild(GUILD), i) == ["✔", "Администратор"]
    assert rules.wanted_roles(cfg.for_guild(RUWIKI), i) == ["аутентифицирован(а)", "Администратор", "ЧЮ", "Ревизор"]
    assert rules.wanted_roles(cfg.for_guild(CLERKS), i) == ["ЧЮ"]
    assert rules.wanted_roles(cfg.for_guild(999), i) == []


def test_statuses_and_supersedes(cfg):
    i = info(groups=["closer", "closer-plus", "vandalfighter", "clerk", "techdeleter", "vrts"])
    assert rules.wanted_roles(cfg.for_guild(RUWIKI), i) == [
        "аутентифицирован(а)",
        "ПИ+",
        "Вандалоборец",
        "Клерк",
        "ТУ",
    ], "статусы из JSON гаджета; ПИ+ заменяет ПИ; vrts = '' не выдаётся"
    assert rules.wanted_roles(cfg.for_guild(CLERKS), i) == ["Клерк"]
    g = GuildCfg(roles=dict(cfg.for_guild(RUWIKI).roles, **{"closer-plus": ""}))
    assert "ПИ" in rules.wanted_roles(g, i), "ПИ+ не сопоставлен роли — ПИ остаётся"


def test_managed_and_diff(cfg):
    g = cfg.for_guild(GUILD)
    assert "🆕 new user" in rules.managed_roles(g)
    add, rem = rules.role_diff({"🆕 new user", "Модератор чата"}, {"✔"}, rules.managed_roles(g))
    assert add == ["✔"] and rem == ["🆕 new user"], "чужие роли (Модератор чата) не трогаем"


def test_decide_reject_never_removes_human_roles(cfg):
    """Блокировка: ✔ и роль флага выданы людьми — бот их не снимает, а сообщает, что правило сняло бы."""
    d = rules.decide(Admit(), cfg.for_guild(GUILD), info(blocked=True), {"✔", "Администратор", "Модератор чата"})
    assert not d.ok and d.add == [] and d.remove == [] and d.would_remove == ["Администратор", "✔"] and d.changes


def test_decide_only_cleanup_role_is_removed_and_only_on_admission(cfg):
    g = cfg.for_guild(GUILD)
    ok = rules.decide(cfg.admit_for(GUILD), g, info(groups=["autoreview"]), {"🆕 new user"})
    assert ok.ok and ok.add == ["✔"] and ok.remove == ["🆕 new user"] and ok.would_remove == []
    lost_flag = rules.decide(cfg.admit_for(GUILD), g, info(groups=["autoreview"]), {"✔", "Администратор"})
    assert lost_flag.remove == [] and lost_flag.would_remove == ["Администратор"]  # флаг в рувики пропал: решают люди
    no = rules.decide(cfg.admit_for(GUILD), g, info(editcount=1), {"🆕 new user", "✔"})
    assert not no.ok and no.remove == [] and no.would_remove == ["✔"]  # отказ: 🆕 тоже остаётся


def test_community_rule_apat_or_year_and_edits(cfg):
    """Сервер сообщества: АПАТ либо год стажа и более ста правок (приветствие в #welcome)."""
    a = cfg.admit_for(GUILD)
    assert rules.admissible(a, info(groups=["autoreview"], editcount=3, registration="2026-11-01T00:00:00Z"))[0]
    assert rules.admissible(a, info(editcount=101, registration="2020-01-01T00:00:00Z"))[0]
    assert not rules.admissible(a, info(editcount=100, registration="2020-01-01T00:00:00Z"))[0]
    assert not rules.admissible(a, info(editcount=5000, registration="2999-01-01T00:00:00Z"))[0]
    assert not rules.admissible(a, info(groups=["editor"], blocked=True))[0], "блок сильнее АПАТ"
    assert cfg.admit_for(RUWIKI) is cfg.admit, "без своей секции — общие критерии"


def test_decide_reject_keeps_remove_on_confirm(cfg):
    d = rules.decide(cfg.admit_for(GUILD), cfg.for_guild(GUILD), info(editcount=1), {"🆕 new user"})
    assert not d.ok and d.remove == [] and d.would_remove == []


def test_commands_registered(bot):
    assert sorted(c.name for c in bot.tree.get_commands()) == [
        "audit",
        "confirm",
        "link",
        "login",
        "status",
        "sync",
        "verify",
    ]
