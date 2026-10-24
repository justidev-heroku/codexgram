"""Tests for the Opus 5.5 safeguard pause handling.

Pure helpers (detect_safeguard_pause, detect_safeguard_stop, consume_pty_action) live
inside the WRAPPER_TEMPLATE string in install.py, so they are extracted and exec'd the
same way test_alert_classification.py does. The server.py callbacks (safeguard:switch /
safeguard:ack) are exercised directly with mocks; nothing touches Telegram or systemctl.
"""
import asyncio
import os
import re
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
    m = re.search(r"(# Opus 5\.5 safeguard pause.*?)\n\ndef main\(\) -> int:", src, re.S)
    assert m, "safeguard block not found in install.py wrapper_code"
    ns = {"re": re, "os": os, "Path": Path, "time": time}
    exec(m.group(1), ns)
    return ns


_NS = _load_helpers()
detect_safeguard_pause = _NS["detect_safeguard_pause"]
detect_safeguard_stop = _NS["detect_safeguard_stop"]
consume_pty_action = _NS["consume_pty_action"]
SAFEGUARD_SWITCH_KEYS = _NS["SAFEGUARD_SWITCH_KEYS"]

# Raw dialog as Claude Code draws it (spaces intact).
PAUSE_RAW = (
    "Session paused Opus 5.5's safeguards flagged this session. You may be seeing this for the "
    "first time: Opus 5.5 is more capable and has stronger safeguards as a result, which can "
    "sometimes flag non-cybersecurity work. Edit and retry, or continue with Opus 4.8. Send "
    "feedback with /feedback or learn more: https://support.claude.com/en/articles/8106465 "
    "Details: `[cyber]` ❯ 1. Switch to Opus 4.8 2. Edit prompt and retry with Opus 5.5"
)

# Real sample from the PTY with all spaces lost after ANSI cleanup.
PAUSE_STRIPPED = (
    "Sessionpaused Opus 5.5's safeguardsflaggedthissession.Youmaybeseeingthisforthefirsttime:"
    "Opus5.5ismorecapableandhasstrongersafeguardsasaresult,whichcan... Editandretry,orcontinue"
    "withOpus4.8.Sendfeedbackwith/feedbackorlearnmore:https://support.claude.com/en/articles/8106465"
    "Details:`[cyber]`❯ 1. Switch to Opus4.82.EditpromptandretrywithOpus5.5"
)

STOP_RAW = "● Opus 5.5's safeguards stopped the response above · continuing once with that noted"
STOP_STRIPPED = "Opus5.5'ssafeguardsstoppedtheresponseabove·continuingoncewiththatnoted"


# --- detect_safeguard_pause --------------------------------------------------

def test_pause_detected_with_normal_spacing():
    assert detect_safeguard_pause(PAUSE_RAW) == {"category": "cyber", "fallback": "Opus 4.8"}


def test_pause_detected_with_all_spaces_stripped():
    assert detect_safeguard_pause(PAUSE_STRIPPED) == {"category": "cyber", "fallback": "Opus 4.8"}


def test_pause_detected_with_ansi_leftovers():
    buf = (
        "\x1b[1mSession paused\x1b[22m Opus 5.5's safeguards flagged this session. [39m"
        "Edit and retry, or continue with Opus 4.8. Details: `[cyber]` ❯ "
        "1. Switch to Opus 4.8 2. Edit prompt and retry with Opus 5.5"
    )
    assert detect_safeguard_pause(buf) == {"category": "cyber", "fallback": "Opus 4.8"}


def test_category_missing_defaults_to_empty():
    buf = ("Session paused Opus 5.5's safeguards flagged this session. "
           "1. Switch to Opus 4.8 2. Edit prompt and retry with Opus 5.5")
    assert detect_safeguard_pause(buf)["category"] == ""


def test_category_parsed_from_other_value():
    buf = PAUSE_RAW.replace("[cyber]", "[bio-hazard]")
    assert detect_safeguard_pause(buf)["category"] == "bio-hazard"


def test_fallback_taken_from_continue_line_when_no_switch_option():
    buf = ("Session paused Opus 5.5's safeguards flagged this session. Details: `[cyber]` ❯ "
           "Edit and retry, or continue with Sonnet 4.6")
    assert detect_safeguard_pause(buf)["fallback"] == "Sonnet 4.6"


def test_fallback_default_when_no_model_named():
    buf = "Session paused Opus 5.5's safeguards flagged this session. Details: `[cyber]` ❯"
    assert detect_safeguard_pause(buf) == {"category": "cyber", "fallback": "Opus 4.8"}


def test_fallback_version_not_confused_with_option_number():
    # "Opus4.82." -> version 4.8, option number 2 (stripped form).
    assert detect_safeguard_pause(PAUSE_STRIPPED)["fallback"] == "Opus 4.8"


def test_dialog_still_fresh_when_unrelated_output_follows_without_prompt():
    buf = PAUSE_RAW + "\n✻ Thinking about the next step..."
    assert detect_safeguard_pause(buf) is not None


