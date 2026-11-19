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
    sheet = FakeFile(9, "table.xlsx", b"j" * 10, mime="application/vnd.ms-excel")  # not a default type
    gw.post(-1001, 1, sheet)
    gw.post(-1001, 2, FakeFile(10, "huge.pdf", b"h" * 3 * 1024 * 1024))
    gw.post(-1002, 1, sheet)
    async with running_container(settings, gw) as ctx:
        await ctx.app_settings.update({"max_size_mb": 2})
        await track(ctx, -1001)
        await ctx.scanner.scan(-1001)
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        assert by_tg[9].status == DocStatus.SKIPPED and by_tg[9].last_error == "filtered:ext"
        assert by_tg[10].status == DocStatus.SKIPPED and by_tg[10].last_error == "filtered:size"

        # Chat B allows spreadsheets: the same document becomes downloadable.
        await track(ctx, -1002)
        async with ctx.db.write() as s:
            (await s.get(Chat, -1002)).allowed_exts = "xlsx"
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


# ------------------------------------------- photos, videos and audio
PHOTO = FakeFile(21, "p.jpg", b"p" * 10, mime="image/jpeg", kind="media")
VIDEO = FakeFile(22, "clip.mp4", b"v" * 30, mime="video/mp4", kind="media")
SONG = FakeFile(23, "song.mp3", b"s" * 20, mime="audio/mpeg", kind="audio")


def positions(chat: Chat) -> tuple[int, int, int]:
    return chat.last_scanned_msg_id, chat.media_scanned_msg_id, chat.audio_scanned_msg_id


async def test_photos_videos_and_audio_are_found_by_default(settings):
    gw = library_gateway()
    gw.post(-1001, 1, FakeFile(20, "book.pdf", b"b"))
    gw.post(-1001, 2, PHOTO, caption="صورة الغلاف")
    gw.post(-1001, 3, VIDEO)
    gw.post(-1001, 4, SONG)
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001)
        await ctx.scanner.scan(-1001)
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        assert set(by_tg) == {20, 21, 22, 23} and all(d.status == DocStatus.PENDING for d in by_tg.values())
        # A photo has no name of its own: it gets one that says what and when.
        assert by_tg[21].original_name == "photo_2024-01-01_2.jpg" and by_tg[21].ext == ".jpg"
        assert by_tg[22].original_name == "clip.mp4"
        # Each kind is a pass of its own, with its own position.
        assert gw.kind_scan_calls == [(-1001, "media", 0), (-1001, "audio", 0)]
        assert positions(await get_chat(ctx, -1001)) == (1, 3, 4)

        gw.post(-1001, 7, FakeFile(27, "p2.jpg", b"q", mime="image/jpeg", kind="media"))
        await ctx.scanner.scan(-1001)
        assert gw.scan_calls[-1] == (-1001, 1)
        assert gw.kind_scan_calls[2:] == [(-1001, "media", 3), (-1001, "audio", 4)]
        assert positions(await get_chat(ctx, -1001)) == (1, 7, 4)
        assert len(await all_docs(ctx)) == 5


async def test_only_the_kinds_a_chat_accepts_are_scanned(settings):
    gw = library_gateway()
    gw.post(-1001, 1, FakeFile(20, "book.pdf", b"b"))
    gw.post(-1001, 2, PHOTO)
    gw.post(-1001, 4, SONG)
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001)
        async with ctx.db.write() as s:
            (await s.get(Chat, -1001)).allowed_exts = "pdf,epub"
        await ctx.scanner.scan(-1001)
        assert gw.kind_scan_calls == [] and {d.tg_document_id for d in await all_docs(ctx)} == {20}

        # Pictures are allowed later. Those already in the chat are listed,
        # not downloaded: automatic download is for what is posted from now on.
        async with ctx.db.write() as s:
            (await s.get(Chat, -1001)).allowed_exts = "pdf,jpg"
        await ctx.scanner.scan(-1001)
        assert gw.kind_scan_calls == [(-1001, "media", 0)]
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        assert set(by_tg) == {20, 21} and by_tg[21].status == DocStatus.AVAILABLE

        gw.post(-1001, 9, FakeFile(29, "new.jpg", b"n", mime="image/jpeg", kind="media"))
        await ctx.scanner.scan(-1001)
        by_tg = {d.tg_document_id: d for d in await all_docs(ctx)}
        assert by_tg[29].status == DocStatus.PENDING and by_tg[21].status == DocStatus.AVAILABLE


async def test_an_interrupted_pass_over_photos_continues_too(settings, monkeypatch):
    monkeypatch.setattr("tglib.engine.scanner.BATCH", 2)
    gw = library_gateway()
    gw.post(-1001, 1, FakeFile(20, "book.pdf", b"b"))
    for i in range(2, 9):
        gw.post(-1001, i, FakeFile(100 + i, f"p{i}.jpg", bytes([i]), mime="image/jpeg", kind="media"))
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1001)
        gw.fail_scan_after = 5  # the book, then four photos, then the connection drops
        await ctx.scanner.scan(-1001)
        chat = await get_chat(ctx, -1001)
        assert positions(chat) == (1, 0, 0)  # files are done; photos are not
        assert (chat.media_top_msg_id, chat.media_resume_msg_id) == (8, 5)

        await ctx.scanner.scan(-1001)
        assert gw.scan_calls[-1] == (-1001, 1)  # nothing is asked twice
        assert gw.kind_scan_calls[-3:] == [(-1001, "media", 0), (-1001, "media", 8), (-1001, "audio", 0)]
        chat = await get_chat(ctx, -1001)
        assert positions(chat) == (1, 8, 0) and chat.media_top_msg_id is None and chat.media_resume_msg_id is None
        assert len(await all_docs(ctx)) == 8


async def test_files_skipped_by_older_settings_come_back_at_start(settings):
    gw = library_gateway()
    gw.post(-1001, 1, FakeFile(30, "scan.jpg", b"j" * 10, mime="image/jpeg"))  # a picture sent as a file
    async with running_container(settings, gw) as ctx:
        await ctx.app_settings.update({"allowed_exts": "pdf"})
        await track(ctx, -1001, auto_download=False)
        await ctx.scanner.scan(-1001)
        assert (await all_docs(ctx))[0].last_error == "filtered:ext"
        await ctx.app_settings.update({"allowed_exts": "pdf,jpg"})  # as a new version's defaults would
    async with running_container(settings, gw, loops=True) as ctx:
        doc = (await all_docs(ctx))[0]
        assert doc.status == DocStatus.AVAILABLE and doc.last_error is None
