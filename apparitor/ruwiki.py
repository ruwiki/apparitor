"""Русская Википедия как источник флагов: группы через API, статусы без техгруппы — из JSON гаджета,
глобальные группы и lock — из CentralAuth. Чистые функции (норма имени, разбор JSON, сборка UserInfo)
отделены от клиента `RuWiki`, чтобы их тестировать без сети."""

from __future__ import annotations

import datetime as dt
import json
import logging
import time

import aiohttp

from .models import UserInfo
from .mw import Client

log = logging.getLogger("apparitor.ruwiki")

API = "https://ru.wikipedia.org/w/api.php"

# группа (локальная рувики, глобальная или псевдогруппа статуса) -> человекочитаемая метка
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
# глобальные группы, влияющие на роли; "wmf" — псевдогруппа, см. pack()
GLOBAL_KEEP = {"steward", "global-sysop", "global-interface-editor", "founder", "ombuds", "wmf"}
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
BAD_CHARS = set("|#<>[]{}")


# --- чистые функции --------------------------------------------------------------
def norm_name(name: str) -> str:
    """Как MediaWiki: пробелы вместо подчёркиваний, первая буква заглавная. API возвращает имя уже в таком виде,
    а ник в Discord может быть «bezik» — сопоставляем по нормализованному."""
    name = name.strip().replace("_", " ")
    return name[:1].upper() + name[1:]


def valid_name(name: str) -> bool:
    return bool(name.strip()) and not (set(name) & BAD_CHARS) and len(name) <= 255


def parse_statuses(content: str) -> dict[str, set[str]]:
    """JSON гаджета -> имя -> псевдогруппы (только ключи из STATUS_GROUPS)."""
    user_set = json.loads(content).get("userSet", {})
    out: dict[str, set[str]] = {}
    for key, group in STATUS_GROUPS.items():
        for name in user_set.get(key, []):
            out.setdefault(name, set()).add(group)
    return out


def pack(
    u: dict, global_groups: list[str] = (), status_groups: set[str] = frozenset(), locked: bool = False
) -> UserInfo:
    """Ответ list=users (+ глобальные группы, статусы, lock) -> UserInfo."""
    groups = [g for g in u.get("groups", []) if g not in HIDDEN]
    groups += [g for g in STATUS_GROUPS.values() if g in status_groups and g not in groups]
    groups += [g for g in global_groups if g in GLOBAL_KEEP]
    if u["name"].endswith(" (WMF)") or any(g in WMF_GROUPS or g.startswith("wmf-") for g in global_groups):
        groups.append("wmf")
    partial = bool(u.get("blockpartial"))
    return UserInfo(
        name=u["name"],
        groups=groups,
        labels=[LABELS.get(g, g) for g in groups],
        editcount=u.get("editcount", 0),
        registration=u.get("registration"),
        blocked=("blockid" in u and not partial) or locked,  # частичная блокировка впуску не мешает
        blocked_partial=partial,
        locked=locked,
    )


# --- клиент ------------------------------------------------------------------------
class RuWiki:
    def __init__(self, session: aiohttp.ClientSession):
        self.mw = Client(session, API)
        self._statuses: tuple[float, dict[str, set[str]]] = (0.0, {})

    async def statuses(self) -> dict[str, set[str]]:
        """Статусы по JSON гаджета, кэш на STATUS_TTL. При сбое — прежний кэш (или пусто) и предупреждение в лог."""
        ts, cached = self._statuses
        if cached and time.monotonic() - ts < STATUS_TTL:
            return cached
        try:
            out = parse_statuses(await self.mw.raw_page(STATUS_PAGE) or "{}")
        except Exception as e:  # любой сбой чтения: флаги из групп важнее, статусы подождут
            log.warning("%s не прочитан (%s); статусы %s", STATUS_PAGE, e, "из кэша" if cached else "не учитываются")
            return cached
        self._statuses = (time.monotonic(), out)
        return out

    async def global_info(self, name: str) -> dict:
        """meta=globaluserinfo: groups, locked, editcount, registration; {} — глобальной учётки нет."""
        g = await self.mw.get(action="query", meta="globaluserinfo", guiuser=name, guiprop="groups|editcount")
        gui = g["query"].get("globaluserinfo", {})
        return {} if "missing" in gui else gui

    async def users_info(
        self, names: list[str], with_global: bool = True, global_fallback: bool = False
    ) -> dict[str, UserInfo | None]:
        """Пакетно (до 50 имён): имя как спросили -> UserInfo | None (ответ приходит с нормализованным именем).
        with_global — глобальные группы и lock отдельным запросом на имя (дорого; только где могут повлиять).
        global_fallback — имя без локальной учётки искать в CentralAuth: только для точного имени
        (/auth, /status); по нику это ловит чужие пустые учётки других разделов."""
        out: dict[str, UserInfo | None] = {}
        names = [n for n in names if valid_name(n)]
        st = await self.statuses() if names else {}
        for i in range(0, len(names), 50):
            chunk = names[i : i + 50]
            asked = {norm_name(n): n for n in chunk}
            d = await self.mw.get(
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
                if "missing" in u:
                    out[key] = await self._global_only(u.get("name", "")) if global_fallback else None
                    continue
                gg, locked = [], False
                if with_global:
                    gui = await self.global_info(u["name"])
                    gg, locked = gui.get("groups", []), bool(gui.get("locked"))
                out[key] = pack(u, gg, st.get(u["name"], set()), locked)
        return out

    async def _global_only(self, name: str) -> UserInfo | None:
        """Учётка без локального аккаунта в рувики (сотрудники Фонда, другие разделы): данные из CentralAuth."""
        gui = await self.global_info(name)
        if not gui:
            return None
        u = {
            "name": gui["name"],
            "groups": [],
            "editcount": gui.get("editcount", 0),
            "registration": gui.get("registration"),
        }
        return pack(u, gui.get("groups", []), set(), bool(gui.get("locked")))

    async def user_info(self, name: str) -> UserInfo | None:
        """Точное имя: локальная учётка, иначе глобальная. None — участника нет нигде."""
        if not valid_name(name):
            return None
        res = await self.users_info([name], global_fallback=True)
        return next(iter(res.values()), None)

    async def find_code_in_contribs(self, name: str, code: str, since: dt.datetime) -> str | None:
        """Правка участника после `since` с `code` в описании. Возвращает revid."""
        d = await self.mw.get(
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
