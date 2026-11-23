from types import SimpleNamespace

import pytest

from apparitor.board import tier
from tests.conftest import GUILD
from tests.test_gate_rooms import capture
from tests.test_joins import Chan, has, set_mode

GENERAL, PRIVATE_CAT, MEMES = 951792828546162709, 999, 733949614385791026
OPEN_CAT = 360741077193326592


class FakeReaction:
    def __init__(self, count, users):
        self.emoji, self.count, self._users = "⭐", count, users

    def users(self):
        async def gen():
            for u in self._users:
                yield SimpleNamespace(id=u)

        return gen()


class FakeMsg:
    def __init__(self, mid=5, stars=7, author=1, starrers=(), bot=False):
        self.id, self.guild = mid, SimpleNamespace(id=GUILD)
        self.author = SimpleNamespace(id=author, bot=bot)
        self.reactions = [FakeReaction(stars, list(starrers))]
        self.jump_url = f"https://discord.com/channels/{GUILD}/{GENERAL}/{mid}"
        self.forwarded = []

    async def forward(self, dest):
        self.forwarded.append(dest)
        return SimpleNamespace(id=900 + self.id)


class SrcChannel:
    def __init__(self, cid, cat, msg):
        self.id, self.category_id, self.guild, self._msg = cid, cat, SimpleNamespace(id=GUILD), msg

    async def fetch_message(self, mid):
        return self._msg


def setup(bot, monkeypatch, msg, cid=GENERAL, cat=OPEN_CAT):
    board = Chan()
    board.id = MEMES
    board.edits = []

    def partial(i):
        async def edit(content):
            board.edits.append((i, content))

        return SimpleNamespace(edit=edit)

    async def send(content=None, **kw):
        board.sent.append((content, kw))
        return SimpleNamespace(id=700 + msg.id)

    board.send, board.get_partial_message = send, partial
    src = SrcChannel(cid, cat, msg)
    guild = SimpleNamespace(
        id=GUILD,
        get_channel_or_thread=lambda i: src if i == cid else None,
        get_channel=lambda i: board if i == MEMES else None,
    )
    monkeypatch.setattr(bot, "get_guild", lambda gid: guild)
    return board


def ev(mid=5, cid=GENERAL, emoji="⭐"):
    return SimpleNamespace(guild_id=GUILD, channel_id=cid, message_id=mid, emoji=emoji, user_id=9)


def test_tiers_like_starboard():
    assert [tier(n) for n in (0, 7, 9, 10, 19, 20, 25)] == ["⭐", "⭐", "⭐", "🌟", "🌟", "💫", "💫"]


async def test_dry_reports_once_per_count(bot, monkeypatch):
    out = capture(bot)
    msg = FakeMsg(stars=7)
    board = setup(bot, monkeypatch, msg)
    await bot.board.reaction(ev())
    await bot.board.reaction(ev())  # то же число звёзд — не повторяем
    assert len([line for line in out if "доска" in line]) == 1
    assert "переслал бы" in has(out, "доска") and "⭐ 7" in has(out, "доска")
    assert board.sent == [] and msg.forwarded == []  # dry: на сервер не пишем


async def test_live_posts_then_updates_counter(bot, monkeypatch):
    capture(bot)
    set_mode(bot, starboard="live")
    msg = FakeMsg(stars=7)
    board = setup(bot, monkeypatch, msg)
    await bot.board.reaction(ev())
    assert board.sent[0][0] == f"⭐ **7** | {msg.jump_url}" and msg.forwarded == [board]
    assert await bot.store.board_get(5) == (705, 905, 7)
    msg.reactions[0].count = 10
    await bot.board.reaction(ev())
    assert board.edits == [(705, f"🌟 **10** | {msg.jump_url}")] and len(msg.forwarded) == 1  # второй раз не пересылаем
    await bot.board.reaction(ev())  # число то же — не правим
    assert len(board.edits) == 1


async def test_below_threshold_and_self_star(bot, monkeypatch):
    capture(bot)
    set_mode(bot, starboard="live")
    msg = FakeMsg(stars=7, author=1, starrers=[2, 1, 3])  # среди семи — звезда автора: остаётся шесть
    board = setup(bot, monkeypatch, msg)
    await bot.board.reaction(ev())
    assert board.sent == [] and await bot.store.board_get(5) is None
    bot.gcfg(GUILD).board_self_star = True  # если свои звёзды считаются, порог пройден
    await bot.board.reaction(ev())
    assert len(board.sent) == 1


@pytest.mark.parametrize("cid,cat", [(MEMES, OPEN_CAT), (777, PRIVATE_CAT)])
async def test_sources_only_open_channels(bot, monkeypatch, cid, cat):
    """Доска сама и закрытые каналы (не в списке категорий) — не источники."""
    capture(bot)
    set_mode(bot, starboard="live")
    msg = FakeMsg(stars=30)
    board = setup(bot, monkeypatch, msg, cid=cid, cat=cat)
    await bot.board.reaction(ev(cid=cid))
    assert board.sent == [] and msg.forwarded == []


async def test_meta_by_id_and_bot_authors_and_other_emoji(bot, monkeypatch):
    capture(bot)
    set_mode(bot, starboard="live")
    meta = 223183550965481473
    msg = FakeMsg(stars=8)
    board = setup(bot, monkeypatch, msg, cid=meta, cat=None)
    await bot.board.reaction(ev(cid=meta))
    assert len(board.sent) == 1  # #meta указан в board_sources по id
    other = FakeMsg(mid=6, stars=50, bot=True)
    board2 = setup(bot, monkeypatch, other)
    await bot.board.reaction(ev(mid=6))
    assert board2.sent == []  # сообщения ботов не пересылаем
    await bot.board.reaction(ev(mid=6, emoji="👍"))
    assert board2.sent == []


async def test_forward_failure_is_reported(bot, monkeypatch):
    import discord

    out = capture(bot)
    set_mode(bot, starboard="live")
    msg = FakeMsg(stars=9)

    async def boom(dest):
        raise discord.Forbidden(SimpleNamespace(status=403, reason="x"), "нет доступа")

    msg.forward = boom
    setup(bot, monkeypatch, msg)
    await bot.board.reaction(ev())
    assert "Доска: не вышло переслать" in has(out, "Доска")
