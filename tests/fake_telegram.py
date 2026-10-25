"""In-memory TelegramGateway for tests."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tglib.engine.errors import (
    AuthFlowError,
    ChatUnavailable,
    FileRefExpired,
    FloodWait,
    NotConnected,
    PasswordNeeded,
)
from tglib.engine.telegram import DialogInfo, DocMessage, DocumentInfo, MeInfo, TelegramGateway

VALID_CODE = "12345"


@dataclass
class FakeFile:
    tg_document_id: int
    name: str
    content: bytes
    mime: str = "application/pdf"


@dataclass
class FakeChat:
    id: int
    title: str
    type: str = "channel"
    username: str | None = None
    # msg_id -> (FakeFile, caption)
    messages: dict[int, tuple[FakeFile, str | None]] = field(default_factory=dict)


class FakeGateway(TelegramGateway):
    def __init__(self, *, authorized: bool = True, password: str | None = None):
        self.connected = False
        self.authorized = authorized
        self.password = password
        self.chats: dict[int, FakeChat] = {}
        self.base_date = datetime(2024, 1, 1)
        # Fault injection
        self.flood_on_next: dict[str, int] = {}  # op -> seconds, raised once
        self.expire_ref_after_bytes: int | None = None  # raise FileRefExpired once
        self.fail_download_after_bytes: int | None = None  # raise ConnectionError once
        self.download_delay = 0.0
        self.block_download: asyncio.Event | None = None  # downloads wait on this
        self.downloads_started: list[tuple[int, int]] = []  # (tg_document_id, offset)
        self.inaccessible_chats: set[int] = set()

    # --- helpers for tests ---
    def add_chat(self, chat_id: int, title: str, type_: str = "channel") -> FakeChat:
        self.chats[chat_id] = FakeChat(chat_id, title, type_)
        return self.chats[chat_id]

    def post(self, chat_id: int, msg_id: int, f: FakeFile, caption: str | None = None) -> None:
        self.chats[chat_id].messages[msg_id] = (f, caption)

    def _maybe_flood(self, op: str) -> None:
        if op in self.flood_on_next:
            raise FloodWait(self.flood_on_next.pop(op))

    def _to_msg(self, chat_id: int, msg_id: int) -> DocMessage:
        f, caption = self.chats[chat_id].messages[msg_id]
        ext = "." + f.name.rsplit(".", 1)[1].lower() if "." in f.name else None
        return DocMessage(
            chat_id=chat_id,
            msg_id=msg_id,
            date=self.base_date + timedelta(minutes=msg_id),
            caption=caption,
            document=DocumentInfo(f.tg_document_id, f.name, ext, f.mime, len(f.content)),
            handle=f,
        )

    # --- connection / auth ---
    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    async def is_authorized(self) -> bool:
        return self.authorized

    async def get_me(self) -> MeInfo | None:
        return MeInfo(1, "Test User", "tester", "15550000") if self.authorized else None

    async def send_code(self, phone: str) -> str:
        if not phone.startswith("+"):
            raise AuthFlowError("phone_invalid", "bad phone")
        return "hash-" + phone

    async def sign_in_code(self, phone: str, code: str, phone_code_hash: str) -> None:
        if phone_code_hash != "hash-" + phone:
            raise AuthFlowError("bad_hash", "bad hash")
        if code != VALID_CODE:
            raise AuthFlowError("code_invalid", "bad code")
        if self.password:
            raise PasswordNeeded()
        self.authorized = True

    async def sign_in_password(self, password: str) -> None:
        if password != self.password:
            raise AuthFlowError("password_invalid", "bad password")
        self.authorized = True

    async def log_out(self) -> None:
        self.authorized = False
        self.connected = False

    # --- data ---
    async def iter_dialogs(self) -> AsyncIterator[DialogInfo]:
        for c in self.chats.values():
            yield DialogInfo(c.id, c.title, c.username, c.type)

    async def iter_document_messages(
        self, chat_id: int, min_id: int, offset_id: int = 0
    ) -> AsyncIterator[DocMessage]:
        if chat_id in self.inaccessible_chats or chat_id not in self.chats:
            raise ChatUnavailable(str(chat_id))
        ids = sorted(self.chats[chat_id].messages, reverse=True)
        for msg_id in ids:
            if msg_id <= min_id or (offset_id and msg_id >= offset_id):
                continue
            self._maybe_flood("scan")
            yield self._to_msg(chat_id, msg_id)

    async def get_document_message(self, chat_id: int, msg_id: int) -> DocMessage | None:
        if chat_id in self.inaccessible_chats or chat_id not in self.chats:
            raise ChatUnavailable(str(chat_id))
        self._maybe_flood("get_message")
        if msg_id not in self.chats[chat_id].messages:
            return None
        return self._to_msg(chat_id, msg_id)

    async def iter_download(
        self, message: DocMessage, offset: int, request_size: int
    ) -> AsyncIterator[bytes]:
        f: FakeFile = message.handle
        self.downloads_started.append((f.tg_document_id, offset))
        self._maybe_flood("download")
        pos = offset
        while pos < len(f.content):
            if self.block_download is not None:
                await self.block_download.wait()
            if self.download_delay:
                await asyncio.sleep(self.download_delay)
            if self.expire_ref_after_bytes is not None and pos >= self.expire_ref_after_bytes:
                self.expire_ref_after_bytes = None
                raise FileRefExpired("expired")
            if self.fail_download_after_bytes is not None and pos >= self.fail_download_after_bytes:
                self.fail_download_after_bytes = None
                raise NotConnected("connection dropped")
            chunk = f.content[pos : pos + request_size]
            pos += len(chunk)
            yield chunk
