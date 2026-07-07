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


def test_corrupt_credentials_file_is_reseeded(tmp_path):
    path = tmp_path / "users.json"
    path.write_text("t{ this is not json")  # a hand-broken file must not crash the app
    store = UserStore(path)
    assert store.verify(DEFAULT_USER, DEFAULT_PASSWORD) is True  # re-seeded with the default
    assert (tmp_path / "users.json.corrupt").exists()  # the bad file is kept for inspection


def _app(tmp_path, monkeypatch):
    from jdssarrow.web.app import create_app

    monkeypatch.delenv("JDSS_AUTH_DISABLED", raising=False)  # conftest sets it; auth ON here
    monkeypatch.setenv("JDSS_AUTH_FILE", str(tmp_path / "users.json"))
    cfg = tmp_path / "web.yaml"
    cfg.write_text(yaml.safe_dump({"plugins": {"transport": "loopback", "security": "null"}}))
    return create_app(str(cfg))


def test_web_login_is_ui_gate_only(tmp_path, monkeypatch):
    """The login guards the dashboard (via /api/auth/me), NOT the REST API itself.

    The API stays open so machine clients (the simulator's POST /api/inject, health probes) keep
    working — they authenticate at the message layer via the coalition PSK, not the web session."""
    with TestClient(_app(tmp_path, monkeypatch)) as client:
        # the REST API is open with or without a session (machine clients, curl, monitoring)
        assert client.get("/api/health").status_code == 200

        # ...but whoami reports "not signed in" until you log in (this is what gates the UI)
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

        # the session cookie now identifies the operator to the UI
        assert client.get("/api/auth/me").json()["username"] == DEFAULT_USER

        # logout clears the session → back to "not signed in" for the UI
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/health").status_code == 200  # API still open regardless


def test_auth_disabled_reports_default_operator(tmp_path, monkeypatch):
    from jdssarrow.web.app import create_app

    monkeypatch.setenv("JDSS_AUTH_DISABLED", "1")
    monkeypatch.setenv("JDSS_AUTH_FILE", str(tmp_path / "users.json"))
    cfg = tmp_path / "web.yaml"
    cfg.write_text(yaml.safe_dump({"plugins": {"transport": "loopback", "security": "null"}}))
    with TestClient(create_app(str(cfg))) as client:
        # with auth disabled the UI shows no login screen — whoami returns a default operator
        assert client.get("/api/auth/me").json()["username"] == "operator"
        assert client.get("/api/health").status_code == 200
