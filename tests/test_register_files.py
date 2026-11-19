"""tools/register_files.py: a standalone program (it imports nothing from the
app) that records files already on disk as downloaded."""

import hashlib
import importlib.util
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fake_telegram import FakeGateway
from helpers import running_container, track
from test_auth import login
from tglib.app.main import create_app
from tglib.db.migrate import upgrade_to_head
from tglib.db.models import Blob, Chat, Document, DocStatus
from tglib.engine import normalize, storage, volumes
from tglib.engine.catalog import Filters, record_messages
from tglib.engine.telegram import DocMessage, DocumentInfo

_spec = importlib.util.spec_from_file_location(
    "register_files", Path(__file__).resolve().parents[1] / "tools" / "register_files.py"
)
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)

NAMES = [
    "Book_Title.Part.1.pdf", "Book Title [@SomeChannel].pdf", "@kutub_pdf - الكتاب.pdf",
    "(الطبعة الثانية) كتاب.pdf", "archive.tar.gz", "رابط t.me/somechannel كتاب.pdf",
    "أحمد إبراهيم آدم ٱلكتاب.pdf", "الْكِتَابُ.pdf", "الكـــتاب.pdf", "الجزء ١٢.pdf", "ﻻمع.pdf",
    "تفسير_الطبري_الجزء_الأول @tafsir_lib.pdf", "تفسير الطبري ج3.pdf", "تفسير الطبري - المجلد الثاني عشر.pdf",
    "تفسير الطبري الجزء العشرون.pdf", "Some Book vol 02.epub", "Some Book 7.epub", "no_extension",
    ".hidden", "con.pdf", 'bad<>:"name?.pdf', "x" * 200 + ".pdf", "photo.JPEG", "",
]


@pytest.mark.parametrize("name", NAMES)
def test_name_rules_are_the_apps(name):
    assert rf.split_ext(name) == normalize.split_ext(name)
    assert rf.fold(name) == normalize.fold(name)
    normalized = normalize.normalize_name(name)
    assert rf.normalize_name(name) == normalized
    assert rf.parse_volume(normalized) == volumes.parse_volume(normalized)
    ext = normalize.split_ext(name)[1]
    assert rf.safe_file_name(name, ext, "f") == storage.file_name(name, ext, "f")


def test_ordinals_are_the_apps():
    assert rf._ORDINALS == volumes._ORDINALS


def make_doc_message(msg_id, tg_id, name, size):
    ext = normalize.split_ext(name)[1]
    return DocMessage(-1001, msg_id, datetime(2024, 5, 1), None, DocumentInfo(tg_id, name, ext, "application/pdf", size))


