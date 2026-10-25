import pytest  # noqa: E402

pytestmark = pytest.mark.skip(reason="stage 3: guest runtime (instances, inbox.sock, managed bots) removed from codex-gram stage 2")
"""Свой бот гостя через Telegram Managed Bots: bot.env, мастер-мидлварь, роутер, /mybot."""
import asyncio
import json
import stat
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402
from aiogram import Bot  # noqa: E402
from aiogram.types import Message, Update  # noqa: E402

OWNER = "111111111"
GUEST = "222222222"
STRANGER = "333333333"
BOT_ID = 7777001
BOT_TOKEN = "7777001:SECRET_TOKEN_value_xyz"


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    owner_state = tmp_path / "owner"
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    owner_state.mkdir()
    monkeypatch.setattr(server, "STATE_DIR", owner_state)
    monkeypatch.setattr(server, "ACCESS_FILE", owner_state / "access.json")
    monkeypatch.setattr(server, "KNOWN_USERS_FILE", owner_state / "known_users.json")
    monkeypatch.setattr(server, "_known_users_cache", None)
    monkeypatch.setattr(server, "GUESTS_STATE_ROOT", owner_state / "guests")
    monkeypatch.setattr(server, "USER_HOME", home)
    monkeypatch.setattr(server, "OWNER_SETTINGS_FILE", home / ".claude" / "settings.json")
    monkeypatch.setattr(server, "session_thread_id", None)
    monkeypatch.setattr(server, "_guest_bot_hint_at", {})
    monkeypatch.delenv("CLAUDE_GRAM_GUESTS_DIR", raising=False)
    (owner_state / "access.json").write_text(json.dumps(
        {"owner": OWNER, "allowFrom": [OWNER, GUEST]}), "utf-8")
    return owner_state


@pytest.fixture
def fake_bot(monkeypatch):
    fb = MagicMock()
    fb.send_message = AsyncMock(return_value=None)
    monkeypatch.setattr(server, "bot", fb)
    return fb


@pytest.fixture
def logs(monkeypatch):
    rec: list[str] = []
    monkeypatch.setattr(server, "log", lambda msg: rec.append(str(msg)))
    return rec


def _user(uid, username=None, first="Guest"):
    u = {"id": uid, "is_bot": False, "first_name": first}
    if username is not None:
        u["username"] = username
    return u


def managed_update(creator=GUEST, bot_id=BOT_ID, username="guest_claude_bot"):
    bot_info = {"id": bot_id, "is_bot": True, "first_name": "Claude"}
    if username is not None:
        bot_info["username"] = username
    return Update.model_validate({
        "update_id": 10,
        "managed_bot": {"user": _user(creator), "bot": bot_info},
    })


def msg_update(uid, text=None, extra=None, update_id=1):
    message = {"message_id": 1, "date": 1700000000,
               "chat": {"id": int(uid), "type": "private", "first_name": "G"},
               "from": _user(int(uid))}
    if text is not None:
        message["text"] = text
    message.update(extra or {})
    return Update.model_validate({"update_id": update_id, "message": message})


def reaction_update(uid):
    return Update.model_validate({
        "update_id": 5,
        "message_reaction": {"chat": {"id": int(uid), "type": "private"}, "date": 1700000000,
                             "message_id": 1, "user": _user(int(uid)),
                             "old_reaction": [], "new_reaction": []},
    })


# --------------------------------------------------------------------------- bot.env


def test_save_load_remove_bot_env(state):
    server.save_guest_bot(GUEST, BOT_TOKEN, BOT_ID, "@guest_claude_bot")
    path = server.guest_bot_env_path(GUEST)
    assert path == server.guest_state_dir(GUEST) / "bot.env"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.with_name("bot.env.tmp").exists()
    assert path.read_text("utf-8") == (
        f"TELEGRAM_BOT_TOKEN={BOT_TOKEN}\nBOT_ID={BOT_ID}\nBOT_USERNAME=guest_claude_bot\n")
    assert server.load_guest_bot(GUEST) == {
        "token": BOT_TOKEN, "bot_id": str(BOT_ID), "username": "guest_claude_bot"}

    server.remove_guest_bot(GUEST)
    assert not path.exists()
    server.remove_guest_bot(GUEST)  # повторно не падает


