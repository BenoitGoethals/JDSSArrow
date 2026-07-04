"""Interoperability with OpenTAKServer (OTS).

These tests do not require a running OTS. Instead they reproduce OTS's *actual* server-side
acceptance logic (from opentakserver/eud_handler/EudHandler.py and EudHandlerSSL.py) and drive
the real :class:`TakServerConnector` / :class:`AtakBridge` against it, proving wire + auth
compatibility:

* Framing - OTS splits the byte stream with ``re.split("</event>|</auth>", buffer)`` and
  validates each fragment with ``xml.etree.ElementTree.fromstring``. Our emitted CoT must survive
  exactly that.
* TCP (unencrypted) port - ``handle_cot`` only gates on ``is_ssl and not is_authenticated``, so a
  plain-TCP client is anonymous: CoT is accepted with no auth. This is the simplest OTS path.
* SSL port - a client whose certificate CommonName matches a registered OTS user is authenticated
  by cert alone (``EudHandlerSSL.setup`` -> ``handle_auth("")`` -> "ID'ed by cert"); no
  username/password ``<auth>`` message is needed. An unknown CN is never authenticated and its CoT
  is ignored.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import ssl
from xml.etree.ElementTree import ParseError, fromstring

from jdssarrow.bridges.atak import AtakBridge
from jdssarrow.bridges.cot import message_to_cot
from jdssarrow.bridges.takserver import TakServerConnector
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
    Overlay,
    OverlayGraphic,
    Presence,
)
from jdssarrow.datamodel.symbology import sidc
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode


# --------------------------------------------------------------------------- OTS mock
class OtsMock:
    """A minimal re-implementation of OpenTAKServer's EUD stream handling.

    Mirrors EudHandler.handle()/handle_cot() and EudHandlerSSL.setup(): frame with
    re.split on the closing tags, ElementTree-validate each fragment, and (for SSL) require the
    peer-cert CommonName to name a known user before relaying any CoT.
    """

    def __init__(self, *, is_ssl: bool, users: set[str] | None = None) -> None:
        self.is_ssl = is_ssl
        self.users = users or set()
        self.accepted: list[str] = []  # CoT the server accepted (== would relay)
        self.ignored: list[str] = []  # CoT dropped because the client wasn't authenticated
        self.common_name: str | None = None
        self.connected = asyncio.Event()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        is_authenticated = not self.is_ssl  # TCP == anonymous; SSL must authenticate

        if self.is_ssl:  # EudHandlerSSL.setup: identify the user from the client cert CN
            sslobj = writer.get_extra_info("ssl_object")
            cert = sslobj.getpeercert() if sslobj else None
            self.common_name = _common_name(cert)
            if self.common_name in self.users:  # handle_auth(""): "ID'ed by cert"
                is_authenticated = True

        # OTS pushes CoT to the client; we send one so the connector's read path is exercised.
        writer.write(b'<event version="2.0" type="a-h-G" uid="ots-track">'
                     b'<point lat="1" lon="2"/><detail><remarks>ots</remarks></detail></event>')
        await writer.drain()
        self.connected.set()

        buf = ""
        while True:
            try:
                data = await reader.read(65536)
            except (ConnectionResetError, ssl.SSLError):
                break
            if not data:
                break
            buf += data.decode("utf-8")
            parts = re.split("</event>|</auth>", buf)  # OTS's exact framing
            if len(parts) < 2:
                continue
            for c in parts:
                try:
                    if "<event" in c:
                        fromstring(c + "</event>")  # OTS validates with ElementTree
                        if self.is_ssl and not is_authenticated:
                            self.ignored.append(c + "</event>")  # "isn't authenticated, ignoring"
                            continue
                        self.accepted.append(c + "</event>")
                    elif "<auth>" in c:
                        fromstring(c + "</auth>")
                except ParseError:
                    pass
            buf = ""


async def _collect(sink: list, raw: bytes) -> None:
    sink.append(raw)


def _common_name(cert: dict | None) -> str | None:
    if not cert:
        return None
    for rdn in cert.get("subject", ()):  # (( ('commonName', 'x'), ), ...)
        for key, val in rdn:
            if key == "commonName":
                return val
    return None


# --------------------------------------------------------------------------- helpers
def _certs(tmp_path):
    """A CA, a server cert (SAN 127.0.0.1) and a client cert (CN=jdss-bridge) for mutual TLS."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    def _key():
        return ec.generate_private_key(ec.SECP256R1())

    def _write(path, data):
        path.write_bytes(data)
        return str(path)

    not_before = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    not_after = datetime.datetime(2100, 1, 1, tzinfo=datetime.UTC)

    ca_key = _key()
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "JDSS-Test-CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before).not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )

    def _leaf(cn, san=None):
        import ipaddress

        k = _key()
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
            .issuer_name(ca_name)
            .public_key(k.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before).not_valid_after(not_after)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=True,
                    data_encipherment=False, key_agreement=False, key_cert_sign=False,
                    crl_sign=False, encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
                ),
                critical=False,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
            )
        )
        if san:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(san))]),
                critical=False,
            )
        cert = builder.sign(ca_key, hashes.SHA256())
        return (
            cert.public_bytes(serialization.Encoding.PEM),
            k.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ),
        )

    ca_pem = _write(tmp_path / "ca.pem", ca.public_bytes(serialization.Encoding.PEM))
    srv_cert, srv_key = _leaf("127.0.0.1", san="127.0.0.1")
    cli_cert, cli_key = _leaf("jdss-bridge")
    return {
        "ca": ca_pem,
        "server_cert": _write(tmp_path / "srv.pem", srv_cert),
        "server_key": _write(tmp_path / "srv.key", srv_key),
        "client_cert": _write(tmp_path / "cli.pem", cli_cert),
        "client_key": _write(tmp_path / "cli.key", cli_key),
    }


