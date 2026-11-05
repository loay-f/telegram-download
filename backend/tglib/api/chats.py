import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from tglib.api.deps import api_error, get_ctx, require_auth
from tglib.app.container import Container
from tglib.db.models import Appearance, Chat, Document, DocStatus
from tglib.engine.dialogs import sync_dialogs
from tglib.engine.errors import FloodWait, TelegramError
from tglib.engine.normalize import fold
from tglib.engine.settings_store import clean_exts

router = APIRouter(prefix="/api/chats", tags=["chats"], dependencies=[Depends(require_auth)])

DIALOG_REFRESH_SECONDS = 600


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
    stale = (
        time.time() - ctx.dialogs_synced_at > DIALOG_REFRESH_SECONDS
        # Logged in (again) since the last sync: the list may be another account's.
        or ctx.dialogs_synced_at < ctx.account.ready_at
    )
    if (refresh or stale) and ctx.account.ready:
        try:
            await sync_dialogs(ctx.db, ctx.account.gateway)
            ctx.dialogs_synced_at = time.time()
        except FloodWait as e:
            if refresh:
                raise api_error(429, "flood", f"طلب تيليغرام الانتظار {e.seconds} ثانية قبل التحديث.")
        except TelegramError as e:
            if refresh:
                raise api_error(502, "telegram", f"تعذّر جلب المحادثات من تيليغرام: {e}")

    downloaded = case((Document.status.in_((DocStatus.DONE, DocStatus.DUPLICATE)), 1), else_=0)
    pending = case((Document.status.in_((DocStatus.PENDING, DocStatus.DOWNLOADING)), 1), else_=0)
    available = case((Document.status == DocStatus.AVAILABLE, 1), else_=0)
    async with ctx.db.read() as s:
        chats = (await s.execute(select(Chat))).scalars().all()
        counts = {
            row.chat_id: row
            for row in await s.execute(
                select(
                    Appearance.chat_id,
                    func.count().label("files"),
                    func.sum(downloaded).label("downloaded"),
                    func.sum(pending).label("pending"),
                    func.sum(available).label("available"),
                )
                .join(Document, Document.id == Appearance.document_id)
                .group_by(Appearance.chat_id)
            )
        }

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
                "downloaded": int(cnt.downloaded or 0) if cnt else 0,
                "pending": int(cnt.pending or 0) if cnt else 0,
                "available": int(cnt.available or 0) if cnt else 0,
                "auto_download": c.auto_download,
                "scanning": c.id == scanning,
                "queued": c.id in queued and c.id != scanning,
            }
        )
    out.sort(key=lambda c: (not c["tracked"], _fold(c["title"])))
    return {"chats": out, "synced": ctx.dialogs_synced_at > 0}


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
    return {"ok": True}


@router.post("/{chat_id}/download-all")
async def download_all(chat_id: int, ctx: Container = Depends(get_ctx)):
    """Queues every file found in this chat that isn't downloaded yet."""
    await _chat_or_404(ctx, chat_id)
    async with ctx.db.read() as s:
        ids = (
            await s.execute(
                select(Document.id)
                .join(Appearance, Appearance.document_id == Document.id)
                .where(Appearance.chat_id == chat_id, Document.status == DocStatus.AVAILABLE)
            )
        ).scalars().all()
    result = await ctx.engine.request(list(ids))
    return {"queued": result.queued}


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
