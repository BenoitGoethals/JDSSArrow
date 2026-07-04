"""Live CoT/TAK server connections managed from the web UI.

Owns one auto-reconnecting :class:`TakServerConnector` per *enabled* server in the config and
bridges it to the web node's running gateway, mirroring :class:`jdssarrow.bridges.atak.AtakBridge`:

* **JDSS -> CoT**: JDSS messages from *other* nodes are translated to CoT and sent to every
  connected server (so coalition traffic reaches OpenTAKServer / ATAK).
* **CoT -> JDSS**: inbound CoT is translated and re-published onto the JDSS network under this
  node's identity (so server-side tracks appear on the coalition COP).

The manager is edited (CRUD) at runtime: :meth:`reconcile` diffs the desired server list against
the live connectors, starting/stopping/replacing as needed. Loop protection reuses the CoT bridge
marker; the node's own originated traffic is not echoed back out (same rule as the ATAK bridge).
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Awaitable, Callable, Iterable
from xml.etree import ElementTree as ET

from jdssarrow.bridges.cot import (
    BRIDGE_MARKER,
    cot_is_stale,
    cot_to_message,
    message_to_cot,
)
from jdssarrow.bridges.takserver import TakServerConnector
from jdssarrow.config.models import ServerConnection
from jdssarrow.datamodel.messages import Identification, JdssMessage, MessageHeader
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode

# fields that require tearing the connector down and rebuilding it when they change
_CONNECT_FIELDS = ("host", "port", "tls", "verify", "client_cert", "client_key", "cacert")


class ServerConnectionManager:
    def __init__(self, *, stale_s: int = 120) -> None:
        self._stale_s = stale_s
        self._defs: dict[str, ServerConnection] = {}  # all servers, incl. disabled (for status)
        self._conns: dict[str, TakServerConnector] = {}  # live connectors, enabled only
        self._node: SoldierNode | None = None
        self._gateway: JdssGateway | None = None
        self._relay_nodes: set[int] = set()  # node ids we've already attached the relay to
        self._tmp: dict[str, list[str]] = {}  # per-server temp PEM files to clean up
        self._origins: dict[str, set[str]] = {}  # server id -> originators it has sourced
        self._identified: set[str] = set()  # originators we've announced an Identification for

    # ------------------------------------------------------------------ wiring
    def attach(self, node: SoldierNode, gateway: JdssGateway) -> None:
        """Point the manager at the (current) running node; called on start and after each
        hot-reconfigure, since reconfigure builds a fresh node."""
        self._node = node
        self._gateway = gateway
        if id(node) not in self._relay_nodes:  # attach the JDSS->CoT relay once per node
            node.add_handler(_ServerRelay(self))
            self._relay_nodes.add(id(node))

    @property
    def _node_id(self) -> str:
        return self._gateway.config.identity.node_id if self._gateway else ""

    # ------------------------------------------------------------------ CRUD/reconcile
    async def reconcile(self, servers: list[ServerConnection]) -> None:
        """Bring live connectors in line with ``servers`` (the desired state)."""
        desired = {s.id: s for s in servers}
        # stop connectors that are gone, disabled, or whose connection params changed
        for sid in list(self._conns):
            new = desired.get(sid)
            if new is None or not new.enabled or _connect_changed(self._defs.get(sid), new):
                await self._stop_one(sid)
        # start connectors that should be up but aren't
        for sid, s in desired.items():
            if s.enabled and sid not in self._conns:
                await self._start_one(s)
        self._defs = desired

    async def _start_one(self, s: ServerConnection) -> None:
        cert, key, ca, tmp = _materialize_certs(s)
        self._tmp[s.id] = tmp
        conn = TakServerConnector(
            s.host, s.port,
            tls=s.tls, verify=s.verify,
            client_cert=cert, client_key=key, cacert=ca,
        )
        conn.on_receive(self._rx_for(s.id))
        self._conns[s.id] = conn
        await conn.start()

    def _rx_for(self, sid: str) -> Callable[[bytes], Awaitable[None]]:
        async def rx(raw: bytes) -> None:
            await self._on_cot(sid, raw)

        return rx

    async def _stop_one(self, sid: str) -> None:
        conn = self._conns.pop(sid, None)
        if conn is not None:
            with contextlib.suppress(Exception):
                await conn.stop()
        self._origins.pop(sid, None)
        _cleanup_temp(self._tmp.pop(sid, []))

    # ------------------------------------------------------------------ bridging
    async def _on_cot(self, sid: str, raw: bytes) -> None:
        """CoT -> JDSS: re-publish an inbound server track onto the JDSS net + local picture."""
        if BRIDGE_MARKER.encode() in raw:
            return  # our own emitted CoT echoed back
        if cot_is_stale(raw):
            return
        gateway = self._gateway
        if gateway is None:
            return
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return
        ctype = root.get("type", "")
        if ctype.startswith("t-x") or ctype.startswith("t-b"):
            return  # TAK control (ping/pong, delete, negotiation) — not mappable
        # a stable per-track originator (uid-scoped to this server), so each remote client is a
        # distinct coalition peer rather than collapsing onto this node's identity.
        uid = root.get("uid") or "track"
        originator = f"tak-{sid}-{uid}"
        message = cot_to_message(raw, originator)
        if message is None:
            return
        self._origins.setdefault(sid, set()).add(originator)

        callsign = getattr(message.body, "callsign", None)
        if callsign and originator not in self._identified:
            self._identified.add(originator)
            ident = Identification(callsign=callsign, unit=f"TAK/{sid}", role="tak", nation="")
            with contextlib.suppress(Exception):
                await gateway.ingest_from_bridge(self._message(originator, ident))

        with contextlib.suppress(Exception):
            await gateway.ingest_from_bridge(self._message(originator, message.body))

    def _message(self, originator: str, body: object) -> JdssMessage:
        c = self._gateway.config  # type: ignore[union-attr]
        header = MessageHeader(
            originator_id=originator,
            network_id=c.network.network_id,
            classification=c.classification.level,
            releasable_to=c.classification.releasable_to,
        )
        return JdssMessage(header=header, body=body)  # type: ignore[arg-type]

    async def _emit(self, message: JdssMessage) -> None:
        """JDSS -> CoT: fan a JDSS message out to every connected server (except its source)."""
        if not self._conns:
            return
        if message.header.originator_id == self._node_id:
            return  # don't echo our own node's originated traffic
        cot = message_to_cot(message, stale_s=self._stale_s)
        if cot is None:
            return
        src = message.header.originator_id
        for sid, conn in list(self._conns.items()):
            if src in self._origins.get(sid, ()):
                continue  # don't echo a server's own track back to it
            with contextlib.suppress(Exception):
                await conn.send(cot)

    # ------------------------------------------------------------------ status/test
    def status(self) -> list[dict]:
        out = []
        for sid, s in self._defs.items():
            conn = self._conns.get(sid)
            out.append({
                **s.model_dump(),
                "state": _state(s, conn),
                "connected": bool(conn and conn.connected),
                "connects": conn.connects if conn else 0,
                "reconnects": conn.reconnects if conn else 0,
                "queued": conn.queued if conn else 0,
                "dropped": conn.dropped if conn else 0,
                "last_error": conn.last_error if conn else None,
            })
        return out

    async def test(self, s: ServerConnection, timeout: float = 5.0) -> dict:
        """One-shot connectivity probe; does not disturb any live connector."""
        cert, key, ca, tmp = _materialize_certs(s)
        conn = TakServerConnector(
            s.host, s.port, tls=s.tls, verify=s.verify,
            client_cert=cert, client_key=key, cacert=ca,
            connect_timeout=timeout,
        )
        conn.on_receive(_noop)
        await conn.start()
        try:
            await conn.wait_connected(timeout)
            return {"ok": True, "detail": f"connected to {s.host}:{s.port}"}
        except TimeoutError:
            return {"ok": False, "detail": conn.last_error or "connection timed out"}
        finally:
            await conn.stop()
            _cleanup_temp(tmp)

    async def stop(self) -> None:
        for sid in list(self._conns):
            await self._stop_one(sid)


async def _noop(_raw: bytes) -> None:
    return None


def _materialize_certs(s: ServerConnection) -> tuple[str | None, str | None, str | None, list[str]]:
    """Resolve the cert/key/CA fields to file paths the ``ssl`` module can load.

    A field holding PEM *content* (e.g. imported from a .p12) is written to a private (0600) temp
    file; a field holding a plain path is used as-is, so power users can still point at files on
    disk. Returns ``(cert_path, key_path, ca_path, temp_files_to_clean_up)``.
    """
    tmp: list[str] = []

    def resolve(value: str | None, suffix: str) -> str | None:
        if not value:
            return None
        if value.lstrip().startswith("-----BEGIN"):
            fd, path = tempfile.mkstemp(suffix=suffix)  # mkstemp creates the file mode 0600
            with os.fdopen(fd, "w") as fh:
                fh.write(value)
            tmp.append(path)
            return path
        return value  # treat as an existing filesystem path

    return (
        resolve(s.client_cert, ".pem"),
        resolve(s.client_key, ".key"),
        resolve(s.cacert, ".pem"),
        tmp,
    )


def _cleanup_temp(paths: list[str]) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            os.unlink(path)


def _connect_changed(old: ServerConnection | None, new: ServerConnection) -> bool:
    if old is None:
        return True
    return any(getattr(old, f) != getattr(new, f) for f in _CONNECT_FIELDS)


def _state(s: ServerConnection, conn: TakServerConnector | None) -> str:
    if not s.enabled:
        return "disabled"
    if conn is None:
        return "stopped"
    return "connected" if conn.connected else "connecting"


class _ServerRelay:
    """Node handler that forwards JDSS messages to the server connectors (JDSS -> CoT)."""

    subscribes_to: Iterable[str] = ("*",)

    def __init__(self, manager: ServerConnectionManager) -> None:
        self._manager = manager

    async def handle(self, message: JdssMessage) -> None:
        await self._manager._emit(message)
