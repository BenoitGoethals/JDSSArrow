"""CRUD + live behaviour of CoT/TAK server connections managed from the Configuration tab."""

from __future__ import annotations

import asyncio

import pytest

from jdssarrow.config.models import ServerConnection
from jdssarrow.web.servers import ServerConnectionManager


class _MockTakServer:
    """Accepts a TCP connection and records the CoT the connector sends (like OTS's TCP port)."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.connected = asyncio.Event()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connected.set()
        while True:
            data = await reader.read(65536)
            if not data:
                break
            self.received.append(data)


async def _server():
    mock = _MockTakServer()
    srv = await asyncio.start_server(mock.handle, "127.0.0.1", 0)
    return mock, srv, srv.sockets[0].getsockname()[1]


def _def(sid: str, port: int, **kw) -> ServerConnection:
    return ServerConnection(id=sid, name=sid, host="127.0.0.1", port=port, **kw)


async def test_reconcile_starts_and_stops_connectors():
    mock, srv, port = await _server()
    mgr = ServerConnectionManager()
    async with srv:
        # enabling a server brings the connector up
        await mgr.reconcile([_def("ots", port)])
        await asyncio.wait_for(mock.connected.wait(), 2)
        status = {s["id"]: s for s in mgr.status()}
        assert status["ots"]["state"] == "connected" and status["ots"]["connected"] is True

        # disabling it tears the connector down (kept in status as 'disabled')
        await mgr.reconcile([_def("ots", port, enabled=False)])
        assert mgr.status()[0]["state"] == "disabled"
        assert not mgr.status()[0]["connected"]

        # removing it entirely drops it from status
        await mgr.reconcile([])
        assert mgr.status() == []
    await mgr.stop()


async def test_reconcile_replaces_on_connection_param_change():
    mock_a, srv_a, port_a = await _server()
    mock_b, srv_b, port_b = await _server()
    mgr = ServerConnectionManager()
    async with srv_a, srv_b:
        await mgr.reconcile([_def("s", port_a)])
        await asyncio.wait_for(mock_a.connected.wait(), 2)
        # change the port → connector is rebuilt against the new endpoint
        await mgr.reconcile([_def("s", port_b)])
        await asyncio.wait_for(mock_b.connected.wait(), 2)
        assert mgr.status()[0]["connected"]
        # stop connectors *before* the servers close: Python 3.12+ Server.wait_closed()
        # (run on `async with` exit) blocks until active connections finish.
        await mgr.stop()


async def test_jdss_to_cot_relay_reaches_server():
    """A JDSS message from another node is translated to CoT and sent to the connected server."""
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
    )
    from jdssarrow.gateway.gateway import JdssGateway
    from jdssarrow.gateway.node import SoldierNode

    mock, srv, port = await _server()
    cfg = GatewayConfig(
        identity=NodeIdentity(node_id="web-node", callsign="WEB"),
        plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
        network=NetworkConfig(network_id="srv-net", repeat=1, psk="k"),
        gossip=GossipConfig(enabled=False),
    )
    node = SoldierNode(JdssGateway(cfg))
    mgr = ServerConnectionManager()
    async with srv:
        await node.start()
        mgr.attach(node, node.gateway)
        await mgr.reconcile([_def("ots", port)])
        await asyncio.wait_for(mock.connected.wait(), 2)
        try:
            # a message from ANOTHER originator must be relayed out as CoT
            msg = JdssMessage(
                header=MessageHeader(originator_id="other-node"),
                body=ContactSighting(location=Location(lat=50.8, lon=4.3), description="enemy"),
            )
            await mgr._emit(msg)
            await asyncio.sleep(0.1)
            assert any(b"<event" in d for d in mock.received)
            # our OWN traffic is NOT echoed back out (loop protection)
            mock.received.clear()
            own = JdssMessage(
                header=MessageHeader(originator_id="web-node"),
                body=ContactSighting(location=Location(lat=1, lon=2)),
            )
            await mgr._emit(own)
            await asyncio.sleep(0.1)
            assert not mock.received
        finally:
            await mgr.stop()
            await node.stop()


async def test_test_probe_reports_ok_and_failure():
    mock, srv, port = await _server()
    mgr = ServerConnectionManager()
    async with srv:
        ok = await mgr.test(_def("ots", port), timeout=2)
        assert ok["ok"] is True
    # server is closed now → probe fails
    bad = await mgr.test(_def("ots", port), timeout=1)
    assert bad["ok"] is False


@pytest.mark.parametrize("bad_port", [0, 70000])
def test_server_connection_validates_port(bad_port):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ServerConnection(id="x", host="h", port=bad_port)


# --------------------------------------------------------------------------- PKCS#12 import
def _pki(tmp_path, client_cn: str, p12_password: str):
    """A CA, a server cert (SAN 127.0.0.1) written to disk, and a client .p12 (cert+key+CA)."""
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    nb = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    na = datetime.datetime(2100, 1, 1, tzinfo=datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "JDSS-CA")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(nb).not_valid_after(na)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )

    def leaf(cn, san=None):
        k = ec.generate_private_key(ec.SECP256R1())
        b = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
            .issuer_name(ca_name).public_key(k.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(nb).not_valid_after(na)
            .add_extension(
                x509.KeyUsage(True, False, True, False, False, False, False, False, False), True
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
                ),
                False,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
            )
        )
        if san:
            b = b.add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(san))]), False
            )
        return k, b.sign(ca_key, hashes.SHA256())

    srv_key, srv_cert = leaf("127.0.0.1", san="127.0.0.1")
    cli_key, cli_cert = leaf(client_cn)
    server_cert = tmp_path / "srv.pem"
    server_key = tmp_path / "srv.key"
    server_cert.write_bytes(srv_cert.public_bytes(serialization.Encoding.PEM))
    server_key.write_bytes(
        srv_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    p12 = pkcs12.serialize_key_and_certificates(
        b"bridge", cli_key, cli_cert, [ca], BestAvailableEncryption(p12_password.encode())
    )
    return p12, str(server_cert), str(server_key)


def test_pkcs12_import_extracts_cert_key_ca_and_cn(tmp_path):
    from jdssarrow.security.pkcs12 import load_pkcs12

    p12, _, _ = _pki(tmp_path, "jdss-bridge", "atakatak")
    out = load_pkcs12(p12, "atakatak")
    assert out["common_name"] == "jdss-bridge"
    assert out["client_cert"].startswith("-----BEGIN CERTIFICATE-----")
    assert out["client_key"].startswith("-----BEGIN PRIVATE KEY-----")
    assert out["cacert"] and "CERTIFICATE" in out["cacert"]
    with pytest.raises(ValueError):
        load_pkcs12(p12, "wrong-password")


def test_pkcs12_endpoint_returns_pem_and_rejects_bad_password(tmp_path):
    import base64

    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    p12, _, _ = _pki(tmp_path, "jdss-bridge", "atakatak")
    b64 = base64.b64encode(p12).decode()
    with TestClient(create_app()) as c:
        r = c.post("/api/servers/pkcs12", json={"p12_base64": b64, "password": "atakatak"})
        assert r.status_code == 200 and r.json()["common_name"] == "jdss-bridge"
        bad = c.post("/api/servers/pkcs12", json={"p12_base64": b64, "password": "nope"})
        assert bad.status_code == 400
        junk = c.post("/api/servers/pkcs12", json={"p12_base64": "!!not base64!!", "password": ""})
        assert junk.status_code == 400


def _chain():
    """Return (ca_cert, client_key, client_cert) objects for building odd .p12 shapes."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    nb = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    na = datetime.datetime(2100, 1, 1, tzinfo=datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "JDSS-CA")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(nb).not_valid_after(na)
        .add_extension(x509.BasicConstraints(True, None), True).sign(ca_key, hashes.SHA256())
    )
    cli_key = ec.generate_private_key(ec.SECP256R1())
    cli = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "jdss-bridge")]))
        .issuer_name(ca_name).public_key(cli_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(nb).not_valid_after(na)
        .sign(ca_key, hashes.SHA256())
    )
    return ca, cli_key, cli


