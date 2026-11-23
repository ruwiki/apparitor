"""Стена прав сервера: каталог «Добро пожаловать» виден только непроверенным и модераторам, остальное закрыто от тех,
кто зашёл без проверки. Скрипт, не часть бота: бот ничего из этого сам не делает.

    python -m apparitor.wall snapshot --dir D --token-file F      снимок (чтение): каналы — токеном, что видит все,
                                                                  участники — ботом через --read-token-file
    python -m apparitor.wall plan     --dir D [--from СНИМОК]     что изменится; кто что видит до и после
    python -m apparitor.wall apply   --dir D --token-file F      без --yes печатает; с --yes — делает и проверяет
    python -m apparitor.wall rollback --dir D --token-file F      без --yes печатает; с --yes — возвращает

Правки прав — только минимальными битами (добавить или убрать), а не заменой всех перекрытий канала: запреты на
участников и роль «✔ read-only 🔇» переживают скрипт. Откат возвращает только те записи, которые поставил apply, и
только если с тех пор их никто не менял. Новый #welcome откат не удаляет — прячет. Токен для apply и rollback нужен с
правами Manage Channels, Manage Roles и Manage Guild; в репозиторий снимки (в них id участников) не кладём."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

API = "https://discord.com/api/v10"
UA = "DiscordBot (https://github.com/ruwiki/apparitor, wall)"
VIEW, REACT, SEND, ADMIN, HISTORY = 1 << 10, 1 << 6, 1 << 11, 1 << 3, 1 << 16
SHOWN = (("VIEW", VIEW), ("SEND", SEND), ("HISTORY", HISTORY), ("REACT", REACT))
NEW = "new:welcome"  # id нового канала, пока его нет на сервере
GROUPS = ("wall", "roles", "position", "board")
SNAP_FILES = ("guild.json", "guild_channels.json", "guild_roles.json", "members.jsonl")


class PlanError(Exception):
    pass


class RestError(Exception):
    def __init__(self, code: int, text: str):
        super().__init__(f"{code}: {text}")
        self.code = code


# --- Discord REST (синхронно: скрипт разовый, ему не нужен цикл событий) --------------------------------------
class Rest:
    def __init__(self, token: str, pace: float = 0.5):
        self.token, self.pace = token, pace

    def request(self, method: str, path: str, body=None, reason: str = ""):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": self.token, "User-Agent": UA, "Content-Type": "application/json"}
        if reason:
            headers["X-Audit-Log-Reason"] = urllib.parse.quote(reason)
        for _ in range(6):
            req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read()
                if method != "GET":
                    time.sleep(self.pace)  # правки прав идут с малым лимитом
                return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                if e.code == 429:
                    time.sleep(float(json.loads(text).get("retry_after", 2)) + 0.5)
                    continue
                raise RestError(e.code, text[:300]) from None
        raise RestError(429, "лимит запросов не отпускает")


def read_token(path: str, bot: bool) -> str:
    """Файл с токеном: сам токен или строка DISCORD_TOKEN=… (.env). --bot добавляет «Bot »."""
    text = Path(path).expanduser().read_text(encoding="utf-8").strip()
    for line in text.splitlines():
        if line.startswith("DISCORD_TOKEN="):
            text = line.split("=", 1)[1].strip().strip("\"'")
    return f"Bot {text}" if bot else text


# --- снимок -----------------------------------------------------------------------------------------------
@dataclass
class Snap:
    guild: dict
    channels: list[dict]
    roles: list[dict]
    members: list[dict]  # id, bot, roles — больше про участников не храним

    @property
    def gid(self) -> str:
        return self.guild["id"]

    def chan(self, cid: str) -> dict:
        for c in self.channels:
            if c["id"] == cid:
                return c
        raise PlanError(f"нет канала {cid}: снимок надо делать токеном, который видит все каналы (администратора)")


def fetch(rest: Rest, gid: str, readers: Rest | None = None) -> Snap:
    """readers — токен для списка участников (его отдаёт только бот с интентом), по умолчанию тот же."""
    guild = rest.request("GET", f"/guilds/{gid}")
    channels = rest.request("GET", f"/guilds/{gid}/channels")
    roles = rest.request("GET", f"/guilds/{gid}/roles")
    members, after = [], "0"
    while True:
        page = (readers or rest).request("GET", f"/guilds/{gid}/members?limit=1000&after={after}")
        members += [{"id": m["user"]["id"], "bot": bool(m["user"].get("bot")), "roles": m["roles"]} for m in page]
        if len(page) < 1000:
            break
        after = page[-1]["user"]["id"]
    return Snap(guild, channels, roles, members)


def save(snap: Snap, d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "guild.json").write_text(json.dumps(snap.guild, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "guild_channels.json").write_text(json.dumps(snap.channels, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "guild_roles.json").write_text(json.dumps(snap.roles, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = (json.dumps({k: m[k] for k in ("id", "bot", "roles")}) for m in snap.members)  # только это
    (d / "members.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load(d: Path) -> Snap:
    j = lambda n: json.loads((d / n).read_text(encoding="utf-8"))  # noqa: E731
    members = [json.loads(x) for x in (d / "members.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    return Snap(
        j("guild.json"),
        j("guild_channels.json"),
        j("guild_roles.json"),
        [{"id": m["id"], "bot": m["bot"], "roles": m["roles"]} for m in members],
    )


# --- права -----------------------------------------------------------------------------------------------
def get_ow(ch: dict, tid: str):
    for o in ch.get("permission_overwrites", []):
        if o["id"] == tid:
            return int(o["allow"]), int(o["deny"])
    return None


def set_ow(ch: dict, tid: str, ttype: int, val) -> None:
    rest = [o for o in ch.setdefault("permission_overwrites", []) if o["id"] != tid]
    if val is not None:
        rest.append({"id": tid, "type": ttype, "allow": str(val[0]), "deny": str(val[1])})
    ch["permission_overwrites"] = rest


def calc(cur, allow_add=0, allow_rm=0, deny_add=0, deny_rm=0):
    """Минимальная правка записи: добавленный бит выходит из противоположного набора; пустая запись — None."""
    a, d = cur or (0, 0)
    a, d = (a & ~allow_rm & ~deny_add) | allow_add, (d & ~deny_rm & ~allow_add) | deny_add
    return None if (a, d) == (0, 0) else (a, d)


def can_view(roles: dict, gid: str, uid: str, rids: list[str], ch: dict) -> bool:
    """VIEW_CHANNEL по правилам Discord: права ролей → @everyone канала → роли (запрет, разрешение) → участник."""
    base = int(roles[gid]["permissions"])
    for r in rids:
        base |= int(roles[r]["permissions"]) if r in roles else 0
    if base & ADMIN:
        return True
    ow = {o["id"]: o for o in ch.get("permission_overwrites", [])}
    p = base
    if gid in ow:
        p = (p & ~int(ow[gid]["deny"])) | int(ow[gid]["allow"])
    a = d = 0
    for r in rids:
        if r in ow and ow[r]["type"] == 0:
            a |= int(ow[r]["allow"])
            d |= int(ow[r]["deny"])
    p = (p & ~d) | a
    if uid in ow and ow[uid]["type"] == 1:
        p = (p & ~int(ow[uid]["deny"])) | int(ow[uid]["allow"])
    return bool(p & VIEW)


def bits(val) -> str:
    """VIEW . → - : «.» не задано, «+» разрешено, «-» запрещено."""
    a, d = val or (0, 0)
    return " ".join(f"{n}:{'+' if a & b else '-' if d & b else '.'}" for n, b in SHOWN)


# --- план ------------------------------------------------------------------------------------------------
@dataclass
class Spec:
    """Что где на сервере «Сообщество Викимедиа»: имена ролей и id каналов."""

    guild_id: str = "223183550965481473"
    verified: str = "✔"
    newbie: str = "🆕 new user"
    mods: tuple = ("Модератор чата", "Техмодерка")  # видят новый #welcome
    old_mods: tuple = ("Модератор чата",)  # видят старый
    welcome_bots: tuple = ("carl-bot", "WikiAuthBot2", "Apparitor")
    apparitor: str = "Apparitor"
    category: str = "828327760648863744"  # «Добро пожаловать!»
    old_welcome: str = "728220635905523782"
    closed: tuple = (
        "353333123381067777",
        "778916107893473310",
        "1569522756142502030",
    )  # readme, announcements, server-hub
    roles_channel: str = "768773664949927946"
    new_name: str = "welcome"
    # какие роли ботов сохраняют просмотр там, где @everyone его теряет (бот пишет в канале): канал -> имена ролей.
    # Остальные боты просмотр потеряют — их список печатает plan. Ставим на роль бота, не на участника.
    bot_view: dict = field(
        default_factory=lambda: {
            "728220635905523782": ("carl-bot", "WikiAuthBot2"),  # пишут приветствия, пока их не перевели на новый
            "353333123381067777": ("carl-bot",),  # его сообщения в #readme
            "778916107893473310": ("wiki bot",),  # WikiBot отвечает в #announcements
        }
    )


@dataclass
class Opts:
    position: int = 49  # куда поднять роль Apparitor: выше самой верхней реакционной роли (#requests-fast = 48)
    groups: tuple = ("wall", "roles", "position")
    system_channel: bool = False  # перенос системного канала: буст-уведомления с ником бустера увидели бы новички
    rename_old: str = ""
    board_channel: str = ""
    board_categories: tuple = ()
    board_sources: tuple = ()
    board_open_role: str = "all"


@dataclass
class Op:
    kind: str  # position | create | overwrite | system | rename
    text: str
    channel: str = ""
    target: str = ""  # id роли или участника
    ttype: int = 0  # 0 роль, 1 участник
    before: list | None = None  # [allow, deny] или None; для position/system/rename — прежнее значение
    after: list | None = None
    extra: dict = field(default_factory=dict)


class Planner:
    def __init__(self, snap: Snap, spec: Spec, opts: Opts):
        self.before, self.spec, self.opts = snap, spec, opts
        self.snap = copy.deepcopy(snap)
        self.roles = {r["id"]: r for r in snap.roles}
        self.ops: list[Op] = []

    # роли и каналы
    def role(self, name: str) -> str:
        found = [r["id"] for r in self.before.roles if r["name"] == name]
        if len(found) != 1:
            raise PlanError(f"роль «{name}»: найдено {len(found)}, нужна ровно одна")
        return found[0]

    def name(self, cid: str) -> str:
        if cid == NEW:
            return f"#{self.spec.new_name} (новый)"
        return "#" + next((c["name"] for c in self.snap.channels if c["id"] == cid), cid)

    def tname(self, tid: str) -> str:
        if tid == self.spec.guild_id:
            return "@everyone"
        r = self.roles.get(tid)
        return r["name"] if r else f"участник {tid}"

    def edit(self, cid: str, tid: str, ttype: int = 0, why: str = "", **kw) -> None:
        ch = self.snap.chan(cid)
        cur = get_ow(ch, tid)
        new = calc(cur, **kw)
        if new == (None if cur == (0, 0) else cur):
            return
        self.ops.append(
            Op(
                "overwrite",
                why,
                cid,
                tid,
                ttype,
                list(cur) if cur else None,
                list(new) if new else None,
            )
        )
        set_ow(ch, tid, ttype, new)

    # шаги плана
    def position_op(self) -> None:
        rid = self.role(self.spec.apparitor)
        cur = self.roles[rid]["position"]
        if cur != self.opts.position:
            self.ops.append(
                Op(
                    "position",
                    f"роль {self.spec.apparitor}: позиция {cur} → {self.opts.position}",
                    target=rid,
                    before=[cur],
                    after=[self.opts.position],
                )
            )

    def create_welcome(self) -> None:
        sp = self.spec
        old = self.snap.chan(sp.old_welcome)
        ows = {o["id"]: (o["type"], int(o["allow"]), int(o["deny"])) for o in old.get("permission_overwrites", [])}

        def put(tid, ttype=0, **kw):
            cur = (ows[tid][1], ows[tid][2]) if tid in ows else None
            new = calc(cur, **kw)
            if new is None:
                ows.pop(tid, None)
            else:
                ows[tid] = (ttype, *new)

        put(sp.guild_id, allow_add=VIEW | SEND | HISTORY)
        put(self.role(sp.verified), deny_add=VIEW)
        put(self.role(sp.newbie), allow_rm=VIEW)  # иначе 🆕, не снятая с проверенных, возвращает им просмотр
        for n in sp.mods:
            put(self.role(n), allow_add=VIEW)
        for n in sp.welcome_bots:
            put(self.role(n), allow_add=VIEW | SEND)
        # по id: Discord отдаёт перекрытия в другом порядке при каждом запросе, а отпечаток плана должен быть один
        overwrites = [{"id": t, "type": v[0], "allow": str(v[1]), "deny": str(v[2])} for t, v in sorted(ows.items())]
        self.ops.append(
            Op(
                "create",
                f"создать #{sp.new_name} в «Добро пожаловать!» (за основу взяты права старого #welcome)",
                NEW,
                extra={
                    "name": sp.new_name,
                    "type": 0,
                    "parent_id": sp.category,
                    "position": old["position"],
                    "permission_overwrites": overwrites,
                },
            )
        )
        self.snap.channels.append(
            {
                "id": NEW,
                "name": sp.new_name,
                "type": 0,
                "parent_id": sp.category,
                "position": old["position"],
                "permission_overwrites": overwrites,
            }
        )

    def hide_ops(self) -> None:
        sp = self.spec
        ver, new, app = self.role(sp.verified), self.role(sp.newbie), self.role(sp.apparitor)
        o = sp.old_welcome
        self.edit(o, sp.guild_id, why="старый #welcome → архив: @everyone не видит", deny_add=VIEW)
        self.edit(o, ver, why="проверенные видят, писать нельзя", allow_add=VIEW | HISTORY, deny_add=SEND)
        for n in sp.old_mods:
            self.edit(o, self.role(n), why="модераторы видят", allow_add=VIEW)
        self.edit(o, new, why="новички больше не видят старый #welcome", allow_rm=VIEW)
        for c in sp.closed:
            self.edit(c, sp.guild_id, why="@everyone не видит", deny_add=VIEW)
            self.edit(c, ver, why="проверенные видят", allow_add=VIEW)
            self.edit(c, app, why="Apparitor видит (пересылка при выходе из каналов)", allow_add=VIEW)

    def bot_ops(self) -> None:
        """Роли ботов, которым нужен просмотр там, где @everyone его заберёт, — до скрытия, чтобы бот не терял канал."""
        for cid, names in self.spec.bot_view.items():
            for n in names:
                self.edit(cid, self.role(n), why=f"бот пишет в канале: роль {n} сохраняет просмотр", allow_add=VIEW)

    def roles_ops(self) -> None:
        app = self.role(self.spec.apparitor)
        self.edit(
            self.spec.roles_channel,
            app,
            why="Apparitor ведёт сообщение с реакциями",
            allow_add=VIEW | SEND | HISTORY | REACT,
        )

    def board_ops(self) -> None:
        o, sp = self.opts, self.spec
        app, open_role = self.role(sp.apparitor), self.role(o.board_open_role)
        for ch in [c for c in self.snap.channels if c["type"] in (0, 5) and c["id"] != NEW]:
            src = ch["id"] in o.board_sources or (
                ch.get("parent_id") in o.board_categories and can_view(self.roles, sp.guild_id, "0", [open_role], ch)
            )
            if ch["id"] == o.board_channel:
                self.edit(ch["id"], app, why="доска: Apparitor пишет сюда", allow_add=VIEW | SEND | HISTORY)
            elif src and not can_view(self.roles, sp.guild_id, "0", [app], ch):
                self.edit(ch["id"], app, why="доска: Apparitor считает звёзды", allow_add=VIEW | HISTORY)

    def tail_ops(self) -> None:
        sp = self.spec
        if self.opts.system_channel and self.snap.guild.get("system_channel_id") == sp.old_welcome:
            self.ops.append(Op("system", "системный канал → новый #welcome", before=[sp.old_welcome], after=[NEW]))
        if self.opts.rename_old:
            old = self.snap.chan(sp.old_welcome)
            self.ops.append(
                Op(
                    "rename",
                    f"старый #welcome → #{self.opts.rename_old}",
                    sp.old_welcome,
                    before=[old["name"]],
                    after=[self.opts.rename_old],
                )
            )


def build(snap: Snap, spec: Spec, opts: Opts) -> Planner:
    """Порядок: роль бота и новый #welcome (новичкам есть куда идти) → просмотр ботам → скрытие → системный канал."""
    g = set(opts.groups)
    p = Planner(snap, spec, opts)
    if "position" in g:
        p.position_op()
    if "wall" in g:
        p.create_welcome()
    if "roles" in g:
        p.roles_ops()
    if "wall" in g:
        p.bot_ops()
        p.hide_ops()
    if "board" in g:
        p.board_ops()
    if "wall" in g:
        p.tail_ops()
    return p


