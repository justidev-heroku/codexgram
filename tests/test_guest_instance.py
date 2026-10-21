"""Гостевой режим: отдельный экземпляр на гостя.

Мастер (владелец) пересылает апдейты гостя в unix-сокет его экземпляра; гостевой
экземпляр (CLAUDE_GRAM_GUEST_UID) слушает сокет и блокирует админ-команды.
"""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402
from aiogram.types import Update  # noqa: E402

OWNER = "111111111"
GUEST = "222222222"
STRANGER = "333333333"
REPO = str(Path(__file__).parent.parent)


@pytest.fixture
def gstate(tmp_path, monkeypatch):
    """Изолированный владелец: STATE_DIR, домашний каталог, глобальный settings.json."""
    owner_state = tmp_path / "owner"
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    owner_state.mkdir()
    monkeypatch.setattr(server, "STATE_DIR", owner_state)
    monkeypatch.setattr(server, "ACCESS_FILE", owner_state / "access.json")
    monkeypatch.setattr(server, "KNOWN_USERS_FILE", owner_state / "known_users.json")
    monkeypatch.setattr(server, "_known_users_cache", None)
    monkeypatch.setattr(server, "MODELS_FILE", owner_state / "models.json")
    monkeypatch.setattr(server, "GUESTS_STATE_ROOT", owner_state / "guests")
    monkeypatch.setattr(server, "USER_HOME", home)
    monkeypatch.setattr(server, "OWNER_SETTINGS_FILE", home / ".claude" / "settings.json")
    monkeypatch.setattr(server, "session_thread_id", None)
    monkeypatch.delenv("CLAUDE_GRAM_GUESTS_DIR", raising=False)
    return SimpleNamespace(state=owner_state, home=home, root=owner_state / "guests")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(orjson.dumps(data))


def make_update(kind: str, uid: int = 222222222) -> Update:
    user = {"id": uid, "is_bot": False, "first_name": "Guest"}
    chat = {"id": uid, "type": "private"}
    base = {"update_id": 1}
    if kind == "message":
        base["message"] = {"message_id": 1, "date": 1700000000, "chat": chat, "from": user, "text": "hi"}
    elif kind == "edited_message":
        base["edited_message"] = {"message_id": 1, "date": 1700000000, "edit_date": 1700000001,
                                  "chat": chat, "from": user, "text": "hi!"}
    elif kind == "callback_query":
        base["callback_query"] = {"id": "1", "from": user, "chat_instance": "x", "data": "close"}
    elif kind == "message_reaction":
        base["message_reaction"] = {"chat": chat, "date": 1700000000, "message_id": 1,
                                    "user": user, "old_reaction": [], "new_reaction": []}
    elif kind == "channel_post":
        base["channel_post"] = {"message_id": 1, "date": 1700000000,
                                "chat": {"id": -100, "type": "channel", "title": "c"},
                                "text": "post"}
    return Update.model_validate(base)


# --------------------------------------------------------------------------- paths


@pytest.mark.parametrize("path, slug", [
    ("/root", "-root"),
    ("/root/guests/123", "-root-guests-123"),
    ("/root/my.dir_x/a b", "-root-my-dir-x-a-b"),
])
def test_project_slug(path, slug):
    assert server.project_slug(path) == slug


def test_project_sessions_dir_follows_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "USER_HOME", tmp_path)
    monkeypatch.setattr(server.os, "getcwd", lambda: "/root/guests/123")
    assert server.project_sessions_dir() == tmp_path / ".claude" / "projects" / "-root-guests-123"
    monkeypatch.setattr(server.os, "getcwd", lambda: "/root")
    assert server.project_sessions_dir() == tmp_path / ".claude" / "projects" / "-root"


def _probe(env_extra: dict) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_GRAM_", "CLAUDE_TELEGRAM_"))}
    env.update({"TELEGRAM_BOT_TOKEN": "123456789:TEST_TOKEN_not_real_aaaaaaaaaaaaaaaaaa",
                "TELEGRAM_STATE_DIR": tempfile.mkdtemp(prefix="gi-probe-")})
    env.update(env_extra)
    code = ("import server; print(server.SETTINGS_FILE); print(server.IS_GUEST_INSTANCE);"
            " print(server.GUEST_INSTANCE_UID)")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_settings_file_from_env_and_guest_instance_flags(tmp_path):
    out = _probe({"CLAUDE_GRAM_SETTINGS_FILE": str(tmp_path / "s.json"),
                  "CLAUDE_GRAM_GUEST_UID": GUEST}).split("\n")
    assert out[0] == str(tmp_path / "s.json")
    assert out[1] == "True" and out[2] == GUEST


