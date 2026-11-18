import time

from fastapi.testclient import TestClient

from tglib.app.main import create_app
from fake_telegram import VALID_CODE, FakeFile, FakeGateway
from helpers import sync_chats
from test_auth import login, wait_for_state


def wait_until(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition not met")


def test_list_track_and_auto_scan(settings):
    gw = FakeGateway()
    gw.add_chat(-1001, "مكتبة التراث")
    gw.add_chat(-1002, "Other channel")
    gw.add_chat(-1003, "مجموعة", "group")
    gw.add_chat(42, "A person", "user")
    gw.post(-1001, 1, FakeFile(1, "a.pdf", b"a"))
    gw.post(-1001, 2, FakeFile(2, "b.pdf", b"b"))

    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        wait_for_state(client, "ready")
        chats = sync_chats(client)
        assert {c["id"]: c["type"] for c in chats} == {
            -1001: "channel", -1002: "channel", -1003: "group", 42: "user",
        }

        # Search folds Arabic letter variants (ة ↔ ه).
        found = client.get("/api/chats", params={"q": "التراث"}).json()["chats"]
        assert [c["id"] for c in found] == [-1001]

        assert client.post("/api/chats/-1001/track", json={"tracked": True}).status_code == 200

        def scanned():
            c = next(c for c in client.get("/api/chats").json()["chats"] if c["id"] == -1001)
            return c if c["last_scan_at"] and c["files"] == 2 else None

        chat = wait_until(scanned)
        # Tracking only scans: files are listed, nothing is downloaded.
        assert chat["tracked"] is True and chat["available"] == 2 and chat["downloaded"] == 0
        assert gw.downloads_started == []
        # Tracked chats sort first.
        assert client.get("/api/chats").json()["chats"][0]["id"] == -1001

        r = client.post("/api/chats/-1002/scan")
        assert r.status_code == 400 and r.json()["detail"]["code"] == "not_tracked"
        assert client.post("/api/chats/scan-all").status_code == 200

        r = client.post("/api/chats/-1001/settings", json={"allowed_exts": ".PDF, epub", "max_size_mb": 5})
        assert r.status_code == 200
        chat = next(c for c in client.get("/api/chats").json()["chats"] if c["id"] == -1001)
        assert chat["allowed_exts"] == "epub,pdf" and chat["max_size_mb"] == 5


def test_left_chats_hidden_unless_tracked(settings):
    gw = FakeGateway()
    gw.add_chat(-1001, "A")
    gw.add_chat(-1002, "B")
    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        wait_for_state(client, "ready")
        sync_chats(client)
        client.post("/api/chats/-1001/track", json={"tracked": True})
        del gw.chats[-1001]
        del gw.chats[-1002]
        chats = sync_chats(client)
        assert [(c["id"], c["is_member"]) for c in chats] == [(-1001, False)]


def test_list_answers_from_db_while_syncing_in_background(settings):
    gw = FakeGateway()
    for i in range(250):
        gw.add_chat(-1000 - i, f"Channel {i}")
    gw.dialogs_pause_after = 150  # Telegram is slow after the first requests

    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        wait_for_state(client, "ready")
        try:
            # The first request doesn't wait for Telegram.
            res = client.get("/api/chats").json()
            assert res["dialogs"]["syncing"] is True

            # Full batches are saved and listed as they arrive.
            def first_batch():
                r = client.get("/api/chats").json()
                return r if r["dialogs"]["loaded"] == 100 else None

            r = wait_until(first_batch)
            assert len(r["chats"]) == 100 and r["dialogs"]["syncing"] is True
            # Page loads while a sync runs don't start another one.
            assert gw.dialog_calls == 1
        finally:
            gw.dialogs_resume.set()

        def done():
            r = client.get("/api/chats").json()
            return r if not r["dialogs"]["syncing"] else None

        r = wait_until(done)
        assert len(r["chats"]) == 250
        assert r["dialogs"] == {"syncing": False, "synced": True, "loaded": 250, "error": None}
        # Fresh: further page loads don't ask Telegram again.
        client.get("/api/chats")
        assert gw.dialog_calls == 1


def test_failed_sync_is_reported_and_not_retried_on_every_load(settings):
    gw = FakeGateway()
    gw.add_chat(-1001, "A")
    gw.flood_on_next["dialogs"] = 900

    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        wait_for_state(client, "ready")
        res = sync_chats(client)
        assert res == []
        status = client.get("/api/chats").json()["dialogs"]
        assert status["syncing"] is False and "900" in status["error"]
        # Page loads (which happen on every live update) back off...
        for _ in range(3):
            client.get("/api/chats")
        assert gw.dialog_calls == 1
        # ...but the refresh button retries right away.
        assert [c["id"] for c in sync_chats(client)] == [-1001]
        assert client.get("/api/chats").json()["dialogs"]["error"] is None


def test_new_login_refreshes_chat_list(settings):
    gw = FakeGateway()
    gw.add_chat(-1001, "Old account channel")
    gw.add_chat(-1003, "Old account, tracked")

    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        wait_for_state(client, "ready")
        assert {c["id"] for c in sync_chats(client)} == {-1001, -1003}
        client.post("/api/chats/-1003/track", json={"tracked": True})

        # Log out and back in (another device, or another account) with different chats.
        assert client.post("/api/telegram/logout").status_code == 200
        wait_for_state(client, "logged_out")
        # The old account's chats aren't listed any more, except tracked ones.
        assert [(c["id"], c["is_member"]) for c in client.get("/api/chats").json()["chats"]] == [
            (-1003, False)
        ]
        del gw.chats[-1001], gw.chats[-1003]
        gw.add_chat(-1002, "New account channel")
        client.post("/api/telegram/send-code", json={"phone": "+15551234"})
        client.post("/api/telegram/code", json={"code": VALID_CODE})
        wait_for_state(client, "ready")

        # No manual refresh needed: the first page load after login syncs from Telegram.
        client.get("/api/chats")
        wait_until(lambda: -1002 in {c["id"] for c in client.get("/api/chats").json()["chats"]})


def test_revoked_session_goes_back_to_login(settings):
    from tglib.engine.errors import NotAuthorized

    gw = FakeGateway()
    gw.add_chat(-1001, "Channel")

    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        wait_for_state(client, "ready")

        # Session terminated from another device: Telegram rejects the key.
        async def revoked():
            raise NotAuthorized("The key is not registered in the system")
            yield

        gw.iter_dialogs = revoked
        assert client.get("/api/chats", params={"refresh": True}).status_code == 200
        assert wait_for_state(client, "logged_out")["state"] == "logged_out"
