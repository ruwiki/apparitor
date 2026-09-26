"""Чтение рувики и глобальных групп через API. Только чтение, без аккаунта."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time

import aiohttp

log = logging.getLogger("apparitor")

API = "https://ru.wikipedia.org/w/api.php"
UA = "Apparitor/0.1 (https://ru.wikipedia.org/wiki/User:Carn; access bot for ruwiki ArbCom Discord)"

# группа (локальная рувики или глобальная) -> человекочитаемая метка
LABELS = {
    "sysop": "админ",
    "bureaucrat": "бюрократ",
    "checkuser": "чекюзер",
    "suppress": "ревизор",
    "arbcom": "арбитр",
    "closer": "подводящий итоги",
    "editor": "патрулирующий",
    "autoreview": "автопатрулируемый",
    "rollbacker": "откатывающий",
    "engineer": "инженер",
    "bot": "бот",
    "interface-admin": "админ интерфейса",
    "steward": "стюард",
    "global-sysop": "глобальный админ",
    "global-interface-editor": "глобальный редактор интерфейса",
    "closer-plus": "полномочный ПИ",
    "vandalfighter": "борец с вандализмом",
    "clerk": "клерк",
    "techdeleter": "технический удаляющий",
    "vrts": "VRTS",
    "wmf": "сотрудник WMF",
}
# Сотрудники Фонда: глобальные группы wmf-* / staff / sysadmin либо учётка «… (WMF)» (у многих групп нет).
WMF_GROUPS = {"staff", "sysadmin"}
# Статусы без технической группы (ПИ+, борцы с вандализмом, клерки, ТУ, VRTS) — из JSON гаджета markadmins,
# его обновляет MBHbot раз в несколько дней: ключ гаджета -> псевдогруппа. arbcom берём и оттуда:
# в группе рувики нет арбитров с флагом админа, в JSON — весь действующий состав.
STATUS_PAGE = "MediaWiki:Gadget-markadmins.json"
STATUS_GROUPS = {
    "I+": "closer-plus",
    "V": "vandalfighter",
    "K": "clerk",
    "D": "techdeleter",
    "T": "vrts",
    "Ar": "arbcom",
}
STATUS_TTL = 6 * 3600
_status_cache: tuple[float, dict[str, set[str]]] = (0.0, {})
HIDDEN = {
    "*",
    "user",
    "autoconfirmed",
    "temporary-account-viewer",
    "uploader",
    "filemover",
    "ipblock-exempt",
    "suppressredirect",
    "confirmed",
}
GLOBAL_KEEP = {"steward", "global-sysop", "global-interface-editor", "founder", "ombuds", "wmf"}
BAD_CHARS = set("|#<>[]{}")


def norm_name(name: str) -> str:
    """Как MediaWiki: пробелы вместо подчёркиваний, первая буква заглавная. API возвращает имя уже в таком виде,
    а ник в Discord может быть «bezik» — сопоставляем по нормализованному."""
    name = name.strip().replace("_", " ")
    return name[:1].upper() + name[1:]


def valid_name(name: str) -> bool:
    return bool(name.strip()) and not (set(name) & BAD_CHARS) and len(name) <= 255


async def _get(session: aiohttp.ClientSession, **params) -> dict:
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    for attempt in range(4):
        async with session.get(API, params=params, headers={"User-Agent": UA}) as r:
            if r.status == 429:
                await asyncio.sleep(2**attempt)
                continue
            r.raise_for_status()
            return await r.json()
    raise RuntimeError("ruwiki API: 429 после 4 попыток")


def parse_statuses(content: str) -> dict[str, set[str]]:
    """JSON гаджета -> имя -> псевдогруппы (только ключи из STATUS_GROUPS)."""
    user_set = json.loads(content).get("userSet", {})
    out: dict[str, set[str]] = {}
    for key, group in STATUS_GROUPS.items():
        for name in user_set.get(key, []):
            out.setdefault(name, set()).add(group)
    return out


async def statuses(session: aiohttp.ClientSession) -> dict[str, set[str]]:
    """Статусы по JSON гаджета, кэш на STATUS_TTL. При сбое — прежний кэш (или пусто) и предупреждение в лог."""
    global _status_cache
    ts, cached = _status_cache
    if cached and time.monotonic() - ts < STATUS_TTL:
        return cached
    try:
        d = await _get(session, action="query", prop="revisions", rvprop="content", rvslots="main", titles=STATUS_PAGE)
        out = parse_statuses(d["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"])
    except Exception as e:  # любой сбой чтения: флаги из групп важнее, статусы подождут
        log.warning("%s не прочитан (%s); статусы %s", STATUS_PAGE, e, "из кэша" if cached else "не учитываются")
        return cached
    _status_cache = (time.monotonic(), out)
    return out


def _pack(u: dict, global_groups: list[str], status_groups: set[str] = frozenset(), locked: bool = False) -> dict:
    groups = [g for g in u.get("groups", []) if g not in HIDDEN]
    groups += [g for g in STATUS_GROUPS.values() if g in status_groups and g not in groups]
    groups += [g for g in global_groups if g in GLOBAL_KEEP]
    if u["name"].endswith(" (WMF)") or any(g in WMF_GROUPS or g.startswith("wmf-") for g in global_groups):
        groups.append("wmf")
    partial = bool(u.get("blockpartial"))
    return {
        "name": u["name"],
        "groups": groups,
        "labels": [LABELS.get(g, g) for g in groups],
        "editcount": u.get("editcount", 0),
        "registration": u.get("registration"),
        "blocked": ("blockid" in u and not partial) or locked,  # частичная блокировка впуску не мешает
        "blocked_partial": partial,
        "locked": locked,  # глобальная блокировка учётки (CentralAuth lock); известна только при with_global
        "sysop": "sysop" in groups,
        # АПАТ: своя группа либо группы, включающие её права.
        "apat": bool({"autoreview", "editor", "sysop"} & set(groups)),
    }


async def users_info(
    session: aiohttp.ClientSession, names: list[str], with_global: bool = True
) -> dict[str, dict | None]:
    """Пакетно (до 50 имён): имя -> info | None. Глобальные группы — отдельным запросом на имя,
    только для тех, у кого они могут быть (стюарды и т.п. редки, но запрос дешёвый)."""
    out: dict[str, dict | None] = {}
    names = [n for n in names if valid_name(n)]
    st = await statuses(session) if names else {}
    for i in range(0, len(names), 50):
        chunk = names[i : i + 50]
        asked = {norm_name(n): n for n in chunk}  # ответ приходит с нормализованным именем; ключ ответа = как спросили
        d = await _get(
            session,
            action="query",
            list="users",
            ususers="|".join(chunk),
            usprop="groups|editcount|registration|blockinfo",
        )
        for u in d["query"]["users"]:
            key = asked.get(norm_name(u.get("name", "")), u.get("name", ""))
            if "invalid" in u:
                out[key] = None
                continue
            if "missing" in u:  # нет локальной учётки — может быть глобальная (сотрудники Фонда, другие разделы)
                out[key] = await _global_only(session, u.get("name", ""))
                continue
            gg, locked = [], False
            if with_global:
                g = await _get(session, action="query", meta="globaluserinfo", guiuser=u["name"], guiprop="groups")
                gui = g["query"].get("globaluserinfo", {})
                gg, locked = gui.get("groups", []), bool(gui.get("locked"))
            out[key] = _pack(u, gg, st.get(u["name"], set()), locked)
    return out


async def _global_only(session: aiohttp.ClientSession, name: str) -> dict | None:
    """Учётка без локального аккаунта в рувики: данные из CentralAuth. Локальных флагов нет по определению."""
    g = await _get(session, action="query", meta="globaluserinfo", guiuser=name, guiprop="groups|editcount")
    gui = g["query"].get("globaluserinfo", {})
    if "missing" in gui:
        return None
    u = {
        "name": gui["name"],
        "groups": [],
        "editcount": gui.get("editcount", 0),
        "registration": gui.get("registration"),
    }
    return _pack(u, gui.get("groups", []), set(), bool(gui.get("locked")))


async def user_info(session: aiohttp.ClientSession, name: str) -> dict | None:
    """groups (локальные + глобальные), editcount, registration, blocked. None — нет участника."""
    if not valid_name(name):
        return None
    res = await users_info(session, [name])
    return next(iter(res.values()), None)


async def find_code_in_contribs(session: aiohttp.ClientSession, name: str, code: str, since: dt.datetime) -> str | None:
    """Ищет правку участника после `since`, в описании которой есть `code`. Возвращает revid."""
    d = await _get(
        session,
        action="query",
        list="usercontribs",
        ucuser=name,
        uclimit=20,
        ucprop="ids|comment|timestamp",
        ucend=since.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    for c in d["query"].get("usercontribs", []):
        if code.lower() in (c.get("comment") or "").lower():
            return str(c["revid"])
    return None
