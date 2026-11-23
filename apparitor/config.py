"""Конфиг (TOML) и окружение. Секреты только из окружения: DISCORD_TOKEN, OAUTH_*, TOOL_TOOLSDB_*."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields


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
    apat_suffices: bool = False  # АПАТ впускает без проверки правок и стажа (правило сервера сообщества)


@dataclass
class GuildCfg:
    """Секция сервера. roles: условие -> имя роли ("" = не использовать); admitted/apat — условия,
    остальные ключи = группы вики и псевдогруппы статусов."""

    report_channel_id: int = 0
    report_names: bool = False
    roles: dict[str, str] = field(default_factory=dict)
    remove_on_confirm: list[str] = field(default_factory=list)
    admit: Admit | None = None  # свои критерии впуска; нет — общие [admit]
    dry_run: bool | None = None  # свой холостой режим; нет — общий
    gate_watch: bool = False  # искать связки непроверенных в WikiAuthBot (нужен WIKIAUTHBOT_KEY_<id сервера>)
    roles_channel_id: int = 0  # канал, где бот держит своё сообщение с реакциями
    track_categories: dict[str, str] = field(default_factory=dict)  # id категории -> префикс роли нового канала
    optout_channels: list[int] = field(default_factory=list)  # из них можно выйти реакцией, сообщения — в личку


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
        """Нет секции — пустая: ролей нет, служебного канала нет; команды отвечают только спросившему."""
        return self.guild.get(guild_id) or GuildCfg()

    def admit_for(self, guild_id: int) -> Admit:
        return self.for_guild(guild_id).admit or self.admit

    def dry_for(self, guild_id: int) -> bool:
        g = self.for_guild(guild_id).dry_run
        return self.dry_run if g is None else g


def _section(cls, raw: dict, where: str):
    """dataclass из секции TOML; неизвестный ключ (опечатка) — понятная ошибка вместо TypeError при старте."""
    known = {f.name for f in fields(cls)}
    if bad := set(raw) - known:
        raise SystemExit(f"конфиг, секция {where}: неизвестные ключи {sorted(bad)}; допустимы {sorted(known)}")
    return cls(**raw)


def _guild(raw: dict, where: str) -> GuildCfg:
    raw = dict(raw)
    admit = raw.pop("admit", None)
    g = _section(GuildCfg, raw, where)
    if admit is not None:
        g.admit = _section(Admit, admit, f"{where}.admit")
    return g


def load_config(path: str = "config.toml") -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    if not raw.get("guilds"):
        raise SystemExit("guilds в конфиге обязателен: список id серверов, на которых работает бот")
    return Config(
        guilds=[int(g) for g in raw["guilds"]],
        guild={int(k): _guild(v, f"guild.{k}") for k, v in raw.get("guild", {}).items()},
        admit=_section(Admit, raw.get("admit", {}), "admit"),
        dry_run=bool(raw.get("dry_run", True)),
        base_url=raw.get("base_url", ""),
        repo_url=raw.get("repo_url", ""),
        db=raw.get("db", {}),
    )
