"""Reading a chat's messages, straight from Telegram, a page at a time.

Nothing is stored. A small cache keeps the same page or picture from being
asked for twice (paging back and forth, the page checking for new messages,
several tabs), and requests that would fail anyway — Telegram asked us to
wait, the account is gone — are answered without contacting Telegram."""

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from tglib.engine.account import TelegramAccount
from tglib.engine.errors import FileRefExpired, FloodWait, NotAuthorized, NotConnected
from tglib.engine.telegram import ChatMessage, TelegramGateway

log = logging.getLogger(__name__)

T = TypeVar("T")

PAGE_MAX = 100  # Telegram returns up to 100 messages per request
LATEST_TTL = 5  # seconds a page of the newest messages is reused
OLDER_TTL = 300  # ... and a page of older ones, which rarely change
SEARCH_TTL = 60  # ... and a page of search results
PAGES_MAX = 64  # cached pages
MESSAGES_MAX = 4000  # messages with a picture kept for `preview`
PREVIEWS_MAX_BYTES = 48 * 1024 * 1024
PREVIEW_WORKERS = 3  # pictures fetched from Telegram at once
REQUEST_TIMEOUT = 30  # seconds before a request to Telegram is given up


class ChatHistory:
    def __init__(self, account: TelegramAccount):
        self._account = account
        self._login = 0.0  # `account.ready_at` the caches belong to
        self._pages: OrderedDict[tuple, tuple[float, list[ChatMessage]]] = OrderedDict()
        self._messages: OrderedDict[tuple[int, int], ChatMessage] = OrderedDict()
        self._previews: OrderedDict[tuple[int, int, bool], bytes] = OrderedDict()
        self._previews_bytes = 0
        self._inflight: dict[tuple, asyncio.Task] = {}
        self._flood_until = 0.0  # time.monotonic() before which Telegram wants no requests
        self._preview_slots = asyncio.Semaphore(PREVIEW_WORKERS)

    # --- public ---
    async def page(
        self, chat_id: int, *, before: int = 0, after: int = 0, limit: int = 50, forward: bool = False
    ) -> list[ChatMessage]:
        """Up to `limit` messages, newest first, with id > `after` and (if
        `before`) id < `before`: the newest of them, or with `forward` the
        ones that come right after `after`."""
        if forward:
            return await self._cached(
                ("after", chat_id, after, limit),
                lambda: self._call(lambda g: g.get_history_after(chat_id, after, limit)),
                # A full page lies inside the history; a short one ends at the newest message.
                lambda page: OLDER_TTL if len(page) == limit else LATEST_TTL,
            )
        return await self._cached(
            (chat_id, before, after, limit),
            lambda: self._fetch_page(chat_id, before, after, limit),
            lambda page: OLDER_TTL if before else LATEST_TTL,
        )

    async def search(self, chat_id: int, query: str, *, before: int = 0, limit: int = 30) -> list[ChatMessage]:
        """Up to `limit` messages of the chat matching `query`, newest first,
        with id < `before` if given."""
        return await self._cached(
            ("search", chat_id, query, before, limit),
            lambda: self._call(lambda g: g.search_history(chat_id, query, offset_id=before, limit=limit)),
            lambda page: SEARCH_TTL,
        )

    async def search_all(self, query: str, *, limit: int = 30) -> list[ChatMessage]:
        """Up to `limit` messages matching `query` across all the account's
        chats, newest first; each carries its `chat`."""
        return await self._cached(
            ("search-all", query, limit),
            lambda: self._call(lambda g: g.search_messages(query, limit)),
            lambda page: SEARCH_TTL,
        )

    async def preview(self, chat_id: int, msg_id: int, full: bool = False) -> bytes | None:
        """Picture of a message (see `TelegramGateway.download_preview`), or
        None when it has none."""
        self._require_ready()
        key = (chat_id, msg_id, full)
        data = self._previews.get(key)
        if data is not None:
            self._previews.move_to_end(key)
            return data
        return await self._shared(("preview", *key), lambda: self._fetch_preview(*key))

    def clear(self) -> None:
        """Forgets everything read so far (the account logged out)."""
        for task in list(self._inflight.values()):
            task.cancel()
        self._inflight.clear()
        self._pages.clear()
        self._messages.clear()
        self._previews.clear()
        self._previews_bytes = 0
        self._flood_until = 0.0

    async def stop(self) -> None:
        tasks = list(self._inflight.values())
        self.clear()
        await asyncio.gather(*tasks, return_exceptions=True)

    # --- internals ---
    def _require_ready(self) -> None:
        if not self._account.ready:
            raise NotConnected("Telegram account is not ready")
        if self._account.ready_at != self._login:
            # A new login, maybe of another account: nothing cached applies.
            self.clear()
            self._login = self._account.ready_at

    async def _shared(self, key: tuple, start: Callable[[], Awaitable[T]]) -> T:
        """Runs `start` once for all callers asking for `key` at the same time.
        It finishes (and fills the cache) even if its callers go away."""
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(start())
            self._inflight[key] = task

            def done(t: asyncio.Task) -> None:
                if self._inflight.get(key) is t:
                    del self._inflight[key]
                if not t.cancelled():
                    t.exception()  # mark as retrieved: every caller may have left

            task.add_done_callback(done)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():  # dropped by `clear`, not our caller going away
                raise NotConnected("Telegram account changed") from None
            raise

    async def _call(self, request: Callable[[TelegramGateway], Awaitable[T]]) -> T:
        """One request to Telegram, bounded in time, with its failures reported
        to whoever needs to know."""
        self._require_ready()
        wait = self._flood_until - time.monotonic()
        if wait > 0:
            raise FloodWait(round(wait) or 1)  # don't make Telegram's wait longer
        try:
            return await asyncio.wait_for(request(self._account.gateway), REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            self._account.mark_disconnected()
            raise NotConnected("Telegram did not answer in time") from None
        except FloodWait as e:
            log.warning("Reading chats delayed by flood wait: %ss", e.seconds)
            self._flood_until = time.monotonic() + e.seconds
            raise
        except NotAuthorized:
            self._account.mark_unauthorized()
            raise
        except NotConnected:
            self._account.mark_disconnected()
            raise

    async def _cached(
        self,
        key: tuple,
        fetch: Callable[[], Awaitable[list[ChatMessage]]],
        ttl: Callable[[list[ChatMessage]], float],
    ) -> list[ChatMessage]:
        """The page cached under `key`, or `fetch`ed and kept for `ttl(page)` seconds."""
        self._require_ready()
        cached = self._pages.get(key)
        if cached is not None and cached[0] > time.monotonic():
            self._pages.move_to_end(key)
            return cached[1]
        return await self._shared(key, lambda: self._fetch(key, fetch, ttl))

    async def _fetch(self, key: tuple, fetch, ttl) -> list[ChatMessage]:
        login = self._login
        page = await fetch()
        if login != self._login:
            return page  # logged out meanwhile: don't cache
        self._pages[key] = (time.monotonic() + ttl(page), page)
        self._pages.move_to_end(key)
        while len(self._pages) > PAGES_MAX:
            self._pages.popitem(last=False)
        for m in page:
            if m.media is not None and m.media.has_preview:
                self._messages[(m.chat_id, m.id)] = m
                self._messages.move_to_end((m.chat_id, m.id))
        while len(self._messages) > MESSAGES_MAX:
            self._messages.popitem(last=False)
        return page

    async def _fetch_page(self, chat_id: int, before: int, after: int, limit: int) -> list[ChatMessage]:
        if after and not before:
            # "Anything new?" is asked every few seconds and the answer is
            # mostly no. A page is downloaded whole before `after` is applied,
            # so look at the newest message alone first.
            newest = await self._call(lambda g: g.get_history(chat_id, limit=1))
            if not newest or newest[0].id <= after:
                return []
            if newest[0].id == after + 1:
                return newest  # the only new one
        return await self._call(lambda g: g.get_history(chat_id, offset_id=before, min_id=after, limit=limit))

    async def _fetch_preview(self, chat_id: int, msg_id: int, full: bool) -> bytes | None:
        login = self._login
        async with self._preview_slots:
            message = self._messages.get((chat_id, msg_id))
            data = None
            # A message from a cached page may carry an expired file
            # reference: fetch it again once.
            for fresh in (message is None, True):
                if fresh:
                    message = await self._call(lambda g: g.get_message(chat_id, msg_id))
                if message is None or message.media is None or not message.media.has_preview:
                    return None
                try:
                    data = await self._call(lambda g: g.download_preview(message, full))
                    break
                except FileRefExpired:
                    if fresh:
                        raise
        if data is None or login != self._login:
            return data
        self._messages[(chat_id, msg_id)] = message
        self._previews_bytes += len(data) - len(self._previews.get((chat_id, msg_id, full), b""))
        self._previews[(chat_id, msg_id, full)] = data
        while self._previews_bytes > PREVIEWS_MAX_BYTES and len(self._previews) > 1:
            _, old = self._previews.popitem(last=False)
            self._previews_bytes -= len(old)
        return data
