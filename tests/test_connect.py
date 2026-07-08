"""The /api/connect integration endpoint exposes what a new client needs to join."""

import yaml


def _client(tmp_path):
    from fastapi.testclient import TestClient

    from jdssarrow.connections.signing import generate_keypair
    from jdssarrow.web.app import create_app

    _priv, pub = generate_keypair()
    cfg = tmp_path / "web.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "identity": {"node_id": "node-a"},
                "plugins": {"transport": "loopback", "codec": "xml", "security": "psk"},
                "network": {"network_id": "coalition-alpha", "psk": "shared-secret"},
                "connections": {"policy_authority": "node-a", "authority_public_key": pub},
            }
        )
    )
    return TestClient(create_app(str(cfg))), pub


def test_connect_info_has_join_coordinates(tmp_path):
    client, pub = _client(tmp_path)
    with client:
        info = client.get("/api/connect").json()
        assert info["network_id"] == "coalition-alpha"
        assert info["codec"] == "xml"
        assert info["security"] == "psk"
        assert info["psk"] == "shared-secret"
        assert info["multicast_group"].startswith("239.")
        assert isinstance(info["port"], int)
        assert len(info["message_types"]) == 10
        assert info["authority_public_key"] == pub
        assert info["frame"]["magic"] == "JDSS"
