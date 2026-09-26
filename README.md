# Apparitor

Discord-бот доступов для серверов сообщества русской Википедии и Арбитражного комитета.
Проверяет вики-аккаунт участника (OAuth 2.0 через Мету, только идентификация) и выдаёт роли
по флагам в рувики. Хостится на Toolforge: https://apparitor.toolforge.org

## Команды
- `/auth` — вход своей учёткой Викимедиа, привязка и роли.
- `/verify <имя>` + `/confirm` — запасной путь без OAuth: код в описании любой правки.
- `/status [имя]` — флаги участника рувики.
- `/sync` — пересчитать роли всем привязанным (нужно право Manage Roles).

## Что бот НЕ делает
- Не читает сообщения: Message Content Intent не запрашивается.
- Не хранит OAuth-токены: после чтения имени участника токен выбрасывается.
- Не хранит почту и другие данные профиля: только имя участника, Discord ID и глобальный id учётки.
- Логирует только действия и идентификаторы, без текста сообщений.

## Откуда флаги
Группы участника — из API рувики (`list=users`), глобальные (стюард и т.п.) — `meta=globaluserinfo`.
Статусы без технической группы — ПИ+, борцы с вандализмом, клерки, технические удаляющие, VRTS,
а также полный состав АК (в группе `arbcom` нет арбитров-админов) — из `MediaWiki:Gadget-markadmins.json`
(обновляет MBHbot раз в несколько дней), кэш 6 часов. Ключи карты ролей: `closer-plus`, `vandalfighter`,
`clerk`, `techdeleter`, `vrts`, `arbcom`.

## Режимы
`dry_run = true` — бот ничего не меняет, а пишет в служебный канал, что сделал бы.
Роли и критерии впуска — в `config.example.toml`.

## Развёртывание (Toolforge, тул `apparitor`)
Один процесс `web` из `Procfile`: Discord-клиент и HTTP на `$PORT`. Хранилище — ToolsDB
(база `<user>__apparitor`, создаётся один раз: `sql tools` → `create database <user>__apparitor character set utf8mb4`).
Секреты и конфиг — через envvars, в репо их нет:

    toolforge envvars create DISCORD_TOKEN …
    toolforge envvars create OAUTH_CLIENT_ID …
    toolforge envvars create OAUTH_CLIENT_SECRET …
    toolforge envvars create APPARITOR_CONFIG config.toolforge.toml
    toolforge build start https://github.com/ruwiki/apparitor
    toolforge webservice buildservice start
    curl https://apparitor.toolforge.org/healthz     # ok

Обновление: `toolforge build start …` и `toolforge webservice restart`. Логи: `toolforge webservice logs`.

## Локальная отладка
`cp config.example.toml config.toml`, `.env` с теми же переменными, зависимости
`pip install --target vendor -r requirements.txt`, `./run.sh`. Хранилище задаётся в конфиге: `[db] kind = "sqlite"` локально, `"toolsdb"` на Toolforge.
