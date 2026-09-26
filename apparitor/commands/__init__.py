"""Slash-команды: identity — привязка и статус (для всех), admin — /sync и /audit (Manage Roles).
Логика решений — в bot.evaluate, rules, match, audit; здесь только разбор ввода и ответы."""

from __future__ import annotations

from ..bot import Apparitor
from . import admin, identity


def register(bot: Apparitor) -> None:
    identity.register(bot)
    admin.register(bot)