def _run(settings, *folders, extra=()):
    return rf.main(["--db", str(settings.db_path), "--storage", str(settings.storage_dir), *extra, *map(str, folders)])


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def test_registers_files_and_links_known_ones(settings, tmp_path):
    gw = FakeGateway()
    src = tmp_path / "My Downloads"
    known = _write(src / "كتاب التوحيد.pdf", b"known" * 100)
    _write(src / "sub" / "novel.epub", b"novel" * 50)
    _write(src / "sub" / "novel copy.epub", b"novel" * 50)  # same content
    _write(src / "movie.mp4.part", b"partial")
    _write(src / "empty.pdf", b"")

    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001, "قناة الكتب", auto_download=False)
        async with ctx.db.write() as s:
            chat = await s.get(Chat, -1001)
            # Listed by a scan, twice (the second is a name+size duplicate).
            await record_messages(
                s, chat,
                [
                    make_doc_message(10, 5001, "كتاب_التوحيد.pdf", known.stat().st_size),
                    make_doc_message(11, 5002, "كتاب التوحيد.pdf", known.stat().st_size),
                ],
                Filters(exts={".pdf"}, max_size=0),
            )

    assert _run(settings, src, extra=["--dry-run", "--copy"]) == 0
    con = sqlite3.connect(settings.db_path)
    assert con.execute("SELECT count(*) FROM blobs").fetchone()[0] == 0
    con.close()

    assert _run(settings, src, extra=["--copy"]) == 0
    assert list(settings.data_dir.glob("library.db.before-register-*"))
    assert known.exists()  # copied, not moved

    async with running_container(settings, gw) as ctx:
        async with ctx.db.read() as s:
            blobs = (await s.execute(select(Blob).order_by(Blob.id))).scalars().all()
            docs = {d.tg_document_id: d for d in (await s.execute(select(Document))).scalars()}
        assert sorted(b.storage_path for b in blobs) == [
            "library/My Downloads/sub/novel copy.epub",
            "library/My Downloads/كتاب التوحيد.pdf",
        ]
        for b in blobs:
            data = (settings.storage_dir / b.storage_path).read_bytes()
            assert hashlib.sha256(data).hexdigest() == b.sha256 and len(data) == b.size
        # The scanned entries got the content; no extra document for that file.
        assert docs[5001].status == DocStatus.DONE and docs[5002].status == DocStatus.DUPLICATE
        assert docs[5001].blob_id == docs[5002].blob_id is not None
        assert docs[5002].duplicate_of_id == docs[5001].id
        assert len(docs) == 3
        new = next(d for tg, d in docs.items() if tg < 0)
        assert (new.status, new.ext, new.mime) == (DocStatus.DONE, ".epub", "application/epub+zip")
        assert new.normalized_name == "novel copy" and "my downloads sub" in new.search_text
        assert isinstance(new.completed_at, datetime) and isinstance(new.first_seen_at, datetime)

        # The app lists and serves them.
        with TestClient(create_app(settings, lambda: gw)) as client:
            login(client)
            items = client.get("/api/library").json()["items"]
            assert len(items) == 2
            res = client.get(f"/api/files/{new.id}")
            assert res.status_code == 200 and res.content == b"novel" * 50

        # A later scan finds the unlinked file in a channel: already downloaded.
        async with ctx.db.write() as s:
            out = await record_messages(
                s, await s.get(Chat, -1001),
                [make_doc_message(12, 5003, "novel_copy.epub", 250)],
                Filters(exts={".epub"}, max_size=0),
            )
        assert out == [DocStatus.DUPLICATE]

    # Running it again changes nothing.
    assert _run(settings, src, extra=["--copy"]) == 0
    con = sqlite3.connect(settings.db_path)
    assert con.execute("SELECT count(*) FROM blobs").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM documents").fetchone()[0] == 4
    con.close()


def test_move_and_files_already_in_storage(settings, tmp_path):
    settings.ensure_dirs()
    upgrade_to_head(settings.db_path)
    src = tmp_path / "old"
    a = _write(src / "a.pdf", b"aaa")
    b = _write(src / "b: odd?.pdf", b"bbb")
    inside = _write(settings.library_dir / "Some Channel" / "c.pdf", b"ccc")
    _write(settings.library_dir / "Some Channel" / "a.pdf", b"other content")
    _write(settings.tmp_dir / "123.part", b"zzz")

    assert _run(settings, src, settings.storage_dir, extra=["--move", "--no-backup"]) == 0
    assert not a.exists() and not b.exists() and inside.exists()
    con = sqlite3.connect(settings.db_path)
    paths = sorted(r[0] for r in con.execute("SELECT storage_path FROM blobs"))
    con.close()
    assert paths == [
        "library/Some Channel/a.pdf",
        "library/Some Channel/c.pdf",
        "library/old/a.pdf",
        "library/old/b_ odd_.pdf",
    ]
    assert all((settings.storage_dir / p).is_file() for p in paths)


def test_storage_folder_holding_the_collection_is_registered_in_place(settings, tmp_path):
    """The app's storage folder set to the folder the files are already in."""
    settings.ensure_dirs()
    upgrade_to_head(settings.db_path)
    kept = _write(settings.storage_dir / "Telegram Desktop" / "books" / "a.pdf", b"aaa")
    assert _run(settings, settings.storage_dir, extra=["--no-backup"]) == 0
    assert kept.read_bytes() == b"aaa"
    with TestClient(create_app(settings, lambda: FakeGateway())) as client:
        login(client)
        assert len(client.get("/api/library").json()["items"]) == 1
        con = sqlite3.connect(settings.db_path)
        doc_id, path = con.execute(
            "SELECT d.id, b.storage_path FROM documents d JOIN blobs b ON b.id = d.blob_id"
        ).fetchone()
        con.close()
        assert path == "Telegram Desktop/books/a.pdf"
        assert client.get(f"/api/files/{doc_id}").content == b"aaa"


