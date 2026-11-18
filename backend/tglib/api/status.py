"""Stats, settings, engine control and the realtime event stream."""

import asyncio
import json
import shutil
import time

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from tglib.api.deps import get_ctx, require_auth
from tglib.app.container import Container
from tglib.engine.catalog import reapply_filters
from tglib.engine.stats import compute_stats

router = APIRouter(prefix="/api", tags=["status"], dependencies=[Depends(require_auth)])

@router.get("/stats")
async def stats(ctx: Container = Depends(get_ctx)):
    return await compute_stats(ctx.db)


# ------------------------------------------------------------- settings
def _storage(ctx: Container) -> dict:
    try:
        usage = shutil.disk_usage(ctx.settings.storage_dir)
        disk = {"total": usage.total, "free": usage.free}
    except OSError:
        disk = {"total": 0, "free": 0}
    tmp = sum(p.stat().st_size for p in ctx.settings.tmp_dir.glob("*.part") if p.is_file())
    return {**disk, "partial_bytes": tmp, "path": str(ctx.settings.storage_dir)}


@router.get("/settings")
async def get_settings(ctx: Container = Depends(get_ctx)):
    s = ctx.app_settings.current
    return {
        "allowed_exts": s.allowed_exts,
        "max_size_mb": s.max_size_mb,
        "workers": s.workers,
        "scan_interval_hours": s.scan_interval_hours,
        "max_attempts": s.max_attempts,
        "speed_limit_kbps": s.speed_limit_kbps,
        "schedule_enabled": bool(s.schedule_enabled),
        "schedule_start": s.schedule_start,
        "schedule_end": s.schedule_end,
        "timezone": s.timezone,
        "summary_enabled": bool(s.summary_enabled),
        "summary_hour": s.summary_hour,
        "storage": _storage(ctx),
        "library_bytes": (await ctx.stats.get())["library_bytes"],
    }


class SettingsIn(BaseModel):
    allowed_exts: str | None = None
    max_size_mb: int | None = Field(default=None, ge=0)
    workers: int | None = Field(default=None, ge=1, le=10)
    scan_interval_hours: int | None = Field(default=None, ge=1, le=168)
    max_attempts: int | None = Field(default=None, ge=1, le=50)
    speed_limit_kbps: int | None = Field(default=None, ge=0)
    schedule_enabled: bool | None = None
    schedule_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    schedule_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str | None = Field(default=None, max_length=64)
    summary_enabled: bool | None = None
    summary_hour: int | None = Field(default=None, ge=0, le=23)


@router.post("/settings")
async def update_settings(body: SettingsIn, ctx: Container = Depends(get_ctx)):
    before = ctx.app_settings.current
    changes = {k: int(v) if isinstance(v, bool) else v for k, v in body.model_dump(exclude_none=True).items()}
    after = await ctx.app_settings.update(changes)
    reopened = 0
    if (before.allowed_exts, before.max_size_mb) != (after.allowed_exts, after.max_size_mb):
        async with ctx.db.write() as s:
            reopened = await reapply_filters(s, after)
        if reopened:
            ctx.engine.wake()
    ctx.engine.wake()  # a new schedule may allow downloading right now
    result = await get_settings(ctx)
    result["reopened"] = reopened
    return result


# --------------------------------------------------------------- engine
@router.get("/engine")
async def engine_status(ctx: Container = Depends(get_ctx)):
    return ctx.engine.status()


@router.post("/engine/pause")
async def pause(ctx: Container = Depends(get_ctx)):
    await ctx.engine.set_paused(True)
    return ctx.engine.status()


@router.post("/engine/resume")
async def resume(ctx: Container = Depends(get_ctx)):
    await ctx.engine.set_paused(False)
    return ctx.engine.status()


# --------------------------------------------------------------- events
async def snapshot(ctx: Container) -> dict:
    return {
        "engine": ctx.engine.status(),
        "scan": ctx.scanner.status(),
        "account": ctx.account.status(),
        "dialogs": ctx.dialogs.status(),
        "stats": await ctx.stats.get(),
    }


def sse(event: str, data) -> str:
    payload = json.dumps(jsonable_encoder(data), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


@router.get("/events")
async def events(request: Request, ctx: Container = Depends(get_ctx)):
    """Server-Sent Events: a full `status` snapshot every second (download
    progress, engine state, scan progress, counters) plus immediate events
    for state changes."""

    async def stream():
        async with ctx.events.subscribe() as queue:
            yield "retry: 3000\n\n"
            yield sse("status", await snapshot(ctx))
            next_snapshot = time.monotonic() + 1
            while not await request.is_disconnected():
                timeout = max(0.0, next_snapshot - time.monotonic())
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout)
                    yield sse(kind, data)
                except asyncio.TimeoutError:
                    yield sse("status", await snapshot(ctx))
                    next_snapshot = time.monotonic() + 1

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