def test_settings_file_default_without_env():
    out = _probe({}).split("\n")
    assert out[0].endswith(os.path.join(".claude", "settings.json"))
    assert out[1] == "False"


# --------------------------------------------------------------------------- prepare_guest_state


def test_prepare_creates_access_and_dirs(gstate):
    sdir = server.prepare_guest_state(GUEST)
    assert sdir == gstate.root / GUEST
    access = orjson.loads((sdir / "access.json").read_bytes())
    assert access["owner"] == GUEST and access["allowFrom"] == [GUEST]
    assert (gstate.home / "guests" / GUEST).is_dir()
    assert oct(os.stat(sdir).st_mode & 0o777) == "0o700"


def test_prepare_keeps_existing_settings(gstate):
    sdir = gstate.root / GUEST
    write_json(sdir / "settings.json", {"model": "claude-haiku-4-5-20251001", "effortLevel": "low", "x": 1})
    write_json(gstate.home / ".claude" / "settings.json", {"model": "claude-opus-4-8", "effortLevel": "max"})
    server.prepare_guest_state(GUEST)
    assert orjson.loads((sdir / "settings.json").read_bytes()) == {
        "model": "claude-haiku-4-5-20251001", "effortLevel": "low", "x": 1}


def test_prepare_copies_owner_model_and_effort(gstate):
    write_json(gstate.home / ".claude" / "settings.json",
               {"model": "claude-sonnet-4-6", "effortLevel": "high", "theme": "dark"})
    server.prepare_guest_state(GUEST)
    data = orjson.loads((gstate.root / GUEST / "settings.json").read_bytes())
    assert data["model"] == "claude-sonnet-4-6" and data["effortLevel"] == "high"
    assert "theme" not in data


def test_prepare_without_owner_settings_creates_empty(gstate):
    server.prepare_guest_state(GUEST)
    assert orjson.loads((gstate.root / GUEST / "settings.json").read_bytes()) == {}


def test_migration_moves_legacy_files(gstate):
    sdir = gstate.root / GUEST
    sdir.mkdir(parents=True)
    (sdir / "effort").write_text("xhigh\n")
    (sdir / "model").write_text("sonnet\n")  # алиас -> полный id
    (sdir / "goal").write_text("выучить Rust\n")
    (sdir / "session_id").write_text("abc-123\n")
    (sdir / "auto").write_text("off\n")
    server.prepare_guest_state(GUEST)
    data = orjson.loads((sdir / "settings.json").read_bytes())
    assert data["effortLevel"] == "xhigh"
    assert data["model"] == "claude-sonnet-4-6"
    assert (sdir / "session_goal.txt").read_text("utf-8").strip() == "выучить Rust"
    for name in ("session_id", "model", "effort", "auto", "goal"):
        assert not (sdir / name).exists()
    assert not (sdir / "active_session_id").exists()


def test_migration_does_not_override_existing_settings(gstate):
    sdir = gstate.root / GUEST
    write_json(sdir / "settings.json", {"effortLevel": "low"})
    (sdir / "effort").write_text("max")
    server.prepare_guest_state(GUEST)
    assert orjson.loads((sdir / "settings.json").read_bytes())["effortLevel"] == "low"


def test_migration_unknown_alias_is_dropped(gstate):
    sdir = gstate.root / GUEST
    sdir.mkdir(parents=True)
    (sdir / "model").write_text("no-such-alias")
    server.prepare_guest_state(GUEST)
    assert "model" not in orjson.loads((sdir / "settings.json").read_bytes())
    assert not (sdir / "model").exists()


def test_prepare_is_idempotent(gstate):
    sdir = gstate.root / GUEST
    sdir.mkdir(parents=True)
    (sdir / "effort").write_text("high")
    write_json(gstate.home / ".claude" / "settings.json", {"model": "claude-opus-4-8"})
    server.prepare_guest_state(GUEST)

    def snapshot():
        return {p.name: p.read_bytes() for p in sdir.iterdir() if p.is_file()}

    first = snapshot()
    server.prepare_guest_state(GUEST)
    assert snapshot() == first


