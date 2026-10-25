"""Download engine: N async workers pulling `pending` documents from the DB.

Crash safety:
- Data is fsynced before `bytes_downloaded` is persisted, so on resume the
  offset is min(.part size, bytes_downloaded) rounded down to the chunk size;
  bytes after the last durable point are discarded, never trusted.
- New content is moved into the library before the DB commit; a crash in
  between only causes a re-download that lands on the same path.
- Flood waits, disconnects, disk-full and pause release the job back to
  `pending` without counting an attempt.
"""

import asyncio
import errno
import hashlib
import logging
import os
import re
import shutil
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select, update

from tglib.db.models import Appearance, Blob, Document, DocStatus, Setting, utcnow
from tglib.db.session import Database
from tglib.engine.account import TelegramAccount
from tglib.engine.errors import ChatUnavailable, FileRefExpired, FloodWait, NotAuthorized, NotConnected
from tglib.engine.events import EventBus
from tglib.engine.settings_store import SettingsStore
from tglib.engine.telegram import DocMessage

log = logging.getLogger(__name__)

CHUNK = 512 * 1024  # Telegram's maximum request size; offsets stay aligned to it
PERSIST_BYTES = 8 * 1024 * 1024
PERSIST_SECONDS = 3.0
DISK_MARGIN = 200 * 1024 * 1024
OFFLINE_RETRY_SECONDS = 30
MAX_REF_REFRESH = 3
PAUSED_KEY = "engine_paused"

MSG_NO_SOURCE = "لم يعد الملف متاحاً: حُذفت الرسالة أو لم يعد الوصول إلى المحادثة ممكناً."
MSG_CANCELLED = "أُلغي يدوياً"


class EngineState:
    RUNNING = "running"
    IDLE = "idle"
    PAUSED = "paused"
    FLOOD_WAIT = "flood_wait"
    DISK_FULL = "disk_full"
    OFFLINE = "offline"
    WAITING_TELEGRAM = "waiting_telegram"


@dataclass
class Active:
    doc_id: int
    name: str
    size: int
    done: int
    started_offset: int
    speed: float = 0.0
    _sample_t: float = field(default_factory=time.monotonic)
    _sample_bytes: int = 0

    def sample(self) -> None:
        now = time.monotonic()
        dt = now - self._sample_t
        if dt >= 1.0:
            inst = (self.done - self._sample_bytes) / dt
            self.speed = inst if self.speed == 0 else 0.6 * inst + 0.4 * self.speed
            self._sample_t, self._sample_bytes = now, self.done

    def public(self) -> dict:
        return {"id": self.doc_id, "name": self.name, "size": self.size, "done": self.done, "speed": round(self.speed)}


@dataclass
class Job:
    id: int
    tg_document_id: int
    name: str
    ext: str | None
    size: int
    attempts: int
    bytes_downloaded: int


class _Interrupted(Exception):
    def __init__(self, reason: str):
        self.reason = reason  # "cancel" | "pause"