def test_load_missing_or_broken_returns_none(state):
    assert server.load_guest_bot(GUEST) is None
    path = server.guest_bot_env_path(GUEST)
    path.parent.mkdir(parents=True)
    path.write_text("BOT_ID=7\nBOT_USERNAME=x\n", "utf-8")
    assert server.load_guest_bot(GUEST) is None
    path.write_text("TELEGRAM_BOT_TOKEN=\n", "utf-8")
    assert server.load_guest_bot(GUEST) is None
    path.write_bytes(b"\xff\xfe\x00garbage")
    assert server.load_guest_bot(GUEST) is None


def test_save_rejects_empty_or_multiline_token(state):
    for bad in ("", "   ", "a\nb"):
        with pytest.raises(ValueError):
            server.save_guest_bot(GUEST, bad, BOT_ID, "x")
    assert not server.guest_bot_env_path(GUEST).exists()


def test_save_username_none_is_empty(state):
    server.save_guest_bot(GUEST, BOT_TOKEN, BOT_ID, None)
    assert server.load_guest_bot(GUEST)["username"] == ""


# --------------------------------------------------------------- getManagedBotToken


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload


class _FakeCM:
    def __init__(self, result=None, exc=None):
        self._result, self._exc = result, exc

    async def __aenter__(self):
        if self._exc is not None:
            raise self._exc
        return self._result

    async def __aexit__(self, *a):
        return False


def _fake_session(monkeypatch, payload=None, exc=None, calls=None):
    class FakeSession:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, url, json=None):
            if calls is not None:
                calls.append((url, json))
            return _FakeCM(result=_FakeResp(payload), exc=exc)

    monkeypatch.setattr(server.aiohttp, "ClientSession", FakeSession)


def test_get_token_ok_and_posts_user_id(monkeypatch):
    calls = []
    _fake_session(monkeypatch, {"ok": True, "result": BOT_TOKEN}, calls=calls)
    token = asyncio.run(server.get_managed_bot_token(BOT_ID))
    assert token == BOT_TOKEN
    url, body = calls[0]
    assert url.endswith("/getManagedBotToken") and body == {"user_id": BOT_ID}


@pytest.mark.parametrize("payload", [
    {"ok": False, "description": "Bad Request"},
    {"ok": True, "result": ""},
    {"ok": True, "result": None},
    ["not", "a", "dict"],
])
def test_get_token_bad_api_answer_raises(monkeypatch, payload):
    _fake_session(monkeypatch, payload)
    with pytest.raises(RuntimeError):
        asyncio.run(server.get_managed_bot_token(BOT_ID))


def test_get_token_transport_error_hides_url(monkeypatch):
    # aiohttp-ошибка несёт URL с токеном в тексте: наружу должен уйти только тип.
    exc = aiohttp.ClientError(f"boom https://api.telegram.org/bot{BOT_TOKEN}/getManagedBotToken")
    _fake_session(monkeypatch, exc=exc)
    with pytest.raises(RuntimeError) as info:
        asyncio.run(server.get_managed_bot_token(BOT_ID))
    assert BOT_TOKEN not in str(info.value)
    assert "SECRET" not in str(info.value)


# ------------------------------------------------------------------ master middleware


def _run_mw(update, handler=None):
    handler = handler or AsyncMock(return_value="passed")
    return asyncio.run(server._managed_bot_mw(handler, update, {})), handler


def test_mw_guest_creator_saves_token_restarts_instance(state, fake_bot, monkeypatch, logs, _no_real_systemctl):
    get_tok = AsyncMock(return_value=BOT_TOKEN)
    monkeypatch.setattr(server, "get_managed_bot_token", get_tok)
    result, handler = _run_mw(managed_update())

    assert result is None  # дальше по цепочке не передаём
    handler.assert_not_called()
    get_tok.assert_awaited_once_with(BOT_ID)
    saved = server.load_guest_bot(GUEST)
    assert saved["token"] == BOT_TOKEN and saved["username"] == "guest_claude_bot"
    assert stat.S_IMODE(server.guest_bot_env_path(GUEST).stat().st_mode) == 0o600
    # сообщение гостю через общий бот
    fake_bot.send_message.assert_awaited()
    args, kwargs = fake_bot.send_message.await_args
    assert args[0] == int(GUEST)
    assert "t.me/guest_claude_bot" in args[1]
    assert kwargs.get("parse_mode") == "HTML"
    # экземпляр гостя перезапущен, состояние подготовлено
    _no_real_systemctl.assert_awaited_with("restart", "--no-block", server.GUEST_UNIT.format(uid=GUEST))
    assert (server.guest_state_dir(GUEST) / "access.json").exists()


