"""Аккаунт Codex гостя: общий (владельца) по умолчанию, свой после /login гостя.

Главный инвариант: гость никогда не пишет в CODEX_HOME владельца. Владелец здесь —
временный каталог из conftest (CODEX_GRAM_CODEX_HOME), настоящий ~/.codex не трогается.
Вместо настоящего codex — tests/fixtures/fake_codex_cli.py (через CODEX_GRAM_BIN).
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402

OWNER = "111111111"
GUEST = "222222222"
FAKE_CLI = Path(__file__).parent / "fixtures" / "fake_codex_cli.py"
OWNER_AUTH = b'{"who": "OWNER-do-not-touch"}'


@pytest.fixture
def genv(tmp_path, monkeypatch, _isolate_codex_home):
    """Гостевой экземпляр в памяти: STATE_DIR = <owner>/guests/<uid>, access.json гостя."""
    owner_state = tmp_path / "owner"
    gdir = owner_state / "guests" / GUEST
    gdir.mkdir(parents=True)
    (gdir / "access.json").write_bytes(orjson.dumps({"owner": GUEST, "allowFrom": [GUEST]}))
    owner_home = _isolate_codex_home
    (owner_home / "auth.json").write_bytes(OWNER_AUTH)
    monkeypatch.setattr(server, "IS_GUEST_INSTANCE", True)
    monkeypatch.setattr(server, "GUEST_INSTANCE_UID", GUEST)
    monkeypatch.setattr(server, "STATE_DIR", gdir)
    monkeypatch.setattr(server, "OWNER_STATE_DIR", owner_state)
    monkeypatch.setattr(server, "ACCESS_FILE", gdir / "access.json")
    monkeypatch.setattr(server, "KNOWN_USERS_FILE", gdir / "known_users.json")
    monkeypatch.setattr(server, "_known_users_cache", None)
    monkeypatch.setattr(server, "session_thread_id", None)
    monkeypatch.setattr(server, "progress_bubbles", {})
    monkeypatch.setattr(server, "login_state", {})
    log = tmp_path / "fake_codex.jsonl"
    monkeypatch.setenv("CODEX_GRAM_BIN", str(FAKE_CLI))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    monkeypatch.delenv("FAKE_CODEX_RC", raising=False)
    monkeypatch.delenv("FAKE_CODEX_NO_AUTH", raising=False)
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
    fake_bot.delete_message = AsyncMock()
    monkeypatch.setattr(server, "bot", fake_bot)
    bridge = MagicMock()
    bridge.restart_child = AsyncMock(return_value=True)
    bridge.ready = True
    bridge.account = AsyncMock(return_value={"account": {"email": "guest@x", "planType": "plus"}})
    monkeypatch.setattr(server, "codex_bridge", bridge)
    return SimpleNamespace(gdir=gdir, owner_home=owner_home, log=log, bot=fake_bot, bridge=bridge,
                           guest_home=gdir / "codex")


def cli_calls(env):
    if not env.log.exists():
        return []
    return [json.loads(x) for x in env.log.read_text("utf-8").splitlines() if x.strip()]


def gmsg(text, uid=GUEST):
    msg = MagicMock()
    msg.from_user = SimpleNamespace(id=int(uid), username=None, first_name="G", last_name=None, is_bot=False)
    msg.chat.id = int(uid)
    msg.chat.type = server.ChatType.PRIVATE
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def owner_auth_untouched(env):
    return (env.owner_home / "auth.json").read_bytes() == OWNER_AUTH


def sent_texts(env):
    return [c.args[1] for c in env.bot.send_message.await_args_list]


# --------------------------------------------------------------------------- режим по умолчанию


def test_default_is_shared_owner_account(genv):
    assert server.guest_account_mode() == server.GUEST_MODE_SHARED
    assert server.codex_env()["CODEX_HOME"] == str(genv.owner_home)
    assert server.effective_codex_home() == genv.owner_home


def test_own_mode_without_auth_falls_back_to_shared(genv):
    server.set_guest_account_mode(server.GUEST_MODE_OWN)
    assert server.guest_account_mode() == server.GUEST_MODE_SHARED
    assert server.codex_env()["CODEX_HOME"] == str(genv.owner_home)


def test_symlinked_auth_is_not_an_own_account(genv):
    genv.guest_home.mkdir()
    os.symlink(genv.owner_home / "auth.json", genv.guest_home / "auth.json")
    server.set_guest_account_mode(server.GUEST_MODE_OWN)
    assert server.guest_account_mode() == server.GUEST_MODE_SHARED


def test_owner_process_never_uses_guest_mode(genv, monkeypatch):
    monkeypatch.setattr(server, "IS_GUEST_INSTANCE", False)
    genv.guest_home.mkdir()
    (genv.guest_home / "auth.json").write_text("{}")
    server.set_guest_account_mode(server.GUEST_MODE_OWN)
    assert server.codex_env()["CODEX_HOME"] == str(genv.owner_home)


def test_account_mode_file_is_private(genv):
    server.set_guest_account_mode(server.GUEST_MODE_SHARED)
    assert oct(os.stat(genv.gdir / server.GUEST_ACCOUNT_FILE_NAME).st_mode & 0o777) == "0o600"
    with pytest.raises(ValueError):
        server.set_guest_account_mode("../../owner")


# --------------------------------------------------------------------------- /login гостя


def _login(msg_text="/login"):
    async def run():
        await server.cmd_login(gmsg(msg_text))
        task = server.login_state.get("task")
        assert task is not None
        await asyncio.wait_for(task, 30)
    asyncio.run(run())


def test_guest_login_writes_only_guest_codex_home(genv):
    _login()
    calls = cli_calls(genv)
    assert calls == [{"argv": ["login", "--device-auth"], "CODEX_HOME": str(genv.guest_home)}]
    assert (genv.guest_home / "auth.json").read_text() == '{"who": "guest-login"}'
    assert owner_auth_untouched(genv)
    assert server.guest_account_mode() == server.GUEST_MODE_OWN
    assert server.codex_env()["CODEX_HOME"] == str(genv.guest_home)
    genv.bridge.restart_child.assert_awaited_once()
    assert oct(os.stat(genv.guest_home).st_mode & 0o777) == "0o700"
    texts = sent_texts(genv)
    assert any("ABCD-EFG1" in t for t in texts)
    assert any("Вход выполнен" in t for t in texts)


def test_guest_login_failure_keeps_shared(genv, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_RC", "1")
    _login()
    assert server.guest_account_mode() == server.GUEST_MODE_SHARED
    assert owner_auth_untouched(genv)
    genv.bridge.restart_child.assert_not_awaited()


def test_guest_login_rc0_without_auth_keeps_shared(genv, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_NO_AUTH", "1")
    _login()
    assert server.guest_account_mode() == server.GUEST_MODE_SHARED
    assert any("auth.json не появился" in t for t in sent_texts(genv))
    genv.bridge.restart_child.assert_not_awaited()


def test_guest_login_refuses_symlinked_codex_dir(genv, tmp_path):
    os.symlink(genv.owner_home, genv.guest_home)  # попытка подсунуть ~/.codex владельца
    msg = gmsg("/login")
    asyncio.run(server.cmd_login(msg))
    assert server.login_state.get("task") is None
    assert cli_calls(genv) == []
    assert owner_auth_untouched(genv)
    msg.answer.assert_awaited_once()


def test_guest_login_ignores_owner_codex_home_env(genv, monkeypatch):
    """Даже если в окружении стоит CODEX_HOME владельца, login гостя идёт в его каталог."""
    monkeypatch.setenv("CODEX_HOME", str(genv.owner_home))
    _login()
    assert cli_calls(genv)[0]["CODEX_HOME"] == str(genv.guest_home)
    assert owner_auth_untouched(genv)


def test_owner_login_still_uses_owner_home(genv, monkeypatch):
    monkeypatch.setattr(server, "IS_GUEST_INSTANCE", False)
    (genv.gdir / "access.json").write_bytes(orjson.dumps({"owner": OWNER, "allowFrom": [OWNER]}))

    async def run():
        await server.cmd_login(gmsg("/login", uid=OWNER))
        await asyncio.wait_for(server.login_state["task"], 30)

    asyncio.run(run())
    assert cli_calls(genv)[0]["CODEX_HOME"] == str(genv.owner_home)
    assert not genv.guest_home.exists()


# --------------------------------------------------------------------------- /logout, /switch_account


def _own_account(genv):
    genv.guest_home.mkdir(exist_ok=True)
    (genv.guest_home / "auth.json").write_text('{"who": "guest"}')
    server.set_guest_account_mode(server.GUEST_MODE_OWN)


def test_guest_logout_on_shared_never_runs_cli(genv):
    msg = gmsg("/logout")
    asyncio.run(server.cmd_logout(msg))
    assert cli_calls(genv) == []
    assert owner_auth_untouched(genv)
    assert "общем аккаунте" in msg.answer.await_args.args[0]
    genv.bridge.restart_child.assert_not_awaited()


def test_guest_logout_on_own_logs_out_guest_home_only(genv):
    _own_account(genv)
    asyncio.run(server.cmd_logout(gmsg("/logout")))
    assert cli_calls(genv) == [{"argv": ["logout"], "CODEX_HOME": str(genv.guest_home)}]
    assert not (genv.guest_home / "auth.json").exists()
    assert owner_auth_untouched(genv)
    assert server.guest_account_mode() == server.GUEST_MODE_SHARED
    genv.bridge.restart_child.assert_awaited_once()


def test_guest_logout_failure_keeps_own(genv, monkeypatch):
    _own_account(genv)
    monkeypatch.setenv("FAKE_CODEX_RC", "3")
    asyncio.run(server.cmd_logout(gmsg("/logout")))
    assert server.guest_account_mode() == server.GUEST_MODE_OWN
    genv.bridge.restart_child.assert_not_awaited()


def test_switch_to_owner_and_back(genv):
    _own_account(genv)
    asyncio.run(server.cmd_switch_account(gmsg("/switch_account owner")))
    assert server.guest_account_mode() == server.GUEST_MODE_SHARED
    assert (genv.guest_home / "auth.json").exists()  # свой auth.json остаётся
    asyncio.run(server.cmd_switch_account(gmsg("/switch_account own")))
    assert server.guest_account_mode() == server.GUEST_MODE_OWN
    assert genv.bridge.restart_child.await_count == 2
    assert owner_auth_untouched(genv)
    assert cli_calls(genv) == []


def test_switch_to_own_without_login_refused(genv):
    msg = gmsg("/switch_account own")
    asyncio.run(server.cmd_switch_account(msg))
    assert server.guest_account_mode() == server.GUEST_MODE_SHARED
    assert "/login" in msg.answer.await_args.args[0]
    genv.bridge.restart_child.assert_not_awaited()


def test_switch_unknown_name_does_not_touch_saved_owner_accounts(genv):
    # даже если в каталоге гостя кто-то положил «сохранённый аккаунт», гостевой /switch_account
    # не работает с сохранёнными копиями и не пишет в CODEX_HOME
    acc = genv.gdir / "accounts" / "evil"
    acc.mkdir(parents=True)
    (acc / "auth.json").write_text('{"who": "evil"}')
    asyncio.run(server.cmd_switch_account(gmsg("/switch_account evil")))
    assert owner_auth_untouched(genv)
    assert server.guest_account_mode() == server.GUEST_MODE_SHARED


def test_guest_accounts_does_not_show_owner_email(genv):
    msg = gmsg("/accounts")
    asyncio.run(server.cmd_accounts(msg))
    text = msg.answer.await_args.args[0]
    assert "общий аккаунт владельца" in text
    genv.bridge.account.assert_not_awaited()


def test_account_line_hides_owner_email_on_shared(genv):
    assert asyncio.run(server._account_line()) == "общий аккаунт владельца"
    _own_account(genv)
    assert "guest@x" in asyncio.run(server._account_line())


@pytest.mark.parametrize("data", ["gacc:shared", "gacc:own", "gacc:../x"])
def test_guest_account_callback_ignored_in_owner_process(genv, monkeypatch, data):
    monkeypatch.setattr(server, "IS_GUEST_INSTANCE", False)
    cb = MagicMock()
    cb.data = data
    cb.from_user = SimpleNamespace(id=int(GUEST))
    cb.answer = AsyncMock()
    asyncio.run(server.on_guest_account_pick(cb))
    cb.answer.assert_awaited_once_with("Нет доступа.")
    assert not (genv.gdir / server.GUEST_ACCOUNT_FILE_NAME).exists()


def test_guest_account_callback_switches(genv):
    _own_account(genv)
    cb = MagicMock()
    cb.data = "gacc:shared"
    cb.from_user = SimpleNamespace(id=int(GUEST))
    cb.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    asyncio.run(server.on_guest_account_pick(cb))
    assert server.guest_account_mode() == server.GUEST_MODE_SHARED


# --------------------------------------------------------------------------- владельческие аккаунт-команды


@pytest.mark.parametrize("handler, text", [
    ("cmd_save_account", "/save_account mine"),
    ("cmd_delete_account", "/delete_account"),
    ("cmd_check_update", "/check_update"),
    ("cmd_share", f"/share {OWNER}"),
    ("cmd_unshare", "/unshare 333333333"),
    ("cmd_access", "/access"),
    ("cmd_mcp_debug", "/mcp_debug"),
])
def test_owner_commands_refused_inside_guest_handlers(genv, handler, text):
    before = (genv.gdir / "access.json").read_bytes()
    msg = gmsg(text)
    asyncio.run(getattr(server, handler)(msg))
    msg.answer.assert_awaited_once_with(server.GUEST_BLOCKED_TEXT)
    assert (genv.gdir / "access.json").read_bytes() == before
    assert not (genv.gdir / "accounts").exists()
    assert owner_auth_untouched(genv)
    assert cli_calls(genv) == []


@pytest.mark.parametrize("handler, data", [
    ("on_switch_pick", "acc:owner_main"),
    ("on_delete_account_pick", "accdel:owner_main"),
    ("on_update_go", "upd:go"),
])
def test_owner_account_callbacks_refused_in_guest(genv, handler, data):
    # Сохранённый аккаунт в каталоге гостя: подделанная кнопка не должна переписать auth.json.
    saved = genv.gdir / "accounts" / "owner_main"
    saved.mkdir(parents=True)
    (saved / "auth.json").write_text('{"who": "evil"}')
    cb = MagicMock()
    cb.data = data
    cb.from_user = SimpleNamespace(id=int(GUEST))
    cb.answer = AsyncMock()
    asyncio.run(getattr(server, handler)(cb))
    cb.answer.assert_awaited_once_with("Нет доступа.")
    assert owner_auth_untouched(genv)
    assert (saved / "auth.json").exists()


# --------------------------------------------------------------------------- мост и MCP гостя


def test_guest_mcp_env_follows_account_and_has_no_token(genv, monkeypatch):
    monkeypatch.setattr(server, "TOKEN", "123:SECRET_TOKEN_VALUE")
    env = server.build_codex_mcp_servers()["telegram"]["env"]
    assert env["CODEX_GRAM_GUEST_UID"] == GUEST
    assert env["TELEGRAM_STATE_DIR"] == str(genv.gdir)
    assert env["CODEX_GRAM_CODEX_HOME"] == str(genv.owner_home)
    assert "SECRET_TOKEN_VALUE" not in repr(env)
    _own_account(genv)
    assert server.build_codex_mcp_servers()["telegram"]["env"]["CODEX_GRAM_CODEX_HOME"] == str(genv.guest_home)


def test_guest_bridge_uses_guest_env_cwd_and_instructions(genv, monkeypatch, tmp_path):
    mem = tmp_path / "mem"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("- [S](s.md) — SECRET_MEMORY_LINE")
    monkeypatch.setenv("CODEX_GRAM_MEMORY_DIR", str(mem))
    monkeypatch.delenv("CODEX_GRAM_CWD", raising=False)
    b = server.build_codex_bridge()
    assert b.env_factory is server.codex_env
    assert b.cwd == str(server.guest_work_dir(GUEST))
    text = b.instructions()
    assert "guest instance" in text and GUEST in text
    assert "SECRET_MEMORY_LINE" not in text and "Рабочий регламент владельца" not in text
    params = b._thread_params()
    assert params["config"]["mcp_servers"]["telegram"]["env"]["CODEX_GRAM_GUEST_UID"] == GUEST


def test_guest_generated_images_in_own_codex_home_are_sendable(genv, monkeypatch):
    img = genv.guest_home / "generated_images" / "t" / "pic.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"png")
    (genv.guest_home / "auth.json").write_text("{}")
    monkeypatch.setenv("CODEX_GRAM_CODEX_HOME", str(genv.guest_home))
    server.assert_sendable(str(img))
    with pytest.raises(ValueError):
        server.assert_sendable(str(genv.guest_home / "auth.json"))
    with pytest.raises(ValueError):
        server.assert_sendable(str(genv.gdir / "access.json"))