def _cfg(node_id: str, callsign: str):
    return GatewayConfig(
        identity=NodeIdentity(node_id=node_id, callsign=callsign),
        plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
        network=NetworkConfig(network_id="ots-net", repeat=1, psk="k"),
        gossip=GossipConfig(enabled=False),
    )


def _bridge_cfg():
    return _cfg("atak-bridge", "BR")


# --------------------------------------------------------------------------- tests
def test_every_cot_type_survives_ots_framing_and_parser():
    """Each CoT we emit must split cleanly on OTS's re.split and parse as valid XML."""
    msgs = [
        JdssMessage(header=MessageHeader(originator_id="n"),
                    body=Presence(location=Location(lat=1, lon=2), callsign="A")),
        JdssMessage(header=MessageHeader(originator_id="n"),
                    body=ContactSighting(location=Location(lat=1, lon=2), description="x")),
        JdssMessage(header=MessageHeader(originator_id="n"),
                    body=CasevacRequest(location=Location(lat=1, lon=2), patients_urgent=1)),
        JdssMessage(header=MessageHeader(originator_id="n"),
                    body=Overlay(name="ov", graphics=[OverlayGraphic(
                        sidc=sidc("control_point"), location=Location(lat=1, lon=2), label="l")])),
        JdssMessage(header=MessageHeader(originator_id="n"), body=ChatMessage(text="hi")),
    ]
    # concatenate them on the wire exactly as the connector would stream them
    stream = b"".join(message_to_cot(m) + b"\n" for m in msgs).decode()
    parts = [c for c in re.split("</event>|</auth>", stream) if "<event" in c]
    assert len(parts) == len(msgs)
    for c in parts:
        el = fromstring(c + "</event>")  # OTS would reject on ParseError; this must not raise
        assert el.tag == "event"
        assert el.find("point") is not None  # OTS needs a point to geolocate the track


