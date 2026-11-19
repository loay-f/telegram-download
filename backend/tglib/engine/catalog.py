"""Recording scanned messages into documents/appearances, with the
pre-download duplicate check, and turning documents into download requests."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tglib.db.models import Appearance, Chat, Document, DocStatus, utcnow
from tglib.engine.normalize import fold, normalize_name, split_ext
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

# Ids per statement, well under SQLite's bound-parameter limit.
IN_CHUNK = 500


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


def fallback_name(dm: DocMessage, ext: str | None) -> str:
    """A name for a file that has none (photos, most videos): what it is,
    the day it was posted and its message, e.g. photo_2024-05-01_1234.jpg."""
    mime = dm.document.mime or ""
    what = next((w for w in ("image", "video", "audio") if mime.startswith(w)), None)
    if what is None:
        return f"telegram-{dm.document.tg_document_id}{ext or ''}"
    what = "photo" if what == "image" else what
    day = f"{dm.date:%Y-%m-%d}_" if dm.date else ""
    return f"{what}_{day}{dm.msg_id}{ext or ''}"


def doc_ext(dm: DocMessage) -> str | None:
    _, ext = split_ext(dm.document.file_name)
    return ext or dm.document.ext


def search_text(name: str | None, captions: Iterable[str | None] = ()) -> str:
    """What the library's search looks through for a document: its name, then
    each caption it was posted with, folded, one per line."""
    parts = [fold(name or "")]
    for caption in captions:
        folded = fold(caption) if caption else ""
        if folded and not any(folded in p for p in parts):
            parts.append(folded)
    return "\n".join(parts)


def _add_caption(doc: Document, caption: str | None) -> None:
    """A new post of a known document: its caption becomes searchable too."""
    if not caption:
        return
    known = doc.search_text if doc.search_text is not None else search_text(doc.original_name)
    folded = fold(caption)
    doc.search_text = known if folded in known else f"{known}\n{folded}"


async def backfill_search_text(s: AsyncSession) -> int:
    """Fills the search text of documents recorded before it existed, in one
    statement (`fold` is registered on the app's connections)."""
    res = await s.execute(
        text(
            """
            UPDATE documents SET search_text = fold(coalesce(original_name, '')) || coalesce(
                char(10) || (
                    SELECT group_concat(c, char(10)) FROM (
                        SELECT DISTINCT fold(a.caption) AS c FROM appearances a
                        WHERE a.document_id = documents.id AND a.caption IS NOT NULL AND a.caption != ''
                    )
                ), '')
            WHERE search_text IS NULL
            """
        )
    )
    return res.rowcount or 0


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


async def _filters_by_chat(s: AsyncSession, settings: AppSettings) -> dict[int, Filters]:
    return {c.id: Filters.for_chat(c, settings) for c in (await s.execute(select(Chat))).scalars()}


def _chunks(ids: Sequence[int], size: int = IN_CHUNK) -> Iterable[Sequence[int]]:
    return (ids[i : i + size] for i in range(0, len(ids), size))


async def reapply_filters(s: AsyncSession, settings: AppSettings) -> int:
    """After the filter settings change, documents skipped by a filter come
    back if any chat they appeared in now accepts them. Returns how many."""
    # Plain columns: there can be hundreds of thousands of skipped files, and
    # only the ones coming back are needed as objects.
    rows = await s.execute(
        select(Document.id, Document.ext, Document.size, Appearance.chat_id)
        .join(Appearance, Appearance.document_id == Document.id)
        .where(Document.status == DocStatus.SKIPPED, Document.last_error.like("filtered:%"))
    )
    filters = await _filters_by_chat(s, settings)
    accepted: dict[int, Filters] = {}
    for doc_id, ext, size, chat_id in rows:
        f = filters.get(chat_id)
        if f is not None and doc_id not in accepted and f.rejection(ext, size) is None:
            accepted[doc_id] = f
    for chunk in _chunks(sorted(accepted)):
        docs = (await s.execute(select(Document).where(Document.id.in_(chunk)).order_by(Document.id))).scalars().all()
        for doc in docs:
            # The lookup sees the documents decided before this one: a query
            # flushes what is pending first.
            match = await find_name_size_match(s, doc.size, doc.normalized_name or "", doc.id)
            _apply(doc, decide(None, match, accepted[doc.id].auto_download))
        await s.flush()
    return len(accepted)


async def hide_rejected(s: AsyncSession, settings: AppSettings) -> int:
    """After a file type is excluded (or the size limit lowered), files that
    were only listed and that no chat they appeared in accepts any more leave
    the list. Nothing downloaded, queued or downloading is touched, and they
    come back if the type is allowed again (`reapply_filters`). Returns how
    many."""
    rows = await s.execute(
        select(Document.id, Document.ext, Document.size, Appearance.chat_id)
        .join(Appearance, Appearance.document_id == Document.id)
        .where(Document.status == DocStatus.AVAILABLE)
    )
    filters = await _filters_by_chat(s, settings)
    verdict: dict[int, str | None] = {}
    for doc_id, ext, size, chat_id in rows:
        if doc_id in verdict and verdict[doc_id] is None:
            continue  # some chat accepts it
        f = filters.get(chat_id)
        if f is not None:
            verdict[doc_id] = f.rejection(ext, size)
    by_reason: dict[str, list[int]] = {}
    for doc_id, rejection in verdict.items():
        if rejection:
            by_reason.setdefault(rejection, []).append(doc_id)
    hidden = 0
    for reason, ids in by_reason.items():
        for chunk in _chunks(ids):
            res = await s.execute(
                update(Document)
                .where(Document.id.in_(chunk), Document.status == DocStatus.AVAILABLE)
                .values(status=DocStatus.SKIPPED, last_error=reason)
            )
            hidden += res.rowcount or 0
    return hidden


def _best_match(candidates: Sequence[Document]) -> Document | None:
    """As `find_name_size_match` orders them: one that already has content
    first, then the oldest. `candidates` are oldest first."""
    live = [d for d in candidates if d.status in _DEDUP_CANDIDATE_STATUSES]
    return next((d for d in live if d.blob_id is not None), live[0] if live else None)


async def record_messages(s: AsyncSession, chat: Chat, batch: Sequence[DocMessage], filters: Filters) -> list[str]:
    """Upserts scanned messages of one chat. Returns what happened to each:
    'known' (already recorded), 'appearance' (new place for a known document),
    or the new document's status.

    What the batch needs from the tables is read with a few queries and what
    it adds is written together, instead of several statements per message:
    the write connection is the only one, and downloads wait for it."""
    if not batch:
        return []
    apps: dict[int, Appearance] = {
        a.msg_id: a
        for a in (
            await s.execute(
                select(Appearance).where(
                    Appearance.chat_id == chat.id, Appearance.msg_id.in_([dm.msg_id for dm in batch])
                )
            )
        ).scalars()
    }
    docs: dict[int, Document] = {
        d.tg_document_id: d
        for d in (
            await s.execute(
                select(Document).where(
                    Document.tg_document_id.in_({dm.document.tg_document_id for dm in batch})
                )
            )
        ).scalars()
    }

    # New documents: what each is, and the ones it may be a name+size
    # duplicate of (oldest first; this batch's own are appended as they come).
    described: dict[int, tuple[str | None, str, str | None]] = {}  # tg id -> ext, normalized name, rejection
    for dm in batch:
        info = dm.document
        if info.tg_document_id not in docs and info.tg_document_id not in described:
            ext = doc_ext(dm)
            described[info.tg_document_id] = (ext, normalize_name(info.file_name), filters.rejection(ext, info.size))
    same_name: dict[tuple[int, str], list[Document]] = {}
    wanted = {
        (dm.document.size, described[dm.document.tg_document_id][1])
        for dm in batch
        if dm.document.tg_document_id in described
        and described[dm.document.tg_document_id][1]
        and dm.document.size > 0
        and described[dm.document.tg_document_id][2] is None
    }
    if wanted:
        # By size alone, so the index on it is the one used (few files share
        # an exact size; given the name and status too, SQLite may walk a
        # whole status instead). The rest is checked here and by `_best_match`.
        candidates = await s.execute(
            select(Document).where(Document.size.in_({size for size, _ in wanted})).order_by(Document.id)
        )
        for d in candidates.scalars():
            if (d.size, d.normalized_name) in wanted:
                same_name.setdefault((d.size, d.normalized_name), []).append(d)

    outcomes: list[str] = []
    placed: dict[int, Document] = {}  # msg id -> document, for appearances this batch adds
    # New documents are numbered here, as SQLite would number them (this is
    # the only connection that writes, and it holds the write lock): rows that
    # come with their ids are inserted together, not one statement each.
    next_id: int | None = None
    for dm in batch:
        info = dm.document
        app = apps.get(dm.msg_id)
        doc = docs.get(info.tg_document_id)
        if doc is not None and (placed.get(dm.msg_id) is doc or (app is not None and app.document_id == doc.id)):
            outcomes.append("known")
            continue

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
            _add_caption(doc, dm.caption)
        else:
            ext, normalized, rejection = described[info.tg_document_id]
            key = (info.size, normalized)
            match = None if rejection else _best_match(same_name.get(key, ()))
            series_key, volume = parse_volume(normalized)
            name = info.file_name or fallback_name(dm, ext)
            if next_id is None:
                next_id = (await s.execute(select(func.coalesce(func.max(Document.id), 0)))).scalar() + 1
            doc = Document(
                id=next_id,
                tg_document_id=info.tg_document_id,
                original_name=name,
                normalized_name=normalized,
                series_key=series_key,
                volume=volume,
                search_text=search_text(name, [dm.caption]),
                ext=ext,
                mime=info.mime,
                size=info.size,
                first_seen_at=utcnow(),
                priority=0,
                bytes_downloaded=0,
                attempts=0,
            )
            next_id += 1
            _apply(doc, decide(rejection, match, filters.auto_download))
            s.add(doc)
            docs[info.tg_document_id] = doc
            if key in wanted:
                same_name.setdefault(key, []).append(doc)
            outcome = doc.status

        if dm.msg_id in placed:
            # The same message twice in one batch, carrying another file now.
            placed[dm.msg_id] = doc
            apps[dm.msg_id].caption = dm.caption
        elif app is not None:
            # The message was edited to carry a different file.
            app.document_id = doc.id
            app.caption = dm.caption
        else:
            apps[dm.msg_id] = Appearance(chat_id=chat.id, msg_id=dm.msg_id, posted_at=dm.date, caption=dm.caption)
            placed[dm.msg_id] = doc
        outcomes.append(outcome)

    await s.flush()  # documents first: their posts refer to them
    for msg_id, doc in placed.items():
        apps[msg_id].document_id = doc.id
        s.add(apps[msg_id])
    await s.flush()
    return outcomes


async def record_message(s: AsyncSession, chat: Chat, dm: DocMessage, filters: Filters) -> str:
    """`record_messages` for a single message."""
    return (await record_messages(s, chat, [dm], filters))[0]


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
    # Read in a few queries; `s.get` below then finds them in the session
    # (which only keeps what something else still refers to, hence the list).
    loaded = [
        d
        for chunk in _chunks(doc_ids)
        for d in (await s.execute(select(Document).where(Document.id.in_(chunk)))).scalars()
    ]
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
    del loaded
    return result


async def request_chat_download(s: AsyncSession, chat_id: int) -> int:
    """Queues every file of the chat that is only listed, in one statement
    however many there are. Returns how many."""
    res = await s.execute(
        update(Document)
        .where(
            Document.status == DocStatus.AVAILABLE,
            Document.blob_id.is_(None),
            Document.id.in_(select(Appearance.document_id).where(Appearance.chat_id == chat_id)),
        )
        .values(status=DocStatus.PENDING, attempts=0, last_error=None, next_attempt_at=None, priority=0)
        .execution_options(synchronize_session=False)
    )
    return res.rowcount or 0


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
