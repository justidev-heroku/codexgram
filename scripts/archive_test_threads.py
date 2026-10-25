"""Одноразовая чистка: архивирует тестовые треды (chat_id="111") из истории codex app-server.

Поднимает свежий `codex app-server` (через CodexBridge с временным state dir и ephemeral-тредом,
сам по себе ничего не сохраняет), листает thread/list и архивирует треды, у которых в превью
есть `chat_id="111"` (след смоук-прогонов и прототипа).

Использование:
    python3 scripts/archive_test_threads.py            # то же, что --dry-run
    python3 scripts/archive_test_threads.py --dry-run  # только показать список
    python3 scripts/archive_test_threads.py --apply    # архивировать (thread/archive)
"""

import argparse
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from codex_bridge import CodexBridge  # noqa: E402
from codex_threads import clean_preview  # noqa: E402

TEST_MARKER = 'chat_id="111"'
PAGE_SIZE = 100


async def list_all_threads(bridge: CodexBridge) -> list[dict]:
    threads: list[dict] = []
    cursor = None
    while True:
        params = {"archived": False, "limit": PAGE_SIZE, "sortKey": "updated_at", "sortDirection": "desc"}
        if cursor:
            params["cursor"] = cursor
        result = await bridge._request("thread/list", params)
        threads.extend(t for t in result.get("data") or [] if isinstance(t, dict) and t.get("id"))
        cursor = result.get("nextCursor")
        if not cursor:
            return threads


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только показать список (по умолчанию)")
    mode.add_argument("--apply", action="store_true", help="архивировать найденные треды")
    args = ap.parse_args()
    apply = args.apply
    print("mode:", "APPLY" if apply else "dry-run", flush=True)

    base = Path(tempfile.mkdtemp(prefix="codex-gram-archive-"))
    bridge = CodexBridge(
        state_dir=base / "state",
        cwd=str(base),
        log=lambda m: print(f"[bridge] {m}", flush=True),
        ephemeral=True,
    )
    try:
        if not await bridge.start(wait_ready=True, timeout=120):
            print("app-server не поднялся", file=sys.stderr)
            return 2
        threads = await list_all_threads(bridge)
        print(f"threads listed: {len(threads)}")
        targets = [t for t in threads if TEST_MARKER in str(t.get("preview") or "")]
        print(f"matching {TEST_MARKER}: {len(targets)}")
        for t in targets:
            preview = clean_preview(t.get("preview"))[:60] or "(пусто)"
            print(f"  {t['id']}  updatedAt={t.get('updatedAt')}  name={t.get('name')!r}  preview={preview!r}")
            if apply:
                try:
                    await bridge.archive_thread(t["id"])
                    print("    archived")
                except Exception as e:  # noqa: BLE001
                    print(f"    archive failed: {e}", file=sys.stderr)
        if not apply:
            print("dry-run: ничего не изменено (запусти с --apply, чтобы архивировать)")
        return 0
    finally:
        await bridge.stop()
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
