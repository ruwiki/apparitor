# Apparitor

Бот доступов АК рувики. Этап 1 (26.09.2026): впуск по вики-аккаунту и роли по флагам рувики
(замена Черепашки), чтение статуса АПАТ. Заметки и план — `stuff/Projects/Статьи/ак-клерк-копайлот/04_apparitor-план.md`.

Запуск: `cp config.example.toml config.toml`, `.env` с `DISCORD_TOKEN=…`, `./run.sh`.
Зависимости в `vendor/` (`pip install --target vendor discord.py`), venv не нужен.
Интенты: только default — текст сообщений бот не читает. Команды: `/verify`, `/confirm`, `/status`, `/sync`.
Привязка: правка в рувики с кодом в описании (без OAuth; OAuth — следующий этап).
