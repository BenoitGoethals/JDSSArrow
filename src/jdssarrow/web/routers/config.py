"""Config endpoints — view the running config and the AEP-76 volume map."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from jdssarrow import AEP76_VOLUMES
from jdssarrow.config.models import GatewayConfig
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.security.classification import Classification
from jdssarrow.web.deps import get_gateway
from jdssarrow.web.runtime import persist_config_section, reconfigure

router = APIRouter(tags=["config"])


@router.get("/api/volumes")
def volumes() -> dict[str, str]:
    """The five AEP-76 volumes this system implements."""
    return AEP76_VOLUMES


@router.get("/api/classifications")
def classifications() -> dict[int, str]:
    """Classification level → label, for badges and the banner."""
    return {int(c): c.label for c in Classification}


@router.get("/api/config", response_model=GatewayConfig)
def get_config(gateway: JdssGateway = Depends(get_gateway)) -> GatewayConfig:
    return gateway.config


@router.put("/api/config", response_model=GatewayConfig)
async def update_config(patch: dict[str, Any], request: Request) -> GatewayConfig:
    """Apply a partial config change and hot-restart the gateway on the new settings.

    The merged config is validated first; the running node is only replaced once the new
    config is valid, so an invalid patch (HTTP 422) never takes the node offline.
    """
    try:
        return await reconfigure(request.app, patch)
    except ValidationError as exc:
        detail = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise HTTPException(status_code=422, detail=detail) from exc


@router.get("/api/config/endpoint")
def endpoint(gateway: JdssGateway = Depends(get_gateway)) -> dict:
    group, port = gateway.endpoint
    return {"multicast_group": group, "port": port, "network_id": gateway.config.network.network_id}


@router.get("/api/capabilities")
def get_capabilities(gateway: JdssGateway = Depends(get_gateway)) -> dict:
    """The per-message-type receive/emit permission matrix."""
    return gateway.capabilities_snapshot()


@router.post("/api/capabilities/{message_type}")
def set_capability(
    message_type: str,
    direction: str,
    allowed: bool,
    request: Request,
    gateway: JdssGateway = Depends(get_gateway),
) -> dict:
    """Toggle one cell of the capability matrix and persist it. direction ∈ {receive, emit}."""
    if direction not in ("receive", "emit"):
        raise HTTPException(status_code=400, detail="direction must be receive or emit")
    snap = gateway.set_capability(message_type, direction, allowed)
    # persist the whole matrix back to the config file so it survives a restart
    persisted = persist_config_section(
        request.app, "capabilities", {"receive": snap["receive"], "emit": snap["emit"]}
    )
    return {**snap, "persisted": persisted}
