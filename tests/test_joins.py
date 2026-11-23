import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from apparitor.config import load_config
from apparitor.joins import Joins
from tests.conftest import GUILD
from tests.test_gate_rooms import capture

FIX = Path(__file__).parent / "fixtures"
ADMIN = 1 << 3
MANAGE_MESSAGES = 1 << 13


def role(i, name, perms=0, managed=False, default=False, assignable=True):
    return SimpleNamespace(
        id=i,
        name=name,
        managed=managed,
        permissions=SimpleNamespace(value=perms),
        is_default=lambda: default,
        is_assignable=lambda: assignable,
    )


EVERYONE = role(1, "@everyone", default=True)
NEW = role(10, "🆕 new user")
OK = role(12, "✔")
THEME = role(33, "#pro-games")
MOD = role(84, "Модератор чата", perms=MANAGE_MESSAGES)
ADM = role(78, "Администратор", perms=ADMIN)
BOTROLE = role(73, "carl-bot", managed=True)
HIGH = role(99, "Высокая", assignable=False)
ALL_ROLES = [EVERYONE, NEW, OK, THEME, MOD, ADM, BOTROLE, HIGH]


class Chan:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kw):
        self.sent.append((content, kw))


def make_guild(roles=ALL_ROLES, chan=None):
    return SimpleNamespace(
        id=GUILD,
        roles=roles,
        member_count=452,
        get_role=lambda i: next((r for r in roles if r.id == i), None),
        get_channel=lambda i: chan,
    )


def make_member(guild, roles=(EVERYONE,), mid=7):
    m = SimpleNamespace(
        id=mid,
        mention=f"<@{mid}>",
        name="login",
        display_name="Имя",
        bot=False,
        guild=guild,
        roles=list(roles),
        added=[],
        removed=[],
    )

    async def add_roles(*rs, reason=""):
        m.added += [r.name for r in rs]
        m.roles += rs

    async def remove_roles(*rs, reason=""):
        m.removed += [r.name for r in rs]
        m.roles = [r for r in m.roles if r not in rs]

    m.add_roles, m.remove_roles = add_roles, remove_roles
    return m


async def settle(bot):
    await asyncio.gather(*list(bot._tasks))


def has(out, *words):
    """Последняя строка отчёта со всеми словами: порядок строк (приветствие, сверка) тесту не важен."""
    found = [line for line in out if all(w in line for w in words)]
    assert found, (words, out)
    return found[-1]


@pytest.fixture
def jbot(bot, monkeypatch):
    """Бот с мгновенной сверкой и поиском участника в словаре."""
    bot.joins.PARITY_DELAY = 0.2  # Carl «действует» между вызовами теста; события базы дают циклу время
    bot.cfg.guild[GUILD].join_delay = 0  # в конфиге 240 с, как у Carl; задержку проверяют отдельные тесты
    bot.cfg.guild[GUILD].farewell_channel_id = 5
    bot.people = {}

    async def member(gid, did):
        return bot.people.get(did)

    monkeypatch.setattr(bot, "member", member)
    return bot


def set_mode(bot, **modes):
    bot.cfg.guild[GUILD].features.update(modes)


def test_texts_match_carl_verbatim():
    """Приветствие в конфиге — буква в букву сообщение Carl-bot из #welcome; прощание — его embed."""
    g = load_config("config.toolforge.toml").guild[GUILD]
    assert g.welcome_text.replace("{mention}", "<@1>") == (FIX / "carl_welcome.txt").read_text(encoding="utf-8")
    emb = json.loads((FIX / "carl_farewell.json").read_text(encoding="utf-8"))
    m = make_member(make_guild())
    phrase = emb["description"].split(",  ", 1)[1]
    assert phrase in g.farewell_texts and len(g.farewell_texts) == 5  # «{random: …}» Carl: пять вариантов
    e = Joins.farewell(m, phrase)
    assert e.title.startswith(emb["title"].split(" ")[0]) and e.color.value == emb["color"]
    assert e.footer.text == emb["footer"] == "Нас осталось 452"
    assert emb["description"].startswith("<@1> (login),  ") and e.description == f"<@7> (login),  {phrase}"


def test_reaction_tables_name_real_roles():
    """Каждая роль из таблиц реакций существует на сервере ровно одна (имена из выгрузки 23.11.2026)."""
    server = json.loads((FIX / "server_roles.json").read_text(encoding="utf-8"))
    names = [r["name"] for r in server]
    tables = load_config("config.toolforge.toml").guild[GUILD].reaction_roles
    pairs = [(e, n) for t in tables.values() for e, n in t.items()]
    assert len(pairs) == 37 and len(tables) == 4  # 5-е сообщение Carl в удалённом канале не переносим
    for emoji, name in pairs:
        assert names.count(name) == 1, (emoji, name)
    assert len({n for _, n in pairs}) == 36  # две реакции (👼 и 📢) дают одну роль «+ #offtopic»
    g = load_config("config.toolforge.toml").guild[GUILD]
    assert g.reaction_require == {"849976979155451925": ["✔"]} and all(n in names for n in g.sticky_skip)


