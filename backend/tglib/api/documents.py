"""Queue (documents), library search, and file serving."""

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import String, and_, case, cast, exists, func, literal, or_, select

from tglib.api.deps import api_error, get_ctx, require_auth
from tglib.app.container import Container
from tglib.db.models import Appearance, Blob, Chat, Document, DocStatus
from tglib.engine.normalize import fold, normalize_name

router = APIRouter(prefix="/api", tags=["documents"], dependencies=[Depends(require_auth)])

QUEUE_STATUSES = (DocStatus.PENDING, DocStatus.DOWNLOADING, DocStatus.FAILED, DocStatus.SKIPPED)


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _text_filter(q: str):
    """Matches the file name or any caption, ignoring Arabic spelling variants.
    Both are stored already folded (`Document.search_text`), so nothing is
    folded per row here."""
    folded = fold(q.strip())
    # Same rules as stored names; the dummy ".x" keeps a dot in the query
    # (e.g. "vol.2") from being taken as an extension.
    norm = normalize_name(q.strip() + ".x")
    conds = [Document.search_text.like(_like(folded), escape="\\")]
    if norm:
        conds.append(Document.normalized_name.like(_like(norm), escape="\\"))
    return or_(*conds)


async def _appearances(s, doc_ids: list[int]) -> dict[int, list[dict]]:
    if not doc_ids:
        return {}
    rows = await s.execute(
        select(Appearance.document_id, Appearance.chat_id, Appearance.msg_id, Appearance.posted_at,
               Appearance.caption, Chat.title, Chat.username, Chat.type)
        .join(Chat, Chat.id == Appearance.chat_id)
        .where(Appearance.document_id.in_(doc_ids))
        .order_by(Appearance.posted_at)
    )
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r.document_id, []).append(
            {
                "chat_id": r.chat_id,
                "chat_title": r.title,
                "msg_id": r.msg_id,
                "posted_at": r.posted_at,
                "caption": r.caption,
                "link": _message_link(r.chat_id, r.username, r.type, r.msg_id),
            }
        )
    return out


def _message_link(chat_id: int, username: str | None, chat_type: str | None, msg_id: int) -> str | None:
    """Telegram has message links only for channels and groups."""
    if chat_type not in ("channel", "group"):
        return None
    if username:
        return f"https://t.me/{username}/{msg_id}"
    s = str(chat_id)
    if s.startswith("-100"):
        return f"https://t.me/c/{s[4:]}/{msg_id}"
    return None


# ---------------------------------------------------------------- queue
@router.get("/documents")
async def list_documents(
    status: list[str] = Query(default=list(QUEUE_STATUSES)),
    q: str = "",
    chat_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ctx: Container = Depends(get_ctx),
):
    conds = [Document.status.in_([st for st in status if st in DocStatus.ALL])]
    if q.strip():
        conds.append(_text_filter(q))
    if chat_id is not None:
        conds.append(exists().where(Appearance.document_id == Document.id, Appearance.chat_id == chat_id))
    where = and_(*conds)
    order = (
        Document.status != DocStatus.DOWNLOADING,
        Document.priority.desc(),
        Document.first_seen_at,
        Document.id,
    )
    async with ctx.db.read() as s:
        total = (await s.execute(select(func.count()).select_from(Document).where(where))).scalar()
        docs = (
            await s.execute(
                select(Document).where(where).order_by(*order).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars().all()
        apps = await _appearances(s, [d.id for d in docs])
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": d.id,
                "name": d.original_name,
                "ext": d.ext,
                "size": d.size,
                "status": d.status,
                "priority": d.priority,
                "bytes_downloaded": d.bytes_downloaded,
                "attempts": d.attempts,
                "last_error": d.last_error,
                "next_attempt_at": d.next_attempt_at,
                "first_seen_at": d.first_seen_at,
                "chats": sorted({a["chat_title"] or "" for a in apps.get(d.id, [])}),
            }
            for d in docs
        ],
    }


