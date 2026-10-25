"""Database engines.

SQLite allows one writer at a time. To avoid "database is locked" errors from
deferred transactions upgrading to write locks, all writes go through a
dedicated single-connection engine that opens transactions with
BEGIN IMMEDIATE. Reads use a separate pool and run concurrently (WAL mode).

Rules: never keep a session open across a Telegram/network await. Session
cleanup (commit/rollback/close) always runs to completion even if the task is
cancelled; otherwise the single write connection could leak and every later
write would stall.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypeVar

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

T = TypeVar("T")


async def complete(aw: Awaitable[T]) -> T:
    """Await `aw` to completion even if the caller is cancelled meanwhile;
    the cancellation is re-raised afterwards."""
    task = asyncio.ensure_future(aw)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return task.result()


def _configure(engine: AsyncEngine, immediate: bool) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn, _record):
        # Let SQLAlchemy (not the driver) control BEGIN.
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


class Database:
    def __init__(self, db_path: Path):
        url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        self.read_engine = create_async_engine(url, pool_size=5, max_overflow=5)
        self.write_engine = create_async_engine(
            url, pool_size=1, max_overflow=0, pool_timeout=300
        )
        _configure(self.read_engine, immediate=False)
        _configure(self.write_engine, immediate=True)
        self._read = async_sessionmaker(self.read_engine, expire_on_commit=False)
        self._write = async_sessionmaker(self.write_engine, expire_on_commit=False)

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        s = self._read()
        try:
            yield s
        finally:
            await complete(s.close())

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncSession]:
        """Transaction that commits on success and rolls back on error."""
        s = self._write()
        try:
            yield s
            await complete(s.commit())
        except BaseException:
            await complete(s.rollback())
            raise
        finally:
            await complete(s.close())

    async def dispose(self) -> None:
        await self.read_engine.dispose()
        await self.write_engine.dispose()
