"""Owner + guests access model: migration, /share /unshare /access, gating."""
import asyncio
import datetime
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402
from aiogram.enums import ChatType  # noqa: E402

OWNER = "111111111"
GUEST = "222222222"
STRANGER = "333333333"


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "ACCESS_FILE", tmp_path / "access.json")
    monkeypatch.setattr(server, "KNOWN_USERS_FILE", tmp_path / "known_users.json")
    monkeypatch.setattr(server, "_known_users_cache", None)
    monkeypatch.setattr(server, "session_thread_id", None)
    return tmp_path


def write_access(state, data):
    (state / "access.json").write_bytes(orjson.dumps(data))


def read_access(state):
    return orjson.loads((state / "access.json").read_bytes())


def user(uid, username=None, first="Name", last=None):
    return SimpleNamespace(id=int(uid), username=username, first_name=first, last_name=last, is_bot=False)


def make_msg(uid, text="hi", username=None, first="Name"):
    msg = MagicMock()
    msg.from_user = user(uid, username, first)
    msg.chat.id = int(uid)
    msg.chat.type = ChatType.PRIVATE
    msg.text = text
    msg.message_id = 42
    msg.message_thread_id = None
    msg.reply_to_message = None
    msg.date = datetime.datetime(2026, 10, 14, tzinfo=datetime.timezone.utc)
    msg.answer = AsyncMock()
    return msg


# --------------------------------------------------------------------------- migration


def test_migration_sets_owner_from_first_allowfrom(state):
    write_access(state, {"dmPolicy": "allowlist", "allowFrom": [OWNER]})
    a = server.load_access()
    assert a["owner"] == OWNER
    assert read_access(state)["owner"] == OWNER  # persisted
    assert read_access(state)["dmPolicy"] == "allowlist"  # other keys kept


def test_migration_keeps_owner_first_and_coerces_ints(state):
    write_access(state, {"owner": GUEST, "allowFrom": [int(OWNER), int(GUEST)]})
    a = server.load_access()
    assert a["owner"] == GUEST
    assert a["allowFrom"] == [GUEST, OWNER]


def test_migration_owner_not_in_allowfrom_means_owner_changed(state):
    # allowFrom переписан вручную (/telegram:init сменил владельца)
    write_access(state, {"owner": OWNER, "allowFrom": [STRANGER]})
    a = server.load_access()
    assert a["owner"] == STRANGER
    assert a["allowFrom"] == [STRANGER]


def test_migration_empty_allowfrom_drops_owner(state):
    write_access(state, {"owner": OWNER, "allowFrom": []})
    a = server.load_access()
    assert "owner" not in a
    assert server.owner_id(a) is None


def test_migration_noop_does_not_rewrite(state):
    write_access(state, {"owner": OWNER, "allowFrom": [OWNER]})
    mtime = (state / "access.json").stat().st_mtime_ns
    server.load_access()
    assert (state / "access.json").stat().st_mtime_ns == mtime


def test_owner_and_guest_predicates(state):
    write_access(state, {"allowFrom": [OWNER, GUEST]})
    a = server.load_access()
    assert server.owner_id(a) == OWNER
    assert server.is_owner(OWNER, a) and not server.is_owner(GUEST, a)
    assert server.is_guest(GUEST, a) and not server.is_guest(OWNER, a)
    assert not server.is_guest(STRANGER, a)


# --------------------------------------------------------------------------- known users / resolve


def test_remember_and_resolve_username(state):
    server.remember_user(user(GUEST, "SomeGuy", "Some", "Guy"))
    assert server.resolve_user_ref("@someguy") == (GUEST, "Some Guy")
    assert server.resolve_user_ref("SomeGuy")[0] == GUEST
    assert server.resolve_user_ref("@unknown_user") == (None, "")
    # persisted
    assert orjson.loads((state / "known_users.json").read_bytes())[GUEST]["username"] == "someguy"


def test_username_moves_to_new_owner(state):
    server.remember_user(user(GUEST, "nick"))
    server.remember_user(user(STRANGER, "nick"))
    assert server.resolve_user_ref("@nick")[0] == STRANGER


