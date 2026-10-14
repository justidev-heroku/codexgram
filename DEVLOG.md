# DEVLOG

## 2026-10-14 — Гостевые версии команд

- Гостю доступны свои `/model`, `/effort`, `/auto`, `/resume`, `/goal`, `/close`
  (= `/new`), `/restart`, `/usage`, плюс `/new`, `/stop`. Владельческие хэндлеры
  сначала проверяют `guest_cmd_sender()` и для гостя зовут `guest_cmd_*`; у
  владельца поведение прежнее.
- Настройки гостя — файлы в `guests/<id>/` (`model`, `effort`, `auto`, `goal`),
  применяются со следующего запуска: `--model`, `--effort`, `/auto` выкл →
  `--permission-mode dontAsk` + `permissions.deny` на Bash/Edit/Write/MultiEdit/
  NotebookEdit во флаговых `--settings` (иначе allow `Bash` из settings.json
  владельца пропустил бы shell), цель — в `--append-system-prompt`.
  Кнопки гостя — отдельные префиксы `gmodel:`, `geffort:`, `gres:`, `ggoal_clear`,
  проверка `guest_gate_cb` (только гость и только в своей личке).
- `/resume` гостя: сессии из `~/.claude/projects/<ключ ~/guests/<id>>/`, выбор
  пишет `guests/<id>/session_id`. `/restart` гостя — kill его запуска + очистка
  очереди, бот не перезапускается. `/usage` — лимиты общего аккаунта владельца.
- Аккаунт Claude у гостей всегда владельца: `/accounts`, `/login`, `/save_account`,
  `/switch_account`, `/delete_account`, `/logout`, `/check_update`, `/mcp_debug`,
  `/share`, `/unshare`, `/access` — только владельцу.
- Меню: гостям `BotCommandScopeChat` со своим набором (при старте бота и `/share`),
  при `/unshare` scope удаляется; из общего меню убраны `/new` и `/stop`.
- Тесты: `tests/test_guest_commands.py`.

## 2026-10-14 — Гости работают независимо от владельца

- Сообщения гостей больше не инжектятся в сессию владельца и не дублируются
  ему уведомлением «гость написал». Для каждого гостя `server.py` запускает
  отдельный headless `claude -p --output-format stream-json` (первый раз
  `--session-id <uuid>`, дальше `--resume`; id в
  `~/.claude/channels/telegram/guests/<id>/session_id`), рабочий каталог
  `~/guests/<id>` (env `CLAUDE_GRAM_GUESTS_DIR`), очередь — один запуск на гостя.
- Без подтверждений: `--permission-mode bypassPermissions` + `IS_SANDBOX=1`.
  Изоляция от моста: без `--channels`, `--strict-mcp-config --mcp-config
  '{"mcpServers":{}}'`, плагин claude-gram выключен через `--settings`, из env
  вычищены `CLAUDE_CODE_*`, `CLAUDECODE`, `CLAUDE_TELEGRAM_*`, `TELEGRAM_*` —
  второго поллера (409) не возникает.
- Модель: `CLAUDE_GRAM_GUEST_MODEL` > файл `guest_model` > `last_launched_model`.
  Таймаут запуска `CLAUDE_GRAM_GUEST_TIMEOUT` (сек, по умолчанию 3600).
- Гостю: статус-пузырь «⏳ работаю…» с последними действиями, финальный ответ
  plain text чанками по 4096, строки `SEND_FILE: /abs/path` → файлы в чат.
  Команды гостя: `/new` (новая сессия), `/stop` (kill текущего запуска + очистка
  очереди). Владелец: `/access` показывает «сейчас работает», `/unshare` убивает
  активный запуск гостя.
- Тесты: `tests/test_guest_runner.py` (subprocess замокан, `os.killpg` подменён).

## 2026-07-25 — Установка на чистую машину и права на токены

Шесть фиксов, из-за которых бот ломался при установке не на «эту» машину.

- **A. Пользователь `claude-login` создаётся установщиком.** Команда `/login`
  запускает Claude под отдельным системным юзером, но никто его не создавал —
  на чистой машине `/login` просто не работал. В `install.py` добавлена
  `ensure_login_user()`: проверяет `pwd.getpwnam("claude-login")`, при
  отсутствии делает `sudo useradd -m -s /bin/bash claude-login`. Если не
  получилось — жёлтое предупреждение с командой для ручного создания,
  установка продолжается.
- **B. Убран `os.getlogin()` из шаблона systemd-юнита.** Без управляющего
  терминала он падает с `OSError`, и установщик валился трейсбеком. Теперь
  `resolve_service_user(HOME_DIR)` берёт владельца каталога HOME
  (`pwd.getpwuid(os.stat(...).st_uid)`), при любой ошибке — `root`. Побочно
  вылечен рассинхрон «`User=` не тот, чей `HOME=`» при установке через sudo.
