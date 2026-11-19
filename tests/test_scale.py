"""What keeps the app quick when the library holds hundreds of thousands of
files: few statements per scanned batch, a search that folds nothing per row,
a queue that is never sorted to take its next file, and as few requests to
Telegram per downloaded file as it takes."""

import asyncio
import hashlib
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update

from tglib.api.documents import _text_filter
from tglib.app.main import create_app
from tglib.db.models import Blob, Document, DocStatus
from tglib.engine import downloader
from tglib.engine.catalog import backfill_search_text
from tglib.engine.downloader import CHUNK
from tglib.engine.errors import NotConnected
from tglib.engine.telegram import DocMessage, DocumentInfo, TelethonGateway
from fake_telegram import FakeFile, FakeGateway
from helpers import all_docs, running_container, sync_chats, track
from test_auth import login, wait_for_state
from test_chats_api import wait_until
from test_download import all_settled, start_engine, wait_for


@pytest.fixture(autouse=True)
def fast_engine(monkeypatch):
    monkeypatch.setattr(downloader, "DISK_MARGIN", 0)


def statements(engine) -> list[str]:
    """Every statement the engine runs from now on."""
    seen: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _seen(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    return seen


# ------------------------------------------------------------------ scanning
async def test_a_scanned_batch_takes_a_handful_of_statements(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    for i in range(1, 101):
        gw.post(-1, i, FakeFile(i, f"كتاب رقم {i}.pdf", b"x" * i), caption=f"وصف {i}")
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1, auto_download=False)
        seen = statements(ctx.db.write_engine)
        await ctx.scanner.scan(-1)
        docs = await all_docs(ctx)
        assert len(docs) == 100 and all(d.status == DocStatus.AVAILABLE for d in docs)
        # It was five per message: the write connection is the only one, and
        # downloads wait for it.
        assert len(seen) < 40, seen
        assert [d.id for d in docs] == list(range(1, 101))


async def test_same_name_and_size_twice_in_one_batch(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 3, FakeFile(30, "book.pdf", b"same size"))
    gw.post(-1, 2, FakeFile(20, "Book.PDF", b"same size"))  # another upload of it
    gw.post(-1, 1, FakeFile(10, "book.pdf", b"another size!"))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1, auto_download=False)
        await ctx.scanner.scan(-1)
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        # Newest first: 30 is the one the other is a duplicate of.
        assert by_tg[30].status == DocStatus.AVAILABLE
        assert by_tg[20].status == DocStatus.DUPLICATE and by_tg[20].duplicate_of_id == by_tg[30].id
        assert by_tg[10].status == DocStatus.AVAILABLE


# ------------------------------------------------------------------- search
async def found(ctx, q: str) -> set[int]:
    async with ctx.db.read() as s:
        return set((await s.execute(select(Document.tg_document_id).where(_text_filter(q)))).scalars())


async def test_search_looks_through_the_name_and_every_caption(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.add_chat(-2, "B")
    book = FakeFile(1, "تفسير الطبري.pdf", b"abc")
    gw.post(-1, 1, book, caption="الطبعة الأولى")
    gw.post(-2, 7, book, caption="نسخة أحمد شاكر")  # the same file, posted elsewhere
    gw.post(-1, 2, FakeFile(2, "100%_real.pdf", b"abcd"))
    async with running_container(settings, gw) as ctx:
        for chat in (-1, -2):
            await track(ctx, chat, auto_download=False)
            await ctx.scanner.scan(chat)
        assert await found(ctx, "الطبري") == {1}
        assert await found(ctx, "الاولى") == {1}  # folded: أ = ا
        assert await found(ctx, "احمد شاكر") == {1}  # the caption of its second post
        assert await found(ctx, "100%") == {2}  # % is itself, not "anything"
        assert await found(ctx, "غير موجود") == set()


async def test_search_text_of_files_recorded_before_it_existed(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 1, FakeFile(1, "مقدمة ابن خلدون.epub", b"abc"), caption="طبعة دار الفكر")
    gw.post(-1, 2, FakeFile(2, "بلا وصف.pdf", b"abcd"))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1, auto_download=False)
        await ctx.scanner.scan(-1)
        recorded = {d.tg_document_id: d.search_text for d in await all_docs(ctx)}
        async with ctx.db.write() as s:
            await s.execute(update(Document).values(search_text=None))
        assert await found(ctx, "دار الفكر") == set()
        async with ctx.db.write() as s:
            assert await backfill_search_text(s) == 2
        assert {d.tg_document_id: d.search_text for d in await all_docs(ctx)} == recorded
        assert await found(ctx, "دار الفكر") == {1}


