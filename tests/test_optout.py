"""Выход из #readme и #announcements: отдельный пост бота в #roles, строки «🚪 #readme — поставьте реакцию…»."""

from types import SimpleNamespace

import discord

from apparitor.rooms import HEADER, OPTOUT_HEADER, OPTOUT_POOL, POOL, plain
from tests.conftest import GUILD
from tests.test_gate_rooms import capture

README, ANN, ROLES = 353333123381067777, 778916107893473310, 768773664949927946


class FakeMsg:
    def __init__(self, mid, content, channel):
        self.id, self.content, self.channel, self.reactions, self.forwarded = mid, content, channel, [], []

    async def edit(self, content):
        self.content = content

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def forward(self, dest):
        self.forwarded.append(dest)


class FakeChannel:
    """Канал: сообщения, история, записи участников (настоящий PermissionOverwrite)."""

    def __init__(self, cid, name, history=()):
        self.id, self.name, self.mention = cid, name, f"<#{cid}>"
        self.msgs, self.ow, self.n, self._history = {}, {}, 0, list(history)
        self.guild = SimpleNamespace(id=GUILD)

    async def send(self, content, **kw):
        self.n += 1
        self.msgs[1000 + self.n] = m = FakeMsg(1000 + self.n, content, self)
        return m

    async def fetch_message(self, mid):
        return self.msgs[mid]

    def overwrites_for(self, member):
        return self.ow.get(member.id, discord.PermissionOverwrite())

    async def set_permissions(self, member, overwrite=None, reason=None):
        if overwrite is None:
            self.ow.pop(member.id, None)
        else:
            self.ow[member.id] = overwrite

    def history(self, limit, oldest_first):
        async def gen():
            for m in self._history[:limit]:
                yield m

        return gen()

    def get_partial_message(self, mid):
        async def remove_reaction(emoji, member):
            self.removed = (mid, emoji, member.id)

        return SimpleNamespace(remove_reaction=remove_reaction)


def world(bot, monkeypatch, mode="live", readme_history=()):
    chans = {
        ROLES: FakeChannel(ROLES, "roles"),
        README: FakeChannel(README, "readme", readme_history),
        ANN: FakeChannel(ANN, "announcements"),
    }
    guild = SimpleNamespace(id=GUILD, name="Сообщество", get_channel=lambda i: chans.get(i))
    monkeypatch.setattr(bot, "get_guild", lambda gid: guild)
    # режим выхода — своя функция: dry_run сервера (в конфиге true) на неё не влияет
    assert bot.cfg.guild[GUILD].dry_run is not False
    if mode == "off":
        bot.cfg.guild[GUILD].features.pop("optout", None)
    else:
        bot.cfg.guild[GUILD].features["optout"] = mode
    return guild, chans


class Dm:
    def __init__(self, closed=False):
        self.sent, self.closed = [], closed

    async def send(self, text):
        if self.closed:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="x"), "закрыто")
        self.sent.append(text)


def member(chans, dm=None, mid=7):
    dm = dm or Dm()

    async def create_dm():
        return dm

    return SimpleNamespace(
        id=mid,
        mention=f"<@{mid}>",
        bot=False,
        guild=SimpleNamespace(id=GUILD, name="Сообщество", get_channel=lambda i: chans.get(i)),
        create_dm=create_dm,
    ), dm


LINE_README = "🚪 #readme — поставьте реакцию, чтобы выйти; снимите — канал вернётся"
LINE_ANN = "🔕 #announcements — поставьте реакцию, чтобы выйти; снимите — канал вернётся"


async def test_exit_post_is_its_own_message_with_reactions_set_by_the_bot(bot, monkeypatch):
    capture(bot)
    guild, chans = world(bot, monkeypatch)
    await bot.rooms.ensure_optout(guild)
    (msg,) = chans[ROLES].msgs.values()
    assert msg.content == f"{OPTOUT_HEADER}\n{LINE_README}\n{LINE_ANN}"
    assert OPTOUT_HEADER.startswith("Подписка в личку:") and not msg.content.startswith(HEADER)
    assert msg.reactions == ["🚪", "🔕"]  # бот ставит реакции сам, иначе нажать нечего
    assert await bot.store.reaction(msg.id, "🚪") == ("optout", README)
    assert await bot.store.reaction(msg.id, "🔕") == ("optout", ANN)
    await bot.rooms.ensure_optout(guild)  # повторный запуск (рестарт, второй on_ready) строк не множит
    assert len(chans[ROLES].msgs) == 1 and msg.content.count("поставьте реакцию, чтобы выйти") == 2


async def test_two_on_ready_at_once_post_once(bot, monkeypatch):
    import asyncio

    capture(bot)
    guild, chans = world(bot, monkeypatch)
    await asyncio.gather(bot.rooms.ensure_optout(guild), bot.rooms.ensure_optout(guild))
    assert len(chans[ROLES].msgs) == 1 and len(await bot.store.reactions_of(GUILD)) == 2


async def test_new_channel_lines_do_not_mix_into_the_exit_post(bot, monkeypatch):
    capture(bot)
    guild, chans = world(bot, monkeypatch)
    await bot.rooms.ensure_optout(guild)
    exit_msg = next(iter(chans[ROLES].msgs.values()))
    emoji = await bot.rooms.add_entry(guild, "<#555>", "role", 99)
    assert len(chans[ROLES].msgs) == 2 and "<#555>" not in exit_msg.content
    role_msg = next(m for m in chans[ROLES].msgs.values() if m is not exit_msg)
    assert role_msg.content == f"{HEADER}\n{emoji} <#555>"
    # а новая строка выхода дописывается в пост выхода, не в пост ролей
    await bot.rooms.add_entry(guild, "#extra — …", "optout", 777, "📤", header=OPTOUT_HEADER)
    assert exit_msg.content.endswith("📤 #extra — …") and "#extra" not in role_msg.content


