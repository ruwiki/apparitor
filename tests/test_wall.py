"""Стена прав: план, apply, rollback на синтетическом мини-сервере (id выдуманные, настоящих участников нет)."""

import argparse
import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from apparitor import wall
from apparitor.wall import HISTORY, NEW, REACT, SEND, VIEW, Opts, RestError, Spec, apply_to_snap, build, calc

G = "g"  # @everyone и сервер
APPCMD = 1 << 31
BASE = VIEW | SEND | HISTORY | APPCMD  # то, что у @everyone на сервере вообще
OLD, README, ANN, HUB, ROLES, GEN, CAT, MEMES = "oldw", "readme", "ann", "hub", "rolesch", "gen", "cat", "memes"
SPEC = Spec(
    guild_id=G, category=CAT, old_welcome=OLD, roles_channel=ROLES, bot_view={OLD: ("carl-bot", "WikiAuthBot2")}
)
# вариант с закрытыми каналами: по умолчанию их нет (хозяин закрывать readme/announcements/server-hub не просил)
SPEC_CLOSED = Spec(
    **{
        **asdict(SPEC),
        "closed": (README, ANN, HUB),
        "bot_view": {**SPEC.bot_view, README: ("carl-bot",), ANN: ("wiki bot",)},
    }
)


def role(i, name, pos, perms=0, managed=False, bot=None):
    return {
        "id": i,
        "name": name,
        "position": pos,
        "permissions": str(perms),
        "managed": managed,
        "tags": {"bot_id": bot} if bot else None,
    }


def ow(tid, allow=0, deny=0, ttype=0):
    return {"id": tid, "type": ttype, "allow": str(allow), "deny": str(deny)}


def chan(cid, name, ctype=0, parent=CAT, pos=0, ows=()):
    return {
        "id": cid,
        "name": name,
        "type": ctype,
        "parent_id": parent,
        "position": pos,
        "permission_overwrites": list(ows),
    }


def world() -> wall.Snap:
    roles = [
        role(G, "@everyone", 0, BASE),
        role("apparitor", "Apparitor", 1, managed=True, bot="b-app"),
        role("rq", "#requests-fast", 2),
        role("wikibot", "wiki bot", 3),
        role("ver", "✔", 4),
        role("new", "🆕 new user", 5),
        role("ro", "✔ read-only 🔇", 6),
        role("mod", "Модератор чата", 7, VIEW),
        role("tech", "Техмодерка", 8, VIEW),
        role("carl", "carl-bot", 9, VIEW, managed=True, bot="b-carl"),
        role("wab", "WikiAuthBot2", 10, managed=True, bot="b-wab"),
    ]
    channels = [
        chan(CAT, "Добро пожаловать!", 4, None),
        chan(
            OLD,
            "welcome",
            pos=8,
            ows=[
                ow(G, APPCMD, 1 | (1 << 35)),
                ow("new", VIEW | SEND | APPCMD),
                ow("ro", 0, SEND),
                ow("u9", 0, SEND, 1),
                ow("mod", 1 << 15 | REACT),
                ow("carl", REACT),
            ],
        ),
        chan(README, "readme", pos=6, ows=[ow(G, 0, SEND | REACT | (1 << 13)), ow("carl", SEND | REACT)]),
        chan(ANN, "announcements", pos=5, ows=[ow(G, 0, SEND | 1), ow("mod", SEND)]),
        chan(HUB, "server-hub", 20, None),
        chan(ROLES, "roles", pos=7, ows=[ow(G, 0, VIEW), ow("ver", VIEW | HISTORY)]),
        chan(GEN, "general", 0, "open", ows=[ow(G, 0, VIEW), ow("ver", VIEW)]),
        chan(MEMES, "memes", 0, "open", ows=[ow(G, 0, VIEW), ow("ver", VIEW)]),
    ]
    members = [
        {"id": "s1", "bot": False, "roles": ["new"]},
        {"id": "s2", "bot": False, "roles": []},
        {"id": "v1", "bot": False, "roles": ["ver"]},
        {"id": "v2", "bot": False, "roles": ["ver", "new"]},  # проверен, но 🆕 не снята
        {"id": "m1", "bot": False, "roles": ["ver", "mod"]},
        {"id": "b-app", "bot": True, "roles": ["apparitor", "new"]},
        {"id": "b-carl", "bot": True, "roles": ["carl"]},
        {"id": "b-wab", "bot": True, "roles": ["wab"]},
        {"id": "b-wiki", "bot": True, "roles": ["wikibot"]},  # роль «wiki bot», без ✔
        {"id": "b-texit", "bot": True, "roles": []},
    ]
    guild = {"id": G, "system_channel_id": OLD}
    return wall.Snap(guild, channels, roles, members)


