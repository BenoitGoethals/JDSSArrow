"""Built-in ATAK/EUD TAK server: ATAK connects directly to this node (CoT <-> JDSS)."""

from __future__ import annotations

import asyncio
import socket

from jdssarrow.config.models import (
    EudServerConfig,
    GatewayConfig,
    GossipConfig,
    NetworkConfig,
    NodeIdentity,
    PluginSelection,
)
from jdssarrow.datamodel.messages import (
    ContactSighting,
    JdssMessage,
    Location,
    MessageHeader,
)
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode
from jdssarrow.web.eud_server import EudServerManager

# a friendly self-SA, like ATAK's periodic position report
ATAK_SA = (
    b'<event version="2.0" uid="ATAK-123" type="a-f-G-U-C">'
    b'<point lat="50.9" lon="4.4"/><detail><contact callsign="FOX-1"/></detail></event>'
)


def _cfg(node_id: str, callsign: str) -> GatewayConfig:
    return GatewayConfig(
        identity=NodeIdentity(node_id=node_id, callsign=callsign),
        plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
        network=NetworkConfig(network_id="eud-net", repeat=1, psk="k"),
        gossip=GossipConfig(enabled=False),
    )


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Rx:
    subscribes_to = ("*",)

    def __init__(self) -> None:
        self.msgs: list = []

    async def handle(self, m) -> None:
        self.msgs.append(m)


async def test_reconfigure_toggles_listener():
    node = SoldierNode(JdssGateway(_cfg("web", "WEB")))
    await node.start()
    mgr = EudServerManager()
    mgr.attach(node, node.gateway)
    try:
        port = _free_port()
        await mgr.reconfigure(EudServerConfig(enabled=True, host="127.0.0.1", port=port))
        st = mgr.status()
        assert st["listening"] is True and st["enabled"] is True and st["port"] == port
        assert st["lan_ip"]  # the address to point ATAK at

        await mgr.reconfigure(EudServerConfig(enabled=False))
        assert mgr.status()["listening"] is False
    finally:
        await mgr.stop()
        await node.stop()


async def test_atak_cot_published_as_distinct_peer():
    """An EUD's CoT is published onto the JDSS net under its own per-device originator."""
    web = SoldierNode(JdssGateway(_cfg("web", "WEB")))
    obs = SoldierNode(JdssGateway(_cfg("obs", "OBS")))  # a separate coalition node
    await web.start()
    await obs.start()
    rx = _Rx()
    obs.add_handler(rx)
    mgr = EudServerManager()
    mgr.attach(web, web.gateway)
    port = _free_port()
    await mgr.reconfigure(EudServerConfig(enabled=True, host="127.0.0.1", port=port))
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(ATAK_SA)
        await writer.drain()
        await asyncio.sleep(0.3)
        # the observer sees a Presence from the EUD, NOT from the web node
        assert any(m.type == "Presence" and m.header.originator_id == "atak-ATAK-123"
                   for m in rx.msgs)
        assert not any(m.header.originator_id == "web" for m in rx.msgs)
        assert mgr.status()["client_count"] == 1
    finally:
        writer.close()
        await mgr.stop()
        await web.stop()
        await obs.stop()


async def test_jdss_streams_to_connected_eud():
    """A coalition message from another node is streamed to a connected EUD as CoT."""
    web = SoldierNode(JdssGateway(_cfg("web", "WEB")))
    await web.start()
    mgr = EudServerManager()
    mgr.attach(web, web.gateway)
    port = _free_port()
    await mgr.reconfigure(EudServerConfig(enabled=True, host="127.0.0.1", port=port))
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        await asyncio.sleep(0.05)
        await mgr._emit(JdssMessage(
            header=MessageHeader(originator_id="scout-1"),
            body=ContactSighting(location=Location(lat=50.8, lon=4.3), description="enemy"),
        ))
        data = await asyncio.wait_for(reader.read(4096), 2)
        assert b"<event" in data and b"</event>" in data  # got CoT

        # our OWN node's traffic is never echoed to EUDs (loop protection)
        drained = b""
        await mgr._emit(JdssMessage(
            header=MessageHeader(originator_id="web"),
            body=ContactSighting(location=Location(lat=1, lon=2)),
        ))
        try:
            drained = await asyncio.wait_for(reader.read(4096), 0.3)
        except TimeoutError:
            pass
        assert drained == b""
    finally:
        writer.close()
        await mgr.stop()
        await web.stop()


async def test_eud_router_get_and_put():
    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    with TestClient(create_app()) as c:
        assert c.get("/api/eud").json()["enabled"] is False  # off by default
        port = _free_port()
        r = c.put("/api/eud", json={"enabled": True, "host": "127.0.0.1", "port": port})
        body = r.json()
        assert r.status_code == 200 and body["listening"] is True and body["port"] == port
        assert body["lan_ip"]
        # an advertised host (for Docker/NAT) round-trips and persists into status
        adv = c.put(
            "/api/eud",
            json={
                "enabled": True, "host": "127.0.0.1", "port": port,
                "advertised_host": "203.0.113.7",
            },
        ).json()
        assert adv["advertised_host"] == "203.0.113.7"
        assert c.put("/api/eud", json={"enabled": False}).json()["listening"] is False
