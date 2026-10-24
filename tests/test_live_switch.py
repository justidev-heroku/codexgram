"""Tests for live /model, /effort and queued PTY actions.

The launcher helpers live inside the WRAPPER_TEMPLATE string in install.py, so they are
extracted and exec'd (same approach as test_safeguard.py). The server.py callbacks are
called directly with mocks; nothing touches Telegram, systemctl or the real claude binary.
The global ~/.claude/settings.json is never used: every test passes its own tmp path.
"""
import asyncio
import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
INSTALL_PY = ROOT / "install.py"


def _load_helpers():
    src = INSTALL_PY.read_text("utf-8")
    m = re.search(r"(# Interactive startup dialogs.*?)\n\ndef main\(\) -> int:", src, re.S)
    assert m, "launcher helper block not found in install.py wrapper_code"
    ns = {"re": re, "os": os, "json": json, "time": time, "signal": signal, "Path": Path,
          "send_telegram_alert": MagicMock(), "LAST_MODEL_FILE": None}
    exec(m.group(1), ns)
    return ns


NS = _load_helpers()
read_pty_actions = NS["read_pty_actions"]
is_idle_prompt = NS["is_idle_prompt"]
snapshot_settings_keys = NS["snapshot_settings_keys"]
restore_settings_keys = NS["restore_settings_keys"]
start_live_switch = NS["start_live_switch"]
finish_live_switch = NS["finish_live_switch"]
safeguard_stop_alert_due = NS["safeguard_stop_alert_due"]
LIVE_CONFIRM_RE = NS["LIVE_CONFIRM_RE"]
LIVE_MODEL_NAME_RE = NS["LIVE_MODEL_NAME_RE"]
LIVE_SWITCH_TIMEOUT = NS["LIVE_SWITCH_TIMEOUT"]
LIVE_SWITCH_GIVE_UP = NS["LIVE_SWITCH_GIVE_UP"]
PTY_ACTIONS_MAX_AGE = NS["PTY_ACTIONS_MAX_AGE"]

IDLE_SCREEN = (
    "● Готово, вот ответ.\n"
    "─────────────────────────────────────────────\n"
    "❯ \n"
    "─────────────────────────────────────────────\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)"
)
BUSY_SCREEN = (
    "● Читаю файлы\n"
    "✻ Thinking… (12s · esc to interrupt)\n"
    "─────────────────────────────────────────────\n"
    "❯ \n"
    "─────────────────────────────────────────────\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)"
)
PERMISSION_SCREEN = (
    "Do you want to make this edit?\n"
    "❯ 1. Yes\n"
    "  2. No\n"
)


# --- read_pty_actions --------------------------------------------------------

def _act(d, name, text, age=0.0, now=None):
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(text + "\n", "utf-8")
    if now is not None:
        os.utime(f, (now - age, now - age))
    return f


def test_read_actions_in_time_order_and_files_removed(tmp_path):
    now = time.time()
    d = tmp_path / "pty_actions"
    _act(d, "1700000000000000002.act", "set_effort:high", now=now)
    _act(d, "1700000000000000001.act", "set_model:claude-opus-4-8", now=now)
    _act(d, "1700000000000000003.act", "new_session", now=now)
    assert read_pty_actions(d, now) == ["set_model:claude-opus-4-8", "set_effort:high", "new_session"]
    assert list(d.glob("*.act")) == []


def test_stale_action_dropped(tmp_path):
    now = time.time()
    d = tmp_path / "pty_actions"
    _act(d, "1.act", "set_model:claude-opus-4-8", age=PTY_ACTIONS_MAX_AGE + 5, now=now)
    _act(d, "2.act", "set_effort:max", age=1.0, now=now)
    assert read_pty_actions(d, now) == ["set_effort:max"]
    assert list(d.glob("*.act")) == []


def test_malformed_and_unknown_lines_dropped(tmp_path):
    now = time.time()
    d = tmp_path / "pty_actions"
    _act(d, "1.act", "set_effort:ultra", now=now)
    _act(d, "2.act", "rm -rf /", now=now)
    _act(d, "3.act", "set_model:claude opus", now=now)  # space is not a valid id char
    _act(d, "4.act", "safeguard:switch", now=now)
    assert read_pty_actions(d, now) == ["safeguard:switch"]
    assert list(d.glob("*.act")) == []