class _Release(Exception):
    """Put the job back to pending without counting an attempt."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(4 * 1024 * 1024):
            h.update(block)
    return h.hexdigest()


def resume_offset(part_size: int, durable_bytes: int, chunk: int = CHUNK) -> int:
    """Where to continue a partial download: the last durably recorded byte,
    never past the file's real size, aligned down to the chunk size."""
    usable = max(0, min(part_size, durable_bytes))
    return (usable // chunk) * chunk


def backoff_seconds(attempts: int) -> int:
    return min(30 * 2 ** max(0, attempts - 1), 3600)


_SAFE_EXT = re.compile(r"^\.[a-z0-9]{1,10}$")


def blob_relpath(sha: str, ext: str | None) -> str:
    ext = ext if ext and _SAFE_EXT.match(ext) else ""
    return f"library/{sha[:2]}/{sha}{ext}"


class DownloadEngine:
    def __init__(
        self,
        db: Database,
        account: TelegramAccount,
        settings: SettingsStore,
        events: EventBus,
        storage_dir: Path,
    ):
        self._db = db
        self._account = account
        self._settings = settings
        self._events = events
        self.storage_dir = storage_dir
        self.tmp_dir = storage_dir / "tmp"
        self.paused = False
        self.flood_until = 0.0
        self.offline_until = 0.0
        self.disk_full: str | None = None
        self.active: dict[int, Active] = {}
        self._cancel: set[int] = set()
        self._wake = asyncio.Event()
        self._finalize_lock = asyncio.Lock()
        self._workers: dict[int, asyncio.Task] = {}
        self._busy: set[int] = set()  # indexes of workers processing a job
        self._supervisor: asyncio.Task | None = None
        self._stopping = False

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        async with self._db.write() as s:
            await s.execute(
                update(Document)
                .where(Document.status == DocStatus.DOWNLOADING)
                .values(status=DocStatus.PENDING)
            )
            row = await s.get(Setting, PAUSED_KEY)
            self.paused = bool(row and row.value == "1")
        await self._cleanup_tmp()
        self._supervisor = asyncio.create_task(self._supervise(), name="download-supervisor")

    async def stop(self, grace: float = 8.0) -> None:
        """Lets active downloads save their progress and release their jobs at
        the next chunk; cancels whatever is still running after `grace`."""
        self._stopping = True
        self.wake()  # idle workers re-check _stopping and exit
        tasks = [self._supervisor] if self._supervisor else []
        busy = []
        for index, task in self._workers.items():
            (busy if index in self._busy else tasks).append(task)
        for t in tasks:
            t.cancel()
        if busy:
            await asyncio.wait(busy, timeout=grace)
        for t in busy:
            t.cancel()
        await asyncio.gather(*tasks, *busy, return_exceptions=True)
        self._workers.clear()

    async def _cleanup_tmp(self) -> None:
        """Delete .part files that no longer belong to a resumable document."""
        keep_statuses = (DocStatus.PENDING, DocStatus.DOWNLOADING, DocStatus.FAILED)
        async with self._db.read() as s:
            keep = set(
                (
                    await s.execute(
                        select(Document.tg_document_id).where(Document.status.in_(keep_statuses))
                    )
                ).scalars()
            )
        for p in self.tmp_dir.glob("*.part"):
            try:
                tg_id = int(p.stem)
            except ValueError:
                tg_id = None
            if tg_id not in keep:
                log.info("Removing orphan partial file %s", p.name)
                p.unlink(missing_ok=True)

    async def _supervise(self) -> None:
        """Keeps the number of workers equal to the `workers` setting."""
        while True:
            target = self._settings.current.workers
            for i in range(target):
                t = self._workers.get(i)
                if t is None or t.done():
                    if t is not None and not t.cancelled() and t.exception():
                        log.error("Download worker %s died", i, exc_info=t.exception())
                    self._workers[i] = asyncio.create_task(self._worker(i), name=f"download-{i}")
            for i in [i for i in self._workers if i >= target and self._workers[i].done()]:
                del self._workers[i]
            await asyncio.sleep(2)

    # ------------------------------------------------------------- control
    def wake(self) -> None:
        self._wake.set()

    async def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if not paused:
            self.disk_full = None
        async with self._db.write() as s:
            await s.merge(Setting(key=PAUSED_KEY, value="1" if paused else "0"))
        self.wake()
        self._publish_state()

    async def cancel(self, doc_id: int) -> bool:
        if doc_id in self.active:
            self._cancel.add(doc_id)
            return True
        async with self._db.write() as s:
            doc = await s.get(Document, doc_id)
            if doc is None or doc.status not in (DocStatus.PENDING, DocStatus.FAILED):
                return False
            doc.status = DocStatus.SKIPPED
            doc.last_error = MSG_CANCELLED
            await self._unlink_name_duplicates(s, doc.id)
            tg_id = doc.tg_document_id
        self._part_path(tg_id).unlink(missing_ok=True)
        return True

    async def retry(self, doc_id: int) -> bool:
        async with self._db.write() as s:
            doc = await s.get(Document, doc_id)
            if doc is None or doc.status not in (DocStatus.FAILED, DocStatus.SKIPPED):
                return False
            doc.status = DocStatus.PENDING
            doc.attempts = 0
            doc.last_error = None
            doc.next_attempt_at = None
        self.wake()
        return True

    async def retry_all_failed(self) -> int:
        async with self._db.write() as s:
            res = await s.execute(
                update(Document)
                .where(Document.status == DocStatus.FAILED)
                .values(status=DocStatus.PENDING, attempts=0, last_error=None, next_attempt_at=None)
            )
        self.wake()
        return res.rowcount or 0

    async def bump_priority(self, doc_id: int) -> bool:
        async with self._db.write() as s:
            top = (await s.execute(select(func.max(Document.priority)))).scalar() or 0
            res = await s.execute(
                update(Document).where(Document.id == doc_id).values(priority=top + 1, next_attempt_at=None)
            )
        self.wake()
        return bool(res.rowcount)

    # -------------------------------------------------------------- status
    @property
    def state(self) -> str:
        if not self._account.ready:
            return EngineState.WAITING_TELEGRAM
        if self.disk_full:
            return EngineState.DISK_FULL
        if self.paused:
            return EngineState.PAUSED
        if self.flood_until > time.time():
            return EngineState.FLOOD_WAIT
        if self.offline_until > time.time():
            return EngineState.OFFLINE
        return EngineState.RUNNING if self.active else EngineState.IDLE

    def status(self) -> dict:
        for a in self.active.values():
            a.sample()
        return {
            "state": self.state,
            "paused": self.paused,
            "flood_wait_seconds": max(0, round(self.flood_until - time.time())),
            "disk_full": self.disk_full,
            "workers": self._settings.current.workers,
            "active": [a.public() for a in self.active.values()],
        }

    def _publish_state(self) -> None:
        self._events.publish("engine", self.status())

    # -------------------------------------------------------------- worker
    async def _can_run(self) -> None:
        """Blocks until downloading is allowed."""
        while True:
            if not self._account.ready:
                await self._account.wait_ready()
                continue
            if self.paused or self.disk_full:
                self._wake.clear()
                await self._wake.wait()
                continue
            now = time.time()
            wait = max(self.flood_until, self.offline_until) - now
            if wait > 0:
                await asyncio.sleep(min(wait, 5))
                continue
            return

    def _should_stop(self, doc_id: int) -> str | None:
        if doc_id in self._cancel:
            return "cancel"
        if self._stopping or self.paused or self.disk_full or self.flood_until > time.time() or not self._account.ready:
            return "pause"
        return None

    async def _worker(self, index: int) -> None:
        while index < self._settings.current.workers and not self._stopping:
            await self._can_run()
            if index >= self._settings.current.workers or self._stopping:
                break  # the worker count was lowered while waiting
            self._busy.add(index)
            self._wake.clear()  # before claiming, so a wake during the claim isn't lost
            try:
                job = await self._claim()
                if job is not None:
                    await self._process(job)
            finally:
                self._busy.discard(index)
            if job is None:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass

    async def _claim(self) -> Job | None:
        now = utcnow()
        async with self._db.write() as s:
            next_id = (
                select(Document.id)
                .where(
                    Document.status == DocStatus.PENDING,
                    (Document.next_attempt_at.is_(None)) | (Document.next_attempt_at <= now),
                )
                .order_by(Document.priority.desc(), Document.first_seen_at, Document.id)
                .limit(1)
                .scalar_subquery()
            )
            row = (
                await s.execute(
                    update(Document)
                    .where(Document.id == next_id, Document.status == DocStatus.PENDING)
                    .values(status=DocStatus.DOWNLOADING)
                    .returning(
                        Document.id, Document.tg_document_id, Document.original_name, Document.ext,
                        Document.size, Document.attempts, Document.bytes_downloaded,
                    )
                )
            ).first()
        if row is None:
            return None
        return Job(*row)

    def _part_path(self, tg_document_id: int) -> Path:
        return self.tmp_dir / f"{tg_document_id}.part"

    async def _process(self, job: Job) -> None:
        part = self._part_path(job.tg_document_id)
        try:
            self._check_disk(job, part)
            source = await self._find_source(job)
            if source is None:
                await self._fail(job, MSG_NO_SOURCE, permanent=True)
                return
            await self._download(job, source, part)
            await self._finalize(job, part)
        except _Interrupted as e:
            if e.reason == "cancel":
                await self._release(job, part, status=DocStatus.SKIPPED, error=MSG_CANCELLED)
                part.unlink(missing_ok=True)
            else:
                await self._release(job, part)
        except _Release:
            await self._release(job, part)
        except FloodWait as e:
            log.warning("Flood wait of %ss; pausing all downloads", e.seconds)
            self.flood_until = max(self.flood_until, time.time() + e.seconds)
            await self._release(job, part)
            self._publish_state()
        except NotAuthorized:
            self._account.mark_unauthorized()
            await self._release(job, part)
        except (NotConnected, ConnectionError, asyncio.TimeoutError) as e:
            log.warning("Connection problem while downloading %s: %s", job.id, e)
            self.offline_until = time.time() + OFFLINE_RETRY_SECONDS
            await self._release(job, part)
            self._publish_state()
        except OSError as e:
            if e.errno == errno.ENOSPC:
                self._set_disk_full()
                await self._release(job, part)
            else:
                log.exception("File error while downloading %s", job.id)
                await self._fail(job, f"خطأ في الملف: {e}")
        except asyncio.CancelledError:
            # Shutdown: keep progress, resume next start.
            await asyncio.shield(self._release(job, part))
            raise
        except Exception as e:
            log.exception("Download of %s failed", job.id)
            await self._fail(job, str(e) or e.__class__.__name__)
        finally:
            self.active.pop(job.id, None)
            self._cancel.discard(job.id)
            self._publish_state()

    def _check_disk(self, job: Job, part: Path) -> None:
        have = part.stat().st_size if part.exists() else 0
        free = shutil.disk_usage(self.storage_dir).free
        if free < (job.size - have) + DISK_MARGIN:
            self._set_disk_full()
            raise _Release()

    def _set_disk_full(self) -> None:
        if not self.disk_full:
            log.error("Storage is full; pausing downloads")
        self.disk_full = "مساحة التخزين ممتلئة. أفرغ مساحة على القرص ثم اضغط «استئناف»."
        self._publish_state()

    async def _find_source(self, job: Job) -> DocMessage | None:
        """A fresh message (with a valid file reference) carrying this document."""
        async with self._db.read() as s:
            apps = (
                await s.execute(
                    select(Appearance.chat_id, Appearance.msg_id)
                    .where(Appearance.document_id == job.id)
                    .order_by(Appearance.posted_at.desc())
                )
            ).all()
        gw = self._account.gateway
        for chat_id, msg_id in apps:
            try:
                dm = await gw.get_document_message(chat_id, msg_id)
            except ChatUnavailable:
                continue
            if dm is not None and dm.document.tg_document_id == job.tg_document_id:
                return dm
        return None

    async def _download(self, job: Job, source: DocMessage, part: Path) -> None:
        have = part.stat().st_size if part.exists() else 0
        offset = resume_offset(have, job.bytes_downloaded)
        if have and offset:
            log.info("Resuming %s at %s of %s bytes", job.id, offset, job.size)
        active = self.active[job.id] = Active(job.id, job.name, job.size, offset, offset, _sample_bytes=offset)
        self._publish_state()

        with open(part, "r+b" if part.exists() else "w+b") as f:
            f.truncate(offset)
            f.seek(offset)
            durable = offset
            last_persist = time.monotonic()
            refreshes = 0
            try:
                while offset < job.size:
                    try:
                        async with aclosing(
                            self._account.gateway.iter_download(source, offset, CHUNK)
                        ) as stream:
                            async for chunk in stream:
                                f.write(chunk)
                                offset += len(chunk)
                                active.done = offset
                                if (
                                    offset - durable >= PERSIST_BYTES
                                    or time.monotonic() - last_persist >= PERSIST_SECONDS
                                ):
                                    await self._persist(job.id, f, offset)
                                    durable, last_persist = offset, time.monotonic()
                                if reason := self._should_stop(job.id):
                                    raise _Interrupted(reason)
                        if offset < job.size:
                            raise RuntimeError(f"انقطع التنزيل عند {offset} من {job.size} بايت")
                    except FileRefExpired:
                        refreshes += 1
                        if refreshes > MAX_REF_REFRESH:
                            raise
                        log.info("File reference expired for %s; re-fetching message", job.id)
                        await self._persist(job.id, f, offset)
                        durable = offset
                        fresh = await self._find_source(job)
                        if fresh is None:
                            raise RuntimeError(MSG_NO_SOURCE)
                        source = fresh
            finally:
                # Record whatever is durable now, whatever happens next.
                if offset != durable:
                    await asyncio.shield(self._persist(job.id, f, offset))

    async def _persist(self, doc_id: int, f, offset: int) -> None:
        f.flush()
        await asyncio.to_thread(os.fsync, f.fileno())
        async with self._db.write() as s:
            await s.execute(update(Document).where(Document.id == doc_id).values(bytes_downloaded=offset))

    async def _finalize(self, job: Job, part: Path) -> None:
        size = part.stat().st_size
        if size != job.size:
            part.unlink(missing_ok=True)
            async with self._db.write() as s:
                await s.execute(update(Document).where(Document.id == job.id).values(bytes_downloaded=0))
            raise RuntimeError(f"حجم الملف غير مطابق ({size} بدلاً من {job.size})")
        sha = await asyncio.to_thread(sha256_file, part)

        async with self._finalize_lock:
            delete_part = False
            async with self._db.write() as s:
                blob = (await s.execute(select(Blob).where(Blob.sha256 == sha))).scalars().first()
                doc = await s.get(Document, job.id)
                if blob is not None:
                    doc.status = DocStatus.DUPLICATE
                    delete_part = True
                    log.info("%s is identical to existing blob %s", job.id, blob.id)
                else:
                    rel = blob_relpath(sha, job.ext)
                    dest = self.storage_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(os.replace, part, dest)
                    blob = Blob(sha256=sha, size=size, storage_path=rel, created_at=utcnow())
                    s.add(blob)
                    await s.flush()
                    doc.status = DocStatus.DONE
                doc.blob_id = blob.id
                doc.bytes_downloaded = size
                doc.completed_at = utcnow()
                doc.last_error = None
                doc.next_attempt_at = None
                # Name+size duplicates that were waiting for this original.
                await s.execute(
                    update(Document)
                    .where(Document.duplicate_of_id == job.id, Document.blob_id.is_(None))
                    .values(blob_id=blob.id, completed_at=utcnow())
                )
                status = doc.status
            if delete_part:
                part.unlink(missing_ok=True)
        self._events.publish("document", {"id": job.id, "status": status, "name": job.name})

    async def _release(
        self, job: Job, part: Path, status: str = DocStatus.PENDING, error: str | None = None
    ) -> None:
        async with self._db.write() as s:
            values: dict = {"status": status}
            if error is not None:
                values["last_error"] = error
            await s.execute(
                update(Document)
                .where(Document.id == job.id, Document.status == DocStatus.DOWNLOADING)
                .values(**values)
            )
            if status == DocStatus.SKIPPED:
                await self._unlink_name_duplicates(s, job.id)

    async def _fail(self, job: Job, error: str, permanent: bool = False) -> None:
        attempts = job.attempts + 1
        final = permanent or attempts >= self._settings.current.max_attempts
        async with self._db.write() as s:
            values: dict = {"attempts": attempts, "last_error": error[:1000]}
            if final:
                values["status"] = DocStatus.FAILED
                values["next_attempt_at"] = None
            else:
                values["status"] = DocStatus.PENDING
                values["next_attempt_at"] = utcnow() + timedelta(seconds=backoff_seconds(attempts))
            await s.execute(update(Document).where(Document.id == job.id).values(**values))
            if final:
                await self._unlink_name_duplicates(s, job.id)
        log.warning("Download of %s %s: %s", job.id, "failed" if final else "will retry", error)
        if final:
            self._events.publish("document", {"id": job.id, "status": DocStatus.FAILED, "name": job.name})

    @staticmethod
    async def _unlink_name_duplicates(s, doc_id: int) -> None:
        """The original won't be downloaded; its name+size duplicates are
        separate Telegram files and get their own chance."""
        await s.execute(
            update(Document)
            .where(
                Document.duplicate_of_id == doc_id,
                Document.blob_id.is_(None),
                Document.status == DocStatus.DUPLICATE,
            )
            .values(status=DocStatus.PENDING, duplicate_of_id=None)
        )
