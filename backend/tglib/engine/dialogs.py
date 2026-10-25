"""Mirrors the account's channels and groups into the `chats` table."""

from sqlalchemy import select, update

from tglib.db.models import Chat
from tglib.db.session import Database
from tglib.engine.telegram import TelegramGateway


async def sync_dialogs(db: Database, gateway: TelegramGateway) -> int:
    dialogs = [d async for d in gateway.iter_dialogs() if d.type in ("channel", "group")]
    seen = {d.id for d in dialogs}
    async with db.write() as s:
        existing = {c.id: c for c in (await s.execute(select(Chat))).scalars()}
        for d in dialogs:
            chat = existing.get(d.id)
            if chat is None:
                s.add(
                    Chat(
                        id=d.id, title=d.title, username=d.username, type=d.type,
                        tracked=False, last_scanned_msg_id=0, is_member=True,
                    )
                )
            else:
                chat.title, chat.username, chat.type, chat.is_member = d.title, d.username, d.type, True
        gone = [cid for cid in existing if cid not in seen]
        if gone:
            await s.execute(update(Chat).where(Chat.id.in_(gone)).values(is_member=False))
    return len(dialogs)
