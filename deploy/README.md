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

## Гости (этап 3)

Каждый гость — отдельный процесс `server.py` (юнит-шаблон `codex-telegram-guest@.service`)
со своим `codex app-server`, тредом и рабочим каталогом `/root/guests-codex/<uid>`.
Состояние гостя: `/root/.codex-gram/telegram/guests/<uid>/` (access.json с owner=гость,
codex_settings.json, inbox.sock, bot.env, codex/ при своём аккаунте).

1. Установить шаблон (сам мастер юниты не создаёт, только включает экземпляры):
   ```
   cp /root/codex-gram/deploy/codex-telegram-guest@.service /etc/systemd/system/
   systemctl daemon-reload
   ```
2. Перезапустить мастер, чтобы он подхватил новый код: `systemctl restart codex-telegram.service`.
   При старте мастер делает `enable --now` юнитам всех гостей из access.json.
3. `/share <id|@username>` — доступ + запуск экземпляра + кнопка «Создать своего бота».
   `/unshare` — `disable --now` экземпляра, удаление bot.env. Каталоги гостя остаются.

Как ходят апдейты: мастер поллит бота (`allowed_updates` включает `managed_bot`), апдейты
гостя пишет JSON-строкой в `guests/<uid>/inbox.sock`. Гость со своим ботом (bot.env) поллит
его сам, сокет не поднимает; в общем боте ему приходит подсказка «пиши туда».

Аккаунт Codex гостя: по умолчанию общий (`~/.codex` владельца). `/login` у гостя пишет только в
`guests/<uid>/codex/auth.json` и переключает его на свой аккаунт; `/switch_account owner|own`,
`/logout` (только свой), `/accounts`. `/save_account`, `/delete_account`, `/share`, `/unshare`,
`/access`, `/check_update`, `/mcp_debug` гостю закрыты. Инструкции гостевого треда — только
`codex_instructions.md` и строка доступа гостя, без `codex_workflow.md` и памяти владельца.

Проверка гостя: `systemctl status codex-telegram-guest@<uid>`, лог
`/root/.codex-gram/telegram/guests/<uid>/bot.log`.

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
| `CODEX_GRAM_MEMORY_DIR` | каталог памяти владельца | индекс памяти в инструкциях треда (гостям не передаётся) |
| `CODEX_GRAM_GUEST_UID` | не задан | uid гостя: процесс — гостевой экземпляр |
| `CODEX_GRAM_GUESTS_DIR` | `~/guests-codex` | корень рабочих каталогов гостей |

## Откат

`systemctl disable --now codex-telegram.service`. Состояние остаётся в `STATE_DIR`.
Гости: `systemctl disable --now 'codex-telegram-guest@*'`.
Claude-бот (`claude-telegram*`) эта установка не трогает.
