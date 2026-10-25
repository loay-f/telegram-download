"""Scans tracked chats for documents, one chat at a time, plus the periodic
rescan scheduler."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import timedelta

from sqlalchemy import select, update

from tglib.db.models import Chat, utcnow
from tglib.db.session import Database
from tglib.engine.account import TelegramAccount
from tglib.engine.catalog import Filters, record_message
from tglib.engine.errors import ChatUnavailable, FloodWait, NotAuthorized, NotConnected
from tglib.engine.events import EventBus
from tglib.engine.settings_store import SettingsStore
from tglib.engine.telegram import DocMessage

log = logging.getLogger(__name__)

BATCH = 100


@dataclass
class ScanProgress:
    chat_id: int
    title: str
    messages: int = 0
    new_documents: int = 0
    flood_wait_until: float | None = None  # epoch seconds
    started_at: float = field(default_factory=time.time)

    def public(self) -> dict:
        d = asdict(self)
        d["flood_wait_seconds"] = (
            max(0, round(self.flood_wait_until - time.time())) if self.flood_wait_until else 0
        )
        del d["flood_wait_until"]
        return d


class Scanner:
    def __init__(
        self,
        db: Database,
        account: TelegramAccount,
        settings: SettingsStore,
        events: EventBus,
        on_new_documents: Callable[[], None] = lambda: None,
    ):
        self._db = db
        self._account = account
        self._settings = settings
        self._events = events
        self._on_new = on_new_documents
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._queued: set[int] = set()
        self.current: ScanProgress | None = None
        self._tasks: list[asyncio.Task] = []

    # --- public API ---
    def enqueue(self, chat_id: int) -> bool:
        if chat_id in self._queued or (self.current and self.current.chat_id == chat_id):
            return False
        self._queued.add(chat_id)
        self._queue.put_nowait(chat_id)
        self._publish()
        return True

    def queued_ids(self) -> list[int]:
        return list(self._queued)

    def status(self) -> dict:
        return {
            "current": self.current.public() if self.current else None,
            "queued": len(self._queued),
        }

    async def enqueue_all_tracked(self) -> int:
        async with self._db.read() as s:
            ids = (await s.execute(select(Chat.id).where(Chat.tracked.is_(True)))).scalars().all()
        return sum(self.enqueue(i) for i in ids)

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._worker(), name="scanner"),
            asyncio.create_task(self._scheduler(), name="scan-scheduler"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # --- loops ---
    async def _worker(self) -> None:
        while True:
            chat_id = await self._queue.get()
            await self._account.wait_ready()
            try:
                await self.scan(chat_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Scan of chat %s crashed", chat_id)
            finally:
                self._queued.discard(chat_id)
                self.current = None
                self._publish()

    async def _scheduler(self) -> None:
        """Every minute, queue tracked chats whose last scan is older than the
        configured interval."""
        while True:
            try:
                if self._account.ready:
                    hours = self._settings.current.scan_interval_hours
                    cutoff = utcnow() - timedelta(hours=hours)
                    async with self._db.read() as s:
                        ids = (
                            await s.execute(
                                select(Chat.id).where(
                                    Chat.tracked.is_(True),
                                    (Chat.last_scan_at.is_(None)) | (Chat.last_scan_at < cutoff),
                                )
                            )
                        ).scalars().all()
                    for i in ids:
                        self.enqueue(i)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Scan scheduler error")
            await asyncio.sleep(60)

    # --- the scan itself ---
    async def scan(self, chat_id: int) -> None:
        async with self._db.read() as s:
            chat = await s.get(Chat, chat_id)
        if chat is None or not chat.tracked:
            return

        gw = self._account.gateway
        progress = self.current = ScanProgress(chat_id=chat_id, title=chat.title or "")
        self._publish()
        min_id = chat.last_scanned_msg_id
        max_seen = min_id
        offset_id = 0  # newest-first: resume below the lowest id handled so far
        batch: list[DocMessage] = []
        error: str | None = None
        log.info("Scanning %s (%s) from message %s", chat.title, chat_id, min_id)

        try:
            while True:
                try:
                    async for dm in gw.iter_document_messages(chat_id, min_id, offset_id):
                        batch.append(dm)
                        max_seen = max(max_seen, dm.msg_id)
                        offset_id = dm.msg_id
                        progress.messages += 1
                        if len(batch) >= BATCH:
                            await self._flush(chat_id, batch, progress)
                    break
                except FloodWait as e:
                    await self._flush(chat_id, batch, progress)
                    log.warning("Flood wait %ss while scanning %s", e.seconds, chat_id)
                    progress.flood_wait_until = time.time() + e.seconds
                    self._publish()
                    await asyncio.sleep(e.seconds)
                    progress.flood_wait_until = None
                    self._publish()
            await self._flush(chat_id, batch, progress)
        except ChatUnavailable as e:
            error = "لا يمكن الوصول إلى هذه المحادثة (ربما غادرتها أو أصبحت خاصة)."
            log.warning("Chat %s unavailable: %s", chat_id, e)
        except NotAuthorized:
            self._account.mark_unauthorized()
            self._requeue_later(chat_id)
            return
        except (NotConnected, ConnectionError, OSError) as e:
            log.warning("Scan of %s interrupted by connection error: %s", chat_id, e)
            self._requeue_later(chat_id)
            return

        async with self._db.write() as s:
            values = {"last_scan_at": utcnow(), "last_scan_error": error}
            if error is None:
                # Only after a complete pass: an interrupted scan reruns from
                # the old position and inserts are idempotent.
                values["last_scanned_msg_id"] = max_seen
            await s.execute(update(Chat).where(Chat.id == chat_id).values(**values))
        log.info(
            "Scan of %s done: %s messages, %s new documents",
            chat_id, progress.messages, progress.new_documents,
        )
        self._events.publish("scan_done", {"chat_id": chat_id, **progress.public()})

    def _requeue_later(self, chat_id: int, delay: float = 60) -> None:
        loop = asyncio.get_running_loop()
        loop.call_later(delay, self.enqueue, chat_id)

    async def _flush(self, chat_id: int, batch: list[DocMessage], progress: ScanProgress) -> None:
        if not batch:
            return
        new = 0
        async with self._db.write() as s:
            chat = await s.get(Chat, chat_id)
            filters = Filters.for_chat(chat, self._settings.current)
            for dm in batch:
                outcome = await record_message(s, chat, dm, filters)
                if outcome not in ("known", "appearance"):
                    new += 1
        batch.clear()
        progress.new_documents += new
        self._publish()
        if new:
            self._on_new()

    def _publish(self) -> None:
        self._events.publish("scan", self.status())