def test_prepare_keeps_other_access_keys(gstate):
    sdir = gstate.root / GUEST
    write_json(sdir / "access.json", {"allowFrom": ["999"], "threads": False})
    server.prepare_guest_state(GUEST)
    access = orjson.loads((sdir / "access.json").read_bytes())
    assert access == {"allowFrom": [GUEST], "owner": GUEST, "threads": False}


def test_prepare_broken_access_is_moved_aside(gstate):
    sdir = gstate.root / GUEST
    sdir.mkdir(parents=True)
    (sdir / "access.json").write_text("{not json")
    server.prepare_guest_state(GUEST)
    access = orjson.loads((sdir / "access.json").read_bytes())
    assert access["allowFrom"] == [GUEST]
    assert any(p.name.startswith("access.json.corrupt-") for p in sdir.iterdir())


def test_prepare_broken_settings_is_moved_aside_and_rebuilt(gstate):
    sdir = gstate.root / GUEST
    sdir.mkdir(parents=True)
    (sdir / "settings.json").write_text("][")
    write_json(gstate.home / ".claude" / "settings.json", {"effortLevel": "medium"})
    server.prepare_guest_state(GUEST)
    assert orjson.loads((sdir / "settings.json").read_bytes()) == {"effortLevel": "medium"}
    assert any(p.name.startswith("settings.json.corrupt-") for p in sdir.iterdir())


# --------------------------------------------------------------------------- uid extraction


@pytest.mark.parametrize("kind", ["message", "edited_message", "callback_query", "message_reaction"])
def test_guest_uid_of_update_kinds(kind):
    assert server.guest_uid_of_update(make_update(kind, 222222222)) == "222222222"


def test_guest_uid_none_without_user():
    assert server.guest_uid_of_update(make_update("channel_post")) is None
    assert server.guest_uid_of_update(Update(update_id=5)) is None


# --------------------------------------------------------------------------- master middleware


def _write_access(gstate, data):
    write_json(gstate.state / "access.json", data)


def test_router_guest_forwarded_and_handler_skipped(gstate, monkeypatch):
    _write_access(gstate, {"allowFrom": [OWNER, GUEST]})
    fwd = AsyncMock(return_value=True)
    monkeypatch.setattr(server, "forward_to_guest", fwd)
    handler = AsyncMock(return_value="owner")
    upd = make_update("message", int(GUEST))
    result = asyncio.run(server._guest_router_mw(handler, upd, {}))
    assert result is None
    handler.assert_not_awaited()
    fwd.assert_awaited_once()
    uid, raw = fwd.await_args.args
    assert uid == GUEST
    assert raw["message"]["from"]["id"] == int(GUEST) and raw["update_id"] == 1
    assert "from" in raw["message"]  # by_alias


def test_router_guest_is_remembered(gstate, monkeypatch):
    _write_access(gstate, {"allowFrom": [OWNER, GUEST]})
    monkeypatch.setattr(server, "forward_to_guest", AsyncMock(return_value=True))
    asyncio.run(server._guest_router_mw(AsyncMock(), make_update("message", int(GUEST)), {}))
    assert server.load_known_users().get(GUEST) is not None


def test_router_owner_goes_to_handler(gstate, monkeypatch):
    _write_access(gstate, {"allowFrom": [OWNER, GUEST]})
    fwd = AsyncMock()
    monkeypatch.setattr(server, "forward_to_guest", fwd)
    handler = AsyncMock(return_value="ok")
    upd = make_update("message", int(OWNER))
    assert asyncio.run(server._guest_router_mw(handler, upd, {})) == "ok"
    handler.assert_awaited_once()
    fwd.assert_not_awaited()


def test_router_stranger_goes_to_handler(gstate, monkeypatch):
    _write_access(gstate, {"allowFrom": [OWNER]})
    fwd = AsyncMock()
    monkeypatch.setattr(server, "forward_to_guest", fwd)
    handler = AsyncMock(return_value="drop")
    assert asyncio.run(server._guest_router_mw(handler, make_update("message", int(STRANGER)), {})) == "drop"
    handler.assert_awaited_once()
    fwd.assert_not_awaited()


def test_router_update_without_user_goes_to_handler(gstate, monkeypatch):
    _write_access(gstate, {"allowFrom": [OWNER, GUEST]})
    fwd = AsyncMock()
    monkeypatch.setattr(server, "forward_to_guest", fwd)
    handler = AsyncMock(return_value=None)
    asyncio.run(server._guest_router_mw(handler, make_update("channel_post"), {}))
    handler.assert_awaited_once()
    fwd.assert_not_awaited()


