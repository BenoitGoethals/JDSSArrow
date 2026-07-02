"""ATAK/CoT ↔ JDSS bridge: translation both ways + end-to-end relay over loopback."""

import asyncio

from jdssarrow.bridges.atak import AtakBridge
from jdssarrow.bridges.cot import BRIDGE_MARKER, cot_to_message, message_to_cot
from jdssarrow.config.models import (
    GatewayConfig,
    GossipConfig,
    NetworkConfig,
    NodeIdentity,
    PluginSelection,
)
from jdssarrow.datamodel.messages import (
    CasevacRequest,
    ChatMessage,
    ContactSighting,
    JdssMessage,
    Location,
    MessageHeader,
    Presence,
)
from jdssarrow.datamodel.symbology import StandardIdentity
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode
from jdssarrow.iem.transport_loopback import LoopbackTransport

FRIENDLY_COT = (
    b'<event version="2.0" uid="ATAK-1" type="a-f-G-U-C" how="m-g" '
    b'time="2026-01-01T00:00:00Z" start="2026-01-01T00:00:00Z" stale="2099-01-01T00:05:00Z">'
    b'<point lat="50.85" lon="4.35" hae="0" ce="9" le="9"/>'
    b'<detail><contact callsign="ALPHA-1"/></detail></event>'
)
HOSTILE_COT = FRIENDLY_COT.replace(b"a-f-G-U-C", b"a-h-G").replace(b"ATAK-1", b"ATAK-2")
CHAT_COT = (
    b'<event version="2.0" uid="GeoChat.1" type="b-t-f" time="2026-01-01T00:00:00Z" '
    b'start="2026-01-01T00:00:00Z" stale="2099-01-01T00:05:00Z">'
    b'<point lat="0" lon="0" hae="0" ce="9" le="9"/>'
    b"<detail><remarks>contact front</remarks></detail></event>"
)


# --------------------------------------------------------------- CoT → JDSS
def test_cot_to_message_types():
    assert cot_to_message(FRIENDLY_COT, "br").type == "Presence"
    contact = cot_to_message(HOSTILE_COT, "br")
    assert contact.type == "ContactSighting"
    assert contact.body.identity == StandardIdentity.HOSTILE
    chat = cot_to_message(CHAT_COT, "br")
    assert chat.type == "Chat" and chat.body.text == "contact front"
    assert cot_to_message(b"not xml", "br") is None


# --------------------------------------------------------------- JDSS → CoT
def _msg(body):
    return JdssMessage(header=MessageHeader(originator_id="node-x"), body=body)


def test_message_to_cot_types_and_marker():
    pres = message_to_cot(_msg(Presence(location=Location(lat=1, lon=2), callsign="A")))
    assert b"a-f-G-U-C" in pres and BRIDGE_MARKER.encode() in pres
    hostile = message_to_cot(
        _msg(ContactSighting(location=Location(lat=1, lon=2), identity=StandardIdentity.HOSTILE))
    )
    assert b"a-h-G" in hostile
    casevac = message_to_cot(_msg(CasevacRequest(location=Location(lat=1, lon=2))))
    assert b"b-r-f-h-c" in casevac
    chat = message_to_cot(_msg(ChatMessage(text="hi")))
    assert b"b-t-f" in chat
    # Identification has no CoT representation
    from jdssarrow.datamodel.messages import Identification

    assert message_to_cot(_msg(Identification(callsign="A", unit="U"))) is None


def test_cot_roundtrip_contact():
    original = _msg(
        ContactSighting(location=Location(lat=50.1, lon=4.2), identity=StandardIdentity.HOSTILE)
    )
    back = cot_to_message(message_to_cot(original), "br")
    assert back.type == "ContactSighting"
    assert back.body.identity == StandardIdentity.HOSTILE
    assert abs(back.body.location.lat - 50.1) < 1e-6


# --------------------------------------------------------------- end-to-end
def _bridge_config():
    return GatewayConfig(
        identity=NodeIdentity(node_id="atak-bridge", callsign="ATAK-BR"),
        plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
        network=NetworkConfig(network_id="atak-net", repeat=1, psk="k"),
        gossip=GossipConfig(enabled=False),
    )


def _observer():
    return SoldierNode(
        JdssGateway(
            GatewayConfig(
                identity=NodeIdentity(node_id="obs", callsign="OBS"),
                plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
                network=NetworkConfig(network_id="atak-net", repeat=1, psk="k"),
                gossip=GossipConfig(enabled=False),
            )
        )
    )


class _Collector:
    subscribes_to = ("*",)

    def __init__(self):
        self.msgs = []

    async def handle(self, message):
        self.msgs.append(message)


async def test_cot_into_jdss_reaches_the_network():
    # a simulated ATAK sending CoT on a loopback "cot" bus that the bridge also joins
    atak = LoopbackTransport(group="cot")
    bridge = AtakBridge(_bridge_config(), cot_transport=LoopbackTransport(group="cot"))
    observer = _observer()
    rx = _Collector()
    observer.add_handler(rx)

    await observer.start()
    await bridge.start()
    await atak.start()
    try:
        await atak.send(HOSTILE_COT)  # ATAK reports a hostile contact
        await asyncio.sleep(0.05)
        got = [m for m in rx.msgs if m.type == "ContactSighting"]
        assert got, "bridge did not relay CoT into JDSS"
        assert got[0].header.originator_id == "atak-bridge"  # re-originated by the bridge
        assert bridge.stats["cot_in"] >= 1 and bridge.stats["jdss_out"] >= 1
    finally:
        await atak.stop()
        await bridge.stop()
        await observer.stop()


async def test_jdss_into_cot_reaches_atak():
    atak = LoopbackTransport(group="cot")
    atak_rx: list[bytes] = []
    atak.on_receive(lambda raw: _append(atak_rx, raw))
    bridge = AtakBridge(_bridge_config(), cot_transport=LoopbackTransport(group="cot"))
    observer = _observer()

    await observer.start()
    await bridge.start()
    await atak.start()
    try:
        await observer.report_contact(50.9, 4.4, "enemy squad")  # JDSS traffic
        await asyncio.sleep(0.05)
        assert any(b"event" in raw and b"a-h-G" in raw for raw in atak_rx), "no CoT reached ATAK"
        assert bridge.stats["cot_out"] >= 1
    finally:
        await atak.stop()
        await bridge.stop()
        await observer.stop()


async def _append(sink: list, raw: bytes) -> None:
    sink.append(raw)
