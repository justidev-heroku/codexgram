"""Тесты CodexBridge против фейкового app-server (tests/fixtures/fake_app_server.py).

Реальный `codex` здесь не запускается: см. scripts/smoke_bridge.py.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from codex_bridge import CodexBridge, RPCError, build_input

FIXTURES = Path(__file__).parent / "fixtures"
FAKE = FIXTURES / "fake_app_server.py"
MCP_SERVERS = {
    "telegram": {
        "command": sys.executable,
        "args": ["/nonexistent/server.py", "--mcp"],
        "env": {"CLAUDE_TELEGRAM_BACKGROUND": "0"},
    }
}
INSTR = "INSTRUCTIONS-MARKER"


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


class Env:
    """Временный state dir + файлы фейка + bridge с короткими backoff."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        self.state_dir = tmp_path / "state"
        self.fake_state = tmp_path / "fake_state.json"
        self.fake_log = tmp_path / "fake.jsonl"
        self.logs: list[str] = []
        self.bridge = self.make_bridge()

    def make_bridge(self) -> CodexBridge:
        return CodexBridge(
            state_dir=self.state_dir,
            cwd=str(self.dir),
            instructions=INSTR,
            mcp_servers=MCP_SERVERS,
            cmd=[sys.executable, str(FAKE), str(self.fake_state), str(self.fake_log)],
            log=self.logs.append,
            restart_backoff=(0.05, 0.2),
            request_timeout=10,
        )

    def records(self):
        if not self.fake_log.exists():
            return []
        return [json.loads(x) for x in self.fake_log.read_text("utf-8").splitlines() if x.strip()]

    def requests(self, method=None):
        out = [r for r in self.records() if r.get("kind") == "request"]
        if method is not None:
            out = [r for r in out if r.get("method") == method]
        return out

    def stored_id(self):
        try:
            return (self.state_dir / "active_session_id").read_text("utf-8").strip()
        except OSError:
            return None


