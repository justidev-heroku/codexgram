"""Гостевой режим codex-gram: отдельный экземпляр на гостя.

Мастер (владелец) пересылает апдейты гостя в unix-сокет его экземпляра; гостевой
экземпляр (CODEX_GRAM_GUEST_UID) слушает сокет и блокирует админ-команды.
"""
import asyncio
import json
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
    """Изолированный владелец: STATE_DIR, домашний каталог."""
    owner_state = tmp_path / "owner"
    home = tmp_path / "home"
    home.mkdir()
    owner_state.mkdir()
    monkeypatch.setattr(server, "STATE_DIR", owner_state)
    monkeypatch.setattr(server, "ACCESS_FILE", owner_state / "access.json")
    monkeypatch.setattr(server, "KNOWN_USERS_FILE", owner_state / "known_users.json")
    monkeypatch.setattr(server, "_known_users_cache", None)
    monkeypatch.setattr(server, "GUESTS_STATE_ROOT", owner_state / "guests")
    monkeypatch.setattr(server, "USER_HOME", home)
    monkeypatch.setattr(server, "session_thread_id", None)
    monkeypatch.delenv("CODEX_GRAM_GUESTS_DIR", raising=False)
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


def test_guest_paths_default(gstate):
    assert server.guest_state_dir(GUEST) == gstate.root / GUEST
    assert server.guest_work_dir(GUEST) == gstate.home / "guests-codex" / GUEST
    assert server.guest_socket_path(GUEST) == gstate.root / GUEST / "inbox.sock"


def test_guest_paths_env_override(gstate, monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_GRAM_GUESTS_DIR", str(tmp_path / "w"))
    assert server.guest_work_dir(GUEST) == tmp_path / "w" / GUEST


@pytest.mark.parametrize("bad", ["", "../1", "12a", "1/2", "-5", "1" * 21, "..", "1\n2"])
def test_guest_paths_reject_bad_uid(gstate, bad):
    with pytest.raises(ValueError):
        server.guest_state_dir(bad)
    with pytest.raises(ValueError):
        server.guest_work_dir(bad)


# --------------------------------------------------------------------------- guest process (subprocess probe)


PROBE = (
    "import json, server;"
    "print(json.dumps({'guest': server.IS_GUEST_INSTANCE, 'uid': server.GUEST_INSTANCE_UID,"
    " 'own_bot': server.GUEST_OWN_BOT, 'token': server.TOKEN, 'owner_state': str(server.OWNER_STATE_DIR),"
    " 'cwd': server.codex_thread_cwd(), 'access': server._ACCESS_INSTRUCTION,"
    " 'mcp': server.build_codex_mcp_servers()['telegram']['env'],"
    " 'instr': server.build_codex_bridge().instructions()}))"
)
OWNER_TOKEN = "111:OWNER_TOKEN_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OWN_BOT_TOKEN = "999:GUEST_OWN_TOKEN_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _probe(tmp_path, uid=GUEST, state=None, bot_env=None, memory=None, extra=None):
    owner_state = tmp_path / "owner"
    gdir = state if state is not None else owner_state / "guests" / uid
    gdir.mkdir(parents=True, exist_ok=True)
    (owner_state / ".env").write_text(f"TELEGRAM_BOT_TOKEN={OWNER_TOKEN}\nOTHER_SECRET=zzz\n")
    if bot_env is not None:
        (gdir / "bot.env").write_text(bot_env)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_GRAM_", "CLAUDE_TELEGRAM_", "CODEX_GRAM_", "TELEGRAM_"))}
    env.update({"TELEGRAM_STATE_DIR": str(gdir), "CODEX_GRAM_GUEST_UID": uid,
                "CODEX_GRAM_CODEX_HOME": str(tmp_path / "owner_codex"),
                "CODEX_GRAM_MEMORY_DIR": str(memory or tmp_path / "nomem"),
                "HOME": str(tmp_path / "home")})
    env.update(extra or {})
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO, env=env,
                         capture_output=True, text=True, timeout=120)
    return out


