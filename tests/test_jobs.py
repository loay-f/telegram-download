"""Job claiming and document status transitions in the download engine."""

import asyncio
from datetime import timedelta

from sqlalchemy import update

from tglib.db.models import Document, DocStatus, utcnow
from fake_telegram import FakeFile, FakeGateway
from helpers import all_docs, running_container, track


async def seeded(settings, n=6):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    for i in range(1, n + 1):
        gw.post(-1, i, FakeFile(i, f"{i}.pdf", bytes([i]) * 10))
    return gw


async def by_tg(ctx):
    return {d.tg_document_id: d for d in await all_docs(ctx)}


async def test_concurrent_claims_never_hand_out_the_same_job(settings):
    gw = await seeded(settings, n=6)
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1)
        await ctx.scanner.scan(-1)
        jobs = await asyncio.gather(*(ctx.engine._claim() for _ in range(10)))
        claimed = [j.id for j in jobs if j is not None]
        assert len(claimed) == 6 and len(set(claimed)) == 6
        assert all(d.status == DocStatus.DOWNLOADING for d in await all_docs(ctx))


async def test_claim_order_priority_then_first_seen_and_backoff(settings):
    gw = await seeded(settings, n=4)
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1)
        await ctx.scanner.scan(-1)
        docs = await by_tg(ctx)
        base = utcnow() - timedelta(hours=1)
        async with ctx.db.write() as s:
            for tg, d in docs.items():
                await s.execute(
                    update(Document).where(Document.id == d.id).values(first_seen_at=base + timedelta(minutes=tg))
                )
            # Waiting out a backoff: not claimable yet.
            await s.execute(
                update(Document)
                .where(Document.id == docs[1].id)
                .values(next_attempt_at=utcnow() + timedelta(hours=1))
            )
        await ctx.engine.bump_priority(docs[4].id)
        order = []
        while (job := await ctx.engine._claim()) is not None:
            order.append(job.tg_document_id)
        assert order == [4, 2, 3]


async def test_status_transitions(settings):
    gw = await seeded(settings, n=3)
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1)
        await ctx.scanner.scan(-1)
        docs = await by_tg(ctx)
        eng = ctx.engine

        # pending -> available by cancel; available -> pending by retry
        assert await eng.cancel(docs[1].id)
        assert (await by_tg(ctx))[1].status == DocStatus.AVAILABLE
        assert not await eng.cancel(docs[1].id)
        assert await eng.retry(docs[1].id)
        assert (await by_tg(ctx))[1].status == DocStatus.PENDING

        # pending can't be "retried"; done can't be cancelled or retried
        assert not await eng.retry(docs[2].id)
        async with ctx.db.write() as s:
            await s.execute(update(Document).where(Document.id == docs[3].id).values(status=DocStatus.DONE))
        assert not await eng.cancel(docs[3].id)
        assert not await eng.retry(docs[3].id)

        # failed -> pending via retry-all, attempts reset
        async with ctx.db.write() as s:
            await s.execute(
                update(Document).where(Document.id == docs[2].id).values(status=DocStatus.FAILED, attempts=5)
            )
        assert await eng.retry_all_failed() == 1
        d2 = (await by_tg(ctx))[2]
        assert d2.status == DocStatus.PENDING and d2.attempts == 0


async def test_startup_resets_interrupted_downloads(settings):
    gw = await seeded(settings, n=2)
    async with running_container(settings, gw) as ctx:
        await track(ctx, -1)
        await ctx.scanner.scan(-1)
        await ctx.engine._claim()
    async with running_container(settings, gw) as ctx:
        await ctx.engine.start()
        await ctx.engine.set_paused(True)  # keep workers from picking them up
        statuses = {d.status for d in await all_docs(ctx)}
        assert DocStatus.DOWNLOADING not in statuses


async def test_failed_original_releases_its_name_duplicates(settings):
    gw = FakeGateway()
    gw.add_chat(-1, "A")
    gw.post(-1, 1, FakeFile(1, "same name.pdf", b"a" * 10))
    gw.post(-1, 2, FakeFile(2, "same_name.pdf", b"b" * 10))
    async with running_container(settings, gw) as ctx:
        await ctx.app_settings.update({"max_attempts": 1})
        await track(ctx, -1)
        await ctx.scanner.scan(-1)
        docs = await by_tg(ctx)
        original = next(d for d in docs.values() if d.status == DocStatus.PENDING)
        dup = next(d for d in docs.values() if d.status == DocStatus.DUPLICATE)
        gw.always_fail_ids.add(original.tg_document_id)
        await ctx.engine.start()

        async def dup_downloaded():
            d = (await by_tg(ctx))[dup.tg_document_id]
            return d.status == DocStatus.DONE

        end = asyncio.get_running_loop().time() + 10
        while not await dup_downloaded():
            assert asyncio.get_running_loop().time() < end, "duplicate never got its own chance"
            await asyncio.sleep(0.05)
        assert (await by_tg(ctx))[original.tg_document_id].status == DocStatus.FAILED
