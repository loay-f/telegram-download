"""Library counters for the dashboard."""

import asyncio
import time
from dataclasses import dataclass

from sqlalchemy import case, func, select

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
        self._writes = -1  # `db.writes` the value was computed at
        self._lock = asyncio.Lock()

    async def get(self) -> dict:
        async with self._lock:
            # Nothing written since: the numbers are the same.
            stale = time.monotonic() - self._at > STATS_TTL and self._db.writes != self._writes
            if self._value is None or stale:
                self._writes = self._db.writes
                self._value = await compute_stats(self._db)
                self._at = time.monotonic()
            return self._value


@dataclass(frozen=True)
class ChatCount:
    files: int  # posts of a file in the chat
    downloaded: int  # ... whose file is in the library (or is a duplicate of one that is)
    pending: int  # ... queued or downloading
    available: int  # ... listed, waiting to be picked
    stored: int  # distinct files of the chat that are on disk


class ChatCounts:
    """Per-chat file counters, for the chats page and the library's list of
    chats. They take a pass over every post of every file, so they are kept
    until something is written that could change them (`Database.version`)."""

    def __init__(self, db: Database):
        self._db = db
        self._value: dict[int, ChatCount] | None = None
        self._version = -1
        self._lock = asyncio.Lock()

    async def get(self) -> dict[int, ChatCount]:
        async with self._lock:
            # Read before computing: a write that lands meanwhile is seen next time.
            version = self._db.version
            if self._value is None or version != self._version:
                self._value = await self._compute()
                self._version = version
            return self._value

    async def _compute(self) -> dict[int, ChatCount]:
        downloaded = case((Document.status.in_((DocStatus.DONE, DocStatus.DUPLICATE)), 1), else_=0)
        pending = case((Document.status.in_((DocStatus.PENDING, DocStatus.DOWNLOADING)), 1), else_=0)
        available = case((Document.status == DocStatus.AVAILABLE, 1), else_=0)
        async with self._db.read() as s:
            rows = await s.execute(
                select(
                    Appearance.chat_id,
                    func.count(),
                    func.sum(downloaded),
                    func.sum(pending),
                    func.sum(available),
                    func.count(func.distinct(Document.blob_id)),
                )
                .join(Document, Document.id == Appearance.document_id)
                .group_by(Appearance.chat_id)
            )
            return {
                chat_id: ChatCount(files, int(done or 0), int(queued or 0), int(listed or 0), stored)
                for chat_id, files, done, queued, listed, stored in rows
            }
