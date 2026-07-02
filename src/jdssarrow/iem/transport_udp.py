"""Real asyncio UDP multicast transport (Vol IV).

Frames are sent to a multicast group so every gateway that has joined the group receives
them — the network analogue of the loopback bus. This is a *best-effort* bearer; ordering,
deduplication and retransmission are the exchange engine's job, keeping this class a thin
adapter over the OS socket.

The socket is configured for local-segment multicast (the tactical-edge assumption of a
loaned-radio mesh): ``SO_REUSEADDR``/``SO_REUSEPORT`` so several nodes can share a host, a
small TTL, and loopback enabled so co-hosted demo nodes hear each other.
"""

from __future__ import annotations

import asyncio
import socket
import struct
from collections.abc import Awaitable, Callable


class _McastProtocol(asyncio.DatagramProtocol):
    def __init__(self, owner: UdpMulticastTransport) -> None:
        self._owner = owner

    def datagram_received(self, data: bytes, addr: tuple) -> None:  # noqa: D401
        self._owner._on_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover
        self._owner._metrics_drop(f"socket error: {exc}")


class UdpMulticastTransport:
    name = "udp"

    def __init__(
        self,
        group: str = "239.4.6.77",
        port: int = 46770,
        ttl: int = 1,
        drop_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._group = group
        self._port = port
        self._ttl = ttl
        self._handler: Callable[[bytes], Awaitable[None]] | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._drop_hook = drop_hook

    def on_receive(self, handler: Callable[[bytes], Awaitable[None]]) -> None:
        self._handler = handler

    def _metrics_drop(self, reason: str) -> None:
        if self._drop_hook:
            self._drop_hook(reason)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with_reuseport = getattr(socket, "SO_REUSEPORT", None)
        if with_reuseport is not None:
            sock.setsockopt(socket.SOL_SOCKET, with_reuseport, 1)
        sock.bind(("", self._port))
        # Join the group on the default interface.
        mreq = struct.pack("4sl", socket.inet_aton(self._group), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self._ttl)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.setblocking(False)
        self._transport, _ = await self._loop.create_datagram_endpoint(
            lambda: _McastProtocol(self), sock=sock
        )

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    async def send(self, frame: bytes) -> None:
        if self._transport is None:
            raise RuntimeError("transport not started")
        self._transport.sendto(frame, (self._group, self._port))

    def _on_datagram(self, data: bytes, addr: tuple) -> None:
        if self._handler is None or self._loop is None:
            return
        # Bridge the sync protocol callback into the async handler.
        handler = self._handler

        async def _deliver() -> None:
            await handler(data)

        self._loop.create_task(_deliver())
