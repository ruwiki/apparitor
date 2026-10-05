"""Проверка запуска без сети: конфиг, хранилище (sqlite), регистрация команд, load_env/read_token."""

import asyncio
import os

os.environ.pop("PORT", None)
os.environ.setdefault("DISCORD_TOKEN", "x")
from apparitor.bot import Apparitor
from apparitor.commands import register
from apparitor.config import load_config, load_env, read_token
from apparitor.store import Store

load_env()
read_token()
cfg = load_config(os.environ.get("APPARITOR_CONFIG", "config.example.toml"))
cfg.db = {"kind": "sqlite", "sqlite_path": ":memory:"}
bot = Apparitor(cfg)
register(bot)
names = sorted(c.name for c in bot.tree.get_commands())
assert names == ["audit", "confirm", "link", "login", "status", "sync", "verify"], names
asyncio.run(Store(cfg.db).health())
print("smoke ok:", names)