def test_mw_owner_creator_saves_nothing(state, fake_bot, monkeypatch, logs, _no_real_systemctl):
    get_tok = AsyncMock(return_value=BOT_TOKEN)
    monkeypatch.setattr(server, "get_managed_bot_token", get_tok)
    result, handler = _run_mw(managed_update(creator=OWNER))
    assert result is None
    get_tok.assert_not_awaited()
    assert server.load_guest_bot(OWNER) is None
    assert not server.guest_bot_env_path(OWNER).exists()
    _no_real_systemctl.assert_not_awaited()
    fake_bot.send_message.assert_not_awaited()
    assert any("not a guest" in m for m in logs)


def test_mw_stranger_creator_saves_nothing(state, fake_bot, monkeypatch, logs, _no_real_systemctl):
    get_tok = AsyncMock(return_value=BOT_TOKEN)
    monkeypatch.setattr(server, "get_managed_bot_token", get_tok)
    _run_mw(managed_update(creator=STRANGER))
    get_tok.assert_not_awaited()
    assert not server.guest_bot_env_path(STRANGER).exists()


def test_mw_api_error_tells_guest_and_saves_nothing(state, fake_bot, monkeypatch, logs, _no_real_systemctl):
    monkeypatch.setattr(server, "get_managed_bot_token",
                        AsyncMock(side_effect=RuntimeError("getManagedBotToken: API error")))
    result, _ = _run_mw(managed_update())
    assert result is None
    assert not server.guest_bot_env_path(GUEST).exists()
    _no_real_systemctl.assert_not_awaited()
    sent = [c.args for c in fake_bot.send_message.await_args_list]
    assert (int(GUEST), server.MANAGED_BOT_ERROR_TEXT) in sent


def test_mw_empty_token_from_api_is_error(state, fake_bot, monkeypatch, logs, _no_real_systemctl):
    _fake_session(monkeypatch, {"ok": True, "result": "   "})
    _run_mw(managed_update())
    assert not server.guest_bot_env_path(GUEST).exists()
    sent = [c.args for c in fake_bot.send_message.await_args_list]
    assert (int(GUEST), server.MANAGED_BOT_ERROR_TEXT) in sent


def test_mw_username_none_still_connects(state, fake_bot, monkeypatch, logs, _no_real_systemctl):
    monkeypatch.setattr(server, "get_managed_bot_token", AsyncMock(return_value=BOT_TOKEN))
    _run_mw(managed_update(username=None))
    saved = server.load_guest_bot(GUEST)
    assert saved["token"] == BOT_TOKEN and saved["username"] == ""
    text = fake_bot.send_message.await_args.args[1]
    assert "t.me/" not in text
    _no_real_systemctl.assert_awaited()


def test_mw_user_and_bot_ids_as_strings(state, fake_bot, monkeypatch, logs, _no_real_systemctl):
    get_tok = AsyncMock(return_value=BOT_TOKEN)
    monkeypatch.setattr(server, "get_managed_bot_token", get_tok)
    upd = Update.model_validate({"update_id": 3, "managed_bot": {
        "user": {"id": GUEST, "is_bot": False, "first_name": "G"},
        "bot": {"id": str(BOT_ID), "is_bot": True, "first_name": "B", "username": "sbot"}}})
    _run_mw(upd)
    get_tok.assert_awaited_once_with(BOT_ID)
    assert server.load_guest_bot(GUEST)["bot_id"] == str(BOT_ID)


def test_mw_missing_bot_id_is_ignored(state, fake_bot, monkeypatch, logs, _no_real_systemctl):
    get_tok = AsyncMock(return_value=BOT_TOKEN)
    monkeypatch.setattr(server, "get_managed_bot_token", get_tok)
    upd = Update.model_validate({"update_id": 4, "managed_bot": {
        "user": {"id": GUEST, "is_bot": False, "first_name": "G"},
        "bot": {"is_bot": True, "first_name": "B"}}})
    result, handler = _run_mw(upd)
    assert result is None
    get_tok.assert_not_awaited()
    assert server.load_guest_bot(GUEST) is None


def test_mw_passes_plain_updates_through(state, fake_bot, monkeypatch, logs):
    result, handler = _run_mw(msg_update(GUEST, "hi"))
    handler.assert_awaited_once()
    assert result == "passed"


