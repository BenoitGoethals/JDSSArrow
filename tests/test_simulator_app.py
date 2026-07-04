"""The external PyQt6 simulator's engine (headless — no Qt/display needed)."""

from __future__ import annotations

import pytest
from simulator import geo
from simulator.engine import SimulatorEngine
from simulator.scenarios import SCENARIOS


def test_geo_helpers_are_sane():
    a, b = (50.0, 5.0), (50.0, 5.1)  # ~7.1 km east at this latitude
    d = geo.haversine(a, b)
    assert 6000 < d < 8000
    assert 80 < geo.bearing(a, b) < 100  # roughly due east
    moved = geo.dest_point(a, 90.0, 1000.0)  # 1 km east
    assert abs(geo.haversine(a, moved) - 1000.0) < 5.0


@pytest.mark.parametrize("key", list(SCENARIOS))
@pytest.mark.parametrize("secure", [True, False])
async def test_scenario_runs_secure_and_nonsecure(key, secure):
    events: list[dict] = []
    eng = SimulatorEngine(
        SCENARIOS[key],
        secure=secure,
        transport="loopback",
        network_id=f"sim-{key}-{int(secure)}",
        on_event=events.append,
        seed=3,
    )
    await eng.start()
    # every unit uses the matching security plugin
    assert all(
        u.gateway.config.plugins.security == ("psk" if secure else "null") for u in eng.units
    )
    starts = {u.spec.node_id: (u.lat, u.lon) for u in eng.units}
    try:
        for i in range(12):
            await eng.tick(1.0, i)
        # units advanced along their routes
        assert all((u.lat, u.lon) != starts[u.spec.node_id] for u in eng.units)
    finally:
        await eng.stop()

    types = {e["type"] for e in events if e["kind"] == "sent"}
    assert {"Presence", "Identification", "ContactSighting"} <= types
    assert eng.sent > 0
    assert eng.units == []  # cleaned up on stop


async def test_engine_receives_external_gateway_traffic():
    """A unit's inbound handler surfaces traffic from a separate node on the same network."""
    from jdssarrow.config.models import (
        GatewayConfig,
        GossipConfig,
        NetworkConfig,
        NodeIdentity,
        PluginSelection,
    )
    from jdssarrow.datamodel.messages import ChatMessage
    from jdssarrow.gateway.gateway import JdssGateway

    net = "sim-rx-net"
    events: list[dict] = []
    eng = SimulatorEngine(
        SCENARIOS["narvik"], secure=True, transport="loopback",
        network_id=net, on_event=events.append, seed=1,
    )
    await eng.start()
    outsider = JdssGateway(
        GatewayConfig(
            identity=NodeIdentity(node_id="gateway-x", callsign="GATEWAY"),
            plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
            network=NetworkConfig(network_id=net, psk="jdss-coalition-key", repeat=1),
            gossip=GossipConfig(enabled=False),
        )
    )
    await outsider.start()
    try:
        await outsider.publish(ChatMessage(text="gateway online"))
        import asyncio

        await asyncio.sleep(0.1)
        received = [e for e in events if e["kind"] == "received"]
        assert any(e["from"] == "gateway-x" for e in received)
    finally:
        await outsider.stop()
        await eng.stop()
