"""Связки Discord ↔ вики-аккаунт, ожидающие коды/state и журнал действий.

Бэкенд задаётся явно в конфиге: [db] kind = "toolsdb" (MariaDB ToolsDB на Toolforge, креды из
envvars TOOL_TOOLSDB_USER/PASSWORD) или "sqlite" (только локальная отладка). Драйверы синхронные,
поэтому все публичные методы async и выполняют запрос в отдельном потоке под замком —
зависший ToolsDB не останавливает heartbeat Discord."""
from __future__ import annotations
import asyncio
import logging
import os
import sqlite3
import threading
import datetime as dt

log = logging.getLogger("apparitor.store")
TOOLSDB_HOST = "tools.db.svc.wikimedia.cloud"
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

SCHEMA = {
    "links": """(discord_id bigint primary key, wiki_name varchar(255) not null,
                 verified_at varchar(20) not null, method varchar(32) not null, evidence varchar(255))""",
    "pending": """(discord_id bigint primary key, guild_id bigint not null, wiki_name varchar(255) not null,
                   code varchar(64) not null, issued_at varchar(20) not null)""",
    "candidates": """(guild_id bigint not null, discord_id bigint not null, wiki_name varchar(255) not null,
                      source varchar(32) not null, seen_at varchar(20) not null, primary key (guild_id, discord_id))""",
    "log": """(id integer primary key {autoinc}, ts varchar(20), actor varchar(32), action varchar(32),
               target varchar(255), detail text)""",
}
INDEXES = ["create index {ine} pending_code on pending (code)"]


def now_ts() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime(TS_FMT)


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, TS_FMT).replace(tzinfo=dt.timezone.utc)


