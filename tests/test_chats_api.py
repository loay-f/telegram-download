import time

from fastapi.testclient import TestClient

from tglib.app.main import create_app
from fake_telegram import VALID_CODE, FakeFile, FakeGateway
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
        chats = client.get("/api/chats").json()["chats"]
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
        client.get("/api/chats")
        client.post("/api/chats/-1001/track", json={"tracked": True})
        del gw.chats[-1001]
        del gw.chats[-1002]
        chats = client.get("/api/chats", params={"refresh": True}).json()["chats"]
        assert [(c["id"], c["is_member"]) for c in chats] == [(-1001, False)]


def test_new_login_refreshes_chat_list(settings):
    gw = FakeGateway()
    gw.add_chat(-1001, "Old account channel")

    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        wait_for_state(client, "ready")
        assert [c["id"] for c in client.get("/api/chats").json()["chats"]] == [-1001]

        # Log out and back in (another device, or another account) with different chats.
        assert client.post("/api/telegram/logout").status_code == 200
        wait_for_state(client, "logged_out")
        gw.add_chat(-1002, "New account channel")
        client.post("/api/telegram/send-code", json={"phone": "+15551234"})
        client.post("/api/telegram/code", json={"code": VALID_CODE})
        wait_for_state(client, "ready")

        # No manual refresh needed: the first list after login comes from Telegram.
        ids = {c["id"] for c in client.get("/api/chats").json()["chats"]}
        assert -1002 in ids