async def test_files_elsewhere_are_registered_where_they_are(settings, tmp_path):
    """The default: nothing is copied or moved, and the app does not download
    the file again when a scan finds it."""
    gw = FakeGateway()
    src = tmp_path / "Telegram Desktop"
    book = _write(src / "deep" / "My Book.pdf", b"book" * 10)
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001, "books", auto_download=True)
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file() and "data" not in p.parts)

    assert _run(settings, src, extra=["--no-backup"]) == 0
    assert sorted(p for p in tmp_path.rglob("*") if p.is_file() and "data" not in p.parts) == before
    con = sqlite3.connect(settings.db_path)
    assert con.execute("SELECT storage_path FROM blobs").fetchall() == [(book.resolve().as_posix(),)]
    con.close()

    # Again: recognised by its place and size, without reading it.
    rf.sha256_file, real = None, rf.sha256_file
    try:
        assert _run(settings, src, extra=["--no-backup"]) == 0
    finally:
        rf.sha256_file = real

    async with running_container(settings, gw) as ctx:
        async with ctx.db.write() as s:
            out = await record_messages(
                s, await s.get(Chat, -1001),
                [make_doc_message(7, 9001, "My_Book.pdf", 40)],
                Filters(exts={".pdf"}, max_size=0, auto_download=True),
            )
        assert out == [DocStatus.DUPLICATE]  # not queued for download
    with TestClient(create_app(settings, lambda: gw)) as client:
        login(client)
        assert len(client.get("/api/library").json()["items"]) == 1
        con = sqlite3.connect(settings.db_path)
        (doc_id,) = con.execute("SELECT id FROM documents WHERE tg_document_id < 0").fetchone()
        con.close()
        assert client.get(f"/api/files/{doc_id}").status_code == 404  # outside the storage folder
    assert book.read_bytes() == b"book" * 10


def test_refuses_a_database_that_is_not_the_apps(settings, tmp_path):
    settings.ensure_dirs()
    sqlite3.connect(settings.db_path).close()
    with pytest.raises(SystemExit):
        _run(settings, tmp_path)


def _wait_import(client, timeout=10.0):
    end = time.monotonic() + timeout
    while True:
        job = client.get("/api/import").json()["job"]
        if job["state"] != "running":
            return job
        assert time.monotonic() < end, "the import did not finish"
        time.sleep(0.02)


def test_app_adds_local_files_while_running(settings, tmp_path):
    """The app's own page: the same run, started over the API with the app on."""
    src = tmp_path / "My Downloads"
    _write(src / "a.pdf", b"aaa")
    _write(src / "sub" / "b.epub", b"bbb")
    _write(src / "sub" / "b again.epub", b"bbb")
    _write(src / ".hidden" / "c.pdf", b"ccc")
    with TestClient(create_app(settings, lambda: FakeGateway())) as client:
        assert client.get("/api/import").status_code == 401
        login(client)
        assert client.get("/api/import").json()["job"] is None

        # The folder picker.
        kinds = {s["kind"]: s["path"] for s in client.get("/api/import/folders").json()["shortcuts"]}
        assert kinds["storage"] == str(settings.storage_dir)
        listing = client.get("/api/import/folders", params={"path": str(src)}).json()
        assert [d["name"] for d in listing["dirs"]] == ["sub"] and listing["files"] == 1
        assert listing["parent"] == str(tmp_path)
        assert client.get("/api/import/folders", params={"path": str(src / "nope")}).status_code == 400

        assert client.post("/api/import", json={"folders": [str(src / "nope")]}).status_code == 400
        assert client.post("/api/import", json={"folders": []}).status_code == 422

        # A check changes nothing.
        assert client.post("/api/import", json={"folders": [str(src)], "check_only": True}).status_code == 200
        job = _wait_import(client)
        assert (job["state"], job["counts"]["new"], job["counts"]["same"]) == ("done", 2, 1)
        assert client.get("/api/library").json()["items"] == []
        assert client.get("/api/stats").json()["files"] == 0

        # The real thing, copied into the storage folder.
        client.post("/api/import", json={"folders": [str(src)], "mode": "copy"})
        job = _wait_import(client)
        assert job["state"] == "done" and job["error"] is None and job["failures"] == []
        assert (job["files_done"], job["files_total"], job["bytes_done"]) == (3, 3, 9)
        assert job["counts"]["new"] == 2 and job["counts"]["same"] == 1 and job["moved_bytes"] == 6
        items = client.get("/api/library").json()["items"]
        assert len(items) == 2
        assert client.get(f"/api/files/{items[0]['entries'][0]['id']}").status_code == 200
        assert (src / "a.pdf").exists()
        assert len(list(settings.data_dir.glob("library.db.before-register-*"))) == 1

        # Again: everything is already there, and no second safety copy today.
        client.post("/api/import", json={"folders": [str(src)], "mode": "move"})
        job = _wait_import(client)
        assert job["counts"]["new"] == 0 and job["counts"]["same"] == 3
        assert len(client.get("/api/library").json()["items"]) == 2
        assert len(list(settings.data_dir.glob("library.db.before-register-*"))) == 1