async def test_ots_tcp_anonymous_accepts_bridge(tmp_path):
    """OTS TCP port (default 8088): anonymous, so the bridge relays with no auth."""
    ots = OtsMock(is_ssl=False)
    server = await asyncio.start_server(ots.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    observer = SoldierNode(JdssGateway(_cfg("obs", "OBS")))  # distinct id, not the bridge's own
    bridge = AtakBridge(_bridge_cfg(), cot_transport=TakServerConnector("127.0.0.1", port))
    got = []

    class _Rx:
        subscribes_to = ("*",)

        async def handle(self, m):
            got.append(m)

    observer.add_handler(_Rx())

    async with server:
        await observer.start()
        await bridge.start()
        try:
            await asyncio.wait_for(ots.connected.wait(), 2)
            await asyncio.sleep(0.1)
            # OTS -> JDSS: the server's track was relayed into the JDSS net
            assert any(m.type == "ContactSighting" for m in got)
            # JDSS -> OTS: our CoT is accepted by OTS's parser+framing (no auth on TCP)
            await observer.report_contact(50.8, 4.3, "enemy patrol")
            await asyncio.sleep(0.15)
            assert ots.accepted, "OTS accepted no CoT from the bridge"
            assert all(fromstring(c).tag == "event" for c in ots.accepted)
        finally:
            await bridge.stop()
            await observer.stop()


async def test_ots_ssl_cert_cn_authenticates(tmp_path):
    """OTS SSL port (default 8089): a client cert whose CN is a known user authenticates by cert
    alone - no username/password <auth> needed - and CoT then flows both ways."""
    c = _certs(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(c["server_cert"], c["server_key"])
    server_ctx.load_verify_locations(c["ca"])
    server_ctx.verify_mode = ssl.CERT_REQUIRED  # request + require the client cert (mutual TLS)

    ots = OtsMock(is_ssl=True, users={"jdss-bridge"})  # CN registered as an OTS user
    server = await asyncio.start_server(ots.handle, "127.0.0.1", 0, ssl=server_ctx)
    port = server.sockets[0].getsockname()[1]

    conn = TakServerConnector(
        "127.0.0.1", port, tls=True,
        client_cert=c["client_cert"], client_key=c["client_key"], cacert=c["ca"], verify=True,
    )
    got: list[bytes] = []
    conn.on_receive(lambda raw: _collect(got, raw))  # handler must be a coroutine
    async with server:
        await conn.start()
        try:
            assert conn.connected is True  # mutual-TLS handshake succeeded
            await asyncio.wait_for(ots.connected.wait(), 2)
            await asyncio.sleep(0.05)
            assert ots.common_name == "jdss-bridge"  # OTS read our cert CN
            assert any(b"ots-track" in e for e in got)  # received OTS's pushed CoT over TLS
            await conn.send(message_to_cot(JdssMessage(
                header=MessageHeader(originator_id="n"),
                body=Presence(location=Location(lat=3, lon=4), callsign="X"))))
            await asyncio.sleep(0.15)
            assert ots.accepted and not ots.ignored  # authenticated -> relayed, nothing dropped
        finally:
            await conn.stop()


async def test_ots_ssl_unknown_cert_is_not_relayed(tmp_path):
    """A client cert whose CN is not a registered OTS user connects but is never authenticated,
    so OTS ignores its CoT (the auth boundary that keeps unauthorised EUDs off the network)."""
    c = _certs(tmp_path)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(c["server_cert"], c["server_key"])
    server_ctx.load_verify_locations(c["ca"])
    server_ctx.verify_mode = ssl.CERT_REQUIRED

    ots = OtsMock(is_ssl=True, users=set())  # CN "jdss-bridge" is NOT a known user
    server = await asyncio.start_server(ots.handle, "127.0.0.1", 0, ssl=server_ctx)
    port = server.sockets[0].getsockname()[1]

    conn = TakServerConnector(
        "127.0.0.1", port, tls=True,
        client_cert=c["client_cert"], client_key=c["client_key"], cacert=c["ca"], verify=True,
    )
    conn.on_receive(lambda raw: _collect([], raw))
    async with server:
        await conn.start()
        try:
            await asyncio.wait_for(ots.connected.wait(), 2)
            await conn.send(message_to_cot(JdssMessage(
                header=MessageHeader(originator_id="n"),
                body=Presence(location=Location(lat=3, lon=4), callsign="X"))))
            await asyncio.sleep(0.15)
            assert ots.ignored and not ots.accepted  # unauthenticated -> dropped by OTS
        finally:
            with contextlib.suppress(Exception):
                await conn.stop()
