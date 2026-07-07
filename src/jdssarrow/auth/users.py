"""A tiny file-backed user store with salted password hashes and signed session tokens.

The credentials file is JSON::

    {
      "secret": "<hex — signs session cookies>",
      "users": {"warrior": {"salt": "<hex>", "hash": "<hex>"}}
    }

It is created on first use, seeded with the default ``warrior`` / ``warrior1401`` account. To add
or change users by hand, drop a plaintext password into the file and it is hashed in place on the
next load — either shorthand works::

    {"users": {"scout": "hunter2"}}
    {"users": {"scout": {"password": "hunter2"}}}

No external dependencies: PBKDF2-HMAC-SHA256 for passwords, HMAC-SHA256 for session tokens.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger("jdssarrow.auth")

DEFAULT_USER = "warrior"
DEFAULT_PASSWORD = "warrior1401"
DEFAULT_FILE = "jdss-users.json"

_ITERATIONS = 200_000
_SESSION_TTL = 12 * 60 * 60  # 12 hours


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS).hex()


def _make_record(password: str) -> dict[str, str]:
    salt = secrets.token_hex(16)
    return {"salt": salt, "hash": _hash(password, salt)}


class UserStore:
    """Loads/seeds a JSON credentials file and verifies passwords + session tokens."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._path = Path(path or os.environ.get("JDSS_AUTH_FILE") or DEFAULT_FILE)
        self._data = self._load_or_seed()

    # ---- persistence -----------------------------------------------------------------

    def _seed(self) -> dict:
        data = {
            "secret": secrets.token_hex(32),
            "users": {DEFAULT_USER: _make_record(DEFAULT_PASSWORD)},
        }
        self._save(data)
        log.info("seeded credentials file %s with default user %r", self._path, DEFAULT_USER)
        return data

    def _load_or_seed(self) -> dict:
        if not self._path.exists():
            return self._seed()

        try:
            data = json.loads(self._path.read_text())
            if not isinstance(data, dict):
                raise ValueError("credentials file is not a JSON object")
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            # a corrupt / hand-broken file must not take the whole gateway down: back it up and
            # re-seed the default account rather than crashing at import time.
            backup = self._path.with_suffix(self._path.suffix + ".corrupt")
            with contextlib.suppress(OSError):
                self._path.replace(backup)
            log.warning(
                "unreadable credentials file %s (%s); re-seeded, kept backup at %s",
                self._path, exc, backup,
            )
            return self._seed()
        dirty = "secret" not in data  # persist a generated secret so cookies survive restarts
        data.setdefault("secret", secrets.token_hex(32))
        users = data.setdefault("users", {})

        # migrate any hand-written plaintext passwords to salted hashes
        for name, rec in list(users.items()):
            if isinstance(rec, str):
                users[name] = _make_record(rec)
                dirty = True
            elif isinstance(rec, dict) and "password" in rec:
                users[name] = _make_record(rec["password"])
                dirty = True

        if not users:  # never leave an unloginnable gateway
            users[DEFAULT_USER] = _make_record(DEFAULT_PASSWORD)
            dirty = True

        if dirty:
            self._save(data)
        return data

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2) + "\n")
        with contextlib.suppress(OSError):
            self._path.chmod(0o600)  # secret + hashes: keep it owner-only

    # ---- passwords -------------------------------------------------------------------

    def verify(self, username: str, password: str) -> bool:
        rec = self._data["users"].get(username)
        if not isinstance(rec, dict) or "salt" not in rec or "hash" not in rec:
            return False
        return hmac.compare_digest(_hash(password, rec["salt"]), rec["hash"])

    # ---- session tokens (stateless, HMAC-signed) -------------------------------------

    @property
    def _secret(self) -> bytes:
        return (os.environ.get("JDSS_AUTH_SECRET") or self._data["secret"]).encode()

    def make_token(self, username: str, ttl: int = _SESSION_TTL) -> str:
        expires = int(time.time()) + ttl
        payload = f"{username}|{expires}"
        sig = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}|{sig}"

    def verify_token(self, token: str | None) -> str | None:
        """Return the username for a valid, unexpired token, else ``None``."""
        if not token:
            return None
        try:
            username, expires, sig = token.rsplit("|", 2)
        except ValueError:
            return None
        payload = f"{username}|{expires}"
        expected = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        try:
            if int(expires) < time.time():
                return None
        except ValueError:
            return None
        if username not in self._data["users"]:
            return None
        return username
