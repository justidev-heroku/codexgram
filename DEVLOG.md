# DEVLOG

## 2026-10-25 — codex-gram, этап 2: команды, прогресс, подтверждения, отказ от Claude-машинерии

Этап 2 убирает Claude-специфику из рантайма и добавляет команды Codex поверх моста из этапа 1.

**Удалено (Claude-only).** Инсталлер `install.py/.sh/.ps1`, `.claude-plugin/`, `.mcp.json`,
`skills/`. Все Claude-пути в server.py: auto_update_loop, usage_watch_loop, profile sync и
keepalive (OAuth-профили, которые могли ротировать токены живого Claude-бота), PTY-лаунчер
/login и /switch_account, чтение `~/.claude` (сессии, settings.json, .credentials.json),
модели и effort через PTY, Claude-ветка /goal (оценка через API), safeguard и limit-кнопки,
permission relay MCP. Тесты, которые покрывали только это: test_alert_classification,
test_install_env, test_install_fork_session, test_live_switch, test_limit_matching,
test_model_editing, test_profile_sync, test_safeguard, test_thinking, test_new_features.

**Оставлено, но не работает на codex-gram до этапа 3.** Гостевой runtime (инстансы, inbox.sock,
systemd-юниты гостей, managed bots): гостевые апдейты отвечают заглушкой, их сообщения не
попадают в сессию владельца. Тесты `test_guest_instance.py` и `test_managed_bots.py` помечены
`skip` с причиной «stage 3». `test_install_guest.py` тоже помечен `skip`. `/share`, `/unshare`,
`/access` работают как раньше (только access.json). `/mybot` отвечает заглушкой.

**Команды (владелец):**
- `/new` — `thread/start`. `/close` — `thread/archive` текущего треда и новый тред.
- `/resume` — `thread/list` (10 последних, с давностью), кнопка → `thread/resume`.
- `/model`, `/effort` — `model/list`; выбор в `STATE_DIR/codex_settings.json`, применяется в
  `turn/start` со следующего хода. Effort — только из `supportedReasoningEfforts` модели.
- `/stop` — `turn/interrupt`. `/goal` — `thread/goal/get|set|clear`.
- `/usage` — `account/rateLimits/read`, полоски 5ч и недельного окна, сброс в МСК и относительно.
  Фоновый алерт на `account/rateLimits/updated`: 80% и 100% один раз на окно (ключи дедупа в
  `STATE_DIR/usage_alerts.json`, переживают рестарт).
- `/login` — `codex login --device-auth` с `CODEX_HOME` аккаунта, URL и код из вывода (ANSI
  вычищается), ожидание до 15 минут, по успеху — перезапуск app-server. `/logout` — `codex logout`
  и перезапуск.
- `/accounts`, `/save_account <имя>`, `/switch_account`, `/delete_account` — копии `auth.json` в
  `STATE_DIR/accounts/<имя>/` (0600). Имя: `^[A-Za-z0-9_-]{1,32}$` и проверка пути после resolve.
- `/auto` — переключает approvalPolicy `never` ↔ `on-request`. В on-request запросы
  CommandExecution/FileChange/Permissions приходят с кнопками «Разрешить/Отклонить», без ответа
  за 10 минут — отказ (`codex_approvals.py`).
- `/check_update` — `codex --version` против `npm view @openai/codex version`; кнопка →
  `npm i -g @openai/codex@latest` и перезапуск.
- `/restart` — перезапуск дочернего app-server (сервис не трогается).

**Прогресс и ходы.** Пузырь «🤖 Готовлю ответ…» собирается из `item/started|completed`
(команды, правки файлов, MCP-вызовы, поиск), последние 5 действий с ⏳/✅/❌. Пузырь снимается
при успешном вызове `reply`/`reply_file` и при `turn/completed`; есть предохранитель 15 минут.
Если ход завершился `failed` или `interrupted` либо `completed` без вызова reply, владелец
получает предупреждение с кнопками «Повторить» и «Сменить модель». Остановка через /stop
отмечается коротко, без кнопок.

