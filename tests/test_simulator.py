"""Multi-client JDSS interoperability simulator: compliance & interaction tests."""

from jdssarrow.datamodel.messages import MessageType
from jdssarrow.plugins.registry import registry
from jdssarrow.simulator.profiles import CLIENT_PROFILES
from jdssarrow.simulator.scenario import DEFAULT_ROSTER, Simulation


def test_all_client_profiles_are_registered_plugins():
    names = set(registry.names("profiles"))
    assert set(CLIENT_PROFILES).issubset(names)


async def test_default_scenario_is_fully_compliant():
    sim = Simulation(transport="loopback", codec="xml")
    report = await sim.run(ticks=16, tick_interval=0.0)

    # every AEP-76 message type appears on the network (structural compliance surface)
    assert report.all_seven_types, report.types_observed
    assert set(report.types_observed) == {str(t) for t in MessageType}

    # every non-monitor client delivered traffic to the command post
    expected_peers = sum(c for r, c in DEFAULT_ROSTER if r != "commandpost")
    assert len(report.peers_at_command_post) == expected_peers

    # the medic answered every CASEVAC — cross-client C2 interaction works
    assert report.casevac_requests >= 1
    assert report.casevac_acks >= report.casevac_requests


async def test_roles_emit_their_characteristic_messages():
    sim = Simulation(transport="loopback", codec="json")
    report = await sim.run(ticks=12, tick_interval=0.0)
    by_role: dict[str, set[str]] = {}
    for c in report.clients:
        by_role.setdefault(c["role"], set()).update(c["sent_by_type"])

    assert str(MessageType.SKETCH) in by_role["scout"]
    assert str(MessageType.OVERLAY) in by_role["forward_observer"]
    assert str(MessageType.CASEVAC) in by_role["team_leader"]
    assert str(MessageType.CONTACT) in by_role["uav_sensor"]
    assert str(MessageType.CHAT) in by_role["medic"]  # the CASEVAC acknowledgement


async def test_connection_matrix_is_fully_meshed():
    """On one multicast net every legit node hears every other (full mesh minus diagonal)."""
    sim = Simulation(transport="loopback", codec="xml")
    report = await sim.run(ticks=12, tick_interval=0.0)

    legit = [c.node_id for c in sim.clients]
    assert set(report.nodes) == set(legit)
    for observer in legit:
        row = report.matrix[observer]
        assert observer not in row  # no self-edge (a node never hears itself)
        for other in legit:
            if other != observer:
                assert row.get(other, 0) > 0, f"{observer} did not hear {other}"


async def test_matrix_rogue_column_is_empty():
    """The rogue's column is all-zero (nobody accepts it) though its row may be populated."""
    sim = Simulation(transport="loopback", codec="xml", rogue="garbage")
    report = await sim.run(ticks=12, tick_interval=0.0)

    rogue = report.rogue_node
    assert rogue in report.nodes
    # no legitimate observer accepted anything from the rogue
    for observer, row in report.matrix.items():
        if observer != rogue:
            assert row.get(rogue, 0) == 0


async def test_custom_client_type_plugs_in():
    """A brand-new compliant client type can be added without touching the engine."""
    from jdssarrow.simulator.profiles import ClientProfile

    seen: list[str] = []

    class SapperClient(ClientProfile):
        role = "sapper"
        emits = (MessageType.PRESENCE, MessageType.CHAT)

        async def on_tick(self, client, tick):
            await client.presence()
            if tick == 1:
                await client.chat("obstacle breached")
                seen.append("chatted")

    reg = registry
    reg.register("profiles", "sapper", SapperClient)
    try:
        sim = Simulation(
            roster=[("commandpost", 1), ("sapper", 1)],
            transport="loopback",
            codec="xml",
            registry=reg,
        )
        report = await sim.run(ticks=4, tick_interval=0.0)
        assert "chatted" in seen
        # command post saw the sapper's traffic
        assert any("sapper" in p for p in report.peers_at_command_post)
    finally:
        reg._overrides.get("profiles", {}).pop("sapper", None)