def digest(ops: list[Op]) -> str:
    return hashlib.sha256(json.dumps([asdict(o) for o in ops], sort_keys=True).encode()).hexdigest()[:12]


# --- кто что видит -----------------------------------------------------------------------------------
def visibility(snap: Snap, spec: Spec) -> dict:
    """канал -> {без ролей: видит ли посторонний, люди: сколько, без ✔, с ✔, боты}."""
    roles = {r["id"]: r for r in snap.roles}
    ver = next((r["id"] for r in snap.roles if r["name"] == spec.verified), "")
    out = {}
    for ch in snap.channels:
        if ch["type"] == 4:
            continue
        row = {
            "stranger": can_view(roles, spec.guild_id, "0", [], ch),
            "people": 0,
            "unverified": 0,
            "verified": 0,
            "bots": 0,
        }
        for m in snap.members:
            if can_view(roles, spec.guild_id, m["id"], m["roles"], ch):
                if m["bot"]:
                    row["bots"] += 1
                else:
                    row["people"] += 1
                    row["verified" if ver in m["roles"] else "unverified"] += 1
        out[ch["id"]] = {"name": ch["name"], **row}
    return out


def apply_to_snap(snap: Snap, ops: list[Op], ids: dict[str, str] | None = None) -> Snap:
    """Состояние сервера после операций — для «после» в плане и для проверки после apply."""
    s = copy.deepcopy(snap)
    ids = ids or {}
    sub = lambda x: ids.get(x, x)  # noqa: E731
    for op in ops:
        if op.kind == "create":
            e = op.extra
            s.channels.append(
                {
                    "id": sub(NEW),
                    "name": e["name"],
                    "type": e["type"],
                    "parent_id": e["parent_id"],
                    "position": e["position"],
                    "permission_overwrites": copy.deepcopy(e["permission_overwrites"]),
                }
            )
        elif op.kind == "overwrite":
            set_ow(s.chan(sub(op.channel)), op.target, op.ttype, tuple(op.after) if op.after else None)
        elif op.kind == "system":
            s.guild["system_channel_id"] = sub(op.after[0])
        elif op.kind == "rename":
            s.chan(op.channel)["name"] = op.after[0]
        elif op.kind == "position":
            moved = next(r for r in s.roles if r["id"] == op.target)
            old, new = moved["position"], op.after[0]
            for r in s.roles:
                if new > old and old < r["position"] <= new:
                    r["position"] -= 1
                elif new < old and new <= r["position"] < old:
                    r["position"] += 1
            moved["position"] = new
    return s


