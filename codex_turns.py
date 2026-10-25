"""Учёт ходов: был ли ответ пользователю, чем закончился ход, уведомления и алерты лимитов.

TurnTracker: вердикт по turn/completed. Если ход завершился сбоем, прерыванием или
без вызова reply/reply_file (детектируется по mcpToolCall из item/started), возвращается
текст предупреждения и флаг, нужны ли кнопки «Повторить».

Throttle: не даёт слать одинаковые или частые служебные уведомления (warning, deprecation,
model/rerouted, error) чаще раза в интервал.

UsageAlerts: один алерт на окно и порог (80% и 100%), дедуп по resetsAt. Ключи
сохраняются, чтобы после рестарта не спамить тем же алертом.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from codex_format import esc, truncate

NO_REPLY_TEXT = "⚠️ Ход завершился без ответа"
FAILED_TEXT = "⚠️ Ход завершился с ошибкой"
INTERRUPTED_TEXT = "⚠️ Ход прерван"
STOPPED_TEXT = "⏹ Ход остановлен по команде /stop."


@dataclass
class Verdict:
    kind: str                 # ok | stopped | failed | interrupted | no_reply
    text: str = ""
    retry: bool = False       # показать «Повторить» и «Сменить модель»


@dataclass
class TurnTracker:
    replied: set = field(default_factory=set)
    errors: dict = field(default_factory=dict)
    last_user_text: Optional[str] = None
    stop_requested: set = field(default_factory=set)
    _order: list = field(default_factory=list)
    _limit: int = 500

    def _remember(self, turn_id: str) -> None:
        self._order.append(turn_id)
        if len(self._order) > self._limit:
            old = self._order.pop(0)
            self.replied.discard(old)
            self.errors.pop(old, None)
            self.stop_requested.discard(old)

    def note_reply(self, turn_id: Optional[str]) -> None:
        if turn_id:
            self.replied.add(turn_id)
            self._remember(turn_id)

    def note_error(self, turn_id: Optional[str], message: str) -> None:
        if turn_id and message:
            self.errors[turn_id] = message
            self._remember(turn_id)

    def note_stop(self, turn_id: Optional[str]) -> None:
        if turn_id:
            self.stop_requested.add(turn_id)
            self._remember(turn_id)

    def set_last_user(self, text: str) -> None:
        self.last_user_text = text

    def on_turn_completed(self, turn_id: Optional[str], status: str,
                          error_message: Optional[str] = None) -> Verdict:
        replied = bool(turn_id) and turn_id in self.replied
        stored_error = self.errors.get(turn_id or "", "")
        message = truncate(error_message or stored_error or "", 300)
        if status == "interrupted":
            if turn_id in self.stop_requested:
                return Verdict("stopped", STOPPED_TEXT, retry=False)
            return Verdict("interrupted", _with_detail(INTERRUPTED_TEXT, message), retry=True)
        if status == "failed":
            return Verdict("failed", _with_detail(FAILED_TEXT, message), retry=True)
        if status == "completed" and not replied:
            return Verdict("no_reply", _with_detail(NO_REPLY_TEXT, message), retry=True)
        return Verdict("ok")


def _with_detail(head: str, message: str) -> str:
    if not message:
        return head
    return f"{head}\n<blockquote>{esc(message)}</blockquote>"


class Throttle:
    """allow(text) → True, если такое уведомление можно отправить сейчас."""

    def __init__(self, interval: float = 30.0, same_window: float = 600.0,
                 clock: Callable[[], float] = time.monotonic):
        self.interval = interval
        self.same_window = same_window
        self._clock = clock
        self._last_sent = None
        self._seen: dict[str, float] = {}

    def allow(self, text: str) -> bool:
        now = self._clock()
        last_same = self._seen.get(text)
        if last_same is not None and now - last_same < self.same_window:
            return False
        if self._last_sent is not None and now - self._last_sent < self.interval:
            return False
        self._last_sent = now
        self._seen[text] = now
        if len(self._seen) > 200:
            for key in sorted(self._seen, key=self._seen.get)[:100]:
                self._seen.pop(key, None)
        return True


THRESHOLDS = (80, 100)


@dataclass
class UsageAlert:
    key: str
    window: str
    percent: int
    threshold: int
    resets_at: Optional[int]


class UsageAlerts:
    MAX_KEYS = 200

    def __init__(self, seen: Iterable[str] = ()):
        self.seen: list[str] = list(dict.fromkeys(str(k) for k in seen))

    def check(self, snapshot: dict) -> list[UsageAlert]:
        """snapshot — RateLimitSnapshot. Возвращает новые алерты и запоминает их."""
        if not isinstance(snapshot, dict):
            return []
        out: list[UsageAlert] = []
        limit_id = str(snapshot.get("limitId") or "codex")
        for window in ("primary", "secondary"):
            data = snapshot.get(window)
            if not isinstance(data, dict):
                continue
            try:
                percent = int(data.get("usedPercent") or 0)
            except (TypeError, ValueError):
                continue
            resets = data.get("resetsAt")
            resets_int = int(resets) if isinstance(resets, (int, float)) else None
            for threshold in THRESHOLDS:
                if percent < threshold:
                    continue
                key = f"{limit_id}:{window}:{threshold}:{resets_int}"
                if key in self.seen:
                    continue
                self.seen.append(key)
                out.append(UsageAlert(key, window, percent, threshold, resets_int))
        if len(self.seen) > self.MAX_KEYS:
            self.seen = self.seen[-self.MAX_KEYS:]
        return out
