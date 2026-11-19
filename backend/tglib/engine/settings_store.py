"""User-tunable settings stored in the DB `settings` table, cached in memory."""

import logging
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from tglib.db.models import Setting
from tglib.db.session import Database

log = logging.getLogger(__name__)

# The kinds of file the settings page offers, by extension. Keep in step with
# frontend/src/lib/fileTypes.ts, which shows the same groups.
BOOK_EXTS = "pdf,epub,djvu,mobi,azw3,doc,docx,txt,md,rtf,odt"
ARCHIVE_EXTS = "zip,rar,7z"
IMAGE_EXTS = "jpg,jpeg,png,webp,gif,heic,heif,bmp,tif,tiff"
VIDEO_EXTS = "mp4,m4v,mkv,mov,avi,webm,3gp,wmv,flv,mpg,mpeg"
AUDIO_EXTS = "mp3,m4a,ogg,oga,opus,wav,flac,aac,wma,amr"
MEDIA_EXTS = f"{IMAGE_EXTS},{VIDEO_EXTS},{AUDIO_EXTS}"
DEFAULT_EXTS = f"{BOOK_EXTS},{ARCHIVE_EXTS},{MEDIA_EXTS}"


@dataclass
class AppSettings:
    allowed_exts: str = DEFAULT_EXTS
    max_size_mb: int = 0  # 0 = no limit
    workers: int = 3
    # Pieces of one file asked for at once. 1 = one after the other, as
    # Telegram's own apps do for accounts without Premium; more can be faster
    # for big files, or bring flood waits that pause every download.
    download_streams: int = 1
    scan_interval_hours: int = 6
    max_attempts: int = 5
    speed_limit_kbps: int = 0  # 0 = unlimited
    schedule_enabled: int = 0  # download only between schedule_start and schedule_end
    schedule_start: str = "01:00"
    schedule_end: str = "07:00"
    timezone: str = "UTC"  # IANA name, taken from the user's browser
    summary_enabled: int = 0  # daily summary in Telegram's Saved Messages
    summary_hour: int = 8

    def ext_set(self) -> set[str]:
        return parse_exts(self.allowed_exts)

    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    def in_download_window(self, now: datetime | None = None) -> bool:
        if not self.schedule_enabled:
            return True
        t = (now or datetime.now(self.tz())).astimezone(self.tz()).strftime("%H:%M")
        start, end = self.schedule_start, self.schedule_end
        if start == end:
            return True
        if start < end:
            return start <= t < end
        return t >= start or t < end  # the window crosses midnight


def parse_exts(value: str | None) -> set[str]:
    if not value:
        return set()
    return {"." + e.strip().lower().lstrip(".") for e in value.replace("،", ",").split(",") if e.strip()}


def scan_kinds(exts: set[str]) -> list[str]:
    """Which of Telegram's kinds of message a scan has to go through to find
    files of these types: plain files always, photos and videos, audio."""
    kinds = ["file"]
    if exts & (parse_exts(IMAGE_EXTS) | parse_exts(VIDEO_EXTS)):
        kinds.append("media")
    if exts & parse_exts(AUDIO_EXTS):
        kinds.append("audio")
    return kinds


def clean_exts(value: str) -> str:
    return ",".join(sorted(e.lstrip(".") for e in parse_exts(value)))


LIMITS = {
    "max_size_mb": (0, 4096 * 1024),
    "workers": (1, 10),
    "download_streams": (1, 8),
    "scan_interval_hours": (1, 24 * 7),
    "max_attempts": (1, 50),
    "speed_limit_kbps": (0, 1024 * 1024),
    "schedule_enabled": (0, 1),
    "summary_enabled": (0, 1),
    "summary_hour": (0, 23),
}
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SettingsStore:
    def __init__(self, db: Database):
        self._db = db
        self.current = AppSettings()

    async def load(self) -> AppSettings:
        async with self._db.read() as s:
            rows = {r.key: r.value for r in (await s.execute(select(Setting))).scalars()}
        values = {}
        for key, value in rows.items():
            try:
                values.update(_coerce({key: value}))
            except ValueError:
                log.warning("Ignoring bad setting %s=%r", key, value)
        self.current = AppSettings(**{**asdict(AppSettings()), **values})
        return self.current

    async def update(self, changes: dict) -> AppSettings:
        merged = {**asdict(self.current), **_coerce(changes)}
        merged["allowed_exts"] = clean_exts(merged["allowed_exts"])
        for key, (lo, hi) in LIMITS.items():
            merged[key] = max(lo, min(hi, int(merged[key])))
        defaults = AppSettings()
        for key in ("schedule_start", "schedule_end"):
            if not _TIME.match(merged[key]):
                merged[key] = getattr(self.current, key) if _TIME.match(getattr(self.current, key)) else getattr(defaults, key)
        try:
            ZoneInfo(merged["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            merged["timezone"] = self.current.timezone
        async with self._db.write() as s:
            for key, value in merged.items():
                await s.merge(Setting(key=key, value=str(value)))
        self.current = AppSettings(**merged)
        return self.current


def _coerce(values: dict) -> dict:
    out = {}
    types = {f.name: f.type for f in fields(AppSettings)}
    for k, v in values.items():
        if k not in types or v is None:
            continue
        out[k] = int(v) if types[k] is int else str(v)
    return out
