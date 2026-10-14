"""Independent guest sessions: headless claude runner per guest (subprocess mocked)."""
import asyncio
import datetime
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402
from aiogram.enums import ChatType  # noqa: E402

OWNER = "111111111"
GUEST = "222222222"
GUEST2 = "444444444"
KILLED = []


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    work_root = tmp_path / "work"  # как в проде: рабочие каталоги вне STATE_DIR
    tmp_path = tmp_path / "state"
    tmp_path.mkdir()
    monkeypatch.setattr(server, "STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "ACCESS_FILE", tmp_path / "access.json")
    monkeypatch.setattr(server, "KNOWN_USERS_FILE", tmp_path / "known_users.json")
    monkeypatch.setattr(server, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(server, "_known_users_cache", None)
    monkeypatch.setattr(server, "session_thread_id", None)
    monkeypatch.setattr(server, "guest_sessions", {})
    monkeypatch.setattr(server, "GUEST_STATUS_INTERVAL", 0.01)
    monkeypatch.setenv("CLAUDE_GRAM_GUESTS_DIR", str(work_root))
    monkeypatch.delenv("CLAUDE_GRAM_GUEST_MODEL", raising=False)
    # Никаких реальных сигналов: фейковые pid не должны задеть живые процессы.
    KILLED.clear()
    monkeypatch.setattr(server.os, "killpg", lambda pid, sig: KILLED.append((pid, sig)))
    (tmp_path / "access.json").write_bytes(orjson.dumps({"allowFrom": [OWNER, GUEST, GUEST2]}))
    return tmp_path


class FakeBot:
    def __init__(self):
        self.sent = []  # (chat_id, text, kwargs)
        self._id = 100
        self.edit_message_text = AsyncMock()
        self.delete_message = AsyncMock()
        self.send_document = AsyncMock()
        self.send_photo = AsyncMock()
        self.send_audio = AsyncMock()

    async def send_message(self, chat_id, text, **kw):
        self._id += 1
        self.sent.append((chat_id, text, kw))
        return SimpleNamespace(message_id=self._id)

    def texts(self, chat_id=None):
        return [t for c, t, _ in self.sent if chat_id is None or c == chat_id]


@pytest.fixture
def bot(monkeypatch):
    b = FakeBot()
    monkeypatch.setattr(server, "bot", b)
    return b


def ev(obj) -> bytes:
    return orjson.dumps(obj) + b"\n"


def init(sid):
    return ev({"type": "system", "subtype": "init", "session_id": sid, "tools": []})


def assistant_tool(name, inp):
    return ev({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}})


def assistant_text(text):
    return ev({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})


def result(text, sid="", is_error=False, subtype="success"):
    return ev({"type": "result", "subtype": subtype, "is_error": is_error, "result": text, "session_id": sid})


class FakeStream:
    def __init__(self, lines, gate=None):
        self.lines = list(lines)
        self.gate = gate  # asyncio.Event: перед EOF ждём (имитация долгой работы)

    async def readline(self):
        if self.lines:
            return self.lines.pop(0)
        if self.gate is not None:
            await self.gate.wait()
        return b""


class FakeProc:
    _pid = 900000

    def __init__(self, lines, rc=0, stderr=b"", gate=None):
        FakeProc._pid += 1
        self.pid = FakeProc._pid
        self.returncode = None
        self._rc = rc
        self.stdin = MagicMock()
        self.stdin.drain = AsyncMock()
        self.stdout = FakeStream(lines, gate)
        self.gate = gate
        self.stderr = SimpleNamespace(read=AsyncMock(return_value=stderr))
        self.written = b""
        self.stdin.write.side_effect = lambda b: setattr(self, "written", self.written + b)

    async def wait(self):
        if self.gate is not None:
            await self.gate.wait()
        self.returncode = self._rc
        return self._rc


@pytest.fixture
def spawner(monkeypatch):
    """Подменяет _spawn_guest_process; процессы выдаются из очереди scripts."""
    calls = []
    scripts = []

    async def fake_spawn(cmd, cwd, env):
        proc = scripts.pop(0)
        calls.append(SimpleNamespace(cmd=cmd, cwd=cwd, env=env, proc=proc))
        return proc

    monkeypatch.setattr(server, "_spawn_guest_process", fake_spawn)
    return SimpleNamespace(calls=calls, scripts=scripts)


def make_msg(uid, text="hi"):
    msg = MagicMock()
    msg.from_user = SimpleNamespace(id=int(uid), username="u" + uid[:3], first_name="Guest", last_name=None, is_bot=False)
    msg.chat.id = int(uid)
    msg.chat.type = ChatType.PRIVATE
    msg.text = text
    msg.message_id = 42
    msg.message_thread_id = None
    msg.reply_to_message = None
    msg.date = datetime.datetime(2026, 10, 14, tzinfo=datetime.timezone.utc)
    msg.answer = AsyncMock()
    return msg


async def drain(uid):
    gs = server.guest_sessions.get(uid)
    for _ in range(500):
        if gs is None or gs.worker is None:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("guest worker did not finish")


def flag_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


# --------------------------------------------------------------------------- pure helpers


def test_parse_stream_events():
    assert server.parse_stream_event(init("s1")) == [("init", "s1")]
    assert server.parse_stream_event(assistant_tool("Bash", {"command": "ls -la"})) == [("tool", "Bash: ls -la")]
    assert server.parse_stream_event(assistant_text("hi")) == [("text", "hi")]
    assert server.parse_stream_event(result("done", "s1")) == [("result", "done", False, "s1")]
    assert server.parse_stream_event(result("", "s1", True, "error_max_turns"))[0][:3] == ("result", "error_max_turns", True)
    assert server.parse_stream_event(b"not json\n") == []
    assert server.parse_stream_event(b"\n") == []
    assert server.parse_stream_event(ev({"type": "rate_limit_event"})) == []


def test_describe_tool_prefers_description_and_truncates():
    assert server.describe_tool_use("Bash", {"command": "x", "description": "List files"}) == "Bash: List files"
    long = server.describe_tool_use("Read", {"file_path": "/a/" + "b" * 200})
    assert long.startswith("Read: /a/") and len(long) < 90 and long.endswith("…")
    assert server.describe_tool_use("TodoWrite", None) == "TodoWrite"


def test_extract_send_files():
    text = "Вот отчёт.\nSEND_FILE: /root/guests/1/report.pdf\n  SEND_FILE: `/tmp/a b.png`  \nКонец"
    clean, files = server.extract_send_files(text)
    assert files == ["/root/guests/1/report.pdf", "/tmp/a b.png"]
    assert "SEND_FILE" not in clean and clean.startswith("Вот отчёт.") and clean.endswith("Конец")
    assert server.extract_send_files("no files") == ("no files", [])


def test_split_long_message():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(300))
    parts = server.split_message(text)
    assert len(parts) > 1 and all(len(p) <= 4096 for p in parts)
    assert "".join(p.replace("\n", "") for p in parts) == text.replace("\n", "")
    assert server.split_message("short") == ["short"]


