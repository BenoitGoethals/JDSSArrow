"""Extract PEM material from a PKCS#12 (.p12 / .pfx) bundle.

A .p12 exported from OpenTAKServer (or any TAK server / ATAK data package) holds the client
certificate, its private key and usually the issuing CA chain, all password-protected. This turns
it into the PEM strings the server connector needs, so an operator can import a .p12 from the web
console without ever handling a file path.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


def load_pkcs12(data: bytes, password: str | None) -> dict:
    """Decrypt a .p12 and return its client cert, private key and CA chain as PEM strings.

    Returns ``{client_cert, client_key, cacert, common_name}``. ``cacert`` is ``None`` when the
    bundle carries no CA chain. Raises ``ValueError`` on a wrong password or malformed data (the
    web layer maps that to HTTP 400).
    """
    pwd = password.encode() if password else None
    try:
        key, cert, extra = pkcs12.load_key_and_certificates(data, pwd)
    except ValueError as exc:  # wrong password or not a PKCS#12 file
        raise ValueError("wrong password or not a valid .p12 file") from exc

    extras = list(extra or [])

    if key is None:
        # No private key at all — almost certainly a CA / truststore bundle, not a user cert.
        raise ValueError(
            "no private key in this .p12 — it looks like a CA/truststore bundle; import the "
            "user/client .p12 (the one holding your certificate and private key) instead"
        )

    # load_key_and_certificates returns `cert` = the certificate whose localKeyID matches the key.
    # Some exporters omit that link, leaving the client cert among the 'additional' certs — recover
    # it by matching the private key's public key so those bundles still import.
    if cert is None:
        want = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        for candidate in extras:
            got = candidate.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            if got == want:
                cert = candidate
                extras.remove(candidate)
                break

    if cert is None:
        raise ValueError("the .p12 has a private key but no matching client certificate")

    client_cert = cert.public_bytes(serialization.Encoding.PEM).decode()
    client_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    cacert = "".join(c.public_bytes(serialization.Encoding.PEM).decode() for c in extras)

    cn = ""
    attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if attrs:
        cn = str(attrs[0].value)

    return {
        "client_cert": client_cert,
        "client_key": client_key,
        "cacert": cacert or None,
        "common_name": cn,
    }