def test_guest_process_uses_owner_token_and_guest_state(tmp_path):
    mem = tmp_path / "mem"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("- [S](s.md) — SECRET_MEMORY_INDEX")
    out = _probe(tmp_path, memory=mem)
    assert out.returncode == 0, out.stderr
    data = orjson.loads(out.stdout.strip().splitlines()[-1])
    assert data["guest"] is True and data["uid"] == GUEST and data["own_bot"] is False
    assert data["token"] == OWNER_TOKEN
    assert data["owner_state"] == str(tmp_path / "owner")
    assert data["cwd"] == str(tmp_path / "home" / "guests-codex" / GUEST)
    assert "guest instance" in data["access"] and GUEST in data["access"]
    assert data["mcp"]["CODEX_GRAM_GUEST_UID"] == GUEST
    assert data["mcp"]["TELEGRAM_STATE_DIR"] == str(tmp_path / "owner" / "guests" / GUEST)
    assert OWNER_TOKEN not in json.dumps(data["mcp"])
    assert "SECRET_MEMORY_INDEX" not in data["instr"]
    assert "Рабочий регламент владельца" not in data["instr"]
    # из .env мастера берётся только токен, остальное в окружение гостя не попадает
    assert "zzz" not in out.stdout


def test_guest_process_prefers_own_bot_env(tmp_path):
    out = _probe(tmp_path, bot_env=f"TELEGRAM_BOT_TOKEN={OWN_BOT_TOKEN}\nBOT_ID=999\nBOT_USERNAME=gbot\n",
                 extra={"TELEGRAM_BOT_TOKEN": OWNER_TOKEN})
    assert out.returncode == 0, out.stderr
    data = orjson.loads(out.stdout.strip().splitlines()[-1])
    assert data["own_bot"] is True and data["token"] == OWN_BOT_TOKEN


@pytest.mark.parametrize("bad_env", ["TELEGRAM_BOT_TOKEN=\n", "BOT_ID=1\n", "TELEGRAM_BOT_TOKEN=a b\n"])
def test_guest_process_broken_bot_env_falls_back_to_shared_bot(tmp_path, bad_env):
    out = _probe(tmp_path, bot_env=bad_env)
    assert out.returncode == 0, out.stderr
    data = orjson.loads(out.stdout.strip().splitlines()[-1])
    assert data["own_bot"] is False and data["token"] == OWNER_TOKEN


def test_guest_process_rejects_state_dir_of_other_guest(tmp_path):
    out = _probe(tmp_path, state=tmp_path / "owner" / "guests" / "333333333")
    assert out.returncode == 2
    assert "guests/<uid>" in out.stderr


def test_guest_process_rejects_owner_state_dir(tmp_path):
    out = _probe(tmp_path, state=tmp_path / "owner")
    assert out.returncode == 2


@pytest.mark.parametrize("uid", ["abc", "../222"])
def test_guest_process_rejects_bad_uid(tmp_path, uid):
    out = _probe(tmp_path, uid=uid, state=tmp_path / "owner" / "guests" / "x")
    assert out.returncode == 2


