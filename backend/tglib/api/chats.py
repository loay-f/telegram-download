import asyncio

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from tglib.api.deps import api_error, get_ctx, require_auth
from tglib.app.container import Container
from tglib.db.models import Chat, Document
from tglib.engine.catalog import hide_rejected, reapply_filters, record_explicit
from tglib.engine.errors import ChatUnavailable, FloodWait, NotAuthorized, NotConnected, TelegramError
from tglib.engine.history import PAGE_MAX
from tglib.engine.normalize import fold
from tglib.engine.settings_store import clean_exts
from tglib.engine.telegram import ChatMessage, DialogInfo

router = APIRouter(prefix="/api/chats", tags=["chats"], dependencies=[Depends(require_auth)])


def _require_ready(ctx: Container) -> None:
    if not ctx.account.ready:
        raise api_error(409, "telegram_not_ready", "حساب تيليغرام غير متصل الآن.")


async def _chat_or_404(ctx: Container, chat_id: int) -> Chat:
    async with ctx.db.read() as s:
        chat = await s.get(Chat, chat_id)
    if chat is None:
        raise api_error(404, "not_found", "المحادثة غير موجودة.")
    return chat


def _fold(text: str | None) -> str:
    return fold(text or "")


@router.get("")
async def list_chats(q: str = "", refresh: bool = False, ctx: Container = Depends(get_ctx)):
    """Answers from the local copy right away. A stale copy (or `refresh`)
    starts a background sync from Telegram; the page follows its progress
    through the `dialogs` part of the live status and refetches."""
    ctx.dialogs.request(force=refresh)
    # Taken before reading the table, so the list is never older than the
    # status sent with it (a batch may be saved while the query runs).
    sync = ctx.dialogs.status()

    async with ctx.db.read() as s:
        chats = (await s.execute(select(Chat))).scalars().all()
    counts = await ctx.chat_counts.get()

    needle = _fold(q.strip())
    scanning = ctx.scanner.current.chat_id if ctx.scanner.current else None
    queued = set(ctx.scanner.queued_ids())
    out = []
    for c in chats:
        if not c.is_member and not c.tracked:
            continue
        if needle and needle not in _fold(c.title) and needle not in _fold(c.username):
            continue
        cnt = counts.get(c.id)
        out.append(
            {
                "id": c.id,
                "title": c.title,
                "username": c.username,
                "type": c.type,
                "tracked": c.tracked,
                "is_member": c.is_member,
                "last_scan_at": c.last_scan_at,
                "last_scan_error": c.last_scan_error,
                "allowed_exts": c.allowed_exts,
                "max_size_mb": c.max_size_mb,
                "files": cnt.files if cnt else 0,
                "downloaded": cnt.downloaded if cnt else 0,
                "pending": cnt.pending if cnt else 0,
                "available": cnt.available if cnt else 0,
                "auto_download": c.auto_download,
                "scanning": c.id == scanning,
                "queued": c.id in queued and c.id != scanning,
            }
        )
    out.sort(key=lambda c: (not c["tracked"], _fold(c["title"])))
    return {"chats": out, "dialogs": sync}


class TrackIn(BaseModel):
    tracked: bool


@router.post("/{chat_id}/track")
async def set_tracked(chat_id: int, body: TrackIn, ctx: Container = Depends(get_ctx)):
    await _chat_or_404(ctx, chat_id)
    async with ctx.db.write() as s:
        chat = await s.get(Chat, chat_id)
        chat.tracked = body.tracked
    if body.tracked and ctx.account.ready:
        ctx.scanner.enqueue(chat_id)
    return {"ok": True}


class ChatSettingsIn(BaseModel):
    allowed_exts: str | None = None  # null/empty = use global setting
    max_size_mb: int | None = Field(default=None, ge=0)
    auto_download: bool | None = None  # None = leave unchanged