# -------------------------------------------------------------------- queue
async def test_taking_the_next_file_does_not_sort_the_queue(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    for i in range(1, 6):
        gw.post(-1, i, FakeFile(i, f"f{i}.pdf", b"x" * i))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1)
        await ctx.scanner.scan(-1)
        claims: list[tuple[str, tuple]] = []

        @event.listens_for(ctx.db.write_engine.sync_engine, "before_cursor_execute")
        def _seen(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE documents"):
                claims.append((statement, parameters))

        job = await ctx.engine._claim()
        assert job is not None and job.tg_document_id == 5  # scanned newest first
        statement, parameters = claims[0]
        with sqlite3.connect(settings.db_path) as con:
            plan = " | ".join(row[3] for row in con.execute("EXPLAIN QUERY PLAN " + statement, parameters))
        assert "ix_documents_claim" in plan and "TEMP B-TREE" not in plan, plan


async def test_whole_chat_is_queued_in_one_statement(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    for i in range(1, 31):
        gw.post(-1, i, FakeFile(i, f"f{i}.pdf", b"x" * i))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1, auto_download=False)
        await ctx.scanner.scan(-1)
        seen = statements(ctx.db.write_engine)
        assert await ctx.engine.request_chat(-1) == 30
        assert sum(st.startswith("UPDATE") for st in seen) == 1
        assert all(d.status == DocStatus.PENDING for d in await all_docs(ctx))
        assert await ctx.engine.request_chat(-1) == 0


# ---------------------------------------------------------------- counters
async def test_chat_counters_are_kept_until_something_changes(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 1, FakeFile(1, "a.pdf", b"abc"))
    gw.post(-1, 2, FakeFile(2, "b.pdf", b"abcd"))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1, auto_download=False)
        await ctx.scanner.scan(-1)
        counts = await ctx.chat_counts.get()
        assert (counts[-1].files, counts[-1].available, counts[-1].pending) == (2, 2, 0)
        assert await ctx.chat_counts.get() is counts
        # Download progress changes none of them.
        async with ctx.db.write(minor=True) as s:
            await s.execute(update(Document).values(bytes_downloaded=1))
        assert await ctx.chat_counts.get() is counts
        doc = (await all_docs(ctx))[0]
        await ctx.engine.request([doc.id])
        counts = await ctx.chat_counts.get()
        assert (counts[-1].available, counts[-1].pending) == (1, 1)


async def test_a_repost_counts_as_downloaded_only_once_its_original_is(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 1, FakeFile(1, "كتاب.pdf", b"a" * 100))
    gw.post(-1, 2, FakeFile(2, "كتاب.pdf", b"b" * 100))  # same name and size: a repost
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1, auto_download=False)
        await ctx.scanner.scan(-1)
        assert {d.status for d in await all_docs(ctx)} == {DocStatus.AVAILABLE, DocStatus.DUPLICATE}
        counts = await ctx.chat_counts.get()
        assert (counts[-1].files, counts[-1].downloaded, counts[-1].available) == (2, 0, 1)
        assert await ctx.engine.request_chat(-1) == 1
        await start_engine(ctx)
        await wait_for(all_settled(ctx))
        counts = await ctx.chat_counts.get()
        assert (counts[-1].downloaded, counts[-1].stored) == (2, 1)


NAMES = ["الأغاني", "البيان والتبيين", "الكامل", "العقد الفريد", "أدب الكاتب"]  # five works, no volumes


