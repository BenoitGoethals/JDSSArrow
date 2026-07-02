"""End-to-end: two gateways (loopback transport) exchange Presence + CASEVAC."""

import asyncio

from jdssarrow.config.models import (
    GatewayConfig,
    NetworkConfig,
    NodeIdentity,
    PluginSelection,
)
from jdssarrow.datamodel.messages import JdssMessage
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode


class _Collector:
    subscribes_to = ("*",)

    def __init__(self):
        self.by_type: dict[str, list[JdssMessage]] = {}

    async def handle(self, message: JdssMessage) -> None:
        self.by_type.setdefault(message.type, []).append(message)


def _config(node_id: str, callsign: str) -> GatewayConfig:
    return GatewayConfig(
        identity=NodeIdentity(node_id=node_id, callsign=callsign),
        # loopback transport keeps the test off the network; every other volume is real.
        plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
        network=NetworkConfig(network_id="coalition-test", repeat=2, psk="k"),
    )


async def test_presence_and_casevac_roundtrip():
    gw_a = JdssGateway(_config("node-a", "ALFA-1"))
    gw_b = JdssGateway(_config("node-b", "BRAVO-2"))
    node_a, node_b = SoldierNode(gw_a), SoldierNode(gw_b)
    rx_b = _Collector()
    node_b.add_handler(rx_b)

    # Both derive the same multicast group from the shared network_id (Vol V).
    assert gw_a.endpoint == gw_b.endpoint

    await node_a.start()
    await node_b.start()
    try:
        await node_a.presence(50.85, 4.35, battery_pct=90)
        await node_a.request_casevac(50.86, 4.36, urgent=2)
        await asyncio.sleep(0.05)

        assert len(rx_b.by_type.get("Presence", [])) == 1
        casevacs = rx_b.by_type.get("CasevacRequest", [])
        assert len(casevacs) == 1
        assert casevacs[0].body.patients_urgent == 2
        # Vol I: the message authenticated (psk) and Vol II decoded via XML codec.
        assert "node-a" in gw_b.metrics.nodes()
    finally:
        await node_a.stop()
        await node_b.stop()


async def test_psk_mismatch_drops_messages():
    gw_a = JdssGateway(_config("node-a", "ALFA-1"))
    cfg_b = _config("node-b", "BRAVO-2")
    cfg_b.network.psk = "different-key"  # wrong coalition key
    gw_b = JdssGateway(cfg_b)
    node_a, node_b = SoldierNode(gw_a), SoldierNode(gw_b)
    rx_b = _Collector()
    node_b.add_handler(rx_b)

    await node_a.start()
    await node_b.start()
    try:
        await node_a.presence(50.85, 4.35)
        await asyncio.sleep(0.05)
        assert rx_b.by_type == {}  # HMAC verify fails → dropped, not delivered
    finally:
        await node_a.stop()
        await node_b.stop()
