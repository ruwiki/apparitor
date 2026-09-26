"""Чтение рувики и глобальных групп через API. Только чтение, без аккаунта."""
from __future__ import annotations
import asyncio
import datetime as dt
import aiohttp

API = "https://ru.wikipedia.org/w/api.php"
UA = "Apparitor/0.1 (https://ru.wikipedia.org/wiki/User:Carn; access bot for ruwiki ArbCom Discord)"

# группа (локальная рувики или глобальная) -> человекочитаемая метка
LABELS = {
    "sysop": "админ", "bureaucrat": "бюрократ", "checkuser": "чекюзер", "suppress": "ревизор",
    "arbcom": "арбитр", "closer": "подводящий итоги", "editor": "патрулирующий",
    "autoreview": "автопатрулируемый", "rollbacker": "откатывающий", "engineer": "инженер",
    "bot": "бот", "interface-admin": "админ интерфейса", "steward": "стюард",
    "global-sysop": "глобальный админ", "global-interface-editor": "глобальный редактор интерфейса",
}
HIDDEN = {"*", "user", "autoconfirmed", "temporary-account-viewer", "uploader", "filemover",
          "ipblock-exempt", "suppressredirect", "confirmed"}
GLOBAL_KEEP = {"steward", "global-sysop", "global-interface-editor", "founder", "ombuds"}
BAD_CHARS = set("|#<>[]{}")


def valid_name(name: str) -> bool:
    return bool(name.strip()) and not (set(name) & BAD_CHARS) and len(name) <= 255


async def _get(session: aiohttp.ClientSession, **params) -> dict:
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    for attempt in range(4):
        async with session.get(API, params=params, headers={"User-Agent": UA}) as r:
            if r.status == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return await r.json()
    raise RuntimeError("ruwiki API: 429 после 4 попыток")


def _pack(u: dict, global_groups: list[str]) -> dict:
    groups = [g for g in u.get("groups", []) if g not in HIDDEN] + [g for g in global_groups if g in GLOBAL_KEEP]
    partial = bool(u.get("blockpartial"))
    return {
        "name": u["name"],
        "groups": groups,
        "labels": [LABELS.get(g, g) for g in groups],
        "editcount": u.get("editcount", 0),
        "registration": u.get("registration"),
        "blocked": "blockid" in u and not partial,   # частичная блокировка впуску не мешает
        "blocked_partial": partial,
        "sysop": "sysop" in groups,
        # АПАТ: своя группа либо группы, включающие её права.
        "apat": bool({"autoreview", "editor", "sysop"} & set(groups)),
    }


async def users_info(session: aiohttp.ClientSession, names: list[str], with_global: bool = True) -> dict[str, dict | None]:
    """Пакетно (до 50 имён): имя -> info | None. Глобальные группы — отдельным запросом на имя,
    только для тех, у кого они могут быть (стюарды и т.п. редки, но запрос дешёвый)."""
    out: dict[str, dict | None] = {}
    names = [n for n in names if valid_name(n)]
    for i in range(0, len(names), 50):
        chunk = names[i:i + 50]
        d = await _get(session, action="query", list="users", ususers="|".join(chunk),
                       usprop="groups|editcount|registration|blockinfo")
        for u in d["query"]["users"]:
            if "missing" in u or "invalid" in u:
                out[u.get("name", "")] = None
                continue
            gg = []
            if with_global:
                g = await _get(session, action="query", meta="globaluserinfo", guiuser=u["name"], guiprop="groups")
                gg = g["query"].get("globaluserinfo", {}).get("groups", [])
            out[u["name"]] = _pack(u, gg)
    return out


async def user_info(session: aiohttp.ClientSession, name: str) -> dict | None:
    """groups (локальные + глобальные), editcount, registration, blocked. None — нет участника."""
    if not valid_name(name):
        return None
    res = await users_info(session, [name])
    return next(iter(res.values()), None)


async def find_code_in_contribs(session: aiohttp.ClientSession, name: str, code: str,
                                since: dt.datetime) -> str | None:
    """Ищет правку участника после `since`, в описании которой есть `code`. Возвращает revid."""
    d = await _get(session, action="query", list="usercontribs", ucuser=name, uclimit=20,
                   ucprop="ids|comment|timestamp", ucend=since.strftime("%Y-%m-%dT%H:%M:%SZ"))
    for c in d["query"].get("usercontribs", []):
        if code.lower() in (c.get("comment") or "").lower():
            return str(c["revid"])
    return None