def test_managed_bot_parsed_from_real_json_as_extra(state):
    raw = {"update_id": 1, "managed_bot": {"user": _user(int(GUEST)),
                                           "bot": {"id": BOT_ID, "is_bot": True, "first_name": "B"}}}
    upd = Update.model_validate(json.loads(json.dumps(raw)))
    assert isinstance(server._field(upd, "managed_bot"), dict)
    assert "managed_bot" in upd.model_extra


def test_token_never_logged(state, fake_bot, monkeypatch, capsys, tmp_path):
    # Настоящий логгер (без подмены), проверяем и stderr, и файл /tmp/bot_debug.log.
    debug_file = tmp_path / "bot_debug.log"
    real_open = open

    def fake_open(path, *a, **kw):
        if str(path) == "/tmp/bot_debug.log":
            return real_open(debug_file, *a, **kw)
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(server, "get_managed_bot_token", AsyncMock(return_value=BOT_TOKEN))
    _run_mw(managed_update())
    _run_mw(managed_update(creator=OWNER))
    _fake_session(monkeypatch, exc=aiohttp.ClientError(f"x/bot{BOT_TOKEN}/y"))
    _run_mw(managed_update(creator=STRANGER))
    _run_mw(managed_update(creator=GUEST, bot_id=BOT_ID + 1))  # настоящий getManagedBotToken падает
    out = capsys.readouterr()
    haystack = out.out + out.err + (debug_file.read_text("utf-8") if debug_file.exists() else "")
    assert BOT_TOKEN not in haystack
    assert "SECRET_TOKEN" not in haystack
    assert "7777001:" not in haystack


def test_token_not_logged_on_api_failure(state, fake_bot, monkeypatch, logs):
    _fake_session(monkeypatch, exc=aiohttp.ClientError(f"x/bot{BOT_TOKEN}/y"))
    _run_mw(managed_update())
    assert logs and all(BOT_TOKEN not in m and "SECRET_TOKEN" not in m for m in logs)


# ------------------------------------------------------------------ guest router


@pytest.fixture
def forward(monkeypatch):
    fwd = AsyncMock(return_value=True)
    monkeypatch.setattr(server, "forward_to_guest", fwd)
    return fwd


def _run_router(update):
    handler = AsyncMock(return_value="owner-handler")
    return asyncio.run(server._guest_router_mw(handler, update, {})), handler


def test_router_plain_guest_is_forwarded(state, fake_bot, forward):
    result, handler = _run_router(msg_update(GUEST, "hello"))
    forward.assert_awaited_once()
    handler.assert_not_called()
    assert result is None


def test_router_own_bot_guest_redirected_not_forwarded(state, fake_bot, forward):
    server.save_guest_bot(GUEST, BOT_TOKEN, BOT_ID, "guest_claude_bot")
    result, handler = _run_router(msg_update(GUEST, "hello"))
    forward.assert_not_awaited()
    handler.assert_not_called()
    assert result is None
    fake_bot.send_message.assert_awaited_once()
    args = fake_bot.send_message.await_args.args
    assert args[0] == int(GUEST) and "@guest_claude_bot" in args[1]


def test_router_own_bot_hint_throttled_60s(state, fake_bot, forward):
    server.save_guest_bot(GUEST, BOT_TOKEN, BOT_ID, "guest_claude_bot")
    for i in range(5):
        _run_router(msg_update(GUEST, f"m{i}", update_id=i + 1))
    assert fake_bot.send_message.await_count == 1
    # прошло больше 60 с -> снова подсказка
    server._guest_bot_hint_at[GUEST] -= 61
    _run_router(msg_update(GUEST, "again"))
    assert fake_bot.send_message.await_count == 2
    forward.assert_not_awaited()


def test_router_own_bot_other_updates_silently_ignored(state, fake_bot, forward):
    server.save_guest_bot(GUEST, BOT_TOKEN, BOT_ID, "guest_claude_bot")
    result, handler = _run_router(reaction_update(GUEST))
    assert result is None
    forward.assert_not_awaited()
    fake_bot.send_message.assert_not_awaited()


CREATED = {"managed_bot_created": {"bot": {"id": BOT_ID, "is_bot": True, "first_name": "B"}}}