class FakeRest:
    """Сервер в памяти: правила записи как у Discord, нужные скрипту; fail — каналы, где запись даёт 400."""

    def __init__(self, snap: wall.Snap, fail=(), me="op"):
        self.s, self.fail, self.calls, self.n, self.me = copy.deepcopy(snap), set(fail), [], 0, me

    def request(self, method, path, body=None, reason=""):
        self.calls.append((method, path, body))
        parts = path.split("?")[0].strip("/").split("/")
        s = self.s
        if method == "GET":
            if parts == ["users", "@me"]:
                return {"id": self.me}
            if parts[0] == "guilds" and len(parts) == 2:
                return copy.deepcopy(s.guild)
            if parts[-1] == "channels":
                return copy.deepcopy(s.channels)
            if parts[-1] == "roles":
                return copy.deepcopy(s.roles)
            if parts[-1] == "members":
                return [{"user": {"id": m["id"], "bot": m["bot"]}, "roles": m["roles"]} for m in s.members]
            return copy.deepcopy(s.chan(parts[1]))
        if parts[0] == "channels" and parts[2:3] == ["permissions"]:
            if parts[1] in self.fail:
                raise RestError(400, "не поддерживается")
            ch = s.chan(parts[1])
            wall.set_ow(
                ch, parts[3], (body or {}).get("type", 0), (int(body["allow"]), int(body["deny"])) if body else None
            )
        elif method == "POST":
            self.n += 1
            made = {
                "id": f"new{self.n}",
                "name": body["name"],
                "type": body["type"],
                "parent_id": body["parent_id"],
                "position": body["position"],
                "permission_overwrites": copy.deepcopy(body["permission_overwrites"]),
            }
            s.channels.append(made)
            return copy.deepcopy(made)
        elif parts[-1] == "roles":  # PATCH позиций
            for item in body:
                snap = apply_to_snap(s, [wall.Op("position", "", target=item["id"], after=[item["position"]])])
                s.roles = snap.roles
            return copy.deepcopy(s.roles)
        elif parts[0] == "guilds":
            s.guild.update(body)
        else:  # PATCH канала
            s.chan(parts[1]).update(body)
        return None


def ow_of(snap, cid):
    return {o["id"]: (int(o["allow"]), int(o["deny"])) for o in snap.chan(cid)["permission_overwrites"]}


def test_calc_is_minimal_bits():
    assert calc((0, SEND | REACT), deny_add=VIEW) == (0, SEND | REACT | VIEW)  # запреты на месте
    assert calc((VIEW, 0), allow_rm=VIEW) is None  # пустая запись исчезает
    assert calc((0, VIEW), allow_add=VIEW) == (VIEW, 0)  # разрешение вычёркивает тот же бит из запрета
    assert calc(None, allow_add=VIEW | SEND) == (VIEW | SEND, 0)


