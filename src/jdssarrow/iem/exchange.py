"""The Information Exchange Mechanism engine (Vol IV).

This is the reliability + dispatch layer that sits *above* a best-effort :class:`Transport`
and *below* the application. Responsibilities:

* **Framing** — wrap an encoded, security-protected payload with a compact header that names
  the codec and security scheme used, so heterogeneous coalition partners can interpret it.
* **Sequencing** — stamp each outbound message with a per-originator sequence number.
* **Reliability** — on a lossy multicast bearer there are no ACKs, so the engine uses
  *message repetition* (send each frame ``repeat`` times) combined with receiver-side
  **deduplication**, the standard tactical-edge technique. Repeats are idempotent.
* **Dispatch** — publish/subscribe fan-out to registered :class:`MessageHandler`s.

The engine depends only on the ``Transport``, ``Codec``, ``SecurityProvider`` and
``MetricsSink`` protocols — never on concrete implementations.
"""

from __future__ import annotations

import json
import logging
import struct
from collections import deque
from collections.abc import Awaitable, Callable, Iterable

from jdssarrow.datamodel.messages import JdssMessage
from jdssarrow.interfaces import (
    Capabilities,
    Codec,
    ConnectionPolicy,
    MessageHandler,
    MetricsSink,
    SecurityProvider,
    Transport,
)

_log = logging.getLogger("jdssarrow.iem")

_MAGIC = b"JDSS"
_VERSION = 1

#: reserved codec name marking an out-of-band control frame (e.g. peer-digest gossip).
#: Control frames are still HMAC-protected, but carry JSON control data rather than a JDSSDM
#: message, and are dispatched separately so they never enter the operational picture.
CONTROL_CODEC = "__ctl__"


