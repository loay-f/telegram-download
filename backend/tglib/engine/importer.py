"""Adds files that are already on this computer to the library, from the app.

The work itself, and what a page shows of it (the run's progress, the folders
for a folder picker), is tools/register_files.py, the standalone program: it
is loaded from its file so there is one copy of it. Here is only what the app
adds: one run at a time off the event loop, and telling the rest of the app
that the tables changed.
"""

import asyncio
import importlib.util
import logging
from pathlib import Path

from tglib.app.config import Settings
from tglib.db.session import Database
from tglib.engine.events import EventBus

log = logging.getLogger(__name__)


def _load_tool():
    here = Path(__file__).resolve()
    # Next to the backend in the repository and the Docker image; inside the
    # bundle in the desktop build (where the bundle is the backend folder).
    for base in (here.parents[2], here.parents[3]):
        path = base / "tools" / "register_files.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location("tglib_register_files", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise RuntimeError("tools/register_files.py is missing from this installation")


tool = _load_tool()

FolderError = tool.FolderError
shortcuts = tool.shortcuts
list_folder = tool.list_folder


class Importer:
    def __init__(self, settings: Settings, db: Database, events: EventBus):
        self._settings = settings
        self._db = db
        self._events = events
        self.job = None  # tool.Job
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict | None:
        return self.job.as_dict() if self.job else None

    def start(self, folders: list[str], mode: str, check_only: bool) -> None:
        if self.running:
            raise FolderError("busy", "هناك إضافة تعمل الآن. انتظر حتى تنتهي أو أوقفها.")
        loop = asyncio.get_running_loop()
        self.job = tool.Job(folders, mode, check_only, lambda: loop.call_soon_threadsafe(self._registered))
        self._task = asyncio.create_task(self._run(self.job))

    def request_stop(self) -> None:
        if self.job and self.running:
            self.job.stop = True

    async def stop(self) -> None:
        """Shutdown: the run stops after the file it is on; what it registered is saved."""
        self.request_stop()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    def _registered(self) -> None:
        # The run writes through its own connection, so the app is told here
        # that the tables changed: counters are recomputed and open pages refetch.
        self._db.writes += 1
        self._db.version += 1
        self._events.publish("document", {"imported": True})

    async def _run(self, job) -> None:
        s = self._settings
        try:
            await asyncio.to_thread(tool.run_job, job, s.db_path, s.storage_dir, live=True)
        finally:
            if not job.check_only:
                self._registered()
        log.info(
            "Local files %s: %s (%s) %s %s",
            "checked" if job.check_only else "added", job.state, job.mode, job.counts, job.error or "",
        )
