"""Профили аккаунтов: активный профиль, sync ротированных токенов, слияние ~/.claude.json, keepalive."""
import json
import stat
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import server

NOW_MS = int(time.time() * 1000)
HOUR_MS = 3_600_000


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def state(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setattr(server, "STATE_DIR", d)
    monkeypatch.setattr(server, "_refresh_cooldown", {})
    return d


def _creds(refresh, expires_ms=NOW_MS + 10 * HOUR_MS, access="acc"):
    return {"claudeAiOauth": {"accessToken": access, "refreshToken": refresh, "expiresAt": expires_ms}}


def _claude(uuid, email, **extra):
    data = {"oauthAccount": {"accountUuid": uuid, "emailAddress": email}, "userID": f"uid-{uuid}"}
    data.update(extra)
    return data


def _write(path: Path, obj):
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj), "utf-8")


def _profile(home: Path, name, refresh, uuid, expires_ms=NOW_MS + 10 * HOUR_MS):
    _write(home / ".claude" / f".credentials.{name}.json", _creds(refresh, expires_ms))
    _write(home / f".claude.{name}.json", _claude(uuid, f"{name}@x.test"))


def _set_active(home: Path, creds, claude):
    _write(home / ".claude" / ".credentials.json", creds)
    if claude is not None:
        _write(home / ".claude.json", claude)


def _read(path: Path):
    return json.loads(path.read_text("utf-8"))


def _msg(text):
    m = MagicMock()
    m.text = text
    m.answer = AsyncMock()
    return m


@pytest.fixture
def switch_env(monkeypatch):
    monkeypatch.setattr(server, "dm_command_gate", lambda msg: {"senderId": "1"})
    monkeypatch.setattr(server, "safe_restart", MagicMock())
    monkeypatch.setattr(server, "refresh_oauth_token", AsyncMock(return_value="ok"))


# ---------------------------------------------------------------------------
# current_profile_name
# ---------------------------------------------------------------------------

def test_current_profile_uses_marker_when_profile_exists(state, tmp_path):
    home = server.USER_HOME
    _profile(home, "a", "ra", "ua")
    _profile(home, "b", "rb", "ub")
    (state / "active_profile").write_text("a", "utf-8")
    _set_active(home, _creds("rb"), _claude("ub", "b@x.test"))  # marker wins over matching
    assert server.current_profile_name() == "a"


def test_current_profile_falls_back_to_account_uuid(state):
    home = server.USER_HOME
    _profile(home, "a", "ra", "ua")
    _profile(home, "b", "rb", "ub")
    _set_active(home, _creds("other-token"), _claude("ub", "b@x.test"))
    assert server.current_profile_name() == "b"


def test_current_profile_falls_back_to_refresh_token(state):
    home = server.USER_HOME
    _profile(home, "a", "ra", "ua")
    _profile(home, "b", "rb", "ub")
    _set_active(home, _creds("ra"), {})  # нет oauthAccount -> по refresh token
    assert server.current_profile_name() == "a"


def test_current_profile_ambiguous_returns_none(state):
    home = server.USER_HOME
    _profile(home, "a", "same", "ua")
    _profile(home, "b", "same", "ua")
    _set_active(home, _creds("same"), _claude("ua", "a@x.test"))
    assert server.current_profile_name() is None


def test_current_profile_no_match_returns_none(state):
    home = server.USER_HOME
    _profile(home, "a", "ra", "ua")
    _set_active(home, _creds("zzz"), _claude("uz", "z@x.test"))
    assert server.current_profile_name() is None


def test_current_profile_stale_marker_is_ignored(state):
    home = server.USER_HOME
    _profile(home, "a", "ra", "ua")
    (state / "active_profile").write_text("gone", "utf-8")  # профиля нет -> фолбэк
    _set_active(home, _creds("ra"), _claude("ua", "a@x.test"))
    assert server.current_profile_name() == "a"


# ---------------------------------------------------------------------------
# sync_active_to_profile
# ---------------------------------------------------------------------------