class Frame:
    """Wire framing: magic | version | codec-name | security-name | payload."""

    __slots__ = ("codec_name", "security_name", "payload")

    def __init__(self, codec_name: str, security_name: str, payload: bytes) -> None:
        self.codec_name = codec_name
        self.security_name = security_name
        self.payload = payload

    def to_bytes(self) -> bytes:
        codec = self.codec_name.encode("ascii")
        sec = self.security_name.encode("ascii")
        return b"".join(
            [
                _MAGIC,
                bytes([_VERSION, len(codec)]),
                codec,
                bytes([len(sec)]),
                sec,
                struct.pack(">I", len(self.payload)),
                self.payload,
            ]
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> Frame:
        if raw[:4] != _MAGIC:
            raise ValueError("bad magic")
        version = raw[4]
        if version != _VERSION:
            raise ValueError(f"unsupported frame version {version}")
        pos = 5
        clen = raw[pos]
        pos += 1
        codec_name = raw[pos : pos + clen].decode("ascii")
        pos += clen
        slen = raw[pos]
        pos += 1
        security_name = raw[pos : pos + slen].decode("ascii")
        pos += slen
        (plen,) = struct.unpack(">I", raw[pos : pos + 4])
        pos += 4
        payload = raw[pos : pos + plen]
        return cls(codec_name, security_name, payload)


class _NullMetrics:
    name = "null"

    def record_sent(self, message: JdssMessage) -> None: ...
    def record_received(self, message: JdssMessage) -> None: ...
    def record_dropped(self, reason: str, message: JdssMessage | None = None) -> None: ...
    def node_seen(self, node_id: str) -> None: ...


class ExchangeEngine:
    def __init__(
        self,
        *,
        node_id: str,
        transport: Transport,
        codec: Codec,
        security: SecurityProvider,
        metrics: MetricsSink | None = None,
        policy: ConnectionPolicy | None = None,
        capabilities: Capabilities | None = None,
        repeat: int = 2,
        dedup_window: int = 4096,
    ) -> None:
        self._node_id = node_id
        self._transport = transport
        self._codec = codec
        self._security = security
        self._policy = policy
        self._capabilities = capabilities
        self._metrics: MetricsSink = metrics or _NullMetrics()
        self._repeat = max(1, repeat)
        self._handlers: list[MessageHandler] = []
        self._control_handlers: dict[str, Callable[[dict], Awaitable[None]]] = {}
        self._seq = 0
        # Bounded dedup: a set for O(1) membership + a deque for FIFO eviction.
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=dedup_window)
        transport.on_receive(self._on_frame)

    def add_handler(self, handler: MessageHandler) -> None:
        self._handlers.append(handler)

    def on_control(self, kind: str, handler: Callable[[dict], Awaitable[None]]) -> None:
        """Register a handler for an out-of-band control message ``kind``."""
        self._control_handlers[kind] = handler

    async def start(self) -> None:
        await self._transport.start()

    async def stop(self) -> None:
        await self._transport.stop()

    # ------------------------------------------------------------------ publish
    async def publish(self, message: JdssMessage) -> JdssMessage:
        """Stamp, encode, protect and transmit a message (with repetition)."""
        message.header.originator_id = message.header.originator_id or self._node_id
        self._seq += 1
        message.header.sequence = self._seq
        payload = self._codec.encode(message)
        wire = self._security.protect(payload)
        frame = Frame(self._codec.name, self._security.name, wire).to_bytes()
        for _ in range(self._repeat):
            await self._transport.send(frame)
        self._metrics.record_sent(message)
        self._remember(message)  # so our own repeats/echoes are ignored on receive
        return message

    async def publish_control(self, kind: str, data: dict) -> None:
        """Broadcast an out-of-band, HMAC-protected control message (not a JDSSDM message).

        Used by peer-digest gossip. Control frames never touch the codec, dedup, telemetry or
        the operational picture — but they *are* authenticated, so an unauthorised node's
        control frames are dropped exactly like its data frames.
        """
        body = {"kind": kind, "node": self._node_id, **data}
        wire = self._security.protect(json.dumps(body).encode("utf-8"))
        frame = Frame(CONTROL_CODEC, self._security.name, wire).to_bytes()
        for _ in range(self._repeat):
            await self._transport.send(frame)

    # ------------------------------------------------------------------ receive
    async def _on_frame(self, raw: bytes) -> None:
        # each stage records a *specific* reason so the audit log can say exactly why a frame
        # was rejected (framing / security / codec / policy / capability / duplicate).
        try:
            frame = Frame.from_bytes(raw)
        except Exception as exc:
            self._reject(f"framing: {exc}")
            return
        try:
            payload = self._security.verify(frame.payload)
        except Exception as exc:
            self._reject(f"security: {exc}")
            return
        if frame.codec_name == CONTROL_CODEC:
            try:
                await self._handle_control(payload)
            except Exception as exc:  # control frames must never break the receive loop
                _log.debug("control frame ignored: %s", exc)
            return
        try:
            codec = self._select_codec(frame.codec_name)
            message = codec.decode(payload)
        except Exception as exc:
            self._reject(f"codec: {exc}")
            return

        if self._policy is not None and not self._policy.allows(message):
            self._reject(f"policy: sender {message.header.originator_id} not permitted", message)
            return
        if self._capabilities is not None and not self._capabilities.can_receive(message.type):
            self._reject(f"capability: receive of {message.type} disabled", message)
            return
        if self._is_duplicate(message):
            self._metrics.record_dropped("duplicate", message)  # routine; not logged as WARN
            return
        self._remember(message)
        self._metrics.node_seen(message.header.originator_id)
        self._metrics.record_received(message)
        await self._dispatch(message)

    def _reject(self, reason: str, message: JdssMessage | None = None) -> None:
        self._metrics.record_dropped(reason, message)
        _log.warning("[%s] rejected inbound frame — %s", self._node_id, reason)

    async def _handle_control(self, payload: bytes) -> None:
        data = json.loads(payload.decode("utf-8"))
        handler = self._control_handlers.get(data.get("kind", ""))
        if handler is not None:
            await handler(data)

    def _select_codec(self, name: str) -> Codec:
        # Fast path: same codec both ends. Cross-codec interop would look the codec up in
        # the plugin registry; kept simple here since the gateway configures one codec.
        if name == self._codec.name:
            return self._codec
        raise ValueError(f"no codec for frame codec {name!r}")

    async def _dispatch(self, message: JdssMessage) -> None:
        for handler in self._handlers:
            subs: Iterable[str] = handler.subscribes_to
            if "*" in subs or message.type in subs:
                await handler.handle(message)

    # ------------------------------------------------------------------ dedup
    @staticmethod
    def _key(message: JdssMessage) -> str:
        return f"{message.header.originator_id}:{message.header.message_id}"

    def _is_duplicate(self, message: JdssMessage) -> bool:
        return self._key(message) in self._seen

    def _remember(self, message: JdssMessage) -> None:
        key = self._key(message)
        if key in self._seen:
            return
        if len(self._seen_order) == self._seen_order.maxlen:
            self._seen.discard(self._seen_order[0])  # evicted by append below
        self._seen_order.append(key)
        self._seen.add(key)