class Store:
    def __init__(self, cfg: dict):
        self.kind = cfg.get("kind", "sqlite")
        self.sqlite_path = cfg.get("sqlite_path", "apparitor.db")
        self._lock = threading.Lock()
        self.db = None
        if self.kind == "toolsdb":
            if not os.environ.get("TOOL_TOOLSDB_USER") or not os.environ.get("TOOL_TOOLSDB_PASSWORD"):
                raise SystemExit("db.kind=toolsdb, но нет TOOL_TOOLSDB_USER/PASSWORD в окружении")
        elif self.kind != "sqlite":
            raise SystemExit(f"db.kind: ожидается toolsdb или sqlite, получено {self.kind!r}")
        elif os.environ.get("PORT"):
            raise SystemExit("db.kind=sqlite при заданном PORT: на Toolforge sqlite не годится (NFS, потеря при рестарте)")
        self._connect()
        self._init_schema()

    # --- соединение -------------------------------------------------------------
    def _connect(self):
        if self.kind == "toolsdb":
            import pymysql
            user = os.environ["TOOL_TOOLSDB_USER"]
            self.db = pymysql.connect(host=TOOLSDB_HOST, user=user, password=os.environ["TOOL_TOOLSDB_PASSWORD"],
                                      database=f"{user}__apparitor", charset="utf8mb4", autocommit=True,
                                      connect_timeout=5, read_timeout=10, write_timeout=10)
        else:
            self.db = sqlite3.connect(self.sqlite_path, isolation_level=None, check_same_thread=False)

    def _init_schema(self):
        autoinc = "auto_increment" if self.kind == "toolsdb" else "autoincrement"
        suffix = " default charset=utf8mb4" if self.kind == "toolsdb" else ""
        for name, body in SCHEMA.items():
            self._exec(f"create table if not exists {name} {body.format(autoinc=autoinc)}{suffix}")
        for idx in INDEXES:
            try:  # MariaDB и sqlite понимают IF NOT EXISTS; на всякий случай не падаем
                self._exec(idx.format(ine="if not exists"))
            except Exception as e:  # noqa: BLE001
                log.warning("индекс: %s", e)

    def _exec(self, sql: str, params: tuple | None = None):
        """Синхронно, под замком; при обрыве соединения с ToolsDB — один реконнект и повтор."""
        with self._lock:
            if self.kind == "toolsdb":
                import pymysql
                sql = sql.replace("?", "%s")
                for attempt in (1, 2):
                    try:
                        cur = self.db.cursor()
                        cur.execute(sql, params or None)
                        return cur
                    except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
                        if attempt == 2:
                            raise
                        log.warning("ToolsDB: %s — переподключаюсь", e)
                        try:
                            self.db.close()
                        except Exception:  # noqa: BLE001
                            pass
                        self._connect()
            return self.db.execute(sql, params or ())

    def _tx(self, statements: list[tuple[str, tuple]]):
        """Несколько запросов одной транзакцией."""
        with self._lock:
            if self.kind == "toolsdb":
                self.db.begin()
                try:
                    cur = self.db.cursor()
                    for sql, params in statements:
                        cur.execute(sql.replace("?", "%s"), params or None)
                    self.db.commit()
                except Exception:
                    self.db.rollback()
                    raise
            else:
                self.db.execute("begin")
                try:
                    for sql, params in statements:
                        self.db.execute(sql, params)
                    self.db.execute("commit")
                except Exception:
                    self.db.execute("rollback")
                    raise

    async def _run(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    def _upsert_sql(self, table: str, cols: str, n: int) -> str:
        verb = "replace into" if self.kind == "toolsdb" else "insert or replace into"
        return f"{verb} {table} ({cols}) values ({','.join('?' * n)})"

    # --- публичный API (async) -------------------------------------------------
    async def health(self) -> bool:
        try:
            await self._run(self._exec, "select 1")
            return True
        except Exception as e:  # noqa: BLE001
            log.error("health: %s", e)
            return False

    async def set_pending(self, discord_id: int, guild_id: int, wiki_name: str, code: str) -> None:
        await self._run(self._exec, self._upsert_sql("pending", "discord_id, guild_id, wiki_name, code, issued_at", 5),
                        (discord_id, guild_id, wiki_name, code, now_ts()))

    async def get_pending(self, discord_id: int):
        r = await self._run(lambda: self._exec(
            "select guild_id, wiki_name, code, issued_at from pending where discord_id=?", (discord_id,)).fetchone())
        return r and {"guild_id": int(r[0]), "wiki_name": r[1], "code": r[2], "issued_at": parse_ts(r[3])}

    async def pending_by_state(self, state: str):
        """Для OAuth: state хранится в поле code, wiki_name пустое."""
        r = await self._run(lambda: self._exec(
            "select discord_id, guild_id, issued_at from pending where code=?", (state,)).fetchone())
        return r and {"discord_id": int(r[0]), "guild_id": int(r[1]), "issued_at": parse_ts(r[2])}

    async def link(self, discord_id: int, wiki_name: str, method: str, evidence: str) -> None:
        """Связка и снятие ожидания — одной транзакцией."""
        await self._run(self._tx, [
            (self._upsert_sql("links", "discord_id, wiki_name, verified_at, method, evidence", 5),
             (discord_id, wiki_name, now_ts(), method, evidence)),
            ("delete from pending where discord_id=?", (discord_id,)),
        ])

    async def wiki_of(self, discord_id: int) -> str | None:
        r = await self._run(lambda: self._exec("select wiki_name from links where discord_id=?", (discord_id,)).fetchone())
        return r and r[0]

    async def all_links(self) -> list[tuple[int, str]]:
        rows = await self._run(lambda: self._exec("select discord_id, wiki_name from links").fetchall())
        return [(int(a), b) for a, b in rows]

    async def set_candidates(self, guild_id: int, rows: list[tuple[int, str, str]]) -> None:
        """Сопоставление по нику (не подтверждено человеком): (discord_id, wiki_name, source)."""
        sql = self._upsert_sql("candidates", "guild_id, discord_id, wiki_name, source, seen_at", 5)
        ts = now_ts()
        await self._run(self._tx, [(sql, (guild_id, did, name, src, ts)) for did, name, src in rows])

    async def log(self, actor: str, action: str, target: str, detail: str = "") -> None:
        await self._run(self._exec, "insert into log (ts, actor, action, target, detail) values (?,?,?,?,?)",
                        (now_ts(), actor, action, target, detail))
