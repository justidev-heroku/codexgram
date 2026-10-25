"""deploy/init_access.py: создание access.json с владельцем, защита от перезаписи."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "deploy" / "init_access.py"
sys.path.insert(0, str(SCRIPT.parent))
import init_access  # noqa: E402


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def test_creates_access_with_owner_and_0600(tmp_path):
    r = run("8739693469", "--state-dir", str(tmp_path))
    assert r.returncode == 0, r.stderr
    data = json.loads((tmp_path / "access.json").read_text("utf-8"))
    assert data == {"allowFrom": ["8739693469"], "owner": "8739693469", "threads": True}
    assert oct((tmp_path / "access.json").stat().st_mode & 0o777) == "0o600"


def test_same_owner_is_idempotent(tmp_path):
    run("8739693469", "--state-dir", str(tmp_path))
    r = run("8739693469", "--state-dir", str(tmp_path))
    assert r.returncode == 0 and "уже настроено" in r.stdout


def test_other_owner_refused_without_force(tmp_path):
    run("8739693469", "--state-dir", str(tmp_path))
    r = run("123456789", "--state-dir", str(tmp_path))
    assert r.returncode == 1
    assert json.loads((tmp_path / "access.json").read_text("utf-8"))["owner"] == "8739693469"


def test_force_replaces_owner(tmp_path):
    run("8739693469", "--state-dir", str(tmp_path))
    r = run("123456789", "--state-dir", str(tmp_path), "--force")
    assert r.returncode == 0
    assert json.loads((tmp_path / "access.json").read_text("utf-8"))["owner"] == "123456789"


@pytest.mark.parametrize("bad", ["", "abc", "0123", "12", "1" * 25, "-5", "8739693469; rm -rf /", "1 2"])
def test_rejects_bad_ids(tmp_path, bad):
    r = run(bad, "--state-dir", str(tmp_path))
    assert r.returncode == 2
    assert not (tmp_path / "access.json").exists()


def test_corrupt_existing_file_is_not_silently_owner_match(tmp_path):
    (tmp_path / "access.json").write_text("{broken", "utf-8")
    r = run("8739693469", "--state-dir", str(tmp_path))
    assert r.returncode == 1


def test_build_access_shape():
    assert init_access.build_access("42") == {"allowFrom": ["42"], "owner": "42", "threads": True}
