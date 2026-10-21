"""Tests for environment-resolution helpers in install.py / server.py.

Some of the functions under test live inside install.py's ``wrapper_code``
triple-quoted template (written verbatim to /usr/local/bin/claude-telegram-bot),
and server.py cannot be imported without starting a bot. So, as in
test_install_fork_session.py, we extract the function sources by regex and exec
them in a throwaway namespace instead of duplicating the logic here.
"""
import glob as glob_mod
import os
import pwd
import re
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL_PY = REPO / "install.py"
SERVER_PY = REPO / "server.py"


def _extract(src: str, name: str, ns: dict) -> object:
    m = re.search(rf"^def {name}\(.*?(?=\n\S|\Z)", src, re.S | re.M)
    assert m, f"{name} not found"
    exec(m.group(0), ns)
    return ns[name]


def _load_install_func(name: str, ns=None):
    return _extract(INSTALL_PY.read_text("utf-8"), name, ns if ns is not None else {})


def _load_wrapper_resolve_claude_bin(home="/home/tester"):
    """resolve_claude_bin as it appears in the generated wrapper script."""
    src = INSTALL_PY.read_text("utf-8")
    m = re.search(r"^def resolve_claude_bin\(.*?(?=\n\ndef )", src, re.S | re.M)
    assert m, "resolve_claude_bin not found in install.py wrapper_code"
    body = m.group(0).replace("##HOME##", home)
    assert "##HOME##" not in body
    # Stub os/shutil rather than monkeypatching the real modules: the exec'd
    # function only needs shutil.which and os.path.exists.
    ns = {
        "shutil": SimpleNamespace(which=lambda _n: None),
        "os": SimpleNamespace(path=SimpleNamespace(exists=lambda _p: False)),
    }
    exec(body, ns)
    return ns["resolve_claude_bin"], ns


# --------------------------------------------------------------------------
# resolve_service_user (FIX 2/3)
# --------------------------------------------------------------------------

resolve_service_user = _load_install_func("resolve_service_user", {"os": os})


def test_resolve_service_user_returns_directory_owner(tmp_path):
    expected = pwd.getpwuid(os.getuid()).pw_name
    assert resolve_service_user(tmp_path) == expected


def test_resolve_service_user_falls_back_to_root_for_missing_path(tmp_path):
    assert resolve_service_user(tmp_path / "definitely-does-not-exist") == "root"


def test_install_py_has_no_os_getlogin():
    """os.getlogin() raises OSError without a controlling terminal, and under
    sudo it names the caller rather than the HOME owner."""
    assert "os.getlogin" not in INSTALL_PY.read_text("utf-8")


# --------------------------------------------------------------------------
# resolve_claude_bin (FIX 4)
# --------------------------------------------------------------------------


def test_resolve_claude_bin_prefers_which():
    fn, ns = _load_wrapper_resolve_claude_bin()
    ns["shutil"].which = lambda _n: "/opt/bin/claude"
    assert fn() == "/opt/bin/claude"


@pytest.mark.parametrize("candidate", [
    "/usr/local/bin/claude",
    "/usr/bin/claude",
    os.path.expanduser("~/.local/bin/claude"),
    "/home/tester/.local/bin/claude",
])
def test_resolve_claude_bin_scans_candidates_when_which_fails(candidate):
    fn, ns = _load_wrapper_resolve_claude_bin(home="/home/tester")
    ns["shutil"].which = lambda _n: None
    ns["os"] = type("os_stub", (), {"path": type("p", (), {
        "exists": staticmethod(lambda p: p == candidate),
        "expanduser": staticmethod(os.path.expanduser)})()})()
    assert fn() == candidate


def test_resolve_claude_bin_bare_name_when_nothing_found():
    fn, ns = _load_wrapper_resolve_claude_bin()
    ns["shutil"].which = lambda _n: None
    ns["os"] = type("os_stub", (), {"path": type("p", (), {
        "exists": staticmethod(lambda _p: False),
        "expanduser": staticmethod(os.path.expanduser)})()})()
    assert fn() == "claude"


def test_wrapper_imports_shutil():
    src = INSTALL_PY.read_text("utf-8")
    wrapper = re.search(r'WRAPPER_TEMPLATE = r"""(.*?)"""', src, re.S)
    assert wrapper, "WRAPPER_TEMPLATE literal not found"
    assert re.search(r"^import shutil$", wrapper.group(1), re.M)


def test_no_hardcoded_usr_bin_claude_outside_resolvers():
    """A bare /usr/bin/claude may only appear inside a resolver's candidate list."""
    for path in (INSTALL_PY, SERVER_PY):
        for i, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if "/usr/bin/claude" not in line:
                continue
            assert "cand" in line or "/usr/local/bin/claude" in line, \
                f"{path.name}:{i} hardcodes /usr/bin/claude"


