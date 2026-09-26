"""Конфиг (TOML) и окружение. Секреты только из окружения: DISCORD_TOKEN, OAUTH_*, TOOL_TOOLSDB_*."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field


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


@dataclass
class Admit:
    """Критерии впуска; 0 = не проверять."""

    min_edits: int = 0
    min_age_days: int = 0
    reject_blocked: bool = True


@dataclass
class GuildCfg:
    """Секция сервера. roles: условие -> имя роли ("" = не использовать); admitted/apat — условия,
    остальные ключи = группы вики и псевдогруппы статусов."""

    report_channel_id: int = 0
    report_names: bool = False
    roles: dict[str, str] = field(default_factory=dict)
    remove_on_confirm: list[str] = field(default_factory=list)


@dataclass
class Config:
    guilds: list[int]
    guild: dict[int, GuildCfg]
    admit: Admit
    dry_run: bool = True
    base_url: str = ""
    repo_url: str = ""
    db: dict = field(default_factory=dict)

    def for_guild(self, guild_id: int) -> GuildCfg:
        """Нет секции — пустая: бот на таком сервере только отчитывается."""
        return self.guild.get(guild_id) or GuildCfg()


def load_config(path: str = "config.toml") -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    if not raw.get("guilds"):
        raise SystemExit("guilds в конфиге обязателен: список id серверов, на которых работает бот")
    return Config(
        guilds=[int(g) for g in raw["guilds"]],
        guild={int(k): GuildCfg(**v) for k, v in raw.get("guild", {}).items()},
        admit=Admit(**raw.get("admit", {})),
        dry_run=bool(raw.get("dry_run", True)),
        base_url=raw.get("base_url", ""),
        repo_url=raw.get("repo_url", ""),
        db=raw.get("db", {}),
    )
