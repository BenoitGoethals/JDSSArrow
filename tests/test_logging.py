"""Application log + message audit log (incoming/outgoing, accepted/rejected + reason)."""

import asyncio
import logging

import yaml

from jdssarrow.audit import app_log, setup_logging
from jdssarrow.config.models import (
    CapabilitiesConfig,
    ConnectionsConfig,
    GatewayConfig,
    GossipConfig,
    NetworkConfig,
    NodeIdentity,
    PluginSelection,
)
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode


def _gw(node_id, *, psk="k", capabilities=None, blocked=None):
    return JdssGateway(
        GatewayConfig(
            identity=NodeIdentity(node_id=node_id, callsign=node_id.upper()),
            plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
            network=NetworkConfig(network_id="log-net", repeat=1, psk=psk),
            capabilities=CapabilitiesConfig(receive=capabilities or {}),
            connections=ConnectionsConfig(blocked=blocked or []),
            gossip=GossipConfig(enabled=False),
        )
    )


async def _run(sender_gw, receiver_gw, action):
    ns, nr = SoldierNode(sender_gw), SoldierNode(receiver_gw)
    await ns.start()
    await nr.start()
    try:
        await action(ns, nr)
        await asyncio.sleep(0.05)
    finally:
        await ns.stop()
        await nr.stop()


async def test_audit_logs_incoming_and_outgoing_accepted():
    a, b = _gw("node-a"), _gw("node-b")
    await _run(a, b, lambda ns, nr: ns.presence(1, 2))

    out = a.message_log(direction="out")
    assert any(e["type"] == "Presence" and e["disposition"] == "accepted" for e in out)
    inc = b.message_log(direction="in", disposition="accepted")
    assert any(e["type"] == "Presence" and e["originator_id"] == "node-a" for e in inc)


async def test_audit_logs_security_rejection_with_reason():
    a, b = _gw("node-a", psk="right"), _gw("node-b", psk="wrong")
    await _run(a, b, lambda ns, nr: ns.presence(1, 2))

    rejected = b.message_log(disposition="rejected")
    assert rejected, "expected a rejection entry"
    assert any((e["reason"] or "").startswith("security") for e in rejected)


async def test_audit_logs_capability_rejection_with_type():
    a = _gw("node-a")
    b = _gw("node-b", capabilities={"ContactSighting": False})
    await _run(a, b, lambda ns, nr: ns.report_contact(1, 2, "enemy"))

    rejected = b.message_log(disposition="rejected")
    assert any(
        (e["reason"] or "").startswith("capability") and e["type"] == "ContactSighting"
        for e in rejected
    )


async def test_audit_logs_policy_rejection():
    a = _gw("node-a")
    b = _gw("node-b", blocked=["node-a"])
    await _run(a, b, lambda ns, nr: ns.presence(1, 2))
    rejected = b.message_log(disposition="rejected")
    assert any((e["reason"] or "").startswith("policy") for e in rejected)


def test_application_log_captures_errors():
    setup_logging()
    logging.getLogger("jdssarrow.test").error("boom happened")
    recent = app_log()
    assert any(r["level"] == "ERROR" and "boom happened" in r["message"] for r in recent)
    assert app_log(min_level="ERROR")  # filter works


def test_web_log_endpoints(tmp_path):
    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    cfg = tmp_path / "web.yaml"
    cfg.write_text(yaml.safe_dump({"plugins": {"transport": "loopback", "security": "null"}}))
    with TestClient(create_app(str(cfg))) as client:
        # the node identifies on startup → an outgoing accepted entry exists
        client.post("/api/publish/chat", json={"text": "hello log"})
        msgs = client.get("/api/logs/messages?direction=out").json()
        assert msgs["counts"]["out"] >= 1
        assert any(e["type"] == "Chat" for e in msgs["entries"])

        app = client.get("/api/logs/app").json()
        assert isinstance(app, list)  # app log records (gateway start etc.)


def test_purge_clears_all_message_stores():
    """POST /api/purge empties the audit log, telemetry cache and dedup, leaving peers intact."""
    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    with TestClient(create_app()) as c:
        for _ in range(4):
            c.post("/api/publish/chat", json={"text": "x"})
        assert len(c.get("/api/logs/messages").json()["entries"]) > 0
        r = c.post("/api/purge")
        assert r.status_code == 200
        cleared = r.json()["cleared"]
        assert cleared["audit_log"] > 0 and cleared["telemetry"] > 0
        assert "server_cot_log" in cleared and "eud_cot_log" in cleared  # bridge logs cleared too
        # everything is empty afterwards
        assert c.get("/api/logs/messages").json()["entries"] == []
        assert c.get("/api/monitor/snapshot").json()["buffered"] == 0