def test_library_pages_carry_the_total(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    for i in range(1, 6):
        gw.post(-1, i, FakeFile(i, f"{NAMES[i - 1]}.pdf", b"x" * i))
    with TestClient(create_app(settings, lambda: gw)) as c:
        login(c)
        wait_for_state(c, "ready")
        sync_chats(c)
        c.post("/api/chats/-1/track", json={"tracked": True})
        wait_until(lambda: c.get("/api/stats").json()["status_counts"]["available"] == 5)

        def page(n: int, **params) -> dict:
            return c.get("/api/library", params={"view": "available", "page_size": 2, "page": n, **params}).json()

        assert [(len(page(n)["items"]), page(n)["total"]) for n in (1, 2, 3, 9)] == [(2, 5), (2, 5), (1, 5), (0, 5)]
        names = [w["title"] for n in (1, 2, 3) for w in page(n, sort="name")["items"]]
        assert names == [f"{name}.pdf" for name in sorted(NAMES)]
        assert page(1, q="لا شيء")["total"] == 0
        chats = c.get("/api/library/chats").json()
        assert [(x["id"], x["downloaded"], x["available"]) for x in chats] == [(-1, 0, 5)]


# ---------------------------------------------------------------- downloads
async def test_one_request_brings_the_messages_of_the_files_queued_next(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    for i in range(1, 13):
        gw.post(-1, i, FakeFile(i, f"photo{i}.jpg", os.urandom(50 + i), mime="image/jpeg"))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1)
        await ctx.scanner.scan(-1)
        await ctx.engine.start()
        await wait_for(all_settled(ctx))
        assert all(d.status == DocStatus.DONE for d in await all_docs(ctx))
        # Twelve files, one request for their messages (it was one each).
        assert len(gw.message_requests) == 1
        assert sorted(gw.message_requests[0][1]) == list(range(1, 13))


async def test_kept_message_with_an_expired_reference_is_fetched_again(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 1, FakeFile(1, "a.pdf", os.urandom(CHUNK * 2)))
    gw.post(-1, 2, FakeFile(2, "b.pdf", os.urandom(CHUNK * 2)))
    async with running_container(settings, gw) as ctx:
        await ctx.app_settings.update({"workers": 1})
        await track(ctx, -1)
        await ctx.scanner.scan(-1)
        gw.expire_ref_after_bytes = CHUNK
        await ctx.engine.start()
        await wait_for(all_settled(ctx))
        assert all(d.status == DocStatus.DONE and d.attempts == 0 for d in await all_docs(ctx))
        # Both in the first request; the expired one again, alone.
        assert [sorted(ids) for _, ids in gw.message_requests] == [[1, 2], [2]]


async def test_a_file_is_hashed_as_it_arrives_and_recorded_once(settings, monkeypatch):
    def no_second_read(path):
        raise AssertionError("the file was read again to hash it")

    monkeypatch.setattr(downloader, "sha256_file", no_second_read)
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    content = os.urandom(CHUNK * 2 + 77)
    gw.post(-1, 1, FakeFile(1, "a.pdf", content))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1)
        await ctx.scanner.scan(-1)
        seen = statements(ctx.db.write_engine)
        await ctx.engine.start()
        await wait_for(all_settled(ctx))
        doc = (await all_docs(ctx))[0]
        assert doc.status == DocStatus.DONE and doc.bytes_downloaded == len(content)
        async with ctx.db.read() as s:
            blob = await s.get(Blob, doc.blob_id)
        assert blob.sha256 == hashlib.sha256(content).hexdigest()
        assert (settings.storage_dir / blob.storage_path).read_bytes() == content
        # Its progress isn't written just before the row that completes it.
        progress = [st for st in seen if "SET bytes_downloaded=?" in st and "status" not in st]
        assert progress == []


async def test_pieces_at_once_is_a_setting_passed_to_the_download(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 1, FakeFile(1, "a.pdf", os.urandom(CHUNK + 5)))
    async with running_container(settings, gw) as ctx:
        assert ctx.app_settings.current.download_streams == 1  # one after the other unless asked
        await ctx.app_settings.update({"download_streams": 4})
        await track(ctx, -1)
        await ctx.scanner.scan(-1)
        await ctx.engine.start()
        await wait_for(all_settled(ctx))
        assert gw.download_streams == [4]
        assert (await ctx.app_settings.update({"download_streams": 99})).download_streams == 8


# ------------------------------------------------ pieces of a file at once
class FakePieces:
    """Stands in for Telethon's `iter_download(limit=1)`: one piece per call,
    arriving after a delay of its own."""

    def __init__(self, content: bytes, delays: dict[int, float] | None = None, short_at: int | None = None):
        self.content = content
        self.delays = delays or {}
        self.short_at = short_at
        self.calls: list[int] = []
        self.running = 0
        self.most_running = 0
        self.closed = 0

    def __call__(self, handle, *, offset, limit, request_size, file_size):
        assert limit == 1 and file_size == len(self.content)
        self.calls.append(offset)
        outer = self

        class Piece:
            async def __aiter__(self):
                outer.running += 1
                outer.most_running = max(outer.most_running, outer.running)
                try:
                    await asyncio.sleep(outer.delays.get(offset, 0.001))
                    size = request_size // 2 if offset == outer.short_at else request_size
                    yield outer.content[offset : offset + size]
                finally:
                    outer.running -= 1

            async def close(self):
                outer.closed += 1

        return Piece()


