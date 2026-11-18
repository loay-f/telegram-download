import json
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from tglib.api.status import sse
from tglib.app.main import create_app
from fake_telegram import FakeFile, FakeGateway
from helpers import sync_chats
from test_auth import login, wait_for_state
from test_chats_api import wait_until

PDF = b"%PDF-1.4 fake pdf body"


def build_gateway() -> FakeGateway:
    gw = FakeGateway()
    gw.add_chat(-1001, "مكتبة التراث")
    gw.add_chat(-1002, "قناة التاريخ")
    gw.post(-1001, 1, FakeFile(1, "تفسير الطبري.pdf", PDF), caption="الطبعة الأولى من دار المعارف")
    gw.post(-1002, 5, FakeFile(2, "نسخة أخرى.pdf", PDF))  # same content, other name
    gw.post(-1001, 2, FakeFile(3, "Muqaddimah.epub", b"epub-bytes", mime="application/epub+zip"))
    gw.post(-1001, 3, FakeFile(4, "page.html", b"<script>alert(1)</script>", mime="text/html"))
    gw.post(-1001, 4, FakeFile(5, "photo.jpg", b"jpg", mime="image/jpeg"))
    return gw


@pytest.fixture
def client(settings):
    gw = build_gateway()
    with TestClient(create_app(settings, lambda: gw)) as c:
        login(c)
        wait_for_state(c, "ready")
        c.post("/api/settings", json={"allowed_exts": "pdf,epub,html"})
        sync_chats(c)
        for chat in (-1001, -1002):
            c.post(f"/api/chats/{chat}/settings", json={"auto_download": True})
            c.post(f"/api/chats/{chat}/track", json={"tracked": True})

        def settled():
            st = c.get("/api/stats").json()["status_counts"]
            return st["done"] + st["duplicate"] == 4 and st["pending"] == st["downloading"] == 0

        wait_until(settled, timeout=10)
        yield c


def test_library_groups_duplicates_and_lists_appearances(client):
    lib = client.get("/api/library").json()
    assert lib["total"] == 3
    tafsir = next(e for i in lib["items"] for e in i["entries"] if e["copies"] == 2)
    assert {a["chat_title"] for a in tafsir["appearances"]} == {"مكتبة التراث", "قناة التاريخ"}
    assert tafsir["other_names"] and tafsir["has_file"]


@pytest.mark.parametrize(
    "q",
    ["الطبري", "تفسير الطبرى", "دار المعارف", "الطبعه الاولي", "MUQADDIMAH"],
)
def test_library_search_name_and_caption_with_arabic_variants(client, q):
    items = client.get("/api/library", params={"q": q}).json()["items"]
    assert len(items) == 1


def test_library_filters(client):
    assert client.get("/api/library", params={"ext": "epub"}).json()["total"] == 1
    assert client.get("/api/library", params={"chat_id": -1002}).json()["total"] == 1
    assert client.get("/api/library", params={"date_from": "2030-01-01"}).json()["total"] == 0
    f = client.get("/api/library/filters").json()
    assert {e["ext"] for e in f["exts"]} == {"pdf", "epub", "html"}


def test_file_download_arabic_name(client):
    item = client.get("/api/library", params={"q": "الطبري"}).json()["items"][0]["entries"][0]
    r = client.get(f"/api/files/{item['id']}")
    assert r.status_code == 200 and r.content == PDF
    cd = r.headers["content-disposition"]
    assert cd.startswith("inline") and "utf-8''" in cd
    assert unquote(cd.split("utf-8''")[1]) == item["name"]
    r = client.get(f"/api/files/{item['id']}", params={"download": True})
    assert r.headers["content-disposition"].startswith("attachment")


def test_html_file_is_never_served_inline(client):
    item = client.get("/api/library", params={"q": "page"}).json()["items"][0]["entries"][0]
    r = client.get(f"/api/files/{item['id']}")
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_stats(client):
    st = client.get("/api/stats").json()
    assert st["files"] == 3 and st["duplicates"] == 1
    assert st["space_saved_bytes"] == len(PDF)
    assert st["status_counts"]["skipped"] == 1  # the jpg


def test_queue_and_actions(client):
    q = client.get("/api/documents", params={"status": "skipped"}).json()
    assert [i["name"] for i in q["items"]] == ["photo.jpg"]
    doc_id = q["items"][0]["id"]
    assert client.post(f"/api/documents/{doc_id}/retry").status_code == 200

    def done():
        items = client.get("/api/documents", params={"status": ["done"]}).json()["items"]
        return any(i["id"] == doc_id for i in items)

    wait_until(done)
    assert client.post(f"/api/documents/{doc_id}/cancel").status_code == 409
    assert client.post("/api/documents/999999/priority").status_code == 404


def test_settings_change_reopens_filtered_documents(client):
    r = client.post("/api/settings", json={"allowed_exts": "pdf,epub,html,jpg", "workers": 2})
    body = r.json()
    assert body["reopened"] == 1 and body["workers"] == 2
    assert "jpg" in body["allowed_exts"] and body["storage"]["free"] > 0
    assert client.get("/api/settings").json()["workers"] == 2


def test_engine_pause_resume(client):
    assert client.post("/api/engine/pause").json()["state"] == "paused"
    assert client.post("/api/engine/resume").json()["state"] in ("idle", "running")


def test_sse_format():
    out = sse("status", {"name": "كتاب", "n": 1})
    assert out.startswith("event: status\ndata: ") and out.endswith("\n\n")
    assert json.loads(out.split("data: ", 1)[1]) == {"name": "كتاب", "n": 1}


def test_events_endpoint_requires_auth(settings):
    with TestClient(create_app(settings, lambda: FakeGateway())) as c:
        assert c.get("/api/events").status_code == 401
