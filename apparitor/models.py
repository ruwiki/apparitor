"""Типы, которыми обмениваются модули. Словарей между слоями нет: поле либо есть, либо ошибка при импорте."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UserInfo:
    """Участник вики глазами бота: группы (локальные + глобальные + псевдогруппы статусов), метки для людей,
    вклад, блокировки. Локальных групп у учётки без аккаунта в рувики нет по определению."""

    name: str
    groups: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    editcount: int = 0
    registration: str | None = None
    blocked: bool = False  # ситевая блокировка либо глобальный lock; частичная — нет
    blocked_partial: bool = False
    locked: bool = False  # CentralAuth lock; известен только при запросе глобальных данных

    @property
    def sysop(self) -> bool:
        return "sysop" in self.groups

    @property
    def apat(self) -> bool:
        """АПАТ: своя группа либо группы, включающие её права."""
        return bool({"autoreview", "editor", "sysop"} & set(self.groups))


@dataclass
class Decision:
    """Полное решение по участнику: впуск и роли."""

    ok: bool
    why: str
    want: list[str]
    add: list[str]
    remove: list[str]

    @property
    def changes(self) -> bool:
        return bool(self.add or self.remove)
