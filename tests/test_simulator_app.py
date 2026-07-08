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


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_expand_scenario_to_500_operators(key):
    from simulator.scenarios import expand_scenario

    base = SCENARIOS[key]
    big = expand_scenario(base, 500)
    assert len(big.units) == 500
    ids = [u.node_id for u in big.units]
    assert len(set(ids)) == 500  # unique ids/callsigns
    # roles/nations/behaviours are cycled from the base roster, each has a real patrol loop
    assert {u.role for u in big.units} == {u.role for u in base.units}
    assert all(len(u.route) >= 3 for u in big.units)
    # small counts are left untouched (no point cloning below the base size)
    assert expand_scenario(base, 3) is base


async def test_stress_run_drives_500_operators_headless():
    """A 500-operator stress run (multicast/loopback) advances every track and floods traffic."""
    stats: list[dict] = []
    eng = SimulatorEngine(
        SCENARIOS["eben_emael"],
        mode="multicast",
        secure=False,  # null security keeps 500 local nodes light
        transport="loopback",
        network_id="sim-stress",
        on_event=lambda e: stats.append(e) if e.get("kind") == "stats" else None,
        stress=500,
        concurrency=128,
        seed=7,
    )
    assert eng.stress == 500
    assert len(eng.scenario.units) == 500
    await eng.start()
    try:
        assert len(eng.units) == 500
        starts = {u.spec.node_id: (u.lat, u.lon) for u in eng.units}
        await eng.tick(1.0, 0)
        assert all((u.lat, u.lon) != starts[u.spec.node_id] for u in eng.units)
        assert eng.sent >= 500  # at least one presence per operator this tick
        last = stats[-1]
        assert last["nodes"] == 500 and last["stress"] == 500 and "rate" in last
    finally:
        await eng.stop()
    assert eng.units == []


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


def test_inject_payload_maps_extended_types():
    """GenInfo / Receipt / Chatrooms flatten to fields the /api/inject schema understands."""
    from simulator.engine import LiveUnit

    from jdssarrow.datamodel.messages import ChatRoom, Chatrooms, GeneralInfo, Location, Receipt

    eng = SimulatorEngine(SCENARIOS["narvik"], mode="inject", on_event=lambda e: None)
    u = LiveUnit(spec=SCENARIOS["narvik"].units[0], gateway=None, lat=1.0, lon=2.0, idx=0)

    g = eng._inject_payload(u, GeneralInfo(subject="SITREP", text="all clear",
                                           location=Location(lat=5, lon=6)))
    assert g["type"] == "GenInfo" and g["description"] == "SITREP" and g["text"] == "all clear"
    assert g["lat"] == 5

    r = eng._inject_payload(u, Receipt(ack_message_id="abc", status="received", note="ok"))
    assert r["type"] == "Receipt" and r["ack_message_id"] == "abc" and r["description"] == "ok"

    cr = eng._inject_payload(u, Chatrooms(rooms=[ChatRoom(room_id="a", name="Alpha"),
                                                 ChatRoom(room_id="b", name="Bravo")]))
    assert cr["type"] == "Chatrooms" and cr["text"] == "Alpha,Bravo"


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
        # bi-directional: the sim auto-acknowledged the external message with a Receipt
        sent_receipts = [e for e in events if e["kind"] == "sent" and e["type"] == "Receipt"]
        assert sent_receipts, "no Receipt sent back for received coalition traffic"
        assert eng.acked >= 1
    finally:
        await outsider.stop()
        await eng.stop()


async def test_engine_does_not_ack_when_auto_ack_disabled():
    import asyncio

    from jdssarrow.config.models import (
        GatewayConfig,
        GossipConfig,
        NetworkConfig,
        NodeIdentity,
        PluginSelection,
    )
    from jdssarrow.datamodel.messages import ChatMessage
    from jdssarrow.gateway.gateway import JdssGateway

    net = "sim-noack-net"
    events: list[dict] = []
    eng = SimulatorEngine(
        SCENARIOS["narvik"], mode="multicast", secure=False, transport="loopback",
        network_id=net, on_event=events.append, seed=1, auto_ack=False,
    )
    await eng.start()
    outsider = JdssGateway(
        GatewayConfig(
            identity=NodeIdentity(node_id="gw-y", callsign="GW"),
            plugins=PluginSelection(transport="loopback", codec="xml", security="null"),
            network=NetworkConfig(network_id=net, psk="jdss-coalition-key", repeat=1),
            gossip=GossipConfig(enabled=False),
        )
    )
    await outsider.start()
    try:
        await outsider.publish(ChatMessage(text="ping"))
        await asyncio.sleep(0.1)
        rx = [e for e in events if e["kind"] == "received" and e["from"] == "gw-y"]
        assert rx  # still receives coalition traffic
        assert not [e for e in events if e["kind"] == "sent" and e["type"] == "Receipt"]  # no ack
    finally:
        await outsider.stop()
        await eng.stop()


async def test_scenario_emits_extended_types_headless():
    """A scenario run originates GenInfo (SITREP) and Chatrooms (room enumeration)."""
    events: list[dict] = []
    eng = SimulatorEngine(
        SCENARIOS["eben_emael"], mode="multicast", secure=False, transport="loopback",
        network_id="sim-ext-types", on_event=events.append, seed=2,
    )
    await eng.start()
    try:
        for i in range(10):
            await eng.tick(1.0, i)
        sent_types = {e["type"] for e in events if e["kind"] == "sent"}
        assert "GenInfo" in sent_types
        assert "Chatrooms" in sent_types
    finally:
        await eng.stop()
