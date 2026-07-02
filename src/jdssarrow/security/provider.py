"""Security providers (Vol I).

Two reference implementations behind the :class:`SecurityProvider` protocol:

* :class:`NullSecurity` — pass-through, for open exercises and tests.
* :class:`PreSharedKeySecurity` — appends an HMAC-SHA256 tag so receivers can authenticate
  the origin and integrity of a frame with a shared coalition key. This is the minimal
  bearer-independent protection the loaned-radio mesh needs; a TLS/DTLS provider would slot
  in here with no change to the exchange engine.

Providers operate on opaque bytes and know nothing about the JDSSDM — protection is kept
orthogonal to representation (Interface Segregation).
"""

from __future__ import annotations

import hashlib
import hmac

_TAG_LEN = 32  # HMAC-SHA256


class SecurityError(Exception):
    """Raised when a received frame fails authentication/integrity checks."""


class NullSecurity:
    name = "null"

    def protect(self, payload: bytes) -> bytes:
        return payload

    def verify(self, wire: bytes) -> bytes:
        return wire


class PreSharedKeySecurity:
    name = "psk"

    def __init__(self, key: str | bytes = "jdss-coalition-key") -> None:
        self._key = key.encode("utf-8") if isinstance(key, str) else key

    def _tag(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def protect(self, payload: bytes) -> bytes:
        return payload + self._tag(payload)

    def verify(self, wire: bytes) -> bytes:
        if len(wire) < _TAG_LEN:
            raise SecurityError("frame too short for HMAC tag")
        payload, tag = wire[:-_TAG_LEN], wire[-_TAG_LEN:]
        if not hmac.compare_digest(tag, self._tag(payload)):
            raise SecurityError("HMAC verification failed")
        return payload
