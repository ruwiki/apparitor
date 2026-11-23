"""Выход из #readme и #announcements — строки «− #канал: поставьте реакцию — выйти (…), снимите — вернуться»."""

from types import SimpleNamespace

import discord

from apparitor.rooms import HEADER, OPTOUT_POOL, POOL, plain
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


def world(bot, monkeypatch, live=True, readme_history=()):
    chans = {
        ROLES: FakeChannel(ROLES, "roles"),
        README: FakeChannel(README, "readme", readme_history),
        ANN: FakeChannel(ANN, "announcements"),
    }
    guild = SimpleNamespace(id=GUILD, name="Сообщество", get_channel=lambda i: chans.get(i))
    monkeypatch.setattr(bot, "get_guild", lambda gid: guild)
    bot.cfg.guild[GUILD].dry_run = not live
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


async def test_lines_go_into_one_message_with_common_header(bot, monkeypatch):
    capture(bot)
    guild, chans = world(bot, monkeypatch)
    await bot.rooms.ensure_optout(guild)
    (msg,) = chans[ROLES].msgs.values()
    assert msg.content == (
        f"{HEADER}\n🚪 − #readme: поставьте реакцию — выйти (новые сообщения придут в личку), снимите — вернуться"
        "\n🔕 − #announcements: поставьте реакцию — выйти (новые сообщения придут в личку), снимите — вернуться"
    )
    assert HEADER.startswith("Каналы:") and msg.reactions == ["🚪", "🔕"]
    assert await bot.store.reaction(msg.id, "🚪") == ("optout", README)
    assert await bot.store.reaction(msg.id, "🔕") == ("optout", ANN)
    await bot.rooms.ensure_optout(guild)  # повторный запуск (рестарт) строк не множит
    assert len(chans[ROLES].msgs) == 1 and msg.content.count("поставьте реакцию — выйти (") == 2
    # новый канал дописывается в то же сообщение
    emoji = await bot.rooms.add_entry(guild, "<#555>", "role", 99)
    assert msg.content.endswith(f"\n{emoji} <#555>") and len(chans[ROLES].msgs) == 1


async def test_dry_reports_once_and_posts_nothing(bot, monkeypatch):
    out = capture(bot)
    guild, chans = world(bot, monkeypatch, live=False)
    await bot.rooms.ensure_optout(guild)
    await bot.rooms.ensure_optout(guild)
    lines = [x for x in out if "[холостой] добавил бы" in x]
    assert len(lines) == 2 and "🚪" in lines[0] and "🔕" in lines[1]  # по одной на канал, не по запуску
    assert chans[ROLES].msgs == {} and await bot.store.reactions_of(GUILD) == []


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
