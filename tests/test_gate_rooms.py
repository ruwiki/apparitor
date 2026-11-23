from types import SimpleNamespace

from tests.conftest import GUILD, info
from tests.test_bot import member


def capture(bot):
    out = []

    async def report(gid, text, file=None):
        out.append(text)
        return True

    bot.report = report
    return out


async def test_gate_check_links_and_judges(bot, monkeypatch):
    """Связка из WikiAuthBot → имя по номеру CentralAuth → правило сервера; связка записана."""
    capture(bot)

    async def whois(gid, did):
        return 77

    async def by_id(cid):
        return "Новичок" if cid == 77 else None

    async def user_info(name):
        return info(name=name, editcount=50, registration="2020-01-01T00:00:00Z")

    monkeypatch.setattr(bot.gate.wab, "whois", whois)
    monkeypatch.setattr(bot.wiki, "name_by_central_id", by_id)
    monkeypatch.setattr(bot.wiki, "user_info", user_info)
    m = member("🆕 new user")
    m.name = "newbie"
    line = await bot.gate.check(m)
    assert "Новичок" in line and "правок 50 < 101" in line
    assert await bot.store.wiki_of(1) == "Новичок"


async def test_gate_no_link(bot, monkeypatch):
    async def whois(gid, did):
        return None

    monkeypatch.setattr(bot.gate.wab, "whois", whois)
    assert await bot.gate.check(member("🆕 new user")) is None


async def test_gate_report_batches(bot):
    out = capture(bot)
    await bot.gate.tell(GUILD, ["a"] * 6)
    assert len(out) == 1 and "новых итогов 6" in out[0] and out[0].startswith("[холостой]")


async def test_new_channel_dry_run(bot):
    out = capture(bot)
    everyone = SimpleNamespace()
    guild = SimpleNamespace(id=GUILD, default_role=everyone, roles=[])
    tracked = SimpleNamespace(
        guild=guild,
        category_id=360739274045390848,
        name="pro-x",
        mention="<#5>",
        permissions_for=lambda r: SimpleNamespace(view_channel=False),
    )
    await bot.rooms.channel_created(tracked)
    assert "создал бы роль «#pro-x»" in out[-1] and "без проверки" not in out[-1]
    stray = SimpleNamespace(
        **{**vars(tracked), "category_id": 1, "permissions_for": lambda r: SimpleNamespace(view_channel=True)}
    )
    await bot.rooms.channel_created(stray)
    assert "вне отслеживаемых" in out[-1] and "без проверки" in out[-1]


async def test_untracked_guild_silent(bot):
    out = capture(bot)
    ch = SimpleNamespace(guild=SimpleNamespace(id=1044474820089368666), category_id=1)
    await bot.rooms.channel_created(ch)
    assert out == []


async def test_foreign_reaction_skips_db(bot, monkeypatch):
    async def boom(*a):
        raise AssertionError("БД не должна спрашиваться")

    await bot.rooms.ours()
    monkeypatch.setattr(bot.store, "reaction", boom)
    await bot.rooms.reaction(SimpleNamespace(guild_id=GUILD, user_id=5, message_id=123, emoji="👍"), True)


def fake_member(role=None, optout_channels=()):
    """Участник с ловушками: любой вызов, меняющий сервер, роняет тест."""
    calls = []

    async def boom(*a, **k):
        calls.append((a, k))
        raise AssertionError("в холостом режиме сервер менять нельзя")

    guild = SimpleNamespace(
        id=GUILD,
        name="S",
        get_role=lambda i: role,
        get_channel=lambda i: SimpleNamespace(id=i, name="c", mention=f"<#{i}>", set_permissions=boom),
    )
    return SimpleNamespace(
        id=7, mention="<@7>", bot=False, guild=guild, add_roles=boom, remove_roles=boom, create_dm=boom
    )


async def test_reaction_dry_run_only_reports(bot):
    """Реакция на сообщении бота при dry_run: роль не меняется, в служебном канале строка «выдал бы»."""
    out = capture(bot)
    await bot.store.add_reaction(GUILD, 123, "🇦", "role", 99)
    m = fake_member(role=SimpleNamespace(id=99, name="all"))
    p = SimpleNamespace(guild_id=GUILD, user_id=7, message_id=123, emoji="🇦", member=m, channel_id=1)
    await bot.rooms.reaction(p, True)
    assert out and out[-1].startswith("[холостой]") and "выдал бы «all»" in out[-1]
    await bot.rooms.reaction(p, False)
    assert "снял бы «all»" in out[-1]


async def test_optout_dry_run_only_reports(bot):
    out = capture(bot)
    await bot.store.add_reaction(GUILD, 124, "📭", "optout", 0)
    m = fake_member()
    p = SimpleNamespace(guild_id=GUILD, user_id=7, message_id=124, emoji="📭", member=m, channel_id=1)
    await bot.rooms.reaction(p, True)
    assert "[холостой]" in out[-1] and "📭" in out[-1]
    assert await bot.store.optouts(GUILD) == []  # и в БД выход не записан


async def test_forward_dry_run_does_nothing(bot, monkeypatch):
    await bot.store.set_optout(GUILD, 7, True)

    async def boom(*a, **k):
        raise AssertionError("пересылка в холостом режиме")

    async def member(gid, did):  # участник найден: иначе тест прошёл бы, не дойдя до пересылки
        return fake_member()

    monkeypatch.setattr(bot, "member", member)

    ch = bot.gcfg(GUILD).optout_channels[0]
    m = SimpleNamespace(guild=SimpleNamespace(id=GUILD), channel=SimpleNamespace(id=ch), forward=boom)
    await bot.rooms.message(m)