def test_plan_touches_only_needed_bits():
    snap = world()
    p = build(snap, SPEC_CLOSED, Opts(position=3))
    after = apply_to_snap(snap, p.ops)
    # #readme: запреты на отправку, реакции и управление сообщениями остались, добавился запрет просмотра
    assert ow_of(after, README)[G] == (0, SEND | REACT | (1 << 13) | VIEW)
    assert ow_of(after, README)["carl"] == (SEND | REACT | VIEW, 0)  # бот пишет в канале — просмотр на его роль
    assert ow_of(after, README)["ver"] == (VIEW, 0) and ow_of(after, README)["apparitor"] == (VIEW, 0)
    # старый #welcome: запреты на участника и роль «✔ read-only 🔇» пережили скрипт; 🆕 потеряла только просмотр
    old = ow_of(after, OLD)
    assert old["u9"] == (0, SEND) and old["ro"] == (0, SEND)
    assert old["new"] == (SEND | APPCMD, 0)
    assert old[G][1] & VIEW and old["ver"] == (VIEW | HISTORY, SEND) and old["mod"][0] & VIEW
    # #server-hub: записей не было — появились три
    assert ow_of(after, HUB) == {G: (0, VIEW), "ver": (VIEW, 0), "apparitor": (VIEW, 0)}
    # #roles: Apparitor может ставить реакции; запрет @everyone и разрешение ✔ нетронуты
    assert ow_of(after, ROLES)["apparitor"] == (VIEW | SEND | HISTORY | REACT, 0)
    assert ow_of(after, ROLES)[G] == (0, VIEW) and ow_of(after, ROLES)["ver"] == (VIEW | HISTORY, 0)
    # чужие каналы не тронуты
    assert ow_of(after, GEN) == ow_of(snap, GEN) and after.chan(MEMES) == snap.chan(MEMES)


def test_new_welcome_is_clone_without_new_role_view():
    snap = world()
    p = build(snap, SPEC, Opts(position=3))
    create = next(o for o in p.ops if o.kind == "create")
    got = {o["id"]: (int(o["allow"]), int(o["deny"])) for o in create.extra["permission_overwrites"]}
    assert got[G] == (APPCMD | VIEW | SEND | HISTORY, 1 | (1 << 35))  # прежние запреты на приглашения и ветки остались
    assert got["ver"] == (0, VIEW) and got["mod"][0] & VIEW and got["tech"] == (VIEW, 0)
    assert got["new"] == (SEND | APPCMD, 0)  # без VIEW: иначе проверенные с неснятой 🆕 остались бы в списке канала
    assert got["u9"] == (0, SEND) and got["ro"] == (0, SEND)  # запреты на участников и роль перенесены
    for bot in ("carl", "wab", "apparitor"):
        assert got[bot][0] & (VIEW | SEND) == VIEW | SEND
    assert create.extra["parent_id"] == CAT and create.extra["position"] == 8


def test_who_sees_what_before_and_after():
    snap = world()
    p = build(snap, SPEC, Opts(position=3))
    after = apply_to_snap(snap, p.ops)
    before_v, after_v = wall.visibility(snap, SPEC), wall.visibility(after, SPEC)
    assert {c for c, r in before_v.items() if r["stranger"]} == {OLD, README, ANN, HUB}
    assert {c for c, r in after_v.items() if r["stranger"]} == {README, ANN, HUB, NEW}  # старый #welcome скрыт
    new = after_v[NEW]
    assert (new["unverified"], new["verified"], new["people"]) == (2, 1, 3)  # s1, s2 и модератор; v1, v2 — нет
    lines = []
    warns = wall.report(snap, after, SPEC, lines.append)
    assert warns == [] and any("b-texit" in x and "#welcome" in x for x in lines)  # бот без роли теряет старый #welcome


def test_closed_channels_are_off_by_default_and_hide_from_strangers_when_asked():
    """Хозяин закрывать readme, announcements и server-hub не просил: план их не трогает (закрытие — Spec.closed)."""
    snap = world()
    default = build(snap, SPEC, Opts(position=3))
    assert not any(o.channel in (README, ANN, HUB) for o in default.ops)
    assert Spec().closed == () and set(Spec().bot_view) == {Spec().old_welcome}
    after = apply_to_snap(snap, build(snap, SPEC_CLOSED, Opts(position=3)).ops)
    assert {c for c, r in wall.visibility(after, SPEC_CLOSED).items() if r["stranger"]} == {NEW}


