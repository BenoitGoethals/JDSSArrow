"""In-memory loopback transport (Vol IV).

Delivers frames to every other transport that has joined the same in-process "wire",
without touching the network. This is what the tests and the single-host demo use: it makes
the whole exchange path deterministic and fast, and it proves the :class:`Transport`
abstraction is honoured (the exchange engine cannot tell it apart from UDP).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable

# Shared in-process buses keyed by group name; each transport registers itself.
_BUSES: dict[str, set[LoopbackTransport]] = defaultdict(set)


class LoopbackTransport:
    name = "loopback"

    def __init__(self, group: str = "default") -> None:
        self._group = group
        self._handler: Callable[[bytes], Awaitable[None]] | None = None
        self._running = False

    async def start(self) -> None:
        _BUSES[self._group].add(self)
        self._running = True

    async def stop(self) -> None:
        self._running = False
        _BUSES[self._group].discard(self)

    def on_receive(self, handler: Callable[[bytes], Awaitable[None]]) -> None:
        self._handler = handler

    async def send(self, frame: bytes) -> None:
        if not self._running:
            raise RuntimeError("transport not started")
        # Deliver to every peer except ourselves, concurrently.
        peers = [p for p in _BUSES[self._group] if p is not self and p._handler]
        await asyncio.gather(*(p._handler(frame) for p in peers))  # type: ignore[misc]

    @staticmethod
    def reset() -> None:
        """Clear all buses (test hygiene)."""
        _BUSES.clear()