@router.get("/documents/{doc_id}")
async def get_document(doc_id: int, ctx: Container = Depends(get_ctx)):
    async with ctx.db.read() as s:
        d = await s.get(Document, doc_id)
    if d is None:
        raise api_error(404, "not_found", "الملف غير موجود.")
    return {"id": d.id, "name": d.original_name, "ext": d.ext, "size": d.size, "status": d.status,
            "has_file": d.blob_id is not None}


@router.post("/documents/{doc_id}/retry")
async def retry(doc_id: int, ctx: Container = Depends(get_ctx)):
    if not await ctx.engine.retry(doc_id):
        raise api_error(409, "not_retryable", "لا يمكن إعادة محاولة هذا الملف الآن.")
    return {"ok": True}


@router.post("/documents/{doc_id}/cancel")
async def cancel(doc_id: int, ctx: Container = Depends(get_ctx)):
    if not await ctx.engine.cancel(doc_id):
        raise api_error(409, "not_cancellable", "لا يمكن إلغاء هذا الملف.")
    return {"ok": True}


@router.post("/documents/{doc_id}/priority")
async def priority(doc_id: int, ctx: Container = Depends(get_ctx)):
    if not await ctx.engine.bump_priority(doc_id):
        raise api_error(404, "not_found", "الملف غير موجود.")
    return {"ok": True}


@router.post("/documents/retry-failed")
async def retry_failed(ctx: Container = Depends(get_ctx)):
    return {"count": await ctx.engine.retry_all_failed()}


class DownloadIn(BaseModel):
    ids: list[int]
    to_front: bool = False  # put ahead of everything already queued


@router.post("/documents/download")
async def download(body: DownloadIn, ctx: Container = Depends(get_ctx)):
    """Queues picked files (available, failed, skipped) for download."""
    result = await ctx.engine.request(body.ids[:5000], to_front=body.to_front)
    return {"queued": result.queued, "already": result.already}


# -------------------------------------------------------------- library
LIBRARY_VIEWS = {
    # Downloaded: one entry per stored file; name+size/content duplicates fold in.
    "downloaded": None,
    # Found by scans (or search) but not stored yet.
    "available": (DocStatus.AVAILABLE, DocStatus.PENDING, DocStatus.DOWNLOADING, DocStatus.FAILED),
}


def _work_key(view: str):
    """Volumes of one series form one work; otherwise each file is its own."""
    unit = (literal("b:") + cast(Document.blob_id, String)) if view == "downloaded" else (
        literal("d:") + cast(Document.id, String)
    )
    return case(
        (func.coalesce(Document.series_key, "") != "", literal("s:") + Document.series_key),
        else_=unit,
    )


async def _work_documents(s, keys: list[str], view: str, conds: list, key) -> list:
    """(document, work key) for the documents of the works with these keys
    that match `conds`, oldest first.

    They are looked up by what a key is made of (a series, a blob, an id),
    which is indexed. `conds` are checked on what that finds rather than put
    in the WHERE, where SQLite may prefer to walk every document of a status."""
    series = [k[2:] for k in keys if k.startswith("s:")]
    units = [int(k[2:]) for k in keys if not k.startswith("s:")]
    unit = Document.blob_id if view == "downloaded" else Document.id
    matches = and_(*conds).label("matches")
    found = []
    for wanted in (
        Document.series_key.in_(series) if series else None,
        and_(unit.in_(units), func.coalesce(Document.series_key, "") == "") if units else None,
    ):
        if wanted is not None:
            rows = await s.execute(select(Document, key, matches).where(wanted))
            found += [(d, k) for d, k, ok in rows if ok]
    return sorted(found, key=lambda row: row[0].id)


