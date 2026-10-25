#!/usr/bin/env python3
"""Создаёт STATE_DIR/access.json с владельцем codex-gram (аналог /telegram:init).

Использование:
  python3 deploy/init_access.py 8739693469
  python3 deploy/init_access.py 8739693469 --state-dir /root/.codex-gram/telegram

Файл пишется с правами 0600. Если access.json уже есть и владелец другой, скрипт
ничего не меняет (нужен --force). Токен бота не читается и не печатается.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_STATE = Path.home() / ".codex-gram" / "telegram"
ID_RE = re.compile(r"^[1-9][0-9]{4,19}$")


def build_access(owner: str) -> dict:
    return {"allowFrom": [owner], "owner": owner, "threads": True}


def write_access(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("owner_id", help="числовой Telegram ID владельца")
    parser.add_argument("--state-dir", default=os.environ.get("TELEGRAM_STATE_DIR") or str(DEFAULT_STATE))
    parser.add_argument("--force", action="store_true", help="перезаписать существующий access.json")
    args = parser.parse_args(argv)

    owner = args.owner_id.strip()
    if not ID_RE.match(owner):
        print("ошибка: owner_id должен быть числовым Telegram ID (5–20 цифр, без нуля в начале)", file=sys.stderr)
        return 2
    path = Path(args.state_dir) / "access.json"
    if path.exists() and not args.force:
        try:
            current = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            current = {}
        if current.get("owner") == owner:
            print(f"уже настроено: владелец {owner} ({path})")
            return 0
        print(f"ошибка: {path} уже существует с другим владельцем; запусти с --force, если уверен",
              file=sys.stderr)
        return 1
    write_access(path, build_access(owner))
    env_ok = (Path(args.state_dir) / ".env").is_file()
    print(f"записан {path} (владелец {owner}, права 0600)")
    if not env_ok:
        print(f"внимание: нет {Path(args.state_dir) / '.env'} с TELEGRAM_BOT_TOKEN=...", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