def test_build_guest_prompt_with_attachments():
    p = server.build_guest_prompt("look", {"image_path": "/x/1.jpg", "image_paths": "/x/1.jpg,/x/2.jpg",
                                           "file_path": "/x/doc.pdf", "file_name": "doc.pdf"})
    assert p.startswith("look")
    assert "/x/1.jpg, /x/2.jpg" in p and "/x/doc.pdf" in p and "doc.pdf" in p


def test_guest_cmd_isolated_and_bypass(state):
    (state / "settings.json").write_bytes(orjson.dumps({"enabledPlugins": {
        "claude-gram@justidev-marketplace": True, "claude-gram@other": True, "foo@bar": True}}))
    cmd = server.build_guest_cmd(GUEST, ["--session-id", "abc"], "Pal", "claude-opus-5-5")
    assert "-p" in cmd and flag_after(cmd, "--output-format") == "stream-json" and "--verbose" in cmd
    assert flag_after(cmd, "--permission-mode") == "bypassPermissions"
    assert "--strict-mcp-config" in cmd
    assert orjson.loads(flag_after(cmd, "--mcp-config")) == {"mcpServers": {}}
    plugins = orjson.loads(flag_after(cmd, "--settings"))["enabledPlugins"]
    assert plugins["claude-gram@justidev-marketplace"] is False and plugins["claude-gram@other"] is False
    assert "foo@bar" not in plugins
    assert "--channels" not in cmd
    assert flag_after(cmd, "--model") == "claude-opus-5-5"
    assert cmd[-2:] == ["--session-id", "abc"]
    sp = flag_after(cmd, "--append-system-prompt")
    assert "Pal" in sp and "SEND_FILE:" in sp and str(server.guest_work_dir(GUEST)) in sp


