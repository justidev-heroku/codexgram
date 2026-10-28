## codex-gram

codex-gram — форк claude-gram, который управляет не Claude Code, а **OpenAI Codex CLI** (0.164+) через `codex app-server` (JSON-RPC по stdio). Telegram-интерфейс тот же: сообщения владельца уходят в тред Codex, ответы возвращаются через инструмент `reply`.

Отличия от claude-gram:
- Нет PTY-лаунчера и сессий `~/.claude`: тред ведётся через `thread/start` / `thread/resume`, состояние в `STATE_DIR` (по умолчанию `~/.codex-gram/telegram`).
- Аккаунты Codex: `/login` (device code), `/logout`, `/accounts`, `/save_account`, `/switch_account` — копии `auth.json` в `STATE_DIR/accounts`.
- Модель, effort и режим подтверждений (`/auto`) меняются из Telegram и действуют со следующего хода, без перезапуска.
- Лимиты `/usage` из `account/rateLimits`, алерты на 80% и 100%.
- Пузырь прогресса с последними действиями (команды, правки, MCP-вызовы).
- Проверка «ход завершился без ответа» с кнопками «Повторить» и «Сменить модель».
- Гости: `/share` поднимает гостю отдельный экземпляр (`codex-telegram-guest@<uid>.service`) со своим `codex app-server` и тредом, `/unshare` гасит. Аккаунт Codex у гостя по умолчанию общий (владельца), после `/login` гостя — свой в `guests/<uid>/codex`. `/mybot` — свой бот гостя через Managed Bots.
- Установка и запуск: [deploy/README.md](deploy/README.md).

---

# Claude-Gram v2 — RU

[![Telegram](https://img.shields.io/badge/Telegram-@justidev-2CA5E0?logo=telegram&logoColor=white)](https://t.me/justidev)

![Claude-Gram v2 Banner](https://raw.githubusercontent.com/justidev-heroku/claude-gram-v2/main/assets/claude_gram_v2.png)

**Claude-Gram v2** – мост Telegram-канала для [Claude Code](https://claude.ai/code), позволяющий отправлять и получать тексты, форматированный код, файлы, логи и фотографии прямо из контекста ИИ-агента.

> [!NOTE]
> Этот проект является форком оригинального репозитория [claude-gram от @ripcats](https://github.com/ripcats/ripcats-marketplace/tree/main/claude-gram).

Актуальная версия: **v2.0.0**.

---

## Установка

### Вариант А — Claude Code Marketplace (рекомендуется)

Добавьте маркетплейс и установите плагин прямо из Claude Code:

```shell
/plugin marketplace add justidev-heroku/claude-gram-v2
/plugin install claude-gram@justidev-marketplace
```

Затем настройте токен бота:

```shell
/claude-gram:init
```

---

### Вариант Б — Ручная установка (клонирование + скрипт)

#### 1. Клонирование репозитория
```bash
git clone https://github.com/justidev-heroku/claude-gram-v2.git
cd claude-gram-v2
```

#### 2. Запуск установщика
Скрипт автоматически проверит Python, установит необходимые зависимости (включая `aiogram`, `playwright`, `curl_cffi`) и настроит фоновую службу автозапуска.

##### Linux / macOS:
```bash
chmod +x install.sh
./install.sh
```

##### Windows (запустите PowerShell от Администратора):
```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
.\install.ps1
```

---

## Основные возможности

- **Маскот «Клодграмик»** – милый пиксельный персонаж приветствует вас при установке.
- **Градиентные баннеры** – красивое приветствие при запуске, которое само подстраивается под ширину вашего терминала.
- **Автообновление из Git** – бот раз в 15 минут проверяет обновления, сам скачивает их и присылает в Telegram список изменений.
- **Защита от зависаний** – бот корректно завершает работу и больше не зависает при перезапуске службы.
- **Несколько аккаунтов** – можно легко добавлять, бэкапить и переключать профили прямо из чата.
- **Смена моделей и лимитов** – переключайте модели (Sonnet/Opus/Haiku) и уровень рассуждений (Effort) кнопками в Telegram.
- **HTML-верстка сообщений** – весь текст сохраняет форматирование Telegram (жирный, курсив, цитаты, код).


---

## Команды бота

| Команда | Описание |
|---|---|
| `/start` | Приветствие и первичная привязка Telegram ID владельца |
| `/accounts` | Список всех сохраненных профилей и отметка активного |
| `/login <имя>` | Запуск интерактивной OAuth-авторизации нового профиля |
| `/save_account` | Резервное копирование учетных данных активного профиля |
| `/switch_account <имя>` | Переключение на другой сохраненный профиль с перезапуском |
| `/delete_account <имя>` | Полное удаление файла профиля с сервера |
| `/model` | Быстрая смена модели кнопками инлайн-клавиатуры |
| `/effort` | Смена уровня рассуждений (low, medium, high) |
| `/usage` | Красивая статистика лимитов использования API и счетов |
| `/resume` | Выбор и возобновление любой из прошлых сессий Claude Code |
| `/auto` | Включение/выключение автоматического подтверждения прав |
| `/close` | Завершение сессии, очистка логов и закрытие форум-топика |

---

## Инструменты агента (MCP-сервер)

| Инструмент | Описание |
|---|---|
| `reply` | Отправить текстовый ответ в Telegram (HTML разметка) |
| `reply_file` | Отправить один файл или альбом (логи, скрипты, фото) |
| `reactions` | Автоматическая/ручная emoji-реакция на сообщения |
| `rename_thread` | Переименование форум-топика текущей сессии |
| `edit_message` | Изменение ранее отправленного ботом сообщения |
| `get_history` | Получение локальной истории активного треда |

---

## Конфигурация (access.json)

| Параметр | Описание |
|---|---|
| `allowFrom` | Список разрешенных Telegram ID |
| `ackReaction` | Дефолтная emoji-реакция на входящие сообщения |
| `tz` | Часовой пояс для форум-топиков (например `Europe/Moscow`) |
| `threads` | Флаг включения/отключения форум-топиков (`true`/`false`) |

---

## Переключение языка

- [English docs](README.EN.md)

---

## Лицензия

MIT · [@justidev](https://t.me/justidev)
