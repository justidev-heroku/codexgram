"""reply_file не должен отдавать секреты: CODEX_HOME (auth.json) и каталог состояния."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402


@pytest.mark.parametrize("path", [
    str(Path.home() / ".codex" / "auth.json"),
    str(Path.home() / ".codex" / "config.toml"),
    str(server.STATE_DIR / ".env"),
    str(server.STATE_DIR / "codex_settings.json"),
])
def test_secrets_are_not_sendable(path, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE_DIR", server.STATE_DIR)
    with pytest.raises(Exception):
        server.assert_sendable(path)
