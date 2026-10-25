"""server.py этапа 2: события моста → пузырь прогресса, вердикты ходов, уведомления, гости."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402


@pytest.fixture
def owner_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "ACCESS_FILE", tmp_path / "access.json")
    monkeypatch.setattr(server, "KNOWN_USERS_FILE", tmp_path / "known_users.json")
    monkeypatch.setattr(server, "_known_users_cache", None)
    monkeypatch.setattr(server, "session_thread_id", None)
    (tmp_path / "access.json").write_bytes(orjson.dumps({"allowFrom": ["111"], "owner": "111"}))
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=77))
    bot.edit_message_text = AsyncMock()
    bot.delete_message = AsyncMock()
    monkeypatch.setattr(server, "bot", bot)
    monkeypatch.setattr(server, "progress_bubbles", {})
    monkeypatch.setattr(server, "turn_state", server.TurnTracker())
    monkeypatch.setattr(server, "notice_throttle", server.Throttle(interval=0, same_window=0))
    return bot


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


def test_bubble_created_and_fed_by_command_item(owner_env):
    async def scenario():
        await server.start_thinking("111")
        server.on_codex_event("item/started", {"turnId": "T1", "item": {
            "id": "c1", "type": "commandExecution", "command": "pytest -q", "status": "inProgress"}})
        entry = server.progress_bubbles["111"]
        assert entry["tracker"].actions() == [("⏳", "$ pytest -q")]
        server.stop_thinking("111")
        await _settle()
        assert "111" not in server.progress_bubbles
        owner_env.delete_message.assert_awaited()

    asyncio.run(scenario())


def test_bubble_not_duplicated_during_turn(owner_env):
    async def scenario():
        await server.start_thinking("111")
        await server.start_thinking("111")
        assert owner_env.send_message.await_count == 1
        server.stop_thinking("111")
        await _settle()

    asyncio.run(scenario())


def test_reply_completion_removes_bubble_and_counts_as_reply(owner_env):
    async def scenario():
        await server.start_thinking("111")
        item = {"id": "r1", "type": "mcpToolCall", "server": "telegram", "tool": "reply", "status": "completed"}
        server.on_codex_event("item/completed", {"turnId": "T1", "item": item})
        await _settle()
        assert "111" not in server.progress_bubbles
        assert "T1" in server.turn_state.replied

    asyncio.run(scenario())


def test_failed_reply_call_does_not_count(owner_env):
    async def scenario():
        item = {"id": "r2", "type": "mcpToolCall", "server": "telegram", "tool": "reply", "status": "failed"}
        server.on_codex_event("item/completed", {"turnId": "T2", "item": item})
        await _settle()
        assert "T2" not in server.turn_state.replied

    asyncio.run(scenario())


def test_turn_without_reply_sends_warning_with_retry_buttons(owner_env):
    async def scenario():
        server.turn_state.set_last_user("<channel>hi</channel>")
        server.on_codex_event("turn/completed", {"turn": {"id": "T9", "status": "completed"}})
        await _settle()
        await _settle()
        owner_env.send_message.assert_awaited()
        args, kwargs = owner_env.send_message.call_args
        assert "без ответа" in args[1]
        kb = kwargs["reply_markup"]
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert callbacks == ["turn:retry", "turn:model"]

    asyncio.run(scenario())


def test_turn_completed_with_reply_sends_nothing(owner_env):
    async def scenario():
        server.turn_state.note_reply("T3")
        server.on_codex_event("turn/completed", {"turn": {"id": "T3", "status": "completed"}})
        await _settle()
        owner_env.send_message.assert_not_awaited()

    asyncio.run(scenario())


def test_failed_turn_includes_error_message(owner_env):
    async def scenario():
        server.on_codex_event("turn/completed", {"turn": {
            "id": "T4", "status": "failed", "error": {"message": "usage limit <x>"}}})
        await _settle()
        text = owner_env.send_message.call_args[0][1]
        assert "с ошибкой" in text and "&lt;x&gt;" in text

    asyncio.run(scenario())


def test_turn_completed_after_stop_is_short_and_has_no_retry(owner_env):
    async def scenario():
        server.turn_state.note_stop("T5")
        server.on_codex_event("turn/completed", {"turn": {"id": "T5", "status": "interrupted"}})
        await _settle()
        owner_env.send_message.assert_awaited_once()
        args, kwargs = owner_env.send_message.call_args
        assert "остановлен" in args[1]
        assert kwargs["reply_markup"] is None

    asyncio.run(scenario())


def test_error_with_turn_is_not_forwarded_separately(owner_env):
    async def scenario():
        server.on_codex_event("error", {"turnId": "T6", "willRetry": False, "error": {"message": "x"}})
        await _settle()
        owner_env.send_message.assert_not_awaited()

    asyncio.run(scenario())


def test_notices_forwarded_and_throttled(owner_env, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(server, "notice_throttle", server.Throttle(interval=30, same_window=600, clock=lambda: now[0]))

    async def scenario():
        server.on_codex_event("warning", {"message": "disk <full>"})
        server.on_codex_event("warning", {"message": "another"})  # внутри 30 с — молчим
        await _settle()
        assert owner_env.send_message.await_count == 1
        assert "&lt;full&gt;" in owner_env.send_message.call_args[0][1]
        now[0] = 40
        server.on_codex_event("model/rerouted", {"fromModel": "a", "toModel": "b"})
        await _settle()
        assert owner_env.send_message.await_count == 2

    asyncio.run(scenario())


def test_listener_failure_never_propagates(owner_env):
    server.on_codex_event("item/started", {"item": "not-a-dict"})
    server.on_codex_event("turn/completed", None)  # type: ignore[arg-type]


def test_guest_update_is_answered_and_not_handled(owner_env, monkeypatch):
    server.load_access.cache_clear() if hasattr(server.load_access, "cache_clear") else None
    (server.STATE_DIR / "access.json").write_bytes(orjson.dumps({"allowFrom": ["111", "222"], "owner": "111"}))
    handler = AsyncMock()
    update = SimpleNamespace(message=SimpleNamespace(text="hi"), from_user=SimpleNamespace(id=222, username=None,
                                                                                         first_name="g", last_name=None, is_bot=False))
    update.event = update
    monkeypatch.setattr(server, "extract_update_user", lambda u: update.from_user)
    asyncio.run(server._guest_router_mw(handler, update, {}))
    handler.assert_not_awaited()
    owner_env.send_message.assert_awaited()
    assert owner_env.send_message.call_args[0][0] == 222


def test_owner_update_passes_to_handler(owner_env, monkeypatch):
    handler = AsyncMock(return_value="ok")
    user = SimpleNamespace(id=111, username=None, first_name="o", last_name=None, is_bot=False)
    monkeypatch.setattr(server, "extract_update_user", lambda u: user)
    update = SimpleNamespace(message=SimpleNamespace(text="hi"))
    assert asyncio.run(server._guest_router_mw(handler, update, {})) == "ok"
    handler.assert_awaited_once()


def test_inbound_from_non_owner_is_not_delivered(owner_env, monkeypatch):
    deliver = AsyncMock()
    monkeypatch.setattr(server, "deliver", deliver)
    monkeypatch.setattr(server, "route_ok", AsyncMock(return_value=True))
    (server.STATE_DIR / "access.json").write_bytes(orjson.dumps({"allowFrom": ["111", "222"], "owner": "111"}))
    msg = MagicMock()
    msg.from_user = SimpleNamespace(id=222, username="g", first_name="g", last_name=None, is_bot=False)
    msg.chat.id = 222
    msg.message_id = 1
    msg.date = None
    msg.message_thread_id = None
    asyncio.run(server.handle_inbound(msg, "hello", None))
    deliver.assert_not_awaited()
