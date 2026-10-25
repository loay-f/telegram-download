"""Wires together the long-lived services that share the process's event loop."""

from collections.abc import Callable

from tglib.app.config import Settings
from tglib.app.security import AppAuth, load_secret
from tglib.db.session import Database
from tglib.engine.account import TelegramAccount
from tglib.engine.downloader import DownloadEngine
from tglib.engine.events import EventBus
from tglib.engine.scanner import Scanner
from tglib.engine.settings_store import SettingsStore
from tglib.engine.stats import StatsCache
from tglib.engine.telegram import TelegramGateway, TelethonGateway


class Container:
    def __init__(
        self,
        settings: Settings,
        gateway_factory: Callable[[], TelegramGateway] | None = None,
    ):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.events = EventBus()
        self.auth = AppAuth(load_secret(settings.data_dir), settings.app_password)
        if gateway_factory is None and settings.telegram_configured:
            gateway_factory = lambda: TelethonGateway(  # noqa: E731
                settings.session_path, settings.tg_api_id, settings.tg_api_hash
            )
        self.account = TelegramAccount(gateway_factory, self.events)
        self.app_settings = SettingsStore(self.db)
        self.engine = DownloadEngine(
            self.db, self.account, self.app_settings, self.events, settings.storage_dir
        )
        self.scanner = Scanner(
            self.db, self.account, self.app_settings, self.events, on_new_documents=self.engine.wake
        )
        self.stats = StatsCache(self.db)
        self.dialogs_synced_at = 0.0

    async def start(self) -> None:
        await self.app_settings.load()
        await self.account.start()
        await self.engine.start()
        self.scanner.start()

    async def stop(self) -> None:
        await self.engine.stop()
        await self.scanner.stop()
        await self.account.stop()
        await self.db.dispose()