def test_owner_process_is_not_guest(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_GRAM_GUEST", "CLAUDE_TELEGRAM_"))}
    env["TELEGRAM_STATE_DIR"] = str(tmp_path / "s")
    code = "import server; print(server.IS_GUEST_INSTANCE, server.GUEST_OWN_BOT, server.OWNER_STATE_DIR == server.STATE_DIR)"
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["False", "False", "True"]


# --------------------------------------------------------------------------- prepare_guest_state


def test_prepare_creates_access_and_dirs(gstate):
    sdir = server.prepare_guest_state(GUEST)
    assert sdir == gstate.root / GUEST
    access = orjson.loads((sdir / "access.json").read_bytes())
    assert access["owner"] == GUEST and access["allowFrom"] == [GUEST]
    assert (gstate.home / "guests-codex" / GUEST).is_dir()
    assert oct(os.stat(sdir).st_mode & 0o777) == "0o700"
    assert oct(os.stat(sdir / "access.json").st_mode & 0o777) == "0o600"


def test_prepare_inherits_owner_codex_settings_once(gstate):
    write_json(gstate.state / "codex_settings.json",
               {"model": "gpt-x", "effort": "high", "auto": "on-request", "secret_key": "no"})
    server.prepare_guest_state(GUEST)
    data = orjson.loads((gstate.root / GUEST / "codex_settings.json").read_bytes())
    assert data == {"model": "gpt-x", "effort": "high", "auto": "on-request"}
    # дальше настройки гостя свои: владелец меняет свои — у гостя не меняется
    write_json(gstate.state / "codex_settings.json", {"model": "gpt-y"})
    server.prepare_guest_state(GUEST)
    assert orjson.loads((gstate.root / GUEST / "codex_settings.json").read_bytes())["model"] == "gpt-x"


def test_prepare_keeps_existing_guest_settings(gstate):
    write_json(gstate.root / GUEST / "codex_settings.json", {"model": "mine"})
    write_json(gstate.state / "codex_settings.json", {"model": "owner", "auto": "never"})
    server.prepare_guest_state(GUEST)
    assert orjson.loads((gstate.root / GUEST / "codex_settings.json").read_bytes()) == {"model": "mine"}


def test_prepare_without_owner_settings_creates_empty(gstate):
    server.prepare_guest_state(GUEST)
    assert orjson.loads((gstate.root / GUEST / "codex_settings.json").read_bytes()) == {}


def test_prepare_is_idempotent(gstate):
    write_json(gstate.state / "codex_settings.json", {"model": "gpt-x"})
    sdir = server.prepare_guest_state(GUEST)

    def snapshot():
        return {p.name: p.read_bytes() for p in sdir.iterdir() if p.is_file()}

    first = snapshot()
    server.prepare_guest_state(GUEST)
    assert snapshot() == first


def test_prepare_keeps_other_access_keys_but_never_guests(gstate):
    sdir = gstate.root / GUEST
    write_json(sdir / "access.json", {"allowFrom": ["999", GUEST], "threads": False,
                                      "guests": {"999": {"name": "x"}}})
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


def test_prepare_rejects_bad_uid(gstate):
    with pytest.raises(ValueError):
        server.prepare_guest_state("../owner")
    assert not (gstate.state / "access.json").exists()


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


@pytest.mark.parametrize("text", ["/share 123", "/access@SomeBot", "/unshare @x", "/save_account a",
                                  "/delete_account", "/check_update", "/mcp_debug", "/SHARE 1"])
def test_guest_instance_blocks_admin_commands(text):
    handler = AsyncMock()
    msg = _msg(text)
    assert asyncio.run(server._guest_instance_block_mw(handler, msg, {})) is None
    handler.assert_not_awaited()
    msg.answer.assert_awaited_once_with(server.GUEST_BLOCKED_TEXT)


@pytest.mark.parametrize("text", ["/model", "/effort high", "/resume", "/loginx", "/restart",
                                  "обычный текст", "/share_me_not_really_x", "/login", "/logout",
                                  "/accounts", "/switch_account owner", "/mybot", "/usage"])
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
    assert {"model", "effort", "auto", "goal", "resume", "restart", "login", "logout", "accounts",
            "switch_account", "mybot", "new", "stop", "usage"} <= names
    assert not {"share", "unshare", "access", "save_account", "delete_account", "check_update"} & names


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


# --------------------------------------------------------------------------- этап 3: злые кейсы

G2 = "444444444"


def test_router_forwards_only_to_sender(gstate, monkeypatch):
    """Апдейт гостя A уходит только в экземпляр A: другой гость и владелец его не получают."""
    _write_access(gstate, {"allowFrom": [OWNER, GUEST, G2]})
    fwd = AsyncMock(return_value=True)
    monkeypatch.setattr(server, "forward_to_guest", fwd)
    handler = AsyncMock()
    for uid in (GUEST, G2, GUEST):
        asyncio.run(server._guest_router_mw(handler, make_update("message", int(uid)), {}))
    assert [c.args[0] for c in fwd.await_args_list] == [GUEST, G2, GUEST]
    for c in fwd.await_args_list:
        assert c.args[1]["message"]["from"]["id"] == int(c.args[0])
    handler.assert_not_awaited()


def test_router_unshared_guest_goes_to_owner_handlers_and_is_dropped(gstate, monkeypatch):
    _write_access(gstate, {"allowFrom": [OWNER]})
    fwd = AsyncMock()
    monkeypatch.setattr(server, "forward_to_guest", fwd)
    handler = AsyncMock(return_value="gate")
    assert asyncio.run(server._guest_router_mw(handler, make_update("message", int(GUEST)), {})) == "gate"
    fwd.assert_not_awaited()


def test_guest_sockets_are_per_uid(gstate):
    assert server.guest_socket_path(GUEST) != server.guest_socket_path(G2)
    assert server.guest_socket_path(GUEST).parent.name == GUEST


def _cb(data, uid=GUEST):
    cb = MagicMock()
    cb.data = data
    cb.from_user = SimpleNamespace(id=int(uid))
    cb.answer = AsyncMock()
    return cb


@pytest.mark.parametrize("data", ["acc:main", "accdel:main", "upd:go"])
def test_guest_instance_blocks_owner_callbacks(data):
    handler = AsyncMock()
    cb = _cb(data)
    assert asyncio.run(server._guest_instance_block_cb_mw(handler, cb, {})) is None
    handler.assert_not_awaited()
    cb.answer.assert_awaited_once()


@pytest.mark.parametrize("data", ["gacc:own", "res:abc", "model:gpt", "apr:a:x", "turn:retry", None])
def test_guest_instance_passes_guest_callbacks(data):
    handler = AsyncMock(return_value="ok")
    assert asyncio.run(server._guest_instance_block_cb_mw(handler, _cb(data), {})) == "ok"


def test_guest_instance_drops_strangers_and_owner(gstate):
    """В экземпляре гостя owner=гость: сообщения владельца бота и посторонних не доставляются."""
    sdir = gstate.root / GUEST
    write_json(sdir / "access.json", {"owner": GUEST, "allowFrom": [GUEST]})
    server.ACCESS_FILE = sdir / "access.json"
    try:
        for uid, action in ((GUEST, "deliver"), (OWNER, "drop"), (STRANGER, "drop")):
            m = MagicMock()
            m.from_user = SimpleNamespace(id=int(uid))
            assert server.gate(m)["action"] == action
        with pytest.raises(ValueError):
            server.assert_allowed_chat(OWNER)  # MCP гостя не может написать владельцу
        server.assert_allowed_chat(GUEST)
    finally:
        server.ACCESS_FILE = gstate.state / "access.json"


# --------------------------------------------------------------------------- /resume: только свои треды


def _thr(tid, chat, cwd):
    return {"id": tid, "preview": f'<channel source="telegram" chat_id="{chat}">hi {tid}</channel>',
            "cwd": cwd, "updatedAt": 1700000000}


@pytest.fixture
def resume_env(gstate, monkeypatch):
    gcwd = str(gstate.home / "guests-codex" / GUEST)
    threads = [
        _thr("own1", GUEST, gcwd),
        _thr("owner1", OWNER, "/root"),
        _thr("other_cwd", GUEST, "/root"),             # chat_id гостя, но не его каталог
        _thr("g2", G2, str(gstate.home / "guests-codex" / G2)),
    ]
    by_id = {t["id"]: t for t in threads}
    bridge = MagicMock()
    bridge.ready = True
    bridge.thread_id = "own1"
    bridge.active_turn_id = None
    bridge.cwd = gcwd

    async def list_threads(limit=10, cwd=None):
        return [t for t in threads if cwd is None or t["cwd"] == cwd]

    async def read_thread(tid):
        if tid not in by_id:
            raise server.RPCError(-1, "nope")
        return by_id[tid]

    bridge.list_threads = AsyncMock(side_effect=list_threads)
    bridge.read_thread = AsyncMock(side_effect=read_thread)
    bridge.resume_thread = AsyncMock(side_effect=lambda tid: tid)
    monkeypatch.setattr(server, "codex_bridge", bridge)
    return SimpleNamespace(bridge=bridge, gcwd=gcwd)


def _as_guest(gstate, monkeypatch):
    sdir = gstate.root / GUEST
    write_json(sdir / "access.json", {"owner": GUEST, "allowFrom": [GUEST]})
    monkeypatch.setattr(server, "IS_GUEST_INSTANCE", True)
    monkeypatch.setattr(server, "GUEST_INSTANCE_UID", GUEST)
    monkeypatch.setattr(server, "STATE_DIR", sdir)
    monkeypatch.setattr(server, "ACCESS_FILE", sdir / "access.json")


def _resume_buttons(msg):
    kb = msg.answer.await_args.kwargs.get("reply_markup")
    return [row[0].callback_data for row in kb.inline_keyboard] if kb else []


def test_guest_resume_lists_only_own_threads(gstate, monkeypatch, resume_env):
    _as_guest(gstate, monkeypatch)
    msg = _share_msg("/resume")
    msg.from_user = SimpleNamespace(id=int(GUEST), username=None, first_name="G", last_name=None, is_bot=False)
    msg.chat.id = int(GUEST)
    asyncio.run(server.cmd_resume(msg))
    assert _resume_buttons(msg) == ["res:own1"]
    assert resume_env.bridge.list_threads.await_args.kwargs["cwd"] == resume_env.gcwd


def test_owner_resume_never_lists_guest_threads(gstate, resume_env):
    _write_access(gstate, {"owner": OWNER, "allowFrom": [OWNER, GUEST]})
    msg = _share_msg("/resume")
    asyncio.run(server.cmd_resume(msg))
    assert _resume_buttons(msg) == ["res:owner1"]
    assert resume_env.bridge.list_threads.await_args.kwargs["cwd"] is None


@pytest.mark.parametrize("tid", ["owner1", "other_cwd", "g2", "missing"])
def test_guest_forged_resume_callback_refused(gstate, monkeypatch, resume_env, tid):
    _as_guest(gstate, monkeypatch)
    cb = _cb(f"res:{tid}")
    asyncio.run(server.on_resume_pick(cb))
    resume_env.bridge.resume_thread.assert_not_awaited()
    assert "недоступен" in cb.answer.await_args.args[0]


def test_guest_resume_own_thread_ok(gstate, monkeypatch, resume_env):
    _as_guest(gstate, monkeypatch)
    cb = _cb("res:own1")
    cb.message.edit_text = AsyncMock()
    asyncio.run(server.on_resume_pick(cb))
    resume_env.bridge.resume_thread.assert_awaited_once_with("own1")


@pytest.mark.parametrize("tid", ["own1", "g2", "other_cwd"])
def test_owner_forged_resume_of_guest_thread_refused(gstate, resume_env, tid):
    _write_access(gstate, {"owner": OWNER, "allowFrom": [OWNER, GUEST]})
    cb = _cb(f"res:{tid}", uid=OWNER)
    asyncio.run(server.on_resume_pick(cb))
    resume_env.bridge.resume_thread.assert_not_awaited()


def test_resume_falls_back_to_list_when_read_has_no_preview(gstate, resume_env):
    _write_access(gstate, {"owner": OWNER, "allowFrom": [OWNER]})
    resume_env.bridge.read_thread = AsyncMock(return_value={"id": "owner1"})
    cb = _cb("res:owner1", uid=OWNER)
    cb.message.edit_text = AsyncMock()
    asyncio.run(server.on_resume_pick(cb))
    resume_env.bridge.resume_thread.assert_awaited_once_with("owner1")


# --------------------------------------------------------------------------- systemctl и юнит


def test_ensure_guest_running_enables_codex_unit(gstate, real_systemctl, monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda name: "/bin/systemctl")
    asyncio.run(server.ensure_guest_running(GUEST))
    args = real_systemctl.await_args.args
    assert args[:5] == ("systemctl", "enable", "--now", "--no-block", f"codex-telegram-guest@{GUEST}.service")
    assert (gstate.root / GUEST / "access.json").exists()


def test_stop_guest_disables_codex_unit(gstate, real_systemctl, monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda name: "/bin/systemctl")
    asyncio.run(server.stop_guest_instance(GUEST))
    assert real_systemctl.await_args.args[:5] == (
        "systemctl", "disable", "--now", "--no-block", f"codex-telegram-guest@{GUEST}.service")


@pytest.mark.parametrize("bad", ["../x", "1;rm", "", "abc"])
def test_systemctl_never_called_for_bad_uid(gstate, _no_real_systemctl, bad):
    asyncio.run(server.ensure_guest_running(bad))
    asyncio.run(server.stop_guest_instance(bad))
    _no_real_systemctl.assert_not_awaited()


def test_tests_never_reach_real_systemctl(_no_real_systemctl):
    assert server._systemctl is _no_real_systemctl


def test_guest_unit_template_contract():
    unit = (Path(REPO) / "deploy" / "codex-telegram-guest@.service").read_text("utf-8")
    assert "Environment=CODEX_GRAM_GUEST_UID=%i" in unit
    assert "Environment=TELEGRAM_STATE_DIR=/root/.codex-gram/telegram/guests/%i" in unit
    assert "Environment=CODEX_GRAM_CWD=/root/guests-codex/%i" in unit
    assert "Environment=CLAUDE_TELEGRAM_BACKGROUND=1" in unit
    assert "ExecStart=/usr/bin/python3 /root/codex-gram/server.py" in unit
    assert "KillMode=control-group" in unit and "Restart=always" in unit
    assert "TELEGRAM_BOT_TOKEN" not in unit.split("[Service]")[1].split("ExecStart")[0].replace(
        "# Токен бота", "")
    assert "CODEX_HOME=" not in unit
    assert server.GUEST_UNIT.format(uid="%i") == "codex-telegram-guest@%i.service"


# --------------------------------------------------------------------------- старт мастера и гостя


def _setup_bot(monkeypatch):
    fb = MagicMock()
    fb.get_me = AsyncMock(return_value=SimpleNamespace(username="CodexExeRobot"))
    fb.set_my_commands = AsyncMock()
    monkeypatch.setattr(server, "bot", fb)
    return fb


def test_master_setup_starts_every_guest(gstate, monkeypatch):
    _write_access(gstate, {"owner": OWNER, "allowFrom": [OWNER, GUEST, G2]})
    fb = _setup_bot(monkeypatch)
    ensure = AsyncMock()
    monkeypatch.setattr(server, "ensure_guest_running", ensure)
    asyncio.run(server._bot_setup())
    assert sorted(c.args[0] for c in ensure.await_args_list) == sorted([GUEST, G2])
    scopes = [type(c.kwargs["scope"]).__name__ for c in fb.set_my_commands.await_args_list]
    assert scopes.count("BotCommandScopeAllPrivateChats") == 1


def test_guest_on_shared_bot_does_not_touch_menus(gstate, monkeypatch):
    _as_guest(gstate, monkeypatch)
    monkeypatch.setattr(server, "GUEST_OWN_BOT", False)
    fb = _setup_bot(monkeypatch)
    ensure = AsyncMock()
    monkeypatch.setattr(server, "ensure_guest_running", ensure)
    asyncio.run(server._bot_setup())
    fb.set_my_commands.assert_not_awaited()  # иначе гость перетёр бы меню владельца
    ensure.assert_not_awaited()


def test_guest_with_own_bot_sets_guest_menu(gstate, monkeypatch):
    _as_guest(gstate, monkeypatch)
    monkeypatch.setattr(server, "GUEST_OWN_BOT", True)
    fb = _setup_bot(monkeypatch)
    asyncio.run(server._bot_setup())
    names = {c.command for c in fb.set_my_commands.await_args.args[0]}
    assert names == {c for c, _ in server.GUEST_BOT_COMMANDS}
