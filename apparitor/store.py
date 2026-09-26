"""Связки Discord ↔ вики-аккаунт и ожидающие проверки коды. SQLite."""
from __future__ import annotations
import sqlite3
import datetime as dt

SCHEMA = """
create table if not exists links (
  discord_id integer primary key, wiki_name text not null, verified_at text not null,
  method text not null, evidence text);
create table if not exists pending (
  discord_id integer primary key, wiki_name text not null, code text not null, issued_at text not null);
create table if not exists log (
  ts text, actor text, action text, target text, detail text);
"""


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def now(self) -> str:
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def set_pending(self, discord_id: int, wiki_name: str, code: str) -> None:
        self.db.execute("insert or replace into pending values (?,?,?,?)",
                        (discord_id, wiki_name, code, self.now()))
        self.db.commit()

    def get_pending(self, discord_id: int):
        r = self.db.execute("select wiki_name, code, issued_at from pending where discord_id=?",
                            (discord_id,)).fetchone()
        return r and {"wiki_name": r[0], "code": r[1],
                      "issued_at": dt.datetime.strptime(r[2], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)}

    def link(self, discord_id: int, wiki_name: str, method: str, evidence: str) -> None:
        self.db.execute("insert or replace into links values (?,?,?,?,?)",
                        (discord_id, wiki_name, self.now(), method, evidence))
        self.db.execute("delete from pending where discord_id=?", (discord_id,))
        self.db.commit()

    def wiki_of(self, discord_id: int) -> str | None:
        r = self.db.execute("select wiki_name from links where discord_id=?", (discord_id,)).fetchone()
        return r and r[0]

    def all_links(self) -> list[tuple[int, str]]:
        return self.db.execute("select discord_id, wiki_name from links").fetchall()

    def log(self, actor: str, action: str, target: str, detail: str = "") -> None:
        self.db.execute("insert into log values (?,?,?,?,?)", (self.now(), actor, action, target, detail))
        self.db.commit()