def test_bots_with_a_role_keep_the_channels_they_write_in():
    snap = world()
    after = apply_to_snap(snap, build(snap, SPEC_CLOSED, Opts(position=3)).ops)
    v = wall.visibility(after, SPEC_CLOSED)
    roles = {r["id"]: r for r in after.roles}
    members = {m["id"]: m for m in after.members}

    def sees(bot, cid):
        return wall.can_view(roles, G, bot, members[bot]["roles"], after.chan(cid))

    assert sees("b-carl", README) and sees("b-carl", OLD) and sees("b-wab", OLD) and sees("b-wiki", ANN)
    assert not sees("b-texit", README) and not sees("b-wab", README)
    assert v[README]["bots"] == 2  # carl-bot и Apparitor


def test_role_moves_and_system_channel():
    snap = world()
    p = build(snap, SPEC, Opts(position=3, system_channel=True))
    after = apply_to_snap(snap, p.ops)
    pos = {r["name"]: r["position"] for r in after.roles}
    assert (
        pos["Apparitor"] == 3 and pos["#requests-fast"] == 1 and pos["wiki bot"] == 2
    )  # ниже поднятой роли сдвинулись вниз
    assert after.guild["system_channel_id"] == NEW
    assert [o.kind for o in p.ops][:2] == ["position", "create"]  # сначала роль и новый #welcome, потом скрытие
    assert not any(o.kind == "system" for o in build(snap, SPEC, Opts(position=3)).ops)  # по умолчанию канал не трогаем
    other = world()
    other.guild["system_channel_id"] = "else"  # системный канал уже не старый #welcome — не трогаем и с флагом
    assert not any(o.kind == "system" for o in build(other, SPEC, Opts(position=3, system_channel=True)).ops)