def test_router_own_bot_mybot_answered_by_master_not_forwarded(state, fake_bot, forward):
    server.save_guest_bot(GUEST, BOT_TOKEN, BOT_ID, "guest_claude_bot")
    result, handler = _run_router(msg_update(GUEST, "/mybot"))
    forward.assert_not_awaited()
    handler.assert_not_called()
    assert result is None
    fake_bot.send_message.assert_awaited_once()
    args, kwargs = fake_bot.send_message.await_args
    assert args[0] == int(GUEST)
    assert "@guest_claude_bot" in args[1]
    assert kwargs["reply_markup"].keyboard[0][0].request_managed_bot is not None
    real_bot = Bot(server.TOKEN)  # server.bot подменён моком, сериализуем настоящим Bot
    wire = json.loads(real_bot.session.prepare_value(kwargs["reply_markup"], bot=real_bot, files={}))
    assert wire["keyboard"][0][0]["request_managed_bot"]["request_id"] == 1


def test_router_own_bot_mybot_with_bot_suffix_not_forwarded(state, fake_bot, forward):
    server.save_guest_bot(GUEST, BOT_TOKEN, BOT_ID, "guest_claude_bot")
    _run_router(msg_update(GUEST, "/mybot@SomeBot"))
    forward.assert_not_awaited()
    fake_bot.send_message.assert_awaited_once()


def test_router_own_bot_managed_bot_created_never_forwarded(state, fake_bot, forward):
    server.save_guest_bot(GUEST, BOT_TOKEN, BOT_ID, "guest_claude_bot")
    result, handler = _run_router(msg_update(GUEST, None, extra=CREATED))
    assert result is None
    forward.assert_not_awaited()
    handler.assert_not_called()
    fake_bot.send_message.assert_not_awaited()


def test_router_no_bot_env_managed_bot_created_never_forwarded(state, fake_bot, forward):
    result, handler = _run_router(msg_update(GUEST, None, extra=CREATED))
    assert result is None
    forward.assert_not_awaited()
    handler.assert_not_called()


def test_router_no_bot_env_mybot_still_forwarded(state, fake_bot, forward):
    assert server.load_guest_bot(GUEST) is None
    _run_router(msg_update(GUEST, "/mybot"))
    forward.assert_awaited_once()
    fake_bot.send_message.assert_not_awaited()


def test_recreate_offer_failure_is_logged(state, fake_bot, forward, logs):
    server.save_guest_bot(GUEST, BOT_TOKEN, BOT_ID, "guest_claude_bot")
    fake_bot.send_message.side_effect = RuntimeError("blocked")
    _run_router(msg_update(GUEST, "/mybot"))
    forward.assert_not_awaited()
    assert any("recreate offer failed" in m for m in logs)


def test_router_owner_and_stranger_not_touched(state, fake_bot, forward):
    result, handler = _run_router(msg_update(OWNER, "x"))
    handler.assert_awaited_once()
    forward.assert_not_awaited()


# ------------------------------------------------------------------ own-bot instance access


def test_own_bot_instance_drops_strangers(state, monkeypatch):
    # Экземпляр гостя: owner=гость, allowFrom=[гость]. Посторонние отсекаются gate().
    (state / "access.json").write_text(json.dumps({"owner": GUEST, "allowFrom": [GUEST]}), "utf-8")
    guest_msg = Message.model_validate(msg_update(GUEST, "hi").message.model_dump(mode="json", exclude_none=True))
    stranger_msg = Message.model_validate(msg_update(STRANGER, "hi").message.model_dump(mode="json", exclude_none=True))
    assert server.gate(guest_msg)["action"] == "deliver"
    assert server.gate(stranger_msg)["action"] == "drop"
    assert server.dm_command_gate(stranger_msg) is None
    assert server.dm_command_gate(guest_msg) is not None


