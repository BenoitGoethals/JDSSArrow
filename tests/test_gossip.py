"""Peer-digest gossip builds a live cross-node matrix; control frames stay out of the COP."""

import asyncio

from jdssarrow.config.models import (
    GatewayConfig,
    GossipConfig,
    NetworkConfig,
    NodeIdentity,
    PluginSelection,
)
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode


def _gw(node_id: str, psk: str = "k", network: str = "gossip-test") -> JdssGateway:
    return JdssGateway(
        GatewayConfig(
            identity=NodeIdentity(node_id=node_id, callsign=node_id.upper()),
            plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
            network=NetworkConfig(network_id=network, repeat=1, psk=psk),
            gossip=GossipConfig(enabled=True, interval_s=0.02),
        )
    )


async def _exchange_and_settle(nodes: list[SoldierNode], rounds: int = 5) -> None:
    for _ in range(rounds):
        for n in nodes:
            await n.presence(50.0, 4.0)
        await asyncio.sleep(0.03)
    await asyncio.sleep(0.15)  # let gossip digests propagate


async def test_gossip_assembles_full_live_matrix():
    gws = [_gw("node-a"), _gw("node-b"), _gw("node-c")]
    nodes = [SoldierNode(g) for g in gws]
    for n in nodes:
        await n.start()
    try:
        await _exchange_and_settle(nodes)
        # every gateway, from its own vantage point, has learned all three rows via gossip
        for g in gws:
            m = g.connection_matrix()
            assert {"node-a", "node-b", "node-c"}.issubset(set(m["nodes"]))
            assert set(m["rows"]) == {"node-a", "node-b", "node-c"}
            # a remote row it learned (not its own) is populated with that node's peers
            me = g.config.identity.node_id
            others = [nid for nid in ("node-a", "node-b", "node-c") if nid != me]
            for other in others:
                assert other in m["rows"]
                assert len(m["rows"][other]) >= 1
    finally:
        for n in nodes:
            await n.stop()


async def test_control_frames_do_not_enter_the_operational_picture():
    gws = [_gw("node-a"), _gw("node-b")]
    nodes = [SoldierNode(g) for g in gws]
    for n in nodes:
        await n.start()
    try:
        await _exchange_and_settle(nodes)
        # telemetry/counts only ever contain real JDSSDM message types, never gossip
        for g in gws:
            counts = g.metrics.snapshot()["counts_by_type"]
            assert set(counts).issubset({"Presence", "Identification"})
            assert "peerdigest" not in counts and "__ctl__" not in counts
    finally:
        for n in nodes:
            await n.stop()


async def test_unauthorised_node_digest_is_rejected_from_matrix():
    legit = [_gw("node-a"), _gw("node-b")]
    rogue = _gw("rogue-x", psk="wrong-key")  # different coalition key
    nodes = [SoldierNode(g) for g in (*legit, rogue)]
    for n in nodes:
        await n.start()
    try:
        await _exchange_and_settle(nodes)
        # the rogue gossips too, but its digests fail HMAC → never learned by legit nodes
        for g in legit:
            assert "rogue-x" not in g.connection_matrix()["nodes"]
    finally:
        for n in nodes:
            await n.stop()
