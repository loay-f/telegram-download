import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from tglib.api import auth as auth_api
from tglib.api import chats as chats_api
from tglib.api import documents as documents_api
from tglib.api import status as status_api
from tglib.api import telegram as telegram_api
from tglib.app.config import Settings, get_settings
from tglib.app.container import Container
from tglib.app.logging_setup import setup_logging
from tglib.db.migrate import upgrade_to_head
from tglib.engine.telegram import TelegramGateway

log = logging.getLogger(__name__)

PLACEHOLDER = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
<title>مكتبة تيليغرام</title><body style="font-family:sans-serif;padding:2rem">
<h1>مكتبة تيليغرام</h1><p>الواجهة لم تُبنَ بعد.</p></body></html>"""


def create_app(
    settings: Settings | None = None,
    gateway_factory: Callable[[], TelegramGateway] | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        setup_logging(settings.data_dir, settings.log_level)
        await asyncio.to_thread(upgrade_to_head, settings.db_path)
        ctx = Container(settings, gateway_factory)
        app.state.ctx = ctx
        await ctx.start()
        log.info("Started; data=%s storage=%s", settings.data_dir, settings.storage_dir)
        if not settings.app_password:
            log.warning("APP_PASSWORD is not set; the web UI will refuse logins.")
        try:
            yield
        finally:
            await ctx.stop()

    app = FastAPI(title="TG Library", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    app.include_router(auth_api.router)
    app.include_router(telegram_api.router)
    app.include_router(chats_api.router)
    app.include_router(documents_api.router)
    app.include_router(status_api.router)

    _mount_frontend(app, settings.frontend_dir)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        # Unknown /api paths must 404 instead of returning the SPA.
        if path == "api" or path.startswith("api/"):
            return HTMLResponse("Not found", status_code=404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and root in candidate.parents:
            # Hashed asset names never change content; cache them for a long time.
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(candidate, headers={"Cache-Control": cache})
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(PLACEHOLDER)


app = create_app()