def test_guest_env_strips_parent_session(monkeypatch):
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID",
              "CLAUDE_TELEGRAM_BACKGROUND", "TELEGRAM_BOT_TOKEN", "CLAUDE_PID"):
        monkeypatch.setenv(k, "1")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "keep")
    monkeypatch.delenv("IS_SANDBOX", raising=False)
    env = server.guest_env()
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID",
              "CLAUDE_TELEGRAM_BACKGROUND", "TELEGRAM_BOT_TOKEN", "CLAUDE_PID"):
        assert k not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep"
    assert env["IS_SANDBOX"] == "1" and env["CLAUDE_ALLOW_ROOT"] == "1"
    assert env.get("PATH") == os.environ.get("PATH")


def test_guest_model_resolution(state, monkeypatch):
    assert server.guest_model() is None
    (state / "last_launched_model").write_text("claude-opus-5-5\n")
    assert server.guest_model() == "claude-opus-5-5"
    (state / "guest_model").write_text("claude-sonnet-5")
    assert server.guest_model() == "claude-sonnet-5"
    monkeypatch.setenv("CLAUDE_GRAM_GUEST_MODEL", "claude-haiku-4-5")
    assert server.guest_model() == "claude-haiku-4-5"


def test_work_dir_outside_owner_state(state):
    work = server.guest_work_dir(GUEST)
    assert work == state.parent / "work" / GUEST
    assert server.guest_state_dir(GUEST) == state / "guests" / GUEST


# --------------------------------------------------------------------------- runner end-to-end


def test_first_run_new_session_then_resume(state, bot, spawner):
    async def go():
        spawner.scripts.append(FakeProc([init("sid-1"), assistant_tool("Bash", {"command": "ls"}),
                                         assistant_text("thinking"), result("Привет <b>!</b>", "sid-1")]))
        await server.enqueue_guest_message(GUEST, GUEST, "hi", "Pal")
        await drain(GUEST)
        spawner.scripts.append(FakeProc([init("sid-1"), result("second", "sid-1")]))
        await server.enqueue_guest_message(GUEST, GUEST, "again", "Pal")
        await drain(GUEST)

    asyncio.run(go())
    first, second = spawner.calls
    assert first.cmd[-2] == "--session-id"
    # session_id берётся из init-события claude (в проде совпадает с --session-id)
    assert (state / "guests" / GUEST / "session_id").read_text() == "sid-1"
    assert second.cmd[-2:] == ["--resume", "sid-1"]
    assert first.cwd == str(state.parent / "work" / GUEST) and Path(first.cwd).is_dir()
    assert first.proc.written == "hi".encode()
    assert "CLAUDE_TELEGRAM_BACKGROUND" not in first.env
    texts = bot.texts(GUEST)
    assert texts[0].startswith("⏳")
    # финальный текст как plain text (без parse_mode), разметка не интерпретируется
    final = [(t, kw) for c, t, kw in bot.sent if t == "Привет <b>!</b>"]
    assert final and "parse_mode" not in final[0][1]
    assert "second" in texts
    assert bot.delete_message.await_count == 2  # статус-пузыри убраны
    assert not any(c == OWNER for c, _, _ in bot.sent)


def test_inbound_guest_routed_to_runner_no_owner_notify(state, bot, spawner, monkeypatch):
    deliver = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(server, "deliver", deliver)
    monkeypatch.setattr(server, "notify", notify)

    async def go():
        spawner.scripts.append(FakeProc([init("s"), result("ok", "s")]))
        await server.handle_inbound(make_msg(GUEST, "сделай что-нибудь"), "сделай что-нибудь")
        await drain(GUEST)

    asyncio.run(go())
    deliver.assert_not_awaited()
    notify.assert_not_awaited()
    assert len(spawner.calls) == 1 and "сделай что-нибудь" in spawner.calls[0].proc.written.decode()
    assert not any(c == OWNER for c, _, _ in bot.sent)
    assert "ok" in bot.texts(GUEST)


def test_send_file_lines_are_sent(state, bot, spawner):
    work = state.parent / "work" / GUEST
    work.mkdir(parents=True)
    pdf = work / "report.pdf"
    pdf.write_bytes(b"%PDF")
    png = work / "pic.png"
    png.write_bytes(b"png")

    async def go():
        spawner.scripts.append(FakeProc([init("s"), result(
            f"Готово\nSEND_FILE: {pdf}\nSEND_FILE: {png}\nSEND_FILE: /nonexistent/x.txt", "s")]))
        await server.enqueue_guest_message(GUEST, GUEST, "x")
        await drain(GUEST)

    asyncio.run(go())
    assert "Готово" in bot.texts(GUEST)
    bot.send_document.assert_awaited_once()
    bot.send_photo.assert_awaited_once()
    assert any("Не удалось отправить" in t and "/nonexistent/x.txt" in t for t in bot.texts(GUEST))


