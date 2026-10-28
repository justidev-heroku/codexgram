"""Изоляция тестов от боевого состояния codex-gram.

server.py при импорте читает STATE_DIR (.env, access.json). Здесь каталог состояния и
CODEX_HOME подменяются на временные до импорта server, а на каждый тест — заново.
Тесты никогда не запускают настоящие codex, npm или systemctl (фейковый app-server — можно).
"""
import os
import tempfile

_TMP_STATE = tempfile.mkdtemp(prefix="codex-gram-test-state-")
_TMP_CODEX_HOME = tempfile.mkdtemp(prefix="codex-gram-test-codex-home-")
os.environ["TELEGRAM_STATE_DIR"] = _TMP_STATE
os.environ["CODEX_GRAM_CODEX_HOME"] = _TMP_CODEX_HOME
os.environ["CODEX_GRAM_MEMORY_DIR"] = os.path.join(_TMP_STATE, "_no_memory")
# Тесты никогда не должны вести себя как боевой поллер (PID-файл, kill «старого» бота).
os.environ.pop("CLAUDE_TELEGRAM_BACKGROUND", None)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:TEST_TOKEN_not_real_aaaaaaaaaaaaaaaaaa")

import asyncio  # noqa: E402
import sys  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_codex_home(tmp_path, monkeypatch):
    """CODEX_HOME на каждый тест — временный. ~/.codex и auth.json не трогаются."""
    home = tmp_path / "_codex_home"
    home.mkdir()
    monkeypatch.setenv("CODEX_GRAM_CODEX_HOME", str(home))
    yield home


@pytest.fixture(autouse=True)
def _no_real_codex_cli(monkeypatch):
    """Настоящие codex и npm из тестов не запускаются."""
    real = asyncio.create_subprocess_exec

    async def _guard(program, *args, **kwargs):
        name = os.path.basename(str(program))
        if name in ("codex", "npm", "systemctl"):
            raise AssertionError(f"test tried to run an external CLI: {program} {args}")
        return await real(program, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _guard)
    yield


# ---------------------------------------------------------------------------
# Гости (этап 3): ни один тест не поднимает настоящий юнит гостя и не создаёт
# каталоги гостей в реальном доме. В claude-gram тест однажды завёл фейкового гостя
# 222222222 настоящим `systemctl enable --now`. Здесь:
#   - server._systemctl подменён AsyncMock на каждый тест (оригинал — через real_systemctl,
#     и там create_subprocess_exec тоже подменён);
#   - USER_HOME, GUESTS_STATE_ROOT и CODEX_GRAM_GUESTS_DIR — временные.
# ---------------------------------------------------------------------------
from unittest.mock import AsyncMock  # noqa: E402

_REAL_SYSTEMCTL: dict = {}


@pytest.fixture(autouse=True)
def _no_real_systemctl(monkeypatch):
    import server  # noqa: WPS433 - импорт после установки env выше

    _REAL_SYSTEMCTL.setdefault("fn", server._systemctl)
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(server, "_systemctl", mock)
    yield mock


@pytest.fixture(autouse=True)
def _isolate_guest_dirs(tmp_path, monkeypatch):
    import server  # noqa: WPS433

    home = tmp_path / "_user_home"
    home.mkdir()
    monkeypatch.setattr(server, "USER_HOME", home)
    monkeypatch.setattr(server, "GUESTS_STATE_ROOT", tmp_path / "_guests_state")
    monkeypatch.setenv("CODEX_GRAM_GUESTS_DIR", str(tmp_path / "_guests_work"))
    yield home


@pytest.fixture
def real_systemctl(monkeypatch, _no_real_systemctl):
    """Оригинальный server._systemctl с подменённым subprocess (systemctl не запускается)."""
    import server  # noqa: WPS433

    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate.return_value = (b"", b"")
    proc.kill = lambda: None
    exec_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_mock)
    monkeypatch.setattr(server, "_systemctl", _REAL_SYSTEMCTL["fn"])
    yield exec_mock


_REAL_GUEST_PATHS = ("/root/guests-codex", "/root/.codex-gram/telegram/guests")


def pytest_sessionstart(session):
    session.config._guest_paths_before = {p: os.path.exists(p) for p in _REAL_GUEST_PATHS}


def pytest_sessionfinish(session, exitstatus):
    """Тесты не должны создавать гостевые каталоги в реальном доме / боевом состоянии."""
    before = getattr(session.config, "_guest_paths_before", {})
    leaked = [p for p in _REAL_GUEST_PATHS if os.path.exists(p) and not before.get(p)]
    if leaked:
        session.exitstatus = 1
        sys.stderr.write(f"\nERROR: tests created real guest paths: {leaked}\n")
