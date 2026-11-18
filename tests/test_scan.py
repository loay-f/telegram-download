from tglib.db.models import Chat, DocStatus
from fake_telegram import FakeFile, FakeGateway
from helpers import all_appearances, all_docs, get_chat, running_container, track


def library_gateway() -> FakeGateway:
    gw = FakeGateway()
    gw.add_chat(-1001, "A")
    gw.add_chat(-1002, "B")
    return gw


async def test_scan_populates_and_rescan_adds_nothing(settings):
    gw = library_gateway()
    gw.post(-1001, 1, FakeFile(100, "one.pdf", b"1" * 10), caption="first")
    gw.post(-1001, 2, FakeFile(101, "two.epub", b"2" * 20))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001)
        await ctx.scanner.scan(-1001)
        docs = await all_docs(ctx)
        assert {d.tg_document_id for d in docs} == {100, 101}
        assert all(d.status == DocStatus.PENDING for d in docs)
        assert (await get_chat(ctx, -1001)).last_scanned_msg_id == 2
        apps = await all_appearances(ctx)
        assert {(a.chat_id, a.msg_id) for a in apps} == {(-1001, 1), (-1001, 2)}
        assert next(a for a in apps if a.msg_id == 1).caption == "first"

        await ctx.scanner.scan(-1001)
        assert len(await all_docs(ctx)) == 2
        assert len(await all_appearances(ctx)) == 2

        # Force a full rescan from zero: still idempotent.
        async with ctx.db.write() as s:
            (await s.get(Chat, -1001)).last_scanned_msg_id = 0
        await ctx.scanner.scan(-1001)
        assert len(await all_docs(ctx)) == 2
        assert len(await all_appearances(ctx)) == 2


async def test_only_new_messages_are_fetched_after_first_pass(settings):
    gw = library_gateway()
    gw.post(-1001, 1, FakeFile(100, "one.pdf", b"1"))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001)
        await ctx.scanner.scan(-1001)
        gw.post(-1001, 5, FakeFile(105, "five.pdf", b"5"))
        await ctx.scanner.scan(-1001)
        assert gw.scan_calls[-1] == (-1001, 1)
        assert {d.tg_document_id for d in await all_docs(ctx)} == {100, 105}
        assert (await get_chat(ctx, -1001)).last_scanned_msg_id == 5


async def test_forwards_across_chats_are_one_document(settings):
    gw = library_gateway()
    book = FakeFile(500, "كتاب.pdf", b"x" * 100)
    gw.post(-1001, 1, book)
    gw.post(-1002, 77, book)  # forwarded: same Telegram document id
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001)
        await track(ctx, -1002)
        await ctx.scanner.scan(-1001)
        await ctx.scanner.scan(-1002)
        docs = await all_docs(ctx)
        assert len(docs) == 1
        apps = await all_appearances(ctx)
        assert {(a.chat_id, a.msg_id) for a in apps} == {(-1001, 1), (-1002, 77)}
        assert all(a.document_id == docs[0].id for a in apps)


async def test_reupload_with_same_name_and_size_is_duplicate(settings):
    gw = library_gateway()
    gw.post(-1001, 1, FakeFile(1, "تفسير_الطبري @chan.pdf", b"a" * 50))
    gw.post(-1002, 1, FakeFile(2, "تفسير الطبري.pdf", b"b" * 50))  # same size, same normalized name
    gw.post(-1002, 2, FakeFile(3, "تفسير الطبري.pdf", b"c" * 51))  # different size
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001)
        await track(ctx, -1002)
        await ctx.scanner.scan(-1001)
        await ctx.scanner.scan(-1002)
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        assert by_tg[1].status == DocStatus.PENDING
        assert by_tg[2].status == DocStatus.DUPLICATE
        assert by_tg[2].duplicate_of_id == by_tg[1].id
        assert by_tg[3].status == DocStatus.PENDING


