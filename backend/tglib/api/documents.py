"""Queue (documents), library search, and file serving."""

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import and_, exists, func, or_, select

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
    """Matches the file name or any caption, ignoring Arabic spelling variants."""
    folded = fold(q.strip())
    # Same rules as stored names; the dummy ".x" keeps a dot in the query
    # (e.g. "vol.2") from being taken as an extension.
    norm = normalize_name(q.strip() + ".x")
    caption_hit = exists().where(
        Appearance.document_id == Document.id,
        func.fold(Appearance.caption).like(_like(folded), escape="\\"),
    )
    conds = [func.fold(Document.original_name).like(_like(folded), escape="\\"), caption_hit]
    if norm:
        conds.append(Document.normalized_name.like(_like(norm), escape="\\"))
    return or_(*conds)


async def _appearances(s, doc_ids: list[int]) -> dict[int, list[dict]]:
    if not doc_ids:
        return {}
    rows = await s.execute(
        select(Appearance.document_id, Appearance.chat_id, Appearance.msg_id, Appearance.posted_at,
               Appearance.caption, Chat.title, Chat.username)
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
                "link": _message_link(r.chat_id, r.username, r.msg_id),
            }
        )
    return out


def _message_link(chat_id: int, username: str | None, msg_id: int) -> str | None:
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


# -------------------------------------------------------------- library
@router.get("/library")
async def library(
    q: str = "",
    ext: str | None = None,
    chat_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: str = Query("recent", pattern="^(recent|name|size)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    ctx: Container = Depends(get_ctx),
):
    """One entry per stored file (blob); duplicates fold into it."""
    conds = [Document.blob_id.is_not(None)]
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

    grouped = (
        select(
            Document.blob_id.label("blob_id"),
            func.max(Document.completed_at).label("completed_at"),
            func.min(Document.original_name).label("name"),
        )
        .where(*conds)
        .group_by(Document.blob_id)
        .subquery()
    )
    order = {
        "recent": grouped.c.completed_at.desc(),
        "name": grouped.c.name,
        "size": Blob.size.desc(),
    }[sort]
    async with ctx.db.read() as s:
        total = (await s.execute(select(func.count()).select_from(grouped))).scalar()
        page_rows = (
            await s.execute(
                select(grouped.c.blob_id, Blob.size, Blob.sha256)
                .join(Blob, Blob.id == grouped.c.blob_id)
                .order_by(order, grouped.c.blob_id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        blob_ids = [r.blob_id for r in page_rows]
        docs = (
            await s.execute(
                select(Document).where(Document.blob_id.in_(blob_ids)).order_by(Document.id)
            )
        ).scalars().all() if blob_ids else []
        apps = await _appearances(s, [d.id for d in docs])

    by_blob: dict[int, list[Document]] = {}
    for d in docs:
        by_blob.setdefault(d.blob_id, []).append(d)
    items = []
    for r in page_rows:
        group = by_blob.get(r.blob_id, [])
        main = next((d for d in group if d.status == DocStatus.DONE), group[0] if group else None)
        if main is None:
            continue
        appearances = [a for d in group for a in apps.get(d.id, [])]
        appearances.sort(key=lambda a: a["posted_at"] or datetime.min)
        items.append(
            {
                "id": main.id,
                "name": main.original_name,
                "other_names": sorted({d.original_name for d in group if d.original_name != main.original_name}),
                "ext": main.ext,
                "mime": main.mime,
                "size": r.size,
                "completed_at": max((d.completed_at for d in group if d.completed_at), default=None),
                "copies": len(group),
                "appearances": appearances,
            }
        )
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/library/filters")
async def library_filters(ctx: Container = Depends(get_ctx)):
    """Values for the library filter dropdowns."""
    async with ctx.db.read() as s:
        exts = (
            await s.execute(
                select(Document.ext, func.count(func.distinct(Document.blob_id)))
                .where(Document.blob_id.is_not(None), Document.ext.is_not(None))
                .group_by(Document.ext)
                .order_by(func.count(func.distinct(Document.blob_id)).desc())
            )
        ).all()
        chats = (
            await s.execute(
                select(Chat.id, Chat.title)
                .where(exists().where(Appearance.chat_id == Chat.id))
                .order_by(Chat.title)
            )
        ).all()
    return {
        "exts": [{"ext": e.lstrip("."), "count": c} for e, c in exts],
        "chats": [{"id": i, "title": t} for i, t in chats],
    }


# ---------------------------------------------------------------- files
@router.get("/files/{doc_id}")
async def get_file(doc_id: int, download: bool = False, ctx: Container = Depends(get_ctx)):
    async with ctx.db.read() as s:
        row = (
            await s.execute(
                select(Document.original_name, Document.mime, Blob.storage_path)
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


INLINE_SAFE_MIMES = {"application/pdf", "text/plain"}
