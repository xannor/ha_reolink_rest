"""async io utilities"""

import asyncio


class Busy:

    __slots__ = ("_mutex", "_event", "_counter")

    def __init__(self) -> None:
        self._mutex = asyncio.Lock()
        self._event = asyncio.Event()
        self._event.set()
        self._counter = 0

    async def __aenter__(self):
        await self.acquire()
        # We have no use for the "as ..."  clause in the with
        # statement for locks.
        return None

    async def __aexit__(self, exc_type, exc, tb):
        await self.release()

    def __repr__(self) -> str:
        res = super().__repr__()
        extra = "busy" if self._counter > 0 else "free"

        return f"<{res[1:-1]} [{extra}]>"

    async def acquire(self):
        async with self._mutex:
            if self._counter == 0:
                self._event.clear()
            self._counter += 1

    async def release(self):
        async with self._mutex:
            if self._counter > 0:
                self._counter -= 1
            elif not self._event.is_set():
                self._event.set()

    def is_busy(self):
        return not self._event.is_set()

    async def wait(self):
        return not await self._event.wait()