def test_dialog_stale_after_new_prompt_following_options():
    # Dialog, then Claude carried on and a fresh input prompt appeared after the options.
    buf = PAUSE_RAW + "\n● Done with the task.\n❯ "
    assert detect_safeguard_pause(buf) is None


def test_dialog_stale_in_stripped_form_when_prompt_follows():
    assert detect_safeguard_pause(PAUSE_STRIPPED + "❯") is None


def test_repainted_dialog_after_old_one_is_fresh_again():
    # An old dialog that was answered, then a new one: only the last occurrence counts.
    buf = PAUSE_RAW + "\n● Some answer\n❯ " + PAUSE_RAW
    assert detect_safeguard_pause(buf) is not None


def test_dialog_without_options_yet_is_fresh():
    buf = "Session paused Opus 5.5's safeguards flagged this session. Details: `[cyber]` ❯"
    assert detect_safeguard_pause(buf) is not None


def test_ordinary_text_mentioning_safeguards_is_not_a_pause():
    assert detect_safeguard_pause("The safeguards in this module flag bad input.") is None
    assert detect_safeguard_pause("Opus 5.5 has stronger safeguards. ❯ 1. Switch to Opus 4.8") is None
    assert detect_safeguard_pause("") is None


# --- detect_safeguard_stop ---------------------------------------------------

def test_stop_line_detected_raw_and_stripped():
    assert detect_safeguard_stop(STOP_RAW) is True
    assert detect_safeguard_stop(STOP_STRIPPED) is True


def test_stop_line_not_triggered_by_ordinary_text():
    assert detect_safeguard_stop("The safeguards stopped me earlier, let's retry.") is False
    assert detect_safeguard_stop(PAUSE_RAW) is False


# --- consume_pty_action ------------------------------------------------------

def test_switch_with_pending_returns_keys_and_deletes_file(tmp_path):
    f = tmp_path / "pty_action"
    f.write_text("safeguard:switch", "utf-8")
    keys = consume_pty_action(f, True, time.time())
    assert keys == SAFEGUARD_SWITCH_KEYS == [b"\r"]
    assert not f.exists()


def test_switch_without_pending_only_deletes_file(tmp_path):
    f = tmp_path / "pty_action"
    f.write_text("safeguard:switch", "utf-8")
    assert consume_pty_action(f, False, time.time()) is None
    assert not f.exists()


def test_unknown_action_is_ignored_and_deleted(tmp_path):
    f = tmp_path / "pty_action"
    f.write_text("something:else", "utf-8")
    assert consume_pty_action(f, True, time.time()) is None
    assert not f.exists()


def test_stale_action_file_is_deleted_without_action(tmp_path):
    f = tmp_path / "pty_action"
    f.write_text("safeguard:switch", "utf-8")
    now = time.time()
    os.utime(f, (now - 700, now - 700))
    assert consume_pty_action(f, True, now) is None
    assert not f.exists()


def test_missing_file_returns_none(tmp_path):
    assert consume_pty_action(tmp_path / "nope", True, time.time()) is None


# --- server.py callbacks -----------------------------------------------------

import server  # noqa: E402

OWNER = "111111111"
GUEST = "222222222"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "ACCESS_FILE", tmp_path / "access.json")
    (tmp_path / "access.json").write_text('{"allowFrom": ["%s", "%s"]}' % (OWNER, GUEST), "utf-8")
    return tmp_path


def _queued(state):
    # Action lines the callbacks queued for the launcher (STATE_DIR/pty_actions/*.act).
    d = state / "pty_actions"
    return [f.read_text("utf-8").strip() for f in sorted(d.glob("*.act"))] if d.exists() else []


def _cb(uid):
    cb = MagicMock()
    cb.from_user = SimpleNamespace(id=int(uid), username=None, first_name="N", last_name=None, is_bot=False)
    cb.message.edit_text = AsyncMock()
    cb.message.edit_reply_markup = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def test_switch_callback_writes_pty_action_for_owner(state):
    cb = _cb(OWNER)
    asyncio.run(server.on_safeguard_switch(cb))
    assert _queued(state) == ["safeguard:switch"]
    cb.message.edit_text.assert_awaited_once()
    assert "Переключаю" in cb.message.edit_text.await_args.args[0]
    cb.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)


def test_switch_callback_denied_for_guest_writes_nothing(state):
    cb = _cb(GUEST)
    asyncio.run(server.on_safeguard_switch(cb))
    assert _queued(state) == []
    cb.answer.assert_awaited_once_with("Нет доступа.")
    cb.message.edit_text.assert_not_awaited()


def test_ack_callback_edits_message_and_writes_nothing(state):
    cb = _cb(OWNER)
    asyncio.run(server.on_safeguard_ack(cb))
    assert not (state / "pty_action").exists()
    assert "оставлена на паузе" in cb.message.edit_text.await_args.args[0]
    cb.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)


def test_ack_callback_denied_for_guest(state):
    cb = _cb(GUEST)
    asyncio.run(server.on_safeguard_ack(cb))
    cb.answer.assert_awaited_once_with("Нет доступа.")
    cb.message.edit_text.assert_not_awaited()
