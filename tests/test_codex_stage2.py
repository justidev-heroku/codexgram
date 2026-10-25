"""Тесты этапа 2 codex-gram: настройки, аккаунты, форматирование, пузырь, ходы, алерты, подтверждения."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import codex_accounts as acc  # noqa: E402
from codex_approvals import ApprovalBroker, describe_request, request_text  # noqa: E402
from codex_bridge import approval_result, child_env  # noqa: E402
from codex_format import (  # noqa: E402
    extract_version, fmt_ago, fmt_msk, fmt_relative, is_newer, login_succeeded,
    parse_device_login, render_usage, strip_ansi, truncate, usage_bar, window_label,
)
from codex_progress import ActionTracker, describe_item  # noqa: E402
from codex_settings import (  # noqa: E402
    AUTO_NEVER, AUTO_ON_REQUEST, approval_policy, effort_setting, load_settings, model_setting, update_settings,
)
from codex_turns import Throttle, TurnTracker, UsageAlerts  # noqa: E402


# ---------------------------------------------------------------- settings

def test_settings_roundtrip_and_none_removes_key(tmp_path):
    update_settings(tmp_path, model="m1", effort="high")
    assert load_settings(tmp_path) == {"model": "m1", "effort": "high"}
    update_settings(tmp_path, effort=None)
    assert load_settings(tmp_path) == {"model": "m1"}


def test_settings_broken_file_is_empty(tmp_path):
    (tmp_path / "codex_settings.json").write_text("{not json", "utf-8")
    assert load_settings(tmp_path) == {}
    (tmp_path / "codex_settings.json").write_text("[1,2]", "utf-8")
    assert load_settings(tmp_path) == {}


def test_approval_policy_defaults_and_rejects_garbage():
    assert approval_policy({}) == AUTO_NEVER
    assert approval_policy({"auto": "on-request"}) == AUTO_ON_REQUEST
    assert approval_policy({"auto": "always-yes"}) == AUTO_NEVER
    assert approval_policy({"auto": ["on-request"]}) == AUTO_NEVER


def test_model_and_effort_must_be_non_empty_strings():
    assert model_setting({"model": ""}) is None
    assert model_setting({"model": 5}) is None
    assert effort_setting({"effort": "xhigh"}) == "xhigh"


def test_settings_write_is_atomic_no_tmp_left(tmp_path):
    update_settings(tmp_path, model="x")
    assert not (tmp_path / "codex_settings.tmp").exists()


# ---------------------------------------------------------------- accounts

@pytest.mark.parametrize("name", ["../x", "a/b", "..", ".", "", "a" * 33, "имя", "a b", "a.b", "a\x00b", "-"])
def test_account_name_rejects_bad_names(name):
    if name == "-":  # одиночный дефис допустим по регэкспу
        assert acc.validate_name(name) == "-"
        return
    with pytest.raises(acc.AccountError):
        acc.validate_name(name)


def test_account_name_accepts_valid():
    assert acc.validate_name("work_1-b") == "work_1-b"
    assert acc.validate_name("a" * 32)


def test_account_name_none_or_non_string():
    with pytest.raises(acc.AccountError):
        acc.validate_name(None)  # type: ignore[arg-type]


def test_save_switch_list_delete_cycle(tmp_path):
    state = tmp_path / "state"
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text('{"token":"A"}', "utf-8")
    assert acc.save_account(state, home, "personal") is False
    target = state / "accounts" / "personal" / "auth.json"
    assert target.read_text("utf-8") == '{"token":"A"}'
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert acc.list_accounts(state) == ["personal"]
    # перезапись существующей копии
    (home / "auth.json").write_text('{"token":"B"}', "utf-8")
    assert acc.save_account(state, home, "personal") is True
    # переключение возвращает токен в CODEX_HOME с правами 0600
    other = tmp_path / "other_home"
    other.mkdir()
    acc.switch_account(state, other, "personal")
    assert (other / "auth.json").read_text("utf-8") == '{"token":"B"}'
    assert oct((other / "auth.json").stat().st_mode & 0o777) == "0o600"
    assert acc.delete_account(state, "personal") is True
    assert acc.list_accounts(state) == []
    assert not (state / "accounts" / "personal").exists()


def test_save_without_auth_json_fails(tmp_path):
    with pytest.raises(acc.AccountError):
        acc.save_account(tmp_path / "s", tmp_path / "empty", "x")
    assert not (tmp_path / "s" / "accounts" / "x").exists()


def test_save_refuses_symlinked_auth(tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text("{}", "utf-8")
    home = tmp_path / "home"
    home.mkdir()
    os.symlink(secret, home / "auth.json")
    with pytest.raises(acc.AccountError):
        acc.save_account(tmp_path / "s", home, "x")


def test_switch_unknown_account_refused(tmp_path):
    with pytest.raises(acc.AccountError):
        acc.switch_account(tmp_path, tmp_path / "h", "ghost")


def test_switch_refuses_symlinked_saved_copy(tmp_path):
    state = tmp_path / "s"
    d = state / "accounts" / "evil"
    d.mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text("{}", "utf-8")
    os.symlink(victim, d / "auth.json")
    assert acc.list_accounts(state) == []  # симлинк не считается аккаунтом
    with pytest.raises(acc.AccountError):
        acc.switch_account(state, tmp_path / "h", "evil")


def test_account_dir_guards_traversal(tmp_path):
    with pytest.raises(acc.AccountError):
        acc.account_dir(tmp_path, "../../etc")


def test_list_accounts_ignores_junk_entries(tmp_path):
    root = tmp_path / "accounts"
    (root / "ok").mkdir(parents=True)
    (root / "ok" / "auth.json").write_text("{}", "utf-8")
    (root / "noauth").mkdir()
    (root / "bad name").mkdir()
    (root / "file").write_text("x", "utf-8")
    assert acc.list_accounts(tmp_path) == ["ok"]


def test_delete_missing_is_false_and_symlink_dir_untouched(tmp_path):
    assert acc.delete_account(tmp_path, "nope") is False
    real = tmp_path / "real"
    real.mkdir()
    (real / "auth.json").write_text("keep", "utf-8")
    (tmp_path / "accounts").mkdir()
    os.symlink(real, tmp_path / "accounts" / "lnk")
    assert acc.delete_account(tmp_path, "lnk") is False
    assert (real / "auth.json").read_text("utf-8") == "keep"


def test_codex_home_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_GRAM_CODEX_HOME", str(tmp_path / "h"))
    assert acc.codex_home_dir() == tmp_path / "h"
    assert child_env()["CODEX_HOME"] == str(tmp_path / "h")


# ---------------------------------------------------------------- formatting

def test_strip_ansi_and_device_code_parsing():
    text = ("\x1b[1mOpen\x1b[0m \x1b]8;;https://x\x07link\x1b]8;;\x07 "
            "https://auth.openai.com/codex/device and enter \x1b[32mABCD-EFG1\x1b[0m.")
    assert "\x1b" not in strip_ansi(text)
    url, code = parse_device_login(text)
    assert url == "https://auth.openai.com/codex/device"
    assert code == "ABCD-EFG1"


def test_device_login_no_code_or_url():
    assert parse_device_login("nothing here") == (None, None)
    assert parse_device_login("https://a.b/c only") == ("https://a.b/c", None)


def test_device_login_ignores_lowercase_and_longer_tokens():
    url, code = parse_device_login("codex-gram-setup ABCDE-FGHIJKLMNOPQ")
    assert code is None


def test_device_login_strips_trailing_punctuation_from_url():
    url, _ = parse_device_login("Go to https://auth.openai.com/device.")
    assert url == "https://auth.openai.com/device"


def test_login_succeeded_mark():
    assert login_succeeded("\x1b[32mSuccessfully logged in\x1b[0m")
    assert not login_succeeded("failed")


@pytest.mark.parametrize("seconds,expected", [
    (-5, "уже прошло"), (0, "уже прошло"), (30, "через 1 мин"), (3600 * 4 + 720, "через 4 ч 12 мин"),
    (3600 * 5, "через 5 ч"), (86400 * 2 + 3600 * 3, "через 2 д 3 ч"), (86400, "через 1 д"),
])
def test_fmt_relative(seconds, expected):
    assert fmt_relative(seconds) == expected


def test_fmt_ago_buckets():
    assert fmt_ago(10) == "только что"
    assert fmt_ago(300) == "5 мин назад"
    assert fmt_ago(7200) == "2 ч назад"
    assert fmt_ago(86400 * 3) == "3 дн назад"


def test_fmt_msk_handles_garbage():
    assert fmt_msk(None) == "—"
    assert fmt_msk("abc") == "—"
    assert fmt_msk(1e30) == "—"
    assert fmt_msk(0).endswith("МСК")


def test_usage_bar_clamps():
    assert usage_bar(-5) == "░" * 10
    assert usage_bar(250) == "█" * 10
    assert usage_bar("x") == "░" * 10
    assert usage_bar(50) == "█" * 5 + "░" * 5


def test_window_labels():
    assert window_label(300) == "5-часовое окно"
    assert window_label(10080) == "недельное окно"
    assert window_label(None) == "окно"
    assert window_label(-1) == "окно"
    assert window_label(120) == "окно 120 мин".replace("окно 120 мин", "2-часовое окно")


def test_render_usage_contains_windows_credits_and_reached():
    snap = {
        "primary": {"usedPercent": 85, "windowDurationMins": 300, "resetsAt": 1800000000},
        "secondary": {"usedPercent": 10, "windowDurationMins": 10080, "resetsAt": None},
        "credits": {"hasCredits": True, "unlimited": False, "balance": "12.5"},
        "rateLimitReachedType": "rate_limit_reached",
    }
    text = render_usage(snap, 1799990000, plan="plus", email="a@b.c")
    assert "5-часовое окно" in text and "85%" in text and "Недельное окно" in text
    assert "Кредиты: 12.5" in text
    assert "plus" in text and "a@b.c" in text
    assert "Лимит достигнут" in text


def test_render_usage_escapes_html_and_handles_empty_snapshot():
    text = render_usage({"primary": None, "credits": {"hasCredits": False, "unlimited": False}}, 0,
                        email="<script>")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "нет данных" in text
    assert "Кредиты: нет" in text


def test_extract_and_compare_versions():
    assert extract_version("codex-cli 0.164.0\n") == "0.164.0"
    assert extract_version("nothing") is None
    assert is_newer("0.165.0", "0.164.9")
    assert not is_newer("0.164.0", "0.164.0")
    assert not is_newer(None, "0.1")
    assert not is_newer("garbage", "0.1.0")


def test_truncate_collapses_whitespace():
    assert truncate("a   b\n c", 50) == "a b c"
    assert truncate("x" * 20, 5).endswith("…")
    assert len(truncate("x" * 20, 5)) == 5


# ---------------------------------------------------------------- progress bubble

def test_command_action_status_marks():
    running = describe_item({"type": "commandExecution", "command": "ls", "status": "inProgress"}, "started")
    done = describe_item({"type": "commandExecution", "command": "ls", "status": "completed", "exitCode": 0}, "completed")
    failed = describe_item({"type": "commandExecution", "command": "ls", "status": "completed", "exitCode": 2}, "completed")
    declined = describe_item({"type": "commandExecution", "command": "rm", "status": "declined"}, "completed")
    assert running[0] == "⏳" and done[0] == "✅" and failed[0] == "❌" and declined[0] == "❌"


def test_reply_tool_is_labelled_and_other_tools_are_named():
    reply = describe_item({"type": "mcpToolCall", "server": "telegram", "tool": "reply", "status": "inProgress"}, "started")
    assert "отправляю ответ" in reply[1]
    other = describe_item({"type": "mcpToolCall", "server": "x", "tool": "search", "status": "completed"}, "completed")
    assert other[1] == "🔧 x.search"


def test_unknown_or_empty_items_are_skipped():
    assert describe_item({"type": "agentMessage", "text": "hi"}, "completed") is None
    assert describe_item({"type": "commandExecution", "command": ""}, "started") is None
    assert describe_item({"type": "fileChange", "changes": []}, "completed") is None


def test_tracker_keeps_last_five_and_updates_in_place():
    t = ActionTracker()
    for i in range(7):
        t.on_item({"id": str(i), "type": "commandExecution", "command": f"c{i}", "status": "inProgress"}, "started")
    assert [label for _, label in t.actions()] == [f"$ c{i}" for i in range(2, 7)]
    t.on_item({"id": "6", "type": "commandExecution", "command": "c6", "status": "completed", "exitCode": 0}, "completed")
    assert t.actions()[-1] == ("✅", "$ c6")


def test_tracker_render_escapes_and_bounds_labels():
    t = ActionTracker()
    t.on_item({"id": "1", "type": "commandExecution", "command": "echo <b>x</b> " + "y" * 300, "status": "completed", "exitCode": 0}, "completed")
    out = t.render("🤖 Готовлю ответ…")
    assert "<b>x</b>" not in out and "&lt;b&gt;" in out
    assert len(out) < 800
    assert "Готовлю" in out and not t.dirty


def test_tracker_ignores_non_dict_items():
    t = ActionTracker()
    assert t.on_item(None, "started") is False  # type: ignore[arg-type]
    assert t.actions() == []


# ---------------------------------------------------------------- turns

def test_turn_completed_with_reply_is_ok():
    t = TurnTracker()
    t.note_reply("T1")
    assert t.on_turn_completed("T1", "completed").kind == "ok"


def test_turn_completed_without_reply_warns_with_retry():
    t = TurnTracker()
    v = t.on_turn_completed("T1", "completed")
    assert v.kind == "no_reply" and v.retry and "без ответа" in v.text


def test_reply_of_other_turn_does_not_count():
    t = TurnTracker()
    t.note_reply("T-old")
    assert t.on_turn_completed("T-new", "completed").kind == "no_reply"


def test_failed_turn_includes_error_text_escaped():
    t = TurnTracker()
    v = t.on_turn_completed("T1", "failed", "boom <x>")
    assert v.kind == "failed" and v.retry
    assert "&lt;x&gt;" in v.text


def test_stored_error_used_when_completion_has_none():
    t = TurnTracker()
    t.note_error("T1", "rate limited")
    v = t.on_turn_completed("T1", "failed")
    assert "rate limited" in v.text


def test_interrupted_by_user_stop_is_quiet():
    t = TurnTracker()
    t.note_stop("T1")
    v = t.on_turn_completed("T1", "interrupted")
    assert v.kind == "stopped" and not v.retry


def test_interrupted_not_by_user_offers_retry():
    t = TurnTracker()
    v = t.on_turn_completed("T1", "interrupted")
    assert v.kind == "interrupted" and v.retry


def test_turn_tracker_memory_is_bounded():
    t = TurnTracker()
    t._limit = 3
    for i in range(10):
        t.note_reply(f"T{i}")
    assert len(t.replied) <= 3


def test_last_user_text_is_kept():
    t = TurnTracker()
    t.set_last_user("<channel>x</channel>")
    assert t.last_user_text == "<channel>x</channel>"


def test_throttle_interval_and_same_text_window():
    now = [0.0]
    th = Throttle(interval=30, same_window=600, clock=lambda: now[0])
    assert th.allow("a") is True
    assert th.allow("b") is False          # внутри 30 с
    now[0] = 31
    assert th.allow("a") is False          # тот же текст внутри 10 мин
    assert th.allow("b") is True
    now[0] = 700
    assert th.allow("a") is True           # окно одинакового текста прошло


def test_usage_alerts_fire_once_per_threshold_and_reset_window():
    alerts = UsageAlerts()
    snap = {"primary": {"usedPercent": 85, "resetsAt": 1000}}
    first = alerts.check(snap)
    assert [a.threshold for a in first] == [80]
    assert alerts.check(snap) == []                       # дедуп
    up = alerts.check({"primary": {"usedPercent": 100, "resetsAt": 1000}})
    assert [a.threshold for a in up] == [100]
    new_window = alerts.check({"primary": {"usedPercent": 85, "resetsAt": 2000}})
    assert [a.threshold for a in new_window] == [80]     # новое окно → снова


def test_usage_alerts_restored_from_disk_keys_do_not_repeat():
    first = UsageAlerts()
    snap = {"secondary": {"usedPercent": 90, "resetsAt": 5}}
    first.check(snap)
    again = UsageAlerts(first.seen)
    assert again.check(snap) == []


def test_usage_alerts_ignore_bad_payloads():
    a = UsageAlerts()
    assert a.check(None) == []  # type: ignore[arg-type]
    assert a.check({"primary": "oops"}) == []
    assert a.check({"primary": {"usedPercent": "lots"}}) == []
    # битый resetsAt не отменяет алерт по проценту, но ключ без даты сброса
    got = a.check({"primary": {"usedPercent": 99, "resetsAt": "soon"}})
    assert [x.resets_at for x in got] == [None]


def test_usage_alerts_key_bound():
    a = UsageAlerts()
    for i in range(UsageAlerts.MAX_KEYS + 50):
        a.check({"primary": {"usedPercent": 85, "resetsAt": i}})
    assert len(a.seen) <= UsageAlerts.MAX_KEYS


# ---------------------------------------------------------------- approvals

def test_approval_results_accept_and_deny_shapes():
    assert approval_result("item/commandExecution/requestApproval", {}, True) == {"decision": "accept"}
    assert approval_result("item/commandExecution/requestApproval", {}, False) == {"decision": "decline"}
    assert approval_result("item/fileChange/requestApproval", {}, False) == {"decision": "decline"}
    assert approval_result("execCommandApproval", {}, False) == {"decision": "denied"}
    assert approval_result("applyPatchApproval", {}, True) == {"decision": "approved"}
    perms = {"network": True}
    assert approval_result("item/permissions/requestApproval", {"permissions": perms}, True) == {
        "permissions": perms, "scope": "turn"}
    assert approval_result("item/permissions/requestApproval", {"permissions": perms}, False) == {
        "permissions": {}, "scope": "turn"}


def test_approval_permissions_non_dict_is_not_granted():
    assert approval_result("item/permissions/requestApproval", {"permissions": ["x"]}, True)["permissions"] == {}


def test_describe_and_request_text_escape_command():
    title, detail = describe_request("item/commandExecution/requestApproval",
                                     {"command": ["rm", "-rf", "<x>"]})
    assert title.startswith("Запрос на выполнение") and detail == "rm -rf <x>"
    text = request_text("item/commandExecution/requestApproval", {"command": "<script>"})
    assert "&lt;script&gt;" in text and "<script>" not in text


def test_broker_allow_deny_and_timeout():
    async def scenario():
        sent = []

        async def send(token, method, params):
            sent.append(token)

        broker = ApprovalBroker(send=send, timeout=0.05)
        task = asyncio.create_task(broker.ask("m", {}))
        await asyncio.sleep(0.01)
        assert broker.resolve(sent[0], True) is True
        assert await task is True
        # таймаут → отказ
        assert await broker.ask("m", {}) is False
        assert broker.pending_count == 0
        # повторный клик и чужой токен ничего не делают
        assert broker.resolve(sent[0], False) is False
        assert broker.resolve("nope", True) is False

    asyncio.run(scenario())


def test_broker_send_failure_denies_and_cleans_up():
    async def scenario():
        async def send(token, method, params):
            raise RuntimeError("telegram down")

        broker = ApprovalBroker(send=send, timeout=1)
        assert await broker.ask("m", {}) is False
        assert broker.pending_count == 0

    asyncio.run(scenario())


def test_broker_cancel_all_denies_waiters():
    async def scenario():
        async def send(token, method, params):
            return None

        broker = ApprovalBroker(send=send, timeout=5)
        task = asyncio.create_task(broker.ask("m", {}))
        await asyncio.sleep(0.01)
        broker.cancel_all()
        assert await task is False

    asyncio.run(scenario())


def test_token_is_unguessable_length():
    async def scenario():
        seen = []

        async def send(token, method, params):
            seen.append(token)

        broker = ApprovalBroker(send=send, timeout=0.01)
        await broker.ask("m", {})
        await broker.ask("m", {})
        assert len(seen[0]) == 8 and seen[0] != seen[1]

    asyncio.run(scenario())
