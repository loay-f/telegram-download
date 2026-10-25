"""Recording scanned messages into documents/appearances, with the
pre-download duplicate check, and turning documents into download requests."""

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tglib.db.models import Appearance, Chat, Document, DocStatus, utcnow
from tglib.engine.normalize import normalize_name, split_ext
from tglib.engine.settings_store import AppSettings, parse_exts
from tglib.engine.telegram import DialogInfo, DocMessage
from tglib.engine.volumes import parse_volume

FILTERED_EXT = "filtered:ext"
FILTERED_SIZE = "filtered:size"
DELETED = "deleted"  # removed from the library by the user

# Documents a newly found file may be considered a name+size duplicate of.
# Failed/skipped ones don't count: the new copy deserves its own chance.
_DEDUP_CANDIDATE_STATUSES = (
    DocStatus.AVAILABLE,
    DocStatus.PENDING,
    DocStatus.DOWNLOADING,
    DocStatus.DONE,
    DocStatus.DUPLICATE,
)


@dataclass
class Filters:
    exts: set[str]
    max_size: int  # bytes; 0 = unlimited
    auto_download: bool = False  # queue new files, or only list them

    @classmethod
    def for_chat(cls, chat: Chat, settings: AppSettings) -> "Filters":
        exts = parse_exts(chat.allowed_exts) if chat.allowed_exts else settings.ext_set()
        max_mb = chat.max_size_mb if chat.max_size_mb is not None else settings.max_size_mb
        return cls(exts=exts, max_size=max_mb * 1024 * 1024, auto_download=bool(chat.auto_download))

    def rejection(self, ext: str | None, size: int) -> str | None:
        if ext not in self.exts:
            return FILTERED_EXT
        if self.max_size and size > self.max_size:
            return FILTERED_SIZE
        return None


@dataclass
class Decision:
    status: str
    blob_id: int | None = None
    duplicate_of_id: int | None = None
    last_error: str | None = None


def decide(rejection: str | None, match: Document | None, auto_download: bool = True) -> Decision:
    """Status for a NEW Telegram document.

    rejection: why the filters reject it, if they do.
    match: an existing document with the same size and normalized name.
    auto_download: queue it (pending) rather than only list it (available).
    """
    if rejection:
        return Decision(DocStatus.SKIPPED, last_error=rejection)
    if match is None:
        return Decision(DocStatus.PENDING if auto_download else DocStatus.AVAILABLE)
    if match.blob_id is not None:
        return Decision(DocStatus.DUPLICATE, blob_id=match.blob_id, duplicate_of_id=match.id)
    # The original isn't downloaded yet; link to it and inherit its blob later.
    return Decision(DocStatus.DUPLICATE, duplicate_of_id=match.duplicate_of_id or match.id)


def doc_ext(dm: DocMessage) -> str | None:
    _, ext = split_ext(dm.document.file_name)
    return ext or dm.document.ext


async def find_name_size_match(
    s: AsyncSession, size: int, normalized: str, exclude_id: int | None = None
) -> Document | None:
    if not normalized or size <= 0:
        return None
    q = (
        select(Document)
        .where(
            Document.size == size,
            Document.normalized_name == normalized,
            Document.status.in_(_DEDUP_CANDIDATE_STATUSES),
        )
        # Prefer one that already has content, then the oldest.
        .order_by(Document.blob_id.is_(None), Document.id)
        .limit(1)
    )
    if exclude_id is not None:
        q = q.where(Document.id != exclude_id)
    return (await s.execute(q)).scalars().first()


def _apply(doc: Document, d: Decision) -> None:
    doc.status = d.status
    doc.blob_id = d.blob_id
    doc.duplicate_of_id = d.duplicate_of_id
    doc.last_error = d.last_error
    if d.status == DocStatus.DUPLICATE and d.blob_id is not None:
        doc.completed_at = utcnow()


async def reapply_filters(s: AsyncSession, settings: AppSettings) -> int:
    """After the filter settings change, documents skipped by a filter come
    back if any chat they appeared in now accepts them. Returns how many."""
    rows = (
        await s.execute(
            select(Document, Chat)
            .join(Appearance, Appearance.document_id == Document.id)
            .join(Chat, Chat.id == Appearance.chat_id)
            .where(Document.status == DocStatus.SKIPPED, Document.last_error.like("filtered:%"))
        )
    ).all()
    accepted: dict[int, tuple[Document, Filters]] = {}
    for doc, chat in rows:
        filters = Filters.for_chat(chat, settings)
        if doc.id not in accepted and filters.rejection(doc.ext, doc.size) is None:
            accepted[doc.id] = (doc, filters)
    for doc, filters in accepted.values():
        match = await find_name_size_match(s, doc.size, doc.normalized_name or "", doc.id)
        _apply(doc, decide(None, match, filters.auto_download))
        await s.flush()
    return len(accepted)


