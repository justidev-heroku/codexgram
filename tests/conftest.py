"""Изоляция тестов от боевого состояния бота.

server.py при импорте читает ~/.claude/channels/telegram (.env, access.json), а
load_access() умеет мигрировать access.json на диске. Чтобы тесты никогда не
трогали реальные файлы, до импорта server подменяем каталог состояния на
временный и подставляем фиктивный токен.
"""
import os
import tempfile

_TMP_STATE = tempfile.mkdtemp(prefix="claude-gram-test-state-")
os.environ["TELEGRAM_STATE_DIR"] = _TMP_STATE
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:TEST_TOKEN_not_real_aaaaaaaaaaaaaaaaaa")