# --------------------------------------------------------------------------- forward_to_guest over a real socket


@pytest.fixture
def fwd_env(monkeypatch):
    base = Path(tempfile.mkdtemp(prefix="gf-"))
    monkeypatch.setattr(server, "GUESTS_STATE_ROOT", base)
    monkeypatch.setattr(server, "GUEST_WAKE_RETRY", 0.01)
    monkeypatch.setattr(server, "GUEST_WAKE_TIMEOUT", 0.3)
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    monkeypatch.setattr(server, "bot", fake_bot)
    monkeypatch.setattr(server, "ensure_guest_running", AsyncMock())
    yield SimpleNamespace(base=base, bot=fake_bot)
    shutil.rmtree(base, ignore_errors=True)


async def _start_fake_guest(base: Path, received: list):
    sock_dir = base / GUEST
    sock_dir.mkdir(parents=True, exist_ok=True)

    async def handle(reader, writer):
        while True:
            line = await reader.readline()
            if not line:
                break
            received.append(line)
        writer.close()

    return await asyncio.start_unix_server(handle, path=str(sock_dir / "inbox.sock"))


def test_forward_delivers_one_json_line(fwd_env):
    received = []

    async def run():
        srv = await _start_fake_guest(fwd_env.base, received)
        ok = await server.forward_to_guest(GUEST, {"update_id": 7, "message": {"from": {"id": 222}}})
        await asyncio.sleep(0.05)
        srv.close()
        await srv.wait_closed()
        return ok

    assert asyncio.run(run()) is True
    assert len(received) == 1 and received[0].endswith(b"\n")
    body = orjson.loads(received[0])
    assert body == {"update_id": 7, "message": {"from": {"id": 222}}}
    server.ensure_guest_running.assert_not_awaited()


def test_forward_wakes_guest_and_keeps_order(fwd_env, monkeypatch):
    received = []
    servers = []

    async def wake(uid):
        servers.append(await _start_fake_guest(fwd_env.base, received))

    monkeypatch.setattr(server, "ensure_guest_running", AsyncMock(side_effect=wake))

    async def run():
        results = await asyncio.gather(*[
            server.forward_to_guest(GUEST, {"update_id": i}) for i in (1, 2, 3)])
        await asyncio.sleep(0.05)
        for s in servers:
            s.close()
        return results

    results = asyncio.run(run())
    assert results == [True, True, True]
    assert [orjson.loads(x)["update_id"] for x in received] == [1, 2, 3]
    server.ensure_guest_running.assert_awaited_once_with(GUEST)
    fwd_env.bot.send_message.assert_awaited_once_with(int(GUEST), server.GUEST_WAKING_TEXT)


def test_forward_gives_up_after_timeout_and_drops_buffer(fwd_env):
    async def run():
        return await asyncio.gather(*[server.forward_to_guest(GUEST, {"update_id": i}) for i in (1, 2, 3)])

    assert asyncio.run(run()) == [False, False, False]
    texts = [c.args[1] for c in fwd_env.bot.send_message.await_args_list]
    assert texts.count(server.GUEST_WAKE_FAILED_TEXT) == 1
    assert texts.count(server.GUEST_WAKING_TEXT) == 1
    assert server.ensure_guest_running.await_count >= 1
    assert not server._guest_pending.get(GUEST)


def test_forward_retries_after_give_up_starts_new_episode(fwd_env):
    async def run():
        first = await server.forward_to_guest(GUEST, {"update_id": 1})
        second = await server.forward_to_guest(GUEST, {"update_id": 2})
        return first, second

    assert asyncio.run(run()) == (False, False)
    waking = [c for c in fwd_env.bot.send_message.await_args_list if c.args[1] == server.GUEST_WAKING_TEXT]
    assert len(waking) == 2  # по одному «запускаю» на каждый эпизод пробуждения


# --------------------------------------------------------------------------- guest instance: command block


def _msg(text):
    msg = MagicMock()
    msg.text = text
    msg.answer = AsyncMock()
    return msg


@pytest.mark.parametrize("text", ["/login", "/share 123", "/access@SomeBot", "/unshare @x",
                                  "/logout", "/switch_account a", "/accounts", "/mcp_debug"])
