"""User-tunable settings stored in the DB `settings` table, cached in memory."""

import logging
from dataclasses import asdict, dataclass, fields

from sqlalchemy import select

from tglib.db.models import Setting
from tglib.db.session import Database

log = logging.getLogger(__name__)

DEFAULT_EXTS = "pdf,epub,djvu,mobi,azw3,doc,docx,txt,md,rtf,odt,zip,rar,7z"


@dataclass
class AppSettings:
    allowed_exts: str = DEFAULT_EXTS
    max_size_mb: int = 0  # 0 = no limit
    workers: int = 3
    scan_interval_hours: int = 6
    max_attempts: int = 5

    def ext_set(self) -> set[str]:
        return parse_exts(self.allowed_exts)


def parse_exts(value: str | None) -> set[str]:
    if not value:
        return set()
    return {"." + e.strip().lower().lstrip(".") for e in value.replace("،", ",").split(",") if e.strip()}


def clean_exts(value: str) -> str:
    return ",".join(sorted(e.lstrip(".") for e in parse_exts(value)))


LIMITS = {
    "max_size_mb": (0, 4096 * 1024),
    "workers": (1, 10),
    "scan_interval_hours": (1, 24 * 7),
    "max_attempts": (1, 50),
}


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
