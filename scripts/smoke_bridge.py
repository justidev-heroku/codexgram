"""Смоук-тест CodexBridge против НАСТОЯЩЕГО `codex app-server` (без Telegram).

Поднимает мост с MCP-стабом tests/fixtures/fake_mcp.py вместо реального telegram-сервера,
отправляет одно сообщение в формате <channel>, ждёт turn/completed и проверяет,
что модель вызвала инструмент `reply` стаба.

Использование:
    python3 scripts/smoke_bridge.py --base /path/to/scratch [--timeout 240]

--base — каталог для временного state dir и лога стаба (по умолчанию системный temp).
Боевой ~/.codex-gram не используется.
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from codex_bridge import CodexBridge  # noqa: E402

FAKE_MCP = REPO / "tests" / "fixtures" / "fake_mcp.py"
from codex_instructions import build_developer_instructions, memory_dir_from_env  # noqa: E402
PROMPT = (
    '<channel source="telegram" chat_id="111" message_id="5" user="smoke" user_id="1" ts="2026-10-25T00:00:00+00:00">'
    "Smoke test: ответь одним словом pong через инструмент reply (chat_id=111)."
    "</channel>"
)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None, help="каталог для временных файлов (scratchpad)")
    ap.add_argument("--timeout", type=float, default=240.0)
    args = ap.parse_args()

    base = Path(args.base) if args.base else Path(tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="codex-gram-smoke-", dir=str(base)))
    state = work / "state"
    mcp_log = work / "mcp_calls.log"
    print(f"work dir: {work}")

    logs: list[str] = []
    mcp_servers = {
        "telegram": {
            "command": sys.executable,
            "args": [str(FAKE_MCP), str(mcp_log)],
            "env": {"CLAUDE_TELEGRAM_BACKGROUND": "0", "PATH": os.environ.get("PATH", "")},
        }
    }
    bridge = CodexBridge(
        state_dir=state,
        cwd=str(work),
        instructions=lambda: build_developer_instructions(REPO, memory_dir_from_env()),
        mcp_servers=mcp_servers,
        log=lambda m: (logs.append(m), print(f"[bridge] {m}", flush=True)),
    )
    done = asyncio.Event()
    methods: list[str] = []

    def on_notify(method, params):
        methods.append(method)
        if method == "turn/completed":
            done.set()

    bridge.add_listener(on_notify)

    t0 = time.time()
    ok = await bridge.start(wait_ready=True, timeout=120)
    print(f"ready={ok} thread={bridge.thread_id} ({time.time() - t0:.1f}s)", flush=True)
    if not ok:
        await bridge.stop()
        return 2

    status = await bridge.submit(PROMPT)
    print(f"submit -> {status}", flush=True)
    try:
        await asyncio.wait_for(done.wait(), args.timeout)
        turn_ok = True
    except asyncio.TimeoutError:
        turn_ok = False
    await bridge.stop()

    calls = []
    if mcp_log.exists():
        calls = [json.loads(x) for x in mcp_log.read_text("utf-8").splitlines() if x.strip()]
    reply_calls = [c for c in calls if c.get("name") == "reply"]
    print(f"turn/completed received: {turn_ok} (after {time.time() - t0:.1f}s)")
    print(f"notifications seen: {sorted(set(methods))}")
    print(f"mcp tools/call total: {len(calls)}, reply: {len(reply_calls)}")
    for c in reply_calls:
        print("reply args:", json.dumps(c.get("arguments"), ensure_ascii=False))
    print(f"stored thread id: {(state / 'active_session_id').read_text().strip() if (state / 'active_session_id').exists() else None}")

    passed = turn_ok and bool(reply_calls)
    print("SMOKE", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
