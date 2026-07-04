"""Per-authority Ed25519 signing for coalition policy updates.

The shared coalition key authenticates that a frame came from *a* coalition member; it does
not prove *which* member. To make coalition policy updates unforgeable, the authority signs
each update with its own Ed25519 **private** key, and every node verifies with the authority's
**public** key (distributed in config). A key-holding member without the private key cannot
produce a valid signature, so it cannot impersonate the authority.

The signature covers a canonical serialization of the policy (version + default action +
overrides), so a replay with altered contents fails verification.
"""

from __future__ import annotations

import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def generate_keypair() -> tuple[str, str]:
    """Return ``(private_key_hex, public_key_hex)`` for a new Ed25519 authority key."""
    priv = Ed25519PrivateKey.generate()
    return priv.private_bytes_raw().hex(), priv.public_key().public_bytes_raw().hex()


def canonical_payload(
    version: int, default_action: str, overrides: dict, pairs: dict | None = None
) -> bytes:
    """Deterministic bytes signed/verified for a policy update (stable key ordering)."""
    return json.dumps(
        {
            "version": version,
            "default_action": default_action,
            "overrides": overrides,
            "pairs": pairs or {},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class AuthoritySigner:
    def __init__(self, private_key_hex: str) -> None:
        self._key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))

    def sign(self, payload: bytes) -> str:
        return self._key.sign(payload).hex()


class AuthorityVerifier:
    def __init__(self, public_key_hex: str) -> None:
        self._key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))

    def verify(self, payload: bytes, signature_hex: str) -> bool:
        try:
            self._key.verify(bytes.fromhex(signature_hex), payload)
            return True
        except (InvalidSignature, ValueError):
            return False
