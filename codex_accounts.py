"""Несколько аккаунтов Codex: сохранённые копии auth.json.

CODEX_HOME (по умолчанию ~/.codex, переопределяется CODEX_GRAM_CODEX_HOME) содержит
auth.json активного аккаунта. Сохранённые копии лежат в
STATE_DIR/accounts/<имя>/auth.json (0600). Имя проверяется регэкспом, а итоговый путь
дополнительно сверяется с каталогом аккаунтов, так что обхода каталога не будет.

Модуль не импортирует server.py. Все пути передаются явно, поэтому тесты работают во
временных каталогах и не трогают ~/.codex.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ACCOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
AUTH_FILE = "auth.json"
ACCOUNTS_DIR = "accounts"


class AccountError(ValueError):
    """Ошибка, которую можно показать пользователю как есть."""


def codex_home_dir() -> Path:
    raw = os.environ.get("CODEX_GRAM_CODEX_HOME") or os.environ.get("CODEX_HOME")
    return Path(raw) if raw else Path.home() / ".codex"


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not ACCOUNT_NAME_RE.match(name):
        raise AccountError("Имя аккаунта: 1–32 символа, латиница, цифры, «_» или «-».")
    return name


def accounts_root(state_dir: Path) -> Path:
    return Path(state_dir) / ACCOUNTS_DIR


def account_dir(state_dir: Path, name: str) -> Path:
    validate_name(name)
    root = accounts_root(state_dir).resolve()
    target = (root / name).resolve()
    if target.parent != root:  # защита на случай подмены симлинком или иного обхода
        raise AccountError("Недопустимый путь аккаунта.")
    return target


def list_accounts(state_dir: Path) -> list[str]:
    root = accounts_root(state_dir)
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    names = []
    for entry in entries:
        if not ACCOUNT_NAME_RE.match(entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        if (entry / AUTH_FILE).is_file() and not (entry / AUTH_FILE).is_symlink():
            names.append(entry.name)
    return names


def write_secret(path: Path, data: bytes) -> None:
    """Пишет файл с правами 0600 атомарно (сначала tmp с 0600, потом replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _read_regular(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise AccountError("auth.json не найден или это не обычный файл.")
    return path.read_bytes()


def save_account(state_dir: Path, codex_home: Path, name: str) -> bool:
    """Копирует текущий auth.json в сохранённый аккаунт. True, если запись перезаписала старую."""
    target_dir = account_dir(state_dir, name)
    data = _read_regular(Path(codex_home) / AUTH_FILE)
    existed = (target_dir / AUTH_FILE).is_file()
    write_secret(target_dir / AUTH_FILE, data)
    return existed


def switch_account(state_dir: Path, codex_home: Path, name: str) -> None:
    """Кладёт сохранённый auth.json обратно в CODEX_HOME."""
    source = account_dir(state_dir, name) / AUTH_FILE
    if name not in list_accounts(state_dir):
        raise AccountError("Такого сохранённого аккаунта нет.")
    data = _read_regular(source)
    write_secret(Path(codex_home) / AUTH_FILE, data)


def delete_account(state_dir: Path, name: str) -> bool:
    try:
        target_dir = account_dir(state_dir, name)
    except AccountError:
        return False
    auth = target_dir / AUTH_FILE
    if not target_dir.is_dir() or target_dir.is_symlink():
        return False
    if auth.exists() or auth.is_symlink():
        if auth.is_symlink():
            auth.unlink()
        else:
            # Затираем содержимое перед удалением, чтобы токен не остался в свободных блоках.
            size = auth.stat().st_size
            with open(auth, "r+b") as fh:
                fh.write(b"\0" * size)
                fh.flush()
                os.fsync(fh.fileno())
            auth.unlink()
    try:
        target_dir.rmdir()
    except OSError:
        pass
    return True


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, AccountError):
        return str(exc)
    return f"Не получилось: {exc.__class__.__name__}"
