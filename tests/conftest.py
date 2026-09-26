import os

import pytest

from apparitor.bot import Apparitor
from apparitor.commands import register
from apparitor.config import load_config
from apparitor.models import UserInfo

GUILD = 223183550965481473


@pytest.fixture
def cfg():
    os.environ.pop("PORT", None)
    c = load_config("config.toolforge.toml")
    c.db = {"kind": "sqlite", "sqlite_path": ":memory:"}
    return c


@pytest.fixture
def bot(cfg):
    b = Apparitor(cfg, members_intent=False)
    register(b)
    return b


def info(**kw) -> UserInfo:
    base = {"name": "X", "editcount": 10, "registration": "2010-01-01T00:00:00Z"}
    base.update(kw)
    return UserInfo(**base)