def test_guest_instance_blocks_admin_commands(text):
    handler = AsyncMock()
    msg = _msg(text)
    assert asyncio.run(server._guest_instance_block_mw(handler, msg, {})) is None
    handler.assert_not_awaited()
    msg.answer.assert_awaited_once_with(server.GUEST_BLOCKED_TEXT)


@pytest.mark.parametrize("text", ["/model", "/effort high", "/resume", "/loginx", "/restart",
                                  "обычный текст", "/share_me_not_really_x"])
def test_guest_instance_passes_guest_commands(text):
    handler = AsyncMock(return_value="passed")
    msg = _msg(text)
    assert asyncio.run(server._guest_instance_block_mw(handler, msg, {})) == "passed"
    handler.assert_awaited_once()
    msg.answer.assert_not_awaited()


def test_command_name_parsing():
    assert server.command_name("/login") == "login"
    assert server.command_name("/Login@Bot x") == "login"
    assert server.command_name("/loginx") == "loginx"
    assert server.command_name("login") is None
    assert server.command_name(None) is None


def test_guest_bot_commands_exclude_blocked():
    names = {c for c, _ in server.GUEST_BOT_COMMANDS}
    assert names and not names & server.GUEST_BLOCKED_COMMANDS
    assert {"model", "effort", "auto", "goal", "resume", "restart"} <= names


def test_guest_instance_text_mentions_guest_not_owner():
    assert "guest instance" in server._GUEST_ACCESS_INSTRUCTION
    assert "headless" not in server._OWNER_ACCESS_INSTRUCTION
    assert "never edit access.json" in server._GUEST_ACCESS_INSTRUCTION.lower()


# --------------------------------------------------------------------------- guest instance: inbox socket


def test_inbox_feeds_valid_updates_and_survives_garbage(tmp_path):
    base = Path(tempfile.mkdtemp(prefix="gi-"))
    sock = base / "inbox.sock"
    dispatcher = MagicMock()
    dispatcher.feed_raw_update = AsyncMock()
    bot_obj = object()

    async def send(lines):
        _, w = await asyncio.open_unix_connection(str(sock))
        for ln in lines:
            w.write(ln)
        await w.drain()
        w.close()
        await w.wait_closed()

    async def wait_calls(n):
        for _ in range(200):
            if dispatcher.feed_raw_update.await_count >= n:
                return
            await asyncio.sleep(0.01)

    async def run():
        srv = await server.start_guest_inbox(dispatcher, bot_obj, path=sock)
        try:
            ok = orjson.dumps({"update_id": 1, "message": {"text": "a"}}) + b"\n"
            await send([ok, b"{broken json\n", orjson.dumps({"update_id": 2}) + b"\n"])
            await wait_calls(2)
            await send([orjson.dumps({"update_id": 3}) + b"\n"])  # сервер жив после мусора
            await wait_calls(3)
        finally:
            srv.close()
            await srv.wait_closed()

    try:
        asyncio.run(run())
    finally:
        shutil.rmtree(base, ignore_errors=True)
    calls = dispatcher.feed_raw_update.await_args_list
    assert [c.args[1]["update_id"] for c in calls] == [1, 2, 3]
    assert all(c.args[0] is bot_obj for c in calls)


# --------------------------------------------------------------------------- owner: share / unshare hooks


@pytest.fixture
def share_env(gstate, monkeypatch):
    _write_access(gstate, {"allowFrom": [OWNER]})
    menu = MagicMock()
    menu.set_my_commands = AsyncMock()
    menu.delete_my_commands = AsyncMock()
    monkeypatch.setattr(server, "bot", menu)
    monkeypatch.setattr(server, "ensure_guest_running", AsyncMock())
    monkeypatch.setattr(server, "stop_guest_instance", AsyncMock())
    return menu


def _share_msg(text):
    msg = MagicMock()
    msg.from_user = SimpleNamespace(id=int(OWNER), username=None, first_name="O", last_name=None, is_bot=False)
    msg.chat.id = int(OWNER)
    msg.chat.type = server.ChatType.PRIVATE
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def test_share_starts_instance_and_sets_menu(share_env):
    asyncio.run(server.cmd_share(_share_msg(f"/share {GUEST}")))
    server.ensure_guest_running.assert_awaited_once_with(GUEST)
    share_env.set_my_commands.assert_awaited_once()
    scope = share_env.set_my_commands.await_args.kwargs["scope"]
    assert isinstance(scope, server.BotCommandScopeChat) and scope.chat_id == int(GUEST)
    names = {c.command for c in share_env.set_my_commands.await_args.args[0]}
    assert names == {c for c, _ in server.GUEST_BOT_COMMANDS}