def test_tmp_files_are_not_consumed(tmp_path):
    now = time.time()
    d = tmp_path / "pty_actions"
    _act(d, ".1.act.tmp", "new_session", now=now)
    assert read_pty_actions(d, now) == []
    assert (d / ".1.act.tmp").exists()


def test_missing_dir_returns_empty(tmp_path):
    assert read_pty_actions(tmp_path / "nope", time.time()) == []


def test_legacy_pty_action_file_is_one_more_action(tmp_path):
    now = time.time()
    d = tmp_path / "pty_actions"
    _act(d, "1.act", "set_effort:low", now=now)
    legacy = tmp_path / "pty_action"
    legacy.write_text("safeguard:switch", "utf-8")
    assert read_pty_actions(d, now, legacy) == ["safeguard:switch", "set_effort:low"]
    assert not legacy.exists()


def test_legacy_file_other_content_ignored(tmp_path):
    legacy = tmp_path / "pty_action"
    legacy.write_text("something:else", "utf-8")
    assert read_pty_actions(tmp_path / "pty_actions", time.time(), legacy) == []
    assert not legacy.exists()


# --- is_idle_prompt ----------------------------------------------------------

def test_idle_prompt_detected():
    assert is_idle_prompt(IDLE_SCREEN) is True


def test_busy_turn_with_esc_after_prompt_is_not_idle():
    buf = IDLE_SCREEN + "\n✻ Thinking… (esc to interrupt)"
    assert is_idle_prompt(buf) is False


def test_busy_detected_when_spaces_are_stripped():
    assert is_idle_prompt("●Готово\n❯\n✻Thinking…(esctointerrupt)") is False
    assert is_idle_prompt("●Готово\n❯───⏵⏵bypasspermissionson(shift+tabtocycle)") is True


def test_no_prompt_is_not_idle():
    assert is_idle_prompt("● some output without a prompt") is False
    assert is_idle_prompt("") is False


def test_permission_dialog_is_not_idle():
    assert is_idle_prompt(PERMISSION_SCREEN) is False


def test_pending_safeguard_pause_is_not_idle():
    buf = ("Session paused Opus 5.5's safeguards flagged this session. Details: `[cyber]` ❯ "
           "1. Switch to Opus 4.8 2. Edit prompt and retry with Opus 5.5")
    assert is_idle_prompt(buf) is False


def test_startup_dialog_is_not_idle():
    buf = "Make auto mode your default permission mode?\n❯ 1. Yes\n❯"
    assert is_idle_prompt(buf) is False


# --- confirmation regexes ----------------------------------------------------

@pytest.mark.parametrize("text", [
    "⎿ Set model to Opus 4.8 and saved as your default for new sessions",
    "⎿  Set  model  to Opus 4.8",
    "⎿ Setmodeltoopus4.8andsavedasyourdefault",
])
def test_model_confirmation_matches_with_mangled_spacing(text):
    assert LIVE_CONFIRM_RE["set_model"].search(text)


@pytest.mark.parametrize("text", [
    "⎿ Set effort level to high (saved as your default for new sessions): ...",
    "⎿ Seteffortlevelto high",
    "⎿ Set  effort level  to  high",
])
def test_effort_confirmation_matches_with_mangled_spacing(text):
    assert LIVE_CONFIRM_RE["set_effort"].search(text)


def test_confirmation_regexes_do_not_cross_match():
    assert LIVE_CONFIRM_RE["set_effort"].search("Set model to Opus 4.8 and saved") is None
    assert LIVE_CONFIRM_RE["set_model"].search("Set effort level to high") is None


def test_model_display_name_parsed_from_confirmation():
    m = LIVE_MODEL_NAME_RE.search("⎿ Set model to Opus 4.8 and saved as your default")
    assert m and m.group(1) == "Opus 4.8"


# --- settings snapshot / restore ---------------------------------------------

def _write(path, data):
    path.write_text(json.dumps(data, indent=2), "utf-8")


def _read(path):
    return json.loads(path.read_text("utf-8"))


