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
        mode="multicast",
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


def test_inject_payload_maps_bodies():
    from simulator.engine import LiveUnit

    from jdssarrow.datamodel import symbology
    from jdssarrow.datamodel.messages import ChatMessage, ContactSighting, Location, Presence

    eng = SimulatorEngine(SCENARIOS["narvik"], mode="inject", on_event=lambda e: None)
    spec = SCENARIOS["narvik"].units[0]
    u = LiveUnit(spec=spec, gateway=None, lat=1.0, lon=2.0, idx=0)

    p = eng._inject_payload(u, Presence(location=Location(lat=1, lon=2), callsign="X",
                                        course_deg=90, speed_mps=3))
    assert p["type"] == "Presence" and p["originator"] == spec.node_id
    assert p["lat"] == 1 and p["course_deg"] == 90
    c = eng._inject_payload(u, ContactSighting(location=Location(lat=1, lon=2),
                                               identity=symbology.StandardIdentity.HOSTILE,
                                               description="tgt"))
    assert c["type"] == "ContactSighting" and c["identity"] == 6 and c["description"] == "tgt"
    ch = eng._inject_payload(u, ChatMessage(text="hi"))
    assert ch["type"] == "Chat" and ch["text"] == "hi"


def test_inject_endpoint_fans_out_as_distinct_peer_counted_once():
    """POST /api/inject makes the injector a distinct coalition peer, counted once (as received)."""
    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    with TestClient(create_app()) as c:
        r = c.post("/api/inject", json={
            "originator": "u1", "type": "Presence", "callsign": "UNIT-1",
            "lat": 50.8, "lon": 4.3, "course_deg": 90, "speed_mps": 2,
        })
        assert r.status_code == 200 and r.json()["originator"] == "u1"
        peers = {p["node_id"]: p for p in c.get("/api/monitor/peers").json()}
        assert "u1" in peers and peers["u1"]["callsign"] == "UNIT-1"
        # exactly one Presence (the injected one), i.e. not double-counted as sent+received
        assert c.get("/api/monitor/snapshot").json()["counts_by_type"].get("Presence") == 1
        assert c.get("/api/logs/messages").json()["counts"]["in"] >= 1  # recorded as received
        assert c.post("/api/inject", json={"originator": "u1", "type": "Nope"}).status_code == 400


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
        SCENARIOS["narvik"], mode="multicast", secure=True, transport="loopback",
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
