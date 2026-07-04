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
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

from jdssarrow.bridges.cot import (
    BRIDGE_MARKER,
    cot_is_stale,
    cot_to_message,
    message_to_cot,
)
from jdssarrow.config.models import EudServerConfig
from jdssarrow.datamodel.messages import Identification, JdssMessage, MessageHeader
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode
from jdssarrow.web.cotlog import CotTrafficLog

_EVENT_END = b"</event>"
_EVENT_START = b"<event"


class EudServerManager:
    def __init__(self, *, stale_s: int = 120) -> None:
        self._stale_s = stale_s
        self._cfg: EudServerConfig | None = None
        self._server: asyncio.Server | None = None
        self._clients: dict[int, tuple[asyncio.StreamWriter, str]] = {}  # id -> (writer, peer)
        self._origins: dict[int, set[str]] = {}  # id -> originators this client has sourced
        self.traffic = CotTrafficLog()  # inspectable log of CoT to/from connected EUDs
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
        self._origins.clear()
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
        self._origins[cid] = set()
        # per-connection state: provisional originator until we learn the device's self-SA uid,
        # and the set of originators we've already announced an Identification for.
        state = {"originator": f"atak-{cid}", "identified": set()}
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
                    await self._ingest(doc, writer, cid, state)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.pop(cid, None)
            self._origins.pop(cid, None)
            with contextlib.suppress(Exception):
                writer.close()

    async def _ingest(
        self, raw: bytes, writer: asyncio.StreamWriter, cid: int, state: dict
    ) -> None:
        """CoT -> JDSS for one inbound event."""
        if BRIDGE_MARKER.encode() in raw:
            return  # our own emitted CoT echoed back
        peer = self._clients.get(cid, (None, f"eud-{cid}"))[1]
        self.traffic.record(peer=peer, direction="in", raw=raw)  # visible in the dashboard
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return
        ctype = root.get("type", "")
        if ctype == "t-x-c-t":
            await self._pong(writer)  # ATAK keepalive ping → reply so the link stays healthy
            return
        if ctype.startswith("t-x") or ctype.startswith("t-b"):
            return  # other TAK control (protocol negotiation, delete, bits) — not mappable
        if cot_is_stale(raw):
            return
        gateway = self._gateway
        if gateway is None:
            return
        # adopt the device's stable uid from its self-SA so it's one coalition peer, not many
        uid = root.get("uid")
        if uid and ctype.startswith("a-f"):
            state["originator"] = f"atak-{uid}"
        originator = state["originator"]
        message = cot_to_message(raw, originator)
        if message is None:
            return
        self._origins[cid].add(originator)

        # announce an Identification the first time we learn this EUD's callsign, so it shows up as
        # a named peer in the dashboard/matrix (Presence alone gives a callsign but no role/unit).
        callsign = getattr(message.body, "callsign", None)
        if callsign and originator not in state["identified"]:
            state["identified"].add(originator)
            ident = Identification(callsign=callsign, unit="ATAK EUD", role="atak", nation="")
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

    async def _pong(self, writer: asyncio.StreamWriter) -> None:
        now = datetime.now(UTC)
        iso, stale = now.isoformat(), (now + timedelta(seconds=20)).isoformat()
        pong = (
            f'<event version="2.0" uid="takPong" type="t-x-c-t-r" how="m-g" '
            f'time="{iso}" start="{iso}" stale="{stale}">'
            f'<point lat="0" lon="0" hae="0" ce="9999999" le="9999999"/></event>\n'
        ).encode()
        with contextlib.suppress(Exception):
            writer.write(pong)
            await writer.drain()

    async def _emit(self, message: JdssMessage) -> None:
        """JDSS -> CoT: stream a JDSS message to every connected EUD (except its source)."""
        if not self._clients:
            return
        if message.header.originator_id == self._node_id:
            return  # never emit our own node's originated traffic
        cot = message_to_cot(message, stale_s=self._stale_s)
        if cot is None:
            return
        frame = cot if cot.endswith(b"\n") else cot + b"\n"
        src = message.header.originator_id
        for cid, (writer, peer) in list(self._clients.items()):
            if src in self._origins.get(cid, ()):
                continue  # don't echo an EUD's own track back to it
            self.traffic.record(peer=peer, direction="out", raw=cot)
            try:
                writer.write(frame)
                await writer.drain()
            except Exception:
                self._clients.pop(cid, None)
                self._origins.pop(cid, None)
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