def test_restore_puts_back_changed_keys_and_keeps_others(tmp_path):
    f = tmp_path / "settings.json"
    _write(f, {"model": "claude-sonnet-4-6", "effortLevel": "low", "theme": "dark",
               "modelSettings": {"claude-sonnet-4-6": {"effortLevel": "low"}}})
    snap = snapshot_settings_keys(f)
    # What claude does on /model + /effort.
    _write(f, {"model": "claude-opus-4-8", "effortLevel": "high", "theme": "dark",
               "modelSettings": {"claude-opus-4-8": {"effortLevel": "high"}, "claude-sonnet-4-6": {"effortLevel": "low"}},
               "permissions": {"allow": ["Bash"]}})
    assert restore_settings_keys(f, snap) is True
    assert _read(f) == {"model": "claude-sonnet-4-6", "effortLevel": "low", "theme": "dark",
                        "modelSettings": {"claude-sonnet-4-6": {"effortLevel": "low"}},
                        "permissions": {"allow": ["Bash"]}}


def test_key_absent_before_is_removed_again(tmp_path):
    f = tmp_path / "settings.json"
    _write(f, {"theme": "dark"})
    snap = snapshot_settings_keys(f)
    assert snap == {}
    _write(f, {"theme": "dark", "model": "claude-opus-4-8", "effortLevel": "max"})
    restore_settings_keys(f, snap)
    assert _read(f) == {"theme": "dark"}


def test_model_settings_object_round_trip(tmp_path):
    f = tmp_path / "settings.json"
    original = {"modelSettings": {"claude-opus-4-8": {"effortLevel": "xhigh", "other": 1}}}
    _write(f, original)
    snap = snapshot_settings_keys(f)
    _write(f, {"modelSettings": {"claude-opus-4-8": {"effortLevel": "max"}}})
    restore_settings_keys(f, snap)
    assert _read(f) == original


def test_restore_leaves_file_untouched_when_nothing_changed(tmp_path):
    f = tmp_path / "settings.json"
    _write(f, {"model": "claude-opus-4-8"})
    snap = snapshot_settings_keys(f)
    mtime = f.stat().st_mtime_ns
    assert restore_settings_keys(f, snap) is True
    assert f.stat().st_mtime_ns == mtime
    assert not (tmp_path / "settings.json.tmp").exists()


