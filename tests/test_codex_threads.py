"""Подписи кнопок /resume и отбор собственных тредов (codex_threads.py)."""

import ast
from pathlib import Path

from codex_threads import (
    EMPTY_LABEL_TEXT,
    LABEL_LIMIT,
    build_thread_label,
    clean_preview,
    is_own_thread,
    preview_chat_id,
    thread_title,
)

REPO = Path(__file__).resolve().parent.parent
OWNER = "8739693469"
NOW = 1_800_000_000.0
TAG = f'<channel source="telegram" chat_id="{OWNER}" message_id="5" user="strabismic" user_id="{OWNER}" ts="2026-10-25T01:00:00+00:00">'


def thread(preview=None, name=None, updated=NOW - 120, tid="t-1"):
    return {"id": tid, "preview": preview, "name": name, "updatedAt": updated}


# ---------------------------------------------------------------------------
# Очистка превью
# ---------------------------------------------------------------------------


def test_raw_tag_is_stripped():
    assert clean_preview(TAG + "что тут</channel>") == "что тут"


def test_tag_without_closing_part_keeps_text():
    assert clean_preview(TAG + "просто текст") == "просто текст"


def test_truncated_open_tag_gives_empty_text():
    # Превью оборвано внутри открывающего тега: закрывающего «>» нет.
    assert clean_preview('<channel source="telegram" chat_id="873') == ""
    assert build_thread_label(thread('<channel source="telegram" chat_id="873'), NOW) == (
        f"🕐 2 мин назад · {EMPTY_LABEL_TEXT}"
    )


def test_truncated_closing_tag_is_removed():
    assert clean_preview(TAG + "ответ </chan") == "ответ"
    assert clean_preview(TAG + "ответ </") == "ответ"
    assert clean_preview(TAG + "ответ <") == "ответ"


def test_attribute_with_gt_inside_quotes():
    raw = '<channel source="telegram" user="a>b" chat_id="8739693469">текст</channel>'
    assert clean_preview(raw) == "текст"


def test_html_entities_unescaped():
    assert clean_preview(TAG + "A &amp; B &lt;3 &quot;q&quot;</channel>") == 'A & B <3 "q"'


def test_whitespace_collapsed():
    assert clean_preview(TAG + "  много\n\n   пробелов \t здесь  </channel>") == "много пробелов здесь"


def test_emoji_and_unicode_kept():
    assert clean_preview(TAG + "привет 👋 日本語 ё</channel>") == "привет 👋 日本語 ё"


def test_empty_preview_fallback():
    assert clean_preview("") == ""
    assert clean_preview(None) == ""
    assert clean_preview(TAG + "   </channel>") == ""
    assert build_thread_label(thread(TAG + "</channel>"), NOW) == f"🕐 2 мин назад · {EMPTY_LABEL_TEXT}"
    assert build_thread_label(thread(None), NOW) == f"🕐 2 мин назад · {EMPTY_LABEL_TEXT}"


# ---------------------------------------------------------------------------
# Подписи
# ---------------------------------------------------------------------------


def test_label_time_first_and_clean_text():
    label = build_thread_label(thread(TAG + "что тут</channel>"), NOW)
    assert label == "🕐 2 мин назад · что тут"


def test_label_very_long_is_truncated_to_limit():
    long_text = "очень длинный текст " * 20
    label = build_thread_label(thread(TAG + long_text + "</channel>"), NOW)
    assert len(label) == LABEL_LIMIT
    assert label.endswith("…")
    assert label.startswith("🕐 2 мин назад · очень")


def test_label_time_never_cut_off():
    label = build_thread_label(thread(TAG + "x" * 200 + "</channel>", updated=NOW - 3 * 86400), NOW)
    assert label.startswith("🕐 3 дн назад · ")
    assert len(label) <= LABEL_LIMIT


def test_name_preferred_over_preview():
    label = build_thread_label(thread(TAG + "первое</channel>", name="Мой проект"), NOW)
    assert label == "🕐 2 мин назад · Мой проект"
    assert thread_title(thread(TAG + "x</channel>", name="  Имя\n  тут ")) == "Имя тут"


def test_name_is_empty_string_falls_back_to_preview():
    assert thread_title(thread(TAG + "превью</channel>", name="   ")) == "превью"


def test_current_thread_mark():
    label = build_thread_label(thread(TAG + "a</channel>"), NOW, current=True)
    assert label.startswith("✓ 🕐 ")
    assert len(label) <= LABEL_LIMIT + 2


def test_updated_at_missing_uses_now():
    label = build_thread_label({"id": "x", "preview": TAG + "a</channel>"}, NOW)
    assert label == "🕐 только что · a"


# ---------------------------------------------------------------------------
# Отбор: свои / чужие / тестовые
# ---------------------------------------------------------------------------


def test_preview_chat_id():
    assert preview_chat_id(TAG + "x") == OWNER
    assert preview_chat_id('<channel chat_id="111">x</channel>') == "111"
    assert preview_chat_id("просто текст") is None
    assert preview_chat_id(None) is None


def test_own_thread_is_kept():
    assert is_own_thread(thread(TAG + "x</channel>"), OWNER) is True


def test_foreign_thread_is_dropped():
    assert is_own_thread(thread('<channel source="telegram" chat_id="42">x</channel>'), OWNER) is False


def test_smoke_test_thread_is_dropped():
    raw = '<channel source="telegram" chat_id="111" message_id="5" user="smoke">Smoke test</channel>'
    assert is_own_thread(thread(raw), OWNER) is False


def test_cli_thread_without_tag_is_dropped():
    assert is_own_thread(thread("привет из терминала"), OWNER) is False
    assert is_own_thread(thread(None), OWNER) is False


def test_owner_chat_id_matches_only_exact_value():
    # 873 не должно совпасть с 8739693469.
    assert is_own_thread(thread('<channel chat_id="873">x</channel>'), OWNER) is False


def test_unknown_owner_shows_nothing():
    assert is_own_thread(thread(TAG + "x</channel>"), None) is False
    assert is_own_thread(thread(TAG + "x</channel>"), "") is False


# ---------------------------------------------------------------------------
# Смоук-скрипт: тред эфемерный
# ---------------------------------------------------------------------------


def test_smoke_bridge_creates_ephemeral_bridge():
    tree = ast.parse((REPO / "scripts" / "smoke_bridge.py").read_text("utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "CodexBridge"]
    assert len(calls) == 1
    flags = {kw.arg: kw.value for kw in calls[0].keywords}
    assert isinstance(flags.get("ephemeral"), ast.Constant) and flags["ephemeral"].value is True


# --------------------------------------------------------------------------- гости: cwd

from codex_threads import is_own_thread as _own  # noqa: E402


def _thr(chat, cwd):
    return {"id": "t", "preview": f'<channel source="telegram" chat_id="{chat}">hi</channel>', "cwd": cwd}


def test_guest_thread_requires_chat_and_cwd():
    gcwd = "/root/guests-codex/222"
    assert _own(_thr("222", gcwd), "222", cwd=gcwd)
    assert not _own(_thr("222", "/root"), "222", cwd=gcwd)        # чат свой, каталог чужой
    assert not _own(_thr("111", gcwd), "222", cwd=gcwd)           # каталог свой, чат владельца
    assert not _own({"id": "t", "preview": "", "cwd": gcwd}, "222", cwd=gcwd)
    assert not _own(_thr("222", gcwd + "/sub"), "222", cwd=gcwd)


def test_owner_does_not_see_guest_threads():
    assert _own(_thr("111", "/root"), "111")
    assert not _own(_thr("222", "/root/guests-codex/222"), "111")