def report(before: Snap, after: Snap, spec: Spec, out=print) -> list[str]:
    """Печатает, кто что видит до и после; возвращает предупреждения."""
    vb, va = visibility(before, spec), visibility(after, spec)
    warns = []
    out("== без ролей (зашёл и не проверен): каналы, которые он видит ==")
    for label, v in (("до", vb), ("после", va)):
        seen = [(c, r) for c, r in v.items() if r["stranger"]]
        out(f"  {label}: {len(seen)}")
        for c, r in seen:
            out(
                f"    #{r['name']} ({c}): людей {r['people']} (без ✔ {r['unverified']}, с ✔ {r['verified']}), "
                f"ботов {r['bots']}"
            )
    out("== кто потерял или получил просмотр канала (по людям) ==")
    for c in va:
        b = vb.get(c, {"people": 0, "unverified": 0, "verified": 0, "bots": 0, "name": va[c]["name"]})
        a = va[c]
        if (b["people"], b["bots"]) != (a["people"], a["bots"]) or b["verified"] != a["verified"]:
            out(
                f"    #{a['name']} ({c}): людей {b['people']} → {a['people']} "
                f"(с ✔ {b['verified']} → {a['verified']}, без ✔ {b['unverified']} → {a['unverified']}), "
                f"ботов {b['bots']} → {a['bots']}"
            )
            if c in vb and a["verified"] < b["verified"]:
                warns.append(f"проверенные теряют просмотр #{a['name']}: {b['verified']} → {a['verified']}")
    roles = {r["id"]: r for r in before.roles}
    out("== боты, которые перестанут видеть канал (просмотр им не возвращаем; добавить — в Spec.bot_view) ==")
    channels_after = {c["id"]: c for c in after.channels}
    for m in before.members:
        if not m["bot"]:
            continue
        lost = [
            "#" + c["name"]
            for c in before.channels
            if c["type"] != 4
            and can_view(roles, spec.guild_id, m["id"], m["roles"], c)
            and not can_view(roles, spec.guild_id, m["id"], m["roles"], channels_after[c["id"]])
        ]
        if lost:
            names = ", ".join(roles[r]["name"] for r in m["roles"] if r in roles and r != spec.guild_id)
            out(f"    бот {m['id']} [{names}]: {', '.join(lost)}")
    return warns