def test_bad_feature_in_config(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('guilds=[1]\n[guild.1]\n[guild.1.features]\nwelcom = "dry"\n')
    with pytest.raises(SystemExit):
        load_config(str(p))
    p.write_text('guilds=[1]\n[guild.1]\n[guild.1.features]\nwelcome = "yes"\n')
    with pytest.raises(SystemExit):
        load_config(str(p))


async def test_join_dry_parity(jbot):
    """dry: бот пишет, что выдал бы, и через паузу сверяет с Carl: совпало или расхождение."""
    out = capture(jbot)
    guild = make_guild()
    m = make_member(guild)
    jbot.people[7] = m
    await jbot.joins.joined(m)
    assert m.added == []  # dry: сервер не трогаем
    m.roles.append(NEW)  # Carl выдал 🆕
    await settle(jbot)
    assert has(out, "вход <@7>", "выдал бы 🆕 new user").endswith("— совпало с Carl.")
    m2 = make_member(guild, mid=8)
    jbot.people[8] = m2
    await jbot.joins.joined(m2)
    await settle(jbot)  # Carl ничего не выдал
    assert "РАСХОЖДЕНИЕ: Carl не выдал 🆕 new user" in has(out, "вход <@8>")


async def test_join_live_gives_roles_and_greets(jbot):
    out = capture(jbot)
    chan = Chan()
    guild = make_guild(chan=chan)
    set_mode(jbot, join_roles="live", welcome="live")
    m = make_member(guild)
    await jbot.joins.joined(m)
    assert m.added == ["🆕 new user"] and "выдал 🆕 new user" in out[-1]
    text, kw = chan.sent[0]
    assert text.startswith("<:wikipetan") and "<@7>, пожалуйста" in text and kw["allowed_mentions"].users == [m]


async def test_join_off_does_nothing(jbot):
    out = capture(jbot)
    set_mode(jbot, join_roles="off", welcome="off", sticky_roles="off")
    jbot.cfg.guild[GUILD].features.clear()
    await jbot.joins.joined(make_member(make_guild()))
    assert out == []


async def test_sticky_leave_then_return(jbot):
    """Выход: роли записаны без @everyone и служебных. Возврат: безопасные возвращаются, с правами управления — нет,
    выше роли бота — отдельной строкой."""
    out = capture(jbot)
    chan = Chan()
    guild = make_guild(chan=chan)
    set_mode(jbot, sticky_roles="live", farewell="live")
    leaving = make_member(guild, roles=[EVERYONE, OK, THEME, MOD, ADM, BOTROLE, HIGH])
    await jbot.joins.left(leaving)
    assert await jbot.store.sticky_get(GUILD, 7) == [12, 33, 84, 78, 99]
    emb = chan.sent[0][1]["embed"]
    assert emb.description.startswith("<@7> (login),  ") and emb.footer.text == "Нас осталось 452"
    back = make_member(guild)
    await jbot.joins.joined(back)
    assert back.added == ["✔", "#pro-games"]  # Высокая — выше роли бота, не выдана
    line = has(out, "не возвращаю")
    assert "Модератор чата" in line and "Администратор" in line and "выше роли бота: Высокая" in line
    assert await jbot.store.sticky_get(GUILD, 7) == []  # запись использована


async def test_sticky_dry_notes_what_carl_returned(jbot):
    out = capture(jbot)
    guild = make_guild()
    set_mode(jbot, sticky_roles="dry")
    await jbot.joins.left(make_member(guild, roles=[EVERYONE, OK, MOD]))
    assert await jbot.store.sticky_get(GUILD, 7) == [12, 84]  # в dry записываем: к live данные нужны
    back = make_member(guild)
    jbot.people[7] = back
    await jbot.joins.joined(back)
    back.roles += [OK, MOD]  # Carl вернул обе
    await settle(jbot)
    line = has(out, "вернул бы ✔", "совпало с Carl")
    assert "Carl вернул то, что бот не вернул бы: Модератор чата" in line
    assert await jbot.store.sticky_get(GUILD, 7) == [12, 84]  # dry запись не тратит


async def test_farewell_dry_does_not_send(jbot):
    out = capture(jbot)
    chan = Chan()
    set_mode(jbot, farewell="dry")
    await jbot.joins.left(make_member(make_guild(chan=chan)))
    assert chan.sent == [] and "написал бы прощание" in out[-1]


async def test_parity_member_gone(jbot):
    out = capture(jbot)
    await jbot.parity(5, GUILD, "x", {"a"}, set(), 0)
    assert "уже вышел" in out[-1]


async def test_sticky_purge(jbot):
    await jbot.store.sticky_save(GUILD, 1, [5])
    await jbot.store.sticky_purge(GUILD, "2999-01-01T00:00:00Z")
    assert await jbot.store.sticky_get(GUILD, 1) == []


def reaction(mid, emoji, member, user=7):
    return SimpleNamespace(guild_id=GUILD, user_id=user, message_id=mid, emoji=emoji, member=member, channel_id=1)


async def test_config_reaction_dry_and_live(jbot):
    out = capture(jbot)
    guild = make_guild(roles=[EVERYONE, SimpleNamespace(**{**vars(NEW), "name": "all"})])
    m = make_member(guild)
    jbot.people[7] = m
    mid = 1017476950408835182
    await jbot.rooms.reaction(reaction(mid, "🇦", m), True)
    await settle(jbot)
    assert m.added == [] and "выдал бы «all» — РАСХОЖДЕНИЕ: Carl не выдал all" in out[-1]
    m.roles.append(guild.roles[1])  # Carl выдал
    await jbot.rooms.reaction(reaction(mid, "🇦", m), True)
    await settle(jbot)
    assert out[-1].endswith("совпало с Carl.")
    set_mode(jbot, reaction_roles="live")
    m2 = make_member(guild, mid=8)
    await jbot.rooms.reaction(reaction(mid, "🇦", m2, 8), True)
    assert m2.added == ["all"]
    await jbot.rooms.reaction(reaction(mid, "🇦", m2, 8), False)
    assert m2.removed == ["all"]


async def test_config_reaction_variation_selector_and_unknown(jbot):
    capture(jbot)
    set_mode(jbot, reaction_roles="live")
    server = json.loads((FIX / "server_roles.json").read_text(encoding="utf-8"))
    roles = [EVERYONE] + [role(int(r["id"]), r["name"]) for r in server if r["name"] != "@everyone"]
    guild = make_guild(roles=roles)
    m = make_member(guild)
    mid = 1017476950408835182
    await jbot.rooms.reaction(reaction(mid, "🗣️", m), True)  # с селектором U+FE0F, в конфиге без него
    await jbot.rooms.reaction(reaction(mid, "🥔", m), True)  # эмодзи вне таблицы
    assert m.added == ["+ #general"]


async def test_config_reaction_ambiguous_role(jbot):
    out = capture(jbot)
    set_mode(jbot, reaction_roles="live")
    guild = make_guild(roles=[EVERYONE, role(2, "all"), role(3, "all")])
    m = make_member(guild)
    await jbot.rooms.reaction(reaction(1017476950408835182, "🇦", m), True)
    assert m.added == [] and "ролей с именем «all» на сервере 2" in out[-1]


async def test_custom_emoji_by_id(jbot):
    """В выгрузке Carl кастомное эмодзи записано id, у события — как <:имя:id>."""
    capture(jbot)
    set_mode(jbot, reaction_roles="live")
    server = json.loads((FIX / "server_roles.json").read_text(encoding="utf-8"))
    guild = make_guild(roles=[EVERYONE] + [role(int(r["id"]), r["name"]) for r in server if r["name"] != "@everyone"])
    m = make_member(guild)
    await jbot.rooms.reaction(reaction(977692093201907773, "<:featured:232142398921113601>", m), True)
    assert m.added == ["#pro-quality"]


async def test_reaction_whitelist(jbot):
    """Подписки (📊📣) — только держателям ✔: у остальных роль не выдаётся, и в dry это видно в сверке."""
    out = capture(jbot)
    server = json.loads((FIX / "server_roles.json").read_text(encoding="utf-8"))
    guild = make_guild(
        roles=[EVERYONE, OK] + [role(int(r["id"]), r["name"]) for r in server if r["name"] == "votings subscribe"]
    )
    mid = 849976979155451925
    set_mode(jbot, reaction_roles="live")
    stranger, member = make_member(guild), make_member(guild, roles=[EVERYONE, OK], mid=8)
    await jbot.rooms.reaction(reaction(mid, "📊", stranger), True)
    await jbot.rooms.reaction(reaction(mid, "📊", member, 8), True)
    assert stranger.added == [] and member.added == ["votings subscribe"]
    set_mode(jbot, reaction_roles="dry")
    jbot.people[7] = stranger
    await jbot.rooms.reaction(reaction(mid, "📊", stranger), True)
    await settle(jbot)
    assert "не выдал бы" in has(out, "реакция") and "совпало с Carl" in has(out, "реакция")


async def test_join_delay_like_carl(jbot):
    """join_delay: 🆕 выдаётся через заданное время, а не сразу; вышедший за это время — не получает."""
    capture(jbot)
    jbot.cfg.guild[GUILD].join_delay = 0.1
    set_mode(jbot, join_roles="live")
    guild = make_guild()
    m = make_member(guild)
    jbot.people[7] = m
    await jbot.joins.joined(m)
    assert m.added == []  # сразу — нет
    await settle(jbot)
    assert m.added == ["🆕 new user"]
    gone = make_member(guild, mid=9)  # вышел, пока ждали: бот его уже не находит
    await jbot.joins.joined(gone)
    await settle(jbot)
    assert gone.added == []


async def test_farewell_goes_only_to_configured_channel(jbot):
    out = capture(jbot)
    chan = Chan()
    set_mode(jbot, farewell="live")
    jbot.cfg.guild[GUILD].farewell_channel_id = 0  # никуда
    await jbot.joins.left(make_member(make_guild(chan=chan)))
    assert chan.sent == [] and out == []