# --------------------------------------------------------------------------
# write_secret (FIX 5)
# --------------------------------------------------------------------------

write_secret = _extract(SERVER_PY.read_text("utf-8"), "write_secret",
                        {"os": os, "Path": Path})


def test_write_secret_creates_file_with_0600(tmp_path):
    target = tmp_path / ".credentials.osnova.json"
    write_secret(target, '{"token": "x"}')
    assert target.read_text("utf-8") == '{"token": "x"}'
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_write_secret_tightens_existing_loose_file(tmp_path):
    target = tmp_path / ".claude.json"
    target.write_text("old", "utf-8")
    os.chmod(target, 0o644)
    write_secret(target, "new")
    assert target.read_text("utf-8") == "new"
    assert oct(target.stat().st_mode & 0o777) == "0o600"


# --------------------------------------------------------------------------
# harden_secret_permissions — backfill of pre-existing 0644 secrets (FIX 5)
# --------------------------------------------------------------------------


def _load_harden(home: Path):
    """Extract the backfill with USER_HOME pointed at a throwaway home."""
    return _extract(SERVER_PY.read_text("utf-8"), "harden_secret_permissions",
                    {"os": os, "glob": glob_mod, "stat": stat, "Path": Path,
                     "USER_HOME": home})


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _seed_home(tmp_path: Path) -> Path:
    (tmp_path / ".claude").mkdir()
    return tmp_path


def test_backfill_tightens_loose_secrets(tmp_path):
    home = _seed_home(tmp_path)
    active = home / ".claude" / ".credentials.json"
    profile = home / ".claude" / ".credentials.osnova.json"
    claude_json = home / ".claude.osnova.json"
    for f in (active, profile, claude_json):
        f.write_text("{}", "utf-8")
        os.chmod(f, 0o644)

    _load_harden(home)()

    assert _mode(active) == 0o600
    assert _mode(profile) == 0o600
    assert _mode(claude_json) == 0o600


def test_backfill_leaves_already_private_file_intact(tmp_path):
    home = _seed_home(tmp_path)
    tight = home / ".claude" / ".credentials.json"
    tight.write_text("payload", "utf-8")
    os.chmod(tight, 0o600)

    _load_harden(home)()

    assert _mode(tight) == 0o600
    assert tight.read_text("utf-8") == "payload"


def test_backfill_ignores_unrelated_files(tmp_path):
    home = _seed_home(tmp_path)
    other = home / ".claude" / "settings.json"
    other.write_text("{}", "utf-8")
    os.chmod(other, 0o644)

    _load_harden(home)()

    assert _mode(other) == 0o644


def test_backfill_is_silent_on_empty_home(tmp_path):
    _load_harden(tmp_path / "no-such-home")()  # must not raise


def test_backfill_runs_at_startup():
    src = SERVER_PY.read_text("utf-8")
    start = src.index("async def main() -> None:")
    assert "harden_secret_permissions()" in src[start:start + 400]


# --------------------------------------------------------------------------
# USER_HOME (FIX 6)
# --------------------------------------------------------------------------


def test_server_py_has_no_hardcoded_root_paths():
    """All account/token paths must derive from USER_HOME, otherwise a non-root
    install writes profiles where /accounts never looks."""
    offenders = [
        f"{i}: {line.strip()}"
        for i, line in enumerate(SERVER_PY.read_text("utf-8").splitlines(), 1)
        if '"/root' in line or "'/root" in line
    ]
    assert not offenders, offenders


def test_server_py_defines_user_home_with_env_override():
    src = SERVER_PY.read_text("utf-8")
    assert re.search(
        r'^USER_HOME = Path\(os\.environ\.get\("CLAUDE_GRAM_HOME"\) or Path\.home\(\)\)$',
        src, re.M)


def test_list_profiles_glob_uses_user_home():
    """/accounts and /login must agree on where profiles live."""
    src = SERVER_PY.read_text("utf-8")
    assert 'glob.glob(str(USER_HOME / ".claude" / ".credentials.*.json"))' in src


# --------------------------------------------------------------------------
# claude-login user (FIX 1)
# --------------------------------------------------------------------------


def test_installer_creates_claude_login_user():
    src = INSTALL_PY.read_text("utf-8")
    assert "def ensure_login_user()" in src
    assert '"useradd", "-m", "-s", "/bin/bash", "claude-login"' in src
    assert re.search(r"^        ensure_login_user\(\)$", src, re.M), \
        "ensure_login_user() must be called from the Linux branch"


def test_cmd_login_guards_missing_user_and_non_root():
    src = SERVER_PY.read_text("utf-8")
    start = src.index("async def cmd_login(")
    body = src[start:src.index("\n@dp.message(", start)]
    assert 'pwd.getpwnam("claude-login")' in body
    assert "os.geteuid() != 0" in body
    assert "os.path.exists(resolve_claude_bin())" in body
