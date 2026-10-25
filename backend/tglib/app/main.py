import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from tglib.app.config import Settings, get_settings
from tglib.app.logging_setup import setup_logging
from tglib.db.migrate import upgrade_to_head
from tglib.db.session import Database

log = logging.getLogger(__name__)

PLACEHOLDER = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
<title>مكتبة تيليغرام</title><body style="font-family:sans-serif;padding:2rem">
<h1>مكتبة تيليغرام</h1><p>الواجهة لم تُبنَ بعد.</p></body></html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        setup_logging(settings.data_dir, settings.log_level)
        await asyncio.to_thread(upgrade_to_head, settings.db_path)
        db = Database(settings.db_path)
        app.state.settings = settings
        app.state.db = db
        log.info("Started; data=%s storage=%s", settings.data_dir, settings.storage_dir)
        try:
            yield
        finally:
            await db.dispose()

    app = FastAPI(title="TG Library", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    _mount_frontend(app, settings.frontend_dir)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        # Unknown /api paths must 404 instead of returning the SPA.
        if path.startswith("api/"):
            return HTMLResponse("Not found", status_code=404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(PLACEHOLDER)


app = create_app()
