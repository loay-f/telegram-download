"""Recording scanned messages into documents/appearances, with the
pre-download duplicate check."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tglib.db.models import Appearance, Chat, Document, DocStatus, utcnow
from tglib.engine.normalize import normalize_name, split_ext
from tglib.engine.settings_store import AppSettings, parse_exts
from tglib.engine.telegram import DocMessage

FILTERED_EXT = "filtered:ext"
FILTERED_SIZE = "filtered:size"

# Documents a newly found file may be considered a name+size duplicate of.
# Failed/skipped ones don't count: the new copy deserves its own chance.
_DEDUP_CANDIDATE_STATUSES = (DocStatus.PENDING, DocStatus.DOWNLOADING, DocStatus.DONE, DocStatus.DUPLICATE)


@dataclass
class Filters:
    exts: set[str]
    max_size: int  # bytes; 0 = unlimited

    @classmethod
    def for_chat(cls, chat: Chat, settings: AppSettings) -> "Filters":
        exts = parse_exts(chat.allowed_exts) if chat.allowed_exts else settings.ext_set()
        max_mb = chat.max_size_mb if chat.max_size_mb is not None else settings.max_size_mb
        return cls(exts=exts, max_size=max_mb * 1024 * 1024)

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


def decide(rejection: str | None, match: Document | None) -> Decision:
    """Status for a NEW Telegram document.

    rejection: why the filters reject it, if they do.
    match: an existing document with the same size and normalized name.
    """
    if rejection:
        return Decision(DocStatus.SKIPPED, last_error=rejection)
    if match is None:
        return Decision(DocStatus.PENDING)
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
    """After the filter settings change, documents skipped by a filter become
    pending if any chat they appeared in now accepts them. Returns how many."""
    rows = (
        await s.execute(
            select(Document, Chat)
            .join(Appearance, Appearance.document_id == Document.id)
            .join(Chat, Chat.id == Appearance.chat_id)
            .where(Document.status == DocStatus.SKIPPED, Document.last_error.like("filtered:%"))
        )
    ).all()
    accepted: dict[int, Document] = {}
    for doc, chat in rows:
        if doc.id not in accepted and Filters.for_chat(chat, settings).rejection(doc.ext, doc.size) is None:
            accepted[doc.id] = doc
    for doc in accepted.values():
        match = await find_name_size_match(s, doc.size, doc.normalized_name or "", doc.id)
        _apply(doc, decide(None, match))
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
                _apply(doc, decide(None, match))
                outcome = doc.status
    else:
        ext = doc_ext(dm)
        normalized = normalize_name(info.file_name)
        rejection = filters.rejection(ext, info.size)
        match = None if rejection else await find_name_size_match(s, info.size, normalized)
        doc = Document(
            tg_document_id=info.tg_document_id,
            original_name=info.file_name or f"telegram-{info.tg_document_id}{ext or ''}",
            normalized_name=normalized,
            ext=ext,
            mime=info.mime,
            size=info.size,
            first_seen_at=utcnow(),
            priority=0,
            bytes_downloaded=0,
            attempts=0,
        )
        _apply(doc, decide(rejection, match))
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
