"""ATAK ↔ JDSS bridge.

Runs a JDSS node and, in parallel, joins ATAK's Cursor-on-Target multicast group. It relays:

* **CoT → JDSS**: inbound CoT events are translated and re-published onto the JDSS network
  under the bridge's identity, so ATAK tracks/markers/chat appear on the coalition COP.
* **JDSS → CoT**: JDSS messages from *other* nodes are translated to CoT and emitted to ATAK,
  so the operator sees coalition traffic on their EUD.

Loop protection: the bridge never re-emits its own JDSS messages to CoT, and ignores inbound
CoT carrying the ``__jdssbridge`` marker (its own multicast echoes).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Iterable

from jdssarrow.bridges.cot import (
    BRIDGE_MARKER,
    cot_delete,
    cot_is_stale,
    cot_to_message,
    message_to_cot,
)
from jdssarrow.config.models import GatewayConfig
from jdssarrow.datamodel.messages import JdssMessage, MessageType
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode
from jdssarrow.interfaces import Transport
from jdssarrow.plugins.registry import Registry

DEFAULT_COT_GROUP = "239.2.3.1"  # ATAK default mesh SA multicast group
DEFAULT_COT_PORT = 6969


class AtakBridge:
    def __init__(
        self,
        config: GatewayConfig,
        cot_transport: Transport | None = None,
        cot_group: str = DEFAULT_COT_GROUP,
        cot_port: int = DEFAULT_COT_PORT,
        cot_stale_s: int = 60,
        drop_stale_inbound: bool = True,
        registry: Registry | None = None,
    ) -> None:
        self.gateway = JdssGateway(config, registry)
        self.node = SoldierNode(self.gateway)
        self._node_id = config.identity.node_id
        self._stale_s = max(5, cot_stale_s)
        self._drop_stale = drop_stale_inbound
        if cot_transport is None:
            # imported lazily so unit tests can inject a loopback transport without sockets
            from jdssarrow.iem.transport_udp import UdpMulticastTransport

            cot_transport = UdpMulticastTransport(group=cot_group, port=cot_port)
        self._cot = cot_transport
        #: originator -> last time we saw a Presence (drives the stale-track delete sweep)
        self._last_seen: dict[str, float] = {}
        self._sweep_task: asyncio.Task | None = None
        self.stats = {"cot_in": 0, "jdss_out": 0, "jdss_in": 0, "cot_out": 0, "stale_dropped": 0,
                      "cot_deleted": 0}

    async def start(self) -> None:
        self.node.add_handler(_JdssToCot(self))
        await self.node.start()
        await self.node.identify()
        self._cot.on_receive(self._on_cot)
        await self._cot.start()
        self._sweep_task = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweep_task
            self._sweep_task = None
        await self._cot.stop()
        await self.node.stop()

    # ---------------------------------------------------------------- CoT → JDSS
    async def _on_cot(self, raw: bytes) -> None:
        if BRIDGE_MARKER.encode() in raw:
            return  # our own emitted CoT echoed back by the multicast group
        if self._drop_stale and cot_is_stale(raw):
            self.stats["stale_dropped"] += 1  # don't inject an already-expired track
            return
        message = cot_to_message(raw, self._node_id)
        if message is None:
            return
        self.stats["cot_in"] += 1
        await self.gateway.publish(message.body)  # re-originate under the bridge's identity
        self.stats["jdss_out"] += 1

    # ---------------------------------------------------------------- JDSS → CoT
    async def _emit_cot(self, message: JdssMessage) -> None:
        if message.header.originator_id == self._node_id:
            return  # don't echo our own (CoT-sourced) traffic back to ATAK
        if message.type == MessageType.PRESENCE:
            self._last_seen[message.header.originator_id] = time.time()
        cot = message_to_cot(message, stale_s=self._stale_s)
        if cot is None:
            return
        self.stats["jdss_in"] += 1
        await self._cot.send(cot)
        self.stats["cot_out"] += 1

    # ------------------------------------------------------------ stale sweep
    async def _sweep_once(self, now: float) -> None:
        """Emit a CoT delete for any node whose Presence hasn't refreshed within the window."""
        dead = [o for o, ts in self._last_seen.items() if now - ts > self._stale_s]
        for originator in dead:
            await self._cot.send(cot_delete(f"JDSS.{originator}"))
            self._last_seen.pop(originator, None)
            self.stats["cot_deleted"] += 1

    async def _sweep_loop(self) -> None:
        interval = max(2.0, self._stale_s / 2)
        while True:
            await asyncio.sleep(interval)
            with contextlib.suppress(Exception):
                await self._sweep_once(time.time())


class _JdssToCot:
    subscribes_to: Iterable[str] = ("*",)

    def __init__(self, bridge: AtakBridge) -> None:
        self._bridge = bridge

    async def handle(self, message: JdssMessage) -> None:
        await self._bridge._emit_cot(message)
