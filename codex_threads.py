"""Список тредов для /resume: какие треды показывать и как подписывать кнопки.

Codex берёт в качестве `preview` первое сообщение треда. Наши сообщения приходят в виде
`<channel source="telegram" chat_id="..." ...>текст</channel>`, поэтому без очистки кнопка
показывала бы сырой тег. Фильтрация: см. `is_own_thread`.
"""

from __future__ import annotations

import html
import re
from typing import Optional

from codex_format import fmt_ago, truncate

LABEL_LIMIT = 48
EMPTY_LABEL_TEXT = "(медиа/без текста)"

# Полный открывающий тег (атрибуты могут содержать «>» внутри кавычек).
_OPEN_TAG_RE = re.compile(r"<channel\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", re.IGNORECASE)
_CLOSE_TAG_RE = re.compile(r"</channel\s*>", re.IGNORECASE)
# Открывающий тег, оборванный превью (закрывающего «>» уже нет): всё с него — служебное.
_OPEN_TAG_CUT_RE = re.compile(r"<channel\b.*$", re.IGNORECASE | re.DOTALL)
# Закрывающий тег, оборванный в конце превью: «<», «</», «</chan» и т.п.
_CLOSE_TAG_CUT_RE = re.compile(r"</?(?:c(?:h(?:a(?:n(?:n(?:e(?:l)?)?)?)?)?)?)?$", re.IGNORECASE)
_CHANNEL_ATTR_RE = re.compile(r"chat_id\s*=\s*\"([^\"]*)\"")


def clean_preview(preview: Optional[str]) -> str:
    """Текст первого сообщения без служебного тега <channel>; пустая строка, если текста нет."""
    text = preview or ""
    text = _OPEN_TAG_RE.sub(" ", text)
    text = _CLOSE_TAG_RE.sub(" ", text)
    text = _OPEN_TAG_CUT_RE.sub(" ", text)
    text = _CLOSE_TAG_CUT_RE.sub("", text)
    text = html.unescape(text)
    return " ".join(text.split())


def preview_chat_id(preview: Optional[str]) -> Optional[str]:
    """chat_id из первого тега <channel> превью (если тег есть и цел)."""
    m = _OPEN_TAG_RE.search(preview or "")
    if not m:
        return None
    attr = _CHANNEL_ATTR_RE.search(m.group(0))
    return attr.group(1) if attr else None


def is_own_thread(thread: dict, owner_chat_id: Optional[str]) -> bool:
    """Тред принадлежит этому боту и владельцу.

    В Thread (ThreadListResponse) нет поля serviceName, поэтому признак «наш» берём из превью:
    первое сообщение треда несёт chat_id владельца. Тред с другим chat_id (в т.ч. тестовый
    chat_id="111" от смоук-прогонов) и тред без тега (чужой или пустой) не показываем.
    Если владелец не известен, не показываем ничего.
    """
    if not owner_chat_id:
        return False
    return preview_chat_id(thread.get("preview")) == str(owner_chat_id)


def thread_title(thread: dict) -> str:
    """Название треда: user-facing name, иначе очищенное превью, иначе заглушка."""
    name = " ".join(str(thread.get("name") or "").split())
    if name:
        return html.unescape(name)
    return clean_preview(thread.get("preview")) or EMPTY_LABEL_TEXT


def build_thread_label(thread: dict, now: float, *, current: bool = False) -> str:
    """Подпись кнопки: «🕐 <давность> · <текст>», общая длина до LABEL_LIMIT."""
    updated = float(thread.get("updatedAt") or now)
    ago = fmt_ago(now - updated)
    label = truncate(f"🕐 {ago} · {thread_title(thread)}", LABEL_LIMIT)
    return f"✓ {label}" if current else label
