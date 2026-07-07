"""File-backed dashboard login: user store, session tokens, and the web auth gate."""

import json

import yaml
from fastapi.testclient import TestClient

from jdssarrow.auth import DEFAULT_PASSWORD, DEFAULT_USER, UserStore


def test_user_store_seeds_default_user(tmp_path):
    path = tmp_path / "users.json"
    store = UserStore(path)
    assert path.exists()
    assert store.verify(DEFAULT_USER, DEFAULT_PASSWORD) is True
    assert store.verify(DEFAULT_USER, "wrong") is False
    assert store.verify("nobody", DEFAULT_PASSWORD) is False
    # password is stored hashed, never in plaintext
    on_disk = json.loads(path.read_text())
    assert "password" not in on_disk["users"][DEFAULT_USER]
    assert DEFAULT_PASSWORD not in path.read_text()


def test_session_token_roundtrip_and_tamper(tmp_path):
    store = UserStore(tmp_path / "users.json")
    token = store.make_token(DEFAULT_USER)
    assert store.verify_token(token) == DEFAULT_USER
    assert store.verify_token(None) is None
    assert store.verify_token("garbage") is None
    assert store.verify_token(token + "x") is None  # tampered signature
    assert store.verify_token(store.make_token(DEFAULT_USER, ttl=-1)) is None  # expired


def test_plaintext_password_migrated_in_place(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(json.dumps({"users": {"scout": "hunter2"}}))
    store = UserStore(path)
    assert store.verify("scout", "hunter2") is True
    saved = json.loads(path.read_text())
    assert set(saved["users"]["scout"]) == {"salt", "hash"}  # rewritten as a hash
    assert "secret" in saved  # a signing secret was generated + persisted


def _app(tmp_path, monkeypatch):
    from jdssarrow.web.app import create_app

    monkeypatch.delenv("JDSS_AUTH_DISABLED", raising=False)  # conftest sets it; auth ON here
    monkeypatch.setenv("JDSS_AUTH_FILE", str(tmp_path / "users.json"))
    cfg = tmp_path / "web.yaml"
    cfg.write_text(yaml.safe_dump({"plugins": {"transport": "loopback", "security": "null"}}))
    return create_app(str(cfg))


def test_web_login_gate(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as client:
        # a protected endpoint is refused before login
        assert client.get("/api/health").status_code == 401
        assert client.get("/api/auth/me").status_code == 401

        # bad credentials are rejected
        assert client.post(
            "/api/auth/login", json={"username": DEFAULT_USER, "password": "nope"}
        ).status_code == 401

        # default credentials work and set a session cookie
        r = client.post(
            "/api/auth/login", json={"username": DEFAULT_USER, "password": DEFAULT_PASSWORD}
        )
        assert r.status_code == 200
        assert r.json()["username"] == DEFAULT_USER

        # now the session cookie unlocks the API + whoami
        assert client.get("/api/auth/me").json()["username"] == DEFAULT_USER
        assert client.get("/api/health").status_code == 200

        # logout clears the session
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/health").status_code == 401


def test_auth_disabled_opens_gate(tmp_path, monkeypatch):
    from jdssarrow.web.app import create_app

    monkeypatch.setenv("JDSS_AUTH_DISABLED", "1")
    monkeypatch.setenv("JDSS_AUTH_FILE", str(tmp_path / "users.json"))
    cfg = tmp_path / "web.yaml"
    cfg.write_text(yaml.safe_dump({"plugins": {"transport": "loopback", "security": "null"}}))
    with TestClient(create_app(str(cfg))) as client:
        assert client.get("/api/health").status_code == 200  # no login required
        assert client.get("/api/auth/me").json()["username"] == "operator"