def test_unreadable_settings_are_never_rewritten(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text("{ broken", "utf-8")
    snap = snapshot_settings_keys(f)
    assert snap is None
    assert restore_settings_keys(f, snap) is False
    assert f.read_text("utf-8") == "{ broken"


def test_missing_file_snapshot_is_empty(tmp_path):
    assert snapshot_settings_keys(tmp_path / "nope.json") == {}


# --- start / finish of a live switch -----------------------------------------

def test_start_snapshots_before_typing_and_types_command(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    _write(f, {"model": "claude-sonnet-4-6"})
    ns = NS
    monkeypatch.setitem(ns, "live_settings_path", lambda: f)
    typed = []
    seen_snap = {}
    real_snapshot = ns["snapshot_settings_keys"]

    def fake_snapshot(path):
        seen_snap["value"] = real_snapshot(path)
        return seen_snap["value"]

    monkeypatch.setitem(ns, "snapshot_settings_keys", fake_snapshot)
    monkeypatch.setattr(os, "write", lambda fd, data: typed.append(data))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    live = start_live_switch("set_model", "claude-opus-4-8", 99, 1000.0)
    assert b"".join(typed) == b"/model claude-opus-4-8\r"
    assert seen_snap["value"] == {"model": "claude-sonnet-4-6"}
    assert live["deadline"] == 1000.0 + LIVE_SWITCH_TIMEOUT
    assert live["output"] == ""


def test_effort_command_is_typed_as_effort(monkeypatch):
    typed = []
    monkeypatch.setattr(os, "write", lambda fd, data: typed.append(data))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    start_live_switch("set_effort", "high", 99, 0.0)
    assert b"".join(typed) == b"/effort high\r"


def test_success_finish_records_last_model_and_alerts(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    _write(f, {"model": "claude-sonnet-4-6"})
    last = tmp_path / "last_launched_model"
    alert = MagicMock()
    monkeypatch.setitem(NS, "live_settings_path", lambda: f)
    monkeypatch.setitem(NS, "LAST_MODEL_FILE", last)
    monkeypatch.setitem(NS, "send_telegram_alert", alert)
    live = {"kind": "set_model", "value": "claude-opus-4-8", "snapshot": {"model": "claude-sonnet-4-6"},
            "deadline": 0.0, "output": "⎿ Set model to Opus 4.8 and saved as your default"}
    _write(f, {"model": "claude-opus-4-8"})  # claude's own write
    finish_live_switch(live, True)
    assert _read(f) == {"model": "claude-sonnet-4-6"}
    assert last.read_text("utf-8") == "claude-opus-4-8"
    alert.assert_called_once()
    assert alert.call_args.args[0] == "✅ Модель переключена на Opus 4.8 без перезапуска"


def test_effort_success_does_not_touch_last_model(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    _write(f, {"effortLevel": "low"})
    last = tmp_path / "last_launched_model"
    alert = MagicMock()
    monkeypatch.setitem(NS, "live_settings_path", lambda: f)
    monkeypatch.setitem(NS, "LAST_MODEL_FILE", last)
    monkeypatch.setitem(NS, "send_telegram_alert", alert)
    live = {"kind": "set_effort", "value": "high", "snapshot": {"effortLevel": "low"},
            "deadline": 0.0, "output": ""}
    finish_live_switch(live, True)
    assert not last.exists()
    assert _read(f) == {"effortLevel": "low"}
    assert alert.call_args.args[0] == "✅ Effort переключён на high без перезапуска"


def test_timeout_finish_restores_and_offers_restart(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    _write(f, {"model": "claude-sonnet-4-6"})
    last = tmp_path / "last_launched_model"
    alert = MagicMock()
    monkeypatch.setitem(NS, "live_settings_path", lambda: f)
    monkeypatch.setitem(NS, "LAST_MODEL_FILE", last)
    monkeypatch.setitem(NS, "send_telegram_alert", alert)
    live = {"kind": "set_model", "value": "claude-opus-4-8", "snapshot": {"model": "claude-sonnet-4-6"},
            "deadline": 0.0, "output": ""}
    _write(f, {"model": "claude-opus-4-8"})
    finish_live_switch(live, False)
    assert _read(f) == {"model": "claude-sonnet-4-6"}
    assert not last.exists()
    text = alert.call_args.args[0]
    assert text.startswith("⚠️ Не получилось переключить на лету")
    kb = alert.call_args.kwargs["reply_markup"]
    assert kb["inline_keyboard"][0][0]["callback_data"] == "restart_confirm"


# --- stop alert gating -------------------------------------------------------

def test_stop_alert_suppressed_before_startup_cleared():
    assert safeguard_stop_alert_due(False, 1000.0, 0.0) is False


def test_stop_alert_after_startup_and_cooldown():
    assert safeguard_stop_alert_due(True, 1000.0, 0.0) is True
    assert safeguard_stop_alert_due(True, 1000.0, 999.0) is False


# --- server.py callbacks -----------------------------------------------------

import server  # noqa: E402

OWNER = "111111111"
GUEST = "222222222"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "ACCESS_FILE", tmp_path / "access.json")
    monkeypatch.setattr(server, "MODELS_FILE", tmp_path / "models.json")
    (tmp_path / "access.json").write_text('{"allowFrom": ["%s", "%s"]}' % (OWNER, GUEST), "utf-8")
    settings = tmp_path / "settings.json"
    settings.write_text('{"model": "claude-sonnet-4-6", "theme": "dark"}', "utf-8")
    monkeypatch.setattr(server, "SETTINGS_FILE", settings)
    return tmp_path


def _queued(state):
    d = state / "pty_actions"
    return [f.read_text("utf-8").strip() for f in sorted(d.glob("*.act"))] if d.exists() else []


def _cb(uid, data=""):
    cb = MagicMock()
    cb.data = data
    cb.from_user = SimpleNamespace(id=int(uid), username=None, first_name="N", last_name=None, is_bot=False)
    cb.message.edit_text = AsyncMock()
    cb.message.edit_reply_markup = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def test_model_callback_saves_setting_and_enqueues_live_switch(state):
    cb = _cb(OWNER, "model:opus")
    asyncio.run(server.on_model_callback(cb))
    assert _read(state / "settings.json")["model"] == "claude-opus-4-8"
    assert _queued(state) == ["set_model:claude-opus-4-8"]
    text = cb.message.edit_text.await_args.args[0]
    assert "Применяю без перезапуска" in text
    assert cb.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "restart_confirm"


def test_effort_callback_saves_setting_and_enqueues_live_switch(state):
    cb = _cb(OWNER, "effort:high")
    asyncio.run(server.on_effort_callback(cb))
    assert _read(state / "settings.json")["effortLevel"] == "high"
    assert _queued(state) == ["set_effort:high"]
    assert "Применяю без перезапуска" in cb.message.edit_text.await_args.args[0]


def test_model_callback_denied_for_guest_queues_nothing(state):
    cb = _cb(GUEST, "model:opus")
    asyncio.run(server.on_model_callback(cb))
    assert _queued(state) == []
    assert _read(state / "settings.json")["model"] == "claude-sonnet-4-6"
    cb.answer.assert_awaited_once_with("Нет доступа.")


def test_safeguard_new_callback_queues_new_session(state):
    cb = _cb(OWNER, "safeguard:new")
    asyncio.run(server.on_safeguard_new(cb))
    assert _queued(state) == ["new_session"]
    assert "Начинаю новую сессию" in cb.message.edit_text.await_args.args[0]
    cb.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)


def test_safeguard_new_callback_denied_for_guest(state):
    cb = _cb(GUEST, "safeguard:new")
    asyncio.run(server.on_safeguard_new(cb))
    assert _queued(state) == []
    cb.answer.assert_awaited_once_with("Нет доступа.")
    cb.message.edit_text.assert_not_awaited()


def test_switch_callback_queues_switch_action(state):
    cb = _cb(OWNER, "safeguard:switch")
    asyncio.run(server.on_safeguard_switch(cb))
    assert _queued(state) == ["safeguard:switch"]


def test_sgauto_toggle_writes_owner_settings_and_flips(state):
    cb = _cb(OWNER, "mdl:sgauto")
    asyncio.run(server.on_model_sgauto(cb))
    assert _read(state / "settings.json")["switchModelsOnFlag"] is True
    assert _read(state / "settings.json")["theme"] == "dark"
    asyncio.run(server.on_model_sgauto(cb))
    assert _read(state / "settings.json")["switchModelsOnFlag"] is False


def test_sgauto_toggle_writes_guest_own_settings_file(state, tmp_path, monkeypatch):
    # A guest instance runs with SETTINGS_FILE pointing at its own settings.json (--settings).
    guest_dir = tmp_path / "guests" / GUEST
    guest_dir.mkdir(parents=True)
    guest_settings = guest_dir / "settings.json"
    guest_settings.write_text("{}\n", "utf-8")
    owner_before = (state / "settings.json").read_text("utf-8")
    monkeypatch.setattr(server, "SETTINGS_FILE", guest_settings)
    cb = _cb(OWNER, "mdl:sgauto")
    asyncio.run(server.on_model_sgauto(cb))
    assert _read(guest_settings)["switchModelsOnFlag"] is True
    assert (state / "settings.json").read_text("utf-8") == owner_before


def test_sgauto_toggle_denied_for_guest(state):
    cb = _cb(GUEST, "mdl:sgauto")
    asyncio.run(server.on_model_sgauto(cb))
    assert "switchModelsOnFlag" not in _read(state / "settings.json")
    cb.answer.assert_awaited_once_with("Нет доступа.")


def test_model_menu_shows_sgauto_state_on_button(state):
    kb = server.model_menu_markup("opus")
    sg = [b for row in kb.inline_keyboard for b in row if b.callback_data == "mdl:sgauto"]
    assert len(sg) == 1 and sg[0].text.endswith("выкл")
    _write(state / "settings.json", {"switchModelsOnFlag": True})
    kb = server.model_menu_markup("opus")
    sg = [b for row in kb.inline_keyboard for b in row if b.callback_data == "mdl:sgauto"]
    assert sg[0].text.endswith("вкл")


def test_enqueue_writes_one_file_per_action(state):
    server.enqueue_pty_action("set_effort:low")
    server.enqueue_pty_action("set_effort:max")
    assert _queued(state) == ["set_effort:low", "set_effort:max"]
    assert not list((state / "pty_actions").glob(".*.tmp"))


def test_switch_model_dialog_detected_with_and_without_spaces():
    f = NS["detect_switch_model_dialog"]
    real = ("Switch model? Your next response will be slower and use more tokens This conversation "
            "i cached for th curr nt model. ❯ 1. Yes, switch to Opus 4.8 2. No, go back")
    assert f(real)
    assert f(real.replace(" ", ""))
    assert not f("❯ /model claude-opus-4-8 ⎿ Set model to Opus 4.8 and saved as your default")
    assert not f("Should I switch model? yes")
