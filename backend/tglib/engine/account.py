"""The single Telegram account: connection supervision and the login flow."""

import asyncio
import logging
from collections.abc import Callable

from tglib.engine.errors import AuthFlowError, PasswordNeeded
from tglib.engine.events import EventBus
from tglib.engine.telegram import MeInfo, TelegramGateway

log = logging.getLogger(__name__)


class AccountState:
    UNCONFIGURED = "unconfigured"  # TG_API_ID / TG_API_HASH missing
    CONNECTING = "connecting"
    OFFLINE = "offline"  # can't reach Telegram; retrying
    LOGGED_OUT = "logged_out"  # connected, waiting for phone number
    CODE_SENT = "code_sent"
    PASSWORD_NEEDED = "password_needed"
    READY = "ready"


class TelegramAccount:
    def __init__(self, gateway_factory: Callable[[], TelegramGateway] | None, events: EventBus):
        self._factory = gateway_factory
        self._events = events
        self.gateway: TelegramGateway | None = None
        self.state = AccountState.UNCONFIGURED if gateway_factory is None else AccountState.CONNECTING
        self.me: MeInfo | None = None
        self.phone: str | None = None
        self._phone_code_hash: str | None = None
        self._ready = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    # --- lifecycle ---
    async def start(self) -> None:
        if self._factory is None:
            return
        self.gateway = self._factory()
        self._task = asyncio.create_task(self._supervise(), name="tg-supervisor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.gateway:
            await self.gateway.disconnect()

    async def _supervise(self) -> None:
        """Keeps a connection open; detects logins and silent disconnects."""
        delay = 5
        while True:
            try:
                if not self.gateway.is_connected():
                    if self.state not in (AccountState.CODE_SENT, AccountState.PASSWORD_NEEDED):
                        self._set_state(
                            AccountState.CONNECTING
                            if self.state == AccountState.CONNECTING
                            else AccountState.OFFLINE
                        )
                    async with self._lock:
                        await self.gateway.connect()
                        await self._refresh_authorization()
                    delay = 5
                elif self.state == AccountState.OFFLINE:
                    async with self._lock:
                        await self._refresh_authorization()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Telegram connection failed: %s", e)
                self._set_state(AccountState.OFFLINE)
                delay = min(delay * 2, 120)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay if self.state == AccountState.OFFLINE else 30)
            except asyncio.TimeoutError:
                pass

    async def _refresh_authorization(self) -> None:
        if await self.gateway.is_authorized():
            self.me = await self.gateway.get_me()
            self._set_state(AccountState.READY)
        elif self.state not in (AccountState.CODE_SENT, AccountState.PASSWORD_NEEDED):
            self._set_state(AccountState.LOGGED_OUT)

    def _set_state(self, state: str) -> None:
        if state == self.state:
            return
        log.info("Telegram account: %s -> %s", self.state, state)
        self.state = state
        if state == AccountState.READY:
            self._ready.set()
        else:
            self._ready.clear()
        self._events.publish("account", self.status())

    # --- queries ---
    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    async def wait_ready(self) -> None:
        await self._ready.wait()

    def status(self) -> dict:
        return {
            "state": self.state,
            "phone": self.phone,
            "me": (
                {"name": self.me.name, "username": self.me.username, "phone": self.me.phone}
                if self.me and self.state == AccountState.READY
                else None
            ),
        }

    def mark_unauthorized(self) -> None:
        """Called when any request reports the session was revoked."""
        self.me = None
        self._set_state(AccountState.LOGGED_OUT)

    def mark_disconnected(self) -> None:
        self._wake.set()

    # --- login flow ---
    def _require_connected(self) -> None:
        if self.gateway is None:
            raise AuthFlowError("unconfigured", "لم يتم ضبط TG_API_ID و TG_API_HASH في ملف ‎.env")
        if not self.gateway.is_connected():
            raise AuthFlowError("offline", "لا يوجد اتصال بتيليغرام الآن. تحقق من الإنترنت ثم حاول مرة أخرى.")

    async def send_code(self, phone: str) -> None:
        phone = "".join(ch for ch in phone.strip() if ch.isdigit() or ch == "+")
        async with self._lock:
            self._require_connected()
            if self.state == AccountState.READY:
                raise AuthFlowError("already", "الحساب مسجّل الدخول بالفعل.")
            self._phone_code_hash = await self.gateway.send_code(phone)
            self.phone = phone
            self._set_state(AccountState.CODE_SENT)

    async def submit_code(self, code: str) -> None:
        code = "".join(ch for ch in code if ch.isdigit())
        async with self._lock:
            self._require_connected()
            if self.state != AccountState.CODE_SENT or not self._phone_code_hash:
                raise AuthFlowError("bad_step", "اطلب رمز الدخول أولاً.")
            try:
                await self.gateway.sign_in_code(self.phone, code, self._phone_code_hash)
            except PasswordNeeded:
                self._set_state(AccountState.PASSWORD_NEEDED)
                return
            await self._finish_login()

    async def submit_password(self, password: str) -> None:
        async with self._lock:
            self._require_connected()
            if self.state != AccountState.PASSWORD_NEEDED:
                raise AuthFlowError("bad_step", "اطلب رمز الدخول أولاً.")
            await self.gateway.sign_in_password(password)
            await self._finish_login()

    async def _finish_login(self) -> None:
        self._phone_code_hash = None
        self.me = await self.gateway.get_me()
        self._set_state(AccountState.READY)

    async def cancel_login(self) -> None:
        async with self._lock:
            if self.state in (AccountState.CODE_SENT, AccountState.PASSWORD_NEEDED):
                self._phone_code_hash = None
                self._set_state(AccountState.LOGGED_OUT)

    async def log_out(self) -> None:
        async with self._lock:
            if self.gateway is None:
                return
            self._ready.clear()
            await self.gateway.log_out()
            self.me = None
            self.phone = None
            self._phone_code_hash = None
            self._set_state(AccountState.CONNECTING)
        self._wake.set()
