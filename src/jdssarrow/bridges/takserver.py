"""TAK Server connector — stream CoT over TCP (optionally mutual TLS), with auto-reconnect.

Where the multicast bearer suits an ATAK *mesh*, a TAK Server aggregates clients over a TCP
stream of CoT XML events. This connector speaks that stream and exposes the same
:class:`~jdssarrow.interfaces.Transport` surface the ATAK bridge already uses (``start``/
``stop``/``send``/``on_receive``), so the bridge is agnostic to which bearer carries CoT.

A supervisor task keeps the connection up: if the server is unreachable or the link drops, it
reconnects with exponential backoff until :meth:`stop` is called. Sends during a disconnect are
dropped (the bridge keeps running); ``connects``/``last_error`` expose link state.

CoT events on the wire are self-delimiting XML documents (``<event>…</event>``); the read loop
buffers bytes and dispatches each complete event. TLS supports the usual TAK setup: a CA to
verify the server and an optional client certificate for mutual auth.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from collections import deque
from collections.abc import Awaitable, Callable

_EVENT_END = b"</event>"
_EVENT_START = b"<event"


class TakServerConnector:
    name = "takserver"

    def __init__(
        self,
        host: str,
        port: int,
        *,
        tls: bool = False,
        client_cert: str | None = None,
        client_key: str | None = None,
        cacert: str | None = None,
        verify: bool = True,
        base_backoff: float = 0.5,
        max_backoff: float = 30.0,
        connect_timeout: float = 5.0,
        queue_max: int = 1000,
    ) -> None:
        self._host = host
        self._port = port
        self._tls = tls
        self._client_cert = client_cert
        self._client_key = client_key
        self._cacert = cacert
        self._verify = verify
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._connect_timeout = connect_timeout

        self._handler: Callable[[bytes], Awaitable[None]] | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._supervisor: asyncio.Task | None = None
        self._connected = asyncio.Event()
        self._stopped = False
        # bounded outbound buffer: frames sent while disconnected are held and flushed on
        # reconnect; when full the oldest is dropped (freshest CoT matters most).
        self._queue: deque[bytes] = deque(maxlen=max(1, queue_max))
        self._write_lock = asyncio.Lock()
        self.connects = 0  # successful (re)connections
        self.dropped = 0  # frames dropped because the queue was full
        self.last_error: str | None = None

    def on_receive(self, handler: Callable[[bytes], Awaitable[None]]) -> None:
        self._handler = handler

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def reconnects(self) -> int:
        return max(0, self.connects - 1)

    @property
    def queued(self) -> int:
        return len(self._queue)

    async def wait_connected(self, timeout: float | None = None) -> None:
        if timeout is None:
            await self._connected.wait()
        else:
            await asyncio.wait_for(self._connected.wait(), timeout)

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=self._cacert)
        if self._client_cert:
            ctx.load_cert_chain(self._client_cert, self._client_key)
        if not self._verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def start(self) -> None:
        self._stopped = False
        self._supervisor = asyncio.create_task(self._run())
        # give the first connection a chance so callers can send immediately when the server is up
        with contextlib.suppress(asyncio.TimeoutError):
            await self.wait_connected(self._connect_timeout)

    async def stop(self) -> None:
        self._stopped = True
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
            self._supervisor = None

    async def send(self, frame: bytes) -> None:
        writer = self._writer
        if writer is None:
            self._enqueue(frame)  # disconnected — buffer for flush on reconnect
            return
        try:
            await self._write(writer, frame)
        except Exception as exc:  # link broke mid-send → buffer for retry
            self.last_error = repr(exc)
            self._enqueue(frame)

    def _enqueue(self, frame: bytes) -> None:
        if self._queue.maxlen is not None and len(self._queue) >= self._queue.maxlen:
            self.dropped += 1  # deque evicts the oldest on append
        self._queue.append(frame)

    async def _write(self, writer: asyncio.StreamWriter, frame: bytes) -> None:
        async with self._write_lock:  # serialize flush vs. concurrent sends
            writer.write(frame if frame.endswith(b"\n") else frame + b"\n")
            await writer.drain()

    async def _flush_queue(self, writer: asyncio.StreamWriter) -> None:
        """Send buffered frames in FIFO order; stop (keeping the rest) if the link breaks."""
        while self._queue:
            frame = self._queue[0]
            try:
                await self._write(writer, frame)
            except Exception as exc:
                self.last_error = repr(exc)
                return
            self._queue.popleft()

    # ------------------------------------------------------------ supervisor
    async def _run(self) -> None:
        backoff = self._base_backoff
        while not self._stopped:
            try:
                ssl_ctx = self._ssl_context() if self._tls else None
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port, ssl=ssl_ctx),
                    timeout=self._connect_timeout,
                )
                self._reader, self._writer = reader, writer
                self.connects += 1
                self._connected.set()
                backoff = self._base_backoff  # reset on a good connection
                await self._flush_queue(writer)  # drain anything buffered while down
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = repr(exc)
            finally:
                self._connected.clear()
                stale_writer, self._reader, self._writer = self._writer, None, None
                if stale_writer is not None:
                    stale_writer.close()
                    with contextlib.suppress(Exception):
                        await stale_writer.wait_closed()
            if self._stopped:
                break
            await asyncio.sleep(backoff)
            backoff = min(self._max_backoff, backoff * 2)

    async def _read_loop(self) -> None:
        reader = self._reader
        assert reader is not None
        buf = b""
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break  # server closed the connection → supervisor reconnects
            buf += chunk
            while True:
                end = buf.find(_EVENT_END)
                if end == -1:
                    break
                end += len(_EVENT_END)
                start = buf.find(_EVENT_START)
                if start == -1 or start > end:
                    buf = buf[end:]  # stray bytes before an event
                    continue
                doc, buf = buf[start:end], buf[end:]
                if self._handler is not None:
                    await self._handler(doc)
