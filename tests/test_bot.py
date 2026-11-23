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


async def test_live_blocked_member_keeps_every_role_and_moderators_get_a_line(bot, monkeypatch):
    """Главное правило: решение человека выше правила. Заблокированный с ✔ и Администратором — снятий 0."""
    out, calls = [], []

    async def report(gid, text, file=None):
        out.append(text)
        return True

    async def remove_roles(*r, reason=""):
        calls.append(("remove", [x.name for x in r]))

    async def add_roles(*r, reason=""):
        calls.append(("add", [x.name for x in r]))

    monkeypatch.setattr(bot, "report", report)
    bot.cfg.guild[GUILD].dry_run = False
    m = member("✔", "Администратор")
    m.add_roles, m.remove_roles = add_roles, remove_roles
    v = await bot.evaluate(m, info(blocked=True), "X")
    assert calls == []
    assert "по правилу снял бы Администратор, ✔ — решают модераторы" in out[0] and v.notable
    assert (
        "решают модераторы" in v.mods and "решают модераторы" not in v.text
    )  # человеку строка для модераторов не идёт
    out.clear()
    m = member("🆕 new user")  # впуск: единственное снятие — своя уборка 🆕
    m.add_roles, m.remove_roles = add_roles, remove_roles
    await bot.evaluate(m, info(groups=["autoreview"]), "X")
    assert ("remove", ["🆕 new user"]) in calls and ("add", ["✔"]) in calls


async def test_each_server_gets_its_own_command_list(bot, monkeypatch):
    """Список команд свой у каждого сервера (guild.<id>.commands); у сообщества все семь."""
    seen = {}

    async def fake_sync(*, guild=None):
        seen[guild.id] = sorted(c.name for c in bot.tree.get_commands(guild=guild))

    monkeypatch.setattr(bot.tree, "sync", fake_sync)
    for gid in (GUILD, 1044474820089368666, 1071563548678959134):
        await bot.sync_guild(gid)
    assert seen[GUILD] == ["audit", "confirm", "link", "login", "status", "sync", "verify"]
    for gid in (1044474820089368666, 1071563548678959134):
        assert {"login", "verify", "confirm", "status", "sync", "link", "audit"} <= set(seen[gid])
    registered = {c.name for c in bot.tree.get_commands()}
    assert set(bot.gcfg(GUILD).commands) <= registered, "в конфиге нет опечаток в именах команд"


# --- features.admit: роли тому, кто сам подтвердил аккаунт (/login, /confirm), независимо от dry_run сервера ----------
def recording(bot, monkeypatch):
    calls, out = [], []

    async def report(gid, text, file=None):
        out.append(text)
        return True

    async def add_roles(*r, reason=""):
        calls.append(("add", sorted(x.name for x in r)))

    async def remove_roles(*r, reason=""):
        calls.append(("remove", sorted(x.name for x in r)))

    monkeypatch.setattr(bot, "report", report)
    m = member("🆕 new user")
    m.add_roles, m.remove_roles = add_roles, remove_roles
    return m, calls, out


async def test_self_confirmed_admission_follows_features_admit_not_server_dry_run(bot, monkeypatch):
    m, calls, out = recording(bot, monkeypatch)
    assert bot.dry(GUILD) is True and bot.cfg.feature(GUILD, "admit") == "live"  # сервер холостой, впуск живой
    v = await bot.evaluate(m, info(editcount=500), "X", self_confirmed=True)
    assert calls == [("add", ["✔"]), ("remove", ["🆕 new user"])] and v.text.startswith("Роли: выдать ✔")
    assert not any("[холостой]" in x for x in out)


async def test_other_paths_stay_on_server_dry_run(bot, monkeypatch):
    """Обход gate (quiet), /sync и /link — по dry_run сервера: массово ✔ не раздаём."""
    m, calls, out = recording(bot, monkeypatch)
    v = await bot.evaluate(m, info(editcount=500), "X")
    await bot.evaluate(m, info(editcount=500), "X", quiet=True)
    assert calls == [] and "Холостой режим" in v.text and any("[холостой]" in x for x in out)


async def test_admit_dry_overrides_a_live_server_and_missing_key_follows_the_server(bot, monkeypatch):
    m, calls, _ = recording(bot, monkeypatch)
    bot.cfg.guild[GUILD].dry_run = False
    bot.cfg.guild[GUILD].features["admit"] = "dry"
    await bot.evaluate(m, info(editcount=500), "X", self_confirmed=True)
    assert calls == []  # admit = dry держит впуск холостым, хоть сервер и боевой
    del bot.cfg.guild[GUILD].features["admit"]
    await bot.evaluate(m, info(editcount=500), "X", self_confirmed=True)
    assert calls == [("add", ["✔"]), ("remove", ["🆕 new user"])]  # ключа нет — решает сервер (боевой)
    calls.clear()
    bot.cfg.guild[GUILD].dry_run = True
    await bot.evaluate(m, info(editcount=500), "X", self_confirmed=True)
    assert calls == []  # ключа нет, сервер холостой — холостой


async def test_oauth_callback_and_confirm_are_self_confirmed(bot, monkeypatch):
    """/login (колбэк → after_link) и /confirm — самоподтверждение: при admit = live роли выдаются."""
    from types import SimpleNamespace

    m, calls, _ = recording(bot, monkeypatch)

    async def mem(gid, did):
        return m

    async def user_info(name):
        return info(name=name, editcount=500)

    monkeypatch.setattr(bot, "member", mem)
    monkeypatch.setattr(bot.wiki, "user_info", user_info)
    assert (await bot.after_link(1, GUILD, "X")).startswith("Роли: выдать ✔")
    assert calls == [("add", ["✔"]), ("remove", ["🆕 new user"])]
    calls.clear()

    async def get_pending(uid):
        import datetime as dt

        return {"wiki_name": "X", "code": "apparitor-1", "issued_at": dt.datetime.now(dt.UTC)}

    async def find(name, code, since):
        return "777"

    sent = []

    async def send(text, **kw):
        sent.append(text)

    async def noop(**kw):
        pass

    async def link(*a):
        pass

    monkeypatch.setattr(bot.store, "get_pending", get_pending)
    monkeypatch.setattr(bot.store, "link", link)
    monkeypatch.setattr(bot.wiki, "find_code_in_contribs", find)
    inter = SimpleNamespace(
        guild_id=GUILD, user=m, response=SimpleNamespace(defer=noop), followup=SimpleNamespace(send=send)
    )
    await bot.tree.get_command("confirm").callback(inter)
    assert calls == [("add", ["✔"]), ("remove", ["🆕 new user"])] and "Роли: выдать ✔" in sent[0]
