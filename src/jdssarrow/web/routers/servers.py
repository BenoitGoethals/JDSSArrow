"""CRUD for CoT/TAK server connections, managed from the Configuration tab.

Each change updates the running node's config, reconciles the live connectors (so enabling a
server connects to it immediately and deleting one drops it), and persists the ``servers`` section
to the config file so it survives a restart.
"""

from __future__ import annotations

import base64
import binascii
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from jdssarrow.config.models import ServerConnection
from jdssarrow.security.pkcs12 import load_pkcs12
from jdssarrow.web.deps import get_gateway
from jdssarrow.web.runtime import persist_config_section

router = APIRouter(tags=["servers"])


class ServerInput(BaseModel):
    """Editable fields for a server connection (``id`` is assigned by the server on create)."""

    name: str = ""
    host: str = "127.0.0.1"
    port: int = 8089
    tls: bool = False
    verify: bool = True
    client_cert: str | None = None
    client_key: str | None = None
    cacert: str | None = None
    enabled: bool = True


def _current(request: Request) -> list[ServerConnection]:
    return list(get_gateway(request).config.servers)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "server"


def _unique_id(base: str, existing: set[str]) -> str:
    sid, n = base, 2
    while sid in existing:
        sid, n = f"{base}-{n}", n + 1
    return sid


async def _apply(request: Request, servers: list[ServerConnection]) -> None:
    """Make ``servers`` the live + persisted truth: config, connectors, and the config file."""
    get_gateway(request).config.servers = servers
    await request.app.state.server_manager.reconcile(servers)
    persist_config_section(request.app, "servers", [s.model_dump() for s in servers])


@router.get("/api/servers")
def list_servers(request: Request) -> dict:
    """All server connections with their live connection status."""
    return {"servers": request.app.state.server_manager.status()}


@router.post("/api/servers", status_code=201)
async def create_server(body: ServerInput, request: Request) -> dict:
    servers = _current(request)
    existing = {s.id for s in servers}
    sid = _unique_id(_slug(body.name or body.host), existing)
    server = ServerConnection(id=sid, **body.model_dump())
    servers.append(server)
    await _apply(request, servers)
    return {"servers": request.app.state.server_manager.status(), "id": sid}


@router.put("/api/servers/{server_id}")
async def update_server(server_id: str, body: ServerInput, request: Request) -> dict:
    servers = _current(request)
    if not any(s.id == server_id for s in servers):
        raise HTTPException(status_code=404, detail=f"no server '{server_id}'")
    servers = [
        ServerConnection(id=server_id, **body.model_dump()) if s.id == server_id else s
        for s in servers
    ]
    await _apply(request, servers)
    return {"servers": request.app.state.server_manager.status()}


@router.delete("/api/servers/{server_id}")
async def delete_server(server_id: str, request: Request) -> dict:
    servers = _current(request)
    remaining = [s for s in servers if s.id != server_id]
    if len(remaining) == len(servers):
        raise HTTPException(status_code=404, detail=f"no server '{server_id}'")
    await _apply(request, remaining)
    return {"servers": request.app.state.server_manager.status()}


@router.post("/api/servers/test")
async def test_server(body: ServerInput, request: Request) -> dict:
    """Probe a server's reachability without saving it (transient connector)."""
    server = ServerConnection(id="__test__", **body.model_dump())
    return await request.app.state.server_manager.test(server)


class Pkcs12Import(BaseModel):
    p12_base64: str  # the raw .p12/.pfx bytes, base64-encoded by the browser
    password: str = ""


@router.post("/api/servers/pkcs12")
def import_pkcs12(body: Pkcs12Import) -> dict:
    """Decrypt an uploaded .p12/.pfx into PEM material for a server's cert/key/CA fields.

    Stateless: returns ``{client_cert, client_key, cacert, common_name}`` for the UI to drop into
    the form; nothing is saved until the server is created/updated. The CommonName is returned so
    the operator can confirm it matches a registered OpenTAKServer user.
    """
    try:
        data = base64.b64decode(body.p12_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid base64 for the .p12 file") from exc
    try:
        return load_pkcs12(data, body.password or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"could not read .p12: {exc}") from exc