**Уведомления.** `warning`, `deprecationNotice`, `model/rerouted` и ошибки без хода — владельцу,
не чаще раза в 30 с, одинаковый текст не чаще раза в 10 минут (`Throttle`).

**Инфраструктура.** Лог перенесён из `/tmp/bot_debug.log` (общий с живым Claude-ботом) в
`STATE_DIR/bot.log`. Лог MCP-отладки — `STATE_DIR/mcp_debug.log`. CODEX_HOME для дочернего
процесса задаётся `CODEX_GRAM_CODEX_HOME` (тесты используют временные каталоги и не трогают
`~/.codex`). Сообщения не от владельца в сессию не передаются.

**Деплой.** `deploy/codex-telegram.service` (Type=simple, Restart=always, KillMode=control-group),
`deploy/init_access.py` (создаёт access.json с владельцем, 0600), `deploy/README.md`.

**Тесты.** Новые: `test_codex_stage2.py` (настройки, аккаунты, форматирование, пузырь, ходы,
троттлинг, алерты, брокер), `test_codex_bridge_stage2.py` (on-request, restart, resume на фейке),
`test_codex_server_events.py` (события → пузырь/вердикты/уведомления, гости, инбаунд),
`test_codex_sendable.py` (секреты не отправляются через reply_file), `test_init_access.py`.

**Не сделано / риски.**
- Сохранённые копии `auth.json` могут протухнуть: refresh-токены одноразовые, после обновления одной
  копией другая становится недействительной. Перед переключением делай `/save_account`.
- Гости и свои боты — этап 3. Персональный `~/.codex` при `/switch_account` перезаписывается.
- `scripts/smoke_bridge.py` не запускался с живым аккаунтом (нужен реальный codex и вход).
- Инструкции треда по-прежнему читают `codex_workflow.md` и `CODEX_GRAM_MEMORY_DIR`
  (по умолчанию каталог памяти владельца в `~/.claude`: только чтение).

## 2026-10-25 — codex-gram, этап 1: мост к Codex CLI вместо Claude Code

Форк claude-gram (`/root/claude-gram-v2`) на OpenAI Codex CLI 0.164. Репозиторий
`/root/codex-gram`, remote удалён, пушей нет. Этап 1 — только транспорт; /model,
прогресс-пузырь и гостевые экземпляры переделываются на следующих этапах.

- Новый модуль `codex_bridge.py` (`CodexBridge`): поднимает `codex app-server`
  (JSON-RPC 2.0 по stdio), делает handshake, хранит id активного треда в
  `STATE_DIR/active_session_id` и возобновляет его через `thread/resume`. Если тред
  сервер не принял, стартует новый. Сообщения уходят `turn/start`. Если ход уже идёт,
  используется `turn/steer`. При ошибке steer сообщение ждёт `turn/completed`.
  Server-запросы (approvals и т.п.) получают ответ сразу, без зависаний. Упавший
  процесс перезапускается с backoff, тред возобновляется.
- `server.py`: в фоновом режиме входящие Telegram-сообщения оборачиваются в тег
  `<channel ...>` (как в Claude Channels) и уходят в мост. Картинки передаются как
  `localImage`. `server.py --mcp` — MCP-стаб, который поднимает сам codex. Он
  принудительно не поллит Telegram, иначе будет 409. Дефолтный STATE_DIR теперь
  `~/.codex-gram/telegram`.
- `codex_instructions.md` — developerInstructions для треда: ответ пользователю
  только через `reply`, `chat_id` из тега, format=html, язык пользователя.
- Инструкции треда собирает `codex_instructions.py`: правила моста + `codex_workflow.md`
  (регламент владельца, адаптированный под Codex) + индекс памяти `MEMORY.md` (до 60 КБ).
  Собирается при каждом `thread/start`, каталог памяти — `CODEX_GRAM_MEMORY_DIR`.
- Токен бота в MCP-конфиг треда не кладётся: конфиг может осесть в логах сессий codex.
- Тесты: `tests/test_codex_bridge.py` (фейковый app-server), `tests/test_codex_gram_wiring.py`.
  `scripts/smoke_bridge.py` — прогон через настоящий `codex app-server` с MCP-стабом.