async def test_dry_reports_once_and_posts_nothing(bot, monkeypatch):
    out = capture(bot)
    guild, chans = world(bot, monkeypatch, mode="dry")
    await bot.rooms.ensure_optout(guild)
    await bot.rooms.ensure_optout(guild)
    lines = [x for x in out if "[холостой] добавил бы" in x]
    assert len(lines) == 2 and "🚪" in lines[0] and "🔕" in lines[1]  # по одной на канал, не по запуску
    assert chans[ROLES].msgs == {} and await bot.store.reactions_of(GUILD) == []


async def test_off_does_nothing_at_all(bot, monkeypatch):
    out = capture(bot)
    guild, chans = world(bot, monkeypatch, mode="off")
    await bot.rooms.ensure_optout(guild)
    assert out == [] and chans[ROLES].msgs == {}
    await bot.store.add_reaction(GUILD, 1001, "🚪", "optout", README)  # реакция на старом посте при выключенной функции
    m, dm = member(chans)
    p = SimpleNamespace(guild_id=GUILD, user_id=7, message_id=1001, emoji="🚪", member=m, channel_id=ROLES)
    await bot.rooms.reaction(p, True)
    assert chans[README].ow == {} and dm.sent == [] and await bot.store.optouts(GUILD, README) == []
    await bot.store.set_optout(GUILD, README, 7, True)
    msg = FakeMsg(5, "новое", SimpleNamespace(id=README))
    msg.guild = SimpleNamespace(id=GUILD)
    await bot.rooms.message(msg)
    assert msg.forwarded == []


def test_exit_emoji_are_not_carls():
    from apparitor.config import load_config

    g = load_config("config.toolforge.toml").guild[GUILD]
    carl = {plain(k) for t in g.reaction_roles.values() for k in t}
    chosen = list(g.optout_emoji.values()) + OPTOUT_POOL
    assert g.optout_emoji == {str(README): "🚪", str(ANN): "🔕"}
    assert not {plain(e) for e in chosen} & carl and not set(chosen) & set(POOL)


async def test_exit_hides_only_view_and_return_restores(bot, monkeypatch):
    capture(bot)
    old = [FakeMsg(1, "правила", None)]
    guild, chans = world(bot, monkeypatch, readme_history=old)
    await bot.rooms.ensure_optout(guild)
    (msg,) = chans[ROLES].msgs.values()
    m, dm = member(chans)
    chans[README].ow[7] = discord.PermissionOverwrite(send_messages=False)  # запрет писать от модератора
    p = SimpleNamespace(guild_id=GUILD, user_id=7, message_id=msg.id, emoji="🚪", member=m, channel_id=ROLES)
    await bot.rooms.reaction(p, True)
    ow = chans[README].ow[7]
    assert ow.view_channel is False and ow.send_messages is False  # прежний запрет жив
    assert 7 not in chans[ANN].ow  # другой канал не тронут
    assert await bot.store.optouts(GUILD, README) == [7] and await bot.store.optouts(GUILD, ANN) == []
    assert dm.sent and "#readme" in dm.sent[0] and old[0].forwarded == [dm]  # короткий канал — целиком
    await bot.rooms.reaction(p, False)  # реакцию сняли — вернулся
    assert chans[README].ow[7].view_channel is None and chans[README].ow[7].send_messages is False
    assert await bot.store.optouts(GUILD, README) == []
    chans[README].ow.pop(7)  # без чужих записей запись исчезает целиком
    await bot.rooms.reaction(p, True)
    await bot.rooms.reaction(p, False)
    assert 7 not in chans[README].ow


async def test_closed_dm_changes_nothing(bot, monkeypatch):
    out = capture(bot)
    guild, chans = world(bot, monkeypatch)
    await bot.rooms.ensure_optout(guild)
    (msg,) = chans[ROLES].msgs.values()
    m, _ = member(chans, Dm(closed=True))
    p = SimpleNamespace(guild_id=GUILD, user_id=7, message_id=msg.id, emoji="🔕", member=m, channel_id=ROLES)
    await bot.rooms.reaction(p, True)
    assert chans[ANN].ow == {} and await bot.store.optouts(GUILD, ANN) == []
    assert chans[ROLES].removed[2] == 7 and "личка закрыта" in out[-1]  # реакцию убираем, канал не прячем


async def test_messages_go_only_to_those_who_left_that_channel(bot, monkeypatch):
    capture(bot)
    world(bot, monkeypatch)
    await bot.store.set_optout(GUILD, README, 7, True)
    await bot.store.set_optout(GUILD, ANN, 8, True)
    dms = {7: Dm(), 8: Dm()}

    async def mem(gid, did):
        return member({}, dms[did], did)[0]

    monkeypatch.setattr(bot, "member", mem)
    m = FakeMsg(5, "новое", SimpleNamespace(id=README))
    m.guild = SimpleNamespace(id=GUILD)
    await bot.rooms.message(m)
    assert m.forwarded == [dms[7]]  # получатель из #readme; тот, кто вышел из #announcements, не получает