def test_sync_skips_invalid_json(state):
    home = server.USER_HOME
    _profile(home, "a", "ra", "ua")
    before = (home / ".claude" / ".credentials.a.json").read_text("utf-8")
    (state / "active_profile").write_text("a", "utf-8")
    claude = _claude("ua", "a@x.test")
    for bad in ("{not json", "", "[]", json.dumps({"foo": 1})):
        _set_active(home, bad, claude)
        assert server.sync_active_to_profile() is None
        assert (home / ".claude" / ".credentials.a.json").read_text("utf-8") == before


def test_sync_writes_changed_tokens_with_0600(state):
    home = server.USER_HOME
    _profile(home, "a", "ra", "ua")
    (state / "active_profile").write_text("a", "utf-8")
    rotated = _creds("ra-new", access="new")
    _set_active(home, rotated, _claude("ua", "a@x.test"))
    assert server.sync_active_to_profile() == "a"
    target = home / ".claude" / ".credentials.a.json"
    assert _read(target) == rotated
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    # второй вызов: уже совпадает -> ничего не делаем
    assert server.sync_active_to_profile() is None


def test_sync_refuses_when_active_account_differs(state):
    home = server.USER_HOME
    _profile(home, "a", "ra", "ua")
    (state / "active_profile").write_text("a", "utf-8")
    _set_active(home, _creds("foreign"), _claude("uz", "z@x.test"))
    assert server.sync_active_to_profile() is None
    assert _read(home / ".claude" / ".credentials.a.json") == _creds("ra")


# ---------------------------------------------------------------------------
# /switch_account: rotation scenario and ~/.claude.json merge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rotation_then_switch_away_and_back(state, switch_env):
    home = server.USER_HOME
    _profile(home, "a", "ra-old", "ua")
    _profile(home, "b", "rb", "ub")
    (state / "active_profile").write_text("a", "utf-8")
    # claude-code сам ротировал токен A в активном файле
    rotated_a = _creds("ra-new", access="fresh")
    _set_active(home, rotated_a, _claude("ua", "a@x.test", projects={"/p": {"hasTrustDialogAccepted": True}}))

    await server.cmd_switch_account(_msg("/switch_account b"))

    assert _read(home / ".claude" / ".credentials.a.json") == rotated_a  # профиль A получил свежий токен
    assert _read(home / ".claude" / ".credentials.json") == _creds("rb")
    assert (state / "active_profile").read_text("utf-8") == "b"

    await server.cmd_switch_account(_msg("/switch_account a"))
    assert _read(home / ".claude" / ".credentials.json") == rotated_a  # назад на A работает


