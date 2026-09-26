"""Правила впуска и ролей. Чистые функции без Discord и сети — их и тестируем."""

from __future__ import annotations

import datetime as dt

from .config import Admit, GuildCfg
from .models import Decision, UserInfo, parse_ts

# Старший статус заменяет младший: ПИ+ вместо ПИ (решение владельца 26.09). Действует, только если старшему
# на сервере сопоставлена роль; иначе младшая роль остаётся.
SUPERSEDES = {"closer-plus": "closer"}
CONDITIONS = ("admitted", "apat")  # ключи карты ролей, которые не группы


def admissible(admit: Admit, info: UserInfo) -> tuple[bool, str]:
    if admit.reject_blocked and info.blocked:
        return False, "учётка глобально заблокирована (lock)" if info.locked else "аккаунт заблокирован"
    if admit.min_edits and info.editcount < admit.min_edits:
        return False, f"правок {info.editcount} < {admit.min_edits}"
    if admit.min_age_days and info.registration:
        age = (dt.datetime.now(dt.UTC) - parse_ts(info.registration)).days
        if age < admit.min_age_days:
            return False, f"аккаунту {age} дн. < {admit.min_age_days}"
    return True, "ок"


def managed_roles(g: GuildCfg) -> set[str]:
    """Роли, которыми бот распоряжается на сервере: все из карты + снимаемые после подтверждения."""
    return {v for v in g.roles.values() if v} | set(g.remove_on_confirm)


def wanted_roles(g: GuildCfg, info: UserInfo) -> list[str]:
    """Имена ролей по флагам: admitted всем прошедшим, apat по условию, остальные ключи = группы.
    Порядок = порядок ключей в конфиге."""
    r = g.roles
    out = [r["admitted"]] if r.get("admitted") else []
    if info.apat and r.get("apat"):
        out.append(r["apat"])
    groups = set(info.groups)
    groups -= {low for high, low in SUPERSEDES.items() if high in groups and r.get(high)}
    out += [v for k, v in r.items() if k not in CONDITIONS and v and k in groups]
    return out


def role_diff(have: set[str], want: set[str], managed: set[str]) -> tuple[list[str], list[str]]:
    """Что выдать и что снять, не трогая роли вне управляемого набора."""
    return sorted(want - have), sorted((have & managed) - want)


def decide(admit: Admit, g: GuildCfg, info: UserInfo, have: set[str]) -> Decision:
    ok, why = admissible(admit, info)
    want = set(wanted_roles(g, info)) if ok else set()
    add, rem = role_diff(have, want, managed_roles(g))
    return Decision(ok=ok, why=why, add=add, remove=rem)
