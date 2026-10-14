"""Guest versions of bot commands: each acts only on the guest's own runner/settings."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

import server  # noqa: E402  (sys.path set up by test_guest_runner import below)
from test_guest_runner import (  # noqa: F401  (fixtures)
    GUEST, GUEST2, KILLED, OWNER, FakeProc, bot, drain, flag_after, init, make_msg, result, spawner, state,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    monkeypatch.setattr(server, "USER_HOME", h)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return h


@pytest.fixture
def menu_bot(bot):
    bot.set_my_commands = AsyncMock()
    bot.delete_my_commands = AsyncMock()
    return bot


@pytest.fixture
def no_restart(monkeypatch):
    restart = MagicMock()
    monkeypatch.setattr(server, "safe_restart", restart)
    kill = MagicMock()
    monkeypatch.setattr(server.os, "kill", kill)
    return SimpleNamespace(restart=restart, kill=kill)


def make_cb(uid, data, chat=None):
    cb = MagicMock()
    cb.from_user = SimpleNamespace(id=int(uid), username="u", first_name="G", last_name=None, is_bot=False)
    cb.data = data
    cb.message.chat.id = int(chat or uid)
    cb.message.edit_text = AsyncMock()
    cb.message.edit_reply_markup = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def owner_files(state):
    """Снимок всего, что принадлежит владельцу в state-dir (без guests/)."""
    server.load_access()  # разовая миграция owner в access.json — до снимка
    snap = {}
    for p in sorted(state.rglob("*")):
        if p.is_file() and "guests" not in p.relative_to(state).parts:
            snap[str(p)] = p.read_bytes()
    return snap


def gfile(state, uid, name):
    return state / "guests" / uid / name


def run(coro):
    return asyncio.run(coro)


def run_once(uid, spawner, lines=None):
    async def go():
        spawner.scripts.append(FakeProc(lines or [init("s"), result("ok", "s")]))
        await server.enqueue_guest_message(uid, uid, "hi", "Pal")
        await drain(uid)
    asyncio.run(go())
    return spawner.calls[-1].cmd


# --------------------------------------------------------------------------- build_guest_cmd


def test_cmd_defaults_bypass_no_effort(state):
    cmd = server.build_guest_cmd(GUEST, ["--session-id", "a"])
    assert flag_after(cmd, "--permission-mode") == "bypassPermissions"
    assert "--effort" not in cmd
    assert "permissions" not in orjson.loads(flag_after(cmd, "--settings"))
    assert "Restricted mode" not in flag_after(cmd, "--append-system-prompt")


def test_cmd_effort_auto_off_goal(state):
    (state / "settings.json").write_bytes(orjson.dumps({"permissions": {"allow": ["Bash"]}}))
    cmd = server.build_guest_cmd(GUEST, ["--resume", "a"], "Pal", "m", effort="high", auto=False, goal="ship it")
    assert flag_after(cmd, "--effort") == "high"
    assert flag_after(cmd, "--permission-mode") == "dontAsk"
    settings = orjson.loads(flag_after(cmd, "--settings"))
    # deny перекрывает allow ["Bash"] из settings.json владельца
    assert set(server.GUEST_RESTRICTED_TOOLS) <= set(settings["permissions"]["deny"]) and "Bash" in settings["permissions"]["deny"]
    sp = flag_after(cmd, "--append-system-prompt")
    assert "ship it" in sp and "Restricted mode" in sp and "/auto" in sp
    assert cmd[-2:] == ["--resume", "a"]


def test_denials_parsed_and_reported(state, bot, spawner):
    line = orjson.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "не смог",
                         "session_id": "s", "permission_denials": [
                             {"tool_name": "Bash", "tool_use_id": "1", "tool_input": {}},
                             {"tool_name": "Bash", "tool_use_id": "2", "tool_input": {}},
                             {"tool_name": "Write", "tool_use_id": "3", "tool_input": {}}]}) + b"\n"
    assert server.parse_stream_event(line)[0] == ("denied", ["Bash", "Write"])
    run_once(GUEST, spawner, [init("s"), line])
    texts = bot.texts(GUEST)
    assert "не смог" in texts
    assert any(t.startswith("🔒 Отклонено") and "Bash, Write" in t and "/auto" in t for t in texts)


# --------------------------------------------------------------------------- /model


def test_guest_model_keyboard_and_callback_only_for_this_guest(state, bot, spawner):
    before = owner_files(state)
    msg = make_msg(GUEST, "/model")
    run(server.cmd_model(msg))
    kb = msg.answer.await_args.kwargs["reply_markup"]
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "gmodel:default" in data and all(d.startswith("gmodel:") for d in data)
    alias, full, _ = server.get_models()[0]
    cb = make_cb(GUEST, f"gmodel:{alias}")
    run(server.on_guest_model_callback(cb))
    assert gfile(state, GUEST, "model").read_text() == alias
    assert not gfile(state, GUEST2, "model").exists()
    assert owner_files(state) == before  # settings.json/access.json владельца не тронуты
    assert flag_after(run_once(GUEST, spawner), "--model") == full
    assert "--model" not in run_once(GUEST2, spawner)
    # сброс к умолчанию
    run(server.on_guest_model_callback(make_cb(GUEST, "gmodel:default")))
    assert not gfile(state, GUEST, "model").exists()


def test_guest_model_unknown_alias_rejected(state):
    cb = make_cb(GUEST, "gmodel:../../x")
    run(server.on_guest_model_callback(cb))
    cb.answer.assert_awaited_once_with("Неизвестная модель")
    assert not gfile(state, GUEST, "model").exists()


@pytest.mark.parametrize("handler,data", [("on_guest_model_callback", "gmodel:default"),
                                          ("on_guest_effort_callback", "geffort:high"),
                                          ("on_guest_resume_callback", "gres:new"),
                                          ("on_guest_goal_clear", "ggoal_clear")])
def test_guest_callbacks_denied_for_owner_and_foreign_chat(state, handler, data):
    gfile(state, GUEST, "x").parent.mkdir(parents=True)
    gfile(state, GUEST, "session_id").write_text("keep")
    for cb in (make_cb(OWNER, data), make_cb(GUEST, data, chat=GUEST2), make_cb("999", data)):
        run(getattr(server, handler)(cb))
        cb.answer.assert_awaited_once_with("Нет доступа.")
    assert gfile(state, GUEST, "session_id").read_text() == "keep"
    assert not gfile(state, GUEST, "effort").exists()


def test_owner_model_callback_still_owner_only(state):
    cb = make_cb(GUEST, "model:sonnet")
    run(server.on_model_callback(cb))
    cb.answer.assert_awaited_once_with("Нет доступа.")


# --------------------------------------------------------------------------- /effort


def test_guest_effort(state, bot, spawner):
    (state / "settings.json").write_bytes(orjson.dumps({"effortLevel": "low"}))
    before = owner_files(state)
    msg = make_msg(GUEST, "/effort")
    run(server.cmd_effort(msg))
    data = [b.callback_data for row in msg.answer.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert "geffort:max" in data and "geffort:default" in data
    run(server.on_guest_effort_callback(make_cb(GUEST, "geffort:max")))
    assert gfile(state, GUEST, "effort").read_text() == "max"
    assert owner_files(state) == before
    assert flag_after(run_once(GUEST, spawner), "--effort") == "max"
    assert "--effort" not in run_once(GUEST2, spawner)
    bad = make_cb(GUEST, "geffort:ultra")
    run(server.on_guest_effort_callback(bad))
    bad.answer.assert_awaited_once_with("Неизвестный уровень")
    assert gfile(state, GUEST, "effort").read_text() == "max"


# --------------------------------------------------------------------------- /auto


def test_guest_auto_toggle_per_guest(state, bot, spawner, monkeypatch):
    monkeypatch.setattr(server, "auto_allow", False)
    msg = make_msg(GUEST, "/auto")
    run(server.cmd_allows(msg))
    assert gfile(state, GUEST, "auto").read_text() == "off"
    assert "выключено" in msg.answer.await_args.args[0]
    assert server.auto_allow is False  # владельческий /auto не тронут
    cmd = run_once(GUEST, spawner)
    assert flag_after(cmd, "--permission-mode") == "dontAsk"
    assert flag_after(run_once(GUEST2, spawner), "--permission-mode") == "bypassPermissions"
    run(server.cmd_allows(make_msg(GUEST, "/auto")))
    assert not gfile(state, GUEST, "auto").exists()
    assert flag_after(run_once(GUEST, spawner), "--permission-mode") == "bypassPermissions"


def test_owner_auto_unchanged(state, monkeypatch):
    monkeypatch.setattr(server, "auto_allow", False)
    run(server.cmd_allows(make_msg(OWNER, "/auto")))
    assert server.auto_allow is True
    assert not (state / "guests").exists()


# --------------------------------------------------------------------------- /resume


def _session_file(home, uid, sid, text="старая задача"):
    d = server.guest_project_dir(uid)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    f.write_bytes(orjson.dumps({"type": "user", "message": {"content": text}}) + b"\n")
    return f


def test_guest_project_dir_key(state, home):
    work = str(server.guest_work_dir(GUEST))
    d = server.guest_project_dir(GUEST)
    assert d.parent == home / ".claude" / "projects"
    assert d.name == work.replace("/", "-").replace(".", "-").replace("_", "-")


def test_guest_resume_lists_own_sessions_and_selects(state, home, bot):
    _session_file(home, GUEST, "sid-a", "моя сессия")
    _session_file(home, GUEST2, "sid-b", "чужая сессия")
    owner_active = state / "active_session_id"
    owner_active.write_text("owner-sid")
    msg = make_msg(GUEST, "/resume")
    run(server.cmd_resume(msg))
    kb = msg.answer.await_args.kwargs["reply_markup"]
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert "gres:sel:sid-a" in data and "gres:sel:sid-b" not in data
    assert any("моя сессия" in t for t in texts) and not any("чужая" in t for t in texts)
    assert all(d.startswith("gres:") for d in data)

    run(server.on_guest_resume_callback(make_cb(GUEST, "gres:sel:sid-a")))
    assert server.read_guest_session_id(GUEST) == "sid-a"
    # чужую сессию и обход путей выбрать нельзя
    for bad in ("gres:sel:sid-b", "gres:sel:../sid-a"):
        cb = make_cb(GUEST, bad)
        run(server.on_guest_resume_callback(cb))
    assert server.read_guest_session_id(GUEST) == "sid-a"
    assert server.read_guest_session_id(GUEST2) == ""
    assert owner_active.read_text() == "owner-sid"

    run(server.on_guest_resume_callback(make_cb(GUEST, "gres:new")))
    assert server.read_guest_session_id(GUEST) == ""


def test_guest_resume_selected_session_used_by_runner(state, home, bot, spawner):
    _session_file(home, GUEST, "sid-a")
    run(server.on_guest_resume_callback(make_cb(GUEST, "gres:sel:sid-a")))
    assert run_once(GUEST, spawner, [init("sid-a"), result("ok", "sid-a")])[-2:] == ["--resume", "sid-a"]


def test_owner_resume_keyboard_unchanged(home):
    d = home / ".claude" / "projects" / "-root"
    d.mkdir(parents=True)
    (d / "o1.jsonl").write_bytes(orjson.dumps({"type": "user", "message": {"content": "x"}}) + b"\n")
    data = [b.callback_data for row in server.make_resume_keyboard(1).inline_keyboard for b in row]
    assert "res:sel:o1" in data and "res:new" in data and not any(x.startswith("gres") for x in data)


# --------------------------------------------------------------------------- /goal


def test_guest_goal_set_show_clear(state, bot, spawner):
    owner_goal = state / "session_goal.txt"
    msg = make_msg(GUEST, "/goal собрать отчёт")

    async def go():
        spawner.scripts.append(FakeProc([init("s"), result("ok", "s")]))
        await server.cmd_goal(msg)
        await drain(GUEST)
    run(go())
    assert gfile(state, GUEST, "goal").read_text() == "собрать отчёт"
    assert not owner_goal.exists() and server.load_goal() == ""
    call = spawner.calls[-1]
    assert call.proc.written == "собрать отчёт".encode()
    assert "собрать отчёт" in flag_after(call.cmd, "--append-system-prompt")
    assert "собрать отчёт" not in flag_after(run_once(GUEST2, spawner), "--append-system-prompt")

    show = make_msg(GUEST, "/goal")
    run(server.cmd_goal(show))
    assert "собрать отчёт" in show.answer.await_args.args[0]
    run(server.on_guest_goal_clear(make_cb(GUEST, "ggoal_clear")))
    assert not gfile(state, GUEST, "goal").exists()


# --------------------------------------------------------------------------- /close, /restart


def test_guest_close_is_new(state, bot):
    server.write_guest_session_id(GUEST, "s1")
    server.write_guest_session_id(GUEST2, "s2")
    msg = make_msg(GUEST, "/close")
    run(server.cmd_delete(msg))
    assert server.read_guest_session_id(GUEST) == ""
    assert server.read_guest_session_id(GUEST2) == "s2"
    assert "Новая сессия" in msg.answer.await_args.args[0]


def test_guest_restart_kills_only_own_runner(state, bot, spawner, no_restart):
    server.write_guest_session_id(GUEST, "keep")

    async def go():
        g1, g2 = asyncio.Event(), asyncio.Event()
        p1 = FakeProc([init("keep")], gate=g1)
        p2 = FakeProc([init("x")], gate=g2)
        spawner.scripts += [p1, p2]
        await server.enqueue_guest_message(GUEST, GUEST, "long", "Pal")
        await server.enqueue_guest_message(GUEST, GUEST, "queued", "Pal")
        await server.enqueue_guest_message(GUEST2, GUEST2, "other", "Pal")
        for _ in range(200):
            if len(spawner.calls) == 2:
                break
            await asyncio.sleep(0.005)
        msg = make_msg(GUEST, "/restart")
        await server.cmd_restart(msg)
        assert "Раннер перезапущен" in msg.answer.await_args.args[0]
        assert not server.guest_sessions[GUEST].queue
        g1.set()
        await drain(GUEST)
        assert server.guest_sessions[GUEST2].running  # другой гость работает дальше
        g2.set()
        await drain(GUEST2)
        return p1, p2
    p1, p2 = run(go())
    assert [pid for pid, _ in KILLED] == [p1.pid]
    no_restart.restart.assert_not_called()
    no_restart.kill.assert_not_called()
    assert len(spawner.calls) == 2  # «queued» не запускался
    assert server.read_guest_session_id(GUEST) == "keep"
    assert "⏹ Остановлено." in bot.texts(GUEST)


def test_guest_restart_idle(state, bot, no_restart):
    msg = make_msg(GUEST, "/restart")
    run(server.cmd_restart(msg))
    assert "ничего не выполнялось" in msg.answer.await_args.args[0]
    no_restart.restart.assert_not_called()


# --------------------------------------------------------------------------- /usage


def test_guest_usage_shows_shared_owner_limits(state, home, monkeypatch):
    creds = home / ".claude" / ".credentials.json"
    creds.write_text("{}")
    fetch = AsyncMock(return_value={"limits": [{"kind": "session", "percent": 40, "resets_at": None}]})
    monkeypatch.setattr(server, "fetch_usage_data", fetch)
    msg = make_msg(GUEST, "/usage")
    progress = SimpleNamespace(edit_text=AsyncMock())
    msg.answer = AsyncMock(return_value=progress)
    run(server.cmd_usage(msg))
    fetch.assert_awaited_once()
    assert "Лимиты" in progress.edit_text.await_args.args[0]


# --------------------------------------------------------------------------- owner-only + menus


@pytest.mark.parametrize("handler", ["cmd_accounts", "cmd_login", "cmd_save_account", "cmd_switch_account",
                                     "cmd_delete_account", "cmd_logout", "cmd_check_update", "cmd_mcp_debug",
                                     "cmd_share", "cmd_unshare", "cmd_access"])
def test_owner_only_commands_denied_to_guest(state, handler, no_restart):
    before = owner_files(state)

    async def go():
        msg = make_msg(GUEST, f"/x {GUEST2}")
        await getattr(server, handler)(msg)
        await asyncio.sleep(0)
        return msg
    msg = run(go())
    assert "только владельцу" in msg.answer.await_args.args[0]
    assert owner_files(state) == before
    no_restart.restart.assert_not_called()


def test_share_sets_and_unshare_clears_guest_menu(state, menu_bot):
    state.joinpath("access.json").write_bytes(orjson.dumps({"allowFrom": [OWNER]}))
    run(server.cmd_share(make_msg(OWNER, f"/share {GUEST}")))
    menu_bot.set_my_commands.assert_awaited_once()
    cmds, scope = menu_bot.set_my_commands.await_args.args[0], menu_bot.set_my_commands.await_args.kwargs["scope"]
    assert isinstance(scope, server.BotCommandScopeChat) and scope.chat_id == int(GUEST)
    names = {c.command for c in cmds}
    assert {"new", "stop", "model", "effort", "auto", "resume", "goal", "restart", "usage", "close"} == names
    assert not names & {"share", "unshare", "access", "check_update", "mcp_debug", "login", "accounts"}

    run(server.cmd_unshare(make_msg(OWNER, f"/unshare {GUEST}")))
    scope = menu_bot.delete_my_commands.await_args.kwargs["scope"]
    assert isinstance(scope, server.BotCommandScopeChat) and scope.chat_id == int(GUEST)


def test_failed_share_does_not_touch_menu(state, menu_bot):
    run(server.cmd_share(make_msg(OWNER, f"/share {OWNER}")))
    menu_bot.set_my_commands.assert_not_awaited()


def test_owner_new_stop_still_hint(state, bot):
    for h in (server.cmd_guest_new, server.cmd_guest_stop):
        msg = make_msg(OWNER, "/new")
        run(h(msg))
        assert msg.answer.await_args.args[0] == server.OWNER_GUEST_CMD_HINT
