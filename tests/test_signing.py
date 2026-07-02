"""Per-authority Ed25519 signing: valid updates accepted, forged/unsigned/tampered rejected."""

import asyncio

from jdssarrow.config.models import (
    ConnectionsConfig,
    GatewayConfig,
    GossipConfig,
    NetworkConfig,
    NodeIdentity,
    PluginSelection,
)
from jdssarrow.connections.distributor import PolicyDistributor
from jdssarrow.connections.policy import MatrixConnectionPolicy
from jdssarrow.connections.signing import (
    AuthoritySigner,
    AuthorityVerifier,
    canonical_payload,
    generate_keypair,
)
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode


def test_sign_verify_roundtrip_and_tamper():
    priv, pub = generate_keypair()
    signer, verifier = AuthoritySigner(priv), AuthorityVerifier(pub)
    payload = canonical_payload(3, "allow", {"node-c": "block"})
    sig = signer.sign(payload)
    assert verifier.verify(payload, sig) is True
    # tampered payload (different version) fails
    assert verifier.verify(canonical_payload(4, "allow", {"node-c": "block"}), sig) is False
    # wrong key fails
    _, other_pub = generate_keypair()
    assert AuthorityVerifier(other_pub).verify(payload, sig) is False


async def test_distributor_rejects_forged_update():
    """An attacker with the coalition key but not the private key cannot forge an update."""
    priv, pub = generate_keypair()
    _, attacker_pub = generate_keypair()
    attacker_priv, _ = generate_keypair()

    policy = MatrixConnectionPolicy(node_id="COALITION")

    class _Engine:  # minimal stand-in; we call _on_update directly
        def on_control(self, *_a, **_k):
            pass

    dist = PolicyDistributor(
        "node-b", _Engine(), policy, authority_id="node-a", verifier=AuthorityVerifier(pub)
    )

    # forged: signed with the wrong private key
    forged_payload = canonical_payload(5, "allow", {"node-c": "block"})
    forged_sig = AuthoritySigner(attacker_priv).sign(forged_payload)
    await dist._on_update(
        {"node": "node-a", "version": 5, "default_action": "allow",
         "overrides": {"node-c": "block"}, "sig": forged_sig}
    )
    assert policy.allows_peer("node-c") is True  # rejected → still allowed

    # unsigned update also rejected when a verifier is configured
    await dist._on_update(
        {"node": "node-a", "version": 6, "default_action": "allow",
         "overrides": {"node-c": "block"}}
    )
    assert policy.allows_peer("node-c") is True

    # correctly signed update is accepted
    good_sig = AuthoritySigner(priv).sign(forged_payload)
    await dist._on_update(
        {"node": "node-a", "version": 5, "default_action": "allow",
         "overrides": {"node-c": "block"}, "sig": good_sig}
    )
    assert policy.allows_peer("node-c") is False


def _signed_gw(node_id, priv, pub):
    conn = ConnectionsConfig(policy_authority="node-a", authority_public_key=pub)
    if node_id == "node-a":
        conn.authority_private_key = priv
    return JdssGateway(
        GatewayConfig(
            identity=NodeIdentity(node_id=node_id, callsign=node_id.upper()),
            plugins=PluginSelection(transport="loopback", codec="xml", security="psk"),
            network=NetworkConfig(network_id="sign-test", repeat=1, psk="k"),
            connections=conn,
            gossip=GossipConfig(enabled=False, interval_s=0.02),
        )
    )


async def test_signed_coalition_policy_propagates_end_to_end():
    priv, pub = generate_keypair()
    gw_a, gw_b = _signed_gw("node-a", priv, pub), _signed_gw("node-b", priv, pub)
    na, nb = SoldierNode(gw_a), SoldierNode(gw_b)
    assert gw_a.coalition_snapshot()["signed"] is True
    for n in (na, nb):
        await n.start()
    try:
        await gw_a.coalition_set("node-c", "block")
        await asyncio.sleep(0.2)
        # node-b accepted the *signed* update from the authority
        assert gw_b.coalition.allows_peer("node-c") is False
        assert gw_b.coalition_snapshot()["signed"] is True
    finally:
        for n in (na, nb):
            await n.stop()
