"""Built-in CoT/TAK server so ATAK EUDs connect directly to this node.

This is the inbound counterpart to :class:`jdssarrow.web.servers.ServerConnectionManager` (which
dials *out* to a TAK server). When enabled, the node listens on a TCP port and speaks the legacy
TAK streaming protocol (CoT XML, protocol version 0 — what ATAK falls back to without protobuf
negotiation):

* **CoT -> JDSS**: a connected EUD's CoT is translated and published onto the JDSS network, under a
  per-EUD identity learned from its self-SA uid, so each device shows up as its own coalition peer.
* **JDSS -> CoT**: JDSS messages from other nodes are streamed to every connected EUD as CoT.

Loop protection reuses the CoT bridge marker, and the node's own originated traffic is never sent
back out (same rule as the ATAK bridge).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from xml.etree import ElementTree as ET

from jdssarrow.bridges.cot import (
    BRIDGE_MARKER,
    cot_is_stale,
    cot_to_message,
    message_to_cot,
)
from jdssarrow.config.models import EudServerConfig
from jdssarrow.datamodel.messages import JdssMessage, MessageHeader
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode

_EVENT_END = b"</event>"
_EVENT_START = b"<event"


class EudServerManager:
    def __init__(self, *, stale_s: int = 120) -> None:
        self._stale_s = stale_s
        self._cfg: EudServerConfig | None = None
        self._server: asyncio.Server | None = None
        self._clients: dict[int, tuple[asyncio.StreamWriter, str]] = {}  # id -> (writer, peer)
        self._node: SoldierNode | None = None
        self._gateway: JdssGateway | None = None
        self._relay_nodes: set[int] = set()
        self.last_error: str | None = None

    # ------------------------------------------------------------------ wiring
    def attach(self, node: SoldierNode, gateway: JdssGateway) -> None:
        self._node = node
        self._gateway = gateway
        if id(node) not in self._relay_nodes:
            node.add_handler(_EudRelay(self))
            self._relay_nodes.add(id(node))

    @property
    def _node_id(self) -> str:
        return self._gateway.config.identity.node_id if self._gateway else ""

    # ------------------------------------------------------------------ lifecycle
    async def reconfigure(self, cfg: EudServerConfig) -> None:
        """Start/stop/rebind the listener to match ``cfg``."""
        rebind = self._cfg is None or cfg.port != self._cfg.port or cfg.host != self._cfg.host
        if self._server is not None and (not cfg.enabled or rebind):
            await self._stop_server()
        self._cfg = cfg
        if cfg.enabled and self._server is None:
            await self._start_server(cfg)

    async def _start_server(self, cfg: EudServerConfig) -> None:
        try:
            self._server = await asyncio.start_server(self._on_client, cfg.host, cfg.port)
            self.last_error = None
        except OSError as exc:  # e.g. port already in use
            self._server = None
            self.last_error = str(exc)

    async def _stop_server(self) -> None:
        server, self._server = self._server, None
        for writer, _ in list(self._clients.values()):
            with contextlib.suppress(Exception):
                writer.close()
        self._clients.clear()
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()

    async def stop(self) -> None:
        await self._stop_server()

    # ------------------------------------------------------------------ per-client I/O
    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        cid = id(writer)
        peer = writer.get_extra_info("peername")
        self._clients[cid] = (writer, _fmt_peer(peer))
        originator = f"atak-{cid}"  # provisional until we learn the device's self-SA uid
        buf = b""
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
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
                    originator = await self._ingest(doc, originator)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.pop(cid, None)
            with contextlib.suppress(Exception):
                writer.close()

    async def _ingest(self, raw: bytes, originator: str) -> str:
        """CoT -> JDSS. Returns the (possibly updated) per-connection originator id."""
        if BRIDGE_MARKER.encode() in raw:
            return originator  # our own emitted CoT echoed back
        if cot_is_stale(raw):
            return originator
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return originator
        # adopt the device's stable uid from its self-SA so it's one coalition peer, not many
        uid, ctype = root.get("uid"), root.get("type", "")
        if uid and ctype.startswith("a-f"):
            originator = f"atak-{uid}"
        gateway = self._gateway
        if gateway is None:
            return originator
        message = cot_to_message(raw, originator)
        if message is not None:
            # publish under the EUD's *own* originator (not this node's identity), so each ATAK
            # device is a distinct coalition peer and doesn't collide with the web node's presence.
            c = gateway.config
            header = MessageHeader(
                originator_id=originator,
                network_id=c.network.network_id,
                classification=c.classification.level,
                releasable_to=c.classification.releasable_to,
            )
            with contextlib.suppress(Exception):
                await gateway.engine.publish(JdssMessage(header=header, body=message.body))
        return originator

    async def _emit(self, message: JdssMessage) -> None:
        """JDSS -> CoT: stream a JDSS message to every connected EUD."""
        if not self._clients:
            return
        if message.header.originator_id == self._node_id:
            return  # never echo our own (incl. CoT-sourced) traffic back out
        cot = message_to_cot(message, stale_s=self._stale_s)
        if cot is None:
            return
        frame = cot if cot.endswith(b"\n") else cot + b"\n"
        for cid, (writer, _) in list(self._clients.items()):
            try:
                writer.write(frame)
                await writer.drain()
            except Exception:
                self._clients.pop(cid, None)
                with contextlib.suppress(Exception):
                    writer.close()

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        cfg = self._cfg
        return {
            "enabled": bool(cfg and cfg.enabled),
            "listening": self._server is not None,
            "host": cfg.host if cfg else "0.0.0.0",
            "port": cfg.port if cfg else 8087,
            "advertised_host": cfg.advertised_host if cfg else None,
            "clients": [peer for _, peer in self._clients.values()],
            "client_count": len(self._clients),
            "last_error": self.last_error,
            "lan_ip": _lan_ip(),  # best-effort; unreliable under Docker (shows the container IP)
        }


def _fmt_peer(peer: object) -> str:
    if isinstance(peer, tuple) and len(peer) >= 2:
        return f"{peer[0]}:{peer[1]}"
    return str(peer)


def _lan_ip() -> str:
    """Best-effort primary LAN IP (the address an EUD should point ATAK at)."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packets sent; just selects the outbound interface
        return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class _EudRelay:
    """Node handler that streams JDSS messages to connected EUDs (JDSS -> CoT)."""

    subscribes_to: Iterable[str] = ("*",)

    def __init__(self, manager: EudServerManager) -> None:
        self._manager = manager

    async def handle(self, message: JdssMessage) -> None:
        await self._manager._emit(message)