def _library_conds(view, q, ext, chat_id, date_from, date_to) -> list:
    if view == "downloaded":
        conds = [Document.blob_id.is_not(None)]
    else:
        conds = [Document.status.in_(LIBRARY_VIEWS[view])]
    if q.strip():
        conds.append(_text_filter(q))
    if ext:
        conds.append(Document.ext == "." + ext.lower().lstrip("."))
    app_conds = []
    if chat_id is not None:
        app_conds.append(Appearance.chat_id == chat_id)
    if date_from is not None:
        app_conds.append(Appearance.posted_at >= date_from)
    if date_to is not None:
        app_conds.append(Appearance.posted_at < date_to + timedelta(days=1))
    if app_conds:
        conds.append(exists().where(Appearance.document_id == Document.id, *app_conds))
    return conds


@router.get("/library")
async def library(
    view: str = Query("downloaded", pattern="^(downloaded|available)$"),
    q: str = "",
    ext: str | None = None,
    chat_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: str = Query("recent", pattern="^(recent|name|size)$"),
    page: int = Query(1, ge=1),
    # Large enough for the page to bring every stretch it shows up to date at once.
    page_size: int = Query(30, ge=1, le=1000),
    ctx: Container = Depends(get_ctx),
):
    """Works (a single file, or the volumes of a series) matching the filters."""
    conds = _library_conds(view, q, ext, chat_id, date_from, date_to)
    key = _work_key(view).label("key")
    when = Document.completed_at if view == "downloaded" else Document.first_seen_at
    grouped = (
        select(
            key,
            func.max(when).label("recent"),
            func.min(Document.normalized_name).label("name"),
            func.sum(Document.size).label("size"),
        )
        .where(*conds)
        .group_by(key)
        .subquery()
    )
    order = {
        "recent": grouped.c.recent.desc(),
        "name": grouped.c.name,
        "size": grouped.c.size.desc(),
    }[sort]
    async with ctx.db.read() as s:
        # The page and the number of works there are, from one pass over them.
        rows = (
            await s.execute(
                select(grouped.c.key, func.count().over().label("total"))
                .order_by(order, grouped.c.key)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        keys = [r.key for r in rows]
        if rows:
            total = rows[0].total
        elif page == 1:
            total = 0
        else:  # a page past the end has no row to carry the total
            total = (await s.execute(select(func.count()).select_from(grouped))).scalar()
        docs = await _work_documents(s, keys, view, conds, key)
        apps = await _appearances(s, [d.id for d, _ in docs])

    by_key: dict[str, list[Document]] = {}
    for d, k in docs:
        by_key.setdefault(k, []).append(d)
    items = [_work(k, by_key.get(k, []), apps, view) for k in keys]
    return {"total": total, "page": page, "page_size": page_size, "items": [i for i in items if i]}


def _work(key: str, docs: list[Document], apps: dict[int, list[dict]], view: str) -> dict | None:
    if not docs:
        return None
    entries = []
    if view == "downloaded":
        by_blob: dict[int, list[Document]] = {}
        for d in docs:
            by_blob.setdefault(d.blob_id, []).append(d)
        for group in by_blob.values():
            main = next((d for d in group if d.status == DocStatus.DONE), group[0])
            entries.append(
                _entry(main, [a for d in group for a in apps.get(d.id, [])])
                | {
                    "other_names": sorted({d.original_name for d in group if d.original_name != main.original_name}),
                    "copies": len(group),
                    "completed_at": max((d.completed_at for d in group if d.completed_at), default=None),
                }
            )
    else:
        entries = [_entry(d, apps.get(d.id, [])) for d in docs]
    entries.sort(key=lambda e: (e["volume"] is None, e["volume"] or 0, e["name"] or ""))
    chats = sorted({a["chat_title"] or "" for e in entries for a in e["appearances"]})
    return {
        "key": key,
        "series": key.startswith("s:"),
        "title": entries[0]["name"],
        "size": sum(e["size"] for e in entries),
        "chats": chats,
        "entries": entries,
    }


def _entry(d: Document, appearances: list[dict]) -> dict:
    appearances = sorted(appearances, key=lambda a: a["posted_at"] or datetime.min)
    return {
        "id": d.id,
        "name": d.original_name,
        "ext": d.ext,
        "size": d.size,
        "status": d.status,
        "volume": d.volume,
        "has_file": d.blob_id is not None,
        "bytes_downloaded": d.bytes_downloaded,
        "last_error": d.last_error,
        "first_seen_at": d.first_seen_at,
        "completed_at": d.completed_at,
        "caption": next((a["caption"] for a in appearances if a["caption"]), None),
        "appearances": appearances,
    }


@router.get("/library/chats")
async def library_chats(ctx: Container = Depends(get_ctx)):
    """Chats that have files, with how many are downloaded and available."""
    counts = await ctx.chat_counts.get()
    async with ctx.db.read() as s:
        rows = (await s.execute(select(Chat.id, Chat.title, Chat.type).order_by(Chat.title))).all()
    return [
        {"id": r.id, "title": r.title, "type": r.type, "downloaded": c.stored, "available": c.available}
        for r in rows
        if (c := counts.get(r.id)) is not None and (c.stored or c.available)
    ]


@router.get("/library/filters")
async def library_filters(ctx: Container = Depends(get_ctx)):
    """Values for the library filter dropdowns."""
    async with ctx.db.read() as s:
        exts = (
            await s.execute(
                select(Document.ext, func.count())
                .where(Document.ext.is_not(None), Document.status != DocStatus.SKIPPED)
                .group_by(Document.ext)
                .order_by(func.count().desc())
            )
        ).all()
    return {"exts": [{"ext": e.lstrip("."), "count": c} for e, c in exts]}


@router.delete("/library/{doc_id}")
async def delete_book(doc_id: int, ctx: Container = Depends(get_ctx)):
    sha = await ctx.engine.delete_book(doc_id)
    if sha is None:
        raise api_error(404, "not_found", "الكتاب غير موجود في المكتبة.")
    ctx.thumbs.remove(sha)
    return {"ok": True}


# ---------------------------------------------------------------- files
async def _stored_file(ctx: Container, doc_id: int):
    async with ctx.db.read() as s:
        row = (
            await s.execute(
                select(Document.original_name, Document.mime, Document.ext, Blob.storage_path, Blob.sha256)
                .join(Blob, Blob.id == Document.blob_id)
                .where(Document.id == doc_id)
            )
        ).first()
    if row is None:
        raise api_error(404, "not_found", "الملف غير موجود أو لم يُنزَّل بعد.")
    root = ctx.settings.storage_dir.resolve()
    path = (root / row.storage_path).resolve()
    if root not in path.parents or not path.is_file():
        raise api_error(404, "missing_on_disk", "الملف غير موجود على القرص. ربما نُقل أو حُذف.")
    return row, path


@router.get("/thumbs/{doc_id}")
async def thumbnail(doc_id: int, ctx: Container = Depends(get_ctx)):
    row, path = await _stored_file(ctx, doc_id)
    thumb = await ctx.thumbs.get(row.sha256, path, row.ext)
    if thumb is None:
        raise api_error(404, "no_thumbnail", "لا توجد صورة غلاف لهذا الملف.")
    return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})


@router.get("/files/{doc_id}")
async def get_file(doc_id: int, download: bool = False, ctx: Container = Depends(get_ctx)):
    row, path = await _stored_file(ctx, doc_id)
    # Files come from strangers' channels. Only types the browser renders
    # without running scripts may open inline on this origin; anything else
    # (HTML, SVG, ...) is forced to download and sandboxed.
    mime = row.mime if row.mime in INLINE_SAFE_MIMES else "application/octet-stream"
    inline = not download and mime in INLINE_SAFE_MIMES
    headers = {"X-Content-Type-Options": "nosniff"}
    if mime != "application/pdf":
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return FileResponse(
        path,
        media_type=mime,
        filename=Path(row.original_name or path.name).name,
        content_disposition_type="inline" if inline else "attachment",
        headers=headers,
    )


# Pictures, video and sound can't run scripts either (SVG can: it stays out).
INLINE_SAFE_MIMES = {
    "application/pdf", "text/plain",
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp",
    "video/mp4", "video/webm",
    "audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav", "audio/x-wav", "audio/flac", "audio/aac",
}
