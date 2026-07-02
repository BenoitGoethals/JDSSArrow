"""Vol V allocator determinism + Vol I security behaviour + web API smoke test."""

import pytest

from jdssarrow.networkaccess.allocator import DefaultAddressAllocator
from jdssarrow.security.provider import PreSharedKeySecurity, SecurityError


def test_allocator_is_deterministic_and_scoped():
    a1 = DefaultAddressAllocator()
    a2 = DefaultAddressAllocator()
    # Same input → same output on independently-configured nodes (pre-mission distribution).
    assert a1.multicast_group("netX") == a2.multicast_group("netX")
    assert a1.allocate_unicast("node-7") == a2.allocate_unicast("node-7")
    group, port = a1.multicast_group("netX")
    assert group.startswith("239.4.")  # admin-scoped multicast
    assert 46000 <= port < 47000


def test_different_networks_get_different_groups():
    a = DefaultAddressAllocator()
    assert a.multicast_group("alpha") != a.multicast_group("bravo")


def test_psk_detects_tampering():
    sec = PreSharedKeySecurity("key")
    wire = sec.protect(b"hello")
    assert sec.verify(wire) == b"hello"
    with pytest.raises(SecurityError):
        sec.verify(wire[:-1] + bytes([wire[-1] ^ 0x01]))  # flip a tag bit


def test_web_app_endpoints(tmp_path):
    import yaml
    from fastapi.testclient import TestClient

    from jdssarrow.web.app import create_app

    # Use the loopback transport so the test never opens a real multicast socket.
    cfg = tmp_path / "web.yaml"
    cfg.write_text(yaml.safe_dump({"plugins": {"transport": "loopback", "security": "null"}}))

    app = create_app(str(cfg))
    with TestClient(app) as client:
        assert client.get("/api/volumes").json()["II"].startswith("Data Model")
        assert client.get("/api/health").json()["status"] == "ok"
        plugins = client.get("/api/plugins").json()
        assert "xml" in plugins["codecs"]
        assert client.get("/metrics").status_code == 200
