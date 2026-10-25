import asyncio
import hashlib
import os
import time
from collections import namedtuple

import pytest
from sqlalchemy import update

from tglib.db.models import Blob, Document, DocStatus
from tglib.engine import downloader
from tglib.engine.downloader import CHUNK, EngineState, backoff_seconds, resume_offset
from fake_telegram import FakeFile, FakeGateway
from helpers import all_docs, running_container, track


async def wait_for(cond, timeout=10.0, interval=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = await cond()
        if result:
            return result
        await asyncio.sleep(interval)
    raise AssertionError("timed out waiting for condition")


def all_settled(ctx):
    async def check():
        docs = await all_docs(ctx)
        return docs and all(d.status not in (DocStatus.PENDING, DocStatus.DOWNLOADING) for d in docs)

    return check


async def blobs(ctx) -> list[Blob]:
    from sqlalchemy import select

    async with ctx.db.read() as s:
        return list((await s.execute(select(Blob))).scalars())


def library_files(settings) -> list:
    return [p for p in (settings.storage_dir / "library").rglob("*") if p.is_file()]


@pytest.fixture(autouse=True)
def fast_engine(monkeypatch):
    monkeypatch.setattr(downloader, "PERSIST_BYTES", CHUNK)
    monkeypatch.setattr(downloader, "OFFLINE_RETRY_SECONDS", 0.2)
    monkeypatch.setattr(downloader, "backoff_seconds", lambda attempts: 0)
    monkeypatch.setattr(downloader, "DISK_MARGIN", 0)


# ---------------------------------------------------------------- unit tests
def test_resume_offset():
    assert resume_offset(0, 0) == 0
    assert resume_offset(CHUNK * 3 + 10, CHUNK * 3 + 10) == CHUNK * 3
    # Never trust bytes beyond the last durable point...
    assert resume_offset(CHUNK * 5, CHUNK * 2 + 1) == CHUNK * 2
    # ...nor a recorded value larger than the file actually is.
    assert resume_offset(CHUNK + 5, CHUNK * 4) == CHUNK
    assert resume_offset(100, 50, chunk=16) == 48


def test_backoff_grows_and_caps():
    assert [backoff_seconds(a) for a in (1, 2, 3)] == [30, 60, 120]
    assert backoff_seconds(30) == 3600


# --------------------------------------------------------- engine scenarios
async def start_engine(ctx):
    await ctx.engine.start()


async def scan_all(ctx, *chat_ids):
    for c in chat_ids:
        await track(ctx, c)
        await ctx.scanner.scan(c)


async def test_downloads_dedup_by_content_and_forward(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.add_chat(-2, "B")
    content = os.urandom(CHUNK * 2 + 123)
    gw.post(-1, 1, FakeFile(10, "book.pdf", content))
    gw.post(-2, 1, FakeFile(10, "book.pdf", content))  # forward
    gw.post(-2, 2, FakeFile(11, "renamed copy.pdf", content))  # re-upload, other name
    gw.post(-2, 3, FakeFile(12, "other.epub", b"different"))
    async with running_container(settings, gw) as ctx:
        await scan_all(ctx, -1, -2)
        await start_engine(ctx)
        await wait_for(all_settled(ctx))
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        assert len(by_tg) == 3
        assert {by_tg[10].status, by_tg[11].status} == {DocStatus.DONE, DocStatus.DUPLICATE}
        assert by_tg[10].blob_id == by_tg[11].blob_id
        assert by_tg[12].status == DocStatus.DONE

        bl = {b.id: b for b in await blobs(ctx)}
        assert len(bl) == 2 and len(library_files(settings)) == 2
        stored = settings.storage_dir / bl[by_tg[10].blob_id].storage_path
        assert stored.read_bytes() == content
        sha = hashlib.sha256(content).hexdigest()
        assert hashlib.sha256(stored.read_bytes()).hexdigest() == sha
        # Stored once, in the folder of its chat, under one of its names.
        rel = stored.relative_to(settings.storage_dir).as_posix()
        assert rel in ("library/chat/book.pdf", "library/chat/renamed copy.pdf")
        assert not list((settings.storage_dir / "tmp").glob("*.part"))


async def test_name_size_duplicate_inherits_blob(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 1, FakeFile(1, "كتاب_التاريخ.pdf", b"a" * 1000))
    gw.post(-1, 2, FakeFile(2, "كتاب التاريخ @chan.pdf", b"b" * 1000))
    async with running_container(settings, gw) as ctx:
        await scan_all(ctx, -1)
        await start_engine(ctx)
        await wait_for(all_settled(ctx))
        docs = await all_docs(ctx)
        # The scan is newest-first, so the later post becomes the original.
        original = next(d for d in docs if d.status == DocStatus.DONE)
        dup = next(d for d in docs if d.status == DocStatus.DUPLICATE)
        assert dup.blob_id == original.blob_id and dup.duplicate_of_id == original.id
        assert [tg for tg, _ in gw.downloads_started] == [original.tg_document_id]


async def test_graceful_stop_mid_download_resumes_from_partial(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    content = os.urandom(CHUNK * 8)
    gw.post(-1, 1, FakeFile(7, "big.pdf", content))
    gw.download_delay = 0.03
    async with running_container(settings, gw) as ctx:
        await scan_all(ctx, -1)
        await start_engine(ctx)

        async def progressed():
            d = (await all_docs(ctx))[0]
            return d.bytes_downloaded >= CHUNK * 3

        await wait_for(progressed)
    # Container stopped ("docker stop"). Start again on the same data.
    gw.download_delay = 0
    async with running_container(settings, gw) as ctx:
        await start_engine(ctx)
        await wait_for(all_settled(ctx))
        doc = (await all_docs(ctx))[0]
        assert doc.status == DocStatus.DONE
        second_offset = gw.downloads_started[-1][1]
        assert second_offset >= CHUNK * 3 and second_offset % CHUNK == 0
        (b,) = await blobs(ctx)
        assert (settings.storage_dir / b.storage_path).read_bytes() == content


async def test_hard_kill_with_unsynced_garbage_tail(settings):
    """Simulates power loss: the DB says `downloading`, the .part has more
    bytes than were recorded as durable, and the tail is garbage."""
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    content = os.urandom(CHUNK * 6)
    gw.post(-1, 1, FakeFile(8, "big.pdf", content))
    async with running_container(settings, gw) as ctx:
        await scan_all(ctx, -1)
        durable = CHUNK * 3 + 1000
        part = settings.storage_dir / "tmp" / "8.part"
        part.write_bytes(content[:durable] + b"\0" * 5000)
        async with ctx.db.write() as s:
            await s.execute(
                update(Document).values(status=DocStatus.DOWNLOADING, bytes_downloaded=durable)
            )
        await start_engine(ctx)
        await wait_for(all_settled(ctx))
        assert gw.downloads_started == [(8, CHUNK * 3)]
        (b,) = await blobs(ctx)
        assert (settings.storage_dir / b.storage_path).read_bytes() == content


async def test_orphan_partials_removed_on_start(settings):
    gw = FakeGateway()
    async with running_container(settings, gw) as ctx:
        orphan = settings.storage_dir / "tmp" / "999.part"
        orphan.write_bytes(b"x")
        await start_engine(ctx)
        assert not orphan.exists()


async def test_flood_wait_pauses_all_and_recovers(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 1, FakeFile(1, "a.pdf", b"a" * 100))
    gw.post(-1, 2, FakeFile(2, "b.pdf", b"b" * 100))
    gw.flood_on_next["download"] = 1
    async with running_container(settings, gw) as ctx:
        await scan_all(ctx, -1)
        await start_engine(ctx)

        async def flooded():
            return ctx.engine.state == EngineState.FLOOD_WAIT

        await wait_for(flooded, timeout=3)
        assert ctx.engine.status()["flood_wait_seconds"] >= 0
        await wait_for(all_settled(ctx))
        docs = await all_docs(ctx)
        assert all(d.status == DocStatus.DONE for d in docs)
        assert all(d.attempts == 0 for d in docs)  # flood waits don't count


async def test_expired_file_reference_refetches_message(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    content = os.urandom(CHUNK * 3)
    gw.post(-1, 1, FakeFile(1, "a.pdf", content))
    gw.expire_ref_after_bytes = CHUNK
    async with running_container(settings, gw) as ctx:
        await scan_all(ctx, -1)
        await start_engine(ctx)
        await wait_for(all_settled(ctx))
        doc = (await all_docs(ctx))[0]
        assert doc.status == DocStatus.DONE and doc.attempts == 0
        assert gw.get_message_calls == 2
        assert gw.downloads_started == [(1, 0), (1, CHUNK)]


async def test_connection_drop_is_not_an_attempt(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    content = os.urandom(CHUNK * 3)
    gw.post(-1, 1, FakeFile(1, "a.pdf", content))
    gw.fail_download_after_bytes = CHUNK * 2
    async with running_container(settings, gw) as ctx:
        await scan_all(ctx, -1)
        await start_engine(ctx)
        await wait_for(all_settled(ctx))
        doc = (await all_docs(ctx))[0]
        assert doc.status == DocStatus.DONE and doc.attempts == 0
        assert gw.downloads_started[-1] == (1, CHUNK * 2)


async def test_deleted_message_falls_back_to_other_appearance(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.add_chat(-2, "B")
    f = FakeFile(1, "a.pdf", b"abc")
    gw.post(-1, 1, f)
    gw.post(-2, 9, f)
    gw.post(-1, 2, FakeFile(2, "gone.pdf", b"zzz"))
    async with running_container(settings, gw) as ctx:
        await scan_all(ctx, -1, -2)
        del gw.chats[-2].messages[9]  # newest appearance deleted
        del gw.chats[-1].messages[2]  # only appearance deleted
        await start_engine(ctx)
        await wait_for(all_settled(ctx))
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        assert by_tg[1].status == DocStatus.DONE
        assert by_tg[2].status == DocStatus.FAILED and "حُذفت" in by_tg[2].last_error


async def test_errors_retry_then_fail_after_max_attempts(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 1, FakeFile(1, "a.pdf", b"abc"))
    gw.always_fail_ids.add(1)
    async with running_container(settings, gw) as ctx:
        await ctx.app_settings.update({"max_attempts": 3})
        await scan_all(ctx, -1)
        await start_engine(ctx)
        await wait_for(all_settled(ctx))
        doc = (await all_docs(ctx))[0]
        assert doc.status == DocStatus.FAILED and doc.attempts == 3
        assert "simulated" in doc.last_error

        gw.always_fail_ids.clear()
        assert await ctx.engine.retry(doc.id)
        await wait_for(all_settled(ctx))
        assert (await all_docs(ctx))[0].status == DocStatus.DONE


async def test_disk_full_pauses_without_failing(settings, monkeypatch):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 1, FakeFile(1, "a.pdf", b"a" * 5000))
    Usage = namedtuple("Usage", "total used free")
    free = {"value": 10}
    monkeypatch.setattr(downloader.shutil, "disk_usage", lambda p: Usage(1, 1, free["value"]))
    async with running_container(settings, gw) as ctx:
        await scan_all(ctx, -1)
        await start_engine(ctx)

        async def full():
            return ctx.engine.state == EngineState.DISK_FULL

        await wait_for(full)
        await asyncio.sleep(0.1)
        doc = (await all_docs(ctx))[0]
        assert doc.status == DocStatus.PENDING and doc.attempts == 0

        free["value"] = 10**9
        await ctx.engine.set_paused(False)  # the UI's "resume" clears disk-full
        await wait_for(all_settled(ctx))
        assert (await all_docs(ctx))[0].status == DocStatus.DONE


async def test_pause_cancel_and_priority(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    for i in range(1, 5):
        gw.post(-1, i, FakeFile(i, f"{i}.pdf", bytes([i]) * 10))
    async with running_container(settings, gw) as ctx:
        await scan_all(ctx, -1)
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        await ctx.app_settings.update({"workers": 1})
        await ctx.engine.set_paused(True)
        await ctx.engine.start()
        await asyncio.sleep(0.2)
        assert ctx.engine.state == EngineState.PAUSED
        assert gw.downloads_started == []

        assert await ctx.engine.cancel(by_tg[2].id)
        assert await ctx.engine.bump_priority(by_tg[4].id)
        await ctx.engine.set_paused(False)
        await wait_for(all_settled(ctx))
        order = [tg for tg, _ in gw.downloads_started]
        assert order[0] == 4 and 2 not in order
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        assert by_tg[2].status == DocStatus.AVAILABLE  # cancelled: back to the catalog


async def test_paused_state_survives_restart(settings):
    gw = FakeGateway()
    async with running_container(settings, gw) as ctx:
        await ctx.engine.start()
        await ctx.engine.set_paused(True)
    async with running_container(settings, gw) as ctx:
        await ctx.engine.start()
        assert ctx.engine.paused is True