@router.post("/{chat_id}/settings")
async def chat_settings(chat_id: int, body: ChatSettingsIn, ctx: Container = Depends(get_ctx)):
    await _chat_or_404(ctx, chat_id)
    async with ctx.db.write() as s:
        chat = await s.get(Chat, chat_id)
        fields = body.model_fields_set
        if "allowed_exts" in fields:
            chat.allowed_exts = clean_exts(body.allowed_exts) if body.allowed_exts else None
        if "max_size_mb" in fields:
            chat.max_size_mb = body.max_size_mb
        if body.auto_download is not None:
            chat.auto_download = body.auto_download
        # The chat's own file types apply to what is already listed too.
        reopened = hidden = 0
        if fields & {"allowed_exts", "max_size_mb"}:
            await s.flush()
            reopened = await reapply_filters(s, ctx.app_settings.current)
            hidden = await hide_rejected(s, ctx.app_settings.current)
    if reopened:
        ctx.engine.wake()
    return {"ok": True, "reopened": reopened, "hidden": hidden}


@router.post("/{chat_id}/download-all")
async def download_all(chat_id: int, ctx: Container = Depends(get_ctx)):
    """Queues every file found in this chat that isn't downloaded yet."""
    await _chat_or_404(ctx, chat_id)
    return {"queued": await ctx.engine.request_chat(chat_id)}


@router.post("/{chat_id}/scan")
async def scan_now(chat_id: int, ctx: Container = Depends(get_ctx)):
    _require_ready(ctx)
    chat = await _chat_or_404(ctx, chat_id)
    if not chat.tracked:
        raise api_error(400, "not_tracked", "فعّل متابعة المحادثة أولاً.")
    ctx.scanner.enqueue(chat_id)
    return ctx.scanner.status()


@router.post("/scan-all")
async def scan_all(ctx: Container = Depends(get_ctx)):
    _require_ready(ctx)
    await ctx.scanner.enqueue_all_tracked()
    return ctx.scanner.status()


# ------------------------------------------------------- reading a chat
def _telegram_error(e: TelegramError):
    if isinstance(e, FloodWait):
        err = api_error(429, "flood", f"طلب تيليغرام الانتظار {e.seconds} ثانية. حاول بعد قليل.")
        err.headers = {"Retry-After": str(e.seconds)}
        return err
    if isinstance(e, ChatUnavailable):
        return api_error(404, "chat_unavailable", "لا يمكن الوصول إلى هذه المحادثة. ربما غادرتها أو حُذفت.")
    if isinstance(e, NotAuthorized):
        return api_error(409, "telegram_not_ready", "انتهت جلسة تيليغرام. سجّل الدخول مرة أخرى.")
    if isinstance(e, NotConnected):
        return api_error(503, "offline", "تعذّر الاتصال بتيليغرام الآن. حاول بعد قليل.")
    return api_error(502, "telegram", f"خطأ من تيليغرام: {e}")


def _file_json(doc: Document | None) -> dict:
    """Where a message's file stands in the library."""
    return {
        "doc_id": doc.id if doc else None,
        "status": doc.status if doc else None,
        "has_file": bool(doc and doc.blob_id),
    }


def _message_json(m: ChatMessage, known: dict[int, Document]) -> dict:
    media = None
    if m.media is not None:
        media = {
            "kind": m.media.kind,
            "name": m.media.name,
            "size": m.media.size,
            "duration": m.media.duration,
            "width": m.media.width,
            "height": m.media.height,
            "preview": m.media.has_preview,
            "file": None,
        }
        if m.media.document is not None:
            d = m.media.document
            media["file"] = {"ext": d.ext, **_file_json(known.get(d.tg_document_id))}
    return {
        "id": m.id,
        "date": m.date,
        "text": m.text,
        "entities": [
            {"type": e.type, "offset": e.offset, "length": e.length, "url": e.url} for e in m.entities
        ],
        "out": m.out,
        "sender": m.sender,
        "reply_to": m.reply_to,
        "forwarded_from": m.forwarded_from,
        "edited": m.edited,
        "service": m.service,
        "media": media,
    }


async def _known_documents(ctx: Container, page: list[ChatMessage]) -> dict[int, Document]:
    """The library's record of the files in these messages, by Telegram id."""
    tg_ids = [m.media.document.tg_document_id for m in page if m.media and m.media.document]
    if not tg_ids:
        return {}
    async with ctx.db.read() as s:
        docs = (await s.execute(select(Document).where(Document.tg_document_id.in_(tg_ids)))).scalars()
        return {d.tg_document_id: d for d in docs}


