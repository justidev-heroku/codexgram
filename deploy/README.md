# codex-gram: установка и первый запуск

Сервис `codex-telegram.service` запускает `server.py` в фоновом режиме. Тот поднимает
`codex app-server` дочерним процессом (`KillMode=control-group`, при остановке сервиса
умирает и он).

## Установка

1. Убедиться, что `codex` установлен и в PATH (`codex --version`).
2. Создать каталог состояния и файл с токеном бота (токен не коммитить):
   ```
   mkdir -p /root/.codex-gram/telegram
   printf 'TELEGRAM_BOT_TOKEN=<токен из @BotFather>\n' > /root/.codex-gram/telegram/.env
   chmod 600 /root/.codex-gram/telegram/.env
   ```
3. Создать `access.json` с владельцем (аналог `/telegram:init`):
   ```
   python3 /root/codex-gram/deploy/init_access.py 8739693469
   ```
   Скрипт не перезаписывает файл с другим владельцем без `--force`.
4. Войти в аккаунт Codex: `codex login` (или `/login` в Telegram после запуска).
5. Установить юнит (только по команде владельца, сам скрипт ничего не включает):
   ```
   cp /root/codex-gram/deploy/codex-telegram.service /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now codex-telegram.service
   ```

## Проверка

- `systemctl status codex-telegram.service` — `active (running)`.
- `journalctl -u codex-telegram.service -n 50` и `/root/.codex-gram/telegram/bot.log`.
- В Telegram: `/usage` покажет лимиты; `/model` — список моделей.

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `TELEGRAM_STATE_DIR` | `~/.codex-gram/telegram` | `.env`, `access.json`, логи, аккаунты |
| `CODEX_GRAM_CWD` | домашний каталог | рабочий каталог треда |
| `CODEX_GRAM_BIN` | `codex` из PATH | путь к бинарю codex |
| `CODEX_GRAM_CODEX_HOME` | не задан (`~/.codex`) | каталог аккаунта Codex (для тестов и отдельных аккаунтов) |
| `CODEX_GRAM_MEMORY_DIR` | каталог памяти владельца | индекс памяти в инструкциях треда |

## Откат

`systemctl disable --now codex-telegram.service`. Состояние остаётся в `STATE_DIR`.
Claude-бот (`claude-telegram*`) эта установка не трогает.
