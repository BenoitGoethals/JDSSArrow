"""Stale-track handling + TAK Server (TCP) connector."""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from jdssarrow.bridges.atak import AtakBridge
from jdssarrow.bridges.cot import cot_delete, cot_is_stale, message_to_cot
from jdssarrow.bridges.takserver import TakServerConnector
from jdssarrow.config.models import (
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
    Presence,
)
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode
from jdssarrow.iem.transport_loopback import LoopbackTransport


# ------------------------------------------------------------------ stale bits
def test_presence_has_stable_uid_and_stale_window():
    m = JdssMessage(
        header=MessageHeader(originator_id="node-x"),
        body=Presence(location=Location(lat=1, lon=2), callsign="A"),
    )
    cot = message_to_cot(m, stale_s=60)
    assert b'uid="JDSS.node-x"' in cot  # stable per-node track
    # a contact is a discrete event → per-message uid, not the node track
    c = JdssMessage(
        header=MessageHeader(originator_id="node-x"),
        body=ContactSighting(location=Location(lat=1, lon=2)),
    )
    assert b'uid="JDSS.node-x.' in message_to_cot(c)  # per-message suffix


def test_cot_is_stale_and_delete():
    past = datetime(2000, 1, 1, tzinfo=UTC)
    fresh = (
        b'<event stale="'
        + (datetime.now(UTC) + timedelta(hours=1)).isoformat().encode()
        + b'" type="a-f-G"><point lat="0" lon="0"/></event>'
    )
    stale = (
        b'<event stale="' + past.isoformat().encode() + b'" type="a-f-G">'
        b'<point lat="0" lon="0"/></event>'
    )
    assert cot_is_stale(stale) is True
    assert cot_is_stale(fresh) is False
    assert cot_is_stale(b"garbage") is False
    assert b"t-x-d-d" in cot_delete("JDSS.node-x")


# --------------------------------------------------------------- bridge sweep
def _bridge_cfg():
    return GatewayConfig(
        identity=NodeIdentity(node_id="atak-bridge", callsign="BR"),
        plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
        network=NetworkConfig(network_id="stale-net", repeat=1, psk="k"),
        gossip=GossipConfig(enabled=False),
    )


async def test_bridge_drops_stale_inbound_cot():
    bridge = AtakBridge(_bridge_cfg(), cot_transport=LoopbackTransport(group="c1"))
    atak = LoopbackTransport(group="c1")
    await bridge.start()
    await atak.start()
    try:
        old = datetime(2000, 1, 1, tzinfo=UTC).isoformat().encode()
        await atak.send(
            b'<event type="a-h-G" stale="' + old + b'"><point lat="1" lon="2"/></event>'
        )
        await asyncio.sleep(0.05)
        assert bridge.stats["stale_dropped"] >= 1
        assert bridge.stats["cot_in"] == 0  # never relayed
    finally:
        await atak.stop()
        await bridge.stop()


async def test_bridge_sweep_emits_delete_for_silent_track():
    sent: list[bytes] = []
    cot = LoopbackTransport(group="c2")
    atak = LoopbackTransport(group="c2")
    atak.on_receive(lambda raw: _collect(sent, raw))
    bridge = AtakBridge(_bridge_cfg(), cot_transport=cot, cot_stale_s=5)
    await bridge.start()
    await atak.start()
    try:
        # pretend we relayed a Presence for node-z a while ago
        bridge._last_seen["node-z"] = 0.0
        await bridge._sweep_once(now=10_000.0)  # far in the future → node-z is stale
        await asyncio.sleep(0.05)
        assert any(b"t-x-d-d" in raw and b"JDSS.node-z" in raw for raw in sent)
        assert bridge.stats["cot_deleted"] >= 1
        assert "node-z" not in bridge._last_seen
    finally:
        await atak.stop()
        await bridge.stop()


async def _collect(sink: list, raw: bytes) -> None:
    sink.append(raw)


# ------------------------------------------------------------ TAK Server (TCP)
async def test_tak_server_connector_send_receive():
    received_by_server: list[bytes] = []
    ready = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # push a CoT event to the client, then read whatever it sends back
        writer.write(b'<event type="a-h-G" uid="srv-1"><point lat="1" lon="2"/></event>')
        await writer.drain()
        ready.set()
        data = await reader.read(65536)
        received_by_server.append(data)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    got: list[bytes] = []
    conn = TakServerConnector("127.0.0.1", port)
    conn.on_receive(lambda raw: _collect(got, raw))
    async with server:
        await conn.start()
        try:
            await asyncio.wait_for(ready.wait(), timeout=2)
            await asyncio.sleep(0.05)
            assert any(b"srv-1" in e for e in got)  # received the server's event, framed
            await conn.send(b'<event type="a-f-G" uid="cli-1"><point lat="3" lon="4"/></event>')
            await asyncio.sleep(0.1)
            assert any(b"cli-1" in d for d in received_by_server)
        finally:
            await conn.stop()


