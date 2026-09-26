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

## Режимы
`dry_run = true` — бот ничего не меняет, а пишет в служебный канал, что сделал бы.
Роли и критерии впуска — в `config.example.toml`.

## Запуск
Локально: `cp config.example.toml config.toml`, `.env` с `DISCORD_TOKEN`, `OAUTH_CLIENT_ID`,
`OAUTH_CLIENT_SECRET`; зависимости `pip install --target vendor -r requirements.txt`; `./run.sh`.
Toolforge: build service из этого репо (`Procfile`), секреты через `toolforge envvars`,
конфиг `config.toolforge.toml` (путь задаётся envvar `APPARITOR_CONFIG`).
