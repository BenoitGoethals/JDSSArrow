"""Connection-management endpoints — view and edit this node's admit/deny matrix row.

Blocking/allowing a peer mutates the live :class:`ConnectionPolicy`, so enforcement changes
immediately: a blocked peer's subsequent messages are dropped on ingest (drop reason
``policy``) and it drops out of the operational picture.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.web.deps import get_gateway

router = APIRouter(tags=["connections"])


@router.get("/api/connections")
def get_connections(gateway: JdssGateway = Depends(get_gateway)) -> dict:
    """Current policy plus known peers, so the UI can render the manageable matrix row."""
    return {
        "policy": gateway.connection_policy(),
        "peers": gateway.peers(),
        "dropped_by_policy": gateway.metrics.drops().get("policy", 0),
    }


@router.post("/api/connections/{peer_id}")
def set_connection(
    peer_id: str, action: str, gateway: JdssGateway = Depends(get_gateway)
) -> dict:
    """Allow / block / reset a peer at runtime. ``action`` ∈ {allow, block, reset}."""
    if action == "block":
        return gateway.block_peer(peer_id)
    if action == "allow":
        return gateway.allow_peer(peer_id)
    if action == "reset":
        return gateway.reset_peer(peer_id)
    raise HTTPException(status_code=400, detail="action must be allow, block or reset")


@router.get("/api/coalition")
def get_coalition(gateway: JdssGateway = Depends(get_gateway)) -> dict:
    """The coalition-wide policy as this node currently sees it (distributed via gossip)."""
    return gateway.coalition_snapshot()


@router.post("/api/coalition/{peer_id}")
async def set_coalition(
    peer_id: str, action: str, gateway: JdssGateway = Depends(get_gateway)
) -> dict:
    """Authority-only: set a coalition-wide directive and distribute it to every node."""
    try:
        return await gateway.coalition_set(peer_id, action)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
