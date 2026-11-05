"""The single Telegram account: connection supervision and the login flow."""

import asyncio
import logging
import time
from collections.abc import Callable

from tglib.db.models import utcnow
from tglib.engine.errors import AuthFlowError, PasswordNeeded
from tglib.engine.events import EventBus
from tglib.engine.telegram import MeInfo, SentCodeInfo, TelegramGateway

log = logging.getLogger(__name__)


class AccountState:
    UNCONFIGURED = "unconfigured"  # TG_API_ID / TG_API_HASH missing
    CONNECTING = "connecting"
    OFFLINE = "offline"  # can't reach Telegram; retrying
    LOGGED_OUT = "logged_out"  # connected, waiting for phone number
    CODE_SENT = "code_sent"
    QR_WAITING = "qr_waiting"  # showing a QR code to scan from a logged-in phone
    PASSWORD_NEEDED = "password_needed"
    READY = "ready"


_LOGIN_IN_PROGRESS = (AccountState.CODE_SENT, AccountState.QR_WAITING, AccountState.PASSWORD_NEEDED)


def _qr_svg(url: str) -> str:
    import segno

    return segno.make(url, error="m").svg_inline(scale=6, border=2, dark="#17302c", light="#ffffff")


class TelegramAccount:
    def __init__(self, gateway_factory: Callable[[], TelegramGateway] | None, events: EventBus):
        self._factory = gateway_factory
        self._events = events
        self.gateway: TelegramGateway | None = None
        self.state = AccountState.UNCONFIGURED if gateway_factory is None else AccountState.CONNECTING
        self.me: MeInfo | None = None
        self.phone: str | None = None
        self._phone_code_hash: str | None = None
        self._code: SentCodeInfo | None = None
        self._code_sent_at = 0.0
        self._qr_url: str | None = None
        self._qr_task: asyncio.Task | None = None
        self.login_error: str | None = None  # shown when a background QR login fails
        self._ready = asyncio.Event()
        self.ready_at = 0.0  # when the account last became ready (a new login)
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
        self._stop_qr()
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
                    if self.state not in _LOGIN_IN_PROGRESS:
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
        elif self.state not in _LOGIN_IN_PROGRESS:
            self._set_state(AccountState.LOGGED_OUT)

    def _set_state(self, state: str) -> None:
        if state == self.state:
            return
        log.info("Telegram account: %s -> %s", self.state, state)
        self.state = state
        if state == AccountState.READY:
            self.ready_at = time.time()
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
        code = None
        if self._code is not None and self.state == AccountState.CODE_SENT:
            wait = (self._code.timeout or 0) - (time.time() - self._code_sent_at)
            code = {
                "via": self._code.via,
                "next_via": self._code.next_via,
                "resend_in": max(0, round(wait)),
                "email_pattern": self._code.email_pattern,
                "fragment_url": self._code.fragment_url,
            }
        qr = None
        if self._qr_url and self.state == AccountState.QR_WAITING:
            qr = {"url": self._qr_url, "svg": _qr_svg(self._qr_url)}
        return {
            "state": self.state,
            "phone": self.phone,
            "code": code,
            "qr": qr,
            "login_error": self.login_error,
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
            self._stop_qr()
            self._remember_code(await self.gateway.send_code(phone))
            self.phone = phone
            self._set_state(AccountState.CODE_SENT)

    async def resend_code(self) -> None:
        """Asks Telegram to deliver the code by its next method (e.g. SMS)."""
        async with self._lock:
            self._require_connected()
            if self.state != AccountState.CODE_SENT or not self.phone:
                raise AuthFlowError("bad_step", "اطلب رمز الدخول أولاً.")
            self._remember_code(await self.gateway.send_code(self.phone))

    def _remember_code(self, info: SentCodeInfo) -> None:
        self._phone_code_hash = info.phone_code_hash
        self._code = info
        self._code_sent_at = time.time()

    async def start_qr(self) -> None:
        """Login by scanning a QR code with Telegram on a phone that is
        already logged in (Settings → Devices → Link Desktop Device)."""
        async with self._lock:
            self._require_connected()
            if self.state == AccountState.READY:
                raise AuthFlowError("already", "الحساب مسجّل الدخول بالفعل.")
            self._stop_qr()
            self._qr_url, expires = await self.gateway.qr_login_start()
            self.login_error = None
            self._set_state(AccountState.QR_WAITING)
            self._qr_task = asyncio.create_task(self._qr_loop(expires), name="qr-login")

    async def _qr_loop(self, expires) -> None:
        try:
            while self.state == AccountState.QR_WAITING:
                left = (expires - utcnow()).total_seconds() - 1
                try:
                    done = await self.gateway.qr_login_wait(max(1.0, left))
                except PasswordNeeded:
                    self._set_state(AccountState.PASSWORD_NEEDED)
                    return
                if done:
                    async with self._lock:
                        await self._finish_login()
                    return
                # Token expired unscanned: show a fresh code.
                self._qr_url, expires = await self.gateway.qr_login_start()
                self._events.publish("account", self.status())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("QR login failed: %s", e)
            self.login_error = "تعذّر الدخول برمز QR. حاول مرة أخرى أو استخدم رقم الهاتف."
            self._qr_url = None
            if self.state == AccountState.QR_WAITING:
                self._set_state(AccountState.LOGGED_OUT)

    def _stop_qr(self) -> None:
        if self._qr_task and not self._qr_task.done() and self._qr_task is not asyncio.current_task():
            self._qr_task.cancel()
        self._qr_task = None
        self._qr_url = None

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
        self._code = None
        self._qr_url = None
        self.login_error = None
        self.me = await self.gateway.get_me()
        self._set_state(AccountState.READY)

    async def cancel_login(self) -> None:
        async with self._lock:
            self._stop_qr()
            if self.state in _LOGIN_IN_PROGRESS:
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