def test_unshare_stops_instance_and_clears_menu(share_env, gstate):
    _write_access(gstate, {"allowFrom": [OWNER, GUEST], "guests": {GUEST: {"added": "x"}}})
    asyncio.run(server.cmd_unshare(_share_msg(f"/unshare {GUEST}")))
    server.stop_guest_instance.assert_awaited_once_with(GUEST)
    scope = share_env.delete_my_commands.await_args.kwargs["scope"]
    assert isinstance(scope, server.BotCommandScopeChat) and scope.chat_id == int(GUEST)


def test_failed_share_does_not_start_instance(share_env):
    asyncio.run(server.cmd_share(_share_msg(f"/share {OWNER}")))
    server.ensure_guest_running.assert_not_awaited()
    share_env.set_my_commands.assert_not_awaited()


# --------------------------------------------------------------------------- update_to_raw / delivery


def test_update_to_raw_link_preview_roundtrip():
    upd = Update.model_validate({"update_id": 1, "message": {
        "message_id": 5, "date": 1700000000, "chat": {"id": 7, "type": "private"},
        "from": {"id": 7, "is_bot": False, "first_name": "a"},
        "text": "hi https://x.com", "link_preview_options": {"url": "https://x.com"}}})
    raw = server.update_to_raw(upd)
    assert orjson.dumps(raw)  # не падает на сериализацию
    assert "from" in raw["message"]
    back = Update.model_validate(orjson.loads(orjson.dumps(raw)))
    assert back.message.text == "hi https://x.com"
    assert back.message.from_user.is_bot is False
    assert int(back.message.date.timestamp()) == 1700000000


def test_update_to_raw_callback_query_roundtrip():
    upd = make_update("callback_query", int(GUEST))
    raw = orjson.loads(orjson.dumps(server.update_to_raw(upd)))
    assert "from" in raw["callback_query"]
    back = Update.model_validate(raw)
    assert back.callback_query.data == "close"
    assert back.callback_query.from_user.id == int(GUEST)


def test_update_to_raw_entities_expandable_blockquote_roundtrip():
    text = "quote line\nmore"
    upd = Update.model_validate({"update_id": 2, "message": {
        "message_id": 6, "date": 1700000000, "chat": {"id": 7, "type": "private"},
        "from": {"id": 7, "is_bot": False, "first_name": "a"},
        "text": text,
        "entities": [{"type": "expandable_blockquote", "offset": 0, "length": len(text)}]}})
    raw = orjson.loads(orjson.dumps(server.update_to_raw(upd)))
    assert raw["message"]["entities"][0]["type"] == "expandable_blockquote"
    back = Update.model_validate(raw)
    assert back.message.entities[0].type == "expandable_blockquote"
    assert back.message.entities[0].length == len(text)
    assert back.message.text == text


def test_router_serialization_failure_notifies_guest(gstate, monkeypatch):
    _write_access(gstate, {"allowFrom": [OWNER, GUEST]})

    def boom(update):
        raise TypeError("Unable to serialize unknown type")

    monkeypatch.setattr(server, "update_to_raw", boom)
    fwd = AsyncMock()
    monkeypatch.setattr(server, "forward_to_guest", fwd)
    safe_send = AsyncMock()
    monkeypatch.setattr(server, "_safe_send", safe_send)
    handler = AsyncMock(return_value="owner")
    result = asyncio.run(server._guest_router_mw(handler, make_update("message", int(GUEST)), {}))
    assert result is None
    handler.assert_not_awaited()
    fwd.assert_not_awaited()
    safe_send.assert_awaited_once()
    uid, text = safe_send.await_args.args
    assert uid == GUEST
    assert "Не удалось доставить" in text


def test_router_forward_failure_notifies_guest(gstate, monkeypatch):
    _write_access(gstate, {"allowFrom": [OWNER, GUEST]})
    monkeypatch.setattr(server, "forward_to_guest", AsyncMock(side_effect=OSError("socket gone")))
    safe_send = AsyncMock()
    monkeypatch.setattr(server, "_safe_send", safe_send)
    handler = AsyncMock()
    asyncio.run(server._guest_router_mw(handler, make_update("message", int(GUEST)), {}))
    handler.assert_not_awaited()
    safe_send.assert_awaited_once()
    assert "Не удалось доставить" in safe_send.await_args.args[1]