async def record_message(s: AsyncSession, chat: Chat, dm: DocMessage, filters: Filters) -> str:
    """Upserts one scanned message. Returns what happened:
    'known' (already recorded), 'appearance' (new place for a known document),
    or the new document's status."""
    info = dm.document
    existing_app = await s.get(Appearance, (chat.id, dm.msg_id))
    doc = (
        await s.execute(select(Document).where(Document.tg_document_id == info.tg_document_id))
    ).scalars().first()

    if existing_app is not None and doc is not None and existing_app.document_id == doc.id:
        return "known"

    if doc is not None:
        outcome = "appearance"
        # Rejected by another chat's filters, but this chat accepts it.
        if doc.status == DocStatus.SKIPPED and (doc.last_error or "").startswith("filtered:"):
            if filters.rejection(doc.ext, doc.size) is None:
                match = await find_name_size_match(s, doc.size, doc.normalized_name or "", doc.id)
                _apply(doc, decide(None, match, filters.auto_download))
                outcome = doc.status
        # Listed from another chat, and this chat downloads automatically.
        elif doc.status == DocStatus.AVAILABLE and filters.auto_download:
            doc.status = DocStatus.PENDING
            outcome = doc.status
    else:
        ext = doc_ext(dm)
        normalized = normalize_name(info.file_name)
        series_key, volume = parse_volume(normalized)
        rejection = filters.rejection(ext, info.size)
        match = None if rejection else await find_name_size_match(s, info.size, normalized)
        doc = Document(
            tg_document_id=info.tg_document_id,
            original_name=info.file_name or f"telegram-{info.tg_document_id}{ext or ''}",
            normalized_name=normalized,
            series_key=series_key,
            volume=volume,
            ext=ext,
            mime=info.mime,
            size=info.size,
            first_seen_at=utcnow(),
            priority=0,
            bytes_downloaded=0,
            attempts=0,
        )
        _apply(doc, decide(rejection, match, filters.auto_download))
        s.add(doc)
        await s.flush()
        outcome = doc.status

    if existing_app is not None:
        # The message was edited to carry a different file.
        existing_app.document_id = doc.id
        existing_app.caption = dm.caption
    else:
        s.add(
            Appearance(
                chat_id=chat.id,
                msg_id=dm.msg_id,
                document_id=doc.id,
                posted_at=dm.date,
                caption=dm.caption,
            )
        )
    await s.flush()
    return outcome


async def ensure_chat(s: AsyncSession, info: DialogInfo, is_member: bool) -> Chat:
    """A chat row for a file found by search or a pasted link (not tracked)."""
    chat = await s.get(Chat, info.id)
    if chat is None:
        chat = Chat(
            id=info.id, title=info.title, username=info.username, type=info.type,
            tracked=False, auto_download=False, last_scanned_msg_id=0, is_member=is_member,
        )
        s.add(chat)
        await s.flush()
    return chat


async def record_explicit(s: AsyncSession, chat_info: DialogInfo, dm: DocMessage, is_member: bool) -> Document:
    """Records a file the user picked directly (search result or link). The
    user's choice overrides the type and size filters."""
    chat = await ensure_chat(s, chat_info, is_member)
    permissive = Filters(exts={doc_ext(dm)}, max_size=0, auto_download=False)
    await record_message(s, chat, dm, permissive)
    return (
        await s.execute(select(Document).where(Document.tg_document_id == dm.document.tg_document_id))
    ).scalars().one()


@dataclass
class RequestResult:
    queued: int = 0  # newly queued for download
    already: int = 0  # already downloaded or already in the queue


async def request_download(s: AsyncSession, doc_ids: list[int], *, to_front: bool = False) -> RequestResult:
    """Queues the given documents for download. Name+size duplicates without
    content are redirected to their original."""
    result = RequestResult()
    top = (await s.execute(select(func.max(Document.priority)))).scalar() or 0
    seen: set[int] = set()
    for doc_id in doc_ids:
        doc = await s.get(Document, doc_id)
        hops = 0
        while doc is not None and doc.blob_id is None and doc.status == DocStatus.DUPLICATE and hops < 5:
            doc = await s.get(Document, doc.duplicate_of_id) if doc.duplicate_of_id else None
            hops += 1
        if doc is None or doc.id in seen:
            continue
        seen.add(doc.id)
        if doc.blob_id is not None or doc.status in (DocStatus.PENDING, DocStatus.DOWNLOADING):
            result.already += 1
            if to_front and doc.status == DocStatus.PENDING:
                doc.priority = top + 1
            continue
        doc.status = DocStatus.PENDING
        doc.attempts = 0
        doc.last_error = None
        doc.next_attempt_at = None
        doc.priority = top + 1 if to_front else 0
        result.queued += 1
    await s.flush()
    return result


async def backfill_series(s: AsyncSession) -> int:
    """Computes series/volume for documents recorded before volumes existed."""
    rows = (
        await s.execute(
            select(Document.id, Document.normalized_name).where(Document.series_key.is_(None))
        )
    ).all()
    for doc_id, normalized in rows:
        series_key, volume = parse_volume(normalized)
        await s.execute(
            update(Document).where(Document.id == doc_id).values(series_key=series_key, volume=volume)
        )
    return len(rows)
