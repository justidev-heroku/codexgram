"""assert_sendable: сгенерированные Codex картинки отправляются, всё остальное под ~/.codex и скрытые папки — нет.

HOME и CODEX_HOME подменяются на временные каталоги, настоящий ~/.codex не трогается.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = Path(os.path.realpath(tmp_path / "home"))
    (home / ".codex" / "generated_images" / "thr1").mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / "docs").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_GRAM_CODEX_HOME", raising=False)
    return home


def _write(p: Path, text: str = "x") -> str:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_generated_image_is_allowed(fake_home):
    img = _write(fake_home / ".codex" / "generated_images" / "thr1" / "pic.png")
    server.assert_sendable(img)


def test_codex_auth_json_is_denied(fake_home):
    auth = _write(fake_home / ".codex" / "auth.json", "{}")
    with pytest.raises(ValueError):
        server.assert_sendable(auth)


def test_dotdot_escape_from_generated_images_is_denied(fake_home):
    _write(fake_home / ".codex" / "auth.json", "{}")
    escape = str(fake_home / ".codex" / "generated_images" / ".." / "auth.json")
    with pytest.raises(ValueError):
        server.assert_sendable(escape)


def test_symlink_inside_generated_images_to_ssh_key_is_denied(fake_home):
    key = _write(fake_home / ".ssh" / "id_rsa", "PRIVATE")
    link = fake_home / ".codex" / "generated_images" / "thr1" / "link.png"
    os.symlink(key, link)
    with pytest.raises(ValueError):
        server.assert_sendable(str(link))


def test_hidden_subdir_inside_generated_images_is_denied(fake_home):
    hidden = _write(fake_home / ".codex" / "generated_images" / ".hidden" / "pic.png")
    with pytest.raises(ValueError):
        server.assert_sendable(hidden)


def test_hidden_file_inside_generated_images_is_denied(fake_home):
    hidden = _write(fake_home / ".codex" / "generated_images" / "thr1" / ".pic.png")
    with pytest.raises(ValueError):
        server.assert_sendable(hidden)


def test_ssh_dir_is_denied(fake_home):
    key = _write(fake_home / ".ssh" / "id_rsa", "PRIVATE")
    with pytest.raises(ValueError):
        server.assert_sendable(key)


def test_codex_home_env_is_respected(fake_home, monkeypatch):
    custom = fake_home / ".alt-codex"
    img = _write(custom / "generated_images" / "t" / "pic.png")
    auth = _write(custom / "auth.json", "{}")
    monkeypatch.setenv("CODEX_HOME", str(custom))
    server.assert_sendable(img)
    with pytest.raises(ValueError):
        server.assert_sendable(auth)

