"""Cover thumbnails rendered from the first page of downloaded books."""

import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# Formats PyMuPDF can open.
RENDERABLE = {".pdf", ".epub", ".mobi", ".fb2", ".xps", ".cbz"}
WIDTH = 240


def _render(path: Path, out: Path, ext: str) -> bool:
    try:
        import pymupdf

        with pymupdf.open(path, filetype=ext.lstrip(".")) as doc:
            if doc.page_count == 0:
                return False
            page = doc[0]
            zoom = WIDTH / max(page.rect.width, 1)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            tmp = out.with_suffix(".tmp")
            pix.save(str(tmp), output="jpeg", jpg_quality=75)
        os.replace(tmp, out)
        return True
    except Exception as e:
        log.info("No thumbnail for %s: %s", path.name, e)
        return False


class Thumbnails:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "thumbs"
        self._sem = asyncio.Semaphore(2)

    async def get(self, sha: str, path: Path, ext: str | None) -> Path | None:
        out = self.dir / f"{sha}.jpg"
        failed = self.dir / f"{sha}.none"
        if out.exists():
            return out
        if failed.exists() or (ext or "").lower() not in RENDERABLE or not path.is_file():
            return None
        self.dir.mkdir(parents=True, exist_ok=True)
        async with self._sem:
            if out.exists():
                return out
            if await asyncio.to_thread(_render, path, out, ext.lower()):
                return out
        failed.touch()  # don't try again for this content
        return None

    def remove(self, sha: str) -> None:
        for suffix in (".jpg", ".none"):
            (self.dir / f"{sha}{suffix}").unlink(missing_ok=True)