- Известные хвосты этапа 2: Claude-специфичные пути в server.py (`.claude/channels`
  для active_session_id в ресьюме, usage/profile-циклы, /model, /login), гостевые
  экземпляры и часть тестов старого Claude-лаунчера пока не адаптированы.

## 2026-10-21 — Свой бот у гостя (Telegram Managed Bots)

- Мастер (@ClaudeCodeExeBot, `can_manage_bots`) при /share и по /mybot шлёт гостю
  reply-кнопку `request_managed_bot`. Гость создаёт своего бота в 2 тапа.
- Апдейт `managed_bot` ловит `dp.update.outer_middleware` (`_managed_bot_mw`), потому что
  aiogram 3.26 этого типа не знает (поле лежит в `model_extra`). Токен забирается
  сырым `getManagedBotToken` и пишется в `guests/<uid>/bot.env` (0600). Затем
  `systemctl restart claude-telegram-guest@<uid>`. Мастер поллит с
  `allowed_updates` + `managed_bot`. aiogram НЕ обновляли: системный пакет общий с другими ботами.
- Лаунчер: если в `bot.env` есть токен, гость стартует на нём с
  `CLAUDE_GRAM_GUEST_OWN_BOT=1`. Такой экземпляр сам поллит свой бот (inbox.sock не
  поднимает) и ставит себе меню. Без `bot.env` всё работает как раньше, через общий бот.
- Гостю со своим ботом общий бот отвечает «пиши в @свой_бот» (не чаще раза в минуту),
  а /mybot там предлагает пересоздать бота. `managed_bot_created` мастер гасит сам и не
  пересылает в сокет перезапускающегося экземпляра. /unshare удаляет `bot.env`.
- Тесты: `tests/test_managed_bots.py`, `tests/test_install_guest.py`. 287 passed,
  4 старых падения /usage.

## 2026-10-21 — Гость = полноценный отдельный экземпляр Claude

- Headless-раннер (`claude -p` на каждое сообщение) удалён целиком. Каждый гость
  теперь — постоянный `claude --channels plugin:claude-gram` под лаунчером
  `claude-telegram-bot --guest <uid>` в systemd-юните `claude-telegram-guest@<uid>`.
- Telegram по-прежнему поллит только мастер (владельческий `server.py`). Апдейты
  гостей (сообщения, кнопки, реакции) outer-middleware на `dp.update` пересылает
  сырым JSON в `guests/<uid>/inbox.sock`; гостевой `server.py` (`CLAUDE_GRAM_GUEST_UID`)
  не поллит, а слушает сокет и делает `dp.feed_raw_update`. Если экземпляр не поднят —
  мастер будит юнит, сообщения буферизуются по порядку (до 120 с).
- В экземпляре гость — «владелец» своего `access.json`, поэтому дизайн и команды
  те же, что у владельца. Заблокированы: /share /unshare /access /check_update
  /mcp_debug и все аккаунтные (/login /logout /accounts /save_account /switch_account
  /delete_account) — аккаунт общий.
- Модель/effort гостя — в `guests/<uid>/settings.json` (`CLAUDE_GRAM_SETTINGS_FILE`,
  `--settings`), глобальный settings.json владельца не трогается. Сессии — в
  `~/.claude/projects/<slug ~/guests/<uid>>/` (`project_sessions_dir()` вместо `-root`).
- Миграция: старые файлы `guests/<uid>/{session_id,model,effort,auto,goal}` переносятся
  в settings.json/session_goal.txt и удаляются; сессия гостя сбрасывается.
- `install.py`: шаблон лаунчера вынесен в `WRAPPER_TEMPLATE` + `render_wrapper()`,
  все пути состояния от `STATE_DIR`, `ensure_trusted()` для рабочего каталога,
  генерация `claude-telegram-guest@.service` (`render_guest_unit()`).
- Тесты: `tests/test_guest_instance.py`, `tests/test_install_guest.py`; старые
  `test_guest_runner.py`/`test_guest_commands.py` удалены.

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
