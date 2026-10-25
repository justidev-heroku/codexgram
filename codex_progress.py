"""Пузырь прогресса: последние действия Codex в одном сообщении Telegram.

ActionTracker принимает ThreadItem из item/started и item/completed и хранит последние
MAX_ACTIONS действий. Telegram здесь не используется: render() отдаёт HTML, а отправку и
редактирование делает server.py.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional

from codex_format import esc, truncate

MAX_ACTIONS = 5
LABEL_LIMIT = 80
REPLY_TOOLS = frozenset({"reply", "reply_file", "send_file_to_tg"})
_FAILED = {"failed", "declined", "error"}
_DONE = {"completed", "success", "succeeded"}


def _status_mark(status: Optional[str], exit_code: Optional[int] = None) -> str:
    if status in _FAILED or (isinstance(exit_code, int) and exit_code != 0):
        return "❌"
    if status in _DONE or exit_code == 0:
        return "✅"
    return "⏳"


def describe_item(item: dict, phase: str) -> Optional[tuple[str, str]]:
    """Возвращает (метка, метка статуса) или None, если элемент не показываем в пузыре."""
    kind = item.get("type")
    status = item.get("status")
    if kind == "commandExecution":
        command = item.get("command") or ""
        if not command:
            return None
        mark = _status_mark(status, item.get("exitCode"))
        return mark, f"$ {truncate(command, LABEL_LIMIT)}"
    if kind == "fileChange":
        paths = [c.get("path", "") for c in item.get("changes") or [] if isinstance(c, dict)]
        paths = [p for p in paths if p]
        if not paths:
            return None
        label = "✏️ " + truncate(", ".join(paths), LABEL_LIMIT)
        return _status_mark(status), label
    if kind == "mcpToolCall":
        tool = item.get("tool") or "?"
        if item.get("server") == "telegram" and tool in REPLY_TOOLS:
            return _status_mark(status), "💬 отправляю ответ"
        return _status_mark(status), f"🔧 {truncate(str(item.get('server') or '') + '.' + tool, LABEL_LIMIT)}"
    if kind == "dynamicToolCall":
        return _status_mark(status), f"🔧 {truncate(item.get('tool') or '?', LABEL_LIMIT)}"
    if kind == "webSearch":
        query = item.get("query") or ""
        if not query:
            return None
        return _status_mark("completed" if phase == "completed" else None), f"🔎 {truncate(query, LABEL_LIMIT)}"
    if kind == "imageView":
        return _status_mark("completed" if phase == "completed" else None), "🖼 " + truncate(item.get("path") or "", LABEL_LIMIT)
    return None


class ActionTracker:
    def __init__(self, max_actions: int = MAX_ACTIONS):
        self.max_actions = max_actions
        self._items: "OrderedDict[str, tuple[str, str]]" = OrderedDict()
        self.dirty = False

    def on_item(self, item: dict, phase: str) -> bool:
        """phase: 'started' или 'completed'. True, если пузырь нужно перерисовать."""
        if not isinstance(item, dict):
            return False
        described = describe_item(item, phase)
        if described is None:
            return False
        key = str(item.get("id") or f"anon-{len(self._items)}-{item.get('type')}")
        if key in self._items:
            self._items[key] = described
        else:
            self._items[key] = described
            while len(self._items) > self.max_actions:
                self._items.popitem(last=False)
        self.dirty = True
        return True

    def actions(self) -> list[tuple[str, str]]:
        return list(self._items.values())

    def render(self, header: str) -> str:
        lines = [f"<blockquote><b>{esc(header)}</b></blockquote>"]
        if self._items:
            body = "\n".join(f"{mark} {esc(label)}" for mark, label in self._items.values())
            lines.append(f"<blockquote>{body}</blockquote>")
        self.dirty = False
        return "\n".join(lines)
