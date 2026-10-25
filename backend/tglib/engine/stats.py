"""Library counters for the dashboard."""

import asyncio
import time

from sqlalchemy import func, select

from tglib.db.models import Appearance, Blob, Document, DocStatus
from tglib.db.session import Database

STATS_TTL = 3.0


async def compute_stats(db: Database) -> dict:
    async with db.read() as s:
        files, library_bytes = (
            await s.execute(select(func.count(Blob.id), func.coalesce(func.sum(Blob.size), 0)))
        ).one()
        by_status = {
            st: (n, size or 0, done or 0)
            for st, n, size, done in await s.execute(
                select(
                    Document.status,
                    func.count(),
                    func.sum(Document.size),
                    func.sum(Document.bytes_downloaded),
                ).group_by(Document.status)
            )
        }
        dup_never_downloaded = (
            await s.execute(
                select(func.coalesce(func.sum(Document.size), 0)).where(
                    Document.status == DocStatus.DUPLICATE, Document.bytes_downloaded == 0
                )
            )
        ).scalar()
        appearances = (await s.execute(select(func.count()).select_from(Appearance))).scalar()
        documents = sum(n for n, _, _ in by_status.values())

    def count(st):
        return by_status.get(st, (0, 0, 0))[0]

    remaining = sum(
        by_status.get(st, (0, 0, 0))[1] - by_status.get(st, (0, 0, 0))[2]
        for st in (DocStatus.PENDING, DocStatus.DOWNLOADING)
    )
    return {
        "files": files,
        "library_bytes": library_bytes,
        "documents": documents,
        "status_counts": {st: count(st) for st in DocStatus.ALL},
        "duplicates": count(DocStatus.DUPLICATE),
        # Duplicate content never stored twice.
        "space_saved_bytes": by_status.get(DocStatus.DUPLICATE, (0, 0, 0))[1],
        # Duplicates recognized before download (name + size).
        "bandwidth_saved_bytes": dup_never_downloaded,
        # Extra posts of an already-known file (forwards across chats).
        "repeat_posts": max(0, appearances - documents),
        "remaining_bytes": max(0, remaining),
    }


class StatsCache:
    def __init__(self, db: Database):
        self._db = db
        self._value: dict | None = None
        self._at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> dict:
        async with self._lock:
            if self._value is None or time.monotonic() - self._at > STATS_TTL:
                self._value = await compute_stats(self._db)
                self._at = time.monotonic()
            return self._value
