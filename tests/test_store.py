import datetime as dt

from apparitor.store import Store, parse_ts


async def test_pending_link_roundtrip():
    s = Store({"kind": "sqlite", "sqlite_path": ":memory:"})
    assert await s.health()
    await s.set_pending(1, 7, "", "state")
    p = await s.pending_by_state("state")
    assert p["discord_id"] == 1 and p["guild_id"] == 7 and p["issued_at"].tzinfo is dt.UTC
    await s.link(1, "Тест ✔", "oauth", "sub")
    assert await s.wiki_of(1) == "Тест ✔"
    assert await s.get_pending(1) is None, "link снимает ожидание в той же транзакции"
    assert await s.all_links() == [(1, "Тест ✔")]


async def test_candidates_upsert():
    s = Store({"kind": "sqlite", "sqlite_path": ":memory:"})
    await s.set_candidates(7, [(10, "A", "nick")])
    await s.set_candidates(7, [(10, "A2", "nick"), (11, "B", "nick")])
    assert s._exec("select discord_id, wiki_name from candidates order by discord_id").fetchall() == [
        (10, "A2"),
        (11, "B"),
    ]


def test_parse_ts():
    assert parse_ts("2026-09-26T15:45:48Z").hour == 15
