"""Мост этапа 2: режим on-request, restart_child, resume_thread, approval_handler — на фейковом app-server."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_codex_bridge import Env, run, wait_for  # noqa: E402
from codex_bridge import BridgeError  # noqa: E402
from codex_settings import update_settings  # noqa: E402


def _replies(env):
    return [r for r in env.records() if r.get("kind") == "server_reply"]


def test_on_request_mode_asks_owner_and_answers_decline(tmp_path):
    env = Env(tmp_path)
    update_settings(env.state_dir, auto="on-request")
    asked = []

    async def handler(method, params):
        asked.append(method)
        return False

    async def scenario():
        env.bridge.set_approval_handler(handler)
        await env.bridge.start(timeout=10)
        await env.bridge.submit("[approval] run ls")
        await wait_for(lambda: len(_replies(env)) == 1, timeout=10)
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    assert asked == ["item/commandExecution/requestApproval"]
    assert json.dumps(_replies(env)[0]).find("decline") != -1


def test_on_request_mode_allows_when_owner_allows(tmp_path):
    env = Env(tmp_path)
    update_settings(env.state_dir, auto="on-request")

    async def handler(method, params):
        return True

    async def scenario():
        env.bridge.set_approval_handler(handler)
        await env.bridge.start(timeout=10)
        await env.bridge.submit("[approval] run ls")
        await wait_for(lambda: len(_replies(env)) == 1, timeout=10)
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    assert "accept" in json.dumps(_replies(env)[0])


def test_on_request_without_handler_denies(tmp_path):
    env = Env(tmp_path)
    update_settings(env.state_dir, auto="on-request")

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.submit("[approval] run ls")
        await wait_for(lambda: len(_replies(env)) == 1, timeout=10)
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    assert "decline" in json.dumps(_replies(env)[0])


def test_on_request_handler_exception_denies(tmp_path):
    env = Env(tmp_path)
    update_settings(env.state_dir, auto="on-request")

    async def handler(method, params):
        raise RuntimeError("boom")

    async def scenario():
        env.bridge.set_approval_handler(handler)
        await env.bridge.start(timeout=10)
        await env.bridge.submit("[approval] run ls")
        await wait_for(lambda: len(_replies(env)) == 1, timeout=10)
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    assert "decline" in json.dumps(_replies(env)[0])


def test_never_mode_does_not_consult_handler(tmp_path):
    env = Env(tmp_path)
    called = []

    async def handler(method, params):
        called.append(method)
        return False

    async def scenario():
        env.bridge.set_approval_handler(handler)
        await env.bridge.start(timeout=10)
        await env.bridge.submit("[approval] run ls")
        await wait_for(lambda: len(_replies(env)) == 1, timeout=10)
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    assert called == []
    assert "accept" in json.dumps(_replies(env)[0])


def test_turn_start_carries_approval_policy_from_settings(tmp_path):
    env = Env(tmp_path)
    update_settings(env.state_dir, auto="on-request", model="m-x", effort="high")

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.submit("hello")
        await wait_for(lambda: env.bridge.active_turn_id is None)
        await env.bridge.stop()

    run(scenario())
    starts = env.requests("turn/start")
    assert starts
    params = starts[-1]["params"]
    assert params["approvalPolicy"] == "on-request"
    assert params["model"] == "m-x"
    assert params["effort"] == "high"


def test_restart_child_resumes_same_thread(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        first = env.bridge.thread_id
        ok = await env.bridge.restart_child(wait_timeout=10)
        assert ok is True
        assert env.bridge.thread_id == first
        await env.bridge.stop()

    run(scenario())


def test_resume_refused_while_turn_running(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.submit("[slow] long")
        await wait_for(lambda: env.bridge.active_turn_id is not None)
        with pytest.raises(BridgeError):
            await env.bridge.resume_thread("thr-1")
        await wait_for(lambda: env.bridge.active_turn_id is None, timeout=10)
        await env.bridge.stop()

    run(scenario())


def test_resume_rejects_empty_id(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        for bad in ("", None):
            with pytest.raises(BridgeError):
                await env.bridge.resume_thread(bad)
        await env.bridge.stop()

    run(scenario())


def test_methods_refuse_when_not_ready(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        with pytest.raises(BridgeError):
            await env.bridge.list_models()
        with pytest.raises(BridgeError):
            await env.bridge.rate_limits()
        with pytest.raises(BridgeError):
            await env.bridge.goal_get()

    run(scenario())


# ---------------------------------------------------------------------------
# Этап 3: окружение дочернего процесса, вычисляемые mcp_servers, thread/list cwd, thread/read
# ---------------------------------------------------------------------------


def test_env_factory_is_called_on_every_spawn(tmp_path):
    env = Env(tmp_path)
    homes = iter([str(tmp_path / "home-a"), str(tmp_path / "home-b")])
    env.bridge.env_factory = lambda: {**__import__("os").environ, "CODEX_HOME": next(homes)}

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.restart_child()
        await env.bridge.stop()

    run(scenario())
    seen = [r["CODEX_HOME"] for r in env.records() if r.get("kind") == "env"]
    assert seen == [str(tmp_path / "home-a"), str(tmp_path / "home-b")]


def test_callable_mcp_servers_are_evaluated_per_thread_request(tmp_path):
    env = Env(tmp_path)
    calls = []

    def servers():
        calls.append(1)
        return {"telegram": {"command": "x", "args": [], "env": {"N": str(len(calls))}}}

    env.bridge.mcp_servers = servers

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.new_thread()
        await env.bridge.stop()

    run(scenario())
    starts = env.requests("thread/start")
    assert [s["params"]["config"]["mcp_servers"]["telegram"]["env"]["N"] for s in starts] == ["1", "2"]


def test_list_threads_passes_cwd_filter_only_when_given(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        await env.bridge.list_threads(limit=5)
        await env.bridge.list_threads(limit=5, cwd="/root/guests-codex/1")
        await env.bridge.stop()

    run(scenario())
    lists = [r["params"] for r in env.requests("thread/list")]
    assert "cwd" not in lists[0]
    assert lists[1]["cwd"] == "/root/guests-codex/1"


def test_read_thread_returns_metadata_and_raises_on_unknown(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.bridge.start(timeout=10)
        meta = await env.bridge.read_thread(env.bridge.thread_id)
        with pytest.raises(BridgeError):
            await env.bridge.read_thread("nope")
        await env.bridge.stop()
        return meta

    meta = run(scenario())
    assert meta["id"].startswith("thr-")
    req = env.requests("thread/read")[0]["params"]
    assert req["includeTurns"] is False
