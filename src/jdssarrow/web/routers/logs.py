"""Logging endpoints — the application log and the message audit log.

- ``/api/logs/messages`` — every incoming/outgoing message with its disposition
  (accepted/rejected) and, for rejections, the reason why (security, codec, framing, policy,
  capability, duplicate). Filterable by direction / disposition.
- ``/api/logs/app`` — recent application log records (lifecycle, warnings, errors).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.web.deps import get_gateway

router = APIRouter(tags=["logs"])


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
