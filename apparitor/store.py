"""Связки Discord ↔ вики-аккаунт, ожидающие коды/state и журнал действий.
Бэкенд: ToolsDB (MySQL) на Toolforge — если заданы TOOL_TOOLSDB_USER/PASSWORD; иначе sqlite для
локальной отладки. NFS-домашка тула для sqlite не годится, поэтому на Toolforge только ToolsDB."""
from __future__ import annotations
import os
import sqlite3
import datetime as dt

TOOLSDB_HOST = "tools.db.svc.wikimedia.cloud"

SCHEMA = [
    """create table if not exists links (
         discord_id bigint primary key, wiki_name varchar(255) not null, verified_at varchar(20) not null,
         method varchar(32) not null, evidence varchar(255))""",
    """create table if not exists pending (
         discord_id bigint primary key, wiki_name varchar(255) not null, code varchar(64) not null,
         issued_at varchar(20) not null)""",
    """create table if not exists log (
         id integer primary key {autoinc}, ts varchar(20), actor varchar(32), action varchar(32),
         target varchar(255), detail text)""",
]


def _ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


class Store:
    def __init__(self, sqlite_path: str = "apparitor.db"):
        user = os.environ.get("TOOL_TOOLSDB_USER")
        if user:
            import pymysql
            self.kind = "mysql"
            self.db = pymysql.connect(host=TOOLSDB_HOST, user=user, password=os.environ["TOOL_TOOLSDB_PASSWORD"],
                                      database=f"{user}__apparitor", charset="utf8mb4", autocommit=True)
            autoinc = "auto_increment"
        else:
            self.kind = "sqlite"
            self.db = sqlite3.connect(sqlite_path, isolation_level=None)
            autoinc = "autoincrement"
        for stmt in SCHEMA:
            self._exec(stmt.format(autoinc=autoinc))
        if self.kind == "sqlite":
            self._exec("create index if not exists pending_code on pending(code)")

    # --- низкий уровень -----------------------------------------------------
    def _exec(self, sql: str, params: tuple = ()):
        if self.kind == "mysql":
            self.db.ping(reconnect=True)
            cur = self.db.cursor()
            cur.execute(sql.replace("?", "%s"), params)
            return cur
        return self.db.execute(sql, params)

    def _one(self, sql: str, params: tuple = ()):
        return self._exec(sql, params).fetchone()

    def _upsert(self, table: str, cols: str, params: tuple):
        marks = ",".join("?" * len(params))
        verb = "replace into" if self.kind == "mysql" else "insert or replace into"
        self._exec(f"{verb} {table} ({cols}) values ({marks})", params)

    @staticmethod
    def now() -> str:
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- ожидающие ----------------------------------------------------------
    def set_pending(self, discord_id: int, wiki_name: str, code: str) -> None:
        self._upsert("pending", "discord_id, wiki_name, code, issued_at", (discord_id, wiki_name, code, self.now()))

    def get_pending(self, discord_id: int):
        r = self._one("select wiki_name, code, issued_at from pending where discord_id=?", (discord_id,))
        return r and {"wiki_name": r[0], "code": r[1], "issued_at": _ts(r[2])}

    def pending_by_state(self, state: str):
        """Для OAuth: state хранится в поле code, wiki_name пустое."""
        r = self._one("select discord_id, issued_at from pending where code=?", (state,))
        return r and {"discord_id": int(r[0]), "issued_at": _ts(r[1])}

    # --- связки ---------------------------------------------------------------
    def link(self, discord_id: int, wiki_name: str, method: str, evidence: str) -> None:
        self._upsert("links", "discord_id, wiki_name, verified_at, method, evidence",
                     (discord_id, wiki_name, self.now(), method, evidence))
        self._exec("delete from pending where discord_id=?", (discord_id,))

    def wiki_of(self, discord_id: int) -> str | None:
        r = self._one("select wiki_name from links where discord_id=?", (discord_id,))
        return r and r[0]

    def all_links(self) -> list[tuple[int, str]]:
        return [(int(a), b) for a, b in self._exec("select discord_id, wiki_name from links").fetchall()]

    # --- журнал ---------------------------------------------------------------
    def log(self, actor: str, action: str, target: str, detail: str = "") -> None:
        self._exec("insert into log (ts, actor, action, target, detail) values (?,?,?,?,?)",
                   (self.now(), actor, action, target, detail))
