"""File-backed authentication for the web dashboard.

A deliberately small, dependency-free login: credentials live in a JSON file (default
``jdss-users.json`` in the working directory, seeded with ``warrior`` / ``warrior1401``),
passwords are stored salted+hashed (PBKDF2-HMAC-SHA256), and sessions are stateless
HMAC-signed cookie tokens. See :class:`~jdssarrow.auth.users.UserStore`.
"""

from __future__ import annotations

from jdssarrow.auth.users import DEFAULT_PASSWORD, DEFAULT_USER, UserStore

__all__ = ["DEFAULT_PASSWORD", "DEFAULT_USER", "UserStore"]