async def wait_for(pred, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached in time")


# ---------------------------------------------------------------------------
# Чистые функции
# ---------------------------------------------------------------------------


def test_build_input_text_and_images():
    items = build_input("hi", ["/a/b.png", "", "/c.jpg"])
    assert items == [
        {"type": "text", "text": "hi"},
        {"type": "localImage", "path": "/a/b.png"},
        {"type": "localImage", "path": "/c.jpg"},
    ]


def test_build_input_empty():
    assert build_input("", []) == []


def test_server_request_unknown_method_has_no_answer():
    assert CodexBridge._server_request_result("some/unknown", {}) is None


def test_server_request_approval_accepts():
    res = CodexBridge._server_request_result("item/commandExecution/requestApproval", {})
    assert res == {"decision": "accept"}


# ---------------------------------------------------------------------------
# Handshake и тред
# ---------------------------------------------------------------------------


def test_handshake_and_thread_start_params(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        assert await env.bridge.start(timeout=10) is True
        await env.bridge.stop()

    run(scenario())
    methods = [r.get("method") for r in env.records() if r.get("kind") == "request"]
    assert methods[:3] == ["initialize", "initialized", "thread/start"]
    start = env.requests("thread/start")[0]["params"]
    assert start["cwd"] == str(tmp_path)
    assert start["approvalPolicy"] == "never"
    assert start["sandbox"] == "danger-full-access"
    assert start["developerInstructions"] == INSTR
    assert start["config"] == {"mcp_servers": MCP_SERVERS}


def test_thread_start_persists_id(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        tid = env.bridge.thread_id
        await env.bridge.stop()
        return tid

    tid = run(scenario())
    assert tid == "thr-1"
    assert env.stored_id() == "thr-1"


def test_resume_uses_stored_id(tmp_path):
    env = Env(tmp_path)
    env.fake_state.write_text(json.dumps({"threads": ["thr-7"], "counter": 7}))
    env.state_dir.mkdir(parents=True)
    (env.state_dir / "active_session_id").write_text("thr-7\n")

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.stop()

    run(scenario())
    assert env.requests("thread/start") == []
    resume = env.requests("thread/resume")
    assert len(resume) == 1 and resume[0]["params"]["threadId"] == "thr-7"
    assert resume[0]["params"]["config"] == {"mcp_servers": MCP_SERVERS}
    assert env.stored_id() == "thr-7"


def test_resume_rejected_falls_back_to_new_thread(tmp_path):
    env = Env(tmp_path)
    env.state_dir.mkdir(parents=True)
    (env.state_dir / "active_session_id").write_text("thr-gone")

    async def scenario():
        await env.bridge.start(timeout=10)
        tid = env.bridge.thread_id
        await env.bridge.stop()
        return tid

    tid = run(scenario())
    assert len(env.requests("thread/resume")) == 1
    assert len(env.requests("thread/start")) == 1
    assert tid == "thr-1"
    assert env.stored_id() == "thr-1"


def test_new_sentinel_starts_fresh_thread(tmp_path):
    env = Env(tmp_path)
    env.state_dir.mkdir(parents=True)
    (env.state_dir / "active_session_id").write_text("new")

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.stop()

    run(scenario())
    assert env.requests("thread/resume") == []
    assert len(env.requests("thread/start")) == 1


def test_new_thread_switches_active_id(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        tid = await env.bridge.new_thread()
        await env.bridge.stop()
        return tid

    tid = run(scenario())
    assert tid == "thr-2"
    assert env.stored_id() == "thr-2"


# ---------------------------------------------------------------------------
# Ходы: turn/start, turn/steer, очередь
# ---------------------------------------------------------------------------


def test_submit_idle_starts_turn(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        status = await env.bridge.submit("hello")
        await wait_for(lambda: env.bridge.active_turn_id is None and env.requests("turn/start"))
        await env.bridge.stop()
        return status

    assert run(scenario()) == "started"
    starts = env.requests("turn/start")
    assert len(starts) == 1
    assert starts[0]["params"]["threadId"] == "thr-1"
    assert starts[0]["params"]["input"] == [{"type": "text", "text": "hello"}]


def test_submit_during_active_turn_steers(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        assert await env.bridge.submit("[slow] first") == "started"
        await wait_for(lambda: env.bridge.active_turn_id == "turn-1")
        assert await env.bridge.submit("second") == "steered"
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    steers = env.requests("turn/steer")
    assert len(steers) == 1
    assert steers[0]["params"]["expectedTurnId"] == "turn-1"
    assert steers[0]["params"]["input"] == [{"type": "text", "text": "second"}]
    assert len(env.requests("turn/start")) == 1


def test_steer_error_queues_until_turn_completed(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        assert await env.bridge.submit("[slow] a") == "started"
        await wait_for(lambda: env.bridge.active_turn_id == "turn-1")
        assert await env.bridge.submit("[nosteer] b") == "queued"
        assert env.bridge.queued_count == 1
        # до конца хода второго turn/start быть не должно
        await asyncio.sleep(0.3)
        assert len(env.requests("turn/start")) == 1
        await wait_for(lambda: len(env.requests("turn/start")) == 2, timeout=10)
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    starts = env.requests("turn/start")
    assert starts[1]["params"]["input"] == [{"type": "text", "text": "[nosteer] b"}]
    assert env.bridge.queued_count == 0


def test_submit_before_ready_is_queued_and_flushed(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        assert await env.bridge.submit("early") == "queued"
        await env.bridge.start(timeout=10)
        await wait_for(lambda: env.requests("turn/start"))
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    starts = env.requests("turn/start")
    assert len(starts) == 1
    assert starts[0]["params"]["input"] == [{"type": "text", "text": "early"}]


def test_empty_submit_is_ignored(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        status = await env.bridge.submit("", [])
        await env.bridge.stop()
        return status

    assert run(scenario()) == "empty"
    assert env.requests("turn/start") == []


def test_huge_and_unicode_text_passes_unchanged(tmp_path):
    env = Env(tmp_path)
    big = ("привет 👋 ‮RTL \"quotes\" <tag> \\ back\n" * 6000)  # ~ 250 KB
    assert len(big) > 200_000
    uni = "Ёж 🦔 \u0000? нет, ​ zero-width"

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.submit(uni)
        await wait_for(lambda: len(env.requests("turn/start")) == 1)
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.submit(big)
        await wait_for(lambda: len(env.requests("turn/start")) == 2)
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    starts = env.requests("turn/start")
    assert starts[0]["params"]["input"][0]["text"] == uni
    assert starts[1]["params"]["input"][0]["text"] == big


def test_images_become_local_image_inputs(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.submit("look", ["/tmp/x.png"])
        await wait_for(lambda: env.requests("turn/start"))
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    assert env.requests("turn/start")[0]["params"]["input"] == [
        {"type": "text", "text": "look"},
        {"type": "localImage", "path": "/tmp/x.png"},
    ]


def test_interrupt_active_turn(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.submit("[slow] long")
        await wait_for(lambda: env.bridge.active_turn_id == "turn-1")
        ok = await env.bridge.interrupt()
        await env.bridge.stop()
        return ok

    assert run(scenario()) is True
    intr = env.requests("turn/interrupt")
    assert intr and intr[0]["params"] == {"threadId": "thr-1", "turnId": "turn-1"}


def test_interrupt_without_turn_returns_false(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        ok = await env.bridge.interrupt()
        await env.bridge.stop()
        return ok

    assert run(scenario()) is False


def test_listeners_receive_turn_notifications(tmp_path):
    env = Env(tmp_path)
    seen: list[str] = []

    async def scenario():
        env.bridge.add_listener(lambda m, p: seen.append(m))
        await env.bridge.start(timeout=10)
        await env.bridge.submit("hi")
        await wait_for(lambda: "turn/completed" in seen)
        await env.bridge.stop()

    run(scenario())
    assert "turn/started" in seen and "turn/completed" in seen


# ---------------------------------------------------------------------------
# Server-requests и падения процесса
# ---------------------------------------------------------------------------


def test_server_request_is_answered(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.submit("[approval] run ls")
        await wait_for(
            lambda: any(r.get("kind") == "server_reply" for r in env.records()), timeout=10
        )
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    replies = [r for r in env.records() if r.get("kind") == "server_reply"]
    assert len(replies) == 1
    assert replies[0]["id"] == 1001
    assert replies[0]["result"] == {"decision": "accept"}
    assert replies[0]["error"] is None


def test_child_crash_restarts_and_resumes_thread(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        tid = env.bridge.thread_id
        await env.bridge.submit("[crash] boom")
        # ждём второй initialize = перезапуск процесса
        await wait_for(lambda: len(env.requests("initialize")) == 2, timeout=15)
        await wait_for(lambda: env.bridge.ready, timeout=15)
        assert env.bridge.thread_id == tid
        # после перезапуска новые ходы работают
        assert await env.bridge.submit("after restart") == "started"
        await wait_for(lambda: len(env.requests("turn/start")) == 2, timeout=15)
        await wait_for(lambda: env.bridge.active_turn_id is None, timeout=15)
        await env.bridge.stop()
        return tid

    tid = run(scenario())
    resumes = env.requests("thread/resume")
    assert len(resumes) == 1 and resumes[0]["params"]["threadId"] == tid
    assert len(env.requests("thread/start")) == 1
    assert env.stored_id() == tid
    assert any("app-server exited" in m for m in env.logs)


def test_stop_terminates_child(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        proc = env.bridge._proc
        await env.bridge.stop()
        return proc

    proc = run(scenario())
    assert proc.returncode is not None
    assert env.bridge.ready is False


def test_bad_command_reports_not_ready(tmp_path):
    env = Env(tmp_path)
    bridge = CodexBridge(
        state_dir=tmp_path / "state",
        cwd=str(tmp_path),
        cmd=["/nonexistent/binary-for-test"],
        log=env.logs.append,
        restart_backoff=(0.05, 0.1),
        request_timeout=2,
    )

    async def scenario():
        ok = await bridge.start(timeout=0.3)
        await bridge.stop()
        return ok

    assert run(scenario()) is False
    assert any("connect failed" in m for m in env.logs)


def test_rpc_error_is_bridge_error_subclass():
    from codex_bridge import BridgeError

    err = RPCError(-32000, "boom")
    assert isinstance(err, BridgeError)
    assert err.code == -32000
