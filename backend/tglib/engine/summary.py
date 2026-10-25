"""Optional daily summary posted to the account's Saved Messages."""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from tglib.db.models import Document, DocStatus, Setting, utcnow
from tglib.db.session import Database
from tglib.engine.account import TelegramAccount
from tglib.engine.settings_store import SettingsStore

log = logging.getLogger(__name__)

LAST_DATE_KEY = "summary_last_date"  # local date of the last summary
SINCE_KEY = "summary_since"  # UTC time the next summary counts from
CHECK_SECONDS = 300
MAX_TITLES = 10

_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def _n(value: int) -> str:
    return str(value).translate(_DIGITS)


def _size(n: int) -> str:
    units = ["بايت", "ك.ب", "م.ب", "ج.ب", "ت.ب"]
    value, i = float(n), 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    text = f"{value:.0f}" if i == 0 else f"{value:.1f}"
    return f"{text.translate(_DIGITS)} {units[i]}"


async def compose(db: Database, since: datetime) -> str | None:
    """The summary text, or None when nothing happened since `since`."""
    async with db.read() as s:
        done = (
            await s.execute(
                select(Document.original_name, Document.size)
                .where(Document.status == DocStatus.DONE, Document.completed_at >= since)
                .order_by(Document.completed_at.desc())
            )
        ).all()
        found = (
            await s.execute(
                select(func.count()).where(
                    Document.first_seen_at >= since,
                    Document.status.in_((DocStatus.AVAILABLE, DocStatus.PENDING)),
                )
            )
        ).scalar()
        failed = (
            await s.execute(select(func.count()).where(Document.status == DocStatus.FAILED))
        ).scalar()
    if not done and not found and not failed:
        return None
    lines = ["📚 ملخص مكتبة تيليغرام", ""]
    if done:
        lines.append(f"نُزّل منذ الملخص السابق: {_n(len(done))} (الحجم {_size(sum(sz for _, sz in done))})")
        lines += [f"• {name}" for name, _ in done[:MAX_TITLES]]
        if len(done) > MAX_TITLES:
            lines.append(f"• و{_n(len(done) - MAX_TITLES)} غيرها")
        lines.append("")
    if found:
        lines.append(f"ملفات جديدة متاحة للتنزيل في المكتبة: {_n(found)}")
    if failed:
        lines.append(f"ملفات لم ينجح تنزيلها: {_n(failed)} — راجع «قائمة الانتظار والأخطاء».")
    return "\n".join(lines).strip()


class DailySummary:
    def __init__(self, db: Database, account: TelegramAccount, settings: SettingsStore):
        self._db = db
        self._account = account
        self._settings = settings
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="daily-summary")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.maybe_send()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Daily summary not sent: %s", e)
            await asyncio.sleep(CHECK_SECONDS)

    async def maybe_send(self, now: datetime | None = None) -> bool:
        st = self._settings.current
        if not st.summary_enabled or not self._account.ready:
            return False
        local = (now or datetime.now(st.tz())).astimezone(st.tz())
        if local.hour < st.summary_hour:
            return False
        async with self._db.read() as s:
            last = await s.get(Setting, LAST_DATE_KEY)
            since_row = await s.get(Setting, SINCE_KEY)
        today = local.date().isoformat()
        if last is not None and last.value == today:
            return False
        since = datetime.fromisoformat(since_row.value) if since_row else utcnow() - timedelta(days=1)
        sent_at = utcnow()
        text = await compose(self._db, since)
        if text:
            await self._account.gateway.send_to_saved(text)
            log.info("Daily summary sent")
        async with self._db.write() as s:
            await s.merge(Setting(key=LAST_DATE_KEY, value=today))
            await s.merge(Setting(key=SINCE_KEY, value=sent_at.isoformat()))
        return bool(text)