def telethon_download(tmp_path, monkeypatch, pieces: FakePieces):
    gw = TelethonGateway(tmp_path / "test", 12345, "0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(gw._client, "iter_download", pieces)
    dm = DocMessage(-1, 1, None, None, DocumentInfo(1, "a.bin", ".bin", None, len(pieces.content)), handle=object())
    return gw, dm


async def test_pieces_asked_for_at_once_arrive_in_order(tmp_path, monkeypatch):
    size = 4096
    content = os.urandom(size * 9 + 100)
    # The first piece is the slowest: the ones after it wait their turn.
    pieces = FakePieces(content, delays={size: 0.05, size * 2: 0.0})
    gw, dm = telethon_download(tmp_path, monkeypatch, pieces)
    got = [chunk async for chunk in gw.iter_download(dm, size, size, streams=3)]
    assert b"".join(got) == content[size:]
    assert [len(c) for c in got] == [size] * 8 + [100]
    assert pieces.most_running == 3 and pieces.running == 0
    assert pieces.calls == list(range(size, len(content), size)) and pieces.closed == len(pieces.calls)


async def test_a_short_piece_stops_the_download_instead_of_corrupting_it(tmp_path, monkeypatch):
    size = 4096
    pieces = FakePieces(os.urandom(size * 6), short_at=size * 2)
    gw, dm = telethon_download(tmp_path, monkeypatch, pieces)
    got = []
    with pytest.raises(NotConnected):
        async for chunk in gw.iter_download(dm, 0, size, streams=4):
            got.append(chunk)
    assert b"".join(got) == pieces.content[: size * 2]  # everything before it, nothing after
    await asyncio.sleep(0.01)
    assert pieces.running == 0  # the pieces asked for ahead were stopped


async def test_stopping_early_stops_the_pieces_asked_for_ahead(tmp_path, monkeypatch):
    size = 4096
    pieces = FakePieces(os.urandom(size * 20), delays={o: 0.02 for o in range(size, size * 20, size)})
    gw, dm = telethon_download(tmp_path, monkeypatch, pieces)
    stream = gw.iter_download(dm, 0, size, streams=4)
    assert await anext(stream) == pieces.content[:size]
    await stream.aclose()
    assert pieces.running == 0 and len(pieces.calls) <= 5


async def test_messages_of_one_chat_are_fetched_in_one_request(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from telethon.tl import types

    gw = TelethonGateway(tmp_path / "test", 12345, "0123456789abcdef0123456789abcdef")
    calls = []

    def message(msg_id: int):
        doc = types.Document(
            id=1000 + msg_id, access_hash=1, file_reference=b"ref", date=datetime(2024, 5, 1, tzinfo=timezone.utc),
            mime_type="application/pdf", size=4096, dc_id=2,
            attributes=[types.DocumentAttributeFilename(file_name=f"{msg_id}.pdf")],
        )
        return types.Message(
            id=msg_id, peer_id=types.PeerChannel(channel_id=1234), date=datetime(2024, 5, 1, tzinfo=timezone.utc),
            message="", media=types.MessageMediaDocument(document=doc),
        )

    async def fake_input_entity(chat_id):
        return types.InputPeerChannel(1234, 1)

    async def fake_get_messages(entity, ids):
        calls.append(ids)
        # A deleted message comes back as None, one without a file as it is.
        return [message(7), None, types.Message(id=9, peer_id=types.PeerChannel(channel_id=1234), message="نص")]

    monkeypatch.setattr(gw._client, "get_input_entity", fake_input_entity)
    monkeypatch.setattr(gw._client, "get_messages", fake_get_messages)
    found_messages = await gw.get_document_messages(-1001234, (7, 8, 9))
    assert calls == [[7, 8, 9]]
    assert {k: v.document.tg_document_id for k, v in found_messages.items()} == {7: 1007}
