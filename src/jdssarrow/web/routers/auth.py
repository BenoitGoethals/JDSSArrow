"""Login / logout / whoami for the web dashboard.

A minimal session: ``POST /api/auth/login`` verifies a username+password against the file-backed
:class:`~jdssarrow.auth.users.UserStore` and sets an httpOnly, HMAC-signed session cookie;
``GET /api/auth/me`` reports the current user (401 when signed out); ``POST /api/auth/logout``
clears the cookie. Every other ``/api/*`` route is gated by the auth middleware in
:mod:`jdssarrow.web.app`, which checks this same cookie.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

router = APIRouter(tags=["auth"])

#: session cookie name — also referenced by the auth middleware and the WebSocket handler.
COOKIE_NAME = "jdss_session"


def auth_disabled() -> bool:
    """Opt out of the login gate (dev, or when auth is enforced by a reverse proxy).

    Set ``JDSS_AUTH_DISABLED=1`` to serve the dashboard and API without a session. Off by
    default — the gateway ships closed."""
    return os.environ.get("JDSS_AUTH_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}


class LoginIn(BaseModel):
    username: str
    password: str


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    # Secure flag only over https, so plain-http localhost/LAN dashboards still receive the cookie.
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=12 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    store = request.app.state.user_store
    if not store.verify(body.username, body.password):
        raise HTTPException(status_code=401, detail="invalid username or password")
    _set_session_cookie(request, response, store.make_token(body.username))
    return {"username": body.username}


@router.get("/api/auth/me")
def me(request: Request) -> dict:
    if auth_disabled():
        return {"username": "operator"}
    store = request.app.state.user_store
    username = store.verify_token(request.cookies.get(COOKIE_NAME))
    if not username:
        raise HTTPException(status_code=401, detail="not authenticated")
    return {"username": username}


@router.post("/api/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}