def test_pkcs12_truststore_without_key_rejected_clearly():
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12

    from jdssarrow.security.pkcs12 import load_pkcs12

    ca, _, _ = _chain()  # a CA-only / truststore bundle (no private key)
    p12 = pkcs12.serialize_key_and_certificates(
        None, None, None, [ca], BestAvailableEncryption(b"pw")
    )
    with pytest.raises(ValueError, match="truststore"):
        load_pkcs12(p12, "pw")


def test_pkcs12_recovers_client_cert_from_additional_bag():
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12

    from jdssarrow.security.pkcs12 import load_pkcs12

    ca, cli_key, cli = _chain()
    # client cert not linked to the key (cert=None) → it lands in the 'additional' bag
    p12 = pkcs12.serialize_key_and_certificates(
        b"x", cli_key, None, [cli, ca], BestAvailableEncryption(b"pw")
    )
    out = load_pkcs12(p12, "pw")
    assert out["common_name"] == "jdss-bridge"
    assert out["client_cert"].startswith("-----BEGIN CERTIFICATE-----")
    assert out["cacert"] and "CERTIFICATE" in out["cacert"]  # the CA remained as the chain


async def test_connects_over_tls_with_imported_pem(tmp_path):
    """A server whose cert/key/CA hold imported PEM content connects over mutual TLS (the manager
    materializes the PEM to temp files for the ssl module)."""
    import ssl

    from jdssarrow.security.pkcs12 import load_pkcs12

    p12, server_cert, server_key = _pki(tmp_path, "jdss-bridge", "atakatak")
    pem = load_pkcs12(p12, "atakatak")

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(server_cert, server_key)
    server_ctx.load_verify_locations(cadata=pem["cacert"])
    server_ctx.verify_mode = ssl.CERT_REQUIRED  # require the client cert (mutual TLS)

    mock = _MockTakServer()
    srv = await asyncio.start_server(mock.handle, "127.0.0.1", 0, ssl=server_ctx)
    port = srv.sockets[0].getsockname()[1]

    mgr = ServerConnectionManager()
    server = ServerConnection(
        id="ots", host="127.0.0.1", port=port, tls=True, verify=True,
        client_cert=pem["client_cert"], client_key=pem["client_key"], cacert=pem["cacert"],
    )
    async with srv:
        await mgr.reconcile([server])
        await asyncio.wait_for(mock.connected.wait(), 3)
        assert mgr.status()[0]["connected"] is True  # mutual TLS with imported certs
        await mgr.stop()  # stop before the server closes (Python 3.12+ wait_closed blocks)
