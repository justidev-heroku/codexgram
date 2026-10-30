# codex-gram

codex-gram is a fork of claude-gram that drives **OpenAI Codex CLI** (0.164+) instead of Claude Code, via `codex app-server` (JSON-RPC over stdio). The Telegram interface is the same: the owner's messages go into a Codex thread, replies come back through the `reply` tool.

Differences from claude-gram:
- No PTY launcher and no `~/.claude` sessions: the thread is driven by `thread/start` / `thread/resume`, state lives in `STATE_DIR` (default `~/.codex-gram/telegram`).
- Codex accounts: `/login` (device code), `/logout`, `/accounts`, `/save_account`, `/switch_account` — copies of `auth.json` in `STATE_DIR/accounts`.
- Model, effort and approval mode (`/auto`) are changed from Telegram and apply from the next turn, no restart.
- `/usage` limits from `account/rateLimits`, alerts at 80% and 100%.
- Progress bubble with the latest actions (commands, edits, MCP calls).
- "Turn ended without a reply" check with "Retry" and "Change model" buttons.
- Guests: `/share` starts a separate instance for the guest (`codex-telegram-guest@<uid>.service`) with its own `codex app-server` and thread, `/unshare` stops it. The guest uses the owner's Codex account by default, or their own in `guests/<uid>/codex` after `/login`. `/mybot` — the guest's own bot via Managed Bots.
- Install and run: [deploy/README.md](deploy/README.md).

Русский: [README.md](README.md)

## License

MIT · [@justidev](https://t.me/justidev)
