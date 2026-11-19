"""Finding files on Telegram: search across all chats, or a pasted t.me link."""

import time

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from tglib.api.deps import api_error, get_ctx, require_auth
from tglib.app.container import Container
from tglib.db.models import Chat, Document
from tglib.engine.catalog import ensure_chat, record_explicit
from tglib.engine.errors import ChatUnavailable, FloodWait, NotConnected, TelegramError
from tglib.engine.history import PAGE_MAX
from tglib.engine.links import parse_message_link
from tglib.engine.telegram import DialogInfo, DocMessage

router = APIRouter(prefix="/api", tags=["search"], dependencies=[Depends(require_auth)])

SEARCH_LIMIT = 100
CACHE_TTL = 3600
CACHE_MAX = 3000


def _require_ready(ctx: Container) -> None:
    if not ctx.account.ready:
        raise api_error(409, "telegram_not_ready", "حساب تيليغرام غير متصل الآن.")


def _telegram_error(e: TelegramError):
    if isinstance(e, FloodWait):
        return api_error(429, "flood", f"طلب تيليغرام الانتظار {e.seconds} ثانية. حاول بعد قليل.")
    if isinstance(e, NotConnected):
        return api_error(503, "offline", "تعذّر الاتصال بتيليغرام الآن. حاول بعد قليل.")
    return api_error(502, "telegram", f"خطأ من تيليغرام: {e}")


async def _ensure_chats(ctx: Container, chats: list[DialogInfo]) -> None:
    """Search finds messages in chats the local list may not have yet; a row
    for each lets its result be opened and read."""
    wanted = {c.id: c for c in chats}
    if not wanted:
        return
    async with ctx.db.read() as s:
        have = set((await s.execute(select(Chat.id).where(Chat.id.in_(wanted)))).scalars())
    missing = [c for i, c in wanted.items() if i not in have]
    if missing:
        async with ctx.db.write() as s:
            for c in missing:
                await ensure_chat(s, c, is_member=True)


def _remember(ctx: Container, dm: DocMessage, chat: DialogInfo) -> None:
    cache = ctx.search_cache
    now = time.time()
    if len(cache) > CACHE_MAX:
        for k in [k for k, (_, _, at) in cache.items() if now - at > CACHE_TTL] or list(cache)[: len(cache) // 2]:
            cache.pop(k, None)
    cache[(dm.chat_id, dm.msg_id)] = (dm, chat, now)


async def _known(ctx: Container, tg_ids: list[int]) -> dict[int, Document]:
    if not tg_ids:
        return {}
    async with ctx.db.read() as s:
        docs = (await s.execute(select(Document).where(Document.tg_document_id.in_(tg_ids)))).scalars()
        return {d.tg_document_id: d for d in docs}


def _hit(dm: DocMessage, chat: DialogInfo, known: Document | None) -> dict:
    return {
        "chat": {"id": chat.id, "title": chat.title, "type": chat.type},
        "msg_id": dm.msg_id,
        "date": dm.date,
        "caption": dm.caption,
        "name": dm.document.file_name or f"telegram-{dm.document.tg_document_id}{dm.document.ext or ''}",
        "ext": dm.document.ext,
        "size": dm.document.size,
        "doc_id": known.id if known else None,
        "status": known.status if known else None,
        "has_file": bool(known and known.blob_id),
    }


@router.get("/search")
async def search(q: str = Query(..., min_length=2, max_length=200), ctx: Container = Depends(get_ctx)):
    """Files whose name or caption matches, across all of the account's chats."""
    _require_ready(ctx)
    try:
        hits = await ctx.account.gateway.search_documents(q.strip(), SEARCH_LIMIT)
    except TelegramError as e:
        raise _telegram_error(e)
    known = await _known(ctx, [dm.document.tg_document_id for dm, _ in hits])
    for dm, chat in hits:
        _remember(ctx, dm, chat)
    await _ensure_chats(ctx, [chat for _, chat in hits])
    return {"items": [_hit(dm, chat, known.get(dm.document.tg_document_id)) for dm, chat in hits]}


@router.get("/search/messages")
async def search_messages(
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(30, ge=1, le=PAGE_MAX),
    ctx: Container = Depends(get_ctx),
):
    """Messages that match, across all of the account's chats, newest first.
    Each can be opened in its chat (`/api/chats/{id}/messages?around=`).
    Messages that are a file are left to the search for files (`/api/search`).
    `has_more` says Telegram's page was full: a larger `limit` may find more."""
    _require_ready(ctx)
    query = " ".join(q.split())
    if len(query) < 2:
        return {"items": [], "has_more": False}
    try:
        found = await ctx.history.search_all(query, limit=limit)
    except TelegramError as e:
        raise _telegram_error(e)
    full = len(found) == limit
    found = [m for m in found if not (m.media and m.media.kind == "file")]
    await _ensure_chats(ctx, [m.chat for m in found])
    return {
        "items": [
            {
                "chat": {"id": m.chat.id, "title": m.chat.title, "type": m.chat.type},
                "id": m.id,
                "date": m.date,
                "text": m.text,
                "out": m.out,
                "sender": m.sender,
                "service": m.service,
                "media": {"kind": m.media.kind, "name": m.media.name} if m.media else None,
            }
            for m in found
        ],
        "has_more": full,
    }


async def _record_and_queue(ctx: Container, dm: DocMessage, chat: DialogInfo, is_member: bool) -> dict:
    async with ctx.db.write() as s:
        doc = await record_explicit(s, chat, dm, is_member=is_member)
        doc_id = doc.id
    result = await ctx.engine.request([doc_id], to_front=True)
    known = await _known(ctx, [dm.document.tg_document_id])
    return {**_hit(dm, chat, known.get(dm.document.tg_document_id)), "queued": result.queued > 0}


class PickIn(BaseModel):
    chat_id: int
    msg_id: int


@router.post("/search/download")
async def download_result(body: PickIn, ctx: Container = Depends(get_ctx)):
    _require_ready(ctx)
    cached = ctx.search_cache.get((body.chat_id, body.msg_id))
    if cached is None:
        raise api_error(410, "stale", "انتهت صلاحية نتيجة البحث. ابحث مرة أخرى.")
    dm, chat, _ = cached
    return await _record_and_queue(ctx, dm, chat, is_member=True)


class LinkIn(BaseModel):
    url: str


@router.post("/links/download")
async def download_link(body: LinkIn, ctx: Container = Depends(get_ctx)):
    """Downloads the file in one message given by its t.me link."""
    _require_ready(ctx)
    link = parse_message_link(body.url)
    if link is None:
        raise api_error(
            400, "bad_link",
            "هذا ليس رابط رسالة من تيليغرام. الرابط الصحيح يشبه ‎https://t.me/اسم_القناة/123",
        )
    try:
        found = await ctx.account.gateway.resolve_link(link.peer, link.msg_id)
    except ChatUnavailable:
        raise api_error(
            404, "chat_unavailable",
            "لا يمكن الوصول إلى هذه المحادثة. إن كانت خاصة فيجب أن تكون عضواً فيها.",
        )
    except TelegramError as e:
        raise _telegram_error(e)
    if found is None:
        raise api_error(404, "no_file", "لا يوجد ملف في هذه الرسالة، أو أنها حُذفت.")
    dm, chat = found
    return await _record_and_queue(ctx, dm, chat, is_member=False)
