"""Connection-management policy: the matrix that governs (and enforces) who a node accepts."""

import asyncio

import yaml

from jdssarrow.config.models import (
    ConnectionsConfig,
    GatewayConfig,
    GossipConfig,
    NetworkConfig,
    NodeIdentity,
    PluginSelection,
)
from jdssarrow.connections.policy import AllowAllPolicy, MatrixConnectionPolicy
from jdssarrow.datamodel.messages import JdssMessage, Location, MessageHeader, Presence
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode
from jdssarrow.simulator.scenario import Simulation


def _msg(originator: str) -> JdssMessage:
    return JdssMessage(
        header=MessageHeader(originator_id=originator),
        body=Presence(location=Location(lat=0, lon=0), callsign="X"),
    )


def test_matrix_policy_default_and_overrides():
    p = MatrixConnectionPolicy(node_id="me", default_action="allow")
    assert p.allows(_msg("node-b")) is True
    p.block("node-b")
    assert p.allows(_msg("node-b")) is False
    assert p.blocked_peers() == ["node-b"]
    p.reset("node-b")
    assert p.allows(_msg("node-b")) is True

    deny = MatrixConnectionPolicy(default_action="deny")
    assert deny.allows(_msg("node-b")) is False
    deny.allow("node-b")
    assert deny.allows(_msg("node-b")) is True
    assert AllowAllPolicy().allows(_msg("anyone")) is True


class _Collector:
    subscribes_to = ("*",)

    def __init__(self):
        self.seen: list[JdssMessage] = []

    async def handle(self, message):
        self.seen.append(message)


def _gw(node_id, blocked=None):
    return JdssGateway(
        GatewayConfig(
            identity=NodeIdentity(node_id=node_id, callsign=node_id.upper()),
            plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
            network=NetworkConfig(network_id="conn-test", repeat=1, psk="k"),
            connections=ConnectionsConfig(blocked=blocked or []),
            gossip=GossipConfig(enabled=False),
        )
    )


async def test_blocked_peer_messages_are_dropped_on_ingest():
    # node-a blocks node-b from the start; node-c is unaffected.
    gw_a = _gw("node-a", blocked=["node-b"])
    gw_b, gw_c = _gw("node-b"), _gw("node-c")
    na, nb, nc = SoldierNode(gw_a), SoldierNode(gw_b), SoldierNode(gw_c)
    rx_a = _Collector()
    na.add_handler(rx_a)
    for n in (na, nb, nc):
        await n.start()
    try:
        await nb.presence(1, 1)  # from blocked peer
        await nc.presence(2, 2)  # from allowed peer
        await asyncio.sleep(0.05)
        origins = {m.header.originator_id for m in rx_a.seen}
        assert "node-b" not in origins  # blocked
        assert "node-c" in origins  # allowed
        assert gw_a.metrics.drops().get("policy", 0) >= 1
        assert "node-b" not in [p["node_id"] for p in gw_a.peers()]
    finally:
        for n in (na, nb, nc):
            await n.stop()


async def test_runtime_block_then_allow_changes_delivery():
    gw_a, gw_b = _gw("node-a"), _gw("node-b")
    na, nb = SoldierNode(gw_a), SoldierNode(gw_b)
    rx = _Collector()
    na.add_handler(rx)
    await na.start()
    await nb.start()
    try:
        await nb.presence(1, 1)
        await asyncio.sleep(0.03)
        assert any(m.header.originator_id == "node-b" for m in rx.seen)

        gw_a.block_peer("node-b")  # manage the connection at runtime
        rx.seen.clear()
        await nb.presence(2, 2)
        await asyncio.sleep(0.03)
        assert rx.seen == []  # now dropped

        gw_a.allow_peer("node-b")  # re-open
        await nb.presence(3, 3)
        await asyncio.sleep(0.03)
        assert any(m.header.originator_id == "node-b" for m in rx.seen)
    finally:
        await na.stop()
        await nb.stop()


async def test_simulator_block_shows_in_matrix():
    # command post refuses the UAV sensor; other nodes still hear it.
    sim = Simulation(transport="loopback", codec="xml", blocks={"commandpost-1": ["sensor-1"]})
    report = await sim.run(ticks=12, tick_interval=0.0)
    # CP's own row has no sensor-1 (blocked) ...
    assert report.matrix["commandpost-1"].get("sensor-1", 0) == 0
    # ... but a non-blocking node did hear the sensor
    assert report.matrix["rifleman-1"].get("sensor-1", 0) > 0
    assert "sensor-1" not in report.peers_at_command_post


def test_web_connection_management(tmp_path):
    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    cfg = tmp_path / "web.yaml"
    cfg.write_text(yaml.safe_dump({"plugins": {"transport": "loopback", "security": "null"}}))
    with TestClient(create_app(str(cfg))) as client:
        base = client.get("/api/connections").json()
        assert base["policy"]["default_action"] == "allow"

        blocked = client.post("/api/connections/node-z?action=block").json()
        assert blocked["overrides"]["node-z"] == "block"
        # reflected in the live matrix policy overlay
        assert client.get("/api/monitor/matrix").json()["policy"]["overrides"]["node-z"] == "block"

        client.post("/api/connections/node-z?action=reset")
        assert "node-z" not in client.get("/api/connections").json()["policy"]["overrides"]
        assert client.post("/api/connections/node-z?action=bogus").status_code == 400
