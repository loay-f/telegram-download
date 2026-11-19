"""Mirrors the account's chats (channels, groups, private chats, bots and
Saved Messages) into the `chats` table.

Fetching every dialog of a big account takes many requests and Telegram
often makes us wait between them, so the sync runs in the background: the
chats page always answers from the table and fills in as batches arrive."""

import asyncio
import logging
import time
from collections.abc import Callable

from sqlalchemy import select, update

from tglib.db.models import Chat
from tglib.db.session import Database
from tglib.engine.account import TelegramAccount
from tglib.engine.errors import FloodWait, NotAuthorized, TelegramError
from tglib.engine.events import EventBus
from tglib.engine.telegram import DialogInfo, TelegramGateway

log = logging.getLogger(__name__)

REFRESH_SECONDS = 600  # the list is re-synced when older than this
BATCH = 100  # Telegram returns dialogs 100 per request
RETRY_SECONDS = 60  # pause before a failed sync is retried automatically


class SyncAborted(Exception):
    """The account logged out or changed while the sync was running."""


async def _save_batch(db: Database, batch: list[DialogInfo]) -> None:
    async with db.write() as s:
        ids = [d.id for d in batch]
        existing = {c.id: c for c in (await s.execute(select(Chat).where(Chat.id.in_(ids)))).scalars()}
        for d in batch:
            chat = existing.get(d.id)
            if chat is None:
                s.add(
                    Chat(
                        id=d.id, title=d.title, username=d.username, type=d.type,
                        tracked=False, last_scanned_msg_id=0, is_member=True,
                    )
                )
            else:
                chat.title, chat.username, chat.type, chat.is_member = d.title, d.username, d.type, True


async def sync_dialogs(
    db: Database,
    gateway: TelegramGateway,
    on_progress: Callable[[int], None] = lambda n: None,
    check: Callable[[], None] = lambda: None,
) -> int:
    """Saves dialogs in batches as they arrive, then marks the chats that
    weren't seen as left. `check` raises to stop before the next write."""
    seen: set[int] = set()
    batch: list[DialogInfo] = []

    async def flush() -> None:
        check()
        await _save_batch(db, batch)
        batch.clear()
        on_progress(len(seen))

    async for d in gateway.iter_dialogs():
        if d.id in seen:
            continue
        seen.add(d.id)
        batch.append(d)
        if len(batch) >= BATCH:
            await flush()
    if batch:
        await flush()

    check()
    async with db.write() as s:
        members = (await s.execute(select(Chat.id).where(Chat.is_member.is_(True)))).scalars()
        gone = [cid for cid in members if cid not in seen]
        for i in range(0, len(gone), 500):  # stay under SQLite's bound-parameter limit
            await s.execute(update(Chat).where(Chat.id.in_(gone[i : i + 500])).values(is_member=False))
    on_progress(len(seen))
    return len(seen)


class DialogSync:
    """Runs dialog syncs in the background, one at a time."""

    def __init__(self, db: Database, account: TelegramAccount, events: EventBus):
        self._db = db
        self._account = account
        self._events = events
        self.synced_at = 0.0  # end of the last complete sync
        self.loaded = 0  # dialogs saved by the running (or last) sync
        self.error: str | None = None  # why the last sync failed, for the user
        self._task: asyncio.Task | None = None
        self._again = False  # a forced sync was asked for while one was running
        # After a failure, page loads don't retry before this (epoch seconds);
        # the refresh button still can.
        self._retry_after = 0.0

    @property
    def syncing(self) -> bool:
        return self._task is not None and not self._task.done()

    def stale(self) -> bool:
        return (
            time.time() - self.synced_at > REFRESH_SECONDS
            # Logged in (again) since the last sync: the list may be another account's.
            or self.synced_at < self._account.ready_at
        )

    def request(self, force: bool = False) -> None:
        """Starts a sync if the list is stale (or `force`) and none is running.
        A forced request during a sync runs another one right after it, so it
        sees changes made after the request."""
        if not self._account.ready:
            return
        if self.syncing:
            self._again = self._again or force
        elif force or (self.stale() and time.time() >= self._retry_after):
            self._task = asyncio.create_task(self._run(), name="dialog-sync")

    async def forget(self) -> None:
        """The account logged out: stop syncing and stop listing its chats
        (tracked ones stay listed) until the next login syncs again."""
        await self.stop()
        self.synced_at = 0.0
        self.loaded = 0
        self.error = None
        self._retry_after = 0.0
        async with self._db.write() as s:
            await s.execute(update(Chat).where(Chat.is_member.is_(True)).values(is_member=False))

    async def stop(self) -> None:
        self._again = False
        if self.syncing:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def status(self) -> dict:
        return {
            "syncing": self.syncing,
            "synced": self.synced_at > 0,
            "loaded": self.loaded,
            "error": self.error,
        }

    def _publish(self) -> None:
        self._events.publish("dialogs", self.status())

    async def _run(self) -> None:
        try:
            while True:
                self._again = False
                await self._sync_once()
                if not (self._again and self._account.ready):
                    return
        finally:
            self._task = None
            self._publish()

    async def _sync_once(self) -> None:
        gateway = self._account.gateway
        ready_at = self._account.ready_at

        def check() -> None:
            if not self._account.ready or self._account.ready_at != ready_at:
                raise SyncAborted()

        def progress(n: int) -> None:
            self.loaded = n
            self._publish()

        self.loaded = 0
        self.error = None
        self._publish()
        try:
            await sync_dialogs(self._db, gateway, progress, check)
            self.synced_at = time.time()
        except SyncAborted:
            log.info("Dialog sync stopped: the account logged out or changed")
        except NotAuthorized:
            # The session was revoked (e.g. terminated from another device):
            # go back to the login screen instead of showing a stale list.
            self.error = "انتهت جلسة تيليغرام. سجّل الدخول مرة أخرى."
            if self._account.ready and self._account.ready_at == ready_at:
                self._account.mark_unauthorized()
        except FloodWait as e:
            log.warning("Dialog sync delayed by flood wait: %ss", e.seconds)
            self.error = f"طلب تيليغرام الانتظار {e.seconds} ثانية قبل إكمال تحديث القائمة."
            self._retry_after = time.time() + max(RETRY_SECONDS, e.seconds)
        except TelegramError as e:
            log.warning("Dialog sync failed: %s", e)
            self.error = f"تعذّر جلب المحادثات من تيليغرام: {e}"
            self._retry_after = time.time() + RETRY_SECONDS
        except Exception:
            log.exception("Dialog sync failed")
            self.error = "تعذّر جلب المحادثات من تيليغرام."
            self._retry_after = time.time() + RETRY_SECONDS
