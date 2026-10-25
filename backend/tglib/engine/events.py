import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class EventBus:
    """In-process pub/sub feeding the SSE endpoint. Slow subscribers drop events
    rather than block publishers."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def publish(self, type_: str, data: Any = None) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait((type_, data))
            except asyncio.QueueFull:
                pass

    @asynccontextmanager
    async def subscribe(self, maxsize: int = 200) -> AsyncIterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subs.add(q)
        try:
            yield q
        finally:
            self._subs.discard(q)
