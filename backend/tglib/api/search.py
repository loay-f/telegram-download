"""Finding files on Telegram: search across all chats, or a pasted t.me link."""

import time

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from tglib.api.deps import api_error, get_ctx, require_auth
from tglib.app.container import Container
from tglib.db.models import Document
from tglib.engine.catalog import record_explicit
from tglib.engine.errors import ChatUnavailable, FloodWait, TelegramError
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
    return api_error(502, "telegram", f"خطأ من تيليغرام: {e}")


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
    return {"items": [_hit(dm, chat, known.get(dm.document.tg_document_id)) for dm, chat in hits]}


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