def show(p: Planner, out=print) -> None:
    n = len(p.ops)
    for i, op in enumerate(p.ops, 1):
        head = f"[{i:2}/{n}]"
        if op.kind == "overwrite":
            out(f"{head} {p.name(op.channel)} ({op.channel})  {p.tname(op.target)}: {op.text}")
            out(
                f"        {bits(op.before)}  →  {bits(op.after)}   "
                f"(allow/deny {op.before or '-'} → {op.after or 'удалить'})"
            )
        else:
            out(f"{head} {op.text}")


# --- apply / rollback ---------------------------------------------------------------------------------
def log_line(path: Path, rec: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run_apply(rest: Rest, d: Path, ops: list[Op], gid: str, tag: str) -> dict[str, str]:
    """Выполняет по одной операции и записывает каждую в changes.jsonl сразу; на первой ошибке останавливается."""
    log, ids = d / "changes.jsonl", {}
    sub = lambda x: ids.get(x, x)  # noqa: E731
    why = f"Apparitor wall {tag}"
    for i, op in enumerate(ops):
        rec = {"i": i, "op": asdict(op)}
        try:
            if op.kind == "position":
                rest.request("PATCH", f"/guilds/{gid}/roles", [{"id": op.target, "position": op.after[0]}], why)
                now = next(r["position"] for r in rest.request("GET", f"/guilds/{gid}/roles") if r["id"] == op.target)
                rec["actual"] = now
            elif op.kind == "create":
                made = rest.request("POST", f"/guilds/{gid}/channels", op.extra, why)
                ids[NEW] = made["id"]
                rec["created"] = made["id"]
            elif op.kind == "overwrite":
                url = f"/channels/{sub(op.channel)}/permissions/{op.target}"
                if op.after:
                    rest.request(
                        "PUT", url, {"allow": str(op.after[0]), "deny": str(op.after[1]), "type": op.ttype}, why
                    )
                else:
                    rest.request("DELETE", url, None, why)
            elif op.kind == "system":
                rest.request("PATCH", f"/guilds/{gid}", {"system_channel_id": sub(op.after[0])}, why)
            elif op.kind == "rename":
                rest.request("PATCH", f"/channels/{op.channel}", {"name": op.after[0]}, why)
        except RestError as e:
            log_line(log, {**rec, "status": "failed", "error": str(e)})
            raise
        log_line(log, {**rec, "status": "done"})
    return ids


def run_rollback(rest: Rest, d: Path, gid: str, out=print, yes: bool = False) -> list[str]:
    done = [json.loads(x) for x in (d / "changes.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    done = [r for r in done if r["status"] == "done"]
    ids = {NEW: r["created"] for r in done if r.get("created")}  # noqa: B035
    skipped = []
    for r in reversed(done):
        op = Op(**r["op"])
        cid = ids.get(op.channel, op.channel)
        if op.kind == "overwrite":
            ch = rest.request("GET", f"/channels/{cid}")
            cur = get_ow(ch, op.target)
            want = tuple(op.after) if op.after else None
            if cur != want:
                skipped.append(f"{cid}/{op.target}: изменено после apply ({cur} вместо {want}) — не трогаю")
                out("  пропуск: " + skipped[-1])
                continue
            out(f"  вернуть {cid}/{op.target}: {bits(cur)} → {bits(tuple(op.before) if op.before else None)}")
            if yes and op.before:
                rest.request(
                    "PUT",
                    f"/channels/{cid}/permissions/{op.target}",
                    {"allow": str(op.before[0]), "deny": str(op.before[1]), "type": op.ttype},
                    "Apparitor wall rollback",
                )
            elif yes:
                rest.request("DELETE", f"/channels/{cid}/permissions/{op.target}", None, "Apparitor wall rollback")
        elif op.kind == "create":
            made = ids[NEW]
            ch = rest.request("GET", f"/channels/{made}")
            new = calc(get_ow(ch, gid), allow_rm=VIEW, deny_add=VIEW)
            out(f"  новый канал {made} не удаляю, прячу: @everyone {bits(get_ow(ch, gid))} → {bits(new)}")
            if yes and new:
                rest.request(
                    "PUT",
                    f"/channels/{made}/permissions/{gid}",
                    {"allow": str(new[0]), "deny": str(new[1]), "type": 0},
                    "Apparitor wall rollback",
                )
        elif op.kind == "system":
            cur = rest.request("GET", f"/guilds/{gid}").get("system_channel_id")
            if cur != ids.get(NEW, op.after[0]):
                skipped.append(f"системный канал уже другой ({cur}) — не трогаю")
                out("  пропуск: " + skipped[-1])
                continue
            out(f"  системный канал {cur} → {op.before[0]}")
            if yes:
                rest.request("PATCH", f"/guilds/{gid}", {"system_channel_id": op.before[0]}, "Apparitor wall rollback")
        elif op.kind == "rename":
            cur = rest.request("GET", f"/channels/{cid}")["name"]
            if cur != op.after[0]:
                skipped.append(f"{cid}: имя уже другое ({cur}) — не трогаю")
                out("  пропуск: " + skipped[-1])
                continue
            out(f"  имя {cid}: {cur} → {op.before[0]}")
            if yes:
                rest.request("PATCH", f"/channels/{cid}", {"name": op.before[0]}, "Apparitor wall rollback")
        elif op.kind == "position":
            cur = next(x["position"] for x in rest.request("GET", f"/guilds/{gid}/roles") if x["id"] == op.target)
            if cur != r.get("actual", op.after[0]):
                skipped.append(f"роль {op.target}: позиция уже {cur} — не трогаю")
                out("  пропуск: " + skipped[-1])
                continue
            out(f"  роль {op.target}: позиция {cur} → {op.before[0]}")
            if yes:
                rest.request(
                    "PATCH",
                    f"/guilds/{gid}/roles",
                    [{"id": op.target, "position": op.before[0]}],
                    "Apparitor wall rollback",
                )
    return skipped


def verify(snap_after: Snap, ops: list[Op], ids: dict[str, str]) -> list[str]:
    """Сверка: записи, которые поставил apply, на сервере — такие, как предсказал план."""
    bad = []
    for op in ops:
        if op.kind != "overwrite":
            continue
        cid = ids.get(op.channel, op.channel)
        got = get_ow(snap_after.chan(cid), op.target)
        want = tuple(op.after) if op.after else None
        if got != want:
            bad.append(f"{cid}/{op.target}: на сервере {got}, ожидалось {want}")
    return bad


# --- командная строка ---------------------------------------------------------------------------------
def make_opts(a: argparse.Namespace, spec: Spec) -> Opts:
    o = Opts(
        position=a.apparitor_position,
        groups=tuple(g for g in a.groups.split(",") if g),
        system_channel=a.system_channel,
        rename_old=a.rename_old or "",
    )
    for g in o.groups:
        if g not in GROUPS:
            raise SystemExit(f"неизвестная группа {g}: есть {', '.join(GROUPS)}")
    if "board" in o.groups:
        from .config import load_config

        g = load_config(a.config).for_guild(int(spec.guild_id))
        o.board_channel, o.board_open_role = str(g.board_channel_id), g.board_open_role
        o.board_categories, o.board_sources = tuple(map(str, g.board_categories)), tuple(map(str, g.board_sources))
    return o


def cmd_plan(a, d: Path, out=print) -> Planner:
    spec = Spec()
    snap = load(Path(a.__dict__.get("from_dir") or d / "before"))
    p = build(snap, spec, make_opts(a, spec))
    show(p, out)
    after = apply_to_snap(snap, p.ops)
    warns = report(snap, after, spec, out)
    for w in warns:
        out("!! " + w)
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(
        json.dumps(
            {"digest": digest(p.ops), "spec": asdict(spec), "opts": asdict(p.opts), "ops": [asdict(o) for o in p.ops]},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    out(f"операций: {len(p.ops)}; отпечаток плана {digest(p.ops)}; файл {d / 'plan.json'}")
    return p


def welcome_snippet(spec: Spec, new_id: str) -> str:
    """Правка конфига: приветствие Apparitor в новом #welcome (Carl и WikiAuthBot2 пишут в старый до переключения)."""
    return (
        "Правка config.toolforge.toml (сам файл скрипт не меняет), затем влить и выкатить:\n"
        f"  [guild.{spec.guild_id}]\n"
        f"  welcome_channel_id = {new_id}\n"
        f"  [guild.{spec.guild_id}.features]\n"
        '  welcome = "live"'
    )


def cmd_apply(a, d: Path, out=print) -> int:
    saved = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    spec, opts = Spec(**{k: tuple(v) if isinstance(v, list) else v for k, v in saved["spec"].items()}), saved["opts"]
    opts = Opts(**{k: tuple(v) if isinstance(v, list) else v for k, v in opts.items()})
    rest = Rest(read_token(a.token_file, a.bot))
    readers = Rest(read_token(a.read_token_file, a.read_bot)) if a.read_token_file else None
    snap = fetch(rest, spec.guild_id, readers)
    save(snap, d / "before-apply")
    p = build(snap, spec, opts)
    if digest(p.ops) != saved["digest"]:
        out(
            f"сервер изменился с момента плана: отпечаток {digest(p.ops)} вместо {saved['digest']}. "
            "Сделайте snapshot и plan заново, проверьте."
        )
        show(p, out)
        return 2
    show(p, out)
    if not a.yes:
        out("Это только печать. Выполнить: добавьте --yes.")
        return 0
    ids = {}
    try:
        ids = run_apply(rest, d, p.ops, spec.guild_id, saved["digest"])
    except RestError as e:
        out(f"ОСТАНОВЛЕНО на ошибке: {e}. Сделанное записано в {d / 'changes.jsonl'}; вернуть — команда rollback.")
        return 1
    after = fetch(rest, spec.guild_id, readers)
    save(after, d / "after-apply")
    bad = verify(after, p.ops, ids)
    report(snap, after, spec, out)
    out("проверка: все записи совпали с планом" if not bad else "РАСХОЖДЕНИЯ:\n  " + "\n  ".join(bad))
    if a.welcome_live and not bad and NEW in ids:
        out(welcome_snippet(spec, ids[NEW]))
    return 0 if not bad else 1


def cmd_rollback(a, d: Path, out=print) -> int:
    saved = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    rest = Rest(read_token(a.token_file, a.bot))
    skipped = run_rollback(rest, d, saved["spec"]["guild_id"], out, a.yes)
    out("Это только печать. Выполнить: добавьте --yes." if not a.yes else f"откат выполнен; пропущено {len(skipped)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m apparitor.wall", description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("snapshot", "plan", "apply", "rollback"))
    ap.add_argument("--dir", required=True, help="рабочая папка: снимки, plan.json, changes.jsonl (не в репозитории)")
    ap.add_argument("--from", dest="from_dir", help="plan: взять готовый снимок из этой папки вместо DIR/before")
    ap.add_argument("--token-file")
    ap.add_argument("--bot", action="store_true", help="токен бота: добавить «Bot »")
    ap.add_argument(
        "--read-token-file", help="snapshot, apply: токен бота для списка участников (у токена человека его нет)"
    )
    ap.add_argument("--read-bot", action="store_true")
    ap.add_argument("--guild", default=Spec.guild_id)
    ap.add_argument("--config", default="config.toolforge.toml")
    ap.add_argument("--groups", default="wall,roles,position", help=f"из: {', '.join(GROUPS)}")
    ap.add_argument("--apparitor-position", type=int, default=49)
    ap.add_argument(
        "--system-channel", action="store_true", help="перенести системный канал в новый #welcome (по умолчанию нет)"
    )
    ap.add_argument("--rename-old", help="переименовать старый #welcome (по умолчанию имя не трогаем)")
    ap.add_argument(
        "--welcome-live", action="store_true", help="apply: после успеха напечатать правку конфига приветствия"
    )
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    d = Path(a.dir).expanduser()
    if a.cmd == "snapshot":
        readers = Rest(read_token(a.read_token_file, a.read_bot)) if a.read_token_file else None
        save(fetch(Rest(read_token(a.token_file, a.bot)), a.guild, readers), d / "before")
        print(f"снимок сохранён: {d / 'before'}")
        return 0
    if a.cmd == "plan":
        cmd_plan(a, d)
        return 0
    return (cmd_apply if a.cmd == "apply" else cmd_rollback)(a, d)


if __name__ == "__main__":
    sys.exit(main())
