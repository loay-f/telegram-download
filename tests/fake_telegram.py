"""In-memory TelegramGateway for tests."""

import asyncio
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from tglib.engine.errors import (
    AuthFlowError,
    ChatUnavailable,
    FileRefExpired,
    FloodWait,
    NotConnected,
    PasswordNeeded,
)
from tglib.engine.telegram import (
    ChatMessage,
    DialogInfo,
    DocMessage,
    DocumentInfo,
    MediaInfo,
    MeInfo,
    SentCodeInfo,
    TelegramGateway,
)

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
    # msg_id -> message without a library file (text, photos, service notices)
    talk: dict[int, ChatMessage] = field(default_factory=dict)


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
        self.codes_sent = 0
        self.qr_tokens = 0
        self.qr_lifetime = 30.0
        self.qr_scanned = asyncio.Event()  # tests set it to "scan" the QR
        self.saved_messages: list[str] = []
        self.scan_calls: list[tuple[int, int]] = []  # (chat_id, min_id)
        self.scan_offsets: list[int] = []  # offset_id of each scan call
        self.fail_scan_after: int | None = None  # raise NotConnected once, after this many files
        self.get_message_calls = 0
        self.always_fail_ids: set[int] = set()  # tg_document_ids whose download always errors
        # Dialog listing stops after this many chats until `dialogs_resume` is set
        # (a threading.Event: tests run the app on another thread's loop).
        self.dialogs_pause_after: int | None = None
        self.dialogs_resume = threading.Event()
        self.dialog_calls = 0
        # Reading chats
        self.history_calls: list[tuple[int, int, int, int]] = []  # (chat_id, offset_id, min_id, limit)
        self.message_calls: list[tuple[int, int]] = []  # get_message (chat_id, msg_id)
        self.preview_calls: list[tuple[int, int, bool]] = []  # (chat_id, msg_id, full)
        self.pictures: dict[tuple[int, int], bytes] = {}  # (chat_id, msg_id) -> image
        # Messages handed out before this was last bumped have expired file references.
        self.ref_generation = 0
        self.history_delay = 0.0

    # --- helpers for tests ---
    def add_chat(self, chat_id: int, title: str, type_: str = "channel", username: str | None = None) -> FakeChat:
        self.chats[chat_id] = FakeChat(chat_id, title, type_, username)
        return self.chats[chat_id]

    def post(self, chat_id: int, msg_id: int, f: FakeFile, caption: str | None = None) -> None:
        self.chats[chat_id].messages[msg_id] = (f, caption)

    def say(self, chat_id: int, msg_id: int, text: str = "", **fields) -> ChatMessage:
        """Adds a message to read: text by default; pass `media`, `service`,
        `sender`, `out`... for anything else."""
        defaults = dict(
            entities=[], out=False, sender=None, reply_to=None, forwarded_from=None,
            edited=False, service=None, media=None,
        )
        msg = ChatMessage(
            chat_id=chat_id, id=msg_id, date=self.base_date + timedelta(minutes=msg_id), text=text,
            **{**defaults, **fields},
        )
        self.chats[chat_id].talk[msg_id] = msg
        return msg

    def show(self, chat_id: int, msg_id: int, picture: bytes, kind: str = "photo") -> ChatMessage:
        """Adds a photo (or a video with this thumbnail)."""
        self.pictures[(chat_id, msg_id)] = picture
        return self.say(chat_id, msg_id, media=MediaInfo(kind, width=320, height=200, has_preview=True))

    def _chat_message(self, chat_id: int, msg_id: int) -> ChatMessage | None:
        chat = self.chats[chat_id]
        if msg_id in chat.talk:
            return replace(chat.talk[msg_id], handle=self.ref_generation)
        if msg_id not in chat.messages:
            return None
        dm = self._to_msg(chat_id, msg_id)
        d = dm.document
        return ChatMessage(
            chat_id=chat_id, id=msg_id, date=dm.date, text=dm.caption or "", entities=[], out=False,
            sender=None, reply_to=None, forwarded_from=None, edited=False, service=None,
            media=MediaInfo("file", name=d.file_name, size=d.size, document=d),
        )

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

    async def send_code(self, phone: str) -> SentCodeInfo:
        if not phone.startswith("+"):
            raise AuthFlowError("phone_invalid", "bad phone")
        self.codes_sent += 1
        # First delivery in the app, then SMS, like Telegram usually does.
        if self.codes_sent == 1:
            return SentCodeInfo("hash-" + phone, via="app", next_via="sms", timeout=0)
        return SentCodeInfo("hash-" + phone, via="sms")

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

    async def qr_login_start(self):
        self.qr_tokens += 1
        return f"tg://login?token=fake{self.qr_tokens}", datetime.utcnow() + timedelta(seconds=self.qr_lifetime)

    async def qr_login_wait(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self.qr_scanned.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        if self.password:
            raise PasswordNeeded()
        self.authorized = True
        return True

    async def log_out(self) -> None:
        self.authorized = False
        self.connected = False

    # --- data ---
    async def iter_dialogs(self) -> AsyncIterator[DialogInfo]:
        self.dialog_calls += 1
        self._maybe_flood("dialogs")
        for i, c in enumerate(list(self.chats.values())):
            if i == self.dialogs_pause_after:
                while not self.dialogs_resume.is_set():  # polled, so cancelling works
                    await asyncio.sleep(0.01)
            yield DialogInfo(c.id, c.title, c.username, c.type)

    async def iter_document_messages(
        self, chat_id: int, min_id: int, offset_id: int = 0
    ) -> AsyncIterator[DocMessage]:
        self.scan_calls.append((chat_id, min_id))
        self.scan_offsets.append(offset_id)
        if chat_id in self.inaccessible_chats or chat_id not in self.chats:
            raise ChatUnavailable(str(chat_id))
        ids = sorted(self.chats[chat_id].messages, reverse=True)
        sent = 0
        for msg_id in ids:
            if msg_id <= min_id or (offset_id and msg_id >= offset_id):
                continue
            self._maybe_flood("scan")
            if self.fail_scan_after is not None and sent >= self.fail_scan_after:
                self.fail_scan_after = None
                raise NotConnected("connection dropped")
            sent += 1
            yield self._to_msg(chat_id, msg_id)

    async def get_document_message(self, chat_id: int, msg_id: int) -> DocMessage | None:
        if chat_id in self.inaccessible_chats or chat_id not in self.chats:
            raise ChatUnavailable(str(chat_id))
        self.get_message_calls += 1
        self._maybe_flood("get_message")
        if msg_id not in self.chats[chat_id].messages:
            return None
        return self._to_msg(chat_id, msg_id)

    def _dialog(self, chat_id: int) -> DialogInfo:
        c = self.chats[chat_id]
        return DialogInfo(c.id, c.title, c.username, c.type)

    async def search_documents(self, query: str, limit: int):
        self._maybe_flood("search")
        hits = []
        for chat in self.chats.values():
            for msg_id, (f, caption) in sorted(chat.messages.items(), reverse=True):
                if query.lower() in f.name.lower() or query.lower() in (caption or "").lower():
                    hits.append((self._to_msg(chat.id, msg_id), self._dialog(chat.id)))
        return hits[:limit]

    async def resolve_link(self, peer, msg_id: int):
        if isinstance(peer, str):
            chat = next((c for c in self.chats.values() if c.username == peer), None)
        else:
            chat = self.chats.get(peer)
        if chat is None or chat.id in self.inaccessible_chats:
            raise ChatUnavailable(str(peer))
        if msg_id not in chat.messages:
            return None
        return self._to_msg(chat.id, msg_id), self._dialog(chat.id)

    async def send_to_saved(self, text: str) -> None:
        self.saved_messages.append(text)

    # --- reading a chat ---
    def _require_chat(self, chat_id: int) -> FakeChat:
        if chat_id in self.inaccessible_chats or chat_id not in self.chats:
            raise ChatUnavailable(str(chat_id))
        return self.chats[chat_id]

    async def get_history(self, chat_id: int, *, offset_id: int = 0, min_id: int = 0, limit: int = 50):
        self.history_calls.append((chat_id, offset_id, min_id, limit))
        if self.history_delay:
            await asyncio.sleep(self.history_delay)
        self._maybe_flood("history")
        chat = self._require_chat(chat_id)
        ids = sorted({*chat.talk, *chat.messages}, reverse=True)
        ids = [i for i in ids if i > min_id and (not offset_id or i < offset_id)]
        return [self._chat_message(chat_id, i) for i in ids[:limit]]

    async def get_message(self, chat_id: int, msg_id: int) -> ChatMessage | None:
        self.message_calls.append((chat_id, msg_id))
        self._require_chat(chat_id)
        return self._chat_message(chat_id, msg_id)

    async def download_preview(self, message: ChatMessage, full: bool) -> bytes | None:
        self.preview_calls.append((message.chat_id, message.id, full))
        self._maybe_flood("preview")
        if message.handle != self.ref_generation:
            raise FileRefExpired("expired")
        return self.pictures.get((message.chat_id, message.id))

    async def iter_download(
        self, message: DocMessage, offset: int, request_size: int
    ) -> AsyncIterator[bytes]:
        f: FakeFile = message.handle
        self.downloads_started.append((f.tg_document_id, offset))
        self._maybe_flood("download")
        if f.tg_document_id in self.always_fail_ids:
            raise RuntimeError("simulated server error")
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
