"""Чтение рувики через API. Только чтение, без аккаунта."""
from __future__ import annotations
import asyncio
import datetime as dt
import aiohttp

API = "https://ru.wikipedia.org/w/api.php"
UA = "Apparitor/0.1 (https://ru.wikipedia.org/wiki/User:Carn; access bot for ruwiki ArbCom Discord)"

# группа рувики -> человекочитаемая метка
LABELS = {
    "sysop": "админ", "bureaucrat": "бюрократ", "checkuser": "чекюзер", "suppress": "ревизор",
    "arbcom": "арбитр", "closer": "подводящий итоги", "editor": "патрулирующий",
    "autoreview": "автопатрулируемый", "rollbacker": "откатывающий", "engineer": "инженер",
    "bot": "бот", "interface-admin": "админ интерфейса",
}
HIDDEN = {"*", "user", "autoconfirmed", "temporary-account-viewer", "uploader", "filemover",
          "ipblock-exempt", "suppressredirect", "confirmed"}


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


async def user_info(session: aiohttp.ClientSession, name: str) -> dict | None:
    """groups, editcount, registration, blocked. None — нет такого участника."""
    d = await _get(session, action="query", list="users", ususers=name,
                   usprop="groups|editcount|registration|blockinfo")
    u = d["query"]["users"][0]
    if "missing" in u or "invalid" in u:
        return None
    groups = [g for g in u.get("groups", []) if g not in HIDDEN]
    return {
        "name": u["name"],
        "groups": groups,
        "labels": [LABELS.get(g, g) for g in groups],
        "editcount": u.get("editcount", 0),
        "registration": u.get("registration"),
        "blocked": "blockid" in u,
        "sysop": "sysop" in groups,
        # АПАТ: своя группа либо группы, включающие её права.
        "apat": bool({"autoreview", "editor", "sysop"} & set(groups)),
    }


async def find_code_in_contribs(session: aiohttp.ClientSession, name: str, code: str,
                                since: dt.datetime) -> str | None:
    """Ищет правку участника после `since`, в описании которой есть `code`. Возвращает revid."""
    d = await _get(session, action="query", list="usercontribs", ucuser=name, uclimit=20,
                   ucprop="ids|comment|timestamp", ucend=since.strftime("%Y-%m-%dT%H:%M:%SZ"))
    for c in d["query"].get("usercontribs", []):
        if code.lower() in (c.get("comment") or "").lower():
            return str(c["revid"])
    return None


async def group_members(session: aiohttp.ClientSession, group: str) -> list[str]:
    names, cont = [], {}
    while True:
        d = await _get(session, action="query", list="allusers", augroup=group, aulimit=500, **cont)
        names += [u["name"] for u in d["query"]["allusers"]]
        if "continue" not in d:
            return names
        cont = d["continue"]
