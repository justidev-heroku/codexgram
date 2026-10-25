"""Тесты сборки developerInstructions: правила моста, регламент, память.

Все файлы памяти — только во временных каталогах. Реальный каталог памяти не трогаем.
"""
import asyncio
import json
import sys
from pathlib import Path

from codex_bridge import CodexBridge
from codex_instructions import (
    DEFAULT_MEMORY_DIR,
    MAX_INDEX_BYTES,
    build_developer_instructions,
    memory_dir_from_env,
)

REPO = Path(__file__).resolve().parent.parent
FAKE = REPO / "tests" / "fixtures" / "fake_app_server.py"


def _mem(tmp_path: Path, index: bytes | None = b"- [A](a.md) \xe2\x80\x94 hook\n") -> Path:
    d = tmp_path / "memory"
    d.mkdir(parents=True, exist_ok=True)
    if index is not None:
        (d / "MEMORY.md").write_bytes(index)
    return d


# ---------------------------------------------------------------------------
# Состав
# ---------------------------------------------------------------------------


def test_contains_bridge_rules_workflow_and_index(tmp_path):
    text = build_developer_instructions(REPO, _mem(tmp_path))
    assert "Telegram bridge (codex-gram)" in text  # codex_instructions.md
    assert "Рабочий регламент владельца" in text  # codex_workflow.md
    assert "Согласовываешь план" in text
    assert "## Индекс памяти (MEMORY.md)" in text
    assert "- [A](a.md) — hook" in text


def test_workflow_has_owner_rules_and_no_claude_only_items(tmp_path):
    text = build_developer_instructions(REPO, _mem(tmp_path))
    for must in ("Ничего не писать", "reply", "format='html'", "sg-code-backup", "push", "pytest"):
        assert must in text, must
    lowered = text.lower()
    for banned in ("haiku", "opus", "askuserquestion", "model=\"haiku\"", "agent tool"):
        assert banned not in lowered, banned


def test_memory_guide_describes_format_and_write_rules(tmp_path):
    mem = _mem(tmp_path)
    text = build_developer_instructions(REPO, mem)
    assert f"`{mem}`" in text
    for must in ("metadata.type", "**Why:**", "**How to apply:**", "[[name]]", "не переписывай", "обнови его"):
        assert must in text, must
    assert "Никогда не сохраняй" in text


# ---------------------------------------------------------------------------
# Свежее чтение
# ---------------------------------------------------------------------------


def test_each_build_reads_memory_fresh(tmp_path):
    mem = _mem(tmp_path, b"- [Old](old.md) \xe2\x80\x94 first\n")
    first = build_developer_instructions(REPO, mem)
    (mem / "MEMORY.md").write_bytes(b"- [New](new.md) \xe2\x80\x94 second\n")
    second = build_developer_instructions(REPO, mem)
    assert "old.md" in first and "old.md" not in second
    assert "new.md" in second


def test_bridge_thread_start_sees_memory_change_between_sessions(tmp_path):
    mem = _mem(tmp_path, b"MARK-ONE\n")
    state = tmp_path / "state"
    log = tmp_path / "fake.jsonl"
    bridge = CodexBridge(
        state_dir=state,
        cwd=str(tmp_path),
        instructions=lambda: build_developer_instructions(REPO, mem),
        cmd=[sys.executable, str(FAKE), str(tmp_path / "fs.json"), str(log)],
        log=lambda m: None,
        restart_backoff=(0.05, 0.2),
        request_timeout=10,
    )

    async def scenario():
        await bridge.start(timeout=10)
        (mem / "MEMORY.md").write_bytes(b"MARK-TWO\n")
        await bridge.new_thread()
        await bridge.stop()

    asyncio.run(asyncio.wait_for(scenario(), 60))
    starts = [json.loads(x) for x in log.read_text("utf-8").splitlines()]
    starts = [r["params"] for r in starts if r.get("kind") == "request" and r.get("method") == "thread/start"]
    assert len(starts) == 2
    assert "MARK-ONE" in starts[0]["developerInstructions"]
    assert "MARK-TWO" not in starts[0]["developerInstructions"]
    assert "MARK-TWO" in starts[1]["developerInstructions"]
    assert "MARK-ONE" not in starts[1]["developerInstructions"]


# ---------------------------------------------------------------------------
# Граничные случаи
# ---------------------------------------------------------------------------


def test_missing_memory_dir_does_not_crash(tmp_path):
    text = build_developer_instructions(REPO, tmp_path / "does-not-exist")
    assert "Telegram bridge (codex-gram)" in text
    assert "## Индекс памяти (MEMORY.md)" in text
    assert "индекс недоступен" in text


def test_missing_index_file_does_not_crash(tmp_path):
    mem = _mem(tmp_path, index=None)
    text = build_developer_instructions(REPO, mem)
    assert "индекс недоступен" in text
    assert "Рабочий регламент владельца" in text


def test_huge_index_is_capped_with_note(tmp_path):
    big = ("- [Строка](x.md) — длинная строка с юникодом 🦔\n" * 10000).encode("utf-8")
    assert len(big) > MAX_INDEX_BYTES
    mem = _mem(tmp_path, big)
    text = build_developer_instructions(REPO, mem)
    idx = text.split("## Индекс памяти (MEMORY.md)", 1)[1]
    assert "индекс обрезан" in idx
    assert f"показано {MAX_INDEX_BYTES} из {len(big)} байт" in idx
    assert len(idx.encode("utf-8")) < MAX_INDEX_BYTES + 400


def test_non_utf8_bytes_in_index_do_not_crash(tmp_path):
    mem = _mem(tmp_path, b"- [Bad](b.md) \xff\xfe broken \x80\x81 bytes\n")
    text = build_developer_instructions(REPO, mem)
    assert "broken" in text and "bytes" in text


def test_non_utf8_bytes_in_truncated_index_do_not_crash(tmp_path):
    raw = b"\xff" * (MAX_INDEX_BYTES + 100)
    mem = _mem(tmp_path, raw)
    text = build_developer_instructions(REPO, mem)
    assert "индекс обрезан" in text


def test_memory_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_GRAM_MEMORY_DIR", str(tmp_path / "m"))
    assert memory_dir_from_env() == tmp_path / "m"
    monkeypatch.delenv("CODEX_GRAM_MEMORY_DIR")
    assert str(memory_dir_from_env()) == DEFAULT_MEMORY_DIR