def test_guest_instance_managed_bot_created_answer(state, monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    msg = Message.model_validate(msg_update(GUEST, None, extra={
        "managed_bot_created": {"bot": {"id": BOT_ID, "is_bot": True, "first_name": "B"}}}).message.model_dump(
        mode="json", exclude_none=True))
    handler = AsyncMock()
    result = asyncio.run(server._guest_instance_block_mw(handler, msg, {}))
    assert result is None
    handler.assert_not_called()
    answer.assert_awaited_once_with(server.MANAGED_BOT_CREATED_TEXT)


# ------------------------------------------------------------------ keyboard and commands


def test_keyboard_serializes_request_managed_bot():
    kb = server.managed_bot_keyboard("Ann")
    dumped = kb.model_dump(exclude_none=True)
    btn = dumped["keyboard"][0][0]
    assert btn["request_managed_bot"] == {"request_id": 1, "suggested_name": "Claude Ann"}
    assert dumped["resize_keyboard"] is True and dumped["one_time_keyboard"] is True
    # то же, что уходит в запрос к Bot API
    prepared = server.bot.session.prepare_value(kb, bot=server.bot, files={})
    wire = json.loads(prepared)
    assert wire["keyboard"][0][0]["request_managed_bot"]["request_id"] == 1


def test_keyboard_suggested_name_is_capped_and_handles_none():
    kb = server.managed_bot_keyboard("Я" * 200)
    name = kb.model_dump(exclude_none=True)["keyboard"][0][0]["request_managed_bot"]["suggested_name"]
    assert len(name) == 64 and name.startswith("Claude ")
    kb2 = server.managed_bot_keyboard(None)
    assert kb2.model_dump(exclude_none=True)["keyboard"][0][0]["request_managed_bot"]["suggested_name"] == "Claude"


def test_mybot_in_guest_menu():
    assert "mybot" in [c for c, _ in server.OWNER_BOT_COMMANDS]
    assert ("mybot", "Создать своего бота") in server.GUEST_BOT_COMMANDS
    assert "mybot" not in server.GUEST_BLOCKED_COMMANDS


def test_cmd_mybot_owner_gets_keyboard_and_warning(state, monkeypatch):
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    msg = Message.model_validate(msg_update(OWNER, "/mybot").message.model_dump(mode="json", exclude_none=True))
    asyncio.run(server.cmd_mybot(msg))
    answer.assert_awaited_once()
    kwargs = answer.await_args.kwargs
    assert "не подключается" in answer.await_args.args[0]
    wire = json.loads(server.bot.session.prepare_value(kwargs["reply_markup"], bot=server.bot, files={}))
    assert wire["keyboard"][0][0]["request_managed_bot"]["request_id"] == 1


def test_offer_sent_with_keyboard(state, fake_bot):
    asyncio.run(server._send_managed_bot_offer(GUEST))
    args, kwargs = fake_bot.send_message.await_args
    assert args[0] == int(GUEST) and args[1] == server.MANAGED_BOT_OFFER_TEXT
    assert kwargs["reply_markup"].keyboard[0][0].request_managed_bot is not None


def test_offer_failure_is_swallowed(state, fake_bot, logs):
    fake_bot.send_message.side_effect = RuntimeError("bot was blocked by the user")
    asyncio.run(server._send_managed_bot_offer(GUEST))
    assert any("offer failed" in m for m in logs)


def test_allowed_updates_contain_managed_bot():
    allowed = server._master_allowed_updates()
    assert "managed_bot" in allowed
    assert "message" in allowed and "callback_query" in allowed
    assert allowed == sorted(allowed)


# ------------------------------------------------------------------ unshare cleanup


def test_unshare_removes_guest_bot(state, monkeypatch, fake_bot, _no_real_systemctl):
    server.save_guest_bot(GUEST, BOT_TOKEN, BOT_ID, "guest_claude_bot")
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    monkeypatch.setattr(server, "clear_guest_commands", AsyncMock())
    msg = Message.model_validate(msg_update(OWNER, "/unshare 222222222").message.model_dump(
        mode="json", exclude_none=True))
    asyncio.run(server.cmd_unshare(msg))
    assert not server.guest_bot_env_path(GUEST).exists()
    _no_real_systemctl.assert_any_await("disable", "--now", "--no-block", server.GUEST_UNIT.format(uid=GUEST))


def test_managed_bot_drops_stale_thread_file(tmp_path, monkeypatch):
    """После подключения своего бота сохранённый топик общего бота удаляется."""
    import asyncio
    import server
    monkeypatch.setattr(server, "GUESTS_STATE_ROOT", tmp_path)
    monkeypatch.setattr(server, "is_guest", lambda uid, *a, **k: True)
    async def fake_token(bot_id):
        return "123456:" + "A" * 30
    monkeypatch.setattr(server, "get_managed_bot_token", fake_token)
    async def fake_send(*a, **k):
        return None
    monkeypatch.setattr(server.bot, "send_message", fake_send, raising=False)
    monkeypatch.setattr(server, "prepare_guest_state", lambda uid: (tmp_path / str(uid)).mkdir(exist_ok=True))
    (tmp_path / "555").mkdir()
    thread = tmp_path / "555" / server.THREAD_FILE.name
    thread.write_text("42")
    asyncio.run(server._handle_managed_bot({"user": {"id": 555}, "bot": {"id": 777, "username": "x_bot"}}))
    assert not thread.exists()
    assert server.load_guest_bot("555")["username"] == "x_bot"