@pytest.mark.asyncio
async def test_switch_merges_claude_json_keeps_settings(state, switch_env):
    home = server.USER_HOME
    _profile(home, "b", "rb", "ub")
    (state / "active_profile").write_text("a", "utf-8")
    _write(home / ".claude" / ".credentials.a.json", _creds("ra"))
    _set_active(home, _creds("ra"), _claude("ua", "a@x.test", **{
        "projects": {"/root": {"hasTrustDialogAccepted": True, "mcpServers": {"x": {}}}},
        "mcpServers": {"global": {"command": "y"}},
        "hasCompletedOnboarding": True,
    }))

    await server.cmd_switch_account(_msg("/switch_account b"))

    data = _read(home / ".claude.json")
    assert data["oauthAccount"]["accountUuid"] == "ub"
    assert data["userID"] == "uid-ub"
    assert data["projects"]["/root"]["hasTrustDialogAccepted"] is True
    assert data["mcpServers"] == {"global": {"command": "y"}}
    assert data["hasCompletedOnboarding"] is True
    assert stat.S_IMODE((home / ".claude.json").stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_switch_drops_account_keys_missing_in_profile(state, switch_env):
    home = server.USER_HOME
    _write(home / ".claude" / ".credentials.b.json", _creds("rb"))
    _write(home / ".claude.b.json", {"oauthAccount": {"accountUuid": "ub"}})  # без userID
    _set_active(home, _creds("ra"), _claude("ua", "a@x.test", projects={"/p": {}}))

    await server.cmd_switch_account(_msg("/switch_account b"))
    data = _read(home / ".claude.json")
    assert data["oauthAccount"] == {"accountUuid": "ub"}
    assert "userID" not in data
    assert data["projects"] == {"/p": {}}


@pytest.mark.asyncio
async def test_switch_without_profile_claude_json_keeps_file(state, switch_env):
    home = server.USER_HOME
    _write(home / ".claude" / ".credentials.b.json", _creds("rb"))  # .claude.b.json нет
    _set_active(home, _creds("ra"), _claude("ua", "a@x.test", projects={"/p": {"x": 1}}))

    await server.cmd_switch_account(_msg("/switch_account b"))
    data = _read(home / ".claude.json")
    assert "oauthAccount" not in data and "userID" not in data
    assert data["projects"] == {"/p": {"x": 1}}
    assert (home / ".claude.json").exists()


# ---------------------------------------------------------------------------
# keepalive_once
# ---------------------------------------------------------------------------

@pytest.fixture
def keepalive_env(state, monkeypatch):
    monkeypatch.setattr(server, "PROFILE_KEEPALIVE_GAP", 0)
    monkeypatch.setattr(server, "load_access", lambda: {"owner": "111", "allowFrom": ["111"]})
    home = server.USER_HOME
    now = int(time.time() * 1000)
    # активный аккаунт (текущий профиль "cur")
    _profile(home, "cur", "r-cur", "ucur", expires_ms=now + 10 * HOUR_MS)
    (state / "active_profile").write_text("cur", "utf-8")
    _set_active(home, _creds("r-cur"), _claude("ucur", "cur@x.test"))
    # неактивные
    _write(home / ".claude" / ".credentials.near.json", _creds("r-near", now + HOUR_MS))
    _write(home / ".claude" / ".credentials.past.json", _creds("r-past", now - HOUR_MS))
    _write(home / ".claude" / ".credentials.far.json", _creds("r-far", now + 10 * HOUR_MS))
    _write(home / ".claude" / ".credentials.same.json", _creds("r-cur", now + HOUR_MS))  # тот же токен, что активный
    return home


@pytest.mark.asyncio
async def test_keepalive_refreshes_only_near_expiry_inactive(keepalive_env, monkeypatch):
    refresh = AsyncMock(return_value="refreshed")
    monkeypatch.setattr(server, "refresh_oauth_token", refresh)

    results = await server.keepalive_once(time.time(), None)

    called = sorted(c.args[0].name for c in refresh.await_args_list)
    assert called == [".credentials.near.json", ".credentials.past.json"]
    for c in refresh.await_args_list:
        assert c.kwargs.get("allow_browser") is False
    assert set(results) == {"near", "past"}


@pytest.mark.asyncio
async def test_keepalive_respects_cooldown(keepalive_env, monkeypatch):
    refresh = AsyncMock(return_value="refreshed")
    monkeypatch.setattr(server, "refresh_oauth_token", refresh)
    server._refresh_cooldown[str(keepalive_env / ".claude" / ".credentials.near.json")] = time.time() + 600

    results = await server.keepalive_once(time.time(), None)
    assert results["near"] == "cooldown"
    assert refresh.await_count == 1  # только past


@pytest.mark.asyncio
async def test_keepalive_dead_notified_once_then_cleared(keepalive_env, monkeypatch):
    status = {"value": "error"}

    async def fake_refresh(path, allow_browser=True, force=False):
        return status["value"]

    monkeypatch.setattr(server, "refresh_oauth_token", fake_refresh)
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await server.keepalive_once(time.time(), bot)
    await server.keepalive_once(time.time(), bot)
    assert bot.send_message.await_count == 2  # near и past, по одному разу каждый
    sent_texts = [c.args[1] for c in bot.send_message.await_args_list]
    assert any("<code>near</code>" in t and "/login near" in t for t in sent_texts)
    assert sorted(server._load_dead_profiles()) == ["near", "past"]
    bot.send_message.reset_mock()
    await server.keepalive_once(time.time(), bot)
    assert bot.send_message.await_count == 0  # повторно не шлём

    status["value"] = "refreshed"
    await server.keepalive_once(time.time(), bot)
    assert server._load_dead_profiles() == set()

    status["value"] = "error"
    await server.keepalive_once(time.time(), bot)
    assert bot.send_message.await_count == 2  # после успешного обновления уведомляем снова


@pytest.mark.asyncio
async def test_keepalive_blocked_is_not_dead(keepalive_env, monkeypatch):
    async def fake_refresh(path, allow_browser=True, force=False):
        return "blocked"

    monkeypatch.setattr(server, "refresh_oauth_token", fake_refresh)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    results = await server.keepalive_once(time.time(), bot)
    assert set(results.values()) == {"blocked"}
    assert bot.send_message.await_count == 0
    assert server._load_dead_profiles() == set()


def _fake_session(status_code):
    class Resp:
        def __init__(self):
            self.status_code = status_code
            self.headers = {}
            self.content = b"{}"

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return Resp()

    return Session


@pytest.mark.asyncio
@pytest.mark.parametrize("code,expected", [(400, "error"), (401, "error"), (403, "blocked"),
                                           (429, "blocked"), (502, "blocked")])
async def test_refresh_without_browser_classifies_status(tmp_path, monkeypatch, code, expected):
    import curl_cffi.requests as cr
    monkeypatch.setattr(cr, "AsyncSession", _fake_session(code))
    path = tmp_path / ".credentials.x.json"
    path.write_text('{"claudeAiOauth": {"refreshToken": "r", "expiresAt": 0}}', "utf-8")
    server._refresh_cooldown.pop(str(path), None)
    assert await server.refresh_oauth_token(path, allow_browser=False, force=True) == expected


@pytest.mark.asyncio
async def test_resave_clears_dead_flag(state, switch_env):
    home = server.USER_HOME
    server._save_dead_profiles({"a"})
    _set_active(home, _creds("ra-new"), _claude("ua", "a@x.test"))

    await server.cmd_save_account(_msg("/save_account a"))
    assert "a" not in server._load_dead_profiles()
    assert (state / "active_profile").read_text("utf-8") == "a"


@pytest.mark.asyncio
async def test_accounts_marks_active_and_dead(state, switch_env):
    home = server.USER_HOME
    _profile(home, "a", "ra", "ua")
    _profile(home, "b", "rb", "ub")
    (state / "active_profile").write_text("a", "utf-8")
    _set_active(home, _creds("ra"), _claude("ua", "a@x.test"))
    server._save_dead_profiles({"b"})

    msg = _msg("/accounts")
    await server.cmd_accounts(msg)
    text = msg.answer.await_args.args[0]
    line_a = next(l for l in text.splitlines() if "<code>a</code>" in l)
    line_b = next(l for l in text.splitlines() if "<code>b</code>" in l)
    assert "✅ активный" in line_a
    assert "✅" not in line_b and "токен не обновляется" in line_b


# ---------------------------------------------------------------------------
# sync loop helper: a single pass syncs when mtime changed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_profile_sync_loop_syncs_on_change(state, monkeypatch):
    home = server.USER_HOME
    _profile(home, "a", "ra", "ua")
    (state / "active_profile").write_text("a", "utf-8")
    _set_active(home, _creds("ra-new"), _claude("ua", "a@x.test"))

    import asyncio
    evt = asyncio.Event()
    monkeypatch.setattr(server, "PROFILE_SYNC_INTERVAL", 0.01)
    task = asyncio.create_task(server.profile_sync_loop(evt, MagicMock()))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if _read(home / ".claude" / ".credentials.a.json") == _creds("ra-new"):
            break
    evt.set()
    await asyncio.wait_for(task, timeout=2)
    assert _read(home / ".claude" / ".credentials.a.json") == _creds("ra-new")
