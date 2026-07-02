"""Background simulation manager + web start/stop control."""

import asyncio

import yaml

from jdssarrow.simulator.manager import SimulationManager


async def test_manager_start_stop_lifecycle():
    mgr = SimulationManager()
    assert mgr.running is False

    await mgr.start(
        network_id="sim-mgr-test",
        transport="loopback",
        codec="xml",
        psk="k",
        interval=0.02,
    )
    try:
        assert mgr.running is True
        status = mgr.status()
        assert status["client_count"] >= 5
        assert status["network_id"] == "sim-mgr-test"
        await asyncio.sleep(0.15)  # let several ticks run
        assert mgr.status()["ticks"] >= 1
    finally:
        await mgr.stop()
    assert mgr.running is False
    assert mgr.status()["client_count"] == 0


async def test_manager_rejects_double_start():
    mgr = SimulationManager()
    await mgr.start(network_id="x", transport="loopback", codec="xml", psk="k", interval=0.05)
    try:
        raised = False
        try:
            await mgr.start(network_id="y", transport="loopback", codec="xml", psk="k")
        except RuntimeError:
            raised = True
        assert raised
    finally:
        await mgr.stop()


def _client(tmp_path):
    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    cfg = tmp_path / "web.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "identity": {"node_id": "node-a"},
                "plugins": {"transport": "loopback", "security": "null"},
                "network": {"network_id": "web-sim-net"},
                "gossip": {"enabled": True, "interval_s": 0.05},
            }
        )
    )
    return TestClient(create_app(str(cfg)))


def test_web_sim_start_stop(tmp_path):
    with _client(tmp_path) as client:
        assert client.get("/api/sim").json()["running"] is False

        started = client.post("/api/sim/start", json={"interval": 0.05}).json()
        assert started["running"] is True
        assert started["client_count"] >= 5
        # joins this node's own network by default
        assert started["network_id"] == "web-sim-net"

        # a second start is a conflict
        assert client.post("/api/sim/start", json={}).status_code == 409

        stopped = client.post("/api/sim/stop").json()
        assert stopped["running"] is False


def test_web_sim_isolated_and_rogue(tmp_path):
    with _client(tmp_path) as client:
        started = client.post(
            "/api/sim/start", json={"interval": 0.05, "isolated": True, "rogue": "garbage"}
        ).json()
        assert started["running"] is True
        assert started["network_id"] == "sim-isolated"
        assert any(c["role"] == "rogue" for c in started["clients"])
        client.post("/api/sim/stop")
