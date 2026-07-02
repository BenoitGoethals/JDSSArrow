"""Classification propagation, peer tracking, health, and runtime config apply."""

import yaml

from jdssarrow.datamodel.codec.arrow_codec import ArrowCodec
from jdssarrow.datamodel.codec.xml_mip import XmlMipCodec
from jdssarrow.datamodel.messages import (
    Identification,
    JdssMessage,
    Location,
    MessageHeader,
    Presence,
)
from jdssarrow.monitor.metrics import GatewayMetrics


def _msg(body, classification=0, releasable_to="ALL", originator="node-x"):
    return JdssMessage(
        header=MessageHeader(
            originator_id=originator, classification=classification, releasable_to=releasable_to
        ),
        body=body,
    )


def test_classification_roundtrips_through_xml_and_arrow():
    msg = _msg(
        Presence(location=Location(lat=1, lon=2), callsign="ALFA-1"),
        classification=2,
        releasable_to="REL BEL NLD",
    )
    for codec in (XmlMipCodec(), ArrowCodec()):
        restored = codec.decode(codec.encode(msg))
        assert restored.header.classification == 2
        assert restored.header.releasable_to == "REL BEL NLD"


def test_metrics_learns_peer_identity_and_classification():
    m = GatewayMetrics("me")
    ident = _msg(
        Identification(callsign="BRAVO-2", unit="2PL", nation="NLD", role="medic"),
        classification=3,
        originator="node-b",
    )
    m.node_seen("node-b")
    m.record_received(ident)

    peers = m.peers(timeout_s=60)
    assert len(peers) == 1
    peer = peers[0]
    assert peer["callsign"] == "BRAVO-2"
    assert peer["nation"] == "NLD"
    assert peer["role"] == "medic"
    assert peer["classification"] == 3
    assert peer["connected"] is True
    assert m.snapshot()["max_classification_seen"] == 3


def test_peer_goes_stale_past_timeout():
    m = GatewayMetrics("me")
    m.record_received(_msg(Presence(location=Location(lat=0, lon=0), callsign="C"), originator="n"))
    # timeout_s=0 → everything is immediately "stale"/disconnected
    assert m.peers(timeout_s=0)[0]["connected"] is False


def _web_client(tmp_path):
    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    cfg = tmp_path / "web.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "plugins": {"transport": "loopback", "security": "null"},
                "network": {"repeat": 2},
                "classification": {"level": 1, "releasable_to": "REL BEL NLD"},
            }
        )
    )
    return TestClient(create_app(str(cfg))), cfg


def test_health_and_peers_endpoints(tmp_path):
    client, _ = _web_client(tmp_path)
    with client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["engine_running"] is True
        assert health["threads"] >= 1
        assert health["classification"] == 1
        assert client.get("/api/monitor/peers").json() == []  # no peers on isolated loopback
        classes = client.get("/api/classifications").json()
        assert classes["3"] == "NATO SECRET"


def test_runtime_config_apply_and_persist(tmp_path):
    client, cfg = _web_client(tmp_path)
    with client:
        # change reliability repeat and raise classification at runtime
        resp = client.put(
            "/api/config",
            json={"network": {"repeat": 5}, "classification": {"level": 3}},
        )
        assert resp.status_code == 200
        applied = resp.json()
        assert applied["network"]["repeat"] == 5
        assert applied["classification"]["level"] == 3

        # GET reflects the hot-restarted gateway, and health shows the new repeat
        assert client.get("/api/config").json()["network"]["repeat"] == 5
        assert client.get("/api/health").json()["repeat"] == 5

        # persisted to the config file
        saved = yaml.safe_load(cfg.read_text())
        assert saved["network"]["repeat"] == 5
        assert saved["classification"]["level"] == 3


def test_live_matrix_endpoint(tmp_path):
    client, _ = _web_client(tmp_path)
    with client:
        # live matrix (gossip): a single web node knows at least its own row
        m = client.get("/api/monitor/matrix").json()
        assert "node-a" in m["nodes"]
        assert "node-a" in m["rows"]


def test_matrix_probe_endpoint_shows_rogue_rejected(tmp_path):
    client, _ = _web_client(tmp_path)
    with client:
        mr = client.get("/api/monitor/matrix/probe?ticks=8&rogue=garbage").json()
        rogue = mr["rogue_node"]
        assert rogue is not None
        for observer, row in mr["rows"].items():
            if observer != rogue:
                assert row.get(rogue, 0) == 0


def test_invalid_config_apply_is_rejected_and_node_stays_up(tmp_path):
    client, _ = _web_client(tmp_path)
    with client:
        resp = client.put("/api/config", json={"classification": {"level": 9}})  # out of range
        assert resp.status_code == 422
        # node still serving on the previous good config
        assert client.get("/api/health").json()["status"] == "ok"
