"""Database engines.

SQLite allows one writer at a time. To avoid "database is locked" errors from
deferred transactions upgrading to write locks, all writes go through a
dedicated single-connection engine that opens transactions with
BEGIN IMMEDIATE. Reads use a separate pool and run concurrently (WAL mode).

Rule: never keep a session open across a Telegram/network await.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


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
        async with self._read() as s:
            yield s

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncSession]:
        """Transaction that commits on success and rolls back on error."""
        async with self._write() as s:
            async with s.begin():
                yield s

    async def dispose(self) -> None:
        await self.read_engine.dispose()
        await self.write_engine.dispose()
