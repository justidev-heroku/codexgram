"""Сборка developerInstructions для треда Codex.

Состав (конкатенация, каждый раз заново — без кэша):
  1. codex_instructions.md  — правила моста (ответ только через reply и т.д.);
  2. codex_workflow.md      — регламент владельца, адаптированный под Codex;
  3. описание системы памяти и индекс MEMORY.md из каталога памяти.

Каталог памяти берётся из CODEX_GRAM_MEMORY_DIR. Отсутствие каталога или файла
не ломает сборку: индекс просто помечается как недоступный.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

DEFAULT_MEMORY_DIR = "/root/.claude/projects/-root/memory"
MAX_INDEX_BYTES = 60 * 1024
INDEX_HEADING = "## Индекс памяти (MEMORY.md)"


def memory_dir_from_env() -> Path:
    return Path(os.environ.get("CODEX_GRAM_MEMORY_DIR") or DEFAULT_MEMORY_DIR)


def _read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _memory_guide(memory_dir: Path) -> str:
    return f"""## Память проекта

Каталог памяти: `{memory_dir}`

Формат: один факт на файл. Вверху frontmatter с полями `name`, `description`, `metadata.type` (одно из: `user`, `feedback`, `project`, `reference`). Для `feedback` и `project` в теле есть строки `**Why:**` (зачем это важно) и `**How to apply:**` (как применять). Связи между файлами записаны как `[[name]]`.

Как пользоваться:
- Индекс `MEMORY.md` ниже в этом сообщении. Когда задача касается конкретного проекта, открой нужный файл по ссылке (`cat`). Не читай всю память подряд.
- Записи могут устареть. Прежде чем на них опираться, проверь, что упомянутые файлы, пути и флаги существуют (`ls`, `grep`). Если чего-то нет, скажи об этом и обнови запись.

Как записывать:
- Можно создавать и обновлять файлы памяти в том же формате.
- Сначала проверь, нет ли уже файла по этой теме. Если есть, обнови его, не создавай дубликат.
- В индекс добавляй ТОЛЬКО одну строку вида `- [Заголовок](file.md) — хук`, дописывая в конец файла (режим `a` / `>>`). Никогда не переписывай `MEMORY.md` целиком: Claude-сессия правит его параллельно.
- Никогда не сохраняй в память секреты: токены, пароли, приватные ключи, OAuth-данные."""


def _index_section(memory_dir: Path) -> str:
    index_path = memory_dir / "MEMORY.md"
    if not index_path.is_file():
        return f"{INDEX_HEADING}\n\n(индекс недоступен: файл `{index_path}` не найден)"
    try:
        raw = index_path.read_bytes()
    except OSError as e:
        return f"{INDEX_HEADING}\n\n(индекс недоступен: {e})"
    note = ""
    if len(raw) > MAX_INDEX_BYTES:
        total = len(raw)
        raw = raw[:MAX_INDEX_BYTES]
        note = f"\n\n[... индекс обрезан: показано {MAX_INDEX_BYTES} из {total} байт ...]"
        body = raw.decode("utf-8", errors="ignore")
    else:
        body = raw.decode("utf-8", errors="replace")
    return f"{INDEX_HEADING}\n\n{body.rstrip()}{note}"


def build_developer_instructions(repo_dir: Path, memory_dir: Optional[Path] = None) -> str:
    repo_dir = Path(repo_dir)
    memory_dir = Path(memory_dir) if memory_dir is not None else memory_dir_from_env()
    parts = [
        _read_text(repo_dir / "codex_instructions.md").strip(),
        _read_text(repo_dir / "codex_workflow.md").strip(),
        _memory_guide(memory_dir),
        _index_section(memory_dir),
    ]
    return "\n\n".join(p for p in parts if p) + "\n"
