"""Сопоставление участника Discord с учёткой вики по нику — подсказка для человека, не связка.
Чистые функции: кандидаты из трёх имён, выбор среди найденных."""

from __future__ import annotations

from .models import UserInfo


def candidates(nick: str | None, global_name: str | None, username: str) -> list[str]:
    """Ник на сервере, отображаемое имя, логин — без пустых и повторов, в этом порядке."""
    xs = [nick, global_name, username]
    return [x for i, x in enumerate(xs) if x and x not in xs[:i]]


def pick(names: list[str], infos: dict[str, UserInfo | None]) -> str | None:
    """Из найденных — с наибольшим числом правок: короткий ник («Pessimist», «Всеслав») часто существует
    в рувики как чужая пустая учётка. None — не найден ни один."""
    found = [n for n in names if infos.get(n)]
    return max(found, key=lambda n: infos[n].editcount) if found else None