async def test_filters_skip_and_per_chat_override(settings):
    gw = library_gateway()
    pic = FakeFile(9, "photo.jpg", b"j" * 10, mime="image/jpeg")
    gw.post(-1001, 1, pic)
    gw.post(-1001, 2, FakeFile(10, "huge.pdf", b"h" * 3 * 1024 * 1024))
    gw.post(-1002, 1, pic)
    async with running_container(settings, gw) as ctx:
        await ctx.app_settings.update({"max_size_mb": 2})
        await track(ctx, -1001)
        await ctx.scanner.scan(-1001)
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        assert by_tg[9].status == DocStatus.SKIPPED and by_tg[9].last_error == "filtered:ext"
        assert by_tg[10].status == DocStatus.SKIPPED and by_tg[10].last_error == "filtered:size"

        # Chat B allows images: the same document becomes downloadable.
        await track(ctx, -1002)
        async with ctx.db.write() as s:
            (await s.get(Chat, -1002)).allowed_exts = "jpg"
        await ctx.scanner.scan(-1002)
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        assert by_tg[9].status == DocStatus.PENDING


async def test_flood_wait_during_scan_resumes_where_it_stopped(settings):
    gw = library_gateway()
    for i in range(1, 6):
        gw.post(-1001, i, FakeFile(100 + i, f"b{i}.pdf", bytes([i])))
    gw.flood_on_next["scan"] = 1
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001)
        await ctx.scanner.scan(-1001)
        assert len(await all_docs(ctx)) == 5
        assert (await get_chat(ctx, -1001)).last_scanned_msg_id == 5


async def test_inaccessible_chat_records_error_and_keeps_position(settings):
    gw = library_gateway()
    gw.inaccessible_chats.add(-1001)
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001)
        await ctx.scanner.scan(-1001)
        chat = await get_chat(ctx, -1001)
        assert chat.last_scan_error and chat.last_scanned_msg_id == 0 and chat.last_scan_at


async def test_interrupted_scan_continues_below_where_it_stopped(settings, monkeypatch):
    monkeypatch.setattr("tglib.engine.scanner.BATCH", 2)
    gw = library_gateway()
    for i in range(1, 8):
        gw.post(-1001, i, FakeFile(100 + i, f"b{i}.pdf", bytes([i])))
    gw.fail_scan_after = 5  # 7..3 are read, then the connection drops
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001)
        await ctx.scanner.scan(-1001)
        # Two full batches were saved; the position is not final yet.
        assert {d.tg_document_id for d in await all_docs(ctx)} == {107, 106, 105, 104}
        chat = await get_chat(ctx, -1001)
        assert (chat.last_scanned_msg_id, chat.scan_top_msg_id, chat.scan_resume_msg_id) == (0, 7, 4)
        assert chat.last_scan_at is None

    gw.post(-1001, 9, FakeFile(109, "b9.pdf", b"9"))  # posted while the app was down
    async with running_container(settings, gw) as ctx:  # the app restarted
        await ctx.scanner.scan(-1001)
        # The rest of the old pass, then what was posted above it: nothing twice.
        assert gw.scan_calls[-2:] == [(-1001, 0), (-1001, 7)]
        assert gw.scan_offsets[-2:] == [4, 0]
        assert {d.tg_document_id for d in await all_docs(ctx)} == {101, 102, 103, 104, 105, 106, 107, 109}
        assert len(await all_appearances(ctx)) == 8
        chat = await get_chat(ctx, -1001)
        assert (chat.last_scanned_msg_id, chat.scan_top_msg_id, chat.scan_resume_msg_id) == (9, None, None)
        assert chat.last_scan_at and chat.last_scan_error is None


async def test_scan_interrupted_twice_still_finishes(settings, monkeypatch):
    monkeypatch.setattr("tglib.engine.scanner.BATCH", 2)
    gw = library_gateway()
    for i in range(1, 10):
        gw.post(-1001, i, FakeFile(100 + i, f"b{i}.pdf", bytes([i])))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001)
        gw.fail_scan_after = 4
        await ctx.scanner.scan(-1001)
        gw.fail_scan_after = 3
        await ctx.scanner.scan(-1001)
        chat = await get_chat(ctx, -1001)
        assert (chat.last_scanned_msg_id, chat.scan_top_msg_id, chat.scan_resume_msg_id) == (0, 9, 4)
        await ctx.scanner.scan(-1001)
        assert len(await all_docs(ctx)) == 9
        chat = await get_chat(ctx, -1001)
        assert (chat.last_scanned_msg_id, chat.scan_top_msg_id, chat.scan_resume_msg_id) == (9, None, None)
