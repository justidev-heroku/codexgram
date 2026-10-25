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