def test_long_answer_split(state, bot, spawner):
    long = "\n".join("строка " + "я" * 80 for _ in range(150))

    async def go():
        spawner.scripts.append(FakeProc([init("s"), result(long, "s")]))
        await server.enqueue_guest_message(GUEST, GUEST, "x")
        await drain(GUEST)

    asyncio.run(go())
    parts = [t for t in bot.texts(GUEST) if t.startswith("строка")]
    assert len(parts) >= 3 and all(len(p) <= 4096 for p in parts)


def test_error_result_reported(state, bot, spawner):
    async def go():
        spawner.scripts.append(FakeProc([init("s"), result("API Error: overloaded", "s", is_error=True)], rc=1))
        await server.enqueue_guest_message(GUEST, GUEST, "x")
        await drain(GUEST)

    asyncio.run(go())
    assert any(t.startswith("⚠️ Ошибка Claude") and "overloaded" in t for t in bot.texts(GUEST))


def test_missing_session_restarts_fresh(state, bot, spawner):
    server.write_guest_session_id(GUEST, "gone-sid")

    async def go():
        spawner.scripts.append(FakeProc([], rc=1, stderr=b"No conversation found with session ID: gone-sid"))
        spawner.scripts.append(FakeProc([init("fresh"), result("hello", "fresh")]))
        await server.enqueue_guest_message(GUEST, GUEST, "x")
        await drain(GUEST)

    asyncio.run(go())
    assert spawner.calls[0].cmd[-2:] == ["--resume", "gone-sid"]
    assert spawner.calls[1].cmd[-2] == "--session-id"
    assert server.read_guest_session_id(GUEST) == "fresh"
    assert "hello" in bot.texts(GUEST)


def test_queue_one_run_at_a_time(state, bot, spawner):
    order = []

    async def go():
        gate1 = asyncio.Event()
        p1 = FakeProc([init("s"), result("one", "s")], gate=gate1)
        p2 = FakeProc([init("s"), result("two", "s")])
        spawner.scripts.extend([p1, p2])
        await server.enqueue_guest_message(GUEST, GUEST, "first")
        await server.enqueue_guest_message(GUEST, GUEST, "second")
        await asyncio.sleep(0.05)
        order.append(len(spawner.calls))  # пока первый работает — второй не стартовал
        assert server.guest_is_busy(GUEST)
        assert "📥" in "".join(bot.texts(GUEST))
        gate1.set()
        await drain(GUEST)
        order.append(len(spawner.calls))

    asyncio.run(go())
    assert order == [1, 2]
    assert spawner.calls[1].proc.written == b"second"
    texts = bot.texts(GUEST)
    assert texts.index("one") < texts.index("two")
    assert not server.guest_is_busy(GUEST)


def test_guests_run_in_parallel_independently(state, bot, spawner):
    async def go():
        gate = asyncio.Event()
        spawner.scripts.extend([FakeProc([init("a"), result("A", "a")], gate=gate),
                                FakeProc([init("b"), result("B", "b")])])
        await server.enqueue_guest_message(GUEST, GUEST, "x")
        await server.enqueue_guest_message(GUEST2, GUEST2, "y")
        await drain(GUEST2)
        assert "B" in bot.texts(GUEST2) and server.guest_is_busy(GUEST)
        gate.set()
        await drain(GUEST)

    asyncio.run(go())
    assert spawner.calls[0].cwd != spawner.calls[1].cwd


def test_stop_command_kills_current_run_and_clears_queue(state, bot, spawner):
    msg = make_msg(GUEST, "/stop")

    async def go():
        gate = asyncio.Event()
        p1 = FakeProc([init("s")], gate=gate)
        spawner.scripts.append(p1)
        await server.enqueue_guest_message(GUEST, GUEST, "long task")
        await server.enqueue_guest_message(GUEST, GUEST, "queued")
        await asyncio.sleep(0.05)
        await server.cmd_guest_stop(msg)
        assert (p1.pid, server.signal.SIGTERM) in KILLED
        gate.set()  # процесс «умер»
        await drain(GUEST)

    asyncio.run(go())
    assert len(spawner.calls) == 1  # очередь очищена
    assert "⏹ Остановлено." in bot.texts(GUEST)
    msg.answer.assert_not_awaited()


def test_stop_when_idle(state, bot):
    msg = make_msg(GUEST, "/stop")
    asyncio.run(server.cmd_guest_stop(msg))
    msg.answer.assert_awaited_once()
    assert "ничего" in msg.answer.await_args.args[0]


