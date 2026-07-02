"""Device types in the simulator + the per-message-type capability matrix."""

import asyncio

import pytest
import yaml

from jdssarrow.capabilities import CapabilityError, CapabilityMatrix
from jdssarrow.config.models import (
    CapabilitiesConfig,
    GatewayConfig,
    GossipConfig,
    NetworkConfig,
    NodeIdentity,
    PluginSelection,
)
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode
from jdssarrow.simulator.scenario import DEFAULT_ROSTER, Simulation


# --------------------------------------------------------------------- devices
async def test_simulator_includes_atak_and_device_types():
    sim = Simulation(transport="loopback", codec="xml")
    report = await sim.run(ticks=8, tick_interval=0.0)
    devices = {c["device"] for c in report.clients}
    # varied device classes, incl. an ATAK end-user device
    assert "atak" in devices
    assert {"eud", "uav", "c2_workstation", "vehicle"}.issubset(devices)
    assert any(c["role"] == "atak_operator" for c in report.clients)


def test_default_roster_has_atak_and_vehicle():
    names = [r for r, _ in DEFAULT_ROSTER]
    assert "atak" in names and "vehicle" in names


# ---------------------------------------------------------------- capabilities
def test_capability_matrix_defaults_and_toggle():
    m = CapabilityMatrix()
    assert m.can_receive("Presence") and m.can_emit("Chat")
    m.set("Chat", "emit", False)
    assert m.can_emit("Chat") is False
    m.set("Presence", "receive", False)
    assert m.can_receive("Presence") is False
    snap = m.snapshot()
    assert snap["emit"]["Chat"] is False and snap["receive"]["Presence"] is False
    assert "CasevacRequest" in snap["types"]


class _Collector:
    subscribes_to = ("*",)

    def __init__(self):
        self.types: list[str] = []

    async def handle(self, message):
        self.types.append(message.type)


def _gw(node_id, receive=None):
    return JdssGateway(
        GatewayConfig(
            identity=NodeIdentity(node_id=node_id, callsign=node_id.upper()),
            plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
            network=NetworkConfig(network_id="cap-test", repeat=1, psk="k"),
            capabilities=CapabilitiesConfig(receive=receive or {}),
            gossip=GossipConfig(enabled=False),
        )
    )


async def test_receive_capability_drops_disallowed_type():
    gw_a = _gw("node-a", receive={"ContactSighting": False})  # refuse contacts
    gw_b = _gw("node-b")
    na, nb = SoldierNode(gw_a), SoldierNode(gw_b)
    rx = _Collector()
    na.add_handler(rx)
    await na.start()
    await nb.start()
    try:
        await nb.presence(1, 1)
        await nb.report_contact(1, 1, "enemy")
        await asyncio.sleep(0.05)
        assert "Presence" in rx.types
        assert "ContactSighting" not in rx.types  # dropped by capability matrix
        assert gw_a.metrics.drops().get("capability", 0) >= 1
    finally:
        await na.stop()
        await nb.stop()


async def test_emit_capability_blocks_send():
    gw = JdssGateway(
        GatewayConfig(
            identity=NodeIdentity(node_id="node-a"),
            plugins=PluginSelection(transport="loopback", codec="xml", security="null"),
            network=NetworkConfig(network_id="cap-emit", psk="k"),
            capabilities=CapabilitiesConfig(emit={"Chat": False}),
            gossip=GossipConfig(enabled=False),
        )
    )
    node = SoldierNode(gw)
    await node.start()
    try:
        with pytest.raises(CapabilityError):
            await node.chat("should be blocked")
        # a permitted type still works
        await node.presence(1, 1)
    finally:
        await node.stop()


def test_web_capabilities_matrix(tmp_path):
    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    cfg = tmp_path / "web.yaml"
    cfg.write_text(yaml.safe_dump({"plugins": {"transport": "loopback", "security": "null"}}))
    with TestClient(create_app(str(cfg))) as client:
        snap = client.get("/api/capabilities").json()
        assert "Presence" in snap["types"]
        assert snap["emit"]["Chat"] is True

        r = client.post("/api/capabilities/Chat?direction=emit&allowed=false").json()
        assert r["emit"]["Chat"] is False
        assert r["persisted"] is True  # written back to the config file
        # emitting a disabled type is refused
        assert client.post("/api/publish/chat", json={"text": "x"}).status_code == 403
        assert client.post("/api/capabilities/Chat?direction=bogus&allowed=true").status_code == 400

    # the toggle was written to disk...
    saved = yaml.safe_load(cfg.read_text())
    assert saved["capabilities"]["emit"]["Chat"] is False
    # ...and a fresh app started from that file honours it
    with TestClient(create_app(str(cfg))) as client2:
        assert client2.get("/api/capabilities").json()["emit"]["Chat"] is False


def test_capabilities_not_persisted_without_config_file():
    import os

    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    os.environ["JDSS_PLUGINS__TRANSPORT"] = "loopback"
    os.environ["JDSS_PLUGINS__SECURITY"] = "null"
    try:
        with TestClient(create_app()) as client:  # no config file
            r = client.post("/api/capabilities/Chat?direction=emit&allowed=false").json()
            assert r["persisted"] is False  # nothing to persist to
            assert r["emit"]["Chat"] is False  # still applied live
    finally:
        del os.environ["JDSS_PLUGINS__TRANSPORT"]
        del os.environ["JDSS_PLUGINS__SECURITY"]