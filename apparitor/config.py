"""Конфиг (TOML) и окружение. Секреты только из окружения: DISCORD_TOKEN, OAUTH_*, TOOL_TOOLSDB_*."""

from __future__ import annotations

import os
import tomllib


def load_env(path: str = ".env") -> None:
    """Локальный .env в окружение; на Toolforge всё уже в envvars."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)


def read_token() -> str:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN не задан (.env или окружение)")
    return token


def load_config(path: str = "config.toml") -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    if not cfg.get("guilds"):
        raise SystemExit("guilds в конфиге обязателен: список id серверов, на которых работает бот")
    cfg["guild"] = {int(k): v for k, v in cfg.get("guild", {}).items()}
    cfg.setdefault("admit", {})
    cfg.setdefault("dry_run", True)
    return cfg


def guild_cfg(cfg: dict, guild_id: int) -> dict:
    """Секция сервера: report_channel_id, report_names, roles, remove_on_confirm. Нет секции — пустая."""
    return cfg["guild"].get(guild_id, {})