def test_apply_then_verify_then_rollback_restores(tmp_path):
    snap = world()
    fake = FakeRest(snap)
    p = build(snap, SPEC, Opts(position=3, system_channel=True))
    ids = wall.run_apply(fake, tmp_path, p.ops, G, "t")
    assert ids[NEW] == "new1"
    now = wall.fetch(fake, G)
    assert wall.verify(now, p.ops, ids) == []  # сервер совпал с предсказанием плана
    assert now.guild["system_channel_id"] == "new1"
    log = [json.loads(x) for x in (tmp_path / "changes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(log) == len(p.ops) and all(r["status"] == "done" for r in log)
    skipped = wall.run_rollback(fake, tmp_path, G, lambda *_: None, yes=True)
    assert skipped == []
    back = wall.fetch(fake, G)
    for cid in (OLD, README, ANN, HUB, ROLES):  # снимок затронутых каналов восстановлен
        assert ow_of(back, cid) == ow_of(snap, cid), cid
    assert back.guild["system_channel_id"] == OLD
    assert {r["name"]: r["position"] for r in back.roles} == {r["name"]: r["position"] for r in snap.roles}
    # новый канал не удалён, а спрятан
    hidden = back.chan("new1")
    assert ow_of(back, "new1")[G][1] & VIEW and not ow_of(back, "new1")[G][0] & VIEW
    assert hidden["name"] == "lobby"


def test_rollback_leaves_changes_made_by_others(tmp_path):
    snap = world()
    fake = FakeRest(snap)
    p = build(snap, SPEC_CLOSED, Opts(position=3))
    wall.run_apply(fake, tmp_path, p.ops, G, "t")
    wall.set_ow(fake.s.chan(README), "ver", 0, (VIEW | SEND, 0))  # модератор дал ✔ право писать в #readme
    wall.set_ow(fake.s.chan(ANN), "extra", 0, (SEND, 0))  # и добавил свою запись в #announcements
    lines = []
    skipped = wall.run_rollback(fake, tmp_path, G, lines.append, yes=True)
    assert len(skipped) == 1 and "изменено после apply" in skipped[0]
    back = wall.fetch(fake, G)
    assert ow_of(back, README)["ver"] == (VIEW | SEND, 0)  # правка модератора не затёрта
    assert ow_of(back, ANN)["extra"] == (SEND, 0)  # чужая запись, которой нет в плане, цела
    assert ow_of(back, README)[G] == ow_of(snap, README)[G]  # а наши записи в том же канале откатились


def test_apply_stops_on_error_and_rollback_undoes_done_part(tmp_path):
    snap = world()
    fake = FakeRest(snap, fail={HUB})  # #server-hub (тип 20) не принимает права
    p = build(snap, SPEC_CLOSED, Opts(position=3, system_channel=True))
    with pytest.raises(RestError):
        wall.run_apply(fake, tmp_path, p.ops, G, "t")
    log = [json.loads(x) for x in (tmp_path / "changes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[-1]["status"] == "failed" and "server-hub" not in json.dumps(log[:-1], ensure_ascii=False)
    assert fake.s.guild["system_channel_id"] == OLD  # системный канал в конце плана — до него не дошли
    wall.run_rollback(fake, tmp_path, G, lambda *_: None, yes=True)
    back = wall.fetch(fake, G)
    for cid in (OLD, README, ANN, ROLES):
        assert ow_of(back, cid) == ow_of(snap, cid), cid


def test_rollback_without_yes_changes_nothing(tmp_path):
    snap = world()
    fake = FakeRest(snap)
    p = build(snap, SPEC, Opts(position=3))
    wall.run_apply(fake, tmp_path, p.ops, G, "t")
    fake.calls.clear()
    wall.run_rollback(fake, tmp_path, G, lambda *_: None, yes=False)
    assert all(c[0] == "GET" for c in fake.calls)


def test_digest_ignores_overwrite_order_from_api():
    """Discord возвращает перекрытия канала в разном порядке: тот же сервер должен давать тот же отпечаток."""
    a, b = world(), world()
    for ch in b.channels:
        ch["permission_overwrites"].reverse()
    assert wall.digest(build(a, SPEC, Opts()).ops) == wall.digest(build(b, SPEC, Opts()).ops)


def test_plan_changes_when_server_changes():
    """apply сверяет отпечаток плана со свежим снимком: правка модератора между plan и apply останавливает запуск."""
    a, b = world(), world()
    wall.set_ow(b.chan(README), "ver", 0, (VIEW, 0))
    assert wall.digest(build(a, SPEC_CLOSED, Opts()).ops) != wall.digest(build(b, SPEC_CLOSED, Opts()).ops)
    assert wall.digest(build(a, SPEC_CLOSED, Opts()).ops) == wall.digest(build(world(), SPEC_CLOSED, Opts()).ops)


def test_cmd_apply_prints_only_without_yes_and_refuses_changed_server(tmp_path, monkeypatch):
    snap = world()
    fake = FakeRest(snap)
    monkeypatch.setattr(wall, "Rest", lambda token: fake)
    monkeypatch.setattr(wall, "read_token", lambda path, bot: "t")
    monkeypatch.setattr(wall, "Spec", lambda **kw: Spec(**{**kw, **{"guild_id": G}}))
    p = build(snap, SPEC_CLOSED, Opts(position=3))
    plan = {"digest": wall.digest(p.ops), "spec": wall.asdict(SPEC_CLOSED), "opts": wall.asdict(p.opts)}
    (tmp_path / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    args = argparse.Namespace(
        token_file="x", bot=True, read_token_file=None, read_bot=False, yes=False, welcome_live=False
    )
    out = []
    assert wall.cmd_apply(args, tmp_path, out.append) == 0
    assert all(c[0] == "GET" for c in fake.calls) and "Это только печать" in out[-1]
    wall.set_ow(fake.s.chan(README), "ver", 0, (VIEW, 0))  # сервер изменился после plan
    args.yes = True
    out.clear()
    assert wall.cmd_apply(args, tmp_path, out.append) == 2 and "сервер изменился" in out[0]
    assert not (tmp_path / "changes.jsonl").exists()


def test_board_group_gives_apparitor_only_what_counting_needs():
    snap = world()
    snap.roles.append(role("all", "all", 11))
    wall.set_ow(snap.chan(GEN), "all", 0, (VIEW, 0))  # #general открыт для «all», #memes (доска) — нет роли «all»
    opts = Opts(groups=("board",), board_channel=MEMES, board_categories=("open",), board_open_role="all")
    after = apply_to_snap(snap, build(snap, SPEC, opts).ops)
    assert ow_of(after, GEN)["apparitor"] == (VIEW | HISTORY, 0)  # источник: смотреть и читать историю
    assert ow_of(after, MEMES)["apparitor"] == (VIEW | SEND | HISTORY, 0)  # доска: ещё и писать
    assert "apparitor" not in ow_of(after, README)  # чужое не трогаем


def test_missing_role_is_a_plan_error():
    snap = world()
    snap.roles = [r for r in snap.roles if r["name"] != "Техмодерка"]
    with pytest.raises(wall.PlanError, match="Техмодерка"):
        build(snap, SPEC, Opts())


def test_snapshot_roundtrip_keeps_only_ids_and_roles(tmp_path: Path):
    snap = world()
    snap.members[0]["name"] = "не должно сохраниться"
    wall.save(snap, tmp_path)
    back = wall.load(tmp_path)
    assert back.members[0] == {"id": "s1", "bot": False, "roles": ["new"]}
    assert "не должно" not in (tmp_path / "members.jsonl").read_text(encoding="utf-8")


def test_cmd_apply_yes_prints_welcome_config_only_with_flag(tmp_path, monkeypatch):
    """--welcome-live: после успешного apply — правка конфига с id нового канала; сам конфиг скрипт не трогает."""
    snap = world()
    fake = FakeRest(snap)
    monkeypatch.setattr(wall, "Rest", lambda token: fake)
    monkeypatch.setattr(wall, "read_token", lambda path, bot: "t")
    p = build(snap, SPEC, Opts(position=3))
    plan = {"digest": wall.digest(p.ops), "spec": wall.asdict(SPEC), "opts": wall.asdict(p.opts)}
    (tmp_path / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    args = argparse.Namespace(
        token_file="x", bot=True, read_token_file=None, read_bot=False, yes=True, welcome_live=False
    )
    out = []
    assert wall.cmd_apply(args, tmp_path, out.append) == 0
    assert "проверка: все записи совпали с планом" in out and not any("welcome_channel_id" in x for x in out)
    fake2 = FakeRest(snap)
    monkeypatch.setattr(wall, "Rest", lambda token: fake2)
    (tmp_path / "changes.jsonl").unlink()
    args.welcome_live, out = True, []
    assert wall.cmd_apply(args, tmp_path, out.append) == 0
    text = "\n".join(out)
    assert "welcome_channel_id = new1" in text and 'welcome = "live"' in text and f"[guild.{G}.features]" in text


def test_default_plan_names_the_channel_lobby_and_keeps_old_name():
    """Решение хозяина: новый канал lobby; старый #welcome не переименовываем (rename — только по флагу)."""
    snap = world()
    p = build(snap, SPEC, Opts(position=3))
    create = next(o for o in p.ops if o.kind == "create")
    assert create.extra["name"] == "lobby" and not any(o.kind == "rename" for o in p.ops)
    after = apply_to_snap(snap, p.ops)
    assert after.chan(OLD)["name"] == "welcome" and after.chan(NEW)["name"] == "lobby"
    assert any(o.kind == "rename" for o in build(snap, SPEC, Opts(position=3, rename_old="welcome-archive")).ops)


# --- оператор: человек с токеном apply тоже должен видеть канал, пока его правит ---------------------------
def with_operator(roles: list[str]) -> wall.Snap:
    snap = world()
    snap.roles.append(
        role("opr", "оператор", 11, (1 << 4) | (1 << 28))
    )  # Manage Channels, Manage Roles; просмотр — как у всех
    snap.roles.append(role("adm", "админ", 12, wall.ADMIN))
    snap.members.append({"id": "op", "bot": False, "roles": roles})
    snap.operator = "op"
    return snap


def channel_ops(p, cid):
    return [o for o in p.ops if o.kind == "overwrite" and o.channel == cid]


def test_everyone_deny_is_the_last_write_in_each_hidden_channel():
    """Был баг: запрет @everyone шёл раньше разрешений, и оператор без ✔/модератора терял канал до своих же правок."""
    p = build(world(), SPEC_CLOSED, Opts(position=3))
    for cid in (OLD, README, ANN, HUB):
        ops = channel_ops(p, cid)
        hide = [i for i, o in enumerate(ops) if o.target == G and o.after and o.after[1] & VIEW]
        assert hide == [len(ops) - 1], cid
        assert any(o.target == "ver" for o in ops[:-1])  # ✔ получил просмотр раньше


def test_plan_from_the_fixed_order_gives_operator_no_403_warning():
    snap = with_operator(["opr"])  # видит каналы только через @everyone
    p = build(snap, SPEC_CLOSED, Opts(position=3))
    warns = wall.operator_warnings(snap, p.ops, SPEC_CLOSED)
    assert not any("403" in w for w in warns)
    assert len([w for w in warns if "не видит" in w]) == 4  # старый #welcome, readme, announcements, server-hub
    snap = with_operator(["opr"])  # стена по умолчанию: закрыт только старый #welcome
    warns = wall.operator_warnings(snap, build(snap, SPEC, Opts(position=3)).ops, SPEC)
    assert len(warns) == 1 and "не видит #welcome" in warns[0]


def test_operator_warning_when_view_is_lost_before_later_steps_in_the_same_channel():
    snap = with_operator(["opr"])
    ops = list(build(snap, SPEC, Opts(position=3)).ops)
    hide = next(i for i, o in enumerate(ops) if o.channel == OLD and o.target == G)
    first = next(i for i, o in enumerate(ops) if o.channel == OLD and o.target == "ver")
    ops.insert(first, ops.pop(hide))  # прежний порядок: запрет @everyone раньше разрешений
    warns = wall.operator_warnings(snap, ops, SPEC)
    bad = [w for w in warns if "403" in w]
    assert len(bad) == 1 and "#welcome" in bad[0] and f"шаге {first + 1}" in bad[0] and "оператор op" in bad[0]


def test_operator_with_admin_or_verified_role_gets_no_warning_where_view_stays():
    snap = with_operator(["adm"])
    assert wall.operator_warnings(snap, build(snap, SPEC, Opts(position=3)).ops, SPEC) == []
    snap = with_operator(["ver"])  # ✔ видит старый и закрытые каналы и после плана
    assert wall.operator_warnings(snap, build(snap, SPEC, Opts(position=3)).ops, SPEC) == []


def test_unknown_operator_is_a_warning_not_silence():
    snap = world()
    p = build(snap, SPEC, Opts(position=3))
    assert "не найден" in wall.operator_warnings(snap, p.ops, SPEC)[0]
    snap.operator = "ghost"
    assert "не найден" in wall.operator_warnings(snap, p.ops, SPEC)[0]


def test_snapshot_keeps_operator_and_old_snapshots_load_without_it(tmp_path: Path):
    snap = with_operator(["opr"])
    wall.save(snap, tmp_path)
    assert wall.load(tmp_path).operator == "op"
    (tmp_path / "operator.json").unlink()
    assert wall.load(tmp_path).operator == ""


def test_plan_and_apply_rehearsal_print_operator_warnings(tmp_path, monkeypatch):
    snap = with_operator(["opr"])
    monkeypatch.setattr(wall, "Spec", lambda **kw: Spec(**{**kw, **{"guild_id": G}}) if kw else SPEC)
    monkeypatch.setattr(wall, "read_token", lambda path, bot: "t")
    wall.save(snap, tmp_path / "snap")
    a = argparse.Namespace(
        from_dir=str(tmp_path / "snap"),
        apparitor_position=3,
        groups="wall,roles,position",
        system_channel=False,
        rename_old=None,
    )
    out = []
    wall.cmd_plan(a, tmp_path / "run", out.append)
    assert any(x.startswith("!! оператор op после шага") for x in out)
    fake = FakeRest(snap, me="op")
    monkeypatch.setattr(wall, "Rest", lambda token: fake)
    args = argparse.Namespace(
        token_file="x", bot=True, read_token_file=None, read_bot=False, yes=False, welcome_live=False
    )
    out.clear()
    assert wall.cmd_apply(args, tmp_path / "run", out.append) == 0
    assert any(x.startswith("!! оператор op после шага") for x in out) and "Это только печать" in out[-1]
    assert wall.load(tmp_path / "run" / "before-apply").operator == "op"  # владелец токена взят из GET /users/@me
