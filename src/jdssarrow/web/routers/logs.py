"""Logging endpoints — the application log and the message audit log.

- ``/api/logs/messages`` — every incoming/outgoing message with its disposition
  (accepted/rejected) and, for rejections, the reason why (security, codec, framing, policy,
  capability, duplicate). Filterable by direction / disposition.
- ``/api/logs/app`` — recent application log records (lifecycle, warnings, errors).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.web.deps import get_gateway

router = APIRouter(tags=["logs"])


@router.post("/api/purge")
def purge_messages(request: Request, gateway: JdssGateway = Depends(get_gateway)) -> dict:
    """Delete all messages: the message audit log, the telemetry cache, the receive dedup cache,
    and the CoT bridge traffic logs. (Peers, the application log and Prometheus counters stay.)"""
    cleared = gateway.purge_messages()
    server_manager = getattr(request.app.state, "server_manager", None)
    eud_server = getattr(request.app.state, "eud_server", None)
    if server_manager is not None:
        cleared["server_cot_log"] = server_manager.traffic.clear()
    if eud_server is not None:
        cleared["eud_cot_log"] = eud_server.traffic.clear()
    return {"cleared": cleared, "total": sum(cleared.values())}


@router.get("/api/logs/messages")
def message_log(
    limit: int = 200,
    direction: str | None = None,
    disposition: str | None = None,
    gateway: JdssGateway = Depends(get_gateway),
) -> dict:
    return {
        "counts": gateway.metrics.audit.counts(),
        "entries": gateway.message_log(limit, direction=direction, disposition=disposition),
    }


@router.get("/api/logs/app")
def application_log(
    limit: int = 200,
    min_level: str | None = None,
    gateway: JdssGateway = Depends(get_gateway),
) -> list[dict]:
    return gateway.application_log(limit, min_level=min_level)