def test_resolve_numeric_id_without_knowledge(state):
    assert server.resolve_user_ref("444555666") == ("444555666", "")
    assert server.resolve_user_ref("not valid!") == (None, "")


# --------------------------------------------------------------------------- share / unshare / access


def test_share_by_id(state):
    write_access(state, {"allowFrom": [OWNER]})
    ok, text = server.share_access(GUEST)
    assert ok and GUEST in text
    a = read_access(state)
    assert a["allowFrom"] == [OWNER, GUEST]
    assert a["guests"][GUEST]["added"]


def test_share_by_known_username(state):
    write_access(state, {"allowFrom": [OWNER]})
    server.remember_user(user(GUEST, "friend", "Friend"))
    ok, _ = server.share_access("@friend")
    assert ok
    a = read_access(state)
    assert GUEST in a["allowFrom"]
    assert a["guests"][GUEST]["username"] == "friend"
    assert a["guests"][GUEST]["name"] == "Friend"


def test_share_unknown_username_asks_for_id(state):
    write_access(state, {"allowFrom": [OWNER]})
    ok, text = server.share_access("@nobody_here")
    assert not ok and "ID" in text
    assert read_access(state)["allowFrom"] == [OWNER]


def test_share_owner_and_duplicate_refused(state):
    write_access(state, {"allowFrom": [OWNER, GUEST]})
    assert server.share_access(OWNER)[0] is False
    assert server.share_access(GUEST)[0] is False
    assert server.share_access("")[0] is False
    assert server.load_access()["allowFrom"] == [OWNER, GUEST]


def test_unshare_guest(state):
    write_access(state, {"allowFrom": [OWNER]})
    server.share_access(GUEST)
    ok, _ = server.unshare_access(GUEST)
    assert ok
    a = read_access(state)
    assert a["allowFrom"] == [OWNER]
    assert GUEST not in a.get("guests", {})


def test_unshare_by_username_from_guests_record(state):
    write_access(state, {"allowFrom": [OWNER, GUEST], "guests": {GUEST: {"username": "pal", "added": ""}}})
    ok, _ = server.unshare_access("@pal")  # known_users пуст — берём из guests
    assert ok
    assert server.load_access()["allowFrom"] == [OWNER]


def test_unshare_owner_refused(state):
    write_access(state, {"allowFrom": [OWNER, GUEST]})
    ok, text = server.unshare_access(OWNER)
    assert not ok and "Владельца" in text
    assert OWNER in server.load_access()["allowFrom"]


def test_unshare_unknown(state):
    write_access(state, {"allowFrom": [OWNER]})
    assert server.unshare_access(STRANGER)[0] is False


def test_access_list(state):
    write_access(state, {"allowFrom": [OWNER]})
    server.remember_user(user(OWNER, "boss", "Boss"))
    server.remember_user(user(GUEST, "pal", "Pal"))
    server.share_access(GUEST)
    text = server.access_list_text()
    assert OWNER in text and "владелец" in text
    assert GUEST in text and "Pal" in text and "@pal" in text
    assert "выдан" in text


# --------------------------------------------------------------------------- gating


def test_dm_command_gate_owner_guest_stranger(state):
    write_access(state, {"allowFrom": [OWNER, GUEST]})

    async def run():
        owner_msg, guest_msg, stranger_msg = make_msg(OWNER), make_msg(GUEST), make_msg(STRANGER)
        assert server.dm_command_gate(owner_msg)["senderId"] == OWNER
        assert server.dm_command_gate(guest_msg) is None
        assert server.dm_command_gate(stranger_msg) is None
        await asyncio.sleep(0)
        guest_msg.answer.assert_not_awaited()  # гости обслуживаются своим экземпляром
        stranger_msg.answer.assert_not_awaited()
        owner_msg.answer.assert_not_awaited()

    asyncio.run(run())




