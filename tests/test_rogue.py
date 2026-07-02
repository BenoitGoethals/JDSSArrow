"""Rogue / non-compliant client is rejected at every boundary, network stays healthy."""

import pytest

from jdssarrow.simulator.rogue import ROGUE_MODES
from jdssarrow.simulator.scenario import DEFAULT_ROSTER, Simulation


@pytest.mark.parametrize("mode", ROGUE_MODES)
async def test_rogue_is_rejected_and_network_unaffected(mode):
    sim = Simulation(transport="loopback", codec="xml", rogue=mode)
    report = await sim.run(ticks=14, tick_interval=0.0)

    # 1) the rogue injected traffic...
    assert report.rogue_frames_sent > 0
    # 2) ...which the legitimate receivers dropped (failed auth / framing / schema)...
    assert report.rogue_frames_rejected > 0
    # 3) ...so it never reached the common operational picture...
    assert report.rogue_observed is False
    assert "rogue-1" not in report.peers_at_command_post
    assert report.rogue_rejected is True

    # 4) ...and the legitimate network is entirely unaffected: all clients + all 7 types.
    assert report.all_seven_types
    expected_peers = sum(c for r, c in DEFAULT_ROSTER if r != "commandpost")
    assert len(report.peers_at_command_post) == expected_peers
    assert report.casevac_acks >= report.casevac_requests


async def test_wrong_key_rejected_by_security_layer():
    """Vol I: an unauthorised key means HMAC verification fails at every receiver."""
    sim = Simulation(transport="loopback", codec="xml", rogue="wrong_key")
    report = await sim.run(ticks=8, tick_interval=0.0)
    # command post recorded 'decode' drops but no message from the rogue
    cp = next(c for c in sim.clients if c.profile.role == "command_post")
    assert cp.gateway.metrics.drops().get("decode", 0) > 0
    assert report.rogue_rejected


async def test_insider_bad_payload_rejected_by_datamodel():
    """Vol II: even with the (leaked) coalition key, a non-JDSSDM payload is rejected."""
    sim = Simulation(transport="loopback", codec="xml", rogue="insider")
    report = await sim.run(ticks=8, tick_interval=0.0)
    assert report.rogue_frames_rejected > 0
    assert report.rogue_observed is False


def test_unknown_rogue_mode_raises():
    with pytest.raises(ValueError):
        Simulation(transport="loopback", rogue="mystery")
