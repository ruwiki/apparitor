# Apparitor

Discord-бот доступов для серверов сообщества русской Википедии и Арбитражного комитета.
Проверяет вики-аккаунт участника (OAuth 2.0 через Мету, только идентификация) и выдаёт роли
по флагам в рувики. Хостится на Toolforge: https://apparitor.toolforge.org

## Команды
- `/login` — вход своей учёткой Викимедиа, привязка и роли.
- `/verify <имя>` + `/confirm` — запасной путь без OAuth: код в описании любой правки.
- `/status [имя]` — флаги участника рувики.
- `/sync` — пересчитать роли всем привязанным (нужно право Manage Roles).
- `/link @участник <имя>` — привязать вручную, когда ник никогда не совпадёт (Manage Roles; кто привязал — в журнале).
- `/audit` — сопоставить всех участников сервера с рувики, отчёт файлом (Manage Roles).
- `/optout-post` — опубликовать в канале ролей сообщение с 📭: выход из readme/announcements (Manage Roles).

## Фоновые функции (по серверу, в конфиге)
- `gate_watch` — раз в 5 минут бот спрашивает WikiAuthBot-ng (`/whois`, ключ сервера в `WIKIAUTHBOT_KEY_<id>`),
  нет ли у непроверенных участников связки с учёткой Викимедиа; нашлась — правило впуска сервера и роли
  (вхолостую — сводка в служебный канал). Новичков первых двух суток — каждый обход, остальных — раз в 6 часов.
- `track_categories` — новый канал в этих категориях получает роль доступа (префикс + имя канала) и строку
  с реакцией 🇦…🇿 в сообщении бота в `roles_channel_id`; канал вне них или видимый без ролей — строка в служебный канал.
- `optout_channels` — реакция 📭 прячет эти каналы участнику (запрет на участника) и включает пересылку их новых
  сообщений в личку; короткий канал (до 30 сообщений, readme) присылается целиком. Сначала проверяется,
  что личка открыта.

## Что бот НЕ делает
- Не читает сообщения: Message Content Intent не запрашивается. Пересылка в личку — штатный forward Discord,
  текст боту для этого не нужен.
- Не хранит OAuth-токены: после чтения имени участника токен выбрасывается.
- Не хранит почту и другие данные профиля: только имя участника, Discord ID и глобальный id учётки.
- Логирует только действия и идентификаторы, без текста сообщений.

## Откуда флаги
Группы участника — из API рувики (`list=users`), глобальные (стюард и т.п.) — `meta=globaluserinfo`.
Статусы без технической группы — ПИ+, борцы с вандализмом, клерки, технические удаляющие, VRTS,
а также полный состав АК (в группе `arbcom` нет арбитров-админов) — из `MediaWiki:Gadget-markadmins.json`
(обновляет MBHbot раз в несколько дней), кэш 6 часов. Ключи карты ролей: `closer-plus`, `vandalfighter`,
`clerk`, `techdeleter`, `vrts`, `arbcom`. ПИ+ заменяет ПИ: при сопоставленной роли ПИ+ роль ПИ не выдаётся.

## Режимы
`dry_run = true` — бот ничего не меняет, а пишет в служебный канал, что сделал бы. Можно задать на сервер
(`dry_run` в секции `[guild.<id>]`), как и свои критерии впуска (`[guild.<id>.admit]`, `apat_suffices` — АПАТ впускает сразу).
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

## Структура кода
| модуль | что | зависимости |
|---|---|---|
| `models.py` | `UserInfo`, `Decision`, формат времени — типы между слоями | — |
| `config.py` | TOML → `Config`/`GuildCfg`/`Admit` | — |
| `mw.py` | клиент API MediaWiki (одна вики, бэкофф 429); сюда же ляжет запись для арбвики | aiohttp |
| `ruwiki.py` | рувики как источник флагов: группы, статусы из JSON гаджета, CentralAuth → `UserInfo` | mw |
| `rules.py` | впуск и роли: чистые функции | config, models |
| `match.py` | сопоставление по нику: кандидаты, выбор | models |
| `audit.py` | сборка отчёта /audit из строк | models |
| `store.py` | ToolsDB/sqlite: связки, ожидания, кандидаты, журнал | models, pymysql |
| `bot.py` | Discord-клиент, отчёты, применение решения | всё выше |
| `commands/` | slash-команды: `identity` (все), `admin` (Manage Roles) | bot |
| `web.py` | OAuth-колбэк, healthz | bot |
| `wikiauthbot.py` | клиент WikiAuthBot-ng `/whois` | aiohttp |
| `gate.py` | обход непроверенных: связка → правило → роли, сводка | bot, wikiauthbot |
| `rooms.py` | новые каналы, реакции, выход из каналов и пересылка в личку | bot, store |

Новый источник (арбвики, Google Groups, WikiAuthBot) = свой модуль уровня `ruwiki.py`, который отдаёт
данные типами из `models.py`; правила и команды его не знают.

## Локальная отладка
`cp config.example.toml config.toml`, `.env` с теми же переменными, зависимости
`pip install --target vendor -r requirements.txt`, `./run.sh`. Хранилище задаётся в конфиге: `[db] kind = "sqlite"` локально, `"toolsdb"` на Toolforge.
