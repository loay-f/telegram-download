import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select

from tglib.app.config import Settings
from tglib.app.container import Container
from tglib.db.migrate import upgrade_to_head
from tglib.db.models import Appearance, Chat, Document
from fake_telegram import FakeGateway


@asynccontextmanager
async def running_container(
    settings: Settings, gw: FakeGateway, *, loops: bool = False
) -> AsyncIterator[Container]:
    """Container with migrated DB and a ready Telegram account. Background
    loops (scanner, download workers) only run when `loops` is true."""
    settings.ensure_dirs()
    await asyncio.to_thread(upgrade_to_head, settings.db_path)
    ctx = Container(settings, lambda: gw)
    if loops:
        await ctx.start()
    else:
        await ctx.app_settings.load()
        await ctx.account.start()
    await asyncio.wait_for(ctx.account.wait_ready(), 5)
    try:
        yield ctx
    finally:
        await ctx.stop()


async def track(ctx: Container, chat_id: int, title: str = "chat", auto_download: bool = True) -> None:
    """Tracks a chat. Engine tests want files queued, so auto-download is on
    by default here (the app's default is off: scan only)."""
    async with ctx.db.write() as s:
        chat = await s.get(Chat, chat_id)
        if chat is None:
            s.add(Chat(id=chat_id, title=title, type="channel", tracked=True, auto_download=auto_download,
                       last_scanned_msg_id=0, is_member=True))
        else:
            chat.tracked = True
            chat.auto_download = auto_download


async def all_docs(ctx: Container) -> list[Document]:
    async with ctx.db.read() as s:
        return list((await s.execute(select(Document).order_by(Document.id))).scalars())


async def all_appearances(ctx: Container) -> list[Appearance]:
    async with ctx.db.read() as s:
        return list((await s.execute(select(Appearance))).scalars())


async def get_chat(ctx: Container, chat_id: int) -> Chat:
    async with ctx.db.read() as s:
        return await s.get(Chat, chat_id)
