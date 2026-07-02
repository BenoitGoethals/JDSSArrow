"""Extension-point contracts for JDSSArrow.

Every volume of AEP-76 is implemented against the ``Protocol`` types defined here, never
against concrete classes. This is the Dependency Inversion Principle in practice: the
:mod:`jdssarrow.gateway` composition root is the only place that knows which concrete
implementation backs each abstraction, and the plugin registry lets any of them be
swapped at runtime.

The protocols are intentionally minimal — one reason to change each.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from jdssarrow.datamodel.messages import JdssMessage


# --------------------------------------------------------------------------- codec (Vol II)
@runtime_checkable
class Codec(Protocol):
    """Serialize/deserialize a :class:`JdssMessage` to/from wire bytes.

    Implementations are pure functions of their input (no I/O, no shared state), which is
    what lets them be selected per-message and unit-tested in isolation.
    """

    #: short stable identifier, also the entry-point name (e.g. ``"xml"``).
    name: str
    #: MIME-ish content type advertised on the wire frame.
    content_type: str

    def encode(self, message: JdssMessage) -> bytes: ...

    def decode(self, raw: bytes) -> JdssMessage: ...


# ----------------------------------------------------------------------- security (Vol I)
@runtime_checkable
class SecurityProvider(Protocol):
    """Protect and verify a serialized payload (Vol I).

    Kept orthogonal to the codec so classification/authentication is independent of the
    data-model representation.
    """

    name: str

    def protect(self, payload: bytes) -> bytes:
        """Wrap an encoded payload (e.g. sign/encrypt). Returns bytes to transmit."""

    def verify(self, wire: bytes) -> bytes:
        """Unwrap/authenticate a received frame. Returns the original payload.

        Raises :class:`jdssarrow.security.provider.SecurityError` on failure.
        """


# --------------------------------------------------------------------- transport (Vol IV)
@runtime_checkable
class Transport(Protocol):
    """Best-effort datagram transport for the Information Exchange Mechanism (Vol IV).

    A transport moves opaque frames between gateways. Reliability semantics (dedup,
    retransmit) live in :class:`jdssarrow.iem.exchange.ExchangeEngine`, above this line,
    so a transport stays a thin bearer adapter.
    """

    name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, frame: bytes) -> None:
        """Emit a frame to all peers on the group."""

    def on_receive(self, handler: Callable[[bytes], Awaitable[None]]) -> None:
        """Register the coroutine invoked for every inbound frame."""


# ------------------------------------------------------------------ loaned radio (Vol III)
@runtime_checkable
class RadioBearer(Protocol):
    """A loaned radio offered to a coalition partner (Vol III).

    Models the physical/link resource that a national system borrows to reach the
    coalition network. The simulated implementation wraps a :class:`Transport`.
    """

    name: str
    radio_id: str

    async def join(self, network_id: str) -> None: ...

    async def leave(self) -> None: ...

    def transport(self) -> Transport:
        """The transport this radio exposes to the borrowing system."""


# --------------------------------------------------------------- network access (Vol V)
@runtime_checkable
class AddressAllocator(Protocol):
    """Assign unicast/multicast addresses prior to a mission (Vol V)."""

    name: str

    def allocate_unicast(self, node_id: str) -> str: ...

    def multicast_group(self, network_id: str) -> tuple[str, int]:
        """Return ``(group_ip, port)`` for a coalition network."""


# ---------------------------------------------------------------------- dispatch handlers
@runtime_checkable
class MessageHandler(Protocol):
    """Application-level reaction to a decoded, authenticated message."""

    #: message types this handler cares about; ``"*"`` matches all.
    subscribes_to: Iterable[str]

    async def handle(self, message: JdssMessage) -> None: ...


# ------------------------------------------------------------------ connection policy
@runtime_checkable
class ConnectionPolicy(Protocol):
    """Decides whether this node accepts traffic from a given peer.

    Enforced on ingest *after* security and schema validation but *before* dispatch, so a
    disallowed peer's messages never enter the operational picture. This is the "connection
    matrix that manages connections": each node's policy is its row of the coalition
    admit/deny matrix, editable at runtime.
    """

    name: str

    def allows(self, message: JdssMessage) -> bool: ...


# ------------------------------------------------------------------ capabilities
@runtime_checkable
class Capabilities(Protocol):
    """Which message types this node may receive (enforced on ingest)."""

    def can_receive(self, message_type: str) -> bool: ...


# ----------------------------------------------------------------------------- monitoring
@runtime_checkable
class MetricsSink(Protocol):
    """Receives observability events from the gateway."""

    name: str

    def record_sent(self, message: JdssMessage) -> None: ...

    def record_received(self, message: JdssMessage) -> None: ...

    def record_dropped(self, reason: str, message: JdssMessage | None = None) -> None: ...

    def node_seen(self, node_id: str) -> None: ...


# --------------------------------------------------------------------------- config store
@runtime_checkable
class ConfigStore(Protocol):
    """Persist and load gateway configuration (files by default)."""

    name: str

    def load(self) -> dict: ...

    def save(self, data: dict) -> None: ...
