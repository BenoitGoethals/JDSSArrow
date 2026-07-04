"""Coalition-wide policy distributed via gossip: authority broadcasts, all nodes enforce."""

import asyncio

import yaml

from jdssarrow.config.models import (
    ConnectionsConfig,
    GatewayConfig,
    GossipConfig,
    NetworkConfig,
    NodeIdentity,
    PluginSelection,
)
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode


def _gw(node_id: str, authority: str | None = "node-a", psk: str = "k") -> JdssGateway:
    return JdssGateway(
        GatewayConfig(
            identity=NodeIdentity(node_id=node_id, callsign=node_id.upper()),
            plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
            network=NetworkConfig(network_id="coalition-test", repeat=1, psk=psk),
            connections=ConnectionsConfig(policy_authority=authority),
            gossip=GossipConfig(enabled=False, interval_s=0.02),  # distributor runs regardless
        )
    )


class _Collector:
    subscribes_to = ("*",)

    def __init__(self):
        self.origins: set[str] = set()

    async def handle(self, message):
        self.origins.add(message.header.originator_id)


async def test_coalition_block_propagates_to_all_nodes():
    gw_a, gw_b, gw_c = _gw("node-a"), _gw("node-b"), _gw("node-c")  # node-a is authority
    na, nb, nc = SoldierNode(gw_a), SoldierNode(gw_b), SoldierNode(gw_c)
    rx_b, rx_c = _Collector(), _Collector()
    nb.add_handler(rx_b)
    nc.add_handler(rx_c)
    for n in (na, nb, nc):
        await n.start()
    try:
        # authority blocks node-c coalition-wide
        await gw_a.coalition_set("node-c", "block")
        await asyncio.sleep(0.2)  # let the policy update propagate

        # node-b learned the coalition policy from the authority and now refuses node-c
        assert gw_b.coalition_snapshot()["overrides"].get("node-c") == "block"
        assert gw_b.coalition.allows_peer("node-c") is False

        rx_b.origins.clear()
        await nc.presence(1, 1)  # node-c transmits
        await asyncio.sleep(0.05)
        assert "node-c" not in rx_b.origins  # every node drops it, not just the authority
        # a non-blocked node still gets through
        await na.presence(2, 2)
        await asyncio.sleep(0.05)
        assert "node-a" in rx_b.origins
    finally:
        for n in (na, nb, nc):
            await n.stop()


def test_coalition_pair_route_is_not_shadowed(monkeypatch):
    """POST /api/coalition/pair must hit the pair endpoint, not /api/coalition/{peer_id='pair'}."""
    monkeypatch.setenv("JDSS_CONNECTIONS__POLICY_AUTHORITY", "node-a")
    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    with TestClient(create_app()) as c:
        r = c.post("/api/coalition/pair?observer=node-b&originator=node-c&action=block")
        assert r.status_code == 200
        assert r.json()["pairs"] == {"node-b": ["node-c"]}
        assert "pair" not in r.json()["overrides"]  # not misrouted as a column block


async def test_coalition_pair_block_is_per_observer():
    """A per-pair block (obs, from) is enforced only by the observer node — the interactive matrix.

    Authority blocks the cell (node-b <- node-c): node-b refuses node-c, but node-a still hears it.
    """
    gw_a, gw_b, gw_c = _gw("node-a"), _gw("node-b"), _gw("node-c")  # node-a is authority
    na, nb, nc = SoldierNode(gw_a), SoldierNode(gw_b), SoldierNode(gw_c)
    rx_a, rx_b = _Collector(), _Collector()
    na.add_handler(rx_a)
    nb.add_handler(rx_b)
    for n in (na, nb, nc):
        await n.start()
    try:
        await gw_a.coalition_set_pair("node-b", "node-c", "block")
        await asyncio.sleep(0.2)  # propagate to every node
        # node-b learned + enforces the pair; other nodes carry the map but it isn't their row
        assert gw_b.coalition_snapshot()["pairs"].get("node-b") == ["node-c"]

        rx_a.origins.clear()
        rx_b.origins.clear()
        await nc.presence(1, 1)  # node-c transmits
        await asyncio.sleep(0.1)
        assert "node-c" not in rx_b.origins  # node-b blocked exactly this pair
        assert "node-c" in rx_a.origins  # node-a is unaffected (per-observer, not global)

        # allow it back → node-b hears node-c again
        await gw_a.coalition_set_pair("node-b", "node-c", "allow")
        await asyncio.sleep(0.2)
        assert gw_b.coalition_snapshot()["pairs"] == {}
        rx_b.origins.clear()
        await nc.presence(2, 2)
        await asyncio.sleep(0.1)
        assert "node-c" in rx_b.origins
    finally:
        for n in (na, nb, nc):
            await n.stop()


async def test_non_authority_cannot_forge_coalition_policy():
    # node-b trusts node-a, but here node-b tries to act as authority — rejected.
    gw_a, gw_b = _gw("node-a"), _gw("node-b")
    na, nb = SoldierNode(gw_a), SoldierNode(gw_b)
    for n in (na, nb):
        await n.start()
    try:
        # node-b is not the authority → cannot set coalition policy
        try:
            await gw_b.coalition_set("node-a", "block")
            raised = False
        except PermissionError:
            raised = True
        assert raised
        await asyncio.sleep(0.1)
        # node-a's coalition view is unchanged (no forged update accepted)
        assert gw_a.coalition_snapshot()["overrides"] == {}
    finally:
        for n in (na, nb):
            await n.stop()


async def test_authority_and_versioning():
    gw_a, gw_b = _gw("node-a"), _gw("node-b")
    assert gw_a.coalition_snapshot()["am_authority"] is True
    assert gw_b.coalition_snapshot()["am_authority"] is False
    na, nb = SoldierNode(gw_a), SoldierNode(gw_b)
    for n in (na, nb):
        await n.start()
    try:
        v0 = gw_b.coalition_snapshot()["version"]
        await gw_a.coalition_set("node-x", "block")
        await gw_a.coalition_set("node-y", "block")
        await asyncio.sleep(0.2)
        # node-b converged to the authority's newer version
        assert gw_b.coalition_snapshot()["version"] > v0
        assert set(gw_b.coalition_snapshot()["overrides"]) == {"node-x", "node-y"}
    finally:
        for n in (na, nb):
            await n.stop()


def test_web_coalition_authority_and_non_authority(tmp_path):
    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    # authority node
    auth_cfg = tmp_path / "auth.yaml"
    auth_cfg.write_text(
        yaml.safe_dump(
            {
                "identity": {"node_id": "node-a"},
                "plugins": {"transport": "loopback", "security": "null"},
                "connections": {"policy_authority": "node-a"},
            }
        )
    )
    with TestClient(create_app(str(auth_cfg))) as client:
        assert client.get("/api/coalition").json()["am_authority"] is True
        r = client.post("/api/coalition/node-z?action=block").json()
        assert r["overrides"]["node-z"] == "block"

    # non-authority node cannot set coalition policy
    plain_cfg = tmp_path / "plain.yaml"
    plain_cfg.write_text(
        yaml.safe_dump(
            {
                "identity": {"node_id": "node-b"},
                "plugins": {"transport": "loopback", "security": "null"},
                "connections": {"policy_authority": "node-a"},
            }
        )
    )
    with TestClient(create_app(str(plain_cfg))) as client:
        assert client.get("/api/coalition").json()["am_authority"] is False
        assert client.post("/api/coalition/node-z?action=block").status_code == 403
