"""Telegram access behind a small interface.

`TelegramGateway` is what the rest of the app uses. `TelethonGateway` is the
real implementation; tests provide a fake. Only this module imports Telethon.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tglib.engine.errors import (
    AuthFlowError,
    ChatUnavailable,
    FileRefExpired,
    FloodWait,
    NotAuthorized,
    NotConnected,
    PasswordNeeded,
)

log = logging.getLogger(__name__)


@dataclass
class DialogInfo:
    id: int  # Telegram "marked" peer id (channels are -100...)
    title: str
    username: str | None
    type: str  # channel / group / user


@dataclass
class DocumentInfo:
    tg_document_id: int
    file_name: str | None
    ext: str | None  # with leading dot, lowercase; may be guessed from mime
    mime: str | None
    size: int


@dataclass
class DocMessage:
    chat_id: int
    msg_id: int
    date: datetime | None  # naive UTC
    caption: str | None
    document: DocumentInfo
    # Opaque object passed back to `iter_download` (Telethon Document).
    handle: Any = field(default=None, repr=False)


@dataclass
class SentCodeInfo:
    phone_code_hash: str
    # Where Telegram sent the code: app / sms / call / email / fragment /
    # setup_email (account must add a login email first) / other.
    via: str
    next_via: str | None = None  # what "send another way" would use
    timeout: int | None = None  # seconds before resending is allowed
    email_pattern: str | None = None  # e.g. "j***@gmail.com"
    fragment_url: str | None = None


@dataclass
class MeInfo:
    id: int
    name: str
    username: str | None
    phone: str | None


class TelegramGateway(abc.ABC):
    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def disconnect(self) -> None: ...

    @abc.abstractmethod
    def is_connected(self) -> bool: ...

    @abc.abstractmethod
    async def is_authorized(self) -> bool: ...

    @abc.abstractmethod
    async def get_me(self) -> MeInfo | None: ...

    # --- login flow ---
    @abc.abstractmethod
    async def send_code(self, phone: str) -> SentCodeInfo:
        """Requests a login code. Calling it again for the same phone asks
        Telegram to resend the code by its next delivery method."""

    @abc.abstractmethod
    async def sign_in_code(self, phone: str, code: str, phone_code_hash: str) -> None:
        """Raises PasswordNeeded when 2FA is enabled."""

    @abc.abstractmethod
    async def sign_in_password(self, password: str) -> None: ...

    @abc.abstractmethod
    async def qr_login_start(self) -> tuple[str, datetime]:
        """Starts (or renews) QR login. Returns the tg://login URL to show as a
        QR code and when it expires (naive UTC)."""

    @abc.abstractmethod
    async def qr_login_wait(self, timeout: float) -> bool:
        """Waits for the QR code to be scanned. True when logged in, False on
        timeout; raises PasswordNeeded when 2FA is enabled."""

    @abc.abstractmethod
    async def log_out(self) -> None:
        """Logs out and removes the session file."""

    # --- data ---
    @abc.abstractmethod
    def iter_dialogs(self) -> AsyncIterator[DialogInfo]: ...

    @abc.abstractmethod
    def iter_document_messages(
        self, chat_id: int, min_id: int, offset_id: int = 0
    ) -> AsyncIterator[DocMessage]:
        """Messages with documents, newest first, with msg_id > min_id and
        (if offset_id) msg_id < offset_id."""

    @abc.abstractmethod
    async def get_document_message(self, chat_id: int, msg_id: int) -> DocMessage | None:
        """Fresh copy of one message (new file reference); None if deleted or
        not a document anymore."""

    @abc.abstractmethod
    def iter_download(
        self, message: DocMessage, offset: int, request_size: int
    ) -> AsyncIterator[bytes]: ...


def _naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class TelethonGateway(TelegramGateway):
    def __init__(self, session_path: Path, api_id: int, api_hash: str):
        self._session_path = session_path
        self._api_id = api_id
        self._api_hash = api_hash
        self._client = self._new_client()
        self._qr = None

    def _new_client(self):
        from telethon import TelegramClient

        return TelegramClient(
            str(self._session_path),
            self._api_id,
            self._api_hash,
            # Short waits are slept automatically; longer ones surface as
            # FloodWait so the UI can show them.
            flood_sleep_threshold=10,
            connection_retries=5,
            retry_delay=3,
            auto_reconnect=True,
            request_retries=3,
            device_model="TG Library",
            system_version="Docker",
            app_version="1.0",
        )

    # --- error translation ---
    @asynccontextmanager
    async def _translate(self):
        from telethon import errors

        try:
            yield
        except errors.FloodError as e:
            raise FloodWait(getattr(e, "seconds", 30) or 30) from e
        except (errors.FileReferenceExpiredError, errors.FilerefUpgradeNeededError) as e:
            raise FileRefExpired(str(e)) from e
        except (
            errors.AuthKeyUnregisteredError,
            errors.SessionRevokedError,
            errors.SessionExpiredError,
            errors.UserDeactivatedError,
            errors.UserDeactivatedBanError,
        ) as e:
            raise NotAuthorized(str(e)) from e
        except (
            errors.ChannelPrivateError,
            errors.ChatForbiddenError,
            errors.ChannelInvalidError,
            errors.ChatIdInvalidError,
            errors.PeerIdInvalidError,
        ) as e:
            raise ChatUnavailable(str(e)) from e
        except ConnectionError as e:
            raise NotConnected(str(e)) from e

    async def _entity(self, chat_id: int):
        """Input peer for a chat id, refreshing the entity cache if needed."""
        try:
            return await self._client.get_input_entity(chat_id)
        except ValueError:
            pass
        # The session's entity cache doesn't know it; dialogs repopulate it.
        async with self._translate():
            async for _ in self._client.iter_dialogs():
                pass
        try:
            return await self._client.get_input_entity(chat_id)
        except ValueError as e:
            raise ChatUnavailable(f"Unknown chat {chat_id}") from e

    # --- connection ---
    async def connect(self) -> None:
        async with self._translate():
            await self._client.connect()
        self._protect_session()

    def _protect_session(self) -> None:
        """The session file is a full login to the account: owner-only access."""
        p = self._session_path.with_name(self._session_path.name + ".session")
        try:
            if p.exists():
                os.chmod(p, 0o600)
        except OSError:
            pass

    async def disconnect(self) -> None:
        await self._client.disconnect()

    def is_connected(self) -> bool:
        return self._client.is_connected()

    async def is_authorized(self) -> bool:
        if not self._client.is_connected():
            return False
        async with self._translate():
            return await self._client.is_user_authorized()

    async def get_me(self) -> MeInfo | None:
        async with self._translate():
            me = await self._client.get_me()
        if me is None:
            return None
        name = " ".join(p for p in (me.first_name, me.last_name) if p) or (me.username or "")
        return MeInfo(id=me.id, name=name, username=me.username, phone=me.phone)

    # --- login flow ---
    async def send_code(self, phone: str) -> SentCodeInfo:
        from telethon import errors

        try:
            sent = await self._client.send_code_request(phone)
        except errors.PhoneNumberInvalidError:
            raise AuthFlowError("phone_invalid", "رقم الهاتف غير صحيح. اكتبه مع رمز الدولة، مثل ‎+9665xxxxxxxx")
        except errors.PhoneNumberBannedError:
            raise AuthFlowError("phone_banned", "هذا الرقم محظور من تيليغرام.")
        except errors.ApiIdInvalidError:
            raise AuthFlowError("api_invalid", "قيم TG_API_ID / TG_API_HASH غير صحيحة. راجع ملف ‎.env")
        except (errors.FloodError, errors.PhoneNumberFloodError) as e:
            raise AuthFlowError("flood", _flood_message(getattr(e, "seconds", 0)))
        except errors.SendCodeUnavailableError:
            raise AuthFlowError(
                "no_more_methods",
                "استنفد تيليغرام طرق إرسال الرمز لهذا الرقم. انتظر بعض الوقت ثم ابدأ من جديد.",
            )
        log.info("Login code sent via %s (next: %s)", type(sent.type).__name__, type(sent.next_type).__name__)
        return SentCodeInfo(
            phone_code_hash=sent.phone_code_hash,
            via=_code_via(sent.type),
            next_via=_code_via(sent.next_type) if sent.next_type else None,
            timeout=sent.timeout,
            email_pattern=getattr(sent.type, "email_pattern", None),
            fragment_url=getattr(sent.type, "url", None),
        )

    async def sign_in_code(self, phone: str, code: str, phone_code_hash: str) -> None:
        from telethon import errors

        try:
            await self._client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except errors.SessionPasswordNeededError:
            raise PasswordNeeded()
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
            raise AuthFlowError("code_invalid", "الرمز غير صحيح. تأكد منه وحاول مرة أخرى.")
        except errors.PhoneCodeExpiredError:
            raise AuthFlowError("code_expired", "انتهت صلاحية الرمز. اطلب رمزاً جديداً.")
        except errors.PhoneNumberUnoccupiedError:
            raise AuthFlowError("phone_unregistered", "لا يوجد حساب تيليغرام بهذا الرقم.")
        except errors.FloodError as e:
            raise AuthFlowError("flood", _flood_message(getattr(e, "seconds", 0)))

    async def sign_in_password(self, password: str) -> None:
        from telethon import errors

        try:
            await self._client.sign_in(password=password)
        except errors.PasswordHashInvalidError:
            raise AuthFlowError("password_invalid", "كلمة مرور التحقق بخطوتين غير صحيحة.")
        except errors.FloodError as e:
            raise AuthFlowError("flood", _flood_message(getattr(e, "seconds", 0)))

    async def qr_login_start(self) -> tuple[str, datetime]:
        async with self._translate():
            if self._qr is None:
                self._qr = await self._client.qr_login()
            else:
                await self._qr.recreate()
        return self._qr.url, _naive_utc(self._qr.expires)

    async def qr_login_wait(self, timeout: float) -> bool:
        from telethon import errors

        try:
            async with self._translate():
                await self._qr.wait(timeout=timeout)
        except asyncio.TimeoutError:
            return False
        except errors.SessionPasswordNeededError:
            self._qr = None
            raise PasswordNeeded()
        self._qr = None
        return True

    async def log_out(self) -> None:
        try:
            if self._client.is_connected():
                # Also deletes the session file.
                await self._client.log_out()
        except Exception:
            log.exception("log_out failed; removing session file anyway")
        finally:
            await self._client.disconnect()
            for suffix in (".session", ".session-journal"):
                p = self._session_path.with_name(self._session_path.name + suffix)
                p.unlink(missing_ok=True)
            self._client = self._new_client()
            self._qr = None

    # --- data ---
    async def iter_dialogs(self) -> AsyncIterator[DialogInfo]:
        from telethon.tl import types

        async with self._translate():
            async for d in self._client.iter_dialogs():
                ent = d.entity
                if isinstance(ent, types.Channel):
                    kind = "group" if ent.megagroup or ent.gigagroup else "channel"
                elif isinstance(ent, types.Chat):
                    kind = "group"
                elif isinstance(ent, types.User):
                    kind = "user"
                else:
                    continue
                yield DialogInfo(
                    id=d.id,
                    title=d.name or "",
                    username=getattr(ent, "username", None),
                    type=kind,
                )

    def _to_doc_message(self, chat_id: int, msg) -> DocMessage | None:
        doc = getattr(msg, "document", None)
        if doc is None or msg.file is None:
            return None
        name = msg.file.name
        ext = None
        if name and "." in name:
            ext = "." + name.rsplit(".", 1)[1].lower()
        elif msg.file.ext:
            ext = msg.file.ext.lower()
        return DocMessage(
            chat_id=chat_id,
            msg_id=msg.id,
            date=_naive_utc(msg.date),
            caption=msg.message or None,
            document=DocumentInfo(
                tg_document_id=doc.id,
                file_name=name,
                ext=ext,
                mime=doc.mime_type,
                size=doc.size or 0,
            ),
            handle=doc,
        )

    async def iter_document_messages(
        self, chat_id: int, min_id: int, offset_id: int = 0
    ) -> AsyncIterator[DocMessage]:
        from telethon.tl.types import InputMessagesFilterDocument

        entity = await self._entity(chat_id)
        async with self._translate():
            async for msg in self._client.iter_messages(
                entity, filter=InputMessagesFilterDocument, min_id=min_id, offset_id=offset_id
            ):
                dm = self._to_doc_message(chat_id, msg)
                if dm is not None:
                    yield dm

    async def get_document_message(self, chat_id: int, msg_id: int) -> DocMessage | None:
        entity = await self._entity(chat_id)
        async with self._translate():
            msg = await self._client.get_messages(entity, ids=msg_id)
        if msg is None:
            return None
        return self._to_doc_message(chat_id, msg)

    async def iter_download(
        self, message: DocMessage, offset: int, request_size: int
    ) -> AsyncIterator[bytes]:
        it = self._client.iter_download(
            message.handle,
            offset=offset,
            request_size=request_size,
            file_size=message.document.size,
        )
        try:
            async with self._translate():
                async for chunk in it:
                    yield bytes(chunk)
        finally:
            # Releases the borrowed connection if the file lives on another DC.
            await it.close()


def _code_via(code_type) -> str:
    """Telethon SentCodeType*/CodeType* -> short delivery name."""
    name = type(code_type).__name__
    for key, via in (
        ("SetUpEmail", "setup_email"),
        ("Email", "email"),
        ("Fragment", "fragment"),
        ("App", "app"),
        ("Sms", "sms"),
        ("Call", "call"),
    ):
        if key in name:
            return via
    return "other"


def _flood_message(seconds: int) -> str:
    if not seconds:
        return "طلبات كثيرة. انتظر قليلاً ثم حاول مرة أخرى."
    minutes = max(1, round(seconds / 60))
    return f"طلب تيليغرام الانتظار قبل المحاولة مرة أخرى (حوالي {minutes} دقيقة)."

