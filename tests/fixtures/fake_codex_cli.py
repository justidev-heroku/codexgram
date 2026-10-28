#!/usr/bin/env python3
"""Фейковый `codex` CLI для тестов login/logout (настоящий codex из тестов не запускается).

login --device-auth: печатает URL и код, пишет $CODEX_HOME/auth.json.
logout:              удаляет $CODEX_HOME/auth.json.
Каждый вызов дописывает строку {argv, CODEX_HOME} в $FAKE_CODEX_LOG.
FAKE_CODEX_RC задаёт код выхода; FAKE_CODEX_NO_AUTH=1 — login без auth.json.
"""
import json
import os
import sys

home = os.environ.get("CODEX_HOME") or ""
log = os.environ.get("FAKE_CODEX_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "CODEX_HOME": home}) + "\n")
rc = int(os.environ.get("FAKE_CODEX_RC") or 0)
args = sys.argv[1:]
if args[:1] == ["login"]:
    print("Open https://auth.openai.com/codex/device and enter ABCD-EFG1", flush=True)
    if rc == 0 and os.environ.get("FAKE_CODEX_NO_AUTH") != "1":
        os.makedirs(home, exist_ok=True)
        with open(os.path.join(home, "auth.json"), "w", encoding="utf-8") as f:
            f.write('{"who": "guest-login"}')
elif args[:1] == ["logout"] and rc == 0:
    try:
        os.unlink(os.path.join(home, "auth.json"))
    except FileNotFoundError:
        pass
    print("Successfully logged out")
sys.exit(rc)
