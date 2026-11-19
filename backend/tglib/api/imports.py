"""Adding files that are already on this computer to the library."""

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from tglib.api.deps import api_error, get_ctx, require_auth
from tglib.app.container import Container
from tglib.engine.importer import FolderError, list_folder, shortcuts

router = APIRouter(prefix="/api/import", tags=["import"], dependencies=[Depends(require_auth)])


def _state(ctx: Container) -> dict:
    return {
        "job": ctx.importer.status(),
        "storage_path": str(ctx.settings.storage_dir),
        # In Docker the app sees the container's folders, not the computer's.
        "in_docker": Path("/.dockerenv").exists(),
    }


@router.get("")
async def status(ctx: Container = Depends(get_ctx)):
    return _state(ctx)


class ImportIn(BaseModel):
    folders: list[str] = Field(min_length=1, max_length=50)
    mode: Literal["keep", "copy", "move"] = "keep"
    # Only report what would be added; change nothing.
    check_only: bool = False


@router.post("")
async def start(body: ImportIn, ctx: Container = Depends(get_ctx)):
    for folder in body.folders:
        p = Path(folder)
        if not p.is_absolute() or not await asyncio.to_thread(p.is_dir):
            raise api_error(400, "not_found", f"هذا المجلد غير موجود: {folder}")
    try:
        ctx.importer.start(body.folders, body.mode, body.check_only)
    except FolderError as e:
        raise api_error(409, e.code, e.message)
    return _state(ctx)


@router.post("/stop")
async def stop(ctx: Container = Depends(get_ctx)):
    ctx.importer.request_stop()
    return _state(ctx)


@router.get("/folders")
async def folders(path: str | None = None, ctx: Container = Depends(get_ctx)):
    """Without `path`: the places to start from. With it: what is inside."""
    if not path:
        return {"shortcuts": await asyncio.to_thread(shortcuts, ctx.settings.storage_dir)}
    try:
        return await asyncio.to_thread(list_folder, path)
    except FolderError as e:
        raise api_error(400, e.code, e.message)
