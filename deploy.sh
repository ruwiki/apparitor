#!/bin/bash
# Деплой на Toolforge (запускает мантейнер со своей машины; секретов нет — они в envvars тула).
# Порядок: сборка образа из main → перезапуск вебсервиса → healthz.
set -e
cd "$(dirname "$0")"
# перед сборкой: линтер, тесты, запуск без сети, живая проверка чтения рувики (статусы из JSON, группы)
PATH="$PWD/tools/bin:$PATH" PYTHONPATH="vendor:tools:$PWD" ruff check . --exclude vendor,tools -q
PATH="$PWD/tools/bin:$PATH" PYTHONPATH="vendor:tools:$PWD" python3 -m pytest -q tests >/dev/null
PYTHONPATH="vendor:$PWD" APPARITOR_CONFIG=config.toolforge.toml python3 smoke.py
PYTHONPATH="vendor:$PWD" python3 -c '
import asyncio
from apparitor.ruwiki import RuWiki
async def main():
    w = RuWiki(); r = await w.users_info(["Carn"], with_global=False); await w.mw.close()
    assert r["Carn"] and "clerk" in r["Carn"].groups, r
asyncio.run(main()); print("live ok")'
[ -z "$(git status --porcelain)" ] || { echo "есть незакоммиченные изменения — сборка идёт из GitHub"; exit 1; }
git diff --quiet origin/main..HEAD 2>/dev/null || { echo "локальные коммиты не запушены"; exit 1; }
ssh toolforge 'become apparitor bash -s' <<'REMOTE'
set -e
toolforge build start https://github.com/ruwiki/apparitor >/dev/null
for i in $(seq 1 40); do
  st=$(toolforge build show 2>/dev/null | grep -E "^Status" || true)
  echo "$st" | grep -qiE "ok|success|fail|error" && break; sleep 15
done
echo "$st"; echo "$st" | grep -qi "ok" || { toolforge build logs | tail -30; exit 1; }
toolforge webservice buildservice restart --mount none
REMOTE
for i in $(seq 1 12); do sleep 10; curl -sf -m 10 https://apparitor.toolforge.org/healthz && { echo; exit 0; }; done
echo "healthz не ответил ok за 2 минуты"; exit 1
