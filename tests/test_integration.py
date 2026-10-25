"""End to end with the fake client: scan -> queue -> download -> dedup ->
restart mid-download -> resume -> rescan finds only new posts."""

import hashlib
import os

import pytest

from tglib.db.models import DocStatus
from tglib.engine import downloader
from tglib.engine.downloader import CHUNK
from fake_telegram import FakeFile, FakeGateway
from helpers import all_appearances, all_docs, running_container, track
from test_download import blobs, library_files, wait_for


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(downloader, "PERSIST_BYTES", CHUNK)
    monkeypatch.setattr(downloader, "DISK_MARGIN", 0)


async def test_full_lifecycle(settings):
    big = os.urandom(CHUNK * 10 + 77)
    small = os.urandom(5000)
    gw = FakeGateway()
    gw.add_chat(-1001, "مكتبة")
    gw.add_chat(-1002, "قناة أخرى")
    gw.post(-1001, 1, FakeFile(1, "موسوعة.pdf", big))
    gw.post(-1001, 2, FakeFile(2, "رسالة.pdf", small))
    gw.post(-1002, 10, FakeFile(1, "موسوعة.pdf", big))  # forward of 1
    gw.post(-1002, 11, FakeFile(3, "نسخة مرفوعة من جديد.pdf", small))  # re-upload of 2
    gw.download_delay = 0.02

    # --- first run: scan both chats, start downloading, "crash" mid-way
    async with running_container(settings, gw, loops=True) as ctx:
        await track(ctx, -1001)
        await track(ctx, -1002)
        ctx.scanner.enqueue(-1001)
        ctx.scanner.enqueue(-1002)

        async def big_half_done():
            docs = {d.tg_document_id: d for d in await all_docs(ctx)}
            return 1 in docs and docs[1].bytes_downloaded >= CHUNK * 4

        await wait_for(big_half_done, timeout=20)
        docs = await all_docs(ctx)
        assert len(docs) == 3  # forward folded into one document
        assert len(await all_appearances(ctx)) == 4

    # --- second run: resumes the partial file instead of restarting
    gw.download_delay = 0
    starts_before = len(gw.downloads_started)
    async with running_container(settings, gw, loops=True) as ctx:

        async def settled():
            ds = await all_docs(ctx)
            return all(d.status in (DocStatus.DONE, DocStatus.DUPLICATE) for d in ds)

        await wait_for(settled, timeout=20)
        resumed = [off for tg, off in gw.downloads_started[starts_before:] if tg == 1]
        assert resumed and resumed[0] >= CHUNK * 4

        docs = {d.tg_document_id: d for d in await all_docs(ctx)}
        assert docs[1].status == DocStatus.DONE
        assert {docs[2].status, docs[3].status} == {DocStatus.DONE, DocStatus.DUPLICATE}
        assert docs[2].blob_id == docs[3].blob_id

        stored = {b.sha256: settings.storage_dir / b.storage_path for b in await blobs(ctx)}
        assert stored[hashlib.sha256(big).hexdigest()].read_bytes() == big
        assert stored[hashlib.sha256(small).hexdigest()].read_bytes() == small
        assert len(library_files(settings)) == 2  # one copy per unique content

        # --- a new post appears; a rescan picks up only that one
        gw.post(-1001, 3, FakeFile(4, "جديد.epub", b"new book"))
        await ctx.scanner.scan(-1001)
        assert gw.scan_calls[-1] == (-1001, 2)
        await wait_for(settled, timeout=10)
        assert len(await all_docs(ctx)) == 4
        assert len(library_files(settings)) == 3
