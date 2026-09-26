"""Правила впуска и ролей. Чистые функции без Discord и сети — их и тестируем."""

from __future__ import annotations

import datetime as dt

from .store import parse_ts


def admissible(admit: dict, info: dict) -> tuple[bool, str]:
    """Критерии впуска; порог 0 = не проверять."""
    if admit.get("reject_blocked", True) and info["blocked"]:
        return False, "аккаунт заблокирован"
    if admit.get("min_edits") and info["editcount"] < admit["min_edits"]:
        return False, f"правок {info['editcount']} < {admit['min_edits']}"
    if admit.get("min_age_days") and info["registration"]:
        age = (dt.datetime.now(dt.UTC) - parse_ts(info["registration"])).days
        if age < admit["min_age_days"]:
            return False, f"аккаунту {age} дн. < {admit['min_age_days']}"
    return True, "ок"


def managed_roles(gcfg: dict) -> set[str]:
    """Роли, которыми бот распоряжается на сервере: все из карты + снимаемые после подтверждения."""
    return {v for v in gcfg.get("roles", {}).values() if v} | set(gcfg.get("remove_on_confirm", []))


def wanted_roles(gcfg: dict, info: dict) -> list[str]:
    """Имена ролей по флагам: admitted всем прошедшим, apat по условию, остальные ключи = группы вики.
    Пустая строка в конфиге = не использовать."""
    r = gcfg.get("roles", {})
    out = [r["admitted"]] if r.get("admitted") else []
    if info["apat"] and r.get("apat"):
        out.append(r["apat"])
    out += [v for k, v in r.items() if k not in ("admitted", "apat") and v and k in info["groups"]]
    return out


def role_diff(have: set[str], want: set[str], managed: set[str]) -> tuple[list[str], list[str]]:
    """Что выдать и что снять, не трогая роли вне управляемого набора."""
    return sorted(want - have), sorted((have & managed) - want)


def decide(admit: dict, gcfg: dict, info: dict, have: set[str]) -> dict:
    """Полное решение по участнику: ok/why, want, add, remove."""
    ok, why = admissible(admit, info)
    want = set(wanted_roles(gcfg, info)) if ok else set()
    add, rem = role_diff(have, want, managed_roles(gcfg))
    return {"ok": ok, "why": why, "want": sorted(want), "add": add, "remove": rem}
