"""Изоляция тестов от боевого состояния бота.

server.py при импорте читает ~/.claude/channels/telegram (.env, access.json), а
load_access() умеет мигрировать access.json на диске. Чтобы тесты никогда не
трогали реальные файлы, до импорта server подменяем каталог состояния на
временный и подставляем фиктивный токен.
"""
import os
import tempfile

_TMP_STATE = tempfile.mkdtemp(prefix="claude-gram-test-state-")
os.environ["TELEGRAM_STATE_DIR"] = _TMP_STATE
# Тесты никогда не должны вести себя как боевой поллер (PID-файл, kill «старого» бота).
os.environ.pop("CLAUDE_TELEGRAM_BACKGROUND", None)
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:TEST_TOKEN_not_real_aaaaaaaaaaaaaaaaaa")


# ---------------------------------------------------------------------------
# Запрет реальных systemctl-вызовов из тестов.
#
# server._systemctl запускает `systemctl enable/disable --now` для гостевых
# юнитов. Тесты (/share и т.п.) доходят до него через обычные обработчики, поэтому
# подменяем функцию на AsyncMock для каждого теста. Оригинал сохраняем, чтобы
# тест, который сам проверяет _systemctl, мог получить его через фикстуру
# real_systemctl (там subprocess тоже подменён).
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402

_REAL_SYSTEMCTL: dict = {}


@pytest.fixture(autouse=True)
def _no_real_systemctl(monkeypatch):
    import server  # noqa: WPS433 - импорт после установки env выше

    _REAL_SYSTEMCTL.setdefault("fn", server._systemctl)
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(server, "_systemctl", mock)
    yield mock


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
