"""Чистые функции форматирования для команд codex-gram (без Telegram и без I/O).

Здесь: разбор вывода `codex login --device-auth`, проценты лимитов, относительное и
московское время, сравнение версий, обрезка строк для Telegram.
"""

from __future__ import annotations

import datetime
import html
import re
from typing import Any, Optional

MSK = datetime.timezone(datetime.timedelta(hours=3))
ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"          # CSI: цвета, курсор
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: заголовок окна, гиперссылки
    r"|\x1b[@-Z\\-_]"                  # прочие двухбайтовые ESC-последовательности
    r"|\r"
)
URL_RE = re.compile(r"https?://[^\s<>\"'`)\]]+")
DEVICE_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9]{4,5}-[A-Z0-9]{4,6})(?![A-Za-z0-9])")
LOGIN_OK_MARK = "Successfully logged in"


def esc(text: Any) -> str:
    return html.escape(str(text), quote=False)


def truncate(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if limit <= 1 or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def parse_device_login(text: str) -> tuple[Optional[str], Optional[str]]:
    """Достаёт URL и одноразовый код из вывода `codex login --device-auth`."""
    clean = strip_ansi(text)
    url_match = URL_RE.search(clean)
    code_match = DEVICE_CODE_RE.search(clean)
    url = url_match.group(0).rstrip(".,;:") if url_match else None
    code = code_match.group(1) if code_match else None
    return url, code


def login_succeeded(text: str) -> bool:
    return LOGIN_OK_MARK in strip_ansi(text)


def fmt_relative(seconds: float) -> str:
    """Сколько осталось до момента: «через 4 ч 12 мин» / «уже прошло»."""
    if seconds <= 0:
        return "уже прошло"
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"через {days} д {hours} ч" if hours else f"через {days} д"
    if hours:
        return f"через {hours} ч {minutes} мин" if minutes else f"через {hours} ч"
    return f"через {max(minutes, 1)} мин"


def fmt_msk(epoch_seconds: Optional[float]) -> str:
    if epoch_seconds is None:
        return "—"
    try:
        dt = datetime.datetime.fromtimestamp(float(epoch_seconds), tz=MSK)
    except (OverflowError, OSError, ValueError):
        return "—"
    return dt.strftime("%d.%m %H:%M МСК")


def usage_bar(percent: Any, width: int = 10) -> str:
    try:
        value = float(percent)
    except (TypeError, ValueError):
        value = 0.0
    value = min(max(value, 0.0), 100.0)
    filled = int(round(value / 100 * width))
    return "█" * filled + "░" * (width - filled)


def window_label(minutes: Any) -> str:
    try:
        mins = int(minutes)
    except (TypeError, ValueError):
        return "окно"
    if mins <= 0:
        return "окно"
    if mins == 300:
        return "5-часовое окно"
    if mins == 10080:
        return "недельное окно"
    if mins % 1440 == 0:
        return f"{mins // 1440}-дневное окно"
    if mins % 60 == 0:
        return f"{mins // 60}-часовое окно"
    return f"окно {mins} мин"


def _window_line(title: str, window: Optional[dict], now: float) -> list[str]:
    if not isinstance(window, dict):
        return [f"<b>{esc(title)}</b>: нет данных"]
    used = window.get("usedPercent")
    resets = window.get("resetsAt")
    left = ""
    if isinstance(resets, (int, float)):
        left = f" · сброс {fmt_relative(resets - now)} ({fmt_msk(resets)})"
    return [f"<b>{esc(title)}</b>: {int(used or 0)}%{left}", f"<code>{usage_bar(used)}</code>"]


def render_usage(snapshot: dict, now: float, plan: Optional[str] = None,
                 email: Optional[str] = None) -> str:
    """Текст для /usage. snapshot — RateLimitSnapshot из account/rateLimits/read."""
    lines = ["<b>Лимиты Codex</b>"]
    head = []
    if email:
        head.append(esc(email))
    plan_name = plan or snapshot.get("planType")
    if plan_name:
        head.append(f"тариф: {esc(plan_name)}")
    if head:
        lines.append(" · ".join(head))
    lines.append("")
    for key in ("primary", "secondary"):
        window = snapshot.get(key) or {}
        title = window_label(window.get("windowDurationMins")).capitalize()
        lines += _window_line(title, window if window else None, now)
        lines.append("")
    lines.pop()
    credits = snapshot.get("credits")
    if isinstance(credits, dict):
        lines.append("")
        if credits.get("unlimited"):
            lines.append("Кредиты: безлимит")
        elif credits.get("hasCredits"):
            balance = credits.get("balance")
            lines.append(f"Кредиты: {esc(balance)}" if balance else "Кредиты: есть")
        else:
            lines.append("Кредиты: нет")
    reached = snapshot.get("rateLimitReachedType")
    if reached:
        lines.append(f"⚠️ Лимит достигнут: {esc(reached)}")
    return "\n".join(lines)


def usage_alert_text(window_title: str, percent: int, resets_at: Optional[float], now: float) -> str:
    left = f" · сброс {fmt_relative(resets_at - now)} ({fmt_msk(resets_at)})" if resets_at else ""
    if percent >= 100:
        head = "🚫 Лимит исчерпан"
    else:
        head = f"⚠️ Расход {percent}%"
    return f"{head}: <b>{esc(window_title)}</b>{left}"


_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")


def extract_version(text: str) -> Optional[str]:
    match = _VERSION_RE.search(text or "")
    return match.group(1) if match else None


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def is_newer(candidate: Optional[str], current: Optional[str]) -> bool:
    if not candidate or not current:
        return False
    try:
        return version_tuple(candidate) > version_tuple(current)
    except ValueError:
        return False


def fmt_ago(seconds: float) -> str:
    """«5 мин назад» по числу секунд давности."""
    if seconds < 60:
        return "только что"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    return f"{hours // 24} дн назад"
