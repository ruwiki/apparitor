#!/bin/bash
# Деплой на Toolforge (запускает мантейнер со своей машины; секретов нет — они в envvars тула).
# Порядок: сборка образа из main → перезапуск вебсервиса → healthz.
set -e
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