def test_window_registers_files_where_they_are(settings, tmp_path):
    """The program's own window: its page, served to this computer only."""
    import json
    import threading
    import urllib.error
    import urllib.request

    settings.ensure_dirs()
    upgrade_to_head(settings.db_path)
    src = tmp_path / "Telegram Desktop"
    book = _write(src / "deep" / "My Book.pdf", b"book" * 10)
    server, window, url = rf.serve(settings.db_path, settings.storage_dir)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(path, body=None, base=url):
        data = None if body is None else json.dumps(body).encode()
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data), timeout=5) as r:
            raw = r.read()
            return raw.decode() if "html" in r.headers["Content-Type"] else json.loads(raw)

    def finished():
        end = time.monotonic() + 10
        while True:
            job = call("api/state")["job"]
            if job["state"] != "running":
                return job
            assert time.monotonic() < end
            time.sleep(0.02)

    try:
        assert "<html" in call("")
        state = call("api/state")
        assert state["problem"] is None and state["job"] is None
        # The address holds a secret: another page on this computer cannot use the program.
        with pytest.raises(urllib.error.HTTPError):
            call("api/state", base=url.rsplit("/", 2)[0] + "/guess/")
        listing = call("api/folders?path=" + urllib.request.quote(str(src)))
        assert [d["name"] for d in listing["dirs"]] == ["deep"]
        with pytest.raises(urllib.error.HTTPError):
            call("api/start", {"folders": [str(src / "nope")], "check_only": False})

        call("api/start", {"folders": [str(src)], "check_only": True})
        assert finished()["counts"]["new"] == 1
        con = sqlite3.connect(settings.db_path)
        assert con.execute("SELECT count(*) FROM blobs").fetchone()[0] == 0

        call("api/start", {"folders": [str(src)], "check_only": False})
        job = finished()
        assert (job["state"], job["mode"], job["counts"]["new"], job["moved_bytes"]) == ("done", "keep", 1, 0)
        assert con.execute("SELECT storage_path FROM blobs").fetchall() == [(book.resolve().as_posix(),)]
        con.close()
        assert book.read_bytes() == b"book" * 10 and not list(settings.library_dir.rglob("*.pdf"))

        # A database it may not write to: it says so, with what to do about it.
        settings.db_path.chmod(0o444)
        state = call("api/state")
        if state["problem"] is not None:  # (root may write anyway)
            assert state["problem"] == "not_writable" and "chown" in state["fix"]
            with pytest.raises(urllib.error.HTTPError):
                call("api/start", {"folders": [str(src)], "check_only": False})
    finally:
        settings.db_path.chmod(0o644)
        server.shutdown()
        server.server_close()


def test_files_left_in_place_need_no_write_access_to_storage(settings, tmp_path):
    settings.ensure_dirs()
    upgrade_to_head(settings.db_path)
    _write(tmp_path / "old" / "a.pdf", b"aaa")
    settings.library_dir.chmod(0o555)
    try:
        assert _run(settings, tmp_path / "old", extra=["--no-backup"]) == 0
    finally:
        settings.library_dir.chmod(0o755)
    con = sqlite3.connect(settings.db_path)
    assert con.execute("SELECT count(*) FROM blobs").fetchone()[0] == 1
    con.close()
