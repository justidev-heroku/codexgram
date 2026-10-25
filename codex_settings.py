"""Настройки codex-gram, которые меняются из Telegram: модель, effort, режим approvals.

Файл STATE_DIR/codex_settings.json. Читается при каждом ходе (turn/start), поэтому
смена модели или effort действует со следующего сообщения, без рестарта.
Запись атомарная (tmp + os.replace). Битый файл трактуется как пустой.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SETTINGS_FILE_NAME = "codex_settings.json"
AUTO_NEVER = "never"            # полный автопилот (по умолчанию)
AUTO_ON_REQUEST = "on-request"  # запросы подтверждений показываются в Telegram
APPROVAL_POLICIES = (AUTO_NEVER, AUTO_ON_REQUEST)
EFFORT_FALLBACK = ("minimal", "low", "medium", "high", "xhigh")
_STR_KEYS = ("model", "effort")


def settings_path(state_dir: Path) -> Path:
    return Path(state_dir) / SETTINGS_FILE_NAME


def load_settings(state_dir: Path) -> dict:
    try:
        data = json.loads(settings_path(state_dir).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update_settings(state_dir: Path, **changes: Any) -> dict:
    """Меняет ключи. Значение None удаляет ключ. Возвращает новое содержимое."""
    state_dir = Path(state_dir)
    data = load_settings(state_dir)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    state_dir.mkdir(parents=True, exist_ok=True)
    target = settings_path(state_dir)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(tmp, target)
    return data


def approval_policy(settings: dict) -> str:
    value = settings.get("auto")
    return value if value in APPROVAL_POLICIES else AUTO_NEVER


def model_setting(settings: dict) -> str | None:
    value = settings.get("model")
    return value if isinstance(value, str) and value else None


def effort_setting(settings: dict) -> str | None:
    value = settings.get("effort")
    return value if isinstance(value, str) and value else None
