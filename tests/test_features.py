"""Catalog-only tracking, picking files, volumes, deletion, thumbnails,
search, links, schedule, speed limit and the daily summary."""

import asyncio
import sqlite3
import time
from datetime import datetime, timezone

import pymupdf
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select

from tglib.app.main import create_app
from tglib.db.migrate import alembic_config
from tglib.db.models import Blob, Document, DocStatus
from tglib.engine import downloader
from tglib.engine.downloader import RateLimiter
from tglib.engine.settings_store import AppSettings
from tglib.engine.summary import compose
from fake_telegram import FakeFile, FakeGateway
from helpers import all_docs, running_container, sync_chats, track
from test_auth import login, wait_for_state
from test_chats_api import wait_until


def pdf_bytes(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def gateway() -> FakeGateway:
    gw = FakeGateway()
    gw.add_chat(-1001, "مكتبة التراث", username="turath_lib")
    gw.add_chat(-1002, "قناة أخرى")
    gw.post(-1001, 1, FakeFile(1, "تفسير الطبري ج1.pdf", pdf_bytes("vol 1")))
    gw.post(-1001, 2, FakeFile(2, "تفسير الطبري - الجزء الثاني.pdf", pdf_bytes("vol 2")))
    gw.post(-1001, 3, FakeFile(3, "تفسير_الطبري_الجزء_الثالث.pdf", pdf_bytes("vol 3")))
    gw.post(-1001, 4, FakeFile(4, "مقدمة ابن خلدون.epub", b"not really an epub"))
    gw.post(-1002, 9, FakeFile(9, "كتاب نادر.pdf", pdf_bytes("rare")), caption="نسخة نادرة")
    return gw


@pytest.fixture
def app(settings):
    gw = gateway()
    with TestClient(create_app(settings, lambda: gw)) as c:
        login(c)
        wait_for_state(c, "ready")
        sync_chats(c)
        c.post("/api/chats/-1001/track", json={"tracked": True})

        def scanned():
            chat = next(x for x in c.get("/api/chats").json()["chats"] if x["id"] == -1001)
            return chat["last_scan_at"] and chat["available"] == 4

        wait_until(scanned)
        yield c, gw


def statuses(c) -> dict[str, int]:
    return c.get("/api/stats").json()["status_counts"]


def wait_downloaded(c, n: int) -> None:
    wait_until(lambda: statuses(c)["done"] + statuses(c)["duplicate"] >= n and statuses(c)["pending"] == 0)


# ------------------------------------------------- scan only, then pick
def test_tracking_only_scans_and_lists_files(app):
    c, gw = app
    assert gw.downloads_started == []
    assert c.get("/api/library").json()["total"] == 0
    available = c.get("/api/library", params={"view": "available"}).json()
    # Three volumes form one work; the epub is its own.
    assert available["total"] == 2
    series = next(w for w in available["items"] if w["series"])
    assert [e["volume"] for e in series["entries"]] == [1, 2, 3]
    assert all(e["status"] == "available" for e in series["entries"])


def test_pick_one_file_then_whole_chat(app):
    c, gw = app
    work = next(w for w in c.get("/api/library", params={"view": "available"}).json()["items"] if not w["series"])
    r = c.post("/api/documents/download", json={"ids": [work["entries"][0]["id"]], "to_front": True})
    assert r.json() == {"queued": 1, "already": 0}
    wait_downloaded(c, 1)
    assert [tg for tg, _ in gw.downloads_started] == [4]

    assert c.post("/api/chats/-1001/download-all").json()["queued"] == 3
    wait_downloaded(c, 4)
    lib = c.get("/api/library").json()
    series = next(w for w in lib["items"] if w["series"])
    assert [e["volume"] for e in series["entries"]] == [1, 2, 3]
    assert c.get("/api/library", params={"view": "available"}).json()["total"] == 0


def test_auto_download_chat_option(app):
    c, gw = app
    c.post("/api/chats/-1001/settings", json={"auto_download": True})
    chat = next(x for x in c.get("/api/chats").json()["chats"] if x["id"] == -1001)
    assert chat["auto_download"] is True and chat["allowed_exts"] is None  # other options untouched
    gw.post(-1001, 5, FakeFile(5, "جديد.pdf", pdf_bytes("new")))
    c.post("/api/chats/-1001/scan")
    wait_until(lambda: any(tg == 5 for tg, _ in gw.downloads_started))
    # Files found before the option was turned on stay available.
    assert {tg for tg, _ in gw.downloads_started} == {5}


def test_library_chat_list(app):
    c, _ = app
    chats = c.get("/api/library/chats").json()
    assert chats == [{"id": -1001, "title": "مكتبة التراث", "type": "channel", "downloaded": 0, "available": 4}]


# ----------------------------------------------- delete, thumbnails, files
def test_delete_book(app, settings):
    c, gw = app
    c.post("/api/chats/-1001/download-all")
    wait_downloaded(c, 4)
    files = sorted(p.relative_to(settings.storage_dir).as_posix() for p in (settings.storage_dir / "library").rglob("*.*"))
    assert files[0].startswith("library/مكتبة التراث/") and len(files) == 4

    entry = next(w for w in c.get("/api/library").json()["items"] if not w["series"])["entries"][0]
    assert c.delete(f"/api/library/{entry['id']}").json() == {"ok": True}
    assert len(list((settings.storage_dir / "library").rglob("*.*"))) == 3
    assert c.get(f"/api/files/{entry['id']}").status_code == 404
    skipped = c.get("/api/documents", params={"status": "skipped"}).json()["items"]
    assert [(i["id"], i["last_error"]) for i in skipped] == [(entry["id"], "deleted")]

    # A rescan doesn't bring it back...
    c.post("/api/chats/-1001/scan")
    time.sleep(0.5)
    assert statuses(c)["skipped"] == 1
    # ...but the user can download it again.
    assert c.post(f"/api/documents/{entry['id']}/retry").status_code == 200
    wait_downloaded(c, 4)


def test_thumbnails(app, settings):
    c, _ = app
    c.post("/api/chats/-1001/download-all")
    wait_downloaded(c, 4)
    works = c.get("/api/library").json()["items"]
    pdf = next(w for w in works if w["series"])["entries"][0]
    r = c.get(f"/api/thumbs/{pdf['id']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert list((settings.data_dir / "thumbs").glob("*.jpg"))
    broken = next(w for w in works if not w["series"])["entries"][0]  # not a real epub
    assert c.get(f"/api/thumbs/{broken['id']}").status_code == 404
    assert c.get(f"/api/thumbs/{broken['id']}").status_code == 404  # remembered, not re-rendered


# ------------------------------------------------------- search and links
def test_search_all_chats_and_download_result(app):
    c, gw = app
    hits = c.get("/api/search", params={"q": "نادر"}).json()["items"]
    assert len(hits) == 1
    hit = hits[0]
    assert hit["chat"] == {"id": -1002, "title": "قناة أخرى", "type": "channel"} and hit["doc_id"] is None
    r = c.post("/api/search/download", json={"chat_id": -1002, "msg_id": 9}).json()
    assert r["queued"] is True and r["status"] == "pending"
    wait_downloaded(c, 1)
    again = c.get("/api/search", params={"q": "نادر"}).json()["items"][0]
    assert again["has_file"] is True
    assert c.post("/api/search/download", json={"chat_id": -1002, "msg_id": 12345}).status_code == 410


def test_download_from_link(app):
    c, gw = app
    r = c.post("/api/links/download", json={"url": "https://t.me/turath_lib/4"})
    assert r.status_code == 200 and r.json()["name"] == "مقدمة ابن خلدون.epub"
    wait_downloaded(c, 1)
    assert c.post("/api/links/download", json={"url": "https://example.com/x/1"}).json()["detail"]["code"] == "bad_link"
    assert c.post("/api/links/download", json={"url": "https://t.me/turath_lib/999"}).status_code == 404
    assert c.post("/api/links/download", json={"url": "https://t.me/nobody_here/1"}).status_code == 404


# ------------------------------------------------- schedule and speed limit
def test_download_window():
    utc = timezone.utc
    night = AppSettings(schedule_enabled=1, schedule_start="23:00", schedule_end="06:00", timezone="UTC")
    assert night.in_download_window(datetime(2026, 1, 1, 23, 30, tzinfo=utc))
    assert night.in_download_window(datetime(2026, 1, 1, 5, 59, tzinfo=utc))
    assert not night.in_download_window(datetime(2026, 1, 1, 12, 0, tzinfo=utc))
    day = AppSettings(schedule_enabled=1, schedule_start="09:00", schedule_end="17:00", timezone="Asia/Riyadh")
    assert day.in_download_window(datetime(2026, 1, 1, 7, 0, tzinfo=utc))  # 10:00 in Riyadh
    assert not day.in_download_window(datetime(2026, 1, 1, 15, 0, tzinfo=utc))  # 18:00 in Riyadh
    assert AppSettings(schedule_enabled=0).in_download_window(datetime(2026, 1, 1, 3, 0, tzinfo=utc))


def test_schedule_settings_and_engine_state(app):
    c, _ = app
    assert c.post("/api/settings", json={"schedule_start": "25:00"}).status_code == 422
    now = datetime.now(timezone.utc)
    start = f"{(now.hour + 2) % 24:02d}:00"
    end = f"{(now.hour + 3) % 24:02d}:00"
    r = c.post(
        "/api/settings",
        json={"schedule_enabled": True, "schedule_start": start, "schedule_end": end, "timezone": "UTC",
              "speed_limit_kbps": 512},
    ).json()
    assert r["schedule_enabled"] is True and r["speed_limit_kbps"] == 512
    engine = c.get("/api/engine").json()
    assert engine["state"] == "scheduled" and engine["schedule"] == {"enabled": True, "start": start, "end": end}
    c.post("/api/chats/-1001/download-all")
    time.sleep(0.5)
    assert statuses(c)["pending"] == 4  # waiting for the window
    c.post("/api/settings", json={"schedule_enabled": False})
    wait_downloaded(c, 4)


async def test_rate_limiter_paces_bytes():
    limiter = RateLimiter(lambda: 100_000)
    start = time.monotonic()
    for _ in range(4):
        await limiter.take(50_000)
    # 200 KB at 100 KB/s, minus the first second's allowance burst.
    assert time.monotonic() - start >= 1.4
    unlimited = RateLimiter(lambda: 0)
    start = time.monotonic()
    await unlimited.take(10**9)
    assert time.monotonic() - start < 0.1


# ------------------------------------------------------------- summary
async def test_daily_summary(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 1, FakeFile(1, "كتاب جديد.pdf", b"x" * 2048))
    gw.post(-1, 2, FakeFile(2, "متاح.pdf", b"y"))
    async with running_container(settings, gw) as ctx:
        await ctx.app_settings.update({"summary_enabled": 1, "summary_hour": 8, "timezone": "UTC"})
        await track(ctx, -1, auto_download=False)
        await ctx.scanner.scan(-1)
        await ctx.engine.start()
        doc1 = next(d for d in await all_docs(ctx) if d.tg_document_id == 1)
        await ctx.engine.request([doc1.id])
        for _ in range(200):
            if any(d.status == DocStatus.DONE for d in await all_docs(ctx)):
                break
            await asyncio.sleep(0.02)

        early = datetime(2030, 1, 1, 7, 0, tzinfo=timezone.utc)
        assert await ctx.summary.maybe_send(early) is False and gw.saved_messages == []
        later = datetime(2030, 1, 1, 9, 0, tzinfo=timezone.utc)
        assert await ctx.summary.maybe_send(later) is True
        (text,) = gw.saved_messages
        assert "كتاب جديد.pdf" in text and "متاحة للتنزيل" in text
        assert await ctx.summary.maybe_send(later) is False  # once a day
        assert len(gw.saved_messages) == 1


async def test_summary_skips_quiet_days(settings):
    async with running_container(settings, FakeGateway()) as ctx:
        assert await compose(ctx.db, datetime(2000, 1, 1)) is None


# ---------------------------------------------------- upgrades of old data
async def test_legacy_files_move_into_chat_folders(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "قناة: الكتب")
    gw.post(-1, 1, FakeFile(1, "كتاب.pdf", b"content"))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1, title="قناة: الكتب", auto_download=False)
        await ctx.scanner.scan(-1)
        sha = "ab" * 32
        legacy = settings.storage_dir / "library" / "ab" / f"{sha}.pdf"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"content")
        async with ctx.db.write() as s:
            blob = Blob(sha256=sha, size=7, storage_path=f"library/ab/{sha}.pdf", created_at=datetime(2026, 1, 1))
            s.add(blob)
            await s.flush()
            doc = (await s.execute(select(Document))).scalars().one()
            doc.status, doc.blob_id = DocStatus.DONE, blob.id
        await ctx.engine.start()
        moved = settings.storage_dir / "library" / "قناة_ الكتب" / "كتاب.pdf"
        assert moved.read_bytes() == b"content"
        assert not (settings.storage_dir / "library" / "ab").exists()


def test_migration_turns_queued_files_into_available(settings):
    settings.ensure_dirs()
    cfg = alembic_config(settings.db_path)
    command.upgrade(cfg, "0001")
    con = sqlite3.connect(settings.db_path)
    rows = [(1, "pending", 0), (2, "downloading", 4096), (3, "done", 0), (4, "failed", 0)]
    for tg, status, done in rows:
        con.execute(
            "INSERT INTO documents (tg_document_id, original_name, normalized_name, size, status, priority,"
            " bytes_downloaded, attempts, first_seen_at) VALUES (?, ?, ?, 10, ?, 0, ?, 0, '2026-01-01')",
            (tg, f"تاريخ بغداد {tg}.pdf", f"تاريخ بغداد {tg}", status, done),
        )
    con.commit()
    con.close()
    command.upgrade(cfg, "head")
    con = sqlite3.connect(settings.db_path)
    got = dict(con.execute("SELECT tg_document_id, status FROM documents").fetchall())
    assert got == {1: "available", 2: "available", 3: "done", 4: "failed"}
    assert con.execute("SELECT count(*) FROM documents WHERE series_key IS NULL").fetchone()[0] == 4
    con.close()