- **C. Путь к бинарю `claude` больше не захардкожен.** Вместо `/usr/bin/claude`
  и в wrapper'е, и в `server.py` появилась `resolve_claude_bin()`: сначала
  `shutil.which`, затем `/usr/local/bin`, `/usr/bin`, `~/.local/bin`, в
  крайнем случае — просто `claude` (пусть `execvp` ищет по PATH).
- **D. Файлы с токенами пишутся с правами 0600.** Профили
  `.credentials.<имя>.json` и `.claude.<имя>.json` создавались с 0644, тогда
  как сам Claude держит 0600. Введён хелпер `write_secret()` (запись + chmod),
  он используется во всех местах сохранения/переключения аккаунтов. Плюс
  `harden_secret_permissions()` при старте бота один раз ужимает права уже
  лежащих на диске файлов, если они шире 0600.
- **E. Единый HOME вместо `/root`.** В `server.py` было ~30 захардкоженных
  путей `/root/...`, при установке не под root профили писались в `/root`, а
  `/accounts` искал их в `Path.home()` — список получался пустым. Добавлена
  константа `USER_HOME` (с override через `CLAUDE_GRAM_HOME`), все пути
  считаются от неё. Пути служебного юзера `/home/claude-login/...` не
  тронуты; при `HOME=/root` поведение прежнее.
- **F. Понятная диагностика вместо тихого зависания `/login`.** Файлы sudoers
  намеренно не автоматизируются. Вместо этого `cmd_login` перед запуском PTY
  проверяет: есть ли юзер `claude-login`, найден ли бинарь `claude`, запущен
  ли бот от root (иначе `sudo -u claude-login` спросит пароль и флоу
  повиснет). На каждый случай — сообщение в Telegram с готовой командой.

Тесты: `tests/test_install_env.py` (23 кейса) — `resolve_service_user`,
`resolve_claude_bin` (приоритет кандидатов и fallback), `write_secret`,
бэкфилл прав, отсутствие `/root` и `os.getlogin` в исходниках.

## 2026-07-23 — Лимит сессии: кнопки вместо перезапуска

При достижении лимита сессии Claude бот больше НЕ перезапускается. Теперь
приходит алерт с двумя инлайн-кнопками:

- «✅ Понял» (`limit:ack`) — просто снимает клавиатуру.
- «▶️ Продолжить работу» (`limit:resume`) — шлёт Claude промпт
  «Continue from where you left off.» после сброса лимита.

Что изменилось:

- `classify_pty_alert` в `install.py` теперь возвращает 5 значений — добавлен
  `session_limit_reset` (строка времени сброса или `""` для живого баннера,
  иначе `None`). `kill_after_alert = False` для лимита сессии.
- Живым баннером лимита считается буфер, где ОДНОВРЕМЕННО есть
  `hit your session limit`, `resets` и один из `/upgrade` / `usage-credits`
  / `/usage` (плюс прежняя проверка `is_fresh`). Это отсекает ложное
  срабатывание от эхо агент-ошибки
  «Agent terminated early due to an API error: You've hit your session limit».
- Время сброса парсится узким regex
  `resets\s+(\d{1,2}:\d{2}\s*[ap]m(?:\s*\([^)]*\))?)` — без жадности,
  `/upgrade` в строку не попадает.
- В `main()` добавлен дедуп по сигнатуре сброса (файл
  `~/.claude/channels/telegram/last_session_limit`): повторный тот же баннер
  не пересылается.
- В `server.py` добавлены колбэки `limit:ack` и `limit:resume`.
- Тесты `tests/test_alert_classification.py` и `tests/test_limit_matching.py`
  обновлены под новую арность и новые кейсы.

Не трогалось: billing / weekly / 5-hour / auth(401) / rate-limit / overloaded,
форк-сессии, ветка «no conversation found», прочие колбэки.

## 2026-09-22 — editable model list
- Модели теперь читаются из ~/.claude/channels/telegram/models.json в рантайме поверх встроенного MODELS. Новые функции load_custom_models/save_custom_models/get_models; все обращения к MODELS в /model, коллбэке выбора и /effort заменены на get_models().
- Управление кнопками в /model: «➕ Добавить модель» (mdl:add → просит `model-id Название` след. сообщением, захват в on_text через pending_model_add), «🗑 Удалить модель» (mdl:manage → mdl:del:<alias>), только владелец. Добавление/удаление БЕЗ рестарта; рестарт только для применения активной модели.
- Засеяны Fable 5.1 (claude-fable-5-1) и Opus 4.7 (claude-opus-4-7). Тесты tests/test_model_editing.py (5). pytest: +5 passed (4 pre-existing usage-locale фейла не связаны).
