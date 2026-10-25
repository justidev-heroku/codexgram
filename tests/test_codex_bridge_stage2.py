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