def test_new_command_resets_session(state, bot, spawner):
    server.write_guest_session_id(GUEST, "old-sid")
    msg = make_msg(GUEST, "/new")

    async def go():
        await server.cmd_guest_new(msg)
        spawner.scripts.append(FakeProc([init("new-sid"), result("ok", "new-sid")]))
        await server.enqueue_guest_message(GUEST, GUEST, "x")
        await drain(GUEST)

    asyncio.run(go())
    assert "Новая сессия" in msg.answer.await_args.args[0]
    assert spawner.calls[0].cmd[-2] == "--session-id"
    assert server.read_guest_session_id(GUEST) == "new-sid"


def test_new_during_run_does_not_get_overwritten(state, bot, spawner):
    async def go():
        gate = asyncio.Event()
        spawner.scripts.append(FakeProc([init("old")], gate=gate))
        await server.enqueue_guest_message(GUEST, GUEST, "x")
        await asyncio.sleep(0.05)
        assert server.read_guest_session_id(GUEST) == "old"
        await server.cmd_guest_new(make_msg(GUEST, "/new"))
        gate.set()
        await drain(GUEST)

    asyncio.run(go())
    assert server.read_guest_session_id(GUEST) == ""


def test_owner_new_stop_only_hint(state, bot, spawner):
    for handler in (server.cmd_guest_new, server.cmd_guest_stop):
        msg = make_msg(OWNER, "/x")
        asyncio.run(handler(msg))
        assert "гостевых сессий" in msg.answer.await_args.args[0]
    assert spawner.calls == []


def test_stranger_new_stop_ignored(state, bot):
    msg = make_msg("333333333", "/new")
    asyncio.run(server.cmd_guest_new(msg))
    asyncio.run(server.cmd_guest_stop(msg))
    msg.answer.assert_not_awaited()


def test_unshare_kills_active_run(state, bot, spawner):
    async def go():
        gate = asyncio.Event()
        p = FakeProc([init("s")], gate=gate)
        spawner.scripts.append(p)
        await server.enqueue_guest_message(GUEST, GUEST, "x")
        await asyncio.sleep(0.05)
        ok, _ = server.unshare_access(GUEST)
        assert ok
        assert (p.pid, server.signal.SIGTERM) in KILLED
        gate.set()
        await drain(GUEST)

    asyncio.run(go())


def test_access_list_shows_working_marker(state, bot, spawner):
    async def go():
        gate = asyncio.Event()
        spawner.scripts.append(FakeProc([init("s"), result("ok", "s")], gate=gate))
        await server.enqueue_guest_message(GUEST, GUEST, "x")
        await asyncio.sleep(0.05)
        txt = server.access_list_text()
        line_busy = [ln for ln in txt.splitlines() if GUEST in ln][0]
        line_idle = [ln for ln in txt.splitlines() if GUEST2 in ln][0]
        assert "сейчас работает" in line_busy and "сейчас работает" not in line_idle
        gate.set()
        await drain(GUEST)
        assert "сейчас работает" not in server.access_list_text()

    asyncio.run(go())


def test_timeout_kills_and_reports(state, bot, spawner, monkeypatch):
    monkeypatch.setattr(server, "GUEST_RUN_TIMEOUT", 0.05)

    async def go():
        gate = asyncio.Event()
        p = FakeProc([init("s")], gate=gate)
        spawner.scripts.append(p)
        await server.enqueue_guest_message(GUEST, GUEST, "x")
        await asyncio.sleep(0.2)
        assert (p.pid, server.signal.SIGTERM) in KILLED
        gate.set()
        await drain(GUEST)

    asyncio.run(go())
    assert any(t.startswith("⌛") for t in bot.texts(GUEST))


def test_status_bubble_shows_recent_actions(state, bot, spawner):
    async def go():
        gate = asyncio.Event()
        spawner.scripts.append(FakeProc([init("s"), assistant_tool("Bash", {"description": "Run tests"}),
                                         result("ok", "s")], gate=gate))
        await server.enqueue_guest_message(GUEST, GUEST, "x")
        await asyncio.sleep(0.1)
        gate.set()
        await drain(GUEST)

    asyncio.run(go())
    edits = [c.kwargs["text"] for c in bot.edit_message_text.await_args_list]
    assert any("⏳" in t and "Bash: Run tests" in t for t in edits)


def test_instructions_say_guests_are_not_in_owner_session():
    assert "guest messages never arrive in this session" in server.INSTRUCTIONS
    assert 'role="guest"' not in server.INSTRUCTIONS