async def test_tak_connector_reconnects_after_drop():
    writers: list[asyncio.StreamWriter] = []

    async def handle(reader, writer):
        writers.append(writer)
        writer.write(b'<event uid="s"><point lat="0" lon="0"/></event>')
        await writer.drain()
        with contextlib.suppress(Exception):
            await reader.read(65536)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    conn = TakServerConnector("127.0.0.1", port, base_backoff=0.05, max_backoff=0.2)
    conn.on_receive(lambda raw: _collect([], raw))
    await conn.start()
    try:
        await conn.wait_connected(2)
        assert conn.connects == 1

        # wait for the server side to accept before we drop it
        for _ in range(60):
            if writers:
                break
            await asyncio.sleep(0.02)
        # drop the current link; the server keeps listening, so the supervisor reconnects to it
        writers[0].close()
        for _ in range(60):
            if conn.connects >= 2:
                break
            await asyncio.sleep(0.05)
        assert conn.connects >= 2 and conn.reconnects >= 1
        assert conn.connected is True  # back up on its own
    finally:
        await conn.stop()  # close the client before tearing down the server
        server.close()


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def test_outbound_queue_flushes_on_reconnect():
    port = _free_port()  # nothing listening yet
    conn = TakServerConnector(
        "127.0.0.1", port, base_backoff=0.05, connect_timeout=0.3, queue_max=100
    )
    conn.on_receive(lambda raw: _collect([], raw))
    await conn.start()
    try:
        assert conn.connected is False
        # sent while down → buffered, not lost
        await conn.send(b'<event uid="f1"><point lat="0" lon="0"/></event>')
        await conn.send(b'<event uid="f2"><point lat="0" lon="0"/></event>')
        assert conn.queued == 2

        received: list[bytes] = []

        async def handle(reader, writer):
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                received.append(data)

        server = await asyncio.start_server(handle, "127.0.0.1", port)
        try:
            await conn.wait_connected(3)  # supervisor reconnects...
            await asyncio.sleep(0.2)
            joined = b"".join(received)
            assert b"f1" in joined and b"f2" in joined  # ...and flushes the buffer, in order
            assert joined.find(b"f1") < joined.find(b"f2")
            assert conn.queued == 0
        finally:
            await conn.stop()
            server.close()
    finally:
        if conn.connected:
            await conn.stop()


async def test_outbound_queue_is_bounded():
    port = _free_port()  # never comes up
    conn = TakServerConnector(
        "127.0.0.1", port, base_backoff=0.05, connect_timeout=0.2, queue_max=3
    )
    conn.on_receive(lambda raw: _collect([], raw))
    await conn.start()
    try:
        for i in range(10):
            await conn.send(f"<event uid='e{i}'/>".encode())
        assert conn.queued == 3  # bounded
        assert conn.dropped >= 7  # oldest evicted
    finally:
        await conn.stop()


def _self_signed(tmp_path):
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC))
        .not_valid_after(datetime.datetime(2100, 1, 1, tzinfo=datetime.UTC))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


async def test_tak_connector_tls_roundtrip(tmp_path):
    import ssl

    cert, key = _self_signed(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)

    server_rx: list[bytes] = []
    ready = asyncio.Event()

    async def handle(reader, writer):
        writer.write(b'<event type="a-h-G" uid="tls-1"><point lat="1" lon="2"/></event>')
        await writer.drain()
        ready.set()
        server_rx.append(await reader.read(65536))

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_ctx)
    port = server.sockets[0].getsockname()[1]

    got: list[bytes] = []
    # verify=True with the self-signed cert as the trusted CA (mutual-verify TLS)
    conn = TakServerConnector("127.0.0.1", port, tls=True, cacert=cert, verify=True)
    conn.on_receive(lambda raw: _collect(got, raw))
    async with server:
        await conn.start()
        try:
            assert conn.connected is True  # TLS handshake + cert verification succeeded
            await asyncio.wait_for(ready.wait(), 2)
            await asyncio.sleep(0.05)
            assert any(b"tls-1" in e for e in got)  # received over TLS
            await conn.send(b'<event uid="cli-tls"><point lat="3" lon="4"/></event>')
            await asyncio.sleep(0.1)
            assert any(b"cli-tls" in d for d in server_rx)  # sent over TLS
        finally:
            await conn.stop()


async def test_bridge_over_tak_server():
    """Full bridge using a TAK Server TCP connector against a mock server."""
    from_server_to_client = (
        b'<event type="a-h-G" uid="srv-track"><point lat="50.9" lon="4.4"/>'
        b"<detail><remarks>server track</remarks></detail></event>"
    )
    server_rx: list[bytes] = []
    client_connected = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(from_server_to_client)
        await writer.drain()
        client_connected.set()
        while True:
            data = await reader.read(65536)
            if not data:
                break
            server_rx.append(data)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    observer = SoldierNode(
        JdssGateway(
            GatewayConfig(
                identity=NodeIdentity(node_id="obs", callsign="OBS"),
                plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
                network=NetworkConfig(network_id="stale-net", repeat=1, psk="k"),
                gossip=GossipConfig(enabled=False),
            )
        )
    )

    class _Rx:
        subscribes_to = ("*",)

        def __init__(self):
            self.msgs = []

        async def handle(self, m):
            self.msgs.append(m)

    rx = _Rx()
    observer.add_handler(rx)
    bridge = AtakBridge(_bridge_cfg(), cot_transport=TakServerConnector("127.0.0.1", port))

    async with server:
        await observer.start()
        await bridge.start()
        try:
            await asyncio.wait_for(client_connected.wait(), timeout=2)
            await asyncio.sleep(0.1)
            # server's CoT track was relayed into JDSS
            assert any(m.type == "ContactSighting" for m in rx.msgs)
            # JDSS traffic is relayed out to the TAK Server as CoT
            await observer.report_contact(50.8, 4.3, "enemy")
            await asyncio.sleep(0.1)
            assert any(b"event" in d for d in server_rx)
        finally:
            await bridge.stop()
            await observer.stop()
