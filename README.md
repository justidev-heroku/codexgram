# codex-gram

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

English: [README.EN.md](README.EN.md)

## Лицензия

MIT · [@justidev](https://t.me/justidev)