@router.get("/{chat_id}/messages")
async def chat_messages(
    chat_id: int,
    before: int = Query(0, ge=0),
    after: int = Query(0, ge=0),
    around: int = Query(0, ge=0),
    forward: bool = False,
    limit: int = Query(50, ge=1, le=PAGE_MAX),
    ctx: Container = Depends(get_ctx),
):
    """A page of the chat's messages for reading, oldest first.

    By default: the newest `limit` messages with id > `after` and (if
    `before`) id < `before`. `has_more` says the page was full, so there are
    more messages beyond it: older ones, or — when asking what came `after` —
    a gap up to this page.

    To read from the middle of the history (a search result): `around` gives
    message `around` with `limit`/2 messages on each side, and `after` with
    `forward` the `limit` messages that follow `after`. `has_newer` says the
    page doesn't reach the chat's newest message."""
    _require_ready(ctx)
    chat = await _chat_or_404(ctx, chat_id)
    has_newer = False
    try:
        if around:
            half = max(1, limit // 2)
            newer, older = await asyncio.gather(
                ctx.history.page(chat_id, after=around, limit=half, forward=True),
                ctx.history.page(chat_id, before=around + 1, limit=half),
            )
            page = newer + older
            has_more = len(older) == half and older[-1].id > 1
            has_newer = len(newer) == half
        elif forward:
            page = await ctx.history.page(chat_id, after=after, limit=limit, forward=True)
            has_more = False
            has_newer = len(page) == limit
        else:
            page = await ctx.history.page(chat_id, before=before, after=after, limit=limit)
            has_more = len(page) == limit and page[-1].id > 1
    except TelegramError as e:
        raise _telegram_error(e)
    known = await _known_documents(ctx, page)
    return {
        "chat": {"id": chat.id, "title": chat.title, "username": chat.username, "type": chat.type},
        "messages": [_message_json(m, known) for m in reversed(page)],
        "has_more": has_more,
        "has_newer": has_newer,
    }


@router.get("/{chat_id}/messages/search")
async def search_chat_messages(
    chat_id: int,
    q: str = Query(max_length=200),
    before: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=PAGE_MAX),
    ctx: Container = Depends(get_ctx),
):
    """The chat's messages that match `q`, newest first, as Telegram's own
    search finds them. `before` continues below the last result shown;
    `has_more` says the page was full, so there may be more."""
    _require_ready(ctx)
    await _chat_or_404(ctx, chat_id)
    query = " ".join(q.split())
    if not query:
        return {"messages": [], "has_more": False}
    try:
        page = await ctx.history.search(chat_id, query, before=before, limit=limit)
    except TelegramError as e:
        raise _telegram_error(e)
    known = await _known_documents(ctx, page)
    return {"messages": [_message_json(m, known) for m in page], "has_more": len(page) == limit}


def _image_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


@router.get("/{chat_id}/messages/{msg_id}/preview")
async def message_preview(chat_id: int, msg_id: int, full: bool = False, ctx: Container = Depends(get_ctx)):
    """The picture of a message: its photo (small, or full size with `full`)
    or the thumbnail of its video or sticker."""
    _require_ready(ctx)
    await _chat_or_404(ctx, chat_id)
    try:
        data = await ctx.history.preview(chat_id, msg_id, full)
    except TelegramError as e:
        raise _telegram_error(e)
    if data is None:
        raise api_error(404, "no_preview", "لا توجد صورة لهذه الرسالة.")
    return Response(
        data,
        media_type=_image_type(data),
        # The browser keeps it: scrolling back doesn't ask Telegram again.
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/{chat_id}/messages/{msg_id}/download")
async def download_message_file(chat_id: int, msg_id: int, ctx: Container = Depends(get_ctx)):
    """Adds the file in this message to the library and queues its download."""
    _require_ready(ctx)
    chat = await _chat_or_404(ctx, chat_id)
    try:
        dm = await ctx.account.gateway.get_document_message(chat_id, msg_id)
    except TelegramError as e:
        raise _telegram_error(e)
    if dm is None:
        raise api_error(404, "no_file", "لا يوجد ملف في هذه الرسالة، أو أنها حُذفت.")
    info = DialogInfo(chat.id, chat.title or "", chat.username, chat.type or "channel")
    async with ctx.db.write() as s:
        doc_id = (await record_explicit(s, info, dm, is_member=chat.is_member)).id
    result = await ctx.engine.request([doc_id], to_front=True)
    async with ctx.db.read() as s:
        doc = await s.get(Document, doc_id)
    return {"ext": dm.document.ext, **_file_json(doc), "queued": result.queued > 0}