def test_owner_share_command_works(state):
    write_access(state, {"allowFrom": [OWNER]})

    async def run():
        msg = make_msg(OWNER, f"/share {GUEST}")
        await server.cmd_share(msg)
        assert "Доступ выдан" in msg.answer.await_args.args[0]

    asyncio.run(run())
    assert GUEST in read_access(state)["allowFrom"]


@pytest.mark.parametrize("handler", ["on_model_callback", "on_model_add", "on_restart_confirm",
                                     "on_effort_callback", "on_delete_button", "on_goal_clear",
                                     "on_restart_cancel", "on_close_button"])
def test_guest_denied_callbacks(state, handler, monkeypatch):
    write_access(state, {"allowFrom": [OWNER, GUEST]})
    restart = MagicMock()
    monkeypatch.setattr(server, "safe_restart", restart)
    cb = MagicMock()
    cb.from_user = user(GUEST)
    cb.data = "model:sonnet"
    cb.answer = AsyncMock()
    asyncio.run(getattr(server, handler)(cb))
    cb.answer.assert_awaited_once_with("Нет доступа.")
    restart.assert_not_called()


def test_guest_denied_permission_callback(state):
    write_access(state, {"allowFrom": [OWNER, GUEST]})
    handler = server.on_permission_button
    cb = MagicMock()
    cb.from_user = user(GUEST)
    cb.data = "perm:allow:abcde"
    cb.answer = AsyncMock()
    notify = AsyncMock()
    server_notify = server.notify
    server.notify = notify
    try:
        asyncio.run(handler(cb))
    finally:
        server.notify = server_notify
    cb.answer.assert_awaited_once_with("Нет доступа.")
    notify.assert_not_awaited()


# --------------------------------------------------------------------------- inbound


@pytest.fixture
def inbound(monkeypatch):
    deliver = AsyncMock()
    notify = AsyncMock()
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.set_message_reaction = AsyncMock()
    monkeypatch.setattr(server, "deliver", deliver)
    monkeypatch.setattr(server, "notify", notify)
    monkeypatch.setattr(server, "start_thinking", AsyncMock())
    monkeypatch.setattr(server, "bot", bot)
    return SimpleNamespace(deliver=deliver, notify=notify, bot=bot)




def test_owner_message_has_no_guest_meta(state, inbound):
    write_access(state, {"allowFrom": [OWNER, GUEST]})
    msg = make_msg(OWNER, "hi")
    asyncio.run(server.handle_inbound(msg, "hi"))
    inbound.deliver.assert_awaited_once()
    meta = inbound.deliver.await_args.args[1]
    assert "role" not in meta and "guest" not in meta
    inbound.bot.send_message.assert_not_awaited()


def test_stranger_message_dropped(state, inbound):
    write_access(state, {"allowFrom": [OWNER, GUEST]})
    msg = make_msg(STRANGER, "let me in")
    asyncio.run(server.handle_inbound(msg, "let me in"))
    inbound.deliver.assert_not_awaited()
    inbound.bot.send_message.assert_not_awaited()


def test_unshared_guest_is_dropped(state, inbound):
    write_access(state, {"allowFrom": [OWNER]})
    server.share_access(GUEST)
    server.unshare_access(GUEST)
    asyncio.run(server.handle_inbound(make_msg(GUEST, "hi"), "hi"))
    inbound.deliver.assert_not_awaited()






def test_thread_kwargs_only_for_owner_chat(state, monkeypatch):
    write_access(state, {"allowFrom": [OWNER, GUEST]})
    monkeypatch.setattr(server, "session_thread_id", 77)
    assert server.thread_kwargs(OWNER) == {"message_thread_id": 77}
    assert server.thread_kwargs(GUEST) == {}
    assert server.thread_kwargs() == {"message_thread_id": 77}


def test_remember_middleware_records_strangers(state):
    async def handler(event, data):
        return "ok"

    ev = SimpleNamespace(from_user=user(STRANGER, "newbie", "New"))
    assert asyncio.run(server._remember_users_mw(handler, ev, {})) == "ok"
    assert server.resolve_user_ref("@newbie")[0] == STRANGER
